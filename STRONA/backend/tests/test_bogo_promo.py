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
from app.models import Account, Order, PayoutRequest, Product, Trader  # noqa: E402

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


def test_grant_bogo_nie_zwraca_oplaty_drugi_raz():
    """Zamówienie grantowe z `bogo_paid_key` nosi cenę OPŁACONEGO tieru (dla
    faktury), więc pierwsza wypłata z konta grantowego nie może traktować jej
    jak własnej opłaty — zwrot należy się raz, przy wypłacie z konta kupionego."""
    _product()
    tid = _trader()
    r = client.post("/api/admin/grant", headers=ADMIN, json={
        "trader_id": tid, "product_key": "bogo-25k",
        "note": "BOGO promotion", "bogo_paid_key": "bogo-25k", "funded": True})
    assert r.status_code == 200, r.text
    aid = r.json()["account_id"]

    s = SessionLocal()
    gratis = s.query(Order).filter(Order.trader_id == tid, Order.provider == "grant").one()
    assert gratis.amount_usd == 249 and gratis.status == "paid"
    acc = s.get(Account, aid)
    acc.balance = acc.equity = acc.initial_balance + 1_000
    pr = PayoutRequest(account_id=aid, trader_id=tid, profit_amount=1_000.0,
                       trader_share=800.0, method="wise", details="{}", status="pending")
    s.add(pr); s.commit()
    req_id = pr.id
    s.close()

    r = client.post(f"/api/admin/payout-requests/{req_id}/approve", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["fee_refund"] == 0.0
    assert r.json()["total_paid"] == 800.0


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


def test_awaria_grantu_zostawia_trwaly_slad(monkeypatch):
    """Padnięty grant BOGO nie może zniknąć bez śladu: zamówienie zostaje
    opłacone (klient ma pierwsze konto), a flaga bogo_grant_failed świeci
    w panelu, dopóki admin nie przyzna drugiego konta ręcznie."""
    _product()
    r = client.post("/api/admin/orders", headers=ADMIN, json={
        "email": f"bogo-fail{next(LICZNIK)}@test.pl", "product_key": "bogo-25k",
        "bogo": True, "flag": "", "notify_trader": False})
    assert r.status_code == 200
    oid = r.json()["id"]

    def _pada(*a, **kw):
        raise RuntimeError("pusta pula MT5")
    monkeypatch.setattr(billing, "grant_challenge", _pada)

    r = client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    assert r.status_code == 200
    d = r.json()
    assert d["bogo"] is True and d["bogo_grant_ok"] is False and d["account_id"]

    s = SessionLocal()
    o = s.get(Order, oid)
    assert o.status == "paid" and o.flag == "bogo_grant_failed"
    assert s.query(Account).filter(Account.trader_id == o.trader_id).count() == 1
    s.close()

    wiersz = next(x for x in client.get("/api/admin/orders", headers=ADMIN).json()
                  if x["id"] == oid)
    assert wiersz["flag"] == "bogo_grant_failed"


def test_stats_nie_licza_grantow_jako_sprzedazy():
    """Grant $0 to nie sprzedaż: orders_paid w /api/stats liczy tylko płatne."""
    _product()
    tid = _trader()
    _bogo(True)
    try:
        s = SessionLocal()
        tr = s.get(Trader, tid)
        oid = billing.create_checkout(s, tr, "bogo-25k", None)["order_id"]
        billing.mock_complete(s, oid, tid)
        paid_sale = s.query(Order).filter(Order.status == "paid",
                                          Order.provider != "grant").count()
        paid_all = s.query(Order).filter(Order.status == "paid").count()
        s.close()
    finally:
        _bogo(False)
    assert paid_all > paid_sale
    assert client.get("/api/stats", headers=ADMIN).json()["orders_paid"] == paid_sale


def test_strona_platnosci_i_json_partnera_mowia_o_bogo(monkeypatch):
    """Obietnica z Telegramu musi stać przy kwocie: /pay/<token> i partnerski
    JSON /api/pay/<token> niosą BOGO, a mail „awaiting payment" wymienia bonus."""
    # Env nie wystarczy: get_settings() jest cache'owane i w pełnym suicie
    # wcześniejszy moduł buduje Settings bez sekretu partnera.
    monkeypatch.setattr(get_settings(), "partner_api_token", "partner-test-token", raising=False)
    _product()
    r = client.post("/api/admin/orders", headers=ADMIN, json={
        "email": f"lead-pay{next(LICZNIK)}@test.pl", "product_key": "bogo-25k",
        "bogo": True, "flag": "", "notify_trader": False})
    assert r.status_code == 200 and r.json()["bogo"] is True
    oid = r.json()["id"]

    token = client.post(f"/api/admin/orders/{oid}/pay-link", headers=ADMIN
                        ).json()["url"].rsplit("/", 1)[-1]

    html = client.get(f"/pay/{token}").text
    assert "Buy 1 Get 1 Free" in html and "second account of the same size" in html

    d = client.get(f"/api/pay/{token}",
                   headers={"X-Partner-Token": "partner-test-token"}).json()
    assert d["bogo"] is True

    from app import notify  # noqa: E402
    subject, body = notify._render("order_awaiting_payment", {
        "name": "T", "product_label": "Bogo 25K", "amount": 249,
        "reference": f"PTF-{oid}", "bogo": True})
    assert "second account of the same size" in body

    # Zamówienie bez stempla nie obiecuje niczego.
    r = client.post("/api/admin/orders", headers=ADMIN, json={
        "email": f"lead-pay{next(LICZNIK)}@test.pl", "product_key": "bogo-25k",
        "bogo": False, "flag": "", "notify_trader": False})
    token2 = client.post(f"/api/admin/orders/{r.json()['id']}/pay-link", headers=ADMIN
                         ).json()["url"].rsplit("/", 1)[-1]
    assert "Buy 1 Get 1 Free" not in client.get(f"/pay/{token2}").text
