"""SMS do leada — wyjście awaryjne, gdy Telegram do kogoś nie dochodzi.

Ta wysyłka kosztuje za sztukę i jest nieodwracalna, więc testy pilnują nie tego,
czy „działa", tylko czterech rzeczy, które mogą zaboleć: że nie idzie do obcej
osoby (numer bez kierunkowego jest odrzucany, a nie doklejany na oko), że nie
idzie dwa razy do tej samej, że powód odmowy dociera do człowieka, który kliknął,
i że przy braku konfiguracji nie dzieje się nic — zamiast przycisku, który
zawsze odmawia.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import base64  # noqa: E402
import json  # noqa: E402
import urllib.parse  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as glowny  # noqa: E402
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


@pytest.fixture
def twilio(monkeypatch):
    """Konfiguracja i transport-atrapa. Zwraca listę wysyłek do obejrzenia."""
    for pole, wartosc in (("twilio_sid", "AC_test"), ("twilio_token", "sekret"),
                          ("twilio_from", "+15550001111"), ("sms_telegram_url", TG)):
        monkeypatch.setattr(sms.settings, pole, wartosc)
    poszly = []

    def transport(url, body, headers):
        poszly.append({"url": url, "pola": dict(urllib.parse.parse_qsl(body.decode())),
                       "headers": headers})
        return 201, json.dumps({"sid": "SM1", "status": "queued"}).encode()

    monkeypatch.setattr(sms, "_urllib_transport", transport)
    return poszly


def _lead(*, phone="+48123456789", telegram=None, outcome="qualified",
          name="Anna Nowak", status="new"):
    s = SessionLocal()
    lead = Lead(email=f"sms{next(LICZNIK)}@probe.test", name=name, phone=phone,
                telegram=telegram, outcome=outcome, status=status)
    s.add(lead); s.commit()
    lid = lead.id; s.close()
    return lid


def _zdarzenia(lead_id, kind="sms"):
    s = SessionLocal()
    ile = s.query(LeadEvent).filter(LeadEvent.lead_id == lead_id,
                                    LeadEvent.kind == kind).count()
    s.close()
    return ile


# --- numer: to jest miejsce, w którym pomyłka trafia w obcego człowieka -------

@pytest.mark.parametrize("surowy,oczekiwany", [
    ("+48 123 456 789", "+48123456789"),
    ("+48-123-456-789", "+48123456789"),
    ("0048123456789", "+48123456789"),
    ("+1 (555) 000-1111", "+15550001111"),
])
def test_numer_sprowadzony_do_e164(surowy, oczekiwany):
    assert sms.numer_e164(surowy) == oczekiwany


@pytest.mark.parametrize("surowy", ["123456789", "601 234 567", "", None, "+123",
                                    "+1234567890123456", "brak"])
def test_numer_bez_kierunkowego_odrzucony(surowy):
    """Doklejenie kraju „na oko" to SMS do kogoś zupełnie innego — płatny,
    z treścią o cudzym zgłoszeniu i nie do cofnięcia. Wolimy nie wysłać."""
    assert sms.numer_e164(surowy) is None


# --- transport ----------------------------------------------------------------

def test_wysylka_trafia_do_twilio_z_kluczem_i_trescia(twilio):
    poszlo, powod = sms.wyslij("+48123456789", "Cześć")
    assert poszlo and powod == ""
    strzal = twilio[0]
    assert strzal["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC_test/Messages.json"
    assert strzal["pola"] == {"To": "+48123456789", "Body": "Cześć", "From": "+15550001111"}
    klucz = base64.b64encode(b"AC_test:sekret").decode()
    assert strzal["headers"]["Authorization"] == f"Basic {klucz}"


def test_messaging_service_zamiast_numeru(twilio, monkeypatch):
    """Na numery amerykańskie A2P 10DLC przechodzi tylko przez Messaging Service,
    więc to samo pole musi umieć jedno i drugie."""
    monkeypatch.setattr(sms.settings, "twilio_from", "MG0000")
    assert sms.wyslij("+15551234567", "hej")[0] is True
    assert twilio[0]["pola"]["MessagingServiceSid"] == "MG0000"
    assert "From" not in twilio[0]["pola"]


def test_odmowa_twilio_wraca_z_powodem(twilio, monkeypatch):
    """Bez tego admin widzi „nie poszło" i szuka w logach hostingu, że numer
    jest niepoprawny albo konto nie ma środków."""
    monkeypatch.setattr(sms, "_urllib_transport", lambda u, b, h: (
        400, json.dumps({"code": 21211, "message": "The 'To' number is not valid"}).encode()))
    poszlo, powod = sms.wyslij("+48123456789", "hej")
    assert poszlo is False and "not valid" in powod


def test_bez_konfiguracji_nic_nie_leci():
    poszlo, powod = sms.wyslij("+48123456789", "hej")
    assert poszlo is False and "not configured" in powod
    assert sms.is_enabled() is False


def test_bez_adresu_telegrama_sms_nie_wychodzi(twilio, monkeypatch):
    """SMS bez linku byłby mostem donikąd: człowiek dostaje wiadomość i nie ma
    jak odpowiedzieć."""
    monkeypatch.setattr(sms.settings, "sms_telegram_url", "")
    assert sms.is_enabled() is False
    assert sms.wyslij("+48123456789", "hej")[0] is False
    assert twilio == []


# --- treść --------------------------------------------------------------------

def test_obie_wersje_prowadza_na_telegram(twilio):
    przyjety = sms.tresc("Anna Nowak", zakwalifikowany=True)
    odrzucony = sms.tresc("Anna Nowak", zakwalifikowany=False)
    assert przyjety != odrzucony
    for t in (przyjety, odrzucony):
        # Cały sens tej wysyłki to wrócić tam, gdzie dział pracuje.
        assert TG in t and "Anna" in t and "Nowak" not in t
        assert len(t) <= sms.MAX_ZNAKOW


def test_lead_bez_imienia_dostaje_zdanie_bez_dziury(twilio):
    assert "Hi there," in sms.tresc("", zakwalifikowany=True)


# --- panel: przycisk z ręki ---------------------------------------------------

def test_przycisk_wysyla_i_zapisuje_slad(twilio):
    lid = _lead()
    r = client.post(f"/api/admin/leads/{lid}/sms", headers=ADMIN)
    assert r.status_code == 200
    assert twilio[0]["pola"]["To"] == "+48123456789"
    assert TG in twilio[0]["pola"]["Body"]
    # Historia leada to jedyne miejsce, w którym po tygodniu widać, że coś poszło.
    assert _zdarzenia(lid) == 1


def test_odrzucony_lead_dostaje_swoja_wersje(twilio):
    lid = _lead(outcome="not_qualified")
    client.post(f"/api/admin/leads/{lid}/sms", headers=ADMIN)
    assert twilio[0]["pola"]["Body"] == sms.tresc("Anna Nowak", zakwalifikowany=False)


def test_drugi_klik_nie_wysyla_drugi_raz(twilio):
    lid = _lead()
    assert client.post(f"/api/admin/leads/{lid}/sms", headers=ADMIN).status_code == 200
    r = client.post(f"/api/admin/leads/{lid}/sms", headers=ADMIN)
    assert r.status_code == 400 and "already" in r.json()["detail"]
    assert len(twilio) == 1 and _zdarzenia(lid) == 1


def test_admin_moze_wymusic_powtorke(twilio):
    """Pierwszy SMS bywa zjadany przez operatora. Świadome wymuszenie ma przejść —
    blokada chroni przed przypadkiem, nie przed decyzją."""
    lid = _lead()
    client.post(f"/api/admin/leads/{lid}/sms", headers=ADMIN)
    assert client.post(f"/api/admin/leads/{lid}/sms?force=true",
                       headers=ADMIN).status_code == 200
    assert len(twilio) == 2 and _zdarzenia(lid) == 2


def test_numer_bez_kierunkowego_konczy_sie_bledem_a_nie_wysylka(twilio):
    lid = _lead(phone="601234567")
    r = client.post(f"/api/admin/leads/{lid}/sms", headers=ADMIN)
    assert r.status_code == 400 and "international" in r.json()["detail"]
    assert twilio == [] and _zdarzenia(lid) == 0


def test_nieudana_wysylka_nie_zostawia_sladu(twilio, monkeypatch):
    """Historia leada ma mówić, co się stało, a nie co próbowaliśmy zrobić —
    inaczej blokada „raz na leada" zadziałałaby po nieudanej próbie."""
    monkeypatch.setattr(sms, "_urllib_transport",
                        lambda u, b, h: (400, b'{"message":"insufficient funds"}'))
    lid = _lead()
    assert client.post(f"/api/admin/leads/{lid}/sms", headers=ADMIN).status_code == 400
    assert _zdarzenia(lid) == 0


def test_panel_widzi_czy_jest_do_kogo_wyslac(twilio):
    dobry, zly = _lead(), _lead(phone=None)
    karty = {l["id"]: l for l in client.get("/api/admin/leads", headers=ADMIN).json()}
    assert karty[dobry]["sms_ready"] is True
    assert karty[zly]["sms_ready"] is False


def test_bez_twilio_panel_nie_obiecuje_przycisku():
    lid = _lead()
    karty = {l["id"]: l for l in client.get("/api/admin/leads", headers=ADMIN).json()}
    assert karty[lid]["sms_ready"] is False


# --- Telegram: automat przy zmianie statusu -----------------------------------

def _klik(lead_id, akcja="messaged"):
    """Kliknięcie przycisku pod kartą leada na kanale — pomijamy webhook,
    bo mierzymy decyzję, a nie parsowanie callbacku."""
    s = SessionLocal()
    try:
        lead = s.get(Lead, lead_id)
        dymek, zmiana = glowny._wykonaj_akcje(s, lead, akcja, "Bartek")
        if zmiana:
            s.commit()
        return dymek
    finally:
        s.close()


def test_klik_messaged_wysyla_smsa_gdy_nie_ma_telegrama(twilio):
    """Sedno: „napisaliśmy" do kogoś bez handle'a jest deklaracją bez pokrycia —
    nie ma dokąd napisać. Wtedy SMS jest jedyną drogą."""
    lid = _lead(telegram=None)
    dymek = _klik(lid)
    assert len(twilio) == 1 and _zdarzenia(lid) == 1
    assert "SMS" in dymek


def test_lead_z_telegramem_nie_dostaje_smsa(twilio):
    """Kto podał handle, dostaje wiadomość tam. SMS zostaje pod ręcznym
    przyciskiem — automat nie ma prawa dublować kanału i kosztu."""
    lid = _lead(telegram="annanowak")
    _klik(lid)
    assert twilio == [] and _zdarzenia(lid) == 0


def test_powtorny_klik_w_ten_sam_status_nie_wysyla_drugiego_smsa(twilio):
    """Przycisk zostaje pod wiadomością na kanale i klika się go odruchowo."""
    lid = _lead(telegram=None)
    _klik(lid)
    _klik(lid)
    assert len(twilio) == 1 and _zdarzenia(lid) == 1


def test_klik_w_status_ktory_juz_stoi_nie_wysyla_nic(twilio):
    """Najgroźniejszy wariant, bo blokada „raz na leada" tu nie pomoże.

    Lead trafił do „messaged" inną drogą (panel), a przycisk na kanale wisi
    dalej i ktoś w niego klika. Taki klik niczego nie zmienia, więc wywołujący
    nie commituje — SMS by poszedł, ale zapis o wysyłce zniknąłby razem z
    transakcją. Blokada nie miałaby czego znaleźć i każde następne kliknięcie
    wysyłałoby kolejnego, w kółko, na koszt konta Twilio.
    """
    lid = _lead(telegram=None, status="messaged")
    _klik(lid)
    _klik(lid)
    assert twilio == [] and _zdarzenia(lid) == 0


def test_odbicie_statusu_tam_i_z_powrotem_nie_mnozy_smsow(twilio):
    lid = _lead(telegram=None)
    _klik(lid)
    _klik(lid, "new")
    _klik(lid)
    assert len(twilio) == 1, "każde odbicie statusu byłoby kolejnym SMS-em"


def test_klikajacy_dowiaduje_sie_ze_sms_nie_poszedl(twilio):
    """Bez tego zostaje w przekonaniu, że kontakt nastąpił, i lead cicho umiera
    w „messaged", choć nie dotarło do niego nic."""
    lid = _lead(telegram=None, phone="601234567")
    dymek = _klik(lid)
    assert "international" in dymek and twilio == []


def test_inne_statusy_nie_wysylaja_niczego(twilio):
    for akcja in ("replied", "no_reply", "rejected"):
        lid = _lead(telegram=None)
        _klik(lid, akcja)
    assert twilio == []


def test_bez_konfiguracji_klik_zachowuje_sie_jak_dawniej():
    """Kto nie chce SMS-ów, nie ma prawa zobaczyć w dymku ani słowa o Twilio."""
    lid = _lead(telegram=None)
    dymek = _klik(lid)
    assert "SMS" not in dymek and "Twilio" not in dymek
