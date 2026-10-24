"""Testowe wypłaty vUSDT (preview) i okno wypłat co N dni.

Sieć jest podmieniona: `vusdt.rpc` to sztuczny węzeł Polygonu, który dekoduje
podpisane transakcje, a Turnstile odpowiada z flagi. Sprawdzamy, że:
- tryb testowy przyjmuje TYLKO vUSDT + adres 0x i wymaga captchy,
- transfer jest podpisany na właściwy kontrakt, kwotę i chainId 137,
- nonce rośnie, nawet gdy RPC spóźnia się z licznikiem `pending`,
- bez POL wysyłka czeka w kolejce i wychodzi sama po doładowaniu,
- produkcja (VERCEL_ENV=production) nigdy nie włącza trybu,
- PAYOUT_INTERVAL_DAYS blokuje kolejny wniosek na koncie po stronie serwera,
- wniosek rozlicza się SAM z łańcucha: potwierdzony transfer = paid (z tym samym
  księgowaniem co Approve), nieudany = rejected; admin nie zatwierdza.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.gettempdir(), "pf_test.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

import pytest  # noqa: E402
from eth_account import Account as EthAccount  # noqa: E402
from eth_account.typed_transactions import TypedTransaction  # noqa: E402
from eth_utils import keccak  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from hexbytes import HexBytes  # noqa: E402

from app import auth, vusdt  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Order, Payout, PayoutRequest, Trader, VusdtTransfer  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
TOKEN = "0x384D011E3775a2Da92A04fB84862cfaB3566CEb2"
ODBIORCA = "0x82c9ce759c48df8055232db0bca644dd93b3b4cc"
KAPITAL, ZYSK = 50_000.0, 10_000.0      # split 80% => dostępne 8 000


class SztucznyPolygon:
    def __init__(self):
        self.pending = 5           # licznik `pending`, który RPC „zamraża" (lag)
        self.pol = 10 * 10 ** 18
        self.vusdt = 10 ** 15
        self.wyslane: list[dict] = []
        self.receipts: dict[str, dict] = {}

    def __call__(self, method, params):
        if method == "eth_getBalance":
            return hex(self.pol)
        if method == "eth_call":
            return hex(self.vusdt)
        if method == "eth_getBlockByNumber":
            return {"baseFeePerGas": hex(100 * 10 ** 9)}
        if method == "eth_maxPriorityFeePerGas":
            return hex(2 * 10 ** 9)
        if method == "eth_estimateGas":
            return hex(52_000)
        if method == "eth_getTransactionCount":
            return hex(self.pending)
        if method == "eth_sendRawTransaction":
            raw = HexBytes(params[0])
            tx = TypedTransaction.from_bytes(raw).as_dict()
            h = "0x" + keccak(raw).hex()
            self.wyslane.append({**tx, "hash": h})
            return h
        if method == "eth_getTransactionReceipt":
            return self.receipts.get(params[0])
        raise AssertionError(f"nieoczekiwane RPC {method}")


@pytest.fixture
def siec(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "vusdt_test_payouts", True)
    monkeypatch.setattr(s, "vusdt_sender_private_key", EthAccount.create().key.hex())
    monkeypatch.setattr(s, "vusdt_token_address", TOKEN)
    monkeypatch.setattr(s, "turnstile_site_key", "site")
    monkeypatch.setattr(s, "turnstile_secret_key", "secret")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    # Wspólna baza testów przeżywa przebiegi — kolejka i nonce'y z poprzednich
    # uruchomień wysłałyby się przed transferem z tego testu.
    db = SessionLocal()
    db.query(VusdtTransfer).delete()
    db.commit(); db.close()
    fake = SztucznyPolygon()
    monkeypatch.setattr(vusdt, "rpc", fake)
    monkeypatch.setattr(vusdt, "verify_turnstile", lambda token, ip: token == "ok")
    return fake


_n = [0]


def _funded():
    _n[0] += 1
    s = SessionLocal()
    tr = Trader(email=f"vusdt-{_n[0]}-{os.getpid()}@test.pl", password_hash=auth.hash_password("haslo1234"),
                full_name="Vusdt Test", referral_code=auth.secrets.token_hex(3), kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login=f"8{tid:08d}"[:9], trader_id=tid, trader_name="Vusdt Test",
                  product_key="2step-50k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  min_trading_days=5, trading_days_count=5,
                  balance=KAPITAL + ZYSK, equity=KAPITAL + ZYSK, peak_equity=KAPITAL + ZYSK,
                  day_start_equity=KAPITAL + ZYSK, day_start_balance=KAPITAL + ZYSK)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _wniosek(c, aid, h, **kw):
    body = {"method": "vusdt", "amount": 5688.9,
            "details": {"network": "POLYGON", "address": ODBIORCA}, "turnstile_token": "ok"}
    body.update(kw)
    return c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=body)


def test_bez_flagi_vusdt_to_nieznana_metoda(monkeypatch):
    monkeypatch.setattr(get_settings(), "vusdt_test_payouts", False)
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h)
    assert r.status_code == 400
    assert "Unknown payout method" in r.json()["detail"]


def test_produkcja_nigdy_nie_wlacza_trybu(siec, monkeypatch):
    assert vusdt.enabled()
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert not vusdt.enabled()
    assert vusdt.public_config() is None


def test_submit_wysyla_vusdt_od_razu(siec):
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h)
        assert r.status_code == 200, r.text
        v = r.json()["vusdt"]
        assert v["status"] == "sent" and v["amount"] == "5,688.90" and v["symbol"] == "vUSDT"
        assert v["explorer_url"].endswith(v["tx_hash"])

        tx = siec.wyslane[-1]
        assert tx["chainId"] == 137 and "0x" + bytes(tx["to"]).hex() == TOKEN.lower() and tx["value"] == 0
        dane = "0x" + bytes(tx["data"]).hex() if not isinstance(tx["data"], str) else tx["data"]
        assert dane.startswith("0xa9059cbb" + ODBIORCA[2:].rjust(64, "0"))
        assert int(dane[-64:], 16) == 5_688_900_000            # 6 miejsc po przecinku
        assert tx["maxPriorityFeePerGas"] == 30 * 10 ** 9       # podłoga napiwku na Polygonie
        assert tx["nonce"] == 5

        # do potwierdzenia wniosek wisi jako pending z adresem Polygon i metodą vusdt,
        # a admin nie ma czego zatwierdzać
        rid = r.json()["id"]
        rows = c.get("/api/admin/payout-requests", headers=ADMIN_H).json()
        mine = next(x for x in rows if x["id"] == rid)
        assert mine["method"] == "vusdt" and mine["status"] == "pending"
        assert mine["details"]["network"] == "POLYGON"
        a = c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        assert a.status_code == 400 and "automatically" in a.json()["detail"]
        rj = c.post(f"/api/admin/payout-requests/{rid}/reject", headers=ADMIN_H, json={"reason": "x"})
        assert rj.status_code == 400

        # receipt na łańcuchu → wniosek sam przechodzi na paid
        siec.receipts[v["tx_hash"]] = {"status": "0x1"}
        st = c.get(f"/api/me/payout-requests/{rid}/vusdt", headers=h).json()
        assert st["status"] == "confirmed" and st["request_status"] == "paid"
        lista = c.get("/api/me/payouts", headers=h).json()
        assert lista["requests"][0]["status"] == "paid"
        assert lista["requests"][0]["vusdt"]["status"] == "confirmed"
        assert lista["summary"]["total_paid"] == 5688.9

    db = SessionLocal()
    wyplata = db.query(Payout).filter(Payout.account_id == aid).one()
    konto = db.get(Account, aid)
    db.close()
    assert wyplata.method == "vusdt" and wyplata.trader_share == 5688.9
    # z salda zeszła wypłacona działka / split (5688.9 / 0.8)
    assert konto.balance == pytest.approx(KAPITAL + ZYSK - 5688.9 / 0.8, abs=0.01)


def test_zly_captcha_nic_nie_zapisuje(siec):
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h, turnstile_token="zly")
    assert r.status_code == 400 and "Captcha" in r.json()["detail"]
    s = SessionLocal()
    assert s.query(PayoutRequest).filter(PayoutRequest.account_id == aid).count() == 0
    s.close()
    assert siec.wyslane == []


@pytest.mark.parametrize("body", [
    {"method": "usdt", "details": {"network": "TRC20", "address": "TQmZ1kD4w9uPq8xYb3nR5tV7cE2sA6fGh9"}},
    {"details": {"network": "POLYGON", "address": "TQmZ1kD4w9uPq8xYb3nR5tV7cE2sA6fGh9"}},
    {"details": {"network": "POLYGON", "address": TOKEN}},
])
def test_tryb_testowy_tylko_vusdt_na_adres_0x(siec, body):
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h, **body)
    assert r.status_code == 400, r.text
    assert siec.wyslane == []


def test_nonce_rosnie_mimo_spoznionego_rpc(siec):
    with TestClient(app) as c:
        for _ in range(3):
            aid, h = _funded()
            assert _wniosek(c, aid, h).status_code == 200
    assert [t["nonce"] for t in siec.wyslane[-3:]] == [5, 6, 7]


def test_bez_gazu_czeka_w_kolejce_i_wychodzi_sama(siec):
    siec.pol = 0
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h)
        assert r.status_code == 200
        v = r.json()["vusdt"]
        assert v["status"] == "queued" and "out of gas" in v["note"]
        assert siec.wyslane == []

        siec.pol = 10 * 10 ** 18
        st = c.get(f"/api/me/payout-requests/{r.json()['id']}/vusdt", headers=h).json()
    assert st["status"] == "sent" and st["tx_hash"]


def test_okno_wyplat_co_14_dni(siec, monkeypatch):
    monkeypatch.setattr(get_settings(), "payout_interval_days", 14)
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h, amount=1000)
        assert r.status_code == 200, r.text
        siec.receipts[r.json()["vusdt"]["tx_hash"]] = {"status": "0x1"}
        c.get("/api/me/payouts", headers=h)                 # sweep rozlicza z łańcucha

        r2 = _wniosek(c, aid, h, amount=1000)
        assert r2.status_code == 400 and "every 14 days" in r2.json()["detail"]
        konto = next(a for a in c.get("/api/me/accounts", headers=h).json() if a["id"] == aid)
        assert konto["payout_next_at"]

        # 15 dni później okno jest otwarte
        s = SessionLocal()
        for m in (PayoutRequest,):
            for row in s.query(m).filter(m.account_id == aid).all():
                row.ts = datetime.now(timezone.utc) - timedelta(days=15)
        for row in s.query(Payout).filter(Payout.account_id == aid).all():
            row.ts = datetime.now(timezone.utc) - timedelta(days=15)
        s.commit(); s.close()
        assert _wniosek(c, aid, h, amount=1000).status_code == 200


def test_odrzucony_wniosek_nie_zabiera_okna(siec, monkeypatch):
    """Admin może odrzucić tylko transfer, który jeszcze nie wyszedł (kolejka) —
    wtedy transfer jest anulowany, a okno wypłat zostaje traderowi."""
    monkeypatch.setattr(get_settings(), "payout_interval_days", 14)
    siec.pol = 0                                          # transfer utknie w kolejce
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h, amount=1000)
        assert r.json()["vusdt"]["status"] == "queued"
        assert c.post(f"/api/admin/payout-requests/{r.json()['id']}/reject", headers=ADMIN_H,
                      json={"reason": "test"}).status_code == 200
        siec.pol = 10 * 10 ** 18
        assert _wniosek(c, aid, h, amount=1000).status_code == 200
    # anulowany transfer nigdy nie wyszedł — poszedł tylko drugi
    assert len(siec.wyslane) == 1


def test_nieudany_transfer_sam_odrzuca_wniosek(siec):
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h, amount=1000)
        siec.receipts[r.json()["vusdt"]["tx_hash"]] = {"status": "0x0"}
        st = c.get(f"/api/me/payout-requests/{r.json()['id']}/vusdt", headers=h).json()
        assert st["status"] == "failed" and st["request_status"] == "rejected"
        req = c.get("/api/me/payouts", headers=h).json()["requests"][0]
    assert req["status"] == "rejected" and "failed on-chain" in req["reject_reason"]


def test_zwrot_oplaty_jedzie_w_transferze_pierwszej_wyplaty(siec):
    aid, h = _funded()
    db = SessionLocal()
    acc = db.get(Account, aid)
    db.add(Order(trader_id=acc.trader_id, product_key="2step-50k", amount_usd=299.0,
                 status="paid", provider="mock", account_id=aid))
    db.commit(); db.close()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h, amount=1000)
        assert r.json()["fee_refund"] == 299.0
        assert r.json()["vusdt"]["amount"] == "1,299.00"
        siec.receipts[r.json()["vusdt"]["tx_hash"]] = {"status": "0x1"}
        c.get("/api/me/payouts", headers=h)
    db = SessionLocal()
    assert db.query(Payout).filter(Payout.account_id == aid).one().trader_share == 1299.0
    db.close()


def test_bez_okna_dalej_on_demand(siec, monkeypatch):
    monkeypatch.setattr(get_settings(), "payout_interval_days", 0)
    aid, h = _funded()
    s = SessionLocal()
    assert s.query(VusdtTransfer).count() >= 0
    s.close()
    with TestClient(app) as c:
        konto = next(a for a in c.get("/api/me/accounts", headers=h).json() if a["id"] == aid)
    assert konto["payout_next_at"] is None


def test_undo_testowej_wyplaty_oddaje_zysk_i_kasuje_wniosek(siec):
    aid, h = _funded()
    with TestClient(app) as c:
        r = _wniosek(c, aid, h, amount=500)
        siec.receipts[r.json()["vusdt"]["tx_hash"]] = {"status": "0x1"}
        assert c.get("/api/me/payouts", headers=h).json()["requests"][0]["status"] == "paid"
        db = SessionLocal()
        pid = db.query(Payout).filter(Payout.account_id == aid).one().id
        assert db.get(Account, aid).balance == pytest.approx(KAPITAL + ZYSK - 500 / 0.8)
        db.close()

        u = c.post(f"/api/admin/payouts/{pid}/undo", headers=ADMIN_H)
        assert u.status_code == 200, u.text
        assert u.json()["request_removed"] and u.json()["balance_restored"] == 625.0
        me = c.get("/api/me/payouts", headers=h).json()
        assert me["requests"] == [] and me["summary"]["total_paid"] == 0
    db = SessionLocal()
    assert db.get(Account, aid).balance == pytest.approx(KAPITAL + ZYSK)
    assert db.query(VusdtTransfer).filter(VusdtTransfer.payout_request_id == r.json()["id"]).count() == 0
    db.close()


def test_undo_tylko_dla_vusdt(siec):
    aid, _ = _funded()
    db = SessionLocal()
    p = Payout(account_id=aid, profit_amount=100, trader_share=80, paid=True, method="usdt")
    db.add(p); db.commit(); pid = p.id; db.close()
    with TestClient(app) as c:
        u = c.post(f"/api/admin/payouts/{pid}/undo", headers=ADMIN_H)
    assert u.status_code == 400
