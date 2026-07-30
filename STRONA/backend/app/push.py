"""Web push (PWA) + centrum powiadomień w portalu + dzienny recap.

Subskrypcje urządzeń trzyma tabela push_subscriptions; wysyłka przez pywebpush
z podpisem VAPID. Brak kluczy w env => push cicho wyłączony (tryb dev, 0 zł),
ale wpisy w centrum powiadomień (dzwonek w portalu) powstają zawsze — to tania
historia dla tradera niezależna od zgody przeglądarki.

Push idzie tą samą bramką preferencji co maile (notify._PREF_BY_EVENT), więc
jeden przełącznik w ustawieniach wyłącza kategorię wszędzie naraz.

Zasada produktu: push NIGDY nie komentuje wyników tradingu w czasie rzeczywistym
— tylko zdarzenia konta (poświadczenia, payout, KYC), przypomnienie o serii
i dzienny recap ZAMKNIĘTEGO dnia.

Generowanie kluczy (raz, wynik do env):  python -m app.push
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .config import get_settings

settings = get_settings()

# Zdarzenia, ktore NIE trafiaja do centrum ani pusha (akcje bezpieczenstwa —
# wylacznie mail; link resetu hasla nie ma czego szukac w historii powiadomien).
_SKIP = {"password_reset"}

# Do jakiego widoku portalu prowadzi klik w powiadomienie (reszta -> Challenges).
_EVENT_VIEW = {
    "payout_requested": "payouts", "payout_approved": "payouts", "payout_rejected": "payouts",
    "kyc_approved": "kyc", "kyc_rejected": "kyc",
    "ticket_reply": "support",
    "daily_recap": "analytics",
    "credits_granted": "store",
}

# Krótkie treści pod tytułem (tytuł = temat maila, liczony w notify._render).
# NIGDY nie bierzemy treści maila — `credentials` zawiera hasło MT5, a push
# ląduje w systemowej historii powiadomień urządzenia.
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
    "credits_granted": "Store credit added — it applies automatically at your next checkout.",
}


def is_enabled() -> bool:
    return settings.push_enabled


def event_url(event: str) -> str:
    return f"/portal?view={_EVENT_VIEW.get(event, 'accounts')}"


def _deliver(sub_info: dict, payload: str) -> None:
    """Jedna wysyłka do push service'u przeglądarki. Wydzielone dla testów."""
    from pywebpush import webpush  # lazy: pakiet zbędny, gdy push wyłączony

    webpush(
        subscription_info=sub_info,
        data=payload,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_sub or f"mailto:{settings.support_email}"},
    )


def _center_row(session, trader_id: int, event: str, title: str, body: str, url: str) -> None:
    """Wpis w centrum powiadomień + retencja (najnowsze 50 na tradera)."""
    from .models import Notification
    session.add(Notification(trader_id=trader_id, event=event[:32],
                             title=title[:200], body=body[:400], url=url[:200]))
    stare = (session.query(Notification).filter(Notification.trader_id == trader_id)
             .order_by(Notification.id.desc()).offset(50).all())
    for n in stare:
        session.delete(n)


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
    """Zdarzenie z notify.send: wpis w centrum powiadomień + push.

    Ta sama kategoria preferencji co mail; wpis w centrum powstaje także przy
    wyłączonym pushu — dzwonek w portalu działa bez zgody przeglądarki."""
    if not to_email or event in _SKIP:
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
        _center_row(session, trader_id, event, title,
                    _BODY.get(event, ""), event_url(event))
        session.commit()
    finally:
        session.close()
    return send_to_trader(trader_id, title, _BODY.get(event, ""),
                          url=event_url(event), tag=event)


# --------------------------------------------------------------------------- #
#  Dzienny recap (dopięty do crona /api/tick, raz na dobę)                    #
# --------------------------------------------------------------------------- #
def daily_recap() -> dict:
    """Recap wczorajszego handlu: wynik z transakcji + dystans do celu fazy.

    Zasady: raz na dobę (guard w AppSetting), BRAK transakcji = CISZA (żadnego
    pustego pingu), kategoria notify_marketing („Daily Recap & Offers").
    Komentuje wyłącznie ZAMKNIĘTY dzień — nigdy otwarte pozycje.
    """
    try:
        return _daily_recap()
    except Exception as e:  # pragma: no cover
        print(f"[push] recap błąd: {e}")
        return {"sent": 0, "error": str(e)}


def _daily_recap() -> dict:
    from . import rules
    from .db import SessionLocal
    from .models import Account, AppSetting, Trade, Trader
    session = SessionLocal()
    try:
        dzis = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        guard = session.get(AppSetting, "last_recap_day")
        if guard and guard.value == dzis:
            return {"sent": 0, "skipped": "already ran today"}
        if guard:
            guard.value = dzis
        else:
            session.add(AppSetting(key="last_recap_day", value=dzis))
        session.commit()

        # Wczorajsza doba UTC; closed_at w bazie jest naiwne (UTC bez tz).
        start = datetime.strptime(dzis, "%Y-%m-%d") - timedelta(days=1)
        koniec = start + timedelta(days=1)
        wiersze = (session.query(Trade, Account.trader_id)
                   .join(Account, Trade.account_id == Account.id)
                   .filter(Trade.status == "closed",
                           Trade.closed_at >= start, Trade.closed_at < koniec)
                   .all())
        per: dict[int, list] = {}
        for t, tid in wiersze:
            per.setdefault(tid, []).append(t)

        wyslane = 0
        for tid, ts in per.items():
            tr = session.get(Trader, tid)
            if not tr or (tr.notify_marketing is not None and not tr.notify_marketing):
                continue
            pnl = sum(float(t.pnl or 0) for t in ts)
            konta = {t.account_id for t in ts}
            # najblizszy cel fazy wsrod kont w ewaluacji
            dystans = None
            for a in (session.query(Account)
                      .filter(Account.trader_id == tid, Account.status == "active").all()):
                try:
                    cfg = rules.config_from_account(a)
                    m = rules.display_metrics(cfg, balance=a.balance, equity=a.equity,
                                              peak_equity=a.peak_equity,
                                              day_start_equity=a.day_start_equity,
                                              trading_days=a.trading_days_count)
                except Exception:
                    continue
                cel = m.get("target_equity")
                if cel and a.equity is not None and cel > a.equity:
                    d = cel - a.equity
                    dystans = d if dystans is None else min(dystans, d)
            znak = "+" if pnl >= 0 else "−"
            tytul = f"Daily recap: {znak}${abs(pnl):,.0f} yesterday"
            tresc = (f"{len(ts)} trade{'s' if len(ts) != 1 else ''} across "
                     f"{len(konta)} account{'s' if len(konta) != 1 else ''}."
                     + (f" ${dystans:,.0f} to your phase target." if dystans else ""))
            _center_row(session, tid, "daily_recap", tytul, tresc, event_url("daily_recap"))
            session.commit()
            send_to_trader(tid, tytul, tresc, url=event_url("daily_recap"), tag="daily_recap")
            wyslane += 1
        return {"sent": wyslane}
    finally:
        session.close()


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
