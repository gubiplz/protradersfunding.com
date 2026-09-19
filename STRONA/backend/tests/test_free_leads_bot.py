"""Darmowy czat leadów obsługiwany przez WŁASNEGO bota.

Podział czatów (`lead_chat_id`) istniał wcześniej: leady z darmowego lejka mają
własny kanał. Tu dochodzi drugi wymiar tej samej reguły — własny TOKEN, żeby
zamrożenie jednego konta Telegrama nie odebrało uprawnień wszystkim kanałom
naraz, jak we wrześniu 2026.

Token rozstrzyga się po CZACIE, nie po `source`: wywołujący zna czat zawsze —
z bazy albo z update'u Telegrama — a `source` tylko przy pierwszej wysyłce.
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
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)

TOKEN_GLOWNY = "111111:GLOWNY"
TOKEN_FREE = "222222:FREE"
CZAT_WYPLAT = "@fx_passingpayouts"
CZAT_DZIALU = "-100111111"
CZAT_FREE = "-100222222"
SEKRET_GLOWNY = "sekret-bota-glownego"
SEKRET_FREE = "sekret-bota-free"


@pytest.fixture(autouse=True)
def _srodowisko(monkeypatch):
    u = get_settings()
    monkeypatch.setattr(u, "telegram_bot_token", TOKEN_GLOWNY, raising=False)
    monkeypatch.setattr(u, "telegram_chat_id", CZAT_WYPLAT, raising=False)
    monkeypatch.setattr(u, "telegram_leads_chat_id", CZAT_DZIALU, raising=False)
    monkeypatch.setattr(u, "telegram_free_leads_chat_id", CZAT_FREE, raising=False)
    monkeypatch.setattr(u, "telegram_free_leads_bot_token", TOKEN_FREE, raising=False)
    monkeypatch.setattr(u, "telegram_webhook_secret", SEKRET_GLOWNY, raising=False)
    monkeypatch.setattr(u, "telegram_free_leads_webhook_secret", SEKRET_FREE, raising=False)


def _szpieg():
    """Transport zapisujący adres żądania. Token siedzi w URL-u, więc po nim
    widać, KTÓRY bot wysyłał — a to jest jedyne, co tu sprawdzamy."""
    zapis = {}

    def transport(url, body, content_type):
        zapis["url"] = url
        zapis["token"] = url.split("/bot", 1)[1].split("/", 1)[0]
        return 200, b'{"ok":true,"result":{"message_id":7}}'

    return transport, zapis


# --------------------------------------------------------------------------- #
#  Wybór tokenu                                                                #
# --------------------------------------------------------------------------- #
def test_czat_free_dostaje_wlasnego_bota():
    assert telegram.lead_bot_token(CZAT_FREE) == TOKEN_FREE


def test_czat_dzialu_zostaje_przy_bocie_glownym():
    assert telegram.lead_bot_token(CZAT_DZIALU) == TOKEN_GLOWNY


def test_bez_czatu_bot_glowny():
    assert telegram.lead_bot_token(None) == TOKEN_GLOWNY
    assert telegram.lead_bot_token("") == TOKEN_GLOWNY


def test_nieskonfigurowany_bot_free_spada_na_glownego(monkeypatch):
    """Samo wdrożenie tego kodu niczego nie zmienia, dopóki token nie jest ustawiony."""
    monkeypatch.setattr(get_settings(), "telegram_free_leads_bot_token", "", raising=False)
    assert telegram.lead_bot_token(CZAT_FREE) == TOKEN_GLOWNY


def test_numer_czatu_porownywany_jako_tekst(monkeypatch):
    """`chat_id` bywa intem z update'u, a stringiem z bazy — to ten sam czat."""
    assert telegram.lead_bot_token(int(CZAT_FREE)) == TOKEN_FREE


# --------------------------------------------------------------------------- #
#  Wysyłka                                                                     #
# --------------------------------------------------------------------------- #
def test_karta_na_czat_free_idzie_botem_free():
    transport, zapis = _szpieg()
    poszlo, _, mid = telegram.send_lead_alert(1, "karta", chat_id=CZAT_FREE,
                                              transport=transport)
    assert poszlo and mid == 7
    assert zapis["token"] == TOKEN_FREE


def test_karta_na_czat_dzialu_idzie_botem_glownym():
    transport, zapis = _szpieg()
    telegram.send_lead_alert(1, "karta", chat_id=CZAT_DZIALU, transport=transport)
    assert zapis["token"] == TOKEN_GLOWNY


def test_kasowanie_karty_free_idzie_botem_free():
    """`message_id` należy do bota, który wysłał — cudzym tokenem go nie tknie."""
    transport, zapis = _szpieg()
    telegram.delete_lead_card(7, chat_id=CZAT_FREE, transport=transport)
    assert zapis["token"] == TOKEN_FREE


def test_przypomnienie_na_czat_free_idzie_botem_free():
    transport, zapis = _szpieg()
    telegram.send_lead_message("przypomnienie", chat_id=CZAT_FREE, transport=transport)
    assert zapis["token"] == TOKEN_FREE


def test_przepisanie_karty_free_idzie_botem_free():
    transport, zapis = _szpieg()
    telegram.edit_lead_message(CZAT_FREE, 7, "nowa tresc", transport=transport)
    assert zapis["token"] == TOKEN_FREE


def test_odpowiedz_na_klikniecie_idzie_botem_tego_czatu():
    """`callback_query_id` jest ważny wyłącznie dla bota, który dostał kliknięcie."""
    transport, zapis = _szpieg()
    telegram.answer_callback("cb1", "ok", chat_id=CZAT_FREE, transport=transport)
    assert zapis["token"] == TOKEN_FREE

    transport, zapis = _szpieg()
    telegram.answer_callback("cb1", "ok", chat_id=CZAT_DZIALU, transport=transport)
    assert zapis["token"] == TOKEN_GLOWNY


def test_wyplaty_zostaja_przy_bocie_glownym():
    """Kanał z certyfikatami nie ma z darmowymi leadami nic wspólnego."""
    transport, zapis = _szpieg()
    telegram.send_message("wyplata", transport=transport)
    assert zapis["token"] == TOKEN_GLOWNY


# --------------------------------------------------------------------------- #
#  Webhook                                                                     #
# --------------------------------------------------------------------------- #
def test_webhook_przyjmuje_oba_sekrety():
    """Każdy bot ma własne `setWebhook`, więc i własny sekret — adres jest jeden."""
    for sekret in (SEKRET_GLOWNY, SEKRET_FREE):
        r = client.post("/api/telegram/webhook", json={},
                        headers={"X-Telegram-Bot-Api-Secret-Token": sekret})
        assert r.status_code == 200, sekret


def test_webhook_odrzuca_obcy_sekret():
    r = client.post("/api/telegram/webhook", json={},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "obcy"})
    assert r.status_code == 401


def test_webhook_bez_sekretow_odmawia_wszystkiego(monkeypatch):
    """Pusta konfiguracja nie może znaczyć „wpuszczaj każdego"."""
    u = get_settings()
    monkeypatch.setattr(u, "telegram_webhook_secret", "", raising=False)
    monkeypatch.setattr(u, "telegram_free_leads_webhook_secret", "", raising=False)
    r = client.post("/api/telegram/webhook", json={},
                    headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_GLOWNY})
    assert r.status_code == 401
