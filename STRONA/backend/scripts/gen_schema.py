"""Generuje supabase/schema.sql z modeli SQLAlchemy (dialekt PostgreSQL).

Dzięki temu schemat dla Supabase nie rozjeżdża się z kodem — po każdej zmianie
w `app/models.py` wystarczy:

    python scripts/gen_schema.py

Uwaga: aplikacja i tak potrafi utworzyć tabele sama (`init_db` → `create_all`),
ten plik jest dla osób, które wolą wkleić SQL w edytorze Supabase.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # katalog backend/
os.environ.setdefault("DATABASE_URL", "sqlite:///./_schema_dummy.db")

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app import models  # noqa: E402,F401  (rejestruje tabele)
from app.db import Base  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "supabase" / "schema.sql"

HEAD = """-- Schemat produkcyjny (Supabase / PostgreSQL).
-- WYGENEROWANY AUTOMATYCZNIE z backend/app/models.py — nie edytuj ręcznie.
-- Odświeżenie:  cd backend && python scripts/gen_schema.py
--
-- Aplikacja tworzy te tabele sama przy starcie (init_db), więc ten plik jest
-- opcjonalny: przydaje się, gdy chcesz założyć schemat z edytora SQL Supabase
-- albo przejrzeć strukturę bez uruchamiania backendu.

"""


def main() -> None:
    parts = [HEAD]
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect())).strip()
        # create_all używa IF NOT EXISTS; robimy tak samo, żeby skrypt był idempotentny
        ddl = ddl.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
        parts.append(ddl + ";\n")
        for index in table.indexes:
            idx = str(CreateIndex(index).compile(dialect=postgresql.dialect())).strip()
            parts.append(idx.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                            .replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1) + ";\n")
        parts.append("")
    OUT.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    print(f"zapisano {OUT} ({len(Base.metadata.sorted_tables)} tabel)")


if __name__ == "__main__":
    main()
