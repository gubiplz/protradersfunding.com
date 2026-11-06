"""Feed FX z cTrader Open API, uruchamiany wewnątrz crona świec (bez VPS-a).

Hyperliquid kwotuje EUR/GBP/JPY z czterema miejscami i rusza mid kilka razy na minutę,
więc świece terminala stały i skakały. cTrader Open API (darmowe: aplikacja na
openapi.ctrader.com + konto demo dowolnego brokera z cTraderem) daje prawdziwe ticki
brokera z pięcioma miejscami. Cron Vercela żyje ~55 s na minutę: przez ten czas słuchamy
ticków przez WebSocket (protokół JSON, port 5036), składamy z mid świece 1m i co
ZAPIS_CO_S zapisujemy je do `terminal_candles` jako wiersze ostateczne (n = FEED_N),
więc przeglądarka, która dociąga ostatnie świece co kilka sekund, widzi świecę na żywo.
Luki (przerwa crona, zimny start) łatamy trendbarami M1 z tego samego źródła.

Wypełnienia zleceń dalej idą po cenach Hyperliquida — wykres to cena referencyjna spotu.

Konfiguracja (Vercel env): CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN,
CTRADER_REFRESH_TOKEN, opcjonalnie CTRADER_ACCOUNT_ID i CTRADER_HOST (demo|live).
Odświeżony token trzymamy w AppSetting (env Vercela jest tylko do odczytu); sekretów
nie wypisujemy do logów — httpx loguje URL, więc odświeżanie idzie z wyciszonym loggerem.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .db import SessionLocal
from .models import AppSetting, TerminalCandle

# Rynki terminala (moneta Hyperliquida) → nazwy symboli, których szukamy u brokera.
SYMBOLE = {"xyz:EUR": ("EURUSD",), "xyz:GBP": ("GBPUSD",), "xyz:JPY": ("USDJPY",),
           "xyz:PLATINUM": ("XPTUSD", "XPTUSD.", "PLATINUM")}
SKALA = 100_000                 # ceny w Open API są w 1/100000 jednostki
FEED_N = -1                     # ten sam znacznik co terminal.FEED_N: wiersz ostateczny
ZAPIS_CO_S = 8.0
LUKA_MIN = 2                    # od tylu brakujących minut łatamy trendbarami
TOKEN_KLUCZ = "ctrader_token"   # AppSetting: {"access": ..., "refresh": ..., "exp": ...}
KONTO_KLUCZ = "ctrader_account"
SYMBOLE_KLUCZ = "ctrader_symbols"

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


def _token_z_bazy() -> dict | None:
    return _ustawienie(TOKEN_KLUCZ)


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
                                     "exp": int(time.time()) + int(body.get("expiresIn") or 0)})
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


def _host() -> str:
    return "live.ctraderapi.com" if (get_settings().ctrader_host or "demo").lower() == "live" else "demo.ctraderapi.com"


async def _zaloguj(p: Polaczenie) -> int:
    """Autoryzacja aplikacji i konta; zwraca ctidTraderAccountId."""
    s = get_settings()
    await p.zapytaj("APP_AUTH_REQ", {"clientId": s.ctrader_client_id, "clientSecret": s.ctrader_client_secret}, "APP_AUTH_RES")
    access, _ = _tokeny()
    konto = int(s.ctrader_account_id or 0) or int((_ustawienie(KONTO_KLUCZ) or {}).get("id") or 0)
    if not konto:
        r = await p.zapytaj("ACCOUNTS_REQ", {"accessToken": access}, "ACCOUNTS_RES")
        lista = r.get("ctidTraderAccount") or []
        wybrane = [a for a in lista if not a.get("isLive")] or lista
        if not wybrane:
            raise BladOA("NO_ACCOUNT", "the access token has no trading account")
        konto = int(wybrane[0]["ctidTraderAccountId"])
        _zapisz_ustawienie(KONTO_KLUCZ, {"id": konto})
    await p.zapytaj("ACC_AUTH_REQ", {"ctidTraderAccountId": konto, "accessToken": access}, "ACC_AUTH_RES")
    return konto


async def _symbole(p: Polaczenie, konto: int) -> dict[int, str]:
    """symbolId → moneta terminala; lista brokera w pamięci bazy, żeby nie pytać co minutę."""
    zapis = _ustawienie(SYMBOLE_KLUCZ) or {}
    if zapis.get("konto") == konto and zapis.get("mapa"):
        return {int(k): v for k, v in zapis["mapa"].items()}
    r = await p.zapytaj("SYMBOLS_REQ", {"ctidTraderAccountId": konto}, "SYMBOLS_RES", timeout=20.0)
    po_nazwie = {(x.get("symbolName") or "").upper().replace("/", ""): int(x["symbolId"]) for x in r.get("symbol") or []
                 if x.get("enabled", True)}
    mapa = {}
    for coin, nazwy in SYMBOLE.items():
        for n in nazwy:
            if n in po_nazwie:
                mapa[po_nazwie[n]] = coin
                break
    _zapisz_ustawienie(SYMBOLE_KLUCZ, {"konto": konto, "mapa": {str(k): v for k, v in mapa.items()}})
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
    co ZAPIS_CO_S. `polacz` = fabryka WebSocketu (testy podają atrapę)."""
    if not skonfigurowany():
        return {"skipped": True, "reason": "CTRADER_* not set"}
    start = time.monotonic()
    swiece = Swiece()
    wynik = {"ticks": 0, "rows": 0, "gap_rows": 0}
    if polacz is None:
        import websockets
        polacz = lambda: websockets.connect(f"wss://{_host()}:5036", open_timeout=10, ping_interval=None,  # noqa: E731
                                            max_size=8 * 1024 * 1024)
    for proba in (1, 2):
        try:
            async with polacz() as ws:
                mapa: dict[int, str] = {}

                def on_spot(pl):
                    coin = mapa.get(int(pl.get("symbolId") or 0))
                    if coin:
                        bid, ask = pl.get("bid"), pl.get("ask")
                        swiece.tick(coin, int(bid) / SKALA if bid else None, int(ask) / SKALA if ask else None,
                                    (int(pl["timestamp"]) / 1000) if pl.get("timestamp") else time.time())

                p = Polaczenie(ws, on_spot)
                konto = await _zaloguj(p)
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
                        ostatni_zapis = time.monotonic()
                wynik["rows"] += await asyncio.to_thread(zapisz, swiece.zrzut())
                wynik["ticks"] = swiece.ticki
                wynik["symbols"] = sorted(mapa.values())
                return wynik
        except BladOA as e:
            tokenowy = any(x in e.kod for x in ("TOKEN", "AUTH"))
            if proba == 1 and tokenowy and odswiez_token():
                continue
            print(f"[ctrader] {e}", flush=True)
            wynik["error"] = e.kod
            break
        except Exception as e:  # sieć, timeout — próbki mid Hyperliquida zostają
            print(f"[ctrader] {type(e).__name__}: {str(e)[:160]}", flush=True)
            wynik["error"] = type(e).__name__
            break
    wynik["rows"] += zapisz(swiece.zrzut())
    wynik["ticks"] = swiece.ticki
    return wynik
