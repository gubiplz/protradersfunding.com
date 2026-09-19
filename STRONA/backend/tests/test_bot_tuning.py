"""Ręczne nadpisanie charakteru bota — per konto, puste pole = wartość z ziarna.

Persona konta jest losowana z `bot_seed`, żeby dwa konta nie handlowały tak samo.
Admin może teraz podmienić pojedyncze liczby na jednym koncie, nie ruszając
reszty ani nie zmieniając ziarna.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import tradebot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
START = 100_000.0


def _konto(login: str) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Tune", product_key="2step-100k",
                  preset="2step-100k", initial_balance=START, steps=2, status="active",
                  phase="eval_1", balance=START, equity=START, peak_equity=START,
                  day_start_equity=START, day_start_balance=START)
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


def test_puste_kolumny_to_dokladnie_persona_z_ziarna():
    aid = _konto("bt-czysto")
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert tradebot.persona_for(acc) == tradebot.auto_persona(acc)
    s.close()


def test_nadpisanie_wchodzi_tylko_w_swoje_pole():
    aid = _konto("bt-jedno")
    s = SessionLocal()
    acc = s.get(Account, aid)
    auto = tradebot.auto_persona(acc)
    acc.bot_win_rate = 0.9
    s.commit()
    p = tradebot.persona_for(acc)
    assert p.win_rate == 0.9
    # reszta charakteru bez zmian — ziarno dalej rządzi
    assert (p.avg_r, p.risk_pct, p.daily_target_pct, p.red_day_odds, p.symbols) == \
           (auto.avg_r, auto.risk_pct, auto.daily_target_pct, auto.red_day_odds, auto.symbols)
    s.close()


def test_instrumenty_z_panelu_wypieraja_wylosowane():
    aid = _konto("bt-symbole")
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.bot_symbols = "xauusd, NAS100, NIEISTNIEJE"
    s.commit()
    p = tradebot.persona_for(acc)
    assert p.symbols == ("XAUUSD", "NAS100")
    assert p.weights == (1.0, 1.0)
    s.close()


def test_swing_steruje_amplituda_floatingu():
    """Ten sam plan, inny swing => inne equity po drodze, ten sam wynik na końcu."""
    aid = _konto("bt-swing")
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert tradebot.swing_for(acc) == tradebot.SWING_DOMYSLNY
    acc.bot_swing = 0.0
    s.commit()
    assert tradebot.swing_for(acc) == 0.0
    s.close()


def test_api_zapisuje_czysci_i_pilnuje_widelek():
    aid = _konto("bt-api")
    r = client.get(f"/api/admin/accounts/{aid}/bot/tuning", headers=ADMIN_H)
    assert r.status_code == 200
    assert r.json()["override"]["win_rate"] is None
    assert 0 < r.json()["auto"]["win_rate"] < 1

    r = client.post(f"/api/admin/accounts/{aid}/bot/tuning", headers=ADMIN_H,
                    json={"win_rate": 0.7, "risk_pct": 0.25, "symbols": "BTCUSD"})
    assert r.status_code == 200
    assert r.json()["override"] == {"win_rate": 0.7, "avg_r": None, "risk_pct": 0.25,
                                    "daily_target_pct": None, "red_day_odds": None,
                                    "swing": None, "symbols": "BTCUSD"}

    # pusty formularz = powrót do ziarna, nie „bez zmian"
    r = client.post(f"/api/admin/accounts/{aid}/bot/tuning", headers=ADMIN_H, json={})
    assert r.json()["override"]["win_rate"] is None
    assert r.json()["override"]["symbols"] == ""

    assert client.post(f"/api/admin/accounts/{aid}/bot/tuning", headers=ADMIN_H,
                       json={"win_rate": 1.5}).status_code == 400
    assert client.post(f"/api/admin/accounts/{aid}/bot/tuning", headers=ADMIN_H,
                       json={"symbols": "TSLA"}).status_code == 400


def test_bot_handluje_wylacznie_wskazanym_instrumentem():
    """Nadpisanie ma dojść aż do otwieranej pozycji, nie tylko do persony."""
    from datetime import datetime, timedelta, timezone
    aid = _konto("bt-tick")
    s = SessionLocal()
    acc = s.get(Account, aid)
    tradebot.start(s, acc, style="balanced", pace="busy", target_pct=0.0)
    acc.bot_symbols = "BTCUSD"
    s.commit()
    # Środa w południe: w weekend bot nie otworzyłby nic i test byłby kalendarzowy.
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    for i in range(40):
        tradebot.tick(s, acc, now + timedelta(minutes=30 * i))
    from app.models import Trade
    symbole = {t.symbol for t in s.query(Trade).filter(Trade.account_id == aid).all()}
    s.close()
    assert symbole and symbole == {"BTCUSD"}
