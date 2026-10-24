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
        assert _otwarty(c, aid, h) == {"id": rid, "status": "review"}
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
