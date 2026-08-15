"""Dziennik klienta w panelu: GET /api/admin/traders/{id}/journal.

Trzy pytania, na które panel dotąd nie odpowiadał wprost: czy klient z kontem
założonym ZA niego odebrał je z linku zaproszenia, czy w ogóle się loguje
(i czy dziś), i co po kolei robił — wejścia do portalu, zamówienia, wypłaty.
Oś czasu jest sklejana z istniejących wierszy, więc testujemy też świeże
tropy telemetrii: claim vs zwykły reset hasła, ślad zaproszenia i otwarcie
linku płatności.
"""
import os
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.NamedTemporaryFile(
    suffix=".db", delete=False).name)
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Account, Order, PayoutRequest,  # noqa: E402
                        TelemetryEvent, Trader)

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))


def _trader(*, must_set_password=False):
    s = SessionLocal()
    n = next(LICZNIK)
    tr = Trader(email=f"dziennik{n}@probe.test", full_name="Jan Dziennik",
                password_hash=auth.hash_password("haslo1234"),
                referral_code=f"DZIENNIK{n}",
                must_set_password=must_set_password)
    s.add(tr); s.commit()
    tid, mail, pwh = tr.id, tr.email, tr.password_hash
    s.close()
    return tid, mail, pwh


def _journal(tid):
    r = client.get(f"/api/admin/traders/{tid}/journal", headers=ADMIN)
    assert r.status_code == 200
    return r.json()


def _labels(j):
    return [i["label"] for i in j["items"]]


# --- claim kontra zwykły reset ---------------------------------------------------

def test_claim_z_zaproszenia_widac_w_dzienniku():
    tid, _, pwh = _trader(must_set_password=True)
    token = auth.make_setup_token(tid, pwh)
    r = client.post("/api/auth/reset", json={"token": token, "password": "nowehaslo1"})
    assert r.status_code == 200
    j = _journal(tid)
    t = j["trader"]
    assert t["awaiting_claim"] is False
    assert t["claimed_at"]
    # Odebranie konta to pierwsze wejście klienta — liczy się jak logowanie.
    assert t["logged_in_today"] is True
    assert "Claimed the account — password set from the invite link" in _labels(j)


def test_zwykly_reset_hasla_to_nie_claim():
    tid, _, pwh = _trader()
    token = auth.make_reset_token(tid, pwh)
    assert client.post("/api/auth/reset",
                       json={"token": token, "password": "nowehaslo1"}).status_code == 200
    j = _journal(tid)
    assert j["trader"]["claimed_at"] is None
    assert "Reset the password (forgot password)" in _labels(j)


# --- logowania -------------------------------------------------------------------

def test_logowanie_dzis_i_licznik_7_dni():
    tid, mail, _ = _trader()
    assert client.post("/api/auth/login",
                       json={"email": mail, "password": "haslo1234"}).status_code == 200
    t = _journal(tid)["trader"]
    assert t["logged_in_today"] is True
    assert t["logins_7d"] == 1
    assert t["last_login_at"]


def test_swiezy_klient_nigdy_nie_logowany():
    tid, _, _ = _trader()
    j = _journal(tid)
    t = j["trader"]
    assert t["logged_in_today"] is False
    assert t["last_login_at"] is None and t["logins_7d"] == 0
    # Konto w ogóle istnieje — oś czasu nie może być pusta.
    assert "Portal account created" in _labels(j)


# --- ślad zaproszenia i linku płatności ------------------------------------------

def test_zaproszenie_zostawia_slad():
    tid, _, _ = _trader(must_set_password=True)
    r = client.post(f"/api/admin/traders/{tid}/portal-invite?send=false",
                    headers=ADMIN)
    assert r.status_code == 200
    j = _journal(tid)
    assert j["trader"]["invited_at"]
    assert "Portal invite link copied (sent by hand)" in _labels(j)


def test_otwarcie_linku_platnosci():
    tid, _, _ = _trader()
    s = SessionLocal()
    o = Order(trader_id=tid, product_key="2step-25k", amount_usd=199.0,
              pay_token=f"dziennik-pay-{next(LICZNIK)}")
    s.add(o); s.commit(); oid, tok = o.id, o.pay_token; s.close()
    assert client.get(f"/pay/{tok}").status_code == 200
    labels = _labels(_journal(tid))
    assert f"Opened the payment link (order #{oid})" in labels
    assert any(l.startswith(f"Order #{oid}:") for l in labels)


# --- zwijanie wejść do portalu ---------------------------------------------------

def test_wejscia_do_portalu_zwijane_per_dzien():
    tid, _, _ = _trader()
    wczoraj = datetime.utcnow() - timedelta(days=1)
    s = SessionLocal()
    for chwila in (wczoraj, wczoraj + timedelta(minutes=5),
                   wczoraj + timedelta(minutes=90), datetime.utcnow()):
        s.add(TelemetryEvent(trader_id=tid, name="view_open", created_at=chwila))
    s.commit(); s.close()
    labels = _labels(_journal(tid))
    assert labels.count("Opened the portal (3×)") == 1
    assert labels.count("Opened the portal") == 1


# --- sklejanie zamówień, kont i wypłat -------------------------------------------

def test_wyplata_i_konto_w_osi_czasu():
    tid, _, _ = _trader()
    s = SessionLocal()
    acc = Account(login=f"90{next(LICZNIK)}", trader_id=tid, trader_name="Jan Dziennik",
                  platform_login="900001", platform_password="x",
                  platform_server="MetaQuotes-Demo", product_key="2step-25k",
                  initial_balance=25_000.0, balance=25_000.0, equity=25_000.0,
                  peak_equity=25_000.0, day_start_equity=25_000.0,
                  day_start_balance=25_000.0, status="funded", phase="funded")
    s.add(acc); s.commit()
    s.add(PayoutRequest(account_id=acc.id, trader_id=tid,
                        profit_amount=1000.0, trader_share=800.0))
    s.commit(); s.close()
    labels = _labels(_journal(tid))
    assert "Payout requested: $800.00 (pending)" in labels
    assert any(l.startswith("Account 900001 created") for l in labels)


# --- przegląd zbiorczy (zakładka Activity) ---------------------------------------

def _wiersz(j, tid):
    return next((w for w in j["items"] if w["id"] == tid), None)


def test_przeglad_widzi_zalogowanego_i_czekajacego():
    tid_a, mail_a, _ = _trader()
    assert client.post("/api/auth/login",
                       json={"email": mail_a, "password": "haslo1234"}).status_code == 200
    tid_b, _, _ = _trader(must_set_password=True)
    j = client.get("/api/admin/journal", headers=ADMIN).json()
    a, b = _wiersz(j, tid_a), _wiersz(j, tid_b)
    assert a["logged_in_today"] is True and a["logins_7d"] == 1
    assert b["awaiting_claim"] is True and b["last_login_at"] is None


def test_przeglad_bez_kont_administratora():
    """To lista KLIENTÓW — wiersz admina z setką logowań zaśmiecałby filtry."""
    s = SessionLocal()
    n = next(LICZNIK)
    adm = Trader(email=f"admin{n}@probe.test", full_name="Admin",
                 password_hash=auth.hash_password("haslo1234"),
                 referral_code=f"ADMIN{n}", is_admin=True)
    s.add(adm); s.commit(); aid = adm.id; s.close()
    j = client.get("/api/admin/journal", headers=ADMIN).json()
    assert _wiersz(j, aid) is None


def test_przeglad_bez_tokenu_ani_kroku():
    assert client.get("/api/admin/journal").status_code in (401, 403)


# --- brzegi ----------------------------------------------------------------------

def test_nieznany_trader_to_404():
    assert client.get("/api/admin/traders/999999/journal",
                      headers=ADMIN).status_code == 404


def test_bez_tokenu_admina_ani_kroku():
    tid, _, _ = _trader()
    assert client.get(f"/api/admin/traders/{tid}/journal").status_code in (401, 403)


# --- panel -----------------------------------------------------------------------

def test_panel_ma_karte_klienta_i_dziennik():
    """Endpoint bez widoku byłby martwy: karta „Client" w slide-overze konta
    ładuje dziennik, a pełna oś czasu otwiera się osobnym przyciskiem."""
    kod = client.get("/static/js/admin-panel.js").text
    assert "renderClientCard(" in kod
    assert "openTraderJournal(" in kod
    assert "/journal" in kod
    assert 'id="client-card"' in kod


def test_panel_ma_zakladke_activity():
    """Filtry przeglądu odpowiadają na pytania działu bez klikania po kontach:
    kto nie odebrał konta, kto nigdy nie wszedł, kto zamilkł."""
    kod = client.get("/static/js/admin-panel.js").text
    assert "renderActivity(" in kod
    assert "'/api/admin/journal'" in kod
    assert "v:'activity'" in kod
    for filtr in ("Awaiting claim", "Never signed in", "Active today", "Quiet 7+ days"):
        assert filtr in kod
