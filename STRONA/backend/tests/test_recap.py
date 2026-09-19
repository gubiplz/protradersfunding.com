"""Podsumowanie zakończonej fazy — `/api/me/accounts/{id}/recap`.

Konto po złamaniu reguły dostawało jednego maila i ciszę. Recap zestawia mu
liczby z tej fazy, więc musi liczyć z OKNA fazy, a nie z liczników na koncie:
awans zeruje saldo, best day i dni handlowe, a transakcje z obu faz leżą
w jednej tabeli. Tu pilnujemy, żeby okno faktycznie odcinało cudzą historię.
"""
import os
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.NamedTemporaryFile(
    suffix=".db", delete=False).name)
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, push  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Breach, EquitySnapshot, Trade, Trader  # noqa: E402

init_db()
client = TestClient(app)

TERAZ = datetime.utcnow().replace(microsecond=0)


def _trader(email: str) -> int:
    s = SessionLocal()
    # Kod polecający jest UNIQUE, a bazę dzielimy z resztą zestawu — kod
    # wyprowadzony z maila zderzał się z traderem z innego pliku testów.
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Recap Tester", referral_code=uuid4().hex[:10].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _konto(tid: int, login: str, **kw) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Recap Tester",
                  platform_login=login, platform_password="x",
                  platform_server="MetaQuotes-Demo", product_key="2step-25k",
                  initial_balance=25_000.0, balance=25_000.0, equity=25_000.0,
                  peak_equity=25_000.0, day_start_equity=25_000.0,
                  day_start_balance=25_000.0,
                  steps=2, profit_target_p1=8.0, profit_target_p2=5.0,
                  max_daily_loss_pct=5.0, max_overall_loss_pct=10.0,
                  min_trading_days=4,
                  **{"status": "active", "phase": "eval_1", **kw})
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _trade(aid: int, kiedy: datetime, pnl: float) -> None:
    s = SessionLocal()
    s.add(Trade(account_id=aid, symbol="EURUSD", side="buy", lots=1.0,
                open_price=1.1, close_price=1.1, pnl=pnl, status="closed",
                opened_at=kiedy, closed_at=kiedy))
    s.commit(); s.close()


def _get(tid: int, aid: int) -> dict:
    r = client.get(f"/api/me/accounts/{aid}/recap",
                   headers={"Authorization": f"Bearer {auth.make_token(tid)}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_konto_w_grze_nie_ma_czego_podsumowac():
    tid = _trader("grajace@example.com")
    aid = _konto(tid, "recap-live")
    assert _get(tid, aid)["available"] is False


def test_oblane_konto_dostaje_liczby_i_powod_upadku():
    tid = _trader("oblane@example.com")
    start = TERAZ - timedelta(days=10)
    aid = _konto(tid, "recap-fail", status="failed", phase="eval_1",
                 phase_started_at=start, closed_at=TERAZ,
                 breach_reason="Daily loss limit exceeded")
    # Dwa dni na plus, trzeci zabiera więcej, niż dały poprzednie.
    _trade(aid, start + timedelta(days=1), 400.0)
    _trade(aid, start + timedelta(days=2), 300.0)
    _trade(aid, start + timedelta(days=3), -1_800.0)
    s = SessionLocal()
    s.add(Breach(account_id=aid, ts=TERAZ, type="daily_loss",
                 detail="Equity below daily floor", equity_at_breach=23_900.0))
    s.commit(); s.close()

    d = _get(tid, aid)
    assert d["outcome"] == "failed" and d["phase"] == "eval_1"
    # Dni kalendarzowe po datach, nie po różnicy godzin — okno 10 dni ma ich 11.
    assert d["days_active"] == 11
    assert d["trading_days"] == 3 and d["green_days"] == 2 and d["red_days"] == 1
    assert d["net_pnl"] == -1_100.0
    assert d["best_day"]["pnl"] == 400.0 and d["worst_day"]["pnl"] == -1_800.0
    assert d["target_balance"] == 27_000.0
    assert d["breach"]["type"] == "daily_loss"
    # Jedna obserwacja, nie lista: dzień, który zabrał więcej, niż dały dwa dobre.
    assert d["diagnosis"]["kind"] == "worst_vs_best"
    assert "4.5x your best day" in d["diagnosis"]["text"]
    assert "prawie" not in d["diagnosis"]["text"].lower()


def test_zdana_faza_liczy_swoje_okno_bez_transakcji_nastepnej():
    tid = _trader("zdane@example.com")
    p1 = TERAZ - timedelta(days=20)
    p2 = TERAZ - timedelta(days=5)
    aid = _konto(tid, "recap-pass", phase="eval_2",
                 prev_phase_started_at=p1, phase_started_at=p2)
    _trade(aid, p1 + timedelta(days=2), 1_200.0)
    _trade(aid, p1 + timedelta(days=3), 800.0)
    _trade(aid, p2 + timedelta(days=1), 999.0)   # to już druga faza

    d = _get(tid, aid)
    assert d["outcome"] == "passed" and d["phase"] == "eval_1"
    assert d["trades"] == 2 and d["net_pnl"] == 2_000.0
    assert d["breach"] is None


def test_konto_z_mt5_liczy_ze_snapshotow_bo_nie_ma_transakcji():
    # Feed MT5 oddaje samo equity — takie konto nie ma ANI JEDNEGO wiersza
    # w `trades`, a mimo to musi dostać podsumowanie.
    tid = _trader("zfeeda@example.com")
    start = TERAZ - timedelta(days=6)
    aid = _konto(tid, "recap-mt5", status="failed", phase="eval_1",
                 phase_started_at=start, closed_at=TERAZ)
    s = SessionLocal()
    for nr, (otw, zam) in enumerate([(25_000.0, 25_600.0), (25_600.0, 25_100.0)]):
        dzien = start + timedelta(days=nr + 1)
        klucz = dzien.strftime("%Y-%m-%d")
        for ts, bal in ((dzien, otw), (dzien + timedelta(hours=6), zam)):
            s.add(EquitySnapshot(account_id=aid, ts=ts, balance=bal, equity=bal,
                                 day_key=klucz))
    s.commit(); s.close()

    d = _get(tid, aid)
    assert d["trades"] == 0 and d["win_rate"] is None
    assert d["trading_days"] == 2 and d["green_days"] == 1 and d["red_days"] == 1
    assert d["net_pnl"] == 100.0
    assert d["best_day"]["pnl"] == 600.0 and d["worst_day"]["pnl"] == -500.0


def test_cudze_konto_nie_oddaje_podsumowania():
    tid = _trader("wlasciciel@example.com")
    obcy = _trader("obcy@example.com")
    aid = _konto(tid, "recap-obce", status="failed", closed_at=TERAZ)
    r = client.get(f"/api/me/accounts/{aid}/recap",
                   headers={"Authorization": f"Bearer {auth.make_token(obcy)}"})
    assert r.status_code == 404


def test_powiadomienie_o_koncu_fazy_prowadzi_do_podsumowania():
    # Bez id konta nie ma dokąd prowadzić — link wraca na listę kont.
    assert push.event_url("breached", {"account_id": 7}) == "/portal?view=recap&acc=7"
    assert push.event_url("phase_passed", {"account_id": 7}) == "/portal?view=recap&acc=7"
    assert push.event_url("breached") == "/portal?view=accounts"
    assert push.event_url("payout_approved", {"account_id": 7}) == "/portal?view=payouts"
