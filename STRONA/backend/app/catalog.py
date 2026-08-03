"""Katalog produktów (planów challenge'a), kupony i parametry afiliacji.

Rozmiary i ceny wzorowane na FTMO / Alpha Capital Group (2026): 25k–2M,
modele 2-Step i Instant Funding (bez darmowego triala). Seed synchronizuje
tabelę `products`, z której czyta sklep — TEN plik jest źródłem prawdy.
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
# Oferta 2026-08-02: dwa modele (2-Step i Instant Funding), rozmiary 25k–2M.
# 10k wypadło z oferty — wejściowym rozmiarem jest 25k. Konta już kupione żyją
# dalej; plany znikają tylko ze sklepu (patrz `seed_products`).
_CATALOG = [
    # --- 2-STEP (klasyczna ewaluacja: P1 10% / P2 5%, DD 5/10, split do 90%) ---
    ("2step-25k",  "2-Step 25K",  25_000,    2, 299,  10, 5, 5, 10, "static", 5, 90),
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
    ("instant-25k",  "Instant 25K",  25_000,    0, 369,  0, 0, 5, 8, "static", 30, 70),
    ("instant-50k",  "Instant 50K",  50_000,    0, 529,  0, 0, 5, 8, "static", 30, 70),
    ("instant-100k", "Instant 100K", 100_000,   0, 829,  0, 0, 5, 8, "static", 30, 70),
    ("instant-200k", "Instant 200K", 200_000,   0, 1569, 0, 0, 5, 8, "static", 30, 70),
    ("instant-300k", "Instant 300K", 300_000,   0, 2249, 0, 0, 5, 8, "static", 30, 70),
    ("instant-400k", "Instant 400K", 400_000,   0, 2999, 0, 0, 5, 8, "static", 30, 70),
    ("instant-800k", "Instant 800K", 800_000,   0, 4499, 0, 0, 5, 8, "static", 30, 70),
    ("instant-1m",   "Instant 1M",   1_000_000, 0, 5249, 0, 0, 5, 8, "static", 30, 70),
    ("instant-2m",   "Instant 2M",   2_000_000, 0, 8999, 0, 0, 5, 8, "static", 30, 70),
]


def seed_products(session) -> None:
    """Synchronizuje katalog w bazie z `_CATALOG` (źródłem prawdy w kodzie).

    Dodajemy brakujące plany, wycofujemy usunięte z oferty, aktualizujemy REGUŁY
    RYZYKA (limit wolumenu) i CENY. Cena jedzie z kodu, bo panel admina nie ma
    edytora cennika — gdyby seed jej nie nadpisywał, podwyżka wdrożona w kodzie
    nigdy nie dotarłaby do tabeli `products`, z której czyta sklep, i strona
    dalej sprzedawałaby po starych stawkach.

    Historii to nie rusza: `Order.amount_usd` trzyma kwotę z chwili zakupu.
    """
    catalog_keys = {row[0] for row in _CATALOG}
    changed = False

    # 1. Wycofane z oferty (free trial, 5k, 10k) — znikają ze sklepu, ale zostają
    #    w bazie dla kont, które już je kupiły.
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

    # 3. Cennik z kodu (tylko plany BĘDĄCE w ofercie — wycofanym zostawiamy
    #    ostatnią cenę, po jakiej realnie się sprzedawały).
    for (key, _label, _size, _steps, price, *_rest) in _CATALOG:
        prod = existing.get(key)
        if prod is not None and abs((prod.price_usd or 0) - price) > 1e-9:
            print(f"[seed] cena {key}: {prod.price_usd} -> {price}")
            prod.price_usd = price
            changed = True

    # 4. Nowe plany z katalogu
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
#  Promocja „Upgrade Your Size" (aktywowana kodem)                            #
# --------------------------------------------------------------------------- #
PROMO_NAME = "Upgrade Your Size"


def promo_code_ok(code: str | None) -> bool:
    """Czy podany kod aktywuje promocję (case-insensitive, bez białych znaków).

    Sam kod nie wystarcza — o tym, czy promocja W OGÓLE trwa, decyduje
    `promo_active()`. Rozdzielenie pozwala trzymać kod w marketingu na stałe,
    a promocję włączać/wyłączać jedną flagą.
    """
    if not code or not settings.promo_upgrade_code:
        return False
    return code.strip().upper() == settings.promo_upgrade_code.upper()


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


def next_product(session, steps: int, size: float) -> Product | None:
    """Najmniejszy aktywny plan tej samej rodziny większy niż `size`.

    Jedno miejsce dla dwóch mechanik, które pytają dokładnie o to samo: promocja
    „next size up for the same fee" i plan skalowania konta funded. Rodziny nie
    mieszamy (`steps`) — Instant Funding ma inne limity i split niż ewaluacja.
    None = to już największy tier w ofercie.

    Porównanie po ROZMIARZE, nie po kluczu planu: konta wyskalowane starym
    mechanizmem siedzą na 150k/225k, czyli rozmiarach, których w katalogu nie
    ma, i po kluczu nie dałoby się znaleźć dla nich kolejnego szczebla.
    """
    return (session.query(Product)
            .filter(Product.active == True,                                   # noqa: E712
                    Product.steps == steps,
                    Product.account_size > size)
            .order_by(Product.account_size)
            .first())


def next_size_up(steps: int, size: float) -> float | None:
    """To samo co `next_product`, ale bez bazy — sam rozmiar prosto z katalogu.

    Potrzebne w `_account_dict`, które nie dostaje sesji, a leci po całej liście
    kont tradera: zapytanie per konto byłoby N+1. Do zapisu i tak używamy
    `next_product` (autorytatywna jest tabela `products`, bo plan może być
    wycofany ze sklepu), ta funkcja tylko rysuje ofertę.
    """
    wieksze = sorted(row[2] for row in _CATALOG if row[3] == steps and row[2] > size)
    return float(wieksze[0]) if wieksze else None


def upgrade_target(session, product: Product) -> Product | None:
    """Plan, który klient FAKTYCZNIE dostanie, kupując `product` z kodem promo.

    NASTĘPNY rozmiar w górę, o ile promocja trwa. None = brak promocji albo brak
    większego planu (2M, największy tier w ofercie).
    """
    if not promo_active() or product.price_usd <= 0:
        return None
    return next_product(session, product.steps, product.account_size)


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
                        if x.account_size > p.account_size), None)
            if cel:
                wynik[p.key] = cel
    return wynik
