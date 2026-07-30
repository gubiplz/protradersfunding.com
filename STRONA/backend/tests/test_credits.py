"""Kredyty sklepowe: admin zasila saldo tradera, a checkout automatycznie
odlicza je od ceny. Saldo schodzi dopiero przy DOMKNIĘCIU płatności — porzucony
checkout nie pali środków. Zakup w całości pokryty kredytami omija Stripe.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, CreditLedger, Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)

ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(1000))


def _trader(credits: float = 0.0) -> int:
    email = f"credits{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Credit Tester", referral_code=email[:8].upper(),
                credits_usd=credits)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _saldo(tid: int) -> float:
    s = SessionLocal()
    v = round(float(s.get(Trader, tid).credits_usd or 0), 2)
    s.close()
    return v


def test_zasilenie_wymaga_admina():
    tid = _trader()
    bez = client.post(f"/api/admin/traders/{tid}/credits", json={"amount": 100})
    assert bez.status_code in (401, 403)


def test_admin_zasila_i_ledger_pamieta():
    tid = _trader()
    r = client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                    json={"amount": 150, "note": "Contest prize"})
    assert r.status_code == 200 and r.json()["credits_usd"] == 150
    # korekta w dol dziala, ponizej zera nie
    r2 = client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                     json={"amount": -50})
    assert r2.json()["credits_usd"] == 100
    zle = client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                      json={"amount": -500})
    assert zle.status_code == 400
    zero = client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                       json={"amount": 0})
    assert zero.status_code == 400
    s = SessionLocal()
    wpisy = s.query(CreditLedger).filter(CreditLedger.trader_id == tid).all()
    assert [w.amount for w in wpisy] == [150, -50]
    assert wpisy[0].note == "Contest prize"
    s.close()
    assert _saldo(tid) == 100


def test_checkout_odlicza_kredyty_a_saldo_schodzi_przy_domknieciu():
    tid = _trader(credits=100)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-25k", None)   # $249
    order = s.get(Order, res["order_id"])
    assert order.amount_usd == 149 and order.credits_used == 100
    s.close()
    # przed domknieciem platnosci saldo NIE schodzi (porzucony checkout = 0 strat)
    assert _saldo(tid) == 100
    s = SessionLocal()
    done = billing.mock_complete(s, order.id, tid)
    acc = s.get(Account, done["account_id"])
    assert acc.initial_balance == 25_000
    s.close()
    assert _saldo(tid) == 0
    s = SessionLocal()
    zuzycie = (s.query(CreditLedger)
               .filter(CreditLedger.trader_id == tid, CreditLedger.amount < 0).one())
    assert zuzycie.amount == -100 and zuzycie.order_id == order.id
    s.close()


def test_pelne_pokrycie_kredytami_omija_platnosc():
    tid = _trader(credits=500)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-10k", None)   # $99 < 500
    assert res.get("free") is True                            # konto od razu, bez Stripe
    order = s.get(Order, res["order_id"])
    assert order.status == "paid" and order.amount_usd == 0 and order.credits_used == 99
    s.close()
    assert _saldo(tid) == 401


def test_kolejnosc_kupon_potem_kredyty():
    tid = _trader(credits=50)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-50k", "WELCOME10")   # 349*0.9=314.1
    order = s.get(Order, res["order_id"])
    assert order.amount_usd == round(314.1 - 50, 2) and order.credits_used == 50
    s.close()


def test_auth_me_pokazuje_saldo():
    tid = _trader(credits=75)
    token = auth.make_token(tid)
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["credits_usd"] == 75
    lista = client.get("/api/admin/traders", headers=ADMIN_H).json()
    assert next(t for t in lista if t["id"] == tid)["credits_usd"] == 75
