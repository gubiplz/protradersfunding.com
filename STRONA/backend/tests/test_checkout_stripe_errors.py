"""Błąd po stronie Stripe'a nie może dolecieć do kupującego jako gołe HTTP 500.

Zdarzyło się na produkcji: konto rozlicza się w PLN, Stripe odrzucił sesję
(`amount_too_small`), a `stripe.checkout.Session.create` poleciało wyjątkiem
przez cały stos. Klient zobaczył „Server error" bez jednego słowa wyjaśnienia
i bez podpowiedzi, co zrobić dalej — na ostatnim kroku zakupu.

Tu pilnujemy trzech rzeczy naraz: status jest 4xx, komunikat jest po ludzku,
a zamówienie zostaje w bazie jako `pending` (nikt za nie nie zapłacił, ale ślad
jest potrzebny, żeby odtworzyć sprawę ze zgłoszenia klienta).
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

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

LICZNIK = iter(range(1000))


def _trader() -> int:
    """Profil z kompletem danych — `_dane_klienta` przepuszcza wtedy pusty payload."""
    email = f"stripeerr{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Err Tester", referral_code=email.split("@")[0].upper(),
                first_name="Jan", last_name="Kowalski",
                phone="+48507480663", phone_country="PL")
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


class _FakeStripe:
    """Podstawka pod `billing._stripe()`: wysadza `Session.create`, resztę
    zostawia prawdziwą, żeby `except stripe.error.StripeError` łapało realną
    klasę wyjątku, a nie atrapę, która przeszłaby test przy każdej hierarchii."""
    error = stripe_sdk.error

    class checkout:
        class Session:
            @staticmethod
            def create(**kw):
                raise stripe_sdk.error.InvalidRequestError(
                    "The Checkout Session's total amount must convert to at least "
                    "200 grosz. $0.50 converts to approximately 1.86 zł.",
                    param=None, code="amount_too_small")


@pytest.fixture
def stripe_wysadzony(monkeypatch):
    monkeypatch.setattr(billing.settings, "stripe_secret_key", "sk_test_atrapa")
    monkeypatch.setattr(billing, "_stripe", lambda: _FakeStripe)


def test_blad_stripe_to_4xx_a_nie_500(stripe_wysadzony):
    tid = _trader()
    r = client.post("/api/checkout", json={"product_key": "2step-25k"},
                    headers={"Authorization": f"Bearer {auth.make_token(tid)}"})
    assert r.status_code == 400, f"kupujący dostał {r.status_code} zamiast czytelnego błędu"
    tresc = r.json().get("detail", "")
    assert "payment page" in tresc, "komunikat nie mówi kupującemu, co się stało"
    # Surowy tekst od Stripe'a nie wychodzi na zewnątrz — „200 grosz" nic nie
    # mówi klientowi płacącemu w dolarach i zdradza szczegóły konfiguracji konta.
    assert "grosz" not in tresc


def test_zamowienie_zostaje_pending_po_bledzie(stripe_wysadzony):
    tid = _trader()
    client.post("/api/checkout", json={"product_key": "2step-25k"},
                headers={"Authorization": f"Bearer {auth.make_token(tid)}"})
    s = SessionLocal()
    zam = s.query(Order).filter(Order.trader_id == tid).all()
    s.close()
    assert len(zam) == 1, "ślad po nieudanej próbie zakupu przepadł"
    assert zam[0].status == "pending", "zamówienie bez płatności nie może być opłacone"
    assert zam[0].stripe_session_id in (None, ""), "sesja Stripe'a nie powstała"
