"""Import historycznych wypłat z pliku CSV (wariant z linii poleceń).

Silnik siedzi w `app/payout_import.py` i jest ten sam, co za przyciskiem
"Import history" w panelu admina (Payouts) — ten skrypt przydaje się przy
większych plikach i przy imporcie na bazę, do której nie ma się panelu.

    python scripts/import_payouts.py wyplaty.csv            # podgląd, nic nie zapisuje
    python scripts/import_payouts.py wyplaty.csv --commit   # zapis do bazy

Format CSV, zasady dotyczące certyfikatów i to, co skrypt zakłada w bazie,
opisuje docstring modułu `app/payout_import.py`. W skrócie: wypłaty wjeżdżają
jako rekordy wewnętrzne, BEZ publicznych certyfikatów — te wystawia się osobno
w panelu, pod potwierdzoną wypłatę.

Baza bierze się z DATABASE_URL. Skrypt jest idempotentny: ta sama osoba, kwota
i dzień nie zostaną dodane drugi raz.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app import payout_import  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402


def main() -> None:
    argumenty = [a for a in sys.argv[1:] if not a.startswith("-")]
    zapis = "--commit" in sys.argv
    if not argumenty:
        print(__doc__)
        sys.exit(1)

    sciezka = Path(argumenty[0]).expanduser()
    if not sciezka.exists():
        print(f"Nie ma pliku: {sciezka}")
        sys.exit(1)

    init_db()
    session = SessionLocal()
    try:
        wynik = payout_import.uruchom(session, sciezka.read_text(encoding="utf-8-sig"),
                                      commit=zapis)
        if not wynik["ok"]:
            print("Plik do poprawy:")
            for b in wynik["errors"]:
                print(f"  ✗ {b}")
            sys.exit(1)

        print(f"\n{'ZAPIS' if zapis else 'PODGLĄD (bez --commit nic nie trafia do bazy)'}"
              f" — {len(wynik['rows'])} wypłat z {sciezka.name}\n")
        for w in wynik["rows"]:
            znak = "=" if w["duplicate"] else "+"
            ogon = "już w bazie — pomijam" if w["duplicate"] else (
                f"{w['program']} ${w['account_size']:,.0f}  "
                f"(split {w['split_pct']:.0f}%, zysk ${w['profit_amount']:,.2f})")
            print(f"  {znak} {w['full_name']:<18} ${w['amount_usd']:>9,.2f}  {w['date']}  {ogon}")

        if zapis:
            print(f"\nZapisano: {wynik['added']} wypłat, pominięto {wynik['skipped']} duplikatów.")
            print("Widoczne w panelu admina (Payouts). Na landingu ich nie ma —")
            print("pas 'Recently issued' pokazuje wyłącznie wypłaty z certyfikatem.")
        else:
            print(f"\nDo dodania: {wynik['added']}, duplikatów do pominięcia: {wynik['skipped']}.")
            print("Uruchom ponownie z --commit, żeby zapisać.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
