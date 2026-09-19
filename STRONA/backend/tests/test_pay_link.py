"""Link do płatności wystawiony ręcznie z panelu (/pay/<token>).

Ścieżka: klient pisze na Telegramie, admin robi mu zamówienie i wysyła JEDEN
link. Trzy rzeczy muszą tu trzymać, bo inaczej pieniądze wpadają w próżnię:

1. Sesja Stripe'a powstaje po NASZEJ stronie i ląduje w `order.stripe_session_id`.
   Webhook odrzuca każdą sesję, której id nie zgadza się z zamówieniem — gotowy
   Payment Link ze Stripe'a przeszedłby płatność, a konto nigdy by nie powstało.
2. Link nie wygasa: token jest stały, a kasa otwiera się dopiero po kliknięciu.
   Sesje Stripe'a żyją 24 h, więc link „na sztywno" umarłby po dobie.
3. Strona jest publiczna, więc nie ma prawa pokazać maila ani nazwiska klienta —
   link bywa przeklejany dalej.
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
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))

MAIL_KLIENTA = "paylink{}@test.pl"


class _FakeStripe:
    """Atrapa SDK: zapamiętuje wywołania `Session.create` i przepuszcza podpis
    webhooka. `error` zostaje prawdziwe, żeby `except StripeError` łapało realną
    hierarchię wyjątków."""
    error = stripe_sdk.error
    wywolania: list = []
    licznik = iter(range(1000))

    class checkout:
        class Session:
            @staticmethod
            def create(**kw):
                sid = f"cs_test_{next(_FakeStripe.licznik)}"
                _FakeStripe.wywolania.append((sid, kw))
                return type("CS", (), {"id": sid, "url": f"https://checkout.stripe.com/{sid}"})

    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            return json.loads(payload.decode())


@pytest.fixture
def stripe_wlaczony(monkeypatch):
    _FakeStripe.wywolania = []
    monkeypatch.setattr(billing.settings, "stripe_secret_key", "sk_test_atrapa")
    monkeypatch.setattr(billing.settings, "stripe_webhook_secret", "whsec_atrapa")
    monkeypatch.setattr(billing, "_stripe", lambda: _FakeStripe)
    return _FakeStripe


def _trader() -> tuple[int, str]:
    email = MAIL_KLIENTA.format(next(LICZNIK))
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Link Tester", first_name="Jan", last_name="Kowalski",
                phone="+48507480663", phone_country="PL",
                referral_code=email.split("@")[0].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, email


def _zamowienie(**kw) -> tuple[int, int, str]:
    """Ręczne zamówienie z panelu; zwraca (order_id, trader_id, e-mail)."""
    tid, email = _trader()
    body = {"trader_id": tid, "product_key": "2step-25k", "flag": "",
            "notify_trader": False, **kw}
    r = client.post("/api/admin/orders", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["id"], tid, email


def _link(order_id: int) -> str:
    r = client.post(f"/api/admin/orders/{order_id}/pay-link", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _token(order_id: int) -> str:
    return _link(order_id).rsplit("/", 1)[1]


# --------------------------------------------------------------------------- #
#  Wystawianie linku                                                           #
# --------------------------------------------------------------------------- #
def test_link_prowadzi_na_nasza_domene_a_nie_do_stripe():
    """Klient ma zobaczyć naszą stronę z kwotą, zanim wyjdzie do kasy."""
    oid, _, _ = _zamowienie()
    url = _link(oid)
    assert "/pay/" in url and "stripe.com" not in url
    assert url.startswith("http")


def test_ten_sam_przycisk_daje_ten_sam_link():
    """Drugie kliknięcie nie może unieważnić linku, którego klient już używa."""
    oid, _, _ = _zamowienie()
    assert _link(oid) == _link(oid)


def test_token_jest_nieodgadywalny():
    oid, _, _ = _zamowienie()
    oid2, _, _ = _zamowienie()
    t1, t2 = _token(oid), _token(oid2)
    assert t1 != t2 and len(t1) >= 16
    # Numer zamówienia w tokenie = każdy link daje się wyliczyć z sąsiedniego.
    assert str(oid) not in t1


def test_link_do_oplaconego_zamowienia_sie_nie_wystawia():
    oid, _, _ = _zamowienie()
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    r = client.post(f"/api/admin/orders/{oid}/pay-link", headers=ADMIN)
    assert r.status_code == 400 and "paid" in r.json()["detail"]


def test_link_wystawia_tylko_admin():
    oid, tid, _ = _zamowienie()
    assert client.post(f"/api/admin/orders/{oid}/pay-link").status_code in (401, 403)
    r = client.post(f"/api/admin/orders/{oid}/pay-link",
                    headers={"Authorization": f"Bearer {auth.make_token(tid)}"})
    assert r.status_code in (401, 403)


# --------------------------------------------------------------------------- #
#  Strona płatności                                                            #
# --------------------------------------------------------------------------- #
def test_strona_pokazuje_kwote_i_produkt():
    oid, _, _ = _zamowienie(amount_usd=199.5)
    r = client.get(f"/pay/{_token(oid)}")
    assert r.status_code == 200
    assert "$199.50" in r.text, "klient nie widzi, ile ma zapłacić"
    assert f"PTF-{oid}" in r.text
    assert "2-Step 25K challenge" in r.text, "klient nie widzi, za co płaci"


def test_okragla_kwota_bez_groszy():
    oid, _, _ = _zamowienie(amount_usd=199)
    assert "$199 " in client.get(f"/pay/{_token(oid)}").text.replace("<", " ")


def test_strona_nie_zdradza_kim_jest_klient():
    """Link wędruje po czatach — mail i nazwisko kupującego zostają u nas."""
    oid, _, email = _zamowienie()
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert email not in tresc and "Kowalski" not in tresc


def test_nieznany_token_to_strona_404_a_nie_goly_json():
    r = client.get("/pay/kompletnie-zmyslony-token")
    assert r.status_code == 404
    assert "<html" in r.text.lower(), "klient dostał surowy JSON zamiast strony"
    assert "not valid" in r.text


def test_oplacone_zamowienie_nie_ma_juz_przycisku():
    oid, _, _ = _zamowienie()
    token = _token(oid)
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    tresc = client.get(f"/pay/{token}").text
    assert "Already paid" in tresc
    assert "Pay $" not in tresc, "opłacone zamówienie dalej zaprasza do zapłaty"


def test_zamkniete_zamowienie_nie_ma_juz_przycisku():
    oid, _, _ = _zamowienie()
    token = _token(oid)
    client.post(f"/api/admin/orders/{oid}/mark-failed",
                json={"reason": "Duplicate order"}, headers=ADMIN)
    tresc = client.get(f"/pay/{token}").text
    assert "no longer active" in tresc and "Pay $" not in tresc
    # Wewnętrzny powód zamknięcia zostaje w panelu, nie leci do klienta.
    assert "Duplicate order" not in tresc


def test_strona_nie_wchodzi_do_wyszukiwarki():
    oid, _, _ = _zamowienie()
    assert "noindex" in client.get(f"/pay/{_token(oid)}").text


def test_strona_nie_obiecuje_promocji_ktorej_to_zamowienie_nie_dostanie(monkeypatch):
    """Belka „kup challenge, dostaniesz rozmiar wyżej" to mechanika CHECKOUTU.
    Kwota z panelu jest ustalona ręcznie i żadnego upgrade'u nie uruchamia —
    obietnica nad przyciskiem „Zapłać" byłaby wprost nieprawdziwa.

    Promocja musi tu być WŁĄCZONA, inaczej test przechodzi sam z siebie i nie
    pilnuje niczego (domyślnie `promo_upgrade` jest wyłączone)."""
    monkeypatch.setattr(catalog.settings, "promo_upgrade", True)
    monkeypatch.setattr(catalog.settings, "promo_upgrade_ends", None)
    assert "promo-bar" in client.get("/").text, "promocja nie wstała — test byłby ślepy"
    oid, _, _ = _zamowienie()
    assert "promo-bar" not in client.get(f"/pay/{_token(oid)}").text


def test_naglowek_nie_wchodzi_pod_pasek_nawigacji():
    """Nawigacja jest `position:fixed`, więc pierwsza sekcja musi zarezerwować
    na nią miejsce. Zmierzone w przeglądarce na goŁym `.sec`: przy 390 px pigułka
    „Secure checkout" siedziała na wysokości 66 px, a pasek kończył się na 103 —
    tytuł strony płatności był przykryty. `.page-head` liczy padding z --promo-h."""
    oid, _, _ = _zamowienie()
    assert 'class="page-head"' in client.get(f"/pay/{_token(oid)}").text


# --------------------------------------------------------------------------- #
#  Kasa                                                                        #
# --------------------------------------------------------------------------- #
def test_klikniecie_tworzy_sesje_stripe_i_zapisuje_ja_przy_zamowieniu(stripe_wlaczony):
    """SEDNO całego rozwiązania: bez `stripe_session_id` webhook zignoruje wpłatę."""
    oid, _, _ = _zamowienie(amount_usd=199.5)
    r = client.post(f"/api/pay/{_token(oid)}/start")
    assert r.status_code == 200
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    s = SessionLocal(); o = s.get(Order, oid); sid = o.stripe_session_id; s.close()
    assert sid and sid == stripe_wlaczony.wywolania[-1][0]


def test_kasa_pobiera_dokladnie_kwote_z_zamowienia(stripe_wlaczony):
    oid, _, _ = _zamowienie(amount_usd=199.5)
    client.post(f"/api/pay/{_token(oid)}/start")
    kw = stripe_wlaczony.wywolania[-1][1]
    assert kw["line_items"][0]["price_data"]["unit_amount"] == 19950
    # Bez tego webhook nie ma jak trafić z powrotem w zamówienie.
    assert kw["metadata"]["order_id"] == str(oid)


def test_platnosc_z_linku_zaklada_konto(stripe_wlaczony):
    """Pełna pętla: link -> kasa -> webhook -> konto tradera."""
    oid, tid, _ = _zamowienie()
    client.post(f"/api/pay/{_token(oid)}/start")
    s = SessionLocal(); o = s.get(Order, oid); sid, kwota = o.stripe_session_id, o.amount_usd; s.close()
    zdarzenie = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "id": sid, "payment_status": "paid", "client_reference_id": str(oid),
        "amount_total": int(round(kwota * 100)), "currency": billing.settings.currency}}})
    r = client.post("/api/stripe/webhook", content=zdarzenie,
                    headers={"stripe-signature": "t=1,v1=atrapa"})
    assert r.json().get("provisioned") is True, r.text
    s = SessionLocal(); o = s.get(Order, oid)
    assert o.status == "paid" and o.account_id
    s.close()


def test_powtorne_wejscie_daje_nowa_sesje_wiec_link_nie_wygasa(stripe_wlaczony):
    """Sesje Stripe'a żyją 24 h. Gdyby link niósł jedną na stałe, po dobie
    prowadziłby do martwej strony — dlatego kasa powstaje przy KAŻDYM kliknięciu,
    a przy zamówieniu zostaje ta ostatnia."""
    oid, _, _ = _zamowienie()
    token = _token(oid)
    a = client.post(f"/api/pay/{token}/start").json()["checkout_url"]
    b = client.post(f"/api/pay/{token}/start").json()["checkout_url"]
    assert a != b
    s = SessionLocal(); o = s.get(Order, oid); s.close()
    assert o.stripe_session_id == b.rsplit("/", 1)[1]


def test_stare_zamowienie_nie_domyka_starej_sesji(stripe_wlaczony):
    """Po odświeżeniu linku POPRZEDNIA sesja przestaje domykać zamówienie.
    Klient płaci w tej, którą ma otwartą — a to zawsze ta ostatnio wydana."""
    oid, _, _ = _zamowienie()
    token = _token(oid)
    client.post(f"/api/pay/{token}/start")
    stara = stripe_wlaczony.wywolania[-1][0]
    client.post(f"/api/pay/{token}/start")
    s = SessionLocal(); o = s.get(Order, oid); kwota = o.amount_usd; s.close()
    zdarzenie = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "id": stara, "payment_status": "paid", "client_reference_id": str(oid),
        "amount_total": int(round(kwota * 100)), "currency": billing.settings.currency}}})
    r = client.post("/api/stripe/webhook", content=zdarzenie,
                    headers={"stripe-signature": "t=1,v1=atrapa"})
    assert r.json().get("ignored") == "session mismatch"


def test_kasa_odmawia_gdy_zamowienie_juz_oplacone(stripe_wlaczony):
    oid, _, _ = _zamowienie()
    token = _token(oid)
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    r = client.post(f"/api/pay/{token}/start")
    assert r.status_code == 400 and "already been paid" in r.json()["detail"]
    assert stripe_wlaczony.wywolania == [], "sesja płatności za opłacone zamówienie"


def test_kasa_odmawia_gdy_zamowienie_zamkniete(stripe_wlaczony):
    oid, _, _ = _zamowienie()
    token = _token(oid)
    client.post(f"/api/admin/orders/{oid}/mark-failed", json={"reason": "x"}, headers=ADMIN)
    r = client.post(f"/api/pay/{token}/start")
    assert r.status_code == 400
    assert stripe_wlaczony.wywolania == []


def test_kasa_nieznanego_tokenu_to_404(stripe_wlaczony):
    assert client.post("/api/pay/zmyslony/start").status_code == 404
    assert stripe_wlaczony.wywolania == []


def test_bez_kluczy_stripe_link_prowadzi_do_mocka():
    """Dev bez kluczy: link nie ma prawa wywalić się 500 — domyka portal."""
    oid, _, _ = _zamowienie()
    r = client.post(f"/api/pay/{_token(oid)}/start")
    assert r.status_code == 200 and f"mock_order={oid}" in r.json()["checkout_url"]
