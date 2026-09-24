"""Cel bota to punkt docelowy W OBIE STRONY.

2026-09-24, prośba właściciela: cel ujemny (np. −2%) albo niższy od obecnego
wyniku (konto na +6%, nowy cel +3%) ma sprowadzić konto na ten poziom tak, jak
wygląda gorszy okres u tradera — trochę zysku, potem większy spadek — i stanąć
DOKŁADNIE na nim. Dawniej ujemny cel był odrzucany, a konto nad sufitem stało
bezczynnie („cap overshot").
"""
import asyncio
import hashlib
import os
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import poller, tradebot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Breach, Trade  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
START = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)   # poniedziałek, rynek otwarty
SIZE = 100_000.0


class Zegar:
    def __init__(self, start: datetime):
        self.teraz = start


@contextmanager
def _czas(zegar: Zegar):
    """Ten sam sterowany zegar co w testach doomu — zejście trwa dniami."""
    org_tick, org_poll = tradebot.tick, poller.server_now
    tradebot.tick = lambda s, a, now=None: org_tick(s, a, now=now or zegar.teraz)
    poller.server_now = lambda: zegar.teraz
    try:
        yield
    finally:
        tradebot.tick, poller.server_now = org_tick, org_poll


class NullFeed:
    async def snapshot(self, *a, **kw):
        raise AssertionError("poller poszedł po feed dla konta botowego")


def _konto(login: str, *, cel: float, wynik_pct: float = 0.0) -> int:
    s = SessionLocal()
    saldo = SIZE * (1 + wynik_pct / 100.0)
    acc = Account(login=login, trader_name="Descent", product_key="2step-100k",
                  preset="2step-100k", initial_balance=SIZE, steps=2,
                  status="active", phase="eval_1", profit_target_p1=8.0,
                  max_daily_loss_pct=5.0, max_overall_loss_pct=10.0,
                  min_trading_days=4, max_lots=6.0,
                  balance=saldo, equity=saldo, peak_equity=max(SIZE, saldo),
                  day_start_equity=saldo, day_start_balance=saldo,
                  started_at=START.replace(tzinfo=None))
    s.add(acc); s.commit()
    tradebot.start(s, acc, pace="busy", target_pct=cel)
    acc.bot_seed = int.from_bytes(hashlib.sha256(login.encode()).digest()[:4], "big") & 0x7FFFFFFF
    s.commit()
    aid = acc.id
    s.close()
    return aid


def _jedz(account_id: int, cel_equity: float, maks_tickow: int = 4000, step_sec: int = 600):
    """Kręć pollerem, aż saldo stanie na celu i nie będzie otwartej pozycji."""
    zegar = Zegar(START)
    s = SessionLocal()
    acc = s.get(Account, account_id)
    min_saldo = float(acc.balance)
    with _czas(zegar):
        for _ in range(maks_tickow):
            asyncio.run(poller.process_account(s, acc, NullFeed()))
            min_saldo = min(min_saldo, float(acc.balance or 0.0))
            otwarta = tradebot._open_trade(s, acc) is not None
            if not otwarta and abs(float(acc.balance) - cel_equity) <= tradebot.MIN_FILL:
                break
            zegar.teraz += timedelta(seconds=step_sec)
    s.refresh(acc)
    wyniki_dni = defaultdict(float)
    for t in s.query(Trade).filter(Trade.account_id == account_id, Trade.status == "closed"):
        wyniki_dni[t.closed_at.strftime("%Y-%m-%d")] += t.pnl
    breache = s.query(Breach).filter(Breach.account_id == account_id).count()
    s.close()
    return acc, min_saldo, dict(wyniki_dni), breache, zegar.teraz


def test_ujemny_cel_schodzi_nieregularnie_i_staje_dokladnie_na_nim():
    aid = _konto("desc-minus2", cel=-2.0)
    acc, min_saldo, dni, breache, koniec = _jedz(aid, SIZE * 0.98)
    assert abs(acc.balance - SIZE * 0.98) <= tradebot.MIN_FILL, acc.balance
    assert min_saldo >= SIZE * 0.98 - tradebot.MIN_FILL          # nigdy pod celem
    assert acc.status == "active" and breache == 0
    # nieregularnie: są zielone dni, a czerwone są większe od zielonych
    zielone = [v for v in dni.values() if v > 0]
    czerwone = [v for v in dni.values() if v < 0]
    assert zielone and czerwone
    assert max(abs(v) for v in czerwone) > max(zielone)
    # umiarkowanie: −2% to nie jedna sesja, żaden dzień nie zjada więcej niż 1,5%
    assert len(dni) >= 3 and min(czerwone) > -SIZE * 0.015
    # po dojściu bot stoi
    s = SessionLocal()
    a = s.get(Account, aid)
    assert tradebot._should_open(s, a, tradebot.persona_for(a), a.balance, koniec) is False
    s.close()


def test_nizszy_cel_niz_obecny_wynik_sprowadza_konto_w_dol():
    """Konto na +6%, nowy cel +3% → schodzi do +3%, zamiast stać nad sufitem."""
    aid = _konto("desc-6to3", cel=3.0, wynik_pct=6.0)
    acc, min_saldo, dni, breache, _ = _jedz(aid, SIZE * 1.03)
    assert abs(acc.balance - SIZE * 1.03) <= tradebot.MIN_FILL, acc.balance
    assert min_saldo >= SIZE * 1.03 - tradebot.MIN_FILL
    assert breache == 0 and any(v < 0 for v in dni.values())


def test_api_przyjmuje_ujemny_cel_ale_nie_pod_podloga():
    aid = _konto("desc-api", cel=5.0)
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": -2})
    assert r.status_code == 200 and r.json()["bot_target_pct"] == -2.0
    o = r.json()["bot_outcome"]
    assert o["descent_equity"] == SIZE * 0.98 and o["min_target_pct"] == -9.5
    # pół punktu nad podłogą całkowitą 10% — niżej to już złamanie limitu
    assert client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                        json={"target_pct": -9.5}).status_code == 400
    assert client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                        json={"target_pct": -9.4}).status_code == 200
