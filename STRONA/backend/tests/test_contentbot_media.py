"""Załączniki w kolejce treści: gotowy obraz, klip wideo i cicha awaria.

Trzy rzeczy, które ten plik pilnuje:

1. `media_url` znaczy dwie różne rzeczy. Adres GOTOWEGO pliku ma iść do
   Telegrama wprost — to, co admin zatwierdził, ma trafić na kanał bez
   pośredników. Adres STRONY ma dalej iść przez `certshot`, bo na tym stoi
   publikacja certyfikatów.

2. Wideo. Klip leci adresem, tak jak zdjęcie.

3. Najważniejsze: post ze załącznikiem, dla którego nie powstał ani zrzut,
   ani adres, musi skończyć jako `failed`. Wcześniej wychodził jako goły
   tekst i zapisywał się jako `published` — awaria wyglądała jak sukces,
   więc nikt jej nie szukał.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import certshot, contentbot  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import ChannelPost  # noqa: E402

init_db()

TRESC = "Timeless copy with no figures in it at all."


@pytest.fixture
def sesja():
    s = SessionLocal()
    stworzone = []
    try:
        yield s, stworzone
    finally:
        try:
            for post in stworzone:
                s.delete(post)
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()


def _post(sesja, **pola):
    s, stworzone = sesja
    dane = dict(channel="mgmt", kind="text", body=TRESC, proof="",
                status="approved", origin="panel")
    dane.update(pola)
    post = ChannelPost(**dane)
    s.add(post)
    s.commit()
    stworzone.append(post)
    return post


class _Telegram:
    """Podstawka pod transport: zapamiętuje, czym wywołano Bot API."""

    def __init__(self, ok=True):
        self.wywolania = []
        self.ok = ok

    def __call__(self, metoda, pola, plik=None, token=None):
        self.wywolania.append((metoda, pola, plik))
        if not self.ok:
            return False, "telegram odmowil", {}
        return True, "", {"message_id": 1}


def _wyslij(monkeypatch, s, post, *, zrzut=None):
    """Publikuje post, podmieniając oba wyjścia na zewnątrz."""
    tg = _Telegram()

    def fake_send(chat_id, text, *, png=None, photo_url=None, video_url=None,
                  token=None, transport=None):
        return tg("sendX", {"png": bool(png), "photo_url": photo_url,
                            "video_url": video_url})

    monkeypatch.setattr(contentbot.telegram, "send_content", fake_send)
    monkeypatch.setattr(contentbot.certshot, "render", lambda *a, **k: zrzut)
    monkeypatch.setattr(contentbot, "chat_id", lambda kanal: "@kanal")
    wynik = contentbot.opublikuj(s, post)
    return wynik, tg


# --------------------------------------------------------------------------- #
#  Rozpoznawanie adresu
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("adres", [
    "https://forexpassing.com/tg/am-01.png",
    "https://forexpassing.com/tg/am-01.JPG",
    "https://cdn5.telesco.pe/file/cos.jpeg",
    "https://forexpassing.com/tg/x.webp",
])
def test_adres_pliku_graficznego_rozpoznany(adres):
    assert contentbot._jest_obrazkiem(adres)


@pytest.mark.parametrize("adres", [
    "https://protradersfunding.com/payout/abc?bare=1",
    "https://protradersfunding.com/payout/abc",
    "https://forexpassing.com/tg/klip.mp4",
])
def test_adres_strony_nie_jest_obrazkiem(adres):
    assert not contentbot._jest_obrazkiem(adres)


def test_query_string_nie_myli_rozpoznania():
    # Adres strony z parametrem `png` w query nie jest plikiem graficznym.
    assert not contentbot._jest_obrazkiem("https://x.pl/raport?format=png")
    # A adres pliku z parametrem wersji dalej nim jest.
    assert contentbot._jest_obrazkiem("https://x.pl/a.png?v=3")


# --------------------------------------------------------------------------- #
#  Publikacja
# --------------------------------------------------------------------------- #
def test_gotowy_obraz_idzie_adresem_bez_certshota(sesja, monkeypatch):
    s, _ = sesja
    post = _post(sesja, kind="photo",
                 media_url="https://forexpassing.com/tg/am-01.png")
    wolano = []
    monkeypatch.setattr(certshot, "render",
                        lambda *a, **k: wolano.append(1))
    wynik, tg = _wyslij(monkeypatch, s, post)

    assert wynik["posted"] is True
    assert not wolano, "gotowy obraz nie ma po co isc przez certshota"
    assert tg.wywolania[0][1]["photo_url"].endswith("am-01.png")


def test_adres_strony_dalej_idzie_przez_certshota(sesja, monkeypatch):
    s, _ = sesja
    post = _post(sesja, kind="photo",
                 media_url="https://protradersfunding.com/payout/abc?bare=1")
    wynik, tg = _wyslij(monkeypatch, s, post, zrzut=b"\x89PNG-udawany")

    assert wynik["posted"] is True
    assert tg.wywolania[0][1]["png"] is True
    assert tg.wywolania[0][1]["photo_url"] is None


def test_wideo_leci_adresem(sesja, monkeypatch):
    s, _ = sesja
    post = _post(sesja, kind="video",
                 media_url="https://forexpassing.com/tg/am-11-mike.mp4")
    wynik, tg = _wyslij(monkeypatch, s, post)

    assert wynik["posted"] is True
    assert wynik["video"] is True
    assert tg.wywolania[0][1]["video_url"].endswith("am-11-mike.mp4")


def test_zalacznik_bez_zrodla_konczy_jako_failed(sesja, monkeypatch):
    """Sedno pliku: cicha awaria ma być głośna.

    `certshot.render()` nigdy nie rzuca — przy wyłączonej usłudze zwraca
    `None`. Bez tego testu post wychodził wtedy jako sam tekst i dostawał
    status `published`.
    """
    s, _ = sesja
    post = _post(sesja, kind="photo",
                 media_url="https://protradersfunding.com/payout/abc?bare=1")
    wynik, tg = _wyslij(monkeypatch, s, post, zrzut=None)

    assert wynik["posted"] is False
    assert not tg.wywolania, "nic nie powinno pojsc na Telegrama"
    assert post.status == "failed"
    assert "nie ma czego wysłać" in post.last_error


# --------------------------------------------------------------------------- #
#  Walidacja
# --------------------------------------------------------------------------- #
def test_wideo_wymaga_adresu_klipu(sesja):
    s, _ = sesja
    post = _post(sesja, kind="video", media_url="")
    with pytest.raises(contentbot.NieprawdziwyPost, match="bez adresu klipu"):
        contentbot.waliduj(s, post)


def test_wideo_odrzuca_adres_ktory_nie_jest_plikiem_wideo(sesja):
    s, _ = sesja
    post = _post(sesja, kind="video",
                 media_url="https://youtube.com/watch?v=abc")
    with pytest.raises(contentbot.NieprawdziwyPost, match="plik wideo"):
        contentbot.waliduj(s, post)


def test_podpis_pod_filmem_ma_limit_zdjecia(sesja):
    """1024, nie 4096 — Telegram tnie podpis pod filmem tak samo jak pod zdjęciem."""
    s, _ = sesja
    post = _post(sesja, kind="video",
                 media_url="https://forexpassing.com/tg/klip.mp4",
                 body="x" * (contentbot.LIMIT_PODPISU + 1))
    with pytest.raises(contentbot.NieprawdziwyPost, match="1024"):
        contentbot.waliduj(s, post)
