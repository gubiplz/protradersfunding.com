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


def _strzal(metoda: str, pola: dict[str, str],
            plik: tuple[str, str, bytes] | None, transport) -> tuple[bool, str]:
    """`(czy poszło, powód odmowy)`.

    Powód wraca WYŻEJ, a nie tylko do logu: bez niego panel mówi „Telegram
    odrzucił zdjęcie", a admin musi grzebać w logach hostingu, żeby dowiedzieć
    się, że bot po prostu nie jest administratorem kanału.
    """
    # Tu sprawdzamy WYŁĄCZNIE token, bo to on jest w URL-u. Czy cel wysyłki
    # istnieje, wie tylko wywołujący: kanał z wypłatami i czat z leadami są
    # niezależne i jeden ma prawo działać, gdy drugi jest nieskonfigurowany.
    if not settings.telegram_bot_token:
        return False, "no bot token or channel"
    body, content_type = _multipart(pola, plik)
    url = f"{API}/bot{settings.telegram_bot_token}/{metoda}"
    try:
        status, tresc = (transport or _urllib_transport)(url, body, content_type)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[telegram] {metoda} błąd sieci: {e}")
        return False, f"network error: {e}"
    if status == 200:
        return True, ""
    # Token NIGDY nie może trafić do logu ani do panelu — jest w URL-u, więc
    # przekazujemy dalej sam opis z odpowiedzi, nigdy adresu żądania.
    try:
        opis = (json.loads(tresc or b"{}") or {}).get("description", "")
    except Exception:
        opis = (tresc or b"")[:200].decode("utf-8", "replace")
    opis = opis or f"HTTP {status}"
    print(f"[telegram] {metoda} odrzucone ({status}): {opis}")
    return False, opis


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

# Przyciski pod alertem. Opisy odpowiadają temu, co dzieje się po telefonie,
# a nie nazwom w bazie — klikający ma na ekranie telefon, nie schemat tabeli.
LEAD_BUTTONS = (("✅ Odebrał", "called"),
                ("📵 Nie odbiera", "no_answer"),
                ("❌ Odpada", "rejected"))


def leads_enabled() -> bool:
    return settings.telegram_leads_enabled


def send_lead_alert(lead_id: int, text: str, *, transport=None) -> tuple[bool, str]:
    """Alert o nowym leadzie z trzema przyciskami statusu.

    `callback_data` musi zmieścić się w 64 bajtach, stąd samo `lead:<id>:<status>`
    zamiast czegokolwiek opisowego — resztę webhook dobiera z bazy po id.
    """
    if not leads_enabled():
        return False, "no bot token or leads chat"
    klawiatura = {"inline_keyboard": [[{"text": opis, "callback_data": f"lead:{lead_id}:{stan}"}]
                                      for opis, stan in LEAD_BUTTONS]}
    return _strzal("sendMessage",
                   {"chat_id": settings.telegram_leads_chat_id, "text": text[:4096],
                    "parse_mode": "HTML", "disable_web_page_preview": "true",
                    "reply_markup": json.dumps(klawiatura)},
                   None, transport)


def answer_callback(callback_id: str, text: str, *, transport=None) -> tuple[bool, str]:
    """Zdejmuje „zegarek" z przycisku. Bez tej odpowiedzi Telegram kręci kółkiem
    przez minutę i klikający nie wie, czy cokolwiek się stało."""
    return _strzal("answerCallbackQuery",
                   {"callback_query_id": callback_id, "text": text[:200]},
                   None, transport)


def edit_lead_message(chat_id: str, message_id: int, text: str,
                      *, transport=None) -> tuple[bool, str]:
    """Przepisuje alert po kliknięciu i USUWA przyciski (brak `reply_markup`).

    Wiadomość zostaje w historii jako zapis tego, co wybrano — inaczej czat
    z leadami wygląda po tygodniu tak samo jak przed pierwszym telefonem.
    """
    return _strzal("editMessageText",
                   {"chat_id": chat_id, "message_id": str(message_id),
                    "text": text[:4096], "parse_mode": "HTML",
                    "disable_web_page_preview": "true"},
                   None, transport)
