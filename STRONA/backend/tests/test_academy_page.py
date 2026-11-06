"""Akademia: /academy — statyczna strona lekcji na wzorcu FAQ.

Sprawdzamy kontrakt SEO (200, ItemList w JSON-LD, wpis w sitemapie) i to,
że zwykły gość ma jak do niej trafić (link w nawigacji strony głównej).
"""
import os
import tempfile

# NamedTemporaryFile, nie stała nazwa: ten moduł bywa alfabetycznie pierwszy,
# więc jego baza staje się bazą CAŁEGO przebiegu — stały plik przeżywał między
# przebiegami i drugi run padał na UNIQUE danych z pierwszego.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.NamedTemporaryFile(
    suffix=".db", delete=False).name)
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import catalog  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)


def test_academy_zwraca_lekcje():
    r = client.get("/academy")
    assert r.status_code == 200
    html = r.text
    for kategoria in ["Getting started", "Risk management",
                      "Trading the challenge", "Psychology"]:
        assert kategoria in html
    # moduły z kafelkami lekcji: okładka, czas czytania, link do strony lekcji
    assert html.count('class="ac-card"') == 19
    assert "min read" in html and "<svg viewBox=\"0 0 400 225\"" in html
    assert '"@type":"ItemList"' in html
    assert "Trading Academy" in html


def test_strona_lekcji():
    from app import academy
    r = client.get("/academy/understanding-the-daily-loss-limit")
    assert r.status_code == 200
    assert "Understanding the daily loss limit" in r.text and '"@type":"Article"' in r.text
    assert "{{ site_name }}" not in client.get(f"/academy/{academy.LESSONS[0].slug}").text
    assert client.get("/academy/nie-ma-takiej").status_code == 404


def test_academy_w_sitemapie():
    xml = client.get("/sitemap.xml").text
    assert "/academy</loc>" in xml
    assert "/academy/static-drawdown-explained</loc>" in xml


def test_link_z_glownej():
    html = client.get("/").text
    assert 'href="/academy"' in html
