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
from urllib.parse import unquote  # noqa: E402

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
# Dwa RÓŻNE byty: do pierwszego się pisze, do drugiego dołącza. Testy trzymają
# je osobno, bo pomylenie ich w kodzie nie wywraca niczego — po prostu wysyła
# odrzuconego tam, gdzie nie ma pola do pisania.
KANAL = "https://t.me/probe_channel"
NADAWCA = "Forex Passing <desk@forexpassing.test>"


@pytest.fixture
def poczta(monkeypatch):
    """Konfiguracja i transport-atrapa. Zwraca listę maili do obejrzenia."""
    for pole, wartosc in (("smtp_host", "smtp.probe.test"),
                          ("lead_mail_from", NADAWCA),
                          ("lead_telegram_channel_url", KANAL),
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
    """Wersja TEKSTOWA maila, taka, jaką zobaczy człowiek. Surowy payload nią
    NIE jest z dwóch powodów: od 2026-08-11 mail jest multipartem, więc
    `get_payload` na całości zwraca listę części zamiast treści, a półpauza
    wymusza quoted-printable, więc w źródle stoi „=E2=80=94" i łamiące „=" na
    końcach linii. Porównywanie tego z tekstem ze `tresc()` sprawdzałoby
    strukturę i kodowanie, a nie to, co ktoś przeczyta."""
    return _czesc(msg, "plain")


def _czesc(msg, rodzaj: str) -> str:
    return msg.get_body(preferencelist=(rodzaj,)).get_payload(
        decode=True).decode("utf-8")


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


@pytest.mark.parametrize("pole,zmienna", [
    ("smtp_host", "SMTP_HOST"), ("lead_mail_from", "LEAD_MAIL_FROM"),
    ("sms_telegram_url", "SMS_TELEGRAM_URL")])
def test_panel_mowi_ktorej_zmiennej_brakuje(poczta, monkeypatch, pole, zmienna):
    """Skutkiem braku konfiguracji jest BRAK przycisku, czyli nic — a nic wygląda
    tak samo jak zepsuta funkcja. Pasek stanu ma nazwać zmienną po imieniu,
    inaczej ustawianie tego jest zgadywanką po jednej na deploy."""
    monkeypatch.setattr(lead_mail.settings, pole, "")
    assert lead_mail.czego_brakuje() == [zmienna]
    dane = client.get("/api/stats", headers=ADMIN).json()
    assert dane["lead_mail_missing"] == [zmienna]


def test_komplet_konfiguracji_nie_ma_na_co_narzekac(poczta):
    assert lead_mail.czego_brakuje() == []
    assert client.get("/api/stats", headers=ADMIN).json()["lead_mail_missing"] == []


def test_sms_tez_mowi_czego_mu_brakuje(poczta):
    """Ta sama pułapka co przy mailu: `SMS_TELEGRAM_URL` brzmi jak ustawienie
    SMS-a, a wyłącza oba kanały naraz."""
    braki = client.get("/api/stats", headers=ADMIN).json()["lead_sms_missing"]
    assert "TWILIO_SID" in braki and "SMS_TELEGRAM_URL" not in braki


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


def test_mail_niesie_tekst_ORAZ_html(poczta):
    """HTML doszedł 2026-08-11, na prośbę właściciela: lead widzi markę landingu
    pierwszy raz od zgłoszenia i mail ma ją nieść. Wersja tekstowa ZOSTAJE i to
    jest tu właściwa asercja — mail bez niej dostaje punkty karne od filtrów,
    a w kliencie z wyłączoną grafiką bywa pusty."""
    lead_mail.wyslij("ktos@example.com", "temat", "treść")
    msg = poczta[0]
    assert msg.get_content_type() == "multipart/alternative"
    assert _tresc(msg).strip() == "treść"
    assert "treść" in _czesc(msg, "html")


def test_nadawca_dostaje_nazwe_marki_nawet_gdy_nie_ma_jej_w_konfiguracji(
        poczta, monkeypatch):
    """Gmail pokazuje nazwę wyświetlaną, a gdy jej nie ma — sam człon przed
    małpą. `contact` w skrzynce nie mówi leadowi nic o marce, przez którą się
    zgłosił, a to pierwsza rzecz, na którą patrzy, decydując, czy otworzyć."""
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from",
                        "contact@forexpassing.test")
    lead_mail.wyslij("ktos@example.com", "temat", "treść")
    assert poczta[0]["From"] == "Forex Passing <contact@forexpassing.test>"


def test_html_ma_dokladnie_jedno_wyjscie(poczta):
    """Ta sama zasada co w tekście: drugie call-to-action zawsze zabiera
    kliknięcia pierwszemu."""
    _, tekst = lead_mail.tresc("Anna", zakwalifikowany=True)
    kod = lead_mail._html_z_tekstu(tekst)
    assert kod.count(f'href="{TG}?text=') == 1
    # Surowy URL nie ma prawa zostać obok przycisku jako goły akapit.
    assert f">{TG}<" not in kod


def test_html_pokazuje_logo_marki_z_landingu(poczta, monkeypatch):
    """Logo MUSI stać na domenie marki z landingu. Obrazek zaciągany z domeny
    tej firmy zdradza w kliencie pocztowym dokładnie to, czego pilnuje
    `lead_mail_from` — i to zanim lead przeczyta pierwsze zdanie."""
    monkeypatch.setattr(lead_mail.settings, "lead_mail_logo_url",
                        "https://forexpassing.test/logo-email.png")
    kod = lead_mail._html_z_tekstu(lead_mail.tresc("Anna", zakwalifikowany=True)[1])
    assert 'src="https://forexpassing.test/logo-email.png"' in kod


def test_bez_logo_mail_i_tak_wychodzi(poczta, monkeypatch):
    """Kanał, który staje przez kosmetykę, nie dowozi nic."""
    monkeypatch.setattr(lead_mail.settings, "lead_mail_logo_url", "")
    kod = lead_mail._html_z_tekstu(lead_mail.tresc("Anna", zakwalifikowany=True)[1])
    assert "<img" not in kod and "Forex Passing" in kod
    assert lead_mail.wyslij("ktos@example.com", "temat", "treść")[0] is True


def test_imie_z_bazy_nie_wstrzykuje_html(poczta):
    """Imię leada to pole z formularza, czyli tekst od obcego. W wersji HTML
    trafia do dokumentu, więc musi być zescapowane — inaczej wystarczy wpisać
    znacznik w formularz, żeby zmienić maila, którego wysyła dział."""
    _, tekst = lead_mail.tresc("<b>Anna", zakwalifikowany=True)
    kod = lead_mail._html_z_tekstu(tekst)
    assert "<b>Anna" not in kod and "&lt;b&gt;Anna" in kod


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
        assert "https://t.me/" in tekst
        # Nazwisko wolno nieść wyłącznie linkowi — w prozie „Hi Anna Nowak"
        # czyta się jak korespondencja seryjna i przewraca jedyną rzecz, którą
        # ten mail sprzedaje. W gotowej wiadomości do działu jest odwrotnie:
        # tam lead PRZEDSTAWIA SIĘ obcemu i po nazwisku dział go dopasowuje.
        proza = "\n".join(w for w in tekst.split("\n") if not w.startswith("http"))
        assert "Anna" in proza and "Nowak" not in proza
        assert "Anna" in temat
        # Jedno wyjście, nie dwa: drugie call-to-action zawsze zabiera kliknięcia
        # pierwszemu.
        assert len([w for w in tekst.split("\n") if w.startswith("http")]) == 1


def test_kazda_wersja_idzie_GDZIE_INDZIEJ(poczta):
    """Zakwalifikowanemu obiecujemy rozmowę, więc musi wylądować tam, gdzie da
    się napisać. Odrzuconemu nie obiecujemy jej wcale — jego link prowadzi na
    kanał, gdzie pola do pisania nie ma. Zamiana tych dwóch adresów niczego nie
    wywraca i dlatego jest najgroźniejsza: mail wychodzi, a połowa leadów
    trafia w ścianę."""
    _, tak = lead_mail.tresc("Anna Nowak", zakwalifikowany=True)
    _, nie = lead_mail.tresc("Anna Nowak", zakwalifikowany=False)
    assert TG in tak and KANAL not in tak
    assert KANAL in nie and TG not in nie


def test_odrzuconego_nikt_nie_prosi_o_wiadomosc(poczta):
    """Na kanale nie ma czego wypełnić, więc `?text=` byłby tam martwy, a
    zdanie o gotowej wiadomości — kłamstwem."""
    _, nie = lead_mail.tresc("Anna Nowak", zakwalifikowany=False)
    assert "?text=" not in nie
    assert "conversation" not in nie and "already written" not in nie


def test_przycisk_nazywa_to_co_jest_po_drugiej_stronie(poczta):
    kod_tak = lead_mail._html_z_tekstu(lead_mail.tresc("Anna", zakwalifikowany=True)[1])
    kod_nie = lead_mail._html_z_tekstu(lead_mail.tresc("Anna", zakwalifikowany=False)[1])
    assert "Message the desk on Telegram" in kod_tak and "Join" not in kod_tak
    assert "Join us on Telegram" in kod_nie
    assert "Message the desk" not in kod_nie


def test_odrzucony_nie_dostaje_gratulacji(poczta):
    """Najgorsza możliwa pomyłka w tym module: „you're through" do kogoś, komu
    dział właśnie odmówił."""
    _, tekst = lead_mail.tresc("Anna", zakwalifikowany=False)
    assert "not a yes" in tekst
    assert "through" not in tekst.lower()


def test_lead_bez_imienia_dostaje_zdanie_bez_dziury(poczta):
    temat, tekst = lead_mail.tresc("", zakwalifikowany=True)
    assert "Hi there," in tekst and "there" in temat


# --- gotowa pierwsza wiadomość do działu ---------------------------------------

def _prefill(imie):
    return unquote(lead_mail._link_do_dzialu(imie).split("?text=", 1)[1])


def test_lead_klika_i_ma_juz_co_wyslac(poczta):
    assert _prefill("Anna Nowak") == ("Hi. This is Anna Nowak. "
                                      "My application came back a yes.")


def test_wiadomosc_niesie_nazwisko_bo_dzial_dostaje_ja_od_obcego(poczta):
    """Handle nadawcy nic działowi nie mówi. Samo imię przy dwóch Annach
    w tym samym tygodniu też nie."""
    assert "Anna Nowak" in _prefill("Anna Nowak")


def test_bez_imienia_wiadomosc_nie_ma_dziury(poczta):
    assert _prefill("") == "Hi. My application came back a yes."
    assert _prefill(None) == "Hi. My application came back a yes."


def test_link_zostaje_jednym_slowem(poczta):
    """`_html_z_tekstu` robi przycisk tylko z akapitu, który jest SAMYM adresem.
    Spacja w `?text=` rozbiłaby ten warunek i lead dostałby goły URL w treści."""
    link = lead_mail._link_do_dzialu("Anna Nowak")
    assert " " not in link and "%20" in link
    kod = lead_mail._html_z_tekstu(lead_mail.tresc("Anna Nowak",
                                                   zakwalifikowany=True)[1])
    assert kod.count("<a ") == 1 and f'href="{link}"' in kod


def test_adres_dzialu_z_wlasnym_pytajnikiem_nie_peka(poczta, monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "sms_telegram_url",
                        "https://t.me/probe_desk?start=lead")
    assert "?start=lead&text=" in lead_mail._link_do_dzialu("Anna")


def test_sms_zostaje_przy_gołym_adresie(poczta):
    """Ten sam adres bierze `sms.py`, a tam każdy znak liczy się do segmentu.
    Gotowa wiadomość rozbiłaby SMS-a na dwa i podniosła koszt każdej wysyłki."""
    assert "?text=" not in sms.tresc("Anna Nowak", zakwalifikowany=True)


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
    assert "leadChannel('Lead e-mail',s.lead_mail_missing)" in kod
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


# --- webhook Brevo: czy list w ogóle doszedł ------------------------------------
# Do tej pory historia leada kończyła się na „oddaliśmy do wysyłki". Mail odbity
# od nieistniejącej skrzynki wyglądał w niej identycznie jak doręczony, więc
# dział czekał na odpowiedź, która nie miała prawa przyjść.

SEKRET_BREVO = "sekret-brevo"
TEMAT_OBCY = "Your payout is on the way"


@pytest.fixture
def brevo(monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "brevo_webhook_secret", SEKRET_BREVO)


def _wyslany():
    """Lead, do którego mail już poszedł — punkt wyjścia dla każdego zdarzenia."""
    adres = f"mail{next(LICZNIK)}@probe.test"
    lid = _lead(email=adres)
    client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)
    return lid, adres, lead_mail.tresc("Anna Nowak", zakwalifikowany=True)[0]


def _od_brevo(adres, temat, event="delivered", token=SEKRET_BREVO, **reszta):
    return client.post(f"/api/brevo/webhook?token={token}",
                       json={"event": event, "email": adres, "subject": temat, **reszta})


def _detale(lead_id, kind="delivery"):
    s = SessionLocal()
    try:
        return [z.detail for z in s.query(LeadEvent).filter(
            LeadEvent.lead_id == lead_id, LeadEvent.kind == kind
        ).order_by(LeadEvent.id).all()]
    finally:
        s.close()


def test_bez_sekretu_webhook_nie_istnieje(poczta):
    """Brevo nie podpisuje wywołań, więc nieustawiony sekret nie może znaczyć
    „wpuszczaj wszystkich" — to byłby otwarty dopisywacz do historii leadów."""
    _, adres, temat = _wyslany()
    assert _od_brevo(adres, temat, token="cokolwiek").status_code == 401


def test_cudzy_token_nie_wchodzi(poczta, brevo):
    _, adres, temat = _wyslany()
    assert _od_brevo(adres, temat, token="nie-ten").status_code == 401


def test_doreczenie_wchodzi_do_historii(poczta, brevo):
    lid, adres, temat = _wyslany()
    assert _od_brevo(adres, temat).status_code == 200
    assert _detale(lid) == ["delivered"]


def test_odbicie_niesie_powod(poczta, brevo):
    """Sam „hard bounce" mówi, że nie doszło. Dopiero powód mówi, czy adres jest
    literówką, czy skrzynka nie istnieje — a to są dwie różne decyzje działu."""
    lid, adres, temat = _wyslany()
    _od_brevo(adres, temat, event="hard_bounce", reason="unknown recipient")
    assert _detale(lid) == ["hard bounce: unknown recipient"]


def test_ponowione_wywolanie_nie_dubluje(poczta, brevo):
    """Brevo ponawia. Interesuje nas, CO się z listem stało, a nie ile razy nam
    o tym powiedziano."""
    lid, adres, temat = _wyslany()
    _od_brevo(adres, temat); _od_brevo(adres, temat); _od_brevo(adres, temat)
    assert _detale(lid) == ["delivered"]


def test_kolejny_inny_wynik_dopisuje_sie(poczta, brevo):
    """Skrzynka chwilowo pełna, a potem doręczone — to jedna historia w dwóch
    krokach i oba są prawdziwe."""
    lid, adres, temat = _wyslany()
    _od_brevo(adres, temat, event="soft_bounce", reason="mailbox full")
    _od_brevo(adres, temat)
    assert _detale(lid) == ["soft bounce: mailbox full", "delivered"]


def test_drugi_mail_ma_wlasny_wynik_doreczenia(poczta, brevo):
    """Dedup ma odsiewać RETRY Brevo, nie kolejne maile: „delivered: sent"
    brzmi identycznie przy każdej wysyłce, a przez to wynik każdego maila poza
    pierwszym ginął i historia wyglądała, jakby Brevo zamilkło w południe."""
    lid, adres, temat = _wyslany()
    _od_brevo(adres, temat)
    assert client.post(f"/api/admin/leads/{lid}/email-custom", headers=ADMIN,
                       json={"subject": "Quick follow-up",
                             "body": "Still there?"}).status_code == 200
    _od_brevo(adres, "Quick follow-up")
    _od_brevo(adres, "Quick follow-up")  # retry Brevo — nadal bez dubla
    assert _detale(lid) == ["delivered", "delivered"]


def test_mail_do_tradera_nie_udaje_maila_do_leada(poczta, brevo):
    """Tym samym kontem Brevo wychodzą maile z `notify.py`, a jeden człowiek
    bywa naraz leadem i traderem. Bez dopasowania po temacie potwierdzenie
    wypłaty zapisałoby się jako doręczenie maila do leada."""
    lid, adres, _ = _wyslany()
    assert _od_brevo(adres, TEMAT_OBCY).status_code == 200
    assert _detale(lid) == []


def test_otwarcie_nie_jest_faktem(poczta, brevo):
    """Piksel śledzący kłamie w obie strony — klient pocztowy pobiera go bez
    człowieka, a wyłączone obrazki chowają tego, kto naprawdę przeczytał."""
    lid, adres, temat = _wyslany()
    _od_brevo(adres, temat, event="opened")
    assert _detale(lid) == []


def test_zdarzenie_o_kims_kogo_nie_znamy_przechodzi_bez_szkody(brevo):
    """Webhook jest kontem-wide: lecą przez niego wszystkie maile firmy, także
    do ludzi, których w tabeli leadów nie ma."""
    assert _od_brevo("nikt@obcy.test", "cokolwiek").status_code == 200
