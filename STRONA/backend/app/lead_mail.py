"""Mail do leada — dno drabiny kontaktu.

Kolejność jest zawsze ta sama i wynika z tego, co o człowieku wiemy: Telegram,
bo tam dział pracuje; SMS, gdy handle'a nie podał; mail, gdy nie ma ani jednego,
ani drugiego. Adres jest jedynym polem, którego formularz nie puszcza pustego,
więc ten kanał ZAWSZE ma dokąd pójść — i tylko dlatego istnieje. Nie jest
newsletterem i nie ma się nim stać.

Trzy zasady, te same co w `sms.py` i `telegram.py`:

1. **Nigdy nie wywraca wywołania.** Zły adres, padnięty SMTP czy odmowa
   dostawcy kończą się `(False, powód)`, nie wyjątkiem. Powód wraca WYŻEJ, do
   panelu. To różnica wobec `notify.send()`, które błąd SMTP tylko drukuje —
   tam mail jest dodatkiem do operacji, która i tak się wydarzyła, a tutaj mail
   JEST operacją i „wysłane" bez pokrycia zostawiłoby leada z odhaczonym
   kontaktem, którego nikt nigdy nie miał.

2. **Zero nowych zależności.** `smtplib` i `email` z biblioteki standardowej.

3. **Transport wstrzykiwany** — testy nie ruszają sieci.

**Wysyła multipart: tekst ORAZ HTML.** Do 2026-08-11 szedł sam tekst, celowo —
wyśrodkowana karta z logo mówi „wysyłka masowa", zanim ktokolwiek przeczyta
pierwsze zdanie. Decyzja została odwrócona świadomie, na prośbę właściciela:
mail ma nieść markę landingu, bo lead widzi ją pierwszy raz od zgłoszenia.
Kompromis, który utrzymuje jedyną rzecz, którą ten mail sprzedaje — że
aplikację czytał ktoś żywy — jest w kształcie szablonu, nie w rezygnacji z HTML:
logo siedzi w lewym górnym rogu jak papier firmowy, nie na banerze przez całą
szerokość; jest jeden przycisk i zero kolumn, kafelków i stopek z ikonami.

Źródłem prawdy zostaje TEKST. `tresc()` składa go raz, `_html_z_tekstu()`
ubiera ten sam tekst w szablon, a panel i historia leada dalej trzymają wersję
tekstową. Dzięki temu nie da się doprowadzić do stanu, w którym w skrzynce
stoi co innego, niż dział widział w podglądzie — a taki rozjazd jest jedyną
awarią tego modułu, której nikt nie zauważa, dopóki lead nie zacytuje maila.

**Nie wysyła spod `MAIL_FROM`.** Człowiek zgłosił się przez landing marki
partnerskiej i o tej firmie nie słyszał. Mail z jej domeny jest dla niego
mailem od obcego, a przy okazji rozbiera rozdział marek, który reszta systemu
utrzymuje. Bez `LEAD_MAIL_FROM` ten kanał jest po prostu wyłączony.
"""
from __future__ import annotations

import json
import re
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape
from urllib.parse import quote

from .config import get_settings

settings = get_settings()

TIMEOUT_SEK = 20
# Nadawca podpisuje się nazwą marki z landingu, nie nazwą firmy, która to czyta.
MARKA = "Forex Passing"

# Sanity, nie walidacja RFC: ma odciąć wpisy z ręki („brak", „—", adres ze
# spacją), a nie rozstrzygać spory o to, co jest legalnym adresem. Jedyny koszt
# pomyłki w drugą stronę to odbicie od serwera, które i tak wróci jako `False`.
_ADRES = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Kontrakt transportu:
#     transport(msg: EmailMessage) -> None   (rzuca, gdy nie poszło)


def is_enabled() -> bool:
    """Czy jest czym i spod czego wysłać, i dokąd zaprowadzić.

    Adres Telegrama liczy się na równi z SMTP: mail bez niego kończyłby się
    zaproszeniem donikąd, a to gorsze niż brak maila — człowiek raz odpisze
    „gdzie?", drugi raz już nie.
    """
    return not czego_brakuje()


def czego_brakuje() -> list[str]:
    """Nazwy zmiennych, bez których kanał stoi — dla paska stanu w panelu.

    `SMS_TELEGRAM_URL` zaskakuje najbardziej: nazwa mówi o SMS-ie, a blokuje
    też mail, bo to jedyne miejsce z linkiem, do którego oba kanały prowadzą.
    Kto podpiął sam mail, nie ma prawa się tego domyślić — i bez tej listy widzi
    wyłącznie brak przycisku.

    `LEAD_TELEGRAM_CHANNEL_URL` blokuje CAŁY kanał, choć potrzebuje go tylko
    wersja odmowna. Wysyłka bez niego znaczyłaby maila do odrzuconego z pustym
    miejscem tam, gdzie ma być jedyne wyjście — a odmowa bez drogi dalej jest
    dokładnie tym, czego ten tekst unika. Lepiej, żeby dział zobaczył brakującą
    zmienną, niż żeby połowa leadów dostała ślepy zaułek.
    """
    return czego_brakuje_nadawcy() + [nazwa for nazwa, wartosc in (
        ("SMS_TELEGRAM_URL", settings.sms_telegram_url),
        ("LEAD_TELEGRAM_CHANNEL_URL", settings.lead_telegram_channel_url))
        if not wartosc]


def czego_brakuje_nadawcy() -> list[str]:
    """Czego brakuje, żeby WYSŁAĆ spod marki landingu — bez warunków automatu.

    Mail pisany z ręki w panelu nie prowadzi nigdzie „z automatu", więc nie
    potrzebuje URL-i Telegrama; potrzebuje nadawcy i drogi. Droga to Resend
    (klucz API) albo SMTP — jedno z dwóch wystarcza, dlatego brak obu zgłaszany
    jest jako jedna pozycja, a nie dwie.
    """
    braki = []
    # Kolejność jak dotąd: droga przed nadawcą, bo tak czyta ją pasek stanu.
    # Klucz Resend zastępuje SMTP, więc brak SMTP przy ustawionym kluczu nie
    # jest brakiem; bez obu nazwa zostaje „SMTP_HOST", bo tak ją zna panel.
    if not (settings.resend_api_key or settings.smtp_host):
        braki.append("SMTP_HOST")
    if not settings.lead_mail_from:
        braki.append("LEAD_MAIL_FROM")
    return braki


def nadawca_gotowy() -> bool:
    """Czy da się wysłać dowolny tekst spod marki landingu (panel: nadawca FX)."""
    return not czego_brakuje_nadawcy()


def adres(surowy: str | None) -> str | None:
    """Adres gotowy do wysyłki albo `None`."""
    tekst = (surowy or "").strip()
    return tekst if _ADRES.match(tekst) else None


def _link_do_dzialu(imie: str | None, *, free: bool = False) -> str:
    """Adres działu z pierwszą wiadomością już wpisaną w pole tekstowe.

    Tylko dla ZAKWALIFIKOWANEGO. Telegram czyta `?text=` także dla zwykłego
    konta, nie tylko dla bota, i tylko WPISUJE treść — wysłać musi człowiek.
    Landing tej marki niesie ten sam wzorzec (`watch.html`, `welcome.html`),
    więc dział widzi takie otwarcia od dawna i nie weźmie ich za cudzy skrypt.

    Treść mówi, kto pisze i z czym, bo dział dostaje ją od nieznanego handle'a
    i bez tego pierwsza wymiana zawsze schodzi na „kto to?" — a to jest cała
    minuta między człowiekiem gotowym rozmawiać a człowiekiem, który już
    odłożył telefon.

    Doklejane TU, a nie w `SMS_TELEGRAM_URL`, bo ten sam adres bierze `sms.py`,
    a tam każdy znak jest liczony do segmentu i długi `?text=` rozbiłby SMS na
    dwa. Wersja tekstowa maila zjada dłuższy link bez szkody.
    """
    kto = " ".join((imie or "").split())
    przedstawienie = f"This is {kto}. " if kto else ""
    wiadomosc = f"Hi. {przedstawienie}My application came back a yes."
    # Darmowy lejek ma swój desk (inne konto, inny bot) — patrz
    # `settings.telegram_url_desku`; SMS bierze ten sam link tą samą drogą.
    link = settings.telegram_url_desku(free)
    return f"{link}{'&' if '?' in link else '?'}text={quote(wiadomosc)}"


def tresc(imie: str | None, *, zakwalifikowany: bool,
          free: bool = False) -> tuple[str, str]:
    """`(temat, treść)`. Jedyne miejsce z tym tekstem — woła je i przycisk
    w panelu, i podgląd, który panel pokazuje PRZED wysyłką.

    Obie wersje kończą się linkiem do Telegrama — odmowa też, i to świadomie:
    „nie tym razem" bez żadnej drogi dalej zamyka temat na zawsze, a część
    odrzuconych wraca za kilka miesięcy z innym dorobkiem.

    Ale to NIE JEST ten sam link i na tym stoi cały podział. Zakwalifikowany
    idzie do działu, z gotową pierwszą wiadomością, bo jego mail obiecuje
    rozmowę. Odrzucony idzie na KANAŁ, gdzie się tylko dołącza i nie pisze
    nic — obiecywanie mu rozmowy oznaczałoby albo kłamstwo, albo dział
    tłumaczący każdemu odrzuconemu z osobna, czego zabrakło. Kanał trzyma
    drzwi otwarte za darmo i bez niczyjego czasu.

    Co w tym tekście jest robotą, a nie ozdobą:

    * **Temat bez sprzedaży.** Wygląda na odpowiedź konkretnej osoby, bo nią
      jest. Temat, który obiecuje, ląduje w Promocjach i tam umiera.
    * **Werdykt w pierwszym zdaniu.** Także ten zły. Mail, który owija, każe
      czytać trzy akapity, żeby zrozumieć, o co chodzi — i jest zamykany.
    * **Jedno wyjście.** Jeden link, żadnego wyboru. Drugie call-to-action
      zawsze zabiera klikniętą część pierwszemu.
    * **Zero zmyślonej personalizacji.** Kusi dopisać „spodobało nam się, że
      handlujesz od trzech lat" — nie wiemy tego. Jedno takie zdanie mija się
      z prawdą i zabiera wiarygodność całej reszcie, łącznie z werdyktem.

    Żadnej wersji nie da się wysłać do kogoś, kto o nic nie prosił: `main.py`
    puszcza tylko istniejącego leada, czyli człowieka, który sam wypełnił
    formularz. Zdanie o wypisie stoi na końcu obu i jest prawdziwe.
    """
    pierwsze = (imie or "").strip().split(" ")[0] or "there"
    stopka = (f"\n\n--\n{MARKA}\n"
              f"You are getting this because you applied on our site. "
              f"Reply with \"stop\" and we will not write again.")
    if zakwalifikowany:
        return (f"You're through, {pierwsze}", (
            f"Hi {pierwsze},\n\n"
            f"Your application is a yes. You are into the next step.\n\n"
            f"That part is not automatic — a person reads every application that "
            f"comes in, and yours came back a yes.\n\n"
            f"What happens now is a short conversation, not another form. Nothing "
            f"to prepare, no documents to dig up.\n\n"
            f"We do it on Telegram because that is where our desk works, and it is "
            f"the difference between starting this week and waiting on e-mail:\n\n"
            f"{_link_do_dzialu(imie, free=free)}\n\n"
            f"The first message is already written for you. Send it as it is — "
            f"we pick up the rest from your application.\n\n"
            f"One thing worth knowing: we work through applications in batches, and "
            f"the ones that go quiet get closed to make room. Yours is open now."
        ) + stopka)
    return (f"About your application, {pierwsze}", (
        f"Hi {pierwsze},\n\n"
        f"Straight answer: as it stands, this one is not a yes.\n\n"
        f"That is about the application, not about you, and it is not permanent. "
        f"What we look at changes as your record does, and the door stays open.\n\n"
        f"Before you close this, though: the easiest way to keep that door in "
        f"sight is our Telegram channel. Nothing to write and nobody to "
        f"introduce yourself to — you join, and you read:\n\n"
        f"{settings.lead_telegram_channel_url}\n\n"
        f"When your record moves, apply again. Until then no hard feelings, "
        f"and nobody chasing you."
    ) + stopka)


def _nadawca() -> str:
    """`From` i `Reply-To` zawsze z nazwą wyświetlaną.

    Bez niej Gmail pokazuje sam człon adresu przed małpą — lead widzi w
    skrzynce „contact", a nie markę, przez którą się zgłosił, i to jest
    pierwsza rzecz, na którą patrzy, decydując, czy w ogóle otworzyć. Nazwa
    wpisana w `LEAD_MAIL_FROM` ma pierwszeństwo, więc kto woli trzymać ją
    w konfiguracji, nadal może.
    """
    nazwa, adr = parseaddr(settings.lead_mail_from)
    return formataddr((nazwa or MARKA, adr)) if adr else settings.lead_mail_from


def _naglowek() -> str:
    """Logo marki wyśrodkowane nad treścią albo sama nazwa, gdy go nie ma.

    Brak `LEAD_MAIL_LOGO_URL` nie może wstrzymać wysyłki — mail bez obrazka
    dalej robi swoją robotę, a kanał, który stoi przez kosmetykę, nie dowozi
    nic. Wymiary są wpisane na sztywno, bo klient pocztowy bez nich przez
    moment rysuje obrazek w pełnej rozdzielczości i układ skacze.
    """
    # 128 px jak w mailach z landingu (`api/_lib/emails.js`): logo ma być
    # podpisem na górze kartki, nie banerem. `margin:0 auto` plus
    # `align="center"`, bo Outlook ignoruje samo `margin:auto`.
    if settings.lead_mail_logo_url:
        return (f'<img src="{escape(settings.lead_mail_logo_url, quote=True)}" '
                f'width="128" height="85" alt="{MARKA}" '
                f'style="display:block;margin:0 auto;width:128px;height:auto;border:0;'
                f'outline:none;text-decoration:none;font-size:13px;font-weight:600;'
                f'color:{_INK}">')
    return (f'<div style="font-size:19px;font-weight:700;color:{_INK};'
            f'letter-spacing:-.01em">{MARKA}</div>')


# Paleta i czcionka 1:1 z maili landingu (`api/_lib/emails.js` w repo marki):
# neutralna rampa Apple, zieleń jako JEDYNY akcent — na przycisku i nadtytule.
_PAGE = "#f5f5f7"
_INK = "#1d1d1f"
_SUBTLE = "#6e6e73"
_FAINT = "#86868b"
_HAIRLINE = "#d2d2d7"
_ACCENT = "#16a34a"
_FONT = ("-apple-system,BlinkMacSystemFont,'SF Pro Text','SF Pro Display',"
         "'Helvetica Neue',Helvetica,Arial,sans-serif")

_KROK = re.compile(r"^\s*(\d{1,2})[.)]\s+(.+)$")
_ETYKIETA = re.compile(r"^[A-Z0-9][A-Z0-9 &'’/,-]{2,40}$")


def _linia(odstep: int = 28) -> str:
    return (f'<div style="height:1px;line-height:1px;font-size:0;background:{_HAIRLINE};'
            f'margin:{odstep}px 0">&nbsp;</div>')


def _przycisk(napis: str, href: str) -> str:
    """Pigułka jak na landingu. Kolor trzy razy celowo: `bgcolor` dla klientów
    czytających tylko atrybuty, `background-color` (nie skrót `background`,
    który sanitizery wycinają) na komórce i na linku — biały napis na
    wyciętym tle to niewidzialny przycisk. `align="center"` obok
    `margin:auto`, bo Word w Outlooku ignoruje auto."""
    return (f'<table role="presentation" align="center" cellpadding="0" cellspacing="0" '
            f'style="margin:28px auto 0"><tr><td align="center" bgcolor="{_ACCENT}" '
            f'style="background-color:{_ACCENT};border-radius:980px">'
            f'<a href="{escape(href, quote=True)}" style="display:inline-block;'
            f'background-color:{_ACCENT};border-radius:980px;padding:13px 30px;color:#ffffff;'
            f'font-size:16px;font-weight:600;letter-spacing:-.01em;text-decoration:none">'
            f'{napis}</a></td></tr></table>')


def _napis_przycisku(url: str) -> str:
    # Napis idzie za ADRESEM, nie za gałęzią tekstu. „Message the desk" nad
    # linkiem do kanału obiecywałby rozmowę, której tam nie ma — w kanale nie
    # ma nawet pola do pisania. A link spoza Telegrama (portal, strona
    # płatności) nie ma prawa obiecywać Telegrama wcale.
    if url == settings.lead_telegram_channel_url:
        return "Join us on Telegram"
    if "t.me/" in url or "telegram.me/" in url:
        return "Message the desk on Telegram"
    return "Open the Link"


def _kroki(wiersze: list[tuple[str, str]]) -> str:
    """Lista „1. Tytuł: opis" jako kroki z landingu: szary numer, tytuł,
    jedno zdanie opisu, cienkie linie między krokami."""
    out = []
    for i, (numer, tresc_kroku) in enumerate(wiersze):
        tytul, opis = tresc_kroku, ""
        for sep in (": ", " — ", " – ", " - "):
            if sep in tresc_kroku:
                tytul, opis = tresc_kroku.split(sep, 1)
                break
        out.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td width="30" valign="top" style="padding-right:14px;color:{_FAINT};font-size:15px;'
            f'font-weight:600;padding-top:2px">{escape(numer)}</td><td valign="top">'
            f'<div style="color:{_INK};font-size:16px;font-weight:600;letter-spacing:-.01em">'
            f'{escape(tytul.strip())}</div>'
            + (f'<div style="color:{_SUBTLE};font-size:15px;line-height:1.5;margin-top:4px">'
               f'{escape(opis.strip())}</div>' if opis.strip() else "")
            + '</td></tr></table>'
            + (_linia(18) if i < len(wiersze) - 1 else ""))
    return f'<div style="margin:22px 0">{"".join(out)}</div>'


def _akapit(tekst: str, *, kolor: str, rozmiar: int = 16, srodek: bool = False,
            margines: str = "0 0 16px") -> str:
    linie = "<br>".join(escape(w) for w in tekst.split("\n"))
    return (f'<p style="margin:{margines};color:{kolor};font-size:{rozmiar}px;line-height:1.6'
            f'{";text-align:center" if srodek else ""}">{linie}</p>')


def _stopka_zewnetrzna() -> str:
    """Pod kartką, jak na landingu: marka, domena i adres z NADAWCY (z
    ustawień, nie z kodu — domeny partnera w kodzie być nie może), niżej
    zdanie o ryzyku. Bez słowa o Telegramie: mail z linkiem do portalu nie
    ma prawa go obiecywać nawet w stopce."""
    adr = parseaddr(settings.lead_mail_from or "")[1]
    domena = adr.rpartition("@")[2] if "@" in adr else ""
    # Zwykły tekst, nie linki: mail ma JEDNO wyjście (przycisk), a drugi
    # klikalny element zawsze zabiera kliknięcia pierwszemu.
    czesci = [MARKA] + [escape(x) for x in (domena, adr) if x]
    return (f'<table role="presentation" width="580" cellpadding="0" cellspacing="0" '
            f'style="width:580px;max-width:100%;font-family:{_FONT}">'
            f'<tr><td align="center" style="padding:22px 24px 8px;color:{_FAINT};font-size:12px;'
            f'line-height:1.6">{" &nbsp;·&nbsp; ".join(czesci)}</td></tr>'
            f'<tr><td align="center" style="padding:0 24px 12px;color:{_FAINT};font-size:11px;'
            f'line-height:1.6">Trading carries risk. A prop firm evaluation can fail and no '
            f'outcome is guaranteed. Nothing here is investment advice.</td></tr></table>')


def _html_z_tekstu(tekst: str, temat: str | None = None) -> str:
    """Ten sam tekst, ubrany w papier firmowy marki z landingu.

    Składany Z TEKSTU, a nie obok niego, i to jest cała obrona przed jedyną
    awarią tego modułu, której nikt nie zauważa: podglądem w panelu, który
    pokazuje co innego, niż lead ma w skrzynce. Wygląd 1:1 z maili landingu
    (biała kartka na szarym, logo, duży cichy nagłówek, szary tekst, jedna
    zielona pigułka), bo tamte wyglądały dobrze, a te z panelu jak wydruk.

    Co z tekstu robi się czym:
    * temat → duży nagłówek (poza odpowiedziami „Re:", które mają czytać się
      jak rozmowa);
    * pierwszy akapit (powitanie) ciemny, reszta szara, pojedyncze entery
      zostają łamaniem linii;
    * akapit będący samym adresem URL → jedyny przycisk, a krótkie zdanie
      zaraz pod nim → wyśrodkowany dopisek, jak „odezwiemy się w ciągu dnia";
    * wiersze „1. Tytuł: opis" → kroki z numerem i cienką linią;
    * akapit wersalikami („WHAT HAPPENS NEXT") → szara etykieta sekcji;
    * blok po `--` → drobna stopka w kartce; sama nazwa marki z niej wypada,
      bo stoi pod kartką razem z domeną i adresem.
    """
    akapity = [a.strip("\n") for a in tekst.split("\n\n") if a.strip()]
    stopka: list[str] = []
    if akapity and akapity[-1].strip().startswith("--"):
        stopka = [w.strip() for w in akapity.pop().split("\n")[1:]
                  if w.strip() and w.strip() != MARKA]

    blok: list[str] = []
    # Co stało tuż wyżej: po przycisku i po dopisku pod nim następny akapit
    # potrzebuje oddechu, bo oba nie mają dolnego marginesu.
    poprzedni = ""
    for nr, akapit in enumerate(akapity):
        a = akapit.strip()
        # Sam URL, nie „URL i jeszcze coś" — inaczej akapit zaczynający się od
        # adresu wylądowałby w `href` razem ze zdaniem, które po nim stoi.
        if a.startswith("http") and not a.split()[1:]:
            blok.append(_przycisk(_napis_przycisku(a), a))
            poprzedni = "przycisk"
            continue
        wiersze = [w for w in a.split("\n") if w.strip()]
        kroki = [_KROK.match(w) for w in wiersze]
        if len(wiersze) >= 2 and all(kroki):
            blok.append(_kroki([(m.group(1), m.group(2)) for m in kroki]))
        elif len(wiersze) == 1 and _ETYKIETA.match(a) and a.upper() == a and any(c.isalpha() for c in a):
            blok.append(_linia(26) + f'<div style="color:{_FAINT};font-size:12px;font-weight:600;'
                        f'letter-spacing:.08em;text-transform:uppercase;margin:0 0 4px">'
                        f'{escape(a)}</div>')
        elif poprzedni == "przycisk" and len(a) <= 160:
            blok.append(_akapit(a, kolor=_SUBTLE, rozmiar=14, srodek=True, margines="16px 0 0"))
            poprzedni = "dopisek"
            continue
        else:
            blok.append(_akapit(a, kolor=_INK if nr == 0 else _SUBTLE,
                                margines="28px 0 16px" if poprzedni else "0 0 16px"))
        poprzedni = ""

    if stopka:
        wiersze = "".join(f'<div style="margin:0 0 4px">{escape(w)}</div>' for w in stopka)
        blok.append(f'<div style="margin:30px 0 0;padding:20px 0 0;border-top:1px solid {_HAIRLINE};'
                    f'font-size:13px;line-height:1.55;color:{_FAINT}">{wiersze}</div>')

    tytul = " ".join((temat or "").split())
    naglowek_tekstu = ""
    if tytul and not tytul.lower().startswith(("re:", "fwd:", "fw:")):
        naglowek_tekstu = (f'<tr><td align="center" style="padding:30px 40px 0">'
                           f'<h1 style="margin:0;color:{_INK};font-size:28px;line-height:1.18;'
                           f'font-weight:600;letter-spacing:-.02em">{escape(tytul)}</h1></td></tr>')

    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light"><title>{escape(tytul or MARKA)}</title></head>'
        f'<body style="margin:0;padding:0;background:{_PAGE}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_PAGE};padding:40px 16px"><tr><td align="center">'
        f'<table role="presentation" width="580" cellpadding="0" cellspacing="0" '
        f'style="width:580px;max-width:100%;background:#ffffff;border-radius:18px;'
        f'overflow:hidden;font-family:{_FONT}">'
        f'<tr><td align="center" style="padding:34px 40px 0">{_naglowek()}</td></tr>'
        f'{naglowek_tekstu}'
        f'<tr><td style="padding:30px 40px 38px;font-family:{_FONT}">{"".join(blok)}</td></tr>'
        f'</table>{_stopka_zewnetrzna()}</td></tr></table></body></html>')


def _smtp_transport(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                      timeout=TIMEOUT_SEK) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_pass)
        s.send_message(msg)


RESEND_URL = "https://api.resend.com/emails"
RESEND_UA = "propfunding-mailer/1.0 (+stdlib urllib)"


def _resend_transport(msg: EmailMessage) -> None:
    """Ten sam `EmailMessage`, tylko przez HTTPS Resenda zamiast SMTP.

    Domena marki landingu jest zweryfikowana u Resenda, nie u dostawcy SMTP
    platformy — mail spod niej przez cudzy SMTP wychodzi z etykietą „via" albo
    wcale. `urllib` z biblioteki standardowej, jak w `sms.py`: jedno wywołanie
    nie jest powodem na zależność. Odmowa Resenda (4xx/5xx) idzie wyjątkiem
    z kodem i ciałem, a `wyslij` zamienia go na `(False, powód)`.
    """
    tekst = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    dane = {"from": msg["From"], "to": [msg["To"]], "subject": msg["Subject"],
            "text": tekst.get_content() if tekst else "",
            "html": html.get_content() if html else None}
    if msg["Reply-To"]:
        dane["reply_to"] = msg["Reply-To"]
    # `User-Agent` NIE jest kosmetyką: api.resend.com stoi za Cloudflarem, a ten
    # odrzuca domyślne `Python-urllib/3.x` jako sygnaturę bota — 403 z ciałem
    # „error code: 1010", zanim żądanie w ogóle dotrze do Resenda. Zmierzone
    # 2026-09-23: ten sam POST bez nagłówka = 1010, z nagłówkiem = odpowiedź
    # Resenda. Pierwszy mail z produkcji padł dokładnie na tym.
    req = urllib.request.Request(
        RESEND_URL, data=json.dumps(dane).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.resend_api_key}",
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": RESEND_UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as odp:
            odp.read()
    except urllib.error.HTTPError as e:
        cialo = e.read().decode("utf-8", "replace")[:300]
        # Resend opisuje odmowę w JSON-ie (`message`: „domain is not verified",
        # „API key is invalid") — to zdanie ma zobaczyć człowiek w panelu, nie
        # surowy słownik.
        try:
            powod = json.loads(cialo).get("message") or cialo
        except ValueError:
            powod = cialo
        raise RuntimeError(f"resend {e.code}: {powod}") from None


def _transport():
    """Resend, gdy jest klucz; inaczej SMTP. Wybierane przy KAŻDEJ wysyłce, a
    nie przy imporcie, żeby testy mogły podmienić jedno i drugie."""
    return _resend_transport if settings.resend_api_key else _smtp_transport


def wyslij(email: str | None, temat: str, tekst: str, *,
           transport=None, tylko_nadawca: bool = False) -> tuple[bool, str]:
    """`(czy poszło, powód odmowy)`. Nigdy nie rzuca.

    `tylko_nadawca=True` to tryb maila pisanego z ręki w panelu: potrzebuje
    nadawcy i drogi, ale nie URL-i Telegrama, bo treść nie prowadzi nigdzie
    z automatu — pisze ją człowiek. Domyślnie (automat `tresc()`) obowiązuje
    pełny komplet z `is_enabled()`.
    """
    if not (nadawca_gotowy() if tylko_nadawca else is_enabled()):
        return False, "lead e-mail is not configured"
    cel = adres(email)
    if not cel:
        return False, "no usable e-mail address"
    temat, tekst = (temat or "").strip(), (tekst or "").strip()
    if not temat or not tekst:
        return False, "empty message"

    msg = EmailMessage()
    msg["From"] = _nadawca()
    msg["To"] = cel
    msg["Subject"] = temat
    # Odpowiedzi mają wracać tam, skąd mail wyszedł. Domyślny Reply-To wskazałby
    # skrzynkę firmy, o której lead nie słyszał — i pierwsza odpowiedź w tej
    # relacji zdradziłaby to, czego reszta systemu pilnuje.
    msg["Reply-To"] = _nadawca()
    msg.set_content(tekst)
    # HTML jako ALTERNATYWA, nigdy zamiast. Klient z wyłączoną grafiką, czytnik
    # ekranowy i filtr antyspamowy, który punktuje mail bez wersji tekstowej,
    # dostają pełną wiadomość, a nie zachętę do włączenia obrazków.
    msg.add_alternative(_html_z_tekstu(tekst, temat), subtype="html")
    try:
        (transport or _transport())(msg)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[lead_mail] nie poszło do {cel}: {e}")
        return False, f"mail error: {e}"
    return True, ""
