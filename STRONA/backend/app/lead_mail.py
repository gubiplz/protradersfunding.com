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

import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape

from .config import get_settings

settings = get_settings()

TIMEOUT_SEK = 20
# Nadawca podpisuje się nazwą marki z landingu, nie nazwą firmy, która to czyta.
MARKA = "Forex Passing"

# Kolory z landingu tej marki, nie z palety tej firmy — mail ma wyglądać na
# ciąg dalszy strony, którą lead widział. Czerń logo, zieleń świecy, szarość
# tekstu drugorzędnego.
_ATRAMENT = "#0d1a13"
_ZIELEN = "#15803d"
_SZARY = "#6b7671"
_LINIA = "#e6efea"

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
    """
    return [nazwa for nazwa, wartosc in (
        ("SMTP_HOST", settings.smtp_host),
        ("LEAD_MAIL_FROM", settings.lead_mail_from),
        ("SMS_TELEGRAM_URL", settings.sms_telegram_url)) if not wartosc]


def adres(surowy: str | None) -> str | None:
    """Adres gotowy do wysyłki albo `None`."""
    tekst = (surowy or "").strip()
    return tekst if _ADRES.match(tekst) else None


def tresc(imie: str | None, *, zakwalifikowany: bool) -> tuple[str, str]:
    """`(temat, treść)`. Jedyne miejsce z tym tekstem — woła je i przycisk
    w panelu, i podgląd, który panel pokazuje PRZED wysyłką.

    Obie wersje kończą się tym samym: linkiem do Telegrama. Odmowa też, i to
    świadomie — „nie tym razem" bez drogi odpowiedzi zamyka temat na zawsze,
    a część odrzuconych wraca za kilka miesięcy z innym dorobkiem.

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
    link = settings.sms_telegram_url
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
            f"{link}\n\n"
            f"The opening line does not matter. \"Hi\" is enough — we pick it up "
            f"from your application.\n\n"
            f"One thing worth knowing: we work through applications in batches, and "
            f"the ones that go quiet get closed to make room. Yours is open now."
        ) + stopka)
    return (f"About your application, {pierwsze}", (
        f"Hi {pierwsze},\n\n"
        f"Straight answer: as it stands, this one is not a yes.\n\n"
        f"That is about the application, not about you, and it is not permanent. "
        f"What we look at changes as your record does, and the door stays open.\n\n"
        f"Before you close this, though: one short conversation is the only way to "
        f"learn what specifically was missing. We will tell you plainly, and it "
        f"costs you five minutes:\n\n"
        f"{link}\n\n"
        f"If you would rather leave it, that is a fair call too — no hard feelings, "
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
    """Papier firmowy: logo w lewym górnym rogu albo sama nazwa, gdy go nie ma.

    Brak `LEAD_MAIL_LOGO_URL` nie może wstrzymać wysyłki — mail bez obrazka
    dalej robi swoją robotę, a kanał, który stoi przez kosmetykę, nie dowozi
    nic. Wymiary są wpisane na sztywno, bo klient pocztowy bez nich przez
    moment rysuje obrazek w pełnej rozdzielczości i układ skacze.
    """
    if settings.lead_mail_logo_url:
        return (f'<img src="{escape(settings.lead_mail_logo_url, quote=True)}" '
                f'width="150" height="99" alt="{MARKA}" '
                f'style="display:block;border:0;margin:0 0 26px">')
    return (f'<div style="font-size:19px;font-weight:700;color:{_ATRAMENT};'
            f'margin:0 0 26px">{MARKA}</div>')


def _html_z_tekstu(tekst: str) -> str:
    """Ten sam tekst, ubrany w papier firmowy marki z landingu.

    Składany Z TEKSTU, a nie obok niego, i to jest cała obrona przed jedyną
    awarią tego modułu, której nikt nie zauważa: podglądem w panelu, który
    pokazuje co innego, niż lead ma w skrzynce.

    Akapit będący samym adresem URL zamienia się w jedyny przycisk. Reszta to
    zwykłe akapity, a blok po `--` ląduje w wyszarzonej stopce.
    """
    akapity = [a.strip() for a in tekst.split("\n\n") if a.strip()]
    stopka: list[str] = []
    if akapity and akapity[-1].startswith("--"):
        stopka = [w for w in akapity.pop().split("\n")[1:] if w.strip()]

    blok = []
    for akapit in akapity:
        # Sam URL, nie „URL i jeszcze coś" — inaczej akapit zaczynający się od
        # adresu wylądowałby w `href` razem ze zdaniem, które po nim stoi.
        if akapit.startswith("http") and not akapit.split()[1:]:
            blok.append(
                f'<table role="presentation" cellpadding="0" cellspacing="0" '
                f'style="margin:26px 0"><tr><td bgcolor="{_ZIELEN}" '
                f'style="border-radius:10px">'
                f'<a href="{escape(akapit, quote=True)}" '
                f'style="display:inline-block;padding:14px 28px;font-size:16px;'
                f'font-weight:600;color:#ffffff;text-decoration:none">'
                f'Message the desk on Telegram</a></td></tr></table>')
        else:
            blok.append(
                f'<p style="margin:0 0 18px;font-size:16px;line-height:1.65;'
                f'color:{_ATRAMENT}">{escape(akapit)}</p>')

    if stopka:
        wiersze = "".join(f'<div style="margin:0 0 4px">{escape(w)}</div>'
                          for w in stopka)
        blok.append(f'<div style="margin:32px 0 0;padding:18px 0 0;'
                    f'border-top:1px solid {_LINIA};font-size:13px;'
                    f'line-height:1.55;color:{_SZARY}">{wiersze}</div>')

    return (
        f'<!doctype html><html><body style="margin:0;padding:0;background:#f4f6f5">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#f4f6f5;padding:40px 14px"><tr><td align="center">'
        f'<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;width:100%;background:#ffffff;'
        f'border:1px solid {_LINIA};border-radius:14px"><tr>'
        f'<td style="padding:36px 38px 30px;font-family:-apple-system,'
        f'BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif">'
        f'{_naglowek()}{"".join(blok)}'
        f'</td></tr></table></td></tr></table></body></html>')


def _smtp_transport(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                      timeout=TIMEOUT_SEK) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_pass)
        s.send_message(msg)


def wyslij(email: str | None, temat: str, tekst: str, *,
           transport=None) -> tuple[bool, str]:
    """`(czy poszło, powód odmowy)`. Nigdy nie rzuca."""
    if not is_enabled():
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
    msg.add_alternative(_html_z_tekstu(tekst), subtype="html")
    try:
        (transport or _smtp_transport)(msg)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[lead_mail] nie poszło do {cel}: {e}")
        return False, f"mail error: {e}"
    return True, ""
