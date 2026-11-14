"""Feed FX z cTrader Open API, uruchamiany wewnątrz crona świec (bez VPS-a).

Hyperliquid kwotuje EUR/GBP/JPY z czterema miejscami i rusza mid kilka razy na minutę,
więc świece terminala stały i skakały. cTrader Open API (darmowe: aplikacja na
openapi.ctrader.com + konto dowolnego brokera z cTraderem) daje prawdziwe ticki
brokera z pięcioma miejscami. Cron Vercela żyje ~55 s na minutę: przez ten czas słuchamy
ticków przez WebSocket (protokół JSON, port 5036), składamy z mid świece 1m i co
ZAPIS_CO_S zapisujemy je do `terminal_candles` jako wiersze ostateczne (n = FEED_N),
więc przeglądarka, która dociąga ostatnie świece co kilka sekund, widzi świecę na żywo.
Luki (przerwa crona, zimny start) łatamy trendbarami M1 z tego samego źródła.

Ten sam bid/ask idzie do `broker_quotes`: wycena i wypełnienia tych rynków.

Które konto: token aplikacji widzi wszystkie konta cTID, który ją autoryzował. Bierzemy
konto wskazane w env (CTRADER_LOGIN = numer konta albo CTRADER_BROKER = fragment nazwy
brokera), a gdy go nie ma na tokenie albo broker je wyłączy — pierwsze konto demo, żeby
feed nie stanął; listę sprawdzamy ponownie co PONOW_S. Host (demo/live) wynika z konta.

Konfiguracja (Vercel env): CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN,
CTRADER_REFRESH_TOKEN, CTRADER_BROKER / CTRADER_LOGIN; CTRADER_ACCOUNT_ID + CTRADER_HOST
(demo|live) wymuszają konto na sztywno. Odświeżony token trzymamy w AppSetting (env
Vercela jest tylko do odczytu) ze skrótem tokenu z env, z którego powstał: nowy token
w env wygrywa. Sekretów nie wypisujemy do logów — httpx loguje URL, więc odświeżanie
idzie z wyciszonym loggerem.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from . import broker_quotes
from .config import get_settings
from .db import SessionLocal
from .models import AppSetting, TerminalCandle

# Rynki terminala (moneta Hyperliquida) → nazwy symboli, których szukamy u brokera.
# Nazwy porównujemy bez "/", ".", spacji i podkreśleń; pierwsza znaleziona u brokera wygrywa.
SYMBOLE = {"xyz:EUR": ("EURUSD",), "xyz:GBP": ("GBPUSD",), "xyz:JPY": ("USDJPY",),
           "xyz:PLATINUM": ("XPTUSD", "PLATINUM"),
           "xyz:SILVER": ("XAGUSD", "SILVER"),
           "xyz:JP225": ("JP225", "JPN225", "JAPAN225", "NIKKEI225", "NIK225", "JP225CASH")}
SKALA = 100_000                 # ceny w Open API są w 1/100000 jednostki
FEED_N = -1                     # ten sam znacznik co terminal.FEED_N: wiersz ostateczny
ZAPIS_CO_S = 2.0               # przeglądarka dociąga co 2 s, CDN trzyma odpowiedź 1 s
LUKA_MIN = 2                    # od tylu brakujących minut łatamy trendbarami
TOKEN_KLUCZ = "ctrader_token"   # AppSetting: {"access": ..., "refresh": ..., "exp": ...}
KONTO_KLUCZ = "ctrader_account"  # AppSetting: {"id", "live", "broker", "pref", "at", "match"}
SYMBOLE_KLUCZ = "ctrader_symbols"
ODRZUT_KLUCZ = "ctrader_rejected"  # AppSetting: {ctidTraderAccountId: unix s odrzucenia}
PONOW_S = 600.0                 # na koncie zapasowym: co tyle pytamy, czy wskazane już jest
ODRZUT_S = 3600.0               # konto odrzucone przy autoryzacji omijamy przez godzinę
PROB = 4                        # połączeń na przebieg: zmiana hosta, odświeżenie tokenu, inne konto
TOKENOWE = ("OA_AUTH_TOKEN_EXPIRED", "CH_ACCESS_TOKEN_INVALID")
# Autoryzacja konta odrzucona, choć aplikacja i token są w porządku: konta nie ma, broker
# je wyłączył albo token go nie obejmuje. Wtedy bierzemy następne konto z tokenu.
KONTO_ZNIKLO = ("RET_NO_SUCH_LOGIN", "RET_ACCOUNT_DISABLED", "CH_CTID_TRADER_ACCOUNT_NOT_FOUND",
                "ACCOUNT_NOT_AUTHORIZED", "CHANNEL_IS_BLOCKED", "CANT_ROUTE_REQUEST")

PT = {"APP_AUTH_REQ": 2100, "APP_AUTH_RES": 2101, "ACC_AUTH_REQ": 2102, "ACC_AUTH_RES": 2103,
      "SYMBOLS_REQ": 2114, "SYMBOLS_RES": 2115, "SUB_SPOTS_REQ": 2127, "SUB_SPOTS_RES": 2128,
      "SPOT": 2131, "TRENDBARS_REQ": 2137, "TRENDBARS_RES": 2138, "ERROR": 2142,
      "ACCOUNTS_REQ": 2149, "ACCOUNTS_RES": 2150, "HEARTBEAT": 51}
M1 = 1                          # ProtoOATrendbarPeriod.M1


def skonfigurowany() -> bool:
    s = get_settings()
    return bool(s.ctrader_client_id and s.ctrader_client_secret and (s.ctrader_access_token or _token_z_bazy()))


def _ustawienie(klucz: str):
    session = SessionLocal()
    try:
        w = session.get(AppSetting, klucz)
        return json.loads(w.value) if w and w.value else None
    except (ValueError, TypeError):
        return None
    finally:
        session.close()


def _zapisz_ustawienie(klucz: str, wartosc) -> None:
    session = SessionLocal()
    try:
        w = session.get(AppSetting, klucz)
        if w is None:
            session.add(AppSetting(key=klucz, value=json.dumps(wartosc)))
        else:
            w.value = json.dumps(wartosc)
        session.commit()
    finally:
        session.close()


def _odcisk(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()[:12]


def _token_z_bazy() -> dict | None:
    """Odświeżony token z bazy. Wpis pamięta skrót tokenu z env, z którego powstał (`src`):
    gdy w env jest już inny token, wpis jest nieaktualny i wygrywa env."""
    t = _ustawienie(TOKEN_KLUCZ)
    if not isinstance(t, dict):
        return None
    env = get_settings().ctrader_access_token
    if t.get("src") and env and t["src"] != _odcisk(env):
        return None
    return t


def _tokeny() -> tuple[str, str]:
    """(access, refresh): odświeżony w bazie ma pierwszeństwo przed env."""
    s = get_settings()
    t = _token_z_bazy() or {}
    return t.get("access") or s.ctrader_access_token, t.get("refresh") or s.ctrader_refresh_token


def odswiez_token(transport=None) -> bool:
    """Wymienia refresh token na nowy access token i zapisuje oba w bazie."""
    s = get_settings()
    _, refresh = _tokeny()
    if not refresh:
        return False
    params = {"grant_type": "refresh_token", "refresh_token": refresh,
              "client_id": s.ctrader_client_id, "client_secret": s.ctrader_client_secret}
    log = logging.getLogger("httpx")
    poziom = log.level
    log.setLevel(logging.WARNING)          # URL niesie sekrety — nie do logów
    try:
        if transport:
            status, body = transport(params)
        else:
            import httpx
            r = httpx.post("https://openapi.ctrader.com/apps/token", data=params, timeout=15.0,
                           headers={"Accept": "application/json"})
            status = r.status_code
            try:
                body = r.json()
            except ValueError:
                body = None
    except Exception as e:
        print(f"[ctrader] token refresh failed: {type(e).__name__}", flush=True)
        return False
    finally:
        log.setLevel(poziom)
    if status != 200 or not isinstance(body, dict) or not body.get("accessToken"):
        print(f"[ctrader] token refresh -> {status}: {str((body or {}).get('description') or body)[:120]}", flush=True)
        return False
    _zapisz_ustawienie(TOKEN_KLUCZ, {"access": body["accessToken"], "refresh": body.get("refreshToken") or refresh,
                                     "exp": int(time.time()) + int(body.get("expiresIn") or 0),
                                     "src": _odcisk(s.ctrader_access_token)})
    print("[ctrader] access token refreshed", flush=True)
    return True


class Polaczenie:
    """Cienka warstwa nad WebSocketem: wysyłka z clientMsgId i czekanie na odpowiedź,
    zdarzenia SPOT trafiają do `on_spot`."""

    def __init__(self, ws, on_spot=None):
        self.ws, self.on_spot, self._n = ws, on_spot, 0

    async def wyslij(self, typ: str, payload: dict) -> str:
        self._n += 1
        mid = f"ptf{self._n}"
        await self.ws.send(json.dumps({"clientMsgId": mid, "payloadType": PT[typ], "payload": payload}))
        return mid

    async def zapytaj(self, typ: str, payload: dict, odp: str, timeout: float = 10.0) -> dict:
        mid = await self.wyslij(typ, payload)
        koniec = time.monotonic() + timeout
        while True:
            zostalo = koniec - time.monotonic()
            if zostalo <= 0:
                raise TimeoutError(typ)
            m = json.loads(await asyncio.wait_for(self.ws.recv(), zostalo))
            t = m.get("payloadType")
            if t == PT["SPOT"] and self.on_spot:
                self.on_spot(m.get("payload") or {})
                continue
            if m.get("clientMsgId") != mid and t != PT["ERROR"]:
                continue
            if t == PT["ERROR"]:
                p = m.get("payload") or {}
                raise BladOA(p.get("errorCode") or "ERROR", p.get("description") or "")
            if t == PT[odp]:
                return m.get("payload") or {}


class BladOA(Exception):
    def __init__(self, kod: str, opis: str = ""):
        super().__init__(f"{kod}: {opis}")
        self.kod = kod
        self.konto = 0          # ustawione, gdy błąd dotyczy autoryzacji konkretnego konta


class InnyHost(Exception):
    """Wybrane konto żyje w drugim środowisku (demo/live): trzeba połączyć się z jego hostem."""

    def __init__(self, live: bool):
        super().__init__("live" if live else "demo")
        self.live = live


def _host(live: bool) -> str:
    return "live.ctraderapi.com" if live else "demo.ctraderapi.com"


def _host_z_env() -> bool:
    return (get_settings().ctrader_host or "demo").lower() == "live"


def _pref() -> str:
    """Podpis preferencji z env; zapamiętany wybór konta jest ważny tylko dla tego samego."""
    s = get_settings()
    return f"{(s.ctrader_broker or '').lower()}|{s.ctrader_login or ''}"


def _ranga(a: dict) -> int:
    """0 = numer konta z env, 1 = broker z env, 2 = konto demo, 3 = reszta."""
    s = get_settings()
    if s.ctrader_login and str(a.get("traderLogin") or "") == s.ctrader_login:
        return 0
    if s.ctrader_broker and s.ctrader_broker.lower() in str(a.get("brokerTitleShort") or "").lower():
        return 1
    return 3 if a.get("isLive") else 2


def _wybor() -> dict | None:
    """Zapamiętane konto, o ile powstało dla bieżącej preferencji. Konto zapasowe (wskazanego
    nie było na tokenie) jest ważne przez PONOW_S, potem pytamy o listę jeszcze raz."""
    w = _ustawienie(KONTO_KLUCZ)
    if not isinstance(w, dict) or not w.get("id") or w.get("pref") != _pref():
        return None
    if not w.get("match") and time.time() - float(w.get("at") or 0) > PONOW_S:
        return None
    return w


def _odrzucone() -> dict[int, float]:
    teraz = time.time()
    return {int(k): float(v) for k, v in (_ustawienie(ODRZUT_KLUCZ) or {}).items() if teraz - float(v) < ODRZUT_S}


def _odrzuc(konto: int, kod: str) -> None:
    """Broker nie wpuszcza na to konto: omijamy je przez ODRZUT_S i wybieramy od nowa."""
    lista = _odrzucone()
    lista[int(konto)] = time.time()
    _zapisz_ustawienie(ODRZUT_KLUCZ, {str(k): v for k, v in lista.items()})
    _zapisz_ustawienie(KONTO_KLUCZ, {})
    print(f"[ctrader] account …{int(konto) % 10000:04d} rejected ({kod}), falling back", flush=True)


def _live_na_start() -> bool:
    """Z którym hostem łączymy się najpierw: tym od zapamiętanego konta, inaczej z env."""
    if int(get_settings().ctrader_account_id or 0):
        return _host_z_env()
    w = _wybor()
    return bool(w.get("live")) if w else _host_z_env()


def _opis(a: dict) -> str:
    return (f"{a.get('brokerTitleShort') or '?'} {'live' if a.get('isLive') else 'demo'} "
            f"…{str(a.get('traderLogin') or a.get('ctidTraderAccountId') or '')[-4:]}")


async def _lista_kont(p: Polaczenie, access: str) -> list[dict]:
    r = await p.zapytaj("ACCOUNTS_REQ", {"accessToken": access}, "ACCOUNTS_RES")
    return list(r.get("ctidTraderAccount") or [])


async def _wybierz(p: Polaczenie) -> dict:
    """Konto do feedu: wymuszone w env, zapamiętane albo najlepsze z listy tokenu."""
    s = get_settings()
    jawne = int(s.ctrader_account_id or 0)
    if jawne:
        return {"id": jawne, "live": _host_z_env(), "broker": "", "match": True, "jawne": True}
    w = _wybor()
    if w:
        return w
    access, _ = _tokeny()
    lista = await _lista_kont(p, access)
    chce = bool(s.ctrader_broker or s.ctrader_login)
    if chce and not any(_ranga(a) < 2 for a in lista):
        # Wpis tokenu sprzed stempla `src` mógł przesłonić nowy token z env. Pytamy też nim;
        # zastępuje wpis dopiero, gdy broker odpowie i lista ma wskazane konto.
        zapis = _ustawienie(TOKEN_KLUCZ) or {}
        env = s.ctrader_access_token
        if env and env != access and not zapis.get("src"):
            try:
                lista_env = await _lista_kont(p, env)
            except (BladOA, TimeoutError):
                lista_env = []
            if any(_ranga(a) < 2 for a in lista_env):
                _zapisz_ustawienie(TOKEN_KLUCZ, {"access": env, "refresh": s.ctrader_refresh_token, "exp": 0,
                                                 "src": _odcisk(env)})
                lista = lista_env
    if not lista:
        raise BladOA("NO_ACCOUNT", "the access token has no trading account")
    print("[ctrader] accounts on token: " + ", ".join(_opis(a) for a in lista[:12]), flush=True)
    odrzucone = _odrzucone()
    kandydaci = [a for a in lista if int(a["ctidTraderAccountId"]) not in odrzucone] or lista
    a = min(kandydaci, key=_ranga)          # min jest stabilne: przy remisie kolejność brokera
    trafione = _ranga(a) < 2 or not chce
    if not trafione:
        print(f"[ctrader] preferred account not on this token, using {_opis(a)}", flush=True)
    w = {"id": int(a["ctidTraderAccountId"]), "live": bool(a.get("isLive")),
         "broker": str(a.get("brokerTitleShort") or ""), "pref": _pref(), "at": time.time(), "match": trafione}
    _zapisz_ustawienie(KONTO_KLUCZ, w)
    return w


async def _zaloguj(p: Polaczenie, live: bool) -> dict:
    """Autoryzacja aplikacji i konta; zwraca wybór konta (`id`, `live`, `broker`, `match`).
    Konto z drugiego środowiska → `InnyHost`, zanim poprosimy o jego autoryzację."""
    s = get_settings()
    await p.zapytaj("APP_AUTH_REQ", {"clientId": s.ctrader_client_id, "clientSecret": s.ctrader_client_secret}, "APP_AUTH_RES")
    w = await _wybierz(p)
    if bool(w.get("live")) != live:
        raise InnyHost(bool(w.get("live")))
    access, _ = _tokeny()
    try:
        await p.zapytaj("ACC_AUTH_REQ", {"ctidTraderAccountId": int(w["id"]), "accessToken": access}, "ACC_AUTH_RES")
    except BladOA as e:
        if not w.get("jawne"):
            e.konto = int(w["id"])
        raise
    return w


def _norm(nazwa: str) -> str:
    return "".join(ch for ch in nazwa.upper() if ch not in "/. _-")


async def _symbole(p: Polaczenie, konto: int) -> dict[int, str]:
    """symbolId → moneta terminala; lista brokera w pamięci bazy, żeby nie pytać co minutę."""
    zapis = _ustawienie(SYMBOLE_KLUCZ) or {}
    # Podpis listy: nowy rynek w SYMBOLE przebudowuje mapę zapisaną przez starszą wersję.
    podpis = json.dumps(SYMBOLE, sort_keys=True)
    if zapis.get("konto") == konto and zapis.get("mapa") and zapis.get("podpis") == podpis:
        return {int(k): v for k, v in zapis["mapa"].items()}
    r = await p.zapytaj("SYMBOLS_REQ", {"ctidTraderAccountId": konto}, "SYMBOLS_RES", timeout=20.0)
    po_nazwie = {}
    for x in r.get("symbol") or []:
        if x.get("enabled", True):
            po_nazwie.setdefault(_norm(x.get("symbolName") or ""), int(x["symbolId"]))
    mapa = {}
    for coin, nazwy in SYMBOLE.items():
        for n in nazwy:
            if _norm(n) in po_nazwie:
                mapa[po_nazwie[_norm(n)]] = coin
                break
        else:
            # Podobne nazwy u brokera, żeby alias dało się dopisać do SYMBOLE bez zgadywania.
            poczatki = {_norm(n)[:3] for n in nazwy}
            podobne = sorted(k for k in po_nazwie if k[:3] in poczatki)[:5]
            print(f"[ctrader] brak symbolu u brokera dla {coin}" + (f" (similar: {', '.join(podobne)})" if podobne else ""),
                  flush=True)
    _zapisz_ustawienie(SYMBOLE_KLUCZ, {"konto": konto, "podpis": podpis, "mapa": {str(k): v for k, v in mapa.items()}})
    return mapa


def _minuta(ts_s: float) -> datetime:
    return datetime.fromtimestamp(int(ts_s) // 60 * 60, tz=timezone.utc).replace(tzinfo=None)


class Swiece:
    """Świece 1m z mid (bid+ask)/2. Spot event niesie czasem tylko zmienioną stronę —
    pamiętamy ostatni bid i ask każdego symbolu."""

    def __init__(self):
        self.ba: dict[str, list[float | None]] = {}
        self.sw: dict[tuple[str, datetime], list[float]] = {}
        self.ticki = 0

    def tick(self, coin: str, bid: float | None, ask: float | None, ts_s: float) -> None:
        ba = self.ba.setdefault(coin, [None, None])
        if bid:
            ba[0] = bid
        if ask:
            ba[1] = ask
        if not (ba[0] and ba[1]):
            return
        px = round((ba[0] + ba[1]) / 2, 6)
        k = (coin, _minuta(ts_s))
        s = self.sw.get(k)
        if s is None:
            self.sw[k] = [px, px, px, px]
        else:
            s[1], s[2], s[3] = max(s[1], px), min(s[2], px), px
        self.ticki += 1

    def kwoty(self) -> dict[str, tuple[float, float]]:
        """Ostatni pełny bid/ask każdego symbolu (do wypełnień i wyceny, `broker_quotes`)."""
        return {c: (b, a) for c, (b, a) in self.ba.items() if b and a}

    def zrzut(self) -> dict[tuple[str, datetime], list[float]]:
        out, self.sw = self.sw, {k: list(v) for k, v in self.sw.items() if k[1] >= _minuta(time.time())}
        return out


def zapisz(swiece: dict[tuple[str, datetime], list[float]]) -> int:
    """Scala świece z tickami z wierszami w bazie: otwarcie z pierwszego zapisu tej minuty
    przez feed, H/L rozszerzają się, zamknięcie idzie dalej. Wiersz staje się ostateczny
    (n = FEED_N), więc próbki mid Hyperliquida już go nie ruszą."""
    if not swiece:
        return 0
    session = SessionLocal()
    try:
        n = 0
        for (coin, ts), (o, h, l, c) in sorted(swiece.items(), key=lambda x: (x[0][0], x[0][1])):
            row = session.query(TerminalCandle).filter(TerminalCandle.coin == coin, TerminalCandle.ts == ts).first()
            if row is None:
                session.add(TerminalCandle(coin=coin, ts=ts, o=o, h=h, l=l, c=c, n=FEED_N))
            elif (row.n or 0) == FEED_N:
                row.h, row.l, row.c = max(row.h, h), min(row.l, l), c
            else:
                row.o, row.h, row.l, row.c, row.n = o, h, l, c, FEED_N     # próbki mid ustępują tickom
            n += 1
        session.commit()
        return n
    finally:
        session.close()


def _luki(coin: str, teraz: datetime) -> list[tuple[datetime, datetime]]:
    """Okna [od, do] bez wiersza feedu z ostatniej doby (poza weekendem) — do załatania trendbarami."""
    session = SessionLocal()
    try:
        od = teraz - timedelta(days=1)
        ts = [r[0] for r in session.query(TerminalCandle.ts)
              .filter(TerminalCandle.coin == coin, TerminalCandle.ts >= od, TerminalCandle.n == FEED_N)
              .order_by(TerminalCandle.ts).all()]
    finally:
        session.close()
    punkty = [od - timedelta(minutes=1)] + ts + [teraz]
    out = []
    for a, b in zip(punkty, punkty[1:]):
        if (b - a).total_seconds() // 60 - 1 >= LUKA_MIN:
            out.append((a + timedelta(minutes=1), b - timedelta(minutes=1)))
    return out[-6:]


async def _latanie(p: Polaczenie, konto: int, mapa: dict[int, str], teraz: datetime) -> int:
    """Trendbary M1 dla luk z ostatniej doby; wiersze z feedu (n=FEED_N) zastępują próbki."""
    wiersze = {}
    for sid, coin in mapa.items():
        for od, do in _luki(coin, teraz):
            r = await p.zapytaj("TRENDBARS_REQ", {"ctidTraderAccountId": konto, "symbolId": sid, "period": M1,
                                                   "fromTimestamp": int(od.replace(tzinfo=timezone.utc).timestamp() * 1000),
                                                   "toTimestamp": int((do + timedelta(minutes=1)).replace(tzinfo=timezone.utc).timestamp() * 1000)},
                                "TRENDBARS_RES", timeout=15.0)
            for b in r.get("trendbar") or []:
                low = int(b.get("low") or 0)
                if not low:
                    continue
                ts = datetime.fromtimestamp(int(b["utcTimestampInMinutes"]) * 60, tz=timezone.utc).replace(tzinfo=None)
                if ts >= _minuta(time.time()):
                    continue                     # bieżąca minuta idzie z ticków
                wiersze[(coin, ts)] = [(low + int(b.get("deltaOpen") or 0)) / SKALA, (low + int(b.get("deltaHigh") or 0)) / SKALA,
                                       low / SKALA, (low + int(b.get("deltaClose") or 0)) / SKALA]
    return _nadpisz(wiersze)


def _nadpisz(wiersze: dict) -> int:
    """Trendbary są pełne: nadpisują próbki i stare wiersze feedu tej samej minuty."""
    if not wiersze:
        return 0
    session = SessionLocal()
    try:
        for (coin, ts), (o, h, l, c) in wiersze.items():
            row = session.query(TerminalCandle).filter(TerminalCandle.coin == coin, TerminalCandle.ts == ts).first()
            if row is None:
                session.add(TerminalCandle(coin=coin, ts=ts, o=o, h=h, l=l, c=c, n=FEED_N))
            else:
                row.o, row.h, row.l, row.c, row.n = o, h, l, c, FEED_N
        session.commit()
        return len(wiersze)
    finally:
        session.close()


async def sluchaj(budzet_s: float, polacz=None) -> dict:
    """Jeden przebieg: logowanie, łatanie luk, subskrypcja ticków do końca budżetu, zapis
    co ZAPIS_CO_S. `polacz(live)` = fabryka WebSocketu dla hosta live/demo (testy podają
    atrapę). Do PROB połączeń: konto z drugiego środowiska, odświeżenie tokenu albo konto
    odrzucone przez brokera kończą połączenie i zaczynają następne."""
    if not skonfigurowany():
        return {"skipped": True, "reason": "CTRADER_* not set"}
    start = time.monotonic()
    swiece = Swiece()
    wynik = {"ticks": 0, "rows": 0, "gap_rows": 0}
    if polacz is None:
        import websockets
        polacz = lambda live: websockets.connect(f"wss://{_host(live)}:5036", open_timeout=10,  # noqa: E731
                                                 ping_interval=None, max_size=8 * 1024 * 1024)
    s = get_settings()
    live = _live_na_start()
    odswiezono = False
    for _ in range(PROB):
        try:
            async with polacz(live) as ws:
                mapa: dict[int, str] = {}

                def on_spot(pl):
                    coin = mapa.get(int(pl.get("symbolId") or 0))
                    if coin:
                        bid, ask = pl.get("bid"), pl.get("ask")
                        swiece.tick(coin, int(bid) / SKALA if bid else None, int(ask) / SKALA if ask else None,
                                    (int(pl["timestamp"]) / 1000) if pl.get("timestamp") else time.time())

                p = Polaczenie(ws, on_spot)
                wybor = await _zaloguj(p, live)
                konto = int(wybor["id"])
                wynik["account"] = {"broker": wybor.get("broker") or "", "live": live}
                if (s.ctrader_broker or s.ctrader_login) and not wybor.get("match"):
                    wynik["fallback"] = True
                else:
                    wynik.pop("fallback", None)
                mapa.update(await _symbole(p, konto))
                if not mapa:
                    return {"skipped": True, "reason": "none of the FX symbols found at the broker"}
                try:
                    wynik["gap_rows"] = await _latanie(p, konto, mapa, _minuta(time.time()))
                except (BladOA, TimeoutError) as e:
                    print(f"[ctrader] trendbars: {e}", flush=True)
                await p.zapytaj("SUB_SPOTS_REQ", {"ctidTraderAccountId": konto, "symbolId": list(mapa)}, "SUB_SPOTS_RES")
                ostatni_zapis = time.monotonic()
                ostatni_hb = time.monotonic()
                while True:
                    zostalo = budzet_s - (time.monotonic() - start)
                    if zostalo <= 0:
                        break
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), min(zostalo, 2.0)))
                        if m.get("payloadType") == PT["SPOT"]:
                            on_spot(m.get("payload") or {})
                    except asyncio.TimeoutError:
                        pass
                    if time.monotonic() - ostatni_hb >= 10:
                        await ws.send(json.dumps({"payloadType": PT["HEARTBEAT"], "payload": {}}))
                        ostatni_hb = time.monotonic()
                    if time.monotonic() - ostatni_zapis >= ZAPIS_CO_S:
                        wynik["rows"] += await asyncio.to_thread(zapisz, swiece.zrzut())
                        await asyncio.to_thread(broker_quotes.zapisz, swiece.kwoty())
                        ostatni_zapis = time.monotonic()
                wynik["rows"] += await asyncio.to_thread(zapisz, swiece.zrzut())
                await asyncio.to_thread(broker_quotes.zapisz, swiece.kwoty())
                wynik["ticks"] = swiece.ticki
                wynik["symbols"] = sorted(mapa.values())
                return wynik
        except InnyHost as e:
            live = e.live
            continue
        except BladOA as e:
            # Token: odświeżamy raz. ACCOUNT_NOT_AUTHORIZED bywa i tokenem, i kontem —
            # najpierw odświeżenie, a gdy nie pomoże, konto idzie na listę odrzuconych.
            if not odswiezono and (e.kod in TOKENOWE or e.kod == "ACCOUNT_NOT_AUTHORIZED") and odswiez_token():
                odswiezono = True
                continue
            if e.konto and e.kod in KONTO_ZNIKLO:
                _odrzuc(e.konto, e.kod)
                live = _live_na_start()
                continue
            print(f"[ctrader] {e}", flush=True)
            wynik["error"] = e.kod
            break
        except Exception as e:  # sieć, timeout — próbki mid Hyperliquida zostają
            print(f"[ctrader] {type(e).__name__}: {str(e)[:160]}", flush=True)
            wynik["error"] = type(e).__name__
            break
    else:
        print("[ctrader] no usable account after retries", flush=True)
        wynik["error"] = "RETRIES"
    wynik["rows"] += zapisz(swiece.zrzut())
    wynik["ticks"] = swiece.ticki
    return wynik
