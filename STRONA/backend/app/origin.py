"""Pochodzenie klienta: darmowy lejek, grant, Afryka — jedno miejsce, wiele sygnałów.

Do 2026-09 panel znał tylko `desk` leada (`_desk_leada`: `source` zaczynający
się od „free"), dopasowany do tradera po e-mailu. To jest poprawna odpowiedź na
pytanie „którędy przyszło zgłoszenie", i taka zostaje — routing Telegrama i
kategorie pushy dalej idą po lejku, bo wersja „po kraju" była już raz błędną
próbą. Ale filtr w panelu odpowiada na INNE pytanie: „czy to jest ktoś
z darmowego, afrykańskiego ruchu, którego nie chcę widzieć między płacącymi".
Na to samo `source` nie wystarcza z trzech powodów:

* konto z grantu „free program" bywa założone bez leada (ręcznie),
* Nigeryjczyk potrafi zarejestrować się sam, z innego maila niż w ankiecie,
* lead z darmowego lejka ma czasem `source` przepisane przez dział.

Stąd `Pochodzenie`: `desk` jak dotąd, kraj z pierwszego wiarygodnego sygnału,
`africa` z KTÓREGOKOLWIEK sygnału, `free` jako suma — i `via`, czyli lista
powodów, żeby chip w panelu mówił, SKĄD wynik, a nie tylko jaki jest.
Fałszywie dodatni kosztuje jedno spojrzenie na tooltip; fałszywie ujemny
kosztuje darmowe konto policzone jako klient.

Kolejność źródeł kraju: dokument (KYC) przed numerem, numer przed IP —
IP ostatnie, bo VPN. Do `africa` liczą się wszystkie naraz.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func

from . import countries
from .models import Account, Lead, Trader


def desk_z_source(source: str | None) -> str:
    """Ta sama reguła co `main._desk_leada` — powielona, a nie importowana, bo
    `main` importuje ten moduł i pętla importów byłaby gorsza niż jedna linia.
    `test_leads_desk_free.py` pilnuje, żeby obie wersje dawały to samo."""
    return "free" if (source or "").strip().lower().startswith("free") else "leads"


# Notatka, którą wystawia przycisk „Free account" — patrz `notify.FREE_PROGRAM_NOTE`.
FREE_PROGRAM_NOTE = "free program"


@dataclass
class Pochodzenie:
    desk: str | None = None       # "free" | "leads" | None (bez leada)
    country: str | None = None    # ISO2 z pierwszego wiarygodnego sygnału
    country_via: str | None = None  # z którego sygnału (kyc/phone/login/signup/lead-phone/lead-ip)
    africa: bool = False
    free: bool = False            # desk == "free" or grant or africa
    via: list[str] = field(default_factory=list)

    def json(self) -> dict:
        return {"desk": self.desk, "country": self.country, "country_via": self.country_via,
                "africa": self.africa, "free": self.free, "via": list(self.via)}


def pochodzenie(trader: Trader | None, lead: Lead | None, *,
                free_grant: bool = False) -> Pochodzenie:
    """Werdykt z tego, co o człowieku wiadomo. Czyste, bez bazy — do testów."""
    p = Pochodzenie()
    if lead is not None:
        p.desk = desk_z_source(lead.source)
        if p.desk == "free":
            p.via.append(f"lead:{(lead.source or '').strip() or 'free'}")
    if free_grant:
        p.via.append("grant:free program")

    sygnaly: list[tuple[str, str | None]] = []
    if trader is not None:
        sygnaly += [("kyc", countries.iso_from_name(trader.kyc_country)),
                    ("phone", trader.phone_country),
                    ("login", trader.last_login_country),
                    ("signup", trader.signup_country)]
    if lead is not None:
        sygnaly += [("lead-phone", lead.phone_iso),
                    ("lead-ip", lead.ip_country)]
    znane = [(zrodlo, iso.strip().upper()) for zrodlo, iso in sygnaly
             if iso and iso.strip()]
    if znane:
        p.country_via, p.country = znane[0]
    for zrodlo, iso in znane:
        if countries.is_africa(iso):
            p.africa = True
            p.via.append(f"{zrodlo}:{iso}")
    p.free = p.desk == "free" or free_grant or p.africa
    return p


def mapa_pochodzenia(session, traderzy: list[Trader]) -> dict[int, Pochodzenie]:
    """Pochodzenie dla listy traderów w DWÓCH zapytaniach, nie 2×N.

    Leady dopasowane po e-mailu (lower/strip); `leads.email` jest UNIQUE, ale
    z rozróżnianiem wielkości liter, więc remis jest możliwy i rozstrzyga go
    darmowy lejek — filtr odpowiada na pytanie „kto przyszedł z darmowego".
    Granty „free program" po tej samej regule co `_kanal_free` w `main`.
    """
    if not traderzy:
        return {}
    leady: dict[str, Lead] = {}
    for lead in session.query(Lead).all():
        klucz = (lead.email or "").strip().lower()
        if not klucz:
            continue
        stary = leady.get(klucz)
        if stary is None or (desk_z_source(lead.source) == "free"
                             and desk_z_source(stary.source) != "free"):
            leady[klucz] = lead
    granty = {tid for (tid,) in
              session.query(Account.trader_id)
              .filter(Account.source == "grant",
                      func.lower(func.coalesce(Account.grant_note, ""))
                      == FREE_PROGRAM_NOTE).distinct().all()}
    return {t.id: pochodzenie(t, leady.get((t.email or "").strip().lower()),
                              free_grant=t.id in granty)
            for t in traderzy}


def pochodzenie_leada(lead: Lead, trader: Trader | None = None,
                      *, free_grant: bool = False) -> Pochodzenie:
    """To samo dla wiersza LEADA (lista leadów): trader, jeśli już się
    zarejestrował, dokłada swoje sygnały; bez niego liczy się sam lead."""
    return pochodzenie(trader, lead, free_grant=free_grant)
