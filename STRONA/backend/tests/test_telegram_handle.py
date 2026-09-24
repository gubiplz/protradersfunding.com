"""„Open in Telegram" z uchwytem, który nie istnieje.

Usterka z produkcji: uchwyt z ankiety („CorneliusRogers") nie był nazwą konta,
tylko imieniem wpisanym w złe pole. Panel otwierał `t.me/CorneliusRogers`,
Telegram odpowiadał „ten użytkownik nie istnieje", a dział nie wiedział, czy
zawinił panel. Teraz okno sprawdza uchwyt od razu, a klient z numerem w ankiecie
dostaje czat po telefonie.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

# Wycinki prawdziwych stron t.me (zmierzone 2026-09-24).
ISTNIEJE = (b'<meta property="og:title" content="Pavel Durov">'
            b'<div class="tgme_page_title"><span dir="auto">Pavel Durov</span></div>')
NIE_MA = (b'<meta property="og:title" content="Telegram: Contact @CorneliusRogers">'
          b'<div class="tgme_page_action">If you have Telegram, you can contact</div>')


def test_istniejace_konto_zwraca_nazwe():
    r = telegram.sprawdz_uchwyt("durov", transport=lambda url: (200, ISTNIEJE))
    assert r == {"exists": True, "name": "Pavel Durov"}


def test_imie_zamiast_uchwytu_to_brak_konta():
    r = telegram.sprawdz_uchwyt("CorneliusRogers", transport=lambda url: (200, NIE_MA))
    assert r["exists"] is False


def test_zly_format_nie_idzie_do_sieci():
    def zakaz(url):
        raise AssertionError("nie wolno pytać t.me o coś, co nie może być uchwytem")
    assert telegram.sprawdz_uchwyt("ab", transport=zakaz)["exists"] is False
    assert telegram.sprawdz_uchwyt("Cornelius Rogers", transport=zakaz)["exists"] is False


def test_awaria_sieci_to_brak_werdyktu_a_nie_ostrzezenie():
    """Panel ma milczeć, gdy nie wie — fałszywe „nie istnieje" byłoby gorsze."""
    assert telegram.sprawdz_uchwyt("durov", transport=lambda url: (503, b""))["exists"] is None
    assert telegram.sprawdz_uchwyt("durov", transport=lambda url: (200, b"<html>?</html>"))["exists"] is None


def test_pyta_wlasciwy_adres():
    adresy = []
    telegram.sprawdz_uchwyt("durov", transport=lambda url: (adresy.append(url), (200, ISTNIEJE))[1])
    assert adresy == ["https://t.me/durov"]


def test_endpoint_czysci_uchwyt_i_wymaga_admina(monkeypatch):
    zapytane = []
    monkeypatch.setattr(telegram, "sprawdz_uchwyt",
                        lambda h, **k: (zapytane.append(h), {"exists": False, "name": ""})[1])
    r = client.get("/api/admin/telegram/handle", params={"h": "@CorneliusRogers"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"handle": "CorneliusRogers", "exists": False, "name": ""}
    assert zapytane == ["CorneliusRogers"]
    assert client.get("/api/admin/telegram/handle", params={"h": "x"}).status_code in (401, 403, 404)


def test_lista_klientow_niesie_telefon_z_ankiety():
    """Bez numeru okno z Clients/Accounts nie miało czym zastąpić złego uchwytu."""
    s = SessionLocal()
    mail = "tg-uchwyt-telefon@example.com"
    try:
        t = Trader(email=mail, password_hash="x")
        s.add(t)
        s.add(Lead(email=mail, name="Cornelius Rogers", telegram="CorneliusRogers",
                   phone="+13239446790", source="money", status="new"))
        s.commit()
        tid = t.id
    finally:
        s.close()
    try:
        wiersz = next(x for x in client.get("/api/admin/traders", headers=ADMIN).json()
                      if x["id"] == tid)
        assert wiersz["telegram"] == "CorneliusRogers"
        assert wiersz["phone"] == "+13239446790"
    finally:
        s = SessionLocal()
        s.query(Lead).filter(Lead.email == mail).delete()
        s.query(Trader).filter(Trader.email == mail).delete()
        s.commit()
        s.close()
