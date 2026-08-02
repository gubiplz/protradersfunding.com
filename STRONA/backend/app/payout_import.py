"""Import historycznych wypłat z własnej ewidencji — silnik wspólny dla CLI i panelu.

Wypłaty rozliczone poza panelem (przelewem, przed jego wdrożeniem) nie istnieją
nigdzie w systemie, więc sumy w panelu nie zgadzają się z rzeczywistością, a przy
kolejnej wypłacie trzeba pamiętać, ile klient już dostał. Tutaj wjeżdżają jako
zwykłe wiersze `payouts` na koncie funded.

CO POWSTAJE, A CZEGO NIE
------------------------
Wypłaty są REKORDAMI WEWNĘTRZNYMI — widać je w panelu admina, w historii konta
i w sumach. NIE dostają `cert_token`, więc nie ma publicznego certyfikatu pod
/payout/{token}, a wpis nie trafia na pas "Recently issued" na landingu
(/api/public/certificates/recent filtruje `cert_token != NULL`).

Certyfikat jest dokumentem weryfikowalnym publicznie i wystawia się go pod
potwierdzoną wypłatę — osobnym kliknięciem w panelu (Payouts → Generate).

FORMAT CSV
----------
    full_name,amount_usd,date,account_size,program,email,note

`program` to `2step` (domyślne) albo `instant`, `date` w formacie YYYY-MM-DD.
Bez `email` powstaje adres techniczny `imie.nazwisko@imported.local` z losowym
hasłem — klient wchodzi na konto dopiero po resecie hasła.
"""
from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime, timezone

from . import auth, catalog
from .models import Account, Payout, Product, Trader

PROGRAMY = {"2step": 2, "instant": 0}
KOLUMNY = ("full_name", "amount_usd", "date", "account_size", "program", "email", "note")


def _liczba(wartosc: str) -> float:
    return float(str(wartosc or "").replace(",", "").replace("$", "").strip())


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


def parsuj(session, tekst: str) -> tuple[list[dict], list[str]]:
    """CSV -> (wiersze gotowe do zapisu, błędy). Nic nie zapisuje."""
    wiersze: list[dict] = []
    bledy: list[str] = []
    czytnik = csv.DictReader(io.StringIO((tekst or "").strip()))
    if not czytnik.fieldnames or "full_name" not in czytnik.fieldnames:
        return [], [f"Pierwsza linia musi być nagłówkiem: {','.join(KOLUMNY)}"]

    for nr, r in enumerate(czytnik, start=2):        # 1 to nagłówek
        if not any((v or "").strip() for v in r.values()):
            continue
        nazwa = (r.get("full_name") or "").strip()
        if not nazwa:
            bledy.append(f"wiersz {nr}: brak full_name")
            continue
        try:
            kwota = round(_liczba(r.get("amount_usd")), 2)
        except ValueError:
            bledy.append(f"wiersz {nr}: kwota nie jest liczbą ({r.get('amount_usd')!r})")
            continue
        if kwota <= 0:
            bledy.append(f"wiersz {nr}: kwota musi być większa od zera")
            continue
        try:
            data = datetime.strptime((r.get("date") or "").strip(), "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
        except ValueError:
            bledy.append(f"wiersz {nr}: data musi być w formacie YYYY-MM-DD "
                         f"({r.get('date')!r})")
            continue
        try:
            rozmiar = _liczba(r.get("account_size"))
        except ValueError:
            bledy.append(f"wiersz {nr}: account_size nie jest liczbą "
                         f"({r.get('account_size')!r})")
            continue

        program = (r.get("program") or "2step").strip().lower() or "2step"
        if program not in PROGRAMY:
            bledy.append(f"wiersz {nr}: program to '2step' albo 'instant' ({program!r})")
            continue

        prod = (session.query(Product)
                .filter(Product.account_size == rozmiar, Product.steps == PROGRAMY[program])
                .first())
        if prod is None:
            dostepne = sorted({int(p.account_size) for p in session.query(Product)
                               .filter(Product.steps == PROGRAMY[program]).all()})
            bledy.append(f"wiersz {nr}: nie ma planu {program} na ${rozmiar:,.0f}; "
                         "dostępne: " + ", ".join(f"${d:,}" for d in dostepne))
            continue

        podzial = prod.profit_split_pct or 90.0
        wiersze.append({
            "line": nr, "full_name": nazwa,
            "email": (r.get("email") or "").strip().lower() or _email_techniczny(nazwa),
            "amount_usd": kwota, "date": data, "product_key": prod.key,
            "account_size": prod.account_size, "steps": prod.steps,
            "program": "2-Step" if prod.steps == 2 else "Instant Funding",
            "split_pct": podzial, "profit_amount": round(kwota * 100.0 / podzial, 2),
            "note": (r.get("note") or "").strip() or None,
        })
    return wiersze, bledy


def _duplikat(session, trader: Trader | None, kwota: float, data) -> bool:
    """Ta sama osoba, kwota i dzień = wiersz już zaimportowany."""
    if trader is None:
        return False
    istnieje = (session.query(Payout)
                .join(Account, Payout.account_id == Account.id)
                .filter(Account.trader_id == trader.id, Payout.trader_share == kwota)
                .all())
    return any(p.ts and p.ts.date() == data.date() for p in istnieje)


def uruchom(session, tekst: str, commit: bool = False) -> dict:
    """Import CSV. Bez `commit` tylko podgląd — baza zostaje nietknięta."""
    catalog.seed_products(session)
    wiersze, bledy = parsuj(session, tekst)
    if bledy:
        return {"ok": False, "errors": bledy, "rows": [], "added": 0, "skipped": 0,
                "committed": False}

    login_seq = _nastepny_login(session)
    podglad, dodane, pominiete = [], 0, 0

    for w in wiersze:
        trader = session.query(Trader).filter(Trader.email == w["email"]).first()
        duplikat = _duplikat(session, trader, w["amount_usd"], w["date"])
        podglad.append({**{k: v for k, v in w.items() if k != "date"},
                        "date": w["date"].strftime("%Y-%m-%d"), "duplicate": duplikat})
        if duplikat:
            pominiete += 1
            continue
        dodane += 1
        if not commit:
            continue

        if trader is None:
            trader = Trader(
                email=w["email"],
                # Hasła nie znamy i nie wymyślamy — klient wchodzi przez reset hasła.
                password_hash=auth.hash_password(secrets.token_urlsafe(24)),
                full_name=w["full_name"], referral_code=secrets.token_hex(4).upper(),
                kyc_status="approved", created_at=w["date"],
            )
            session.add(trader)
            session.flush()

        prod = session.query(Product).filter(Product.key == w["product_key"]).first()
        konto = (session.query(Account)
                 .filter(Account.trader_id == trader.id,
                         Account.initial_balance == w["account_size"],
                         Account.steps == w["steps"])
                 .first())
        if konto is None:
            konto = Account(
                login=str(login_seq), trader_name=w["full_name"], trader_id=trader.id,
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
                profit_split_pct=w["split_pct"], max_lots=prod.max_lots,
                phase="funded", status="funded",
                # Stan po wypłacie: zysk zszedł z konta, saldo wraca do bazowego.
                balance=prod.account_size, equity=prod.account_size,
                peak_equity=prod.account_size, day_start_equity=prod.account_size,
                day_start_balance=prod.account_size, day_key=w["date"].strftime("%Y-%m-%d"),
                created_at=w["date"], started_at=w["date"],
            )
            session.add(konto)
            session.flush()
            login_seq += 1

        session.add(Payout(
            account_id=konto.id, ts=w["date"], profit_amount=w["profit_amount"],
            trader_share=w["amount_usd"], paid=True, method="bank transfer",
            note=w["note"] or "imported from records",
            # BEZ cert_tokenu: certyfikat publiczny wystawia się osobno, pod
            # potwierdzoną wypłatę (Payouts -> Generate).
            cert_token=None, balance_reset=False,
        ))

    if commit:
        session.commit()
    return {"ok": True, "errors": [], "rows": podglad, "added": dodane,
            "skipped": pominiete, "committed": bool(commit)}
