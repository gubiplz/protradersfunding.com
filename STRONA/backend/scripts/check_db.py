"""Diagnostyka połączenia z bazą — zanim uruchomisz serwer na Supabase.

    cd backend
    python scripts/check_db.py                        # bierze DATABASE_URL z .env
    python scripts/check_db.py "postgresql+psycopg://..."   # albo z argumentu

Sprawdza po kolei: czy URL jest poprawny, czy host odpowiada, czy da się
zalogować, czy tabele istnieją i ile jest w nich danych. Przy błędzie mówi
konkretnie, co poprawić — zamiast rzucać stack trace z SQLAlchemy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLES = ["traders", "accounts", "orders", "products", "payouts", "payout_requests",
          "equity_snapshots", "breaches", "journal_entries", "support_tickets",
          "ticket_messages", "pool_accounts"]


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else None
    if url:
        os.environ["DATABASE_URL"] = url

    from app.config import get_settings
    settings = get_settings()
    url = settings.database_url
    safe = url
    if "@" in url and "://" in url:                     # nie logujemy hasła
        head, tail = url.split("://", 1)
        creds, host = tail.split("@", 1)
        user = creds.split(":", 1)[0]
        safe = f"{head}://{user}:***@{host}"
    print(f"DATABASE_URL: {safe}")

    if url.startswith("postgresql") and "+psycopg" not in url:
        print("\n✗ Brakuje sterownika w URL. Zamień 'postgresql://' na 'postgresql+psycopg://'.")
        return 1

    from sqlalchemy import inspect, text

    from app.db import engine, init_db

    is_sqlite = url.startswith("sqlite")
    try:
        with engine.connect() as conn:
            probe = "select sqlite_version()" if is_sqlite else "SELECT version()"
            ver = conn.execute(text(probe)).scalar()
        print(f"✓ połączenie OK — {'SQLite ' + str(ver) if is_sqlite else str(ver).split(',')[0]}")
    except Exception as e:
        msg = str(e)
        print(f"\n✗ nie udało się połączyć:\n  {msg.strip().splitlines()[0][:200]}\n")
        low = msg.lower()
        if "password authentication failed" in low:
            print("  → hasło jest błędne. Supabase: Settings → Database → Reset database password.")
        elif "tenant or user not found" in low or "enotfound" in low:
            print("  → zły host/region albo user. Skopiuj URI z przycisku 'Connect' w dashboardzie.")
        elif "could not translate host name" in low or "nodename nor servname" in low:
            print("  → host nie istnieje (DNS). Sprawdź literówkę w adresie.")
        elif "timeout" in low:
            print("  → brak odpowiedzi. Direct connection bywa IPv6-only — użyj poolera (port 6543).")
        return 1

    init_db()
    have = set(inspect(engine).get_table_names())
    missing = [t for t in TABLES if t not in have]
    print(f"✓ tabele: {len(TABLES) - len(missing)}/{len(TABLES)}" +
          (f"  (brakuje: {', '.join(missing)})" if missing else ""))

    from app.db import SessionLocal
    s = SessionLocal()
    try:
        with engine.connect() as conn:
            for t in ("traders", "accounts", "orders", "products"):
                if t in have:
                    n = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                    print(f"    {t:<10} {n}")
    finally:
        s.close()
    print("\nGotowe — możesz uruchomić: uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
