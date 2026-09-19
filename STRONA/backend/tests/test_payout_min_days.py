"""Instant Funding: 30 dni handlu przed pierwszą wypłatą — teraz naprawdę.

Sklep, tabela objectives i dashboard mówiły „min. 30 dni handlu", ale wniosek
o wypłatę sprawdzał wyłącznie status funded, KYC i zysk. Konto Instant jest
funded od pierwszej minuty, więc klient z zyskiem wypłacał drugiego dnia,
patrząc jednocześnie na licznik „2 / 30 min". Testy pilnują, żeby bramka
działała TAM, gdzie obiecana, i NIE działała tam, gdzie nikt jej nie obiecał:
konto po ewaluacji zużyło swoje dni na zdanie fazy i wchodzi na funded
z licznikiem od zera — policzenie ich drugi raz zamroziłoby mu wypłaty.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.gettempdir(), "pf_test.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trader  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

KAPITAL = 50_000.0
ZYSK = 2_000.0
WISE = {"method": "wise", "amount": 100, "details": {"email": "trader@example.com"}}


def _konto(email: str, *, steps: int, dni: int, min_dni: int = 30):
    # Adres unikalny per uruchomienie: przy samodzielnym `pytest tests/ten_plik.py`
    # baza to trwały plik w tempdirze i stałe adresy zderzyłyby się za drugim razem.
    email = f"{auth.secrets.token_hex(4)}-{email}"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Instant Trader", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login=f"8{tid:08d}"[:9], trader_id=tid, trader_name="Instant Trader",
                  product_key="instant-50k" if steps == 0 else "2step-50k",
                  initial_balance=KAPITAL, steps=steps, profit_split_pct=70,
                  status="funded", phase="funded", min_trading_days=min_dni,
                  trading_days_count=dni,
                  balance=KAPITAL + ZYSK, equity=KAPITAL + ZYSK,
                  peak_equity=KAPITAL + ZYSK, day_start_equity=KAPITAL + ZYSK,
                  day_start_balance=KAPITAL + ZYSK)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def test_instant_przed_trzydziestym_dniem_nie_wyplaca():
    aid, h = _konto("md-wczesnie@test.pl", steps=0, dni=12)
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=WISE)
    assert r.status_code == 400
    tresc = r.json()["detail"]
    # Klient ma wyjść z odpowiedzi wiedząc, ILE jeszcze — sam próg nic mu nie mówi
    assert "30" in tresc and "12" in tresc and "18" in tresc


def test_instant_po_trzydziestym_dniu_wyplaca():
    aid, h = _konto("md-gotowe@test.pl", steps=0, dni=30)
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=WISE)
    assert r.status_code == 200, r.text


def test_konto_po_ewaluacji_nie_czeka_drugi_raz():
    """Świeżo sfinansowane 2-Step ma `trading_days_count == 0`, bo licznik zeruje
    się przy zmianie fazy. Dni już zapłacone zdaniem ewaluacji — cennik nie
    obiecuje przy tym planie żadnej karencji na wypłatę."""
    aid, h = _konto("md-2step@test.pl", steps=2, dni=0, min_dni=5)
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=WISE)
    assert r.status_code == 200, r.text


def test_pula_od_admina_nie_skraca_karencji():
    """Pula rządzi KWOTĄ, dni rządzą TERMINEM. Właściciel, który chce wypłacić
    wcześniej, ma do tego „Issue payout" w panelu — poza ścieżką wniosku."""
    aid, h = _konto("md-pula@test.pl", steps=0, dni=3)
    with TestClient(app) as c:
        assert c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
                      json={"amount": 500}).status_code == 200
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=WISE)
        assert r.status_code == 400 and "trading days" in r.json()["detail"]

        # ...a ręczna wypłata z panelu przechodzi mimo karencji
        r = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 100, "method": "bank"})
        assert r.status_code == 200, r.text


def test_portal_i_panel_widza_ile_dni_zostalo():
    aid, h = _konto("md-widok@test.pl", steps=0, dni=25)
    with TestClient(app) as c:
        konto = next(a for a in c.get("/api/me/accounts", headers=h).json()
                     if a["id"] == aid)
        assert konto["payout_days_left"] == 5
        assert konto["metrics"]["trading_days"] == 25
        panel = c.get(f"/api/admin/accounts/{aid}/payouts", headers=ADMIN_H).json()
    assert panel["payout_days_left"] == 5 and panel["min_trading_days"] == 30
