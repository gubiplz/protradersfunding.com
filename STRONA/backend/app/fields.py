"""Walidacja danych osobowych z formularzy.

Do tej pory imię i nazwisko nie miały ŻADNEJ reguły — do bazy wchodziło
„Dawid53" i „5Mazur" — a telefon miał regułę tak luźną, że przechodził numer
5-cyfrowy. To nie jest kwestia estetyki formularza: te trzy pola idą prosto do
rejestracji konta demo u MetaQuotes (`metaquotes_web.py`), więc śmieć w polu
oznacza nieudany provisioning konta, za które klient już zapłacił.

Walidacja stoi TUTAJ, po stronie serwera, bo sprawdzenie w przeglądarce omija
się jednym `curl`-em. Przeglądarka dostaje te same reguły tylko po to, żeby
klient zobaczył błąd od razu, a nie po kliknięciu „Kup".
"""
from __future__ import annotations

import re

from . import countries

# Jedyny wzorzec e-maila w projekcie. Mieszkał w `main`, ale potrzebują go też
# inne moduły, a dwie kopie tej samej reguły zawsze się w końcu rozjeżdżają.
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Znaki, które w nazwisku są normalne, choć nie są literami.
_DOZWOLONE = set(" -'’.")

MAX_NAME = 60


def person_name(value: str | None, label: str = "Name") -> str:
    """Imię/nazwisko: bez cyfr, z sensowną liczbą liter.

    Świadomie NIE zawężam do [A-Za-z]. `str.isalpha()` w Pythonie jest prawdziwe
    dla cyrylicy, greki, alfabetu arabskiego czy chińskiego, więc reguła
    przepuszcza klientów spoza alfabetu łacińskiego. Zawężenie do ASCII
    wycięłoby realnych ludzi — a to gorszy błąd niż wpuszczenie dziwnego zapisu.
    """
    tekst = re.sub(r"\s+", " ", (value or "").strip())
    if not tekst:
        raise ValueError(f"{label} is required.")
    if len(tekst) > MAX_NAME:
        raise ValueError(f"{label} is too long (max {MAX_NAME} characters).")
    if any(c.isdigit() for c in tekst):
        raise ValueError(f"{label} cannot contain digits.")
    for c in tekst:
        if not (c.isalpha() or c in _DOZWOLONE):
            raise ValueError(f"{label} contains an invalid character: “{c}”.")
    if sum(1 for c in tekst if c.isalpha()) < 2:
        raise ValueError(f"Enter your real {label.lower()}.")
    return tekst


def email(value: str | None) -> str:
    tekst = (value or "").strip().lower()
    if not EMAIL_RX.fullmatch(tekst):
        raise ValueError("Enter a valid e-mail address, for example name@example.com.")
    return tekst


def phone(iso2: str | None, value: str | None) -> tuple[str, str]:
    """Zwraca (numer w E.164, ISO2 kraju). Rzuca ValueError z gotowym tekstem."""
    kod = (iso2 or "").strip().upper()
    return countries.check_phone(kod, value), kod


def country_name(value: str | None) -> str:
    """Kraj z listy — dotąd w KYC wpadał dowolny string.

    Przyjmuje nazwę albo kod ISO2 i zwraca zawsze nazwę kanoniczną. Dwie formy,
    bo formularz wysyła nazwę, a klienci API i starsze wpisy w bazie potrafią
    przysłać sam kod — jedno i drugie jednoznacznie wskazuje kraj, więc nie ma
    powodu odrzucać którejkolwiek.
    """
    tekst = (value or "").strip()
    if not tekst:
        raise ValueError("Country is required.")
    if len(tekst) == 2:
        kraj = countries.BY_ISO.get(tekst.upper())
        if kraj:
            return kraj[1]
    for _iso, nazwa, *_ in countries.COUNTRIES:
        if nazwa.casefold() == tekst.casefold():
            return nazwa
    raise ValueError("Pick your country from the list.")
