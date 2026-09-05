"""Warstwa bazy — SQLAlchemy. Działa zarówno na SQLite (lokalnie, 0 zł)
jak i na Postgres/Supabase (produkcja) — wystarczy zmienić DATABASE_URL."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

def _connect_args_for(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    if url.startswith("postgresql"):
        # Supabase/PgBouncer w trybie TRANSACTION nie obsługuje prepared statements:
        # psycopg3 domyślnie je tworzy, przez co co kilka zapytań leci
        # `prepared statement "_pg3_x" does not exist` i zapis przepada.
        return {"prepare_threshold": None}
    return {}


_connect_args = _connect_args_for(settings.database_url)
engine = create_engine(settings.database_url, connect_args=_connect_args,
                       pool_pre_ping=True, pool_recycle=300, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


if settings.database_url.startswith("sqlite"):
    # SQLite domyślnie IGNORUJE klucze obce, Postgres ich pilnuje. Przez tę różnicę
    # kasowanie konta przechodziło lokalnie, a na Supabase leciało 500
    # (ForeignKeyViolation na orders.account_id). Włączamy egzekwowanie, żeby testy
    # zachowywały się jak produkcja.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_wymusz_klucze_obce(dbapi_conn, _rec):  # pragma: no cover - hook sterownika
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  (rejestracja tabel)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _poszerz_kolumny()
    _add_missing_indexes()
    _relax_not_null()
    _przemianuj_statusy_leadow()
    _odbierz_konta_google()
    _uzupelnij_zgubione_claimy()


# Odcisk wersji kodu, dla ktorej schemat jest juz doprowadzony do porzadku.
# Siedzi w `app_settings` (istniejaca tabela klucz-wartosc), zeby nie zakladac
# osobnej tabeli tylko na jeden wiersz.
_KLUCZ_SCHEMATU = "schema_fingerprint"


def schema_fingerprint() -> str | None:
    """Wersja kodu, dla której schemat i cennik są już zsynchronizowane.

    Jeden round-trip; brak tabeli (świeża baza) to nie błąd, tylko odpowiedź
    „jeszcze nic nie zrobiono".
    """
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                               {"k": _KLUCZ_SCHEMATU}).first()
        return row[0] if row else None
    except Exception:
        return None


def mark_schema_current(fingerprint: str) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": _KLUCZ_SCHEMATU})
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"),
                     {"k": _KLUCZ_SCHEMATU, "v": fingerprint})


# Kolumny dodane po tym, jak ktoś już miał bazę. `create_all` NIE robi ALTER-ów,
# więc bez tego stara baza wywala się na SELECT-cie z nowym polem.
_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "traders": {
        # FALSE dla wszystkich, którzy już są w bazie — hasło albo znają, albo
        # przejdą zwykłym „forgot password". Flaga dotyczy kont zakładanych ZA
        # klienta i tylko takie mają dostać w mailu link do ustawienia hasła.
        "must_set_password": "BOOLEAN DEFAULT FALSE",
        "first_name": "VARCHAR(60)",
        "last_name": "VARCHAR(60)",
        "phone": "VARCHAR(32)",
        "phone_country": "VARCHAR(2)",
        "kyc_status": "VARCHAR(16) DEFAULT 'none'",
        "kyc_fullname": "VARCHAR(120)",
        "kyc_country": "VARCHAR(64)",
        "kyc_doc_ref": "VARCHAR(120)",
        "kyc_requested_at": "TIMESTAMP",
        "kyc_locked": "BOOLEAN DEFAULT FALSE",
        "kyc_submitted_at": "TIMESTAMP",
        "kyc_reject_reason": "VARCHAR(200)",
        "kyc_dob": "VARCHAR(16)",
        "kyc_address": "VARCHAR(200)",
        "kyc_id_type": "VARCHAR(32)",
        "kyc_id_number": "VARCHAR(64)",
        "kyc_doc_front": "VARCHAR(80)",
        "kyc_doc_back": "VARCHAR(80)",
        "kyc_doc_residence": "VARCHAR(80)",
        "kyc_reviewed_at": "TIMESTAMP",
        "notify_updates": "BOOLEAN DEFAULT TRUE",
        "notify_trading": "BOOLEAN DEFAULT TRUE",
        "notify_payouts": "BOOLEAN DEFAULT TRUE",
        "notify_marketing": "BOOLEAN DEFAULT TRUE",
        "ui_prefs": "VARCHAR(2000)",
        "credits_usd": "FLOAT DEFAULT 0",
        "checkin_streak": "INTEGER DEFAULT 0",
        "checkin_last": "VARCHAR(10)",
        "bonus_points": "INTEGER DEFAULT 0",
        "points_spent": "INTEGER DEFAULT 0",
        "reveal_last": "VARCHAR(10)",
        "reveal_payload": "VARCHAR(240)",
        "streak_freezes": "INTEGER DEFAULT 1",
        "email_verified": "BOOLEAN DEFAULT TRUE",
        "email_verify_code": "VARCHAR(6)",
        "terms_accepted_at": "TIMESTAMP",
        "google_sub": "VARCHAR(64)",
        "telegram_user_id": "VARCHAR(20)",
        "telegram_link_code": "VARCHAR(12)",
        "telegram_username": "VARCHAR(40)",
    },
    "products": {
        "max_lots": "FLOAT DEFAULT 6.0",
    },
    "orders": {
        "bogo_paid_key": "VARCHAR(48)",
        "bogo": "BOOLEAN DEFAULT FALSE",
        "weekend_trading": "BOOLEAN DEFAULT FALSE",
        "credits_used": "FLOAT DEFAULT 0",
        "flag": "VARCHAR(24)",
        "fail_reason": "VARCHAR(200)",
        "recovery_sent_at": "TIMESTAMP",
        "payment_address": "VARCHAR(200)",
        "payment_network": "VARCHAR(40)",
        "pay_token": "VARCHAR(32)",
        "addon_split_boost": "BOOLEAN DEFAULT FALSE",
        "addon_express_payout": "BOOLEAN DEFAULT FALSE",
        # Tabele `flash_offers` zaklada create_all, ale `orders` stoi na
        # produkcji — bez tego wpisu kazdy SELECT z modelu Order pada na
        # brakujacej kolumnie.
        "flash_offer_id": "INTEGER",
        "brand": "VARCHAR(8)",
        "open_funded": "BOOLEAN DEFAULT FALSE",
        "weekend_free": "BOOLEAN DEFAULT FALSE",
        "pay_headline": "VARCHAR(80)",
    },
    "pool_accounts": {
        "claimed_by_trader_id": "INTEGER",
        "claimed_at": "TIMESTAMP",
        "retired_reason": "VARCHAR(60)",
        "simulated": "BOOLEAN DEFAULT FALSE",
    },
    "payout_requests": {
        "details": "VARCHAR(400)",
        "reject_reason": "VARCHAR(200)",
    },
    "payouts": {
        "method": "VARCHAR(40)",
        "note": "VARCHAR(160)",
        "cert_token": "VARCHAR(32)",
        "balance_reset": "BOOLEAN DEFAULT TRUE",
        # DEFAULT TRUE robi za backfill: wypłaty wystawione przed tą zmianą
        # zostają na pasie na landingu, nic nie znika ze strony po deployu.
        "show_on_lp": "BOOLEAN DEFAULT TRUE",
    },
    "accounts": {
        "platform_investor_password": "VARCHAR(64)",
        "cert_token": "VARCHAR(32)",
        "source": "VARCHAR(16) DEFAULT 'purchase'",
        "grant_note": "VARCHAR(160)",
        "max_lots": "FLOAT DEFAULT 6.0",
        "bogo_paid_size": "FLOAT",
        "weekend_trading": "BOOLEAN DEFAULT FALSE",
        "mt5_backed": "BOOLEAN DEFAULT TRUE",
        "bot_enabled": "BOOLEAN DEFAULT FALSE",
        "bot_seed": "INTEGER",
        "bot_style": "VARCHAR(16)",
        "bot_pace": "VARCHAR(16)",
        "bot_target_pct": "FLOAT DEFAULT 0",
        "bot_paused": "BOOLEAN DEFAULT FALSE",
        "bot_started_at": "TIMESTAMP",
        "bot_mode": "VARCHAR(16) DEFAULT 'profit'",
        "bot_doom_deadline": "TIMESTAMP",
        "bot_doom_limit": "VARCHAR(16) DEFAULT 'overall'",
        "scale_count": "INTEGER DEFAULT 0",
        "express_payout": "BOOLEAN DEFAULT FALSE",
        "limit_warn_daily_day": "VARCHAR(10) DEFAULT ''",
        "limit_warn_dd_day": "VARCHAR(10) DEFAULT ''",
    },
    # Karta leada na kanale. Tabela `leads` stoi na produkcji od pierwszego
    # zgloszenia, wiec `create_all` ja pomija — bez tych wpisow kazdy SELECT
    # z modelu Lead pyta o kolumny, ktorych w bazie nie ma, i panel oddaje
    # 500 na samej liscie leadow.
    "leads": {
        "owner": "VARCHAR(60)",
        "owner_at": "TIMESTAMP",
        "tg_message_id": "INTEGER",
        # Bez indeksu: filtr zawsze wtorny do tg_message_id, ktory indeks ma.
        "tg_chat_id": "VARCHAR(32)",
        "bought": "BOOLEAN DEFAULT FALSE",
        # NULL u wszystkich, ktorzy juz sa w bazie, i tak zostanie: powodu
        # przegranej nie da sie zgadnac wstecz, a wpisanie tam czegokolwiek
        # ("other") zafalszowaloby pierwszy raport o cala historie.
        "lost_reason": "VARCHAR(24)",
    },
}


# Indeksy dolozone po tym, jak ktos juz mial baze. Kazdy ma za soba zmierzony
# pelny skan tabeli (EXPLAIN QUERY PLAN: "SCAN"), a nie przeczucie:
#   traders.referred_by  — /api/auth/me liczy poleconych i prowizje, dwa skany
#                          tabeli traderow przy kazdym wejsciu do portalu,
#   orders.account_id    — zamowienia konta czytane w petli po kontach.
# Kolumny statusowe (orders.status, accounts.status, traders.kyc_status) tu NIE
# trafiaja: maja po 3-4 rozne wartosci, wiec planista i tak woli skan.
_NEW_INDEXES: list[tuple[str, str]] = [
    ("traders", "referred_by"),
    ("orders", "account_id"),
    # Odpowiedz na kanale niesie wylacznie numer wiadomosci, wiec kazda notatka
    # z Telegrama szuka leada po tej kolumnie.
    ("leads", "tg_message_id"),
    # Kazdy klik przycisku na kanale LEADS szuka admina po id konta Telegram.
    ("traders", "telegram_user_id"),
]


# Kolumny, ktore PRZESTALY byc wymagane. `create_all` nie rusza istniejacych
# tabel, wiec stara baza dalej ma na nich NOT NULL i INSERT bez tego pola leci
# bledem — dokladnie tak wysypalo sie dodawanie wpisu do puli MT5 po tym, jak
# admin przestal podawac metaapi_account_id. SQLite pomijamy: nie ma tam ALTER
# COLUMN, a te bazy i tak powstaja od zera z modeli.
_RELAXED_COLUMNS: dict[str, list[str]] = {
    "pool_accounts": ["metaapi_account_id"],
}


def _relax_not_null() -> None:
    from sqlalchemy import inspect, text

    if not engine.dialect.name.startswith("postgres"):
        return
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _RELAXED_COLUMNS.items():
            if table not in existing_tables:
                continue
            nullable = {c["name"]: c["nullable"] for c in inspector.get_columns(table)}
            for name in columns:
                if nullable.get(name) is False:
                    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {name} DROP NOT NULL"))
                    print(f"[db] {table}.{name} nie jest już wymagane")


def _add_missing_indexes() -> None:
    """`create_all` NIE dodaje indeksow do tabeli, ktora juz istnieje.

    Doklejenie `index=True` w modelu zalatwia sprawe tylko dla swiezych baz —
    produkcja stoi od miesiecy, wiec bez tego jedyna baza, na ktorej naprawde
    zalezy, nigdy by tych indeksow nie zobaczyla. Nazwy sa te same, co
    generowane przez SQLAlchemy, wiec swieza baza dostaje je z `create_all`
    i to `IF NOT EXISTS` po prostu nic nie robi.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, column in _NEW_INDEXES:
        if table not in existing_tables:
            continue
        nazwa = f"ix_{table}_{column}"
        if any(i["name"] == nazwa for i in inspector.get_indexes(table)):
            continue
        with engine.begin() as conn:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {nazwa} ON {table} ({column})"))
        print(f"[db] dodano indeks {nazwa}")


# Statusy z czasow, gdy dzial dzwonil zamiast pisac na Telegramie. Wartosci w
# bazie zmienily sie razem z przyciskami, ale wiersze zapisane wczesniej trzymaja
# stare — a tych panel nie ma juz na liscie, wiec pokazywalby lead ze statusem,
# ktorego nie da sie ani odczytac, ani zmienic. Przepisanie, nie kasowanie:
# „odebral" to dzisiejsze „odpisal", „nie odbiera" to „nie odpisuje".
#
# `lead_events` zostaje nietkniete. Historia ma mowic, co kliknieto wtedy.
_STARE_STATUSY_LEADA = {"called": "replied", "no_answer": "no_reply"}


def _przemianuj_statusy_leadow() -> None:
    from sqlalchemy import inspect, text

    if "leads" not in set(inspect(engine).get_table_names()):
        return
    with engine.begin() as conn:
        for stary, nowy in _STARE_STATUSY_LEADA.items():
            zmienione = conn.execute(
                text("UPDATE leads SET status = :nowy WHERE status = :stary"),
                {"nowy": nowy, "stary": stary}).rowcount
            if zmienione:
                print(f"[db] leads.status {stary} -> {nowy}: {zmienione}")


def _odbierz_konta_google() -> None:
    """Klienci z kontem założonym ZA nich, którzy weszli przez Google, zanim
    logowanie Google liczyło się jako odbiór konta — flaga wisiała mimo że
    klient normalnie korzystał z portalu. `google_sub` ustawia wyłącznie
    ścieżka Google, więc para (must_set_password, google_sub) jednoznacznie
    wskazuje ofiary. Claim w dzienniku dostaje datę pierwszego wejścia przez
    Google, nie datę naprawy."""
    from datetime import datetime

    from sqlalchemy import inspect, text

    if not {"traders", "telemetry_events"} <= set(inspect(engine).get_table_names()):
        return
    with engine.begin() as conn:
        ofiary = conn.execute(text(
            "SELECT id FROM traders "
            "WHERE must_set_password AND google_sub IS NOT NULL")).scalars().all()
        for tid in ofiary:
            kiedy = conn.execute(text(
                "SELECT MIN(created_at) FROM telemetry_events "
                "WHERE trader_id = :t AND name IN ('login', 'signup') "
                "AND props LIKE '%\"google\": true%'"), {"t": tid}).scalar()
            conn.execute(text(
                "INSERT INTO telemetry_events (trader_id, name, props, created_at) "
                "VALUES (:t, 'account_claimed', :p, :c)"),
                {"t": tid, "p": '{"google": true, "backfill": true}',
                 "c": kiedy or datetime.utcnow()})
            conn.execute(text(
                "UPDATE traders SET must_set_password = :f WHERE id = :t"),
                {"f": False, "t": tid})
            print(f"[db] trader {tid}: konto odebrane wstecznie (Google)")


# Zdarzenia, których nie da się wywołać bez zalogowania — ich obecność dowodzi,
# że klient wszedł do portalu, choćby wiersz o samym wejściu przepadł.
_SLADY_ZALOGOWANEGO = "('view_open', 'checkin', 'push_subscribed', 'pwa_install')"


def _uzupelnij_zgubione_claimy() -> None:
    """telemetry.track() z zasady nie wywala żądania biznesowego — gdy zapis
    padnie (np. w oknie deployu), klient odbiera konto, a dziennik do końca
    świata twierdzi „never". Zgaszona flaga must_set_password bez śladu
    login/signup/claim, ale z aktywnością wymagającą zalogowania, oznacza
    właśnie zgubiony wiersz. Cezura 2026-08-11 (narodziny must_set_password):
    starsze konta bywały aktywne, zanim logowania trafiały do telemetrii,
    więc pasowałyby do wzorca niewinnie. Claim dostaje datę pierwszego śladu."""
    from sqlalchemy import inspect, text

    if not {"traders", "telemetry_events"} <= set(inspect(engine).get_table_names()):
        return
    with engine.begin() as conn:
        ofiary = conn.execute(text(
            "SELECT id FROM traders "
            "WHERE NOT must_set_password AND created_at >= :cezura "
            "AND id NOT IN (SELECT trader_id FROM telemetry_events "
            "               WHERE name IN ('login', 'signup', 'account_claimed')) "
            "AND id IN (SELECT trader_id FROM telemetry_events "
            f"              WHERE name IN {_SLADY_ZALOGOWANEGO})"),
            {"cezura": "2026-08-11"}).scalars().all()
        for tid in ofiary:
            kiedy = conn.execute(text(
                "SELECT MIN(created_at) FROM telemetry_events "
                f"WHERE trader_id = :t AND name IN {_SLADY_ZALOGOWANEGO}"),
                {"t": tid}).scalar()
            conn.execute(text(
                "INSERT INTO telemetry_events (trader_id, name, props, created_at) "
                "VALUES (:t, 'account_claimed', :p, :c)"),
                {"t": tid, "p": '{"inferred": true, "backfill": true}', "c": kiedy})
            print(f"[db] trader {tid}: claim odtworzony z aktywności portalu")


# Kolumny, którym za ciasno w pierwotnym VARCHAR. Poszerzenie w Postgresie jest
# operacją na katalogu, nie przepisaniem tabeli, więc nie blokuje startu appki.
# SQLite i tak nie egzekwuje długości, więc tam ten krok jest pomijany.
_SZERSZE_KOLUMNY: dict[str, dict[str, str]] = {
    # Lista kanałów Reach BOT-a siedzi tu jako JSON — przy trzecim kanale
    # przekraczała 200 znaków i zapis leciał 500-tką.
    "app_settings": {"value": "TEXT"},
}


def _poszerz_kolumny() -> None:
    from sqlalchemy import inspect, text

    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    istniejace = set(inspector.get_table_names())
    with engine.begin() as conn:
        for tabela, kolumny in _SZERSZE_KOLUMNY.items():
            if tabela not in istniejace:
                continue
            obecne = {c["name"]: c for c in inspector.get_columns(tabela)}
            for nazwa, typ in kolumny.items():
                kol = obecne.get(nazwa)
                # Już TEXT (albo kolumny nie ma) = nic do roboty.
                if not kol or getattr(kol["type"], "length", None) is None:
                    continue
                conn.execute(text(
                    f"ALTER TABLE {tabela} ALTER COLUMN {nazwa} TYPE {typ}"))
                print(f"[db] poszerzono kolumnę {tabela}.{nazwa} do {typ}")


def _add_missing_columns() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            if table not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl_type in columns.items():
                if name in have:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
                print(f"[db] dodano kolumnę {table}.{name}")
