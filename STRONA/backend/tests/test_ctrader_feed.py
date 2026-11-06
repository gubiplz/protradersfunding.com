"""Feed FX z cTrader Open API w cronie świec — na atrapie serwera (bez sieci).

Sprawdzamy protokół (auth aplikacji i konta, wybór konta demo, mapa symboli), składanie
świec 1m z mid ticków, zapis wierszy ostatecznych (n=-1), łatanie luk trendbarami,
odświeżenie tokenu po błędzie autoryzacji i to, że bez konfiguracji nic nie wychodzi.
"""
import asyncio
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")

from app import ctrader_feed as cf  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app import broker_quotes, markets  # noqa: E402
from app.models import AppSetting, TerminalCandle  # noqa: E402

init_db()


@pytest.fixture(autouse=True)
def _czysto(monkeypatch):
    s = cf.get_settings()
    for k, v in {"ctrader_client_id": "cid", "ctrader_client_secret": "csec", "ctrader_access_token": "acc-1",
                 "ctrader_refresh_token": "ref-1", "ctrader_account_id": "", "ctrader_host": "demo"}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(cf, "ZAPIS_CO_S", 0.05)
    db = SessionLocal()
    db.query(TerminalCandle).delete()
    db.query(AppSetting).filter(AppSetting.key.in_((cf.TOKEN_KLUCZ, cf.KONTO_KLUCZ, cf.SYMBOLE_KLUCZ, broker_quotes.KLUCZ))).delete(synchronize_session=False)
    db.commit(); db.close()
    broker_quotes.wyczysc()
    yield
    # Quotes written here must not price FX in other test files.
    db = SessionLocal()
    db.query(AppSetting).filter(AppSetting.key == broker_quotes.KLUCZ).delete(synchronize_session=False)
    db.commit(); db.close()
    broker_quotes.wyczysc()


class Serwer:
    """Atrapa: odpowiada na żądania po payloadType, po subskrypcji wypuszcza ticki."""

    def __init__(self, ticki, trendbary=None, zly_token=None):
        self.ticki, self.trendbary, self.zly_token = list(ticki), trendbary or [], zly_token
        self.q: asyncio.Queue = asyncio.Queue()
        self.wyslane: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, raw):
        m = json.loads(raw)
        self.wyslane.append(m)
        t, p, mid = m["payloadType"], m.get("payload") or {}, m.get("clientMsgId")
        odp = lambda typ, pl: self.q.put_nowait(json.dumps({"clientMsgId": mid, "payloadType": cf.PT[typ], "payload": pl}))  # noqa: E731
        if t == cf.PT["APP_AUTH_REQ"]:
            odp("APP_AUTH_RES", {})
        elif t == cf.PT["ACCOUNTS_REQ"]:
            odp("ACCOUNTS_RES", {"ctidTraderAccount": [{"ctidTraderAccountId": 9001, "isLive": True},
                                                       {"ctidTraderAccountId": 4242, "isLive": False}]})
        elif t == cf.PT["ACC_AUTH_REQ"]:
            if self.zly_token and p.get("accessToken") == self.zly_token:
                odp("ERROR", {"errorCode": "CH_ACCESS_TOKEN_INVALID", "description": "expired"})
            else:
                odp("ACC_AUTH_RES", {})
        elif t == cf.PT["SYMBOLS_REQ"]:
            odp("SYMBOLS_RES", {"symbol": [{"symbolId": 1, "symbolName": "EURUSD"}, {"symbolId": 2, "symbolName": "GBP/USD"},
                                           {"symbolId": 3, "symbolName": "USDJPY"}, {"symbolId": 9, "symbolName": "BTCUSD"},
                                           {"symbolId": 5, "symbolName": "JPN.225"}]})
        elif t == cf.PT["TRENDBARS_REQ"]:
            odp("TRENDBARS_RES", {"trendbar": [b for b in self.trendbary if b["_sid"] == p["symbolId"]]})
        elif t == cf.PT["SUB_SPOTS_REQ"]:
            odp("SUB_SPOTS_RES", {})
            for tk in self.ticki:
                self.q.put_nowait(json.dumps({"payloadType": cf.PT["SPOT"], "payload": tk}))

    async def recv(self):
        return await self.q.get()


def _wiersze(coin):
    s = SessionLocal()
    try:
        return {r.ts: (r.o, r.h, r.l, r.c, r.n) for r in s.query(TerminalCandle).filter(TerminalCandle.coin == coin).all()}
    finally:
        s.close()


def test_ticki_skladaja_swiece_z_mid_i_zapisuja_wiersze_ostateczne():
    teraz = time.time()
    minuta_ms = int(teraz // 60 * 60 * 1000)
    ticki = [{"symbolId": 1, "bid": 111550, "ask": 111560, "timestamp": minuta_ms + 1000},
             {"symbolId": 1, "bid": 111580, "timestamp": minuta_ms + 2000},             # tylko bid
             {"symbolId": 1, "ask": 111530, "bid": 111520, "timestamp": minuta_ms + 3000},
             {"symbolId": 3, "bid": 15815000, "ask": 15816000, "timestamp": minuta_ms + 1500},
             {"symbolId": 9, "bid": 7500000000, "ask": 7500100000}]                       # spoza mapy
    srv = Serwer(ticki)
    w = asyncio.run(cf.sluchaj(0.3, polacz=lambda: srv))
    assert w["ticks"] == 4 and w["symbols"] == ["xyz:EUR", "xyz:GBP", "xyz:JP225", "xyz:JPY"] and not w.get("error")
    m = datetime.fromtimestamp(minuta_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    assert _wiersze("xyz:EUR")[m] == (1.11555, 1.1157, 1.11525, 1.11525, cf.FEED_N)
    assert _wiersze("xyz:JPY")[m] == (158.155, 158.155, 158.155, 158.155, cf.FEED_N)
    # Ostatni bid/ask zapisany do wypełnień: EURUSD 1.11520/1.11530, USDJPY 158.15/158.16.
    q = broker_quotes.kwoty(["xyz:EUR", "xyz:JPY", "BTC"])
    assert q["xyz:EUR"][:2] == (1.1152, 1.1153) and q["xyz:JPY"][:2] == (158.15, 158.16) and "BTC" not in q
    # Konto demo wybrane spośród dwóch i zapamiętane; subskrypcja tylko znalezionych symboli.
    typy = [x["payloadType"] for x in srv.wyslane]
    assert typy[:4] == [cf.PT["APP_AUTH_REQ"], cf.PT["ACCOUNTS_REQ"], cf.PT["ACC_AUTH_REQ"], cf.PT["SYMBOLS_REQ"]]
    acc = next(x for x in srv.wyslane if x["payloadType"] == cf.PT["ACC_AUTH_REQ"])["payload"]
    assert acc == {"ctidTraderAccountId": 4242, "accessToken": "acc-1"}
    sub = next(x for x in srv.wyslane if x["payloadType"] == cf.PT["SUB_SPOTS_REQ"])["payload"]
    assert sorted(sub["symbolId"]) == [1, 2, 3, 5]
    # Drugi przebieg: konto i symbole z pamięci, bez ACCOUNTS_REQ i SYMBOLS_REQ.
    srv2 = Serwer([])
    asyncio.run(cf.sluchaj(0.1, polacz=lambda: srv2))
    assert cf.PT["ACCOUNTS_REQ"] not in [x["payloadType"] for x in srv2.wyslane]
    assert cf.PT["SYMBOLS_REQ"] not in [x["payloadType"] for x in srv2.wyslane]


def test_mapa_ze_starej_wersji_przebudowana():
    # Zapis sprzed dodania JPN225: bez podpisu, tylko EURUSD.
    s = SessionLocal()
    s.add(AppSetting(key=cf.SYMBOLE_KLUCZ, value=json.dumps({"konto": 4242, "mapa": {"1": "xyz:EUR"}})))
    s.add(AppSetting(key=cf.KONTO_KLUCZ, value=json.dumps({"id": 4242})))
    s.commit(); s.close()
    srv = Serwer([])
    w = asyncio.run(cf.sluchaj(0.1, polacz=lambda: srv))
    assert cf.PT["SYMBOLS_REQ"] in [x["payloadType"] for x in srv.wyslane]
    assert "xyz:JP225" in w["symbols"]


def test_luki_latane_trendbarami_a_ticki_nadpisuja_probki():
    teraz = datetime.fromtimestamp(time.time() // 60 * 60, tz=timezone.utc).replace(tzinfo=None)
    s = SessionLocal()
    for i in range(1, 30):      # ostatnie pół godziny z feedu, z dziurą 10–14 minut temu
        if not 10 <= i <= 14:
            s.add(TerminalCandle(coin="xyz:EUR", ts=teraz - timedelta(minutes=i), o=1.1, h=1.1, l=1.1, c=1.1, n=cf.FEED_N))
    s.add(TerminalCandle(coin="xyz:EUR", ts=teraz - timedelta(minutes=12), o=1.2, h=1.2, l=1.2, c=1.2, n=7))   # próbka mid
    s.commit(); s.close()
    dziura = [teraz - timedelta(minutes=i) for i in (14, 13, 12, 11, 10)]
    bary = [{"_sid": 1, "low": 111500, "deltaOpen": 10, "deltaHigh": 40, "deltaClose": 20,
             "utcTimestampInMinutes": int(t.replace(tzinfo=timezone.utc).timestamp() // 60), "volume": 5} for t in dziura]
    srv = Serwer([], trendbary=bary)
    w = asyncio.run(cf.sluchaj(0.1, polacz=lambda: srv))
    assert w["gap_rows"] == 5
    r = _wiersze("xyz:EUR")
    assert all(r[t] == (1.1151, 1.1154, 1.115, 1.1152, cf.FEED_N) for t in dziura)
    req = [x["payload"] for x in srv.wyslane if x["payloadType"] == cf.PT["TRENDBARS_REQ"] and x["payload"]["symbolId"] == 1]
    okna = [(q["fromTimestamp"], q["toTimestamp"]) for q in req]
    od = int(dziura[0].replace(tzinfo=timezone.utc).timestamp() * 1000)
    assert (od, od + 5 * 60_000) in okna and all(q["period"] == cf.M1 for q in req)


def test_wygasly_token_odswiezony_i_zapisany():
    wymiany = []

    def transport(params):
        wymiany.append(params)
        return 200, {"accessToken": "acc-2", "refreshToken": "ref-2", "expiresIn": 2_628_000}

    import app.ctrader_feed as mod
    stary = mod.odswiez_token
    mod.odswiez_token = lambda transport_=None: stary(transport=transport)
    try:
        serwery = [Serwer([], zly_token="acc-1"), Serwer([], zly_token="acc-1")]
        w = asyncio.run(cf.sluchaj(0.1, polacz=lambda: serwery.pop(0)))
    finally:
        mod.odswiez_token = stary
    assert not w.get("error") and wymiany == [{"grant_type": "refresh_token", "refresh_token": "ref-1",
                                               "client_id": "cid", "client_secret": "csec"}]
    assert cf._tokeny() == ("acc-2", "ref-2")


def test_bez_konfiguracji_nic_nie_wychodzi(monkeypatch):
    monkeypatch.setattr(cf.get_settings(), "ctrader_client_id", "")
    assert cf.skonfigurowany() is False
    assert asyncio.run(cf.sluchaj(0.1, polacz=lambda: pytest.fail("no connection without config")))["skipped"] is True


def test_kwotowanie_brokera_wycenia_i_wypelnia_fx(monkeypatch):
    """Świeży bid/ask brokera: mid do wyceny i jeden poziom książki bez poślizgu; HL nie pytany."""
    zapytania = []
    monkeypatch.setattr(markets, "_post", lambda body: zapytania.append(body) or {})
    broker_quotes.zapisz({"xyz:GBP": (1.33510, 1.33516)})
    assert markets.mids({"xyz:GBP"}) == {"xyz:GBP": pytest.approx(1.33513)}
    bids, asks = markets.book("xyz:GBP")
    assert bids[0][0] == 1.3351 and asks[0][0] == 1.33516 and asks[0][1] >= 1e9
    assert zapytania == []
    # Stare kwotowanie: wypełnienie odrzucone, wycena jeszcze po brokerze do MARK_MAX_S.
    broker_quotes.zapisz({"xyz:GBP": (1.33510, 1.33516)}, teraz=time.time() - 30)
    with pytest.raises(broker_quotes.Stale):
        markets.book("xyz:GBP")
    assert "xyz:GBP" in markets.mids({"xyz:GBP"})
    # Po MARK_MAX_S wycena wraca do Hyperliquida.
    broker_quotes.zapisz({"xyz:GBP": (1.33510, 1.33516)}, teraz=time.time() - 120)
    monkeypatch.setattr(markets, "_post", lambda body: {"xyz:GBP": "1.3348"})
    markets._MIDS_CACHE.clear()
    assert markets.mids({"xyz:GBP"}) == {"xyz:GBP": 1.3348}


def test_zlecenie_fx_po_bid_ask_brokera(monkeypatch):
    """1 lot GBPUSD: wejście po asku brokera, nie po książce Hyperliquida (~9 pipsów kosztu)."""
    from app import terminal
    from fastapi import HTTPException
    broker_quotes.zapisz({"xyz:GBP": (1.33510, 1.33516)})
    m = markets.get("GBPUSD")
    vwap, mid, slip = terminal._vwap(m, "buy", 100_000.0, 0.01)
    assert vwap == 1.33516 and mid == pytest.approx(1.33513) and slip < 0.00003
    assert terminal._vwap(m, "sell", 100_000.0, 0.01)[0] == 1.3351
    broker_quotes.zapisz({"xyz:GBP": (1.33510, 1.33516)}, teraz=time.time() - 30)
    with pytest.raises(HTTPException) as e:
        terminal._vwap(m, "buy", 100_000.0, 0.01)
    assert e.value.status_code == 503 and "reconnecting" in e.value.detail


def test_terminal_dostaje_bid_ask_brokera():
    """Ogon świec niesie bid/ask brokera, a /quotes mid-y wszystkich takich rynków."""
    from fastapi.testclient import TestClient
    from app.main import app
    broker_quotes.zapisz({"xyz:GBP": (1.33510, 1.33516)})
    with TestClient(app) as cl:
        d = cl.get("/api/terminal/candles?symbol=GBPUSD&interval=1m&limit=2").json()
        assert d["quote"]["bid"] == 1.3351 and d["quote"]["ask"] == 1.33516 and d["quote"]["live"] is True
        assert "quote" not in cl.get("/api/terminal/candles?symbol=EURUSD&interval=1m&limit=2").json()
        q = cl.get("/api/terminal/quotes").json()["quotes"]
        assert set(q) == {"xyz:GBP"} and q["xyz:GBP"]["live"] is True
