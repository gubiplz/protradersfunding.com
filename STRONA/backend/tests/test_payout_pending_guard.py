"""Wypłaty on-demand: jeden otwarty wniosek na konto, przegląd do 24 h.

Drugi „pending" na tym samym koncie to dubel z niecierpliwości — dwa wnioski
na ten sam zysk rozjeżdżają księgowanie. Po decyzji admina (reject) droga
otwiera się ponownie. Portal pisze „reviewed within 24 hours" z pola
summary.review_hours, a pending wniosek niesie expected_by.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_payout_guard.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trader  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

KAPITAL = 100_000.0
ZYSK = 1_000.0


def _funded(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Guard Tester", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login=f"8{tid:08d}"[:9], trader_id=tid, trader_name="Guard Tester",
                  product_key="2step-100k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  balance=KAPITAL + ZYSK, equity=KAPITAL + ZYSK,
                  peak_equity=KAPITAL + ZYSK, day_start_equity=KAPITAL + ZYSK,
                  day_start_balance=KAPITAL + ZYSK)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


WNIOSEK = {"method": "wise", "amount": 100, "details": {"email": "trader@example.com"}}


def test_drugi_wniosek_przy_otwartym_odbija_sie():
    aid, h = _funded("guard-dubel@test.pl")
    with TestClient(app) as c:
        assert c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                      json=WNIOSEK).status_code == 200
        r = c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=WNIOSEK)
        assert r.status_code == 400
        assert "already under review" in r.json()["detail"]


def test_po_decyzji_admina_droga_znow_otwarta():
    aid, h = _funded("guard-po-reject@test.pl")
    with TestClient(app) as c:
        req_id = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                        json=WNIOSEK).json()["id"]
        assert c.post(f"/api/admin/payout-requests/{req_id}/reject", headers=ADMIN_H,
                      json={"reason": "test"}).status_code == 200
        assert c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                      json=WNIOSEK).status_code == 200


def test_summary_niesie_harmonogram_a_pending_expected_by():
    aid, h = _funded("guard-harmonogram@test.pl")
    with TestClient(app) as c:
        c.post(f"/api/accounts/{aid}/payout-request", headers=h, json=WNIOSEK)
        me = c.get("/api/me/payouts", headers=h).json()
    assert me["summary"]["review_hours"] == 24
    wiersz = me["requests"][0]
    assert wiersz["status"] == "pending"
    # expected_by = ts + 24h; wystarczy sprawdzić, że jest i że to inny dzień/ta
    # sama forma ISO — dokładną arytmetykę niesie timedelta w endpointcie
    assert wiersz["expected_by"] and wiersz["expected_by"] > wiersz["ts"]


def test_zamkniety_wniosek_bez_expected_by():
    aid, h = _funded("guard-done@test.pl")
    with TestClient(app) as c:
        req_id = c.post(f"/api/accounts/{aid}/payout-request", headers=h,
                        json=WNIOSEK).json()["id"]
        c.post(f"/api/admin/payout-requests/{req_id}/reject", headers=ADMIN_H,
               json={"reason": "test"})
        me = c.get("/api/me/payouts", headers=h).json()
    wiersz = next(x for x in me["requests"] if x["id"] == req_id)
    assert wiersz["expected_by"] is None
