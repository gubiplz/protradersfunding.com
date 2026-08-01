"""Import historycznych wypłat z własnej ewidencji (notes, arkusz, wyciąg).

Po co: wypłaty rozliczone poza panelem (przelewem, przed wdrożeniem panelu)
nigdzie w systemie nie istnieją, więc sumy w panelu admina kłamią, a przy
kolejnej wypłacie trzeba pamiętać, ile klient już dostał. Ten skrypt przenosi
taką ewidencję do bazy: zakłada tradera, konto funded i wiersz wypłaty.

CO ROBI, A CZEGO NIE
--------------------
Wypłaty lądują jako REKORDY WEWNĘTRZNE — widać je w panelu admina, w historii
konta i w sumach wypłat. NIE dostają `cert_token`, więc:

  * nie generuje się publiczny certyfikat pod /payout/{token},
  * wpis nie trafia na pas "Recently issued" na landingu — endpoint
    /api/public/certificates/recent filtruje `cert_token != NULL`.

To celowe: certyfikat jest dokumentem weryfikowalnym publicznie i wystawia się
go dopiero pod potwierdzoną wypłatę. Gdy masz potwierdzenie przelewu, klikasz
w panelu admina "wystaw certyfikat" przy danej wypłacie (POST
/api/admin/payouts/{id}/certificate) i wpis sam pojawia się na landingu.

FORMAT CSV
----------
    full_name,amount_usd,date,account_size,program,email,note

  * `program`    — `2step` (domyślne) albo `instant`
  * `date`       — YYYY-MM-DD, data wypłaty
  * `email`      — opcjonalny; bez niego powstaje adres techniczny
                   `imie.nazwisko@imported.local` (klient się nim NIE zaloguje,
                   hasło jest losowe — do logowania trzeba resetu hasła)
  * `note`       — opcjonalny dopisek widoczny przy wypłacie w panelu

UŻYCIE
------
    python scripts/import_payouts.py wyplaty.csv            # podgląd, nic nie zapisuje
    python scripts/import_payouts.py wyplaty.csv --commit   # zapis do bazy

Baza bierze się z DATABASE_URL — na produkcji uruchamiaj z produkcyjnym URL-em.
Skrypt jest idempotentny: wypłata o tym samym nazwisku, kwocie i dacie nie
zostanie dodana drugi raz, więc ponowne uruchomienie niczego nie zdubluje.
"""
from __future__ import annotations

import csv
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app import auth, catalog  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Account, Payout, Product, Trader  # noqa: E402

PROGRAMY = {"2step": 2, "instant": 0}


def _blad(wiersz: int, tekst: str) -> None:
    print(f"  ✗ wiersz {wiersz}: {tekst}")


def _wczytaj(sciezka: Path) -> list[dict]:
    with sciezka.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]


def _email_techniczny(nazwa: str) -> str:
    czesci = [c for c in nazwa.lower().replace(".", " ").split() if c]
    return (".".join(czesci) or "trader") + "@imported.local"


def _nastepny_login(session) -> int:
    """Kolejny wolny login MT5 — kontynuacja istniejącej numeracji."""
    najwyzszy = 700000
    for (login,) in session.query(Account.login).all():
        if login and login.isdigit():
            najwyzszy = max(najwyzszy, int(login))
    return najwyzszy + 1


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
        catalog.seed_products(session)
        wiersze = _wczytaj(sciezka)
        login_seq = _nastepny_login(session)
        plan: list[tuple] = []
        bledy = 0

        for nr, r in enumerate(wiersze, start=2):        # 1 to nagłówek
            nazwa = (r.get("full_name") or "").strip()
            if not nazwa:
                _blad(nr, "brak full_name")
                bledy += 1
                continue
            try:
                kwota = round(float(str(r.get("amount_usd", "")).replace(",", "").replace("$", "")), 2)
            except ValueError:
                _blad(nr, f"kwota nie jest liczbą: {r.get('amount_usd')!r}")
                bledy += 1
                continue
            if kwota <= 0:
                _blad(nr, "kwota musi być większa od zera")
                bledy += 1
                continue
            try:
                data = datetime.strptime((r.get("date") or "").strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                _blad(nr, f"data musi być w formacie YYYY-MM-DD, jest {r.get('date')!r}")
                bledy += 1
                continue
            try:
                rozmiar = float(str(r.get("account_size", "")).replace(",", "").replace("$", ""))
            except ValueError:
                _blad(nr, f"account_size nie jest liczbą: {r.get('account_size')!r}")
                bledy += 1
                continue

            program = (r.get("program") or "2step").strip().lower()
            if program not in PROGRAMY:
                _blad(nr, f"program musi być '2step' albo 'instant', jest {program!r}")
                bledy += 1
                continue

            prod = (session.query(Product)
                    .filter(Product.account_size == rozmiar, Product.steps == PROGRAMY[program])
                    .first())
            if prod is None:
                dostepne = sorted({int(p.account_size) for p in session.query(Product)
                                   .filter(Product.steps == PROGRAMY[program]).all()})
                _blad(nr, f"nie ma planu {program} na ${rozmiar:,.0f}; dostępne: "
                          + ", ".join(f"${d:,}" for d in dostepne))
                bledy += 1
                continue

            email = (r.get("email") or "").strip().lower() or _email_techniczny(nazwa)
            plan.append((nr, nazwa, email, kwota, data, prod, (r.get("note") or "").strip() or None))

        if bledy:
            print(f"\n{bledy} wiersz(y) do poprawy — nic nie zapisano.")
            sys.exit(1)

        print(f"\n{'ZAPIS' if zapis else 'PODGLĄD (bez --commit nic nie trafia do bazy)'}"
              f" — {len(plan)} wypłat z {sciezka.name}\n")
        dodane = pominiete = 0

        for nr, nazwa, email, kwota, data, prod, notatka in plan:
            trader = session.query(Trader).filter(Trader.email == email).first()
            konto = None
            if trader is not None:
                # Duplikat rozpoznajemy po nazwisku + kwocie + dacie, więc ponowne
                # uruchomienie skryptu na tym samym pliku nic nie zdubluje.
                istnieje = (session.query(Payout)
                            .join(Account, Payout.account_id == Account.id)
                            .filter(Account.trader_id == trader.id,
                                    Payout.trader_share == kwota)
                            .all())
                if any(p.ts and p.ts.date() == data.date() for p in istnieje):
                    print(f"  = {nazwa:<18} ${kwota:>9,.2f}  {data:%Y-%m-%d}  już w bazie — pomijam")
                    pominiete += 1
                    continue
                konto = (session.query(Account)
                         .filter(Account.trader_id == trader.id,
                                 Account.initial_balance == prod.account_size,
                                 Account.steps == prod.steps)
                         .first())

            podzial = prod.profit_split_pct or 90.0
            zysk = round(kwota * 100.0 / podzial, 2)
            etykieta = "2-Step" if prod.steps == 2 else "Instant Funding"
            print(f"  + {nazwa:<18} ${kwota:>9,.2f}  {data:%Y-%m-%d}  "
                  f"{etykieta} ${prod.account_size:,.0f}  (split {podzial:.0f}%, zysk ${zysk:,.2f})")
            dodane += 1
            if not zapis:
                continue

            if trader is None:
                trader = Trader(
                    email=email,
                    # Hasła nie znamy i nie wymyślamy — klient wchodzi przez reset hasła.
                    password_hash=auth.hash_password(secrets.token_urlsafe(24)),
                    full_name=nazwa, referral_code=secrets.token_hex(4).upper(),
                    kyc_status="approved", created_at=data,
                )
                session.add(trader)
                session.flush()

            if konto is None:
                konto = Account(
                    login=str(login_seq), trader_name=nazwa, trader_id=trader.id,
                    platform_login=str(login_seq), platform_server="PropFunding-ARCHIVE",
                    # Za tym kontem nie stoi żywy rachunek MT5 — feed musi je pominąć,
                    # inaczej próbowałby logować się loginem, którego u brokera nie ma.
                    mt5_backed=False,
                    source="grant", grant_note="imported from records",
                    product_key=prod.key, preset=prod.key,
                    initial_balance=prod.account_size, steps=prod.steps,
                    profit_target_p1=prod.profit_target_p1, profit_target_p2=prod.profit_target_p2,
                    max_daily_loss_pct=prod.max_daily_loss_pct,
                    max_overall_loss_pct=prod.max_overall_loss_pct,
                    min_trading_days=prod.min_trading_days, drawdown_type=prod.drawdown_type,
                    profit_split_pct=podzial, max_lots=prod.max_lots,
                    phase="funded", status="funded",
                    # Stan po wypłacie: zysk zszedł z konta, saldo wraca do bazowego.
                    balance=prod.account_size, equity=prod.account_size,
                    peak_equity=prod.account_size, day_start_equity=prod.account_size,
                    day_start_balance=prod.account_size, day_key=data.strftime("%Y-%m-%d"),
                    created_at=data, started_at=data,
                )
                session.add(konto)
                session.flush()
                login_seq += 1

            session.add(Payout(
                account_id=konto.id, ts=data, profit_amount=zysk, trader_share=kwota,
                paid=True, method="bank transfer",
                note=notatka or "imported from records",
                # BEZ cert_tokenu: certyfikat publiczny wystawia się osobno, pod
                # potwierdzoną wypłatę (panel admina -> wypłata -> certyfikat).
                cert_token=None, balance_reset=False,
            ))

        if zapis:
            session.commit()
            print(f"\nZapisano: {dodane} wypłat, pominięto {pominiete} duplikatów.")
            print("Widoczne w panelu admina (Payouts). Na landingu ich nie ma —")
            print("pas 'Recently issued' pokazuje wyłącznie wypłaty z certyfikatem.")
        else:
            print(f"\nDo dodania: {dodane}, duplikatów do pominięcia: {pominiete}.")
            print("Uruchom ponownie z --commit, żeby zapisać.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
