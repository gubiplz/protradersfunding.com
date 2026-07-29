"""Oferta 2026-07-29 (2-Step + Instant, 10k–2M), add-on Weekend Trading
i system resetu hasla."""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)


def _trader(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("stare-haslo1"),
                full_name="Offer Tester", referral_code=email[:6].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def test_oferta_2step_i_instant_10k_do_2m_bez_1step():
    ps = client.get("/api/products").json()
    active = {p["key"]: p for p in ps if p["price_usd"] > 0}
    assert active["2step-10k"]["price_usd"] == 99
    assert active["2step-2m"]["price_usd"] == 5999
    assert active["2step-2m"]["account_size"] == 2_000_000
    assert active["instant-10k"]["price_usd"] == 119
    assert active["instant-2m"]["price_usd"] == 7499
    assert active["2step-50k"]["profit_target_p1"] == 10
    assert active["2step-50k"]["profit_split_pct"] == 90
    assert active["instant-50k"]["max_overall_loss_pct"] == 8
    assert active["instant-50k"]["profit_split_pct"] == 70
    # test_business dodaje wlasny produkt test-1step-* (mechanika silnika) — nie liczy sie do oferty
    assert not any(p["steps"] == 1 and not p["key"].startswith("test-")
                   for p in ps if p["price_usd"] > 0), "1-step znikl z oferty"


def test_weekend_addon_dolicza_199_i_laduje_na_koncie():
    tid = _trader("weekend@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-10k", None, weekend_trading=True)
    order = s.get(Order, res["order_id"])
    assert order.amount_usd == 99 + 199
    assert order.weekend_trading is True
    done = billing.mock_complete(s, order.id, tid)
    acc = s.get(Account, done["account_id"])
    assert acc.weekend_trading is True
    s.close()


def test_kupon_endpoint_publiczny():
    r = client.get("/api/coupon/welcome10")
    assert r.status_code == 200 and r.json()["pct"] == 10.0
    assert client.get("/api/coupon/NIE-MA-TAKIEGO").status_code == 404


def test_reset_hasla_dziala_a_stare_haslo_przestaje():
    tid = _trader("reset@test.pl")
    # nieistniejacy email tez dostaje 200 — brak enumeracji kont
    r = client.post("/api/auth/forgot", json={"email": "nie-ma@test.pl"})
    assert r.status_code == 200
    r = client.post("/api/auth/forgot", json={"email": "reset@test.pl"})
    assert r.status_code == 200

    token = auth.make_reset_token(tid)
    r = client.post("/api/auth/reset", json={"token": token, "password": "krotkie"})
    assert r.status_code == 400   # min. 8 znakow
    r = client.post("/api/auth/reset", json={"token": token, "password": "nowe-haslo-123"})
    assert r.status_code == 200

    ok = client.post("/api/auth/login", json={"email": "reset@test.pl", "password": "nowe-haslo-123"})
    assert ok.status_code == 200
    stare = client.post("/api/auth/login", json={"email": "reset@test.pl", "password": "stare-haslo1"})
    assert stare.status_code in (400, 401, 403)

    r = client.post("/api/auth/reset", json={"token": "zepsuty-token", "password": "cokolwiek-123"})
    assert r.status_code == 400
