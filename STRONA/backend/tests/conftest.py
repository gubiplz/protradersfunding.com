"""Izolacja testów od lokalnego `backend/.env`.

`config.py` woła `load_dotenv()`, więc bez tego pliku testy dziedziczyłyby
deweloperskie ustawienia — m.in. `METAQUOTES_WEB_ENABLED=true`, przez co
provisioning w testach startowałby prawdziwą przeglądarkę i strzelał do
MetaQuotes (7 testów padało po ~100 s zamiast 3 s).

conftest.py wykonuje się PRZED modułami testowymi, więc twarde przypisania
poniżej wygrywają z `.env` (load_dotenv nie nadpisuje istniejących zmiennych).
"""
import os

# Kanały provisioningu MUSZĄ być wyłączone — żaden test nie rusza sieci.
os.environ["METAQUOTES_WEB_ENABLED"] = "false"
os.environ["METAAPI_AUTO_CREATE"] = "false"
os.environ["METAAPI_TOKEN"] = ""
os.environ["METAAPI_PROVISIONING_PROFILE_ID"] = ""
os.environ["STRIPE_SECRET_KEY"] = ""     # checkout w trybie MOCK
os.environ["SMTP_HOST"] = ""             # maile na konsolę, bez wysyłki
os.environ["NOTIFY_WEBHOOK_URL"] = ""
# Kanał Telegram i usługa zrzutu certyfikatu — puste, żeby żaden test nie wyszedł
# do sieci ani nie opublikował niczego na prawdziwym kanale. Test Payout BOT-a
# włącza je sobie sam, podstawiając transport.
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["SHOT_API_URL"] = ""
# Dostawca zasięgu (Reach BOT) — puste, żeby żaden test nie zamówił niczego
# za prawdziwe pieniądze. Testy Reach BOT-a włączają go same, podstawiając
# transport, tym samym wzorcem co kanał Telegrama wyżej.
os.environ["REACH_API_URL"] = ""
os.environ["REACH_API_KEY"] = ""
# Poller nie moze chodzic w tle testow — przestawialby salda kont.
os.environ["POLLER_ENABLED"] = "false"
# Payout BOT nie moze publikowac „przy okazji" zwyklych odczytow dashboardu:
# middleware lazy-tick odpala go na ruchu, a testy bija w te sciezki o roznych
# porach zegara — wypłaty pojawialyby sie niedeterministycznie. Test piggybacku
# wlacza go sam monkeypatchem na singletonie settings.
os.environ["PAYOUTBOT_ON_TRAFFIC"] = "false"
# Ten sam powod dla dziennego recapu: request na sciezkach lazy-ticku po 06:00
# polskiego czasu zuzylby dzienny guard i pchnal wpisy do centrum powiadomien
# w srodku niezwiazanych testow. Testy recapu wolaja push.daily_recap wprost.
os.environ["RECAP_ON_TRAFFIC"] = "false"
# I dla follow-upow leadow: sweep z ruchu zuzylby jednorazowe przypomnienia
# (dedup po lead_events) w srodku niezwiazanych testow. Test sweepa wlacza go
# sam monkeypatchem na singletonie settings.
os.environ["LEADS_ON_TRAFFIC"] = "false"
# Web push: puste klucze VAPID = push wylaczony (settings.push_enabled to
# property liczone z kluczy). Testy pusha wlaczaja go same monkeypatchem
# kluczy na singletonie settings — dlatego NIE ustawiamy tu PUSH_ENABLED=false,
# ktore jest awaryjnym wylacznikiem silniejszym niz klucze.
os.environ["VAPID_PUBLIC_KEY"] = ""
os.environ["VAPID_PRIVATE_KEY"] = ""

# Sekrety musza byc znane JUZ TERAZ: `get_settings()` jest cache'owane, wiec
# obiekt ustawien powstaje przy pierwszym imporcie `app.config` — czyli w tym
# pliku testowym, ktory pytest zbierze jako pierwszy. Ustawianie ich dopiero
# w module testowym uzaleznialo wynik od kolejnosci alfabetycznej plikow.
os.environ.setdefault("CRON_SECRET", "sekret-crona")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

# Feed i seed: poszczególne pliki testów mogą nadpisać własnym setdefault-em
# tylko wtedy, gdy ustawimy to przez setdefault (nie twardo).
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

# Rate-limit endpointów auth trzyma licznik per PROCES, a TestClient zawsze
# przychodzi z tego samego "IP" — cała suita szybko wpadłaby w 429. Test
# rate-limitu włącza go sam (monkeypatch main._RL_DISABLED + czysty licznik).
os.environ["RATE_LIMIT_OFF"] = "true"

# Promocja „Upgrade Your Size" jest domyślnie WŁĄCZONA w produkcji, ale w
# testach musi być wyłączona: upgrade wymaga wprawdzie kodu promo, ale sama
# aktywna promocja renderuje pasek na stronach publicznych i pola
# promo_upgrade_* w /api/products — asercje o treści strony przestałyby być
# deterministyczne. Testy promocji włączają ją same (monkeypatch na
# catalog.settings).
os.environ["PROMO_UPGRADE"] = "false"


def zrodlo_portalu() -> str:
    """Szkielet portalu RAZEM z jego kodem — logika siedzi w bundlu, nie w HTML-u.

    Kod portalu wyjechał z `portal.html` do `static/js/portal-app.js` (HTML leci
    z `no-cache`, więc wklejony w stronę szedł po łączu przy każdym wejściu).
    Testy pilnujące treści widoku muszą patrzeć w oba pliki naraz, inaczej
    zaczęłyby przechodzić przez samo przeniesienie kodu, a nie przez jego stan.
    """
    from pathlib import Path
    baza = Path(__file__).resolve().parents[1]
    return ((baza / "templates" / "portal.html").read_text()
            + (baza / "static" / "js" / "portal-app.js").read_text())
