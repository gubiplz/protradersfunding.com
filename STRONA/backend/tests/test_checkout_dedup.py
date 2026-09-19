"""Podwójny klik „Buy" to JEDNO zamówienie — i tylko jedna żywa sesja płatności.

Na telefonie klik w „Buy" potrafi polecieć dwa razy (drganie palca, wolna sieć
i drugi klik „bo nic się nie dzieje"). Każdy klik robił osobny pending Order,
a przy Stripe — osobną, W PEŁNI PŁATNĄ sesję kasy. Dwie karty przeglądarki,
dwie kasy, da się zapłacić obie. Dlatego wiszące zamówienie jest reużywane,
a stara sesja Stripe wygaszana przy otwarciu nowej.

Czego pilnujemy poza samym „jeden wiersz":

* reużycie NIE zamraża wyceny — klient wraca do sklepu, dorzuca addon i cena
  na zamówieniu musi być ta z drugiego podejścia, nie pierwsza,
* dedup nie skleja różnych produktów ani nie odkopuje zamówień opłaconych —
  „kup drugi challenge" to nowa sprzedaż, nie duplikat.
"""
import os
import tempfile
from types import SimpleNamespace

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
    email = f"dedup{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Dedup Tester", referral_code=email.split("@")[0].upper(),
                first_name="Jan", last_name="Kowalski",
                phone="+48507480663", phone_country="PL")
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _kup(tid: int, **extra):
    return client.post("/api/checkout", json={"product_key": "2step-25k", **extra},
                       headers={"Authorization": f"Bearer {auth.make_token(tid)}"})


def _zamowienia(tid: int) -> list[Order]:
    s = SessionLocal()
    zam = s.query(Order).filter(Order.trader_id == tid).order_by(Order.id).all()
    s.close()
    return zam


def test_podwojny_klik_reuzywa_zamowienia():
    tid = _trader()
    r1, r2 = _kup(tid), _kup(tid)
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["order_id"] == r1.json()["order_id"]
    assert len(_zamowienia(tid)) == 1, "drugi klik dorobił drugie zamówienie"


def test_reuzycie_nie_zamraza_wyceny():
    tid = _trader()
    bez = _kup(tid).json()
    z_addonem = _kup(tid, weekend_trading=True).json()
    assert z_addonem["order_id"] == bez["order_id"]
    assert z_addonem["amount"] > bez["amount"], "addon nie podniósł ceny przy reużyciu"
    zam = _zamowienia(tid)[0]
    assert zam.amount_usd == z_addonem["amount"]
    assert zam.weekend_trading


def test_inny_produkt_to_osobne_zamowienie():
    tid = _trader()
    r1 = _kup(tid).json()
    r2 = _kup(tid, product_key="2step-50k").json()
    assert r2["order_id"] != r1["order_id"], "dedup skleił dwa różne produkty"
    assert len(_zamowienia(tid)) == 2


def test_oplacone_nie_jest_odkopywane():
    tid = _trader()
    pierwsze = _kup(tid).json()["order_id"]
    s = SessionLocal()
    s.get(Order, pierwsze).status = "paid"
    s.commit(); s.close()
    drugie = _kup(tid).json()["order_id"]
    assert drugie != pierwsze, '„kup jeszcze raz" nadpisało opłacone zamówienie'


class _FakeStripe:
    """Atrapa pod `billing._stripe()`: `create` oddaje kolejne sesje, `expire`
    tylko zapisuje, co wygaszono. `error` zostaje prawdziwe, żeby except-y
    łapały realną hierarchię wyjątków."""
    error = stripe_sdk.error
    expired: list[str] = []
    _seq = iter(range(1000))

    class checkout:
        class Session:
            @staticmethod
            def create(**kw):
                return SimpleNamespace(id=f"cs_atrapa_{next(_FakeStripe._seq)}",
                                       url="https://stripe.atrapa/pay")

            @staticmethod
            def expire(sid):
                _FakeStripe.expired.append(sid)


@pytest.fixture
def stripe_atrapa(monkeypatch):
    monkeypatch.setattr(billing.settings, "stripe_secret_key", "sk_test_atrapa")
    monkeypatch.setattr(billing, "_stripe", lambda: _FakeStripe)
    _FakeStripe.expired.clear()
    return _FakeStripe


def test_stara_sesja_stripe_jest_wygaszana(stripe_atrapa):
    """Sedno sprawy: bez `expire` obie karty mają czynne kasy i obie pobiorą
    pieniądze — webhook dopasowuje po `stripe_session_id`, więc druga wpłata
    nawet nie domknęłaby zamówienia, tylko wisiała jako sierota u Stripe'a."""
    tid = _trader()
    pierwsze = _kup(tid).json()
    sid1 = _zamowienia(tid)[0].stripe_session_id
    assert sid1, "checkout Stripe nie zapisał sesji przy zamówieniu"

    drugie = _kup(tid).json()
    assert drugie["order_id"] == pierwsze["order_id"]
    assert stripe_atrapa.expired == [sid1], "stara sesja została żywa"
    assert _zamowienia(tid)[0].stripe_session_id != sid1, "zamówienie wskazuje wygaszoną kasę"
