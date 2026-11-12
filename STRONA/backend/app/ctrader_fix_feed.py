"""Feed FX z cTrader FIX API (sesja QUOTE) — ticki bid/ask → świece 1m w cronie.

FTMO (i inni brokerzy cTrader) wystawiają FIX 4.4 na `live.cfixapi.com`:
  QUOTE SSL 5211 — Połączenie Cen (SenderSubID/TargetSubID=QUOTE)
  TRADE SSL 5212 — lista instrumentów (Security List); OHLCV historycznego FIX nie ma.

W cronie: logowanie QUOTE → (ew. TRADE po listę symboli) → MarketDataRequest spot
→ składanie M1 jak w `ctrader_feed` → zapis `terminal_candles` + `broker_quotes`.
Luki historii nadal łata `uzupelnij_historie` (Twelve Data / Finnhub).

Env: CTRADER_FIX_PASSWORD (wymagane), CTRADER_FIX_SENDER_COMP_ID (np. live.ftmo.17246722),
opcjonalnie CTRADER_FIX_USERNAME (login; domyślnie ostatni segment SenderCompID),
CTRADER_FIX_HOST, CTRADER_FIX_QUOTE_PORT, CTRADER_FIX_TRADE_PORT, CTRADER_FIX_TARGET_COMP_ID.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import time
from datetime import datetime, timezone

from . import broker_quotes
from .config import get_settings
from .ctrader_feed import SYMBOLE, Swiece, ZAPIS_CO_S, _norm, zapisz
from .db import SessionLocal
from .models import AppSetting

SOH = "\x01"
SYMBOLE_KLUCZ = "ctrader_fix_symbols"
HB_S = 20


def skonfigurowany() -> bool:
    s = get_settings()
    return bool(s.ctrader_fix_password and s.ctrader_fix_sender_comp_id)


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


def _username(s) -> str:
    return (s.ctrader_fix_username or "").strip() or (s.ctrader_fix_sender_comp_id.rsplit(".", 1)[-1])


def _czas() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]


def _checksum(cialo: str) -> str:
    return f"{sum(ord(c) for c in cialo) % 256:03d}"


def zbuduj(pola: list[tuple[str, str | int]]) -> bytes:
    """FIX 4.4 z BodyLength i CheckSum — kolejność: 8, 9, 35, reszta, 10."""
    mid = [(k, str(v)) for k, v in pola if k not in ("8", "9", "10")]
    if not any(k == "35" for k, _ in mid):
        raise ValueError("MsgType (35) required")
    # 35 musi być pierwsze w body (po 8/9)
    typ = next((v for k, v in mid if k == "35"), "")
    reszta = [(k, v) for k, v in mid if k != "35"]
    body = SOH.join(f"{k}={v}" for k, v in [("35", typ), *reszta]) + SOH
    head = f"8=FIX.4.4{SOH}9={len(body)}{SOH}"
    msg = head + body
    return (msg + f"10={_checksum(msg)}{SOH}").encode("ascii")


def parsuj(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for cz in raw.split(SOH):
        if "=" in cz:
            k, _, v = cz.partition("=")
            # powtarzalne grupy: ostatnia wygrywa w płaskim dict; MD i Security List
            # rozbieramy osobno z listy pól.
            out[k] = v
    return out


def pola(raw: str) -> list[tuple[str, str]]:
    return [tuple(cz.split("=", 1)) for cz in raw.split(SOH) if "=" in cz]  # type: ignore[misc]


class FixSesja:
    """Jedna sesja TCP/SSL FIX: logon, send/recv, heartbeat."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 sender: str, target: str, sub: str, username: str, password: str):
        self.r, self.w = reader, writer
        self.sender, self.target, self.sub = sender, target, sub
        self.username, self.password = username, password
        self.seq = 1
        self.buf = b""

    def _naglowek(self, typ: str, extra: list[tuple[str, str | int]]) -> bytes:
        # RoE: TargetSubID=QUOTE|TRADE; SenderSubID dowolne — FTMO UI pokazuje SenderSubID.
        baza: list[tuple[str, str | int]] = [
            ("35", typ), ("49", self.sender), ("56", self.target), ("34", self.seq),
            ("52", _czas()), ("50", self.sub), ("57", self.sub),
        ]
        self.seq += 1
        return zbuduj(baza + extra)

    async def send(self, typ: str, extra: list[tuple[str, str | int]] | None = None) -> None:
        self.w.write(self._naglowek(typ, extra or []))
        await self.w.drain()

    async def recv(self, timeout: float = 15.0) -> str:
        while True:
            i = self.buf.find(b"10=")
            if i >= 0 and len(self.buf) >= i + 7:
                # 10=XXX<SOH>
                koniec = self.buf.find(SOH.encode(), i)
                if koniec >= 0:
                    raw, self.buf = self.buf[: koniec + 1], self.buf[koniec + 1 :]
                    return raw.decode("ascii", errors="replace")
            try:
                chunk = await asyncio.wait_for(self.r.read(4096), timeout)
            except asyncio.TimeoutError as e:
                raise TimeoutError("FIX recv timeout") from e
            if not chunk:
                raise ConnectionError("FIX connection closed")
            self.buf += chunk

    async def logon(self) -> None:
        await self.send("A", [
            ("98", 0), ("108", HB_S), ("141", "Y"),
            ("553", self.username), ("554", self.password),
        ])
        raw = await self.recv()
        m = parsuj(raw)
        if m.get("35") == "5":
            raise RuntimeError(f"FIX Logout: {m.get('58') or raw[:120]}")
        if m.get("35") != "A":
            raise RuntimeError(f"FIX Logon expected, got 35={m.get('35')}: {m.get('58') or ''}"[:160])

    async def logout(self) -> None:
        try:
            await self.send("5")
        except Exception:
            pass
        try:
            self.w.close()
            await self.w.wait_closed()
        except Exception:
            pass


async def _polacz(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    ctx = ssl.create_default_context()
    return await asyncio.open_connection(host, port, ssl=ctx, server_hostname=host)


def _mapa_z_security_list(raw: str) -> dict[str, str]:
    """symbolId → moneta terminala; nazwy w tagu 1007 (SymbolName) albo 55 gdy tekstowe."""
    po_nazwie: dict[str, str] = {}
    pl = pola(raw)
    i = 0
    while i < len(pl):
        k, v = pl[i]
        if k == "55":
            sid = v
            nazwa = ""
            j = i + 1
            while j < len(pl) and pl[j][0] != "55":
                if pl[j][0] == "1007":
                    nazwa = pl[j][1]
                j += 1
            if nazwa:
                po_nazwie[_norm(nazwa)] = sid
            i = j
        else:
            i += 1
    mapa: dict[str, str] = {}
    for coin, nazwy in SYMBOLE.items():
        for n in nazwy:
            if _norm(n) in po_nazwie:
                mapa[po_nazwie[_norm(n)]] = coin
                break
    return mapa


async def _pobierz_mape(s) -> dict[str, str]:
    zapis = _ustawienie(SYMBOLE_KLUCZ) or {}
    podpis = json.dumps(SYMBOLE, sort_keys=True)
    if zapis.get("sender") == s.ctrader_fix_sender_comp_id and zapis.get("mapa") and zapis.get("podpis") == podpis:
        return {str(k): v for k, v in zapis["mapa"].items()}
    reader, writer = await _polacz(s.ctrader_fix_host, int(s.ctrader_fix_trade_port))
    ses = FixSesja(reader, writer, s.ctrader_fix_sender_comp_id, s.ctrader_fix_target_comp_id,
                   "TRADE", _username(s), s.ctrader_fix_password)
    try:
        await ses.logon()
        await ses.send("x", [("320", f"sl-{int(time.time())}"), ("559", 0)])
        deadline = time.monotonic() + 20
        mapa: dict[str, str] = {}
        while time.monotonic() < deadline:
            raw = await ses.recv(timeout=max(1.0, deadline - time.monotonic()))
            if parsuj(raw).get("35") == "y":
                mapa = _mapa_z_security_list(raw)
                break
            if parsuj(raw).get("35") in ("0", "1"):  # Heartbeat / TestRequest
                if parsuj(raw).get("35") == "1":
                    await ses.send("0", [("112", parsuj(raw).get("112") or "")])
                continue
        if not mapa:
            raise RuntimeError("FIX Security List empty or timeout")
        _zapisz_ustawienie(SYMBOLE_KLUCZ, {"sender": s.ctrader_fix_sender_comp_id, "podpis": podpis,
                                           "mapa": mapa})
        return mapa
    finally:
        await ses.logout()


def _spot_z_md(raw: str) -> list[tuple[str, float | None, float | None]]:
    """Z 35=W/X wyciąga (symbolId, bid, ask); spot depth=1 — jedna para na symbol."""
    pl = pola(raw)
    typ = next((v for k, v in pl if k == "35"), "")
    if typ not in ("W", "X"):
        return []
    # Snapshot W: 55 raz na wiadomość; Incremental X: 55 w grupie wpisów.
    out: dict[str, list[float | None]] = {}
    sid = next((v for k, v in pl if k == "55"), "")
    i = 0
    while i < len(pl):
        k, v = pl[i]
        if k == "55":
            sid = v
        if k == "269" and i + 1 < len(pl):
            entry = v  # 0=bid 1=offer
            px = None
            j = i + 1
            while j < len(pl) and pl[j][0] not in ("269", "279"):
                if pl[j][0] == "270":
                    px = float(pl[j][1])
                if pl[j][0] == "55":
                    sid = pl[j][1]
                j += 1
            if sid and px is not None:
                ba = out.setdefault(sid, [None, None])
                if entry == "0":
                    ba[0] = px
                elif entry == "1":
                    ba[1] = px
            i = j
            continue
        i += 1
    return [(s, b, a) for s, (b, a) in out.items()]


async def sluchaj(budzet_s: float, polacz_quote=None, mapa_override: dict[str, str] | None = None) -> dict:
    """Jeden przebieg crona na sesji QUOTE. `polacz_quote` / `mapa_override` — testy."""
    if not skonfigurowany():
        return {"skipped": True, "reason": "CTRADER_FIX_* not set"}
    s = get_settings()
    start = time.monotonic()
    wynik: dict = {"ticks": 0, "rows": 0, "source": "fix",
                   "account": _username(s), "sender": s.ctrader_fix_sender_comp_id}
    swiece = Swiece()
    try:
        mapa = mapa_override if mapa_override is not None else await _pobierz_mape(s)
        if not mapa:
            return {**wynik, "skipped": True, "reason": "no matching FIX symbols"}
        wynik["symbols"] = sorted(mapa.values())
        if polacz_quote is None:
            reader, writer = await _polacz(s.ctrader_fix_host, int(s.ctrader_fix_quote_port))
        else:
            reader, writer = await polacz_quote()
        ses = FixSesja(reader, writer, s.ctrader_fix_sender_comp_id, s.ctrader_fix_target_comp_id,
                       "QUOTE", _username(s), s.ctrader_fix_password)
        await ses.logon()
        # Jedna subskrypcja na symbol (spot depth=1).
        for n, sid in enumerate(mapa, start=1):
            await ses.send("V", [
                ("262", f"md-{sid}-{int(time.time())}-{n}"), ("263", 1), ("264", 1), ("265", 1),
                ("146", 1), ("55", sid), ("267", 2), ("269", 0), ("269", 1),
            ])
        ostatni_zapis = time.monotonic()
        ostatni_hb = time.monotonic()
        while True:
            zostalo = budzet_s - (time.monotonic() - start)
            if zostalo <= 0:
                break
            try:
                raw = await ses.recv(timeout=min(2.0, max(0.2, zostalo)))
            except (asyncio.TimeoutError, TimeoutError):
                raw = ""
            if raw:
                m = parsuj(raw)
                t = m.get("35")
                if t == "1":
                    await ses.send("0", [("112", m.get("112") or "")])
                elif t == "0":
                    pass
                elif t == "5":
                    wynik["error"] = m.get("58") or "Logout"
                    break
                else:
                    for sid, bid, ask in _spot_z_md(raw):
                        coin = mapa.get(str(sid))
                        if coin:
                            swiece.tick(coin, bid, ask, time.time())
            if time.monotonic() - ostatni_hb >= HB_S:
                await ses.send("0")
                ostatni_hb = time.monotonic()
            if time.monotonic() - ostatni_zapis >= ZAPIS_CO_S:
                wynik["rows"] += await asyncio.to_thread(zapisz, swiece.zrzut())
                await asyncio.to_thread(broker_quotes.zapisz, swiece.kwoty())
                ostatni_zapis = time.monotonic()
        wynik["rows"] += await asyncio.to_thread(zapisz, swiece.zrzut())
        await asyncio.to_thread(broker_quotes.zapisz, swiece.kwoty())
        wynik["ticks"] = swiece.ticki
        await ses.logout()
    except Exception as e:
        print(f"[ctrader-fix] {type(e).__name__}: {str(e)[:160]}", flush=True)
        wynik["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        wynik["rows"] += zapisz(swiece.zrzut())
        wynik["ticks"] = swiece.ticki
        if swiece.kwoty():
            broker_quotes.zapisz(swiece.kwoty())
    return wynik
