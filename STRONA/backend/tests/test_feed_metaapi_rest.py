"""Realne konto MT5 czytane REST-em i router kanałów.

Produkcja stoi na hostingu bezserwerowym, gdzie funkcja żyje sekundę —
strumieniowe połączenie SDK (`MetaApiFeed`) nic tam nie daje, a kosztuje
kilkanaście sekund na pierwszy odczyt. `MetaApiRestFeed` robi to samo dwoma
zapytaniami HTTP, więc testy jadą na wstrzykniętym transporcie i nie dotykają
sieci.

Router pilnuje rzeczy ważniejszej niż wygoda: realny rachunek ma GARSTKA kont
z add-onem Copytrading, a pozostałe 65 kont produkcji musi zachować się
dokładnie tak, jak dotąd.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.feed import (Feed, MarketSnapshot, MetaApiRestFeed,  # noqa: E402
                      NullFeed, RoutedFeed)

KONTO = "acc-123"


class Transport:
    """Atrapa HTTP: oddaje to, co jej wpisano, i zapisuje, o co ją pytano."""

    def __init__(self, odpowiedzi: dict):
        self.odpowiedzi = odpowiedzi
        self.wywolania: list[tuple[str, str, dict | None]] = []

    async def __call__(self, method, url, *, headers, json):
        assert headers.get("auth-token"), "każde zapytanie musi nieść token"
        self.wywolania.append((method, url, json))
        for fragment, (status, body) in self.odpowiedzi.items():
            if fragment in url:
                if isinstance(body, Exception):
                    raise body
                return status, body
        raise AssertionError(f"nieoczekiwany adres: {url}")


def _feed(odpowiedzi) -> tuple[MetaApiRestFeed, Transport]:
    t = Transport(odpowiedzi)
    return MetaApiRestFeed(token="tok", region="new-york", transport=t), t


# --------------------------------------------------------------------------- #
#  Odczyt
# --------------------------------------------------------------------------- #
def test_snapshot_czyta_equity_i_wolumen():
    f, t = _feed({
        "/account-information": (200, {"balance": 25000.0, "equity": 24500.5}),
        "/positions": (200, [{"id": "1", "volume": 0.5}, {"id": "2", "volume": 1.25}]),
    })

    snap = asyncio.run(f.snapshot("111", KONTO, 25000.0))

    assert snap.balance == 25000.0 and snap.equity == 24500.5
    assert snap.open_pnl == -499.5
    assert snap.has_open_position is True
    assert snap.volume_lots == 1.75 and snap.volume_known is True
    assert "mt-client-api-v1.new-york" in t.wywolania[0][1]


def test_bez_pozycji_konto_nie_liczy_dnia_handlowego():
    f, _ = _feed({
        "/account-information": (200, {"balance": 25000.0, "equity": 25000.0}),
        "/positions": (200, []),
    })

    snap = asyncio.run(f.snapshot("111", KONTO, 25000.0))

    assert snap.has_open_position is False
    assert snap.volume_lots == 0.0 and snap.volume_known is True


def test_nieczytelne_pozycje_nie_karza_tradera_limitem_lotow():
    """`volume_known=False` mówi silnikowi, żeby limitu NIE egzekwować —
    inaczej nasz problem z siecią kończyłby się breachem klienta."""
    f, _ = _feed({
        "/account-information": (200, {"balance": 25000.0, "equity": 24000.0}),
        "/positions": (500, None),
    })

    snap = asyncio.run(f.snapshot("111", KONTO, 25000.0))

    assert snap is not None, "sam brak pozycji nie może unieważnić odczytu equity"
    assert snap.volume_known is False
    assert snap.has_open_position is True, "niezerowy wynik = coś jest otwarte"


def test_brak_equity_zwraca_none_zamiast_zera():
    """Najgroźniejszy błąd w tym miejscu: podstawić 0 i zbreachować konto."""
    f, _ = _feed({"/account-information": (500, {"error": "boom"})})

    assert asyncio.run(f.snapshot("111", KONTO, 25000.0)) is None


def test_konto_bez_metaapi_id_nie_generuje_zapytan():
    f, t = _feed({})

    assert asyncio.run(f.snapshot("111", None, 25000.0)) is None
    assert t.wywolania == []


# --------------------------------------------------------------------------- #
#  Egzekwowanie breachu
# --------------------------------------------------------------------------- #
def test_zamyka_kazda_pozycje_z_osobna():
    f, t = _feed({
        "/positions": (200, [{"id": "7"}, {"id": "9"}]),
        "/trade": (200, {"numericCode": 10009}),
    })

    ile = asyncio.run(f.close_all_positions(KONTO))

    assert ile == 2
    zlecenia = [j for m, u, j in t.wywolania if "/trade" in u]
    assert [z["positionId"] for z in zlecenia] == ["7", "9"]
    assert all(z["actionType"] == "POSITION_CLOSE_ID" for z in zlecenia)


def test_jedna_niezamknieta_pozycja_nie_przerywa_reszty():
    """Po breachu liczy się, żeby zamknąć ich jak najwięcej."""
    class T(Transport):
        async def __call__(self, method, url, *, headers, json):
            if "/trade" in url and json["positionId"] == "7":
                raise RuntimeError("rejected")
            return await super().__call__(method, url, headers=headers, json=json)

    t = T({"/positions": (200, [{"id": "7"}, {"id": "9"}]), "/trade": (200, {})})
    f = MetaApiRestFeed(token="tok", transport=t)

    # Obie pozycje próbowane, ale porażka jednej jest ZGŁASZANA — poller
    # zapisuje wtedy enforcement_pending i tick ryzyka ponawia odcięcie.
    try:
        asyncio.run(f.close_all_positions(KONTO))
        raise AssertionError("nieudane zamknięcie musi być widoczne dla wołającego")
    except RuntimeError as e:
        assert "1 position(s) not closed (1 closed)" in str(e)
    # 7 odrzucona, a mimo to 9 zamknięta (atrapa zapisuje tylko udane wywołania)
    zamykane = [j["positionId"] for m, u, j in t.wywolania if "/trade" in u]
    assert zamykane == ["9"]


def test_lock_robi_undeploy_w_provisioningu():
    f, t = _feed({"/undeploy": (200, {})})

    asyncio.run(f.lock(KONTO))

    metoda, url, _ = t.wywolania[0]
    assert metoda == "POST" and url.endswith(f"/accounts/{KONTO}/undeploy")
    assert "mt-provisioning-api-v1" in url


def test_nieudany_undeploy_jest_zglaszany():
    """Breach i tak się dokonuje (to pilnuje poller), ale feed nie może
    udawać sukcesu — inaczej nikt nie ponowi odcięcia."""
    f, _ = _feed({"/undeploy": (503, None)})
    try:
        asyncio.run(f.lock(KONTO))
        raise AssertionError("nieudany undeploy musi rzucić")
    except RuntimeError:
        pass


# --------------------------------------------------------------------------- #
#  Router
# --------------------------------------------------------------------------- #
class _Zapamietujacy(Feed):
    def __init__(self, nazwa):
        self.nazwa = nazwa
        self.pytano = []

    async def snapshot(self, login, metaapi_account_id, initial_balance, *,
                       password=None, server=None):
        self.pytano.append(login)
        return MarketSnapshot(balance=1.0, equity=1.0, open_pnl=0.0, has_open_position=False)

    async def close_all_positions(self, metaapi_account_id, *, login=None, password=None):
        self.pytano.append("close")
        return 1

    async def lock(self, metaapi_account_id, *, login=None, password=None):
        self.pytano.append("lock")


@pytest.fixture
def router():
    baza, realny = _Zapamietujacy("baza"), _Zapamietujacy("realny")
    return RoutedFeed(baza, realny), baza, realny


def test_konto_z_rachunkiem_idzie_do_metaapi(router):
    r, baza, realny = router

    asyncio.run(r.snapshot("111", KONTO, 25000.0))

    assert realny.pytano == ["111"] and baza.pytano == []


def test_konto_bez_rachunku_zostaje_na_dotychczasowym_kanale(router):
    """Sedno: 65 istniejących kont ma się zachować dokładnie jak dotąd."""
    r, baza, realny = router

    asyncio.run(r.snapshot("222", None, 25000.0))

    assert baza.pytano == ["222"] and realny.pytano == []


def test_egzekwowanie_tez_trafia_we_wlasciwy_kanal(router):
    r, baza, realny = router

    asyncio.run(r.close_all_positions(KONTO))
    asyncio.run(r.lock(KONTO))

    assert realny.pytano == ["close", "lock"] and baza.pytano == []


def test_router_bez_realnego_konta_nie_wymaga_metaapi():
    """NullFeed + brak id = zero kontaktu ze światem."""
    r = RoutedFeed(NullFeed(), _Zapamietujacy("realny"))

    assert asyncio.run(r.snapshot("333", None, 1000.0)) is None
