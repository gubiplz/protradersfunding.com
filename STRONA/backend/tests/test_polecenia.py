"""Program poleceń partnera: lead z `ref` → „pending", pierwsza wypłata → „confirmed".

Dotąd partner dopisywał poleconych ręcznie, a „confirmed" ktoś przestawiał
w bazie z ręki. Teraz robi to backend, bo tylko on zna oba fakty: `ref` przychodzi
z landingu razem z leadem, wypłatę zatwierdza admin.
"""
import json
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, polecenia  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Lead, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
TOKEN_LANDINGU = "sekret-landingu-polecenia"
ADRES = "https://baza-partnera.example/rest/v1/rpc/sync_referral"
KLUCZ = "klucz-testowy"
LICZNIK = iter(range(10_000))


@pytest.fixture(autouse=True)
def wyslane(monkeypatch):
    """Transport podmieniony na listę; sieć nie jest dotykana."""
    u = get_settings()
    monkeypatch.setattr(u, "lead_ingest_token", TOKEN_LANDINGU, raising=False)
    monkeypatch.setattr(u, "referral_sync_url", ADRES, raising=False)
    monkeypatch.setattr(u, "referral_sync_key", KLUCZ, raising=False)
    lista: list[tuple[str, dict, str]] = []

    def transport(url, body, key):
        lista.append((url, body, key))
        return 200, json.dumps("confirmed" if body["p_paid"] else "pending").encode()

    monkeypatch.setattr(polecenia, "_post", transport)
    return lista


def _mail():
    return f"polecony{next(LICZNIK)}@test.pl"


def _zgloszenie(email, **nadpisz):
    dane = {"email": email, "name": "Anna Polecona", "source": "questionnaire",
            "ref": "anna-partner", "accountSize": "$50K", "outcome": "qualified",
            "quality": {"tier": "high", "score": 8}}
    dane.update(nadpisz)
    return client.post("/api/leads/ingest", json=dane, headers={"X-Lead-Token": TOKEN_LANDINGU})


def _ref(email):
    s = SessionLocal()
    try:
        return s.query(Lead).filter(Lead.email == email).one().ref
    finally:
        s.close()


def _funded(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Polecony", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    kapital = 100_000.0
    acc = Account(login=f"8{tid:08d}"[:9], trader_id=tid, trader_name="Polecony",
                  product_key="2step-50k", initial_balance=kapital, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  min_trading_days=5, trading_days_count=5,
                  balance=kapital + 1_000, equity=kapital + 1_000,
                  peak_equity=kapital + 1_000, day_start_equity=kapital + 1_000,
                  day_start_balance=kapital + 1_000)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def test_lead_z_linku_partnera_trafia_na_jego_liste_jako_pending(wyslane):
    email = _mail()
    assert _zgloszenie(email.upper()).status_code == 200
    assert wyslane == [(ADRES, {"p_slug": "anna-partner", "p_email": email,
                                "p_account_size": "$50K", "p_paid": False}, KLUCZ)]


def test_drugie_zgloszenie_bez_linku_nie_kasuje_partnera(wyslane):
    email = _mail()
    _zgloszenie(email)
    _zgloszenie(email, ref="")
    assert _ref(email) == "anna-partner"


def test_pierwszy_partner_zostaje(wyslane):
    email = _mail()
    _zgloszenie(email)
    _zgloszenie(email, ref="inny-partner")
    assert _ref(email) == "anna-partner"
    assert {b["p_slug"] for _, b, _ in wyslane} == {"anna-partner"}


def test_smieci_zamiast_sluga_nie_wchodza_ani_do_bazy_ani_dalej(wyslane):
    email = _mail()
    assert _zgloszenie(email, ref="<script>" + "x" * 80).status_code == 200
    assert _ref(email) is None
    assert wyslane == []


def test_lead_bez_ref_nic_nie_wysyla(wyslane):
    _zgloszenie(_mail(), ref=None)
    assert wyslane == []


def test_bez_konfiguracji_nic_nie_wychodzi(wyslane, monkeypatch):
    monkeypatch.setattr(get_settings(), "referral_sync_url", "", raising=False)
    assert _zgloszenie(_mail()).status_code == 200
    assert wyslane == []


def test_padnieta_baza_partnera_nie_psuje_przyjecia_leada(monkeypatch):
    def pada(url, body, key):
        raise OSError("connection refused")
    monkeypatch.setattr(polecenia, "_post", pada)
    r = _zgloszenie(_mail())
    assert r.status_code == 200 and r.json()["ok"] is True


def test_zatwierdzona_wyplata_potwierdza_polecenie(wyslane):
    email = _mail()
    _zgloszenie(email)
    wyslane.clear()
    aid, h = _funded(email)
    r = client.post(f"/api/accounts/{aid}/payout-request", headers=h,
                    json={"method": "wise", "amount": 200, "details": {"email": "x@test.pl"}})
    assert r.status_code == 200, r.text
    assert wyslane == []  # sam wniosek to jeszcze nie pieniądze
    assert client.post(f"/api/admin/payout-requests/{r.json()['id']}/approve",
                       headers=ADMIN).status_code == 200
    assert wyslane == [(ADRES, {"p_slug": "anna-partner", "p_email": email,
                                "p_account_size": None, "p_paid": True}, KLUCZ)]


def test_wyplata_wystawiona_przez_admina_tez_potwierdza(wyslane):
    email = _mail()
    _zgloszenie(email)
    wyslane.clear()
    aid, _ = _funded(email)
    r = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                    json={"amount": 300, "method": "bank", "reset_balance": False})
    assert r.status_code == 200, r.text
    assert [b["p_paid"] for _, b, _ in wyslane] == [True]


def test_wyplata_tradera_bez_polecenia_nic_nie_wysyla(wyslane):
    aid, _ = _funded(_mail())  # trader bez leada — np. zapisał się sam
    r = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                    json={"amount": 300, "method": "bank", "reset_balance": False})
    assert r.status_code == 200, r.text
    assert wyslane == []
