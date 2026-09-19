"""Rozdział leadów na dwa deski Telegrama (domyślny i nigeryjski).

Rozstrzyga LEJEK: strona /freeaccount otwiera formularz z `source="free"`, więc
darmowe zgłoszenia idą na desk nigeryjski, a płatne na domyślny. Kryterium po
kraju numeru istnieje, ale jest domyślnie wyłączone.

Deski to osobne boty, osobne czaty i osobne sekrety webhooka. Tu sprawdzamy trzy
rzeczy, na których to stoi: że lead trafia na właściwy desk, że KAŻDA niepewność
schodzi na desk domyślny zamiast gubić leada, i że raz wybrany desk już się nie
zmienia — karta wisi na konkretnym czacie i cudzym tokenem nie da się jej
ani zdjąć, ani edytować.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
TOKEN_LANDINGU = "sekret-landingu-desk"
SEKRET_LEADS = "sekret-desku-leads"
SEKRET_NG = "sekret-desku-ng"
LICZNIK = iter(range(10_000))
NUMERY_WIADOMOSCI = iter(range(20_000, 30_000))


@pytest.fixture(autouse=True)
def _srodowisko(monkeypatch):
    """Oba deski skonfigurowane, Telegram odcięty od sieci.

    Atrapy zapisują też `bot`, bo w tym pliku sprawdzamy właśnie to: nie CZY
    wysyłka poszła, tylko KTÓRYM botem.
    """
    u = get_settings()
    monkeypatch.setattr(u, "lead_ingest_token", TOKEN_LANDINGU, raising=False)
    monkeypatch.setattr(u, "telegram_webhook_secret", SEKRET_LEADS, raising=False)
    monkeypatch.setattr(u, "telegram_leads_bot_token", "TOKEN-LEADS", raising=False)
    monkeypatch.setattr(u, "telegram_leads_chat_id", "-100111", raising=False)
    monkeypatch.setattr(u, "telegram_leads_ng_bot_token", "TOKEN-NG", raising=False)
    monkeypatch.setattr(u, "telegram_leads_ng_chat_id", "-100222", raising=False)
    monkeypatch.setattr(u, "telegram_leads_ng_webhook_secret", SEKRET_NG, raising=False)
    monkeypatch.setattr(u, "telegram_leads_ng_sources", "free", raising=False)
    # Domyślnie WYŁĄCZONE — testy kryterium krajowego włączają je sobie same.
    monkeypatch.setattr(u, "telegram_leads_ng_iso", "", raising=False)

    wyslane: dict[str, list] = {"alert": [], "delete": [], "przypomnienie": []}
    monkeypatch.setattr(
        telegram, "send_lead_alert",
        lambda lead_id, tekst, **kw: (
            wyslane["alert"].append((lead_id, (kw.get("bot") or telegram.desk("leads")).key)),
            (True, "", next(NUMERY_WIADOMOSCI)))[1])
    monkeypatch.setattr(
        telegram, "delete_lead_card",
        lambda mid, **kw: (
            wyslane["delete"].append((mid, (kw.get("bot") or telegram.desk("leads")).key)),
            (True, ""))[1])
    monkeypatch.setattr(
        telegram, "send_lead_message",
        lambda tekst, **kw: (
            wyslane["przypomnienie"].append((kw.get("bot") or telegram.desk("leads")).key),
            (True, ""))[1])
    return wyslane


def _zgloszenie(**nadpisz):
    dane = {
        "email": f"desk{next(LICZNIK)}@test.pl",
        "name": "Jan Kowalski",
        "phone": "+48111222333",
        "phoneIso": "PL",
        "source": "questionnaire",
        "outcome": "qualified",
        "quality": {"tier": "high", "score": 42},
    }
    dane.update(nadpisz)
    return dane


def _wyslij(dane):
    return client.post("/api/leads/ingest", json=dane,
                       headers={"X-Lead-Token": TOKEN_LANDINGU})


def _lead(lead_id) -> Lead:
    s = SessionLocal()
    try:
        return s.get(Lead, lead_id)
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Wybór desku                                                                 #
# --------------------------------------------------------------------------- #
def test_lejek_free_idzie_na_desk_ng(_srodowisko):
    """Strona /freeaccount otwiera formularz z `source="free"` — to jest KRYTERIUM."""
    r = _wyslij(_zgloszenie(source="free"))
    assert r.status_code == 200
    lead_id = r.json()["id"]
    assert _lead(lead_id).desk == "leads_ng"
    # I — co ważniejsze — kartę wysłał bot TEGO desku, nie domyślny.
    assert _srodowisko["alert"] == [(lead_id, "leads_ng")]


def test_lejek_dopasowywany_po_prefiksie():
    """Doprecyzowany lejek („free_meta") to nadal ten sam lejek."""
    r = _wyslij(_zgloszenie(source="free_meta"))
    assert _lead(r.json()["id"]).desk == "leads_ng"


def test_numer_nigeryjski_z_platnego_lejka_zostaje_na_desku_domyslnym():
    """Domyślnie decyduje WYŁĄCZNIE lejek. Nigeryjski numer z ankiety płatnej
    dotyczy innej oferty niż darmowe konto, więc nie miesza się z tamtym deskiem."""
    r = _wyslij(_zgloszenie(phone="+2348012345678", phoneIso="NG"))
    assert _lead(r.json()["id"]).desk == "leads"


def test_kryterium_krajowe_da_sie_wlaczyc(monkeypatch):
    """Jedna zmienna środowiskowa, bez zmiany kodu."""
    monkeypatch.setattr(get_settings(), "telegram_leads_ng_iso", "NG", raising=False)
    r = _wyslij(_zgloszenie(phone="+2348012345678", phoneIso="NG"))
    assert _lead(r.json()["id"]).desk == "leads_ng"


def test_wlaczone_kryterium_krajowe_lapie_tez_nazwe_kraju(monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_leads_ng_iso", "NG", raising=False)
    r = _wyslij(_zgloszenie(phone="0801234567", phoneIso="", country="Nigeria"))
    assert _lead(r.json()["id"]).desk == "leads_ng"


def test_zwykly_lead_idzie_na_desk_domyslny(_srodowisko):
    r = _wyslij(_zgloszenie())
    lead_id = r.json()["id"]
    assert _lead(lead_id).desk == "leads"
    assert _srodowisko["alert"] == [(lead_id, "leads")]


def test_brak_kraju_i_prefiksu_nie_gubi_leada():
    """Zgłoszenie z safe LP niesie sam mail — ma wylądować na desku domyślnym."""
    r = _wyslij({"email": f"safe{next(LICZNIK)}@test.pl", "name": "Ktoś",
                 "source": "safe", "outcome": "not_qualified"})
    assert r.status_code == 200
    assert _lead(r.json()["id"]).desk == "leads"


def test_desk_ng_nieskonfigurowany_spada_na_domyslny(monkeypatch, _srodowisko):
    """Kod można wdrożyć, ZANIM bot nigeryjski w ogóle powstanie."""
    u = get_settings()
    monkeypatch.setattr(u, "telegram_leads_ng_bot_token", "", raising=False)
    r = _wyslij(_zgloszenie(source="free"))
    lead_id = r.json()["id"]
    assert _lead(lead_id).desk == "leads"
    assert _srodowisko["alert"] == [(lead_id, "leads")]


def test_desk_przeliczany_przy_kazdym_zgloszeniu(_srodowisko):
    """Powrót tego samego człowieka innym lejkiem PRZENOSI go na właściwy desk.

    Wcześniej desk był „lepki" i to okazało się gorsze od problemu, który miał
    rozwiązać: migracja wpisała `leads` wszystkim istniejącym leadom, więc każdy,
    kto kiedykolwiek wypełnił formularz, zostawał na desku domyślnym na zawsze —
    nawet wracając przez /freeaccount."""
    mail = f"powrot{next(LICZNIK)}@test.pl"
    pierwsze = _wyslij(_zgloszenie(email=mail, source="questionnaire"))
    lead_id = pierwsze.json()["id"]
    assert _lead(lead_id).desk == "leads"

    drugie = _wyslij(_zgloszenie(email=mail, source="free"))
    assert drugie.json()["id"] == lead_id
    assert _lead(lead_id).desk == "leads_ng"
    # Nowa karta poszła na desk NG...
    assert _srodowisko["alert"][-1] == (lead_id, "leads_ng")
    # ...a poprzednia została zdjęta ze STAREGO desku, jego własnym botem.
    assert _srodowisko["delete"][-1][1] == "leads"


def test_ponowne_zgloszenie_nie_zostawia_dwoch_kart(_srodowisko):
    """Nawet bez zmiany desku stara karta schodzi — inaczej dział widzi dwie
    karty tego samego człowieka, a klikalna jest tylko nowsza."""
    mail = f"dwiekarty{next(LICZNIK)}@test.pl"
    pierwsze = _wyslij(_zgloszenie(email=mail))
    mid = _lead(pierwsze.json()["id"]).tg_message_id

    _wyslij(_zgloszenie(email=mail))
    assert _srodowisko["delete"] == [(mid, "leads")]


def test_kasowanie_karty_idzie_na_desk_leada(_srodowisko):
    r = _wyslij(_zgloszenie(source="free"))
    lead_id = r.json()["id"]
    mid = _lead(lead_id).tg_message_id
    assert mid

    assert client.delete(f"/api/admin/leads/{lead_id}", headers=ADMIN).status_code == 200
    assert _srodowisko["delete"] == [(mid, "leads_ng")]


# --------------------------------------------------------------------------- #
#  Webhook: desk siedzi w adresie                                              #
# --------------------------------------------------------------------------- #
def test_kazdy_desk_ma_wlasny_sekret():
    """Sekret jednego desku nie otwiera drugiego — inaczej wyciek jednego
    pozwalałby podszyć się pod oba."""
    assert client.post("/api/telegram/webhook/leads_ng", json={},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_NG}
                       ).status_code == 200
    assert client.post("/api/telegram/webhook/leads_ng", json={},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_LEADS}
                       ).status_code == 401
    assert client.post("/api/telegram/webhook/leads", json={},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_NG}
                       ).status_code == 401


def test_adres_bez_desku_dziala_jak_desk_domyslny():
    """Stary `setWebhook` może być jeszcze w locie w chwili wdrożenia."""
    assert client.post("/api/telegram/webhook", json={},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_LEADS}
                       ).status_code == 200


def test_nieznany_desk_to_404():
    assert client.post("/api/telegram/webhook/kanal-tresci", json={},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_LEADS}
                       ).status_code == 404


def test_desk_bez_sekretu_odmawia_wszystkiego(monkeypatch):
    """Pusty sekret nie może znaczyć „wpuszczaj każdego"."""
    monkeypatch.setattr(get_settings(), "telegram_leads_ng_webhook_secret", "",
                        raising=False)
    assert client.post("/api/telegram/webhook/leads_ng", json={},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_NG}
                       ).status_code == 401
