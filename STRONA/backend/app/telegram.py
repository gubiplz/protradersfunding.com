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
import time
import urllib.error
import urllib.request

from .config import get_settings

settings = get_settings()

API = "https://api.telegram.org"
TIMEOUT_SEK = 20

# Najdłuższe czekanie, jakie wolno przespać w miejscu, przy odmowie z limitu.
# Telegram sam podaje, ile trzeba odczekać; przy sekundach taniej jest poczekać
# niż zostawiać kartę kolejce, a przy dłuższych — odwrotnie, bo request stoi.
RETRY_AFTER_MAX_SEK = 5

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
                 plik: tuple[str, str, bytes] | None, transport,
                 ponowione: bool = False,
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
    # Brak `token` = bot główny; osobny podaje ten, kto wie, do którego czatu
    # pisze (patrz `token_dla_czatu`).
    token = token or settings.telegram_bot_token
    if not token:
        return False, "no bot token or channel", {}
    # Metoda bez pól (getMe) nie ma z czego zbudować multiparta, a części bez
    # ANI JEDNEJ granicy Telegram odbija jako HTTP 400 — pusty JSON przechodzi.
    if pola or plik:
        body, content_type = _multipart(pola, plik)
    else:
        body, content_type = b"{}", "application/json"
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
    # Jedna grupa przyjmuje ~20 wiadomości na minutę, a kampania wieczorem to
    # przekracza. Telegram nie odmawia wtedy na stałe — MÓWI, ile odczekać.
    # Bez tego nadwyżka schodziła do kolejki dosyłek, która chodzi z ruchu
    # strony, więc karta z 20:03 lądowała na kanale dopiero po nocy. Jedno
    # ponowienie: drugie znaczyłoby, że limit trzyma dłużej, niż wolno tu stać,
    # i od tego jest właśnie kolejka.
    if status == 429 and not ponowione:
        czekaj = (odp.get("parameters") or {}).get("retry_after")
        if isinstance(czekaj, (int, float)) and 0 < czekaj <= RETRY_AFTER_MAX_SEK:
            time.sleep(float(czekaj))
            return _strzal_json(metoda, pola, plik, transport, ponowione=True,
                                token=token)
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
    poszlo, powod, _ = _strzal_json(metoda, pola, plik, transport, token=token)
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


def send_content(chat_id: str, text: str, *, png: bytes | None = None,
                 photo_url: str | None = None, video_url: str | None = None,
                 token: str | None = None, transport=None) -> tuple[bool, str, dict]:
    """Post na DOWOLNY kanał treści — z grafiką albo bez.

    `send_photo_json` i `send_message_json` celują na sztywno w kanał wypłat,
    bo Payout BOT ma tylko jeden adres. Kolejka treści obsługuje kilka kanałów,
    więc czat jest tu argumentem, a token dobierany tak samo jak wszędzie
    indziej — po czacie, nie po domyśle wywołującego.

    Limit podpisu pod zdjęciem to 1024 znaki, samego tekstu 4096. Walidator
    kolejki odmawia wcześniej, żeby Telegram nie uciął twierdzenia w połowie
    zdania — tutaj przycięcie zostaje wyłącznie jako ostatni bezpiecznik.
    """
    token = token or bot_token_czatu(chat_id)
    if not token or not chat_id:
        return False, "no bot token or chat", {}
    if video_url:
        # Klip idzie ADRESEM, tak samo jak zdjęcie. Uwaga na limit: plik
        # pobierany spod adresu może mieć najwyżej 20 MB — powyżej Telegram
        # odmawia i nie ma tu nic do „spróbowania jeszcze raz".
        # `supports_streaming` pozwala odtwarzać przed pobraniem całości.
        return _strzal_json("sendVideo",
                            {"chat_id": str(chat_id), "video": video_url,
                             "caption": text[:1024], "parse_mode": "HTML",
                             "supports_streaming": "true"},
                            None, transport, token=token)
    if photo_url:
        # Telegram pobiera zdjęcie z podanego adresu SAM. Dzięki temu post
        # odtwarzany z archiwum nie wymaga wnoszenia cudzych plików do repo —
        # wystarczy adres, pod którym Telegram już to zdjęcie trzyma.
        return _strzal_json("sendPhoto",
                            {"chat_id": str(chat_id), "photo": photo_url,
                             "caption": text[:1024], "parse_mode": "HTML"},
                            None, transport, token=token)
    if png:
        return _strzal_json("sendPhoto",
                            {"chat_id": str(chat_id), "caption": text[:1024],
                             "parse_mode": "HTML"},
                            ("photo", "post.png", png), transport, token=token)
    return _strzal_json("sendMessage",
                        {"chat_id": str(chat_id), "text": text[:4096],
                         "parse_mode": "HTML", "disable_web_page_preview": "true"},
                        None, transport, token=token)


def post_url(dane: dict) -> str:
    """Link do wiadomości z odpowiedzi Telegrama (albo pusty).

    Kanał publiczny: `t.me/<username>/<id>`. Kanał prywatny nie ma `username`,
    ale ma link dla członków: `t.me/c/<id czatu bez -100>/<id wiadomości>` —
    otwiera post każdemu, kto na kanale jest, czyli całemu działowi. Do 2026-09
    prywatny kanał dostawał pusty link i „Preview" w panelu nie miało dokąd
    prowadzić. Publiczny link zostaje pierwszy, bo tylko on nadaje się do
    podbijania zasięgu (`reach`)."""
    czat = (dane or {}).get("chat") or {}
    nazwa = czat.get("username")
    mid = (dane or {}).get("message_id")
    if not mid:
        return ""
    if nazwa:
        return f"https://t.me/{nazwa}/{mid}"
    cid = str(czat.get("id") or "")
    if cid.startswith("-100") and cid[4:].isdigit():
        return f"https://t.me/c/{cid[4:]}/{mid}"
    return ""


def edit_content(chat_id: str, message_id: int, text: str, *, kind: str = "text",
                 token: str | None = None, transport=None) -> tuple[bool, str]:
    """Przepisuje OPUBLIKOWANY post na kanale — tekst albo podpis pod grafiką.

    Telegram ma dwie metody, bo to dwa różne pola: `editMessageText` dla
    wiadomości tekstowej, `editMessageCaption` dla zdjęcia i klipu. Pomylenie
    ich kończy się „message can't be edited" bez słowa o powodzie. Samej
    grafiki nie da się podmienić tą drogą (`editMessageMedia` wymaga pliku
    pod adresem) — panel mówi to wprost i nie próbuje."""
    token = token or bot_token_czatu(chat_id)
    if not token or not chat_id or not message_id:
        return False, "no bot token, chat or message"
    if kind in ("photo", "video"):
        pola = {"chat_id": str(chat_id), "message_id": str(message_id),
                "caption": text[:1024], "parse_mode": "HTML"}
        return _strzal("editMessageCaption", pola, None, transport, token)
    pola = {"chat_id": str(chat_id), "message_id": str(message_id),
            "text": text[:4096], "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    return _strzal("editMessageText", pola, None, transport, token)


def delete_content(chat_id: str, message_id: int, *, token: str | None = None,
                   transport=None) -> tuple[bool, str]:
    """Kasuje opublikowany post z kanału. Bot-administrator kanału może to
    zrobić bez limitu czasu (48 h dotyczy czatów prywatnych)."""
    token = token or bot_token_czatu(chat_id)
    if not token or not chat_id or not message_id:
        return False, "no bot token, chat or message"
    return _strzal("deleteMessage",
                   {"chat_id": str(chat_id), "message_id": str(message_id)},
                   None, transport, token)


_BOT_USERNAME: dict[str, str] = {}


def bot_username(token: str | None = None) -> str:
    """Nazwa bota (bez @) — do instrukcji parowania i do przeglądu kanałów.

    getMe raz na proces i PER TOKEN. Jeden wspólny cache był poprawny, dopóki
    bot był jeden; odkąd darmowy czat ma własnego, zwracałby nazwę tego, który
    odpytał pierwszy, dla wszystkich — a panel pokazuje po niej, KTÓRY bot
    obsługuje dany kanał.

    Brak tokenu albo padnięta sieć = pusty string, panel pisze wtedy
    „the desk bot" zamiast linka."""
    token = token or settings.telegram_bot_token
    if not token:
        return ""
    if token not in _BOT_USERNAME:
        try:
            with urllib.request.urlopen(f"{API}/bot{token}/getMe", timeout=5) as r:
                dane = json.loads(r.read() or b"{}")
        except Exception as e:  # pragma: no cover - sieć
            print(f"[telegram] getMe błąd: {e}")
            return ""          # nie cache'ujemy porażki sieciowej
        _BOT_USERNAME[token] = str((dane.get("result") or {}).get("username") or "")
    return _BOT_USERNAME[token]


_BOT_ID: dict[str, int] = {}


def bot_id(*, transport=None, token: str | None = None) -> int:
    """Numeryczne id bota (getMe, raz na proces). 0 = brak tokenu albo błąd.

    Cache jest PER TOKEN. Jeden wspólny był poprawny, dopóki bot był jeden;
    odkąd darmowy czat ma własnego, zwracałby id tego, który odpytał pierwszy,
    dla wszystkich — a po tym id sprawdzamy uprawnienia w kanale."""
    token = token or settings.telegram_bot_token
    if not token:
        return 0
    if token not in _BOT_ID:
        poszlo, _, dane = _strzal_json("getMe", {}, None, transport, token=token)
        if not poszlo:
            return 0          # nie cache'ujemy porażki sieciowej
        _BOT_ID[token] = int(dane.get("id") or 0)
    return _BOT_ID[token]


def chat_info(chat_id: str | int, *, token: str | None = None,
              transport=None) -> dict:
    """`getChat` — nazwa publiczna i tytuł kanału (pusty słownik przy błędzie).

    Panel Reach BOT-a pokazuje, na jaki kanał faktycznie idą posty: w env jest
    samo `TELEGRAM_CHAT_ID` (bywa liczbowe), a admin myśli o kanale nazwą.

    Bez `token` pytamy botem, który TEN czat obsługuje — bot spoza kanału
    dostaje „chat not found" i panel pokazałby pustkę zamiast nazwy."""
    token = token or bot_token_czatu(chat_id)
    if not token or not chat_id:
        return {}
    poszlo, _, dane = _strzal_json("getChat", {"chat_id": str(chat_id)}, None,
                                   transport, token=token)
    if not poszlo:
        return {}
    return {"id": dane.get("id"), "username": dane.get("username") or "",
            "title": dane.get("title") or ""}


def jest_adminem(chat_id: str | int, *, token: str | None = None,
                 transport=None) -> bool | None:
    """Czy bot jest administratorem kanału. `None` = nie dało się sprawdzić.

    To NIE jest kosmetyka: bez uprawnień admina Telegram w ogóle nie wysyła
    `channel_post`, więc automat po cichu nic nie robi. Panel musi umieć
    powiedzieć „dodaj bota jako admina", zamiast milczeć.

    Pytamy o TEGO bota, który ma tu publikować — inaczej odpowiedź dotyczyłaby
    cudzych uprawnień i była bezwartościowa."""
    token = token or bot_token_czatu(chat_id)
    if not token or not chat_id:
        return None
    ja = bot_id(transport=transport, token=token)
    if not ja:
        return None
    poszlo, _, dane = _strzal_json(
        "getChatMember", {"chat_id": str(chat_id), "user_id": str(ja)}, None,
        transport, token=token)
    if not poszlo:
        return False  # „member list is inaccessible" = bot jest poza kanałem
    return str(dane.get("status") or "") in ("administrator", "creator")


def delete_lead_card(message_id: int, *, chat_id: str | None = None,
                     transport=None) -> tuple[bool, str]:
    """Zdejmuje kartę leada z czatu, w którym wisi — wołane przy kasowaniu leada.

    Bez tego wpis testowy znikał z bazy, a jego karta wisiała na kanale jak
    sierota i dalej dawała się klikać — w lead, którego już nie było.

    `message_id` jest unikalne w obrębie czatu, nie bota: bez `chat_id` z bazy
    kasowanie karty free trafiłoby w cudzą wiadomość o tym samym numerze."""
    czat, token, mozna = _lead_sendable(chat_id)
    if not mozna:
        return False, "leads chat not configured"
    return _strzal("deleteMessage",
                   {"chat_id": czat, "message_id": str(message_id)}, None, transport,
                   token)


def send_dm(chat_id: str | int, text: str, *, token: str | None = None,
            transport=None) -> tuple[bool, str]:
    """Wiadomość w prywatnym czacie z botem (odpowiedź na `/start <kod>`).

    `chat_id` przychodzi z update'u, ale TOKEN musi być tego bota, który ten
    update dostał: prywatna rozmowa istnieje osobno z każdym botem, więc
    odpowiedź cudzym tokenem trafia do innej rozmowy albo nigdzie."""
    return _strzal("sendMessage", {"chat_id": str(chat_id), "text": text[:4096]},
                   None, transport, token)


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

# Dlaczego przegraliśmy. Rząd pokazuje się WYŁĄCZNIE póki lead stoi na
# „odpada" albo w koszu i powodu jeszcze nie ma — po wybraniu znika, bo wybrany
# powód widać już w treści karty, a klawiatura na telefonie ma być krótka.
#
# Status zapisuje się nadal PIERWSZYM kliknięciem i te przyciski niczego nie
# blokują. To świadoma różnica względem panelu, który powodu wymaga: tam arkusz
# jest już otwarty i drugi tap nic nie kosztuje, a tu ktoś odkłada telefon
# w połowie — wymuszony drugi krok kosztowałby zapisany status, czyli to jedno,
# czego stracić nie wolno.
#
# Kody muszą być te same co `models.LOST_REASONS`; pilnuje tego test, bo ten
# plik jest transportem i celowo nie importuje modeli.
LOST_REASON_BUTTONS = (("💸 Za drogo", "price"),
                       ("🏃 Kupił gdzie indziej", "competitor"),
                       ("🚫 Nie kwalifikuje się", "not_qualified"),
                       ("👻 Przestał odpisywać", "ghosted"),
                       ("🤖 Bot / śmieć", "spam"),
                       ("❔ Inne", "other"))

# Statusy, po których pytamy o powód. `burned` nie ma swojego przycisku wyżej —
# ustawia się go z panelu — ale karta na kanale przepisuje się po każdej zmianie,
# więc pytanie i tak dojdzie do tego, kto ma pod ręką tylko telefon.
LOST_STATUSES = ("rejected", "burned")

CLAIM_BUTTON = ("🙋 Biorę tego", "claim")
# Ten sam `claim`, inny opis: pod kartą z właścicielem to nie jest wzięcie
# niczyjego leada, tylko odebranie go koledze, i przycisk ma to mówić wprost.
TAKEOVER_BUTTON = ("🤝 Przejmuję", "claim")
RELEASE_BUTTON = ("↩️ Oddaję", "release")


def lead_chat_id(source: str | None = None) -> str:
    """Czat dla leada o takim `source` — free ma swój, reszta idzie do działu.

    Zwykła funkcja, a nie property w ustawieniach, bo `get_settings()` jest
    `lru_cache`'owane i testy podmieniają pola obiektu w miejscu.

    Bez skonfigurowanego czatu free leady z darmowego challenge'u wracają do
    czatu działu — mieszają się z płatnymi, ale nie giną.
    """
    if (source or "").strip().lower().startswith("free") and settings.telegram_free_leads_chat_id:
        return settings.telegram_free_leads_chat_id
    return settings.telegram_leads_chat_id


def bot_token_czatu(chat_id: str | None = None) -> str:
    """Token bota obsługującego TEN czat — leadów albo kanał treści.

    Bliźniak `lead_chat_id`, tylko o jeden krok dalej: tamta funkcja wybiera
    czat po lejku, ta wybiera bota po czacie. Rozstrzyga CZAT, a nie `source`,
    bo wywołujący zna czat zawsze — z bazy (`lead.tg_chat_id`) albo z samego
    update'u Telegrama — a `source` tylko przy pierwszej wysyłce.

    Puste `TELEGRAM_FREE_LEADS_BOT_TOKEN` = darmowy czat obsługuje bot główny,
    czyli zachowanie sprzed podziału. Nic nie przestaje działać przez samo
    wdrożenie tego kodu.
    """
    czat = str(chat_id or "")
    # Tabela, a nie łańcuch ifów: dołożenie kolejnego bota ma być wpisem, nie
    # rozgałęzieniem. Pierwszy wpis z ustawionym tokenem i pasującym czatem
    # wygrywa; brak dopasowania = bot główny.
    for cel, token in ((settings.telegram_free_leads_chat_id,
                        settings.telegram_free_leads_bot_token),
                       (settings.telegram_leads_chat_id,
                        settings.telegram_leads_bot_token)):
        if czat and token and cel and czat == str(cel):
            return token
    return settings.telegram_bot_token


def _lead_sendable(chat_id: str | None) -> tuple[str, str, bool]:
    """Docelowy czat, token do niego i czy da się do niego pisać.

    Sprawdzamy TEN czat, a nie „czy leady w ogóle są skonfigurowane":
    konfiguracja z samym TELEGRAM_FREE_LEADS_CHAT_ID gubiłaby po cichu
    wszystkie karty free, bo tamten warunek patrzy tylko na czat działu.

    Czat i token wracają RAZEM, bo rozdzielenie ich to dokładnie ta pomyłka,
    przed którą ten podział ma chronić: wysyłka cudzym botem na własny czat
    kończy się „Unauthorized" albo, gorzej, trafia nie tam, gdzie miała.
    """
    czat = chat_id if chat_id is not None else settings.telegram_leads_chat_id
    token = bot_token_czatu(czat)
    return czat, token, bool(settings.telegram_on and token and czat)


def lead_alerts_on(chat_id: str | None = None) -> bool:
    """Czy karty leadów mają dokąd iść.

    Wywołujący potrzebuje tego, żeby odróżnić „Telegram odmówił" od „Telegramu
    tu nie ma". Pierwsze jest awarią do ponowienia, drugie — świadomym
    ustawieniem, i zapisywanie go jako awarii dopisywałoby każdemu leadowi
    zdarzenie o nieudanej wysyłce oraz trzymało go w kolejce dosyłek bez końca.
    """
    return _lead_sendable(chat_id)[2]


def lead_keyboard(lead_id: int, *, owner: str | None = None,
                  status: str = "new", tier: str | None = None,
                  lost_reason: str | None = None) -> dict:
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

    Trzeci etap to powód przegranej — dochodzi dopiero, gdy lead stoi na
    „odpada"/koszu i powodu jeszcze nie ma, i znika po wybraniu. Pod każdą inną
    kartą byłby sześcioma przyciskami pytającymi o coś, co się nie stało.
    """
    def guzik(opis: str, akcja: str, wybrany: bool = False) -> dict:
        return {"text": ("• " + opis) if wybrany else opis,
                "callback_data": f"lead:{lead_id}:{akcja}"}

    if not owner:
        return {"inline_keyboard": [[guzik(*CLAIM_BUTTON)]]}
    statusy = [guzik(o, s, s == status) for o, s in LEAD_BUTTONS]
    # Pytanie o powód wchodzi NAD statusy, nie pod nie: to jedyny rząd, który
    # czeka na odpowiedź, a na telefonie widać kilka pierwszych przycisków.
    # Pod tierem i „przejmuję" trzeba by go szukać scrollem.
    powody = ([[guzik(o, f"why_{k}") for o, k in LOST_REASON_BUTTONS[i:i + 2]]
               for i in range(0, len(LOST_REASON_BUTTONS), 2)]
              if status in LOST_STATUSES and not lost_reason else [])
    return {"inline_keyboard": [
        *powody,
        *[statusy[i:i + 2] for i in range(0, len(statusy), 2)],
        [guzik(o, a, a == f"tier_{tier or ''}") for o, a in TIER_BUTTONS],
        # Przejęcie stoi pod kartą, która ma już właściciela, i to jest celowe:
        # lead nie czeka, aż ktoś zdąży kliknąć „oddaję".
        [guzik(*TAKEOVER_BUTTON), guzik(*RELEASE_BUTTON)],
    ]}


def send_lead_alert(lead_id: int, text: str, *,
                    keyboard: dict | None = None,
                    chat_id: str | None = None,
                    transport=None) -> tuple[bool, str, int | None]:
    """Alert o nowym leadzie. Zwraca też `message_id` wysłanej wiadomości.

    `message_id` musi wrócić do bazy: notatki z rozmowy wpisuje się ODPOWIEDZIĄ
    na ten post, a webhook nie ma innego sposobu, żeby dopasować odpowiedź do
    leada. Bez zapisanego id notatka po prostu przepada.

    `callback_data` musi zmieścić się w 64 bajtach, stąd samo `lead:<id>:<akcja>`
    zamiast czegokolwiek opisowego — resztę webhook dobiera z bazy po id.
    """
    czat, token, mozna = _lead_sendable(chat_id)
    if not mozna:
        return False, "no bot token or leads chat", None
    poszlo, powod, wynik = _strzal_json(
        "sendMessage",
        {"chat_id": czat, "text": text[:4096],
         "parse_mode": "HTML", "disable_web_page_preview": "true",
         "reply_markup": json.dumps(keyboard or lead_keyboard(lead_id))},
        None, transport, token=token)
    mid = wynik.get("message_id")
    return poszlo, powod, mid if isinstance(mid, int) else None


def send_lead_message(text: str, *, chat_id: str | None = None,
                      transport=None) -> tuple[bool, str]:
    """Wiadomość na czat z leadami BEZ przycisków — przypomnienia z crona.

    Osobna funkcja od `send_message`, bo tamta celuje w publiczny kanał z
    wypłatami. Przypomnienie niesie imię i mail człowieka, więc pomyłka w czacie
    jest wyciekiem, a nie literówką; jedno wywołanie mniej do pomylenia.
    """
    czat, token, mozna = _lead_sendable(chat_id)
    if not mozna:
        return False, "no bot token or leads chat"
    return _strzal("sendMessage",
                   {"chat_id": czat, "text": text[:4096],
                    "parse_mode": "HTML", "disable_web_page_preview": "true"},
                   None, transport, token)


def answer_callback(callback_id: str, text: str, *, chat_id: str | None = None,
                    transport=None) -> tuple[bool, str]:
    """Zdejmuje „zegarek" z przycisku. Bez tej odpowiedzi Telegram kręci kółkiem
    przez minutę i klikający nie wie, czy cokolwiek się stało.

    Odpowiedzieć musi TEN bot, który dostał kliknięcie — `callback_query_id`
    jest ważny wyłącznie dla niego. Czat pochodzi z update'u, więc `chat_id`
    wystarczy, żeby trafić w tego właściwego."""
    return _strzal("answerCallbackQuery",
                   {"callback_query_id": callback_id, "text": text[:200]},
                   None, transport, bot_token_czatu(chat_id))


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
    # Kartę przepisuje bot, który ją wysłał — `message_id` jest jego i cudzym
    # tokenem nie da się jej tknąć. Czat wystarcza, żeby go wskazać.
    return _strzal("editMessageText", pola, None, transport, bot_token_czatu(chat_id))
