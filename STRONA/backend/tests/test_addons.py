"""Add-ony checkoutu: Split Boost (+10 pp splitu, tylko Instant, $149)
i Express Payout (wnioski na początek kolejki przeglądu, $49).

Walidacja Instant-only siedzi w billing.compute_price — goły POST omijający
modal też dostaje 400. Provisioning przenosi boost na `profit_split_pct`
konta (70→80), a flaga express na koncie ustawia pill i sortowanie w panelu.
Ceny planów z `_CATALOG` pozostają nietknięte (baza wiedzy Forex Passing).
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_addons.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

INSTANT_25K = 369.0   # z _CATALOG — nie zmieniać (patrz docstring)


def _trader(email):
    """Profil z kompletem danych MT5, żeby checkout nie żądał formularza."""
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Addon Tester", referral_code=email.split("@")[0].upper(),
                kyc_status="approved", first_name="Addon", last_name="Tester",
                phone="+48555123456", phone_country="PL")
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def test_preview_dolicza_addony():
    _, h = _trader("addon-preview@test.pl")
    q = client.get("/api/checkout/preview?product_key=instant-25k"
                   "&split_boost=true&express=true", headers=h).json()
    assert q["split_boost_fee_usd"] == catalog.SPLIT_BOOST_ADDON_USD
    assert q["express_payout_fee_usd"] == catalog.EXPRESS_PAYOUT_ADDON_USD
    assert q["total_due_usd"] == INSTANT_25K + 149.0 + 49.0
    bez = client.get("/api/checkout/preview?product_key=instant-25k", headers=h).json()
    assert bez["split_boost_fee_usd"] == 0.0 and bez["express_payout_fee_usd"] == 0.0
    assert bez["total_due_usd"] == INSTANT_25K


def test_split_boost_tylko_dla_instant():
    _, h = _trader("addon-2step@test.pl")
    r = client.get("/api/checkout/preview?product_key=2step-25k&split_boost=true",
                   headers=h)
    assert r.status_code == 400 and "Instant Funding" in r.json()["detail"]
    r = client.post("/api/checkout", headers=h,
                    json={"product_key": "2step-25k", "split_boost": True})
    assert r.status_code == 400 and "Instant Funding" in r.json()["detail"]
    # express payout nie ma ograniczenia planu
    ok = client.get("/api/checkout/preview?product_key=2step-25k&express=true", headers=h)
    assert ok.status_code == 200 and ok.json()["express_payout_fee_usd"] == 49.0


def test_zakup_prowizjonuje_boost_i_flage_express():
    _, h = _trader("addon-zakup@test.pl")
    r = client.post("/api/checkout", headers=h,
                    json={"product_key": "instant-25k",
                          "split_boost": True, "express_payout": True})
    assert r.status_code == 200
    oid = r.json()["order_id"]

    s = SessionLocal()
    order = s.get(Order, oid)
    assert order.addon_split_boost and order.addon_express_payout
    assert order.amount_usd == INSTANT_25K + 149.0 + 49.0
    s.close()

    done = client.post(f"/api/checkout/{oid}/mock-complete", headers=h)
    assert done.status_code == 200
    s = SessionLocal()
    acc = s.get(Account, done.json()["account_id"])
    assert acc.profit_split_pct == 80          # 70 z katalogu + 10 pp boostu
    assert acc.express_payout is True
    s.close()


def test_zakup_bez_addonow_zostaje_przy_katalogowym_splicie():
    _, h = _trader("addon-goly@test.pl")
    oid = client.post("/api/checkout", headers=h,
                      json={"product_key": "instant-25k"}).json()["order_id"]
    done = client.post(f"/api/checkout/{oid}/mock-complete", headers=h)
    s = SessionLocal()
    acc = s.get(Account, done.json()["account_id"])
    assert acc.profit_split_pct == 70 and acc.express_payout is False
    s.close()


# --- express w widokach wypłat ------------------------------------------- #
KAPITAL = 100_000.0

def _funded(tid, login, express):
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Addon Tester",
                  product_key="2step-100k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  balance=KAPITAL + 1_000, equity=KAPITAL + 1_000,
                  peak_equity=KAPITAL + 1_000, day_start_equity=KAPITAL + 1_000,
                  day_start_balance=KAPITAL + 1_000, express_payout=express)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


WNIOSEK = {"method": "wise", "amount": 100, "details": {"email": "trader@example.com"}}


def test_express_pending_idzie_na_gore_kolejki_admina():
    tid, h = _trader("addon-kolejka@test.pl")
    aid_express = _funded(tid, "955500001", express=True)
    aid_zwykle = _funded(tid, "955500002", express=False)
    with TestClient(app) as c:
        # express NAJPIERW (niższe id) — domyślny porządek „najnowsze pierwsze"
        # chowałby go pod zwykłym wnioskiem, sortowanie ma go wyciągnąć na górę
        req_e = c.post(f"/api/accounts/{aid_express}/payout-request", headers=h,
                       json=WNIOSEK).json()["id"]
        req_n = c.post(f"/api/accounts/{aid_zwykle}/payout-request", headers=h,
                       json=WNIOSEK).json()["id"]
        assert req_n > req_e

        me = c.get("/api/me/payouts", headers=h).json()
        po_id = {w["id"]: w for w in me["requests"]}
        assert po_id[req_e]["express"] is True
        assert po_id[req_n]["express"] is False

        lista = c.get("/api/admin/payout-requests", headers=ADMIN_H).json()
        assert lista[0]["id"] == req_e and lista[0]["express"] is True
