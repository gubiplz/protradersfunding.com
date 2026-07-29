"""Zakłada (albo aktualizuje) konto administratora w bazie wskazanej DATABASE_URL.

Panel admina otwiera się kontem z flagą `is_admin`, a nie wspólnym tokenem —
ten skrypt jest sposobem na wpuszczenie pierwszej osoby do świeżej bazy, gdzie
`AUTO_SEED` jest wyłączone (tak jest na produkcji).

    python scripts/make_admin.py                 # login "admin", haslo "admin"
    python scripts/make_admin.py biuro@firma.pl 'mocne-haslo'

Konto o podanym loginie dostaje `is_admin=True`; jeśli już istnieje, skrypt
tylko podmienia hasło i podnosi flagę — nie duplikuje wierszy.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Trader  # noqa: E402


def main() -> None:
    login = (sys.argv[1] if len(sys.argv) > 1 else "admin").strip().lower()
    haslo = sys.argv[2] if len(sys.argv) > 2 else "admin"

    init_db()
    s = SessionLocal()
    try:
        tr = s.query(Trader).filter(Trader.email == login).first()
        if tr is None:
            tr = Trader(email=login, full_name="Administrator",
                        referral_code=f"ADM{login[:4].upper()}", kyc_status="approved")
            s.add(tr)
            akcja = "utworzone"
        else:
            akcja = "zaktualizowane"
        tr.password_hash = auth.hash_password(haslo)
        tr.is_admin = True
        s.commit()
        print(f"konto {akcja}: login={login!r}, is_admin=True")
        if haslo == "admin":
            print("UWAGA: haslo 'admin' jest tymczasowe — zmien je przed startem sprzedazy.")
    finally:
        s.close()


if __name__ == "__main__":
    main()
