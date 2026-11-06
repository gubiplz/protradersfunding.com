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
