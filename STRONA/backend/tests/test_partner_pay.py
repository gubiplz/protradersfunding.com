"""Płatność wystawiona przez stronę partnera.

Partner sprzedaje pod własną marką i jego klient nie ma powodu lądować na naszej
domenie w połowie zakupu. Jego serwer czyta zamówienie przez `GET /api/pay/<token>`,
rysuje kwotę u siebie, a przycisk woła ten sam publiczny `start`, co nasza strona.

Dwie rzeczy muszą tu trzymać, bo obchodzą nas mocniej niż wygoda partnera:
1. Po drugiej stronie stoi CUDZY serwer — z zamówienia nie ma prawa wyjść mail
   ani nazwisko kupującego.
2. Adres partnera nie istnieje w kodzie. Oba repozytoria są publiczne, więc
   domena żyje wyłącznie w zmiennej środowiskowej hostingu.
"""
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
import stripe as stripe_sdk  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog, main  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Product, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
SEKRET = "sekret-partnera"
# Domena wymyślona na potrzeby testu. Prawdziwej nie ma ani tutaj, ani w kodzie —
# to jest cała treść `test_domena_partnera_nie_siedzi_w_kodzie`.
BAZA_PARTNERA = "https://partner.example"
LICZNIK = iter(range(10_000))


PRODUKT = "2step-25k"


@pytest.fixture(autouse=True)
def _partner_wlaczony(monkeypatch):
    u = get_settings()
    monkeypatch.setattr(u, "partner_api_token", SEKRET, raising=False)
    monkeypatch.setattr(u, "partner_pay_base_url", BAZA_PARTNERA, raising=False)
    monkeypatch.setattr(u, "partner_discount_pct", 20.0, raising=False)
    return u


class _StripeAtrapa:
    """Podstawka pod `billing._stripe()`, która ZAPAMIĘTUJE, z czym poszła sesja.

    Adresy powrotu są jedyną rzeczą, jakiej nie da się sprawdzić po odpowiedzi:
    klient widzi je dopiero po zapłacie, na ekranie Stripe'a.
    """
    error = stripe_sdk.error
    ostatnie: dict = {}

    class checkout:
        class Session:
            @staticmethod
            def create(**kw):
                _StripeAtrapa.ostatnie = kw
                return SimpleNamespace(id="cs_atrapa", url="https://checkout.stripe.test/sesja")


@pytest.fixture
def kasa(monkeypatch):
    monkeypatch.setattr(billing.settings, "stripe_secret_key", "sk_test_atrapa")
    monkeypatch.setattr(billing, "_stripe", lambda: _StripeAtrapa)
    _StripeAtrapa.ostatnie = {}
    return _StripeAtrapa


def _cennik(key: str = PRODUKT) -> float:
    s = SessionLocal()
    try:
        return round(float(s.query(Product).filter(Product.key == key).first().price_usd), 2)
    finally:
        s.close()


def _zamowienie(**dodatkowe) -> int:
    email = f"partner{next(LICZNIK)}@partner.test"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Anna Nowak", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit(); tid = tr.id; s.close()
    r = client.post("/api/admin/orders", headers=ADMIN, json={
        "trader_id": tid, "product_key": PRODUKT, "flag": "", "notify_trader": False,
        **dodatkowe})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _czyta(order_id: int) -> dict:
    odp = client.get(f"/api/pay/{_token(order_id)}", headers={"X-Partner-Token": SEKRET})
    assert odp.status_code == 200, odp.text
    return odp.json()


def _linki(order_id: int) -> dict:
    r = client.post(f"/api/admin/orders/{order_id}/pay-link", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _token(order_id: int) -> str:
    return _linki(order_id)["url"].rsplit("/", 1)[1]


# --------------------------------------------------------------------------- #
#  Kto ma prawo czytać zamówienie                                              #
# --------------------------------------------------------------------------- #
def test_bez_naglowka_odmawia():
    assert client.get(f"/api/pay/{_token(_zamowienie())}").status_code == 401


def test_cudzy_sekret_odmawia():
    odp = client.get(f"/api/pay/{_token(_zamowienie())}",
                     headers={"X-Partner-Token": "nie-ten"})
    assert odp.status_code == 401


def test_nie_przyjmuje_tokenu_admina():
    """Sekret partnera jest osobny od ADMIN_TOKEN-a, bo partner stoi na cudzym
    hostingu. Gdy mu wycieknie, ma odsłonić kwoty linków — nigdy panel."""
    odp = client.get(f"/api/pay/{_token(_zamowienie())}",
                     headers={"X-Partner-Token": get_settings().admin_token})
    assert odp.status_code == 401


def test_pusty_sekret_zamyka_endpoint(monkeypatch):
    """Domyślnie (bez PARTNER_API_TOKEN w env) odczyt jest zamknięty. Inaczej
    świeże wdrożenie wystawiałoby zamówienia każdemu, kto zna adres."""
    monkeypatch.setattr(get_settings(), "partner_api_token", "", raising=False)
    token = "x" * 32
    assert client.get(f"/api/pay/{token}").status_code == 401
    assert client.get(f"/api/pay/{token}", headers={"X-Partner-Token": ""}).status_code == 401


def test_nieznany_token_to_404():
    odp = client.get("/api/pay/tego-tokenu-nie-ma", headers={"X-Partner-Token": SEKRET})
    assert odp.status_code == 404


def test_zgadywanie_tokenow_lapie_limit(monkeypatch):
    """Sam sekret nie może wystarczyć do przelecenia listy zgadywanych linków —
    partner czyta jeden link na klienta, nie tysiąc na minutę."""
    monkeypatch.setattr(main, "_RL_DISABLED", False)
    main._RL_HITS.clear()
    kody = {client.get(f"/api/pay/zgaduje{i}", headers={"X-Partner-Token": SEKRET}).status_code
            for i in range(70)}
    main._RL_HITS.clear()
    assert 429 in kody


# --------------------------------------------------------------------------- #
#  Co wychodzi na zewnątrz                                                     #
# --------------------------------------------------------------------------- #
def test_oddaje_pozycje_kwote_i_numer():
    oid = _zamowienie()
    odp = client.get(f"/api/pay/{_token(oid)}", headers={"X-Partner-Token": SEKRET})
    assert odp.status_code == 200
    d = odp.json()
    assert d["reference"] == f"PTF-{oid}"
    assert d["status"] == "pending"
    assert d["currency"] == "USD"
    assert d["amount_usd"] > 0
    assert "challenge" in d["item"]


def test_nie_wypuszcza_danych_osobowych():
    """Po drugiej stronie jest cudzy serwer i cudze logi. Zamówienie zna mail
    i nazwisko kupującego — tu nie ma prawa wyjść ani jedno, ani drugie."""
    oid = _zamowienie()
    s = SessionLocal()
    from app.models import Order
    zam = s.get(Order, oid)
    mail = s.get(Trader, zam.trader_id).email
    s.close()

    tresc = client.get(f"/api/pay/{_token(oid)}",
                       headers={"X-Partner-Token": SEKRET}).text
    assert mail not in tresc
    assert "Anna" not in tresc and "Nowak" not in tresc


# --------------------------------------------------------------------------- #
#  Rabat partnerski widoczny dla kupującego                                    #
# --------------------------------------------------------------------------- #
def test_strona_partnera_dostaje_cene_sprzed_rabatu():
    """Klient partnera płaci mniej, niż stoi w cenniku, i ma prawo to zobaczyć.

    Bez tych trzech pól rabat istnieje wyłącznie w rozmowie na Telegramie i w
    naszym raporcie — czyli dla kupującego nie istnieje wcale.
    """
    katalog = _cennik()
    d = _czyta(_zamowienie(amount_usd=round(katalog * 0.8, 2), partner_discount=True))
    assert d["list_amount_usd"] == katalog
    assert d["discount_pct"] == 20
    assert d["discount_usd"] == round(katalog - d["amount_usd"], 2)


def test_nizsza_kwota_bez_stempla_to_jeszcze_nie_rabat():
    """Sama kwota niższa od cennika NIE znaczy rabatu — i to jest tu cała rzecz.

    Admin wpisuje ją ręcznie: bywa zbiciem ceny jednemu klientowi, ale bywa też
    zaliczką albo dopłatą do wcześniejszej wpłaty. Bez stempla umowy strona
    ogłosiłaby wtedy komuś „oszczędzasz 199 $" na zamówieniu, na którym nikt
    niczego nie obiecał. Przekreślona cena należy się wyłącznie tam, gdzie
    naprawdę stoi za nią umowa.
    """
    d = _czyta(_zamowienie(amount_usd=round(_cennik() - 50, 2)))
    assert "list_amount_usd" not in d and "discount_pct" not in d
    # I to samo dla zamówienia po pełnej cenie — żeby ten test nie zaczął kiedyś
    # przechodzić tylko dlatego, że kwota akurat równa się cennikowi.
    assert "list_amount_usd" not in _czyta(_zamowienie())


def test_kwota_wyzsza_od_cennika_nie_udaje_rabatu():
    """Kwotę wpisuje ręcznie admin (dopłaty, kurs). Przekreślona cena NIŻSZA od
    płaconej byłaby najgorszym możliwym komunikatem tuż przed wpisaniem karty."""
    d = _czyta(_zamowienie(amount_usd=_cennik() + 50, partner_discount=True))
    assert "list_amount_usd" not in d


def test_procent_liczony_z_zamowienia_a_nie_z_dzisiejszej_stawki(monkeypatch):
    """Stawka w env zmienia się szybciej, niż żyją wystawione linki. Strona ma
    pokazać to, co dostał TEN klient, a nie to, co dziś stoi w konfiguracji."""
    katalog = _cennik()
    oid = _zamowienie(amount_usd=round(katalog * 0.8, 2), partner_discount=True)
    monkeypatch.setattr(get_settings(), "partner_discount_pct", 50.0, raising=False)
    assert _czyta(oid)["discount_pct"] == 20


# --------------------------------------------------------------------------- #
#  Dokąd Stripe odsyła po zapłacie                                             #
# --------------------------------------------------------------------------- #
def test_klient_partnera_wraca_na_domene_partnera(kasa):
    """Kupował pod cudzą marką — po zapłacie ma wrócić tam, skąd wyszedł.

    Nasz portal jest dla niego jednocześnie złą marką i ścianą: kazałby się
    zalogować na konto, o którym pierwszy raz słyszy.
    """
    oid = _zamowienie()
    tok = _token(oid)
    r = client.post(f"/api/pay/{tok}/start", headers={"X-Partner-Token": SEKRET})
    assert r.status_code == 200, r.text
    assert kasa.ostatnie["success_url"] == f"{BAZA_PARTNERA}/pay/{tok}?paid=1"
    assert kasa.ostatnie["cancel_url"] == f"{BAZA_PARTNERA}/pay/{tok}?canceled=1"


def test_nasza_strona_platnosci_dalej_wraca_do_portalu(kasa):
    """Ta sama funkcja obsługuje nasz własny /pay/<token>. Zmiana dla partnera
    nie ma prawa przekierować NASZYCH klientów na cudzą domenę."""
    r = client.post(f"/api/pay/{_token(_zamowienie())}/start")
    assert r.status_code == 200, r.text
    assert kasa.ostatnie["success_url"].endswith("/portal?paid=1")
    assert BAZA_PARTNERA not in kasa.ostatnie["success_url"]


def test_zly_sekret_nie_przestawia_adresu_powrotu(kasa):
    """`start` jest publiczny (token JEST wstępem), więc nagłówek niczego nie
    otwiera — ale nie może też po cichu przestawiać, dokąd trafi kupujący."""
    r = client.post(f"/api/pay/{_token(_zamowienie())}/start",
                    headers={"X-Partner-Token": "nie-ten"})
    assert r.status_code == 200, r.text
    assert BAZA_PARTNERA not in kasa.ostatnie["success_url"]


def test_adres_powrotu_bierze_sie_z_konfiguracji(kasa, monkeypatch):
    """Nawet ktoś z ważnym sekretem nie wskaże kasie, dokąd ma odesłać klienta:
    adres czytamy z własnego env, a nie z czegokolwiek w żądaniu."""
    monkeypatch.setattr(get_settings(), "partner_pay_base_url", "", raising=False)
    r = client.post(f"/api/pay/{_token(_zamowienie())}/start",
                    headers={"X-Partner-Token": SEKRET})
    assert r.status_code == 200, r.text
    assert kasa.ostatnie["success_url"].endswith("/portal?paid=1")


# --------------------------------------------------------------------------- #
#  Wybór marki przy wystawianiu linku                                          #
# --------------------------------------------------------------------------- #
def test_panel_dostaje_oba_linki_z_tym_samym_tokenem():
    """Ten sam token pod dwiema markami — to jedno zamówienie, nie dwa. Panel
    daje adminowi wybrać, bo tylko on wie, skąd ten człowiek przyszedł."""
    d = _linki(_zamowienie())
    assert d["partner_url"].startswith(BAZA_PARTNERA + "/pay/")
    assert d["partner_url"].rsplit("/", 1)[1] == d["url"].rsplit("/", 1)[1]


def test_bez_zmiennej_srodowiskowej_nic_sie_nie_zmienia(monkeypatch):
    """Wdrożenie bez PARTNER_PAY_BASE_URL zachowuje się jak przed tą zmianą:
    jeden link, żadnego okna z wyborem."""
    monkeypatch.setattr(get_settings(), "partner_pay_base_url", "", raising=False)
    d = _linki(_zamowienie())
    assert "partner_url" not in d
    assert "/pay/" in d["url"]


def test_domena_partnera_nie_siedzi_w_kodzie():
    """Oba repozytoria są publiczne. Adres partnera ma jedno miejsce — zmienną
    środowiskową hostingu — więc w kodzie i szablonach nie może go być nawet
    jako wartość domyślna."""
    korzen = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    podejrzane = []
    for katalog, _, pliki in os.walk(korzen):
        if any(x in katalog for x in (".venv", "__pycache__", ".git", "tests")):
            continue
        for nazwa in pliki:
            if not nazwa.endswith((".py", ".js", ".html", ".css", ".json")):
                continue
            sciezka = os.path.join(katalog, nazwa)
            with open(sciezka, encoding="utf-8", errors="ignore") as f:
                tresc = f.read()
            if "forexpassing" in tresc.lower():
                podejrzane.append(sciezka)
    assert not podejrzane, f"nazwa partnera w kodzie: {podejrzane}"
