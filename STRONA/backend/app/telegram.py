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
import urllib.parse
import urllib.request
from typing import NamedTuple

from .config import get_settings

settings = get_settings()

API = "https://api.telegram.org"
TIMEOUT_SEK = 20

# Kontrakt transportu (ten sam kształt co w metaapi_provisioning):
#     transport(url: str, body: bytes, content_type: str) -> tuple[int, bytes]


# --------------------------------------------------------------------------- #
#  Trzy boty, nie jeden                                                       #
# --------------------------------------------------------------------------- #
# Do 2026-09 wszystkim zajmował się jeden bot. Zamrożenie konta, do którego był
# przypisany, położyło w jednej chwili kanał z wypłatami, account management,
# track record i OBA deski leadów. Stąd podział: treść, leady i leady
# nigeryjskie mają osobne tokeny, więc następna taka awaria zabiera jedną
# trzecią, a nie całość.
#
# `chat_id` i `secret` siedzą w tej samej strukturze celowo. Update z Telegrama
# NIE niesie żadnej informacji o tym, który bot go dostał — rozróżnia je wyłącznie
# adres webhooka — więc ten, kto zna desk, musi od razu znać komplet: czym wysłać,
# dokąd i jakim sekretem zweryfikować przychodzące.

class Bot(NamedTuple):
    key: str      # "content" | "leads" | "leads_ng"
    token: str
    chat_id: str
    secret: str


def desk(key: str) -> Bot:
    """Komplet danych jednego bota. Nieznany klucz => bot pusty, czyli wyłączony."""
    if key == "content":
        return Bot("content", settings.telegram_bot_token,
                   settings.telegram_chat_id, "")
    if key == "leads":
        return Bot("leads", settings.telegram_leads_token,
                   settings.telegram_leads_chat_id, settings.telegram_webhook_secret)
    if key == "leads_ng":
        return Bot("leads_ng", settings.telegram_leads_ng_bot_token,
                   settings.telegram_leads_ng_chat_id,
                   settings.telegram_leads_ng_webhook_secret)
    return Bot(key, "", "", "")


DESKI_LEADOW = ("leads", "leads_ng")


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
                 plik: tuple[str, str, bytes] | None, transport,
                 token: str | None = None) -> tuple[bool, str, dict]:
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
    # Brak `token` = bot treści; to domyślne z czasów, gdy bot był jeden.
    token = token or settings.telegram_bot_token
    if not token:
        return False, "no bot token or channel", {}
    body, content_type = _multipart(pola, plik)
    url = f"{API}/bot{token}/{metoda}"
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
            plik: tuple[str, str, bytes] | None, transport,
            token: str | None = None) -> tuple[bool, str]:
    """`_strzal_json` dla wywołujących, których `message_id` nie interesuje."""
    poszlo, powod, _ = _strzal_json(metoda, pola, plik, transport, token)
    return poszlo, powod


def send_photo_json(png: bytes, caption: str, *, transport=None) -> tuple[bool, str, dict]:
    """Jak `send_photo`, ale oddaje też wysłaną wiadomość.

    Z `result` potrzebne są `message_id` i `chat.username` — z tych dwóch
    składa się publiczny link do właśnie opublikowanego posta (`post_url`),
    bez którego Reach BOT nie wie, pod czym zamawiać."""
    if not is_enabled():
        return False, "no bot token or channel", {}
    return _strzal_json("sendPhoto",
                        {"chat_id": settings.telegram_chat_id, "caption": caption[:1024],
                         "parse_mode": "HTML"},
                        ("photo", "certificate.png", png), transport)


def send_photo(png: bytes, caption: str, *, transport=None) -> tuple[bool, str]:
    """Grafika + podpis pod nią. `caption` w HTML-u (limit Telegrama: 1024 znaki)."""
    poszlo, powod, _ = send_photo_json(png, caption, transport=transport)
    return poszlo, powod


def post_url(dane: dict) -> str:
    """Publiczny link do wiadomości z odpowiedzi Telegrama (albo pusty).

    Kanał prywatny nie ma `username`, więc nie ma też publicznego linku —
    i nie ma czego podbijać."""
    czat = (dane or {}).get("chat") or {}
    nazwa = czat.get("username")
    mid = (dane or {}).get("message_id")
    return f"https://t.me/{nazwa}/{mid}" if nazwa and mid else ""


def _get(metoda: str, token: str, parametry: str = "") -> dict:
    """GET na Bot API. Zwraca `result` albo `{}`; nigdy nie wywraca wywołania.

    Osobno od `_strzal_json`, bo to są odczyty do panelu (getMe, getChat,
    getChatMember) — krótki timeout, brak multipart i brak logowania odmowy
    jako błędu: „bot nie jest adminem" to tutaj odpowiedź, a nie awaria.
    """
    if not token:
        return {}
    url = f"{API}/bot{token}/{metoda}"
    if parametry:
        url += f"?{parametry}"
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            dane = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            dane = json.loads(e.read() or b"{}")
        except Exception:
            return {}
    except Exception as e:  # pragma: no cover - sieć
        print(f"[telegram] {metoda} błąd: {e}")
        return {}
    wynik = dane.get("result")
    return wynik if isinstance(wynik, dict) else {}


# getMe raz na proces i to PER TOKEN. Jeden wspólny cache był poprawny, dopóki
# bot był jeden; teraz zwróciłby nazwę tego bota, który odpytał pierwszy, dla
# wszystkich trzech.
_BOT_ME: dict[str, dict] = {}


def get_me(token: str | None = None) -> dict:
    """`{id, username, first_name}` bota albo `{}` — do zdrowia w panelu."""
    token = token or settings.telegram_leads_token
    if not token:
        return {}
    if token not in _BOT_ME:
        dane = _get("getMe", token)
        if not dane:
            return {}          # nie cache'ujemy porażki sieciowej
        _BOT_ME[token] = dane
    return _BOT_ME[token]


def bot_username(token: str | None = None) -> str:
    """Nazwa bota (bez @) — do instrukcji parowania w panelu.

    Domyślnie bot DESKU LEADÓW, nie treści: parowanie polega na tym, że admin
    pisze `/start <kod>` do bota, którego kliknięcia obsługuje panel. Wskazanie
    tu bota treści wysyłałoby ludzi do bota, którego webhook prowadzi zupełnie
    gdzie indziej (kupowanie zasięgu), i parowanie po cichu nie działałoby.

    Brak tokenu albo padnięta sieć = pusty string, panel pisze wtedy
    „the desk bot" zamiast linka."""
    return str(get_me(token).get("username") or "")


def get_chat(chat_id: str, *, token: str | None = None) -> dict:
    """Opis kanału/czatu (`title`, `username`) albo `{}`."""
    if not chat_id:
        return {}
    return _get("getChat", token or settings.telegram_bot_token,
                f"chat_id={urllib.parse.quote(str(chat_id))}")


def chat_member_status(chat_id: str, user_id: int, *,
                       token: str | None = None) -> str:
    """Status bota w kanale: `administrator`, `member`, `left`… albo `""`.

    PUŁAPKA, na którą łatwo się nabrać: `getChat` na kanale PUBLICZNYM udaje się
    każdemu botowi, także takiemu bez żadnych uprawnień. Jedyne wiarygodne
    pytanie „czy mogę tu publikować" to `getChatMember` o samego siebie.
    """
    if not chat_id or not user_id:
        return ""
    dane = _get("getChatMember", token or settings.telegram_bot_token,
                f"chat_id={urllib.parse.quote(str(chat_id))}&user_id={user_id}")
    return str(dane.get("status") or "")


def delete_lead_card(message_id: int, *, bot: Bot | None = None,
                     transport=None) -> tuple[bool, str]:
    """Zdejmuje kartę leada z czatu działu — wołane przy kasowaniu leada.

    Bez tego wpis testowy znikał z bazy, a jego karta wisiała na kanale jak
    sierota i dalej dawała się klikać — w lead, którego już nie było.

    `bot` musi być TYM desku, na którym karta wisi: kasowanie cudzym tokenem
    w cudzym czacie po prostu nie trafi w wiadomość i sierota zostanie."""
    bot = bot or desk("leads")
    if not leads_enabled(bot):
        return False, "leads chat not configured"
    return _strzal("deleteMessage",
                   {"chat_id": bot.chat_id, "message_id": str(message_id)},
                   None, transport, bot.token)


def send_dm(chat_id: str | int, text: str, *, bot: Bot | None = None,
            transport=None) -> tuple[bool, str]:
    """Wiadomość w prywatnym czacie z botem (odpowiedź na `/start <kod>`).

    `chat_id` przychodzi z update'u, ale TOKEN musi być tego bota, który ten
    update dostał — prywatna rozmowa istnieje osobno z każdym botem i cudzym
    tokenem nie da się do niej napisać."""
    bot = bot or desk("leads")
    return _strzal("sendMessage", {"chat_id": str(chat_id), "text": text[:4096]},
                   None, transport, bot.token)


def send_message_json(text: str, *, transport=None) -> tuple[bool, str, dict]:
    """Jak `send_message`, ale oddaje też wysłaną wiadomość (patrz `post_url`)."""
    if not is_enabled():
        return False, "no bot token or channel", {}
    return _strzal_json("sendMessage",
                        {"chat_id": settings.telegram_chat_id, "text": text[:4096],
                         "parse_mode": "HTML", "disable_web_page_preview": "false"},
                        None, transport)


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
# INNY bot i INNY czat niż kanał z wypłatami: tamten jest publiczny, a tu leci
# imię, mail i telefon człowieka. Pomyłka w tym miejscu to wyciek danych na
# oczach klientów, więc desk jest jawnym argumentem, a nie czymś, co funkcja
# sobie dobiera — i dlatego `Bot` niesie token razem z czatem: dobranie jednego
# bez drugiego to właśnie ta pomyłka.

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


def leads_enabled(bot: Bot | None = None) -> bool:
    """Czy dany desk leadów jest skonfigurowany. Bez argumentu — desk domyślny."""
    if bot is None or bot.key == "leads":
        return settings.telegram_leads_enabled
    if bot.key == "leads_ng":
        return settings.telegram_leads_ng_enabled
    return bool(bot.token and bot.chat_id)


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
                    bot: Bot | None = None,
                    transport=None) -> tuple[bool, str, int | None]:
    """Alert o nowym leadzie. Zwraca też `message_id` wysłanej wiadomości.

    `message_id` musi wrócić do bazy: notatki z rozmowy wpisuje się ODPOWIEDZIĄ
    na ten post, a webhook nie ma innego sposobu, żeby dopasować odpowiedź do
    leada. Bez zapisanego id notatka po prostu przepada.

    `callback_data` musi zmieścić się w 64 bajtach, stąd samo `lead:<id>:<akcja>`
    zamiast czegokolwiek opisowego — resztę webhook dobiera z bazy po id.
    """
    bot = bot or desk("leads")
    if not leads_enabled(bot):
        return False, "no bot token or leads chat", None
    poszlo, powod, wynik = _strzal_json(
        "sendMessage",
        {"chat_id": bot.chat_id, "text": text[:4096],
         "parse_mode": "HTML", "disable_web_page_preview": "true",
         "reply_markup": json.dumps(keyboard or lead_keyboard(lead_id))},
        None, transport, bot.token)
    mid = wynik.get("message_id")
    return poszlo, powod, mid if isinstance(mid, int) else None


def send_lead_message(text: str, *, bot: Bot | None = None,
                      transport=None) -> tuple[bool, str]:
    """Wiadomość na czat z leadami BEZ przycisków — przypomnienia z crona.

    Osobna funkcja od `send_message`, bo tamta celuje w publiczny kanał z
    wypłatami. Przypomnienie niesie imię i mail człowieka, więc pomyłka w czacie
    jest wyciekiem, a nie literówką; jedno wywołanie mniej do pomylenia.

    Z tego samego powodu `bot` nie ma tu rozsądnej wartości „dowolna": desk
    nigeryjski i domyślny czyta kto inny, a przypomnienie niesie imię i mail.
    """
    bot = bot or desk("leads")
    if not leads_enabled(bot):
        return False, "no bot token or leads chat"
    return _strzal("sendMessage",
                   {"chat_id": bot.chat_id, "text": text[:4096],
                    "parse_mode": "HTML", "disable_web_page_preview": "true"},
                   None, transport, bot.token)


def answer_callback(callback_id: str, text: str, *, bot: Bot | None = None,
                    transport=None) -> tuple[bool, str]:
    """Zdejmuje „zegarek" z przycisku. Bez tej odpowiedzi Telegram kręci kółkiem
    przez minutę i klikający nie wie, czy cokolwiek się stało.

    Odpowiedzieć musi TEN bot, który dostał kliknięcie — `callback_query_id`
    jest ważny wyłącznie dla niego. Desk zna webhook z adresu, którym przyszedł
    update, i przekazuje go tutaj."""
    bot = bot or desk("leads")
    return _strzal("answerCallbackQuery",
                   {"callback_query_id": callback_id, "text": text[:200]},
                   None, transport, bot.token)


def edit_lead_message(chat_id: str, message_id: int, text: str,
                      *, keyboard: dict | None = None,
                      bot: Bot | None = None,
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
    return _strzal("editMessageText", pola, None, transport,
                   (bot or desk("leads")).token)
