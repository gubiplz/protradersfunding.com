"""Odznaki liczone z realnych zdarzeń + nagrody za progi 3/8, 5/8 i 8/8.

Odznaki mieszkały dotąd wewnątrz endpointu w `main.py`. Skoro od ich LICZBY
zależy teraz nagroda, lista musi być jedną rzeczą, a nie dwiema — inaczej próg
liczyłby się z czego innego niż to, co widzi trader.

CO JEST NAGRODĄ
---------------
3/8 i 5/8 to własny kod rabatowy (20% i 25%), jednorazowy i wystawiony na tego
tradera — czyli dokładnie ten sam byt, co kod kupowany za punkty lojalnościowe
(`reward_codes`), więc checkout rozpoznaje go bez żadnej dodatkowej ścieżki.
8/8 to darmowy challenge $50k, przyznawany tą samą drogą co grant admina:
zamówienie na $0 z providerem `grant` i realne konto MT5.

JEDNORAZOWOŚĆ
-------------
Pilnuje jej UNIQUE (trader_id, tier) w bazie, nie `if` w kodzie. Dwa równoległe
kliknięcia „Claim" kończą się jednym wierszem i jedną nagrodą.
"""
from __future__ import annotations

from datetime import timedelta

from .models import AchievementReward, Account, Order, Payout, RewardCode, Trader

# Plan przyznawany za komplet odznak. Klucz, nie rozmiar — rozmiar bierze się
# z katalogu, żeby zmiana ceny czy parametrów planu nie wymagała ruszania tego
# pliku.
FREE_PLAN_KEY = "2step-50k"

# Progi: ile odznak -> co za to. `pct` None znaczy „nagrodą jest konto".
TIERS = [
    {"tier": 3, "pct": 20.0, "plan": None,
     "label": "20% off your next challenge"},
    {"tier": 5, "pct": 25.0, "plan": None,
     "label": "25% off your next challenge"},
    {"tier": 8, "pct": None, "plan": FREE_PLAN_KEY,
     "label": "Free $50,000 challenge"},
]

# Ile żyje kod z nagrody. Ten sam termin co przy wymianie punktów — dwa różne
# terminy na dwa kody rabatowe w jednym portalu byłyby pułapką na klienta.
CODE_TTL_DAYS = 90


def badges(session, trader: Trader) -> list[dict]:
    """Odznaki liczone z REALNYCH zdarzeń na platformie — zero generowania."""
    accs = session.query(Account).filter(Account.trader_id == trader.id).all()
    acc_ids = [a.id for a in accs]
    paid_order = session.query(Order).filter(Order.trader_id == trader.id,
                                             Order.status == "paid").first() is not None
    payout = bool(acc_ids) and session.query(Payout).filter(
        Payout.account_id.in_(acc_ids)).first() is not None
    referred = session.query(Trader).filter(Trader.referred_by == trader.referral_code).count()
    # Licznik, nie porównanie z cennikiem: po skalowaniu konto siedzi DOKŁADNIE
    # na rozmiarze swojego nowego planu, więc dawny warunek
    # `initial_balance > Product.account_size` nie odróżniał go już od świeżo
    # kupionego.
    scaled = any((getattr(a, "scale_count", 0) or 0) > 0 for a in accs)
    lista = [
        ("first_challenge", "First Challenge", "Purchase your first evaluation", paid_order),
        ("phase_passed", "Phase Passed", "Advance past Phase 1 of any challenge",
         any(a.phase in ("eval_2", "funded") or a.status in ("passed", "funded") for a in accs)),
        ("funded", "Funded Trader", "Get any account to funded status",
         any(a.status == "funded" for a in accs)),
        ("first_payout", "First Payout", "Receive your first performance reward", payout),
        ("days_5", "Consistent Trader", "Log 5 trading days on one account",
         any(a.trading_days_count >= 5 for a in accs)),
        ("scaled", "Scaled Up", "Move a funded account up to the next plan", scaled),
        ("referrer", "Ambassador", "Refer your first trader", referred >= 1),
        ("kyc", "Verified", "Complete identity verification", trader.kyc_status == "approved"),
    ]
    return [{"key": k, "name": n, "desc": d, "unlocked": bool(u)} for (k, n, d, u) in lista]


def tier_by_number(tier: int) -> dict | None:
    return next((t for t in TIERS if t["tier"] == tier), None)


def claimed_map(session, trader: Trader) -> dict[int, AchievementReward]:
    rows = (session.query(AchievementReward)
            .filter(AchievementReward.trader_id == trader.id).all())
    return {r.tier: r for r in rows}


def state(session, trader: Trader, odblokowane: int) -> list[dict]:
    """Stan trzech nagród dla portalu: locked / ready / claimed."""
    odebrane = claimed_map(session, trader)
    out = []
    for t in TIERS:
        r = odebrane.get(t["tier"])
        wpis = {"tier": t["tier"], "pct": t["pct"], "plan": t["plan"], "label": t["label"],
                "status": "claimed" if r else ("ready" if odblokowane >= t["tier"] else "locked"),
                "remaining": max(0, t["tier"] - odblokowane),
                "code": r.code if r else None,
                "account_id": r.account_id if r else None}
        out.append(wpis)
    return out


def claim(session, trader: Trader, tier: int, odblokowane: int):
    """Odbiera nagrodę za próg. Woła się to POD blokadą wiersza tradera.

    Zwraca `(AchievementReward, dane_konta|None)`. Rzuca `ValueError` przy złym
    progu, `LookupError` gdy odznak jeszcze za mało, `RuntimeError` gdy nagroda
    była już odebrana.
    """
    from . import billing, loyalty  # lokalnie: billing importuje modele, unikamy cyklu

    spec = tier_by_number(tier)
    if spec is None:
        raise ValueError("unknown tier")
    if odblokowane < tier:
        raise LookupError(str(tier - odblokowane))
    if session.query(AchievementReward).filter(
            AchievementReward.trader_id == trader.id,
            AchievementReward.tier == tier).first() is not None:
        raise RuntimeError("already claimed")

    konto = None
    if spec["plan"]:
        wynik = billing.grant_challenge(session, trader, spec["plan"],
                                        f"Achievement reward {tier}/8")
        nagroda = AchievementReward(trader_id=trader.id, tier=tier,
                                    account_id=wynik["account_id"])
        konto = wynik
    else:
        kod = RewardCode(
            trader_id=trader.id, code=loyalty.generate_code(session), pct=spec["pct"],
            # Kod z odznak nie kosztował punktów — zero, a nie cena z cennika
            # nagród, żeby historia w programie lojalnościowym nie kłamała.
            points_spent=0,
            expires_at=loyalty._utcnow() + timedelta(days=CODE_TTL_DAYS),
        )
        session.add(kod)
        nagroda = AchievementReward(trader_id=trader.id, tier=tier, code=kod.code)
    session.add(nagroda)
    return nagroda, konto
