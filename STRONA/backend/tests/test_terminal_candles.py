"""Świece M1 z próbkowanego mid dla cienkich rynków.

Hyperliquid drukuje na EUR/GBP/JPY kilka transakcji na minutę przy 4 miejscach,
więc świece giełdy stały i skakały. Cron próbkuje mid co sekundę i składa z tego
O/H/L/C; `/api/terminal/candles` oddaje je w żądanym interwale, a feed w
przeglądarce nakłada je na historię giełdy.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import markets, terminal  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppSetting, TerminalCandle  # noqa: E402

init_db()
ADMIN = {"X-Admin-Token": "tajny-token"}


PUSTY_FH = {"s": "no_data"}


def _fh_body(bary):
    """Odpowiedź Finnhub z listy (sekundy UTC, o, h, l, c); None w świecy = minuta bez notowań."""
    return {"s": "ok", "t": [b[0] for b in bary], "o": [b[1] for b in bary], "h": [b[2] for b in bary],
            "l": [b[3] for b in bary], "c": [b[4] for b in bary], "v": [0 for _ in bary]}


@pytest.fixture(autouse=True)
def _czysto(monkeypatch):
    monkeypatch.setattr(terminal, "PROBKA_S", 0.001)
    # Bez klucza feed i historia nie wychodzą do sieci; testy feedu ustawiają klucz same.
    monkeypatch.setattr(terminal.get_settings(), "finnhub_api_key", "")
    terminal._FEED_PAUZA["do"] = 0.0
    s = SessionLocal()
    s.query(TerminalCandle).delete()
    row = s.get(AppSetting, terminal.SWIECE_KLUCZ)
    if row:
        s.delete(row)
    s.commit(); s.close()


def _mid_sekwencja(monkeypatch, ceny):
    it = iter(ceny)
    ostatnia = {"v": ceny[0]}

    def mids(coins):
        try:
            ostatnia["v"] = next(it)
        except StopIteration:
            pass
        return {c: ostatnia["v"] for c in coins}
    monkeypatch.setattr(markets, "mids", mids)


def test_probkowanie_sklada_swiece_i_scala_z_istniejacymi(monkeypatch):
    minuta = datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
    monkeypatch.setattr(terminal, "_now", lambda: minuta.replace(tzinfo=timezone.utc) + timedelta(seconds=10))
    _mid_sekwencja(monkeypatch, [1.1150, 1.1158, 1.1147, 1.1153])
    w = terminal.probkuj_swiece(budzet_s=0.02)
    assert w["coins"] == len(markets.SAMPLED) and w["rows"] == len(markets.SAMPLED) and w["samples"] >= 4
    s = SessionLocal()
    r = s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").one()
    assert (r.o, r.h, r.l, r.c) == (1.1150, 1.1158, 1.1147, 1.1153) and r.n == w["samples"]
    s.close()
    # Drugi przebieg w tej samej minucie (po odstępie): otwarcie zostaje, H/L/C idą dalej.
    s = SessionLocal(); s.get(AppSetting, terminal.SWIECE_KLUCZ).value = "0"; s.commit(); s.close()
    _mid_sekwencja(monkeypatch, [1.1160, 1.1140])
    terminal.probkuj_swiece(budzet_s=0.01)
    s = SessionLocal()
    r = s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").one()
    assert r.o == 1.1150 and r.h == 1.1160 and r.l == 1.1140 and r.c == 1.1140
    s.close()


def test_drugi_przebieg_w_tej_samej_minucie_odpada(monkeypatch):
    _mid_sekwencja(monkeypatch, [1.0])
    assert "skipped" not in terminal.probkuj_swiece(budzet_s=0.001)
    assert terminal.probkuj_swiece(budzet_s=0.001) == {"skipped": True, "reason": "inny przebieg w toku"}


def test_api_candles_sklada_interwaly_i_ignoruje_inne_rynki():
    baza = datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc)
    s = SessionLocal()
    for i, (o, h, l, c) in enumerate([(1.10, 1.12, 1.09, 1.11), (1.11, 1.13, 1.10, 1.12), (1.12, 1.125, 1.115, 1.118),
                                       (1.118, 1.14, 1.117, 1.139), (1.139, 1.141, 1.13, 1.135), (1.135, 1.136, 1.12, 1.121)]):
        s.add(TerminalCandle(coin="xyz:EUR", ts=(baza + timedelta(minutes=i)).replace(tzinfo=None), o=o, h=h, l=l, c=c, n=50))
    s.commit(); s.close()
    koniec = int((baza + timedelta(minutes=6)).timestamp() * 1000)
    with TestClient(app) as cl:
        m1 = cl.get(f"/api/terminal/candles?symbol=EURUSD&interval=1m&end={koniec}&limit=10").json()["candles"]
        assert len(m1) == 6 and m1[0]["timestamp"] == int(baza.timestamp() * 1000) and m1[0]["volume"] == 0
        m5 = cl.get(f"/api/terminal/candles?symbol=EURUSD&interval=5m&end={koniec}&limit=10").json()["candles"]
        assert [k["timestamp"] for k in m5] == [int(baza.timestamp() * 1000), int((baza + timedelta(minutes=5)).timestamp() * 1000)]
        assert (m5[0]["open"], m5[0]["high"], m5[0]["low"], m5[0]["close"]) == (1.10, 1.141, 1.09, 1.135)
        assert cl.get(f"/api/terminal/candles?symbol=BTCUSD&interval=1m&end={koniec}").json() == {"candles": []}
        assert cl.get(f"/api/terminal/candles?symbol=EURUSD&interval=1d&end={koniec}").json() == {"candles": []}
        # Lista rynków niesie flagę dla feedu w przeglądarce.
        rynki = {m["sym"]: m["sampled"] for m in cl.get("/api/terminal/markets").json()["markets"]}
        assert rynki["EURUSD"] is True and rynki["BTCUSD"] is False


def test_cron_candles_wymaga_sekretu_i_oddaje_wynik(monkeypatch):
    _mid_sekwencja(monkeypatch, [157.1, 157.2])
    monkeypatch.setattr("app.main.CANDLES_BUDZET_S", 0.005)
    with TestClient(app) as cl:
        assert cl.get("/api/cron/candles").status_code == 401
        r = cl.get("/api/cron/candles", headers=ADMIN)
        assert r.status_code == 200 and r.json()["rows"] == len(markets.SAMPLED)


# --- historia 1m z zewnętrznego źródła (MetaApi / Twelve Data) ----------------
def _hist_zrodlo(monkeypatch, **pola):
    s = terminal.get_settings()
    for k, v in {"metaapi_token": "", "metaapi_candles_account_id": "", "twelvedata_api_key": "", "finnhub_api_key": "",
                 "candles_history_days": 2, "candles_history_symbols": "", **pola}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(terminal, "HIST_ZAPYTAN", 20)
    db = SessionLocal()
    db.query(AppSetting).filter(AppSetting.key.like(terminal.HIST_KONIEC_KLUCZ + "%")).delete(synchronize_session=False)
    db.commit(); db.close()


def _wiersz(s, coin, ts, n=50, px=1.1):
    s.add(TerminalCandle(coin=coin, ts=ts.replace(tzinfo=None), o=px, h=px + 0.001, l=px - 0.001, c=px, n=n))


def test_dociaganie_historii_lata_dziury_i_idzie_wstecz(monkeypatch):
    teraz = datetime(2026, 11, 6, 14, 0)
    poczatek_zrodla = datetime(2026, 11, 6, 0, 0)       # starszych świec źródło nie ma
    _hist_zrodlo(monkeypatch, metaapi_token="tok", metaapi_candles_account_id="acc-1")
    s = SessionLocal()
    for m in list(range(30, 41)) + list(range(50, 60)):   # 13:30–13:40, dziura, 13:50–13:59
        _wiersz(s, "xyz:EUR", teraz - timedelta(minutes=60 - m))
    s.commit(); s.close()
    zapytania = []

    def transport(url, params, headers):
        zapytania.append((url, dict(params), dict(headers)))
        assert "acc-1" in url and headers == {"auth-token": "tok"}
        sym = url.split("/symbols/")[1].split("/")[0]
        koniec = datetime.strptime(params["startTime"], "%Y-%m-%dT%H:%M:%S.000Z") if "startTime" in params else teraz
        out = []
        for i in range(int(params["limit"])):
            t = koniec - timedelta(minutes=i)
            if t < poczatek_zrodla:
                break
            out.append({"symbol": sym, "time": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "open": 1.2, "high": 1.21,
                        "low": 1.19, "close": 1.205, "tickVolume": 10})
        return 200, out

    w = terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert w["source"] == "metaapi"
    eur = w["markets"]["xyz:EUR"]
    assert eur["gaps"] == 1 and eur["rows"] == 9 + 13 * 60 + 30     # 13:41–13:49 + 00:00–13:29
    # Pierwsze zapytanie łata dziurę: do 13:49, 9 minut; drugie idzie wstecz od 13:29.
    assert zapytania[0][1] == {"limit": 9, "startTime": "2026-11-06T13:49:00.000Z"}
    assert zapytania[1][1]["startTime"] == "2026-11-06T13:29:00.000Z" and zapytania[1][1]["limit"] == 1000
    s = SessionLocal()
    wiersze = {r.ts: r for r in s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").all()}
    assert wiersze[teraz - timedelta(minutes=15)].n == 0 and wiersze[teraz - timedelta(minutes=15)].c == 1.205
    assert wiersze[teraz - timedelta(minutes=5)].n == 50 and wiersze[teraz - timedelta(minutes=5)].c == 1.1   # próbkowana zostaje
    assert min(wiersze) == poczatek_zrodla
    s.close()
    # Kolejny przebieg: źródło nie ma nic starszego → znacznik końca, bez dalszych zapytań o EUR.
    n = len(zapytania)
    w2 = terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert w2["markets"]["xyz:EUR"]["done"] is True
    w3 = terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert w3["markets"]["xyz:EUR"]["done"] is True
    assert sum(1 for q in zapytania[n:] if "EURUSD" in q[0]) == 1
    # Endpoint oddaje historię z obu źródeł w jednym ciągu.
    koniec = int(teraz.replace(tzinfo=timezone.utc).timestamp() * 1000)
    with TestClient(app) as cl:
        m1 = cl.get(f"/api/terminal/candles?symbol=EURUSD&interval=1m&end={koniec}&limit=120").json()["candles"]
        assert len(m1) == 120 and m1[-1]["close"] == 1.1 and m1[0]["close"] == 1.205


def test_bez_kluczy_cron_tylko_probkuje(monkeypatch):
    _hist_zrodlo(monkeypatch)                      # bez MetaApi, Twelve Data i Finnhub
    assert terminal.uzupelnij_historie(1.0)["skipped"] is True
    assert terminal.odswiez_feed()["skipped"] is True
    _mid_sekwencja(monkeypatch, [1.1, 1.2])
    w = terminal.cron_swiece(0.02)
    assert w["rows"] == len(markets.SAMPLED) and w["history"]["skipped"] is True and w["feed_runs"] == 0


def test_twelve_data_jako_zrodlo_historii(monkeypatch):
    teraz = datetime(2026, 11, 6, 14, 0)
    _hist_zrodlo(monkeypatch, twelvedata_api_key="td-key", candles_history_symbols="xyz:JP225=JP225")
    zapytania = []

    def transport(url, params, headers):
        zapytania.append((url, dict(params)))
        # Klucz tylko w nagłówku: httpx loguje pełny URL, a logi Vercela czyta cały zespół.
        assert url == "https://api.twelvedata.com/time_series" and headers == {"Authorization": "apikey td-key"}
        if params["symbol"] == "JP225":
            return 500, {"status": "error", "code": 500, "message": "upstream"}
        if params["symbol"] == "XPT/USD":
            return 404, None                                 # symbolu nie ma w planie
        return 200, {"status": "ok", "values": [
            {"datetime": "2026-11-06 13:59:00", "open": "1.1500", "high": "1.1510", "low": "1.1490", "close": "1.1505"},
            {"datetime": "2026-11-06 13:58:00", "open": "1.1490", "high": "1.1502", "low": "1.1480", "close": "1.1500"}]}

    w = terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert w["source"] == "twelvedata" and w["markets"]["xyz:EUR"]["rows"] == 2
    assert w["markets"]["xyz:JP225"]["rows"] == 0 and w["markets"]["xyz:JP225"]["done"] is False   # błąd: spróbuje znów
    assert w["markets"]["xyz:PLATINUM"]["done"] is True                                           # 404: koniec, bez pytań co minutę
    q = {p["symbol"]: p for _, p in zapytania}
    assert q["EUR/USD"]["interval"] == "1min" and q["EUR/USD"]["outputsize"] == 5000 and "apikey" not in q["EUR/USD"]
    assert q["EUR/USD"]["end_date"] == "2026-11-06 14:00:00" and q["USD/JPY"]["timezone"] == "UTC"
    n = len(zapytania)
    terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert all(p["symbol"] != "XPT/USD" for _, p in zapytania[n:])
    s = SessionLocal()
    r = s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").order_by(TerminalCandle.ts).all()
    assert [(x.ts.minute, x.c, x.n) for x in r] == [(58, 1.15, 0), (59, 1.1505, 0)]
    s.close()


# --- feed spot (Finnhub) dla FX: nadpisuje próbki mid, próbki go nie ruszają -------
def test_feed_spot_nadpisuje_probki_a_probki_nie_nadpisuja_feedu(monkeypatch):
    monkeypatch.setattr(terminal.get_settings(), "finnhub_api_key", "fh-key")
    baza = datetime(2026, 11, 6, 15, 0)
    sek = int(baza.replace(tzinfo=timezone.utc).timestamp())
    s = SessionLocal()
    _wiersz(s, "xyz:EUR", baza, n=40, px=1.1153)                     # próbka mid, 4 miejsca
    s.commit(); s.close()
    zapytania = []

    def transport(url, params, headers):
        zapytania.append((url, dict(params), dict(headers)))
        if params["symbol"] != "OANDA:EUR_USD":
            return 200, PUSTY_FH
        return 200, _fh_body([(sek, 1.11498, 1.11503, 1.11491, 1.11501), (sek + 60, 1.11501, None, 1.11499, 1.11502),
                              (sek + 120 + 17, 1.11502, 1.11511, 1.11500, 1.11509)])

    w = terminal.odswiez_feed(transport=transport)
    assert w["xyz:EUR"] == 2 and w["xyz:GBP"] == 0 and "xyz:JP225" not in w
    assert zapytania[0][0].endswith("/forex/candle") and zapytania[0][1]["resolution"] == "1"
    assert "token" not in zapytania[0][1] and zapytania[0][2] == {"X-Finnhub-Token": "fh-key"}   # klucz tylko w nagłówku
    assert zapytania[0][1]["to"] - zapytania[0][1]["from"] == terminal.FEED_OKNO_S
    s = SessionLocal()
    r = {x.ts: x for x in s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").all()}
    assert (r[baza].o, r[baza].h, r[baza].l, r[baza].c, r[baza].n) == (1.11498, 1.11503, 1.11491, 1.11501, terminal.FEED_N)
    assert baza + timedelta(minutes=1) not in r                        # minuta z brakiem w feedzie pominięta
    assert r[baza + timedelta(minutes=2)].c == 1.11509                  # 15:02:17 → minuta 15:02
    s.close()
    # Próbki mid tej minuty nie nadpisują feedu; nowa minuta bez feedu dalej z próbek.
    terminal._zapisz_swiece({("xyz:EUR", baza): [1.115, 1.1156, 1.115, 1.1156, 5],
                             ("xyz:EUR", baza + timedelta(minutes=3)): [1.1151, 1.1151, 1.1151, 1.1151, 5]})
    s = SessionLocal()
    r = {x.ts: x for x in s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").all()}
    assert (r[baza].h, r[baza].n) == (1.11503, terminal.FEED_N) and r[baza + timedelta(minutes=3)].n == 5
    s.close()
    # Ta sama odpowiedź drugi raz: nic do zapisania.
    assert terminal.odswiez_feed(transport=transport)["xyz:EUR"] == 0


def test_probkowanie_odswieza_feed_w_trakcie(monkeypatch):
    monkeypatch.setattr(terminal.get_settings(), "finnhub_api_key", "fh-key")
    wywolania = []
    monkeypatch.setattr(terminal, "odswiez_feed", lambda transport=None: wywolania.append(1) or {})
    monkeypatch.setattr(terminal, "FEED_CO_S", 0.01)
    _mid_sekwencja(monkeypatch, [1.1, 1.2, 1.3])
    w = terminal.probkuj_swiece(budzet_s=0.05)
    assert w["feed_runs"] >= 2 and len(wywolania) == w["feed_runs"]
    assert terminal.probkuj_swiece(budzet_s=0.01, zajmij=False, feed=False)["feed_runs"] == 0


def test_finnhub_jako_zrodlo_historii_gdy_brak_innych_kluczy(monkeypatch):
    teraz = datetime(2026, 11, 6, 14, 0)
    _hist_zrodlo(monkeypatch, finnhub_api_key="fh-key")                 # bez MetaApi i Twelve Data
    sek = int(teraz.replace(tzinfo=timezone.utc).timestamp())
    zapytania = []

    def transport(url, params, headers):
        zapytania.append((url, dict(params)))
        if params["symbol"] != "OANDA:EUR_USD" or params["to"] < sek - 60:
            return 200, PUSTY_FH
        return 200, _fh_body([(sek - 120, 1.1, 1.1, 1.1, 1.1), (sek - 60, 1.1, 1.1, 1.1, 1.1)])

    w = terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert w["source"] == "finnhub" and w["markets"]["xyz:EUR"]["rows"] == 2 and "xyz:JP225" not in w["markets"]
    assert zapytania[0][1]["to"] == sek and zapytania[0][1]["to"] - zapytania[0][1]["from"] == 7 * 24 * 60 * 60
    w2 = terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert w2["markets"]["xyz:EUR"]["done"] is True


def test_cron_odpala_ctrader_rownolegle_z_probkowaniem(monkeypatch):
    from app import ctrader_feed
    _hist_zrodlo(monkeypatch)
    wywolania = []

    async def sluchaj(budzet_s, polacz=None):
        wywolania.append(budzet_s)
        return {"ticks": 12, "rows": 3}

    monkeypatch.setattr(ctrader_feed, "skonfigurowany", lambda: True)
    monkeypatch.setattr(ctrader_feed, "sluchaj", sluchaj)
    monkeypatch.setattr(terminal, "odswiez_feed", lambda transport=None: pytest.fail("Finnhub not polled while cTrader runs"))
    monkeypatch.setattr(terminal.get_settings(), "finnhub_api_key", "fh-key")
    _mid_sekwencja(monkeypatch, [1.1, 1.2])
    w = terminal.cron_swiece(0.05)
    assert w["ctrader"] == {"ticks": 12, "rows": 3} and len(wywolania) == 1 and w["rows"] >= len(markets.SAMPLED)
