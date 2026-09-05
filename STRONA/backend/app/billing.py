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

from . import catalog, loyalty, provisioning, telemetry
from .config import get_settings
from .models import AppSetting, Order, Product, RewardCode, Trader

settings = get_settings()

# Buy 1 Get 1 Free — globalny wlacznik w AppSetting (przycisk w panelu admina).
BOGO_KEY = "bogo_promo"


def bogo_active(session) -> bool:
    row = session.get(AppSetting, BOGO_KEY)
    return bool(row and row.value == "1")


def _stripe():
    import stripe  # lazy import
    stripe.api_key = settings.stripe_secret_key
    return stripe


def save_customer_details(session, trader: Trader, *, first_name: str | None,
                          last_name: str | None, phone: str | None,
                          phone_country: str | None = None) -> Trader:
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
    if phone_country and phone_country.strip():
        trader.phone_country = phone_country.strip().upper()[:2]
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


def _powod_odmowy(session, trader: Trader, code: str) -> str:
    """Konkretny komunikat dla kodu nagrody, ktorego nie da sie uzyc."""
    k = session.query(RewardCode).filter(RewardCode.code == code).first()
    if k is None or k.trader_id != trader.id:
        raise HTTPException(400, "This coupon is personal and belongs to another account")
    if k.used_at is not None:
        return "This coupon has already been used"
    return "This coupon has expired"


def compute_price(session, trader: Trader, product_key: str, coupon: str | None,
                  promo_code: str | None = None, weekend_trading: bool = False,
                  split_boost: bool = False, express_payout: bool = False,
                  use_credits: bool = True) -> dict:
    """Jedyne miejsce, w ktorym liczy sie cena checkoutu.

    Kolejnosc jest czescia kontraktu: kupon -> add-on Weekend -> kredyty
    sklepowe na samym koncu. Z tej funkcji zyje zarowno create_checkout(), jak
    i podglad GET /api/checkout/preview — dzieki temu modal zakupu nigdy nie
    pokaze innej kwoty niz ta, ktora policzy realny checkout.
    """
    product = session.query(Product).filter(Product.key == product_key, Product.active == True).first()  # noqa: E712
    if not product:
        raise HTTPException(404, "Product not found")

    code = (coupon or "").strip().upper()
    if code in catalog.LUCKY_CODES and not _lucky_active(trader, code):
        raise HTTPException(400, "This coupon is personal and no longer active")

    # Kod wymieniony za punkty lojalnosciowe. Musi byc rozwiazany TUTAJ, bo
    # `catalog.apply_coupon` zna wylacznie zaszyty slownik kodow globalnych i
    # dla wygenerowanego kodu oddalby po cichu 0% — trader zaplacilby pelna cene
    # kodem, za ktory wlasnie oddal punkty.
    kod_nagrody = None
    if code.startswith(loyalty.CODE_PREFIX):
        kod_nagrody = loyalty.find_usable(session, trader, code)
        if kod_nagrody is None:
            # Rozdzielamy powody, bo „nie twoj" i „juz zuzyty" to dla tradera
            # dwie zupelnie inne wiadomosci. Slowo "coupon" w tresci jest
            # potrzebne: modal zakupu po nim rozpoznaje, ze ma to pokazac.
            raise HTTPException(400, _powod_odmowy(session, trader, code))

    if kod_nagrody is not None:
        discount_pct = kod_nagrody.pct
        price = round(product.price_usd * (1 - discount_pct / 100.0), 2)
    else:
        price, discount_pct = catalog.apply_coupon(product.price_usd, coupon)
    plan_po_kuponie = price
    # Add-on Weekend Trading: stala kwota, POZA rabatem kuponu (kupon dotyczy planu).
    if weekend_trading:
        price = round(price + catalog.WEEKEND_ADDON_USD, 2)
    # Split Boost tylko dla Instant Funding — walidacja TUTAJ, nie w UI: modal
    # mozna obejsc golym POST-em, a boost na planie 2-Step (split 90%) dawalby
    # 100% i firma pracowalaby za darmo.
    if split_boost:
        if product.steps != 0:
            raise HTTPException(400, "The profit split boost is available on Instant Funding plans only")
        price = round(price + catalog.SPLIT_BOOST_ADDON_USD, 2)
    if express_payout:
        price = round(price + catalog.EXPRESS_PAYOUT_ADDON_USD, 2)

    # Promocja „Upgrade Your Size": TYLKO z poprawnym kodem promo zamowienie
    # idzie na NASTEPNY plan w gore, a kwota zostaje z planu WYBRANEGO (`price`
    # wyliczone wyzej z `product`). Kod sumuje sie z kuponem rabatowym (osobne
    # pola). `bogo_paid_key` mowi calej reszcie systemu, za co klient faktycznie
    # zaplacil — z tego zyja mail „we upgraded your allocation", baner i faktura.
    upgrade = catalog.upgrade_target(session, product) if catalog.promo_code_ok(promo_code) else None

    # Kredyty sklepowe (nadane przez admina, 1 kredyt = 1 USD): schodza z ceny
    # NA KONCU — po kuponie i add-onie. Trader moze je zostawic na pozniej
    # (use_credits=False). Saldo NIE jest tu ruszane; odejmuje je dopiero
    # domkniecie platnosci (provisioning), wiec porzucony checkout nic nie pali.
    kredyt = 0.0
    if use_credits:
        kredyt = min(round(float(trader.credits_usd or 0), 2), price)
        if kredyt > 0:
            reszta = round(price - kredyt, 2)
            # Stripe nie przyjmie kwoty ponizej progu (`stripe_min_charge`, patrz
            # config) — resztowke zostawiamy na saldzie tradera zamiast wywracac
            # checkout. Prog bierzemy z ustawien, bo zalezy od waluty rozliczenia
            # konta: zaszyte tu wczesniej 0.5 bylo prawdziwe tylko dla konta
            # rozliczanego w USD i wywalalo platnosc 500-tka na koncie w PLN.
            minimum = settings.stripe_min_charge
            if 0 < reszta < minimum:
                # `max(0, ...)` na wypadek ceny juz ponizej progu (mocny kupon):
                # bez tego kredyt wychodzi UJEMNY, czyli doladowuje saldo i
                # podnosi kwote do zaplaty ponad cene planu.
                kredyt = max(0.0, round(price - minimum, 2))
            price = round(price - kredyt, 2)

    return {"product": product, "upgrade": upgrade,
            "plan_price_usd": product.price_usd,
            "discount_pct": discount_pct,
            "discount_usd": round(product.price_usd - plan_po_kuponie, 2),
            "weekend_fee_usd": (catalog.WEEKEND_ADDON_USD if weekend_trading else 0.0),
            "split_boost_fee_usd": (catalog.SPLIT_BOOST_ADDON_USD if split_boost else 0.0),
            "express_payout_fee_usd": (catalog.EXPRESS_PAYOUT_ADDON_USD if express_payout else 0.0),
            "credits_used": (kredyt if kredyt > 0 else 0.0),
            "total_due_usd": price,
            "bogo_paid_key": (product.key if upgrade else None)}


def open_stripe_session(session, order: Order, item_name: str, *,
                        powrot: str | None = None) -> str:
    """Sesja Stripe Checkout dla ISTNIEJĄCEGO zamówienia; zwraca adres kasy.

    Jedyne miejsce, w którym powstaje sesja płatności — dlatego linki wystawiane
    ręcznie z panelu (/pay/<token>) idą tędy, a nie przez Payment Link Stripe'a.
    Webhook domyka zamówienie tylko wtedy, gdy `stripe_session_id` zgadza się co
    do znaku, więc sesja MUSI powstać u nas i MUSI zostać zapisana przy
    zamówieniu — inaczej opłacona płatność zostaje zignorowana.

    `powrot` to adres, na który Stripe odsyła po zapłacie i po rezygnacji —
    domyślnie nasz portal. Podaje go płatność wystawiona przez partnera: jego
    klient kupował pod cudzą marką i wyrzucenie go po zapłacie na NASZĄ stronę
    logowania to jednocześnie zła marka i ściana („zaloguj się" kontem, o którym
    nie wie). Dokleja się `?paid=1` / `?canceled=1`, więc adres musi być bez
    znaku zapytania.
    """
    stripe = _stripe()
    # Zamówienie bywa reużyte (dedup w create_checkout, ponowne wejście w link
    # płatności) — starą sesję wygaszamy, żeby zapłacić dało się tylko
    # najnowszą, a nie obie naraz z dwóch otwartych kart.
    if order.stripe_session_id:
        try:
            stripe.checkout.Session.expire(order.stripe_session_id)
        except stripe.error.StripeError:
            pass  # już wygasła albo opłacona — webhook i tak dopasowuje po id
    baza = powrot or f"{settings.app_base_url}/portal"
    try:
        cs = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": settings.currency,
                    "product_data": {"name": item_name},
                    "unit_amount": int(round(order.amount_usd * 100)),
                },
                "quantity": 1,
            }],
            success_url=f"{baza}?paid=1",
            cancel_url=f"{baza}?canceled=1",
            metadata={"order_id": str(order.id)},
            client_reference_id=str(order.id),
        )
    except stripe.error.StripeError as e:
        # Bez tego kazdy blad Stripe'a leci do klienta jako gole HTTP 500
        # („Server error", zero informacji) — tak wygladal na produkcji
        # amount_too_small. Zamowienie zostaje `pending`: nikt za nie nie
        # zaplacil, a slad jest potrzebny do odtworzenia sprawy.
        session.commit()
        telemetry.track("checkout_failed", order.trader_id, order=order.id,
                        product=order.product_key, amount=order.amount_usd,
                        code=(getattr(e, "code", None) or "stripe_error"))
        raise HTTPException(400, "We could not open the payment page. "
                                 "Please try again or contact support.") from e
    order.stripe_session_id = cs.id
    session.commit()
    return cs.url


def create_checkout(session, trader: Trader, product_key: str, coupon: str | None,
                    promo_code: str | None = None, weekend_trading: bool = False,
                    split_boost: bool = False, express_payout: bool = False,
                    use_credits: bool = True) -> dict:
    quote = compute_price(session, trader, product_key, coupon,
                          promo_code=promo_code, weekend_trading=weekend_trading,
                          split_boost=split_boost, express_payout=express_payout,
                          use_credits=use_credits)
    product, upgrade = quote["product"], quote["upgrade"]
    prowizjonowany = upgrade or product
    price, discount_pct, kredyt = quote["total_due_usd"], quote["discount_pct"], quote["credits_used"]

    # Podwójny klik „Buy" (albo powrót do sklepu po porzuconym checkoucie)
    # reużywa wiszącego zamówienia zamiast tworzyć drugie. Dwa pending ordery
    # to dwie ŻYWE sesje Stripe w dwóch kartach — da się zapłacić obie, a lista
    # zamówień rośnie od duplikatów. Wycena idzie zawsze od nowa (świeże pola
    # niżej), a stara sesja Stripe jest wygaszana przy otwieraniu nowej.
    order = (session.query(Order)
             .filter(Order.trader_id == trader.id, Order.status == "pending",
                     Order.product_key == prowizjonowany.key,
                     Order.provider.in_(("stripe", "mock")))
             .order_by(Order.id.desc()).first())
    if order is None:
        order = Order(trader_id=trader.id, product_key=prowizjonowany.key)
        session.add(order)
    order.amount_usd = price
    order.coupon = coupon or None
    order.weekend_trading = bool(weekend_trading)
    order.addon_split_boost = bool(split_boost)
    order.addon_express_payout = bool(express_payout)
    order.credits_used = kredyt
    order.bogo_paid_key = quote["bogo_paid_key"]
    order.bogo = bogo_active(session)
    order.provider = "stripe" if settings.stripe_enabled else "mock"
    # `commit`, nie `flush`: `telemetry.track` otwiera WLASNA sesje, a wolane na
    # otwartej transakcji zapisu potrafi zablokowac sie na wlasnym zamowieniu.
    # Na SQLicie (dev) to bylo 5,2 s czekania na busy-timeout przy KAZDYM
    # checkoucie. Zamkniecie transakcji przed telemetria kosztuje jeden SELECT
    # (odswiezenie `order` po commicie) i usuwa cala klase tego problemu.
    session.commit()
    telemetry.track("order_created", trader.id, order=order.id,
                    product=order.product_key, amount=price)

    # Cena 0 (darmowy plan albo zakup w calosci pokryty kredytami) ->
    # provisioning od reki, bez Stripe'a.
    if price <= 0:
        acc = provisioning.create_account_from_order(session, order)
        return {"free": True, "order_id": order.id, "account_id": acc.id,
                "credits_used": kredyt, **_account_view(acc)}

    if settings.stripe_enabled:
        nazwa = (f"{prowizjonowany.label} challenge"
                 + (f" ({catalog.PROMO_NAME} promo)" if upgrade else "")
                 + (" + Weekend Trading" if weekend_trading else "")
                 + (" + Split Boost" if split_boost else "")
                 + (" + Express Payout" if express_payout else ""))
        url = open_stripe_session(session, order, nazwa)
        return {"checkout_url": url, "order_id": order.id, "amount": price,
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
    # Mock domyka WYŁĄCZNIE zamówienia mockowe. Order Stripe'owy „opłaca" tylko
    # webhook — bez tego guardu trader mógłby sprovisionować własne pending
    # zamówienie bez płacenia (order_id dostaje w odpowiedzi checkoutu).
    if order.provider != "mock":
        raise HTTPException(400, "This order is completed by the payment provider")
    acc = provisioning.create_account_from_order(session, order)
    return {"order_id": order.id, "account_id": acc.id, **_account_view(acc)}


def handle_webhook(session, payload: bytes, sig_header: str | None) -> dict:
    # Fail CLOSED: bez sekretu nie przetwarzamy niczego — niesygnowany POST
    # z gotowym JSON-em mógłby sprovisionować konto za darmo.
    if not settings.stripe_webhook_secret:
        raise HTTPException(503, "Stripe webhook secret not configured")
    stripe = _stripe()
    try:
        stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except Exception as e:
        raise HTTPException(400, f"Invalid webhook signature: {e}")
    # Po weryfikacji podpisu czytamy surowy JSON — obiekt Event z SDK zmienia
    # interfejs między wersjami (v15 nie jest już dict-em), a payload jest stały.
    event = json.loads(payload.decode() or "{}")
    typ = event.get("type")

    if typ in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        return _webhook_platnosc_nie_doszla(session, typ,
                                            event["data"]["object"])
    if typ == "radar.early_fraud_warning.created":
        return _webhook_fraud_warning(session, event["data"]["object"])
    if typ == "charge.dispute.created":
        return _webhook_chargeback(session, event["data"]["object"])

    if typ == "checkout.session.completed":
        obj = event["data"]["object"]
        raw = (obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id")
        try:
            order = session.get(Order, int(raw))
        except (TypeError, ValueError):
            return {"received": True}
        if not order or order.status == "paid":
            return {"received": True}
        # Zamówienie domyka wyłącznie TA sesja checkout, którą sami stworzyliśmy,
        # faktycznie opłacona i na pełną kwotę. Sandbox jest współdzielony z devem
        # (stripe trigger broadcastuje eventy wszędzie), a metadata w evencie może
        # spreparować każdy, kto ma klucz testowy — samo order_id niczego nie dowodzi.
        if obj.get("id") != order.stripe_session_id:
            return {"received": True, "ignored": "session mismatch"}
        if obj.get("payment_status") != "paid":
            return {"received": True, "ignored": "not paid"}
        if (obj.get("amount_total") != int(round(order.amount_usd * 100))
                or obj.get("currency") != settings.currency):
            return {"received": True, "ignored": "amount mismatch"}
        provisioning.create_account_from_order(session, order)
        return {"provisioned": True, "order_id": order.id}
    return {"received": True}


def _order_po_platnosci(session, payment_intent: str | None) -> Order | None:
    """Zamówienie dla danego payment_intent — przez NASZĄ sesję Checkout.

    Eventy fraudowe niosą charge/payment_intent, a zamówienie zna tylko
    `stripe_session_id` — pytamy więc Stripe'a, która sesja Checkout niesie ten
    intent, i dopasowujemy po jej id. Skutek uboczny w naszą korzyść: obce
    eventy (współdzielony sandbox) nie rozwiążą się na nic."""
    if not payment_intent:
        return None
    try:
        odp = _stripe().checkout.Session.list(payment_intent=payment_intent, limit=1)
        dane = odp["data"] if isinstance(odp, dict) else odp.data
        sid = (dane[0]["id"] if isinstance(dane[0], dict) else dane[0].id) if dane else None
    except Exception as e:
        print(f"[webhook] nie znalazłem sesji dla {payment_intent}: {e}")
        return None
    if not sid:
        return None
    return session.query(Order).filter(Order.stripe_session_id == sid).first()


def _webhook_platnosc_nie_doszla(session, typ: str, obj: dict) -> dict:
    """Porzucony albo odrzucony checkout domyka zamówienie jako failed.

    Bez tego zamówienie, którego Radar nie przepuścił, wisi jako `pending` na
    zawsze i wygląda w panelu jak „czekamy na wpłatę". `awaiting_crypto` celowo
    zostaje: klient płaci poza Stripe'em, więc sesja Checkout MA prawo wygasnąć,
    a zamówienie domyka ręcznie admin."""
    order = (session.query(Order)
             .filter(Order.stripe_session_id == obj.get("id"),
                     Order.status == "pending").first())
    if not order or order.flag == "awaiting_crypto":
        return {"received": True}
    order.status = "failed"
    order.fail_reason = ("Stripe checkout expired (never paid)"
                         if typ == "checkout.session.expired"
                         else "Stripe payment failed")
    session.commit()
    telemetry.track("order_autofailed", order.trader_id, order=order.id, why=typ)
    return {"received": True, "failed": order.id}


def _webhook_fraud_warning(session, obj: dict) -> dict:
    """Bank wystawcy karty zgłosił fraud (Early Fraud Warning / TC40).

    Refund idzie od ręki i bez człowieka, z powodem `fraudulent` (Stripe
    dopisuje kartę do blocklisty): zgłoszenie, które zdąży urosnąć w chargeback,
    psuje metryki ryzyka konta, a proaktywny refund zwykle je ucina."""
    zrefundowano = False
    if obj.get("actionable") and obj.get("charge"):
        try:
            _stripe().Refund.create(charge=obj["charge"], reason="fraudulent")
            zrefundowano = True
        except Exception as e:
            print(f"[webhook] refund po fraud warning nie wyszedł: {e}")
    order = _order_po_platnosci(session, obj.get("payment_intent"))
    if order:
        order.flag = "fraud"
        if order.status == "pending":
            order.status = "failed"
            order.fail_reason = "Stripe fraud warning (issuer report)"
        session.commit()
        telemetry.track("stripe_fraud_warning", order.trader_id,
                        order=order.id, refunded=zrefundowano)
    tytul = "Stripe fraud warning" + (f" — order #{order.id}" if order else "")
    tresc = ("Refunded automatically, card blocklisted."
             if zrefundowano else "Refund NOT issued — check the Stripe dashboard.")
    if order and order.account_id:
        tresc += f" The trading account (id {order.account_id}) is still live — review it."
    from . import notify
    notify.notify_admins("fraud", tytul, tresc)
    return {"received": True, "refunded": zrefundowano,
            **({"order_id": order.id} if order else {})}


def _webhook_chargeback(session, obj: dict) -> dict:
    """Chargeback otwarty — tu już tylko flaga i alarm; odpowiadać trzeba
    w dashboardzie Stripe'a, refund niczego nie cofnie."""
    order = _order_po_platnosci(session, obj.get("payment_intent"))
    if order:
        order.flag = "disputed"
        session.commit()
        telemetry.track("stripe_dispute", order.trader_id, order=order.id)
    kwota = (obj.get("amount") or 0) / 100
    from . import notify
    notify.notify_admins(
        "fraud",
        "Chargeback opened" + (f" — order #{order.id}" if order else ""),
        f"${kwota:.2f} disputed ({obj.get('reason') or 'reason unknown'}). "
        "Respond in the Stripe dashboard.")
    return {"received": True, **({"order_id": order.id} if order else {})}


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
