"""Flash sale — ręczne oferty procentowe na wybrane plany.

Kontrakty, na których stoi cross-sell:
  * oferta imienna jest niewidoczna dla innych traderów i dla anonima,
  * kupon i oferta NIGDY się nie sumują — wygrywa korzystniejszy,
  * kod PF- przegrany z ofertą zostaje niezużyty (klient oddał za niego punkty),
  * porzucony checkout nie pali oferty jednorazowej,
  * podgląd ceny == realny checkout (jedna funkcja wyceny),
  * seed_products() przy starcie nie rusza cen mimo żywej oferty.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog, loyalty, offers  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import FlashOffer, MailLog, Order, Product, RewardCode, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)

ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(1000))


@pytest.fixture(autouse=True)
def _kasuj_oferty_miedzy_testami():
    """Baza zyje przez caly modul, a oferta globalna z jednego testu psulaby
    wycene w nastepnym — kazdy test startuje bez zywych ofert."""
    s = SessionLocal()
    s.query(FlashOffer).filter(FlashOffer.cancelled_at == None).update(       # noqa: E711
        {FlashOffer.cancelled_at: datetime.now(timezone.utc).replace(tzinfo=None)},
        synchronize_session=False)
    s.commit(); s.close()
    yield


def _trader() -> int:
    email = f"flash{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Flash Tester", referral_code=email.split("@")[0].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _oferta(pct: float = 30.0, trader_id: int | None = None, scope: str = "all",
            plan_keys: str | None = None, **pola) -> int:
    s = SessionLocal()
    o = FlashOffer(discount_pct=pct, trader_id=trader_id, scope=scope, plan_keys=plan_keys,
                   ends_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=24))
    for p, v in pola.items():
        setattr(o, p, v)
    s.add(o); s.commit(); oid = o.id; s.close()
    return oid


def _cena(tid: int, key: str, coupon: str | None = None) -> dict:
    s = SessionLocal()
    tr = s.get(Trader, tid)
    q = billing.compute_price(s, tr, key, coupon)
    q.pop("product"); q.pop("upgrade")
    s.close()
    return q


def _token(tid: int) -> dict:
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    r = client.post("/api/auth/login", json={"email": email, "password": "haslo12345"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------- widocznosc ----------------
def test_imienna_oferta_niewidoczna_dla_innych():
    swoj, obcy = _trader(), _trader()
    _oferta(40.0, trader_id=swoj)
    assert _cena(swoj, "2step-100k")["total_due_usd"] == 329.4      # 549 * 0.6
    assert _cena(obcy, "2step-100k")["total_due_usd"] == 549.0
    # publiczne /api/products (anonim) tez jej nie zdradza
    plany = {p["key"]: p for p in client.get("/api/products").json()}
    assert plany["2step-100k"]["offer_pct"] is None
    # ale zalogowany wlasciciel oferty widzi ja w kafelku i w /api/me/offers
    plany_swoje = {p["key"]: p for p in client.get("/api/products", headers=_token(swoj)).json()}
    assert plany_swoje["2step-100k"]["offer_price_usd"] == 329.4
    moje = client.get("/api/me/offers", headers=_token(swoj)).json()["offers"]
    assert [o["discount_pct"] for o in moje] == [40.0]
    assert client.get("/api/me/offers", headers=_token(obcy)).json()["offers"] == []


def test_zakres_2step_nie_rusza_instant():
    tid = _trader()
    _oferta(25.0, scope="2step")
    assert _cena(tid, "2step-25k")["total_due_usd"] == 224.25       # 299 * 0.75
    assert _cena(tid, "instant-25k")["total_due_usd"] == 369.0


def test_zakres_keys_obejmuje_tylko_wskazane():
    tid = _trader()
    _oferta(50.0, scope="keys", plan_keys="2step-100k,2step-200k")
    assert _cena(tid, "2step-100k")["total_due_usd"] == 274.5
    assert _cena(tid, "2step-200k")["total_due_usd"] == 524.5
    assert _cena(tid, "2step-50k")["total_due_usd"] == 349.0


# ---------------- kupon vs oferta ----------------
def test_kupon_i_oferta_nie_sumuja_sie_wygrywa_lepszy():
    tid = _trader()
    _oferta(30.0, trader_id=tid)
    q = _cena(tid, "2step-25k", coupon="WELCOME10")                 # kupon 10% vs oferta 30%
    assert q["discount_pct"] == 30.0 and q["discount_source"] == "offer"
    assert q["total_due_usd"] == 209.3                              # 299*0.7 — nie *0.63, nie *0.6
    q2 = _cena(tid, "2step-25k", coupon="BLACKFRIDAY")              # kupon 30% == oferta 30%
    assert q2["discount_source"] == "coupon" and q2["offer_id"] is None
    q3 = _cena(tid, "2step-25k")                                    # bez kuponu: oferta dziala sama
    assert q3["discount_source"] == "offer" and q3["total_due_usd"] == 209.3


def test_kod_pf_przegrany_z_oferta_zostaje_niezuzyty():
    tid = _trader()
    _oferta(30.0, trader_id=tid, single_use=True)
    s = SessionLocal()
    kod = RewardCode(trader_id=tid, code=loyalty.generate_code(s), pct=10.0, points_spent=500,
                     expires_at=datetime.now(timezone.utc) + timedelta(days=90))
    s.add(kod); s.commit(); kod_txt = kod.code
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-25k", kod_txt)
    order = s.get(Order, res["order_id"])
    assert order.amount_usd == 209.3
    # kupon zszedl z zamowienia, wiec provisioning nie ma czego spalic
    assert order.coupon is None and order.flash_offer_id is not None
    billing.mock_complete(s, order.id, tid)
    s.close()
    s = SessionLocal()
    assert s.query(RewardCode).filter(RewardCode.code == kod_txt).one().used_at is None
    zuzyta = s.get(FlashOffer, order.flash_offer_id)
    assert zuzyta.used_at is not None and zuzyta.order_id == order.id
    s.close()


# ---------------- zuzycie ----------------
def test_porzucony_checkout_nie_pali_oferty():
    tid = _trader()
    oid = _oferta(30.0, trader_id=tid, single_use=True)
    s = SessionLocal()
    billing.create_checkout(s, s.get(Trader, tid), "2step-25k", None)
    s.close()
    s = SessionLocal()
    assert s.get(FlashOffer, oid).used_at is None                   # zamowienie wisi, oferta zyje
    s.close()


def test_oferta_wielorazowa_nie_dostaje_znacznika():
    tid = _trader()
    oid = _oferta(30.0, single_use=False)                           # globalna
    s = SessionLocal()
    res = billing.create_checkout(s, s.get(Trader, tid), "2step-25k", None)
    billing.mock_complete(s, res["order_id"], tid)
    s.close()
    s = SessionLocal()
    o = s.get(FlashOffer, oid)
    assert o.used_at is None and offers.status(o) == "active"
    s.close()


# ---------------- spojnosc wyceny ----------------
def test_podglad_rowna_sie_checkoutowi():
    tid = _trader()
    _oferta(35.0, trader_id=tid)
    q = _cena(tid, "2step-100k")
    s = SessionLocal()
    res = billing.create_checkout(s, s.get(Trader, tid), "2step-100k", None)
    order = s.get(Order, res["order_id"])
    assert order.amount_usd == q["total_due_usd"] == 356.85
    s.close()


def test_seed_products_nie_rusza_cen_mimo_oferty():
    _oferta(50.0)
    s = SessionLocal()
    przed = {p.key: p.price_usd for p in s.query(Product).all()}
    catalog.seed_products(s)
    po = {p.key: p.price_usd for p in s.query(Product).all()}
    assert przed == po
    s.close()


# ---------------- API admina ----------------
def test_admin_tworzy_oferte_a_walidacje_lapia_bzdury():
    koniec = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    r = client.post("/api/admin/offers", headers=ADMIN_H,
                    json={"discount_pct": 40, "scope": "keys",
                          "plan_keys": ["2step-100k"], "ends_at": koniec,
                          "title": "Test sale"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    # bez admina ani rusz
    assert client.post("/api/admin/offers", json={"discount_pct": 10, "ends_at": koniec}).status_code in (401, 403)
    zle = [
        {"discount_pct": 95, "ends_at": koniec},                        # > 90%
        {"discount_pct": 10, "ends_at": koniec, "scope": "keys"},       # keys bez kluczy
        {"discount_pct": 10, "ends_at": koniec, "scope": "keys", "plan_keys": ["nie-ma"]},
        {"discount_pct": 10, "ends_at": "2020-01-01T00:00:00"},         # koniec w przeszlosci
        {"discount_pct": 10, "ends_at": koniec, "trader_email": "nikt@nigdzie.pl"},
    ]
    for payload in zle:
        assert client.post("/api/admin/offers", headers=ADMIN_H, json=payload).status_code in (400, 404), payload


def test_globalna_nigdy_nie_jest_single_use():
    koniec = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    r = client.post("/api/admin/offers", headers=ADMIN_H,
                    json={"discount_pct": 20, "ends_at": koniec, "single_use": True})
    assert r.status_code == 200 and r.json()["single_use"] is False


def test_cancel_gasi_oferte_natychmiast():
    tid = _trader()
    oid = _oferta(30.0, trader_id=tid)
    assert _cena(tid, "2step-25k")["total_due_usd"] == 209.3
    r = client.post(f"/api/admin/offers/{oid}/cancel", headers=ADMIN_H)
    assert r.json()["status"] == "cancelled"
    assert _cena(tid, "2step-25k")["total_due_usd"] == 299.0


def test_bez_zaznaczenia_send_email_mail_log_pusty():
    tid = _trader()
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    koniec = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    r = client.post("/api/admin/offers", headers=ADMIN_H,
                    json={"discount_pct": 25, "ends_at": koniec, "trader_email": email,
                          "send_email": False, "send_push": False})
    assert r.status_code == 200 and r.json()["emailed"] == 0
    s = SessionLocal()
    assert s.query(MailLog).filter(MailLog.event == "flash_offer",
                                   MailLog.to_email == email).count() == 0
    s.close()


def test_send_email_zostawia_slad_w_mail_logu():
    tid = _trader()
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    koniec = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    r = client.post("/api/admin/offers", headers=ADMIN_H,
                    json={"discount_pct": 25, "ends_at": koniec, "trader_email": email,
                          "send_email": True})
    assert r.status_code == 200 and r.json()["emailed"] == 1
    s = SessionLocal()
    assert s.query(MailLog).filter(MailLog.event == "flash_offer",
                                   MailLog.to_email == email).count() == 1
    s.close()


def test_wypisany_z_marketingu_nie_dostaje_maila():
    tid = _trader()
    s = SessionLocal()
    tr = s.get(Trader, tid); tr.notify_marketing = False
    email = tr.email; s.commit(); s.close()
    koniec = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    r = client.post("/api/admin/offers", headers=ADMIN_H,
                    json={"discount_pct": 25, "ends_at": koniec, "trader_email": email,
                          "send_email": True})
    assert r.json()["emailed"] == 0 and r.json()["skipped_optout"] == 1


def test_lista_admina_liczy_sprzedaz_z_oferty():
    tid = _trader()
    oid = _oferta(30.0, trader_id=tid)
    s = SessionLocal()
    res = billing.create_checkout(s, s.get(Trader, tid), "2step-25k", None)
    billing.mock_complete(s, res["order_id"], tid)
    s.close()
    lista = client.get("/api/admin/offers", headers=ADMIN_H).json()["offers"]
    moja = next(o for o in lista if o["id"] == oid)
    assert moja["bought"] == 1 and moja["revenue_usd"] == 209.3
