"""Katalog produktów (planów challenge'a), kupony i parametry afiliacji.

Rozmiary i ceny wzorowane na FTMO / Alpha Capital Group (2026): 10k–2M,
modele 2-Step i Instant Funding (bez darmowego triala). Seed trafia do tabeli
`products`, więc admin może je później edytować w bazie.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from .config import get_settings
from .models import Product

settings = get_settings()

# Prowizja afiliacyjna (% od opłaconego zamówienia poleconego tradera)
AFFILIATE_COMMISSION_PCT = 10.0

# Kupony rabatowe: kod -> % zniżki
COUPONS: dict[str, float] = {
    "WELCOME10": 10.0,
    "BLACKFRIDAY": 30.0,
    "VIP20": 20.0,
    # nagrody z daily reveal (POST /api/me/daily-reveal) — osobiste i czasowe,
    # billing.create_checkout wymaga aktywnego losowania u KONKRETNEGO tradera
    "LUCKY10": 10.0,
    "LUCKY15": 15.0,
}

LUCKY_CODES = {"LUCKY10", "LUCKY15"}

# Limit ŁĄCZNEJ ekspozycji (suma wolumenu wszystkich otwartych pozycji).
# Bez niego trader stawia cały depozyt na jedną świecę i „przechodzi” challenge
# rzutem monetą — a firma płaci za szczęście, nie za umiejętność.
# Skala: 6 lotów na 100k, minimum 1 lot (poniżej nie da się sensownie handlować).
def max_lots_for(account_size: float) -> float:
    return max(1.0, round(account_size / 100_000 * 6 * 2) / 2)   # do 0.5 lota


# Cena add-onu Weekend Trading (2 dodatkowe dni handlu w tygodniu) — jedna
# kwota niezależnie od rozmiaru konta.
WEEKEND_ADDON_USD = 199.0

# (key, label, size, steps, price, p1, p2, daily, maxdd, dd_type, min_days, split)
# Oferta 2026-07-29: dwa modele (2-Step i Instant Funding), rozmiary 10k–2M.
_CATALOG = [
    # --- 2-STEP (klasyczna ewaluacja: P1 10% / P2 5%, DD 5/10, split do 90%) ---
    ("2step-10k",  "2-Step 10K",  10_000,    2, 99,   10, 5, 5, 10, "static", 5, 90),
    ("2step-25k",  "2-Step 25K",  25_000,    2, 249,  10, 5, 5, 10, "static", 5, 90),
    ("2step-50k",  "2-Step 50K",  50_000,    2, 349,  10, 5, 5, 10, "static", 5, 90),
    ("2step-100k", "2-Step 100K", 100_000,   2, 549,  10, 5, 5, 10, "static", 5, 90),
    ("2step-200k", "2-Step 200K", 200_000,   2, 1049, 10, 5, 5, 10, "static", 5, 90),
    ("2step-300k", "2-Step 300K", 300_000,   2, 1499, 10, 5, 5, 10, "static", 5, 90),
    ("2step-400k", "2-Step 400K", 400_000,   2, 1999, 10, 5, 5, 10, "static", 5, 90),
    ("2step-800k", "2-Step 800K", 800_000,   2, 2999, 10, 5, 5, 10, "static", 5, 90),
    ("2step-1m",   "2-Step 1M",   1_000_000, 2, 3499, 10, 5, 5, 10, "static", 5, 90),
    ("2step-2m",   "2-Step 2M",   2_000_000, 2, 5999, 10, 5, 5, 10, "static", 5, 90),
    # --- INSTANT FUNDING (bez ewaluacji: od razu funded; DD 5/8, split 70%,
    #     min. 30 dni handlu przed pierwszą wypłatą) ---
    ("instant-10k",  "Instant 10K",  10_000,    0, 119,  0, 0, 5, 8, "static", 30, 70),
    ("instant-25k",  "Instant 25K",  25_000,    0, 309,  0, 0, 5, 8, "static", 30, 70),
    ("instant-50k",  "Instant 50K",  50_000,    0, 439,  0, 0, 5, 8, "static", 30, 70),
    ("instant-100k", "Instant 100K", 100_000,   0, 689,  0, 0, 5, 8, "static", 30, 70),
    ("instant-200k", "Instant 200K", 200_000,   0, 1309, 0, 0, 5, 8, "static", 30, 70),
    ("instant-300k", "Instant 300K", 300_000,   0, 1869, 0, 0, 5, 8, "static", 30, 70),
    ("instant-400k", "Instant 400K", 400_000,   0, 2499, 0, 0, 5, 8, "static", 30, 70),
    ("instant-800k", "Instant 800K", 800_000,   0, 3749, 0, 0, 5, 8, "static", 30, 70),
    ("instant-1m",   "Instant 1M",   1_000_000, 0, 4369, 0, 0, 5, 8, "static", 30, 70),
    ("instant-2m",   "Instant 2M",   2_000_000, 0, 7499, 0, 0, 5, 8, "static", 30, 70),
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


# --------------------------------------------------------------------------- #
#  Promocja „Double your challenge size"                                      #
# --------------------------------------------------------------------------- #
# Ile razy większe konto dostaje klient. 2.0 = „co najmniej dwa razy większe",
# więc hasło „double" jest prawdą także tam, gdzie drabinka katalogu nie skacze
# dokładnie ×2 (10k→25k, 200k→400k, 300k→800k). Ustawienie 1.0 daje zwykłe
# „następny rozmiar w górę".
PROMO_UPGRADE_X = 2.0
PROMO_NAME = "Double Your Size"


def promo_active(now: datetime | None = None) -> bool:
    """Czy promocja obowiązuje TERAZ (flaga + opcjonalna data końcowa).

    Jedno źródło prawdy dla treści na stronie i dla checkoutu — nie da się
    reklamować upgrade'u, którego kasa nie zrobi, ani rozdawać go po terminie.
    """
    if not settings.promo_upgrade:
        return False
    koniec = settings.promo_upgrade_ends
    if not koniec:
        return True
    try:
        ostatni = date.fromisoformat(koniec)
    except ValueError:      # zła data w configu = promocja wyłączona, nie „na wieczność"
        return False
    dzis = (now or datetime.now(timezone.utc)).date()
    return dzis <= ostatni


def upgrade_target(session, product: Product) -> Product | None:
    """Plan, który klient FAKTYCZNIE dostanie, kupując `product`.

    Najmniejszy aktywny plan tej samej rodziny (`steps`) o rozmiarze co najmniej
    PROMO_UPGRADE_X × opłacony. Rodziny nie mieszamy — Instant Funding ma inne
    limity i split niż ewaluacja. None = brak promocji albo brak większego planu
    (największy tier w ofercie).
    """
    if not promo_active() or product.price_usd <= 0:
        return None
    return (session.query(Product)
            .filter(Product.active == True,                                   # noqa: E712
                    Product.steps == product.steps,
                    Product.account_size >= product.account_size * PROMO_UPGRADE_X)
            .order_by(Product.account_size)
            .first())


def upgrade_map(products: list[Product]) -> dict[str, Product]:
    """To samo co `upgrade_target`, ale dla całej listy naraz (bez N+1 zapytań)."""
    if not promo_active():
        return {}
    wynik: dict[str, Product] = {}
    for rodzina in {p.steps for p in products}:
        w_rodzinie = sorted((p for p in products if p.steps == rodzina and p.price_usd > 0),
                            key=lambda p: p.account_size)
        for p in w_rodzinie:
            cel = next((x for x in w_rodzinie
                        if x.account_size >= p.account_size * PROMO_UPGRADE_X), None)
            if cel:
                wynik[p.key] = cel
    return wynik
