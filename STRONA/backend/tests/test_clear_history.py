"""Czyszczenie dorobku konta — wyjscie po odpaleniu Trade BOT-a na zlym koncie.

`Stop` tego nie zalatwia: domyka pozycje po floatingu i ZOSTAWIA obnizone saldo
oraz cala historie, ktora trader widzi u siebie w portalu. Te testy pilnuja, ze
przycisk „Clear track record" naprawde zdejmuje slad — i ze nie zdejmuje przy
okazji rzeczy, ktore mogly juz wyjsc do klienta (faza, certyfikat, wyplata).
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "local")
os.environ.setdefault("AUTO_SEED", "false")

from datetime import datetime, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import tradebot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Breach, EquitySnapshot, Trade  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

START = 100_000.0


def _konto_po_bocie(login: str, *, status: str = "active", faza: str = "eval_1") -> int:
    """Konto z dorobkiem bota: saldo pod kreska, transakcje, krzywa, breach."""
    s = SessionLocal()
    acc = Account(login=login, trader_name="Clear Test", product_key="2step-100k",
                  preset="2step-100k", initial_balance=START, steps=2, status=status,
                  phase=faza, balance=START, equity=START, peak_equity=START,
                  day_start_equity=START, day_start_balance=START)
    s.add(acc); s.commit()
    tradebot.start(s, acc, style="balanced", pace="busy", target_pct=10.0)

    # Slad, ktory bot zostawia po sobie w bazie: zamknieta i otwarta pozycja,
    # odczyty krzywej, obnizone saldo i liczniki fazy.
    teraz = datetime.now(timezone.utc)
    s.add_all([
        Trade(account_id=acc.id, symbol="XAUUSD", side="buy", lots=0.5,
              open_price=2385.0, close_price=2380.0, pnl=-250.0, opened_at=teraz,
              closed_at=teraz, status="closed", source="bot"),
        Trade(account_id=acc.id, symbol="EURUSD", side="sell", lots=0.3,
              open_price=1.085, pnl=-90.0, opened_at=teraz, status="open", source="bot"),
        EquitySnapshot(account_id=acc.id, ts=teraz, balance=99_750.0, equity=99_660.0,
                       open_pnl=-90.0, day_key="2026-09-03"),
        Breach(account_id=acc.id, ts=teraz, type="daily_loss", detail="test",
               equity_at_breach=99_660.0),
    ])
    acc.balance, acc.equity, acc.open_pnl = 99_750.0, 99_660.0, -90.0
    acc.peak_equity = 100_120.0
    acc.day_key = acc.last_counted_trading_day = "2026-09-03"
    acc.day_start_equity = acc.day_start_balance = 100_000.0
    acc.best_day_profit = 120.0
    acc.trading_days_count = 2
    acc.limit_warn_daily_day = "2026-09-03"
    s.commit()
    aid = acc.id
    s.close()
    return aid


def test_konto_wraca_na_kapital_startowy_bez_sladu_handlu():
    """Sedno przycisku: ani transakcji, ani krzywej, ani obnizonego salda."""
    aid = _konto_po_bocie("clr-czysto")

    r = client.post(f"/api/admin/accounts/{aid}/clear-history", headers=ADMIN_H)
    assert r.status_code == 200
    d = r.json()
    assert (d["trades"], d["snapshots"], d["breaches"]) == (2, 1, 1)
    assert d["balance"] == START

    s = SessionLocal()
    assert s.query(Trade).filter(Trade.account_id == aid).count() == 0
    assert s.query(EquitySnapshot).filter(EquitySnapshot.account_id == aid).count() == 0
    assert s.query(Breach).filter(Breach.account_id == aid).count() == 0

    acc = s.get(Account, aid)
    assert (acc.balance, acc.equity, acc.peak_equity) == (START, START, START)
    assert acc.open_pnl == 0.0
    # Liczniki fazy tez, inaczej konto weszloby w cel z dniami handlowymi
    # wyrobionymi przez bota.
    assert (acc.trading_days_count, acc.best_day_profit) == (0, 0.0)
    assert acc.last_counted_trading_day == "" and acc.limit_warn_daily_day == ""
    # Pusty `day_key` — zostawiony wskazywalby dzien, ktorego baseline'u juz nie ma.
    assert acc.day_key == ""
    assert acc.day_start_equity == START and acc.day_start_balance == START
    s.close()


def test_bot_gasnie_razem_z_ustawieniami():
    """Bot zostawiony wlaczony zapelnilby konto od nowa na pierwszym ticku."""
    aid = _konto_po_bocie("clr-bot")
    client.post(f"/api/admin/accounts/{aid}/clear-history", headers=ADMIN_H)

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.bot_enabled is False and acc.bot_paused is False
    assert (acc.bot_style, acc.bot_pace, acc.bot_seed, acc.bot_started_at) == (None,) * 4
    assert acc.bot_target_pct == 0.0
    s.close()


def test_konto_zamkniete_na_breachu_wraca_do_gry():
    """Powod breachu znika razem z krzywa, wiec `failed` nie ma na czym stac."""
    aid = _konto_po_bocie("clr-failed", status="failed")
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.breach_reason = "Equity fell below the daily loss floor"
    acc.closed_at = datetime.now(timezone.utc)
    s.commit(); s.close()

    assert client.post(f"/api/admin/accounts/{aid}/clear-history",
                       headers=ADMIN_H).json()["status"] == "active"

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.breach_reason is None and acc.closed_at is None
    s.close()


def test_faza_zostaje_nietknieta():
    """Zaliczona faza to fakt handlowy, nie slad aktywnosci — cofa sie ja osobno."""
    aid = _konto_po_bocie("clr-faza", status="funded", faza="funded")
    client.post(f"/api/admin/accounts/{aid}/clear-history", headers=ADMIN_H)

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.phase == "funded" and acc.status == "funded"
    s.close()


def test_czyszczenie_tylko_dla_admina():
    aid = _konto_po_bocie("clr-auth")
    assert client.post(f"/api/admin/accounts/{aid}/clear-history").status_code in (401, 403)
    s = SessionLocal()
    assert s.query(Trade).filter(Trade.account_id == aid).count() == 2
    s.close()


def test_nieznane_konto_to_404():
    assert client.post("/api/admin/accounts/999999/clear-history",
                       headers=ADMIN_H).status_code == 404
