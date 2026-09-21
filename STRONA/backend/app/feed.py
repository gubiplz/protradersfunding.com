"""Źródła danych equity/balance dla kont.

Implementacje za wspólnym interfejsem:
  - NullFeed        : nic nie czyta — konta stoją (domyślne).
  - SimulatedFeed   : losowy spacer equity, BEZ MT5/MetaApi (tryb 0 zł, demo).
  - MetaApiFeed     : realne konto MT5 przez SDK MetaApi (FEED=metaapi + token).
  - MetaApiRestFeed : to samo, ale samym REST-em — dla hostingu bezserwerowego.
  - MetaQuotesWebFeed : equity czytane z web terminala (kanał darmowy).
  - RoutedFeed      : konta z realnym rachunkiem jednym kanałem, reszta drugim.

Poller (poller.py) nie wie którego używa — woła tylko `await feed.snapshot(...)`.
"""
from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import monotonic

from .config import get_settings


@dataclass
class MarketSnapshot:
    balance: float
    equity: float
    open_pnl: float
    has_open_position: bool
    # Laczny wolumen otwartych pozycji (loty). `volume_known=False` = nie udalo
    # sie odczytac; silnik wtedy NIE egzekwuje limitu, zeby nie karac za nasz blad.
    volume_lots: float = 0.0
    volume_known: bool = False


def _g(obj, *names):
    """Pobierz pole z obiektu DTO lub dicta (SDK bywa raz tak, raz tak)."""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


class Feed(ABC):
    @abstractmethod
    async def snapshot(self, login: str, metaapi_account_id: str | None, initial_balance: float,
                       *, password: str | None = None, server: str | None = None) -> MarketSnapshot | None:
        """`password`/`server` są potrzebne kanałom, które logują się na konto
        (web terminal MetaQuotes). MetaApi identyfikuje konto przez swoje id."""

    async def provision(self, spec: dict) -> dict | None:
        """Utwórz realne konto MT5 i zwróć poświadczenia, albo None (brak provisioningu)."""
        return None

    async def close_all_positions(self, metaapi_account_id: str, *,
                                  login: str | None = None, password: str | None = None) -> int:
        """Zamknij wszystkie otwarte pozycje (enforcement breachu). Zwraca liczbę zamkniętych."""
        return 0

    async def lock(self, metaapi_account_id: str, *,
                   login: str | None = None, password: str | None = None) -> None:
        """Odetnij konto od handlu (undeploy w MetaApi / oznaczenie po naszej stronie)."""
        return None

    async def close(self) -> None:  # pragma: no cover - sprzątanie opcjonalne
        return None


# --------------------------------------------------------------------------- #
#  NULL FEED — nic nie jest czytane z zewnątrz (tryb domyślny)                 #
# --------------------------------------------------------------------------- #
class NullFeed(Feed):
    """Brak źródła equity: konta stoją na ostatnim saldzie.

    `process_account` kończy wcześniej, gdy snapshot jest `None`, więc stan konta
    nie zmienia się ani o cent. Ruch pojawia się dopiero po włączeniu Trade BOT-a.
    Świadomie NIE używamy do tego `SimulatedFeed` — ten robi losowy spacer equity,
    czyli ruszałby konta bez żadnego powodu.
    """

    async def snapshot(self, login: str, metaapi_account_id: str | None, initial_balance: float,
                       *, password: str | None = None, server: str | None = None) -> None:
        return None


# --------------------------------------------------------------------------- #
#  SIMULATED FEED — demo bez żadnego konta zewnętrznego                        #
# --------------------------------------------------------------------------- #
class SimulatedFeed(Feed):
    """Generuje wiarygodne krzywe equity. Część kont jest 'tendencyjnie'
    zyskowna, część stratna — żeby na dashboardzie było widać i zaliczenia,
    i złamania reguł (daily loss / max DD)."""

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}

    def _init_state(self, login: str, initial_balance: float) -> dict:
        # bias zależny od loginu -> deterministyczny "charakter" konta
        seed = sum(ord(c) for c in login)
        rng = random.Random(seed)
        bias = rng.choice([+1.0, +1.0, +0.4, -1.2, -0.6])  # niektóre konta polecą w dół
        st = {
            "balance": initial_balance,
            "open_pnl": 0.0,
            "has_open": False,
            "bias": bias,
            "vol": initial_balance * 0.0015,   # zmienność per tick
            "rng": rng,
        }
        self._state[login] = st
        return st

    async def snapshot(self, login: str, metaapi_account_id: str | None, initial_balance: float,
                       *, password: str | None = None, server: str | None = None) -> MarketSnapshot:
        st = self._state.get(login) or self._init_state(login, initial_balance)
        rng: random.Random = st["rng"]

        # losowy ruch otwartej pozycji z dryfem
        if not st["has_open"] and rng.random() < 0.6:
            st["has_open"] = True
            st["open_pnl"] = 0.0

        if st["has_open"]:
            drift = st["bias"] * st["vol"] * 0.25
            st["open_pnl"] += rng.gauss(drift, st["vol"])
            # czasem zamknięcie pozycji -> realizacja do balansu
            if rng.random() < 0.25:
                st["balance"] += st["open_pnl"]
                st["open_pnl"] = 0.0
                st["has_open"] = False

        equity = st["balance"] + st["open_pnl"]
        return MarketSnapshot(
            balance=round(st["balance"], 2),
            equity=round(equity, 2),
            open_pnl=round(st["open_pnl"], 2),
            has_open_position=st["has_open"],
        )


# --------------------------------------------------------------------------- #
#  METAAPI FEED — realne konto MT5 (demo lub live) przez MetaApi cloud         #
# --------------------------------------------------------------------------- #
class MetaApiFeed(Feed):
    """Łączy się z MetaApi cloud i czyta accountInformation (equity/balance)
    oraz otwarte pozycje przez streaming connection (po jednym połączeniu na konto).

    Wymaga: pip install metaapi-cloud-sdk, FEED=metaapi, METAAPI_TOKEN.
    Konto MT5 musi być wcześniej dodane w MetaApi (1 konto w darmowym tierze).
    """

    def __init__(self) -> None:
        s = get_settings()
        if not s.metaapi_token:
            raise RuntimeError("FEED=metaapi wymaga ustawienia METAAPI_TOKEN w .env")
        # lazy import — nie wymuszamy zależności gdy używamy SimulatedFeed
        from metaapi_cloud_sdk import MetaApi  # type: ignore

        self._api = MetaApi(s.metaapi_token, {"region": s.metaapi_region})
        self._connections: dict[str, object] = {}

    async def _get_connection(self, metaapi_account_id: str):
        conn = self._connections.get(metaapi_account_id)
        if conn is not None:
            return conn
        account = await self._api.metatrader_account_api.get_account(metaapi_account_id)
        if account.state not in ("DEPLOYED",):
            await account.deploy()
        await account.wait_connected()
        conn = account.get_streaming_connection()
        await conn.connect()
        await conn.wait_synchronized()
        self._connections[metaapi_account_id] = conn
        return conn

    async def snapshot(self, login: str, metaapi_account_id: str | None, initial_balance: float,
                       *, password: str | None = None, server: str | None = None) -> MarketSnapshot | None:
        if not metaapi_account_id:
            return None
        try:
            conn = await self._get_connection(metaapi_account_id)
            state = conn.terminal_state
            info = state.account_information or {}
            positions = state.positions or []
            balance = float(info.get("balance", initial_balance))
            equity = float(info.get("equity", balance))
            return MarketSnapshot(
                balance=round(balance, 2),
                equity=round(equity, 2),
                open_pnl=round(equity - balance, 2),
                has_open_position=len(positions) > 0,
            )
        except Exception as e:  # pragma: no cover - sieć/SDK
            print(f"[MetaApiFeed] błąd dla {metaapi_account_id}: {e}")
            return None

    async def provision(self, spec: dict) -> dict | None:  # pragma: no cover - sieć/SDK
        """Tworzy realne konto MT5 demo przez MetaApi i rejestruje je do zarządzania.
        Zwraca prawdziwe poświadczenia (login/hasło/serwer) + metaapi_account_id."""
        s = get_settings()
        profile = s.metaapi_provisioning_profile_id
        if not profile:
            print("[MetaApiFeed] brak METAAPI_PROVISIONING_PROFILE_ID — pomijam realny provisioning")
            return None
        # MetaApi przy demo wymaga: name (imię+nazwisko), email, phone
        name = (spec.get("name") or "Trader").strip()
        if len(name.split()) < 2:
            name = f"{name} Trader"
        name = name[:64]
        email = spec.get("email") or "trader@propfunding.local"
        phone = spec.get("phone") or "+10000000000"
        demo = await self._api.metatrader_demo_account_api.create_mt5_demo_account(profile, {
            "accountType": s.metaapi_account_type or "standard",
            "balance": int(spec.get("balance", 100_000)),
            "name": name, "email": email, "phone": phone,
            "keywords": ["PropFunding"],
        })
        login = _g(demo, "login")
        password = _g(demo, "password")
        server = _g(demo, "serverName", "server_name", "server")
        investor = _g(demo, "investorPassword", "investor_password")
        mt = await self._api.metatrader_account_api.create_account({
            "name": name, "type": "cloud", "login": str(login), "password": password,
            "server": server, "platform": "mt5", "magic": 0,
            "application": "MetaApi", "keywords": ["PropFunding"],
        })
        return {"metaapi_account_id": _g(mt, "id"), "login": str(login),
                "password": password, "server": server, "investor_password": investor}

    async def close_all_positions(self, metaapi_account_id: str, *,  # pragma: no cover - sieć/SDK
                                  login: str | None = None, password: str | None = None) -> int:
        conn = await self._get_connection(metaapi_account_id)
        positions = conn.terminal_state.positions or []
        n = 0
        for p in positions:
            pid = _g(p, "id")
            try:
                await conn.close_position(pid)
                n += 1
            except Exception as e:
                print(f"[MetaApiFeed] close_position {pid} błąd: {e}")
        return n

    async def lock(self, metaapi_account_id: str, *,  # pragma: no cover - sieć/SDK
                   login: str | None = None, password: str | None = None) -> None:
        try:
            account = await self._api.metatrader_account_api.get_account(metaapi_account_id)
            await account.undeploy()   # zatrzymuje handel/synchronizację po stronie API
        except Exception as e:
            print(f"[MetaApiFeed] lock/undeploy błąd: {e}")
        self._connections.pop(metaapi_account_id, None)

    async def close(self) -> None:  # pragma: no cover
        for conn in self._connections.values():
            try:
                await conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
#  METAQUOTES WEB FEED — equity czytane z web terminala (kanał darmowy)        #
# --------------------------------------------------------------------------- #
class MetaQuotesWebFeed(Feed):
    """Czyta saldo/equity logując się na konto w web terminalu MetaQuotes.

    KOMPROMIS, który trzeba rozumieć: jedno logowanie trwa ~20 s, a poller tyka
    co 3 s. Dlatego `snapshot()` NIE blokuje pętli — oddaje ostatni znany stan
    z cache i w tle odświeża konta starsze niż `METAQUOTES_WEB_POLL_SEC`.

    Konsekwencja: breach wykrywasz z opóźnieniem rzędu jednego cyklu odświeżenia,
    a nie natychmiast. Przy dziennym limicie straty 5% to realne ryzyko, że trader
    zdąży zejść głębiej, zanim zareagujesz. Do produkcji z setką kont właściwym
    źródłem jest MetaApi (płatne); ten kanał jest dla startu i małej skali.
    """

    def __init__(self) -> None:
        from .metaquotes_web import make_opener

        s = get_settings()
        self._opener = make_opener(s) or _raise_no_opener()
        self._ttl = s.metaquotes_web_poll_sec
        self._cache: dict[str, tuple[float, MarketSnapshot]] = {}
        self._refreshing: set[str] = set()
        self._sem = asyncio.Semaphore(max(1, s.metaquotes_web_concurrency))

    async def snapshot(self, login: str, metaapi_account_id: str | None, initial_balance: float,
                       *, password: str | None = None, server: str | None = None) -> MarketSnapshot | None:
        cached = self._cache.get(login)
        fresh = cached is not None and (monotonic() - cached[0]) < self._ttl

        if not fresh and password and login not in self._refreshing:
            self._refreshing.add(login)
            asyncio.create_task(self._refresh(login, password))

        return cached[1] if cached else None

    async def _refresh(self, login: str, password: str) -> None:
        try:
            async with self._sem:
                state = await self._opener.read_state(login, password)
            self._cache[login] = (monotonic(), MarketSnapshot(
                balance=round(state.balance, 2),
                equity=round(state.equity, 2),
                open_pnl=round(state.equity - state.balance, 2),
                has_open_position=state.has_open_position,
                volume_lots=state.volume_lots,
                volume_known=state.volume_known,
            ))
            # Widoczność w logu: bez tego nie da się odróżnić „czyta z MT5”
            # od „oddaje w kółko stary cache”.
            print(f"[MetaQuotesWebFeed] {login}: balance={state.balance:.2f} "
                  f"equity={state.equity:.2f} "
                  f"loty={state.volume_lots if state.volume_known else '?'}")
        except Exception as e:
            print(f"[MetaQuotesWebFeed] odczyt konta {login} nieudany: {e}")
        finally:
            self._refreshing.discard(login)

    async def close_all_positions(self, metaapi_account_id: str, *,
                                  login: str | None = None, password: str | None = None) -> int:
        if not (login and password):
            print("[MetaQuotesWebFeed] brak poświadczeń — nie mogę zamknąć pozycji")
            return 0
        async with self._sem:
            return await self._opener.close_all_positions(login, password)

    async def lock(self, metaapi_account_id: str, *,
                   login: str | None = None, password: str | None = None) -> None:
        """Na MetaQuotes-Demo nie mamy uprawnień serwerowych — konto zostaje
        oznaczone jako `failed` po naszej stronie i przestaje się liczyć.
        Pozycje są zamknięte osobno przez `close_all_positions`."""
        self._cache.pop(login or "", None)


# --------------------------------------------------------------------------- #
#  METAAPI REST FEED — realne konto MT5 bez SDK i bez strumienia               #
# --------------------------------------------------------------------------- #
class MetaApiRestFeed(Feed):
    """To samo zrodlo co `MetaApiFeed`, ale samymi zapytaniami HTTP.

    `MetaApiFeed` trzyma strumieniowe polaczenie na konto: pierwszy odczyt to
    deploy, `wait_connected` i `wait_synchronized`, czyli kilkanascie sekund,
    a zysk z tego ma dopiero proces, ktory zyje godzinami. Produkcja stoi na
    hostingu bezserwerowym, gdzie funkcja zyje sekunde — tam strumien jest
    czystym kosztem, a SDK do tego siedzi zakomentowane w requirements.txt.

    Tutaj kazdy odczyt to jedno GET-owe zapytanie, bezstanowo. Ten sam styl
    co `metaapi_provisioning` (wstrzykiwalny transport, zero SDK), wiec testy
    nie dotykaja sieci.
    """

    #: Adres API klienta jest regionalny; provisioning ma jeden globalny.
    CLIENT_URL = "https://mt-client-api-v1.{region}.agiliumtrade.ai"
    PROVISIONING_URL = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"

    def __init__(self, token: str | None = None, *, region: str | None = None,
                 transport=None, timeout_sec: float = 20.0) -> None:
        s = get_settings()
        self._token = token or s.metaapi_token
        if not self._token:
            raise RuntimeError("MetaApiRestFeed wymaga METAAPI_TOKEN")
        self._region = region or getattr(s, "metaapi_region", None) or "new-york"
        self._transport = transport or self._httpx_transport
        self._timeout = timeout_sec

    # ---------------------------------------------------------------- HTTP --
    async def _httpx_transport(self, method: str, url: str, *, headers: dict, json: dict | None):
        import httpx  # lazy — testy nie potrzebuja sieci

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(method, url, headers=headers, json=json)
        try:
            body = resp.json()
        except Exception:
            body = None
        return resp.status_code, body

    async def _zapytaj(self, method: str, url: str, json: dict | None = None):
        status, body = await self._transport(
            method, url, headers={"auth-token": self._token}, json=json)
        if status >= 400:
            raise RuntimeError(f"MetaApi {method} {url} -> {status}: {body}")
        return body

    def _klient(self, metaapi_account_id: str, sciezka: str) -> str:
        baza = self.CLIENT_URL.format(region=self._region)
        return f"{baza}/users/current/accounts/{metaapi_account_id}{sciezka}"

    # ------------------------------------------------------------- odczyt --
    async def snapshot(self, login: str, metaapi_account_id: str | None, initial_balance: float,
                       *, password: str | None = None, server: str | None = None) -> MarketSnapshot | None:
        if not metaapi_account_id:
            return None
        try:
            info = await self._zapytaj("GET", self._klient(metaapi_account_id, "/account-information")) or {}
        except Exception as e:
            # Bez equity nie ma czego oceniac — konto zostaje na ostatnim stanie.
            # `process_account` konczy na None, wiec silnik NIE zobaczy zera.
            print(f"[MetaApiRestFeed] {metaapi_account_id}: brak account-information ({e})", flush=True)
            return None

        balance = float(info.get("balance", initial_balance))
        equity = float(info.get("equity", balance))

        # Pozycje sa drugim zapytaniem i moga paść osobno. Wtedy NIE udajemy,
        # ze wiemy: `volume_known=False` mowi silnikowi, zeby nie egzekwowal
        # limitu lotow (rules.py robi to swiadomie — patrz komentarz tam).
        volume_lots, volume_known, otwarte = 0.0, False, None
        try:
            pozycje = await self._zapytaj("GET", self._klient(metaapi_account_id, "/positions")) or []
            volume_lots = round(sum(float(_g(p, "volume") or 0.0) for p in pozycje), 2)
            volume_known = True
            otwarte = len(pozycje) > 0
        except Exception as e:
            print(f"[MetaApiRestFeed] {metaapi_account_id}: pozycje nieczytelne ({e})", flush=True)

        # Gdy pozycji nie udalo sie odczytac, o otwartej pozycji wnioskujemy
        # z niezrealizowanego wyniku. To przyblizenie: pozycja dokladnie na
        # zero zostanie przeoczona i ten dzien moze nie policzyc sie jako dzien
        # handlowy. Wybor swiadomy — pomylka w te strone opoznia zaliczenie
        # fazy, a nie zabiera konta.
        if otwarte is None:
            otwarte = abs(equity - balance) > 0.005

        return MarketSnapshot(
            balance=round(balance, 2),
            equity=round(equity, 2),
            open_pnl=round(equity - balance, 2),
            has_open_position=bool(otwarte),
            volume_lots=volume_lots,
            volume_known=volume_known,
        )

    # -------------------------------------------------------- egzekwowanie --
    async def close_all_positions(self, metaapi_account_id: str, *,
                                  login: str | None = None, password: str | None = None) -> int:
        """Zamyka kazda otwarta pozycje z osobna (POSITION_CLOSE_ID).

        MetaApi nie ma akcji „zamknij wszystko": `POSITIONS_CLOSE_SYMBOL`
        dotyczy jednego instrumentu, wiec i tak trzeba przejsc po liscie.
        Bledy pojedynczej pozycji nie przerywaja petli — po breachu liczy sie,
        zeby zamknac ich jak najwiecej, a nie zeby przerwac na pierwszej.
        """
        try:
            pozycje = await self._zapytaj("GET", self._klient(metaapi_account_id, "/positions")) or []
        except Exception as e:
            print(f"[MetaApiRestFeed] {metaapi_account_id}: nie udalo sie pobrac pozycji do zamkniecia ({e})", flush=True)
            return 0
        zamkniete = 0
        for poz in pozycje:
            pid = _g(poz, "id")
            try:
                await self._zapytaj("POST", self._klient(metaapi_account_id, "/trade"),
                                    {"actionType": "POSITION_CLOSE_ID", "positionId": str(pid)})
                zamkniete += 1
            except Exception as e:
                print(f"[MetaApiRestFeed] {metaapi_account_id}: pozycja {pid} nie zamknieta ({e})", flush=True)
        return zamkniete

    async def lock(self, metaapi_account_id: str, *,
                   login: str | None = None, password: str | None = None) -> None:
        """Undeploy: MetaApi przestaje obslugiwac konto, wiec nasza droga do
        rachunku sie zamyka. UWAGA: to nie odbiera traderowi hasla do MT5 —
        pelne odciecie loginu wymaga MT5 Manager API po stronie brokera."""
        url = f"{self.PROVISIONING_URL}/users/current/accounts/{metaapi_account_id}/undeploy"
        try:
            await self._zapytaj("POST", url, {})
        except Exception as e:
            print(f"[MetaApiRestFeed] {metaapi_account_id}: undeploy nieudany ({e})", flush=True)


# --------------------------------------------------------------------------- #
#  ROUTER — realne konta jednym kanalem, reszta drugim                        #
# --------------------------------------------------------------------------- #
class RoutedFeed(Feed):
    """Konto z `metaapi_account_id` czyta MetaApi, kazde inne — kanal domyslny.

    Add-on Copytrading daje realny rachunek MT5 GARSTCE kont; reszta produkcji
    stoi na poswiadczeniach lokalnych i ma sie zachowywac dokladnie tak, jak
    dotad. Globalne `FEED=metaapi` przelaczyloby wszystkie naraz, wiec wybor
    zapada per konto — a jedyna przeslanka, jakiej potrzeba, i tak jest w
    kazdej metodzie interfejsu.
    """

    def __init__(self, base: Feed, real: Feed) -> None:
        self._base = base
        self._real = real

    def _kanal(self, metaapi_account_id: str | None) -> Feed:
        return self._real if metaapi_account_id else self._base

    async def snapshot(self, login: str, metaapi_account_id: str | None, initial_balance: float,
                       *, password: str | None = None, server: str | None = None) -> MarketSnapshot | None:
        return await self._kanal(metaapi_account_id).snapshot(
            login, metaapi_account_id, initial_balance, password=password, server=server)

    async def provision(self, spec: dict) -> dict | None:
        # Zakladanie kont idzie `provisioning.py` (REST), nie feedem — ta droga
        # zostaje wylacznie dla konfiguracji, ktora ma tylko kanal SDK.
        return await self._base.provision(spec)

    async def close_all_positions(self, metaapi_account_id: str, *,
                                  login: str | None = None, password: str | None = None) -> int:
        return await self._kanal(metaapi_account_id).close_all_positions(
            metaapi_account_id, login=login, password=password)

    async def lock(self, metaapi_account_id: str, *,
                   login: str | None = None, password: str | None = None) -> None:
        return await self._kanal(metaapi_account_id).lock(
            metaapi_account_id, login=login, password=password)

    async def close(self) -> None:
        for kanal in (self._real, self._base):
            try:
                await kanal.close()
            except Exception:
                pass


def _raise_no_opener():
    raise RuntimeError(
        "FEED=metaquotes_web wymaga METAQUOTES_WEB_ENABLED=true (patrz .env.example)"
    )


def make_feed() -> Feed:
    """Kanal domyslny z `FEED`, opakowany routerem, gdy mamy token MetaApi.

    `FEED` zostaje tym, czym byl: wyborem dla CALEJ reszty kont. Router dokłada
    tylko obsluge tych nielicznych, ktore maja realny rachunek MT5 (add-on
    Copytrading) — bez niego trzeba byloby przelaczyc `FEED=metaapi` globalnie
    i wszystkie 65 istniejacych kont zaczeloby sie dobijac do brokera loginem,
    ktorego tam nie ma.

    `FEED=metaapi` (SDK, proces dlugo zyjacy) nie jest opakowywany: tam kanal
    realny juz jest kanalem domyslnym.
    """
    s = get_settings()
    if s.feed == "metaapi":
        return MetaApiFeed()
    if s.feed == "metaquotes_web":
        bazowy: Feed = MetaQuotesWebFeed()
    elif s.feed == "sim":
        bazowy = SimulatedFeed()
    else:
        bazowy = NullFeed()
    if s.metaapi_token:
        try:
            return RoutedFeed(bazowy, MetaApiRestFeed())
        except Exception as e:  # pragma: no cover - zla konfiguracja
            print(f"[feed] MetaApi REST niedostepny ({e}) — zostaje sam {type(bazowy).__name__}", flush=True)
    return bazowy
