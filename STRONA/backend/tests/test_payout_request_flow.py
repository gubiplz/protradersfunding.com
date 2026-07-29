"""Wniosek o wypłatę: kwota wybrana przez tradera + metoda + dane wypłaty.

Trader wskazuje ILE chce wypłacić (część lub całość swojej działki) oraz
DOKĄD: USDT (sieć+adres), przelew bankowy (posiadacz/IBAN/SWIFT) albo Wise
(e-mail). Bez kompletu danych wniosek nie przechodzi — admin nie miałby jak
wykonać przelewu. Duży kapitał kont trzyma procent zysku nisko, żeby nie
wypychać cudzych kont z top-20 publicznego rankingu.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.gettempdir(), "pf_test.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trader  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

KAPITAL = 200_000.0
ZYSK = 1_000.0          # split 80% => dostepne 800


def _funded(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Payout Flow", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login=f"7{tid:08d}"[:9], trader_id=tid, trader_name="Payout Flow",
                  product_key="2step-50k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  balance=KAPITAL + ZYSK, equity=KAPITAL + ZYSK,
                  peak_equity=KAPITAL + ZYSK, day_start_equity=KAPITAL + ZYSK,
                  day_start_balance=KAPITAL + ZYSK)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def test_wniosek_czesciowy_usdt_z_danymi():
    aid, h = _funded("po-usdt@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "usdt", "amount": 200,
                         "details": {"network": "trc-20", "address": "TQmZ1kD4w9uPq8xYb3nR5tV7cE2sA6fGh9"}})
        assert r.status_code == 200, r.text
        assert r.json()["trader_share"] == 200.0

        # admin widzi metode i dane wyplaty przy wniosku
        rows = c.get("/api/admin/payout-requests", headers=ADMIN_H).json()
        mine = next(x for x in rows if x["account_login"].startswith("7"))
        assert mine["method"] == "usdt"
        assert mine["details"]["network"] == "TRC20"
        assert mine["details"]["address"].startswith("TQmZ1kD4")

        # trader widzi metode na swojej liscie wnioskow
        me = c.get("/api/me/payouts", headers=h).json()
        assert me["requests"][0]["method"] == "usdt"


def test_kwota_ponad_dostepna_dzialke_odrzucona():
    aid, h = _funded("po-za-duzo@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "wise", "amount": 800.01,
                         "details": {"email": "trader@example.com"}})
    assert r.status_code == 400
    assert "exceeds" in r.json()["detail"]


def test_brak_danych_wyplaty_odrzucony():
    aid, h = _funded("po-bez-danych@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "bank", "amount": 100})
        assert r.status_code == 400
        assert "account holder" in r.json()["detail"]

        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "usdt", "amount": 100,
                         "details": {"network": "SOLANA", "address": "x" * 30}})
        assert r.status_code == 400 and "network" in r.json()["detail"].lower()

        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "wise", "amount": 100, "details": {"email": "nie-mail"}})
        assert r.status_code == 400


def test_approve_czesciowej_wyplaty_nie_zeruje_konta_do_startu():
    """$200 z dostepnych $800 => z salda schodzi 200/0.8 = $250 profitu,
    a NIE caly zysk — stary reset do salda startowego zabieralby traderowi
    niewyplacone $750 dzialki."""
    aid, h = _funded("po-partial@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "bank", "amount": 200,
                         "details": {"holder": "Payout Flow", "iban": "PL61109010140000071219812874",
                                     "swift": "WBKPPLPP", "bank_name": "Test Bank"}})
        assert r.status_code == 200
        req_id = r.json()["id"]
        r = c.post(f"/api/admin/payout-requests/{req_id}/approve", headers=ADMIN_H)
        assert r.status_code == 200
    s = SessionLocal()
    acc = s.get(Account, aid); balans = acc.balance; s.close()
    assert balans == KAPITAL + ZYSK - 250.0
