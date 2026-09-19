"""Flash sale — ręczne oferty procentowe na wybrane plany (cross-sell).

Oferta obniża cenę katalogową wskazanych planów w oknie czasowym, imiennie
(trader_id) albo globalnie (trader_id NULL). Z tego modułu żyje i podgląd
w sklepie, i realna wycena w `billing.compute_price` — ta sama funkcja
`price_after` liczy obie, więc kafelek planu nigdy nie obieca innej kwoty
niż kasa.

Rabatu NIE zapisujemy w `Product.price_usd`: `catalog.seed_products` nadpisuje
ceny w bazie cenami z `_CATALOG` przy każdym starcie i po pierwszym restarcie
rabat by zniknął (albo — gorzej — zostałby na zawsze).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_

from .models import FlashOffer, Product


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Baza oddaje daty naiwne — porównania robimy w UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def status(o: FlashOffer, now: datetime | None = None) -> str:
    """pending | active | expired | used | cancelled — wyliczane, nie z kolumny."""
    now = now or _utcnow()
    if o.cancelled_at is not None:
        return "cancelled"
    if o.used_at is not None:
        return "used"
    if o.starts_at is not None and now < _aware(o.starts_at):
        return "pending"
    if now >= _aware(o.ends_at):
        return "expired"
    return "active"


def live_for(session, trader_id: int | None, now: datetime | None = None) -> list[FlashOffer]:
    """Oferty AKTYWNE teraz i widoczne dla tradera.

    `trader_id=None` = kontekst anonimowy (landing, publiczne /api/products):
    wychodzą wyłącznie globalne. Zalogowany widzi globalne + swoje imienne.
    """
    now = now or _utcnow()
    kto = (FlashOffer.trader_id == None) if trader_id is None else or_(  # noqa: E711
        FlashOffer.trader_id == None, FlashOffer.trader_id == trader_id)  # noqa: E711
    q = (session.query(FlashOffer)
         .filter(kto,
                 FlashOffer.cancelled_at == None,     # noqa: E711
                 FlashOffer.used_at == None,          # noqa: E711
                 FlashOffer.ends_at > now.replace(tzinfo=None))
         .order_by(FlashOffer.id))
    return [o for o in q.all() if status(o, now) == "active"]


def covers(o: FlashOffer, product: Product) -> bool:
    """Czy oferta obejmuje ten plan."""
    if o.scope == "all":
        return True
    if o.scope == "2step":
        return product.steps == 2
    if o.scope == "instant":
        return product.steps == 0
    if o.scope == "keys":
        keys = {k.strip() for k in (o.plan_keys or "").split(",") if k.strip()}
        return product.key in keys
    return False


def price_after(product: Product, o: FlashOffer) -> float:
    """Cena planu po ofercie — liczona od ceny KATALOGOWEJ, jak kupon."""
    return round(product.price_usd * (1 - o.discount_pct / 100.0), 2)


def best_for(session, trader_id: int | None, product: Product,
             now: datetime | None = None) -> FlashOffer | None:
    """Najkorzystniejsza żywa oferta obejmująca plan. None = brak.

    Gdy trader ma i globalną, i imienną, wygrywa większy procent — klient
    z osobistą 40% nie może dostać gorszej ceny, bo trwa akurat globalna 20%.
    """
    kandydaci = [o for o in live_for(session, trader_id, now)
                 if covers(o, product) and o.discount_pct > 0]
    return max(kandydaci, key=lambda o: o.discount_pct, default=None)


def offer_dict(o: FlashOffer, now: datetime | None = None) -> dict:
    return {
        "id": o.id, "discount_pct": o.discount_pct, "trader_id": o.trader_id,
        "scope": o.scope,
        "plan_keys": [k.strip() for k in (o.plan_keys or "").split(",") if k.strip()],
        "title": o.title, "single_use": bool(o.single_use),
        "starts_at": o.starts_at.isoformat() if o.starts_at else None,
        "ends_at": o.ends_at.isoformat() if o.ends_at else None,
        "used_at": o.used_at.isoformat() if o.used_at else None,
        "cancelled_at": o.cancelled_at.isoformat() if o.cancelled_at else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "order_id": o.order_id,
        "status": status(o, now),
    }
