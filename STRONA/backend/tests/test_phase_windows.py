"""Każda faza konta liczona osobno: po awansie konto startuje „od zera".

Awans zostawia to samo konto i ten sam login, a transakcje nie niosą fazy.
Dawniej krzywa equity, kalendarz, historia i Analytics sumowały Phase 1 i
Phase 2 razem, choć saldo i dni handlowe konta opisywały już tylko nową fazę.
Teraz domyślnie liczy się bieżąca faza, a poprzednie zostają do obejrzenia
parametrem `phase` (wybór w Analytics).
"""
import os
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.NamedTemporaryFile(
    suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trade, Trader  # noqa: E402

init_db()
client = TestClient(app)
TERAZ = datetime.utcnow().replace(microsecond=0)


def _trader() -> tuple[int, dict]:
    s = SessionLocal()
    tr = Trader(email=f"fazy-{uuid4().hex[:8]}@example.com", password_hash=auth.hash_password("x"),
                full_name="Fazy", referral_code=uuid4().hex[:10].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _konto(tid: int, **kw) -> int:
    s = SessionLocal()
    login = f"fz-{uuid4().hex[:8]}"
    pola = dict(login=login, trader_id=tid, trader_name="Fazy", platform_login=login,
                platform_password="x", platform_server="MetaQuotes-Demo",
                product_key="2step-25k", initial_balance=25_000.0, balance=25_000.0,
                equity=25_000.0, peak_equity=25_000.0, day_start_equity=25_000.0,
                day_start_balance=25_000.0, steps=2, profit_target_p1=8.0,
                profit_target_p2=5.0, max_daily_loss_pct=5.0, max_overall_loss_pct=10.0,
                min_trading_days=4, status="active", phase="eval_1")
    pola.update(kw)
    acc = Account(**pola)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _trade(aid: int, kiedy: datetime, pnl: float) -> None:
    s = SessionLocal()
    s.add(Trade(account_id=aid, symbol="EURUSD", side="buy", lots=1.0, open_price=1.1,
                close_price=1.1, pnl=pnl, status="closed", opened_at=kiedy, closed_at=kiedy))
    s.commit(); s.close()


def _faza2():
    """Konto w Phase 2: dwie transakcje z Phase 1, jedna z Phase 2."""
    tid, h = _trader()
    p2 = TERAZ - timedelta(days=5)
    aid = _konto(tid, phase="eval_2", prev_phase_started_at=TERAZ - timedelta(days=20),
                 phase_started_at=p2, balance=25_300.0, equity=25_300.0)
    _trade(aid, p2 - timedelta(days=4), 1_200.0)
    _trade(aid, p2 - timedelta(days=3), 800.0)
    _trade(aid, p2 + timedelta(days=1), 300.0)
    return aid, h


def test_bez_awansu_wszystko_jak_dawniej():
    tid, h = _trader()
    aid = _konto(tid)
    _trade(aid, TERAZ - timedelta(days=40), 100.0)
    _trade(aid, TERAZ - timedelta(days=1), -50.0)
    assert client.get(f"/api/me/accounts/{aid}/stats", headers=h).json()["trades"] == 2
    konto = [a for a in client.get("/api/me/accounts", headers=h).json() if a["id"] == aid][0]
    assert [f["phase"] for f in konto["phases"]] == ["eval_1"]


def test_po_awansie_statystyki_i_kalendarz_startuja_od_zera():
    aid, h = _faza2()
    st = client.get(f"/api/me/accounts/{aid}/stats", headers=h).json()
    assert st["trades"] == 1 and st["net_pnl"] == 300.0
    dni = client.get(f"/api/me/accounts/{aid}/activity", headers=h).json()["days"]
    assert [d["pnl"] for d in dni] == [300.0]


def test_poprzednia_faza_zostaje_do_obejrzenia():
    aid, h = _faza2()
    st = client.get(f"/api/me/accounts/{aid}/stats?phase=eval_1", headers=h).json()
    assert st["trades"] == 2 and st["net_pnl"] == 2_000.0
    led = client.get(f"/api/me/accounts/{aid}/activity?phase=eval_1", headers=h).json()["ledger"]
    assert sorted(r["pnl"] for r in led) == [800.0, 1_200.0]


def test_krzywa_equity_fazy_2_startuje_od_kapitalu_startowego():
    aid, h = _faza2()
    krzywa = client.get(f"/api/me/accounts/{aid}", headers=h).json()["equity_curve"]
    assert krzywa[0]["equity"] == 25_000.0
    assert krzywa[-1]["balance"] == 25_300.0, "koniec krzywej = saldo konta, bez zysku z Phase 1"


def test_lista_faz_dla_wyboru_w_analytics():
    tid, h = _trader()
    p2, fd = TERAZ - timedelta(days=30), TERAZ - timedelta(days=10)
    aid = _konto(tid, phase="funded", status="funded", prev_phase_started_at=p2, phase_started_at=fd)
    _trade(aid, p2 - timedelta(days=2), 500.0)
    _trade(aid, p2 + timedelta(days=2), 400.0)
    _trade(aid, fd + timedelta(days=2), 250.0)
    konto = [a for a in client.get("/api/me/accounts", headers=h).json() if a["id"] == aid][0]
    assert [(f["phase"], f["label"], f["current"]) for f in konto["phases"]] == [
        ("eval_1", "Phase 1", False), ("eval_2", "Phase 2", False), ("funded", "Funded", True)]
    for faza, zysk in (("eval_1", 500.0), ("eval_2", 400.0), ("funded", 250.0)):
        assert client.get(f"/api/me/accounts/{aid}/stats?phase={faza}", headers=h).json()["net_pnl"] == zysk
