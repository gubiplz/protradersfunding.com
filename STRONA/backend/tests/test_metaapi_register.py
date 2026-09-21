"""Podpinanie rachunku MT5 pod MetaApi — automatycznie, nie ręcznie.

Sprawdzone 2026-09-21 na żywym API: MetaApi **nie pozwala zakładać** dem na
`MetaQuotes-Demo` („Matching available trading server … not found"), ale
**podłączenie** istniejącego konta na tym samym serwerze przyjmuje (HTTP 202).
Stąd ten krok: konto powstaje ręcznie (web.metatrader.app → pula), a system
sam podpina je pod MetaApi, bo bez identyfikatora MetaApi silnik nie ma czego
czytać.

Rejestracja KOSZTUJE u dostawcy, więc testy pilnują przede wszystkim tego,
komu jej NIE robimy.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app import provisioning  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Account, AppSetting  # noqa: E402

init_db()


class _Rejestrator:
    """Atrapa klienta MetaApi — liczy, ile razy po nią sięgnięto."""

    def __init__(self, wynik="mt-777", blad=None):
        self.wynik, self.blad = wynik, blad
        self.wywolania = []

    async def register_account(self, creds, *, name, **kw):
        self.wywolania.append((creds.login, creds.server, name))
        if self.blad:
            raise self.blad
        return self.wynik


@pytest.fixture
def rejestrator(monkeypatch):
    r = _Rejestrator()
    monkeypatch.setattr(provisioning.metaapi_provisioning, "make_registrar", lambda s=None: r)
    return r


@pytest.fixture
def wlaczone():
    """Przełącznik „realne MT5 dla Copytradingu" z zakładki MT5 Pool."""
    s = SessionLocal()
    row = s.get(AppSetting, provisioning.COPYTRADING_REAL_KEY)
    if row is None:
        row = AppSetting(key=provisioning.COPYTRADING_REAL_KEY)
        s.add(row)
    row.value = "1"
    s.commit(); s.close()
    yield
    s = SessionLocal()
    row = s.get(AppSetting, provisioning.COPYTRADING_REAL_KEY)
    if row:
        row.value = "0"; s.commit()
    s.close()


LICZNIK = iter(range(10_000))


def _konto(*, copytrading=True, haslo="tajne123", metaapi_id=None, status="active") -> int:
    numer = next(LICZNIK)
    s = SessionLocal()
    acc = Account(login=f"70{numer:05d}", trader_name="Reg Tester", product_key="2step-25k",
                  preset="2step-25k", initial_balance=25_000.0, steps=2, phase="eval_1",
                  status=status, balance=25_000.0, equity=25_000.0, peak_equity=25_000.0,
                  day_start_equity=25_000.0, day_start_balance=25_000.0,
                  platform_login=f"70{numer:05d}", platform_password=haslo,
                  platform_server="MetaQuotes-Demo", mt5_backed=True,
                  metaapi_account_id=metaapi_id, copytrading=copytrading)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _podepnij(aid):
    s = SessionLocal()
    try:
        return asyncio.run(provisioning.zarejestruj_w_metaapi(s, s.get(Account, aid)))
    finally:
        s.close()


def _id_konta(aid):
    s = SessionLocal()
    try:
        return s.get(Account, aid).metaapi_account_id
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Kiedy podpinamy
# --------------------------------------------------------------------------- #
def test_konto_z_addonem_zostaje_podpiete(rejestrator, wlaczone):
    aid = _konto()

    assert _podepnij(aid) == "mt-777"
    assert _id_konta(aid) == "mt-777"
    assert len(rejestrator.wywolania) == 1
    _login, serwer, _nazwa = rejestrator.wywolania[0]
    assert serwer == "MetaQuotes-Demo"


def test_drugie_wywolanie_nie_placi_drugi_raz(rejestrator, wlaczone):
    """Identyfikator już jest — nowa rejestracja to nowe konto u dostawcy."""
    aid = _konto()
    _podepnij(aid)

    _podepnij(aid)

    assert len(rejestrator.wywolania) == 1


# --------------------------------------------------------------------------- #
#  Komu NIE podpinamy — to pilnuje rachunku
# --------------------------------------------------------------------------- #
def test_bez_addonu_ani_jednego_zapytania(rejestrator, wlaczone):
    aid = _konto(copytrading=False)

    assert _podepnij(aid) is None
    assert rejestrator.wywolania == []


def test_wylaczony_przelacznik_wstrzymuje_wszystko(rejestrator):
    """Bez `wlaczone` — panel ma ostatnie słowo, nawet gdy add-on kupiony."""
    aid = _konto()

    assert _podepnij(aid) is None
    assert rejestrator.wywolania == []


def test_konto_bez_hasla_nie_ma_czego_podpiac(rejestrator, wlaczone):
    aid = _konto(haslo=None)

    assert _podepnij(aid) is None
    assert rejestrator.wywolania == []


# --------------------------------------------------------------------------- #
#  Gdy MetaApi nie odpowiada
# --------------------------------------------------------------------------- #
def test_porazka_nie_wywraca_konta_i_naklada_przerwe(monkeypatch, wlaczone):
    """Trader ma działające poświadczenia — nieudana rejestracja to NASZ
    problem z odczytem, nie powód, żeby cokolwiek mu zabierać."""
    r = _Rejestrator(blad=RuntimeError("insufficient funds"))
    monkeypatch.setattr(provisioning.metaapi_provisioning, "make_registrar", lambda s=None: r)
    aid = _konto()

    assert _podepnij(aid) is None
    assert _id_konta(aid) is None

    _podepnij(aid)   # zaraz po błędzie: backoff ma nie dopuścić do API
    assert len(r.wywolania) == 1


def test_recznie_wymuszone_podpiecie_omija_przerwe(monkeypatch, wlaczone):
    """Przerwa chroni przed automatem, nie przed człowiekiem: admin klika
    „Connect them now" właśnie dlatego, że usunął przyczynę poprzedniej
    porażki — kazanie mu czekać pół godziny byłoby karaniem go za to."""
    r = _Rejestrator(blad=RuntimeError("chwilowa awaria"))
    monkeypatch.setattr(provisioning.metaapi_provisioning, "make_registrar", lambda s=None: r)
    aid = _konto()
    _podepnij(aid)                      # porażka → przerwa
    assert len(r.wywolania) == 1

    r.blad = None                       # przyczyna usunięta
    s = SessionLocal()
    try:
        wynik = asyncio.run(provisioning.zarejestruj_w_metaapi(
            s, s.get(Account, aid), None, None, wymus=True))
    finally:
        s.close()

    assert wynik == "mt-777"
    assert len(r.wywolania) == 2, "wymuszenie ma pominąć przerwę"


def test_brak_tokenu_konczy_sie_cicho(monkeypatch, wlaczone):
    monkeypatch.setattr(provisioning.metaapi_provisioning, "make_registrar", lambda s=None: None)
    aid = _konto()

    assert _podepnij(aid) is None
    assert _id_konta(aid) is None


# --------------------------------------------------------------------------- #
#  Sam klient MetaApi
# --------------------------------------------------------------------------- #
def test_klient_powstaje_takze_bez_podanych_ustawien(monkeypatch):
    """Regresja: `make_registrar()` wołane BEZ ustawień wywracało się na
    `NameError` (moduł importuje `get_settings` lokalnie, a ta funkcja o tym
    zapomniała). Wyrażenie `settings or get_settings()` skraca się, gdy
    ustawienia podano — więc ścieżka z provisioningu działała, a dogrywka
    z ticku ryzyka, która podaje `None`, była trwale zepsuta.

    Atrapa `make_registrar` w pozostałych testach tego nie złapie z definicji,
    dlatego ten przypadek woła funkcję PRAWDZIWĄ.
    """
    from app import metaapi_provisioning as mp
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "metaapi_token", "tok-testowy", raising=False)

    klient = mp.make_registrar()          # bez argumentu — o to całe zamieszanie

    assert klient is not None
    assert isinstance(klient, mp.MetaApiProvisioner)


def test_bez_tokenu_klient_nie_powstaje(monkeypatch):
    from app import metaapi_provisioning as mp
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "metaapi_token", "", raising=False)

    assert mp.make_registrar() is None


# --------------------------------------------------------------------------- #
#  Dogrywka z ticku ryzyka
# --------------------------------------------------------------------------- #
def test_dogrywka_bierze_tylko_konta_ktorym_czegos_brakuje(rejestrator, wlaczone):
    """Asercje są per konto, nie na liczniku: w bazie leżą jeszcze konta
    z poprzednich testów i one też kwalifikują się do dogrywki."""
    brakuje = _konto()
    juz_ma = _konto(metaapi_id="mt-juz-jest")
    bez_addonu = _konto(copytrading=False)
    zbreachowane = _konto(status="failed")

    asyncio.run(provisioning.dopnij_brakujace_rejestracje(SessionLocal))

    assert _id_konta(brakuje) == "mt-777", "temu brakowało i miał dostać"
    assert _id_konta(juz_ma) == "mt-juz-jest", "istniejącego id nie wolno nadpisać"
    assert _id_konta(bez_addonu) is None, "bez add-onu nie płacimy za konto"
    assert _id_konta(zbreachowane) is None, "zamkniętego konta nie ma po co podpinać"
