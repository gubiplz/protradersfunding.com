"""Webhook Stripe na złe wieści: fraud, chargeback, porzucony checkout.

Sprawa zamówienia #141 na produkcji: Radar ściął kartę, event `completed`
nigdy nie przyszedł i zamówienie wisiało jako `pending` — wyglądało, jakbyśmy
czekali na wpłatę od cardera. Do tego Stripe zamyka nam konto (apelacja w toku),
więc każdy chargeback to cios w metryki dokładnie wtedy, gdy ktoś je ogląda.
Stąd trzy zachowania: (1) early fraud warning → refund od ręki, bez człowieka,
(2) chargeback → flaga + alarm dla adminów, (3) wygasła/odrzucona sesja →
zamówienie domyka się samo jako failed, chyba że czekamy na crypto.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import json  # noqa: E402

import pytest  # noqa: E402
import stripe as stripe_sdk  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)
LICZNIK = iter(range(10000))


class _FakeStripe:
    error = stripe_sdk.error
    refundy: list = []
    # payment_intent -> id sesji Checkout (odpowiedź na Session.list)
    sesje_po_pi: dict = {}

    class checkout:
        class Session:
            @staticmethod
            def list(payment_intent=None, limit=1):
                sid = _FakeStripe.sesje_po_pi.get(payment_intent)
                return {"data": ([{"id": sid}] if sid else [])}

    class Refund:
        @staticmethod
        def create(**kw):
            _FakeStripe.refundy.append(kw)
            return {"id": "re_test", **kw}

    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            return json.loads(payload.decode())


@pytest.fixture
def stripe_atrapa(monkeypatch):
    _FakeStripe.refundy = []
    _FakeStripe.sesje_po_pi = {}
    monkeypatch.setattr(billing.settings, "stripe_secret_key", "sk_test_atrapa")
    monkeypatch.setattr(billing.settings, "stripe_webhook_secret", "whsec_atrapa")
    monkeypatch.setattr(billing, "_stripe", lambda: _FakeStripe)
    return _FakeStripe


def _zamowienie(status="pending", flag=None, sid=None, account_id=None) -> int:
    n = next(LICZNIK)
    s = SessionLocal()
    tr = Trader(email=f"fraudwh{n}@test.pl", password_hash=auth.hash_password("haslo12345"),
                full_name="Fraud Tester", referral_code=f"FRW{n:04d}")
    s.add(tr); s.flush()
    o = Order(trader_id=tr.id, product_key="2step-25k", amount_usd=498.0,
              provider="stripe", status=status, flag=flag,
              stripe_session_id=sid or f"cs_test_frw{n}", account_id=account_id)
    s.add(o); s.commit(); oid = o.id; s.close()
    return oid


def _order(oid: int) -> Order:
    s = SessionLocal()
    o = s.get(Order, oid); s.expunge(o); s.close()
    return o


def _wyslij(event: dict):
    r = client.post("/api/stripe/webhook", content=json.dumps(event).encode(),
                    headers={"stripe-signature": "atrapa"})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
#  Wygasła / odrzucona sesja Checkout                                          #
# --------------------------------------------------------------------------- #
def test_wygasla_sesja_domyka_pending_jako_failed(stripe_atrapa):
    oid = _zamowienie()
    sid = _order(oid).stripe_session_id
    _wyslij({"type": "checkout.session.expired", "data": {"object": {"id": sid}}})
    o = _order(oid)
    assert o.status == "failed"
    assert "expired" in (o.fail_reason or "")


def test_awaiting_crypto_nie_jest_ruszane(stripe_atrapa):
    """Klient płaci poza Stripe'em — wygaśnięcie sesji Checkout to nie porażka."""
    oid = _zamowienie(flag="awaiting_crypto")
    sid = _order(oid).stripe_session_id
    _wyslij({"type": "checkout.session.expired", "data": {"object": {"id": sid}}})
    assert _order(oid).status == "pending"


def test_cudza_sesja_niczego_nie_zamyka(stripe_atrapa):
    """Współdzielony sandbox broadcastuje eventy — dopasowanie tylko po naszym id."""
    oid = _zamowienie()
    _wyslij({"type": "checkout.session.expired",
             "data": {"object": {"id": "cs_obcy_sandbox"}}})
    assert _order(oid).status == "pending"


def test_odrzucona_platnosc_async_tez_domyka(stripe_atrapa):
    oid = _zamowienie()
    sid = _order(oid).stripe_session_id
    _wyslij({"type": "checkout.session.async_payment_failed",
             "data": {"object": {"id": sid}}})
    o = _order(oid)
    assert o.status == "failed" and "failed" in (o.fail_reason or "")


# --------------------------------------------------------------------------- #
#  Early fraud warning                                                         #
# --------------------------------------------------------------------------- #
def test_fraud_warning_refunduje_od_reki(stripe_atrapa):
    """SEDNO: refund idzie natychmiast i z powodem `fraudulent` (blocklista),
    zanim zgłoszenie urośnie w chargeback."""
    oid = _zamowienie(status="paid")
    stripe_atrapa.sesje_po_pi["pi_frw1"] = _order(oid).stripe_session_id
    wynik = _wyslij({"type": "radar.early_fraud_warning.created",
                     "data": {"object": {"actionable": True, "charge": "ch_frw1",
                                         "payment_intent": "pi_frw1"}}})
    assert wynik.get("refunded") is True
    assert stripe_atrapa.refundy == [{"charge": "ch_frw1", "reason": "fraudulent"}]
    o = _order(oid)
    assert o.flag == "fraud"
    assert o.status == "paid"  # status nie kłamie: pieniądze BYŁY pobrane


def test_fraud_warning_na_pending_ubija_zamowienie(stripe_atrapa):
    """Wariant #141: Radar zablokował, zamówienie nigdy nie było opłacone —
    ma nie wisieć jako pending."""
    oid = _zamowienie()
    stripe_atrapa.sesje_po_pi["pi_frw2"] = _order(oid).stripe_session_id
    _wyslij({"type": "radar.early_fraud_warning.created",
             "data": {"object": {"actionable": False, "charge": "ch_frw2",
                                 "payment_intent": "pi_frw2"}}})
    o = _order(oid)
    assert o.status == "failed" and o.flag == "fraud"
    assert "fraud" in (o.fail_reason or "")


def test_fraud_warning_nieactionable_bez_refundu(stripe_atrapa):
    """`actionable: false` = charge już zrefundowany/w dispute — drugi refund
    poleciałby błędem."""
    oid = _zamowienie(status="paid")
    stripe_atrapa.sesje_po_pi["pi_frw3"] = _order(oid).stripe_session_id
    wynik = _wyslij({"type": "radar.early_fraud_warning.created",
                     "data": {"object": {"actionable": False, "charge": "ch_frw3",
                                         "payment_intent": "pi_frw3"}}})
    assert wynik.get("refunded") is False
    assert stripe_atrapa.refundy == []
    assert _order(oid).flag == "fraud"


def test_fraud_warning_bez_naszego_zamowienia_tylko_refund(stripe_atrapa):
    """Nieznany payment_intent: refund i alarm tak, grzebanie w bazie nie."""
    wynik = _wyslij({"type": "radar.early_fraud_warning.created",
                     "data": {"object": {"actionable": True, "charge": "ch_obcy",
                                         "payment_intent": "pi_obcy"}}})
    assert wynik.get("refunded") is True
    assert "order_id" not in wynik


# --------------------------------------------------------------------------- #
#  Chargeback                                                                  #
# --------------------------------------------------------------------------- #
def test_chargeback_flaguje_zamowienie(stripe_atrapa):
    oid = _zamowienie(status="paid")
    stripe_atrapa.sesje_po_pi["pi_frw4"] = _order(oid).stripe_session_id
    wynik = _wyslij({"type": "charge.dispute.created",
                     "data": {"object": {"payment_intent": "pi_frw4",
                                         "amount": 49800, "reason": "fraudulent"}}})
    assert wynik.get("order_id") == oid
    o = _order(oid)
    assert o.flag == "disputed" and o.status == "paid"
    assert stripe_atrapa.refundy == []  # przy dispute refund niczego nie cofa
