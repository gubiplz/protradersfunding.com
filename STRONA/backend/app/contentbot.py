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

import html
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from . import certshot, reach, telegram
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
_ZNACZNIKI_RX = re.compile(r"<[^>]+>")


def dlugosc_widoczna(tekst: str) -> int:
    """Ile znaków NAPRAWDĘ zobaczy Telegram.

    Przy `parse_mode=HTML` limit dotyczy tekstu PO sparsowaniu: znaczniki
    i encje się do niego nie liczą. Mierzenie surowego HTML-a odrzucało posty,
    które w rzeczywistości się mieszczą — `<a href="…">` to kilkadziesiąt
    znaków, których czytelnik nigdy nie zobaczy, a `&#39;` zamiast apostrofu
    to pięć zamiast jednego. Tą pomyłką cztery posty z archiwum straciły
    grafikę: import degradował je do samego tekstu, bo „nie mieściły się”
    w podpisie, w którym mieściły się z zapasem.
    """
    return len(html.unescape(_ZNACZNIKI_RX.sub("", tekst or "")))


LIMIT_PODPISU = 1024
# Rodzaje postów, które niosą załącznik — i przez to krótszy limit podpisu.
ZE_ZALACZNIKIEM = ("photo", "video")
# Rozszerzenia rozpoznawane jako GOTOWY plik. Świadomie wąskie: adres strony
# z parametrem (np. `/payout/xxx?bare=1`) ma dalej iść do certshota.
_OBRAZKI = (".png", ".jpg", ".jpeg", ".webp")
_FILMY = (".mp4",)


def _sciezka_adresu(adres: str) -> str:
    """Sama ścieżka adresu, bez query stringa i kotwicy."""
    return str(adres or "").split("?", 1)[0].split("#", 1)[0].lower()


def _jest_obrazkiem(adres: str) -> bool:
    return _sciezka_adresu(adres).endswith(_OBRAZKI)


def _jest_filmem(adres: str) -> bool:
    return _sciezka_adresu(adres).endswith(_FILMY)

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
    # Podpis pod zdjęciem I pod filmem ma ten sam limit 1024 znaków.
    limit = LIMIT_PODPISU if post.kind in ZE_ZALACZNIKIEM else LIMIT_TEKSTU
    dlugosc = dlugosc_widoczna(post.body)
    sprawdz(dlugosc <= limit,
            f"treść ma {dlugosc} znaków, a limit dla tego typu posta to {limit} "
            f"— Telegram utnie ją w połowie zdania")

    if post.kind in ZE_ZALACZNIKIEM:
        sprawdz(post.media_url,
                "post ze zdjęciem bez adresu grafiki" if post.kind == "photo"
                else "post z filmem bez adresu klipu")
    if post.kind == "video":
        sprawdz(_jest_filmem(post.media_url),
                "adres klipu musi wskazywać na plik wideo (.mp4)")

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

    if dowod.startswith("archive:"):
        # Dowód POCHODZENIA, nie aktualności. Mówi: „ten tekst przyszedł
        # wprost z archiwum naszego własnego kanału i nikt go od tego czasu nie
        # ruszał" — a człowiek zatwierdził całą partię, wgrywając plik. NIE mówi,
        # że liczby są nadal prawdziwe; dlatego import zostawia jako szkice
        # wszystko, co niesie twierdzenie związane z czasem (patrz CZASOWE_RX),
        # a edycja treści kasuje ten dowód (PATCH w main.py).
        sprawdz(post.origin == dowod,
                "dowód `archive:` obowiązuje tylko dla posta wgranego z archiwum "
                "i nieedytowanego — po zmianie treści trzeba wskazać inne źródło")
        return

    raise NieprawdziwyPost(f"nieznany rodzaj dowodu „{dowod}”; użyj `payout:…`, "
                           f"`stat:…` albo `archive:…`")


# --------------------------------------------------------------------------- #
#  Publikacja                                                                  #
# --------------------------------------------------------------------------- #
def opublikuj(session, post: ChannelPost, *, transport_shot=None,
              transport_tg=None, transport_reach=None) -> dict:
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
    png = adres_foto = adres_klipu = None
    if post.kind == "video" and post.media_url:
        adres_klipu = post.media_url
    elif post.kind == "photo" and post.media_url:
        if post.origin.startswith("archive:") or _jest_obrazkiem(post.media_url):
            # Gotowy obraz leci ADRESEM: Telegram pobiera go sam. Tą drogą idą
            # grafiki zatwierdzone przed publikacją — to, co widział admin,
            # jest dokładnie tym, co zobaczy kanał.
            adres_foto = post.media_url
        else:
            # Ta sama droga co przy certyfikatach: zrzut prawdziwej strony,
            # zamiast piątej kopii tego samego layoutu w kodzie.
            png = certshot.render(post.media_url, transport=transport_shot)

    if post.kind in ZE_ZALACZNIKIEM and not (png or adres_foto or adres_klipu):
        # Bez tego post wychodził jako goły tekst i zapisywał się jako
        # `published` — awaria wyglądała w panelu jak sukces.
        powod = (f"post rodzaju „{post.kind}” nie ma czego wysłać: "
                 f"zrzut się nie udał, a adres nie wskazuje na gotowy plik")
        post.status = "failed"
        post.last_error = powod[:300]
        post.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"posted": False, "reason": powod}

    ok, powod, dane = telegram.send_content(czat, post.body, png=png,
                                            photo_url=adres_foto,
                                            video_url=adres_klipu,
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
    # Zasieg dokupujemy tak samo jak przy wyplatach. Do tej pory `po_publikacji`
    # wolal WYLACZNIE payout bot, wiec post z kolejki tresci wychodzil bez
    # reakcji i wyswietlen — nie dlatego, ze reach bot byl wylaczony, tylko
    # dlatego, ze nikt go nie pytal. Best-effort: nieudane zamowienie nie moze
    # cofnac publikacji, ktora juz sie odbyla.
    zasieg = {}
    if ok:
        try:
            zasieg = reach.po_publikacji(session, post.post_url,
                                         transport=transport_reach, powod="content")
        except Exception as e:  # pragma: no cover - publikacji nie da sie cofnac
            # Post JUZ wisi na kanale. Wyjatek z dokupienia zasiegu nie moze
            # sie stad wydostac, bo wywolujacy zobaczylby nieudany przebieg
            # tam, gdzie publikacja w pelni sie powiodla.
            print(f"[contentbot] zasieg po publikacji nieudany: {e}")
            zasieg = {"ordered": 0, "error": str(e)}

    return {"posted": ok, "reason": "" if ok else post.last_error,
            "post_url": post.post_url,
            "photo": bool(png or adres_foto), "video": bool(adres_klipu),
            "reach": zasieg}


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


# --------------------------------------------------------------------------- #
#  Odtwarzanie treści ze starego kanału                                        #
# --------------------------------------------------------------------------- #
# Stary zestaw kanałów został porzucony po zamrożeniu konta. Jego treść wraca
# na nowy kanał RYTMEM, w jakim wpadała wcześniej — stary account management
# miał 18 postów w 18,5 dnia, czyli mniej więcej jeden dziennie. Jednorazowy
# zrzut osiemnastu postów wyglądałby jak awaria, nie jak prowadzenie kanału.
#
# Archiwum NIE leży w tym repozytorium i leżeć nie może: jest publiczne, a to
# są treści partnera. Plik wgrywa się z panelu, a zdjęcia lecą ADRESEM — Telegram
# pobiera je sam ze swojego CDN-u, więc nie trzeba ich nigdzie kopiować.

# Twierdzenia ZWIĄZANE Z CZASEM. Post, który mówi „w zeszłym miesiącu" albo
# podaje konkretną datę, powtórzony za pół roku jest po prostu nieprawdziwy —
# a dowód `archive:` tego nie wyłapie, bo potwierdza pochodzenie, nie
# aktualność. Takie posty import zostawia jako SZKICE, do decyzji człowieka.
CZASOWE_RX = re.compile(
    r"\b(last month|this month|last week|this week|today|yesterday|"
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b", re.I)

# Krótsze i tak nie są postami — „Channel created", „Channel photo updated".
MIN_DLUGOSC = 40

# Komunikaty SYSTEMOWE Telegrama, które w podglądzie kanału wyglądają jak post
# i bywają dłuższe od progu: „<nazwa kanału> pinned a video". Wpuszczone do
# kolejki wyszłyby na kanał jako zdanie o samym sobie.
SYSTEMOWE_RX = re.compile(
    r"\b(pinned a |pinned \"|joined the (channel|group)|"
    r"channel (created|photo (updated|removed))|video chat (started|ended))", re.I)

# Twierdzenia o LICZBIE WOLNYCH MIEJSC. Licznik miejsc na stronie zmienia się
# każdego dnia, a opis kanału podaje aktualną wartość — post z „Only 2 Spots
# Left" opublikowany w dniu, w którym licznik mówi co innego, przeczy własnemu
# kanałowi. To nie jest to samo co twierdzenie czasowe (nie ma w nim daty),
# ale starzeje się tak samo, więc kończy tak samo: szkicem.
MIEJSCA_RX = re.compile(r"\b\d+\s+spots?\s+(left|remaining|available)\b", re.I)


def wymaga_czlowieka(tekst: str) -> str:
    """Powód, dla którego post nie może pójść sam. Pusty = może."""
    trafienie = CZASOWE_RX.search(tekst)
    if trafienie:
        return f"time-bound claim: {trafienie.group(0)}"
    trafienie = MIEJSCA_RX.search(tekst)
    if trafienie:
        return f"spots-left claim: {trafienie.group(0)}"
    return ""


def _wpisy_archiwum(posty: list[dict]) -> list[dict]:
    """Tylko realne posty, najstarsze pierwsze."""
    out = [p for p in (posty or [])
           if str(p.get("text") or "").strip()
           and len(str(p["text"]).strip()) > MIN_DLUGOSC and p.get("id")
           # Archiwizator zapisuje typ komunikatu systemowego w `service`;
           # dla zwykłych postów jest tam `null`. Wzorzec jest zapasem na
           # archiwa, które tego pola nie mają.
           and not p.get("service")
           and not SYSTEMOWE_RX.search(str(p["text"]))]
    return sorted(out, key=lambda p: str(p.get("date") or ""))


def podglad_archiwum(posty: list[dict]) -> dict:
    """Co jest w pliku i ile z tego pójdzie samo, bez udziału człowieka."""
    wpisy = _wpisy_archiwum(posty)
    czasowe = [p for p in wpisy if wymaga_czlowieka(p["text"])]
    return {"total": len(wpisy), "auto": len(wpisy) - len(czasowe),
            "manual": len(czasowe)}


def importuj_archiwum(session, posty: list[dict], *, kanal: str = "mgmt",
                      co_ile_godzin: int = 24, start: datetime | None = None,
                      now: datetime | None = None) -> dict:
    """Wrzuca archiwalne posty do kolejki, rozłożone co `co_ile_godzin`.

    Post bez twierdzeń związanych z czasem dostaje dowód `archive:` i status
    `scheduled` — pójdzie sam. Post z takim twierdzeniem ląduje jako `draft`
    z zajętym terminem: widać go w kolejce, ale nie wyjdzie, dopóki ktoś go nie
    zatwierdzi. To jest granica, której automat nie przekracza.

    Idempotentne po `origin`: ponowne wgranie tego samego pliku nie zdubluje
    tego, co już wisi w kolejce.
    """
    teraz = now or datetime.now(timezone.utc)
    kiedy = start or (teraz + timedelta(hours=co_ile_godzin))

    juz = {o for (o,) in session.query(ChannelPost.origin)
           .filter(ChannelPost.origin.like("archive:%")).all()}

    dodane = pominiete = recznie = 0
    for wpis in _wpisy_archiwum(posty):
        origin = f"archive:{kanal}/{wpis['id']}"
        if origin in juz:
            pominiete += 1
            continue
        tresc = str(wpis["text"]).strip()
        czasowy = bool(wymaga_czlowieka(tresc))
        # Zdjęcie tylko wtedy, gdy podpis się w nim mieści — Telegram tnie
        # podpis na 1024 znakach, a obcięte zdanie potrafi znaczyć co innego.
        foto = (wpis.get("photos") or [None])[0]
        # Mierzymy TEKST WIDOCZNY, nie surowy HTML — inaczej post, ktory
        # miesci sie w podpisie, traci zdjecie przez wlasne znaczniki.
        ze_zdjeciem = bool(foto) and dlugosc_widoczna(tresc) <= LIMIT_PODPISU
        session.add(ChannelPost(
            channel=kanal,
            kind="photo" if ze_zdjeciem else "text",
            body=tresc,
            media_url=foto if ze_zdjeciem else None,
            proof=origin,
            status="draft" if czasowy else "scheduled",
            scheduled_for=kiedy,
            origin=origin,
            created_by="import"))
        kiedy += timedelta(hours=co_ile_godzin)
        dodane += 1
        if czasowy:
            recznie += 1
    session.commit()
    return {"added": dodane, "skipped": pominiete, "needs_review": recznie,
            "every_hours": co_ile_godzin}
