"""Legal documents and the proof of consent (app/legal.py).

What these tests hold:

1. Every legal page renders, shows the same documents version, and prints
   operator details ONLY when they are configured — nothing made-up.
2. Each acceptance (sign-up, purchase, pay link, newsletter) leaves a
   `consent_records` row with the documents version and the IP, tied to the
   order where there is one.
3. Restricted countries, the age check, the mandatory reason for a refused
   reward and the removal of identity scans on account deletion are enforced
   by the server, not only described in the documents.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, legal, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Account, ConsentRecord, KycFile, Order, PayoutRequest,  # noqa: E402
                        Trader)
from tests.conftest import CHECKOUT_CONSENT  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()

settings = get_settings()
ADMIN_H = {"X-Admin-Token": settings.admin_token}
ZGODY = {"consent_terms": True, "consent_risk": True, "consent_immediate": True}
DANE = {"first_name": "Anna", "last_name": "Kowalska", "phone": "512345678", "phone_country": "PL"}
STRONY = ("/terms", "/privacy", "/refund-policy", "/risk-disclosure", "/affiliate-terms",
          "/legal", "/withdrawal", "/complaints", "/restricted-countries", "/promotions-terms")


def _trader(email, **extra):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Legal Test", referral_code=auth.secrets.token_hex(3), **extra)
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _zgody(**filtr):
    s = SessionLocal()
    try:
        return s.query(ConsentRecord).filter_by(**filtr).all()
    finally:
        s.close()


# ---------------- pages ----------------
def test_kazda_strona_prawna_renderuje_sie_z_ta_sama_wersja():
    with TestClient(app) as c:
        for sciezka in STRONY:
            r = c.get(sciezka)
            assert r.status_code == 200, sciezka
            assert f"Version {legal.DOCS_VERSION}" in r.text, sciezka
        mapa = c.get("/sitemap.xml").text
        stopka = c.get("/").text
    for sciezka in ("/legal", "/withdrawal", "/complaints", "/restricted-countries"):
        assert f"{sciezka}</loc>" in mapa
        assert f'href="{sciezka}"' in stopka


def test_dane_operatora_tylko_gdy_ustawione(monkeypatch):
    """Empty LEGAL_* settings print no company at all; filled ones appear in the
    Terms, the Privacy Policy, the Legal page and the footer."""
    with TestClient(app) as c:
        assert "Registration:" not in c.get("/legal").text
        assert "is operated by" not in c.get("/").text
        monkeypatch.setattr(settings, "legal_entity_name", "Probe Trading sp. z o.o.")
        monkeypatch.setattr(settings, "legal_reg_no", "KRS 0000123456")
        monkeypatch.setattr(settings, "legal_address", "ul. Testowa 1, 00-001 Warszawa")
        monkeypatch.setattr(settings, "legal_governing_law", "Poland")
        for sciezka in ("/legal", "/terms", "/privacy"):
            html = c.get(sciezka).text
            assert "Probe Trading sp. z o.o." in html and "KRS 0000123456" in html, sciezka
        assert "governed by the law of Poland" in c.get("/terms").text
        assert "is operated by Probe Trading sp. z o.o." in c.get("/").text


def test_regulamin_mowi_prawde_o_platformie_i_tokenie():
    with TestClient(app) as c:
        terms, faq, home = c.get("/terms").text, c.get("/faq").text, c.get("/").text
    assert "do not work in the MetaTrader 5 desktop, mobile or web applications" in terms
    assert "may not be convertible into money" in terms
    # Named price venues come with an explicit no-affiliation statement.
    assert "not affiliated with, sponsored by, endorsed by" in terms
    assert all(nazwa in terms for nazwa in ("Hyperliquid", "Binance", "Bybit"))
    assert "not affiliated with, sponsored by, endorsed by" in home
    # A refused reward has a closed list of reasons and is explained in writing.
    assert "only for one of these reasons" in terms and "tell you the reason in writing" in terms
    # The public pages no longer promise MetaTrader apps for every account.
    assert "also work in MT5 on desktop" not in faq and "Powered by" not in home
    assert "We open an MT5 account for you" not in home


# ---------------- consent proof ----------------
def test_rejestracja_zostawia_dowod_zgody_z_wersja_i_ip():
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", headers={"x-forwarded-for": "203.0.113.7"},
                   json={"email": "zgoda-signup@probe.test", "password": "haslo1234",
                         "terms_accepted": True})
        assert r.status_code == 200
    wiersze = _zgody(trader_id=r.json()["trader"]["id"], kind="signup")
    assert len(wiersze) == 1
    assert wiersze[0].doc_version == legal.DOCS_VERSION and wiersze[0].ip == "203.0.113.7"


def test_zakup_zapisuje_zgody_przy_zamowieniu():
    tid, h = _trader("zgoda-checkout@probe.test")
    with TestClient(app) as c:
        r = c.post("/api/checkout", headers=h,
                   json={**CHECKOUT_CONSENT, **DANE, "product_key": "2step-25k"})
        assert r.status_code == 200
        bez = c.post("/api/checkout", headers=h, json={**DANE, "product_key": "2step-25k"})
        assert bez.status_code == 400
    wiersze = _zgody(trader_id=tid, kind="checkout")
    assert len(wiersze) == 1 and wiersze[0].order_id == r.json()["order_id"]
    assert wiersze[0].doc_version == legal.DOCS_VERSION


def test_link_platnosci_wymaga_oswiadczen_i_je_zapisuje():
    tid, _ = _trader("zgoda-paylink@probe.test")
    s = SessionLocal()
    o = Order(trader_id=tid, product_key="2step-25k", amount_usd=299.0, status="pending",
              pay_token="tok-legal-probe-1")
    s.add(o); s.commit()
    oid = o.id
    s.close()
    with TestClient(app) as c:
        assert c.post("/api/pay/tok-legal-probe-1/start").status_code == 400
        assert _zgody(order_id=oid) == []
        assert c.post("/api/pay/tok-legal-probe-1/start", json=ZGODY).status_code == 200
    wiersze = _zgody(order_id=oid, kind="pay_link")
    assert len(wiersze) == 1 and wiersze[0].trader_id == tid


def test_newsletter_bez_zgody_nie_zapisuje():
    with TestClient(app) as c:
        # No `consent` field at all used to be treated as consent.
        assert c.post("/api/newsletter/subscribe", json={"email": "nl-bez@probe.test"}).status_code == 400
        assert c.post("/api/newsletter/subscribe",
                      json={"email": "nl-tak@probe.test", "consent": True}).status_code == 200
        stopka = c.get("/").text
    assert _zgody(email="nl-bez@probe.test") == []
    assert len(_zgody(email="nl-tak@probe.test", kind="newsletter")) == 1
    # The footer form now has its own unticked box.
    formularz = stopka.split('data-source="footer"')[1].split("</form>")[0]
    assert 'type="checkbox" name="consent"' in formularz and "checked" not in formularz


# ---------------- restricted countries and age ----------------
def test_kraj_wylaczony_nie_zarejestruje_sie_ani_nie_kupi():
    assert legal.is_restricted("ir") and not legal.is_restricted("PL") and not legal.is_restricted(None)
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", headers={"x-vercel-ip-country": "IR"},
                   json={"email": "kraj-ir@probe.test", "password": "haslo1234", "terms_accepted": True})
        assert r.status_code == 403
        ok = c.post("/api/auth/signup", headers={"x-vercel-ip-country": "PL"},
                    json={"email": "kraj-pl@probe.test", "password": "haslo1234", "terms_accepted": True})
        assert ok.status_code == 200
        _, h = _trader("kraj-kupuje@probe.test")
        kup = c.post("/api/checkout", headers={**h, "x-vercel-ip-country": "KP"},
                     json={**CHECKOUT_CONSENT, **DANE, "product_key": "2step-25k"})
        assert kup.status_code == 403
        lista = c.get("/restricted-countries").text
    s = SessionLocal()
    assert s.query(Trader).filter(Trader.email == "kraj-ir@probe.test").first() is None
    s.close()
    assert "Iran" in lista and "Cuba" in lista


def test_lista_krajow_idzie_z_konfiguracji(monkeypatch):
    monkeypatch.setattr(settings, "restricted_countries", "KP, ir ,xx1,")
    assert legal.restricted_codes() == frozenset({"KP", "IR"})
    monkeypatch.setattr(settings, "restricted_countries", "")
    assert legal.restricted_codes() == frozenset() and not legal.is_restricted("IR")


@pytest.mark.parametrize("dob,wynik", [("2000-01-15", True), ("2015-06-01", False),
                                       ("", None), ("15/01/2000", None)])
def test_pelnoletnosc_z_daty_urodzenia(dob, wynik):
    assert legal.adult(dob) is wynik


def test_kyc_odrzuca_niepelnoletniego_i_kraj_wylaczony():
    tid, h = _trader("kyc-legal@probe.test")
    s = SessionLocal()
    s.add(Account(login="970001", trader_id=tid, trader_name="Legal Test", platform_login="970001",
                  platform_password="x", platform_server="PTF-sim", product_key="2step-25k",
                  initial_balance=25_000.0, balance=25_000.0, equity=25_000.0, peak_equity=25_000.0,
                  day_start_equity=25_000.0, day_start_balance=25_000.0, status="funded",
                  phase="funded"))
    s.commit(); s.close()
    baza = {"full_name": "Legal Test", "country": "Poland", "dob": "1990-05-05",
            "address": "ul. Testowa 1", "id_type": "Passport", "id_number": "AB123456"}
    with TestClient(app) as c:
        assert c.post("/api/me/kyc", headers=h, json={**baza, "dob": "2012-05-05"}).status_code == 400
        assert c.post("/api/me/kyc", headers=h, json={**baza, "country": "Iran"}).status_code == 403
        assert c.post("/api/me/kyc", headers=h, json=baza).status_code == 200


# ---------------- payouts and deletion ----------------
def test_odrzucenie_wyplaty_wymaga_powodu():
    tid, _ = _trader("reject-legal@probe.test")
    s = SessionLocal()
    acc = Account(login="970002", trader_id=tid, trader_name="Legal Test", platform_login="970002",
                  platform_password="x", platform_server="PTF-sim", product_key="2step-25k",
                  initial_balance=25_000.0, balance=27_000.0, equity=27_000.0, peak_equity=27_000.0,
                  day_start_equity=27_000.0, day_start_balance=27_000.0, status="funded",
                  phase="funded")
    s.add(acc); s.flush()
    req = PayoutRequest(trader_id=tid, account_id=acc.id, profit_amount=1000.0,
                        trader_share=900.0, status="review", method="vusdt")
    s.add(req); s.commit()
    rid = req.id
    s.close()
    with TestClient(app) as c:
        url = f"/api/admin/payout-requests/{rid}/reject"
        assert c.post(url, headers=ADMIN_H, json={"reason": "   "}).status_code == 400
        assert c.post(url, headers=ADMIN_H, json={}).status_code == 400
        r = c.post(url, headers=ADMIN_H, json={"reason": "Identity does not match the account holder"})
        assert r.status_code == 200 and r.json()["reason"]


def test_usuniecie_konta_kasuje_skany_i_portfel():
    tid, h = _trader("delete-legal@probe.test", payout_address="0x" + "a" * 40,
                     payout_network="polygon", google_sub="g-123", signup_country="PL",
                     password_set=True)
    s = SessionLocal()
    s.add(KycFile(trader_id=tid, kind="id_front", filename="id.png", mime="image/png",
                  data=b"\x89PNG"))
    legal.record_consent(s, kind="signup", trader_id=tid)
    s.commit(); s.close()
    with TestClient(app) as c:
        r = c.post("/api/me/delete", headers=h, json={"password": "haslo1234"})
        assert r.status_code == 200
    s = SessionLocal()
    tr = s.get(Trader, tid)
    assert s.query(KycFile).filter(KycFile.trader_id == tid).count() == 0
    assert tr.payout_address is None and tr.google_sub is None and tr.signup_country is None
    # The proof of what was accepted survives the anonymisation.
    assert s.query(ConsentRecord).filter(ConsentRecord.trader_id == tid).count() == 1
    s.close()


# ---------------- e-mail ----------------
def test_mail_z_danymi_mowi_gdzie_dzialaja_i_potwierdza_zakup():
    wspolne = {"name": "Anna", "login": "970003", "platform_login": "970003",
               "platform_password": "Haslo1", "initial_balance": 25000, "steps": 2}
    _, sim = notify._render("credentials", {**wspolne, "platform_server": "PTF-sim"})
    assert "/trade" in sim and "do not work in the MetaTrader 5 apps" in sim
    assert "Log in with MT5 (desktop" not in sim
    assert legal.DOCS_VERSION in sim and "/withdrawal" in sim and "/refund-policy" in sim
    sim_html = notify._render_html("credentials", {**wspolne, "platform_server": "PTF-sim"}, "x")
    assert "Built-in Terminal" in sim_html and "challenge on MT5" not in sim_html
    assert legal.DOCS_VERSION in sim_html
    # A real MetaTrader account keeps the MT5 instructions.
    _, mt5 = notify._render("credentials", {**wspolne, "platform_server": "MetaQuotes-Demo"})
    assert "Log in with MT5 (desktop, mobile or web)" in mt5
    # A granted account is not a purchase: no checkout declarations to confirm.
    _, grant = notify._render("challenge_granted", {**wspolne, "platform_server": "PTF-sim"})
    assert "At checkout you declared" not in grant
