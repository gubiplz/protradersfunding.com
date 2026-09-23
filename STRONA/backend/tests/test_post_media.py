"""Grafika wgrana z podglądu posta: upload, publiczny plik i cała droga do kanału.

Edytor w panelu robi trzy kroki: wgrywa plik (dostaje adres), zapisuje post
z tym adresem i — jeśli post był zatwierdzony — zatwierdza go ponownie.
Te testy przechodzą tę samą drogę na prawdziwych endpointach i kończą na
publikacji z atrapą Telegrama, bo dopiero tam widać, czy wgrana grafika
naprawdę trafia na kanał, a nie tylko do bazy.
"""
import json
import os
import struct
import tempfile
import zlib
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import contentbot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import POST_MEDIA_MAX, app, wymiary_obrazka  # noqa: E402
from app.models import ChannelPost, PostMedia  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}


def _png(szer, wys):
    """Prawdziwy nagłówek PNG z podanymi wymiarami (piksele nie są potrzebne)."""
    def chunk(typ, dane):
        return (struct.pack(">I", len(dane)) + typ + dane
                + struct.pack(">I", zlib.crc32(typ + dane)))
    ihdr = struct.pack(">IIBBBBB", szer, wys, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b""))


def _jpeg(szer, wys):
    """SOI + APP0 (JFIF) + SOF0 z wymiarami — tyle, ile czyta serwer."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, wys, szer, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


@pytest.fixture
def sprzataj():
    posty, media = [], []
    yield posty, media
    s = SessionLocal()
    try:
        if posty:
            s.query(ChannelPost).filter(ChannelPost.id.in_(posty)).delete(
                synchronize_session=False)
        if media:
            s.query(PostMedia).filter(PostMedia.token.in_(media)).delete(
                synchronize_session=False)
        s.commit()
    finally:
        s.close()


def _wgraj(dane, nazwa="a.png", naglowki=ADMIN):
    return client.post("/api/admin/post-media", headers=naglowki,
                       files={"file": (nazwa, dane, "application/octet-stream")})


def _token(url):
    return url.rsplit("/", 1)[1].rsplit(".", 1)[0]


# --------------------------------------------------------------------------- #
#  Wymiary z nagłówka
# --------------------------------------------------------------------------- #
def test_wymiary_png_i_jpeg():
    assert wymiary_obrazka(_png(1320, 1320)) == ("image/png", 1320, 1320)
    assert wymiary_obrazka(_jpeg(1080, 1350)) == ("image/jpeg", 1080, 1350)


def test_inne_formaty_nie_sa_obrazkiem():
    assert wymiary_obrazka(b"RIFF\x00\x00\x00\x00WEBPVP8 ") is None
    assert wymiary_obrazka(b"GIF89a....") is None
    assert wymiary_obrazka(b"") is None


# --------------------------------------------------------------------------- #
#  Upload
# --------------------------------------------------------------------------- #
def test_upload_oddaje_publiczny_adres_z_rozszerzeniem(sprzataj):
    odp = _wgraj(_png(1320, 1320))
    assert odp.status_code == 200, odp.text
    dane = odp.json()
    sprzataj[1].append(_token(dane["url"]))
    assert dane["url"].endswith(".png") and "/media/posts/" in dane["url"]
    assert (dane["width"], dane["height"]) == (1320, 1320)


def test_jpeg_dostaje_rozszerzenie_jpg(sprzataj):
    odp = _wgraj(_jpeg(1080, 1350), "b.jpeg")
    sprzataj[1].append(_token(odp.json()["url"]))
    assert odp.json()["url"].endswith(".jpg")


def test_webp_odrzucony_z_powodem():
    odp = _wgraj(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "c.webp")
    assert odp.status_code == 400 and "PNG or JPG" in odp.json()["detail"]


def test_ponad_5_mb_odrzucone():
    odp = _wgraj(_png(1000, 1000) + b"\x00" * POST_MEDIA_MAX)
    assert odp.status_code == 413


def test_proporcje_ponad_limit_telegrama_odrzucone():
    odp = _wgraj(_png(100, 2500))
    assert odp.status_code == 400 and "1:20" in odp.json()["detail"]


def test_bez_admina_nie_wolno():
    assert _wgraj(_png(10, 10), naglowki={}).status_code in (401, 403)


# --------------------------------------------------------------------------- #
#  Publiczny plik
# --------------------------------------------------------------------------- #
def test_plik_wraca_bajt_w_bajt(sprzataj):
    dane = _png(640, 640)
    url = _wgraj(dane).json()["url"]
    sprzataj[1].append(_token(url))
    odp = client.get("/media/posts/" + url.rsplit("/", 1)[1])
    assert odp.status_code == 200
    assert odp.content == dane
    assert odp.headers["content-type"] == "image/png"
    assert "immutable" in odp.headers["cache-control"]


def test_nieznany_token_to_404():
    assert client.get("/media/posts/nie-ma-takiego.png").status_code == 404


# --------------------------------------------------------------------------- #
#  Droga edytora: upload -> zapis -> ponowne zatwierdzenie -> publikacja
# --------------------------------------------------------------------------- #
def test_zapis_z_nowa_grafika_trzyma_termin_i_wychodzi_ta_grafika(sprzataj, monkeypatch):
    # Test nie może zależeć od tego, czy środowisko ma skonfigurowany kanał.
    ustawienia = get_settings()
    monkeypatch.setattr(ustawienia, "telegram_bot_token", "1:test")
    monkeypatch.setattr(ustawienia, "telegram_mgmt_chat_id", "-100123")
    termin = datetime.now(timezone.utc) + timedelta(days=2)
    s = SessionLocal()
    try:
        p = ChannelPost(channel="mgmt", kind="photo", body="Old copy, no figures.",
                        media_url="https://example.test/payout/abc?bare=1", proof="",
                        status="scheduled", origin="panel", scheduled_for=termin)
        s.add(p)
        s.commit()
        pid = p.id
        sprzataj[0].append(pid)
    finally:
        s.close()

    url = _wgraj(_png(1320, 1320)).json()["url"]
    sprzataj[1].append(_token(url))

    # Dokładnie to, co wysyła tgpZapisz: PATCH, a potem approve.
    odp = client.patch(f"/api/admin/channel-posts/{pid}", headers=ADMIN, json={
        "channel": "mgmt", "kind": "photo", "body": "New copy, still no figures.",
        "media_url": url, "proof": "", "scheduled_for": termin.isoformat()})
    assert odp.status_code == 200 and odp.json()["status"] == "draft"
    odp = client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)
    assert odp.status_code == 200
    assert odp.json()["status"] == "scheduled", "po zapisie post ma wrócić do planu"

    wyslane = []

    def telegram_atrapa(adres, cialo, typ):
        wyslane.append((adres, cialo or b""))
        return 200, json.dumps({"ok": True, "result": {
            "message_id": 7, "chat": {"id": -100, "username": "kanal"}}}).encode()

    def zrzut_zakazany(*a, **k):
        raise AssertionError("wgrana grafika nie może iść przez zrzut strony")

    s = SessionLocal()
    try:
        post = s.get(ChannelPost, pid)
        wynik = contentbot.opublikuj(s, post, transport_shot=zrzut_zakazany,
                                     transport_tg=telegram_atrapa,
                                     transport_reach=lambda *a, **k: (200, b"{}"))
    finally:
        s.close()

    assert wynik["posted"] and wynik["photo"], wynik
    adres, cialo = wyslane[0]
    assert "sendPhoto" in adres
    tresc = cialo.decode("utf-8", "replace")
    assert _token(url) in tresc, "na kanał ma pójść adres wgranej grafiki"
    assert "New copy" in tresc
