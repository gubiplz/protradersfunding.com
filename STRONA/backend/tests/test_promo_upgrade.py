"""Promocja „Double your challenge size": zakup provisionuje konto co najmniej
dwa razy większe, a klient płaci cenę WYBRANEGO tieru.

Promocja jest w conftest wyłączona (inaczej rozjechałyby się asercje o rozmiarach
kont w pozostałych testach), więc każdy test tutaj włącza ją sam.
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


@pytest.fixture
def promo(monkeypatch):
    """Promocja włączona, bez daty końcowej."""
    monkeypatch.setattr(catalog.settings, "promo_upgrade", True)
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "")


def _trader() -> int:
    email = f"promo{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Promo Tester", referral_code=email[:8].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _kup(product_key, **kw):
    """Zwraca (order, account) po zakupie i domknięciu płatności w trybie mock."""
    tid = _trader()
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, product_key, kw.pop("coupon", None), **kw)
    order = s.get(Order, res["order_id"])
    done = billing.mock_complete(s, order.id, tid)
    acc = s.get(Account, done["account_id"])
    dane = (order.product_key, order.bogo_paid_key, order.amount_usd,
            acc.initial_balance, acc.bogo_paid_size)
    s.close()
    return dane


def test_promocja_provisionuje_wyzszy_tier_za_cene_oplaconego(promo):
    key, oplacony, kwota, saldo, bogo = _kup("2step-50k")
    assert key == "2step-100k"          # konto o rozmiarze x2
    assert oplacony == "2step-50k"      # ale zamowienie wie, za co zaplacono
    assert kwota == 349                 # CENA WYBRANEGO tieru, nie wiekszego (549)
    assert saldo == 100_000
    assert bogo == 50_000               # z tego pola zyje mail i baner w portalu


def test_kupon_i_addon_nie_podnosza_ceny_do_wyzszego_tieru(promo):
    key, oplacony, kwota, saldo, _ = _kup("2step-50k", coupon="WELCOME10",
                                          weekend_trading=True)
    assert key == "2step-100k" and oplacony == "2step-50k"
    assert kwota == round(349 * 0.9, 2) + 199
    assert saldo == 100_000


def test_promocja_nie_rusza_najwiekszego_planu(promo):
    key, oplacony, kwota, saldo, bogo = _kup("2step-2m")
    assert key == "2step-2m" and oplacony is None and bogo is None
    assert kwota == 5999 and saldo == 2_000_000


def test_upgrade_zostaje_w_swojej_rodzinie(promo):
    """Instant Funding ma inne limity i split — nie mieszamy rodzin."""
    key, oplacony, _, saldo, _ = _kup("instant-50k")
    assert key == "instant-100k" and oplacony == "instant-50k"
    assert saldo == 100_000


def test_bez_promocji_klient_dostaje_dokladnie_to_co_wybral():
    key, oplacony, kwota, saldo, bogo = _kup("2step-50k")
    assert key == "2step-50k" and oplacony is None and bogo is None
    assert kwota == 349 and saldo == 50_000


def test_po_terminie_promocja_gasnie_wszedzie(monkeypatch):
    """Jedna bramka dla tresci i mechaniki: po dacie ani pasek, ani upgrade."""
    monkeypatch.setattr(catalog.settings, "promo_upgrade", True)
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "2020-01-01")
    assert catalog.promo_active() is False
    assert all(p["promo_upgrade_size"] is None for p in client.get("/api/products").json())
    key, oplacony, _, saldo, _ = _kup("2step-50k")
    assert key == "2step-50k" and oplacony is None and saldo == 50_000
    # data w przyszlosci -> promocja dziala
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "2099-12-31")
    assert catalog.promo_active() is True
    # zla data w configu = promocja WYLACZONA, nie „na wieczność"
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", "za tydzien")
    assert catalog.promo_active() is False


def test_api_products_podaje_cel_upgradeu_dla_landingu(promo):
    ps = {p["key"]: p for p in client.get("/api/products").json()}
    assert ps["2step-50k"]["promo_upgrade_size"] == 100_000
    assert ps["2step-50k"]["promo_upgrade_label"] == "2-Step 100K"
    # drabinka katalogu nie skacze rowno x2 — cel to PIERWSZY plan >= 2x,
    # wiec haslo „double" jest prawda wszedzie
    assert ps["2step-10k"]["promo_upgrade_size"] == 25_000
    assert ps["2step-200k"]["promo_upgrade_size"] == 400_000
    assert ps["2step-300k"]["promo_upgrade_size"] == 800_000
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
