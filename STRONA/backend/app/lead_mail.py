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

Czego ten moduł CELOWO nie robi:

**Nie wysyła HTML-a.** Nie z lenistwa: to jest mail od człowieka do człowieka,
pierwszy w tej relacji, i ma tak wyglądać w skrzynce. Wyśrodkowana biała karta
z logo i przyciskiem mówi „wysyłka masowa" zanim ktokolwiek przeczyta pierwsze
zdanie — psuje i dostarczalność, i jedyną rzecz, którą ten mail ma sprzedać:
że aplikację czytał ktoś żywy. Maile transakcyjne w `notify.py` mają HTML,
bo tam jest odwrotnie — mają wyglądać na wyciąg z systemu.

**Nie wysyła spod `MAIL_FROM`.** Człowiek zgłosił się przez landing marki
partnerskiej i o tej firmie nie słyszał. Mail z jej domeny jest dla niego
mailem od obcego, a przy okazji rozbiera rozdział marek, który reszta systemu
utrzymuje. Bez `LEAD_MAIL_FROM` ten kanał jest po prostu wyłączony.
"""
from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage

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
    return bool(settings.smtp_host and settings.lead_mail_from
                and settings.sms_telegram_url)


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
            f"Your application is through to the next step.\n\n"
            f"That part is not automatic — a person reads every one of these, and "
            f"yours came back a yes.\n\n"
            f"What happens now is a short conversation, not another form. We do it "
            f"on Telegram because that is where our desk works and it is the "
            f"difference between starting this week and waiting on email:\n\n"
            f"{link}\n\n"
            f"Send anything there, \"hi\" is enough, and we pick it up from your "
            f"application.\n\n"
            f"One thing worth knowing: we work through applications in batches, and "
            f"the ones that go quiet get closed to make room. Yours is open now."
        ) + stopka)
    return (f"About your application, {pierwsze}", (
        f"Hi {pierwsze},\n\n"
        f"Straight answer: as it stands, this one is not a yes.\n\n"
        f"That is about the application, not about you, and it is not permanent. "
        f"What we look at changes as your record does, and the door is not closed.\n\n"
        f"Before you close this though: one short conversation is the only way to "
        f"find out what specifically was missing. We will tell you plainly, and it "
        f"costs you five minutes:\n\n"
        f"{link}\n\n"
        f"If you would rather leave it, that is a fair call too."
    ) + stopka)


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
    msg["From"] = settings.lead_mail_from
    msg["To"] = cel
    msg["Subject"] = temat
    # Odpowiedzi mają wracać tam, skąd mail wyszedł. Domyślny Reply-To wskazałby
    # skrzynkę firmy, o której lead nie słyszał — i pierwsza odpowiedź w tej
    # relacji zdradziłaby to, czego reszta systemu pilnuje.
    msg["Reply-To"] = settings.lead_mail_from
    msg.set_content(tekst)
    try:
        (transport or _smtp_transport)(msg)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[lead_mail] nie poszło do {cel}: {e}")
        return False, f"mail error: {e}"
    return True, ""
