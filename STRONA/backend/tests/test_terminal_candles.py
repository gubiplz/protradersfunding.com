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


@pytest.fixture(autouse=True)
def _czysto(monkeypatch):
    monkeypatch.setattr(terminal, "PROBKA_S", 0.001)
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
    for k, v in {"metaapi_token": "", "metaapi_candles_account_id": "", "twelvedata_api_key": "",
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


def test_bez_zrodla_historii_cron_tylko_probkuje(monkeypatch):
    _hist_zrodlo(monkeypatch)
    assert terminal.uzupelnij_historie(1.0)["skipped"] is True
    _mid_sekwencja(monkeypatch, [1.1, 1.2])
    w = terminal.cron_swiece(0.02)
    assert w["rows"] == len(markets.SAMPLED) and w["history"]["skipped"] is True


def test_twelve_data_jako_zrodlo_historii(monkeypatch):
    teraz = datetime(2026, 11, 6, 14, 0)
    _hist_zrodlo(monkeypatch, twelvedata_api_key="td-key", candles_history_symbols="xyz:JP225=JP225")
    zapytania = []

    def transport(url, params, headers):
        zapytania.append((url, dict(params)))
        assert url == "https://api.twelvedata.com/time_series" and headers == {}
        if params["symbol"] == "JP225":
            return 200, {"status": "error", "code": 400, "message": "not available"}
        return 200, {"status": "ok", "values": [
            {"datetime": "2026-11-06 13:59:00", "open": "1.1500", "high": "1.1510", "low": "1.1490", "close": "1.1505"},
            {"datetime": "2026-11-06 13:58:00", "open": "1.1490", "high": "1.1502", "low": "1.1480", "close": "1.1500"}]}

    w = terminal.uzupelnij_historie(5.0, transport=transport, teraz=teraz)
    assert w["source"] == "twelvedata" and w["markets"]["xyz:EUR"]["rows"] == 2
    assert w["markets"]["xyz:JP225"]["rows"] == 0 and w["markets"]["xyz:JP225"]["done"] is False
    q = {p["symbol"]: p for _, p in zapytania}
    assert q["EUR/USD"]["interval"] == "1min" and q["EUR/USD"]["outputsize"] == 5000 and q["EUR/USD"]["apikey"] == "td-key"
    assert "end_date" not in q["EUR/USD"] and q["USD/JPY"]["timezone"] == "UTC"
    s = SessionLocal()
    r = s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").order_by(TerminalCandle.ts).all()
    assert [(x.ts.minute, x.c, x.n) for x in r] == [(58, 1.15, 0), (59, 1.1505, 0)]
    s.close()
