"""Operacje panelu admina: drill-down telemetrii, flagi płatności zamówień,
cofnięcie decyzji KYC, inbox i powiadomienia dla adminów."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from datetime import datetime, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, telemetry  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Account, Notification, Order, Product, SupportTicket,  # noqa: E402
                        TicketMessage, Trader)

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(1000))


def _trader(is_admin=False):
    s = SessionLocal()
    tr = Trader(email=f"ops{next(LICZNIK)}@test.pl",
                password_hash=auth.hash_password("haslo1234"),
                full_name="Ops Tester", referral_code=auth.secrets.token_hex(3),
                is_admin=is_admin)
    s.add(tr); s.commit()
    tid, email = tr.id, tr.email
    s.close()
    return tid, email


def _product():
    s = SessionLocal()
    if not s.query(Product).filter(Product.key == "ops-25k").first():
        s.add(Product(key="ops-25k", label="Ops 25K", account_size=25_000,
                      steps=2, price_usd=249, profit_target_p1=8, profit_target_p2=5,
                      max_daily_loss_pct=5, max_overall_loss_pct=10, drawdown_type="trailing",
                      min_trading_days=3, profit_split_pct=80, max_lots=6, active=True))
        s.commit()
    s.close()


def _order(tid):
    s = SessionLocal()
    o = Order(trader_id=tid, product_key="ops-25k", amount_usd=249, status="pending")
    s.add(o); s.commit()
    oid = o.id
    s.close()
    return oid


def test_telemetry_events_drilldown_filtry():
    tid, email = _trader()
    telemetry.track("ops_klik", tid, widok="store")
    telemetry.track("ops_klik", tid)
    telemetry.track("ops_inne", tid)

    r = client.get("/api/admin/telemetry/events?name=ops_klik", headers=ADMIN)
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["name"] for i in items} == {"ops_klik"}
    assert any(i["email"] == email for i in items)
    assert any(i["props"] and "store" in i["props"] for i in items)

    r2 = client.get(f"/api/admin/telemetry/events?trader_id={tid}", headers=ADMIN)
    names = {i["name"] for i in r2.json()["items"]}
    assert {"ops_klik", "ops_inne"} <= names

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r3 = client.get(f"/api/admin/telemetry/events?day={day}&name=ops_inne", headers=ADMIN)
    assert len(r3.json()["items"]) == 1


def test_order_flag_i_mark_paid():
    _product()
    tid, _ = _trader()
    oid = _order(tid)

    r = client.post(f"/api/admin/orders/{oid}/flag",
                    json={"flag": "awaiting_crypto"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["flag"] == "awaiting_crypto"
    mine = next(o for o in client.get("/api/admin/orders", headers=ADMIN).json()
                if o["id"] == oid)
    assert mine["flag"] == "awaiting_crypto" and mine["status"] == "pending"

    assert client.post(f"/api/admin/orders/{oid}/flag",
                       json={"flag": "zla"}, headers=ADMIN).status_code == 400

    r2 = client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    assert r2.status_code == 200
    aid = r2.json()["account_id"]
    assert aid

    s = SessionLocal()
    o = s.get(Order, oid)
    assert o.status == "paid" and o.paid_at is not None
    assert o.flag is None and o.account_id == aid           # flaga znika po oplaceniu
    assert s.get(Account, aid).trader_id == tid
    s.close()

    # idempotencja: drugi klik nie tworzy drugiego konta
    assert client.post(f"/api/admin/orders/{oid}/mark-paid",
                       headers=ADMIN).json().get("already") is True
    assert client.post("/api/admin/orders/999999/mark-paid",
                       headers=ADMIN).status_code == 404


def test_order_mark_failed_z_powodem():
    """Mark failed ubija nieopłacone zamówienie z powodem; opłaconych nie
    rusza, a Mark paid po failed czyści powód (recovery)."""
    _product()
    tid, _ = _trader()
    oid = _order(tid)

    # flaga crypto znika razem z oznaczeniem failed
    client.post(f"/api/admin/orders/{oid}/flag",
                json={"flag": "awaiting_crypto"}, headers=ADMIN)
    r = client.post(f"/api/admin/orders/{oid}/mark-failed",
                    json={"reason": "Payment never arrived"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    mine = next(o for o in client.get("/api/admin/orders", headers=ADMIN).json()
                if o["id"] == oid)
    assert mine["status"] == "failed" and mine["fail_reason"] == "Payment never arrived"
    assert mine["flag"] is None

    # recovery: płatność jednak doszła — powód znika
    r2 = client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    assert r2.status_code == 200 and r2.json()["account_id"]
    s = SessionLocal()
    o = s.get(Order, oid)
    assert o.status == "paid" and o.fail_reason is None
    s.close()

    # opłaconego nie da się ubić
    assert client.post(f"/api/admin/orders/{oid}/mark-failed",
                       json={"reason": "x"}, headers=ADMIN).status_code == 400
    assert client.post("/api/admin/orders/999999/mark-failed",
                       json={"reason": "x"}, headers=ADMIN).status_code == 404


def test_kyc_reset_cofa_decyzje():
    tid, _ = _trader()
    s = SessionLocal()
    tr = s.get(Trader, tid)
    tr.kyc_status = "approved"
    tr.kyc_reviewed_at = datetime.now(timezone.utc)
    s.commit(); s.close()

    assert client.post(f"/api/admin/kyc/{tid}/reset", headers=ADMIN).status_code == 200

    d = client.get("/api/admin/kyc", headers=ADMIN).json()
    assert any(t["trader_id"] == tid for t in d["pending"])
    assert all(t["trader_id"] != tid for t in d["history"])

    # nie ma juz decyzji do cofniecia
    assert client.post(f"/api/admin/kyc/{tid}/reset", headers=ADMIN).status_code == 400
    assert client.post("/api/admin/kyc/999999/reset", headers=ADMIN).status_code == 404


def test_admin_inbox_agreguje_kolejki():
    _product()
    tid, email = _trader()
    _order(tid)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    tr.kyc_status = "pending"
    tr.kyc_submitted_at = datetime.now(timezone.utc)
    t = SupportTicket(trader_id=tid, subject="Inbox test")
    s.add(t); s.flush()
    s.add(TicketMessage(ticket_id=t.id, author="trader", body="hej"))
    s.commit(); s.close()

    items = client.get("/api/admin/inbox", headers=ADMIN).json()["items"]
    assert {"order", "kyc", "ticket"} <= {i["type"] for i in items}
    for i in items:
        assert i["view"] in ("orders", "kyc", "payouts", "tickets")
        assert i["ts"] and i["title"]
    assert any(i["type"] == "ticket" and "Inbox test" in i["title"] for i in items)


def test_admin_delete_trader_zwalnia_email(monkeypatch):
    from app import push
    monkeypatch.setattr(push, "send_to_trader", lambda *a, **k: 0)
    _product()
    tid, email = _trader()
    oid = _order(tid)
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)  # konto + telemetria
    s = SessionLocal()
    tr = s.get(Trader, tid)
    tr.kyc_status = "pending"
    t = SupportTicket(trader_id=tid, subject="Do usuniecia")
    s.add(t); s.flush()
    s.add(TicketMessage(ticket_id=t.id, author="trader", body="czesc"))
    s.add(Notification(trader_id=tid, event="x", title="x"))
    s.commit(); s.close()

    r = client.delete(f"/api/admin/traders/{tid}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["email"] == email and r.json()["accounts_removed"] == 1

    s = SessionLocal()
    assert s.get(Trader, tid) is None
    assert s.query(Account).filter(Account.trader_id == tid).count() == 0
    assert s.query(Order).filter(Order.trader_id == tid).count() == 0
    assert s.query(Notification).filter(Notification.trader_id == tid).count() == 0
    assert s.query(SupportTicket).filter(SupportTicket.trader_id == tid).count() == 0
    # e-mail wolny: nowy klient rejestruje sie na ten sam adres
    s.add(Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                 full_name="Nowy Klient", referral_code=auth.secrets.token_hex(3)))
    s.commit(); s.close()

    assert client.delete(f"/api/admin/traders/999999", headers=ADMIN).status_code == 404


def test_admin_delete_trader_nie_rusza_admina():
    tid, _ = _trader(is_admin=True)
    assert client.delete(f"/api/admin/traders/{tid}", headers=ADMIN).status_code == 400
    s = SessionLocal()
    assert s.get(Trader, tid) is not None
    s.close()


def test_notify_admins_tworzy_wpis_dla_admina(monkeypatch):
    from app import notify, push
    admin_tid, _ = _trader(is_admin=True)
    zwykly_tid, _ = _trader()
    monkeypatch.setattr(push, "send_to_trader", lambda *a, **k: 0)

    notify.notify_admins("admin_test", "Tytul", "tresc")

    s = SessionLocal()
    rows = s.query(Notification).filter(Notification.event == "admin_test").all()
    trafily_do = {n.trader_id for n in rows}
    s.close()
    assert admin_tid in trafily_do and zwykly_tid not in trafily_do
    assert all(n.url == "/admin" for n in rows)


def test_cron_upsell_nudge(monkeypatch):
    """Cykliczne „Scale your progress": jedno powiadomienie na tradera z zyskiem,
    z prawdziwa matematyka, i cisza az minie okno min_days."""
    from app import push
    wyslane = []
    monkeypatch.setattr(push, "send_to_trader",
                        lambda tid, title, body="", url="/portal", tag=None: wyslane.append((tid, title, url)) or 1)
    _product()
    tid, _ = _trader()
    s = SessionLocal()
    # wiekszy rozmiar w tej samej rodzinie (2 kroki) — to on wchodzi do tresci
    if not s.query(Product).filter(Product.key == "ops-100k").first():
        s.add(Product(key="ops-100k", label="Ops 100K", account_size=100_000,
                      steps=2, price_usd=549, profit_target_p1=8, profit_target_p2=5,
                      max_daily_loss_pct=5, max_overall_loss_pct=10, drawdown_type="static",
                      min_trading_days=3, profit_split_pct=80, max_lots=6, active=True))
    s.add(Account(login="upsell-1", trader_id=tid, product_key="ops-25k", preset="ops-25k",
                  initial_balance=25_000, steps=2, profit_target_p1=8, profit_target_p2=5,
                  max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=3,
                  drawdown_type="static", profit_split_pct=80, phase="eval_1", status="active",
                  balance=26_000, equity=26_000, peak_equity=26_000))
    s.commit(); s.close()

    r = client.post("/api/cron/upsell-nudge", headers=ADMIN)
    assert r.status_code == 200 and r.json()["sent"] >= 1

    s = SessionLocal()
    n = (s.query(Notification).filter(Notification.trader_id == tid,
                                      Notification.event == "upsell_scale").first())
    assert n is not None
    # kwota liczona z NAJWIEKSZEGO aktywnego rozmiaru w rodzinie (pelna suita
    # dzieli baze miedzy plikami, wiec katalog bywa bogatszy niz w tym tescie)
    najwiekszy = max(p.account_size for p in s.query(Product)
                     .filter(Product.steps == 2, Product.active == True).all()  # noqa: E712
                     if p.account_size > 25_000)
    assert f"${najwiekszy * 0.04:,.0f}" in n.title        # konto +4% -> tyle na wiekszym
    assert n.url == "/portal?view=accounts&upsell=1"
    s.close()
    assert any(t[0] == tid for t in wyslane)              # poszedl tez push

    # drugi przebieg w oknie min_days nie dubluje wpisu
    assert client.post("/api/cron/upsell-nudge", headers=ADMIN).json()["sent"] == 0
    s = SessionLocal()
    assert s.query(Notification).filter(Notification.trader_id == tid,
                                        Notification.event == "upsell_scale").count() == 1
    s.close()

    # wylaczona kategoria marketingowa = cisza
    s = SessionLocal()
    tr = s.get(Trader, tid); tr.notify_marketing = False
    s.query(Notification).filter(Notification.trader_id == tid,
                                 Notification.event == "upsell_scale").delete()
    s.commit(); s.close()
    assert client.post("/api/cron/upsell-nudge", headers=ADMIN).json()["sent"] == 0
