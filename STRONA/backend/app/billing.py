"""Płatności — Stripe Checkout z trybem MOCK (gdy brak kluczy => 0 zł, dev).

Przepływ:
  create_checkout() -> Order(pending) -> link do płatności (Stripe lub mock)
  płatność OK    -> webhook Stripe / mock-complete -> provisioning konta
Darmowy trial (cena 0) jest provisionowany natychmiast.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from . import catalog, provisioning, telemetry
from .config import get_settings
from .models import Order, Product, Trader

settings = get_settings()


def _stripe():
    import stripe  # lazy import
    stripe.api_key = settings.stripe_secret_key
    return stripe


def save_customer_details(session, trader: Trader, *, first_name: str | None,
                          last_name: str | None, phone: str | None) -> Trader:
    """Zapisuje dane z kroku płatności na profilu tradera.

    Na te dane zakładane jest potem konto demo MT5, więc nie nadpisujemy
    zapisanych wartości pustkami — trader może kupić drugi challenge bez
    ponownego wpisywania wszystkiego.
    """
    trader = session.get(Trader, trader.id) or trader
    if first_name and first_name.strip():
        trader.first_name = first_name.strip()[:60]
    if last_name and last_name.strip():
        trader.last_name = last_name.strip()[:60]
    if phone and phone.strip():
        trader.phone = phone.strip()[:32]
    if (trader.first_name or trader.last_name) and not (trader.full_name or "").strip():
        trader.full_name = " ".join(x for x in (trader.first_name, trader.last_name) if x)
    session.commit()
    return trader


def _lucky_active(trader: Trader, code: str) -> bool:
    """Kody LUCKY sa osobiste (z daily reveal) i wazne 48h — wyciekniety kod
    nie zadziala u nikogo, kto go faktycznie nie wylosowal."""
    try:
        payload = json.loads(trader.reveal_payload or "null")
    except ValueError:
        return False
    if not payload or payload.get("type") != "coupon" or payload.get("code") != code:
        return False
    expires = payload.get("expires_at")
    if not expires:
        return False
    return datetime.now(timezone.utc) <= datetime.fromisoformat(expires)


def create_checkout(session, trader: Trader, product_key: str, coupon: str | None,
                    promo_code: str | None = None, weekend_trading: bool = False) -> dict:
    product = session.query(Product).filter(Product.key == product_key, Product.active == True).first()  # noqa: E712
    if not product:
        raise HTTPException(404, "Product not found")

    code = (coupon or "").strip().upper()
    if code in catalog.LUCKY_CODES and not _lucky_active(trader, code):
        raise HTTPException(400, "This coupon is personal and no longer active")
    price, discount_pct = catalog.apply_coupon(product.price_usd, coupon)
    # Add-on Weekend Trading: stala kwota, POZA rabatem kuponu (kupon dotyczy planu).
    if weekend_trading:
        price = round(price + catalog.WEEKEND_ADDON_USD, 2)

    # Promocja „Upgrade Your Size": TYLKO z poprawnym kodem promo zamowienie
    # idzie na NASTEPNY plan w gore, a kwota zostaje z planu WYBRANEGO (`price`
    # wyliczone wyzej z `product`). Kod sumuje sie z kuponem rabatowym (osobne
    # pola). `bogo_paid_key` mowi calej reszcie systemu, za co klient faktycznie
    # zaplacil — z tego zyja mail „we upgraded your allocation", baner i faktura.
    upgrade = catalog.upgrade_target(session, product) if catalog.promo_code_ok(promo_code) else None
    prowizjonowany = upgrade or product

    # Kredyty sklepowe (nadane przez admina): schodza z ceny NA KONCU — po
    # kuponie i add-onie. Saldo tradera NIE jest tu ruszane; odejmuje je dopiero
    # domkniecie platnosci (provisioning), wiec porzucony checkout nic nie pali.
    kredyt = min(round(float(trader.credits_usd or 0), 2), price)
    if kredyt > 0:
        reszta = round(price - kredyt, 2)
        # Stripe nie przyjmie kwoty ponizej $0.50 — resztowke zostawiamy na
        # saldzie tradera zamiast wywracac checkout.
        if 0 < reszta < 0.5:
            kredyt = round(price - 0.5, 2)
        price = round(price - kredyt, 2)

    order = Order(trader_id=trader.id, product_key=prowizjonowany.key, amount_usd=price,
                  coupon=(coupon or None), weekend_trading=bool(weekend_trading),
                  credits_used=(kredyt if kredyt > 0 else 0.0),
                  bogo_paid_key=(product.key if upgrade else None),
                  provider="stripe" if settings.stripe_enabled else "mock")
    session.add(order)
    session.flush()
    telemetry.track("order_created", trader.id, order=order.id,
                    product=order.product_key, amount=price)

    # Cena 0 (darmowy plan albo zakup w calosci pokryty kredytami) ->
    # provisioning od reki, bez Stripe'a.
    if price <= 0:
        acc = provisioning.create_account_from_order(session, order)
        return {"free": True, "order_id": order.id, "account_id": acc.id,
                "credits_used": kredyt, **_account_view(acc)}

    if settings.stripe_enabled:
        stripe = _stripe()
        cs = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": settings.currency,
                    "product_data": {"name": f"{prowizjonowany.label} challenge"
                                     + (f" ({catalog.PROMO_NAME} promo)" if upgrade else "")
                                     + (" + Weekend Trading" if weekend_trading else "")},
                    "unit_amount": int(round(price * 100)),
                },
                "quantity": 1,
            }],
            success_url=f"{settings.app_base_url}/portal?paid=1",
            cancel_url=f"{settings.app_base_url}/portal?canceled=1",
            metadata={"order_id": str(order.id)},
            client_reference_id=str(order.id),
        )
        order.stripe_session_id = cs.id
        session.commit()
        return {"checkout_url": cs.url, "order_id": order.id, "amount": price,
                "discount_pct": discount_pct, "credits_used": kredyt}

    # MOCK: zwróć link do naszej stronki symulującej płatność
    session.commit()
    return {"mock": True, "order_id": order.id, "amount": price, "discount_pct": discount_pct,
            "credits_used": kredyt,
            "checkout_url": f"{settings.app_base_url}/portal?mock_order={order.id}"}


def grant_challenge(session, trader: Trader, product_key: str, note: str | None,
                    bogo_paid_key: str | None = None) -> dict:
    """Admin przyznaje challenge bez płatności (promocja, BOGO, rekompensata).

    Idzie DOKŁADNIE tą samą ścieżką co zakup — powstaje zamówienie na $0 z
    providerem `grant`, a provisioning zakłada realne konto MT5. Dzięki temu
    konto przyznane zachowuje się w silniku identycznie jak kupione.

    `bogo_paid_key` = tier, za który klient FAKTYCZNIE zapłacił. Tylko wtedy mail
    i portal mogą napisać „you paid for the $25K tier and we upgraded you to
    $50,000" — bez tego pola byłoby to zdanie zmyślone, więc go nie piszemy.
    """
    product = session.query(Product).filter(Product.key == product_key, Product.active == True).first()  # noqa: E712
    if not product:
        raise HTTPException(404, "Product not found")
    oplacony = (session.query(Product).filter(Product.key == bogo_paid_key).first()
                if bogo_paid_key else None)
    if bogo_paid_key and not oplacony:
        raise HTTPException(404, "The paid tier does not exist")
    # Przy BOGO klient FAKTYCZNIE zapłacił za mniejszy tier — zamówienie musi to
    # odzwierciedlać, inaczej na fakturze widniałoby 0 USD i wyglądałoby to na
    # darmowy produkt.
    order = Order(trader_id=trader.id, product_key=product.key,
                  amount_usd=(oplacony.price_usd if oplacony else 0.0),
                  coupon=(note or None), provider="grant",
                  bogo_paid_key=(bogo_paid_key or None))
    session.add(order)
    session.flush()
    acc = provisioning.create_account_from_order(session, order)
    return {"order_id": order.id, "account_id": acc.id, "login": acc.login,
            "status": acc.status, "product_key": acc.product_key,
            "account_size": acc.initial_balance, **_account_view(acc)}


def mock_complete(session, order_id: int, trader_id: int):
    order = session.get(Order, order_id)
    if not order or order.trader_id != trader_id:
        raise HTTPException(404, "Order not found")
    if order.status == "paid":
        return {"order_id": order.id, "account_id": order.account_id, "already": True}
    acc = provisioning.create_account_from_order(session, order)
    return {"order_id": order.id, "account_id": acc.id, **_account_view(acc)}


def handle_webhook(session, payload: bytes, sig_header: str | None) -> dict:
    if settings.stripe_webhook_secret:
        stripe = _stripe()
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
        except Exception as e:
            raise HTTPException(400, f"Invalid webhook signature: {e}")
    else:
        event = json.loads(payload.decode() or "{}")  # dev: bez weryfikacji podpisu

    if event.get("type") == "checkout.session.completed":
        obj = event["data"]["object"]
        order_id = (obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id")
        if order_id:
            order = session.get(Order, int(order_id))
            if order and order.status != "paid":
                provisioning.create_account_from_order(session, order)
                return {"provisioned": True, "order_id": order.id}
    return {"received": True}


def _account_view(acc) -> dict:
    """Co pokazać kupującemu zaraz po płatności.

    Gdy konto czeka na założenie realnego dema, NIE oddajemy poświadczeń —
    w bazie są wtedy tylko placeholdery, a trader dostanie prawdziwe mailem.
    """
    if acc.status == "provisioning":
        return {"provisioning": True, "status": acc.status}
    return {"provisioning": False, "status": acc.status,
            "platform_login": acc.platform_login,
            "platform_password": acc.platform_password,
            "platform_server": acc.platform_server}
