"""Źródła danych equity/balance dla kont.

Dwie implementacje za wspólnym interfejsem:
  - SimulatedFeed : losowy spacer equity, działa BEZ MT5/MetaApi (tryb 0 zł, demo).
  - MetaApiFeed   : realne konto MT5 przez MetaApi cloud (FEED=metaapi + token).

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


def _raise_no_opener():
    raise RuntimeError(
        "FEED=metaquotes_web wymaga METAQUOTES_WEB_ENABLED=true (patrz .env.example)"
    )


def make_feed() -> Feed:
    s = get_settings()
    if s.feed == "metaapi":
        return MetaApiFeed()
    if s.feed == "metaquotes_web":
        return MetaQuotesWebFeed()
    if s.feed == "sim":
        return SimulatedFeed()
    return NullFeed()
