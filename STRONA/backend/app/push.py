"""Web push (VAPID) + centrum powiadomień w portalu + dzienny recap.

Kanał jest best-effort jak e-mail w notify.py: każdy błąd łykamy i logujemy,
żeby nigdy nie wywrócić requestu, który powiadomienie tylko „dosyła".
Wpis w centrum powiadomień powstaje zawsze (tania historia dla tradera);
sam push wychodzi wyłącznie, gdy PUSH_ENABLED i klucze VAPID są ustawione,
a trader ma subskrypcje przeglądarkowe. Bramka preferencji jest TA SAMA co
dla maili (_PREF_BY_EVENT w notify.py) — jedno wyłączenie ucisza kategorię
na wszystkich kanałach.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .config import get_settings

settings = get_settings()

# Zdarzenia, ktore NIE trafiaja do centrum/pusha (akcje bezpieczenstwa; mail only).
_SKIP = {"password_reset"}

# Do jakiego widoku portalu prowadzi klik w powiadomienie (reszta -> Challenges).
_EVENT_VIEW = {
    "payout_requested": "payouts", "payout_approved": "payouts", "payout_rejected": "payouts",
    "kyc_approved": "kyc", "kyc_rejected": "kyc",
    "ticket_reply": "support",
    "daily_recap": "analytics",
}

# Krotka tresc do pusha/centrum. NIGDY nie bierzemy tresci maila — mail
# `credentials` zawiera haslo MT5, a push laduje w systemowej historii
# powiadomien urzadzenia.
_SHORT = {
    "welcome": "Your account is ready — pick a challenge and get started.",
    "credentials": "Your MT5 credentials are ready. Open your dashboard to see them.",
    "challenge_granted": "A challenge has been added to your account.",
    "phase_passed": "Congratulations — open the dashboard to see your next phase.",
    "account_funded": "You are funded! Complete KYC and you can request payouts.",
    "breached": "A challenge account hit a rule limit. Review the details.",
    "payout_requested": "We received your payout request — status: under review.",
    "payout_approved": "Your payout is approved. Funds are on the way.",
    "payout_rejected": "Your payout request was declined — see the reason.",
    "kyc_approved": "Identity verified — payouts are unlocked.",
    "kyc_rejected": "We could not verify your identity — please resubmit.",
    "ticket_reply": "Support replied to your ticket.",
}


def push_enabled() -> bool:
    return bool(settings.push_enabled and settings.vapid_public_key and settings.vapid_private_key)


def _webpush_send(subscription_info: dict, data: str) -> None:
    """Cienki wrapper — testy podmieniaja te funkcje zamiast calego pywebpush."""
    from pywebpush import webpush
    webpush(subscription_info=subscription_info, data=data,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_sub or f"mailto:{settings.support_email}"},
            timeout=6)


def deliver(event: str, to_email: str | None, subject: str) -> None:
    """Wpis do centrum powiadomień + fan-out web push (best-effort)."""
    if not to_email or event in _SKIP:
        return
    try:
        _deliver(event, to_email, subject)
    except Exception as e:  # pragma: no cover
        print(f"[push] błąd dostarczania '{event}': {e}")


def _deliver(event: str, to_email: str, subject: str) -> None:
    from .db import SessionLocal
    from .models import Trader
    from .notify import _PREF_BY_EVENT
    session = SessionLocal()
    try:
        tr = session.query(Trader).filter(Trader.email == to_email).first()
        if not tr:
            return
        pref = _PREF_BY_EVENT.get(event)
        if pref:
            val = getattr(tr, pref, True)
            if val is not None and not bool(val):
                return
        _row_and_push(session, tr, event, subject,
                      _SHORT.get(event, "Open your dashboard for details."))
    finally:
        session.close()


def _row_and_push(session, trader, event: str, title: str, body: str) -> None:
    """Wspólna końcówka: wiersz w centrum + push na wszystkie subskrypcje.

    Wywołujący dba o preferencje; tu tylko zapis i wysyłka. Commit na końcu
    obejmuje też kasację martwych subskrypcji (404/410 z push-serwisu).
    """
    from .models import Notification, PushSubscription
    url = f"/portal?view={_EVENT_VIEW.get(event, 'accounts')}"
    session.add(Notification(trader_id=trader.id, event=event[:32],
                             title=title[:200], body=body[:400], url=url))
    stare = (session.query(Notification).filter(Notification.trader_id == trader.id)
             .order_by(Notification.id.desc()).offset(50).all())
    for n in stare:
        session.delete(n)
    session.commit()

    if not push_enabled():
        return
    subs = (session.query(PushSubscription)
            .filter(PushSubscription.trader_id == trader.id).all())
    if not subs:
        return
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": event})
    for sub in subs:
        try:
            _webpush_send({"endpoint": sub.endpoint,
                           "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}, payload)
            sub.fail_count = 0
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                session.delete(sub)          # przegladarka uniewaznila subskrypcje
            else:
                sub.fail_count = (sub.fail_count or 0) + 1
                if sub.fail_count >= 8:      # chroniczny blad = tez do kosza
                    session.delete(sub)
            print(f"[push] webpush {code or e}")
    session.commit()


# --------------------------------------------------------------------------- #
#  Dzienny recap (dopięty do crona /api/tick, raz na dobę)                    #
# --------------------------------------------------------------------------- #
def daily_recap() -> dict:
    """Recap wczorajszego handlu: wynik z transakcji + dystans do celu fazy.

    Zasady: raz na dobę (guard w AppSetting), BRAK transakcji = CISZA (żadnego
    pustego pingu), kategoria notify_marketing (w Settings jako
    „Daily Recap & Offers").
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
            _row_and_push(session, tr, "daily_recap", tytul, tresc)
            wyslane += 1
        return {"sent": wyslane}
    finally:
        session.close()
