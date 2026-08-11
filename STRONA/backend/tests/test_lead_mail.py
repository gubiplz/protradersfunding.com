"""Mail do leada — dno drabiny kontaktu, gdy nie ma ani Telegrama, ani numeru.

Ten kanał nie kosztuje za sztukę, więc testy pilnują czego innego niż przy
SMS-ie. Pilnują tego, co psuje relację, a nie rachunku:

* że mail wychodzi spod ADRESU MARKI, przez którą człowiek się zgłosił — mail
  spod firmy, o której nie słyszał, rozbiera rozdział marek i ląduje w spamie
  jako obcy nadawca,
* że ten sam tekst nie idzie dwa razy do jednej skrzynki (drugi raz czyta się
  jak automat i unieważnia zdanie o człowieku, który czytał aplikację),
* że odrzucony dostaje SWOJĄ wersję, a nie gratulacje,
* że w historii leada zostaje PEŁNA treść, a nie sam fakt wysyłki,
* że przy braku konfiguracji nie dzieje się nic — zamiast przycisku, który
  zawsze odmawia.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import json  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import lead_mail  # noqa: E402
from app import sms  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, LeadEvent  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))
TG = "https://t.me/probe_desk"
NADAWCA = "Forex Passing <desk@forexpassing.test>"


@pytest.fixture
def poczta(monkeypatch):
    """Konfiguracja i transport-atrapa. Zwraca listę maili do obejrzenia."""
    for pole, wartosc in (("smtp_host", "smtp.probe.test"),
                          ("lead_mail_from", NADAWCA),
                          ("sms_telegram_url", TG)):
        monkeypatch.setattr(lead_mail.settings, pole, wartosc)
    # SMS musi być wyłączony, inaczej drabina kontaktu wybierze jego, nie mail.
    monkeypatch.setattr(sms.settings, "twilio_sid", "")
    poszly = []
    monkeypatch.setattr(lead_mail, "_smtp_transport", poszly.append)
    return poszly


def _lead(*, email=None, telegram=None, outcome="qualified",
          name="Anna Nowak", status="new", phone=None):
    s = SessionLocal()
    lead = Lead(email=email or f"mail{next(LICZNIK)}@probe.test", name=name,
                phone=phone, telegram=telegram, outcome=outcome, status=status)
    s.add(lead); s.commit()
    lid = lead.id; s.close()
    return lid


def _tresc(msg) -> str:
    """Treść maila taka, jaką zobaczy człowiek. Surowy payload nią NIE jest:
    półpauza wymusza quoted-printable, więc w źródle stoi „=E2=80=94" i łamiące
    „=" na końcach linii. Porównywanie tego z tekstem ze `tresc()` sprawdzałoby
    kodowanie, a nie to, co ktoś przeczyta."""
    return msg.get_payload(decode=True).decode("utf-8")


def _zdarzenia(lead_id, kind="email"):
    s = SessionLocal()
    ile = s.query(LeadEvent).filter(LeadEvent.lead_id == lead_id,
                                    LeadEvent.kind == kind).count()
    s.close()
    return ile


# --- adres ---------------------------------------------------------------------

@pytest.mark.parametrize("surowy", [
    "  ktos@example.com  ", "ktos.nowak+tag@sub.example.co.uk",
])
def test_adres_do_uzycia(surowy):
    assert lead_mail.adres(surowy) == surowy.strip()


@pytest.mark.parametrize("surowy", [
    None, "", "brak", "—", "ktos@example", "kto s@example.com",
    "ktos@@example.com", "@example.com", "ktos@.com",
])
def test_adres_nie_do_uzycia(surowy):
    """Leady dopisane z ręki miewają w tym polu wpisy, które adresem nie są."""
    assert lead_mail.adres(surowy) is None


# --- konfiguracja: brak = cisza, nie przycisk, który odmawia --------------------

@pytest.mark.parametrize("brakuje", ["smtp_host", "lead_mail_from", "sms_telegram_url"])
def test_bez_kompletu_konfiguracji_nic_nie_wychodzi(poczta, monkeypatch, brakuje):
    monkeypatch.setattr(lead_mail.settings, brakuje, "")
    assert lead_mail.is_enabled() is False
    assert lead_mail.wyslij("ktos@example.com", "temat", "treść")[0] is False
    assert poczta == []


def test_bez_adresu_telegrama_mail_nie_wychodzi(poczta, monkeypatch):
    """Mail bez linku byłby zaproszeniem donikąd: człowiek raz odpisze „gdzie?",
    drugi raz już nie."""
    monkeypatch.setattr(lead_mail.settings, "sms_telegram_url", "")
    assert lead_mail.is_enabled() is False


# --- nadawca: tu pomyłka zdradza, kto naprawdę pisze ---------------------------

def test_mail_wychodzi_spod_marki_z_landingu(poczta):
    """Nadawca to adres marki, przez którą człowiek się zgłosił. Gdyby był nim
    `MAIL_FROM`, lead zobaczyłby w skrzynce firmę, o której nigdy nie słyszał —
    i tak by ją potraktował."""
    assert lead_mail.wyslij("ktos@example.com", "temat", "treść") == (True, "")
    msg = poczta[0]
    assert msg["From"] == NADAWCA
    # Odpowiedź musi wrócić tam, skąd mail wyszedł — inaczej pierwsza odpowiedź
    # w tej relacji trafia pod szyld, którego lead nie zna.
    assert msg["Reply-To"] == NADAWCA
    assert msg["To"] == "ktos@example.com"


def test_mail_idzie_czystym_tekstem(poczta):
    """Bez HTML-a i to jest decyzja, nie brak: wyśrodkowana karta z logo mówi
    „wysyłka masowa", zanim ktokolwiek przeczyta pierwsze zdanie — a ten mail ma
    sprzedać dokładnie odwrotne wrażenie."""
    lead_mail.wyslij("ktos@example.com", "temat", "treść")
    assert poczta[0].get_content_type() == "text/plain"
    assert _tresc(poczta[0]).strip() != ""


def test_pusta_tresc_nie_leci(poczta):
    assert lead_mail.wyslij("ktos@example.com", "", "treść")[0] is False
    assert lead_mail.wyslij("ktos@example.com", "temat", "   ")[0] is False
    assert poczta == []


def test_padniety_smtp_wraca_jako_powod_nie_wyjatek(poczta, monkeypatch):
    """Różnica wobec `notify.send()`, gdzie błąd SMTP tylko się drukuje. Tam mail
    jest dodatkiem do operacji, która i tak się wydarzyła; tutaj mail JEST
    operacją, a „wysłane" bez pokrycia zostawia leada z odhaczonym kontaktem,
    którego nikt nigdy nie miał."""
    def pada(msg):
        raise OSError("connection refused")
    monkeypatch.setattr(lead_mail, "_smtp_transport", pada)
    poszlo, powod = lead_mail.wyslij("ktos@example.com", "temat", "treść")
    assert poszlo is False and "connection refused" in powod


# --- treść ---------------------------------------------------------------------

def test_obie_wersje_prowadza_na_telegram(poczta):
    temat_tak, tekst_tak = lead_mail.tresc("Anna Nowak", zakwalifikowany=True)
    temat_nie, tekst_nie = lead_mail.tresc("Anna Nowak", zakwalifikowany=False)
    assert tekst_tak != tekst_nie and temat_tak != temat_nie
    for temat, tekst in ((temat_tak, tekst_tak), (temat_nie, tekst_nie)):
        # Cały sens tej wysyłki to wrócić tam, gdzie dział pracuje.
        assert TG in tekst
        assert "Anna" in tekst and "Nowak" not in tekst
        assert "Anna" in temat
        # Jedno wyjście, nie dwa: drugie call-to-action zawsze zabiera kliknięcia
        # pierwszemu.
        assert tekst.count(TG) == 1


def test_odrzucony_nie_dostaje_gratulacji(poczta):
    """Najgorsza możliwa pomyłka w tym module: „you're through" do kogoś, komu
    dział właśnie odmówił."""
    _, tekst = lead_mail.tresc("Anna", zakwalifikowany=False)
    assert "not a yes" in tekst
    assert "through" not in tekst.lower()


def test_lead_bez_imienia_dostaje_zdanie_bez_dziury(poczta):
    temat, tekst = lead_mail.tresc("", zakwalifikowany=True)
    assert "Hi there," in tekst and "there" in temat


def test_obie_wersje_mowia_jak_przestac(poczta):
    """Pierwszy mail w relacji, do człowieka, który zna tylko landing. Bez drogi
    wyjścia jedyną, jaka mu zostaje, jest przycisk „to spam" — a ten uderza
    w dostarczalność wszystkich następnych."""
    for zakwalifikowany in (True, False):
        _, tekst = lead_mail.tresc("Anna", zakwalifikowany=zakwalifikowany)
        assert "stop" in tekst.lower() and "applied" in tekst


# --- panel: przycisk z ręki ----------------------------------------------------

def test_przycisk_wysyla_i_zapisuje_pelna_tresc(poczta):
    lid = _lead()
    r = client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)
    assert r.status_code == 200
    assert TG in _tresc(poczta[0])
    assert _zdarzenia(lid) == 1
    # Historia leada to jedyne miejsce, w którym po tygodniu widać, co dokładnie
    # ten człowiek przeczytał.
    historia = client.get(f"/api/admin/leads/{lid}", headers=ADMIN).json()["events"]
    # Nie `[0]`: ta sama transakcja dopisuje jeszcze zmianę statusu, a historia
    # idzie malejąco po czasie, który dla obu wpisów jest identyczny.
    zdarzenie = next(z for z in historia if z["kind"] == "email")
    assert zdarzenie["detail"] == lead_mail.tresc("Anna Nowak", zakwalifikowany=True)[0]
    assert zdarzenie["body"] == lead_mail.tresc("Anna Nowak", zakwalifikowany=True)[1]


def test_odrzucony_lead_dostaje_swoja_wersje(poczta):
    lid = _lead(outcome="not_qualified")
    client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)
    assert (_tresc(poczta[0]).strip()
            == lead_mail.tresc("Anna Nowak", zakwalifikowany=False)[1].strip())


def test_wyslany_mail_zdejmuje_leada_ze_stanu_new(poczta):
    """Kontakt bez śladu to lead, do którego za tydzień ktoś napisze drugi raz."""
    lid = _lead(status="new")
    assert client.post(f"/api/admin/leads/{lid}/email",
                       headers=ADMIN).json()["status"] == "messaged"


def test_dalsze_statusy_zostaja_nietkniete(poczta):
    """Cofanie „replied" do „napisano" byłoby cofaniem prawdy o rozmowie, która
    już się odbyła."""
    lid = _lead(status="replied")
    assert client.post(f"/api/admin/leads/{lid}/email",
                       headers=ADMIN).json()["status"] == "replied"


def test_drugi_klik_nie_wysyla_drugi_raz(poczta):
    lid = _lead()
    assert client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN).status_code == 200
    r = client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)
    assert r.status_code == 400 and "already" in r.json()["detail"]
    assert len(poczta) == 1 and _zdarzenia(lid) == 1


def test_admin_moze_wymusic_powtorke(poczta):
    lid = _lead()
    client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)
    assert client.post(f"/api/admin/leads/{lid}/email?force=true",
                       headers=ADMIN).status_code == 200
    assert len(poczta) == 2 and _zdarzenia(lid) == 2


def test_zly_adres_wraca_powodem_do_klikajacego(poczta):
    """Jedyne miejsce, gdzie admin dowie się, że w polu adresu jest „brak"."""
    lid = _lead(email="brak")
    r = client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)
    assert r.status_code == 400 and "e-mail address" in r.json()["detail"]
    assert poczta == [] and _zdarzenia(lid) == 0


def test_nieudana_wysylka_nie_zostawia_sladu_w_historii(poczta, monkeypatch):
    """Historia leada mówi, co się STAŁO, a nie co próbowaliśmy zrobić."""
    def pada(msg):
        raise OSError("smtp down")
    monkeypatch.setattr(lead_mail, "_smtp_transport", pada)
    lid = _lead()
    assert client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN).status_code == 400
    assert _zdarzenia(lid) == 0


def test_panel_widzi_ten_sam_tekst_ktory_wyjdzie(poczta):
    """Podgląd składa serwer, nie panel. Gdyby panel składał go u siebie, prędzej
    czy później pokazywałby co innego, niż faktycznie idzie w świat."""
    lid = _lead()
    dane = client.get(f"/api/admin/leads/{lid}", headers=ADMIN).json()
    assert dane["mail_ready"] is True
    client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)
    assert poczta[0]["Subject"] == dane["mail_subject"]
    assert _tresc(poczta[0]).strip() == dane["mail_text"].strip()


def test_bez_konfiguracji_panel_nie_proponuje_przycisku(poczta, monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "")
    lid = _lead()
    assert client.get(f"/api/admin/leads/{lid}",
                      headers=ADMIN).json()["mail_ready"] is False


def test_panel_ma_czym_wyslac_i_gdzie_pokazac_co_poszlo(poczta):
    """Backend bez tego przycisku jest endpointem, którego nikt nie kliknie.

    Cztery rzeczy, bez których ta funkcja nie istnieje dla działu: przycisk
    zapięty na `mail_ready` (inaczej wysyłałby spod nieustawionego nadawcy),
    strzał pod właściwy endpoint, etykieta zdarzenia w historii i sama treść —
    `e.body` to jedyne miejsce, w którym po tygodniu widać, co ten człowiek
    przeczytał."""
    kod = client.get("/static/js/admin-panel.js").text
    assert "sendLeadEmail(" in kod and "l.mail_ready" in kod
    assert "'/api/admin/leads/'+id+'/email'" in kod
    assert "email:'E-mail sent'" in kod
    assert "e.body" in kod and "lead-sent" in kod
    assert "lead-sent{" in client.get("/static/css/admin.css").text


def test_nieznany_lead_to_404(poczta):
    assert client.post("/api/admin/leads/999999/email", headers=ADMIN).status_code == 404


def test_bez_tokenu_admina_ani_kroku(poczta):
    lid = _lead()
    assert client.post(f"/api/admin/leads/{lid}/email").status_code in (401, 403)
    assert poczta == []


# --- drabina kontaktu: Telegram → SMS → mail -----------------------------------

def test_bez_handlea_i_bez_numeru_leci_mail(poczta):
    """„Napisaliśmy" do kogoś, kto nie podał handle'a ani numeru, byłoby
    deklaracją bez pokrycia. Mail ma dokąd pójść zawsze."""
    lid = _lead(telegram=None, phone=None)
    s = SessionLocal()
    try:
        from app.main import _kontakt_zastepczy
        lead = s.get(Lead, lid)
        assert _kontakt_zastepczy(s, lead, "telegram:kto") == "mail poszedł"
        s.commit()
    finally:
        s.close()
    assert len(poczta) == 1 and _zdarzenia(lid) == 1


def test_gdy_da_sie_wyslac_sms_mail_nie_leci(poczta, monkeypatch):
    """SMS czyta się w minutę, mail bywa otwarty wieczorem albo wcale — więc gdy
    jest czym wysłać SMS, mail czeka."""
    for pole, wartosc in (("twilio_sid", "AC_test"), ("twilio_token", "sekret"),
                          ("twilio_from", "+15550001111"), ("sms_telegram_url", TG)):
        monkeypatch.setattr(sms.settings, pole, wartosc)
    monkeypatch.setattr(sms, "_urllib_transport",
                        lambda url, body, headers: (201, json.dumps({"sid": "SM1"}).encode()))
    lid = _lead(telegram=None, phone="+48123456789")
    s = SessionLocal()
    try:
        from app.main import _kontakt_zastepczy
        assert _kontakt_zastepczy(s, s.get(Lead, lid), "telegram:kto") == "SMS poszedł"
        s.commit()
    finally:
        s.close()
    assert poczta == [] and _zdarzenia(lid, "sms") == 1


def test_zepsuty_numer_nie_blokuje_maila(poczta, monkeypatch):
    """Numer krajowy, bez „+", to najczęstsza rzecz w tym polu — ludzie wpisują
    go tak, jak dyktują przez telefon. Twilio go nie przyjmie, a lead nie może
    przez to zostać bez kontaktu, skoro adres mamy."""
    for pole, wartosc in (("twilio_sid", "AC_test"), ("twilio_token", "sekret"),
                          ("twilio_from", "+15550001111"), ("sms_telegram_url", TG)):
        monkeypatch.setattr(sms.settings, pole, wartosc)
    lid = _lead(telegram=None, phone="601234567")
    s = SessionLocal()
    try:
        from app.main import _kontakt_zastepczy
        assert _kontakt_zastepczy(s, s.get(Lead, lid), "telegram:kto") == "mail poszedł"
        s.commit()
    finally:
        s.close()
    assert len(poczta) == 1 and _zdarzenia(lid, "sms") == 0


def test_bez_zadnego_kanalu_dymek_milczy(poczta, monkeypatch):
    """Klikający nie ma wtedy czego naprawić, a komunikat o brakującym kluczu
    przy każdym kliknięciu uczy tylko tego, żeby przestać czytać dymki."""
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "")
    lid = _lead(telegram=None, phone=None)
    s = SessionLocal()
    try:
        from app.main import _kontakt_zastepczy
        assert _kontakt_zastepczy(s, s.get(Lead, lid), "telegram:kto") == ""
    finally:
        s.close()
    assert poczta == []
