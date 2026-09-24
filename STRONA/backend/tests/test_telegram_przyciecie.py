"""Podpis z linkami nie może być cięty w środku znacznika.

Usterka z produkcji (post `archive:mgmt/7`): 997 widocznych znaków mieściło się
w podpisie 1024, ale po dołożeniu linków CTA surowy HTML miał 1480 znaków,
`caption[:1024]` przeciął `<a href="…">` i Telegram odrzucił post:
„Can't find end tag corresponding to start tag a".
"""
import html
import os
import re
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app import contentbot, telegram  # noqa: E402

# Ten sam kształt co post #7: ~1000 widocznych znaków, wezwanie i dwie wzmianki.
POST_7 = html.escape(
    "✅ THE 120-DAY GUARANTEE IS HERE\n\n" + "We handle it for you. " * 38
    + "\n\n👉 Click here to send us a message\n\n➡ Message @fxpassingadmin to get started!"
    "\n\n❕ Our only admin is @fxpassingadmin if anyone contacts you, it's a scam.",
    quote=False)


def _widoczne(t):
    return len(html.unescape(re.sub(r"<[^>]*>", "", t)))


def _zbalansowane(t):
    stos = []
    for zamyk, nazwa in re.findall(r"<(/?)(\w+)[^>]*>", t):
        if zamyk:
            assert stos and stos[-1] == nazwa, t[-200:]
            stos.pop()
        else:
            stos.append(nazwa)
    assert not stos, stos


def test_mieszczacy_sie_podpis_z_linkami_wychodzi_caly():
    z_linkami = contentbot.dolinkuj(POST_7, "https://t.me/forex_passing_admin")
    assert _widoczne(z_linkami) <= 1024 < len(z_linkami)
    assert telegram.przytnij_html(z_linkami, 1024) == z_linkami


def test_send_content_wysyla_caly_podpis_z_linkami():
    wyslane = []

    def transport(url, body, content_type):
        wyslane.append(body)
        return 200, b'{"ok":true,"result":{"message_id":7}}'

    z_linkami = contentbot.dolinkuj(POST_7, "https://t.me/forex_passing_admin")
    ok, _, _ = telegram.send_content("-100123", z_linkami, photo_url="https://x/7.jpg",
                                     token="t", transport=transport)
    assert ok
    assert z_linkami.encode() in wyslane[0]


def test_za_dlugi_tniety_po_widocznych_i_domkniety():
    t = ("<b>" + "a" * 20 + '<a href="https://t.me/x?text=' + "y" * 300
         + '">klik tu</a></b> koniec')
    wynik = telegram.przytnij_html(t, 24)
    assert _widoczne(wynik) == 24
    _zbalansowane(wynik)
    assert wynik.endswith('">klik</a></b>')


def test_encje_licza_sie_jako_jeden_znak_i_nie_sa_ciete():
    t = "&lt;" * 10
    assert telegram.przytnij_html(t, 10) == t
    assert telegram.przytnij_html(t, 3) == "&lt;&lt;&lt;"
