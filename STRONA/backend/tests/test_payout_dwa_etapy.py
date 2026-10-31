"""Wypłata w dwóch krokach.

Trader najpierw tylko prosi o wypłatę (`review`). Admin ogląda konto i ją
odblokowuje (`unlocked`) — dopiero wtedy trader wybiera kwotę i podaje portfel
USDT (`pending`). Admin wysyła i oznacza jako wypłaconą (`paid`).
"""
import os
import tempfile

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.gettempdir(), "pf_test_dwa_etapy.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, PayoutRequest, Trader  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

KAPITAL = 200_000.0
ZYSK = 1_000.0          # split 80% => dostepne 800
PORTFEL = {"network": "TRC20", "address": "TQmZ1kD4w9uPq8xYb3nR5tV7cE2sA6fGh9"}


def _funded(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Dwa Etapy", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login=f"6{tid:08d}"[:9], trader_id=tid, trader_name="Dwa Etapy",
                  product_key="2step-50k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  min_trading_days=5, trading_days_count=5,
                  balance=KAPITAL + ZYSK, equity=KAPITAL + ZYSK,
                  peak_equity=KAPITAL + ZYSK, day_start_equity=KAPITAL + ZYSK,
                  day_start_balance=KAPITAL + ZYSK)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _status(req_id):
    s = SessionLocal()
    try:
        return s.get(PayoutRequest, req_id).status
    finally:
        s.close()


def _otwarty(c, aid, h):
    return next(a for a in c.get("/api/me/accounts", headers=h).json()
                if a["id"] == aid)["payout_open"]


def test_pelna_sciezka_prosba_odblokowanie_kwota_wyplata(monkeypatch):
    maile = []
    monkeypatch.setattr(notify, "send", lambda ev, to, ctx=None: maile.append((ev, ctx or {})))
    aid, h = _funded("de-pelna@test.pl")
    with TestClient(app) as c:
        # Krok 1: sama prośba — bez kwoty i portfela.
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "review" and r.json()["available"] == 800.0
        rid = r.json()["id"]
        assert _otwarty(c, aid, h) == {"id": rid, "status": "review", "amount": 800.0}
        assert maile[-1][0] == "payout_requested" and maile[-1][1].get("review")

        # Przed akceptacją admina formularza kwoty nie ma.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 300, **PORTFEL})
        assert r.status_code == 400 and "still being reviewed" in r.json()["detail"]

        # Admin odblokowuje → trader dostaje maila z wezwaniem do kroku 2.
        r = c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        assert r.json() == {"unlocked": rid}
        assert _status(rid) == "unlocked" and maile[-1][0] == "payout_unlocked"
        # Drugi klik admina niczego nie wypłaca — czeka na tradera.
        r = c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        assert r.status_code == 400 and "Waiting for the trader" in r.json()["detail"]

        # Kwota ponad dostępną: odmowa, wniosek dalej czeka na poprawkę.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 900, **PORTFEL})
        assert r.status_code == 400 and "exceeds" in r.json()["detail"]
        assert _status(rid) == "unlocked"

        # Centy odpadają — kwota tylko w pełnych dolarach.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": "300,50", "network": "trc-20", "address": PORTFEL["address"]})
        assert r.status_code == 400 and "whole-dollar" in r.json()["detail"]
        assert _status(rid) == "unlocked"

        # Krok 2: kwota całkowita + portfel USDT.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 300, "network": "trc-20", "address": PORTFEL["address"]})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending" and r.json()["trader_share"] == 300.0
        lista = c.get("/api/admin/payout-requests", headers=ADMIN_H).json()
        moj = next(w for w in lista if w["id"] == rid)
        assert moj["method"] == "usdt"
        assert moj["details"] == {"network": "TRC20", "address": PORTFEL["address"]}
        # Drugi raz tego samego formularza nie da się wysłać.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 100, **PORTFEL})
        assert r.status_code == 400

        # Admin wysłał USDT i oznacza jako wypłaconą — księgowanie jak dotąd.
        r = c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        assert r.status_code == 200, r.text
        assert _status(rid) == "paid" and maile[-1][0] == "payout_approved"
        assert _otwarty(c, aid, h) is None
    s = SessionLocal()
    try:
        # 300 przy splicie 80% zdejmuje z salda 375 zysku.
        assert s.get(Account, aid).balance == round(KAPITAL + ZYSK - 300 / 0.8, 2)
    finally:
        s.close()


def test_jeden_otwarty_wniosek_na_kazdym_etapie():
    aid, h = _funded("de-jeden@test.pl")
    with TestClient(app) as c:
        rid = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={}).json()["id"]
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={})
        assert r.status_code == 400 and "already under review" in r.json()["detail"]
        c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={})
        assert r.status_code == 400


def test_odrzucenie_na_przegladzie_otwiera_nowy_wniosek():
    aid, h = _funded("de-odrzut@test.pl")
    with TestClient(app) as c:
        rid = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={}).json()["id"]
        # Wniosku, na który trader czeka, nie da się po cichu skasować.
        assert c.delete(f"/api/admin/payout-requests/{rid}", headers=ADMIN_H).status_code == 400
        r = c.post(f"/api/admin/payout-requests/{rid}/reject", headers=ADMIN_H,
                   json={"reason": "Open positions at the time of the request"})
        assert r.status_code == 200 and _status(rid) == "rejected"
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={})
        assert r.status_code == 200 and r.json()["status"] == "review"


def test_odrzucenie_po_odblokowaniu_zamyka_formularz():
    aid, h = _funded("de-odrzut2@test.pl")
    with TestClient(app) as c:
        rid = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={}).json()["id"]
        c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        assert c.delete(f"/api/admin/payout-requests/{rid}", headers=ADMIN_H).status_code == 400
        assert c.post(f"/api/admin/payout-requests/{rid}/reject", headers=ADMIN_H,
                      json={"reason": "x"}).status_code == 200
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 300, **PORTFEL})
        assert r.status_code == 400


def test_cudzy_wniosek_i_zle_dane_portfela():
    aid, h = _funded("de-wlasciciel@test.pl")
    _, obcy = _funded("de-obcy@test.pl")
    with TestClient(app) as c:
        rid = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={}).json()["id"]
        c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=obcy,
                   json={"amount": 300, **PORTFEL})
        assert r.status_code == 404
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 300, "network": "TRC20", "address": ""})
        assert r.status_code == 400 and "wallet address" in r.json()["detail"]
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 300, "network": "SOLANA", "address": PORTFEL["address"]})
        assert r.status_code == 400 and "network" in r.json()["detail"].lower()
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers={**h,
                   "Content-Type": "application/json"},
                   content='{"amount":NaN,"network":"TRC20","address":"' + PORTFEL["address"] + '"}')
        assert r.status_code == 422
        assert _status(rid) == "unlocked"


def test_konto_ktore_stracilo_funded_nie_odblokuje_sie():
    aid, h = _funded("de-breach@test.pl")
    with TestClient(app) as c:
        rid = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={}).json()["id"]
        s = SessionLocal()
        s.get(Account, aid).status = "breached"
        s.commit(); s.close()
        r = c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        assert r.status_code == 400 and "no longer funded" in r.json()["detail"]
        assert _status(rid) == "review"


# ---- Zwrot opłaty za challenge w kwocie pierwszej wypłaty (jak w FAQ) ----

def _z_zamowieniem(email, cena=299.0):
    from app.models import Order
    aid, h = _funded(email)
    s = SessionLocal()
    acc = s.get(Account, aid)
    s.add(Order(trader_id=acc.trader_id, product_key="2step-50k", amount_usd=cena,
                status="paid", provider="mock", account_id=aid))
    s.commit(); s.close()
    return aid, h


def test_usdt_pierwsza_wyplata_niesie_zwrot_w_jednej_kwocie(monkeypatch):
    from app.models import Payout
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    aid, h = _z_zamowieniem("de-zwrot-usdt@test.pl")
    with TestClient(app) as c:
        konto = next(a for a in c.get("/api/me/accounts", headers=h).json() if a["id"] == aid)
        assert konto["payout_fee_refund"] == 299.0
        rid = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={}).json()["id"]
        # Admin od początku widzi kwotę ze zwrotem — nawet przed krokiem 2.
        wiersz = next(w for w in c.get("/api/admin/payouts", headers=ADMIN_H).json()
                      if w["kind"] == "request" and w["id"] == rid)
        assert wiersz["fee_refund"] == 299.0 and wiersz["total"] == 800.0 + 299.0
        c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 600, **PORTFEL}).json()
        assert r["fee_refund"] == 299.0 and r["total"] == 899.0
        wiersz = next(w for w in c.get("/api/admin/payout-requests", headers=ADMIN_H).json()
                      if w["id"] == rid)
        assert wiersz["trader_share"] == 600.0 and wiersz["total"] == 899.0
        d = c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H).json()
        assert d["total_paid"] == 899.0 and d["fee_refund"] == 299.0
        hist = c.get("/api/me/payouts", headers=h).json()["history"]
        assert hist[0]["trader_share"] == 899.0 and hist[0]["fee_refund"] == 299.0
    s = SessionLocal()
    try:
        p = s.query(Payout).filter(Payout.account_id == aid).one()
        assert p.trader_share == 899.0 and p.fee_refund == 299.0
        # Saldo schodzi o samą działkę (600 / 0.8) — zwrot to pieniądze firmy.
        assert s.get(Account, aid).balance == round(KAPITAL + ZYSK - 600 / 0.8, 2)
        assert s.get(PayoutRequest, rid).fee_refund == 299.0
    finally:
        s.close()


def test_druga_wyplata_bez_zwrotu(monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(get_settings(), "payout_interval_days", 0)
    aid, h = _z_zamowieniem("de-zwrot-drugi@test.pl")
    with TestClient(app) as c:
        for oczekiwany in (299.0, 0.0):
            s = SessionLocal()
            acc = s.get(Account, aid)
            acc.balance = acc.equity = KAPITAL + ZYSK
            s.commit(); s.close()
            rid = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={}).json()["id"]
            c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
            r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                       json={"amount": 500, **PORTFEL}).json()
            assert r["fee_refund"] == oczekiwany
            d = c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H).json()
            assert d["total_paid"] == 500.0 + oczekiwany


def test_issue_payout_admina_tez_dolicza_zwrot(monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    aid, h = _z_zamowieniem("de-zwrot-issue@test.pl", cena=549.0)
    with TestClient(app) as c:
        karta = c.get(f"/api/admin/accounts/{aid}/payouts", headers=ADMIN_H).json()
        assert karta["fee_refund"] == 549.0
        p = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 800, "method": "crypto"}).json()
        assert p["trader_share"] == 1349.0 and p["fee_refund"] == 549.0
        # Zwrot zużyty — kolejna wypłata z tego konta już go nie niesie.
        assert c.get(f"/api/admin/accounts/{aid}/payouts",
                     headers=ADMIN_H).json()["fee_refund"] == 0.0


# ---- Kwota w kroku 1, portfel po akceptacji, zapisany portfel i jego zmiana ----

PORTFEL_2 = {"network": "TRC20", "address": "TXyZ9aB8cD7eF6gH5iJ4kL3mN2pQ1rS0tU"}


def _do_kroku_2(c, aid, h, kwota=None):
    body = {} if kwota is None else {"amount": kwota}
    r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=body)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H).status_code == 200
    return rid


def _reset_salda(aid):
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.balance = acc.equity = KAPITAL + ZYSK
    s.commit(); s.close()


def test_kwota_w_kroku_1_i_brak_metody_przed_danymi(monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    aid, h = _funded("de-kwota1@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={"amount": 900})
        assert r.status_code == 400 and "exceeds" in r.json()["detail"]
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json={"amount": 300})
        assert r.status_code == 200 and r.json()["amount"] == 300.0
        rid = r.json()["id"]
        wiersz = c.get("/api/me/payouts", headers=h).json()["requests"][0]
        # Żadnego zgadniętego tokena, zanim trader poda portfel.
        assert wiersz["method"] == "" and wiersz["trader_share"] == 300.0
        c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        # Więcej niż zatwierdzono — nie.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={"amount": 400, **PORTFEL})
        assert r.status_code == 400 and "approved payout" in r.json()["detail"]
        # Bez kwoty = zatwierdzona.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h, json=PORTFEL)
        assert r.status_code == 200 and r.json()["trader_share"] == 300.0


def test_portfel_zapisany_po_pierwszej_i_podpowiadany(monkeypatch):
    maile = []
    monkeypatch.setattr(notify, "send", lambda ev, to, ctx=None: maile.append((ev, ctx or {})))
    monkeypatch.setattr(get_settings(), "payout_interval_days", 0)
    aid, h = _funded("de-portfel@test.pl")
    with TestClient(app) as c:
        assert c.get("/api/me/payout-wallet", headers=h).json()["saved"] is False
        # Przed pierwszą wypłatą nie ma czego zmieniać.
        r = c.post("/api/me/payout-wallet/code", headers=h, json=PORTFEL_2)
        assert r.status_code == 400
        rid = _do_kroku_2(c, aid, h, 500)
        assert maile[-1][0] == "payout_unlocked" and not maile[-1][1]["saved_wallet"]
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h, json={"use_saved": True})
        assert r.status_code == 400 and "no saved wallet" in r.json()["detail"]
        assert c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                      json=PORTFEL).status_code == 200
        portfel = c.get("/api/me/payouts", headers=h).json()["wallet"]
        assert portfel["saved"] and portfel["address"] == PORTFEL["address"]
        assert portfel["network"] == "TRC20"
        c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)

        # Druga wypłata: mail mówi „confirm", zapisany portfel przechodzi bez kodu.
        _reset_salda(aid)
        rid = _do_kroku_2(c, aid, h, 500)
        assert maile[-1][0] == "payout_unlocked" and maile[-1][1]["saved_wallet"]
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h, json={"use_saved": True})
        assert r.status_code == 200, r.text
        lista = c.get("/api/admin/payout-requests", headers=ADMIN_H).json()
        assert next(w for w in lista if w["id"] == rid)["wallet_new"] is False


def test_zmiana_portfela_tylko_z_kodem(monkeypatch):
    maile = []
    monkeypatch.setattr(notify, "send", lambda ev, to, ctx=None: maile.append((ev, ctx or {})))
    monkeypatch.setattr(get_settings(), "payout_interval_days", 0)
    aid, h = _funded("de-zmiana@test.pl")
    with TestClient(app) as c:
        rid = _do_kroku_2(c, aid, h, 500)
        c.post(f"/api/me/payout-requests/{rid}/details", headers=h, json=PORTFEL)
        c.post(f"/api/admin/payout-requests/{rid}/approve", headers=ADMIN_H)
        _reset_salda(aid)
        rid = _do_kroku_2(c, aid, h, 500)

        # Inny portfel bez kodu: odmowa, wniosek dalej czeka.
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h, json=PORTFEL_2)
        assert r.status_code == 400 and "6-digit code" in r.json()["detail"]
        assert _status(rid) == "unlocked"
        # Kod idzie mailem i jest przypięty do konkretnego adresu.
        assert c.post("/api/me/payout-wallet/code", headers=h,
                      json=PORTFEL).status_code == 400          # to już zapisany
        assert c.post("/api/me/payout-wallet/code", headers=h,
                      json=PORTFEL_2).status_code == 200
        ev, ctx = maile[-1]
        assert ev == "payout_wallet_code" and ctx["address"] == PORTFEL_2["address"]
        kod = ctx["code"]
        zly = "000000" if kod != "000000" else "111111"
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={**PORTFEL_2, "code": zly})
        assert r.status_code == 400
        r = c.post(f"/api/me/payout-requests/{rid}/details", headers=h,
                   json={**PORTFEL_2, "code": kod})
        assert r.status_code == 200, r.text
        assert c.get("/api/me/payout-wallet", headers=h).json()["address"] == PORTFEL_2["address"]
        wiersz = next(w for w in c.get("/api/admin/payouts", headers=ADMIN_H).json()
                      if w["kind"] == "request" and w["id"] == rid)
        assert wiersz["wallet_new"] is True
        # Zużytego kodu nie da się użyć drugi raz (ustawienia).
        r = c.post("/api/me/payout-wallet", headers=h, json={**PORTFEL, "code": kod})
        assert r.status_code == 400


def test_zmiana_portfela_z_ustawien_i_wygasly_kod(monkeypatch):
    from datetime import datetime, timedelta, timezone
    maile = []
    monkeypatch.setattr(notify, "send", lambda ev, to, ctx=None: maile.append((ev, ctx or {})))
    aid, h = _funded("de-ustawienia@test.pl")
    with TestClient(app) as c:
        rid = _do_kroku_2(c, aid, h, 500)
        c.post(f"/api/me/payout-requests/{rid}/details", headers=h, json=PORTFEL)
        c.post("/api/me/payout-wallet/code", headers=h, json=PORTFEL_2)
        kod = maile[-1][1]["code"]
        # Kod na PORTFEL_2 nie przepuści innego adresu.
        inny = {"network": "BEP20", "address": "0x" + "ab" * 20}
        assert c.post("/api/me/payout-wallet", headers=h,
                      json={**inny, "code": kod}).status_code == 400
        s = SessionLocal()
        tr = s.query(Trader).filter(Trader.email == "de-ustawienia@test.pl").one()
        tr.payout_wallet_code_exp = (datetime.now(timezone.utc).replace(tzinfo=None)
                                     - timedelta(minutes=1))
        s.commit(); s.close()
        assert c.post("/api/me/payout-wallet", headers=h,
                      json={**PORTFEL_2, "code": kod}).status_code == 400
        c.post("/api/me/payout-wallet/code", headers=h, json=PORTFEL_2)
        kod = maile[-1][1]["code"]
        r = c.post("/api/me/payout-wallet", headers=h, json={**PORTFEL_2, "code": kod})
        assert r.status_code == 200 and r.json()["address"] == PORTFEL_2["address"]
