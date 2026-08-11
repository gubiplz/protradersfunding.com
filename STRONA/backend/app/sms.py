"""SMS do leada (Twilio).

Powód istnienia jest wąski i warto go trzymać na oku: **część leadów nie ma
działającego Telegrama**. Zgłoszenie jest, numer jest, a jedyny kanał, którym
dział pisze, milczy — i człowiek przepada nie dlatego, że nie chciał, tylko
dlatego, że nie było czym go dotknąć. SMS jest zapasowym wyjściem, nie nowym
kanałem masowej wysyłki: leci z ręki, po jednym, z karty konkretnego leada.

Trzy zasady, te same co w `telegram.py`:

1. **Nigdy nie wywraca wywołania.** Błąd sieci, zły klucz czy odmowa Twilio
   kończą się `(False, powód)`, nie wyjątkiem. Powód wraca WYŻEJ, do panelu —
   inaczej admin widzi „nie poszło" i musi szukać w logach hostingu, że numer
   jest niepoprawny albo konto Twilio nie ma środków.

2. **Zero nowych zależności.** Twilio ma oficjalne SDK, ale to jeden POST
   z formularzem i Basic Auth. `urllib` z biblioteki standardowej robi to
   w kilkunastu linijkach, a bundel na Vercelu zostaje bez zmian.

3. **Transport wstrzykiwany** — testy nie ruszają sieci i nie kosztują.

Czego ten moduł CELOWO nie robi: nie zgaduje kierunkowego. Numer bez `+` jest
odrzucany, choć kusi, żeby dokleić kraj z `phone_iso`. Zgadnięty kierunkowy to
SMS do obcej osoby — płatny, z treścią o cudzym zgłoszeniu, i nie do cofnięcia.
Panel i tak pokazuje akcje telefoniczne dopiero przy numerze z `+`, więc
odmowa tutaj jest spójna z tym, co dział już widzi.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from .config import get_settings

settings = get_settings()

API = "https://api.twilio.com/2010-04-01"
TIMEOUT_SEK = 20
# Trzy segmenty GSM-7. Dłuższa wiadomość nie jest błędem u Twilio — jest droższa
# i po cichu, więc granica stoi tu, gdzie widać ją przy pisaniu, a nie na
# rachunku pod koniec miesiąca.
MAX_ZNAKOW = 480

# Kontrakt transportu:
#     transport(url: str, body: bytes, headers: dict) -> tuple[int, bytes]


def is_enabled() -> bool:
    """Czy w ogóle jest czym wysłać i dokąd. Brak konfiguracji to normalny stan —
    panel ma wtedy nie proponować przycisku, zamiast dawać go i mylić.

    Adres Telegrama liczy się na równi z kluczami: SMS bez niego byłby mostem
    donikąd, a człowiek dostałby wiadomość, na którą nie ma jak odpowiedzieć.
    """
    return bool(settings.twilio_sid and settings.twilio_token
                and settings.twilio_from and settings.sms_telegram_url)


def tresc(imie: str | None, *, zakwalifikowany: bool) -> str:
    """Jedyne miejsce z treścią SMS-a — woła to i przycisk w panelu, i klik
    w Telegramie. Rozjazd tych dwóch ścieżek znaczyłby, że ten sam człowiek
    dostaje inną wiadomość zależnie od tego, gdzie akurat kliknął dział.

    Obie wersje kończą się tym samym: linkiem do Telegrama. Odmowa też, i to
    świadomie — „nie tym razem" bez drogi odpowiedzi zamyka temat na zawsze,
    a część odrzuconych wraca po miesiącu z innym budżetem.

    Dwie rzeczy trzymają tę treść w ryzach i obie kosztują, gdy się je złamie:

    1. **Tylko znaki z GSM-7.** Jedna półpauza, jeden „ładny" apostrof czy polski
       ogonek przełącza CAŁĄ wiadomość na UCS-2, gdzie segment ma 70 znaków
       zamiast 160. To nie jest błąd, tylko potrojony rachunek — po cichu, bo
       Twilio wysyła bez mrugnięcia. Pilnuje tego `test_sms.py`.

    2. **Marka i wyjście w treści.** Amerykański operator wymaga, by wiadomość
       mówiła, kto pisze, i dawała „STOP". Bez tego numer wypada z weryfikacji
       i przestaje dowozić cokolwiek. STOP obsługuje sam Twilio.
    """
    pierwsze = (imie or "").strip().split(" ")[0] or "there"
    link = settings.sms_telegram_url
    if zakwalifikowany:
        return (f"Hi {pierwsze}, Forex Passing desk here - your application went "
                f"through. Next step is Telegram: {link} Reply STOP to opt out.")
    return (f"Hi {pierwsze}, Forex Passing desk here. Not a yes as it stands, but "
            f"worth one conversation: {link} Reply STOP to opt out.")


def numer_e164(surowy: str | None) -> str | None:
    """Numer gotowy do wysyłki albo `None`, gdy nie da się go użyć BEZ zgadywania.

    Wpisy z landingu przychodzą z `+` (widget telefonu wymusza format
    międzynarodowy), ale w bazie siedzą też numery dopisane z ręki. Spacje,
    myślniki i nawiasy lecą — one nic nie znaczą; brak `+` znaczy bardzo dużo.
    """
    tekst = (surowy or "").strip()
    if tekst.startswith("00"):  # zapis „00 48…" to ten sam numer co „+48…"
        tekst = "+" + tekst[2:]
    if not tekst.startswith("+"):
        return None
    cyfry = "".join(z for z in tekst[1:] if z.isdigit())
    # E.164: maksimum 15 cyfr. Dolna granica jest ostrożna — krótsze bywają
    # ucięte przy przepisywaniu i trafiłyby w kogoś innego.
    return f"+{cyfry}" if 8 <= len(cyfry) <= 15 else None


def _urllib_transport(url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as odp:
            return odp.status, odp.read()
    except urllib.error.HTTPError as e:
        # Twilio opisuje odmowę w ciele („is not a valid phone number",
        # „Permission to send an SMS has not been enabled for the region").
        return e.code, e.read()


def wyslij(numer: str | None, tresc: str, *, transport=None) -> tuple[bool, str]:
    """`(czy poszło, powód odmowy)`. Nigdy nie rzuca.

    Nadawcą jest `TWILIO_FROM`. Gdy zaczyna się od `MG`, Twilio traktuje to jako
    Messaging Service — na numery amerykańskie to jedyna droga, która przechodzi
    przez A2P 10DLC, więc obie formy muszą być obsłużone tym samym polem.
    """
    if not is_enabled():
        return False, "SMS is not configured"
    cel = numer_e164(numer)
    if not cel:
        return False, "no usable phone number (needs the international +… form)"
    tresc = (tresc or "").strip()
    if not tresc:
        return False, "empty message"
    if len(tresc) > MAX_ZNAKOW:
        return False, f"message too long ({len(tresc)}/{MAX_ZNAKOW} characters)"

    nadawca = settings.twilio_from
    pola = {"To": cel, "Body": tresc}
    pola["MessagingServiceSid" if nadawca.startswith("MG") else "From"] = nadawca
    body = urllib.parse.urlencode(pola).encode()
    klucz = base64.b64encode(
        f"{settings.twilio_sid}:{settings.twilio_token}".encode()).decode()
    url = f"{API}/Accounts/{settings.twilio_sid}/Messages.json"
    naglowki = {"Authorization": f"Basic {klucz}",
                "Content-Type": "application/x-www-form-urlencoded"}
    try:
        status, odpowiedz = (transport or _urllib_transport)(url, body, naglowki)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[sms] błąd sieci: {e}")
        return False, f"network error: {e}"
    try:
        dane = json.loads(odpowiedz or b"{}") or {}
    except Exception:
        dane = {}
    if status in (200, 201):
        return True, ""
    powod = dane.get("message") or f"Twilio returned {status}"
    print(f"[sms] odmowa {status}: {powod}")
    return False, powod
