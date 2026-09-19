"""Kolejka postów na kanały Telegrama, z walidacją twierdzeń.

Zbudowana celowo jak generator podpisów po stronie landingu: **rzuca wyjątkiem
zamiast opublikować nieprawdę**. Tamten automat stał trzy tygodnie, bo podpis
mówił „every single month in the green", a jeden miesiąc wyszedł na minus —
i to było zachowanie poprawne. Kanał został nietknięty, zamiast dostać
twierdzenie, które przestało być prawdziwe.

Ta sama zasada tutaj, z jedną różnicą: tam liczby wchodziły do szablonu, więc
asercje dało się wpisać na sztywno. Tu tekst pisze człowiek, więc nie da się
z góry wiedzieć, co twierdzi — i dlatego każdy post niesie `proof`, czyli
maszynowo sprawdzalne źródło swoich liczb.

Walidacja biegnie DWA razy: przy zatwierdzeniu i ponownie tuż przed publikacją.
Przy kolejce z terminem to nie jest ostrożność na wyrost — między jednym
a drugim mijają dni, a wypłata może zostać wycofana albo statystyka spaść
poniżej progu.

Czego walidator NIE robi: nie blokuje słownictwa niedoboru („only 2 spots
left"). Licznik miejsc jedzie w opisie tego samego kanału, wpisywany przez
landing, i to świadoma decyzja właściciela — kolejka nie może odrzucać tego,
co tym samym kanałem idzie obok niej.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func

from . import certshot, telegram
from .config import get_settings
from .models import Account, ChannelPost, Payout, Trader

settings = get_settings()

# Kanał -> (pole ustawień z czatem, nazwa do panelu).
KANALY = {
    "mgmt": ("telegram_mgmt_chat_id", "Account Management"),
    "payouts": ("telegram_chat_id", "Payouts"),
    "trackrecord": ("telegram_trackrecord_chat_id", "Track Record"),
}

STATUSY = ("draft", "approved", "scheduled", "published", "failed")

# Limity Telegrama. Podpis pod zdjęciem 1024 znaki, sam tekst 4096.
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
    pole = KANALY.get(kanal, ("", ""))[0]
    return str(getattr(settings, pole, "") or "") if pole else ""


# --------------------------------------------------------------------------- #
#  Dane, na których stoją dowody                                              #
# --------------------------------------------------------------------------- #
def statystyki_publiczne(session) -> dict:
    """Liczby pokazywane publicznie — JEDNA implementacja na dwa zastosowania.

    Czyta je `/api/public/stats` i czyta je walidator. Dwie osobne
    implementacje tych samych liczb rozjechałyby się przy pierwszej zmianie
    definicji, a wtedy walidator przepuszczałby posty sprzeczne ze stroną albo
    odrzucał zgodne.
    """
    # Agregaty w SQL, nie `.all()` w Pythonie: `Payout` rośnie codziennie,
    # a te liczby czyta landing przy każdym wejściu — pełny transfer tabeli
    # z Supabase tylko po to, żeby policzyć sumę, był realnym kosztem.
    pay_cnt, pay_sum, pay_max = (
        session.query(func.count(Payout.id),
                      func.coalesce(func.sum(Payout.trader_share), 0.0),
                      func.coalesce(func.max(Payout.trader_share), 0.0))
        .filter(Payout.paid.is_(True)).one())
    kraje = (session.query(func.count(func.distinct(
                func.lower(func.trim(Trader.kyc_country)))))
             .filter(Trader.kyc_status == "approved",
                     Trader.kyc_country.isnot(None),
                     func.trim(Trader.kyc_country) != "").scalar())
    return {
        "accounts_total": session.query(Account).count(),
        "active_accounts": session.query(Account).filter(Account.status == "active").count(),
        "funded_accounts": session.query(Account).filter(Account.status == "funded").count(),
        "traders_total": session.query(Trader).filter(Trader.is_admin.is_(False)).count(),
        "payouts_count": pay_cnt,
        # Pełne dolary: ".96" przy sześciocyfrowej kwocie poszerzał kafel LP aż do obcięcia.
        "payouts_total_usd": int(round(pay_sum)),
        "largest_payout_usd": int(round(pay_max)),
        "countries_count": kraje,
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
    sprawdz(chat_id(post.channel),
            f"kanał {KANALY[post.channel][1]} nie ma ustawionego czatu")

    tresc = (post.body or "").strip()
    sprawdz(tresc, "pusta treść")

    # Odmowa, nie ciche przycięcie. Telegram utnie podpis do 1024 znaków
    # w połowie zdania, a obcięte zdanie potrafi znaczyć coś innego niż całe.
    limit = LIMIT_PODPISU if post.kind == "photo" else LIMIT_TEKSTU
    sprawdz(len(tresc) <= limit,
            f"treść ma {len(tresc)} znaków, a limit dla tego typu posta to {limit} "
            f"— Telegram utnie ją w połowie zdania")

    if post.kind == "photo":
        sprawdz(post.media_url, "post ze zdjęciem bez adresu grafiki")

    dowod = (post.proof or "").strip()

    if not dowod:
        sprawdz(not LICZBY_RX.search(tresc),
                "treść niesie liczbę, a nie ma wskazanego źródła. Dopisz `proof` "
                "(`payout:<token>` albo `stat:<klucz>:<op>:<wartość>`) albo usuń liczbę")
        return

    if dowod.startswith("payout:"):
        token = dowod.split(":", 1)[1].strip()
        sprawdz(token, "pusty token wypłaty w `proof`")
        wyplata = session.query(Payout).filter(Payout.cert_token == token).one_or_none()
        sprawdz(wyplata is not None, f"nie ma wypłaty o certyfikacie {token}")
        sprawdz(bool(wyplata.paid), f"wypłata {token} nie jest oznaczona jako wypłacona")
        # To jest ta asercja, która czyni „$6,180 payout confirmed for Ryan F."
        # niepodrabialnym: każda kwota w tekście musi być kwotą TEJ wypłaty.
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
        sprawdz(klucz in dane,
                f"nieznana statystyka „{klucz}”; dostępne: {', '.join(sorted(dane))}")
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

    Wołane także z crona, gdzie wyjątek zabrałby cały przebieg ticku.
    """
    try:
        waliduj(session, post)
    except NieprawdziwyPost as e:
        post.status = "failed"
        post.last_error = str(e)[:300]
        post.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"posted": False, "reason": str(e)}

    czat = chat_id(post.channel)
    png = None
    if post.kind == "photo" and post.media_url:
        # Ta sama droga co przy certyfikatach: zrzut prawdziwej strony, zamiast
        # piątej kopii tego samego layoutu w kodzie.
        png = certshot.render(post.media_url, transport=transport_shot)

    ok, powod, dane = telegram.send_content(czat, post.body, png=png,
                                            transport=transport_tg)
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
    Przy przenosinach archiwum to jest wręcz cały sens — treść ma wracać
    rytmem, nie zrzutem.
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
            # rzuciłoby TypeError. Ten sam problem co przy przypomnieniach.
            termin = termin.replace(tzinfo=timezone.utc)
        if termin > teraz:
            continue
        wynik = opublikuj(session, post)
        return {"sent": 1 if wynik.get("posted") else 0, "id": post.id,
                "reason": wynik.get("reason", "")}
    return {"sent": 0}
