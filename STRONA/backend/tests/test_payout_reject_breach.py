"""„Reject & breach account" przy wniosku o wypłatę.

Jeden klik w panelu: wniosek odrzucony I konto zamknięte jako failed z tym
samym powodem — ta sama ścieżka co Breach w karcie konta (wpis `manual`,
odcięcie u brokera, mail). Bramki breachu idą przed jakąkolwiek zmianą: gdy
breach jest niedozwolony, wniosek zostaje otwarty.
"""
from conftest import zloz_wniosek
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_payout_reject_breach.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, notify, poller  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.feed import Feed  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Breach, PayoutRequest, Trader  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
KAPITAL, ZYSK = 100_000.0, 1_000.0
WNIOSEK = {"method": "wise", "amount": 100, "details": {"email": "trader@example.com"}}


class _Broker(Feed):
    def __init__(self):
        self.zamkniete, self.zablokowane = [], []

    async def snapshot(self, login, metaapi_account_id, initial_balance, *,
                       password=None, server=None):
        return None

    async def close_all_positions(self, metaapi_account_id, *, login=None, password=None):
        self.zamkniete.append(metaapi_account_id)
        return 2

    async def lock(self, metaapi_account_id, *, login=None, password=None):
        self.zablokowane.append(metaapi_account_id)


@pytest.fixture
def broker(monkeypatch):
    b = _Broker()
    monkeypatch.setattr(poller, "_feed", b)
    return b


@pytest.fixture
def maile(monkeypatch):
    wyslane = []
    monkeypatch.setattr(notify, "send", lambda kind, to, ctx=None, *a, **k: wyslane.append(kind))
    return wyslane


def _funded(email, *, copytrading=False):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Reject Breach", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    login = f"7{tid:08d}"[:9]
    acc = Account(login=login, trader_id=tid, trader_name="Reject Breach",
                  product_key="2step-100k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  min_trading_days=5, trading_days_count=5,
                  balance=KAPITAL + ZYSK, equity=KAPITAL + ZYSK,
                  peak_equity=KAPITAL + ZYSK, day_start_equity=KAPITAL + ZYSK,
                  day_start_balance=KAPITAL + ZYSK,
                  platform_login=login, platform_password="haslo123",
                  metaapi_account_id=f"acc-{tid}", mt5_backed=True,
                  copytrading=copytrading)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _stan(aid, req_id):
    s = SessionLocal()
    try:
        acc, r = s.get(Account, aid), s.get(PayoutRequest, req_id)
        breache = [b.detail for b in s.query(Breach).filter(Breach.account_id == aid)]
        return acc.status, acc.breach_reason, r.status, r.reject_reason, breache
    finally:
        s.close()


def test_reject_i_breach_jednym_klikiem(broker, maile):
    aid, h = _funded("rb-ok@test.pl")
    powod = "Prohibited trading activity — Terms §6"
    with TestClient(app) as c:
        req_id = zloz_wniosek(c, aid, headers=h, json=WNIOSEK).json()["id"]
        r = c.post(f"/api/admin/payout-requests/{req_id}/reject", headers=ADMIN_H,
                   json={"reason": powod, "breach": True})
    assert r.status_code == 200, r.text
    assert r.json()["breached"] is True and r.json()["positions_closed"] == 2
    assert _stan(aid, req_id) == ("failed", powod, "rejected", powod, [powod])
    assert len(broker.zamkniete) == 1 and broker.zamkniete == broker.zablokowane
    assert "payout_rejected" in maile and "breached" in maile


def test_sam_reject_nie_rusza_konta(broker, maile):
    aid, h = _funded("rb-sam@test.pl")
    with TestClient(app) as c:
        req_id = zloz_wniosek(c, aid, headers=h, json=WNIOSEK).json()["id"]
        r = c.post(f"/api/admin/payout-requests/{req_id}/reject", headers=ADMIN_H,
                   json={"reason": "Trading activity under review"})
    assert r.status_code == 200 and r.json()["breached"] is False
    status, _, req_status, _, breache = _stan(aid, req_id)
    assert (status, req_status, breache) == ("funded", "rejected", [])
    assert broker.zamkniete == [] and "breached" not in maile


def test_copytrading_powod_sprzeczny_nic_nie_zmienia(broker, maile):
    aid, h = _funded("rb-copy@test.pl", copytrading=True)
    with TestClient(app) as c:
        req_id = zloz_wniosek(c, aid, headers=h, json=WNIOSEK).json()["id"]
        r = c.post(f"/api/admin/payout-requests/{req_id}/reject", headers=ADMIN_H,
                   json={"reason": "Copy trading between accounts", "breach": True})
    assert r.status_code == 400 and "Copytrading" in r.json()["detail"]
    status, _, req_status, _, breache = _stan(aid, req_id)
    assert (status, req_status, breache) == ("funded", "pending", [])
    assert "payout_rejected" not in maile and "breached" not in maile


def test_konto_juz_zamkniete_nie_odrzuca_wniosku(broker, maile):
    aid, h = _funded("rb-zamk@test.pl")
    with TestClient(app) as c:
        req_id = zloz_wniosek(c, aid, headers=h, json=WNIOSEK).json()["id"]
        s = SessionLocal(); s.get(Account, aid).status = "failed"; s.commit(); s.close()
        r = c.post(f"/api/admin/payout-requests/{req_id}/reject", headers=ADMIN_H,
                   json={"reason": "Trading activity under review", "breach": True})
    assert r.status_code == 400 and "already closed" in r.json()["detail"]
    assert _stan(aid, req_id)[2] == "pending"
