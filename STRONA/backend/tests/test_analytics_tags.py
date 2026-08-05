"""Znaczniki analityczne: GA4 i Microsoft Clarity.

Ten rodzaj kodu psuje się po cichu. Literówka w nazwie zmiennej kontekstowej,
przemianowany partial albo nowy szablon renderowany z pominięciem `_page()` nie
wywalają niczego — strona działa, a dane po prostu przestają spływać i widać to
dopiero po tygodniu w pustym panelu.

Druga strona tej samej monety: znacznik NIE MOŻE wychodzić poza produkcją. Bez
tego nagrania z localhosta i podglądów gałęzi mieszają się z realnym ruchem.
"""
from conftest import zrodlo_portalu  # noqa: F401  (ustawia env przed importem app)
import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)

GA_TEST_ID = "G-TESTOWY"
CLARITY_TEST_ID = "testowyclarity"

# Strony publiczne renderowane przez `_page()`. Nie ma tu panelu admina —
# dashboard.html świadomie nie dołącza partiala.
STRONY = ["/", "/faq", "/objectives", "/affiliate", "/privacy", "/portal"]


@pytest.fixture
def wlaczona_analityka(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "ga_measurement_id", GA_TEST_ID)
    monkeypatch.setattr(main_mod.settings, "clarity_project_id", CLARITY_TEST_ID)


@pytest.fixture
def wylaczona_analityka(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "ga_measurement_id", "")
    monkeypatch.setattr(main_mod.settings, "clarity_project_id", "")


@pytest.mark.parametrize("sciezka", STRONY)
def test_obie_analityki_sa_na_kazdej_publicznej_stronie(sciezka, wlaczona_analityka):
    html = client.get(sciezka).text
    assert f"gtag/js?id={GA_TEST_ID}" in html, f"{sciezka}: zniknął GA4"
    assert "clarity.ms/tag/" in html, f"{sciezka}: zniknął Clarity"
    assert CLARITY_TEST_ID in html, f"{sciezka}: Clarity bez project ID — tag nic nie nagra"


@pytest.mark.parametrize("sciezka", STRONY)
def test_bez_id_nie_leci_ani_jeden_request_na_zewnatrz(sciezka, wylaczona_analityka):
    """Puste ID = brak snippetu, nie snippet z pustym ID.

    Wersja z pustym ID renderowałaby `clarity.ms/tag/` i strzelała do Microsoftu
    z każdego localhosta i każdego podglądu gałęzi.
    """
    html = client.get(sciezka).text
    assert "googletagmanager" not in html, f"{sciezka}: GA4 wyszedł mimo pustego ID"
    assert "clarity.ms" not in html, f"{sciezka}: Clarity wyszedł mimo pustego ID"


def test_panel_admina_nie_jest_nagrywany(wlaczona_analityka):
    """W panelu operatora na ekranie są cudze dane — nagranie sesji je utrwala."""
    html = client.get("/admin").text
    assert "clarity.ms" not in html, "Clarity nagrywa panel admina razem z danymi klientów"


def test_poza_produkcja_id_sa_puste():
    """Domyślne ID zależy od `VERCEL_ENV`, a ten test biegnie poza produkcją.

    Wartości domyślne liczą się RAZ, przy imporcie `config.py` (są wyrażeniami
    w ciele klasy), więc podmiana `VERCEL_ENV` monkeypatchem już nic tu nie
    zmieni — to jest asercja o tym, co widzi ten proces, i dokładnie ona łapie
    najgroźniejszy błąd: ID wpisane na sztywno, bez bramki na środowisko.
    """
    assert Settings().clarity_project_id == "", "Clarity zbiera dane poza produkcją"
    assert Settings().ga_measurement_id == "", "GA4 zbiera dane poza produkcją"


def test_polityka_prywatnosci_przyznaje_sie_do_analityki():
    """Strona nie może twierdzić, że nie ustawia cookies analitycznych.

    Twierdziła — przez cały czas, gdy GA4 już działał na produkcji. To jest
    oświadczenie na stronie prawnej, więc rozjazd z kodem jest kosztowniejszy
    niż sam brak zgody.
    """
    html = client.get("/privacy").text
    assert "No analytics or marketing cookies are set" not in html, (
        "polityka prywatności znowu zaprzecza znacznikom, które faktycznie ładujemy")
    for nazwa in ("Google Analytics", "Clarity"):
        assert nazwa in html, f"polityka prywatności nie wymienia {nazwa}"
