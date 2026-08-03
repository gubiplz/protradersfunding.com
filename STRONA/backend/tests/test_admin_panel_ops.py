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


def _konto_funded(tid, login):
    """Konto funded z zyskiem — gotowe pod wystawienie wypłaty przez admina."""
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Ops Tester",
                  platform_login=login, product_key="ops-25k",
                  initial_balance=25_000.0, balance=27_000.0, equity=27_000.0,
                  peak_equity=27_000.0, day_start_equity=27_000.0,
                  day_start_balance=27_000.0, steps=2, profit_split_pct=80,
                  status="funded", phase="funded")
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


def test_widok_payouts_pokazuje_wyplaty_admina_nie_tylko_wnioski():
    """Wypłata wystawiona ręcznie przez admina nie tworzy wniosku, więc do tej
    pory nie było jej w widoku Payouts — lista kłamała o tym, ile wyszło."""
    from app.models import Payout, PayoutRequest

    _product()
    tid, _ = _trader()
    aid = _konto_funded(tid, "OPS90001")

    r = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                    json={"amount": 900, "method": "bank", "reset_balance": False})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # wniosek juz rozliczony (status paid) ma swoj wiersz w payouts — nie moze
    # pojawic sie drugi raz jako wniosek
    s = SessionLocal()
    s.add(PayoutRequest(account_id=aid, trader_id=tid, profit_amount=100.0,
                        trader_share=80.0, method="bank", status="paid"))
    s.add(PayoutRequest(account_id=aid, trader_id=tid, profit_amount=200.0,
                        trader_share=160.0, method="bank", status="pending"))
    s.commit(); s.close()

    dane = client.get("/api/admin/payouts", headers=ADMIN).json()
    moje = [x for x in dane if x["account_login"] == "OPS90001"]
    assert len(moje) == 2, f"oczekiwano wyplaty + otwartego wniosku, jest {moje}"
    wyplata = next(x for x in moje if x["kind"] == "payout")
    assert wyplata["id"] == pid and wyplata["trader_share"] == 900
    assert wyplata["cert_url"], "wyplata admina powinna miec od razu certyfikat"
    assert [x["status"] for x in moje if x["kind"] == "request"] == ["pending"]

    s = SessionLocal()
    assert s.query(Payout).filter(Payout.account_id == aid).count() == 1
    s.close()


def test_wycofanie_certyfikatu_zdejmuje_wpis_z_landingu():
    """Do tej pory wystawienie certyfikatu było nieodwracalne — na pas na LP
    mogły trafić dane testowe bez żadnej drogi odwrotu."""
    from app import main as main_mod
    from app.models import Payout

    _product()
    tid, _ = _trader()
    aid = _konto_funded(tid, "OPS90002")
    pid = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                      json={"amount": 700, "method": "bank", "reset_balance": False}).json()["id"]

    main_mod._PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
    pas = client.get("/api/public/certificates/recent").json()
    assert any(x["amount_usd"] == 700 for x in pas), "wyplata nie weszla na pas"

    r = client.delete(f"/api/admin/payouts/{pid}/certificate", headers=ADMIN)
    assert r.status_code == 200 and r.json()["cert_url"] is None

    pas2 = client.get("/api/public/certificates/recent").json()
    assert not any(x["amount_usd"] == 700 for x in pas2), \
        "wycofany certyfikat dalej wisi na landingu"

    # sama wyplata zostaje — to rekord ksiegowy, znika tylko dokument
    s = SessionLocal()
    p = s.get(Payout, pid)
    assert p is not None and p.paid and p.cert_token is None
    s.close()

    assert client.delete("/api/admin/payouts/999999/certificate", headers=ADMIN).status_code == 404


def test_import_historycznych_wyplat_bez_certyfikatow():
    """Wypłaty rozliczone przed wdrożeniem panelu trzeba dało się wprowadzić,
    ale NIE mogą same z siebie wystawiać publicznych certyfikatów."""
    from app import main as main_mod
    from app.models import Payout

    csv = ("full_name,amount_usd,date,account_size,program,email,note\n"
           "Importowy Jeden,2480,2026-07-03,50000,2step,,\n"
           "Importowy Dwa,3125,2026-07-07,100000,2step,,pierwsza wyplata\n")

    # podglad niczego nie zapisuje
    r = client.post("/api/admin/payouts/import", headers=ADMIN, json={"csv": csv})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["added"] == 2 and d["committed"] is False
    assert [w["duplicate"] for w in d["rows"]] == [False, False]
    s = SessionLocal()
    assert s.query(Trader).filter(Trader.email == "importowy.jeden@imported.local").count() == 0
    s.close()

    d2 = client.post("/api/admin/payouts/import", headers=ADMIN,
                     json={"csv": csv, "commit": True}).json()
    assert d2["added"] == 2 and d2["committed"] is True

    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == "importowy.jeden@imported.local").first()
    assert tr is not None
    acc = s.query(Account).filter(Account.trader_id == tr.id).first()
    assert acc.status == "funded" and acc.mt5_backed is False
    p = s.query(Payout).filter(Payout.account_id == acc.id).one()
    assert p.trader_share == 2480 and p.paid
    assert p.cert_token is None, "import nie ma prawa wystawiac publicznego certyfikatu"
    s.close()

    # wpisy nie moga trafic na pas na landingu
    main_mod._PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
    pas = client.get("/api/public/certificates/recent").json()
    assert not any(x["amount_usd"] in (2480, 3125) for x in pas)

    # powtorka tego samego pliku niczego nie dubluje
    d3 = client.post("/api/admin/payouts/import", headers=ADMIN,
                     json={"csv": csv, "commit": True}).json()
    assert d3["added"] == 0 and d3["skipped"] == 2

    # widok Payouts pokazuje je jako zwykle wyplaty, bez certyfikatu
    lista = client.get("/api/admin/payouts", headers=ADMIN).json()
    moje = [x for x in lista if x["trader_email"] == "importowy.jeden@imported.local"]
    assert len(moje) == 1 and moje[0]["kind"] == "payout" and moje[0]["cert_url"] is None


def test_import_wyplat_zglasza_bledy_zamiast_zgadywac():
    zly = ("full_name,amount_usd,date,account_size,program,email,note\n"
           "Bez Daty,1000,,50000,2step,,\n"
           "Zly Plan,1000,2026-07-03,777,2step,,\n")
    d = client.post("/api/admin/payouts/import", headers=ADMIN,
                    json={"csv": zly, "commit": True}).json()
    assert d["ok"] is False and len(d["errors"]) == 2 and d["added"] == 0
    assert any("YYYY-MM-DD" in b for b in d["errors"])
    assert any("nie ma planu" in b for b in d["errors"])

    brak = client.post("/api/admin/payouts/import", headers=ADMIN,
                       json={"csv": "kolumna\n1\n"}).json()
    assert brak["ok"] is False


def test_usuwanie_wiersza_wyplaty_i_wniosku():
    """Wiersz da sie skasowac calkiem (pomylka, cofniecie importu), ale wniosek
    CZEKAJACY nie — trader dostaje decyzje, nie cisze."""
    from app import main as main_mod
    from app.models import Payout, PayoutRequest

    _product()
    tid, _ = _trader()
    aid = _konto_funded(tid, "OPS90003")
    pid = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                      json={"amount": 600, "method": "bank", "reset_balance": False}).json()["id"]

    main_mod._PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
    assert any(x["amount_usd"] == 600 for x in
               client.get("/api/public/certificates/recent").json())

    r = client.delete(f"/api/admin/payouts/{pid}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["had_certificate"] is True
    s = SessionLocal()
    assert s.get(Payout, pid) is None
    s.close()
    assert not any(x["amount_usd"] == 600 for x in
                   client.get("/api/public/certificates/recent").json())
    assert client.delete(f"/api/admin/payouts/{pid}", headers=ADMIN).status_code == 404

    s = SessionLocal()
    czeka = PayoutRequest(account_id=aid, trader_id=tid, profit_amount=100.0,
                          trader_share=80.0, method="bank", status="pending")
    odrzucony = PayoutRequest(account_id=aid, trader_id=tid, profit_amount=50.0,
                              trader_share=40.0, method="bank", status="rejected")
    s.add(czeka); s.add(odrzucony); s.commit()
    czeka_id, odrzucony_id = czeka.id, odrzucony.id
    s.close()

    assert client.delete(f"/api/admin/payout-requests/{czeka_id}", headers=ADMIN).status_code == 400
    assert client.delete(f"/api/admin/payout-requests/{odrzucony_id}", headers=ADMIN).status_code == 200
    s = SessionLocal()
    assert s.get(PayoutRequest, czeka_id) is not None
    assert s.get(PayoutRequest, odrzucony_id) is None
    s.close()


def test_kasowanie_zamowienia_zostawia_konto():
    """Paragon mozna skasowac (porzucony koszyk, test), ale konto zalozone z
    tego zamowienia zyje wlasnym zyciem i zostaje."""
    from app.models import Order

    _product()
    tid, _ = _trader()
    aid = _konto_funded(tid, "OPS90010")
    s = SessionLocal()
    o = Order(trader_id=tid, product_key="ops-25k", amount_usd=249, status="paid", account_id=aid)
    s.add(o); s.commit()
    oid = o.id
    s.close()

    assert client.delete(f"/api/admin/orders/{oid}", headers=ADMIN).status_code == 200
    s = SessionLocal()
    assert s.get(Order, oid) is None
    assert s.get(Account, aid) is not None, "konto nie moze zniknac razem z paragonem"
    s.close()
    assert client.delete(f"/api/admin/orders/{oid}", headers=ADMIN).status_code == 404


def test_kasowanie_kyc_czysci_dane_i_pliki():
    """Usuniecie wiersza KYC kasuje dane ORAZ wgrane skany z dysku — to jedyna
    droga wyczyszczenia dowodu z serwera. Revert (cofniecie decyzji) zostaje."""
    from app.main import UPLOADS

    tid, email = _trader()
    katalog = UPLOADS / "kyc" / str(tid)
    katalog.mkdir(parents=True, exist_ok=True)
    (katalog / "front.jpg").write_bytes(b"skan")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    tr.kyc_status = "approved"; tr.kyc_country = "PL"; tr.kyc_id_number = "ABC123"
    tr.kyc_doc_front = "front.jpg"
    tr.kyc_submitted_at = datetime.now(timezone.utc)
    tr.kyc_reviewed_at = datetime.now(timezone.utc)
    s.commit(); s.close()

    assert any(t["email"] == email for t in client.get("/api/admin/kyc", headers=ADMIN).json()["history"])

    r = client.delete(f"/api/admin/kyc/{tid}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["files_removed"] == 1
    assert not (katalog / "front.jpg").exists(), "skan dowodu zostal na dysku"

    s = SessionLocal()
    tr = s.get(Trader, tid)
    assert tr.kyc_status == "none" and tr.kyc_country is None and tr.kyc_id_number is None
    assert tr.kyc_doc_front is None and tr.kyc_reviewed_at is None
    s.close()
    assert not any(t["email"] == email for t in client.get("/api/admin/kyc", headers=ADMIN).json()["history"])
    assert client.delete("/api/admin/kyc/999999", headers=ADMIN).status_code == 404


def test_certyfikat_nie_drukuje_notatki_z_importu():
    """Notatka "imported from records" jest dla nas — na dokumencie klienta nie
    ma po niej sladu."""
    from app import payout_import
    from app.models import Payout

    _product()
    tid, _ = _trader()
    aid = _konto_funded(tid, "OPS90011")
    s = SessionLocal()
    p = Payout(account_id=aid, profit_amount=1000.0, trader_share=800.0, paid=True,
               note=payout_import.IMPORT_NOTE, cert_token="notka-token-1")
    s.add(p); s.commit(); s.close()

    html = client.get("/payout/notka-token-1").text
    assert html.count("$800") >= 1, "certyfikat sie nie wyrenderowal"
    assert payout_import.IMPORT_NOTE not in html

    s = SessionLocal()
    s.query(Payout).filter(Payout.cert_token == "notka-token-1").update({Payout.note: "1st payout"})
    s.commit(); s.close()
    assert "1st payout" in client.get("/payout/notka-token-1").text, \
        "prawdziwa notatka nadal ma sie drukowac"


def test_import_ustawia_kyc_z_kolumny():
    """Kolumna `kyc` decyduje o statusie weryfikacji — bez niej trader zostaje
    z "none", bo importu nikt nie sprawdzal."""
    csv = ("full_name,amount_usd,date,account_size,program,email,note,kyc\n"
           "Kyc Zatwierdzony,1500,2026-07-04,50000,2step,kyc.ok@test.pl,,approved\n"
           "Kyc Domyslny,1600,2026-07-05,50000,2step,kyc.brak@test.pl,,\n")
    d = client.post("/api/admin/payouts/import", headers=ADMIN,
                    json={"csv": csv, "commit": True}).json()
    assert d["ok"] and d["added"] == 2

    s = SessionLocal()
    ok = s.query(Trader).filter(Trader.email == "kyc.ok@test.pl").first()
    brak = s.query(Trader).filter(Trader.email == "kyc.brak@test.pl").first()
    assert ok.kyc_status == "approved" and ok.kyc_reviewed_at is not None
    assert brak.kyc_status == "none" and brak.kyc_reviewed_at is None
    s.close()

    hist = client.get("/api/admin/kyc", headers=ADMIN).json()["history"]
    maile = {t["email"] for t in hist}
    assert "kyc.ok@test.pl" in maile and "kyc.brak@test.pl" not in maile

    zly = ("full_name,amount_usd,date,account_size,program,email,note,kyc\n"
           "Zly Status,100,2026-07-04,50000,2step,,,zweryfikowany\n")
    r = client.post("/api/admin/payouts/import", headers=ADMIN,
                    json={"csv": zly, "commit": True}).json()
    assert r["ok"] is False and any("kyc" in b for b in r["errors"])


def test_lista_traderow_do_grantu_bez_limitu_i_bez_usunietych():
    """Lista pod „Grant a challenge": wszyscy realni klienci, zero ślepych uliczek.

    Cichy limit ukrywał starszych klientów (nie dało się im nic przyznać),
    a konta po /api/me/delete są zanonimizowane: nie zalogują się, mail na
    @removed.invalid się odbija.
    """
    s = SessionLocal()
    ilu_przed = len(client.get("/api/admin/traders", headers=ADMIN).json())
    for i in range(60):
        s.add(Trader(email=f"masa{i}@test.pl", password_hash="x",
                     full_name=f"Masa {i}", referral_code=f"m{i:04d}"))
    s.add(Trader(email="deleted-777@removed.invalid", password_hash="x",
                 full_name="Deleted User", referral_code="del777"))
    s.commit(); s.close()

    lista = client.get("/api/admin/traders", headers=ADMIN).json()
    maile = {t["email"] for t in lista}
    assert len(lista) == ilu_przed + 60, "lista musi obejmować wszystkich, bez limitu"
    assert "masa0@test.pl" in maile, "najstarszy z dodanych nie może wypaść z listy"
    assert not any(m.endswith("@removed.invalid") for m in maile)

    szukaj = client.get("/api/admin/traders?q=masa4", headers=ADMIN).json()
    assert {t["email"] for t in szukaj} == {f"masa4{i}@test.pl" for i in range(10)} | {"masa4@test.pl"}
