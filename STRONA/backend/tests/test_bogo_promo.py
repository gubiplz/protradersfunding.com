"""Promocja Buy 1 Get 1 Free: globalny przełącznik w panelu, stempel na
zamówieniu, decyzja per zamówienie i drugie konto po opłaceniu."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Order, Product, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(1000))


def _trader():
    s = SessionLocal()
    tr = Trader(email=f"bogo{next(LICZNIK)}@test.pl",
                password_hash=auth.hash_password("haslo1234"),
                full_name="Bogo Tester", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid


def _product():
    s = SessionLocal()
    if not s.query(Product).filter(Product.key == "bogo-25k").first():
        s.add(Product(key="bogo-25k", label="Bogo 25K", account_size=25_000,
                      steps=2, price_usd=249, profit_target_p1=8, profit_target_p2=5,
                      max_daily_loss_pct=5, max_overall_loss_pct=10, drawdown_type="trailing",
                      min_trading_days=3, profit_split_pct=80, max_lots=6, active=True))
        s.commit()
    s.close()


def _bogo(on: bool):
    r = client.post("/api/admin/bogo-promo", json={"enabled": on}, headers=ADMIN)
    assert r.status_code == 200


def test_globalny_przelacznik_i_pasek_na_stronie():
    _bogo(True)
    assert client.get("/api/admin/bogo-promo", headers=ADMIN).json()["enabled"] is True
    assert "bogoBar" in client.get("/").text

    _bogo(False)
    assert client.get("/api/admin/bogo-promo", headers=ADMIN).json()["enabled"] is False
    assert "bogoBar" not in client.get("/").text


def test_checkout_stempluje_bogo_a_platnosc_daje_dwa_konta():
    """Zakup przez checkout przy włączonej promocji: jedno płacone zamówienie,
    po opłaceniu DWA konta tego samego rozmiaru (drugie jako grant za $0)."""
    _product()
    tid = _trader()
    _bogo(True)
    try:
        s = SessionLocal()
        tr = s.get(Trader, tid)
        wynik = billing.create_checkout(s, tr, "bogo-25k", None)   # tryb mock
        oid = wynik["order_id"]
        assert s.get(Order, oid).bogo is True

        billing.mock_complete(s, oid, tid)

        konta = s.query(Account).filter(Account.trader_id == tid).all()
        assert len(konta) == 2
        assert all(a.initial_balance == 25_000 for a in konta)
        assert sorted(a.source for a in konta) == ["grant", "purchase"]
        gratis = (s.query(Order).filter(Order.trader_id == tid, Order.provider == "grant").one())
        assert gratis.amount_usd == 0.0
        assert gratis.coupon == "Buy 1 Get 1 Free"
        assert gratis.status == "paid" and gratis.account_id
        s.close()
    finally:
        _bogo(False)


def test_wylaczona_promocja_nie_stempluje_i_daje_jedno_konto():
    _product()
    tid = _trader()
    s = SessionLocal()
    tr = s.get(Trader, tid)
    wynik = billing.create_checkout(s, tr, "bogo-25k", None)
    assert s.get(Order, wynik["order_id"]).bogo is False
    billing.mock_complete(s, wynik["order_id"], tid)
    assert s.query(Account).filter(Account.trader_id == tid).count() == 1
    s.close()


def test_reczne_zamowienie_z_karty_leada_i_decyzja_per_zamowienie():
    """Zamówienie ręczne z checkboxem BOGO + przełącznik per zamówienie:
    wyłączony globalnie, włączony dla tego jednego leada."""
    _product()
    r = client.post("/api/admin/orders", headers=ADMIN, json={
        "email": f"lead-bogo{next(LICZNIK)}@test.pl", "product_key": "bogo-25k",
        "bogo": False, "flag": "", "notify_trader": False})
    assert r.status_code == 200 and r.json()["bogo"] is False
    oid = r.json()["id"]

    # Decyzja admina w zakładce leadów: to zamówienie MA dostać drugie konto.
    r = client.post(f"/api/admin/orders/{oid}/bogo", json={"bogo": True}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["bogo"] is True
    assert any(o["id"] == oid and o["bogo"] for o in client.get("/api/admin/orders", headers=ADMIN).json())

    r = client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    assert r.status_code == 200

    s = SessionLocal()
    o = s.get(Order, oid)
    konta = s.query(Account).filter(Account.trader_id == o.trader_id).all()
    assert len(konta) == 2
    assert sorted(a.source for a in konta) == ["grant", "purchase"]
    s.close()

    # Po opłaceniu decyzji nie da się już zmienić — provisioning był jednorazowy.
    r = client.post(f"/api/admin/orders/{oid}/bogo", json={"bogo": False}, headers=ADMIN)
    assert r.status_code == 400
