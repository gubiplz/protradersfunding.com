"""Pula wypłaty: admin ustala ILE trader może wypłacić, niezależnie od wykresu.

Ustawiona pula ZASTĘPUJE formułę (balance − initial) × split% jako „available".
Każda zaksięgowana wypłata schodzi z puli (podłoga 0), a saldo NIE resetuje się
— w trybie puli saldo maluje bot i reset by z nim walczył. NULL = stara formuła,
więc istniejące konta nie zmieniają zachowania.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Payout, Trader  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
KAPITAL = 100_000.0


def _funded(email, *, zysk=0.0):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Pool Test", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login=f"9{tid:08d}"[:9], trader_id=tid, trader_name="Pool Test",
                  product_key="2step-100k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  balance=KAPITAL + zysk, equity=KAPITAL + zysk,
                  peak_equity=KAPITAL + zysk, day_start_equity=KAPITAL + zysk,
                  day_start_balance=KAPITAL + zysk)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def test_pula_wiaze_nawet_przy_zerowym_zysku_i_blokuje_nadwyzke():
    """Saldo == start (bot nic nie namalował), pula 500 => trader widzi $500,
    wniosek 600 odpada, wniosek 200 przechodzi."""
    aid, h = _funded("pool-zero@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
                   json={"amount": 500})
        assert r.status_code == 200 and r.json()["payout_available"] == 500.0

        me = c.get("/api/me/accounts", headers=h).json()
        moje = next(a for a in me if a["id"] == aid)
        assert moje["payout_available"] == 500.0
        assert c.get("/api/me/payouts", headers=h).json()["summary"]["available"] == 500.0

        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "wise", "amount": 600,
                         "details": {"email": "trader@example.com"}})
        assert r.status_code == 400 and "exceeds" in r.json()["detail"]

        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "wise", "amount": 200,
                         "details": {"email": "trader@example.com"}})
        assert r.status_code == 200, r.text


def test_approve_dekrementuje_pule_bez_resetu_salda():
    aid, h = _funded("pool-approve@test.pl", zysk=1_000.0)
    with TestClient(app) as c:
        c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
               json={"amount": 500})
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "wise", "amount": 200,
                         "details": {"email": "trader@example.com"}})
        req_id = r.json()["id"]
        assert c.post(f"/api/admin/payout-requests/{req_id}/approve",
                      headers=ADMIN_H).status_code == 200
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.payout_pool_usd == 300.0            # 500 − 200
    assert acc.balance == KAPITAL + 1_000.0        # saldo NIE zresetowane
    p = s.query(Payout).filter(Payout.account_id == aid).one()
    assert p.balance_reset is False
    s.close()


def test_reczna_wyplata_admina_schodzi_z_puli_i_nie_resetuje():
    aid, _h = _funded("pool-issue@test.pl", zysk=2_000.0)
    with TestClient(app) as c:
        c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
               json={"amount": 750})
        # bez kwoty => domyślna działka to cała pula, nie formuła
        d = c.get(f"/api/admin/accounts/{aid}/payouts", headers=ADMIN_H).json()
        assert d["suggested_share"] == 750.0 and d["payout_pool_usd"] == 750.0

        r = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 750, "method": "bank", "reset_balance": True})
        assert r.status_code == 200
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.payout_pool_usd == 0.0
    assert acc.balance == KAPITAL + 2_000.0        # reset wymuszony na skip
    s.close()


def test_null_wraca_do_formuly_a_ujemna_odpada():
    aid, h = _funded("pool-null@test.pl", zysk=1_000.0)
    with TestClient(app) as c:
        assert c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
                      json={"amount": -5}).status_code == 400

        c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
               json={"amount": 300})
        r = c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
                   json={"amount": None})
        # formuła: 1000 zysku × 80% = 800
        assert r.status_code == 200
        assert r.json()["payout_pool_usd"] is None
        assert r.json()["payout_available"] == 800.0

        me = c.get("/api/me/accounts", headers=h).json()
        assert next(a for a in me if a["id"] == aid)["payout_available"] == 800.0


def test_pula_zero_znaczy_wyczerpana_a_nie_formula():
    aid, h = _funded("pool-wyczerpana@test.pl", zysk=1_000.0)
    with TestClient(app) as c:
        c.post(f"/api/admin/accounts/{aid}/payout-pool", headers=ADMIN_H,
               json={"amount": 0})
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                   json={"method": "wise", "amount": 100,
                         "details": {"email": "trader@example.com"}})
        # mimo $800 „formułowych" na wykresie pula 0 blokuje wniosek
        assert r.status_code == 400
