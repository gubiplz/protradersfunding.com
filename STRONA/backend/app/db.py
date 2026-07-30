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
    _relax_not_null()


# Kolumny dodane po tym, jak ktoś już miał bazę. `create_all` NIE robi ALTER-ów,
# więc bez tego stara baza wywala się na SELECT-cie z nowym polem.
_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "traders": {
        "first_name": "VARCHAR(60)",
        "last_name": "VARCHAR(60)",
        "phone": "VARCHAR(32)",
        "kyc_status": "VARCHAR(16) DEFAULT 'none'",
        "kyc_fullname": "VARCHAR(120)",
        "kyc_country": "VARCHAR(64)",
        "kyc_doc_ref": "VARCHAR(120)",
        "kyc_submitted_at": "TIMESTAMP",
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
        "credits_usd": "FLOAT DEFAULT 0",
        "checkin_streak": "INTEGER DEFAULT 0",
        "checkin_last": "VARCHAR(10)",
        "bonus_points": "INTEGER DEFAULT 0",
        "reveal_last": "VARCHAR(10)",
        "reveal_payload": "VARCHAR(240)",
        "streak_freezes": "INTEGER DEFAULT 1",
        "email_verified": "BOOLEAN DEFAULT TRUE",
        "email_verify_code": "VARCHAR(6)",
        "terms_accepted_at": "TIMESTAMP",
    },
    "products": {
        "max_lots": "FLOAT DEFAULT 6.0",
    },
    "orders": {
        "bogo_paid_key": "VARCHAR(48)",
        "weekend_trading": "BOOLEAN DEFAULT FALSE",
        "credits_used": "FLOAT DEFAULT 0",
        "flag": "VARCHAR(24)",
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
    },
}


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
