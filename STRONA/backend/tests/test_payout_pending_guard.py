"""Wypłaty on-demand: jeden otwarty wniosek na konto, przegląd do 24 h.

Drugi „pending" na tym samym koncie to dubel z niecierpliwości — dwa wnioski
na ten sam zysk rozjeżdżają księgowanie. Po decyzji admina (reject) droga
otwiera się ponownie. API dalej niesie summary.review_hours i expected_by
pending wniosku (panel i powiadomienia), ale portal tradera od 2026-09 nie
obiecuje terminu: bez „reviewed within 24 hours" i bez „decision by …".
"""
from conftest import zloz_wniosek
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
                  # Karencja dni handlu na koncie funded (poller.payout_days_left)
                  # obejmuje od 2026-09-22 takze 2-Step, wiec konto bez dni
                  # odbijaloby sie o bramke zamiast sprawdzac swoja rzecz.
                  min_trading_days=5, trading_days_count=5,
                  balance=KAPITAL + ZYSK, equity=KAPITAL + ZYSK,
                  peak_equity=KAPITAL + ZYSK, day_start_equity=KAPITAL + ZYSK,
                  day_start_balance=KAPITAL + ZYSK)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


WNIOSEK = {"method": "wise", "amount": 100, "details": {"email": "trader@example.com"}}


def test_drugi_wniosek_przy_otwartym_odbija_sie():
    aid, h = _funded("guard-dubel@test.pl")
    with TestClient(app) as c:
        assert zloz_wniosek(c, aid, headers=h,
                      json=WNIOSEK).status_code == 200
        r = zloz_wniosek(c, aid, headers=h, json=WNIOSEK)
        assert r.status_code == 400
        assert "already under review" in r.json()["detail"]


def test_po_decyzji_admina_droga_znow_otwarta():
    aid, h = _funded("guard-po-reject@test.pl")
    with TestClient(app) as c:
        req_id = zloz_wniosek(c, aid, headers=h,
                        json=WNIOSEK).json()["id"]
        assert c.post(f"/api/admin/payout-requests/{req_id}/reject", headers=ADMIN_H,
                      json={"reason": "test"}).status_code == 200
        assert zloz_wniosek(c, aid, headers=h,
                      json=WNIOSEK).status_code == 200


def test_api_nie_podaje_terminu_przegladu():
    """Strona nie obiecuje terminów — API też nie niesie „review_hours" ani
    „expected_by", z których portal mógłby taki termin złożyć."""
    aid, h = _funded("guard-harmonogram@test.pl")
    with TestClient(app) as c:
        zloz_wniosek(c, aid, headers=h, json=WNIOSEK)
        me = c.get("/api/me/payouts", headers=h).json()
    assert "review_hours" not in me["summary"]
    wiersz = me["requests"][0]
    assert wiersz["status"] == "pending" and "expected_by" not in wiersz


def test_portal_nie_obiecuje_terminu_decyzji():
    from conftest import zrodlo_portalu
    kod = zrodlo_portalu()
    for zakazane in ("reviewed within", "decision by", "within 24 hours",
                     "within one business day", "next business day"):
        assert zakazane not in kod, zakazane
    assert "Payouts are <b>on demand</b>" in kod
