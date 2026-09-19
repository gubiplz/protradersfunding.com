"""Eksport track recordu kont prowadzonych przez Trade BOT.

Po co osobny moduł: panel pokazuje liczby po to, żeby na nie spojrzeć, a to jest
zrzut do PRZETWARZANIA — arkuszem albo modelem językowym. Stąd dwie rzeczy,
których nigdzie indziej w tym repo nie ma.

**Słownik pól.** Każda kolumna ma tu nazwę, jednostkę i zdanie o tym, co znaczy
i jak została policzona (`SLOWNIK`). Surowa tabela liczb bez tego jest dla
modelu zgadywanką: `pnl` bywa w walucie albo w procentach, `drawdown` liczony
od szczytu albo od salda początkowego, a `win_rate` z transakcji zamkniętych
albo ze wszystkich. Zrzut, który sam się tłumaczy, odpowiada na to z góry.

**Metryki, których backend nie liczył.** Win rate, profit factor, oczekiwana
wartość transakcji, serie i zwroty miesięczne istniały wyłącznie w przeglądarce
(`static/js/portal-app.js`). Tutaj są policzone po stronie serwera, z tych
samych wierszy `trades`, więc zrzut nie zależy od tego, czy ktoś otworzył panel.

Zero nowych zależności: `csv`, `json` i `zipfile` ze standardowej biblioteki.
`pandas` (~50 MB z numpy) wszedłby do bundla Vercela, którego korzeniowy
`requirements.txt` jest celowo chudszy od backendowego — a CSV i tak otwiera
się w Excelu.

Limit czasu funkcji na Vercelu to 60 s (`vercel.json`), więc liczba transakcji
w zrzucie jest ograniczona i obcięcie jest RAPORTOWANE, nie przemilczane.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

from . import rules
from .models import Account, Breach, Certificate, Order, Payout, Trade

# Sufit na konto. 20 000 zamkniętych transakcji to przy najgęstszym trybie bota
# („busy", 20–26 dziennie) ponad dwa lata historii — a i tak mieści się w 60 s.
LIMIT_TRANSAKCJI = 20_000


# --------------------------------------------------------------------------- #
#  Słownik pól — to jest ta „dogłębnie opisana" część                         #
# --------------------------------------------------------------------------- #
SLOWNIK: dict[str, dict[str, str]] = {
    # --- konto ---
    "account_id": {"unit": "—", "desc": "Klucz główny konta w bazie panelu."},
    "login": {"unit": "—", "desc": "Numer konta MT5 pokazywany klientowi."},
    "trader_name": {"unit": "—", "desc": "Imię i nazwisko właściciela konta."},
    "trader_email": {"unit": "—", "desc": "Adres e-mail właściciela konta."},
    "product_key": {"unit": "—", "desc": "Klucz produktu, np. `2step-100k`."},
    "initial_balance": {"unit": "USD", "desc": "Saldo startowe konta. Wszystkie zwroty procentowe liczone są od tej kwoty, nie od salda bieżącego."},
    "phase": {"unit": "—", "desc": "Etap: `eval_1`, `eval_2` albo `funded` (konto opłacane)."},
    "status": {"unit": "—", "desc": "`active`, `funded`, `passed`, `breached` (złamana zasada) albo `closed`."},
    "steps": {"unit": "szt.", "desc": "Liczba etapów ewaluacji: 1 albo 2."},
    "drawdown_type": {"unit": "—", "desc": "`static` — dolny limit stoi na saldzie początkowym; `trailing` — podnosi się za szczytem equity."},
    "max_daily_loss_pct": {"unit": "%", "desc": "Dzienny limit straty, procent salda POCZĄTKOWEGO."},
    "max_overall_loss_pct": {"unit": "%", "desc": "Całkowity limit straty, procent salda początkowego."},
    "profit_target_pct": {"unit": "%", "desc": "Cel zysku obowiązujący w bieżącej fazie."},
    "profit_split_pct": {"unit": "%", "desc": "Udział tradera w zysku przy wypłacie."},
    "consistency_pct": {"unit": "%", "desc": "Reguła najlepszego dnia: 0 = wyłączona; 40 = jeden dzień nie może dać więcej niż 40% zysku."},
    "weekend_trading": {"unit": "0/1", "desc": "Dodatek: handel w weekendy."},
    "mt5_backed": {"unit": "0/1", "desc": "1 = za kontem stoi realny rachunek MT5. 0 = poświadczenia wygenerowane lokalnie."},
    "source": {"unit": "—", "desc": "`purchase` (kupione) albo `grant` (przyznane przez administratora)."},
    "created_at": {"unit": "ISO-8601 UTC", "desc": "Utworzenie konta w panelu."},
    "started_at": {"unit": "ISO-8601 UTC", "desc": "Start handlu na koncie."},
    "closed_at": {"unit": "ISO-8601 UTC", "desc": "Zamknięcie konta, o ile zamknięte."},
    "purchased_at": {"unit": "ISO-8601 UTC", "desc": "Opłacenie zamówienia, z którego powstało konto."},
    "purchase_usd": {"unit": "USD", "desc": "Kwota zapłacona za konto."},

    # --- Trade BOT ---
    "bot_enabled": {"unit": "0/1", "desc": "1 = kontem steruje Trade BOT. To jest kryterium doboru kont do tego zrzutu."},
    "bot_style": {"unit": "—", "desc": "Styl persony bota: `scalper`, `balanced` albo `swing`."},
    "bot_pace": {"unit": "—", "desc": "Tempo: `light` (1–2 transakcje dziennie), `steady` (4–8), `busy` (20–26)."},
    "bot_target_pct": {"unit": "%", "desc": "Cel zysku bota; 0 = bez limitu."},
    "bot_paused": {"unit": "0/1", "desc": "1 = bot nie otwiera nowych pozycji, ale konto zostaje pod jego kontrolą."},
    "bot_started_at": {"unit": "ISO-8601 UTC", "desc": "Uruchomienie bota na tym koncie."},
    "bot_seed": {"unit": "—", "desc": "Ziarno persony. Ta sama wartość daje tę samą charakterystykę handlu — bot jest deterministyczny."},
    "plan_win_rate": {"unit": "0–1", "desc": "ZAMIERZONA skuteczność persony. Porównanie z `win_rate_pct` pokazuje, jak realizacja rozjechała się z planem."},
    "plan_avg_r": {"unit": "R", "desc": "Zamierzony średni stosunek zysku do ryzyka persony."},
    "plan_risk_pct": {"unit": "%", "desc": "Zamierzone ryzyko na transakcję, procent salda."},
    "plan_trades_per_day": {"unit": "szt.", "desc": "Zamierzona liczba transakcji dziennie."},

    # --- stan bieżący ---
    "balance": {"unit": "USD", "desc": "Saldo bez pozycji otwartych."},
    "equity": {"unit": "USD", "desc": "Saldo powiększone o wynik pozycji otwartych."},
    "open_pnl": {"unit": "USD", "desc": "Wynik pozycji otwartych (pływający)."},
    "peak_equity": {"unit": "USD", "desc": "Najwyższe equity w historii konta. Punkt odniesienia dla limitu kroczącego."},
    "trading_days_count": {"unit": "dni", "desc": "Liczba dni, w których wykonano choć jedną transakcję."},
    "best_day_profit": {"unit": "USD", "desc": "Największy zysk w jednym dniu. Podstawa reguły spójności."},
    "daily_loss_used_pct": {"unit": "%", "desc": "Ile z dziennego limitu straty jest już zużyte (100 = limit wyczerpany)."},
    "overall_dd_used_pct": {"unit": "%", "desc": "Ile z całkowitego limitu straty jest zużyte."},
    "breach_reason": {"unit": "—", "desc": "Powód złamania zasady, o ile konto zostało zamknięte."},
    "scale_count": {"unit": "szt.", "desc": "Ile razy konto powiększono planem skalowania."},

    # --- statystyki wyliczone ---
    "trades_total": {"unit": "szt.", "desc": "Wszystkie transakcje, otwarte i zamknięte."},
    "trades_closed": {"unit": "szt.", "desc": "Transakcje zamknięte. WSZYSTKIE statystyki poniżej liczone są wyłącznie z nich."},
    "trades_open": {"unit": "szt.", "desc": "Pozycje otwarte w chwili zrzutu."},
    "wins": {"unit": "szt.", "desc": "Transakcje zamknięte z wynikiem dodatnim (pnl > 0)."},
    "losses": {"unit": "szt.", "desc": "Transakcje zamknięte z wynikiem ujemnym (pnl < 0)."},
    "breakeven": {"unit": "szt.", "desc": "Transakcje zamknięte dokładnie na zero."},
    "win_rate_pct": {"unit": "%", "desc": "wins / trades_closed × 100. Transakcje na zero liczą się do mianownika."},
    "gross_profit": {"unit": "USD", "desc": "Suma wyników dodatnich."},
    "gross_loss": {"unit": "USD", "desc": "Suma wyników ujemnych, jako liczba UJEMNA."},
    "net_pnl": {"unit": "USD", "desc": "gross_profit + gross_loss."},
    "net_pnl_pct": {"unit": "%", "desc": "net_pnl jako procent salda początkowego."},
    "profit_factor": {"unit": "—", "desc": "gross_profit / |gross_loss|. Powyżej 1 = system zyskowny. PUSTE, gdy nie było ani jednej stratnej transakcji (dzielenie przez zero, a nie wynik nieskończenie dobry)."},
    "expectancy": {"unit": "USD", "desc": "net_pnl / trades_closed — ile średnio daje jedna transakcja."},
    "avg_win": {"unit": "USD", "desc": "Średni zysk transakcji zyskownej."},
    "avg_loss": {"unit": "USD", "desc": "Średnia strata transakcji stratnej, jako liczba ujemna."},
    "payoff_ratio": {"unit": "—", "desc": "avg_win / |avg_loss|. Ile wygrana jest warta względem przegranej."},
    "largest_win": {"unit": "USD", "desc": "Najlepsza pojedyncza transakcja."},
    "largest_loss": {"unit": "USD", "desc": "Najgorsza pojedyncza transakcja."},
    "max_consecutive_wins": {"unit": "szt.", "desc": "Najdłuższa seria transakcji zyskownych pod rząd."},
    "max_consecutive_losses": {"unit": "szt.", "desc": "Najdłuższa seria stratnych pod rząd."},
    "total_lots": {"unit": "loty", "desc": "Suma wolumenu transakcji zamkniętych."},
    "avg_lots": {"unit": "loty", "desc": "Średni wolumen transakcji."},
    "avg_duration_min": {"unit": "min", "desc": "Średni czas trzymania pozycji."},
    "max_drawdown_pct": {"unit": "%", "desc": "Największy spadek od szczytu do dołka na krzywej kapitału, procent tego szczytu. Liczony po każdej zamkniętej transakcji; wypłaty są z krzywej WYŁĄCZONE, żeby wypłata nie udawała straty."},
    "max_drawdown_usd": {"unit": "USD", "desc": "Ten sam spadek w dolarach."},
    "green_days": {"unit": "dni", "desc": "Dni handlowe zamknięte na plusie."},
    "red_days": {"unit": "dni", "desc": "Dni handlowe zamknięte na minusie."},
    "best_day": {"unit": "USD", "desc": "Najlepszy dzień w historii konta."},
    "worst_day": {"unit": "USD", "desc": "Najgorszy dzień."},
    "first_trade_at": {"unit": "ISO-8601 UTC", "desc": "Otwarcie pierwszej transakcji."},
    "last_trade_at": {"unit": "ISO-8601 UTC", "desc": "Zamknięcie ostatniej transakcji."},

    # --- wypłaty i zdarzenia ---
    "payouts_count": {"unit": "szt.", "desc": "Liczba wypłat z tego konta."},
    "payouts_profit_usd": {"unit": "USD", "desc": "Suma zysku zdjętego z konta wypłatami."},
    "payouts_trader_usd": {"unit": "USD", "desc": "Suma kwot wypłaconych traderowi (po podziale zysku)."},
    "breaches_count": {"unit": "szt.", "desc": "Liczba złamań zasad."},
    "certificates_count": {"unit": "szt.", "desc": "Liczba wydanych certyfikatów."},

    # --- wiersze w tabelach szczegółowych ---
    "ticket": {"unit": "—", "desc": "Identyfikator transakcji (klucz główny wiersza `trades`)."},
    "symbol": {"unit": "—", "desc": "Instrument, np. `XAUUSD`."},
    "side": {"unit": "—", "desc": "`buy` albo `sell`."},
    "lots": {"unit": "loty", "desc": "Wolumen pozycji."},
    "open_price": {"unit": "cena", "desc": "Cena otwarcia."},
    "close_price": {"unit": "cena", "desc": "Cena zamknięcia; puste dla pozycji otwartej."},
    "pnl": {"unit": "USD", "desc": "Wynik transakcji. Dla pozycji otwartej to wynik pływający."},
    "opened_at": {"unit": "ISO-8601 UTC", "desc": "Moment otwarcia."},
    "closed_at_trade": {"unit": "ISO-8601 UTC", "desc": "Moment zamknięcia; puste dla otwartej."},
    "duration_min": {"unit": "min", "desc": "Czas trzymania pozycji."},
    "cum_pnl": {"unit": "USD", "desc": "Skumulowany wynik po tej transakcji, od początku historii konta."},
    "equity_after": {"unit": "USD", "desc": "initial_balance + cum_pnl. Krzywa kapitału BEZ wypłat."},
    "day": {"unit": "YYYY-MM-DD", "desc": "Dzień handlowy (UTC)."},
    "month": {"unit": "YYYY-MM", "desc": "Miesiąc kalendarzowy (UTC)."},
    "return_pct": {"unit": "%", "desc": "Wynik okresu jako procent salda początkowego — NIE składany."},
    "ts": {"unit": "ISO-8601 UTC", "desc": "Znacznik czasu zdarzenia."},
}


def _iso(d: datetime | None) -> str:
    return d.isoformat() if d else ""


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(float(x), n)


def _statystyki(zamkniete: list[Trade], initial_balance: float) -> dict:
    """Wszystko, co da się policzyć z zamkniętych transakcji.

    Świadomie NIE liczymy R-multiple ani Sharpe'a. Pierwsze wymagałoby stop
    lossa, którego `trades` nie przechowuje; drugie — stopy wolnej od ryzyka
    i szeregu o stałym kroku. Zmyślona metryka jest gorsza niż jej brak, bo
    model policzy na niej wnioski.
    """
    n = len(zamkniete)
    pusto = {k: None for k in (
        "win_rate_pct", "profit_factor", "expectancy", "avg_win", "avg_loss",
        "payoff_ratio", "largest_win", "largest_loss", "avg_duration_min",
        "max_drawdown_pct", "max_drawdown_usd", "best_day", "worst_day",
        "first_trade_at", "last_trade_at")}
    if not n:
        return {"trades_closed": 0, "wins": 0, "losses": 0, "breakeven": 0,
                "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0,
                "net_pnl_pct": 0.0, "max_consecutive_wins": 0,
                "max_consecutive_losses": 0, "total_lots": 0.0, "avg_lots": None,
                "green_days": 0, "red_days": 0, **pusto}

    wyniki = [float(t.pnl or 0.0) for t in zamkniete]
    zyski = [p for p in wyniki if p > 0]
    straty = [p for p in wyniki if p < 0]
    gross_profit, gross_loss = sum(zyski), sum(straty)
    net = gross_profit + gross_loss

    # Serie: liczymy jednym przebiegiem, bo kolejność jest już chronologiczna.
    seria_w = seria_l = max_w = max_l = 0
    for p in wyniki:
        if p > 0:
            seria_w, seria_l = seria_w + 1, 0
        elif p < 0:
            seria_l, seria_w = seria_l + 1, 0
        else:
            seria_w = seria_l = 0
        max_w, max_l = max(max_w, seria_w), max(max_l, seria_l)

    # Obsunięcie liczone na krzywej BEZ wypłat: wypłata zdejmuje zysk z konta,
    # ale nie jest stratą i nie ma prawa udawać obsunięcia.
    szczyt = kapital = initial_balance
    max_dd_usd = max_dd_pct = 0.0
    for p in wyniki:
        kapital += p
        szczyt = max(szczyt, kapital)
        spadek = szczyt - kapital
        if spadek > max_dd_usd:
            max_dd_usd = spadek
            max_dd_pct = spadek / szczyt * 100 if szczyt else 0.0

    po_dniach: dict[str, float] = defaultdict(float)
    for t in zamkniete:
        if t.closed_at:
            po_dniach[t.closed_at.date().isoformat()] += float(t.pnl or 0.0)
    dni = list(po_dniach.values())

    czasy = [(t.closed_at - t.opened_at).total_seconds() / 60.0
             for t in zamkniete if t.closed_at and t.opened_at]
    loty = [float(t.lots or 0.0) for t in zamkniete]

    return {
        "trades_closed": n,
        "wins": len(zyski), "losses": len(straty),
        "breakeven": n - len(zyski) - len(straty),
        "win_rate_pct": _r(len(zyski) / n * 100),
        "gross_profit": _r(gross_profit), "gross_loss": _r(gross_loss),
        "net_pnl": _r(net),
        "net_pnl_pct": _r(net / initial_balance * 100) if initial_balance else None,
        # Puste, a nie „nieskończoność": brak stratnej transakcji to za mało
        # danych na tę miarę, a nie dowód doskonałości.
        "profit_factor": _r(gross_profit / abs(gross_loss), 3) if straty else None,
        "expectancy": _r(net / n),
        "avg_win": _r(gross_profit / len(zyski)) if zyski else None,
        "avg_loss": _r(gross_loss / len(straty)) if straty else None,
        "payoff_ratio": (_r((gross_profit / len(zyski)) / abs(gross_loss / len(straty)), 3)
                         if zyski and straty else None),
        "largest_win": _r(max(wyniki)), "largest_loss": _r(min(wyniki)),
        "max_consecutive_wins": max_w, "max_consecutive_losses": max_l,
        "total_lots": _r(sum(loty)), "avg_lots": _r(sum(loty) / n, 3),
        "avg_duration_min": _r(sum(czasy) / len(czasy), 1) if czasy else None,
        "max_drawdown_usd": _r(max_dd_usd), "max_drawdown_pct": _r(max_dd_pct),
        "green_days": sum(1 for d in dni if d > 0),
        "red_days": sum(1 for d in dni if d < 0),
        "best_day": _r(max(dni)) if dni else None,
        "worst_day": _r(min(dni)) if dni else None,
        "first_trade_at": _iso(zamkniete[0].opened_at),
        "last_trade_at": _iso(zamkniete[-1].closed_at),
    }


def _persona(acc: Account) -> dict:
    """ZAMIERZONA charakterystyka persony bota, jeśli da się ją odtworzyć.

    Cenne obok zrealizowanych liczb: różnica między planem a wynikiem jest
    właśnie tym, co się analizuje. Import lokalny, bo `tradebot` ciągnie za sobą
    swoje zależności, a eksport ma działać także wtedy, gdy bot jest wyłączony.
    """
    try:
        from . import tradebot
        p = tradebot.persona_for(acc)
    except Exception:
        return {}
    return {"plan_win_rate": _r(getattr(p, "win_rate", None), 4),
            "plan_avg_r": _r(getattr(p, "avg_r", None), 3),
            "plan_risk_pct": _r(getattr(p, "risk_pct", None), 3),
            "plan_trades_per_day": getattr(p, "trades_per_day", None)}


def dane_konta(session, acc: Account, *, limit: int = LIMIT_TRANSAKCJI) -> dict:
    """Komplet danych jednego konta: konfiguracja, stan, statystyki i szczegóły."""
    transakcje = (session.query(Trade).filter(Trade.account_id == acc.id)
                  .order_by(Trade.opened_at, Trade.id).limit(limit + 1).all())
    obciete = len(transakcje) > limit
    transakcje = transakcje[:limit]

    zamkniete = sorted([t for t in transakcje if t.status == "closed" and t.closed_at],
                       key=lambda t: t.closed_at)
    otwarte = [t for t in transakcje if t.status != "closed"]

    wyplaty = (session.query(Payout).filter(Payout.account_id == acc.id)
               .order_by(Payout.ts).all())
    zlamania = (session.query(Breach).filter(Breach.account_id == acc.id)
                .order_by(Breach.ts).all())
    certyfikaty = (session.query(Certificate).filter(Certificate.account_id == acc.id)
                   .order_by(Certificate.issued_at).all())
    zamowienie = (session.query(Order)
                  .filter(Order.account_id == acc.id, Order.paid_at.isnot(None))
                  .order_by(Order.paid_at.desc()).first())

    initial = float(acc.initial_balance or 0.0)
    stat = _statystyki(zamkniete, initial)

    # Reguły z bieżącej fazy — liczone tą samą funkcją, którą widzi klient
    # w portalu, żeby zrzut i panel nie mogły się rozjechać.
    try:
        cfg = rules.config_from_account(acc)
        metryki = rules.display_metrics(
            cfg, balance=float(acc.balance or 0.0), equity=float(acc.equity or 0.0),
            peak_equity=float(acc.peak_equity or 0.0),
            day_start_equity=float(acc.day_start_equity or 0.0),
            trading_days=int(acc.trading_days_count or 0))
    except Exception:
        metryki = {}

    podsumowanie = {
        "account_id": acc.id, "login": acc.login or "",
        "trader_name": acc.trader_name or "",
        "trader_email": (acc.trader.email if acc.trader else ""),
        "product_key": acc.product_key or "", "initial_balance": initial,
        "phase": acc.phase or "", "status": acc.status or "",
        "steps": acc.steps, "drawdown_type": acc.drawdown_type or "",
        "max_daily_loss_pct": acc.max_daily_loss_pct,
        "max_overall_loss_pct": acc.max_overall_loss_pct,
        "profit_target_pct": metryki.get("profit_target_pct"),
        "profit_split_pct": acc.profit_split_pct,
        "consistency_pct": getattr(acc, "consistency_pct", 0.0),
        "weekend_trading": int(bool(getattr(acc, "weekend_trading", False))),
        "mt5_backed": int(bool(getattr(acc, "mt5_backed", True))),
        "source": getattr(acc, "source", "") or "",
        "created_at": _iso(acc.created_at), "started_at": _iso(acc.started_at),
        "closed_at": _iso(acc.closed_at),
        "purchased_at": _iso(zamowienie.paid_at) if zamowienie else "",
        "purchase_usd": _r(zamowienie.amount_usd) if zamowienie else None,

        "bot_enabled": int(bool(acc.bot_enabled)),
        "bot_style": acc.bot_style or "", "bot_pace": acc.bot_pace or "",
        "bot_target_pct": acc.bot_target_pct,
        "bot_paused": int(bool(acc.bot_paused)),
        "bot_started_at": _iso(acc.bot_started_at), "bot_seed": acc.bot_seed,
        **_persona(acc),

        "balance": _r(acc.balance), "equity": _r(acc.equity),
        "open_pnl": _r(acc.open_pnl), "peak_equity": _r(acc.peak_equity),
        "trading_days_count": acc.trading_days_count,
        "best_day_profit": _r(acc.best_day_profit),
        "daily_loss_used_pct": metryki.get("daily_loss_used_pct"),
        "overall_dd_used_pct": metryki.get("overall_dd_used_pct"),
        "breach_reason": acc.breach_reason or "",
        "scale_count": getattr(acc, "scale_count", 0),

        "trades_total": len(transakcje), "trades_open": len(otwarte),
        **stat,

        "payouts_count": len(wyplaty),
        "payouts_profit_usd": _r(sum(float(p.profit_amount or 0) for p in wyplaty)),
        "payouts_trader_usd": _r(sum(float(p.trader_share or 0) for p in wyplaty)),
        "breaches_count": len(zlamania),
        "certificates_count": len(certyfikaty),
        "trades_truncated": obciete,
    }

    # Szczegóły transakcji + krzywa kapitału w jednym przebiegu.
    kum = 0.0
    wiersze = []
    for t in transakcje:
        zamkn = t.status == "closed" and t.closed_at
        if zamkn:
            kum += float(t.pnl or 0.0)
        trwanie = ((t.closed_at - t.opened_at).total_seconds() / 60.0
                   if t.closed_at and t.opened_at else None)
        wiersze.append({
            "account_id": acc.id, "login": acc.login or "", "ticket": t.id,
            "symbol": t.symbol or "", "side": t.side or "",
            "lots": _r(t.lots, 3), "open_price": _r(t.open_price, 5),
            "close_price": _r(t.close_price, 5) if t.close_price is not None else None,
            "pnl": _r(t.pnl), "status": t.status or "",
            "opened_at": _iso(t.opened_at), "closed_at_trade": _iso(t.closed_at),
            "duration_min": _r(trwanie, 1),
            "cum_pnl": _r(kum) if zamkn else None,
            "equity_after": _r(initial + kum) if zamkn else None,
        })

    po_dniach: dict[str, dict] = {}
    po_miesiacach: dict[str, dict] = {}
    for t in zamkniete:
        dzien = t.closed_at.date().isoformat()
        miesiac = dzien[:7]
        for klucz, tabela in ((dzien, po_dniach), (miesiac, po_miesiacach)):
            w = tabela.setdefault(klucz, {"pnl": 0.0, "trades": 0, "wins": 0})
            w["pnl"] += float(t.pnl or 0.0)
            w["trades"] += 1
            w["wins"] += 1 if float(t.pnl or 0.0) > 0 else 0

    def _okresy(tabela: dict, nazwa: str) -> list[dict]:
        out = []
        for klucz in sorted(tabela):
            w = tabela[klucz]
            out.append({
                "account_id": acc.id, "login": acc.login or "", nazwa: klucz,
                "pnl": _r(w["pnl"]), "trades": w["trades"], "wins": w["wins"],
                "win_rate_pct": _r(w["wins"] / w["trades"] * 100) if w["trades"] else None,
                "return_pct": _r(w["pnl"] / initial * 100) if initial else None,
            })
        return out

    return {
        "summary": podsumowanie,
        "trades": wiersze,
        "daily": _okresy(po_dniach, "day"),
        "monthly": _okresy(po_miesiacach, "month"),
        "payouts": [{"account_id": acc.id, "login": acc.login or "",
                     "ts": _iso(p.ts), "profit_amount": _r(p.profit_amount),
                     "trader_share": _r(p.trader_share), "paid": int(bool(p.paid)),
                     "method": p.method or "", "cert_token": p.cert_token or "",
                     "balance_reset": int(bool(p.balance_reset))} for p in wyplaty],
        "breaches": [{"account_id": acc.id, "login": acc.login or "",
                      "ts": _iso(b.ts), "type": b.type or "",
                      "detail": b.detail or "",
                      "equity_at_breach": _r(b.equity_at_breach)} for b in zlamania],
        "certificates": [{"account_id": acc.id, "login": acc.login or "",
                          "kind": c.kind or "", "cert_token": c.cert_token or "",
                          "issued_at": _iso(c.issued_at)} for c in certyfikaty],
    }


def zbierz(session, *, account_id: int | None = None,
           limit: int = LIMIT_TRANSAKCJI) -> dict:
    """Zrzut wszystkich kont prowadzonych przez bota (albo jednego wskazanego).

    Kryterium jest jedno i wprost z modelu: `Account.bot_enabled`. Konta, na
    których bota nigdy nie było, nie mają track recordu bota do pokazania.
    """
    q = session.query(Account).filter(Account.bot_enabled.is_(True))
    if account_id is not None:
        q = q.filter(Account.id == account_id)
    konta = q.order_by(Account.id).all()

    zrzuty = [dane_konta(session, a, limit=limit) for a in konta]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Pro Traders Funding — panel administracyjny",
        "criterion": "Account.bot_enabled = true (konta prowadzone przez Trade BOT)",
        "accounts_count": len(zrzuty),
        "trade_limit_per_account": limit,
        "notes": [
            "Wszystkie kwoty w USD, wszystkie znaczniki czasu w UTC (ISO-8601).",
            "Statystyki liczone WYŁĄCZNIE z transakcji zamkniętych; pozycje otwarte "
            "są w tabeli `trades` ze statusem `open` i pustym `closed_at_trade`.",
            "Zwroty procentowe liczone od salda początkowego i NIE są składane.",
            "`profit_factor` jest puste, gdy konto nie miało ani jednej stratnej "
            "transakcji — to brak danych do tej miary, nie wynik doskonały.",
            "R-multiple i Sharpe celowo pominięte: pierwsze wymaga stop lossa, "
            "którego system nie zapisuje, drugie stopy wolnej od ryzyka.",
            "Pola `plan_*` to ZAMIERZONA charakterystyka persony bota, nie wynik.",
            "`trades_truncated=true` oznacza, że konto ma więcej transakcji niż "
            "limit i tabela `trades` jest dla niego niepełna.",
        ],
        "dictionary": SLOWNIK,
        "accounts": zrzuty,
    }


# --------------------------------------------------------------------------- #
#  Formaty wyjściowe                                                          #
# --------------------------------------------------------------------------- #
def do_json(dane: dict) -> bytes:
    return json.dumps(dane, ensure_ascii=False, indent=2).encode("utf-8")


def _csv(wiersze: list[dict]) -> str:
    """CSV z wierszy-słowników. Kolumny z pierwszego wiersza, reszta dopasowana.

    `utf-8-sig` na wyjściu ZIP-a, bo Excel bez BOM-u czyta polskie znaki jako
    krzaki — a ten plik ma się otwierać dwuklikiem, nie przez kreator importu.
    """
    if not wiersze:
        return ""
    kolumny: list[str] = []
    for w in wiersze:
        for k in w:
            if k not in kolumny:
                kolumny.append(k)
    buf = io.StringIO()
    pisarz = csv.DictWriter(buf, fieldnames=kolumny, extrasaction="ignore",
                            lineterminator="\n")
    pisarz.writeheader()
    for w in wiersze:
        pisarz.writerow({k: ("" if w.get(k) is None else w.get(k)) for k in kolumny})
    return buf.getvalue()


def do_csv(dane: dict) -> bytes:
    """Sama tabela kont, jeden wiersz na konto. BOM, żeby Excel nie zrobił krzaków."""
    return _csv([k["summary"] for k in dane["accounts"]]).encode("utf-8-sig")


def _slownik_md(dane: dict) -> str:
    """Opis zrzutu po ludzku — pierwszy plik, który ktoś (albo model) otworzy."""
    linie = ["# Track record kont prowadzonych przez Trade BOT", "",
             f"Wygenerowano: {dane['generated_at']}",
             f"Źródło: {dane['source']}",
             f"Kryterium doboru kont: {dane['criterion']}",
             f"Kont w zrzucie: {dane['accounts_count']}", "",
             "## Jak czytać ten zrzut", ""]
    linie += [f"* {n}" for n in dane["notes"]]
    linie += ["", "## Pliki", "",
              "| plik | zawartość |",
              "|---|---|",
              "| `accounts.csv` | jeden wiersz na konto: konfiguracja, stan i wszystkie statystyki |",
              "| `trades.csv` | jeden wiersz na transakcję, ze skumulowanym wynikiem i krzywą kapitału |",
              "| `daily.csv` | wynik per dzień handlowy |",
              "| `monthly.csv` | wynik per miesiąc |",
              "| `payouts.csv` | wypłaty z kont |",
              "| `breaches.csv` | złamania zasad |",
              "| `certificates.csv` | wydane certyfikaty |",
              "| `track-record.json` | wszystko powyżej w jednym pliku, razem ze słownikiem |",
              "", "## Słownik pól", "",
              "| pole | jednostka | znaczenie |", "|---|---|---|"]
    for pole in sorted(dane["dictionary"]):
        opis = dane["dictionary"][pole]
        linie.append(f"| `{pole}` | {opis['unit']} | {opis['desc']} |")
    return "\n".join(linie) + "\n"


def do_zip(dane: dict) -> bytes:
    """Paczka: CSV-ki do Excela + JSON dla modelu + opis, jak to czytać."""
    sekcje = {
        "accounts.csv": [k["summary"] for k in dane["accounts"]],
        "trades.csv": [w for k in dane["accounts"] for w in k["trades"]],
        "daily.csv": [w for k in dane["accounts"] for w in k["daily"]],
        "monthly.csv": [w for k in dane["accounts"] for w in k["monthly"]],
        "payouts.csv": [w for k in dane["accounts"] for w in k["payouts"]],
        "breaches.csv": [w for k in dane["accounts"] for w in k["breaches"]],
        "certificates.csv": [w for k in dane["accounts"] for w in k["certificates"]],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("CZYTAJ-TO-NAJPIERW.md", _slownik_md(dane).encode("utf-8"))
        for nazwa, wiersze in sekcje.items():
            z.writestr(nazwa, _csv(wiersze).encode("utf-8-sig"))
        z.writestr("track-record.json", do_json(dane))
    return buf.getvalue()
