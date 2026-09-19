"""Web push (PWA) + centrum powiadomień w portalu + dzienny recap.

Subskrypcje urządzeń trzyma tabela push_subscriptions; wysyłka przez pywebpush
z podpisem VAPID. Brak kluczy w env => push cicho wyłączony (tryb dev, 0 zł),
ale wpisy w centrum powiadomień (dzwonek w portalu) powstają zawsze — to tania
historia dla tradera niezależna od zgody przeglądarki.

Push idzie tą samą bramką preferencji co maile (notify._PREF_BY_EVENT), więc
jeden przełącznik w ustawieniach wyłącza kategorię wszędzie naraz.

Zasada produktu: push NIGDY nie komentuje wyników tradingu w czasie rzeczywistym
— tylko zdarzenia konta (poświadczenia, payout, KYC), przypomnienie o serii
oraz recap ZAMKNIĘTEGO dnia i przegląd ZAMKNIĘTEGO tygodnia.

Generowanie kluczy (raz, wynik do env):  python -m app.push
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
    "weekly_review": "weekly",
    "credits_granted": "store",
    "limit_warning": "accounts",
    "target_50": "accounts", "target_75": "accounts", "min_days_met": "accounts",
    "payout_ready": "payouts",
}

# Krótkie treści pod tytułem (tytuł = temat maila, liczony w notify._render).
# NIGDY nie bierzemy treści maila — `credentials` zawiera hasło MT5, a push
# ląduje w systemowej historii powiadomień urządzenia.
_BODY: dict[str, str] = {
    "welcome": "Your trader portal is ready.",
    "verify_email": "Confirm your e-mail address. The code is in the e-mail.",
    "credentials": "Your MT5 credentials are ready. Log in and start trading.",
    "challenge_granted": "A challenge account was added to your portal.",
    "phase_passed": "Objective complete — your next phase account is on the way.",
    "account_funded": "Your funded account is live. Welcome to the payout side.",
    "account_scaled": "You moved up a plan. The new account is being set up.",
    "breached": "A trading rule was breached on your account. See details.",
    "payout_requested": "We received your payout request. It's under review.",
    "payout_approved": "Your payout was approved.",
    "payout_rejected": "Your payout request needs attention.",
    "kyc_approved": "Identity verified. You're cleared for payouts.",
    "kyc_rejected": "Your verification needs another look.",
    "ticket_reply": "Support replied to your ticket.",
    "credits_granted": "Store credit added. It applies automatically at your next checkout.",
    # Wyjątek od zasady „push nie komentuje wyników na żywo": ostrzeżenie o
    # limicie ISTNIEJE po to, żeby uratować konto — cisza tu kosztuje challenge.
    "limit_warning": "You are close to a trading limit. Slow down and protect the account.",
    # Stan konta, czas przeszły, zero rady. Te cztery treści mają przetrwać
    # lekturę regulaminu: mówią, co się JUŻ stało i co z tego wynika dla reguł,
    # nigdy „ile jeszcze zostało" ani „wykorzystaj to teraz".
    "target_50": "A progress update on your evaluation. Your limits and rules are unchanged.",
    "target_75": "A progress update on your evaluation. Your limits and rules are unchanged.",
    "min_days_met": "The trading-day requirement is now behind you. The profit target still decides when the phase closes.",
    "payout_ready": "Your funded account meets the payout conditions. Requests are made from the portal, whenever you choose.",
}


def is_enabled() -> bool:
    return settings.push_enabled


# Zdarzenia kończące fazę prowadzą do jej podsumowania, a nie do listy kont —
# ale tylko wtedy, gdy nadawca podał, KTÓREGO konta dotyczą.
_RECAP_EVENTS = {"breached", "phase_passed", "account_funded"}


def event_url(event: str, ctx: dict | None = None) -> str:
    acc_id = (ctx or {}).get("account_id")
    if event in _RECAP_EVENTS and acc_id:
        return f"/portal?view=recap&acc={acc_id}"
    return f"/portal?view={_EVENT_VIEW.get(event, 'accounts')}"


def _deliver(sub_info: dict, payload: str) -> None:
    """Jedna wysyłka do push service'u przeglądarki. Wydzielone dla testów."""
    from pywebpush import webpush  # lazy: pakiet zbędny, gdy push wyłączony

    webpush(
        subscription_info=sub_info,
        data=payload,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_sub or f"mailto:{settings.support_email}"},
        # Bez limitu wiszący push service trzymałby wywołanie funkcji aż do
        # maxDuration Vercela — a wysyłamy po odpowiedzi, więc nikt by nawet
        # nie widział, że coś stoi. 5 s na urządzenie ogranicza pech do sekund.
        timeout=5,
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
        url = event_url(event, ctx)
        _center_row(session, trader_id, event, title, _BODY.get(event, ""), url)
        session.commit()
    finally:
        session.close()
    return send_to_trader(trader_id, title, _BODY.get(event, ""), url=url, tag=event)


# --------------------------------------------------------------------------- #
#  Dzienny recap (ruch strony od 06:00 czasu polskiego; cron /api/tick to     #
#  tylko zapas na dzień bez wejść)                                            #
# --------------------------------------------------------------------------- #
WARSZAWA = ZoneInfo("Europe/Warsaw")
# Recap to poranna prasówka ZAMKNIĘTEGO dnia, więc nie może wychodzić o
# godzinie crona w środku dnia. 06:00 w Warszawie ≈ północ w Nowym Jorku
# (różnica 6 h trzyma się prawie cały rok mimo DST) — trader w USA dostaje go
# na szczyt skrzynki na rano, właściciel ma go przy kawie.
RECAP_OD_GODZINY = 6


def daily_recap(now: datetime | None = None) -> dict:
    """Recap wczorajszego handlu: wynik z transakcji + dystans do celu fazy.

    Zasady: nie wcześniej niż 06:00 czasu polskiego, raz na dobę (guard w
    AppSetting), BRAK transakcji = CISZA (żadnego pustego pingu), kategoria
    notify_marketing („Daily Recap & Offers"). Komentuje wyłącznie ZAMKNIĘTY
    dzień — nigdy otwarte pozycje. `now` wstrzykują testy.
    """
    try:
        return _daily_recap(now)
    except Exception as e:  # pragma: no cover
        print(f"[push] recap błąd: {e}")
        return {"sent": 0, "error": str(e)}


def _daily_recap(now: datetime | None = None) -> dict:
    from . import rules
    from .db import SessionLocal
    from .models import Account, AppSetting, Trade, Trader
    teraz = now or datetime.now(timezone.utc)
    # Sprawdzenie godziny idzie PRZED guardem: nocny request nie może zużyć
    # dziennego wpisu, bo wtedy poranna wysyłka by nie wyszła.
    if teraz.astimezone(WARSZAWA).hour < RECAP_OD_GODZINY:
        return {"sent": 0, "skipped": "before 06:00 Europe/Warsaw"}
    session = SessionLocal()
    try:
        dzis = teraz.strftime("%Y-%m-%d")
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
        # Konta firmowe nie maja wlasciciela; bez filtru trafiaja tu jako klucz None
        # i lecimy po nie do bazy tylko po to, by je odrzucic.
        wiersze = (session.query(Trade, Account.trader_id)
                   .join(Account, Trade.account_id == Account.id)
                   .filter(Account.trader_id.isnot(None), Trade.status == "closed",
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


# --------------------------------------------------------------------------- #
#  Tygodniowy przegląd (poniedziałek rano, ten sam ruch strony co recap)       #
# --------------------------------------------------------------------------- #
# Obserwacje opisują WYŁĄCZNIE to, co już się wydarzyło — w czasie przeszłym,
# bez liczby w tytule i bez zdania, które dałoby się przeczytać jako sygnał,
# radę albo cel. To rachunek z zamkniętego tygodnia, nie zachęta do pozycji.
WEEKLY_OD_GODZINY = 6
_DNI = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _kwota(v: float) -> str:
    return f"{'+' if v >= 0 else '−'}${abs(v):,.0f}"


def week_window(now: datetime) -> tuple[datetime, datetime]:
    """Ostatni PEŁNY tydzień jako (poniedziałek, poniedziałek) w naiwnym UTC."""
    dzis = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    poniedzialek = dzis - timedelta(days=now.weekday())
    return poniedzialek - timedelta(days=7), poniedzialek


def weekly_stats(session, trader_id: int, start: datetime, koniec: datetime) -> dict:
    """Siedem dni tygodnia z zamkniętych transakcji jednego tradera."""
    from .models import Account, Trade

    dni = [{"day": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
            "name": _DNI[i], "label": _DNI[i][:3], "pnl": 0.0, "trades": 0}
           for i in range(7)]
    wiersze = (session.query(Trade)
               .join(Account, Trade.account_id == Account.id)
               .filter(Account.trader_id == trader_id, Trade.status == "closed",
                       Trade.closed_at >= start, Trade.closed_at < koniec)
               .all())
    wygrane, konta = 0, set()
    for t in wiersze:
        ts = t.closed_at
        # Część zapisów w bazie ma strefę, część nie (models._utcnow kontra
        # wartości ustawiane wprost) — bez normalizacji odejmowanie by padło.
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        pnl = float(t.pnl or 0)
        i = (ts - start).days
        if 0 <= i < 7:
            dni[i]["pnl"] += pnl
            dni[i]["trades"] += 1
        wygrane += 1 if pnl > 0 else 0
        konta.add(t.account_id)
    for d in dni:
        d["pnl"] = round(d["pnl"], 2)
    handlowe = [d for d in dni if d["trades"]]
    return {
        "from": start.strftime("%Y-%m-%d"),
        "to": (koniec - timedelta(days=1)).strftime("%Y-%m-%d"),
        "days": dni,
        "trades": len(wiersze),
        "accounts": len(konta),
        "net_pnl": round(sum(d["pnl"] for d in dni), 2),
        "win_rate": round(wygrane / len(wiersze) * 100) if wiersze else None,
        "trading_days": len(handlowe),
        "green_days": sum(1 for d in handlowe if d["pnl"] > 0),
        "red_days": sum(1 for d in handlowe if d["pnl"] < 0),
        "best_day": max(handlowe, key=lambda d: d["pnl"]) if handlowe else None,
        "worst_day": min(handlowe, key=lambda d: d["pnl"]) if handlowe else None,
    }


def weekly_observations(s: dict, prev: dict) -> list[tuple[str, str]]:
    """Prawdziwe obserwacje o tym tygodniu, od najbardziej wyrazistej.

    Pierwsza trafia do powiadomienia, trzy pierwsze na ekran przeglądu.
    Ostatnia pozycja jest zawsze prawdziwa, więc lista nigdy nie jest pusta.
    """
    dni = [d for d in s["days"] if d["trades"]]
    if not dni:
        return [("quiet", "No closed trades last week.")]
    ruch = sum(abs(d["pnl"]) for d in dni)
    szczyt = max(dni, key=lambda d: abs(d["pnl"]))
    zielone = sum(d["pnl"] for d in dni if d["pnl"] > 0)
    worst = s["worst_day"]
    out: list[tuple[str, str]] = []

    if len(dni) >= 2 and ruch > 0 and abs(szczyt["pnl"]) >= 0.6 * ruch:
        out.append(("one_day", f"{szczyt['name']} carried the week: {_kwota(szczyt['pnl'])} "
                               f"of the ${ruch:,.0f} that moved across {len(dni)} sessions."))
    if worst and worst["pnl"] < 0 and zielone > 0 and abs(worst["pnl"]) > zielone:
        n = s["green_days"]
        out.append(("gave_back", f"{worst['name']} gave back more than your "
                                 f"{n} green day{'' if n == 1 else 's'} put together."))
    if len(dni) >= 3 and s["red_days"] == 0:
        out.append(("all_green", f"{len(dni)} trading days, none of them red. "
                                 f"Net {_kwota(s['net_pnl'])}."))
    if prev["trades"] >= 5 and s["trades"] >= 1.5 * prev["trades"] and s["trades"] >= 15:
        out.append(("busier", f"You placed {s['trades']} trades, up from "
                              f"{prev['trades']} the week before."))
    if prev["trades"] >= 10 and s["trades"] <= 0.5 * prev["trades"]:
        out.append(("quieter", f"You placed {s['trades']} trades, down from "
                               f"{prev['trades']} the week before."))
    if len(dni) >= 3 and ruch > 0 and abs(szczyt["pnl"]) <= 0.4 * ruch:
        out.append(("steady", f"No single day carried the week — the result came "
                              f"from {len(dni)} sessions."))
    out.append(("plain", f"{s['trades']} trade{'' if s['trades'] == 1 else 's'} across "
                         f"{len(dni)} day{'' if len(dni) == 1 else 's'}. "
                         f"Net {_kwota(s['net_pnl'])}."))
    return out


def weekly_review(now: datetime | None = None) -> dict:
    """Przegląd zamkniętego tygodnia: poniedziałek od 06:00 czasu polskiego.

    Raz na tydzień (guard w AppSetting), kategoria notify_marketing, tydzień
    BEZ transakcji = cisza. Tytuł nigdy nie niesie liczby — kwota w powiadomieniu
    systemowym czytałaby się jak wynik do pobicia. `now` wstrzykują testy.
    """
    try:
        return _weekly_review(now)
    except Exception as e:  # pragma: no cover
        print(f"[push] weekly błąd: {e}")
        return {"sent": 0, "error": str(e)}


def _weekly_review(now: datetime | None = None) -> dict:
    from .db import SessionLocal
    from .models import Account, AppSetting, Trade, Trader
    teraz = now or datetime.now(timezone.utc)
    lokalnie = teraz.astimezone(WARSZAWA)
    if lokalnie.weekday() != 0 or lokalnie.hour < WEEKLY_OD_GODZINY:
        return {"sent": 0, "skipped": "not Monday morning Europe/Warsaw"}
    session = SessionLocal()
    try:
        tydzien = teraz.strftime("%G-W%V")
        guard = session.get(AppSetting, "last_weekly_week")
        if guard and guard.value == tydzien:
            return {"sent": 0, "skipped": "already ran this week"}
        if guard:
            guard.value = tydzien
        else:
            session.add(AppSetting(key="last_weekly_week", value=tydzien))
        session.commit()

        start, koniec = week_window(teraz)
        # Konta firmowe nie maja wlasciciela — bez tego filtru None wpada do zbioru
        # i `sorted` wysypuje CALY przeglad tygodnia, dla wszystkich naraz.
        traderzy = {tid for _, tid in
                    (session.query(Trade.id, Account.trader_id)
                     .join(Account, Trade.account_id == Account.id)
                     .filter(Account.trader_id.isnot(None), Trade.status == "closed",
                             Trade.closed_at >= start, Trade.closed_at < koniec).all())}
        wyslane = 0
        for tid in sorted(traderzy):
            tr = session.get(Trader, tid)
            if not tr or (tr.notify_marketing is not None and not tr.notify_marketing):
                continue
            s = weekly_stats(session, tid, start, koniec)
            if not s["trades"]:
                continue
            obs = weekly_observations(s, weekly_stats(session, tid, start - timedelta(days=7), start))
            # Ta sama obserwacja trzeci tydzień z rzędu przestaje cokolwiek
            # znaczyć — wtedy schodzimy do kolejnej prawdziwej.
            historia = [x for x in (tr.weekly_rules or "").split(",") if x][:2]
            klucz, tresc = next((o for o in obs if historia.count(o[0]) < 2), obs[0])
            tr.weekly_rules = ",".join(([klucz] + historia)[:2])
            tytul = "Your week in review"
            _center_row(session, tid, "weekly_review", tytul, tresc, event_url("weekly_review"))
            session.commit()
            send_to_trader(tid, tytul, tresc, url=event_url("weekly_review"), tag="weekly_review")
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
