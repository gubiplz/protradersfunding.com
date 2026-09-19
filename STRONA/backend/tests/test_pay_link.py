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
    wygaszone: list = []
    licznik = iter(range(1000))

    class checkout:
        class Session:
            @staticmethod
            def create(**kw):
                sid = f"cs_test_{next(_FakeStripe.licznik)}"
                _FakeStripe.wywolania.append((sid, kw))
                return type("CS", (), {"id": sid, "url": f"https://checkout.stripe.com/{sid}"})

            @staticmethod
            def expire(sid):
                _FakeStripe.wygaszone.append(sid)

    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            return json.loads(payload.decode())


@pytest.fixture
def stripe_wlaczony(monkeypatch):
    _FakeStripe.wywolania = []
    _FakeStripe.wygaszone = []
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
    # Sprawdzamy wspólny prefiks, nie `str(oid) not in t1`: jednocyfrowy numer
    # trafia się w losowym tokenie co czwarty przebieg i test rzucał monetą.
    assert t1[:8] != t2[:8]


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


def test_strona_jest_samodzielna_bez_nawigacji_serwisu():
    """Kasa nie dziedziczy z base.html: bez paska nawigacji, stopki i belek.
    Klient ma tu JEDEN przycisk — każde wyjście z tej strony to porzucona
    płatność, a przy marce FX nawigacja zdradzałaby w ogóle istnienie PTF."""
    oid, _, _ = _zamowienie()
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "<nav" not in tresc
    assert 'class="logo"' not in tresc


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
    # Stara kasa jest też wygaszana u Stripe'a — klient z dwiema otwartymi
    # kartami nie ma jak zapłacić dwa razy za to samo zamówienie.
    assert stara in stripe_wlaczony.wygaszone


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


# --------------------------------------------------------------------------- #
#  Marka strony (PTF / Forex Passing) i rabat z panelu                         #
# --------------------------------------------------------------------------- #
def test_marka_domyslna_to_ptf():
    oid, _, _ = _zamowienie()
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert f"PTF-{oid}" in tresc
    assert "Forex Passing" not in tresc


def test_marka_fx_nie_zdradza_ptf():
    """Klient z kanału Forex Passing marki PTF nie zna — strona w barwach FX
    nie ma prawa pokazać ani nazwy, ani domeny, ani numeru z prefiksem PTF."""
    oid, _, _ = _zamowienie(brand="fx")
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "Forex Passing" in tresc
    assert "Pro Traders Funding" not in tresc
    assert "protradersfunding" not in tresc.lower()
    assert f"FX-{oid}" in tresc and f"PTF-{oid}" not in tresc


def test_marka_fx_po_zaplacie_nie_wysyla_do_portalu_ptf():
    oid, _, _ = _zamowienie(brand="fx")
    token = _token(oid)
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    tresc = client.get(f"/pay/{token}").text
    assert "Already paid" in tresc
    assert "/portal" not in tresc and "protradersfunding" not in tresc.lower()


def test_nieznana_marka_to_400():
    tid, _ = _trader()
    r = client.post("/api/admin/orders",
                    json={"trader_id": tid, "product_key": "2step-25k", "flag": "",
                          "notify_trader": False, "brand": "xyz"}, headers=ADMIN)
    assert r.status_code == 400


def test_marka_fx_nie_wysyla_ptf_maila():
    """Mail „czekamy na wpłatę" wychodzi na papierze PTF — dla zamówienia FX
    nie wychodzi WCALE, choćby checkbox został zaznaczony."""
    tid, _ = _trader()
    r = client.post("/api/admin/orders",
                    json={"trader_id": tid, "product_key": "2step-25k",
                          "flag": "awaiting_crypto", "notify_trader": True,
                          "brand": "fx"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["emailed"] is False


def test_rabat_z_panelu_widac_na_stronie():
    """Rabat obiecany w rozmowie ma stać na stronie płatności: przekreślony
    cennik i procent. Stempel OFFER — sama niższa kwota rabatu nie tworzy
    (tego pilnuje test_partner_pay)."""
    oid, _, _ = _zamowienie(discount_pct=30)  # cennik 2step-25k: $299
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "List price" in tresc and "$299" in tresc
    assert "$209.30" in tresc, "kwota po rabacie policzona z cennika"
    assert "(30%)" in tresc


def test_nizsza_kwota_bez_stempla_nie_rysuje_rabatu():
    oid, _, _ = _zamowienie(amount_usd=209.30)
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "List price" not in tresc


def test_rabat_nie_laczy_sie_z_cena_partnerska():
    tid, _ = _trader()
    r = client.post("/api/admin/orders",
                    json={"trader_id": tid, "product_key": "2step-25k", "flag": "",
                          "notify_trader": False, "partner_discount": True,
                          "discount_pct": 30}, headers=ADMIN)
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
#  Otwarcie od razu jako funded                                                #
# --------------------------------------------------------------------------- #
def test_open_funded_zaklada_konto_od_razu_funded():
    """Oferta imienna „płacisz i masz konto funded": po opłaceniu konto pomija
    ewaluację, choć plan ma 2 kroki."""
    from app.models import Account
    oid, _, _ = _zamowienie(open_funded=True)
    r = client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    assert r.status_code == 200, r.text
    s = SessionLocal()
    o = s.get(Order, oid)
    acc = s.get(Account, o.account_id)
    s.close()
    assert acc is not None
    assert acc.phase == "funded" and acc.status == "funded"


def test_bez_open_funded_konto_zaczyna_ewaluacje():
    from app.models import Account
    oid, _, _ = _zamowienie()
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    s = SessionLocal()
    o = s.get(Order, oid)
    acc = s.get(Account, o.account_id)
    s.close()
    assert acc.phase == "eval_1" and acc.status == "active"


# --------------------------------------------------------------------------- #
#  Add-on Weekend Trading                                                      #
# --------------------------------------------------------------------------- #
def test_weekend_dolicza_sie_do_kwoty_liczonej_przez_serwer():
    """Bez kwoty w żądaniu serwer liczy jak checkout: (cennik − rabat) + $199 —
    add-on POZA rabatem, bo rabat dotyczy planu."""
    oid, _, _ = _zamowienie(discount_pct=30, weekend_trading=True)  # 299*0.7+199
    s = SessionLocal(); o = s.get(Order, oid); s.close()
    assert o.amount_usd == pytest.approx(408.30)
    assert bool(o.weekend_trading) is True


def test_weekend_nie_rusza_kwoty_wpisanej_recznie():
    """Kwota wpisana ręcznie jest dokładnie tym, co admin obiecał — panel sam
    dolicza $199 w polu, więc serwer nie ma prawa doliczyć drugi raz."""
    oid, _, _ = _zamowienie(amount_usd=250.0, weekend_trading=True)
    s = SessionLocal(); o = s.get(Order, oid); s.close()
    assert o.amount_usd == 250.0


def test_weekend_widac_na_stronie_a_rabat_nie_znika():
    """Add-on ma osobny wiersz, a rabat dalej liczy się od PLANU: bez zdjęcia
    weekendu z kwoty dopłata $199 połknęłaby przekreślony cennik w całości."""
    oid, _, _ = _zamowienie(discount_pct=30, weekend_trading=True)
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "Weekend Trading" in tresc and "$199" in tresc
    assert "List price" in tresc and "$299" in tresc
    assert "(30%)" in tresc
    assert "$408.30" in tresc


def test_weekend_bez_rabatu_nie_rysuje_przekreslonego_cennika():
    """Dopłata to nie rabat: $299 + $199 = $498 bez żadnego przekreślenia."""
    oid, _, _ = _zamowienie(weekend_trading=True)
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "Weekend Trading" in tresc
    assert "List price" not in tresc
    assert "$498" in tresc


def test_weekend_przechodzi_na_konto_po_zaplacie():
    from app.models import Account
    oid, _, _ = _zamowienie(weekend_trading=True)
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    s = SessionLocal()
    o = s.get(Order, oid)
    acc = s.get(Account, o.account_id)
    s.close()
    assert bool(acc.weekend_trading) is True


def test_darmowy_weekend_nie_dolicza_oplaty_a_strona_mowi_free():
    """Weekend „w prezencie": add-on jest, $199 nie ma — kwota to sam plan po
    rabacie, a strona pokazuje przekreślone $199 i FREE. Rabat liczy się dalej
    od planu, bo opłaty w kwocie nie było i nie ma czego zdejmować."""
    oid, _, _ = _zamowienie(discount_pct=30, weekend_trading=True,
                            weekend_free=True)
    s = SessionLocal(); o = s.get(Order, oid); s.close()
    assert o.amount_usd == pytest.approx(209.30)
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "Weekend Trading" in tresc and "FREE" in tresc
    assert "(30%)" in tresc and "$209.30" in tresc


def test_darmowy_weekend_tez_przechodzi_na_konto():
    from app.models import Account
    oid, _, _ = _zamowienie(weekend_trading=True, weekend_free=True)
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    s = SessionLocal()
    o = s.get(Order, oid)
    acc = s.get(Account, o.account_id)
    s.close()
    assert bool(acc.weekend_trading) is True


def test_free_bez_weekendu_nie_zostawia_sladu():
    """Samo `weekend_free` bez add-onu to stan bez sensu — serwer go zeruje,
    zamiast zapisać zamówienie, które „nie dolicza" czegoś, czego nie ma."""
    oid, _, _ = _zamowienie(weekend_free=True)
    s = SessionLocal(); o = s.get(Order, oid); s.close()
    assert bool(o.weekend_free) is False
    assert o.amount_usd == 299.0


# --------------------------------------------------------------------------- #
#  Własny nagłówek strony płatności                                            #
# --------------------------------------------------------------------------- #
def test_naglowek_wlasny_zamiast_domyslnego():
    oid, _, _ = _zamowienie(headline="Weekend Flash Sale")
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "Weekend Flash Sale" in tresc
    assert "Complete your payment" not in tresc


def test_pusty_naglowek_zostawia_domyslny():
    oid, _, _ = _zamowienie(headline="   ")
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "Complete your payment" in tresc


def test_naglowek_jest_escapowany():
    """Nagłówek pisze admin, ale strona jest publiczna i bywa przeklejana —
    HTML z pola nie ma prawa wykonać się w przeglądarce klienta."""
    oid, _, _ = _zamowienie(headline="<script>alert(1)</script>")
    tresc = client.get(f"/pay/{_token(oid)}").text
    assert "<script>alert(1)</script>" not in tresc
