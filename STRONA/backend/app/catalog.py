"""Katalog produktów (planów challenge'a), kupony i parametry afiliacji.

Rozmiary i ceny wzorowane na FTMO / Alpha Capital Group (2026): 5k–200k,
modele 1-step i 2-step (bez darmowego triala). Seed trafia do tabeli `products`,
więc admin może je później edytować w bazie.
"""
from __future__ import annotations

from .models import Product

# Prowizja afiliacyjna (% od opłaconego zamówienia poleconego tradera)
AFFILIATE_COMMISSION_PCT = 10.0

# Kupony rabatowe: kod -> % zniżki
COUPONS: dict[str, float] = {
    "WELCOME10": 10.0,
    "BLACKFRIDAY": 30.0,
    "VIP20": 20.0,
}

# Limit ŁĄCZNEJ ekspozycji (suma wolumenu wszystkich otwartych pozycji).
# Bez niego trader stawia cały depozyt na jedną świecę i „przechodzi” challenge
# rzutem monetą — a firma płaci za szczęście, nie za umiejętność.
# Skala: 6 lotów na 100k, minimum 1 lot (poniżej nie da się sensownie handlować).
def max_lots_for(account_size: float) -> float:
    return max(1.0, round(account_size / 100_000 * 6 * 2) / 2)   # do 0.5 lota


# (key, label, size, steps, price, p1, p2, daily, maxdd, dd_type, min_days, split)
_CATALOG = [
    # --- 2-STEP (klasyczna ewaluacja) ---
    ("2step-10k",  "2-Step 10k",  10_000,  2, 89,  8, 5, 5, 10, "static",   3, 80),
    ("2step-25k",  "2-Step 25k",  25_000,  2, 189, 8, 5, 5, 10, "static",   4, 80),
    ("2step-50k",  "2-Step 50k",  50_000,  2, 289, 8, 5, 5, 10, "static",   4, 80),
    ("2step-100k", "2-Step 100k", 100_000, 2, 549, 8, 5, 5, 10, "static",   4, 80),
    ("2step-200k", "2-Step 200k", 200_000, 2, 999, 8, 5, 5, 10, "static",   4, 80),
    # --- 1-STEP (szybsza ścieżka, trailing DD) ---
    ("1step-10k",  "1-Step 10k",  10_000,  1, 97,  10, 0, 5, 6,  "trailing", 3, 90),
    ("1step-50k",  "1-Step 50k",  50_000,  1, 297, 10, 0, 5, 6,  "trailing", 3, 90),
    ("1step-100k", "1-Step 100k", 100_000, 1, 577, 10, 0, 5, 6,  "trailing", 3, 90),
    # --- INSTANT FUNDING (bez ewaluacji: konto od razu funded) ---
    # steps=0 => brak celu zysku i faz; zarabiasz od pierwszego dnia, ale
    # limity ryzyka są ciaśniejsze, a podział zysku niższy niż po ewaluacji.
    ("instant-10k",  "Instant 10k",  10_000,  0, 249,  0, 0, 4, 6, "static", 3, 70),
    ("instant-25k",  "Instant 25k",  25_000,  0, 499,  0, 0, 4, 6, "static", 3, 70),
    ("instant-50k",  "Instant 50k",  50_000,  0, 799,  0, 0, 4, 6, "static", 3, 70),
    ("instant-100k", "Instant 100k", 100_000, 0, 1499, 0, 0, 4, 6, "static", 3, 70),
]


def seed_products(session) -> None:
    """Synchronizuje katalog w bazie z `_CATALOG` (źródłem prawdy w kodzie).

    Zasada: dodajemy brakujące plany, wycofujemy usunięte z oferty i aktualizujemy
    REGUŁY RYZYKA (limit wolumenu). Cen i nazw NIE nadpisujemy — te admin może
    zmieniać w bazie i zmiany mają przetrwać restart.
    """
    catalog_keys = {row[0] for row in _CATALOG}
    changed = False

    # 1. Wycofane z oferty (free trial, 5k) — znikają ze sklepu, ale zostają w bazie
    #    dla kont, które już je kupiły.
    retired = (session.query(Product)
               .filter(Product.active == True, Product.key.notin_(catalog_keys))  # noqa: E712
               .update({Product.active: False}, synchronize_session=False))
    if retired:
        changed = True
        print(f"[seed] wycofano {retired} planów spoza katalogu")

    existing = {p.key: p for p in session.query(Product).all()}

    # 2. Limit wolumenu to reguła ryzyka — zawsze zgodna z rozmiarem konta.
    for prod in existing.values():
        want = max_lots_for(prod.account_size)
        if not getattr(prod, "max_lots", None) or abs(prod.max_lots - want) > 1e-9:
            prod.max_lots = want
            changed = True

    # 3. Nowe plany z katalogu
    added = 0
    for (key, label, size, steps, price, p1, p2, daily, maxdd, dd, mind, split) in _CATALOG:
        if key in existing:
            if not existing[key].active:
                existing[key].active = True
                changed = True
            continue
        session.add(Product(
            key=key, label=label, account_size=size, steps=steps, price_usd=price,
            profit_target_p1=p1, profit_target_p2=p2, max_daily_loss_pct=daily,
            max_overall_loss_pct=maxdd, drawdown_type=dd, min_trading_days=mind,
            profit_split_pct=split, max_lots=max_lots_for(size), active=True,
        ))
        added += 1
    if added:
        print(f"[seed] dodano {added} nowych planów")
    if changed or added:
        session.commit()
    return



def apply_coupon(price: float, coupon: str | None) -> tuple[float, float]:
    """Zwraca (cena_po_rabacie, procent_zniżki)."""
    if not coupon:
        return price, 0.0
    pct = COUPONS.get(coupon.strip().upper())
    if not pct:
        return price, 0.0
    return round(price * (1 - pct / 100.0), 2), pct
