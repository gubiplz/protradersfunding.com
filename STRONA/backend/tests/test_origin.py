"""Pochodzenie klienta: darmowy lejek, grant „free program", Afryka.

Do 2026-09 checkbox „Free" chował wyłącznie ludzi z leadem `source=free*`.
Nigeryjczyk, który zarejestrował się sam (albo z innego maila niż w ankiecie),
wyglądał jak płacący klient. `origin.pochodzenie()` składa werdykt z WSZYSTKICH
sygnałów — leada, grantu, kraju z KYC, numeru, IP przy rejestracji/logowaniu
i IP zgłoszenia — i mówi w `via`, skąd się wziął. Reguła `desk` (routing
Telegrama) zostaje nietknięta: to inne pytanie.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("LEAD_INGEST_TOKEN", "sekret-origin")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, countries, origin  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Base, Lead, LeadEvent, LeadReminder, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
PRZEDROSTEK = "origin-test"
LICZNIK = iter(range(10000))


@pytest.fixture
def swiat():
    s = SessionLocal()
    try:
        yield s
    finally:
        try:
            s.rollback()
            idki = [t.id for t in s.query(Trader).filter(
                Trader.email.like(f"%{PRZEDROSTEK}%")).all()]
            if idki:
                # Rejestracja i logowanie zostawiają ślady w kilku tabelach
                # (telemetria, powiadomienia, ...) — kasujemy każdą, która ma
                # `trader_id`, zamiast zgadywać listę.
                for mapper in Base.registry.mappers:
                    tabela = mapper.local_table
                    if "trader_id" in tabela.c:
                        s.execute(tabela.delete().where(tabela.c.trader_id.in_(idki)))
            leady = [l.id for l in s.query(Lead).filter(
                Lead.email.like(f"%{PRZEDROSTEK}%")).all()]
            if leady:
                s.query(LeadEvent).filter(LeadEvent.lead_id.in_(leady)).delete(
                    synchronize_session=False)
                s.query(LeadReminder).filter(LeadReminder.lead_id.in_(leady)).delete(
                    synchronize_session=False)
            s.query(Lead).filter(Lead.email.like(f"%{PRZEDROSTEK}%")).delete(
                synchronize_session=False)
            s.query(Trader).filter(Trader.email.like(f"%{PRZEDROSTEK}%")).delete(
                synchronize_session=False)
            s.commit()
        finally:
            s.close()


def _trader(s, mail=None, **pola):
    t = Trader(email=mail or f"{PRZEDROSTEK}-{next(LICZNIK)}@example.com",
               password_hash=auth.hash_password("haslo1234"),
               referral_code=auth.secrets.token_hex(4), full_name="Test", **pola)
    s.add(t)
    s.commit()
    return t


def _lead(s, mail, source="money", **pola):
    lead = Lead(email=mail, name="x", source=source, status="new", **pola)
    s.add(lead)
    s.commit()
    return lead


# --- czysta funkcja ------------------------------------------------------------

def test_afryka_zna_nigerie_i_nie_zna_polski():
    assert countries.is_africa("NG") and countries.is_africa("ng ")
    assert countries.is_africa("ZA") and countries.is_africa("EG")
    assert not countries.is_africa("PL") and not countries.is_africa(None)
    assert len(countries.AFRICA) >= 54


def test_iso_z_nazwy_kraju_jak_w_kyc():
    assert countries.iso_from_name("Nigeria") == "NG"
    assert countries.iso_from_name("nigeria") == "NG"
    assert countries.iso_from_name("PL") == "PL"
    assert countries.iso_from_name("Atlantyda") is None
    assert countries.iso_from_name("") is None


def test_sam_lead_free_daje_free_bez_kraju():
    p = origin.pochodzenie(None, Lead(email="a@b.c", source="freeaccount"))
    assert p.desk == "free" and p.free and not p.africa
    assert p.country is None and p.via == ["lead:freeaccount"]


def test_lead_platny_z_polski_nie_jest_free():
    p = origin.pochodzenie(Trader(email="a@b.c", phone_country="PL"),
                           Lead(email="a@b.c", source="money", phone_iso="PL"))
    assert p.desk == "leads" and not p.free and p.country == "PL" and p.via == []


def test_sam_grant_free_program_daje_free():
    p = origin.pochodzenie(Trader(email="a@b.c"), None, free_grant=True)
    assert p.desk is None and p.free and p.via == ["grant:free program"]


def test_trader_bez_leada_z_nigeryjskim_ip_jest_free():
    p = origin.pochodzenie(Trader(email="a@b.c", signup_country="NG"), None)
    assert p.free and p.africa and p.country == "NG" and p.via == ["signup:NG"]


def test_kyc_nazwa_kraju_liczy_sie_jako_sygnal():
    p = origin.pochodzenie(Trader(email="a@b.c", kyc_country="Nigeria"), None)
    assert p.free and p.country == "NG" and p.via == ["kyc:NG"]


def test_vpn_kyc_polska_ip_nigeria_kraj_z_dokumentu_ale_free():
    """Dokument wygrywa jako `country`, ale JEDEN afrykański sygnał wystarcza
    na `free` — i `via` pokazuje oba, żeby człowiek widział sprzeczność."""
    p = origin.pochodzenie(Trader(email="a@b.c", kyc_country="Poland",
                                  last_login_country="NG"), None)
    assert p.country == "PL" and p.africa and p.free
    assert p.via == ["login:NG"]


def test_ip_zgloszenia_leada_jest_ostatnim_sygnalem():
    p = origin.pochodzenie(None, Lead(email="a@b.c", source="money",
                                      phone_iso="GB", ip_country="NG"))
    assert p.country == "GB" and p.free and p.via == ["lead-ip:NG"]


# --- endpointy -----------------------------------------------------------------

def test_lista_klientow_ma_origin_dla_nigeryjczyka_bez_leada(swiat):
    t = _trader(swiat, last_login_country="NG")
    wiersz = {x["email"]: x for x in
              client.get("/api/admin/traders", headers=ADMIN).json()}[t.email]
    assert wiersz["desk"] is None                     # jak dotąd: bez leada
    assert wiersz["origin"]["free"] is True
    assert wiersz["origin"]["country"] == "NG"
    assert wiersz["origin"]["via"] == ["login:NG"]


def test_lista_klientow_grant_free_program_liczy_sie_jako_free(swiat):
    t = _trader(swiat)
    swiat.add(Account(trader_id=t.id, login=f"9{next(LICZNIK)}", source="grant",
                      grant_note="Free program", status="active",
                      balance=25000, equity=25000))
    swiat.commit()
    wiersz = {x["email"]: x for x in
              client.get("/api/admin/traders", headers=ADMIN).json()}[t.email]
    assert wiersz["origin"]["free"] and "grant:free program" in wiersz["origin"]["via"]


def test_activity_zwraca_desk_i_origin(swiat):
    t = _trader(swiat)
    _lead(swiat, t.email, "free")
    wiersz = {x["email"]: x for x in
              client.get("/api/admin/journal", headers=ADMIN).json()["items"]}[t.email]
    assert wiersz["desk"] == "free" and wiersz["origin"]["free"] is True


def test_lista_leadow_ma_origin_z_sygnalow_tradera(swiat):
    t = _trader(swiat, signup_country="NG")
    _lead(swiat, t.email, "money", phone_iso="GB")
    lead = {x["email"]: x for x in
            client.get("/api/admin/leads", headers=ADMIN).json()}[t.email]
    assert lead["desk"] == "leads"
    assert lead["origin"]["free"] and lead["origin"]["via"] == ["signup:NG"]
    # Sygnały TRADERA (KYC, numer, logowanie, rejestracja) idą przed sygnałami
    # leada — konto wie o człowieku więcej niż ankieta sprzed miesiąca.
    assert lead["origin"]["country"] == "NG"


def test_ingest_zapisuje_kraj_z_ip(swiat):
    mail = f"{PRZEDROSTEK}-ingest@example.com"
    r = client.post("/api/leads/ingest", headers={"X-Lead-Token": "sekret-origin"},
                    json={"email": mail, "name": "Ada", "source": "free",
                          "ipCountry": "ng", "phone": "+2348012345678"})
    assert r.status_code == 200, r.text
    lead = swiat.query(Lead).filter(Lead.email == mail).one()
    assert lead.ip_country == "NG" and lead.phone_iso == "NG"
    dane = client.get(f"/api/admin/leads/{lead.id}", headers=ADMIN).json()
    assert dane["ip_country"] == "NG" and dane["origin"]["free"]


def test_ingest_odrzuca_xx_i_smieci_w_kraju_ip(swiat):
    mail = f"{PRZEDROSTEK}-xx@example.com"
    client.post("/api/leads/ingest", headers={"X-Lead-Token": "sekret-origin"},
                json={"email": mail, "name": "Ada", "ipCountry": "XX"})
    assert swiat.query(Lead).filter(Lead.email == mail).one().ip_country is None


def test_signup_zapisuje_kraj_z_naglowka_vercela(swiat):
    mail = f"{PRZEDROSTEK}-signup@example.com"
    r = client.post("/api/auth/signup", headers={"x-vercel-ip-country": "NG"},
                    json={"email": mail, "password": "haslo12345",
                          "full_name": "Ada Obi", "terms_accepted": True})
    assert r.status_code == 200, r.text
    t = swiat.query(Trader).filter(Trader.email == mail).one()
    assert t.signup_country == "NG" and t.last_login_country == "NG"


def test_signup_bez_naglowka_zostawia_none(swiat):
    mail = f"{PRZEDROSTEK}-nohdr@example.com"
    client.post("/api/auth/signup", json={"email": mail, "password": "haslo12345",
                                          "full_name": "Ada Obi", "terms_accepted": True})
    t = swiat.query(Trader).filter(Trader.email == mail).one()
    assert t.signup_country is None and t.last_login_country is None


def test_panel_ma_jeden_checkbox_free_w_obu_zakladkach():
    """Activity i Clients rysują TEN SAM checkbox i filtrują TYM SAMYM werdyktem
    (`origin.free`), a przełącznik przerysowuje bieżący widok, nie zawsze Clients."""
    kod = client.get("/static/js/admin-panel.js").text
    assert kod.count("${freeCheckbox()}") == 2
    assert "FREE_SHOWN||!isFreeOrigin(t)" in kod
    assert "if(VIEW==='activity')renderActivity();else renderClients();" in kod
    assert kod.count("${freeChip(t)}") == 2
    assert "t.origin.free" in kod


def test_login_aktualizuje_kraj_ostatniego_logowania(swiat):
    t = _trader(swiat)
    t.email_verified = True
    swiat.commit()
    r = client.post("/api/auth/login", headers={"x-vercel-ip-country": "gh"},
                    json={"email": t.email, "password": "haslo1234"})
    assert r.status_code == 200, r.text
    swiat.refresh(t)
    assert t.last_login_country == "GH" and t.signup_country is None
