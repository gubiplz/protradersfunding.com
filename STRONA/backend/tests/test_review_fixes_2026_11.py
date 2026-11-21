"""Poprawki po recenzji strony (21.11.2026).

Teksty, które mówiły co innego niż produkt: EA (od teraz tylko z add-onem
„EA Allowed", dawny Copytrading), prowizja („$9/lot" jest prawdą tylko dla lota FX
za $100k), typ drawdownu, nazwy w terminalu. Do tego terminal w weekend: cena
z zamknięcia tygodnia zamiast żywej ceny Hyperliquid.
"""
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_review_fixes.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import legal, terminal  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)
ROOT = Path(__file__).resolve().parent.parent


def _txt(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_ea_tylko_z_addonem_w_regulaminie_faq_i_czacie():
    terms = _plain(client.get("/terms").text)
    assert "EAs are allowed)" not in terms and "and EAs are allowed" not in terms
    assert "only on accounts purchased with the EA Allowed add-on" in terms
    faq = _plain(client.get("/faq").text)
    assert "Yes to all three" not in faq
    assert "Only on accounts purchased with the EA Allowed add-on" in faq
    for plik in ("knowledge/01-agent-brief.md", "knowledge/02-facts.md", "knowledge/03-answers.md"):
        t = _txt(plik)
        assert "EAs all allowed" not in t and "weekend holding, expert advisors" not in t, plik
        assert "News, weekends and EAs are fine" not in t, plik


def test_nazwa_copytrading_znika_z_tekstow_dla_klienta():
    for sciezka in ("/terms", "/faq", "/"):
        assert "Copytrading" not in _plain(client.get(sciezka).text), sciezka
    for plik in ("static/js/site.js", "static/js/portal-app.js"):
        widoczne = re.findall(r"[>'`\"][^<'`\"]*Copytrading[^<'`\"]*", _txt(plik))
        assert not [w for w in widoczne if "/*" not in w and "//" not in w], (plik, widoczne)


def test_wersja_dokumentow_podbita():
    assert legal.DOCS_VERSION == "2026-11-21"
    assert f"Version {legal.DOCS_VERSION}" in client.get("/terms").text


def test_prowizja_procentem_nie_plaskie_9_dolarow():
    home = _plain(client.get("/").text)
    assert "/lot commission per side" not in home
    assert "0.009% commission per side (about $9 per standard FX lot)" in home
    faq = _plain(client.get("/faq").text)
    assert "A commission of $9 per lot per side" not in faq
    assert "0.009% of the trade value per side" in faq
    assert "$9 per lot per side in the Built-in Terminal" not in _txt("knowledge/02-facts.md")


def test_typ_drawdownu_zgodny_z_silnikiem():
    js = _txt("static/js/site.js")
    assert "'Balance-based'" not in js and "'Static, equity-based'" in js


def test_terminal_mowi_jezykiem_fx():
    html = client.get("/trade").text
    for stare in (">Reduce Only<", ">Max Slippage<", "<small>Mark</small>", "<dt>Fees</dt>"):
        assert stare not in html, stare
    for nowe in (">Close only<", ">Max deviation<", "<small>Price</small>", ">Commission</abbr>",
                 'id="tClosed"', 'id="tClosedNote"'):
        assert nowe in html, nowe


def test_cena_weekendowa_dla_zamknietego_rynku(monkeypatch):
    sobota = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(terminal, "_now", lambda: sobota)
    monkeypatch.setattr(terminal.tradebot, "is_weekend", lambda now=None: True)
    monkeypatch.setattr(terminal, "_cena_weekendowa", lambda coin, now: 3539.5)
    r = client.get("/api/terminal/weekend-close?symbol=XAUUSD")
    assert r.status_code == 200 and r.json() == {"symbol": "XAUUSD", "closed": True, "price": 3539.5}
    krypto = client.get("/api/terminal/weekend-close?symbol=BTCUSD").json()
    assert krypto["closed"] is False and krypto["price"] is None
    assert client.get("/api/terminal/weekend-close?symbol=NOPE").status_code == 404


def test_w_tygodniu_rynek_otwarty(monkeypatch):
    monkeypatch.setattr(terminal.tradebot, "is_weekend", lambda now=None: False)
    r = client.get("/api/terminal/weekend-close?symbol=XAUUSD").json()
    assert r["closed"] is False and r["price"] is None
