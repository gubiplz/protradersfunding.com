"""Statystyki konta dla widoku Analytics: /api/me/accounts/{id}/stats.

Endpoint liczy ze WSZYSTKICH zamkniętych transakcji (księga w /activity jest
przycięta do LEDGER_MAX, więc liczenie w przeglądarce kłamałoby na kontach
z dłuższą historią). Kubełki godzinowe są w czasie serwera (UTC).
"""
import os
import tempfile
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_account_stats.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import LEDGER_MAX, app  # noqa: E402
from app.models import Account, Trade, Trader  # noqa: E402

init_db()
client = TestClient(app)


def _trader(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Stats Tester", referral_code=email.split("@")[0].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _konto(tid, login):
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Stats Tester",
                  platform_login=login, platform_password="x",
                  platform_server="MetaQuotes-Demo", product_key="2step-25k",
                  initial_balance=25_000.0, balance=25_000.0, equity=25_000.0,
                  peak_equity=25_000.0, day_start_equity=25_000.0,
                  day_start_balance=25_000.0, status="funded", phase="funded")
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _trade(aid, symbol, side, pnl, opened, closed):
    return Trade(account_id=aid, symbol=symbol, side=side, lots=0.5,
                 open_price=100.0, close_price=101.0, pnl=pnl, status="closed",
                 opened_at=opened, closed_at=closed)


def _seed_znany_zestaw(aid):
    """6 transakcji o znanych z góry agregatach. 2026-08-03 to poniedziałek."""
    s = SessionLocal()
    s.add_all([
        _trade(aid, "XAUUSD", "buy", 300.0,
               datetime(2026, 8, 3, 9, 0), datetime(2026, 8, 3, 10, 0)),
        _trade(aid, "XAUUSD", "buy", -100.0,
               datetime(2026, 8, 3, 12, 0), datetime(2026, 8, 3, 12, 30)),
        _trade(aid, "EURUSD", "sell", 200.0,
               datetime(2026, 8, 4, 14, 0), datetime(2026, 8, 4, 15, 0)),
        _trade(aid, "EURUSD", "sell", -50.0,
               datetime(2026, 8, 5, 9, 0), datetime(2026, 8, 5, 9, 15)),
        _trade(aid, "US100", "buy", 130.0,
               datetime(2026, 8, 5, 16, 0), datetime(2026, 8, 5, 17, 0)),
        _trade(aid, "XAUUSD", "sell", 80.0,
               datetime(2026, 8, 6, 10, 0), datetime(2026, 8, 6, 11, 0)),
    ])
    # otwarta pozycja NIE wchodzi do statystyk
    s.add(Trade(account_id=aid, symbol="XAUUSD", side="buy", lots=1.0,
                open_price=100.0, pnl=999.0, status="open",
                opened_at=datetime(2026, 8, 6, 12, 0)))
    s.commit(); s.close()


def test_znane_wartosci():
    tid, h = _trader("stats-znane@test.pl")
    aid = _konto(tid, "700100")
    _seed_znany_zestaw(aid)
    st = client.get(f"/api/me/accounts/{aid}/stats", headers=h).json()
    assert st["trades"] == 6 and st["wins"] == 4 and st["losses"] == 2
    assert st["win_rate"] == 66.7
    assert st["net_pnl"] == 560.0
    assert st["gross_profit"] == 710.0 and st["gross_loss"] == -150.0
    assert st["profit_factor"] == 4.73
    assert st["avg_win"] == 177.5 and st["avg_loss"] == -75.0
    assert st["expectancy"] == 93.33
    # 3600+1800+3600+900+3600+3600 = 17100 s / 6
    assert st["avg_duration_sec"] == 2850
    assert st["best_trade"] == {"symbol": "XAUUSD", "pnl": 300.0}
    assert st["worst_trade"] == {"symbol": "XAUUSD", "pnl": -100.0}
    # od końca: +80, +130, potem strata przerywa serię
    assert st["streak"] == 2
    assert st["long"] == {"trades": 3, "wins": 2, "pnl": 330.0}
    assert st["short"] == {"trades": 3, "wins": 2, "pnl": 230.0}


def test_kubelki_symbol_dzien_godzina():
    tid, h = _trader("stats-kubelki@test.pl")
    aid = _konto(tid, "700101")
    _seed_znany_zestaw(aid)
    st = client.get(f"/api/me/accounts/{aid}/stats", headers=h).json()

    assert [r["symbol"] for r in st["by_symbol"]] == ["XAUUSD", "EURUSD", "US100"]
    xau = st["by_symbol"][0]
    assert xau["trades"] == 3 and xau["wins"] == 2 and xau["pnl"] == 280.0
    assert xau["win_rate"] == 66.7
    assert st["by_symbol"][2]["win_rate"] == 100.0

    # zawsze 7 dni (0 = poniedziałek) i 24 godziny, także puste
    assert len(st["by_weekday"]) == 7 and len(st["by_hour"]) == 24
    assert st["by_weekday"][0] == {"trades": 2, "wins": 1, "pnl": 200.0}  # pon
    assert st["by_weekday"][2] == {"trades": 2, "wins": 1, "pnl": 80.0}   # śr
    assert st["by_weekday"][5] == {"trades": 0, "wins": 0, "pnl": 0.0}    # sob
    assert st["by_hour"][10] == {"trades": 1, "wins": 1, "pnl": 300.0}
    assert st["by_hour"][12] == {"trades": 1, "wins": 0, "pnl": -100.0}
    assert st["by_hour"][3] == {"trades": 0, "wins": 0, "pnl": 0.0}


def test_liczy_ponad_sufit_ledgera():
    """Księga w /activity tnie do LEDGER_MAX — statystyki widzą całość."""
    tid, h = _trader("stats-310@test.pl")
    aid = _konto(tid, "700102")
    s = SessionLocal()
    for i in range(310):
        s.add(_trade(aid, "EURUSD", "buy", 1.0,
                     datetime(2026, 7, 1, 8, 0), datetime(2026, 7, 1, 9, 0)))
    s.commit(); s.close()

    ledger = client.get(f"/api/me/accounts/{aid}/activity", headers=h).json()["ledger"]
    assert len(ledger) == LEDGER_MAX  # sufit księgi nadal działa

    st = client.get(f"/api/me/accounts/{aid}/stats", headers=h).json()
    assert st["trades"] == 310 and st["wins"] == 310
    assert st["net_pnl"] == 310.0
    assert st["profit_factor"] is None  # zero strat → brak dzielnika


def test_puste_konto_i_cudzy_dostep():
    tid, h = _trader("stats-pusty@test.pl")
    aid = _konto(tid, "700103")
    assert client.get(f"/api/me/accounts/{aid}/stats", headers=h).json() == {"trades": 0}

    _, h_obcy = _trader("stats-obcy@test.pl")
    assert client.get(f"/api/me/accounts/{aid}/stats", headers=h_obcy).status_code == 404
    assert client.get(f"/api/me/accounts/{aid}/stats").status_code in (401, 403)
