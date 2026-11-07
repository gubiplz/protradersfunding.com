"""Add-ony checkoutu: Split Boost (+10 pp splitu, tylko Instant, $149)
i On-demand Payout (bez limitu 14 dni między wnioskami: $299 2-Step / $399 Instant).

Walidacja Instant-only siedzi w billing.compute_price — goły POST omijający
modal też dostaje 400. Provisioning przenosi boost na `profit_split_pct`
konta (70→80), a flaga ondemand_payout na koncie omija bi-weekly.
Ceny planów z `_CATALOG` pozostają nietknięte (baza wiedzy Forex Passing).
"""
from conftest import zloz_wniosek
import os
import tempfile
import uuid

_db = os.path.join(tempfile.gettempdir(), f"pf_test_addons_{os.getpid()}_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _db
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from tests.conftest import CHECKOUT_CONSENT
from app import auth, catalog  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, AppSetting, Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

INSTANT_25K = 369.0   # z _CATALOG — nie zmieniać (patrz docstring)
TWOSTEP_25K = 299.0


def _trader(email):
    """Profil z kompletem danych MT5, żeby checkout nie żądał formularza."""
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Addon Tester", referral_code=email.split("@")[0].upper(),
                kyc_status="approved", first_name="Addon", last_name="Tester",
                phone="+48555123456", phone_country="PL")
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def test_preview_dolicza_addony():
    _, h = _trader("addon-preview@test.pl")
    q = client.get("/api/checkout/preview?product_key=instant-25k"
                   "&split_boost=true&ondemand=true", headers=h).json()
    assert q["split_boost_fee_usd"] == catalog.SPLIT_BOOST_ADDON_USD
    assert q["ondemand_payout_fee_usd"] == catalog.ONDEMAND_PAYOUT_INSTANT_USD == 399.0
    assert q["total_due_usd"] == INSTANT_25K + 149.0 + 399.0
    bez = client.get("/api/checkout/preview?product_key=instant-25k", headers=h).json()
    assert bez["split_boost_fee_usd"] == 0.0 and bez["ondemand_payout_fee_usd"] == 0.0
    assert bez["total_due_usd"] == INSTANT_25K


def test_split_boost_tylko_dla_instant():
    _, h = _trader("addon-2step@test.pl")
    r = client.get("/api/checkout/preview?product_key=2step-25k&split_boost=true",
                   headers=h)
    assert r.status_code == 400 and "Instant Funding" in r.json()["detail"]
    r = client.post("/api/checkout", headers=h,
                    json={**CHECKOUT_CONSENT, "product_key": "2step-25k", "split_boost": True})
    assert r.status_code == 400 and "Instant Funding" in r.json()["detail"]
    # On-demand nie ma ograniczenia planu — 2-Step = $299
    ok = client.get("/api/checkout/preview?product_key=2step-25k&ondemand=true", headers=h)
    assert ok.status_code == 200 and ok.json()["ondemand_payout_fee_usd"] == 299.0
    assert ok.json()["ondemand_payout_fee_usd"] == catalog.ONDEMAND_PAYOUT_2STEP_USD


def test_split_boost_admin_moze_schowac():
    """Settings → Hide from cart: switch znika i serwer odrzuca goły POST."""
    _, h = _trader("addon-sb-off@test.pl")
    s = SessionLocal()
    row = s.get(AppSetting, catalog.SPLIT_BOOST_OFFERED_KEY)
    if row is None:
        row = AppSetting(key=catalog.SPLIT_BOOST_OFFERED_KEY)
        s.add(row)
    row.value = "0"
    s.commit(); s.close()
    try:
        r = client.get("/api/checkout/preview?product_key=instant-25k&split_boost=true",
                       headers=h)
        assert r.status_code == 400 and "not available" in r.json()["detail"]
        prods = client.get("/api/products").json()
        assert any(p["key"].startswith("instant-") and p.get("split_boost_offered") is False
                   for p in prods)
        off = client.post("/api/admin/split-boost-offer", headers=ADMIN_H,
                          json={"enabled": False})
        assert off.status_code == 200 and off.json()["enabled"] is False
        on = client.post("/api/admin/split-boost-offer", headers=ADMIN_H,
                         json={"enabled": True})
        assert on.status_code == 200 and on.json()["enabled"] is True
    finally:
        s = SessionLocal()
        row = s.get(AppSetting, catalog.SPLIT_BOOST_OFFERED_KEY)
        if row is not None:
            row.value = "1"
            s.commit()
        s.close()


def test_zakup_prowizjonuje_boost_i_flage_ondemand():
    _, h = _trader("addon-zakup@test.pl")
    r = client.post("/api/checkout", headers=h,
                    json={**CHECKOUT_CONSENT, "product_key": "instant-25k",
                          "split_boost": True, "ondemand_payout": True})
    assert r.status_code == 200
    oid = r.json()["order_id"]

    s = SessionLocal()
    order = s.get(Order, oid)
    assert order.addon_split_boost and order.addon_ondemand_payout
    assert order.amount_usd == INSTANT_25K + 149.0 + 399.0
    s.close()

    done = client.post(f"/api/checkout/{oid}/mock-complete", headers=h)
    assert done.status_code == 200
    s = SessionLocal()
    acc = s.get(Account, done.json()["account_id"])
    assert acc.profit_split_pct == 80          # 70 z katalogu + 10 pp boostu
    assert acc.ondemand_payout is True
    s.close()


def test_zakup_2step_ondemand_299():
    _, h = _trader("addon-2step-od@test.pl")
    r = client.post("/api/checkout", headers=h,
                    json={**CHECKOUT_CONSENT, "product_key": "2step-25k", "ondemand_payout": True})
    assert r.status_code == 200
    oid = r.json()["order_id"]
    s = SessionLocal()
    order = s.get(Order, oid)
    assert order.addon_ondemand_payout is True
    assert order.amount_usd == TWOSTEP_25K + 299.0
    s.close()
    done = client.post(f"/api/checkout/{oid}/mock-complete", headers=h)
    s = SessionLocal()
    acc = s.get(Account, done.json()["account_id"])
    assert acc.ondemand_payout is True
    s.close()


def test_zakup_bez_addonow_zostaje_przy_katalogowym_splicie():
    _, h = _trader("addon-goly@test.pl")
    oid = client.post("/api/checkout", headers=h,
                      json={**CHECKOUT_CONSENT, "product_key": "instant-25k"}).json()["order_id"]
    done = client.post(f"/api/checkout/{oid}/mock-complete", headers=h)
    s = SessionLocal()
    acc = s.get(Account, done.json()["account_id"])
    assert acc.profit_split_pct == 70 and acc.ondemand_payout is False
    s.close()


# --- Copytrading ---------------------------------------------------------- #
# Jedyny add-on, ktory kosztuje NAS pieniadze po sprzedazy: konto z nim dostaje
# realny rachunek MT5 czytany przez MetaApi. Dlatego flaga musi dojechac az na
# konto — z niej, a nie z zamowienia, bierze sie decyzja o provisioningu.

def test_copytrading_dolicza_299_na_kazdym_planie():
    """Bez ograniczenia rodziny planow — inaczej niz Split Boost."""
    _, h = _trader("addon-copy-preview@test.pl")

    instant = client.get("/api/checkout/preview?product_key=instant-25k"
                         "&copytrading=true", headers=h).json()
    dwustop = client.get("/api/checkout/preview?product_key=2step-25k"
                         "&copytrading=true", headers=h)

    assert instant["copytrading_fee_usd"] == catalog.COPYTRADING_ADDON_USD == 299.0
    assert instant["total_due_usd"] == INSTANT_25K + 299.0
    assert dwustop.status_code == 200, "2-Step tez ma prawo kupic ten dodatek"
    assert dwustop.json()["copytrading_fee_usd"] == 299.0


def test_zakup_z_copytradingiem_znaczy_zamowienie_i_konto():
    _, h = _trader("addon-copy-zakup@test.pl")

    oid = client.post("/api/checkout", headers=h,
                      json={**CHECKOUT_CONSENT, "product_key": "instant-25k",
                            "copytrading": True}).json()["order_id"]
    done = client.post(f"/api/checkout/{oid}/mock-complete", headers=h)

    assert done.status_code == 200
    s = SessionLocal()
    order, acc = s.get(Order, oid), s.get(Account, done.json()["account_id"])
    assert order.addon_copytrading is True
    assert order.amount_usd == INSTANT_25K + 299.0
    assert acc.copytrading is True
    s.close()


def test_zakup_bez_copytradingu_nie_ustawia_flagi():
    """Domyslka jest wazna: to ona pilnuje, komu zakladamy platne konto MT5."""
    _, h = _trader("addon-copy-brak@test.pl")

    oid = client.post("/api/checkout", headers=h,
                      json={**CHECKOUT_CONSENT, "product_key": "instant-25k"}).json()["order_id"]
    done = client.post(f"/api/checkout/{oid}/mock-complete", headers=h)

    s = SessionLocal()
    order, acc = s.get(Order, oid), s.get(Account, done.json()["account_id"])
    assert order.addon_copytrading is False
    assert acc.copytrading is False
    s.close()


def test_reczne_zamowienie_tez_sprzedaje_copytrading():
    """Sprzedaz poza Stripe'em (crypto, przelew) idzie WYLACZNIE ta droga —
    bez tego dodatku nie dalo sie sprzedac nikomu, kto nie placi karta."""
    r = client.post("/api/admin/orders", headers=ADMIN_H,
                    json={**CHECKOUT_CONSENT, "email": "reczne-copy@test.pl", "product_key": "instant-25k",
                          "copytrading": True})

    assert r.status_code == 200, r.text
    s = SessionLocal()
    order = s.get(Order, r.json()["id"])
    assert order.addon_copytrading is True
    assert order.amount_usd == INSTANT_25K + 299.0, "dodatek ma sie doliczyc do kwoty"
    s.close()


def test_reczna_kwota_zostaje_nietknieta():
    """Kwota wpisana recznie jest doklad­nie tym, co admin obiecal klientowi."""
    r = client.post("/api/admin/orders", headers=ADMIN_H,
                    json={**CHECKOUT_CONSENT, "email": "reczne-kwota@test.pl", "product_key": "instant-25k",
                          "copytrading": True, "amount_usd": 500.0})

    s = SessionLocal()
    order = s.get(Order, r.json()["id"])
    assert order.amount_usd == 500.0 and order.addon_copytrading is True
    s.close()


# --- ondemand w widokach wypłat ------------------------------------------- #
KAPITAL = 100_000.0

def _funded(tid, login, ondemand):
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Addon Tester",
                  product_key="2step-100k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  # Karencja dni handlu na koncie funded (poller.payout_days_left)
                  # obejmuje od 2026-09-22 takze 2-Step, wiec konto bez dni
                  # odbijaloby sie o bramke zamiast sprawdzac swoja rzecz.
                  min_trading_days=5, trading_days_count=5,
                  balance=KAPITAL + 1_000, equity=KAPITAL + 1_000,
                  peak_equity=KAPITAL + 1_000, day_start_equity=KAPITAL + 1_000,
                  day_start_balance=KAPITAL + 1_000, ondemand_payout=ondemand)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


WNIOSEK = {"method": "wise", "amount": 100, "details": {"email": "trader@example.com"}}


def test_ondemand_flaga_w_widokach_wyplat():
    """Add-on nie daje priorytetu w kolejce — tylko flagę i brak limitu 14 dni."""
    tid, h = _trader("addon-kolejka@test.pl")
    aid_od = _funded(tid, "955500001", ondemand=True)
    aid_zwykle = _funded(tid, "955500002", ondemand=False)
    with TestClient(app) as c:
        req_od = zloz_wniosek(c, aid_od, headers=h, json=WNIOSEK).json()["id"]
        req_n = zloz_wniosek(c, aid_zwykle, headers=h, json=WNIOSEK).json()["id"]

        me = c.get("/api/me/payouts", headers=h).json()
        po_id = {w["id"]: w for w in me["requests"]}
        assert po_id[req_od]["ondemand"] is True
        assert po_id[req_n]["ondemand"] is False

        lista = c.get("/api/admin/payout-requests", headers=ADMIN_H).json()
        by_id = {w["id"]: w for w in lista}
        assert by_id[req_od]["ondemand"] is True
        assert by_id[req_n]["ondemand"] is False
