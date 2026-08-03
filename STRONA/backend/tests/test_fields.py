"""Walidacja danych osobowych: imię, nazwisko, telefon, kraj.

Punktem wyjścia jest realny zrzut z produkcji: przez okno zakupu przeszły imię
„Dawid53", nazwisko „5Mazur" i telefon „+4851206". Te trzy pola wypełniają
formularz rejestracji konta demo u MetaQuotes, więc śmieć w nich oznacza konto,
którego klient nie dostanie mimo opłacenia zamówienia.

Testy pilnują dwóch rzeczy naraz, bo obie potrafią zaboleć:
odrzucania oczywistych śmieci ORAZ przepuszczania nazwisk i numerów, które są
nietypowe, ale prawdziwe. Fałszywa odmowa w kasie kosztuje więcej niż dziwny
zapis w bazie.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, countries, fields  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()

client = TestClient(app)
LICZNIK = iter(range(1000))

# Dane dokładnie ze zrzutu, który zgłosił właściciel produktu.
ZE_ZRZUTU = {"first_name": "Dawid53", "last_name": "5Mazur",
             "phone": "+4851206", "phone_country": "PL"}
POPRAWNE = {"first_name": "Dawid", "last_name": "Mazur",
            "phone": "512345678", "phone_country": "PL"}


def _trader(**pola):
    s = SessionLocal()
    tr = Trader(email=f"pola{next(LICZNIK)}@test.pl", password_hash=auth.hash_password("haslo1234"),
                referral_code=auth.secrets.token_hex(3), **pola)
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


# ---------------- imię i nazwisko ----------------
@pytest.mark.parametrize("smiec", ["Dawid53", "5Mazur", "123", "N", "", "   ",
                                   "Jan<script>", "a@b.pl", "Jan_Nowak"])
def test_nazwisko_ze_smieciem_odrzucone(smiec):
    with pytest.raises(ValueError):
        fields.person_name(smiec, "First name")


@pytest.mark.parametrize("dobre,oczekiwane", [
    ("Dawid", "Dawid"),
    ("  Anna   Maria  ", "Anna Maria"),          # nadmiarowe spacje zwijane
    ("Kowalska-Nowak", "Kowalska-Nowak"),
    ("O'Brien", "O'Brien"),
    ("D’Angelo", "D’Angelo"),
    ("Łukasz Ćwikliński", "Łukasz Ćwikliński"),
    ("Мария Иванова", "Мария Иванова"),          # cyrylica
    ("Γεώργιος", "Γεώργιος"),                    # greka
    ("محمد", "محمد"),                            # alfabet arabski
    ("J. R. Ewing", "J. R. Ewing"),
])
def test_prawdziwe_nazwiska_przechodza(dobre, oczekiwane):
    """Zawężenie do [A-Za-z] wycięłoby realnych klientów — to gorszy błąd niż
    wpuszczenie dziwnego zapisu."""
    assert fields.person_name(dobre, "Last name") == oczekiwane


def test_komunikat_mowi_o_co_chodzi():
    with pytest.raises(ValueError, match="cannot contain digits"):
        fields.person_name("Dawid53", "First name")


def test_za_dlugie_nazwisko_odrzucone():
    with pytest.raises(ValueError, match="too long"):
        fields.person_name("A" * 61, "Last name")


# ---------------- telefon ----------------
def test_numer_ze_zrzutu_odrzucony():
    """+4851206 to 5 cyfr numeru krajowego — polski ma 7–9."""
    with pytest.raises(ValueError, match="digits after"):
        fields.phone("PL", "+4851206")
    with pytest.raises(ValueError, match="digits after"):
        fields.phone("PL", "51206")


@pytest.mark.parametrize("wpisane,e164", [
    ("512345678", "+48512345678"),
    ("+48 512 345 678", "+48512345678"),
    ("0048 512 345 678", "+48512345678"),
    ("(512) 345-678", "+48512345678"),
    ("0512345678", "+48512345678"),      # zero z zapisu krajowego
])
def test_ten_sam_numer_w_roznych_zapisach(wpisane, e164):
    assert fields.phone("PL", wpisane)[0] == e164


def test_wloski_numer_zachowuje_zero_wiodace():
    """We Włoszech zero NALEŻY do numeru krajowego. Bezwarunkowe obcinanie zera
    psułoby poprawne numery, więc zdejmujemy je tylko wtedy, gdy inaczej numer
    nie pasuje do żadnej dopuszczalnej długości."""
    assert fields.phone("IT", "0612345678")[0] == "+390612345678"


def test_numer_z_innego_kraju_niz_wybrany():
    with pytest.raises(ValueError, match=r"\+1"):
        fields.phone("PL", "+14155551234")


@pytest.mark.parametrize("iso,numer", [("US", "415555"), ("IN", "12345678901234"),
                                       ("GB", "1"), ("PL", "abc"), ("PL", "")])
def test_zle_numery_odrzucone(iso, numer):
    with pytest.raises(ValueError):
        fields.phone(iso, numer)


def test_nieznany_kraj_odrzucony():
    with pytest.raises(ValueError, match="country"):
        fields.phone("XX", "512345678")


def test_tablica_krajow_jest_kompletna():
    assert len(countries.COUNTRIES) > 200
    for iso2, nazwa, kierunkowy, mn, mx, glowny in countries.COUNTRIES:
        assert len(iso2) == 2 and iso2.isupper(), iso2
        assert nazwa and kierunkowy.isdigit()
        assert 1 <= mn <= mx <= countries.E164_MAX, (iso2, mn, mx)
    assert countries.BY_ISO["PL"][2] == "48"
    assert countries.BY_ISO["US"][2] == "1"


def test_kierunkowy_dzielony_wskazuje_kraj_glowny():
    """+44 nosi Wielka Brytania, Guernsey, Jersey i Wyspa Man, a +1 ponad
    dwadziescia krajow. Bez wskazania kraju glownego numer klienta sprzed
    zmiany trafial pod pierwsza alfabetycznie flage — o innych dopuszczalnych
    dlugosciach numeru — i przestawal przechodzic walidacje."""
    assert countries.MAIN_BY_DIAL["44"] == "GB"
    assert countries.MAIN_BY_DIAL["1"] == "US"
    assert countries.BY_ISO["GG"][5] is False, "Guernsey nie jest glowny dla +44"
    assert countries.BY_ISO["GB"][5] is True
    # kazdy kierunkowy dzielony przez kilka krajow ma dokladnie jednego glownego
    from collections import Counter
    ile = Counter(k[2] for k in countries.COUNTRIES)
    for dial, n in ile.items():
        if n > 1:
            assert dial in countries.MAIN_BY_DIAL, f"+{dial} bez kraju glownego"


# ---------------- kraj ----------------
def test_kraj_po_nazwie_i_po_kodzie():
    assert fields.country_name("Poland") == "Poland"
    assert fields.country_name("poland") == "Poland"
    assert fields.country_name("PL") == "Poland"


def test_wymyslony_kraj_odrzucony():
    with pytest.raises(ValueError):
        fields.country_name("Wakanda")


# ---------------- e-mail ----------------
@pytest.mark.parametrize("zly", ["bezmalpy.pl", "a@b", "@b.pl", "a b@c.pl", ""])
def test_email_bez_malpy_odrzucony(zly):
    with pytest.raises(ValueError):
        fields.email(zly)


def test_email_normalizowany():
    assert fields.email("  Dawid.Mazur@Gmail.COM ") == "dawid.mazur@gmail.com"


# ---------------- kasa (to jest test, który naprawdę broni prowizji) ----------------
def test_checkout_odrzuca_dane_ze_zrzutu():
    """JS da się ominąć jednym curl-em, więc regułą jest ta na serwerze."""
    _, h = _trader()
    r = client.post("/api/checkout", headers=h, json={"product_key": "2step-25k", **ZE_ZRZUTU})
    assert r.status_code == 400, r.text
    assert "digits" in r.json()["detail"]


def test_checkout_przepuszcza_poprawne_dane_i_zapisuje_e164():
    tid, h = _trader()
    r = client.post("/api/checkout", headers=h, json={"product_key": "2step-25k", **POPRAWNE})
    assert r.status_code == 200, r.text
    s = SessionLocal()
    tr = s.get(Trader, tid)
    assert tr.first_name == "Dawid" and tr.last_name == "Mazur"
    assert tr.phone == "+48512345678", "numer ma isc do brokera w postaci miedzynarodowej"
    assert tr.phone_country == "PL"
    s.close()


@pytest.mark.parametrize("pole,wartosc", [("first_name", "Dawid53"), ("last_name", "5Mazur")])
def test_checkout_odrzuca_cyfry_w_nazwisku(pole, wartosc):
    _, h = _trader()
    r = client.post("/api/checkout", headers=h,
                    json={"product_key": "2step-25k", **{**POPRAWNE, pole: wartosc}})
    assert r.status_code == 400 and "digits" in r.json()["detail"]


def test_checkout_bez_danych_wymaga_ich_od_nowego_klienta():
    _, h = _trader()
    r = client.post("/api/checkout", headers=h, json={"product_key": "2step-25k"})
    assert r.status_code == 400, "pusty formularz nie moze byc furtka wokol walidacji"


def test_drugi_zakup_nie_kaze_wpisywac_wszystkiego_od_nowa():
    """Klient z kompletem danych na profilu kupuje ponownie bez formularza."""
    _, h = _trader(first_name="Dawid", last_name="Mazur", phone="+48512345678",
                   phone_country="PL")
    r = client.post("/api/checkout", headers=h, json={"product_key": "2step-25k"})
    assert r.status_code == 200, r.text


# ---------------- pozostale formularze ----------------
def test_signup_odrzuca_nazwe_z_cyframi():
    r = client.post("/api/auth/signup", json={"email": "nowy-smiec@test.pl",
                                              "password": "haslo1234",
                                              "full_name": "Dawid53",
                                              "terms_accepted": True})
    assert r.status_code == 400 and "digits" in r.json()["detail"]


def test_signup_bez_nazwy_dalej_dziala():
    """Pole bywa ukryte (rejestracja przez Google) — puste nie moze blokowac."""
    r = client.post("/api/auth/signup", json={"email": "nowy-bez-nazwy@test.pl",
                                              "password": "haslo1234",
                                              "terms_accepted": True})
    assert r.status_code == 200, r.text


def test_profil_odrzuca_nazwe_z_cyframi():
    _, h = _trader()
    assert client.patch("/api/me", headers=h, json={"full_name": "Dawid53"}).status_code == 400
    assert client.patch("/api/me", headers=h, json={"full_name": "Dawid Mazur"}).status_code == 200


def test_kyc_odrzuca_wymyslony_kraj():
    _, h = _trader()
    zly = client.post("/api/me/kyc", headers=h,
                      json={"full_name": "Dawid Mazur", "country": "Wakanda"})
    assert zly.status_code == 400
    dobry = client.post("/api/me/kyc", headers=h,
                        json={"full_name": "Dawid Mazur", "country": "Poland"})
    assert dobry.status_code == 200


def test_portal_dostaje_liste_krajow_w_html():
    """Okno zakupu potrzebuje kierunkowych od razu — lista jedzie w stronie,
    nie osobnym zapytaniem."""
    html = client.get("/portal").text
    assert 'id="pf-countries"' in html
    assert '"PL"' in html and '"48"' in html
