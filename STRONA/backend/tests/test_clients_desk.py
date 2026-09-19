"""Zakładka Clients wie, z którego lejka przyszedł klient.

Lista pokazuje TRADERÓW, a ci nie niosą źródła — stronę, która ich
przyprowadziła, pamięta wyłącznie lead. Bez dopasowania po mailu filtr
„Nigeria" musiałby zgadywać w przeglądarce.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
PRZEDROSTEK = "desk-clients"


@pytest.fixture
def swiat():
    s = SessionLocal()
    try:
        yield s
    finally:
        try:
            s.rollback()
            s.query(Lead).filter(Lead.email.like(f"%{PRZEDROSTEK}%")).delete(
                synchronize_session=False)
            s.query(Trader).filter(Trader.email.like(f"%{PRZEDROSTEK}%")).delete(
                synchronize_session=False)
            s.commit()
        finally:
            s.close()


def _trader(s, nazwa, mail=None):
    t = Trader(email=mail or f"{PRZEDROSTEK}-{nazwa}@example.com",
               password_hash=auth.hash_password("haslo1234"),
               referral_code=auth.secrets.token_hex(4), full_name=nazwa)
    s.add(t)
    s.commit()
    return t


def _lead(s, mail, source):
    s.add(Lead(email=mail, name="x", source=source, status="new"))
    s.commit()


def _klienci():
    return {t["email"]: t for t in client.get("/api/admin/traders", headers=ADMIN).json()}


def test_klient_z_darmowego_lejka_ma_desk_nigeria(swiat):
    t = _trader(swiat, "darmowy")
    _lead(swiat, t.email, "free")

    assert _klienci()[t.email]["desk"] == "free"


def test_klient_z_platnego_lejka_ma_desk_leads(swiat):
    t = _trader(swiat, "platny")
    _lead(swiat, t.email, "money")

    assert _klienci()[t.email]["desk"] == "leads"


def test_klient_bez_leada_nie_ma_desku(swiat):
    """Rejestracja wprost z portalu: nikt go nie przyprowadził, więc brak desku
    jest poprawną odpowiedzią, a nie luką w danych."""
    t = _trader(swiat, "sam")

    assert _klienci()[t.email]["desk"] is None


def test_dopasowanie_po_mailu_ignoruje_wielkosc_liter(swiat):
    """Formularz zapisuje adres tak, jak go wpisano; konto zakładane bywa
    inaczej. Gdyby porównanie było wrażliwe, filtr gubiłby ludzi po cichu."""
    t = _trader(swiat, "wielkosc", mail=f"{PRZEDROSTEK}-Wielkosc@Example.com")
    _lead(swiat, f"{PRZEDROSTEK}-wielkosc@example.com", "free")

    assert _klienci()[t.email]["desk"] == "free"


def test_przy_dwoch_wierszach_tego_samego_adresu_wygrywa_nigeria(swiat):
    """`leads.email` jest UNIQUE, więc jedna osoba ma JEDEN wiersz — ale
    ograniczenie rozróżnia wielkość liter, a dopasowanie do klienta już nie.
    Tak powstaje jedyny osiągalny remis i rozstrzyga go darmowy lejek, bo
    filtr odpowiada na pytanie „kto przyszedł z darmowego"."""
    t = _trader(swiat, "oba")
    _lead(swiat, f"{PRZEDROSTEK}-oba@example.com", "money")
    _lead(swiat, f"{PRZEDROSTEK}-OBA@example.com", "free")

    assert _klienci()[t.email]["desk"] == "free"
