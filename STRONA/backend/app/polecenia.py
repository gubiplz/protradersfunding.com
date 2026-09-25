"""Program poleceń partnera: zgłoszenie z `ref`, zakup i wypłata idą do jego bazy.

Partner widzi u siebie listę ludzi, których przyprowadził, i status
„confirmed", gdy któryś kupił konto. Oba fakty znamy tylko my: `ref` przychodzi
z landingu razem z leadem, zakup widać w zamówieniach. Dotąd partner dopisywał
poleconych ręcznie, a „confirmed" ktoś przestawiał w bazie z ręki.

Za zakup partnerowi należy się konto tego samego planu i rozmiaru. 2-Step od
razu; Instant dopiero po pierwszej wypłacie poleconego — to konto funded od
pierwszego dnia i wydane z góry byłoby wypłatą bez żadnej ewaluacji.

Po drugiej stronie jest jedna funkcja (`sync_referral`), którą wolno wołać tylko
sekretnym kluczem tamtej bazy. Status zmienia się wyłącznie do przodu: drugie
zgłoszenie albo druga wypłata aktualizują ten sam wiersz, a polecenie odrzucone
ręcznie zostaje odrzucone.

Best-effort i w tle (`notify.w_tle`, czyli po odesłaniu odpowiedzi): padnięta
baza partnera nie ma prawa zablokować przyjęcia leada ani wypłaty.
"""
from __future__ import annotations

import json
import re
import urllib.request

from sqlalchemy import func

from . import notify
from .config import get_settings
from .models import Lead, Order, Product, Trader

settings = get_settings()

# Ten sam kształt, który wymusza baza partnera i link /r/<slug>.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")


def czysty_slug(ref: str | None) -> str | None:
    """Slug partnera albo None — śmieci z formularza nie idą ani do bazy, ani dalej."""
    slug = (ref or "").strip().lower()
    return slug if SLUG_RE.match(slug) else None


def _post(url: str, body: dict, key: str, timeout: float = 6) -> tuple[int, bytes]:
    headers = {"apikey": key, "content-type": "application/json"}
    # Stary klucz service_role to JWT i idzie też jako Bearer. Nowy `sb_secret_…`
    # JWT-em nie jest — bramka sama zamienia go na rolę z nagłówka `apikey`.
    if key.startswith("eyJ"):
        headers["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def wyslij(slug: str | None, email: str, rozmiar: str | None = None,
           wyplata: bool = False) -> str:
    """Jedno zgłoszenie do bazy partnera. Nigdy nie rzuca.

    Zwraca to, co odpowiedziała funkcja (`pending`, `confirmed`, `rejected`,
    `no_partner`, `bad_email`), albo powód, dla którego nic nie wyszło.
    """
    slug = czysty_slug(slug)
    if not slug:
        return "no_ref"
    if not (settings.referral_sync_url and settings.referral_sync_key):
        return "off"
    rodzaj = "confirm" if wyplata else "lead"
    try:
        status, tresc = _post(settings.referral_sync_url,
                              {"p_slug": slug, "p_email": (email or "").strip().lower(),
                               "p_account_size": (rozmiar or None), "p_paid": bool(wyplata)},
                              settings.referral_sync_key)
        wynik = json.loads(tresc or b"null") if status < 300 else f"http {status}"
    except Exception as e:  # noqa: BLE001 — sieć, JSON, cokolwiek: best-effort
        wynik = f"error {type(e).__name__}"
    # Slug i wynik, bez maila: log funkcji czyta więcej osób niż panel.
    print(f"[polecenia] {rodzaj} {slug} -> {wynik}")
    return str(wynik)


def wlasciciel(slug: str | None) -> str | None:
    """„Imię · mail" partnera, do którego należy slug. Nigdy nie rzuca.

    `""` — baza odpowiedziała, że takiego partnera nie ma (odpowiedź ostateczna).
    `None` — nie wiadomo: automat wyłączony albo baza nie odpowiedziała.
    """
    slug = czysty_slug(slug)
    if not slug:
        return ""
    if not (settings.referral_sync_url and settings.referral_sync_key):
        return None
    # Druga funkcja mieszka obok `sync_referral`, pod tym samym /rpc/.
    url = settings.referral_sync_url.rstrip("/").rsplit("/", 1)[0] + "/referral_partner"
    try:
        status, tresc = _post(url, {"p_slug": slug}, settings.referral_sync_key, timeout=3)
        if status >= 300:
            raise ValueError(f"http {status}")
        dane = json.loads(tresc or b"null")
    except Exception as e:  # noqa: BLE001 — best-effort, lead ma wejść i bez tego
        print(f"[polecenia] kto {slug} -> error {type(e).__name__}")
        return None
    if not isinstance(dane, dict):
        return ""
    return " · ".join(str(x) for x in (dane.get("name"), dane.get("email")) if x)[:160]


def opis(lead: Lead) -> str:
    """Kto polecił, jednym ciągiem do powiadomień: „Imię · mail (slug)" albo sam slug."""
    if not lead.ref:
        return ""
    return f"{lead.ref_partner} ({lead.ref})" if lead.ref_partner else lead.ref


def plan_leada(session, email: str | None) -> tuple[str, int] | None:
    """Pierwszy opłacony plan tradera o tym mailu: (etykieta, steps) albo None.

    Pierwszy, bo za niego należy się konto partnerowi — dokupione później nie
    zmienia tego, co kupił jako polecony. Przy BOGO liczy się to, za co zapłacił
    (`bogo_paid_key`), a nie większy tier przyznany przez admina.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    zamowienie = (session.query(Order).join(Trader, Trader.id == Order.trader_id)
                  .filter(func.lower(Trader.email) == email, Order.status == "paid")
                  .order_by(Order.id).first())
    if zamowienie is None:
        return None
    klucz = zamowienie.bogo_paid_key or zamowienie.product_key
    prod = session.query(Product).filter(Product.key == klucz).one_or_none()
    return (prod.label, prod.steps) if prod else (klucz, 2)


def zglos(slug: str | None, email: str, rozmiar: str | None = None,
          wyplata: bool = False) -> None:
    """Jak `wyslij`, ale po odesłaniu odpowiedzi, jeśli trwa request."""
    if czysty_slug(slug):
        notify.w_tle(wyslij, slug, email, rozmiar, wyplata)


def po_wyplacie(session, trader_email: str | None) -> None:
    """Wypłata poszła: jeśli ten trader przyszedł z linku partnera, potwierdź polecenie.

    Lead i trader łączą się wyłącznie mailem — innego klucza między nimi nie ma.
    Konta z bota wypłat i z importu nie mają leada, więc tu nie wchodzą.
    """
    email = (trader_email or "").strip().lower()
    if not email:
        return
    lead = session.query(Lead).filter(Lead.email == email).one_or_none()
    if lead and lead.ref:
        zglos(lead.ref, email, None, True)
