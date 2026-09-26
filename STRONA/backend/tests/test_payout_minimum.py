"""Minimalna wypłata z wniosku tradera: wyższa z $500 i 2% wielkości konta.

$500 chroni małe konta przed wypłatami groszy, 2% skaluje próg z kontem
(konto $1M → co najmniej $20,000 na jeden wniosek).
"""
import os
import tempfile

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.gettempdir(), "pf_test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, main as main_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trader  # noqa: E402
from conftest import zrodlo_portalu  # noqa: E402

PAYOUT_MIN_REAL = True     # conftest nie wyłącza tu progów
init_db()
client = TestClient(app)
USDT = {"network": "TRC20", "address": "TXyz1234567890abcdefghijklmnopqrst"}


def _konto(kapital: float, zysk: float):
    s = SessionLocal()
    tr = Trader(email=f"minpay-{auth.secrets.token_hex(4)}@test.pl",
                password_hash=auth.hash_password("haslo1234"), full_name="Min Payout",
                referral_code=auth.secrets.token_hex(3), kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login=f"8{auth.secrets.randbelow(10**8):08d}", trader_id=tid, trader_name="Min Payout",
                  product_key="2step-50k", initial_balance=kapital, steps=2, profit_split_pct=100,
                  status="funded", phase="funded", min_trading_days=5, trading_days_count=5,
                  balance=kapital + zysk, equity=kapital + zysk, peak_equity=kapital + zysk,
                  day_start_equity=kapital + zysk, day_start_balance=kapital + zysk)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _wniosek(aid, h, kwota):
    return client.post(f"/api/accounts/{aid}/payout-request", headers=h,
                       json={"method": "usdt", "amount": kwota, "details": USDT})


def test_ponizej_500_odrzucone_nawet_na_malym_koncie():
    aid, h = _konto(10_000, 3_000)          # 2% = $200, więc rządzi $500
    r = _wniosek(aid, h, 499)
    assert r.status_code == 400 and "$500" in r.json()["detail"]
    assert _wniosek(aid, h, 500).status_code == 200


def test_duze_konto_wymaga_2_procent():
    aid, h = _konto(1_000_000, 50_000)      # 2% = $20,000
    r = _wniosek(aid, h, 19_999)
    assert r.status_code == 400
    assert "2% of your account size" in r.json()["detail"] and "$20,000.00" in r.json()["detail"]
    assert _wniosek(aid, h, 20_000).status_code == 200


def test_za_mala_dostepna_dzialka_nie_przejdzie():
    aid, h = _konto(100_000, 1_500)         # dostępne $1,500 < 2% = $2,000
    r = _wniosek(aid, h, 1_500)
    assert r.status_code == 400 and "$2,000.00" in r.json()["detail"]


def test_payout_minimum_liczy_wyzszy_prog():
    s = SessionLocal()
    acc = s.query(Account).filter(Account.initial_balance == 1_000_000).first()
    assert main_mod.payout_minimum(acc) == 20_000.0
    acc.initial_balance = 10_000
    assert main_mod.payout_minimum(acc) == 500.0
    s.rollback(); s.close()


def test_okno_wyplaty_ma_minimum_500_i_alert_o_2_procentach():
    kod = zrodlo_portalu()
    assert "const PO_MIN_USD=500, PO_MIN_PCT=2;" in kod
    assert 'min="${PO_MIN_USD}"' in kod
    assert 'id="po-min" role="alert"' in kod and "% of your account size" in kod
