"""Ostrzeżenie o zbliżaniu się do limitu (80% dziennej straty / max DD).

Raz dziennie na typ limitu — strażnik `limit_warn_*_day` na koncie. Kanał:
push + centrum powiadomień (jak daily_recap), świadomie BEZ maila. Preferencja
`notify_trading` wyłącza także to zdarzenie.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_limit_warning.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app import auth, push  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Account, Notification, Trader  # noqa: E402
from app.poller import _limit_warnings_due  # noqa: E402

init_db()

DZIS = "2026-08-15"
JUTRO = "2026-08-16"


def _konto():
    return Account(login="912345678", trader_id=1, trader_name="Limit Tester",
                   product_key="2step-100k", initial_balance=100_000, steps=2,
                   profit_split_pct=90, status="active", phase="phase1",
                   balance=96_000, equity=96_000)


def test_ostrzega_raz_dziennie():
    acc = _konto()
    tytuly = _limit_warnings_due(acc, {"daily_loss_used_pct": 85.0}, DZIS)
    assert tytuly == ["912345678: 85% of daily loss limit used"]
    assert acc.limit_warn_daily_day == DZIS
    assert _limit_warnings_due(acc, {"daily_loss_used_pct": 91.0}, DZIS) == []


def test_nowy_dzien_ostrzega_znow():
    acc = _konto()
    _limit_warnings_due(acc, {"daily_loss_used_pct": 85.0}, DZIS)
    tytuly = _limit_warnings_due(acc, {"daily_loss_used_pct": 82.0}, JUTRO)
    assert len(tytuly) == 1 and acc.limit_warn_daily_day == JUTRO


def test_dd_ma_wlasny_straznik():
    acc = _konto()
    tytuly = _limit_warnings_due(
        acc, {"daily_loss_used_pct": 85.0, "overall_dd_used_pct": 92.0}, DZIS)
    assert len(tytuly) == 2
    assert "max drawdown" in tytuly[1]
    assert acc.limit_warn_dd_day == DZIS
    # dzienny strażnik ustawiony nie wycisza drawdownu następnego dnia
    tytuly = _limit_warnings_due(acc, {"overall_dd_used_pct": 95.0}, JUTRO)
    assert tytuly == ["912345678: 95% of max drawdown used"]


def test_ponizej_progu_cisza():
    acc = _konto()
    assert _limit_warnings_due(
        acc, {"daily_loss_used_pct": 79.9, "overall_dd_used_pct": 50.0}, DZIS) == []
    assert acc.limit_warn_daily_day is None


def _trader(email, notify_trading=True):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Limit Tester", referral_code=email.split("@")[0].upper(),
                notify_trading=notify_trading)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def test_send_event_tworzy_wpis_w_centrum():
    tid = _trader("limit-centrum@test.pl")
    push.send_event("limit_warning", "limit-centrum@test.pl",
                    "912345678: 85% of daily loss limit used")
    s = SessionLocal()
    wpis = (s.query(Notification)
            .filter(Notification.trader_id == tid,
                    Notification.event == "limit_warning").one())
    assert "85% of daily loss limit" in wpis.title
    s.close()


def test_pref_notify_trading_off_wycisza():
    tid = _trader("limit-cisza@test.pl", notify_trading=False)
    wyslane = push.send_event("limit_warning", "limit-cisza@test.pl",
                              "912345678: 85% of daily loss limit used")
    assert wyslane == 0
    s = SessionLocal()
    assert s.query(Notification).filter(Notification.trader_id == tid).count() == 0
    s.close()
