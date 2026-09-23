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


@pytest.fixture
def token(monkeypatch):
    """Sekret ingestu na czas testu — w pełnym zestawie ustawienia są już
    wczytane, więc `os.environ` przed importem nic nie zmienia."""
    monkeypatch.setattr(get_settings(), "lead_ingest_token", "sekret-origin", raising=False)
    return {"X-Lead-Token": "sekret-origin"}


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
    assert p.country_via == "signup" and p.json()["country_via"] == "signup"
    assert origin.pochodzenie(Trader(email="a@b.c"), None).country_via is None


def test_kyc_nazwa_kraju_liczy_sie_jako_sygnal():
    p = origin.pochodzenie(Trader(email="a@b.c", kyc_country="Nigeria"), None)
    assert p.free and p.country == "NG" and p.via == ["kyc:NG"]


def test_vpn_kyc_polska_ip_nigeria_decyduje_dokument():
    """O Afryce decyduje NAJBARDZIEJ wiarygodny sygnał: dokument KYC z Polski
    wygrywa z nigeryjskim IP logowania. Sprzeczność zostaje w `via` (chip ją
    pokaże), ale nie chowa człowieka w filtrze."""
    p = origin.pochodzenie(Trader(email="a@b.c", kyc_country="Poland",
                                  last_login_country="NG"), None)
    assert p.country == "PL" and not p.africa and not p.free
    assert p.via == ["login:NG"]


def test_ip_zgloszenia_leada_jest_ostatnim_sygnalem():
    p = origin.pochodzenie(None, Lead(email="a@b.c", source="money",
                                      phone_iso="GB", ip_country="NG"))
    assert p.country == "GB" and not p.free and p.via == ["lead-ip:NG"]


@pytest.mark.parametrize("kraj", ["US", "CA", "AU", "GB", "DE", "AE"])
def test_high_ticket_nigdy_nie_wpada_pod_free_przez_slabszy_sygnal(kraj):
    """Strażnik ekspansji: klient z USA/Kanady/Australii (i każdego innego kraju
    spoza Afryki) z jednym zbłąkanym afrykańskim sygnałem (VPN, stary lead)
    NIE jest free — inaczej filtr chowałby high ticket."""
    przypadki = [
        Trader(email="a@b.c", kyc_country=countries.BY_ISO[kraj][1], last_login_country="NG"),
        Trader(email="a@b.c", phone_country=kraj, signup_country="NG"),
        Trader(email="a@b.c", last_login_country=kraj, signup_country="GH"),
    ]
    for tr in przypadki:
        p = origin.pochodzenie(tr, Lead(email="a@b.c", source="money", ip_country="NG"))
        assert p.country == kraj and not p.free, (kraj, p)
    # Sam lead z płatnego lejka i krajem high ticket: nic do chowania.
    p = origin.pochodzenie(None, Lead(email="a@b.c", source="google", phone_iso=kraj))
    assert p.country == kraj and not p.free and p.desk == "leads"


def test_nigeryjczyk_z_vpn_na_rejestracji_dalej_jest_free():
    """W drugą stronę: prefiks numeru z checkoutu (NG) jest wiarygodniejszy niż
    amerykańskie IP z VPN-a przy rejestracji."""
    p = origin.pochodzenie(Trader(email="a@b.c", phone_country="NG", signup_country="US"), None)
    assert p.country == "NG" and p.free and p.via == ["phone:NG"]


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
    # Ruch po last seen: bez telemetrii nikt nie jest „aktywny dziś".
    assert wiersz["seen_today"] is False and wiersz["active_days_7d"] == 0


def test_zamowienia_niosa_origin_tradera(swiat):
    from app.models import Order
    t = _trader(swiat, signup_country="NG")
    swiat.add(Order(trader_id=t.id, product_key="2step-25k", amount_usd=0.0,
                    status="paid", provider="grant"))
    swiat.commit()
    wiersz = next(o for o in client.get("/api/admin/orders", headers=ADMIN).json()
                  if o["trader_email"] == t.email)
    assert wiersz["origin"]["free"] is True and wiersz["origin"]["country"] == "NG"
    assert wiersz["desk"] is None


def test_lista_klientow_niesie_uchwyt_telegrama_z_leada(swiat):
    t = _trader(swiat)
    lead = _lead(swiat, t.email, "free", telegram="ada_obi")
    wiersz = {x["email"]: x for x in
              client.get("/api/admin/traders", headers=ADMIN).json()}[t.email]
    assert wiersz["telegram"] == "ada_obi" and wiersz["lead_id"] == lead.id
    sam = _trader(swiat)
    wiersz = {x["email"]: x for x in
              client.get("/api/admin/traders", headers=ADMIN).json()}[sam.email]
    assert wiersz["telegram"] is None and wiersz["lead_id"] is None


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


def test_ingest_zapisuje_kraj_z_ip(swiat, token):
    mail = f"{PRZEDROSTEK}-ingest@example.com"
    r = client.post("/api/leads/ingest", headers=token,
                    json={"email": mail, "name": "Ada", "source": "free",
                          "ipCountry": "ng", "phone": "+2348012345678"})
    assert r.status_code == 200, r.text
    lead = swiat.query(Lead).filter(Lead.email == mail).one()
    assert lead.ip_country == "NG" and lead.phone_iso == "NG"
    dane = client.get(f"/api/admin/leads/{lead.id}", headers=ADMIN).json()
    assert dane["ip_country"] == "NG" and dane["origin"]["free"]


def test_ingest_odrzuca_xx_i_smieci_w_kraju_ip(swiat, token):
    mail = f"{PRZEDROSTEK}-xx@example.com"
    client.post("/api/leads/ingest", headers=token,
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
    # Activity, Clients, Payouts, Orders, Accounts, KYC, Tickets — jeden checkbox, jeden werdykt.
    assert kod.count("${freeCheckbox()}") == 7
    assert "FREE_SHOWN||!isFreeOrigin(t)" in kod
    assert "FREE_SHOWN||!isFreeOrigin(r)" in kod and "FREE_SHOWN||!isFreeOrigin(o)" in kod
    assert "FREE_SHOWN||!isFreeOrigin(a)" in kod
    assert "if(VIEW==='activity')renderActivity();" in kod
    assert "else if(VIEW==='payouts')renderPayoutsView();" in kod
    assert "else if(VIEW==='orders')renderOrders();" in kod
    assert "else if(VIEW==='accounts')renderAccounts();" in kod
    # Clients, Activity, Tickets, KYC (karta i historia).
    assert kod.count("${freeChip(t)}") == 5 and "${freeChip(r)}" in kod
    assert "${freeChip(o)}" in kod and "${freeChip(a)}" in kod
    assert "t.origin.free" in kod
    # Filtr kraju: siedem zakładek, jedna zapamiętana wartość (localStorage),
    # z opcją „Any country", która wyłącza filtrowanie.
    assert kod.count("${countrySelect(") == 7
    assert "localStorage.setItem('pf_admin_country'" in kod
    assert '<option value="">Any country</option>' in kod


def test_kyc_i_tickets_niosa_origin_pod_filtry(swiat):
    """Checkbox Free i filtr kraju w zakładkach KYC i Tickets — wiersz niesie
    `origin` tradera, a kraj z KYC wygrywa z IP rejestracji."""
    from app.models import SupportTicket
    t = _trader(swiat, signup_country="US", kyc_status="pending", kyc_country="Nigeria")
    swiat.add(SupportTicket(trader_id=t.id, subject="Payout question", status="open"))
    swiat.commit()
    kyc = {w["email"]: w for w in client.get("/api/admin/kyc", headers=ADMIN).json()["pending"]}
    assert kyc[t.email]["origin"]["country"] == "NG" and kyc[t.email]["origin"]["free"] is True
    bilet = next(b for b in client.get("/api/admin/tickets", headers=ADMIN).json()
                 if b["trader_email"] == t.email)
    assert bilet["origin"]["country"] == "NG" and bilet["origin"]["africa"] is True
    kod = client.get("/static/js/admin-panel.js").text
    assert "countrySelect(list,'renderTickets')" in kod
    assert "countrySelect(wszystkie,'renderKyc')" in kod
    assert "countrySelect(widoczne,'renderPayoutsView')" in kod
    assert "else if(VIEW==='kyc')renderKyc();" in kod
    assert "else if(VIEW==='tickets')renderTickets();" in kod


def test_lista_kont_niesie_origin_wlasciciela(swiat):
    t = _trader(swiat, signup_country="NG")
    swiat.add(Account(trader_id=t.id, login=f"8{next(LICZNIK)}", source="grant",
                      grant_note="Free program", status="active",
                      balance=25000, equity=25000))
    swiat.commit()
    konta = client.get("/api/accounts?free=1&imported=1", headers=ADMIN).json()
    wiersz = next(a for a in konta if a["trader_email"] == t.email)
    assert wiersz["origin"]["free"] is True and wiersz["origin"]["country"] == "NG"
    assert "grant:free program" in wiersz["origin"]["via"]


def test_login_aktualizuje_kraj_ostatniego_logowania(swiat):
    t = _trader(swiat)
    t.email_verified = True
    swiat.commit()
    r = client.post("/api/auth/login", headers={"x-vercel-ip-country": "gh"},
                    json={"email": t.email, "password": "haslo1234"})
    assert r.status_code == 200, r.text
    swiat.refresh(t)
    assert t.last_login_country == "GH" and t.signup_country is None
