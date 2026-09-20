"""Puls do budzika partnera z naszego ruchu.

Partner odświeża licznik miejsc RUCHEM, bo konto Hobby ma dwa sloty crona
i oba zajmuje ten panel. Jego ruch chodzi falami razem z kampaniami, nasz
panel odzywa się równiej — stąd dokładamy mu uderzeń.

To uprzejmość wobec CUDZEGO systemu, więc trzy rzeczy muszą być pewne: nie
wolno nam go zalewać, nie wolno przez niego wywrócić własnego requestu,
i bez adresu w środowisku ma panować cisza — nie błąd.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import main  # noqa: E402

BAZA = "https://partner.example"


@pytest.fixture(autouse=True)
def zeruj_throttle():
    """Throttle jest stanem modułu — bez zerowania testy widziałyby się nawzajem."""
    main._OSTATNI_PING_PARTNERA = 0.0
    yield
    main._OSTATNI_PING_PARTNERA = 0.0


@pytest.fixture
def strzaly(monkeypatch):
    wywolane: list[str] = []

    class Odp:
        def read(self, _n=None):
            return b"{}"

    def fake(url, timeout=None):
        wywolane.append(url)
        return Odp()

    monkeypatch.setattr(main.urllib.request, "urlopen", fake)
    return wywolane


def test_bez_adresu_w_srodowisku_panuje_cisza(monkeypatch, strzaly):
    """Brak zmiennej to poprawny stan, nie awaria — panel ma dzialac bez
    partnera tak samo jak z nim."""
    monkeypatch.setattr(main.settings, "partner_pay_base_url", "")

    main._ping_partnera()

    assert strzaly == []


def test_puls_idzie_pod_adres_ze_zmiennej(monkeypatch, strzaly):
    monkeypatch.setattr(main.settings, "partner_pay_base_url", BAZA)

    main._ping_partnera()

    assert strzaly == [f"{BAZA}/api/spots-ping"]


def test_drugi_strzal_w_oknie_nie_wychodzi(monkeypatch, strzaly):
    """Sedno: to CUDZY endpoint. Ruch na panelu potrafi isc kilkadziesiat razy
    na minute i bez throttle'a kazde wejscie byloby pukaniem."""
    monkeypatch.setattr(main.settings, "partner_pay_base_url", BAZA)

    for _ in range(25):
        main._ping_partnera()

    assert len(strzaly) == 1


def test_po_uplywie_okna_puls_wraca(monkeypatch, strzaly):
    monkeypatch.setattr(main.settings, "partner_pay_base_url", BAZA)

    main._ping_partnera()
    main._OSTATNI_PING_PARTNERA -= main._PING_PARTNERA_SEK + 1
    main._ping_partnera()

    assert len(strzaly) == 2


def test_padniety_partner_nie_wywraca_naszego_requestu(monkeypatch):
    """Cudza dostepnosc to nie nasz blad. Wyjatek ma zostac polkniety, bo ta
    funkcja siedzi w middleware KAZDEGO wejscia na panel."""
    monkeypatch.setattr(main.settings, "partner_pay_base_url", BAZA)

    def wybuch(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(main.urllib.request, "urlopen", wybuch)

    main._ping_partnera()   # brak wyjatku = test zdany


def test_okno_throttle_jest_rozsadne():
    """Licznik po tamtej stronie zmienia sie kilka razy dziennie. Puls co
    minute bylby pukaniem bez powodu, co godzine — bezuzyteczny."""
    assert 60 <= main._PING_PARTNERA_SEK <= 900
