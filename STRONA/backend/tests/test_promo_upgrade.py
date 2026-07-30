"""Promocja „Upgrade Your Size": zakup z POPRAWNYM kodem promo provisionuje
NASTĘPNY plan w górę, a klient płaci cenę WYBRANEGO tieru. Bez kodu (albo ze
złym) zakup działa klasycznie — nawet gdy promocja trwa.

Promocja jest w conftest wyłączona (żeby pasek nie pojawiał się w testach stron
publicznych), więc każdy test tutaj włącza ją sam.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)

LICZNIK = iter(range(1000))
KOD = "UPGRADE"


@pytest.fixture
def promo(monkeypatch):
    """Promocja włączona, bez daty końcowej, znany kod."""
    monkeypatch.setattr(catalog.settings, "promo_upgrade", True)
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "")
    monkeypatch.setattr(catalog.settings, "promo_upgrade_code", KOD)


def _trader() -> int:
    email = f"promo{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Promo Tester", referral_code=email[:8].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _kup(product_key, promo_code=None, **kw):
    """Zwraca (product_key, bogo_paid_key, amount, saldo, bogo_paid_size)
    po zakupie i domknięciu płatności w trybie mock."""
    tid = _trader()
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, product_key, kw.pop("coupon", None),
                                  promo_code=promo_code, **kw)
    order = s.get(Order, res["order_id"])
    done = billing.mock_complete(s, order.id, tid)
    acc = s.get(Account, done["account_id"])
    dane = (order.product_key, order.bogo_paid_key, order.amount_usd,
            acc.initial_balance, acc.bogo_paid_size)
    s.close()
    return dane


def test_kod_daje_nastepny_rozmiar_za_cene_oplaconego(promo):
    key, oplacony, kwota, saldo, bogo = _kup("2step-50k", promo_code="upgrade")  # case-insensitive
    assert key == "2step-100k"          # nastepny plan w gore
    assert oplacony == "2step-50k"      # ale zamowienie wie, za co zaplacono
    assert kwota == 349                 # CENA WYBRANEGO tieru, nie wiekszego (549)
    assert saldo == 100_000
    assert bogo == 50_000               # z tego pola zyje mail i baner w portalu


def test_bez_kodu_brak_upgradeu_mimo_aktywnej_promocji(promo):
    key, oplacony, kwota, saldo, bogo = _kup("2step-50k")
    assert key == "2step-50k" and oplacony is None and bogo is None
    assert kwota == 349 and saldo == 50_000


def test_zly_kod_nie_daje_upgradeu_ani_nie_blokuje_zakupu(promo):
    key, oplacony, kwota, saldo, _ = _kup("2step-50k", promo_code="DOUBLE")
    assert key == "2step-50k" and oplacony is None
    assert kwota == 349 and saldo == 50_000


def test_kupon_i_addon_sumuja_sie_z_kodem_promo(promo):
    key, oplacony, kwota, saldo, _ = _kup("2step-50k", promo_code=KOD,
                                          coupon="WELCOME10", weekend_trading=True)
    assert key == "2step-100k" and oplacony == "2step-50k"
    assert kwota == round(349 * 0.9, 2) + 199
    assert saldo == 100_000


def test_promocja_nie_rusza_najwiekszego_planu(promo):
    key, oplacony, kwota, saldo, bogo = _kup("2step-2m", promo_code=KOD)
    assert key == "2step-2m" and oplacony is None and bogo is None
    assert kwota == 5999 and saldo == 2_000_000


def test_upgrade_zostaje_w_swojej_rodzinie(promo):
    """Instant Funding ma inne limity i split — nie mieszamy rodzin."""
    key, oplacony, _, saldo, _ = _kup("instant-50k", promo_code=KOD)
    assert key == "instant-100k" and oplacony == "instant-50k"
    assert saldo == 100_000


def test_przy_wylaczonej_promocji_kod_jest_martwy():
    key, oplacony, kwota, saldo, bogo = _kup("2step-50k", promo_code=KOD)
    assert key == "2step-50k" and oplacony is None and bogo is None
    assert kwota == 349 and saldo == 50_000


def test_po_terminie_promocja_gasnie_wszedzie(monkeypatch):
    """Jedna bramka dla tresci i mechaniki: po dacie ani pasek, ani kod."""
    monkeypatch.setattr(catalog.settings, "promo_upgrade", True)
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "2020-01-01")
    monkeypatch.setattr(catalog.settings, "promo_upgrade_code", KOD)
    assert catalog.promo_active() is False
    assert client.get(f"/api/promo?code={KOD}").json()["valid"] is False
    assert all(p["promo_upgrade_size"] is None for p in client.get("/api/products").json())
    key, oplacony, _, saldo, _ = _kup("2step-50k", promo_code=KOD)
    assert key == "2step-50k" and oplacony is None and saldo == 50_000
    # data w przyszlosci -> promocja dziala
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "2099-12-31")
    assert catalog.promo_active() is True
    # zla data w configu = promocja WYLACZONA, nie „na wiecznosc"
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "za tydzien")
    assert catalog.promo_active() is False


def test_api_promo_waliduje_kod_dla_belki(promo):
    assert client.get(f"/api/promo?code={KOD}").json()["valid"] is True
    assert client.get("/api/promo?code=upgrade").json()["valid"] is True
    assert client.get("/api/promo?code=NOPE").json()["valid"] is False
    assert client.get("/api/promo").json()["valid"] is False


def test_api_products_podaje_nastepny_rozmiar_dla_landingu(promo):
    ps = {p["key"]: p for p in client.get("/api/products").json()}
    # nastepny plan w gore, nie ×2 — 2M (najwiekszy) bez celu
    assert ps["2step-10k"]["promo_upgrade_size"] == 25_000
    assert ps["2step-50k"]["promo_upgrade_size"] == 100_000
    assert ps["2step-50k"]["promo_upgrade_label"] == "2-Step 100K"
    assert ps["2step-200k"]["promo_upgrade_size"] == 300_000
    assert ps["2step-300k"]["promo_upgrade_size"] == 400_000
    assert ps["2step-800k"]["promo_upgrade_size"] == 1_000_000
    assert ps["2step-1m"]["promo_upgrade_size"] == 2_000_000
    assert ps["2step-2m"]["promo_upgrade_size"] is None
    assert ps["instant-50k"]["promo_upgrade_label"].startswith("Instant")


def test_mail_z_poswiadczeniami_mowi_o_upgradzie():
    """Klient kupil 50K, dostal konto 100K — mail MUSI to wyjasnic, inaczej
    wyglada to na pomylke po naszej stronie."""
    from app import notify
    ctx = {"name": "Vera", "platform_login": "1", "platform_password": "x",
           "platform_server": "MQ-Demo", "initial_balance": 100_000.0, "steps": 2,
           "bogo_paid_size": 50_000.0}
    tekst = notify._render("credentials", ctx)[1]
    assert "you paid for the $50K tier" in tekst and "$100,000" in tekst
    html = notify._render_html("credentials", ctx, "x")
    assert catalog.PROMO_NAME in html
    # zakup bez promocji: ani slowa o upgradzie
    bez = notify._render("credentials", {**ctx, "bogo_paid_size": None})[1]
    assert "paid for the" not in bez


def test_wylacznik_gasi_takze_tresc_na_stronie(monkeypatch):
    monkeypatch.setattr(catalog.settings, "promo_upgrade", False)
    assert "promo-bar" not in client.get("/").text
    monkeypatch.setattr(catalog.settings, "promo_upgrade", True)
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "")
    html = client.get("/").text
    assert "promo-bar" in html and "has-promo" in html
    # belka ma przycisk aktywacji kodu i input — bez nich promocji nie da sie wlaczyc
    assert 'id="promoApply"' in html and 'id="promoInp"' in html
    assert "next size up" in html and "double" not in html.lower()
