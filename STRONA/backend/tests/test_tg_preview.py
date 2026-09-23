"""Podgląd posta „jak na Telegramie" — logika z tg-preview.js wykonana w Node.

Asercja „w pliku jest taka funkcja" przeszłaby dla kodu, który liczy źle,
więc tu naprawdę wołamy parser i patrzymy na wynik. Najważniejszy jest licznik
znaków: panel pokazuje go obok limitu, a walidator na serwerze liczy po swojemu
(`contentbot.dlugosc_widoczna`). Gdyby te dwie liczby się rozjechały, podgląd
mówiłby „mieści się", a zatwierdzenie odmawiało — dlatego porównujemy je na
tych samych tekstach, zamiast wpisywać oczekiwane liczby ręcznie.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import contentbot

KATALOG = Path(__file__).resolve().parent
MODUL = KATALOG.parent / "static" / "js" / "tg-preview.js"
HARNESS = KATALOG / "tg_preview_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="brak node")


def _wolaj(*zadania):
    wynik = subprocess.run(
        ["node", str(HARNESS), str(MODUL), json.dumps(
            [{"fn": fn, "arg": arg} for fn, arg in zadania])],
        capture_output=True, text=True, timeout=30)
    assert wynik.returncode == 0, (
        f"harness zwrócił {wynik.returncode}: {wynik.stderr.strip()[:400]}")
    return json.loads(wynik.stdout)


def _html(tekst):
    return _wolaj(("tgHtml", tekst))[0]


# --------------------------------------------------------------------------- #
#  Licznik znaków = licznik serwera
# --------------------------------------------------------------------------- #
TEKSTY = [
    "Plain text, nothing else.",
    "<b>MEET GRACE</b> — $8,377. Verified.\nShe runs a payroll desk.",
    'Read it <a href="https://protradersfunding.com/payout/abc?bare=1">here</a>.',
    "Tom &amp; Jerry &#39;quoted&#39; &lt;not a tag&gt;",
    "💰 emoji count as one each 🚀🚀",
    "<i>nested <b>bold</b> italic</i>\n\n<blockquote>quote</blockquote>",
]


@pytest.mark.parametrize("tekst", TEKSTY)
def test_licznik_zgadza_sie_z_walidatorem(tekst):
    assert _html(tekst)["widoczne"] == contentbot.dlugosc_widoczna(tekst)


# --------------------------------------------------------------------------- #
#  Parser w stylu Telegrama
# --------------------------------------------------------------------------- #
def test_obslugiwane_znaczniki_przechodza_bez_uwag():
    w = _html("<b>a</b><strong>b</strong><i>c</i><u>d</u><s>e</s><code>f</code>")
    assert w["problemy"] == []
    assert "<b>a</b>" in w["html"] and "<i>c</i>" in w["html"]


def test_nieobslugiwany_znacznik_jest_ostrzezeniem_a_nie_html():
    """Telegram odrzuca całą wiadomość z <div> — walidator na serwerze tego
    nie widzi, więc podgląd jest jedynym miejscem, które o tym powie."""
    w = _html("<div>hi</div>")
    assert any("does not support <div>" in p for p in w["problemy"])
    assert "<div>" not in w["html"] and "&lt;div&gt;" in w["html"]


def test_zamkniecie_nieobslugiwanego_to_jedno_ostrzezenie():
    """Samo `</div>` nie ma dokładać „zamykasz coś, co nie jest otwarte" —
    przyczyna jest jedna i komunikat ma być jeden."""
    w = _html("<div>hi</div>")
    assert len(w["problemy"]) == 1
    assert "&lt;/div&gt;" in w["html"]


def test_spoiler_wewnatrz_odrzuconego_spana_zamyka_sie_poprawnie():
    w = _html('<span class="red"><span class="tg-spoiler">x</span></span>')
    assert '<span class="tgp-spoiler">x</span>' in w["html"]
    assert len(w["problemy"]) == 1 and "<span>" in w["problemy"][0]


def test_skrypt_nie_przedostaje_sie_do_podgladu():
    w = _html('<script>alert(1)</script><img src=x onerror=alert(1)>')
    assert "<script" not in w["html"] and "<img" not in w["html"]


def test_link_javascript_traci_adres():
    w = _html('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in w["html"]
    assert "<a>x</a>" in w["html"]


def test_link_https_zostaje_i_otwiera_sie_w_nowej_karcie():
    w = _html('<a href="https://t.me/x">x</a>')
    assert 'href="https://t.me/x"' in w["html"] and 'target="_blank"' in w["html"]


def test_niezamkniety_znacznik():
    w = _html("<b>open forever")
    assert any("never closed" in p for p in w["problemy"])
    assert w["html"].endswith("</b>"), "podgląd sam domyka, żeby nie rozlać stylu"


def test_zamkniecie_bez_otwarcia():
    w = _html("text</i>")
    assert any("not open" in p for p in w["problemy"])


def test_spoiler_tylko_z_klasa_telegrama():
    ok = _html('<span class="tg-spoiler">x</span>')
    zly = _html('<span class="red">x</span>')
    assert ok["problemy"] == [] and "tgp-spoiler" in ok["html"]
    assert any("<span>" in p for p in zly["problemy"])


# --------------------------------------------------------------------------- #
#  Grafika — ta sama decyzja co contentbot.opublikuj
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("post,typ", [
    ({"kind": "text", "media_url": ""}, "none"),
    ({"kind": "photo", "media_url": "https://x.test/media/posts/abc.png"}, "image"),
    ({"kind": "photo", "media_url": "https://x.test/a.JPG?v=2"}, "image"),
    ({"kind": "photo", "media_url": "https://x.test/payout/tok?bare=1"}, "page"),
    ({"kind": "photo", "media_url": "https://cdn.test/file", "origin": "archive:7"}, "image"),
    ({"kind": "video", "media_url": "https://x.test/clip.mp4"}, "video"),
])
def test_grafika_jak_przy_publikacji(post, typ):
    assert _wolaj(("tgGrafika", post))[0]["typ"] == typ


def test_zdjecie_bez_adresu_i_film_nie_mp4_to_problem():
    bez, film = _wolaj(("tgGrafika", {"kind": "photo", "media_url": ""}),
                       ("tgGrafika", {"kind": "video", "media_url": "https://x.test/a.mov"}))
    assert bez["problem"] and film["problem"]


def test_limity_jak_w_walidatorze():
    tekst, foto, film = _wolaj(("tgLimit", "text"), ("tgLimit", "photo"), ("tgLimit", "video"))
    assert tekst == contentbot.LIMIT_TEKSTU
    assert foto == film == contentbot.LIMIT_PODPISU


def test_makieta_niesie_tresc_grafike_i_godzine():
    html = _wolaj(("tgMakieta", {"tytul": "Account Management", "godzina": "20:48",
                                 "post": {"kind": "photo", "body": "<b>Hi</b>",
                                          "media_url": "https://x.test/p.png"}}))[0]
    assert "<b>Hi</b>" in html and 'src="https://x.test/p.png"' in html
    assert "20:48" in html and "Account Management" in html and ">AM<" in html
