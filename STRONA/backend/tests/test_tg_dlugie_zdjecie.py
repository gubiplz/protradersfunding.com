"""Zdjęcie z tekstem dłuższym niż podpis.

Usterka z produkcji (archive:mgmt/16): post ze zdjęciem i 1132 znakami tekstu
trafił do kolejki bez grafiki, bo podpis pod zdjęciem Telegram tnie na 1024.
Zwykły post ma 4096 i pokazuje obraz z adresu jako duży podgląd nad tekstem —
tak taki post wychodzi teraz, a edycja trafia w tę samą formę.
"""
import json
import os
import re
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import contentbot, telegram  # noqa: E402
from app.models import ChannelPost  # noqa: E402

FOTO = "https://protradersfunding.com/media/posts/abc.jpg"
DLUGI = "Three people. " + "x" * 1100
KROTKI = "Three people, three reasons."


class _Api:
    """Transport Bot API: zapisuje metodę i pola, odpowiada jak Telegram."""

    def __init__(self, odmowy=None):
        self.wywolania = []
        self.odmowy = odmowy or {}

    def __call__(self, url, body, content_type):
        metoda = url.rsplit("/", 1)[1]
        pola = dict(re.findall(rb'name="([^"]+)"\r\n\r\n(.*?)\r\n--', body, re.S))
        pola = {k.decode(): v.decode() for k, v in pola.items()}
        self.wywolania.append((metoda, pola))
        if metoda in self.odmowy:
            return 400, json.dumps({"ok": False, "description": self.odmowy[metoda]}).encode()
        return 200, json.dumps({"ok": True, "result": {"message_id": 7}}).encode()


def test_dlugi_tekst_ze_zdjeciem_wychodzi_z_podgladem_nad_tekstem():
    api = _Api()
    ok, _, _ = telegram.send_content("@kanal", DLUGI, photo_url=FOTO, token="T", transport=api)
    metoda, pola = api.wywolania[0]
    assert ok and metoda == "sendMessage"
    assert pola["text"] == DLUGI, "treść idzie w całości, bez przycinania"
    podglad = json.loads(pola["link_preview_options"])
    assert podglad == {"url": FOTO, "prefer_large_media": True, "show_above_text": True}
    assert "disable_web_page_preview" not in pola


def test_krotki_tekst_dalej_jest_podpisem_pod_zdjeciem():
    api = _Api()
    telegram.send_content("@kanal", KROTKI, photo_url=FOTO, token="T", transport=api)
    metoda, pola = api.wywolania[0]
    assert metoda == "sendPhoto" and pola["photo"] == FOTO and pola["caption"] == KROTKI


def test_limit_liczy_widoczne_znaki_nie_html():
    """Linki CTA dokładane przy wysyłce nie mogą przerzucić posta na podgląd."""
    tekst = f'<a href="https://t.me/{"a" * 300}">Get started</a> ' + "y" * 990
    api = _Api()
    telegram.send_content("@kanal", tekst, photo_url=FOTO, token="T", transport=api)
    assert api.wywolania[0][0] == "sendPhoto"


def test_edycja_dlugiego_zdjecia_idzie_tekstem_z_tym_samym_podgladem():
    api = _Api()
    ok, _ = telegram.edit_content("@kanal", 7, DLUGI, kind="photo", photo_url=FOTO,
                                  token="T", transport=api)
    metoda, pola = api.wywolania[0]
    assert ok and metoda == "editMessageText"
    assert json.loads(pola["link_preview_options"])["url"] == FOTO


def test_skrocenie_posta_z_podgladem_nie_konczy_sie_bledem():
    """Post wyszedł jako tekst z podglądem, a po edycji mieści się w podpisie.
    Telegram odmawia podpisu („no caption") — edycja przechodzi na tekst."""
    api = _Api(odmowy={"editMessageCaption":
                       "Bad Request: there is no caption in the message to edit"})
    ok, _ = telegram.edit_content("@kanal", 7, KROTKI, kind="photo", photo_url=FOTO,
                                  token="T", transport=api)
    assert ok
    assert [m for m, _ in api.wywolania] == ["editMessageCaption", "editMessageText"]


def test_wydluzenie_podpisu_ponad_limit_mowi_dlaczego():
    api = _Api(odmowy={"editMessageText":
                       "Bad Request: there is no text in the message to edit"})
    ok, powod = telegram.edit_content("@kanal", 7, DLUGI, kind="photo", photo_url=FOTO,
                                      token="T", transport=api)
    assert not ok and "1,024" in powod


@pytest.mark.parametrize("pola,limit", [
    ({"kind": "photo", "media_url": FOTO}, contentbot.LIMIT_TEKSTU),
    ({"kind": "photo", "media_url": "https://x.test/zdj", "origin": "archive:mgmt/16"},
     contentbot.LIMIT_TEKSTU),
    # Zrzut strony nie ma adresu pliku do podglądu, a film podglądu nie ma.
    ({"kind": "photo", "media_url": "https://protradersfunding.com/payout/abc?bare=1"},
     contentbot.LIMIT_PODPISU),
    ({"kind": "video", "media_url": "https://x.test/klip.mp4"}, contentbot.LIMIT_PODPISU),
    ({"kind": "text", "media_url": None}, contentbot.LIMIT_TEKSTU),
])
def test_limit_tresci_zalezy_od_tego_jak_post_wyjdzie(pola, limit):
    post = ChannelPost(channel="mgmt", body="", origin=pola.pop("origin", "panel"), **pola)
    assert contentbot.limit_tresci(post) == limit
