"""Newsletter ze strony publicznej: zapis z kodem, wypisanie, lista i wysyłka w panelu.
Plus strona /contact i blok „Still need help?" na /faq."""
import os
import tempfile

_DB = os.path.join(tempfile.gettempdir(), "pf_test_newsletter.db")
if not os.environ.get("TEST_DATABASE_URL") and os.path.exists(_DB):
    os.remove(_DB)      # świeża baza: poprzedni przebieg zostawiał adresy i psuł asercje
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + _DB
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import NewsletterSubscriber  # noqa: E402

init_db()
ADMIN = {"X-Admin-Token": get_settings().admin_token}


@pytest.fixture
def maile(monkeypatch):
    out = []

    def _send(event, to, ctx=None):
        out.append((event, to, ctx or {}))
        return None
    monkeypatch.setattr(notify, "send", _send)
    monkeypatch.setattr(notify, "send_now", _send)
    return out


def _token(email):
    s = SessionLocal()
    try:
        return s.query(NewsletterSubscriber).filter(NewsletterSubscriber.email == email).one().token
    finally:
        s.close()


def test_zapis_daje_kod_i_mail_powitalny_bez_zdradzania_duplikatu(maile):
    with TestClient(app) as c:
        r = c.post("/api/newsletter/subscribe", json={"email": "Nowy@Test.PL", "consent": True, "source": "popup"})
        assert r.status_code == 200, r.text
        assert r.json()["code"] == "WELCOME10" and r.json()["pct"] == 10
        assert maile[-1][0] == "newsletter_welcome" and maile[-1][1] == "nowy@test.pl"
        assert "/newsletter/unsubscribe/" in maile[-1][2]["unsubscribe_url"]
        # Duplikat: to samo 200, bez drugiego maila.
        n = len(maile)
        assert c.post("/api/newsletter/subscribe", json={"email": "nowy@test.pl"}).status_code == 200
        assert len(maile) == n
        assert c.post("/api/newsletter/subscribe", json={"email": "zle"}).status_code == 400
        assert c.post("/api/newsletter/subscribe", json={"email": "a@b.cc", "consent": False}).status_code == 400
        lista = c.get("/api/admin/newsletter", headers=ADMIN).json()
        assert any(s["email"] == "nowy@test.pl" and s["source"] == "popup" for s in lista["subscribers"])
        assert c.get("/api/admin/newsletter").status_code in (401, 403)


def test_wypisanie_i_powrot(maile):
    with TestClient(app) as c:
        c.post("/api/newsletter/subscribe", json={"email": "bye@test.pl"})
        tok = _token("bye@test.pl")
        r = c.get(f"/newsletter/unsubscribe/{tok}")
        assert r.status_code == 200 and "unsubscribed" in r.text
        assert c.get("/newsletter/unsubscribe/nie-ma-takiego").status_code == 200
        po = {s["email"]: s for s in c.get("/api/admin/newsletter", headers=ADMIN).json()["subscribers"]}
        assert po["bye@test.pl"]["unsubscribed_at"]
        csv = c.get("/api/admin/newsletter.csv", headers=ADMIN).text
        assert csv.startswith("email,source,") and "bye@test.pl" in csv
        # Ponowny zapis przywraca i wysyła powitanie jeszcze raz.
        n = len(maile)
        c.post("/api/newsletter/subscribe", json={"email": "bye@test.pl"})
        assert len(maile) == n + 1
        po = {s["email"]: s for s in c.get("/api/admin/newsletter", headers=ADMIN).json()["subscribers"]}
        assert not po["bye@test.pl"]["unsubscribed_at"]


def test_wysylka_pomija_wypisanych_i_ma_link_wypisania(maile):
    with TestClient(app) as c:
        for e in ("w1@test.pl", "w2@test.pl"):
            c.post("/api/newsletter/subscribe", json={"email": e})
        c.get(f"/newsletter/unsubscribe/{_token('w2@test.pl')}")
        maile.clear()
        r = c.post("/api/admin/newsletter/send", headers=ADMIN,
                   json={"subject": "News", "body": "Hello\n\nNew tier."})
        assert r.status_code == 200, r.text
        wyslane = [m for m in maile if m[0] == "newsletter_blast"]
        assert r.json()["sent"] == len(wyslane) and all(m[1] != "w2@test.pl" for m in wyslane)
        assert all("/newsletter/unsubscribe/" in m[2]["unsubscribe_url"] for m in wyslane)
        assert all(m[2]["subject"] == "News" for m in wyslane)
        # Próbny mail na jeden adres nie rusza listy.
        maile.clear()
        r = c.post("/api/admin/newsletter/send", headers=ADMIN,
                   json={"subject": "Test", "body": "x", "test_to": "ja@test.pl"})
        assert r.json() == {"sent": 1, "test": True} and maile[0][1] == "ja@test.pl"


def test_mail_powitalny_renderuje_kod():
    subject, body = notify._render("newsletter_welcome", {"code": "WELCOME10", "pct": 10,
                                                           "unsubscribe_url": "https://x/u/t"})
    assert "WELCOME10" in body and "https://x/u/t" in body and "10%" in subject
    html = notify._render_html("newsletter_welcome", {"code": "WELCOME10", "pct": 10,
                                                      "unsubscribe_url": "https://x/u/t"}, subject)
    assert "WELCOME10" in html and "Unsubscribe" in html


def test_strona_kontaktu_i_faq_z_pomoca():
    with TestClient(app) as c:
        r = c.get("/contact")
        assert r.status_code == 200
        assert "Still need help?" in r.text and "Message support" in r.text
        assert "How do I start a challenge?" in r.text and "/faq#q-" in r.text
        faq = c.get("/faq").text
        assert "Still need help?" in faq and 'id="faqQ"' in faq and 'id="getting-started"' in faq
        assert faq.count("<summary>") == 21
        home = c.get("/").text
        assert 'id="nlModal"' in home and 'data-source="footer"' in home
        assert "/static/img/steps/step-1.webp" in home and "1. Choose your challenge" in home
        assert "/contact" in c.get("/sitemap.xml").text
