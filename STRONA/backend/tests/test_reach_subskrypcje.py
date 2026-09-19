"""Zakup subskrybentów kanału u tego samego dostawcy co zasięg.

Ta ścieżka wydaje realne pieniądze jednym kliknięciem, więc testy pilnują
przede wszystkim tego, czego panel nie zobaczy: że usługa jest brana z ŻYWEGO
cennika, a nie z tego, co przyszło z przeglądarki, i że odmowa wraca zdaniem.
"""
import json
import os
import tempfile
import urllib.parse
from contextlib import contextmanager

os.environ.setdefault("DATABASE_URL",
                      f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import reach  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AppSetting  # noqa: E402

init_db()

CENNIK = [
    {"service": "5001", "name": "Telegram Channel Members [Real]",
     "category": "Telegram Members", "rate": "1.40", "min": "100", "max": "50000"},
    {"service": "5002", "name": "Telegram Channel Subscribers [Cheap]",
     "category": "Telegram Members", "rate": "0.90", "min": "50", "max": "10000"},
    {"service": "5003", "name": "Telegram Members REMOVE / Leave",
     "category": "Telegram Members", "rate": "0.10", "min": "10", "max": "1000"},
    {"service": "8407", "name": "Telegram Post Views",
     "category": "Telegram Views", "rate": "0.02", "min": "10", "max": "90000"},
    {"service": "9100", "name": "Instagram Followers",
     "category": "Instagram", "rate": "0.30", "min": "10", "max": "9000"},
]


@contextmanager
def _dostawca():
    u = get_settings()
    stare = (u.reach_api_url, u.reach_api_key)
    u.reach_api_url, u.reach_api_key = "https://dostawca.test/api/v2", "KLUCZ"
    try:
        yield
    finally:
        u.reach_api_url, u.reach_api_key = stare


def _transport(saldo="50.00", blad_add=None, log=None):
    def transport(url, body, ct):
        pola = dict(urllib.parse.parse_qsl(body.decode()))
        if log is not None:
            log.append(pola)
        if pola["action"] == "services":
            return 200, json.dumps(CENNIK).encode()
        if pola["action"] == "balance":
            return 200, json.dumps({"balance": saldo, "currency": "USD"}).encode()
        if blad_add:
            return 200, json.dumps({"error": blad_add}).encode()
        return 200, json.dumps({"order": 777}).encode()
    return transport


@pytest.fixture
def s():
    sesja = SessionLocal()
    for row in sesja.query(AppSetting).filter(AppSetting.key.like("reach_%")).all():
        sesja.delete(row)
    sesja.commit()
    try:
        yield sesja
    finally:
        sesja.close()


# --------------------------------------------------------------------------- #
#  Wybór usługi z cennika                                                      #
# --------------------------------------------------------------------------- #
def test_lista_zawiera_tylko_czlonkow_telegrama():
    with _dostawca():
        lista = reach.uslugi_subskrypcji(transport=_transport())
    assert [u["service"] for u in lista] == [5002, 5001]   # od najtanszej


def test_usluga_kasujaca_subskrybentow_nie_trafia_na_liste():
    """„REMOVE / Leave" ma w nazwie „members" i jest najtansza — czyli
    wyladowalaby na samej gorze listy, gotowa do klikniecia."""
    with _dostawca():
        lista = reach.uslugi_subskrypcji(transport=_transport())
    assert all(u["service"] != 5003 for u in lista)


def test_widoki_i_inne_platformy_odpadaja():
    with _dostawca():
        nazwy = " ".join(u["name"] for u in reach.uslugi_subskrypcji(transport=_transport()))
    assert "Views" not in nazwy and "Instagram" not in nazwy


# --------------------------------------------------------------------------- #
#  Zamówienie                                                                  #
# --------------------------------------------------------------------------- #
def test_zamowienie_idzie_na_kanal_a_nie_na_post(s):
    log = []
    with _dostawca():
        wynik = reach.zamow_subskrypcje(s, "@forex_passing_payouts", 350, 5002,
                                        transport=_transport(log=log))
    add = next(p for p in log if p["action"] == "add")
    assert add["link"] == "https://t.me/forex_passing_payouts"
    assert add["quantity"] == "350" and add["service"] == "5002"
    assert wynik["order"] == 777
    assert wynik["cost"] == pytest.approx(0.315)      # 350 * 0.90 / 1000


def test_cena_liczy_sie_z_cennika_dostawcy_nie_z_przegladarki(s):
    """Panel podaje wylacznie id uslugi — stawke serwer bierze sam, bo to on
    pilnuje salda i to on wydaje pieniadze."""
    with _dostawca():
        wynik = reach.zamow_subskrypcje(s, "kanal_testowy", 1000, 5001,
                                        transport=_transport())
    assert wynik["cost"] == pytest.approx(1.40)


def test_usluga_spoza_listy_odrzucona(s):
    with _dostawca():
        with pytest.raises(ValueError) as e:
            reach.zamow_subskrypcje(s, "kanal_testowy", 100, 9100, transport=_transport())
    assert "not on the provider" in str(e.value)


def test_ilosc_ponizej_minimum_uslugi_odrzucona(s):
    with _dostawca():
        with pytest.raises(ValueError) as e:
            reach.zamow_subskrypcje(s, "kanal_testowy", 60, 5001, transport=_transport())
    assert "at least 100" in str(e.value)


def test_puste_konto_zatrzymuje_zamowienie(s):
    with _dostawca():
        with pytest.raises(ValueError) as e:
            reach.zamow_subskrypcje(s, "kanal_testowy", 10000, 5001,
                                    transport=_transport(saldo="0.50"))
    assert "Balance is $0.50" in str(e.value)


def test_zla_nazwa_kanalu_odrzucona(s):
    with _dostawca():
        with pytest.raises(ValueError):
            reach.zamow_subskrypcje(s, "https://t.me/joinchat/AAAA", 200, 5001,
                                    transport=_transport())


def test_odmowa_dostawcy_wraca_zdaniem(s):
    with _dostawca():
        with pytest.raises(ValueError) as e:
            reach.zamow_subskrypcje(s, "kanal_testowy", 200, 5001,
                                    transport=_transport(blad_add="Not enough funds"))
    assert "Not enough funds" in str(e.value)


def test_bez_dostawcy_nie_ma_zamowienia(s):
    with pytest.raises(ValueError) as e:
        reach.zamow_subskrypcje(s, "kanal_testowy", 200, 5001, transport=_transport())
    assert "not configured" in str(e.value)


def test_zamowienie_zostaje_w_ostatnim_wyniku(s):
    with _dostawca():
        reach.zamow_subskrypcje(s, "kanal_testowy", 350, 5002, transport=_transport())
    assert "+350 members" in reach.ustawienia(s)["last_result"]
