"""Publikacja na kanale Telegrama (Bot API).

Kanał z wypłatami dostaje GRAFIKĘ certyfikatu i podpis pod nią. Grafikę robi
`certshot.py`, treść składa `payoutbot.py` — tutaj jest wyłącznie transport.

Trzy rzeczy, które trzymają to w ryzach:

1. **Nigdy nie wywraca wywołania.** Tak jak `notify.py` i `push.py`: błąd sieci,
   zły token czy odrzucenie przez Telegrama kończą się `print` i `False`, nie
   wyjątkiem. Post na kanał jest dodatkiem do wypłaty, a nie warunkiem jej
   powstania — nieudana wysyłka nie może cofnąć zapisanego rekordu.

2. **Zero nowych zależności.** `sendPhoto` wymaga multipart/form-data, którego
   `urllib` nie składa sam, więc enkoder siedzi niżej w tym pliku. To ~20 linii,
   a alternatywą było dociągnięcie `httpx` do bundla dla jednego POST-a dziennie.

3. **Transport wstrzykiwany.** Testy podstawiają własny i nie ruszają sieci —
   ten sam wzorzec co w `metaapi_provisioning.py`.

Bot musi być ADMINISTRATOREM kanału z prawem publikowania. Post wychodzi
z nazwą kanału, nie bota, więc z zewnątrz wygląda jak wpis właściciela.
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request

from .config import get_settings

settings = get_settings()

API = "https://api.telegram.org"
TIMEOUT_SEK = 20

# Kontrakt transportu (ten sam kształt co w metaapi_provisioning):
#     transport(url: str, body: bytes, content_type: str) -> tuple[int, bytes]


def is_enabled() -> bool:
    return settings.telegram_enabled


def _multipart(pola: dict[str, str],
               plik: tuple[str, str, bytes] | None = None) -> tuple[bytes, str]:
    """Składa ciało multipart/form-data. Zwraca `(bajty, content-type)`.

    `plik` to `(nazwa_pola, nazwa_pliku, dane)`. Granica jest losowa i nie ma
    prawa wystąpić w danych — 32 znaki hex wystarczą z ogromnym zapasem.
    """
    granica = "----propfunding" + secrets.token_hex(16)
    czesci: list[bytes] = []
    for klucz, wartosc in pola.items():
        czesci.append(
            f'--{granica}\r\nContent-Disposition: form-data; name="{klucz}"\r\n\r\n'
            f'{wartosc}\r\n'.encode())
    if plik is not None:
        pole, nazwa, dane = plik
        czesci.append(
            f'--{granica}\r\nContent-Disposition: form-data; name="{pole}";'
            f' filename="{nazwa}"\r\nContent-Type: image/png\r\n\r\n'.encode())
        czesci.append(dane)
        czesci.append(b"\r\n")
    czesci.append(f"--{granica}--\r\n".encode())
    return b"".join(czesci), f"multipart/form-data; boundary={granica}"


def _urllib_transport(url: str, body: bytes, content_type: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as odp:
            return odp.status, odp.read()
    except urllib.error.HTTPError as e:
        # Telegram opisuje powód w ciele odpowiedzi ("chat not found", "bot is not
        # a member of the channel chat") — bez tego diagnoza byłaby zgadywanką.
        return e.code, e.read()


def _strzal_json(metoda: str, pola: dict[str, str],
                 plik: tuple[str, str, bytes] | None, transport) -> tuple[bool, str, dict]:
    """`(czy poszło, powód odmowy, `result` z odpowiedzi)`.

    Powód wraca WYŻEJ, a nie tylko do logu: bez niego panel mówi „Telegram
    odrzucił zdjęcie", a admin musi grzebać w logach hostingu, żeby dowiedzieć
    się, że bot po prostu nie jest administratorem kanału.

    `result` potrzebuje z tego jeden wywołujący — alert o leadzie musi zapamiętać
    `message_id`, bo notatki wpisuje się ODPOWIEDZIĄ na tę wiadomość, a Telegram
    nie przekazuje w niej niczego innego, po czym dałoby się trafić do leada.
    """
    # Tu sprawdzamy WYŁĄCZNIE token, bo to on jest w URL-u. Czy cel wysyłki
    # istnieje, wie tylko wywołujący: kanał z wypłatami i czat z leadami są
    # niezależne i jeden ma prawo działać, gdy drugi jest nieskonfigurowany.
    if not settings.telegram_bot_token:
        return False, "no bot token or channel", {}
    body, content_type = _multipart(pola, plik)
    url = f"{API}/bot{settings.telegram_bot_token}/{metoda}"
    try:
        status, tresc = (transport or _urllib_transport)(url, body, content_type)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[telegram] {metoda} błąd sieci: {e}")
        return False, f"network error: {e}", {}
    try:
        odp = json.loads(tresc or b"{}") or {}
    except Exception:
        odp = {}
    if status == 200:
        wynik = odp.get("result")
        return True, "", wynik if isinstance(wynik, dict) else {}
    # Token NIGDY nie może trafić do logu ani do panelu — jest w URL-u, więc
    # przekazujemy dalej sam opis z odpowiedzi, nigdy adresu żądania.
    opis = odp.get("description") or (tresc or b"")[:200].decode("utf-8", "replace")
    opis = opis or f"HTTP {status}"
    print(f"[telegram] {metoda} odrzucone ({status}): {opis}")
    return False, opis, {}


def _strzal(metoda: str, pola: dict[str, str],
            plik: tuple[str, str, bytes] | None, transport) -> tuple[bool, str]:
    """`_strzal_json` dla wywołujących, których `message_id` nie interesuje."""
    poszlo, powod, _ = _strzal_json(metoda, pola, plik, transport)
    return poszlo, powod


def send_photo(png: bytes, caption: str, *, transport=None) -> tuple[bool, str]:
    """Grafika + podpis pod nią. `caption` w HTML-u (limit Telegrama: 1024 znaki)."""
    if not is_enabled():
        return False, "no bot token or channel"
    return _strzal("sendPhoto",
                   {"chat_id": settings.telegram_chat_id, "caption": caption[:1024],
                    "parse_mode": "HTML"},
                   ("photo", "certificate.png", png), transport)


def send_message(text: str, *, transport=None) -> tuple[bool, str]:
    """Sam tekst — awaryjnie, gdy nie udało się zrobić grafiki.

    Lepiej opublikować wpis bez obrazka niż nie opublikować nic: wypłata już
    istnieje i ma publiczny certyfikat, więc cisza na kanale byłaby myląca.
    """
    if not is_enabled():
        return False, "no bot token or channel"
    return _strzal("sendMessage",
                   {"chat_id": settings.telegram_chat_id, "text": text[:4096],
                    "parse_mode": "HTML", "disable_web_page_preview": "false"},
                   None, transport)


# --------------------------------------------------------------------------- #
#  Leady — prywatny czat, wiadomość z przyciskami                             #
# --------------------------------------------------------------------------- #
# Ten sam bot, ale INNY czat niż kanał z wypłatami: tamten jest publiczny,
# a tu leci imię, mail i telefon człowieka. Pomyłka w tym miejscu to wyciek
# danych na oczach klientów, więc czat jest osobną zmienną, nie parametrem
# z wartością domyślną.

# Przyciski pod alertem. Opisy mówią, co się przed chwilą zrobiło, a nie jak
# nazywa się kolumna — klikający ma przed sobą rozmowę, nie schemat tabeli.
#
# Kontakt idzie na Telegram, więc „napisałem" i „odpisał" to dwa różne stany:
# pierwszy jest po naszej stronie i wygasa dopiero po czasie, drugi jest
# odpowiedzią człowieka. Telefon rozstrzygał to jednym kliknięciem, wiadomość nie.
LEAD_BUTTONS = (("✍️ Napisałem", "messaged"),
                ("💬 Odpisał", "replied"),
                ("🔇 Nie odpisuje", "no_reply"),
                ("❌ Odpada", "rejected"))

# Poprawka oceny z ankiety. Formularz punktuje deklaracje, a te po telefonie
# potrafią wyglądać zupełnie inaczej — „high" z ankiety bywa człowiekiem bez
# pieniędzy, a „cold" traderem, który po prostu zaznaczył ostrożnie. To nie jest
# nowa skala, tylko możliwość poprawienia tej samej.
TIER_BUTTONS = (("🔥 High", "tier_high"),
                ("🟡 Warm", "tier_warm"),
                ("⚪️ Cold", "tier_cold"))

CLAIM_BUTTON = ("🙋 Biorę tego", "claim")
# Ten sam `claim`, inny opis: pod kartą z właścicielem to nie jest wzięcie
# niczyjego leada, tylko odebranie go koledze, i przycisk ma to mówić wprost.
TAKEOVER_BUTTON = ("🤝 Przejmuję", "claim")
RELEASE_BUTTON = ("↩️ Oddaję", "release")


def leads_enabled() -> bool:
    return settings.telegram_leads_enabled


def lead_keyboard(lead_id: int, *, owner: str | None = None,
                  status: str = "new", tier: str | None = None) -> dict:
    """Klawiatura pod alertem — DWA etapy i to jest cały sens tej konstrukcji.

    Dopóki leada nikt nie wziął, jest jeden przycisk: „biorę". Statusy i ocena
    pojawiają się dopiero potem. Kanał czyta kilka osób i cztery przyciski
    statusu pod świeżym zgłoszeniem kończyły się dwiema wiadomościami do tej
    samej osoby w ciągu godziny.

    Wybrany stan zostaje oznaczony kropką i NIE znika po kliknięciu: wiadomość
    przewija się w kanale razem z resztą i po godzinie nie da się inaczej
    powiedzieć, czy ktoś już coś kliknął, czy tylko przeczytał. Przy okazji
    pomyłkę da się poprawić, zamiast szukać leada w panelu.

    Statusy idą po dwa w rzędzie, bo cztery obok siebie Telegram na telefonie
    ściska do samych emoji.
    """
    def guzik(opis: str, akcja: str, wybrany: bool = False) -> dict:
        return {"text": ("• " + opis) if wybrany else opis,
                "callback_data": f"lead:{lead_id}:{akcja}"}

    if not owner:
        return {"inline_keyboard": [[guzik(*CLAIM_BUTTON)]]}
    statusy = [guzik(o, s, s == status) for o, s in LEAD_BUTTONS]
    return {"inline_keyboard": [
        *[statusy[i:i + 2] for i in range(0, len(statusy), 2)],
        [guzik(o, a, a == f"tier_{tier or ''}") for o, a in TIER_BUTTONS],
        # Przejęcie stoi pod kartą, która ma już właściciela, i to jest celowe:
        # lead nie czeka, aż ktoś zdąży kliknąć „oddaję".
        [guzik(*TAKEOVER_BUTTON), guzik(*RELEASE_BUTTON)],
    ]}


def send_lead_alert(lead_id: int, text: str, *,
                    keyboard: dict | None = None,
                    transport=None) -> tuple[bool, str, int | None]:
    """Alert o nowym leadzie. Zwraca też `message_id` wysłanej wiadomości.

    `message_id` musi wrócić do bazy: notatki z rozmowy wpisuje się ODPOWIEDZIĄ
    na ten post, a webhook nie ma innego sposobu, żeby dopasować odpowiedź do
    leada. Bez zapisanego id notatka po prostu przepada.

    `callback_data` musi zmieścić się w 64 bajtach, stąd samo `lead:<id>:<akcja>`
    zamiast czegokolwiek opisowego — resztę webhook dobiera z bazy po id.
    """
    if not leads_enabled():
        return False, "no bot token or leads chat", None
    poszlo, powod, wynik = _strzal_json(
        "sendMessage",
        {"chat_id": settings.telegram_leads_chat_id, "text": text[:4096],
         "parse_mode": "HTML", "disable_web_page_preview": "true",
         "reply_markup": json.dumps(keyboard or lead_keyboard(lead_id))},
        None, transport)
    mid = wynik.get("message_id")
    return poszlo, powod, mid if isinstance(mid, int) else None


def send_lead_message(text: str, *, transport=None) -> tuple[bool, str]:
    """Wiadomość na czat z leadami BEZ przycisków — przypomnienia z crona.

    Osobna funkcja od `send_message`, bo tamta celuje w publiczny kanał z
    wypłatami. Przypomnienie niesie imię i mail człowieka, więc pomyłka w czacie
    jest wyciekiem, a nie literówką; jedno wywołanie mniej do pomylenia.
    """
    if not leads_enabled():
        return False, "no bot token or leads chat"
    return _strzal("sendMessage",
                   {"chat_id": settings.telegram_leads_chat_id, "text": text[:4096],
                    "parse_mode": "HTML", "disable_web_page_preview": "true"},
                   None, transport)


def answer_callback(callback_id: str, text: str, *, transport=None) -> tuple[bool, str]:
    """Zdejmuje „zegarek" z przycisku. Bez tej odpowiedzi Telegram kręci kółkiem
    przez minutę i klikający nie wie, czy cokolwiek się stało."""
    return _strzal("answerCallbackQuery",
                   {"callback_query_id": callback_id, "text": text[:200]},
                   None, transport)


def edit_lead_message(chat_id: str, message_id: int, text: str,
                      *, keyboard: dict | None = None,
                      transport=None) -> tuple[bool, str]:
    """Przepisuje alert po każdej zmianie: kto go wziął, jaki status, jaka notatka.

    Wiadomość jest kartą leada, nie powiadomieniem — dział pracuje na kanale,
    nie w panelu, więc stan musi być tam, gdzie się klika. Bez `keyboard`
    przyciski znikają; to zostawione dla wiadomości, które mają się domknąć.
    """
    pola = {"chat_id": chat_id, "message_id": str(message_id),
            "text": text[:4096], "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    if keyboard is not None:
        pola["reply_markup"] = json.dumps(keyboard)
    return _strzal("editMessageText", pola, None, transport)
