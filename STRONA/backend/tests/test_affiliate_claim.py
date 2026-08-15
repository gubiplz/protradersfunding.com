"""Afiliacja: naliczona prowizja zamienia się w kredyty sklepowe NA ŻĄDANIE.

unclaimed = prowizja z opłaconych zamówień poleconych − suma wpisów
`affiliate:claim` w ledgerze. Claim poniżej $10 odbija się (koszt księgowania
groszowych roszczeń), drugi claim z rzędu widzi 0 i też się odbija. Kredyty
z claimu są zwykłymi kredytami sklepowymi — preview checkoutu je odlicza.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_affiliate_claim.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CreditLedger, Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)


def _referrer(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Aff Tester", referral_code=email.split("@")[0].upper())
    s.add(tr); s.commit(); tid = tr.id; kod = tr.referral_code; s.close()
    return tid, kod, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _polecony_z_zamowieniem(kod, email, kwota):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Referred", referral_code=email.split("@")[0].upper(),
                referred_by=kod)
    s.add(tr); s.commit()
    s.add(Order(trader_id=tr.id, product_key="2step-25k", amount_usd=kwota,
                status="paid", provider="stripe"))
    s.commit(); s.close()


def test_claim_zamienia_prowizje_na_kredyty():
    tid, kod, h = _referrer("aff-claim@test.pl")
    _polecony_z_zamowieniem(kod, "aff-claim-ref@test.pl", 500.0)

    me = client.get("/api/auth/me", headers=h).json()["affiliate"]
    assert me["commission_earned"] == 50.0
    assert me["commission_claimed"] == 0.0 and me["commission_unclaimed"] == 50.0

    r = client.post("/api/me/affiliate/claim", headers=h)
    assert r.status_code == 200
    assert r.json()["claimed_usd"] == 50.0 and r.json()["credits_usd"] == 50.0

    s = SessionLocal()
    assert round(float(s.get(Trader, tid).credits_usd), 2) == 50.0
    wpis = (s.query(CreditLedger)
            .filter(CreditLedger.trader_id == tid,
                    CreditLedger.note == "affiliate:claim").one())
    assert wpis.amount == 50.0
    s.close()

    me = client.get("/api/auth/me", headers=h).json()["affiliate"]
    assert me["commission_claimed"] == 50.0 and me["commission_unclaimed"] == 0.0


def test_drugi_claim_widzi_zero_i_sie_odbija():
    _, kod, h = _referrer("aff-dubel@test.pl")
    _polecony_z_zamowieniem(kod, "aff-dubel-ref@test.pl", 500.0)
    assert client.post("/api/me/affiliate/claim", headers=h).status_code == 200
    r = client.post("/api/me/affiliate/claim", headers=h)
    assert r.status_code == 400
    assert "$10" in r.json()["detail"]


def test_ponizej_minimum_odbija_sie():
    _, kod, h = _referrer("aff-drobne@test.pl")
    _polecony_z_zamowieniem(kod, "aff-drobne-ref@test.pl", 50.0)   # prowizja $5
    r = client.post("/api/me/affiliate/claim", headers=h)
    assert r.status_code == 400 and "$5.00" in r.json()["detail"]


def test_nowe_zamowienia_naliczaja_kolejna_transze():
    _, kod, h = _referrer("aff-transza@test.pl")
    _polecony_z_zamowieniem(kod, "aff-transza-ref1@test.pl", 500.0)
    assert client.post("/api/me/affiliate/claim", headers=h).json()["claimed_usd"] == 50.0
    _polecony_z_zamowieniem(kod, "aff-transza-ref2@test.pl", 200.0)
    r = client.post("/api/me/affiliate/claim", headers=h)
    assert r.status_code == 200 and r.json()["claimed_usd"] == 20.0
    assert r.json()["credits_usd"] == 70.0


def test_kredyty_z_claimu_widzi_preview_checkoutu():
    _, kod, h = _referrer("aff-preview@test.pl")
    _polecony_z_zamowieniem(kod, "aff-preview-ref@test.pl", 500.0)
    client.post("/api/me/affiliate/claim", headers=h)
    q = client.get("/api/checkout/preview?product_key=2step-25k", headers=h).json()
    assert q["credits_balance"] == 50.0 and q["credits_used"] == 50.0
    assert q["total_due_usd"] == 249.0   # $299 − $50 kredytów
