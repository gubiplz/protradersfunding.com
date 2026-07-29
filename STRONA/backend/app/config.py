"""Konfiguracja aplikacji — wszystko sterowane zmiennymi środowiskowymi (.env).

Domyślnie system startuje w trybie 0 zł, ale z REALNYMI kontami MT5:
  - DATABASE_URL  -> lokalny SQLite (zero setupu)
  - FEED          -> "metaquotes_web": konta demo zakładane na MetaQuotes-Demo
                     przez web terminal (wymaga: playwright install chromium)
  - STRIPE        -> brak kluczy => MOCK checkout (płatność symulowana, 0 zł)
  - SMTP          -> brak => maile lecą na konsolę (+ opcjonalny webhook Make)

FEED=sim wyłącza kontakt z MetaQuotes — konta dostają wtedy placeholder
`PropFunding-SIM`, którego NIE MA w MetaTraderze (tryb tylko do dem/testów).

Produkcja: klucze Stripe, SMTP, DATABASE_URL (Supabase), AUTO_SEED=false.
"""
from __future__ import annotations

import os
from functools import lru_cache

try:  # wczytanie .env jest opcjonalne (gdy python-dotenv zainstalowane)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


class Settings:
    # --- Baza danych ---
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./prop.db")

    # --- Źródło danych equity ---
    # "local" (domyślnie) = NullFeed: nic nie jest czytane z zewnątrz, konta stoją
    # na ostatnim saldzie i ruszają dopiero pod Trade BOT-em.
    # "metaquotes_web"/"metaapi" = realne konta MT5; "sim" = losowy spacer equity.
    feed: str = os.getenv("FEED", "local").lower()   # local | metaquotes_web | metaapi | sim
    poll_interval_sec: float = float(os.getenv("POLL_INTERVAL_SEC", "3"))
    # Pętla pollera. Wyłączana w testach: startowała razem z aplikacją i w tle
    # przestawiała salda kont, przez co asercje o konkretnych kwotach potrafiły
    # paść zależnie od czasu wykonania.
    poller_enabled: bool = os.getenv("POLLER_ENABLED", "true").lower() == "true"

    # --- Zakładanie kont MT5 ---
    # Wyłączone: konto challenge dostaje wygenerowane lokalnie poświadczenia od razu
    # przy zakupie, bez ani jednego strzału do MetaQuotes. Ustaw true, żeby wrócić
    # do realnego provisioningu (cała ścieżka MetaQuotes/MetaApi zostaje w kodzie).
    mt5_provisioning: bool = os.getenv("MT5_PROVISIONING", "false").lower() == "true"
    # Skad brac poswiadczenia, gdy MT5_PROVISIONING=true:
    #   "pool" (domyslnie) — WYLACZNIE z puli, ktora admin uzupelnia recznie,
    #   "auto"             — najpierw probuj zalozyc konto demo (web terminal
    #                        MetaQuotes / MetaApi), a pula dopiero awaryjnie.
    # Pula jest domyslna, bo brokerzy blokuja programowe zakladanie dem i to
    # admin wie, ktore rachunki naprawde istnieja.
    provisioning_source: str = os.getenv("PROVISIONING_SOURCE", "pool").strip().lower()

    # --- MetaApi (tylko gdy FEED=metaapi) ---
    metaapi_token: str = os.getenv("METAAPI_TOKEN", "")
    # Provisioning profile (MT5 demo) z panelu MetaApi — gdy ustawione, system
    # tworzy realne konta MT5 demo i odcina handel przy breachu.
    metaapi_provisioning_profile_id: str = os.getenv("METAAPI_PROVISIONING_PROFILE_ID", "")
    metaapi_account_type: str = os.getenv("METAAPI_ACCOUNT_TYPE", "standard")
    metaapi_region: str = os.getenv("METAAPI_REGION", "new-york")
    # Auto-tworzenie kont przez MetaApi (działa tylko u brokerów zezwalających na dema).
    # Gdy true — to jest ŚCIEŻKA GŁÓWNA, a pula (PoolAccount) służy jako awaryjna.
    metaapi_auto_create: bool = os.getenv("METAAPI_AUTO_CREATE", "false").lower() == "true"

    # --- Zakładanie kont demo MT5 (gdy METAAPI_AUTO_CREATE=true) ---
    # Nazwa serwera demo brokera, np. "ICMarketsSC-Demo". Puste => szukanie po keywords.
    metaapi_demo_server_name: str = os.getenv("METAAPI_DEMO_SERVER_NAME", "")
    # Nazwy brokera do wyszukania serwera, po przecinku, np. "ICMarkets,IC Markets".
    metaapi_demo_keywords: str = os.getenv("METAAPI_DEMO_KEYWORDS", "")
    metaapi_demo_leverage: int = int(os.getenv("METAAPI_DEMO_LEVERAGE", "100"))
    # Broker wymaga telefonu w formacie międzynarodowym; Trader go nie zbiera.
    metaapi_demo_phone: str = os.getenv("METAAPI_DEMO_PHONE", "+10000000000")
    # Brokerzy rate-limitują rejestrację dem — minimalny odstęp między żądaniami.
    metaapi_demo_min_interval_sec: float = float(os.getenv("METAAPI_DEMO_MIN_INTERVAL_SEC", "2"))
    metaapi_demo_max_attempts: int = int(os.getenv("METAAPI_DEMO_MAX_ATTEMPTS", "5"))

    # --- Konta demo MT5 z web terminala MetaQuotes (kanał darmowy) ---
    # Zakłada realne konta na serwerze MetaQuotes-Demo, sterując formularzem
    # „Open Demo account" danymi klienta zebranymi przy płatności.
    # Domyślnie WŁĄCZONE — bez tego konta dostają placeholder PropFunding-SIM,
    # który nie istnieje w MetaTraderze (świeży klon repo nie ma pliku .env).
    metaquotes_web_enabled: bool = os.getenv("METAQUOTES_WEB_ENABLED", "true").lower() == "true"
    metaquotes_web_url: str = os.getenv("METAQUOTES_WEB_URL", "https://web.metatrader.app/terminal?mode=demo")
    metaquotes_web_headless: bool = os.getenv("METAQUOTES_WEB_HEADLESS", "true").lower() == "true"
    metaquotes_web_account_type: str = os.getenv("METAQUOTES_WEB_ACCOUNT_TYPE", "Forex Hedged USD")
    metaquotes_web_leverage: int = int(os.getenv("METAQUOTES_WEB_LEVERAGE", "100"))
    # MetaQuotes limituje rejestracje po IP — odstęp między kolejnymi kontami.
    metaquotes_web_min_interval_sec: float = float(os.getenv("METAQUOTES_WEB_MIN_INTERVAL_SEC", "20"))
    metaquotes_web_timeout_sec: int = int(os.getenv("METAQUOTES_WEB_TIMEOUT_SEC", "45"))
    metaquotes_web_screenshot_dir: str = os.getenv("METAQUOTES_WEB_SCREENSHOT_DIR", "")
    # Formularz wymaga telefonu — używane, gdy klient go nie poda.
    metaquotes_web_default_phone: str = os.getenv("METAQUOTES_WEB_DEFAULT_PHONE", "+10000000000")
    # Adres ZDALNEJ przeglądarki (Chrome DevTools Protocol), np. usługa typu
    # Browserless/Browserbase albo własny Chrome z --remote-debugging-port.
    # Ustawiony => Playwright podłącza się do niej zamiast uruchamiać Chromium
    # u siebie. To jedyny sposób, żeby kanał MetaQuotes działał na hostingu
    # bezserwerowym, gdzie przeglądarki wgrać się nie da.
    browser_cdp_url: str = os.getenv("BROWSER_CDP_URL", "")
    # Co ile odswiezac equity konta (logowanie do terminala trwa ~20 s).
    # To jest zarazem OPOZNIENIE wykrycia breachu.
    metaquotes_web_poll_sec: float = float(os.getenv("METAQUOTES_WEB_POLL_SEC", "120"))
    # Ile przegladarek naraz (kazda to osobna sesja terminala).
    metaquotes_web_concurrency: int = int(os.getenv("METAQUOTES_WEB_CONCURRENCY", "2"))

    # --- Dzień handlowy (reset daily loss) ---
    server_utc_offset_hours: int = int(os.getenv("SERVER_UTC_OFFSET_HOURS", "2"))

    # --- Demo seed ---
    auto_seed: bool = os.getenv("AUTO_SEED", "true").lower() == "true"

    # --- Marka / strona publiczna ---
    site_name: str = os.getenv("SITE_NAME", "Pro Traders Funding")
    # Podpis na certyfikatach. Puste = blok podpisu w ogóle się nie pokazuje —
    # nie wymyślamy nazwiska osoby, która miałaby firmować dokument.
    cert_signatory: str = os.getenv("CERT_SIGNATORY", "")
    cert_signatory_label: str = os.getenv("CERT_SIGNATORY_LABEL", "CEO signature:")
    support_email: str = os.getenv("SUPPORT_EMAIL", "support@protradersfunding.com")

    # --- Aplikacja / bezpieczeństwo ---
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-change-me")
    admin_token: str = os.getenv("ADMIN_TOKEN", "admin")     # nagłówek X-Admin-Token
    # Sekret dla crona wyzwalającego POST/GET /api/tick. Puste = endpoint
    # przyjmuje wyłącznie token admina. Vercel Cron sam wysyła ten sekret
    # w nagłówku `Authorization: Bearer …`, gdy CRON_SECRET jest ustawiony.
    cron_secret: str = os.getenv("CRON_SECRET", "")
    # Ile sekund może mieć najświeższy odczyt, zanim wejście na dashboard samo
    # dogoni silnik. 0 = wyłączone (tak jest lokalnie, gdzie kręci się poller).
    # Na hostingu bezserwerowym to JEDYNY sposób, żeby konta żyły — cron Vercela
    # na koncie Hobby chodzi raz na dobę.
    lazy_tick_sec: int = int(os.getenv("LAZY_TICK_SEC", "0"))

    # --- Stripe (brak kluczy => tryb MOCK) ---
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    currency: str = os.getenv("CURRENCY", "usd")

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.stripe_secret_key)

    # --- E-mail (SMTP). Brak hosta => maile na konsolę ---
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_pass: str = os.getenv("SMTP_PASS", "")
    mail_from: str = os.getenv("MAIL_FROM", "no-reply@propfunding.local")

    # --- Webhook powiadomień (np. Make/Telegram) — opcjonalny ---
    notify_webhook_url: str = os.getenv("NOTIFY_WEBHOOK_URL", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
