"""Web push (PWA) — powiadomienia na telefon bez App Store.

Subskrypcje urządzeń trzyma tabela push_subscriptions; wysyłka przez pywebpush
z podpisem VAPID. Brak kluczy w env => całość cicho wyłączona (tryb dev, 0 zł).

Push idzie tą samą bramką preferencji co maile (notify._PREF_BY_EVENT), więc
jeden przełącznik w ustawieniach wyłącza kategorię wszędzie naraz.

Zasada produktu: push NIGDY nie komentuje wyników tradingu w czasie rzeczywistym
— tylko zdarzenia konta (poświadczenia, payout, KYC) i przypomnienie o serii.

Generowanie kluczy (raz, wynik do env):  python -m app.push
"""
from __future__ import annotations

import json

from .config import get_settings

settings = get_settings()

# Krótkie treści pod tytułem (tytuł = temat maila, liczony w notify._render)
_BODY: dict[str, str] = {
    "welcome": "Your trader portal is ready.",
    "credentials": "Your MT5 credentials are ready — log in and start trading.",
    "challenge_granted": "A challenge account was added to your portal.",
    "phase_passed": "Objective complete — your next phase account is on the way.",
    "account_funded": "Your funded account is live. Welcome to the payout side.",
    "breached": "A trading rule was breached on your account. See details.",
    "payout_requested": "We received your payout request — it's under review.",
    "payout_approved": "Your payout was approved.",
    "payout_rejected": "Your payout request needs attention.",
    "kyc_approved": "Identity verified — you're cleared for payouts.",
    "kyc_rejected": "Your verification needs another look.",
    "ticket_reply": "Support replied to your ticket.",
}


def is_enabled() -> bool:
    return settings.push_enabled


def _deliver(sub_info: dict, payload: str) -> None:
    """Jedna wysyłka do push service'u przeglądarki. Wydzielone dla testów."""
    from pywebpush import webpush  # lazy: pakiet zbędny, gdy push wyłączony

    webpush(
        subscription_info=sub_info,
        data=payload,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_sub or f"mailto:{settings.support_email}"},
    )


def send_to_trader(trader_id: int, title: str, body: str = "",
                   url: str = "/portal", tag: str | None = None) -> int:
    """Wysyła push na wszystkie urządzenia tradera; zwraca liczbę dostarczeń.

    Martwe subskrypcje (410/404 z push service'u — użytkownik odwołał zgodę
    albo odinstalował PWA) są przy okazji kasowane, żeby nie słać w próżnię."""
    if not is_enabled():
        return 0
    from .db import SessionLocal
    from .models import PushSubscription

    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    session = SessionLocal()
    sent = 0
    try:
        subs = session.query(PushSubscription).filter(
            PushSubscription.trader_id == trader_id).all()
        for sub in subs:
            info = {"endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
            try:
                _deliver(info, payload)
                sent += 1
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):
                    session.delete(sub)
                else:
                    print(f"[push] błąd wysyłki do tradera {trader_id}: {e}")
        session.commit()
        return sent
    finally:
        session.close()


def send_event(event: str, to_email: str | None, title: str, ctx: dict | None = None) -> int:
    """Push dla zdarzenia z notify.send — ta sama kategoria preferencji co mail."""
    if not is_enabled() or not to_email:
        return 0
    from .db import SessionLocal
    from .models import Trader
    from .notify import _PREF_BY_EVENT

    session = SessionLocal()
    try:
        tr = session.query(Trader).filter(Trader.email == to_email).first()
        if tr is None:
            return 0
        pref = _PREF_BY_EVENT.get(event)
        if pref:
            val = getattr(tr, pref, True)
            if val is not None and not bool(val):
                return 0
        trader_id = tr.id
    finally:
        session.close()
    return send_to_trader(trader_id, title, _BODY.get(event, ""), tag=event)


def generate_vapid_keys() -> tuple[str, str]:
    """Zwraca (private, public) w base64url — public idzie też do przeglądarki
    jako applicationServerKey, więc format musi być surowym punktem EC P-256."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02, b64urlencode

    v = Vapid02()
    v.generate_keys()
    priv = b64urlencode(
        v.private_key.private_numbers().private_value.to_bytes(32, "big"))
    pub = b64urlencode(v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    return priv, pub


if __name__ == "__main__":
    priv, pub = generate_vapid_keys()
    print("Wklej do env (Vercel: Settings -> Environment Variables):\n")
    print(f"VAPID_PRIVATE_KEY={priv}")
    print(f"VAPID_PUBLIC_KEY={pub}")
    print("VAPID_SUB=mailto:support@protradersfunding.com")
