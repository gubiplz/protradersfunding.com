"""Trade BOT na prawdziwych cenach (`bot_ceny`): wynik z planu, a wejście
i wyjście leżą na świecach wykresu — z tymi samymi godzinami. Plan w toku
jest dla klienta niewidoczny, transakcja pojawia się po zamknięciu.

Świece są podstawione (deterministyczna fala), więc testy nie dotykają sieci.
"""
import hashlib
import math
import os
import random
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, bot_ceny, markets, terminal, tradebot  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trade, Trader  # noqa: E402

init_db()

BAZA = {"BTC": 77_000.0, "ETH": 2_270.0, "xyz:GOLD": 3_900.0, "xyz:EUR": 1.11, "xyz:GBP": 1.30,
        "xyz:JPY": 157.0, "xyz:XYZ100": 28_500.0, "xyz:CL": 62.0}
MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def _cena(coin, ms, fala=1.0):
    b = BAZA[coin]
    return b * (1 + fala * (0.012 * math.sin(ms / 3.7e6) + 0.005 * math.sin(ms / 9.1e5 + 1)
                            + 0.002 * math.sin(ms / 1.7e5 + 2)))


def _swiece_z(fala):
    def swiece(coin, interval, start_ms, end_ms):
        krok = MS[interval]
        t = start_ms - start_ms % krok
        out = []
        while t <= end_ms:
            probki = [_cena(coin, t + krok * i / 6, fala) for i in range(7)]
            out.append({"t": t, "o": probki[0], "h": max(probki), "l": min(probki), "c": probki[-1]})
            t += krok
        return out
    return swiece


@pytest.fixture
def zywe(monkeypatch):
    monkeypatch.setenv("BOT_LIVE_PRICES", "true")
    monkeypatch.setattr(tradebot, "is_weekend", lambda now=None: False)
    monkeypatch.setattr(tradebot, "market_closed_for", lambda acc, now=None: False)
    # Test jedzie po ostatnich 30 godzinach (świece 1m sięgają 3,5 dnia wstecz), a te
    # bywają weekendem — zamknięcie tygodnia nie może przycinać planów forex/indeksów.
    monkeypatch.setattr(tradebot, "_week_close", lambda now: now + timedelta(days=7))
    monkeypatch.setattr(markets, "candles", _swiece_z(1.0))
    bot_ceny._CACHE.clear()
    yield monkeypatch
    bot_ceny._CACHE.clear()


def _konto(login, **pola):
    s = SessionLocal()
    tr = Trader(email=f"{login}@botceny.test", password_hash=auth.hash_password("haslo1234"),
                full_name="Bot Ceny", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=30)
    acc = Account(login=login, trader_id=tr.id, trader_name="Bot Ceny", product_key="2step-100k",
                  initial_balance=100_000.0, steps=2, max_lots=6.0, balance=100_000.0, equity=100_000.0,
                  peak_equity=100_000.0, day_start_equity=100_000.0, day_start_balance=100_000.0,
                  started_at=start.replace(tzinfo=None), **pola)
    s.add(acc); s.commit()
    tradebot.start(s, acc, style="scalper", pace="busy")
    acc.bot_seed = int.from_bytes(hashlib.sha256(login.encode()).digest()[:4], "big") & 0x7FFFFFFF
    s.commit()
    aid, tid = acc.id, tr.id
    s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}, start


def _swieca(coin, kiedy, interval="1m"):
    ms = int(kiedy.replace(tzinfo=kiedy.tzinfo or timezone.utc).timestamp() * 1000)
    krok = MS[interval]
    return _swiece_z(1.0)(coin, interval, ms - ms % krok, ms - ms % krok)[0]


def test_transakcje_bota_na_wykresie_z_wynikiem_z_planu(zywe):
    aid, h, start = _konto("botceny1")
    s = SessionLocal()
    widziane_w_toku = 0
    with TestClient(app) as c:
        t = start
        koniec = datetime.now(timezone.utc) - timedelta(hours=1)
        while t < koniec:
            acc = s.get(Account, aid)
            snap = tradebot.tick(s, acc, t)
            acc.balance = snap.balance
            s.commit()
            otwarta = tradebot._open_trade(s, acc)
            if otwarta is not None:
                assert otwarta.venue == tradebot.PLAN
                assert snap.has_open_position and snap.volume_lots == 0 and snap.open_pnl == 0
                if widziane_w_toku < 3:            # klient planu nie widzi
                    assert c.get(f"/api/me/accounts/{aid}/positions", headers=h).json() == []
                    widziane_w_toku += 1
            t += timedelta(minutes=3)
    zamkniete = s.query(Trade).filter(Trade.account_id == aid, Trade.status == "closed").all()
    assert len(zamkniete) >= 5
    for tr in zamkniete:
        inst = tradebot.INSTRUMENTS[tr.symbol]
        m = bot_ceny.rynek(tr.symbol)
        assert m is not None and tr.venue == "hyperliquid"
        pv = tradebot._point_value(inst, tr.open_price)
        tolerancja = inst.tick * tr.lots * pv + 0.011
        assert abs(tr.pnl - tr.plan_pnl) <= tolerancja, (tr.symbol, tr.pnl, tr.plan_pnl)
        assert abs(tr.pnl) <= abs(tr.plan_pnl) + 0.011          # nigdy ponad plan
        sign = 1 if tr.side == "buy" else -1
        assert tr.pnl == pytest.approx(round(sign * (tr.close_price - tr.open_price) * tr.lots * pv, 2), abs=0.011)
        we, wy = _swieca(m.hl, tr.opened_at), _swieca(m.hl, tr.closed_at)
        assert we["l"] - inst.tick <= tr.open_price <= we["h"] + inst.tick, (tr.symbol, tr.open_price, we)
        assert wy["l"] - inst.tick <= tr.close_price <= wy["h"] + inst.tick, (tr.symbol, tr.close_price, wy)
        assert (tr.closed_at - tr.opened_at) >= timedelta(minutes=5)
    acc = s.get(Account, aid)
    assert acc.balance == pytest.approx(100_000.0 + sum(t.pnl for t in zamkniete), abs=0.05)
    s.close()


def test_stop_zdejmuje_plan_bez_sladu(zywe):
    aid, _, start = _konto("botceny2")
    s = SessionLocal()
    acc = s.get(Account, aid)
    t = start
    while tradebot._open_trade(s, acc) is None and t < start + timedelta(hours=6):
        tradebot.tick(s, acc, t); s.commit()
        t += timedelta(minutes=5)
    assert tradebot._plan_w_toku(tradebot._open_trade(s, acc))
    tradebot.stop(s, acc, t)
    assert s.query(Trade).filter(Trade.account_id == aid).count() == 0
    assert acc.balance == 100_000.0 and not acc.bot_enabled
    s.close()


def test_plan_nie_liczy_sie_do_limitu_lotow_terminala(zywe):
    aid, _, start = _konto("botceny3")
    s = SessionLocal()
    acc = s.get(Account, aid)
    t = start
    while tradebot._open_trade(s, acc) is None and t < start + timedelta(hours=6):
        tradebot.tick(s, acc, t); s.commit()
        t += timedelta(minutes=5)
    assert tradebot._plan_w_toku(tradebot._open_trade(s, acc))
    fig = terminal.account_figures(s, acc, [], {})
    assert fig["other_lots"] == 0 and fig["other_floating"] == 0
    s.close()


def test_terminal_pokazuje_transakcje_bota_a_planu_nie(zywe):
    aid, h, start = _konto("botceny4")
    s = SessionLocal()
    teraz = datetime.now(timezone.utc).replace(microsecond=0)
    s.add(Trade(account_id=aid, symbol="BTCUSD", side="buy", lots=0.3, open_price=77_000.0, close_price=77_500.0,
                pnl=150.0, plan_pnl=150.0, opened_at=teraz - timedelta(hours=3), closed_at=teraz - timedelta(hours=2),
                status="closed", source="bot", venue="hyperliquid"))
    s.add(Trade(account_id=aid, symbol="XAUUSD", side="sell", lots=0.2, open_price=2_385.0, close_price=2_380.0,
                pnl=100.0, opened_at=teraz - timedelta(hours=5), closed_at=teraz - timedelta(hours=4),
                status="closed", source="bot"))                       # stara, syntetyczna — nie w terminalu
    s.add(Trade(account_id=aid, symbol="ETHUSD", side="buy", lots=1.0, open_price=3_550.0, pnl=0.0,
                plan_pnl=80.0, plan_close_at=teraz + timedelta(hours=1), opened_at=teraz - timedelta(minutes=20),
                status="open", source="bot", venue=tradebot.PLAN))
    s.commit(); s.close()
    with TestClient(app) as c:
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
        assert st["positions"] == [] and st["account"]["other_lots"] == 0
        # Zwykła zamknięta transakcja — bez znacznika, że zrobił ją bot.
        assert [(x["symbol"], x["reason"], x["lots"]) for x in st["history"]] == [("BTCUSD", "close", 0.3)]
        assert "by" not in st["history"][0]
        assert c.get(f"/api/me/accounts/{aid}/positions", headers=h).json() == []


def test_plaski_rynek_czeka_a_potem_wieksza_pozycja(monkeypatch):
    """Bez ruchu w oknie nie da się trafić planu — `zamknij` odmawia (bot czeka)."""
    monkeypatch.setattr(markets, "candles", _swiece_z(0.0))
    bot_ceny._CACHE.clear()

    class T:
        pass
    tr = T()
    teraz = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    tr.symbol, tr.lots, tr.opened_at, tr.plan_close_at = "BTCUSD", 0.5, teraz - timedelta(hours=2), teraz
    assert not bot_ceny.zamknij(tr, tradebot.INSTRUMENTS["BTCUSD"], tradebot._point_value, 350.0,
                                teraz, 6.0, random.Random(1))
    # Z ruchem, ale za małym na 0,5 lota: większy wolumen (do limitu) trafia plan.
    # Fala 0,2 o każdej porze: szybki składnik daje w oknie ≥ ~30 w jedną stronę
    # (≤ ~12 lotów przy limicie 20), a całość ≤ ~520 — za mało na 0,5 lota (700).
    monkeypatch.setattr(markets, "candles", _swiece_z(0.2))
    bot_ceny._CACHE.clear()
    assert bot_ceny.zamknij(tr, tradebot.INSTRUMENTS["BTCUSD"], tradebot._point_value, 350.0,
                            teraz, 20.0, random.Random(1))
    assert tr.lots > 0.5 and abs(tr.pnl - 350.0) <= 0.1 * tr.lots + 0.011
    bot_ceny._CACHE.clear()


def test_koszyk_tylko_z_notowanymi_rynkami(zywe):
    p = tradebot.Persona(style="balanced", pace="steady", win_rate=0.6, avg_r=1.5, risk_pct=0.3,
                         daily_target_pct=0.5, red_day_odds=0.2, trades_per_day=6,
                         symbols=("US30", "GER40", "XAUUSD", "AUDUSD"), weights=(1, 1, 1, 1))
    syms, _ = tradebot._tradable(p, False)
    assert syms == ("XAUUSD",)
    p2 = tradebot.Persona(**{**p.__dict__, "symbols": ("US30", "GER40"), "weights": (1, 1)})
    syms2, _ = tradebot._tradable(p2, False)
    assert set(syms2) == {s for s in tradebot.INSTRUMENTS if bot_ceny.rynek(s)}
