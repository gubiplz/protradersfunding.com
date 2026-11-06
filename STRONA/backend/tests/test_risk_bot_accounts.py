"""Cron ryzyka co minutę obejmuje konta Trade BOT-a.

Konto bota na lokalnych poświadczeniach nie ma `metaapi_account_id`, więc do tej
pory ruszało się tylko dobowym tickiem: plany zamykały się godzinami po czasie,
a baseline dnia stawał o godzinie crona zamiast o północy. Drugi test pilnuje
stempla przeglądu: bez niego dwa równoległe przebiegi (dobowy tick i cron ryzyka
mają osobne zamki) mogłyby otworzyć dwa plany, bo samo otwarcie planu nic na
koncie nie zmienia i `row_version` nie miałby czego porównać.
"""
import asyncio
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app import poller, provisioning, tradebot  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.feed import MarketSnapshot, NullFeed  # noqa: E402
from app.models import Account, AppSetting  # noqa: E402

init_db()


def _konto(login, **pola):
    s = SessionLocal()
    a = Account(login=login, trader_name="Bot", product_key="2step-50k", preset="2step-50k",
                initial_balance=50_000.0, steps=2, phase="eval_1", status="active",
                balance=50_000.0, equity=50_000.0, peak_equity=50_000.0,
                day_start_equity=50_000.0, day_start_balance=50_000.0, **pola)
    s.add(a); s.commit()
    aid = a.id
    s.close()
    return aid


def _cisza(monkeypatch):
    async def nic(*a, **k):
        return None
    monkeypatch.setattr(poller, "dokoncz_odciecia", nic)
    monkeypatch.setattr(provisioning, "provision_pending", nic)
    monkeypatch.setattr(provisioning, "dopnij_brakujace_rejestracje", nic)
    s = SessionLocal()
    for k in (poller.RISK_CURSOR_KEY, poller.RISK_TICK_KEY):
        row = s.get(AppSetting, k)
        if row:
            s.delete(row)
    s.commit(); s.close()


def _archiwizuj(*ids):
    """Baza testowa jest wspólna dla plików; konta z tego pliku nie mogą zostać
    aktywne dla testów, które liczą konta w cronie (test_risk_rotation)."""
    s = SessionLocal()
    s.query(Account).filter(Account.id.in_(ids)).update({Account.status: "archived"}, synchronize_session=False)
    s.commit(); s.close()


def test_cron_ryzyka_liczy_konta_bota_bez_metaapi(monkeypatch):
    _cisza(monkeypatch)
    bot = _konto("94000001", bot_enabled=True)
    zwykle = _konto("94000002")                       # lokalne poświadczenia, bez bota: nic się samo nie rusza
    realne = _konto("94000003", metaapi_account_id="meta-94000003")
    widziane = []

    async def licz(session, acc, feed):
        widziane.append(acc.id)
    monkeypatch.setattr(poller, "process_account", licz)
    monkeypatch.setattr(poller, "RISK_BUDZET_S", 40.0)
    try:
        asyncio.run(poller.tick_ryzyka(min_odstep_s=0))
        assert bot in widziane and realne in widziane
        assert zwykle not in widziane
    finally:
        _archiwizuj(bot, zwykle, realne)


def test_przebieg_bota_zostawia_stempel_przegladu(monkeypatch):
    """`process_account` na koncie bota zawsze pisze `terminal_eval_at`, więc każdy
    przebieg robi UPDATE na `accounts` i wersja wiersza łapie równoległy przebieg."""
    aid = _konto("94000010", bot_enabled=True)
    monkeypatch.setattr(tradebot, "tick", lambda session, acc, now=None: MarketSnapshot(50_000.0, 50_000.0, 0.0, False, 0.0, True))
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.terminal_eval_at is None
    asyncio.run(poller.process_account(s, acc, NullFeed()))
    s.commit()
    pierwszy = acc.terminal_eval_at
    assert pierwszy is not None
    asyncio.run(poller.process_account(s, acc, NullFeed()))
    s.commit()
    assert acc.terminal_eval_at >= pierwszy
    s.close()
    _archiwizuj(aid)
