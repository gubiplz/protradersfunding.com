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
    # referral_code z pełnego prefiksu maila — obcięcie do 8 znaków kolidowało
    # przy 10+ traderach w pliku (credits1 vs credits12 -> "CREDITS1")
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Credit Tester", referral_code=email.split("@")[0].upper(),
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


def test_grant_wysyla_powiadomienie_i_push(monkeypatch):
    """Nadanie kredytów = wpis w dzwonku + push (mail idzie tą samą bramką)."""
    from app import push
    from app.models import Notification, PushSubscription

    tid = _trader()
    s = SessionLocal()
    s.add(PushSubscription(trader_id=tid, endpoint=f"https://push.test/credits{tid}",
                           p256dh="pdh", auth="au"))
    s.commit(); s.close()

    monkeypatch.setattr(push.settings, "vapid_private_key", "test-priv")
    monkeypatch.setattr(push.settings, "vapid_public_key", "test-pub")
    dostarczone = []
    monkeypatch.setattr(push, "_deliver", lambda info, payload: dostarczone.append(payload))

    r = client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                    json={"amount": 120, "note": "Promo"})
    assert r.status_code == 200
    s = SessionLocal()
    wpis = (s.query(Notification)
            .filter(Notification.trader_id == tid,
                    Notification.event == "credits_granted").one())
    assert "$120" in wpis.title and "store" in wpis.url
    s.close()
    assert len(dostarczone) == 1 and "store" in dostarczone[0]


def test_korekta_w_dol_bez_powiadomienia(monkeypatch):
    """Zabranie kredytów (kwota ujemna) nie generuje 'gratulacji'."""
    from app.models import Notification

    tid = _trader(credits=200)
    r = client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                    json={"amount": -50})
    assert r.status_code == 200
    s = SessionLocal()
    assert (s.query(Notification)
            .filter(Notification.trader_id == tid).count()) == 0
    s.close()


def test_preview_liczy_tak_samo_jak_checkout():
    """GET /api/checkout/preview = dokładnie ta sama matematyka co realny
    checkout (kupon -> weekend -> kredyty na końcu)."""
    tid = _trader(credits=100)
    token = auth.make_token(tid)
    r = client.get("/api/checkout/preview?product_key=2step-25k&coupon=WELCOME10"
                   "&weekend=1&use_credits=1",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    q = r.json()
    # 249 * 0.9 = 224.1; +199 weekend = 423.1; -100 kredytu = 323.1
    assert q["plan_price_usd"] == 249 and q["discount_usd"] == 24.9
    assert q["weekend_fee_usd"] == 199 and q["credits_used"] == 100
    assert q["total_due_usd"] == 323.1
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-25k", "WELCOME10", weekend_trading=True)
    order = s.get(Order, res["order_id"])
    assert order.amount_usd == q["total_due_usd"] and order.credits_used == 100
    s.close()


def test_use_credits_false_zostawia_saldo():
    """Trader może zostawić kredyty na później — checkout ich wtedy nie tyka."""
    tid = _trader(credits=100)
    token = auth.make_token(tid)
    q = client.get("/api/checkout/preview?product_key=2step-25k&use_credits=0",
                   headers={"Authorization": f"Bearer {token}"}).json()
    assert q["credits_used"] == 0 and q["total_due_usd"] == 249
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-25k", None, use_credits=False)
    order_id = res["order_id"]
    order = s.get(Order, order_id)
    assert order.amount_usd == 249 and order.credits_used == 0
    billing.mock_complete(s, order_id, tid)
    s.close()
    assert _saldo(tid) == 100          # saldo nietknięte po domknięciu płatności
    s = SessionLocal()
    assert (s.query(CreditLedger)
            .filter(CreditLedger.trader_id == tid, CreditLedger.amount < 0).count()) == 0
    s.close()


def test_preview_guard_minimum_stripe():
    """Resztówka poniżej $0.50 zostaje na saldzie zamiast wywracać Stripe."""
    tid = _trader(credits=98.8)
    token = auth.make_token(tid)
    q = client.get("/api/checkout/preview?product_key=2step-10k",   # $99
                   headers={"Authorization": f"Bearer {token}"}).json()
    assert q["credits_used"] == 98.5 and q["total_due_usd"] == 0.5


def test_api_me_credits_saldo_i_historia():
    tid = _trader()
    token = auth.make_token(tid)
    client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                json={"amount": 150, "note": "Contest prize"})
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-10k", None)   # $99, w pełni pokryte
    assert res.get("free") is True
    s.close()
    r = client.get("/api/me/credits", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    d = r.json()
    assert d["balance_usd"] == 51
    kwoty = [w["amount"] for w in d["ledger"]]
    assert kwoty == [-99, 150]                       # najnowsze pierwsze
    assert d["ledger"][1]["note"] == "Contest prize"
    assert d["ledger"][0]["order_id"] == res["order_id"]
    # cudzy ledger niewidoczny, bez tokenu 401
    obcy = _trader()
    obcy_token = auth.make_token(obcy)
    assert client.get("/api/me/credits",
                      headers={"Authorization": f"Bearer {obcy_token}"}).json()["ledger"] == []
    assert client.get("/api/me/credits").status_code == 401


def test_pref_updates_off_wycisza_kanal(monkeypatch):
    """notify_updates=False => ani wpisu w dzwonku, ani pusha przy grancie."""
    from app import push
    from app.models import Notification

    tid = _trader()
    s = SessionLocal()
    s.get(Trader, tid).notify_updates = False
    s.commit(); s.close()

    monkeypatch.setattr(push.settings, "vapid_private_key", "test-priv")
    monkeypatch.setattr(push.settings, "vapid_public_key", "test-pub")
    dostarczone = []
    monkeypatch.setattr(push, "_deliver", lambda info, payload: dostarczone.append(payload))

    r = client.post(f"/api/admin/traders/{tid}/credits", headers=ADMIN_H,
                    json={"amount": 80})
    assert r.status_code == 200
    s = SessionLocal()
    assert (s.query(Notification)
            .filter(Notification.trader_id == tid).count()) == 0
    s.close()
    assert dostarczone == []
