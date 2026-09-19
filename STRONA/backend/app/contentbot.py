"""Kolejka postów na kanały Telegrama, z walidacją twierdzeń.

Zbudowany celowo jak `captions.js` z repo forexpassing.com: **rzuca wyjątkiem
zamiast opublikować nieprawdę**. Tamten automat stał trzy tygodnie, bo podpis
mówił „every single month in the green", a jeden miesiąc wyszedł na minus —
i to było zachowanie poprawne. Kanał został nietknięty, zamiast dostać
twierdzenie, które przestało być prawdziwe.

Ta sama zasada tutaj, z jedną różnicą: tam liczby wchodziły do szablonu, więc
asercje dało się wpisać na sztywno. Tu tekst pisze człowiek, więc nie da się
z góry wiedzieć, co twierdzi — i dlatego każdy post niesie `proof`, czyli
maszynowo sprawdzalne źródło swoich liczb:

    ""                      — treść ponadczasowa; żadnych kwot i procentów
    "payout:<cert_token>"   — każda kwota w tekście musi zgadzać się z wypłatą
    "stat:<klucz>:<op>:<v>" — relacja przeliczana z bieżących danych

Walidacja biegnie DWA razy: przy zatwierdzeniu i ponownie tuż przed publikacją.
Post zatwierdzony w poniedziałek nie ma prawa wyjść w piątek na poniedziałkowych
liczbach — a właśnie na tym polega kolejka z terminem.

Czego walidator NIE robi: nie blokuje słownictwa niedoboru („only 2 spots left").
Licznik miejsc jedzie w opisie kanału z `spots.js` po stronie forexpassing.com
i to jest świadoma decyzja właściciela — walidator nie może odrzucać w kolejce
tego, co tym samym kanałem idzie obok niego.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import certshot, telegram
from .config import get_settings
from .models import Account, ChannelPost, Payout, Trader

settings = get_settings()

# Kanał -> (nazwa ustawienia z chat_id, opis do panelu).
KANALY = {
    "mgmt": ("telegram_mgmt_chat_id", "Account Management"),
    "payouts": ("telegram_chat_id", "Payouts"),
    "trackrecord": ("telegram_trackrecord_chat_id", "Track Record"),
}

STATUSY = ("draft", "approved", "scheduled", "published", "failed")

# Limity Telegrama. Podpis pod zdjęciem ma 1024 znaki, sam tekst 4096.
LIMIT_PODPISU = 1024
LIMIT_TEKSTU = 4096

# Cokolwiek, co wygląda na twierdzenie liczbowe: kwota, procent albo licznik.
# Tekst bez `proof` nie ma prawa zawierać żadnego z nich — nie dlatego, że
# liczby są złe, tylko dlatego, że nikt nie wskazał, skąd pochodzą.
LICZBY_RX = re.compile(r"\$\s?\d|\d[\d,. ]*\s?%|\b\d[\d,]{2,}\b")
KWOTY_RX = re.compile(r"\$\s?([\d][\d,]*(?:\.\d+)?)")


class NieprawdziwyPost(ValueError):
    """Twierdzenie w treści nie ma pokrycia w danych."""


def sprawdz(warunek, opis: str) -> None:
    """Asercja z komunikatem dla człowieka. Ten sam wzorzec co w `captions.js`."""
    if not warunek:
        raise NieprawdziwyPost(opis)


def chat_id(kanal: str) -> str:
    nazwa = KANALY.get(kanal, ("", ""))[0]
    return getattr(settings, nazwa, "") if nazwa else ""


# --------------------------------------------------------------------------- #
#  Dane, na których stoją dowody                                              #
# --------------------------------------------------------------------------- #
def statystyki_publiczne(session) -> dict:
    """Liczby pokazywane publicznie — JEDNA implementacja na dwa zastosowania.

    Czyta je `/api/public/stats` (przez swój cache) i czyta je walidator. Dwie
    osobne implementacje tych samych liczb rozjechałyby się przy pierwszej
    zmianie definicji, a wtedy walidator przepuszczałby posty sprzeczne ze
    stroną albo odrzucał zgodne.
    """
    wyplaty = session.query(Payout).filter(Payout.paid.is_(True)).all()
    kraje = {t.kyc_country.strip().lower()
             for t in session.query(Trader).filter(Trader.kyc_status == "approved").all()
             if t.kyc_country}
    return {
        "accounts_total": session.query(Account).count(),
        "active_accounts": session.query(Account).filter(Account.status == "active").count(),
        "funded_accounts": session.query(Account).filter(Account.status == "funded").count(),
        "traders_total": session.query(Trader).filter(Trader.is_admin.is_(False)).count(),
        "payouts_count": len(wyplaty),
        "payouts_total_usd": int(round(sum(p.trader_share for p in wyplaty))),
        "largest_payout_usd": int(round(max((p.trader_share for p in wyplaty), default=0.0))),
        "countries_count": len(kraje),
    }


def _kwoty(tekst: str) -> list[float]:
    """Wszystkie kwoty `$…` z treści, jako liczby."""
    out = []
    for surowa in KWOTY_RX.findall(tekst or ""):
        try:
            out.append(float(surowa.replace(",", "")))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------- #
#  Walidacja                                                                   #
# --------------------------------------------------------------------------- #
def waliduj(session, post: ChannelPost) -> None:
    """Rzuca `NieprawdziwyPost`, gdy treść twierdzi coś, czego dane nie potwierdzają."""
    sprawdz(post.channel in KANALY, f"nieznany kanał „{post.channel}”")
    sprawdz(chat_id(post.channel), f"kanał {KANALY[post.channel][1]} nie ma ustawionego czatu")

    tresc = (post.body or "").strip()
    sprawdz(tresc, "pusta treść")

    # Odmowa, nie ciche przycięcie. `send_photo_json` tnie podpis do 1024 znaków,
    # co ucina twierdzenie w połowie zdania — a obcięte zdanie potrafi znaczyć
    # coś innego niż całe.
    limit = LIMIT_PODPISU if post.kind == "photo" else LIMIT_TEKSTU
    sprawdz(len(tresc) <= limit,
            f"treść ma {len(tresc)} znaków, a limit dla tego typu posta to {limit} "
            f"— Telegram utnie ją w połowie zdania")

    if post.kind == "photo":
        sprawdz(post.media_url, "post ze zdjęciem bez adresu do zrzutu")

    dowod = (post.proof or "").strip()

    if not dowod:
        sprawdz(not LICZBY_RX.search(tresc),
                "treść niesie liczbę, a nie ma wskazanego źródła. Dopisz `proof` "
                "(`payout:<token>` albo `stat:<klucz>:<op>:<wartość>`) albo usuń liczbę")
        return

    if dowod.startswith("payout:"):
        token = dowod.split(":", 1)[1].strip()
        sprawdz(token, "pusty token wypłaty w `proof`")
        wyplata = (session.query(Payout)
                   .filter(Payout.cert_token == token).one_or_none())
        sprawdz(wyplata is not None, f"nie ma wypłaty o certyfikacie {token}")
        sprawdz(bool(wyplata.paid), f"wypłata {token} nie jest oznaczona jako wypłacona")
        # To jest ta asercja, która czyni „$6,180 payout confirmed" niepodrabialnym:
        # każda kwota w tekście musi być kwotą TEJ wypłaty.
        from .payoutbot import kwota_txt
        dozwolone = {round(float(wyplata.trader_share or 0.0), 2),
                     round(float(wyplata.profit_amount or 0.0), 2)}
        for kwota in _kwoty(tresc):
            sprawdz(round(kwota, 2) in dozwolone,
                    f"w treści jest {kwota_txt(kwota)}, a wypłata {token} to "
                    f"{kwota_txt(wyplata.trader_share)} dla tradera "
                    f"({kwota_txt(wyplata.profit_amount)} zysku)")
        return

    if dowod.startswith("stat:"):
        czesci = dowod.split(":")
        sprawdz(len(czesci) == 4, "`proof` typu stat ma postać `stat:<klucz>:<op>:<wartość>`")
        _, klucz, op, wartosc = czesci
        dane = statystyki_publiczne(session)
        sprawdz(klucz in dane, f"nieznana statystyka „{klucz}”; dostępne: "
                               f"{', '.join(sorted(dane))}")
        try:
            prog = float(wartosc)
        except ValueError:
            raise NieprawdziwyPost(f"„{wartosc}” nie jest liczbą")
        biezaca = float(dane[klucz])
        relacje = {"gte": biezaca >= prog, "lte": biezaca <= prog,
                   "eq": abs(biezaca - prog) < 0.01}
        sprawdz(op in relacje, f"nieznany operator „{op}”; dostępne: gte, lte, eq")
        sprawdz(relacje[op],
                f"twierdzenie przestało być prawdziwe: {klucz} wynosi dziś "
                f"{biezaca:g}, a post zakłada {op} {prog:g}")
        return

    raise NieprawdziwyPost(f"nieznany rodzaj dowodu „{dowod}”; użyj `payout:…` albo `stat:…`")


# --------------------------------------------------------------------------- #
#  Publikacja                                                                  #
# --------------------------------------------------------------------------- #
def opublikuj(session, post: ChannelPost, *, transport_shot=None,
              transport_tg=None) -> dict:
    """Waliduje PONOWNIE i publikuje. Best-effort: zapisuje błąd, nie rzuca dalej.

    Ponowna walidacja nie jest nadmiarowa. Między zatwierdzeniem a publikacją
    mija zaplanowany czas, a w nim wypłata może zostać wycofana, a statystyka
    spaść poniżej progu.
    """
    try:
        waliduj(session, post)
    except NieprawdziwyPost as e:
        post.status = "failed"
        post.last_error = str(e)[:300]
        post.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"posted": False, "reason": str(e)}

    bot = telegram.desk("content")
    czat = chat_id(post.channel)
    if not bot.token:
        return {"posted": False, "reason": "no bot token"}

    png = certshot.render(post.media_url, transport=transport_shot) \
        if (post.kind == "photo" and post.media_url) else None

    if png:
        ok, powod, dane = telegram._strzal_json(
            "sendPhoto",
            {"chat_id": czat, "caption": post.body[:LIMIT_PODPISU], "parse_mode": "HTML"},
            ("photo", "post.png", png), transport_tg, bot.token)
    else:
        # Bez grafiki idzie sam tekst. Post ze zdjęciem, którego nie udało się
        # zrobić, NIE jest błędem treści — treść jest prawdziwa tak samo.
        ok, powod, dane = telegram._strzal_json(
            "sendMessage",
            {"chat_id": czat, "text": post.body[:LIMIT_TEKSTU], "parse_mode": "HTML",
             "disable_web_page_preview": "true"},
            None, transport_tg, bot.token)

    if ok:
        post.status = "published"
        post.published_at = datetime.now(timezone.utc)
        post.message_id = dane.get("message_id")
        post.post_url = telegram.post_url(dane)
        post.last_error = ""
    else:
        post.status = "failed"
        post.last_error = (powod or "unknown")[:300]
    post.updated_at = datetime.now(timezone.utc)
    session.commit()
    return {"posted": ok, "reason": "" if ok else post.last_error,
            "post_url": post.post_url, "photo": bool(png)}


def wyslij_zaplanowane(session, now: datetime | None = None) -> dict:
    """Publikuje JEDEN zaległy post na przebieg.

    Jeden, nie wszystkie: kolejka zostawiona na tydzień wysypałaby osiem postów
    w półtorej minuty, co na kanale wygląda jak awaria, a nie jak publikacja.
    Kolejne wyjdą przy następnym ticku.
    """
    teraz = now or datetime.now(timezone.utc)
    zalegle = (session.query(ChannelPost)
               .filter(ChannelPost.status == "scheduled",
                       ChannelPost.scheduled_for.isnot(None))
               .order_by(ChannelPost.scheduled_for).all())
    for post in zalegle:
        termin = post.scheduled_for
        if termin.tzinfo is None:
            # SQLite oddaje daty bez strefy; porównanie z `teraz` ze strefą
            # rzuciłoby TypeError. Ten sam problem co w `_wyslij_zaplanowane`.
            termin = termin.replace(tzinfo=timezone.utc)
        if termin > teraz:
            continue
        wynik = opublikuj(session, post)
        return {"sent": 1 if wynik.get("posted") else 0, "id": post.id,
                "reason": wynik.get("reason", "")}
    return {"sent": 0}
