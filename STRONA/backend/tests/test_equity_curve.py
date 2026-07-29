"""Krzywa equity konta — oś pozioma to LICZBA TRANSAKCJI.

Wykres na Account Dashboardzie rysował wcześniej ostatnie 300 tyknięć pollera.
Przy koncie, które akurat nie handluje, wszystkie te tyknięcia mają identyczne
equity — wychodziła płaska kreska bez żadnej informacji, a cała historia konta
(u realnego konta: setki transakcji) była poza oknem wykresu. Te testy pilnują
nowego kontraktu: punkt 0 = kapitał startowy, każdy kolejny = stan po n-tej
zamkniętej transakcji, a ostatni punkt zgadza się z saldem konta.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import _equity_curve  # noqa: E402
from app.models import Account, EquitySnapshot, Payout, Trade  # noqa: E402

init_db()

START = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc).replace(tzinfo=None)


def _account(login: str, size: float = 50_000.0) -> Account:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Curve Tester", product_key="2step-50k",
                  initial_balance=size, steps=2, balance=size, equity=size,
                  peak_equity=size, day_start_equity=size, day_start_balance=size,
                  started_at=START)
    s.add(acc)
    s.commit()
    s.refresh(acc)
    return acc


def _trade(s, acc, pnl: float, minute: int, status: str = "closed", symbol: str = "EURUSD"):
    opened = START + timedelta(minutes=minute)
    s.add(Trade(account_id=acc.id, symbol=symbol, side="buy", lots=0.5,
                open_price=1.1, close_price=1.2, pnl=pnl, opened_at=opened,
                closed_at=(opened + timedelta(minutes=1)) if status == "closed" else None,
                status=status))


def test_krzywa_indeksowana_liczba_transakcji():
    acc = _account("curve-basic")
    s = SessionLocal()
    for i, pnl in enumerate([120.0, -45.0, 310.0]):
        _trade(s, acc, pnl, minute=10 * (i + 1))
    acc = s.get(Account, acc.id)
    acc.balance = acc.equity = 50_000.0 + 120.0 - 45.0 + 310.0
    s.commit()

    curve = _equity_curve(s, acc)

    # punkt startowy + po jednym na transakcję
    assert [p["i"] for p in curve] == [0, 1, 2, 3]
    assert [p["kind"] for p in curve] == ["start", "trade", "trade", "trade"]
    assert [p["equity"] for p in curve] == [50_000.0, 50_120.0, 50_075.0, 50_385.0]
    # ostatni punkt = realne saldo konta (kafelek Balance nie może kłamać)
    assert curve[-1]["balance"] == acc.balance
    # dane transakcji jadą do tooltipa
    assert curve[1]["symbol"] == "EURUSD" and curve[1]["pnl"] == 120.0
    s.close()


def test_wyplata_scina_krzywa_bez_zwiekszania_numeru_transakcji():
    acc = _account("curve-payout")
    s = SessionLocal()
    _trade(s, acc, 800.0, minute=10)
    _trade(s, acc, 200.0, minute=60)
    # wypłata pomiędzy transakcjami — saldo wraca do startowego
    s.add(Payout(account_id=acc.id, ts=START + timedelta(minutes=30),
                 profit_amount=800.0, trader_share=640.0, paid=True, balance_reset=True))
    acc = s.get(Account, acc.id)
    acc.balance = acc.equity = 50_200.0
    s.commit()

    curve = _equity_curve(s, acc)

    kinds = [p["kind"] for p in curve]
    assert kinds == ["start", "trade", "payout", "trade"]
    payout = curve[2]
    # wypłata nie jest transakcją: dzieli oś X z poprzednim punktem
    assert payout["i"] == curve[1]["i"] == 1
    assert payout["payout"] == 800.0 and payout["equity"] == 50_000.0
    assert curve[-1]["balance"] == acc.balance == 50_200.0
    s.close()


def test_wyplata_bez_zdjecia_zysku_nie_scina_krzywej():
    """Admin może zapisać wypłatę bez resetu salda — wtedy krzywa nie spada."""
    acc = _account("curve-payout-noreset")
    s = SessionLocal()
    _trade(s, acc, 500.0, minute=10)
    s.add(Payout(account_id=acc.id, ts=START + timedelta(minutes=20),
                 profit_amount=500.0, trader_share=400.0, paid=True, balance_reset=False))
    acc = s.get(Account, acc.id)
    acc.balance = acc.equity = 50_500.0
    s.commit()

    curve = _equity_curve(s, acc)

    assert [p["kind"] for p in curve] == ["start", "trade"]
    assert curve[-1]["balance"] == acc.balance == 50_500.0
    s.close()


def test_otwarta_pozycja_doklada_punkt_z_floatingiem():
    acc = _account("curve-open")
    s = SessionLocal()
    _trade(s, acc, 100.0, minute=10)
    _trade(s, acc, -60.0, minute=30, status="open")
    acc = s.get(Account, acc.id)
    acc.balance = 50_100.0
    acc.equity = 50_040.0
    s.commit()

    curve = _equity_curve(s, acc)

    last = curve[-1]
    assert last["kind"] == "open"
    # tylko tutaj equity odkleja się od salda — o floating otwartej pozycji
    assert last["balance"] == 50_100.0 and last["equity"] == 50_040.0
    assert last["i"] == curve[-2]["i"] + 1
    s.close()


def test_konto_bez_transakcji_probkuje_cala_historie_snapshotow():
    """Regresja: liczyła się tylko ostatnia setka tyknięć → płaska kreska."""
    acc = _account("curve-snapshots")
    s = SessionLocal()
    # najpierw konto rośnie i spada, potem 400 tyknięć w bezruchu
    for i in range(400):
        eq = 50_000.0 + (i * 5 if i < 100 else 500.0)
        s.add(EquitySnapshot(account_id=acc.id, ts=START + timedelta(seconds=30 * i),
                             balance=eq, equity=eq))
    for i in range(400):
        s.add(EquitySnapshot(account_id=acc.id, ts=START + timedelta(seconds=30 * (400 + i)),
                             balance=50_500.0, equity=50_500.0))
    s.commit()
    acc = s.get(Account, acc.id)

    curve = _equity_curve(s, acc)

    assert curve and {p["kind"] for p in curve} == {"tick"}
    vals = [p["equity"] for p in curve]
    # cała historia, nie tylko martwy ogon: widać start konta i wzrost
    assert min(vals) < 50_100.0 < max(vals)
    assert curve[-1]["equity"] == 50_500.0
    # próbkowanie trzyma wykres w rozsądnym rozmiarze
    assert len(curve) < 800
    s.close()
