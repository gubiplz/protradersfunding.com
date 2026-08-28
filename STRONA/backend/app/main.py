"""FastAPI: REST API + dashboardy.

Strony:
  /         -> landing sprzedażowy (hero, cennik z /api/products, zasady, FAQ)
  /admin    -> panel admina (konta, payouty, KYC, zamówienia)
  /portal   -> portal tradera (rejestracja/logowanie, sklep, moje konta, KYC, wypłaty)
  /docs     -> API docs

Uruchomienie:  uvicorn app.main:app --reload  (z katalogu backend/)
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import random
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from time import monotonic

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from . import (achievements, auth, billing, catalog, certshot, countries, fields, loyalty,
               lead_mail, metaquotes_web, notify,
               payout_import, payoutbot, reach,
               poller, provisioning, push, rules, sms, telegram, telemetry, tradebot)
from .config import get_settings
from .db import SessionLocal, init_db, mark_schema_current, schema_fingerprint
from .models import (LEAD_LOST_STATUSES, LEAD_STATUSES, LOST_REASONS,
                     Account, AchievementReward, AppSetting, Breach, Certificate,
                     CreditLedger, EquitySnapshot, JournalEntry, KycFile, Lead, LeadEvent,
                     LeadMailTemplate, LeadReminder, MailLog, Notification,
                     Order, Payout, PayoutRequest, PoolAccount, Product, PushSubscription,
                     RewardCode, SupportTicket, TelemetryEvent, TicketMessage, Trade, Trader)

settings = get_settings()
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
STATIC = Path(__file__).resolve().parent.parent / "static"
UPLOADS = Path(__file__).resolve().parent.parent / "uploads"


# --------------------------------------------------------------------------- #
#  Oferta (zawsze) + seed demo (admin, traderzy, konta — tylko z AUTO_SEED)    #
# --------------------------------------------------------------------------- #
def sync_catalog() -> None:
    """Wgrywa ofertę z `catalog._CATALOG` do bazy przy KAŻDYM starcie.

    Świadomie POZA flagą `auto_seed`: ta flaga wyłącza dane DEMO (konto admina,
    testowi traderzy), a nie cennik. Produkcja chodzi z AUTO_SEED=false — gdyby
    katalog dalej siedział pod tą flagą, sklep sprzedawałby po cenach z dnia
    założenia bazy i żadna zmiana w kodzie (podwyżka, wycofanie rozmiaru) nigdy
    by tam nie dotarła.
    """
    session = SessionLocal()
    try:
        catalog.seed_products(session)
    finally:
        session.close()


def seed_demo() -> None:
    session = SessionLocal()
    try:
        if not session.query(Trader).filter(Trader.is_admin == True).first():  # noqa: E712
            admin = Trader(email="admin@local", password_hash=auth.hash_password("admin123"),
                           full_name="Administrator", is_admin=True,
                           referral_code="ADMIN", kyc_status="approved")
            session.add(admin)
            session.commit()
            print("[seed] admin: admin@local / admin123  (token panelu: 'admin')")

        # Konta demo tylko w trybie sim (w metaapi konta tworzy realny provisioning)
        if settings.feed == "sim" and session.query(Account).count() == 0:
            demo = [
                ("john@demo.test", "John Carter", "2step-100k", "static"),
                ("anna@demo.test", "Anna Novak", "2step-100k", "trailing"),
                ("peter@demo.test", "Peter Wagner", "2step-25k", "static"),
                ("maria@demo.test", "Maria Lopez", "2step-100k", "static"),
                ("thomas@demo.test", "Thomas Green", "instant-100k", "static"),
            ]
            now = datetime.now(timezone.utc)
            login_seq = 100001
            for email, name, pkey, dd in demo:
                tr = session.query(Trader).filter(Trader.email == email).first()
                if not tr:
                    tr = Trader(email=email, password_hash=auth.hash_password("demo1234"),
                                full_name=name, referral_code=_gen_ref_code(),
                                kyc_status="approved")
                    session.add(tr)
                    session.flush()
                prod = session.query(Product).filter(Product.key == pkey).first()
                session.add(Account(
                    login=str(login_seq), trader_name=name, trader_id=tr.id,
                    platform_login=str(login_seq), platform_password="Demo1234xy",
                    platform_server="PropFunding-SIM",
                    product_key=pkey, preset=pkey, initial_balance=prod.account_size,
                    steps=prod.steps, profit_target_p1=prod.profit_target_p1,
                    profit_target_p2=prod.profit_target_p2, max_daily_loss_pct=prod.max_daily_loss_pct,
                    max_overall_loss_pct=prod.max_overall_loss_pct, min_trading_days=prod.min_trading_days,
                    drawdown_type=dd, profit_split_pct=prod.profit_split_pct,
                    phase="eval_1", status="active",
                    balance=prod.account_size, equity=prod.account_size,
                    peak_equity=prod.account_size, day_start_equity=prod.account_size,
                    day_start_balance=prod.account_size, day_key=now.strftime("%Y-%m-%d"),
                    created_at=now, started_at=now,
                ))
                login_seq += 1
            session.commit()
            print("[seed] utworzono 5 traderów demo + 5 kont (hasło: demo1234)")
    finally:
        session.close()


def _gen_ref_code() -> str:
    return secrets.token_hex(3).upper()


def _migruj_login_admina() -> None:
    """Jednorazowo: konto administratora „admin" → „admin@admin" (hasło: admin).

    Pole logowania w portalu ma type=email, więc goły login „admin" nie
    przechodzi walidacji przeglądarki i admin nie może się zalogować.
    Idempotentne — po zmianie adresu warunek nie łapie już żadnego wiersza.
    Rejestracja wymaga formatu e-mail, więc wiersz „admin" może być tylko
    ręcznie założonym kontem administratora.
    """
    session = SessionLocal()
    try:
        tr = (session.query(Trader)
              .filter(Trader.email == "admin", Trader.is_admin == True)  # noqa: E712
              .first())
        if tr and not session.query(Trader).filter(Trader.email == "admin@admin").first():
            tr.email = "admin@admin"
            tr.password_hash = auth.hash_password("admin")
            session.commit()
            print("[migracja] konto admina: login 'admin' -> 'admin@admin' (haslo: admin)")
    finally:
        session.close()


def _bootstrap_adminow() -> None:
    """Konta administratorów z env `ADMIN_BOOTSTRAP` — bez dostępu do bazy z zewnątrz.

    Format: `email:haslo:Imię Nazwisko` rozdzielane średnikami (imię opcjonalne;
    hasło nie może zawierać dwukropka). Wołane przy każdym starcie procesu, ale
    CELOWO poza bramką fingerprinta schematu: env dodany albo zmieniony między
    deployami działa od następnego zimnego startu, bez redeployu. Koszt trzyma
    w ryzach własny znacznik — bez env return za darmo, z envem jeden SELECT.
    Env jest źródłem prawdy: zmiana hasła nadpisuje hasło w bazie. Hash jest
    przepisywany TYLKO gdy hasło faktycznie się zmieniło — nowy hash to nowa
    sól, a odcisk hasła w tokenach (pwf) wylogowałby wszystkie sesje konta.
    """
    if not settings.admin_bootstrap:
        return
    odcisk = hashlib.sha256(settings.admin_bootstrap.encode()).hexdigest()[:16]
    session = SessionLocal()
    try:
        znacznik = session.get(AppSetting, "admin_bootstrap_fp")
        if znacznik is not None and znacznik.value == odcisk:
            return
        for wpis in settings.admin_bootstrap.split(";"):
            wpis = wpis.strip()
            if not wpis:
                continue
            czesci = wpis.split(":", 2)
            if len(czesci) < 2 or not czesci[0].strip() or not czesci[1]:
                print(f"[bootstrap] pomijam niepoprawny wpis ADMIN_BOOTSTRAP: {wpis[:24]!r}…")
                continue
            email = czesci[0].strip().lower()
            haslo = czesci[1]
            imie = czesci[2].strip() if len(czesci) > 2 and czesci[2].strip() else "Administrator"
            tr = session.query(Trader).filter(Trader.email == email).first()
            if tr is None:
                # referral_code ma UNIQUE, a prefiks bierzemy z e-maila — dwaj
                # admini "bartek@…" nie mogą dostać tego samego kodu.
                kod = f"ADM{email[:4].upper()}"
                while session.query(Trader).filter(Trader.referral_code == kod).first():
                    kod = "ADM" + secrets.token_hex(2).upper()
                session.add(Trader(email=email, password_hash=auth.hash_password(haslo),
                                   full_name=imie, referral_code=kod,
                                   is_admin=True, kyc_status="approved"))
                # flush: sesja nie ma autoflusha, a SELECT unikalnosci kodu w
                # nastepnym obiegu musi widziec ten wiersz (dwaj "bartek@…").
                session.flush()
                print(f"[bootstrap] admin utworzony: {email}")
            else:
                if not auth.verify_password(haslo, tr.password_hash):
                    tr.password_hash = auth.hash_password(haslo)
                    print(f"[bootstrap] admin {email}: nowe haslo z env")
                tr.is_admin = True
                if not tr.full_name:
                    tr.full_name = imie
        if znacznik is None:
            session.add(AppSetting(key="admin_bootstrap_fp", value=odcisk))
        else:
            znacznik.value = odcisk
        session.commit()
    except Exception as e:  # pragma: no cover
        # Np. wyscig dwoch rownoleglych zimnych startow o UNIQUE(email) —
        # przegrany przebieg odpuszcza (zwyciezca juz zalozyl konta); start
        # procesu nie moze sie wywrocic przez bootstrap.
        session.rollback()
        print(f"[bootstrap] blad: {e}")
    finally:
        session.close()


def _warn_if_placeholder_provisioning() -> None:
    """Log startowy: w którym trybie stoi provisioning i skąd biorą się poświadczenia."""
    if provisioning.real_provisioning_enabled(settings):
        channel = "web terminal MetaQuotes" if settings.metaquotes_web_enabled else "MetaApi"
        print(f"[start] provisioning: REALNE konta MT5 przez {channel}")
        if settings.metaquotes_web_enabled:
            from . import metaquotes_web
            if not metaquotes_web.chromium_available():
                print("[start] BLAD KONFIGURACJI: brak przegladarki Playwrighta — konta utkna w "
                      "statusie 'provisioning'.\n"
                      "        Napraw jednorazowo:  playwright install chromium")
    else:
        print(f"[start] provisioning: LOKALNY — konta dostają wygenerowane poświadczenia "
              f"(serwer '{provisioning.PLATFORM_SERVER}') i są aktywne od razu, bez zakładania "
              f"konta demo.\n"
              f"        Realne konta MT5: MT5_PROVISIONING=true + METAQUOTES_WEB_ENABLED=true w .env")
    if settings.feed == "local":
        print("[start] feed: LOCAL — equity nie jest czytane z zewnątrz; ruch daje Trade BOT.")


def _przygotuj_baze() -> None:
    """Migracje schematu i cennik: raz na deploy, nie przy każdym zimnym starcie.

    Na serverless proces wstaje od nowa co kilka minut, a ta ścieżka to 33
    round-tripy do bazy (reflekcja schematu, ALTER-y, upsert oferty). Przy
    Postgresie po drugiej stronie łącza doklejają się w całości do PIERWSZEGO
    żądania użytkownika. Wynik zależy wyłącznie od wersji kodu, więc odkładamy
    odcisk deployu w bazie i kolejne starty tej samej wersji sprawdzają go
    jednym SELECT-em.

    Bez `VERCEL_GIT_COMMIT_SHA` (lokalnie, testy) odcisk jest pusty i pełna
    ścieżka idzie zawsze — tam modele zmieniają się między restartami bez
    żadnego commita, a baza jest tuż obok i nic to nie kosztuje.
    """
    odcisk = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    if odcisk and schema_fingerprint() == odcisk:
        return
    init_db()
    _migruj_login_admina()
    sync_catalog()    # oferta i cennik z kodu — niezależnie od trybu
    if settings.auto_seed:
        seed_demo()   # admin zawsze; konta demo tylko w trybie sim
    if odcisk:
        mark_schema_current(odcisk)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _przygotuj_baze()
    # Poza _przygotuj_baze, bo tamta ścieżka biegnie raz na DEPLOY — a env
    # ADMIN_BOOTSTRAP dodany między deployami ma działać od następnego zimnego
    # startu. Funkcja sama pilnuje kosztu własnym znacznikiem w bazie.
    _bootstrap_adminow()
    _warn_if_placeholder_provisioning()
    if settings.poller_enabled:
        poller.start()
    yield
    await poller.stop()


# Swagger/OpenAPI schowane za bramka admina (routy nizej) — publiczne /docs
# wystawialoby cala mape API kazdemu.
app = FastAPI(title=f"{settings.site_name} API", version="0.7.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
class _CachedStatic(StaticFiles):
    """StaticFiles z sensownym Cache-Control.

    Domyślnie Vercel odsyła assety z `max-age=0, must-revalidate`, więc
    przeglądarka pyta o KAŻDY plik przy każdej nawigacji (zmierzone: 14 zapytań
    na wejście, ~0,5 s zmarnowane), a CDN nic nie trzyma — fonty i obrazki
    generuje funkcja Pythona po drugiej stronie Atlantyku.

    Linki do CSS/JS z szablonów mają `?v=<sha deployu>`, więc pod tym samym
    adresem treść nigdy się nie zmienia — mogą wisieć rok jako `immutable`.
    Reszta idzie po ryzyku podmiany: fonty i biblioteki nie zmieniają się nigdy,
    obrazki potrafią (podmiana logo ma zejść na dół w ciągu doby, nie miesiąca).
    """

    def _cache_control(self, path: str, query: bytes) -> str:
        # `s-maxage` jest konieczne osobno: edge Vercela ignoruje samo `max-age`
        # przy odpowiedziach z funkcji, więc bez niego każdy asset schodzi z
        # lambdy (zmierzone: 18 kB ScrollTriggera potrafiło iść 2,8 s).
        if b"v=" in query:
            return "public, max-age=31536000, s-maxage=31536000, immutable"
        if path.startswith(("fonts/", "lib/")):
            return "public, max-age=2592000, s-maxage=2592000"
        return "public, max-age=86400, s-maxage=86400"

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code in (200, 304):
            resp.headers["Cache-Control"] = self._cache_control(
                path, scope.get("query_string", b""))
        return resp


app.mount("/static", _CachedStatic(directory=str(STATIC)), name="static")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return FileResponse(str(STATIC / "robots.txt"), media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    base = _public_base(request)
    paths = ["/", "/objectives", "/faq", "/academy", "/affiliate", "/install", "/verify",
             "/terms", "/privacy", "/risk-disclosure", "/refund-policy"]
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in paths)
    return Response('<?xml version="1.0" encoding="UTF-8"?>'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f"{urls}</urlset>",
                    media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=86400, s-maxage=86400"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(STATIC / "img" / "favicon.png"), media_type="image/png")


@app.get("/countries.js", include_in_schema=False)
def countries_js():
    """Spis krajów osobnym plikiem, nie wklejony w stronę.

    Kierunkowe i strefy czasowe to 31 kB, a HTML musi lecieć `no-cache` — szły
    więc po łączu przy KAŻDYM wejściu na portal. Treść zmienia się wyłącznie
    z deployem, więc pod adresem z `?v=<sha>` może wisieć w cache rok.

    Zwykły `<script>`, nie `fetch`: skrypty klasyczne wykonują się w kolejności
    dokumentu, więc dane są na miejscu, zanim ruszy kod portalu — flaga przy
    numerze kierunkowym rysuje się od razu, bez doczytywania.
    """
    return Response(f"window.PF_GEO={json.dumps(countries.payload(), separators=(',', ':'))};",
                    media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=31536000, s-maxage=31536000, immutable"})


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Serwowany z korzenia (nie /static/), bo scope service workera nie może
    # wychodzić ponad katalog, z którego jest zarejestrowany. no-cache: nowa
    # wersja SW ma się instalować od razu, nie po wygaśnięciu cache.
    return FileResponse(str(STATIC / "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


# --------------------------------------------------------------------------- #
#  Serializery                                                                 #
# --------------------------------------------------------------------------- #
def _metrics_for(acc: Account) -> dict:
    cfg = rules.config_from_account(acc)
    return rules.display_metrics(
        cfg, balance=acc.balance, equity=acc.equity,
        peak_equity=acc.peak_equity, day_start_equity=acc.day_start_equity,
        trading_days=acc.trading_days_count,
    )


def _account_dict(acc: Account, with_metrics: bool = True, with_credentials: bool = False,
                  admin_view: bool = False) -> dict:
    """`with_credentials` MUSI zostać False na endpointach bez autoryzacji.

    Hasło do MT5 daje pełną kontrolę nad rachunkiem tradera, więc wychodzi
    wyłącznie do właściciela konta (`/api/me/accounts`) i do admina.

    `admin_view` odsłania pola Trade BOT-a. Trader ich NIE dostaje — jego
    dashboard ma wyglądać jak dashboard tradera, a nie panel diagnostyczny.
    """
    d = {
        "id": acc.id, "login": acc.login, "trader_name": acc.trader_name,
        "trader_id": acc.trader_id, "product_key": acc.product_key,
        "initial_balance": acc.initial_balance, "steps": acc.steps,
        "drawdown_type": acc.drawdown_type, "profit_split_pct": acc.profit_split_pct,
        "weekend_trading": bool(getattr(acc, "weekend_trading", False)),
        "max_lots": getattr(acc, "max_lots", 0.0) or 0.0,
        "phase": acc.phase, "status": acc.status,
        "balance": round(acc.balance, 2), "equity": round(acc.equity, 2),
        "open_pnl": round(acc.open_pnl, 2), "breach_reason": acc.breach_reason,
        "platform_login": acc.platform_login,
        "platform_server": acc.platform_server,
        "cert_token": acc.cert_token,
        "source": acc.source, "grant_note": acc.grant_note,
        "bogo_paid_size": getattr(acc, "bogo_paid_size", None),
        "created_at": acc.created_at.isoformat() if acc.created_at else None,
        # Rozmiar następnego planu ALBO None (konto poniżej progu albo już na
        # szczycie drabiny). Portal rysuje z tego wybór „wypłata czy wyższy
        # plan", więc liczy to jedno miejsce w kodzie.
        "scale_up_to": poller.scale_offer(acc),
        "scale_trigger_pct": poller.SCALE_TRIGGER_PCT,
        "scale_count": int(getattr(acc, "scale_count", 0) or 0),
    }
    if admin_view:
        d["mt5_backed"] = bool(getattr(acc, "mt5_backed", True))
        d["bot_enabled"] = bool(getattr(acc, "bot_enabled", False))
        d["bot_paused"] = bool(getattr(acc, "bot_paused", False))
        d["bot_style"] = getattr(acc, "bot_style", None)
        # Przez `normalize_pace`, bo konta włączone przed przejściem na tempa
        # liczone w transakcjach na dzień mają w bazie starą nazwę.
        d["bot_pace"] = (tradebot.normalize_pace(acc.bot_pace)
                         if getattr(acc, "bot_pace", None) else None)
        d["bot_target_pct"] = getattr(acc, "bot_target_pct", 0.0) or 0.0
        # Weekend liczony w czasie serwera MT5, a nie w przeglądarce admina —
        # inaczej panel pokazywałby inny stan rynku niż ten, którym kieruje się bot.
        d["market_closed"] = tradebot.market_closed_for(acc)
    if with_credentials:
        d["platform_password"] = acc.platform_password
    if with_metrics:
        d["metrics"] = _metrics_for(acc)
    return d


def _cert_eligible(acc: Account) -> bool:
    return not (acc.phase == "eval_1" and acc.status != "funded")


def _ensure_cert_token(session, acc: Account) -> str | None:
    if not _cert_eligible(acc):
        return None
    if not acc.cert_token:
        acc.cert_token = secrets.token_urlsafe(16)[:32]
        session.commit()
    return acc.cert_token


def _mask_name(full_name: str) -> str:
    """Publiczny ranking nie może ujawniać pełnych nazwisk klientów."""
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return "Anonymous Trader"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


CURVE_POINTS = 300


def _curve_from_snapshots(session, acc: Account) -> list[dict]:
    """Awaryjna krzywa dla kont bez ani jednej transakcji (np. sam feed sim).

    Próbkuje CAŁĄ historię, a nie ostatnie 300 tyknięć pollera — przy stojącym
    koncie te ostatnie mają identyczne equity i dawały płaską kreskę zamiast
    wykresu.
    """
    q = session.query(EquitySnapshot).filter(EquitySnapshot.account_id == acc.id)
    total = q.count()
    if total <= CURVE_POINTS:
        snaps = q.order_by(EquitySnapshot.ts).all()
    else:
        step = total // CURVE_POINTS + 1
        snaps = q.filter(EquitySnapshot.id % step == 0).order_by(EquitySnapshot.ts).all()
        last = q.order_by(EquitySnapshot.ts.desc()).first()
        if last is not None and (not snaps or snaps[-1].id != last.id):
            snaps.append(last)
    return [{"i": i, "ts": s.ts.isoformat(), "equity": round(s.equity, 2),
             "balance": round(s.balance, 2), "kind": "tick"} for i, s in enumerate(snaps)]


def _equity_curve(session, acc: Account) -> list[dict]:
    """Krzywa equity indeksowana LICZBĄ TRANSAKCJI — jak na dashboardach propów.

    Punkt 0 to kapitał startowy, każdy kolejny — stan konta po n-tej zamkniętej
    transakcji (`i` = numer transakcji na osi poziomej). Wypłaty wchodzą
    chronologicznie jako krok w dół przy tym samym `i` (nie są transakcją), więc
    ostatni punkt zgadza się co do centa z saldem konta. Otwarta pozycja dokłada
    punkt z bieżącym equity — tam i tylko tam `equity` różni się od `balance`.
    """
    trades = (session.query(Trade)
              .filter(Trade.account_id == acc.id, Trade.status == "closed")
              .order_by(Trade.closed_at, Trade.id).all())
    if not trades:
        return _curve_from_snapshots(session, acc)

    payouts = (session.query(Payout)
               .filter(Payout.account_id == acc.id, Payout.balance_reset.is_(True),
                       Payout.profit_amount > 0)
               .order_by(Payout.ts).all())
    running = acc.initial_balance
    first_ts = trades[0].opened_at or trades[0].closed_at
    out = [{"i": 0, "ts": first_ts.isoformat(), "equity": round(running, 2),
            "balance": round(running, 2), "kind": "start"}]

    def _drain_payouts(until, at_index: int) -> None:
        nonlocal running
        while payouts and (until is None or (payouts[0].ts and payouts[0].ts <= until)):
            p = payouts.pop(0)
            running -= p.profit_amount
            out.append({"i": at_index, "ts": p.ts.isoformat(), "equity": round(running, 2),
                        "balance": round(running, 2), "kind": "payout",
                        "payout": round(p.profit_amount, 2)})

    for n, t in enumerate(trades, start=1):
        closed = t.closed_at or t.opened_at
        _drain_payouts(closed, n - 1)
        running += t.pnl
        out.append({"i": n, "ts": closed.isoformat(), "equity": round(running, 2),
                    "balance": round(running, 2), "kind": "trade", "symbol": t.symbol,
                    "side": t.side, "lots": t.lots, "pnl": round(t.pnl, 2)})
    _drain_payouts(None, len(trades))

    floating = sum(t.pnl for t in session.query(Trade)
                   .filter(Trade.account_id == acc.id, Trade.status == "open").all())
    if floating:
        out.append({"i": len(trades) + 1, "ts": datetime.now(timezone.utc).isoformat(),
                    "equity": round(running + floating, 2), "balance": round(running, 2),
                    "kind": "open", "pnl": round(floating, 2)})
    return out


def _account_detail(session, acc: Account, admin_view: bool = False) -> dict:
    """Pełny widok konta: metryki + krzywa equity + breachy + wypłaty."""
    breaches = (session.query(Breach).filter(Breach.account_id == acc.id)
                .order_by(Breach.ts.desc()).all())
    payouts = session.query(Payout).filter(Payout.account_id == acc.id).all()
    preqs = session.query(PayoutRequest).filter(PayoutRequest.account_id == acc.id).all()
    d = _account_dict(acc, with_credentials=True, admin_view=admin_view)
    if admin_view and acc.trader_id:
        tr = session.get(Trader, acc.trader_id)
        d["trader_email"] = tr.email if tr else None
        # Slide-over pokazuje akcje zaproszenia do portalu tylko klientom,
        # których konto założono ZA nich i hasło wciąż nie istnieje.
        d["trader_must_set_password"] = bool(tr.must_set_password) if tr else False
    d["equity_curve"] = _equity_curve(session, acc)
    d["breaches"] = [{"ts": b.ts.isoformat(), "type": b.type, "detail": b.detail} for b in breaches]
    d["payouts"] = [{"ts": p.ts.isoformat(), "profit_amount": p.profit_amount,
                     "trader_share": p.trader_share, "paid": p.paid} for p in payouts]
    d["payout_requests"] = [{"id": r.id, "profit_amount": r.profit_amount,
                             "trader_share": r.trader_share, "status": r.status} for r in preqs]
    return d


def _product_dict(p: Product) -> dict:
    return {"key": p.key, "label": p.label, "account_size": p.account_size, "steps": p.steps,
            "popular": p.account_size == catalog.POPULAR_SIZE,
            "price_usd": p.price_usd, "profit_target_p1": p.profit_target_p1,
            "profit_target_p2": p.profit_target_p2, "max_daily_loss_pct": p.max_daily_loss_pct,
            "max_overall_loss_pct": p.max_overall_loss_pct, "drawdown_type": p.drawdown_type,
            "min_trading_days": p.min_trading_days, "profit_split_pct": p.profit_split_pct,
            "max_lots": getattr(p, "max_lots", 0.0) or 0.0}


def _maile_traderow(session, trader_ids) -> dict[int, str]:
    """Maile wielu traderów jednym zapytaniem — listy w panelu pokazują je przy
    każdym wierszu, a `session.get` w pętli to tyle round-tripów, ile wierszy."""
    ids = {i for i in trader_ids if i}
    if not ids:
        return {}
    return dict(session.query(Trader.id, Trader.email).filter(Trader.id.in_(ids)).all())


def _konta_po_id(session, account_ids) -> dict[int, Account]:
    ids = {i for i in account_ids if i}
    if not ids:
        return {}
    return {a.id: a for a in session.query(Account).filter(Account.id.in_(ids)).all()}


# --- wiersze techniczne z importu ewidencji ---------------------------------
# Za adresem `…@imported.local` nie stoi klient, tylko wiersz z CSV-ki wypłat
# (`payout_import._email_techniczny`). W księdze pieniędzy są niezbędne, ale na
# listach, po których admin szuka LUDZI, tonią prawdziwe wiersze. Filtr jest po
# stronie serwera, bo to nazwiska prawdziwych osób — nie ma powodu wysyłać ich
# do przeglądarki tylko po to, żeby JS je schował.

def _nie_import():
    return ~Trader.email.like(f"%{payout_import.TECHNICZNA_DOMENA}")


def _konto_nie_import(session):
    """To samo dla zapytań po `Account` — z jawną gałęzią na NULL.

    `Account.trader_id` jest nullowalny (konta z puli nie mają właściciela), a
    `NOT IN (…)` daje w SQL-u dla NULL-a wynik NULL, czyli „nie przechodzi".
    Bez `is_(None)` obok, włączenie filtru po cichu zabierałoby z listy całą
    pulę — awaria, która wygląda jak działający filtr.
    """
    techniczni = session.query(Trader.id).filter(
        Trader.email.like(f"%{payout_import.TECHNICZNA_DOMENA}"))
    return or_(Account.trader_id.is_(None), ~Account.trader_id.in_(techniczni))


def _konto_wreczone():
    """Konto, które NAPRAWDĘ komuś wydaliśmy — bez wierszy archiwalnych.

    Import ewidencji i Payout BOT zakładają konto pod wypłatę, która wydarzyła
    się kiedyś indziej (`payout_import.zapisz`): `source="grant"`, więc dla
    każdego guardu pytającego „czy ten człowiek już coś ode mnie dostał"
    wygląda jak wręczony challenge. Adres tam bywa PRAWDZIWY (techniczny jest
    tylko fallbackiem dla wierszy bez maila), więc filtr po `@imported.local`
    tych wierszy nie łapie.

    Skutek bez tego wyjątku: ktoś, komu kiedyś wypłaciliśmy pieniądze — czyli
    najlepszy możliwy kandydat — jako jedyny nie mógł dostać darmowego
    challenge'a, a panel twierdził, że konto już ma.

    Gałąź na NULL jest konieczna: `!=` daje w SQL-u dla NULL-a wynik NULL, więc
    bez niej wypadłyby wszystkie konta bez notatki, czyli kupione.
    """
    return or_(Account.grant_note.is_(None),
               Account.grant_note != payout_import.IMPORT_NOTE)


# --------------------------------------------------------------------------- #
#  AUTH / onboarding                                                          #
# --------------------------------------------------------------------------- #
class SignupIn(BaseModel):
    email: str
    password: str
    full_name: str = ""
    referral: str | None = None
    terms_accepted: bool = False


class LoginIn(BaseModel):
    email: str
    password: str


SESSION_COOKIE = "pf_session"

# Prosty rate-limit per proces (endpointy auth: credential stuffing, mail-bombing
# przez /forgot i masowe zakladanie kont). Na serverless chroni per-instancje —
# swiadome minimum bez zewnetrznego magazynu; okno przesuwne 60 s.
_RL_HITS: dict[tuple[str, str], list[float]] = {}
_RL_DISABLED = os.getenv("RATE_LIMIT_OFF", "false").lower() == "true"
# Wzorzec mieszka teraz w `fields`, zeby serwer i formularze mialy jedna regule.
_EMAIL_RX = fields.EMAIL_RX


def _rate_limit(request: Request, bucket: str, limit: int, window: int = 60) -> None:
    if _RL_DISABLED:
        return
    ip = (request.headers.get("x-forwarded-for")
          or (request.client.host if request.client else "?")).split(",")[0].strip()
    now = time.time()
    key = (bucket, ip)
    hits = [t for t in _RL_HITS.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    hits.append(now)
    _RL_HITS[key] = hits
    if len(_RL_HITS) > 10_000:                      # GC starych wpisow
        for k in [k for k, v in _RL_HITS.items() if not v or now - v[-1] > window]:
            _RL_HITS.pop(k, None)


def _ustaw_ciasteczko_sesji(response: Response, token: str) -> None:
    """Zapisuje sesje w ciasteczku, zeby SERWER mogl bramkowac strone /admin.

    API dalej uwierzytelnia sie naglowkiem Authorization — ciasteczko sluzy
    wylacznie do decyzji „czy w ogole wydac te strone", wiec nie otwiera drogi
    do CSRF. HttpOnly, bo JavaScript i tak trzyma swoj token osobno.
    """
    response.set_cookie(SESSION_COOKIE, token, max_age=auth.TOKEN_MAX_AGE,
                        httponly=True, samesite="lax",
                        secure=settings.app_base_url.startswith("https"))


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


@app.post("/api/auth/forgot")
def forgot_password(payload: ForgotIn, request: Request):
    """Zawsze 200 — odpowiedź nie może zdradzać, czy e-mail istnieje w bazie.

    Dopasowanie przez `lower()` po OBU stronach: adresy z importu wypłat i z
    Google bywają w bazie z wielkiej litery, a porównanie 1:1 kończyło się dla
    nich cichym „wysłaliśmy" bez maila. To akurat ci klienci, którzy hasła
    nigdy nie ustawiali, więc reset jest ich jedyną drogą do portalu.
    """
    _rate_limit(request, "forgot", 5)
    session = SessionLocal()
    try:
        tr = (session.query(Trader)
              .filter(func.lower(Trader.email) == payload.email.strip().lower()).first())
        if tr:
            # Odcisk hasła w tokenie = link jednorazowy (po resecie hash sie
            # zmienia i ten sam link juz nie przejdzie walidacji).
            token = auth.make_reset_token(tr.id, tr.password_hash)
            reset_url = f"{_public_base(request)}/portal?reset={token}"
            notify.send("password_reset", tr.email,
                        {"name": tr.full_name or tr.email, "reset_url": reset_url})
        return {"ok": True, "message": "If that e-mail exists, a reset link is on its way."}
    finally:
        session.close()


@app.post("/api/auth/reset")
def reset_password(payload: ResetIn, response: Response):
    # Dwa rodzaje linków kończą się na tym samym ekranie: godzinny reset
    # („forgot password") i 7-dniowe zaproszenie do konta założonego za
    # klienta. Różnią się tylko solą i oknem ważności — walidacja odcisku
    # hasła niżej jest wspólna, więc oba są jednorazowe.
    parsed = auth.parse_reset_token(payload.token) or auth.parse_setup_token(payload.token)
    if parsed is None:
        raise HTTPException(400, "This reset link is invalid or has expired. Request a new one")
    tid, pwf = parsed
    if len(payload.password) < 8:
        raise HTTPException(400, "The password must be at least 8 characters long")
    session = SessionLocal()
    try:
        tr = session.get(Trader, tid)
        if not tr:
            raise HTTPException(400, "This reset link is invalid or has expired. Request a new one")
        if pwf and pwf != auth._pw_fp(tr.password_hash):
            # Hash już inny niż przy wysyłce linku — link zużyty albo hasło
            # zmienione w międzyczasie. (Tokeny bez pwf: krotkie okno przejsciowe.)
            raise HTTPException(400, "This reset link has already been used. Request a new one")
        # PRZED zapisem, bo za chwilę flaga znika: to jedyne miejsce, w którym
        # widać różnicę między „klient odebrał konto z zaproszenia" (dziennik
        # w panelu żyje z tego rozróżnienia) a zwykłym „zapomniałem hasła".
        odebral_konto = bool(tr.must_set_password)
        tr.password_hash = auth.hash_password(payload.password)
        # Hasło właśnie zaczęło istnieć — konto przestaje być „założone za kogoś".
        tr.must_set_password = False
        session.commit()
        telemetry.track("account_claimed" if odebral_konto else "password_reset", tr.id)
        # Auto-login po udanym resecie — nowy token jest zwiazany z nowym haslem,
        # wszystkie starsze sesje wlasnie umarly.
        token = auth.make_token(tr.id, tr.password_hash)
        _ustaw_ciasteczko_sesji(response, token)
        return {"ok": True, "token": token,
                "trader": {"id": tr.id, "email": tr.email, "full_name": tr.full_name,
                           "is_admin": tr.is_admin, "referral_code": tr.referral_code}}
    finally:
        session.close()


def _wyslij_mail_weryfikacyjny(request: Request, tr: Trader) -> None:
    verify_url = f"{_public_base(request)}/portal?verify={auth.make_verify_token(tr.id)}"
    notify.send("verify_email", tr.email,
                {"name": tr.full_name or tr.email, "code": tr.email_verify_code,
                 "verify_url": verify_url})


def _potwierdz_email(session, tr: Trader) -> None:
    if tr.email_verified:
        return
    tr.email_verified = True
    tr.email_verify_code = None
    session.commit()
    notify.send("welcome", tr.email, {"name": tr.full_name or tr.email})


class VerifyTokenIn(BaseModel):
    token: str


@app.post("/api/auth/verify-email")
def verify_email(payload: VerifyTokenIn):
    """Weryfikacja linkiem z maila — działa również bez zalogowania."""
    tid = auth.parse_verify_token(payload.token)
    session = SessionLocal()
    try:
        tr = session.get(Trader, tid) if tid else None
        if not tr:
            raise HTTPException(400, "This verification link is invalid or has expired. Request a new one")
        _potwierdz_email(session, tr)
        return {"ok": True}
    finally:
        session.close()


class VerifyCodeIn(BaseModel):
    code: str


@app.post("/api/me/verify-email")
def verify_email_code(payload: VerifyCodeIn, request: Request,
                      trader: Trader = Depends(auth.current_trader)):
    # 6-cyfrowy kod bez limitu prób dałoby się brute-force'ować (1M kombinacji).
    _rate_limit(request, "verify_code", 5)
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if not tr.email_verified:
            if not tr.email_verify_code or payload.code.strip() != tr.email_verify_code:
                raise HTTPException(400, "Wrong code. Check the e-mail we sent you")
            _potwierdz_email(session, tr)
        return {"ok": True}
    finally:
        session.close()


@app.post("/api/me/verify-email/resend")
def resend_verify_email(request: Request, trader: Trader = Depends(auth.current_trader)):
    # Bez limitu = mail-bombing wpisanego adresu i palenie kwoty SMTP.
    _rate_limit(request, "verify_resend", 3)
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if not tr.email_verified:
            tr.email_verify_code = f"{secrets.randbelow(1_000_000):06d}"
            session.commit()
            _wyslij_mail_weryfikacyjny(request, tr)
        return {"ok": True}
    finally:
        session.close()


class ChangeEmailIn(BaseModel):
    email: str


@app.post("/api/me/verify-email/change-address")
def change_unverified_email(payload: ChangeEmailIn, request: Request,
                            trader: Trader = Depends(auth.current_trader)):
    """Poprawka literówki w adresie PRZED weryfikacją. Po potwierdzeniu adres
    jest tożsamością konta (login, faktury, poświadczenia MT5) — zmienia go
    już tylko support."""
    _rate_limit(request, "verify_change", 5)
    email = payload.email.strip().lower()
    if not _EMAIL_RX.fullmatch(email):
        raise HTTPException(400, "Enter a valid e-mail address")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if tr.email_verified:
            raise HTTPException(400, "Your e-mail is already verified. Contact support to change it")
        if email == tr.email:
            raise HTTPException(400, "This is already the address on your account")
        if session.query(Trader).filter(Trader.email == email).first():
            raise HTTPException(409, "An account with this e-mail already exists")
        tr.email = email
        tr.email_verify_code = f"{secrets.randbelow(1_000_000):06d}"
        session.commit()
        _wyslij_mail_weryfikacyjny(request, tr)
        return {"ok": True, "email": tr.email}
    finally:
        session.close()


@app.post("/api/auth/signup")
def signup(payload: SignupIn, request: Request, response: Response):
    _rate_limit(request, "signup", 5)
    email = payload.email.strip().lower()
    if not _EMAIL_RX.fullmatch(email):
        raise HTTPException(400, "Enter a valid e-mail address")
    # Login/zmiana hasla wymagaja >=8 — signup nie moze byc jedyna furtka
    # do zalozenia konta ze slabszym haslem.
    if len(payload.password) < 8:
        raise HTTPException(400, "The password must be at least 8 characters long")
    if not payload.terms_accepted:
        raise HTTPException(400, "You must accept the Terms of Service and Privacy Policy")
    # Pole bywa ukryte (rejestracja przez Google), wiec puste zostaje dozwolone —
    # ale jesli cos wpisano, ma to byc imie i nazwisko, a nie „Dawid53".
    nazwa = (payload.full_name or "").strip()
    if nazwa:
        try:
            nazwa = fields.person_name(nazwa, "Full name")
        except ValueError as e:
            raise HTTPException(400, str(e))
    session = SessionLocal()
    try:
        istnieje = session.query(Trader).filter(Trader.email == email).first()
        if istnieje:
            # Samo „konto już istnieje" brzmi jak pomyłka serwera, gdy człowiek
            # zakładał je przez Google (wtedy hasła NIE MA i logowanie hasłem
            # nigdy nie zadziała) albo urwał rejestrację na kodzie z maila.
            # Odpowiedź mówi, którędy wejść — bez tego jedyną drogą jest support.
            if istnieje.google_sub:
                raise HTTPException(400, "This e-mail is already signed up with Google — "
                                         "use the “Continue with Google” button below")
            if not istnieje.email_verified:
                raise HTTPException(400, "This e-mail is already registered but not confirmed yet. "
                                         "Log in and we will send you a new confirmation code")
            raise HTTPException(400, "An account with this e-mail already exists. "
                                     "Log in instead, or use “Forgot password?”")
        # Kod polecajacy tylko istniejacy — literowka nie moze cicho przypisac
        # prowizji do nikogo (ani zostac w bazie jako smiec).
        referred_by = None
        if payload.referral:
            ref = payload.referral.strip().upper()
            if session.query(Trader).filter(Trader.referral_code == ref).first():
                referred_by = ref
        # token_hex(3) to 16.7 mln kombinacji — kolizja rzadka, ale UNIQUE
        # na kolumnie zamienialby ja w 500; kilka prob zamyka temat.
        for _ in range(5):
            code = _gen_ref_code()
            if not session.query(Trader).filter(Trader.referral_code == code).first():
                break
        tr = Trader(
            email=email, password_hash=auth.hash_password(payload.password),
            full_name=nazwa, referral_code=code,
            referred_by=referred_by,
            email_verified=False, email_verify_code=f"{secrets.randbelow(1_000_000):06d}",
            terms_accepted_at=datetime.now(timezone.utc),
        )
        session.add(tr)
        session.commit()
        # welcome idzie dopiero po potwierdzeniu adresu — najpierw sam kod
        _wyslij_mail_weryfikacyjny(request, tr)
        telemetry.track("signup", tr.id, referred=bool(referred_by))
        token = auth.make_token(tr.id, tr.password_hash)
        _ustaw_ciasteczko_sesji(response, token)
        return {"token": token, "trader": {"id": tr.id, "email": tr.email,
                "full_name": tr.full_name, "referral_code": tr.referral_code}}
    finally:
        session.close()


@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request, response: Response):
    _rate_limit(request, "login", 10)
    session = SessionLocal()
    try:
        tr = session.query(Trader).filter(Trader.email == payload.email.strip().lower()).first()
        if not tr or not auth.verify_password(payload.password, tr.password_hash):
            raise HTTPException(401, "Wrong e-mail or password")
        telemetry.track("login", tr.id)
        token = auth.make_token(tr.id, tr.password_hash)
        _ustaw_ciasteczko_sesji(response, token)
        return {"token": token, "trader": {"id": tr.id, "email": tr.email,
                "full_name": tr.full_name, "is_admin": tr.is_admin, "referral_code": tr.referral_code}}
    finally:
        session.close()


def _google_tokeninfo(credential: str) -> dict:
    """Weryfikacja id_tokenu Google przez oficjalny endpoint tokeninfo.

    Stdlib zamiast biblioteki JWT: wolumen logowań jest mały, a tokeninfo
    sprawdza podpis i wygaśnięcie po stronie Google (błędny/wygasły token
    to odpowiedź 4xx -> ValueError). Funkcja modułowa, żeby testy mogły ją
    podmienić bez sieci.
    """
    import urllib.parse
    import urllib.request
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode(
        {"id_token": credential})
    try:
        with urllib.request.urlopen(url, timeout=6) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # HTTPError/URLError/timeout/JSON — jedna ścieżka: odmowa
        raise ValueError(f"tokeninfo failed: {e}")


class GoogleAuthIn(BaseModel):
    credential: str                 # id_token z przycisku GIS
    referral: str | None = None     # kod polecający z ?ref= (jak w signup)


@app.post("/api/auth/google")
def google_login(payload: GoogleAuthIn, request: Request, response: Response):
    """„Sign in with Google": jedno wejście dla logowania i rejestracji.

    Konto dobierane po `sub`, potem po e-mailu (podpięcie istniejącego);
    brak konta = rejestracja z nieużywalnym hasłem (reset e-mailem działa)
    i adresem uznanym za zweryfikowany — potwierdził go Google.
    """
    if not settings.google_login_enabled:
        # Wzorzec jak /admin: niewłączona funkcja nie istnieje.
        raise HTTPException(404, "Not Found")
    _rate_limit(request, "google", 10)
    try:
        claims = _google_tokeninfo(payload.credential)
    except ValueError:
        raise HTTPException(401, "Google sign-in failed. Please try again")
    if (claims.get("aud") != settings.google_client_id
            or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
            or claims.get("email_verified") not in (True, "true")
            or not claims.get("email")):
        raise HTTPException(401, "Google sign-in failed. Please try again")
    email = str(claims["email"]).strip().lower()
    sub = str(claims.get("sub") or "")[:64]
    session = SessionLocal()
    try:
        tr = session.query(Trader).filter(Trader.google_sub == sub).first() if sub else None
        if tr is None:
            tr = session.query(Trader).filter(Trader.email == email).first()
            if tr is not None and sub and not tr.google_sub:
                tr.google_sub = sub                       # podpięcie istniejącego konta
        nowy = tr is None
        # Konto założone ZA klienta czeka na odbiór. Google ręczy za adres tak
        # samo jak klik w link zaproszenia, więc pierwsze wejście przez Google
        # to claim — bez tego klient normalnie korzystał z portalu, a panel do
        # końca świata pokazywał „Awaiting Claim".
        odebral_konto = not nowy and bool(tr.must_set_password)
        if odebral_konto:
            tr.must_set_password = False
        if nowy:
            referred_by = None
            if payload.referral:
                ref = payload.referral.strip().upper()
                if session.query(Trader).filter(Trader.referral_code == ref).first():
                    referred_by = ref
            for _ in range(5):
                code = _gen_ref_code()
                if not session.query(Trader).filter(Trader.referral_code == code).first():
                    break
            tr = Trader(
                email=email,
                # Konto Google nie ma hasła — losowy, nieużywalny hash blokuje
                # logowanie hasłem, a "forgot password" pozwala je nadać.
                password_hash=auth.hash_password(secrets.token_urlsafe(24)),
                full_name=str(claims.get("name") or "").strip()[:120],
                referral_code=code, referred_by=referred_by,
                google_sub=sub or None,
                # False tylko na moment: _potwierdz_email niżej przestawia flagę
                # i wysyła welcome — tą samą ścieżką co klik w link z maila.
                email_verified=False,
                # Klauzula pod przyciskiem: kontynuacja przez Google = zgoda.
                terms_accepted_at=datetime.now(timezone.utc),
            )
            session.add(tr)
        # Google ręczy za adres — potwierdzenie + mail powitalny idą wspólną
        # ścieżką weryfikacji (idempotentne: zweryfikowani nic nie dostają,
        # więc zwykłe logowanie Google nie spamuje welcome'em).
        _potwierdz_email(session, tr)
        session.commit()   # domyka linkowanie google_sub u już zweryfikowanych
        telemetry.track("account_claimed" if odebral_konto
                        else ("signup" if nowy else "login"), tr.id, google=True)
        token = auth.make_token(tr.id, tr.password_hash)
        _ustaw_ciasteczko_sesji(response, token)
        return {"token": token, "trader": {"id": tr.id, "email": tr.email,
                "full_name": tr.full_name, "is_admin": tr.is_admin,
                "referral_code": tr.referral_code}}
    finally:
        session.close()


def _ui_prefs_dict(trader: Trader) -> dict:
    """ui_prefs to string JSON w bazie — do klienta zawsze idzie obiekt."""
    try:
        return json.loads(trader.ui_prefs or "{}") or {}
    except ValueError:
        return {}


def _affiliate_earned(session, trader: Trader) -> float:
    """Prowizja naliczona ŁĄCZNIE z opłaconych zamówień poleconych traderów."""
    # Granty (BOGO) niosą amount_usd opłaconego tieru dla faktury, ale to
    # NIE jest druga płatność — bez tego filtra prowizja liczyłaby się 2×.
    paid_orders = (session.query(Order)
                   .join(Trader, Trader.id == Order.trader_id)
                   .filter(Trader.referred_by == trader.referral_code, Order.status == "paid",
                           Order.provider != "grant").all())
    return round(sum(o.amount_usd for o in paid_orders) * catalog.AFFILIATE_COMMISSION_PCT / 100.0, 2)


def _affiliate_claimed(session, trader_id: int) -> float:
    """Prowizja już zamieniona na kredyty sklepowe. Źródłem prawdy jest ledger
    (nota `affiliate:claim`) — audyt i suma w jednym, bez osobnej kolumny."""
    suma = (session.query(func.coalesce(func.sum(CreditLedger.amount), 0.0))
            .filter(CreditLedger.trader_id == trader_id,
                    CreditLedger.note == "affiliate:claim").scalar())
    return round(float(suma or 0), 2)


# Konta z kampanii "free challenge" (założone ZA klienta): tym osobom portal
# przypomina co kilka dni popupem o opinii na Trustpilot. Lista w kodzie, nie
# w publicznym JS — inaczej wyciekłyby adresy klientów. Zmiana listy = deploy.
_TRUSTPILOT_NUDGE = frozenset({
    "richleewal@gmail.com",
    "bighybenfx@gmail.com",
    "ifedilimatthew19@gmail.com",
    "okechukwuchidibere1@gmail.com",
    "austin.ashford@imported.local",
    "salisalih340@gmail.com",
    "jrw31013@gmail.com",
    "hpdtravel@gmail.com",
    "saleeskb@gmail.com",
    "shamsushehupocket@gmail.com",
    "joyful4sure@gmail.com",
    "scheeperslucrecia522@gmail.com",
    "conerstonepearl@gmail.com",
})


@app.get("/api/auth/me")
def me(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        referred = session.query(Trader).filter(Trader.referred_by == trader.referral_code).count()
        commission = _affiliate_earned(session, trader)
        claimed = _affiliate_claimed(session, trader.id)
        return {"id": trader.id, "email": trader.email, "full_name": trader.full_name,
                "is_admin": trader.is_admin, "kyc_status": trader.kyc_status,
                "review_nudge": (trader.email or "").lower() in _TRUSTPILOT_NUDGE,
                # Czy weryfikacja jest w ogóle otwarta (patrz `kyc_dostepne`) —
                # portal ma pokazać powód zamiast formularza, który i tak dostanie 403.
                "kyc_available": kyc_dostepne(session, trader.id),
                # Portal wstrzymany do czasu akceptacji dokumentów: reszta API
                # odpowie 403 (`auth.portal_wstrzymany`), więc portal ma pokazać
                # ścianę z formularzem KYC zamiast pustego panelu z błędami.
                "kyc_locked": bool(trader.kyc_locked),
                "email_verified": trader.email_verified is not False,
                # potrzebne, żeby formularz KYC podświetlił zapisany kraj na liście
                "kyc_country": trader.kyc_country,
                "kyc_reject_reason": trader.kyc_reject_reason,
                "ui_prefs": _ui_prefs_dict(trader),
                # Panel: czy konto jest sparowane z Telegramem (podpis akcji na
                # kanale LEADS). Dla zwyklych traderow zawsze False i nieuzywane.
                "telegram_linked": bool(trader.telegram_user_id),
                "telegram_username": trader.telegram_username,
                "first_name": trader.first_name, "last_name": trader.last_name, "phone": trader.phone,
                "phone_country": trader.phone_country,
                "referral_code": trader.referral_code,
                "credits_usd": round(float(trader.credits_usd or 0), 2),
                # engagement: streak i punkty bonusowe dla portalu mobilnego
                "bonus_points": trader.bonus_points or 0,
                "checkin_streak": trader.checkin_streak or 0,
                "checkin_last": trader.checkin_last,
                "reveal_last": trader.reveal_last,
                "streak_freezes": trader.streak_freezes or 0,
                "notify": {"updates": bool(trader.notify_updates), "trading": bool(trader.notify_trading),
                           "payouts": bool(trader.notify_payouts), "marketing": bool(trader.notify_marketing)},
                "affiliate": {"referred": referred, "commission_pct": catalog.AFFILIATE_COMMISSION_PCT,
                              "commission_earned": commission,
                              "commission_claimed": claimed,
                              "commission_unclaimed": round(max(0.0, commission - claimed), 2)}}
    finally:
        session.close()


AFFILIATE_CLAIM_MIN_USD = 10.0


@app.post("/api/me/affiliate/claim")
def affiliate_claim(trader: Trader = Depends(auth.current_trader)):
    """Zamiana naliczonej prowizji afiliacyjnej na kredyty sklepowe.

    unclaimed = naliczone (opłacone zamówienia poleconych) − już wypłacone
    (suma wpisów `affiliate:claim` w ledgerze). Blokada wiersza tradera +
    clamp do zera zamykają wyścig dwóch równoległych claimów: drugi wylicza
    unclaimed już PO wpisie pierwszego i dostaje 0."""
    session = SessionLocal()
    try:
        tr = (session.query(Trader).filter(Trader.id == trader.id)
              .with_for_update().first())
        earned = _affiliate_earned(session, tr)
        claimed = _affiliate_claimed(session, tr.id)
        kwota = round(max(0.0, earned - claimed), 2)
        if kwota < AFFILIATE_CLAIM_MIN_USD:
            raise HTTPException(
                400, f"You need at least ${AFFILIATE_CLAIM_MIN_USD:,.0f} in unclaimed "
                     f"commission (you have ${kwota:,.2f})")
        tr.credits_usd = round(float(tr.credits_usd or 0) + kwota, 2)
        session.add(CreditLedger(trader_id=tr.id, amount=kwota, note="affiliate:claim"))
        session.commit()
        telemetry.track("affiliate_claim", tr.id, amount=kwota)
        return {"claimed_usd": kwota, "credits_usd": tr.credits_usd,
                "commission_claimed": round(claimed + kwota, 2),
                "commission_unclaimed": 0.0}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  SKLEP / checkout (Stripe + mock)                                           #
# --------------------------------------------------------------------------- #
class CheckoutIn(BaseModel):
    product_key: str
    coupon: str | None = None
    promo_code: str | None = None      # kod „Upgrade Your Size" (nie kupon rabatowy)
    weekend_trading: bool = False
    split_boost: bool = False          # +10 pp splitu (tylko Instant; pilnuje billing)
    express_payout: bool = False       # wnioski o wypłatę na początek kolejki
    use_credits: bool = True           # False = zostaw kredyty sklepowe na później
    # Dane potrzebne do założenia konta demo MT5 na nazwisko klienta.
    # Zbierane w kroku płatności; zapisywane na profilu tradera.
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    phone_country: str | None = None   # ISO2 z listy krajów; dlugosc numeru zalezy od kraju


@app.get("/api/coupon/{code}")
def coupon_preview(code: str):
    """Podgląd rabatu dla konfiguratora na landingu — sam procent, bez listy kodów.

    Kody kupione za punkty też tu odpowiadają, inaczej trader dostawałby
    „nieprawidłowy kod" na własny, świeżo wymieniony kod. Oddajemy WYŁĄCZNIE
    procent: bez właściciela, bez statusu, bez terminu. Prawo do użycia i tak
    sprawdza checkout, który zna zalogowanego tradera.
    """
    kod = code.strip().upper()
    pct = catalog.COUPONS.get(kod)
    if not pct and kod.startswith(loyalty.CODE_PREFIX):
        session = SessionLocal()
        try:
            k = session.query(RewardCode).filter(RewardCode.code == kod).first()
            pct = k.pct if k else None
        finally:
            session.close()
    if not pct:
        raise HTTPException(404, "Unknown coupon code")
    return {"code": kod, "pct": pct}


@app.get("/api/promo")
def promo_check(code: str = ""):
    """Walidacja kodu „Upgrade Your Size" dla belki na landingu i portalu.

    Celowo bez 404: belka rozróżnia zły kod od wygasłej promocji jednym polem
    `valid`, a treść komunikatu zostaje po stronie frontu.
    """
    # `code` jest jawne, póki promocja trwa — landing i tak drukuje je każdemu
    # w pasku promo; baner upsellu w portalu aplikuje je jednym klikiem.
    return {"valid": bool(catalog.promo_active() and catalog.promo_code_ok(code)),
            "name": catalog.PROMO_NAME, "upgrade": "next size up",
            "code": settings.promo_upgrade_code if catalog.promo_active() else None}


def _products_payload(session) -> list[dict]:
    """Katalog planów w kształcie /api/products — używany też do wstrzyknięcia
    danych w HTML landingu, żeby konfigurator nie czekał na fetch."""
    prods = session.query(Product).filter(Product.active == True).order_by(Product.steps, Product.account_size).all()  # noqa: E712
    # Cel promocji dołączony do każdego planu: landing i portal pokazują
    # DOKŁADNIE to, co zrobi checkout (jedno źródło prawdy).
    upgrades = catalog.upgrade_map(prods)
    out = []
    for p in prods:
        d = _product_dict(p)
        cel = upgrades.get(p.key)
        d["promo_upgrade_size"] = cel.account_size if cel else None
        d["promo_upgrade_label"] = cel.label if cel else None
        out.append(d)
    return out


@app.get("/api/products")
def list_products():
    session = SessionLocal()
    try:
        return _products_payload(session)
    finally:
        session.close()


def _dane_klienta(payload: CheckoutIn, trader: Trader) -> tuple[str, str, str, str]:
    """Sprawdza dane do rejestracji konta MT5 i zwraca je w formie do zapisu.

    Pusty formularz oznacza „zostaw to, co juz jest na profilu" — ale tylko gdy
    profil ma komplet. Bez tego warunku walidacje omijaloby sie, wysylajac
    zadanie bez tych pol.
    """
    puste = not any([(payload.first_name or "").strip(), (payload.last_name or "").strip(),
                     (payload.phone or "").strip()])
    komplet = all([(trader.first_name or "").strip(), (trader.last_name or "").strip(),
                   (trader.phone or "").strip()])
    if puste and komplet:
        return trader.first_name, trader.last_name, trader.phone, trader.phone_country or ""
    try:
        imie = fields.person_name(payload.first_name, "First name")
        nazwisko = fields.person_name(payload.last_name, "Last name")
        tel, kraj = fields.phone(payload.phone_country, payload.phone)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return imie, nazwisko, tel, kraj


@app.post("/api/checkout")
def checkout(payload: CheckoutIn, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        # Te trzy pola ida prosto do formularza rejestracji konta demo u brokera,
        # wiec smiec = nieudany provisioning po pobraniu platnosci. Walidujemy
        # PRZED zapisem i przed utworzeniem zamowienia.
        imie, nazwisko, tel, kraj = _dane_klienta(payload, trader)
        billing.save_customer_details(
            session, trader,
            first_name=imie, last_name=nazwisko, phone=tel, phone_country=kraj,
        )
        return billing.create_checkout(session, trader, payload.product_key, payload.coupon,
                                       promo_code=payload.promo_code,
                                       weekend_trading=payload.weekend_trading,
                                       split_boost=payload.split_boost,
                                       express_payout=payload.express_payout,
                                       use_credits=payload.use_credits)
    finally:
        session.close()


@app.get("/api/checkout/preview")
def checkout_preview(product_key: str, coupon: str | None = None, promo_code: str | None = None,
                     weekend: bool = False, split_boost: bool = False, express: bool = False,
                     use_credits: bool = True,
                     trader: Trader = Depends(auth.current_trader)):
    """Podgląd rozbicia ceny dla modala zakupu — dokładnie ta sama matematyka
    co realny checkout (billing.compute_price), tylko bez tworzenia zamówienia.
    Serwer i tak liczy wszystko ponownie przy POST /api/checkout."""
    session = SessionLocal()
    try:
        q = billing.compute_price(session, trader, product_key, coupon,
                                  promo_code=promo_code, weekend_trading=weekend,
                                  split_boost=split_boost, express_payout=express,
                                  use_credits=use_credits)
        return {"plan_price_usd": q["plan_price_usd"], "discount_pct": q["discount_pct"],
                "discount_usd": q["discount_usd"], "weekend_fee_usd": q["weekend_fee_usd"],
                "split_boost_fee_usd": q["split_boost_fee_usd"],
                "express_payout_fee_usd": q["express_payout_fee_usd"],
                "credits_used": q["credits_used"], "total_due_usd": q["total_due_usd"],
                "credits_balance": round(float(trader.credits_usd or 0), 2)}
    finally:
        session.close()


@app.post("/api/checkout/{order_id}/mock-complete")
def mock_complete(order_id: int, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        return billing.mock_complete(session, order_id, trader.id)
    finally:
        session.close()


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    session = SessionLocal()
    try:
        wynik = billing.handle_webhook(session, payload, sig)
    finally:
        session.close()
    # Jak przy mark-paid: opłacone konto ma dostać rachunek z puli od ręki,
    # a nie przy najbliższym dziennym cronie.
    try:
        await poller.provision_kickoff()
    except Exception as e:  # najbliższy tick i tak dokończy
        print(f"[webhook] natychmiastowy provisioning nie wyszedł: {e}")
    return wynik


@app.get("/api/orders")
def my_orders(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        orders = session.query(Order).filter(Order.trader_id == trader.id).order_by(Order.id.desc()).all()
        produkty = {p.key: p for p in session.query(Product).all()}
        konta = _konta_po_id(session, (o.account_id for o in orders))
        out = []
        for o in orders:
            prod = produkty.get(o.product_key)
            oplacony = produkty.get(getattr(o, "bogo_paid_key", None) or "")
            row = {"id": o.id, "product_key": o.product_key, "amount_usd": o.amount_usd,
                   "status": o.status, "provider": o.provider, "account_id": o.account_id,
                   "coupon": o.coupon, "created_at": o.created_at.isoformat(),
                   # Na fakturze pokazujemy pozycje z CENNIKA, a nie samo `amount_usd`,
                   # ktore przy grancie wynosi 0 i wygladalo jak darmowy produkt.
                   "product_label": prod.label if prod else o.product_key,
                   "list_price": prod.price_usd if prod else None,
                   "account_size": prod.account_size if prod else None,
                   "weekend_trading": bool(getattr(o, "weekend_trading", False)),
                   "credits_used": round(float(getattr(o, "credits_used", 0) or 0), 2),
                   "bogo_paid_key": getattr(o, "bogo_paid_key", None),
                   "bogo_paid_label": oplacony.label if oplacony else None,
                   "bogo_paid_price": oplacony.price_usd if oplacony else None,
                   "bogo_paid_size": oplacony.account_size if oplacony else None}
            # Poświadczenia MT5 kupionego konta — trader widzi je też przy zamówieniu.
            acc = konta.get(o.account_id) if o.account_id else None
            if acc and acc.trader_id == trader.id:
                row["account"] = {
                    "status": acc.status,
                    "platform_server": acc.platform_server,
                    "platform_login": acc.platform_login,
                    "platform_password": acc.platform_password,
                }
            out.append(row)
        return out
    finally:
        session.close()


@app.get("/api/me/credits")
def my_credits(trader: Trader = Depends(auth.current_trader)):
    """Saldo kredytów sklepowych + historia (nadania admina i zużycie w zakupach).

    UWAGA: `note` z modala „Add credits" jest tu widoczne dla tradera — admin
    ma pisać notatki „na zewnątrz" (np. "Contest prize").
    """
    session = SessionLocal()
    try:
        rows = (session.query(CreditLedger)
                .filter(CreditLedger.trader_id == trader.id)
                .order_by(CreditLedger.id.desc()).limit(100).all())
        return {"balance_usd": round(float(trader.credits_usd or 0), 2),
                "ledger": [{"ts": r.created_at.isoformat(), "amount": round(float(r.amount), 2),
                            "note": r.note, "order_id": r.order_id} for r in rows]}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  PORTAL TRADERA — moje konta, KYC, wypłaty                                  #
# --------------------------------------------------------------------------- #
class KycIn(BaseModel):
    full_name: str
    country: str
    doc_ref: str = ""
    dob: str | None = None
    address: str | None = None
    id_type: str | None = None      # Passport | National ID | Driver's License
    id_number: str | None = None


class PayoutReqIn(BaseModel):
    method: str = "bank"
    amount: float | None = None      # None = cała dostępna działka tradera
    details: dict | None = None      # dane wypłaty zależne od metody


# Pola WYMAGANE per metoda — bez nich admin nie ma jak wykonać przelewu.
_PAYOUT_FIELDS = {
    "usdt": ("network", "address"),
    "bank": ("holder", "iban", "swift"),
    "wise": ("email",),
}
_USDT_NETWORKS = {"TRC20", "BEP20", "POLYGON"}


def _payout_details_json(method: str, details: dict) -> str:
    """Waliduje dane wypłaty i zwraca je jako JSON do zapisania przy wniosku."""
    if method not in _PAYOUT_FIELDS:
        raise HTTPException(400, "Unknown payout method. Use usdt, bank or wise")
    clean: dict[str, str] = {}
    for f in _PAYOUT_FIELDS[method]:
        v = str((details or {}).get(f) or "").strip()
        if not v:
            labels = {"network": "USDT network", "address": "wallet address",
                      "holder": "account holder", "iban": "IBAN / account number",
                      "swift": "SWIFT / BIC", "email": "Wise email"}
            raise HTTPException(400, f"Missing payout detail: {labels.get(f, f)}")
        clean[f] = v[:120]
    if method == "usdt":
        clean["network"] = clean["network"].upper().replace("-", "")
        if clean["network"] not in _USDT_NETWORKS:
            raise HTTPException(400, "Unsupported USDT network. Use TRC-20, BEP-20 or Polygon")
        if len(clean["address"]) < 15:
            raise HTTPException(400, "The wallet address looks too short")
    if method == "bank":
        v = str((details or {}).get("bank_name") or "").strip()
        if v:
            clean["bank_name"] = v[:120]
    if method == "wise" and "@" not in clean["email"]:
        raise HTTPException(400, "The Wise email does not look like an email address")
    return json.dumps(clean)


@app.get("/api/me/accounts")
def my_accounts(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        accs = session.query(Account).filter(Account.trader_id == trader.id).order_by(Account.id).all()
        paid: dict[int, float] = {}
        for p in (session.query(Payout).filter(Payout.account_id.in_([a.id for a in accs])).all()
                  if accs else []):
            paid[p.account_id] = paid.get(p.account_id, 0.0) + p.trader_share
        out = []
        for a in accs:
            _ensure_cert_token(session, a)
            d = _account_dict(a, with_credentials=True)
            d["paid_out"] = round(paid.get(a.id, 0.0), 2)
            out.append(d)
        return out
    finally:
        session.close()


@app.get("/api/me/accounts/{account_id}")
def my_account_detail(account_id: int, trader: Trader = Depends(auth.current_trader)):
    """Szczegóły WŁASNEGO konta (krzywa equity, breachy, wypłaty).

    Portal nie może używać admin-only GET /api/accounts/{id} — trader dostawał 403.
    """
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc or acc.trader_id != trader.id:
            raise HTTPException(404, "Account not found")
        _ensure_cert_token(session, acc)
        return _account_detail(session, acc)
    finally:
        session.close()


@app.get("/api/me/accounts/{account_id}/positions")
def my_account_positions(account_id: int, trader: Trader = Depends(auth.current_trader)):
    """Otwarte pozycje konta — tabela "Open trades" w portalu.

    Wiersze istnieją tylko tam, gdzie ktoś je zapisuje (dziś: trade bot).
    Realny feed MT5 zwraca wyłącznie zagregowany open_pnl, więc dla kont
    bez wierszy portal pokazuje pojedynczą kartę z floating P&L — ŻADNEGO
    fabrykowania ticketów po stronie API.
    """
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc or acc.trader_id != trader.id:
            raise HTTPException(404, "Account not found")
        rows = (session.query(Trade)
                .filter(Trade.account_id == acc.id, Trade.status == "open")
                .order_by(Trade.opened_at.desc()).limit(50).all())
        return [{
            "ticket": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "lots": t.lots,
            "open_price": t.open_price,
            "pnl": t.pnl,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        } for t in rows]
    finally:
        session.close()


# Komunikat w JEDNYM miejscu — wychodzi z formularza i z wysyłki plików, a dwa
# różne brzmienia tej samej odmowy czytałyby się jak dwa różne powody.
KYC_WYMAGA_FUNDED = ("Verification opens once one of your accounts is funded. "
                     "Pass an evaluation first — we only collect identity documents "
                     "from traders who have a payout to claim. If you need to verify "
                     "sooner, write to support and we will open it for you.")


def kyc_dostepne(session, trader_id: int) -> bool:
    """Czy weryfikacja jest dla tego tradera otwarta.

    Dwie drogi. Pierwsza to przejście ewaluacji — sprawdzamy FAZĘ, nie status.
    Konto, które zdobyło funded, a potem złamało regułę, ma `phase="funded"`
    i `status="breached"`, a taki trader wciąż może mieć nierozliczoną wypłatę;
    odcięcie go od KYC zablokowałoby mu pieniądze, które już zarobił.

    Druga to prośba wysłana z panelu (`admin_request_kyc`). Domyślna bramka
    istnieje po to, żeby nie zbierać skanów tożsamości od kogoś, kto nie ma po co
    ich podawać — ale kiedy admin sam o dokumenty poprosił, ten argument znika,
    a jego decyzja musi być silniejsza niż reguła, która ma go tylko wyręczać.
    Dlatego chętny klient bez funded dostaje weryfikację po jednym kliknięciu
    w panelu, zamiast czekać, aż przejdzie ewaluację.
    """
    if session.get(Trader, trader_id).kyc_requested_at:
        return True
    return (session.query(Account.id)
            .filter(Account.trader_id == trader_id,
                    Account.phase == "funded").first()) is not None


@app.post("/api/me/kyc")
def submit_kyc(payload: KycIn, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if not kyc_dostepne(session, trader.id):
            raise HTTPException(403, KYC_WYMAGA_FUNDED)
        try:
            nazwa = fields.person_name(payload.full_name, "Full name")
            kraj = fields.country_name(payload.country)
        except ValueError as e:
            raise HTTPException(400, str(e))
        # Poprawka zgłoszenia, którego admin jeszcze nie tknął, jest w porządku:
        # rozmazane zdjęcie dowodu trzeba dać wymienić, zanim ktoś je odrzuci.
        # Ale to JEST to samo zgłoszenie, więc nie robimy z niego nowego:
        # bez tego admin dostaje drugi dzwonek o tym samym człowieku, a wpis
        # „odmładza się" w kolejce i wypada z kolejności czekania.
        poprawka = tr.kyc_status == "pending"
        tr.kyc_status = "pending"
        tr.kyc_fullname = nazwa
        tr.kyc_country = kraj
        tr.kyc_dob = payload.dob
        tr.kyc_address = payload.address
        tr.kyc_id_type = payload.id_type
        tr.kyc_id_number = payload.id_number
        tr.kyc_doc_ref = payload.doc_ref or payload.id_number or ""
        if not (poprawka and tr.kyc_submitted_at):
            tr.kyc_submitted_at = datetime.now(timezone.utc)
        session.commit()
        telemetry.track("kyc_resubmitted" if poprawka else "kyc_submitted", trader.id)
        if not poprawka:
            notify.notify_admins("admin_kyc", "New KYC submission",
                                 tr.kyc_fullname or tr.email)
        return {"kyc_status": tr.kyc_status, "updated": poprawka}
    finally:
        session.close()


@app.post("/api/accounts/{account_id}/payout-request")
def request_payout(account_id: int, payload: PayoutReqIn, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc or acc.trader_id != trader.id:
            raise HTTPException(404, "Account not found")
        if acc.status != "funded":
            raise HTTPException(400, "Payouts are available on funded accounts only")
        tr = session.get(Trader, trader.id)
        if tr.kyc_status != "approved":
            raise HTTPException(403, "Complete KYC verification first")
        # Wypłaty on-demand: JEDEN otwarty wniosek na konto. Drugi „pending" to
        # niemal zawsze dubel z niecierpliwości — przegląd trwa do 24 h i dubel
        # tylko rozjeżdża księgowanie (dwa wnioski na ten sam zysk).
        otwarty = (session.query(PayoutRequest)
                   .filter(PayoutRequest.account_id == acc.id,
                           PayoutRequest.status == "pending").first())
        if otwarty:
            raise HTTPException(400, "A payout request for this account is already under review")
        profit = round(acc.balance - acc.initial_balance, 2)
        if profit <= 0:
            raise HTTPException(400, "No profit available to pay out")
        available = round(profit * acc.profit_split_pct / 100.0, 2)
        # Trader sam wybiera kwotę (część lub całość dostępnej działki).
        share = round(float(payload.amount), 2) if payload.amount is not None else available
        if share <= 0:
            raise HTTPException(400, "The payout amount must be greater than zero")
        if share > available:
            raise HTTPException(400, f"The amount exceeds your available share (${available:,.2f})")
        method = (payload.method or "").lower()
        details_json = _payout_details_json(method, payload.details or {})
        pr = PayoutRequest(account_id=acc.id, trader_id=tr.id, profit_amount=profit,
                           trader_share=share, method=method, details=details_json,
                           status="pending")
        session.add(pr)
        session.commit()
        notify.send("payout_requested", tr.email, {"name": tr.full_name or tr.email,
                    "login": acc.login, "profit_amount": profit, "trader_share": share})
        notify.notify_admins("admin_payout", f"Payout request ${share:,.2f}", tr.email)
        return {"id": pr.id, "profit_amount": profit, "trader_share": share, "status": "pending"}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  PANEL KLIENTA — activity, journal, tickety, payouts, achievements, settings #
# --------------------------------------------------------------------------- #
def _own_account(session, trader: Trader, account_id: int) -> Account:
    acc = session.get(Account, account_id)
    if not acc or acc.trader_id != trader.id:
        raise HTTPException(404, "Account not found")
    return acc


@app.post("/api/accounts/{account_id}/scale-up")
def scale_up_account(account_id: int, trader: Trader = Depends(auth.current_trader)):
    """Trader wybiera WYŻSZY PLAN zamiast wypłaty.

    Wcześniej skalowanie odpalał poller sam z siebie i trader nie miał tu nic do
    powiedzenia. Teraz to jego decyzja, bo obie ścieżki wykluczają się nawzajem:
    ten sam zysk albo idzie na wypłatę, albo zamienia się w większy rachunek.

    Konto wychodzi stąd w stanie `provisioning` — dostaje NOWY rachunek MT5 o
    rozmiarze wyższego planu (patrz `poller.apply_scale_up`), a poświadczenia
    dowozi `provisioning.provision_pending` razem z mailem.
    """
    session = SessionLocal()
    try:
        acc = _own_account(session, trader, account_id)
        if acc.status != "funded":
            raise HTTPException(400, "Scaling is available on funded accounts only")
        if poller.scale_offer(acc) is None:
            # Dwa różne powody odmowy, dwa różne komunikaty: „nie masz jeszcze
            # progu" i „jesteś na największym planie" to nie to samo.
            if catalog.next_size_up(acc.steps, acc.initial_balance) is None:
                raise HTTPException(400, "This is already the largest account size we offer")
            raise HTTPException(
                400, f"Scaling unlocks once the account is up {poller.SCALE_TRIGGER_PCT:.0f}%")
        # Otwarta pozycja przeżyłaby reset salda jako czysty prezent albo strata:
        # jej PnL rozliczyłby się już wobec NOWEGO rozmiaru.
        if abs(acc.open_pnl or 0.0) > 0.005:
            raise HTTPException(400, "Close your open positions before scaling the account up")
        poprzedni = acc.initial_balance
        nowy = poller.apply_scale_up(session, acc)
        session.commit()
        try:
            notify.send("account_scaled", trader.email,
                        {"name": trader.full_name or trader.email, "login": acc.login,
                         "previous_size": poprzedni, "new_size": nowy})
            push.send_to_trader(trader.id, "Moving up to a bigger account",
                                f"Your new ${nowy:,.0f} account is being set up.",
                                url="/portal?view=accounts", tag="account_scaled")
        except Exception as e:  # pragma: no cover
            print(f"[scale-up] powiadomienie nie poszlo: {e}")
        return {"account_id": acc.id, "previous_size": poprzedni, "new_size": nowy,
                "balance": acc.balance, "status": acc.status, "product_key": acc.product_key}
    finally:
        session.close()


LEDGER_MAX = 300


@app.get("/api/me/accounts/{account_id}/activity")
def account_activity(account_id: int, trader: Trader = Depends(auth.current_trader)):
    """Kalendarz dzienny + księga operacji na koncie (transakcje i wypłaty).

    Saldo przy każdym wierszu bierzemy ze SNAPSHOTU z chwili zdarzenia, a nie
    z odliczania wstecz od bieżącego salda. Odliczanie wstecz kłamało po każdej
    wypłacie i po awansie fazy — jedno i drugie resetuje saldo konta, więc cała
    historia przesuwała się o wypłaconą kwotę.

    Dzienny wynik liczymy z TRANSAKCJI, nie z różnicy sald. Inaczej dzień, w
    którym trader zarobił i od razu dostał wypłatę, pokazywał zero.
    """
    session = SessionLocal()
    try:
        acc = _own_account(session, trader, account_id)
        snaps = (session.query(EquitySnapshot).filter(EquitySnapshot.account_id == account_id)
                 .order_by(EquitySnapshot.ts).limit(20000).all())
        trades = (session.query(Trade)
                  .filter(Trade.account_id == account_id, Trade.status == "closed")
                  .order_by(Trade.closed_at).all())
        payouts = (session.query(Payout).filter(Payout.account_id == account_id)
                   .order_by(Payout.ts).all())

        def _naive(dt):
            return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt

        czasy = [_naive(s.ts) for s in snaps]

        def saldo_po(ts):
            """Saldo zapisane w pierwszym snapshocie NIE WCZEŚNIEJSZYM niż zdarzenie."""
            if not snaps or ts is None:
                return None
            import bisect
            i = bisect.bisect_left(czasy, _naive(ts))
            return snaps[min(i, len(snaps) - 1)].balance

        # --- księga: transakcje + wypłaty, malejąco po czasie ---
        ledger = []
        for t in trades:
            ledger.append({"kind": "trade", "ts": _naive(t.closed_at or t.opened_at),
                           "day": (t.closed_at or t.opened_at).strftime("%Y-%m-%d"),
                           "symbol": t.symbol, "side": t.side, "lots": round(t.lots, 2),
                           "pnl": round(t.pnl, 2), "balance": saldo_po(t.closed_at)})
        for p in payouts:
            # Z konta znika CAŁY zysk; trader dostaje z niego swój udział. W kolumnie
            # P&L musi stać kwota zdjęta z konta, inaczej saldo obok się nie zgadza.
            ledger.append({"kind": "payout", "ts": _naive(p.ts),
                           "day": p.ts.strftime("%Y-%m-%d") if p.ts else None,
                           "symbol": "Payout", "side": None, "lots": None,
                           "pnl": -round(p.profit_amount, 2),
                           "trader_share": round(p.trader_share, 2),
                           "balance": saldo_po(p.ts)})
        ledger.sort(key=lambda r: (r["ts"] is None, r["ts"]), reverse=True)

        # --- kalendarz ---
        days: dict[str, float] = {}
        if trades:
            for t in trades:
                d = (t.closed_at or t.opened_at).strftime("%Y-%m-%d")
                days[d] = round(days.get(d, 0.0) + t.pnl, 2)
        else:
            # Konto bez zapisanych transakcji: zostaje różnica sald ze snapshotów,
            # ale bez wypłat — te nie są wynikiem handlu.
            byday: dict[str, list] = {}
            for s in snaps:
                d = byday.setdefault(s.day_key, [s.balance, s.balance])
                d[1] = s.balance
            wyplaty_dnia: dict[str, float] = {}
            for p in payouts:
                if p.ts:
                    k = p.ts.strftime("%Y-%m-%d")
                    wyplaty_dnia[k] = wyplaty_dnia.get(k, 0.0) + p.profit_amount
            for d, (first, last) in byday.items():
                days[d] = round(last - first + wyplaty_dnia.get(d, 0.0), 2)

        return {
            "days": [{"day": d, "pnl": v} for d, v in sorted(days.items())],
            # Sufit historii oddawanej portalowi. Dopoki lista w widoku konta
            # byla nieskonczona, limit 100 byl niewidoczny — przy stronicowaniu
            # staje sie realnym koncem historii, wiec idzie w gore. Wyzej niz
            # tutaj nie ma sensu bez stronicowania po stronie serwera: caly
            # ledger jedzie w jednej odpowiedzi razem z krzywa kapitalu.
            "ledger": [{k: v for k, v in r.items() if k != "ts"} for r in ledger[:LEDGER_MAX]],
        }
    finally:
        session.close()


@app.get("/api/me/accounts/{account_id}/stats")
def account_stats(account_id: int, trader: Trader = Depends(auth.current_trader)):
    """Statystyki WSZYSTKICH zamkniętych transakcji konta — bez sufitu LEDGER_MAX.

    Księga w /activity jest przycięta, więc liczenie w przeglądarce kłamałoby
    na kontach z dłuższą historią. Tu liczy serwer, z pełnej tabeli.
    Kubełki godzinowe są w czasie serwera (UTC) — konto nie zna strefy tradera.
    """
    session = SessionLocal()
    try:
        _own_account(session, trader, account_id)
        trades = (session.query(Trade)
                  .filter(Trade.account_id == account_id, Trade.status == "closed")
                  .order_by(Trade.closed_at).all())
        n = len(trades)
        if n == 0:
            return {"trades": 0}

        def _naive(dt):
            return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt

        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = sum(losses)  # ujemna
        net = sum(pnls)
        durations = [(_naive(t.closed_at) - _naive(t.opened_at)).total_seconds()
                     for t in trades if t.closed_at and t.opened_at]
        best = max(trades, key=lambda t: t.pnl)
        worst = min(trades, key=lambda t: t.pnl)

        # Seria z końca historii: +n = n zysków z rzędu, -n = n strat; 0 przerywa.
        streak = 0
        for t in reversed(trades):
            if t.pnl > 0 and streak >= 0:
                streak += 1
            elif t.pnl < 0 and streak <= 0:
                streak -= 1
            else:
                break

        def _bucket():
            return {"trades": 0, "wins": 0, "pnl": 0.0}

        by_symbol: dict[str, dict] = {}
        by_weekday = [_bucket() for _ in range(7)]   # 0 = poniedziałek
        by_hour = [_bucket() for _ in range(24)]
        sides = {"long": _bucket(), "short": _bucket()}
        for t in trades:
            ts = _naive(t.closed_at or t.opened_at)
            grupy = [by_symbol.setdefault(t.symbol, _bucket()),
                     sides["long" if t.side == "buy" else "short"]]
            if ts:
                grupy += [by_weekday[ts.weekday()], by_hour[ts.hour]]
            for g in grupy:
                g["trades"] += 1
                g["wins"] += 1 if t.pnl > 0 else 0
                g["pnl"] = round(g["pnl"] + t.pnl, 2)
        symbols = [{"symbol": s, **v, "win_rate": round(v["wins"] * 100.0 / v["trades"], 1)}
                   for s, v in by_symbol.items()]
        symbols.sort(key=lambda r: -r["pnl"])

        return {
            "trades": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) * 100.0 / n, 1),
            "net_pnl": round(net, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / -gross_loss, 2) if gross_loss < 0 else None,
            "avg_win": round(gross_profit / len(wins), 2) if wins else None,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else None,
            "expectancy": round(net / n, 2),
            "avg_duration_sec": round(sum(durations) / len(durations)) if durations else None,
            "best_trade": {"symbol": best.symbol, "pnl": round(best.pnl, 2)},
            "worst_trade": {"symbol": worst.symbol, "pnl": round(worst.pnl, 2)},
            "long": sides["long"], "short": sides["short"],
            "streak": streak,
            "by_symbol": symbols,
            "by_weekday": by_weekday,
            "by_hour": by_hour,
        }
    finally:
        session.close()


def _achievements_payload(session, trader: Trader) -> dict:
    odznaki = achievements.badges(session, trader)
    ile = sum(1 for b in odznaki if b["unlocked"])
    return {"badges": odznaki, "unlocked": ile, "total": len(odznaki),
            "rewards": achievements.state(session, trader, ile)}


@app.get("/api/me/achievements")
def my_achievements(trader: Trader = Depends(auth.current_trader)):
    """Odznaki liczone z REALNYCH zdarzeń na platformie — zero generowania,
    plus stan trzech nagród za progi 3/8, 5/8 i 8/8."""
    session = SessionLocal()
    try:
        return _achievements_payload(session, session.get(Trader, trader.id))
    finally:
        session.close()


class ClaimIn(BaseModel):
    tier: int


@app.post("/api/me/achievements/claim")
def my_achievements_claim(payload: ClaimIn, trader: Trader = Depends(auth.current_trader)):
    """Odbiera nagrodę za próg odznak: kod rabatowy (3/8, 5/8) albo konto (8/8).

    Liczba odznak liczona jest TUTAJ, na serwerze — przeglądarka podaje wyłącznie
    próg, o który prosi. Inaczej wystarczyłoby jedno żądanie z `tier: 8`, żeby
    dostać darmowe konto bez ani jednej odznaki.
    """
    session = SessionLocal()
    try:
        # FOR UPDATE jak przy wymianie punktów: dwa równoległe kliknięcia
        # „Claim" nie mogą odebrać tej samej nagrody dwa razy.
        tr = session.query(Trader).filter(Trader.id == trader.id).with_for_update().one()
        ile = sum(1 for b in achievements.badges(session, tr) if b["unlocked"])
        try:
            nagroda, konto = achievements.claim(session, tr, payload.tier, ile)
        except ValueError:
            raise HTTPException(404, "Unknown reward tier")
        except LookupError as brakuje:
            raise HTTPException(400, f"You need {brakuje} more achievement(s) for this reward")
        except RuntimeError:
            raise HTTPException(409, "This reward has already been claimed")
        session.commit()
        telemetry.track("achievement_reward", trader.id, tier=payload.tier)
        # Bez maila: nie ma szablonu na przyznane konto, a wysylanie nieznanego
        # zdarzenia znikneloby po cichu. Portal pokazuje wynik od razu, a konto
        # pojawia sie na liscie challenge'ow.
        return {"claimed": payload.tier, "code": nagroda.code, "account": konto,
                **_achievements_payload(session, tr)}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  Centrum powiadomień (dzwonek w portalu); endpointy push — sekcja
#  „Web push (PWA)" niżej                                                     #
# --------------------------------------------------------------------------- #
@app.get("/api/me/notifications")
def my_notifications(limit: int = 20, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        limit = max(1, min(50, limit))
        rows = (session.query(Notification).filter(Notification.trader_id == trader.id)
                .order_by(Notification.id.desc()).limit(limit).all())
        unread = (session.query(Notification)
                  .filter(Notification.trader_id == trader.id,
                          Notification.read_at.is_(None)).count())
        return {"unread": unread,
                "items": [{"id": n.id, "event": n.event, "title": n.title, "body": n.body,
                           "url": n.url, "read": n.read_at is not None,
                           "created_at": n.created_at.isoformat() if n.created_at else None}
                          for n in rows]}
    finally:
        session.close()


@app.post("/api/me/notifications/read")
def notifications_read(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        (session.query(Notification)
         .filter(Notification.trader_id == trader.id, Notification.read_at.is_(None))
         .update({Notification.read_at: datetime.now(timezone.utc).replace(tzinfo=None)},
                 synchronize_session=False))
        session.commit()
        return {"ok": True}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  Certyfikaty tradera — jego wlasne dokumenty                                 #
# --------------------------------------------------------------------------- #
@app.get("/api/me/certificates")
def my_certificates(trader: Trader = Depends(auth.current_trader)):
    """Wszystkie dokumenty tradera: osiagniecia per konto + certyfikaty wyplat."""
    session = SessionLocal()
    try:
        accs = (session.query(Account).filter(Account.trader_id == trader.id)
                .order_by(Account.id.desc()).all())
        acc_ids = [a.id for a in accs]
        wydane: dict[tuple[int, str], Certificate] = {}
        if acc_ids:
            for c in session.query(Certificate).filter(Certificate.account_id.in_(acc_ids)).all():
                wydane[(c.account_id, c.kind)] = c
        produkty = {p.key: p for p in session.query(Product).all()}

        konta = []
        for a in accs:
            prod = produkty.get(a.product_key)
            konta.append({
                "account_id": a.id, "login": a.login,
                "product": prod.label if prod else a.product_key,
                "size": a.initial_balance, "status": a.status, "phase": a.phase,
                "items": [{
                    "kind": k, "label": CERT_KINDS[k][0],
                    "available": _cert_kind_available(a, k),
                    "url": (f"/certificate/{wydane[(a.id, k)].cert_token}"
                            if (a.id, k) in wydane else None),
                } for k in CERT_KINDS],
            })

        logins = {a.id: a.login for a in accs}
        wyplaty = []
        if acc_ids:
            for pay in (session.query(Payout).filter(Payout.account_id.in_(acc_ids))
                        .order_by(Payout.id.desc()).all()):
                wyplaty.append({"id": pay.id, "ts": pay.ts.isoformat() if pay.ts else None,
                                "amount": round(pay.trader_share, 2),
                                "account": logins.get(pay.account_id, "—"),
                                "url": f"/payout/{pay.cert_token}" if pay.cert_token else None})
        return {"accounts": konta, "payouts": wyplaty}
    finally:
        session.close()


class MyCertIn(BaseModel):
    account_id: int
    kind: str


@app.post("/api/me/certificates")
def my_certificate_issue(payload: MyCertIn, trader: Trader = Depends(auth.current_trader)):
    """Trader sam wystawia sobie certyfikat za etap, ktory OSIAGNAL.

    Bez tego zakladka byla martwa do czasu, az admin recznie wyda dokument. Warunek
    zostaje ten sam co po stronie admina — certyfikat jest publicznie weryfikowalny,
    wiec nie moze twierdzic czegos, czego nie ma w bazie.
    """
    if payload.kind not in CERT_KINDS:
        raise HTTPException(400, "Unknown certificate type")
    session = SessionLocal()
    try:
        acc = _own_account(session, trader, payload.account_id)
        if not _cert_kind_available(acc, payload.kind):
            raise HTTPException(400, "This stage has not been reached on this account yet")
        cert = (session.query(Certificate)
                .filter(Certificate.account_id == acc.id, Certificate.kind == payload.kind).first())
        if cert is None:
            cert = Certificate(account_id=acc.id, kind=payload.kind,
                               cert_token=secrets.token_urlsafe(16)[:32])
            session.add(cert)
            session.commit()
        return {"kind": cert.kind, "url": f"/certificate/{cert.cert_token}"}
    finally:
        session.close()


@app.post("/api/me/payouts/{payout_id}/certificate")
def my_payout_certificate(payout_id: int, trader: Trader = Depends(auth.current_trader)):
    """Dorobienie certyfikatu do WLASNEJ wyplaty (starsze wyplaty nie maja tokenu).

    Certyfikat wystawiony samoobsługowo NIE trafia na pas na landingu: o tym,
    co jest na stronie, decyduje admin (panel → Payouts). Trader dostaje pełny
    dokument z QR i weryfikacją, więc niczego mu to nie odbiera.
    """
    session = SessionLocal()
    try:
        pay = session.get(Payout, payout_id)
        if not pay:
            raise HTTPException(404, "Payout not found")
        _own_account(session, trader, pay.account_id)   # rzuca 404 gdy nie jego
        if not pay.cert_token:
            pay.cert_token = secrets.token_urlsafe(16)[:32]
            pay.show_on_lp = False
            session.commit()
        return {"url": f"/payout/{pay.cert_token}"}
    finally:
        session.close()


@app.get("/api/me/payouts")
def my_payouts(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        accs = session.query(Account).filter(Account.trader_id == trader.id).all()
        by_id = {a.id: a for a in accs}
        acc_ids = list(by_id)
        reqs = (session.query(PayoutRequest).filter(PayoutRequest.account_id.in_(acc_ids))
                .order_by(PayoutRequest.id.desc()).all()) if acc_ids else []
        pays = (session.query(Payout).filter(Payout.account_id.in_(acc_ids)).all()) if acc_ids else []
        available = sum(max(0.0, a.balance - a.initial_balance) * a.profit_split_pct / 100.0
                        for a in accs if a.status == "funded")
        return {
            "summary": {
                "total_paid": round(sum(p.trader_share for p in pays), 2),
                "pending": sum(1 for r in reqs if r.status == "pending"),
                "available": round(available, 2),
                # Harmonogram wypłat = on-demand z przeglądem do 24 h; portal
                # pisze to zdanie z tej liczby, nie z zaszytego stringa.
                "review_hours": 24,
            },
            "requests": [{"id": r.id, "ts": r.ts.isoformat(),
                          "account": by_id[r.account_id].login if r.account_id in by_id else "?",
                          "profit_amount": r.profit_amount, "trader_share": r.trader_share,
                          "method": r.method, "status": r.status,
                          "express": bool(by_id[r.account_id].express_payout) if r.account_id in by_id else False,
                          "expected_by": ((r.ts + timedelta(hours=24)).isoformat()
                                          if r.status == "pending" else None),
                          "reject_reason": r.reject_reason} for r in reqs],
            # Zrealizowane wyplaty — wniosek to dopiero prosba, a trader chce
            # widziec, co faktycznie do niego trafilo i miec do tego certyfikat.
            "history": [{"id": p.id, "ts": p.ts.isoformat(),
                         "account": by_id[p.account_id].login if p.account_id in by_id else "?",
                         "profit_amount": p.profit_amount, "trader_share": p.trader_share,
                         "method": p.method, "paid": bool(p.paid),
                         "cert_token": p.cert_token}
                        for p in sorted(pays, key=lambda x: x.ts, reverse=True)],
        }
    finally:
        session.close()


class JournalIn(BaseModel):
    title: str
    content: str = ""
    account_id: int | None = None


@app.get("/api/me/journal")
def journal_list(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        rows = (session.query(JournalEntry).filter(JournalEntry.trader_id == trader.id)
                .order_by(JournalEntry.id.desc()).limit(200).all())
        logins = {a.id: a.login for a in
                  session.query(Account).filter(Account.trader_id == trader.id).all()}
        return [{"id": e.id, "ts": e.ts.isoformat(), "title": e.title, "content": e.content,
                 "account_id": e.account_id, "account": logins.get(e.account_id)} for e in rows]
    finally:
        session.close()


@app.post("/api/me/journal")
def journal_create(payload: JournalIn, trader: Trader = Depends(auth.current_trader)):
    title = payload.title.strip()
    if not title:
        raise HTTPException(400, "An entry title is required")
    session = SessionLocal()
    try:
        if payload.account_id is not None:
            _own_account(session, trader, payload.account_id)
        e = JournalEntry(trader_id=trader.id, account_id=payload.account_id,
                         title=title[:160], content=(payload.content or "")[:20000])
        session.add(e)
        session.commit()
        return {"id": e.id}
    finally:
        session.close()


@app.delete("/api/me/journal/{entry_id}")
def journal_delete(entry_id: int, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        e = session.get(JournalEntry, entry_id)
        if not e or e.trader_id != trader.id:
            raise HTTPException(404, "Entry not found")
        session.delete(e)
        session.commit()
        return {"deleted": entry_id}
    finally:
        session.close()


# --- Support: tickety -------------------------------------------------------
class TicketIn(BaseModel):
    subject: str
    message: str


class TicketReplyIn(BaseModel):
    message: str


class AdminTicketReplyIn(BaseModel):
    message: str = ""
    close: bool = False


def _wiadomosci_biletow(session, ticket_ids) -> dict[int, list[TicketMessage]]:
    """Wiadomości wielu biletów jednym zapytaniem — do list, nie do pojedynczego wątku."""
    if not ticket_ids:
        return {}
    out: dict[int, list[TicketMessage]] = {i: [] for i in ticket_ids}
    for m in (session.query(TicketMessage).filter(TicketMessage.ticket_id.in_(ticket_ids))
              .order_by(TicketMessage.id).all()):
        out[m.ticket_id].append(m)
    return out


def _ticket_dict(session, t: SupportTicket, with_thread: bool = False, msgs=None) -> dict:
    if msgs is None:
        msgs = (session.query(TicketMessage).filter(TicketMessage.ticket_id == t.id)
                .order_by(TicketMessage.id).all())
    d = {"id": t.id, "subject": t.subject, "status": t.status,
         "created_at": t.created_at.isoformat(),
         "last_ts": msgs[-1].ts.isoformat() if msgs else t.created_at.isoformat(),
         "messages": len(msgs)}
    if with_thread:
        d["thread"] = [{"author": m.author, "body": m.body, "ts": m.ts.isoformat()} for m in msgs]
    return d


@app.post("/api/me/tickets")
def ticket_create(payload: TicketIn, trader: Trader = Depends(auth.current_trader)):
    subject, message = payload.subject.strip(), payload.message.strip()
    if not subject or not message:
        raise HTTPException(400, "Subject and message are required")
    session = SessionLocal()
    try:
        t = SupportTicket(trader_id=trader.id, subject=subject[:200])
        session.add(t)
        session.flush()
        session.add(TicketMessage(ticket_id=t.id, author="trader", body=message[:20000]))
        session.commit()
        notify.notify_admins("admin_ticket", f"New ticket: {subject[:80]}", trader.email)
        return {"id": t.id, "status": t.status}
    finally:
        session.close()


@app.get("/api/me/tickets")
def ticket_list(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        rows = (session.query(SupportTicket).filter(SupportTicket.trader_id == trader.id)
                .order_by(SupportTicket.id.desc()).all())
        wiadomosci = _wiadomosci_biletow(session, [t.id for t in rows])
        return [_ticket_dict(session, t, msgs=wiadomosci[t.id]) for t in rows]
    finally:
        session.close()


@app.get("/api/me/tickets/{ticket_id}")
def ticket_view(ticket_id: int, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        t = session.get(SupportTicket, ticket_id)
        if not t or t.trader_id != trader.id:
            raise HTTPException(404, "Ticket not found")
        return _ticket_dict(session, t, with_thread=True)
    finally:
        session.close()


@app.post("/api/me/tickets/{ticket_id}/reply")
def ticket_reply(ticket_id: int, payload: TicketReplyIn, trader: Trader = Depends(auth.current_trader)):
    body = payload.message.strip()
    if not body:
        raise HTTPException(400, "A reply message is required")
    session = SessionLocal()
    try:
        t = session.get(SupportTicket, ticket_id)
        if not t or t.trader_id != trader.id:
            raise HTTPException(404, "Ticket not found")
        if t.status == "closed":
            raise HTTPException(400, "This ticket is closed")
        session.add(TicketMessage(ticket_id=t.id, author="trader", body=body[:20000]))
        t.status = "open"
        session.commit()
        notify.notify_admins("admin_ticket", f"Ticket reply: {t.subject[:80]}", trader.email)
        return _ticket_dict(session, t, with_thread=True)
    finally:
        session.close()


@app.get("/api/admin/tickets", dependencies=[Depends(auth.require_admin)])
def admin_tickets():
    session = SessionLocal()
    try:
        rows = session.query(SupportTicket).order_by(SupportTicket.id.desc()).limit(200).all()
        # Maile i wiadomosci hurtem: osobne zapytanie na kazdy bilet to bylo 400
        # round-tripow do bazy, a baza stoi za oceanem.
        wiadomosci = _wiadomosci_biletow(session, [t.id for t in rows])
        maile = _maile_traderow(session, {t.trader_id for t in rows})
        out = []
        for t in rows:
            d = _ticket_dict(session, t, msgs=wiadomosci[t.id])
            d["trader_email"] = maile.get(t.trader_id)
            out.append(d)
        return out
    finally:
        session.close()


@app.get("/api/admin/tickets/{ticket_id}", dependencies=[Depends(auth.require_admin)])
def admin_ticket_view(ticket_id: int):
    session = SessionLocal()
    try:
        t = session.get(SupportTicket, ticket_id)
        if not t:
            raise HTTPException(404, "Ticket not found")
        tr = session.get(Trader, t.trader_id)
        d = _ticket_dict(session, t, with_thread=True)
        d["trader_email"] = tr.email if tr else None
        return d
    finally:
        session.close()


@app.post("/api/admin/tickets/{ticket_id}/reply", dependencies=[Depends(auth.require_admin)])
def admin_ticket_reply(ticket_id: int, payload: AdminTicketReplyIn):
    session = SessionLocal()
    try:
        t = session.get(SupportTicket, ticket_id)
        if not t:
            raise HTTPException(404, "Ticket not found")
        tr = session.get(Trader, t.trader_id)
        if payload.message.strip():
            session.add(TicketMessage(ticket_id=t.id, author="admin", body=payload.message.strip()[:20000]))
        t.status = "closed" if payload.close else "answered"
        session.commit()
        if payload.message.strip() and tr:
            notify.send("ticket_reply", tr.email, {"name": tr.full_name or tr.email,
                        "subject": t.subject, "ticket_id": t.id})
        return _ticket_dict(session, t, with_thread=True)
    finally:
        session.close()


@app.delete("/api/admin/tickets/{ticket_id}", dependencies=[Depends(auth.require_admin)])
def admin_ticket_delete(ticket_id: int):
    """Kasuje zgłoszenie razem z całą rozmową. Nieodwracalne.

    Zamknięcie ticketu zostawiało go w „History" na zawsze — a trafiają tam też
    wpisy testowe i spam, których nie ma po co trzymać. Zamknięty i skasowany to
    dwie różne decyzje: pierwsza kończy sprawę, druga usuwa ślad.

    Wiadomości lecą PRZED zgłoszeniem, bo `ticket_messages.ticket_id` wskazuje na
    `support_tickets.id` i Postgres tego pilnuje (SQLite lokalnie też — patrz
    `db.py`). Odwrotna kolejność to 500 wyłącznie na produkcji. Ta sama sekwencja
    co w `admin_delete_trader`.
    """
    session = SessionLocal()
    try:
        t = session.get(SupportTicket, ticket_id)
        if not t:
            raise HTTPException(404, "Ticket not found")
        ile = (session.query(TicketMessage)
               .filter(TicketMessage.ticket_id == ticket_id)
               .delete(synchronize_session=False))
        session.delete(t)
        session.commit()
        return {"deleted": ticket_id, "messages_removed": ile}
    finally:
        session.close()


# --- Settings ---------------------------------------------------------------
class MePatch(BaseModel):
    full_name: str | None = None
    notify_updates: bool | None = None
    notify_trading: bool | None = None
    notify_payouts: bool | None = None
    notify_marketing: bool | None = None
    # Preferencje UI (np. sortowanie tabel) — mały JSON, nadpisywany w całości.
    ui_prefs: dict | None = None


@app.patch("/api/me")
def me_patch(payload: MePatch, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if payload.full_name is not None:
            try:
                tr.full_name = fields.person_name(payload.full_name, "Full name")
            except ValueError as e:
                raise HTTPException(400, str(e))
        for field in ("notify_updates", "notify_trading", "notify_payouts", "notify_marketing"):
            v = getattr(payload, field)
            if v is not None:
                setattr(tr, field, bool(v))
        if payload.ui_prefs is not None:
            blob = json.dumps(payload.ui_prefs, separators=(",", ":"))
            if len(blob) > 2000:
                raise HTTPException(400, "UI preferences too large")
            tr.ui_prefs = blob
        session.commit()
        return {"ok": True, "full_name": tr.full_name,
                "notify": {"updates": bool(tr.notify_updates), "trading": bool(tr.notify_trading),
                           "payouts": bool(tr.notify_payouts), "marketing": bool(tr.notify_marketing)}}
    finally:
        session.close()


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/me/password")
def me_password(payload: PasswordIn, trader: Trader = Depends(auth.current_trader)):
    if len(payload.new_password) < 8:
        raise HTTPException(400, "The new password must be at least 8 characters")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if not auth.verify_password(payload.current_password, tr.password_hash):
            raise HTTPException(400, "Your current password is wrong")
        tr.password_hash = auth.hash_password(payload.new_password)
        session.commit()
        telemetry.track("password_changed", tr.id)
        # Zmiana hasla uniewaznia wszystkie starsze sesje (odcisk hasla w
        # tokenie) — swiezy token pozwala TEJ sesji dzialac dalej bez wylogowania.
        return {"ok": True, "token": auth.make_token(tr.id, tr.password_hash)}
    finally:
        session.close()


class DeleteIn(BaseModel):
    password: str


@app.post("/api/me/delete")
def me_delete(payload: DeleteIn, trader: Trader = Depends(auth.current_trader)):
    """Danger zone: anonimizacja konta klienta. Konta tradingowe i zamówienia
    zostają w bazie (księgowość/AML), ale profil traci dane osobowe i dostęp."""
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if not auth.verify_password(payload.password, tr.password_hash):
            raise HTTPException(400, "Wrong password")
        if tr.is_admin:
            raise HTTPException(400, "An administrator account cannot be deleted from the portal")
        tr.email = f"deleted-{tr.id}@removed.invalid"
        tr.full_name = "Deleted User"
        tr.first_name = tr.last_name = tr.phone = None
        tr.kyc_fullname = tr.kyc_country = tr.kyc_doc_ref = None
        tr.kyc_dob = tr.kyc_address = tr.kyc_id_type = tr.kyc_id_number = None
        tr.kyc_doc_front = tr.kyc_doc_back = tr.kyc_doc_residence = None
        tr.kyc_status = "none"
        tr.password_hash = auth.hash_password(secrets.token_hex(24))
        tr.notify_updates = tr.notify_trading = tr.notify_payouts = tr.notify_marketing = False
        session.commit()
        return {"deleted": True}
    finally:
        session.close()


# --- Engagement: dzienny check-in + mystery reveal ---------------------------
# Bonusy za kamienie milowe serii (nieregularne odstepy sa celowe).
_CHECKIN_BONUS = {3: 25, 7: 50, 12: 75, 18: 100, 25: 150, 40: 250, 60: 400}


@app.post("/api/me/checkin")
def me_checkin(trader: Trader = Depends(auth.current_trader)):
    """Seria liczona po datach UTC; nagrody nadaje serwer, nie klient."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    day_before = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    session = SessionLocal()
    try:
        # FOR UPDATE: dwa rownolegle requesty nie moga podbic serii/bonusu podwojnie
        # (SQLite ignoruje lock, ale i tak serializuje zapisy).
        tr = session.query(Trader).filter(Trader.id == trader.id).with_for_update().one()
        if tr.checkin_last == today:
            return {"streak": tr.checkin_streak or 0, "already": True, "reward": None,
                    "freeze_used": False, "freezes": tr.streak_freezes or 0}
        freeze_used = False
        if tr.checkin_last == yesterday:
            tr.checkin_streak = (tr.checkin_streak or 0) + 1
        elif tr.checkin_last == day_before and (tr.streak_freezes or 0) > 0:
            # 1 dzien przerwy uratowany freezem — seria idzie dalej
            tr.streak_freezes = (tr.streak_freezes or 0) - 1
            tr.checkin_streak = (tr.checkin_streak or 0) + 1
            freeze_used = True
        else:
            tr.checkin_streak = 1
        tr.checkin_last = today
        reward = None
        bonus = _CHECKIN_BONUS.get(tr.checkin_streak)
        if bonus:
            tr.bonus_points = (tr.bonus_points or 0) + bonus
            reward = {"type": "points", "amount": bonus}
        session.commit()
        telemetry.track("checkin", trader.id, streak=tr.checkin_streak)
        return {"streak": tr.checkin_streak, "already": False, "reward": reward,
                "freeze_used": freeze_used, "freezes": tr.streak_freezes or 0}
    finally:
        session.close()


@app.post("/api/me/daily-reveal")
def me_daily_reveal(trader: Trader = Depends(auth.current_trader)):
    """Raz dziennie losowa karta; wynik trzymany w bazie, wiec refresh nie przelosowuje.

    Kupony LUCKY sa osobiste: waznosc pilnowana po `expires_at` w payloadzie
    (billing sprawdza go przy checkoucie), wiec kod wyciekniety na Discorda
    nie dziala u nikogo, kto go nie wylosowal."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    session = SessionLocal()
    try:
        tr = session.query(Trader).filter(Trader.id == trader.id).with_for_update().one()
        if tr.reveal_last == today and tr.reveal_payload:
            return {**json.loads(tr.reveal_payload), "already": True}
        los = random.random()
        if los < 0.62:
            wynik = {"type": "tip", "index": random.randint(0, 23)}
        elif los < 0.82:
            wynik = {"type": "points", "amount": random.randint(5, 25)}
            tr.bonus_points = (tr.bonus_points or 0) + wynik["amount"]
        elif los < 0.90:
            if (tr.streak_freezes or 0) >= 3:
                # zapas pelny — freeze zamienia sie w punkty, zeby nagroda nie byla pusta
                wynik = {"type": "points", "amount": random.randint(5, 25)}
                tr.bonus_points = (tr.bonus_points or 0) + wynik["amount"]
            else:
                wynik = {"type": "freeze"}
                tr.streak_freezes = (tr.streak_freezes or 0) + 1
        elif los < 0.98:
            wynik = {"type": "coupon", "code": "LUCKY10", "pct": 10,
                     "expires_at": (now + timedelta(hours=48)).isoformat()}
        else:
            wynik = {"type": "coupon", "code": "LUCKY15", "pct": 15,
                     "expires_at": (now + timedelta(hours=48)).isoformat()}
        tr.reveal_last = today
        tr.reveal_payload = json.dumps(wynik)
        session.commit()
        telemetry.track("reveal", trader.id, kind=wynik.get("type"))
        return {**wynik, "already": False}
    finally:
        session.close()


# --- Program lojalnościowy: punkty -> własny kod rabatowy --------------------
class RedeemIn(BaseModel):
    reward: str      # klucz nagrody z loyalty.REWARDS


def _loyalty_payload(session, trader: Trader) -> dict:
    stan = loyalty.balance(session, trader)
    kody = (session.query(RewardCode)
            .filter(RewardCode.trader_id == trader.id)
            .order_by(RewardCode.id.desc()).limit(30).all())
    return {
        **stan,
        "tiers": [{"name": n, "min": p} for n, p in loyalty.TIERS],
        "rewards": [{**r, "affordable": stan["points_available"] >= r["cost"]}
                    for r in loyalty.REWARDS],
        "code_ttl_days": loyalty.CODE_TTL_DAYS,
        "codes": [loyalty.code_dict(k) for k in kody],
    }


@app.get("/api/me/loyalty")
def me_loyalty(trader: Trader = Depends(auth.current_trader)):
    """Stan programu: punkty, status, sklepik nagród i wydane kody.

    Punkty liczy SERWER. Wcześniej sumowała je przeglądarka z listy zamówień —
    do wyświetlenia to wystarczało, ale przy wymianie oznaczałoby, że trader
    sam sobie ustala, na co go stać.
    """
    session = SessionLocal()
    try:
        return _loyalty_payload(session, session.get(Trader, trader.id))
    finally:
        session.close()


@app.post("/api/me/loyalty/redeem")
def me_loyalty_redeem(payload: RedeemIn, trader: Trader = Depends(auth.current_trader)):
    """Wymienia punkty na własny kod jednorazowy i od razu je odejmuje."""
    session = SessionLocal()
    try:
        # FOR UPDATE jak przy check-inie: dwa równoległe kliknięcia nie mogą
        # kupić dwóch kodów za te same punkty.
        tr = session.query(Trader).filter(Trader.id == trader.id).with_for_update().one()
        try:
            kod = loyalty.redeem(session, tr, payload.reward)
        except ValueError:
            raise HTTPException(404, "Unknown reward")
        except LookupError as brakuje:
            raise HTTPException(400, f"You need {brakuje} more points for this reward")
        session.commit()
        telemetry.track("loyalty_redeem", trader.id, reward=payload.reward, pct=kod.pct)
        return {"code": loyalty.code_dict(kod), **_loyalty_payload(session, tr)}
    finally:
        session.close()


# --- Web push (PWA) ---------------------------------------------------------
class PushSubscribeIn(BaseModel):
    endpoint: str
    keys: dict   # {p256dh, auth} — dokładnie to, co oddaje pushManager.subscribe()


class PushUnsubscribeIn(BaseModel):
    endpoint: str


@app.get("/api/push/public-key")
def push_public_key():
    """Klucz publiczny VAPID dla przeglądarki (applicationServerKey)."""
    return {"enabled": push.is_enabled(),
            "key": settings.vapid_public_key or None}


@app.post("/api/me/push/subscribe")
def push_subscribe(payload: PushSubscribeIn, trader: Trader = Depends(auth.current_trader)):
    if not push.is_enabled():
        raise HTTPException(503, "Push notifications are not configured")
    p256dh = (payload.keys or {}).get("p256dh")
    auth_key = (payload.keys or {}).get("auth")
    if not payload.endpoint or not p256dh or not auth_key:
        raise HTTPException(400, "Invalid push subscription")
    session = SessionLocal()
    try:
        sub = (session.query(PushSubscription)
               .filter(PushSubscription.endpoint == payload.endpoint).first())
        if sub:
            # ten sam endpoint może wrócić po re-instalacji PWA / zmianie konta
            sub.trader_id, sub.p256dh, sub.auth = trader.id, p256dh, auth_key
        else:
            session.add(PushSubscription(trader_id=trader.id, endpoint=payload.endpoint,
                                         p256dh=p256dh, auth=auth_key))
        session.commit()
        telemetry.track("push_subscribed", trader.id)
        return {"ok": True}
    finally:
        session.close()


@app.post("/api/me/push/unsubscribe")
def push_unsubscribe(payload: PushUnsubscribeIn, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        (session.query(PushSubscription)
         .filter(PushSubscription.endpoint == payload.endpoint,
                 PushSubscription.trader_id == trader.id)
         .delete(synchronize_session=False))
        session.commit()
        return {"ok": True}
    finally:
        session.close()


@app.get("/api/me/telegram-link")
def telegram_link_status(trader: Trader = Depends(auth.current_trader)):
    """Stan parowania — panel odpytuje to po wydaniu kodu, żeby pokazać
    „Connected" w chwili, gdy `/start` dojdzie, bez przeładowania strony."""
    if not trader.is_admin:
        raise HTTPException(404, "Not Found")
    return {"linked": bool(trader.telegram_user_id),
            "username": trader.telegram_username}


@app.post("/api/me/telegram-link")
def telegram_link_code(trader: Trader = Depends(auth.current_trader)):
    """Kod parowania konta admina z Telegramem (Settings → Notifications).

    Wyłącznie dla adminów — parowanie służy podpisywaniu akcji na kanale LEADS
    mailem konta. 404 (nie 403) dla zwykłego tradera z tego samego powodu,
    dla którego /admin oddaje 404: endpoint dla nich nie istnieje.
    Nowy kod unieważnia poprzedni; sparowanie kodu nie kasuje."""
    if not trader.is_admin:
        raise HTTPException(404, "Not Found")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        kod = secrets.token_hex(3).upper()
        tr.telegram_link_code = kod
        session.commit()
        # Nazwa bota robi z instrukcji klikalny link t.me/<bot> — bez niej
        # „napisz do bota działu" wymaga wiedzy, który to bot.
        return {"code": kod, "linked": bool(tr.telegram_user_id),
                "bot": telegram.bot_username()}
    finally:
        session.close()


@app.post("/api/me/telegram-link/test")
def telegram_link_test(trader: Trader = Depends(auth.current_trader)):
    """Testowy DM na sparowane konto — dowód, że połączenie bot→admin działa
    (a nie tylko, że kod został skonsumowany)."""
    if not trader.is_admin:
        raise HTTPException(404, "Not Found")
    if not trader.telegram_user_id:
        raise HTTPException(400, "Telegram is not linked yet")
    # W prywatnym czacie chat_id == id użytkownika, więc uid wystarcza.
    ok, powod = telegram.send_dm(
        trader.telegram_user_id,
        f"Test from the panel — this connection works. "
        f"Your clicks on the LEADS channel sign as {trader.email}.")
    if not ok:
        # „chat not found" nie znaczy, że parowanie jest zepsute — znaczy, że to
        # konto Telegram nigdy nie odezwało się do BIEŻĄCEGO bota (np. token
        # panelu zmieniono po sparowaniu). Surowy komunikat Telegrama nie mówi,
        # co z tym zrobić, więc zamieniamy go na instrukcję.
        if "chat not found" in (powod or "").lower():
            bot = telegram.bot_username()
            raise HTTPException(502, (
                f"Open {('@' + bot) if bot else 'the panel bot'} in Telegram and press "
                "Start, then send the test again — this account has never written to "
                "that bot, so it has no chat to receive the message."))
        raise HTTPException(502, f"Telegram refused: {powod or 'unknown'}")
    return {"ok": True}


# --- Telemetria -------------------------------------------------------------
class TelemetryIn(BaseModel):
    name: str
    props: dict | None = None


# Whitelist zdarzeń klienckich: endpoint wymaga logowania, ale i tak nie
# pozwalamy klientowi wstrzykiwać dowolnych nazw do statystyk admina.
# Ruch marketingowy (strona publiczna) łapie GA4 — tu tylko produkt.
_TELEMETRY_CLIENT_EVENTS = {"view_open", "pwa_install", "js_error"}


@app.post("/api/telemetry")
def telemetry_ingest(payload: TelemetryIn, request: Request,
                     trader: Trader = Depends(auth.current_trader)):
    # view_open leci przy każdej nawigacji, więc limit jest hojny — ale bez
    # niego skrypt z ważnym tokenem zalałby telemetry_events bez ograniczeń.
    _rate_limit(request, "telemetry", 120)
    if payload.name not in _TELEMETRY_CLIENT_EVENTS:
        raise HTTPException(400, "Unknown event")
    props = {str(k)[:32]: str(v)[:80] for k, v in (payload.props or {}).items()}
    telemetry.track(payload.name, trader.id, **props)
    return {"ok": True}


@app.get("/api/admin/telemetry", dependencies=[Depends(auth.require_admin)])
def admin_telemetry():
    """Agregacja per dzień × zdarzenie z ostatnich 14 dni (UTC)."""
    session = SessionLocal()
    try:
        od = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)
        dzien = func.date(TelemetryEvent.created_at)
        rows = (session.query(dzien, TelemetryEvent.name,
                              func.count(TelemetryEvent.id),
                              func.count(func.distinct(TelemetryEvent.trader_id)))
                .filter(TelemetryEvent.created_at >= od)
                .group_by(dzien, TelemetryEvent.name)
                .order_by(dzien.desc(), TelemetryEvent.name).all())
        return {"items": [{"day": str(d), "name": n, "count": c, "traders": t}
                          for d, n, c, t in rows]}
    finally:
        session.close()


@app.get("/api/admin/telemetry/events", dependencies=[Depends(auth.require_admin)])
def admin_telemetry_events(name: str | None = None, day: str | None = None,
                           trader_id: int | None = None):
    """Drill-down agregatów: pojedyncze zdarzenia, filtr po nazwie/dniu/traderze."""
    session = SessionLocal()
    try:
        q = (session.query(TelemetryEvent, Trader.email)
             .outerjoin(Trader, Trader.id == TelemetryEvent.trader_id))
        if name:
            q = q.filter(TelemetryEvent.name == name)
        if day:
            # Doba jako PRZEDZIAŁ na kolumnie, nie `date(created_at) == '2026-08-04'`.
            # Tamto przechodziło na SQLite, a Postgres odbijał całe okno błędem
            # `operator does not exist: date = character varying` — psycopg wysyła
            # napis jako `text`, a takiego porównania z `date` Postgres nie zna.
            # Przy okazji przedział wchodzi na indeks, którego `date(...)` nie tyka.
            try:
                od_dnia = datetime.strptime(day, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "day must be YYYY-MM-DD")
            q = q.filter(TelemetryEvent.created_at >= od_dnia,
                         TelemetryEvent.created_at < od_dnia + timedelta(days=1))
        if trader_id is not None:
            q = q.filter(TelemetryEvent.trader_id == trader_id)
        rows = q.order_by(TelemetryEvent.id.desc()).limit(300).all()
        return {"items": [{"id": e.id, "ts": e.created_at.isoformat(), "name": e.name,
                           "props": e.props, "trader_id": e.trader_id, "email": em}
                          for e, em in rows]}
    finally:
        session.close()


# --- KYC: dokumenty ---------------------------------------------------------
_KYC_KINDS = {"id_front": "kyc_doc_front", "id_back": "kyc_doc_back", "residence": "kyc_doc_residence"}
_KYC_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
_KYC_MAX_BYTES = 5 * 1024 * 1024


@app.post("/api/me/kyc/docs")
async def kyc_upload_docs(trader: Trader = Depends(auth.current_trader),
                          id_front: UploadFile | None = File(default=None),
                          id_back: UploadFile | None = File(default=None),
                          residence: UploadFile | None = File(default=None)):
    files = {"id_front": id_front, "id_back": id_back, "residence": residence}
    if not any(files.values()):
        raise HTTPException(400, "No file was uploaded")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        # Ta sama bramka co na formularzu. Bez niej dokumenty tożsamości dałoby się
        # wgrać z pominięciem `POST /api/me/kyc` — a to właśnie ich zbieranie
        # bramka ma ograniczyć.
        if not kyc_dostepne(session, trader.id):
            raise HTTPException(403, KYC_WYMAGA_FUNDED)
        saved = []
        for kind, up in files.items():
            if up is None:
                continue
            ext = _KYC_MIME.get(up.content_type)
            if not ext:
                raise HTTPException(
                    400, f"{kind}: allowed formats are JPG, PNG and PDF "
                         f"(got {up.content_type or 'unknown'})")
            data = await up.read()
            if len(data) > _KYC_MAX_BYTES:
                raise HTTPException(400, f"{kind}: the file is larger than 5 MB")
            # Plik do bazy, nie na dysk — na Vercelu filesystem jest read-only.
            fname = f"{kind}-{secrets.token_hex(6)}{ext}"
            (session.query(KycFile)
             .filter(KycFile.trader_id == tr.id, KycFile.kind == kind)
             .delete(synchronize_session=False))
            session.add(KycFile(trader_id=tr.id, kind=kind, filename=fname,
                                mime=up.content_type, data=data))
            setattr(tr, _KYC_KINDS[kind], fname)
            saved.append(kind)
        session.commit()
        return {"uploaded": saved}
    finally:
        session.close()


@app.get("/api/admin/kyc/{trader_id}/doc/{kind}", dependencies=[Depends(auth.require_admin)])
def admin_kyc_doc(trader_id: int, kind: str):
    if kind not in _KYC_KINDS:
        raise HTTPException(404, "Unknown document type")
    session = SessionLocal()
    try:
        row = (session.query(KycFile)
               .filter(KycFile.trader_id == trader_id, KycFile.kind == kind)
               .first())
        if row:
            return Response(content=row.data, media_type=row.mime,
                            headers={"Content-Disposition": f'inline; filename="{row.filename}"'})
        # Stare uploady sprzed przejścia na bazę (tylko dev z zapisem na dysku)
        tr = session.get(Trader, trader_id)
        fname = getattr(tr, _KYC_KINDS[kind], None) if tr else None
        if not fname:
            raise HTTPException(404, "No document uploaded")
        path = UPLOADS / "kyc" / str(trader_id) / fname
        if not path.exists():
            raise HTTPException(404, "File not found")
        return FileResponse(str(path))
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  PANEL ADMINA — zatwierdzanie wypłat / KYC, zamówienia                      #
# --------------------------------------------------------------------------- #
@app.get("/api/admin/payout-requests", dependencies=[Depends(auth.require_admin)])
def admin_payout_requests():
    session = SessionLocal()
    try:
        # Tylko OTWARTE wnioski: jedyny konsument tej listy (licznik pendingów
        # na Overview) zamkniętych nie ogląda, a te mają swoje wiersze w widoku
        # Payouts (/api/admin/payouts). Bez filtra każde wejście na Overview
        # ciągnęło całą tabelę, która rośnie z każdym miesiącem.
        rows = (session.query(PayoutRequest).filter(PayoutRequest.status == "pending")
                .order_by(PayoutRequest.id.desc()).all())
        # Konta i maile hurtem: dwa `session.get` na wniosek to bylo 600 round-tripow
        # przy 300 wnioskach.
        konta = _konta_po_id(session, (r.account_id for r in rows))
        maile = _maile_traderow(session, (r.trader_id for r in rows))
        out = []
        for r in rows:
            acc = konta.get(r.account_id)
            tr_email = maile.get(r.trader_id)
            try:
                details = json.loads(r.details) if r.details else {}
            except ValueError:
                details = {}
            out.append({"id": r.id, "account_login": acc.login if acc else None,
                        "trader_email": tr_email, "profit_amount": r.profit_amount,
                        "trader_share": r.trader_share, "method": r.method, "details": details,
                        "status": r.status, "reject_reason": r.reject_reason,
                        "express": bool(acc.express_payout) if acc else False,
                        "ts": r.ts.isoformat()})
        # Klient zapłacił $49 za przeskoczenie kolejki — pending z add-onem
        # Express idą na górę listy, reszta zostaje w porządku „najnowsze pierwsze".
        out.sort(key=lambda w: (0 if (w["express"] and w["status"] == "pending") else 1))
        return out
    finally:
        session.close()


class PayoutImportIn(BaseModel):
    csv: str = ""
    commit: bool = False


@app.post("/api/admin/payouts/import", dependencies=[Depends(auth.require_admin)])
def admin_import_payouts(payload: PayoutImportIn):
    """Wgrywa historyczne wypłaty z CSV (ewidencja sprzed panelu).

    Bez `commit` zwraca sam podgląd — baza zostaje nietknięta. Wypłaty powstają
    jako rekordy wewnętrzne, BEZ publicznych certyfikatów; certyfikat wystawia
    się osobno, pod potwierdzoną wypłatę. Szczegóły: app/payout_import.py.
    """
    session = SessionLocal()
    try:
        return payout_import.uruchom(session, payload.csv, commit=payload.commit)
    finally:
        session.close()


@app.get("/api/admin/payouts", dependencies=[Depends(auth.require_admin)])
def admin_payouts_all():
    """Pełna lista wypłat do widoku Payouts: zaksięgowane wypłaty + otwarte wnioski.

    Widok pokazywał wyłącznie wnioski traderów, więc wypłaty wystawione ręcznie
    przez admina (POST /api/admin/accounts/{id}/payout) nie pojawiały się nigdzie
    poza kartą konta — lista w panelu nie zgadzała się z tym, ile realnie wyszło
    pieniędzy. Tutaj źródłem prawdy jest tabela `payouts`, a wnioski dokładamy
    tylko te, które nie stały się jeszcze wypłatą (pending/rejected); wniosek ze
    statusem `paid` ma już swój wiersz w `payouts` i dublowałby kwotę.

    `kind` rozdziela jedno od drugiego: "payout" ma certyfikat i można go wycofać,
    "request" ma przyciski Approve/Reject.

    Wiersze `…@imported.local` zostają — inaczej niż na listach ludzi. To księga
    pieniędzy: za każdym stoi wypłata, która naprawdę wyszła, a ten widok jest
    jedyną drogą do wystawienia jej certyfikatu. Ukrycie ich zabiłoby sens
    importu ewidencji.
    """
    session = SessionLocal()
    try:
        out = []
        rows = (session.query(Payout, Account, Trader)
                .join(Account, Payout.account_id == Account.id)
                .outerjoin(Trader, Account.trader_id == Trader.id)
                .order_by(Payout.ts.desc()).all())
        for p, acc, tr in rows:
            out.append({
                "kind": "payout", "id": p.id,
                "ts": p.ts.isoformat() if p.ts else None,
                "account_id": p.account_id,
                "account_login": acc.login if acc else None,
                "trader_email": tr.email if tr else None,
                "profit_amount": round(p.profit_amount, 2),
                "trader_share": round(p.trader_share, 2),
                "method": p.method or "bank", "details": {},
                "status": "paid" if p.paid else "pending",
                "reject_reason": None, "note": p.note, "express": False,
                "cert_url": f"/payout/{p.cert_token}" if p.cert_token else None,
                "show_on_lp": bool(getattr(p, "show_on_lp", True)),
            })

        reqs = (session.query(PayoutRequest)
                .filter(PayoutRequest.status != "paid")
                .order_by(PayoutRequest.id.desc()).all())
        konta = _konta_po_id(session, (r.account_id for r in reqs))
        maile = _maile_traderow(session, (r.trader_id for r in reqs))
        for r in reqs:
            acc = konta.get(r.account_id)
            tr_email = maile.get(r.trader_id)
            try:
                details = json.loads(r.details) if r.details else {}
            except ValueError:
                details = {}
            out.append({
                "kind": "request", "id": r.id,
                "ts": r.ts.isoformat() if r.ts else None,
                "account_id": acc.id if acc else None,
                "account_login": acc.login if acc else None,
                "trader_email": tr_email,
                "profit_amount": r.profit_amount, "trader_share": r.trader_share,
                "method": r.method, "details": details,
                "status": r.status, "reject_reason": r.reject_reason,
                "note": None, "cert_url": None,
                "express": bool(acc.express_payout) if acc else False,
            })

        out.sort(key=lambda r: r["ts"] or "", reverse=True)
        # Express ($49) = przeskoczenie kolejki: pending z add-onem nad resztą,
        # w obu grupach zostaje porządek „najnowsze pierwsze" (sort stabilny).
        out.sort(key=lambda w: (0 if (w["express"] and w["status"] == "pending") else 1))
        return out
    finally:
        session.close()


@app.post("/api/admin/payout-requests/{req_id}/approve", dependencies=[Depends(auth.require_admin)])
def admin_approve_payout(req_id: int):
    session = SessionLocal()
    try:
        r = session.get(PayoutRequest, req_id)
        if not r or r.status != "pending":
            raise HTTPException(404, "The request does not exist or was already handled")
        acc = session.get(Account, r.account_id)
        tr = session.get(Trader, r.trader_id)

        # Kwota wniosku jest zamrożona z chwili złożenia, a przegląd trwa do
        # 24 h — trader mógł w tym czasie zjechać zyskiem poniżej wnioskowanej
        # działki. Bez tej kontroli brakującą różnicę po cichu dopłacałaby
        # firma, bo clamp do salda startowego niżej maskuje brak pokrycia.
        split = acc.profit_split_pct / 100.0 if acc.profit_split_pct else 1.0
        available = round(round(acc.balance - acc.initial_balance, 2) * split, 2)
        if r.trader_share > available:
            raise HTTPException(400, f"The account no longer covers this payout "
                                     f"(available share: ${available:,.2f}). Reject the "
                                     f"request and ask the trader to submit a new one.")

        # Przejęcie wniosku warunkowym UPDATE-em (ten sam wzorzec co przejęcie
        # zamówienia w provisioning) — z dwóch równoległych approve zaksięguje
        # tylko jedno, drugie dostaje 404 zamiast dublować wypłatę, mail
        # i zwrot opłaty.
        zajete = (session.query(PayoutRequest)
                  .filter(PayoutRequest.id == req_id, PayoutRequest.status == "pending")
                  .update({"status": "paid"}, synchronize_session=False))
        if not zajete:
            raise HTTPException(404, "The request does not exist or was already handled")

        # zwrot opłaty za challenge przy PIERWSZEJ wypłacie (jak FTMO/The5ers).
        # Granty odpadają: zamówienie grantowe BOGO nosi cenę OPŁACONEGO tieru
        # (dla faktury, patrz billing.grant_challenge) — zwrot z niego oddawałby
        # tę samą opłatę drugi raz, bo konto kupione zwraca ją u siebie.
        first_payout = session.query(Payout).filter(Payout.account_id == acc.id).count() == 0
        fee_refund = 0.0
        if first_payout:
            order = (session.query(Order).filter(Order.account_id == acc.id, Order.status == "paid",
                                                 Order.provider != "grant")
                     .order_by(Order.id).first())
            fee_refund = round(order.amount_usd, 2) if order else 0.0

        session.add(Payout(account_id=acc.id, profit_amount=r.profit_amount,
                           trader_share=round(r.trader_share + fee_refund, 2), paid=True,
                           balance_reset=True, method=r.method))
        # Trader mógł poprosić o CZĘŚĆ dostępnej działki — z salda schodzi profit
        # proporcjonalny do wypłaconej kwoty (kwota / split). Pełna kwota sprowadza
        # konto dokładnie do salda startowego, jak dotychczasowy reset.
        consumed = round(r.trader_share / split, 2)
        new_balance = max(acc.initial_balance, round(acc.balance - consumed, 2))
        acc.balance = new_balance
        acc.equity = new_balance
        acc.peak_equity = new_balance
        acc.day_start_equity = new_balance
        acc.day_start_balance = new_balance
        acc.best_day_profit = 0.0
        session.commit()
        notify.send("payout_approved", tr.email, {"name": tr.full_name or tr.email,
                    "login": acc.login, "trader_share": round(r.trader_share + fee_refund, 2),
                    "fee_refund": bool(fee_refund)})
        return {"approved": req_id, "fee_refund": fee_refund,
                "total_paid": round(r.trader_share + fee_refund, 2)}
    finally:
        session.close()


class PayoutRejectIn(BaseModel):
    reason: str = ""


@app.post("/api/admin/payout-requests/{req_id}/reject", dependencies=[Depends(auth.require_admin)])
def admin_reject_payout(req_id: int, payload: PayoutRejectIn):
    """Odrzuca wniosek z powodem, który trader zobaczy przy swoim wniosku.

    Saldo konta nie zmienia się — odrzucenie niczego nie wypłaca, a trader może
    złożyć nowy wniosek od razu (np. z poprawionymi danymi przelewu)."""
    session = SessionLocal()
    try:
        r = session.get(PayoutRequest, req_id)
        if not r or r.status != "pending":
            raise HTTPException(404, "The request does not exist or was already handled")
        reason = (payload.reason or "").strip()[:200] or None
        # Warunkowy UPDATE jak przy approve — równoległy approve i reject tego
        # samego wniosku nie mogą przejść oba (wypłata + „odrzucono" naraz).
        zajete = (session.query(PayoutRequest)
                  .filter(PayoutRequest.id == req_id, PayoutRequest.status == "pending")
                  .update({"status": "rejected", "reject_reason": reason},
                          synchronize_session=False))
        if not zajete:
            raise HTTPException(404, "The request does not exist or was already handled")
        acc = session.get(Account, r.account_id)
        tr = session.get(Trader, r.trader_id)
        session.commit()
        if tr:
            notify.send("payout_rejected", tr.email, {
                "name": tr.full_name or tr.email, "login": acc.login if acc else "",
                "trader_share": r.trader_share,
                "reason": reason or "not specified"})
        return {"rejected": req_id, "reason": reason}
    finally:
        session.close()


class IssuePayoutIn(BaseModel):
    amount: float | None = None       # kwota dla tradera; None = pełny udział z zysku
    method: str = "bank"
    note: str | None = None
    reset_balance: bool = True        # jak przy zatwierdzeniu wniosku: zysk wypłacony
    # Czy wpis pokazuje się na pasie na landingu. Dokument, QR i weryfikacja
    # powstają NIEZALEŻNIE od tego — to decyzja o publikacji, nie o certyfikacie.
    show_on_lp: bool = True


class CertLpIn(BaseModel):
    show_on_lp: bool = True
    # Numer certyfikatu do ODTWORZENIA. Normalnie losujemy nowy, ale po pomyłkowym
    # wycofaniu trzeba wrócić DOKŁADNIE ten sam: stary numer siedzi w kodzie QR na
    # wydrukach i we wpisach, które już poszły w świat, więc nowy zostawiłby te
    # odwołania martwe.
    token: str | None = None


_TOKEN_RX = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


class LpVisibilityIn(BaseModel):
    show: bool


def _payout_dict(p: Payout, acc: Account | None = None) -> dict:
    return {"id": p.id, "ts": p.ts.isoformat() if p.ts else None,
            "profit_amount": round(p.profit_amount, 2),
            "trader_share": round(p.trader_share, 2), "paid": p.paid,
            "method": p.method, "note": p.note, "cert_token": p.cert_token,
            "cert_url": f"/payout/{p.cert_token}" if p.cert_token else None,
            "show_on_lp": bool(getattr(p, "show_on_lp", True)),
            "account": acc.login if acc else None}


@app.post("/api/admin/accounts/{account_id}/payout", dependencies=[Depends(auth.require_admin)])
def admin_issue_payout(request: Request, account_id: int, payload: IssuePayoutIn):
    """Wystawia wypłatę i od razu certyfikat — bez czekania na wniosek tradera.

    Idzie tą samą ścieżką księgową co zatwierdzenie wniosku: powstaje wiersz
    `Payout`, a konto wraca do salda startowego (zysk został wypłacony).
    """
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")

        if acc.status != "funded":
            raise HTTPException(400, "A payout can only be issued on a funded account — "
                                     f"this account's status is '{acc.status}'")
        profit = round(max(0.0, acc.balance - acc.initial_balance), 2)
        share = payload.amount if payload.amount is not None else round(
            profit * acc.profit_split_pct / 100.0, 2)
        share = round(float(share), 2)
        if share <= 0:
            raise HTTPException(400, "The payout amount must be greater than zero "
                                     f"(zysk na koncie: ${profit:,.2f})")

        p = Payout(account_id=acc.id, profit_amount=profit, trader_share=share, paid=True,
                   method=payload.method, note=(payload.note or None),
                   cert_token=secrets.token_urlsafe(16)[:32],
                   show_on_lp=bool(payload.show_on_lp),
                   balance_reset=bool(payload.reset_balance and profit > 0))
        session.add(p)

        # Wypłacony zysk znika z konta — inaczej ten sam zysk dałoby się wypłacić
        # w kółko, a krzywa equity kłamałaby o dostępnym kapitale.
        if payload.reset_balance and profit > 0:
            acc.balance = acc.initial_balance
            acc.equity = acc.initial_balance
            acc.peak_equity = acc.initial_balance
            acc.day_start_equity = acc.initial_balance
            acc.day_start_balance = acc.initial_balance
            acc.best_day_profit = 0.0
        session.commit()

        trader = session.get(Trader, acc.trader_id) if acc.trader_id else None
        if trader:
            notify.send("payout_approved", trader.email,
                        {"name": trader.full_name or trader.email, "login": acc.login,
                         "trader_share": share, "fee_refund": False,
                         "cert_url": f"{_public_base(request)}/payout/{p.cert_token}"})
        return _payout_dict(p, acc)
    finally:
        session.close()


@app.get("/api/admin/accounts/{account_id}/payouts", dependencies=[Depends(auth.require_admin)])
def admin_account_payouts(account_id: int):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        rows = (session.query(Payout).filter(Payout.account_id == acc.id)
                .order_by(Payout.id.desc()).all())
        available = round(max(0.0, acc.balance - acc.initial_balance)
                          * acc.profit_split_pct / 100.0, 2)
        return {"account": acc.login, "status": acc.status,
                "profit": round(max(0.0, acc.balance - acc.initial_balance), 2),
                "split_pct": acc.profit_split_pct, "suggested_share": available,
                "payouts": [_payout_dict(p, acc) for p in rows]}
    finally:
        session.close()


@app.post("/api/admin/payouts/{payout_id}/certificate", dependencies=[Depends(auth.require_admin)])
def admin_payout_certificate(payout_id: int, payload: CertLpIn | None = None):
    """Dorabia certyfikat do wypłaty, która powstała wcześniej (np. z wniosku).

    `show_on_lp` decyduje WYŁĄCZNIE o pasie na landingu. Dokument, jego QR i
    weryfikacja pod /payout/{token} powstają zawsze — trader dostaje ten sam
    certyfikat niezależnie od tego, czy zgodził się na publikację.
    """
    session = SessionLocal()
    try:
        p = session.get(Payout, payout_id)
        if not p:
            raise HTTPException(404, "Payout not found")
        widoczny = True if payload is None else bool(payload.show_on_lp)
        zadany = (payload.token or "").strip() if payload else ""
        if zadany:
            # Odtworzenie numeru po pomyłkowym wycofaniu. Nadpisujemy nawet gdy
            # certyfikat już jest — inaczej po wystawieniu nowego nie dałoby się
            # wrócić do numeru, który klient ma na wydruku i w kodzie QR.
            if not _TOKEN_RX.match(zadany):
                raise HTTPException(400, "A certificate number is 8-32 characters: "
                                         "letters, digits, '-' and '_'")
            zajety = (session.query(Payout)
                      .filter(Payout.cert_token == zadany, Payout.id != p.id).first())
            if zajety:
                raise HTTPException(409, "That certificate number belongs to another payout")
            p.cert_token = zadany
        elif not p.cert_token:
            p.cert_token = secrets.token_urlsafe(16)[:32]
        p.show_on_lp = widoczny
        session.commit()
        # Pas na landingu ma minutowy cache — bez tego świeży certyfikat
        # pojawiłby się dopiero przy następnym odświeżeniu.
        _PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
        return _payout_dict(p, session.get(Account, p.account_id))
    finally:
        session.close()


@app.post("/api/admin/payouts/{payout_id}/lp", dependencies=[Depends(auth.require_admin)])
def admin_payout_lp_visibility(payout_id: int, payload: LpVisibilityIn):
    """Wpuszcza wypłatę na pas na landingu albo ją z niego zdejmuje.

    Do tej pory jedynym sposobem zdjęcia wpisu ze strony było wycofanie
    certyfikatu, które zabijało też publiczny link tradera — czyli za decyzję
    „nie chcę tego na stronie" płacił dokumentem.
    """
    session = SessionLocal()
    try:
        p = session.get(Payout, payout_id)
        if not p:
            raise HTTPException(404, "Payout not found")
        if not p.cert_token and payload.show:
            raise HTTPException(400, "Issue the certificate first")
        p.show_on_lp = bool(payload.show)
        session.commit()
        _PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
        return _payout_dict(p, session.get(Account, p.account_id))
    finally:
        session.close()


@app.delete("/api/admin/payouts/{payout_id}/certificate", dependencies=[Depends(auth.require_admin)])
def admin_payout_certificate_revoke(payout_id: int):
    """Wycofuje certyfikat wypłaty: kasuje token i zdejmuje wpis z landingu.

    Potrzebne, bo do tej pory wystawienie certyfikatu było nieodwracalne —
    a na pas potrafiły trafić dane testowe albo wypłata wystawiona pomyłkowo.
    Po wycofaniu publiczny link /payout/{token} przestaje istnieć, wpis znika z
    /api/public/certificates/recent, a sama WYPŁATA zostaje w bazie (rekord
    księgowy się nie zmienia — znika tylko dokument).
    """
    session = SessionLocal()
    try:
        p = session.get(Payout, payout_id)
        if not p:
            raise HTTPException(404, "Payout not found")
        if p.cert_token:
            p.cert_token = None
            session.commit()
            _PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
        return _payout_dict(p, session.get(Account, p.account_id))
    finally:
        session.close()


@app.delete("/api/admin/payouts/{payout_id}", dependencies=[Depends(auth.require_admin)])
def admin_payout_delete(payout_id: int):
    """Kasuje wiersz wypłaty z ewidencji — pomyłkowy wpis albo cofnięcie importu.

    Znika sam zapis; SALDO KONTA zostaje takie, jakie jest. Wypłata wystawiona
    z `reset_balance` zdjęła kiedyś zysk z konta i nie odkręcamy tego
    automatycznie: przy żywym koncie nadpisalibyśmy bieżący stan equity danymi
    sprzed wielu dni. Wpisy z importu mają `balance_reset=False`, więc tam nie
    ma czego przywracać.
    """
    session = SessionLocal()
    try:
        p = session.get(Payout, payout_id)
        if not p:
            raise HTTPException(404, "Payout not found")
        mial_certyfikat = bool(p.cert_token)
        session.delete(p)
        session.commit()
        if mial_certyfikat:
            _PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
        return {"deleted": payout_id, "had_certificate": mial_certyfikat}
    finally:
        session.close()


@app.delete("/api/admin/payout-requests/{req_id}", dependencies=[Depends(auth.require_admin)])
def admin_payout_request_delete(req_id: int):
    """Kasuje wniosek o wypłatę, którego nie ma po co trzymać (spam, duplikat).

    Wniosku CZEKAJĄCEGO nie da się skasować — najpierw decyzja (approve/reject),
    żeby cicha kasacja nie zastąpiła odpowiedzi dla tradera.
    """
    session = SessionLocal()
    try:
        r = session.get(PayoutRequest, req_id)
        if not r:
            raise HTTPException(404, "Payout request not found")
        if r.status == "pending":
            raise HTTPException(400, "Approve or reject this request first — a trader is "
                                     "waiting for an answer, deleting it in silence is not one")
        session.delete(r)
        session.commit()
        return {"deleted": req_id}
    finally:
        session.close()


@app.get("/api/admin/traders", dependencies=[Depends(auth.require_admin)])
def admin_traders(q: str | None = None, imported: int = 0):
    """Lista klientów (do wyszukiwarki przy przyznawaniu challenge'u).

    Bez `q` zwraca wszystkich, do których da się cokolwiek wysłać — panel buduje
    z tego listę wyboru, a cichy limit ukrywałby starszych klientów i
    uniemożliwiał przyznanie im czegokolwiek.

    Pomijani są dokładnie ci, dla których ta lista i tak jest ślepą uliczką:
    zanonimizowani (`@removed.invalid`) i wiersze z ewidencji wypłat bez maila
    (`@imported.local` — adres wymyślony przez nas, mail poleciałby w próżnię).
    Ci drudzy wracają przy `?imported=1` i jest to JEDYNE wejście — panel do tego
    endpointu przełącznika „imported" nie podpina, bo nie ma tu listy z
    licznikiem, przy której mógłby usiąść.
    """
    session = SessionLocal()
    try:
        query = session.query(Trader).filter(Trader.is_admin == False)  # noqa: E712
        # Konta zanonimizowane przez /api/me/delete odpadają: logowanie mają
        # zablokowane (auth.current_trader), powiadomienia wyłączone, a mail na
        # @removed.invalid się odbija. Przyznanie im czegokolwiek to ślepa uliczka.
        query = query.filter(Trader.email.notlike("%@removed.invalid"))
        if not imported:
            query = query.filter(_nie_import())
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.filter(func.lower(Trader.email).like(like) |
                                 func.lower(Trader.full_name).like(like))
        rows = query.order_by(Trader.id.desc()).all()
        counts = dict(session.query(Account.trader_id, func.count(Account.id))
                      .group_by(Account.trader_id).all())
        # Ilu traderów przyszło z czyjego polecenia — jedno GROUP BY zamiast
        # COUNT-a na wiersz (lista bez limitu, jak konta wyżej).
        poleceni = dict(session.query(Trader.referred_by, func.count(Trader.id))
                        .filter(Trader.referred_by.isnot(None))
                        .group_by(Trader.referred_by).all())
        return [{"id": t.id, "email": t.email, "full_name": t.full_name,
                 "kyc_status": t.kyc_status, "accounts": counts.get(t.id, 0),
                 "credits_usd": round(float(t.credits_usd or 0), 2),
                 "referred_count": poleceni.get(t.referral_code, 0),
                 "created_at": t.created_at.isoformat() if t.created_at else None} for t in rows]
    finally:
        session.close()


# Nazwy zdarzeń telemetrii → opis w dzienniku klienta. Zdarzenie spoza mapy
# dostaje surową nazwę zamiast zniknąć: dziennik ma mówić „coś się działo",
# nawet gdy zapomnimy dopisać etykietę nowemu trackowi.
_DZIENNIK_OPISY = {
    "signup": "Signed up",
    "login": "Signed in",
    "account_claimed": "Claimed the account — password set from the invite link",
    "password_reset": "Reset the password (forgot password)",
    "password_changed": "Changed the password in the portal",
    "portal_invite": "Portal invite generated",
    "pay_link_opened": "Opened the payment link",
    "kyc_submitted": "Submitted KYC documents",
    "kyc_requested": "Asked for identity verification (e-mail from the panel)",
    "affiliate_claim": "Claimed affiliate commission",
    "achievement_reward": "Claimed an achievement reward",
    "checkin": "Daily check-in",
    "loyalty_redeem": "Redeemed loyalty points",
    "push_subscribed": "Enabled push notifications",
    "pwa_install": "Installed the portal app (PWA)",
    "js_error": "Hit an error in the portal",
}
_DZIENNIK_LOGOWANIA = ("login", "signup", "account_claimed")
_WARSZAWA = ZoneInfo("Europe/Warsaw")


def _dzis_po_warszawsku() -> datetime:
    """Początek dzisiejszego dnia W WARSZAWIE, jako naiwny UTC (format bazy).

    Dział czyta panel w polskim czasie — granica „logged in today" po północy
    UTC kłamałaby jeszcze przez dwie godziny polskiej nocy."""
    polnoc = datetime.now(_WARSZAWA).replace(hour=0, minute=0,
                                             second=0, microsecond=0)
    return polnoc.astimezone(timezone.utc).replace(tzinfo=None)


def _dzien_po_warszawsku(ts: datetime) -> str:
    return (ts.replace(tzinfo=timezone.utc)
            .astimezone(_WARSZAWA).strftime("%Y-%m-%d"))


@app.get("/api/admin/traders/{trader_id}/journal",
         dependencies=[Depends(auth.require_admin)])
def admin_trader_journal(trader_id: int):
    """Dziennik klienta: jedna oś czasu z odpowiedzią na „co on właściwie robił?".

    Trzy pytania, na które panel dotąd nie odpowiadał wprost: czy klient
    z kontem założonym za niego ODEBRAŁ je z linku, czy w ogóle się loguje
    (i czy dziś), i co po kolei działo się na jego kontach. Składane
    z istniejących wierszy (telemetria, zamówienia, wypłaty, konta, tickety) —
    bez nowej tabeli, więc działa też wstecz dla wszystkiego, co już w bazie
    siedzi. Otwarcia portalu (view_open) zwijane per dzień: godzinowa lista
    stu wejść zagrzebałaby zdarzenia, o które naprawdę chodzi.
    """
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        teraz = datetime.now(timezone.utc).replace(tzinfo=None)

        # Podsumowanie liczone osobnymi zapytaniami, nie z uciętej listy niżej:
        # limit osi czasu nie ma prawa przekłamać „ostatniego logowania".
        ostatni_login = (session.query(func.max(TelemetryEvent.created_at))
                         .filter(TelemetryEvent.trader_id == trader_id,
                                 TelemetryEvent.name.in_(_DZIENNIK_LOGOWANIA))
                         .scalar())
        logins_7d = (session.query(TelemetryEvent)
                     .filter(TelemetryEvent.trader_id == trader_id,
                             TelemetryEvent.name.in_(_DZIENNIK_LOGOWANIA),
                             TelemetryEvent.created_at >= teraz - timedelta(days=7))
                     .count())
        ostatnio_widziany = (session.query(func.max(TelemetryEvent.created_at))
                             .filter(TelemetryEvent.trader_id == trader_id)
                             .scalar())
        zaproszenie = (session.query(func.max(TelemetryEvent.created_at))
                       .filter(TelemetryEvent.trader_id == trader_id,
                               TelemetryEvent.name == "portal_invite").scalar())
        odebranie = (session.query(func.max(TelemetryEvent.created_at))
                     .filter(TelemetryEvent.trader_id == trader_id,
                             TelemetryEvent.name == "account_claimed").scalar())
        dzis = _dzis_po_warszawsku()

        items: list[dict] = []

        def add(ts, kind, label):
            if ts:
                items.append({"ts": ts.isoformat(), "kind": kind, "label": label})

        add(tr.created_at, "account", "Portal account created")
        # view_open osobnym zapytaniem: leci przy każdej nawigacji, więc we
        # wspólnym oknie z limitem wypchnąłby po paru tygodniach loginy i claim.
        zdarzenia = (session.query(TelemetryEvent)
                     .filter(TelemetryEvent.trader_id == trader_id,
                             TelemetryEvent.name != "view_open")
                     .order_by(TelemetryEvent.created_at.desc()).limit(500).all())
        wejscia = (session.query(TelemetryEvent)
                   .filter(TelemetryEvent.trader_id == trader_id,
                           TelemetryEvent.name == "view_open")
                   .order_by(TelemetryEvent.created_at.desc()).limit(1000).all())
        otwarcia: dict[str, list] = {}
        for e in wejscia:
            otwarcia.setdefault(_dzien_po_warszawsku(e.created_at), []).append(e)
        for e in zdarzenia:
            opis = _DZIENNIK_OPISY.get(e.name, e.name)
            try:
                props = json.loads(e.props) if e.props else {}
            except ValueError:
                props = {}
            if e.name in ("login", "signup") and props.get("google"):
                opis += " (Google)"
            if e.name == "account_claimed" and props.get("google"):
                opis = "Claimed the account — signed in with Google"
            if e.name == "account_claimed" and props.get("inferred"):
                # Wiersz odtworzony przez _uzupelnij_zgubione_claimy() — nie
                # wiemy, KTÓRĄ ścieżką klient odebrał konto, więc etykieta nie
                # może twierdzić, że ustawił hasło z linku.
                opis = "Claimed the account — recovered from portal activity"
            if e.name == "portal_invite" and props.get("sent") is False:
                opis = "Portal invite link copied (sent by hand)"
            if e.name == "pay_link_opened" and props.get("order"):
                opis += f" (order #{props['order']})"
            if e.name == "js_error" and props.get("m"):
                opis += f": {str(props['m'])[:80]}"
            add(e.created_at, "telemetry" if e.name not in _DZIENNIK_LOGOWANIA
                else "login", opis)
        for dzien, lista in otwarcia.items():
            ile = len(lista)
            opis = f"Opened the portal ({ile}×)" if ile > 1 else "Opened the portal"
            # Nazwy widoków z propsów — stare zdarzenia (tylko start appki) ich
            # nie mają, więc dopisek pojawia się dopiero tam, gdzie są dane.
            widoki: dict[str, int] = {}
            for z in lista:
                try:
                    w = (json.loads(z.props) if z.props else {}).get("view")
                except ValueError:
                    w = None
                if w:
                    widoki[w] = widoki.get(w, 0) + 1
            if widoki:
                opis += " — " + ", ".join(
                    f"{w} ×{n}" if n > 1 else w
                    for w, n in sorted(widoki.items(), key=lambda p: (-p[1], p[0])))
            add(max(z.created_at for z in lista), "view", opis)

        for o in (session.query(Order)
                  .filter(Order.trader_id == trader_id).all()):
            add(o.created_at, "order",
                f"Order #{o.id}: {o.product_key}, ${o.amount_usd:,.2f} ({o.status})")
            add(o.paid_at, "payment",
                f"Order #{o.id} paid: ${o.amount_usd:,.2f} via {o.provider}")
        konta = session.query(Account).filter(Account.trader_id == trader_id).all()
        for a in konta:
            add(a.created_at, "account", f"Account {a.platform_login or a.id} created "
                                         f"({a.product_key})")
            if a.closed_at:
                add(a.closed_at, "account",
                    f"Account {a.platform_login or a.id} closed ({a.status})")
        ids_kont = [a.id for a in konta]
        if ids_kont:
            for b in (session.query(Breach)
                      .filter(Breach.account_id.in_(ids_kont)).all()):
                add(b.ts, "breach", f"Breach: {b.type} — {b.detail}")
            for pr in (session.query(PayoutRequest)
                       .filter(PayoutRequest.account_id.in_(ids_kont)).all()):
                add(pr.ts, "payout", f"Payout requested: ${pr.trader_share:,.2f} "
                                     f"({pr.status})")
        for t in (session.query(SupportTicket)
                  .filter(SupportTicket.trader_id == trader_id).all()):
            add(t.created_at, "ticket", f"Support ticket #{t.id}: {t.subject} "
                                        f"({t.status})")

        items.sort(key=lambda i: i["ts"], reverse=True)
        return {
            "trader": {"id": tr.id, "email": tr.email, "full_name": tr.full_name,
                       "created_at": tr.created_at.isoformat() if tr.created_at else None,
                       "kyc_status": tr.kyc_status,
                       # Data prośby z panelu — panel pisze z niej „KYC asked ...”
                       # i zmienia napis na przycisku na „...again”.
                       "kyc_requested_at": (tr.kyc_requested_at.isoformat()
                                            if tr.kyc_requested_at else None),
                       # Wstrzymany portal jest niewidoczny z zewnątrz, a to
                       # pierwsza rzecz, o którą klient zapyta na supporcie —
                       # dział musi ją mieć na karcie, nie w bazie.
                       "kyc_locked": bool(tr.kyc_locked),
                       "awaiting_claim": bool(tr.must_set_password),
                       "invited_at": zaproszenie.isoformat() if zaproszenie else None,
                       "claimed_at": odebranie.isoformat() if odebranie else None,
                       "last_login_at": ostatni_login.isoformat() if ostatni_login else None,
                       "logged_in_today": bool(ostatni_login and ostatni_login >= dzis),
                       "logins_7d": logins_7d,
                       "last_seen_at": (ostatnio_widziany.isoformat()
                                        if ostatnio_widziany else None)},
            "items": items[:400],
        }
    finally:
        session.close()


@app.get("/api/admin/journal", dependencies=[Depends(auth.require_admin)])
def admin_journal_overview(imported: int = 0):
    """Aktywność wszystkich klientów naraz — wejście do dziennika per trader.

    Dziennik pojedynczego klienta odpowiada „co ON robił", ale pytanie działu
    brzmi zwykle odwrotnie: „KTÓRZY klienci czekają z nieodebranym kontem,
    którzy nigdy się nie zalogowali, kto był dziś?". Na to musi odpowiadać
    lista z filtrami, nie klikanie po kolei w sto kont.

    Agregaty liczone zapytaniami z GROUP BY po całej telemetrii, nie pętlą
    per trader — przy stu klientach pętla to czterysta zapytań na odświeżenie.
    """
    session = SessionLocal()
    try:
        teraz = datetime.now(timezone.utc).replace(tzinfo=None)
        dzis = _dzis_po_warszawsku()

        def _mapa(nazwy=None, od=None, licz=False):
            q = session.query(TelemetryEvent.trader_id,
                              func.count(TelemetryEvent.id) if licz
                              else func.max(TelemetryEvent.created_at))
            if nazwy is not None:
                q = q.filter(TelemetryEvent.name.in_(nazwy))
            if od is not None:
                q = q.filter(TelemetryEvent.created_at >= od)
            return dict(q.filter(TelemetryEvent.trader_id.isnot(None))
                        .group_by(TelemetryEvent.trader_id).all())

        logowania = _mapa(nazwy=_DZIENNIK_LOGOWANIA)
        logins7 = _mapa(nazwy=_DZIENNIK_LOGOWANIA,
                        od=teraz - timedelta(days=7), licz=True)
        widziani = _mapa()
        zaproszenia = _mapa(nazwy=("portal_invite",))
        odebrania = _mapa(nazwy=("account_claimed",))
        konta = dict(session.query(Account.trader_id, func.count(Account.id))
                     .filter(Account.trader_id.isnot(None))
                     .group_by(Account.trader_id).all())

        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        wiersze = []
        # Bez kont administratorów: to lista KLIENTÓW, a wiersz admina z setką
        # „logowań dziś" zaśmiecałby każdy filtr aktywności. Wiersze z importu
        # ewidencji odpadają tutaj i tylko tutaj — słowniki agregatów wyżej są
        # kluczowane po `trader_id`, więc wpisów pominiętych nikt nie odczyta.
        klienci = session.query(Trader).filter(Trader.is_admin.is_(False))
        if not imported:
            klienci = klienci.filter(_nie_import())
        for tr in klienci.all():
            login = logowania.get(tr.id)
            wiersze.append({
                "id": tr.id, "email": tr.email, "full_name": tr.full_name,
                "created_at": iso(tr.created_at),
                "kyc_status": tr.kyc_status,
                "awaiting_claim": bool(tr.must_set_password),
                "invited_at": iso(zaproszenia.get(tr.id)),
                "claimed_at": iso(odebrania.get(tr.id)),
                "last_login_at": iso(login),
                "logged_in_today": bool(login and login >= dzis),
                "logins_7d": int(logins7.get(tr.id) or 0),
                "last_seen_at": iso(widziani.get(tr.id)),
                "accounts": int(konta.get(tr.id) or 0),
            })
        wiersze.sort(key=lambda w: w["last_seen_at"] or w["created_at"] or "",
                     reverse=True)
        return {"items": wiersze}
    finally:
        session.close()


class CreditsIn(BaseModel):
    amount: float                      # dodatni = zasilenie, ujemny = korekta
    note: str | None = None


@app.post("/api/admin/traders/{trader_id}/credits", dependencies=[Depends(auth.require_admin)])
def admin_add_credits(trader_id: int, payload: CreditsIn):
    """Kredyty sklepowe: saldo USD odliczane automatycznie przy nastepnym
    zakupie challenge'a. Kazda zmiana zostawia slad w credit_ledger."""
    kwota = round(float(payload.amount), 2)
    if not kwota:
        raise HTTPException(400, "Amount must be non-zero")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        nowe = round(float(tr.credits_usd or 0) + kwota, 2)
        if nowe < 0:
            raise HTTPException(400, "Balance cannot go below zero")
        tr.credits_usd = nowe
        session.add(CreditLedger(trader_id=tr.id, amount=kwota,
                                 note=(payload.note or "").strip()[:160] or None))
        session.commit()
        email, imie = tr.email, (tr.full_name or tr.email)
        session.close()
        # Po committcie i poza sesją: mail + push + wpis w dzwonku jedną bramką.
        # Korekty w dół (kwota ujemna) po cichu — nie chwalimy się zabieraniem.
        if kwota > 0:
            notify.send("credits_granted", email,
                        {"name": imie, "amount": kwota, "balance": nowe})
        return {"trader_id": trader_id, "email": email, "credits_usd": nowe}
    finally:
        session.close()


class GrantIn(BaseModel):
    trader_id: int
    product_key: str
    note: str | None = None            # np. "BOGO promotion", "Compensation"
    bogo_paid_key: str | None = None   # tier, za który klient FAKTYCZNIE zapłacił
    funded: bool = False               # pomiń ewaluację — konto od razu funded


@app.post("/api/admin/grant", dependencies=[Depends(auth.require_admin)])
def admin_grant(payload: GrantIn):
    """Przyznanie challenge'u bez płatności — konto powstaje tak samo jak kupione."""
    session = SessionLocal()
    try:
        trader = session.get(Trader, payload.trader_id)
        if not trader or trader.is_admin:
            raise HTTPException(404, "Trader not found")
        res = billing.grant_challenge(session, trader, payload.product_key, payload.note,
                                      payload.bogo_paid_key)
        if payload.funded and res.get("account_id"):
            # Ominięcie ewaluacji: konto startuje jako funded z pełnym kapitałem.
            acc = session.get(Account, res["account_id"])
            if acc:
                _ustaw_faze(session, acc, "funded")
                res["status"], res["phase"] = acc.status, acc.phase
        return {"granted": True, "trader_email": trader.email, **res}
    finally:
        session.close()


def _ustaw_faze(session, acc: Account, faza: str) -> None:
    """Ręczne przestawienie fazy konta (admin). Resetuje metryki tak samo jak
    automatyczny awans w pollerze — inaczej konto weszłoby w nową fazę z cudzym
    dorobkiem, licznikiem dni i szczytem equity."""
    acc.phase = faza
    acc.status = "funded" if faza == "funded" else "active"
    acc.balance = acc.initial_balance
    acc.equity = acc.initial_balance
    acc.open_pnl = 0.0
    acc.peak_equity = acc.initial_balance
    acc.day_start_equity = acc.initial_balance
    acc.day_start_balance = acc.initial_balance
    acc.best_day_profit = 0.0
    acc.trading_days_count = 0
    acc.last_counted_trading_day = ""
    acc.breach_reason = None
    acc.closed_at = None
    session.commit()


CERT_KINDS = {
    "phase_1": ("Phase 1 passed", "Phase 1"),
    "phase_2": ("Phase 2 passed", "Phase 2"),
    "funded":  ("Funded trader", "Funded"),
}


def _cert_kind_available(acc: Account, kind: str) -> bool:
    """Czy konto FAKTYCZNIE ma to osiagniecie.

    Certyfikat jest publicznie weryfikowalny, wiec nie moze twierdzic czegos,
    czego w bazie nie ma. Gdy admin chce wystawic go wczesniej, najpierw
    przestawia faze konta — wtedy dokument znow mowi prawde.
    """
    passed_1 = acc.phase in ("eval_2", "funded") or acc.status in ("passed", "funded")
    if kind == "phase_1":
        return passed_1
    if kind == "phase_2":
        return acc.steps >= 2 and (acc.phase == "funded" or acc.status == "funded")
    if kind == "funded":
        return acc.status == "funded" or acc.phase == "funded"
    return False


class CertIn(BaseModel):
    kind: str      # phase_1 | phase_2 | funded


@app.get("/api/admin/accounts/{account_id}/certificates", dependencies=[Depends(auth.require_admin)])
def admin_certificates(account_id: int):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        wydane = {c.kind: c for c in session.query(Certificate)
                  .filter(Certificate.account_id == account_id).all()}
        return [{"kind": k, "label": CERT_KINDS[k][0],
                 "available": _cert_kind_available(acc, k),
                 "token": wydane[k].cert_token if k in wydane else None,
                 "url": f"/certificate/{wydane[k].cert_token}" if k in wydane else None}
                for k in CERT_KINDS]
    finally:
        session.close()


@app.post("/api/admin/accounts/{account_id}/certificate", dependencies=[Depends(auth.require_admin)])
def admin_issue_certificate(account_id: int, payload: CertIn):
    if payload.kind not in CERT_KINDS:
        raise HTTPException(400, "Unknown certificate type")
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        if not _cert_kind_available(acc, payload.kind):
            etap = CERT_KINDS[payload.kind][0]
            raise HTTPException(400, f"This account has not reached the {etap} stage. "
                                     f"Switch the account's phase first.")
        istnieje = (session.query(Certificate)
                    .filter(Certificate.account_id == account_id,
                            Certificate.kind == payload.kind).first())
        if istnieje is None:
            istnieje = Certificate(account_id=account_id, kind=payload.kind,
                                   cert_token=secrets.token_urlsafe(16)[:32])
            session.add(istnieje)
            session.commit()
        return {"kind": istnieje.kind, "token": istnieje.cert_token,
                "url": f"/certificate/{istnieje.cert_token}"}
    finally:
        session.close()


class PhaseIn(BaseModel):
    phase: str      # eval_1 | eval_2 | funded


@app.post("/api/admin/accounts/{account_id}/phase", dependencies=[Depends(auth.require_admin)])
def admin_set_phase(account_id: int, payload: PhaseIn):
    """Awans/cofnięcie fazy ręcznie. Automatyczny awans robi silnik reguł, ale
    przy odciętym feedzie (FEED=local) nikt kont nie tyka — wtedy to jedyna droga."""
    if payload.phase not in ("eval_1", "eval_2", "funded"):
        raise HTTPException(400, "Unknown phase")
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        if payload.phase == "eval_2" and acc.steps < 2:
            raise HTTPException(400, "This plan has no second stage")
        _ustaw_faze(session, acc, payload.phase)
        trader = session.get(Trader, acc.trader_id) if acc.trader_id else None
        if trader and payload.phase == "funded":
            notify.send("account_funded", trader.email,
                        {"name": trader.full_name or trader.email, "login": acc.login,
                         "split": acc.profit_split_pct})
        return {"phase": acc.phase, "status": acc.status, "login": acc.login}
    finally:
        session.close()


class BreachIn(BaseModel):
    reason: str | None = None


@app.post("/api/admin/accounts/{account_id}/breach", dependencies=[Depends(auth.require_admin)])
def admin_breach_account(account_id: int, payload: BreachIn):
    """Ręczne zamknięcie konta za złamanie zasad.

    Zapisuje wpis w historii breachy (typ `manual`), żeby powód został na stałe
    przy koncie — inaczej zostałby tylko w jednym polu i zniknął po pierwszej
    zmianie fazy. Trade BOT jest zatrzymywany: konto jest zamknięte, więc nie ma
    czego dalej rozgrywać.
    """
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        if acc.status == "failed":
            raise HTTPException(400, "This account is already closed")

        powod = (payload.reason or "").strip() or "Closed by the risk desk"
        if getattr(acc, "bot_enabled", False):
            tradebot.stop(session, acc)
        acc.status = "failed"
        acc.breach_reason = powod
        acc.closed_at = datetime.now(timezone.utc)
        session.add(Breach(account_id=acc.id, type="manual", detail=powod,
                           equity_at_breach=round(acc.equity or 0.0, 2)))
        session.commit()

        trader = session.get(Trader, acc.trader_id) if acc.trader_id else None
        if trader:
            notify.send("breached", trader.email,
                        {"name": trader.full_name or trader.email,
                         "login": acc.login, "reason": powod})
        return {"status": acc.status, "reason": acc.breach_reason, "login": acc.login}
    finally:
        session.close()


class BotIn(BaseModel):
    style: str = "balanced"      # scalper | balanced | swing
    pace: str = "steady"         # light (1-2/dzień) | steady (4-8) | busy (~20)
    target_pct: float = 0.0      # 0 = bez limitu zysku


@app.post("/api/admin/accounts/{account_id}/bot", dependencies=[Depends(auth.require_admin)])
def admin_bot_start(account_id: int, payload: BotIn):
    """Odpala Trade BOT-a na koncie.

    Od tej chwili konto NIE jest czytane z MT5 — snapshoty generuje tradebot,
    a przechodzą przez ten sam silnik reguł co dane z realnego feedu.
    """
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        if acc.status not in ("active", "funded"):
            raise HTTPException(400, f"Account status is '{acc.status}'. The bot only runs "
                                     f"on active and funded accounts")
        tradebot.start(session, acc, style=payload.style, pace=payload.pace,
                       target_pct=payload.target_pct)
        return {"bot_enabled": True, "login": acc.login, "style": acc.bot_style,
                "pace": acc.bot_pace, "target_pct": acc.bot_target_pct}
    finally:
        session.close()


class BotPauseIn(BaseModel):
    """Zmiana ustawień DZIAŁAJĄCEGO bota — oba pola opcjonalne, ale co najmniej jedno."""
    paused: bool | None = None
    target_pct: float | None = None


@app.patch("/api/admin/accounts/{account_id}/bot", dependencies=[Depends(auth.require_admin)])
def admin_bot_pause(account_id: int, payload: BotPauseIn):
    """Pauza/wznowienie oraz zmiana docelowego zysku.

    W odróżnieniu od Stop konto zostaje pod kontrolą bota, więc saldo nie wraca
    do feedu — ani po wznowieniu, ani po podniesieniu celu krzywa nie dostaje
    uskoku. Podniesienie celu to jedyny sposób, żeby ruszyć bota, który dobił do
    swojego `bot_target_pct` i przestał otwierać pozycje.
    """
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        if not acc.bot_enabled:
            raise HTTPException(400, "Trade BOT is not running on this account")
        if payload.paused is None and payload.target_pct is None:
            raise HTTPException(400, "Provide 'paused' or 'target_pct'")
        if payload.target_pct is not None:
            if payload.target_pct < 0:
                raise HTTPException(400, "The target cannot be negative")
            tradebot.set_target(session, acc, payload.target_pct)
        if payload.paused is not None:
            tradebot.set_paused(session, acc, payload.paused)
        return {"bot_enabled": True, "bot_paused": acc.bot_paused,
                "bot_target_pct": acc.bot_target_pct, "login": acc.login}
    finally:
        session.close()


@app.delete("/api/admin/accounts/{account_id}/bot", dependencies=[Depends(auth.require_admin)])
def admin_bot_stop(account_id: int):
    """Zatrzymuje bota. Otwarta pozycja jest domykana po bieżącym floatingu."""
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        tradebot.stop(session, acc)
        return {"bot_enabled": False, "login": acc.login, "balance": round(acc.balance, 2)}
    finally:
        session.close()


def _kyc_dict(t: Trader) -> dict:
    return {"trader_id": t.id, "email": t.email, "full_name": t.kyc_fullname,
            "country": t.kyc_country, "doc_ref": t.kyc_doc_ref,
            "dob": t.kyc_dob, "address": t.kyc_address,
            "id_type": t.kyc_id_type, "id_number": t.kyc_id_number,
            "docs": [k for k, col in _KYC_KINDS.items() if getattr(t, col, None)],
            "status": t.kyc_status,
            "submitted_at": t.kyc_submitted_at.isoformat() if t.kyc_submitted_at else None,
            "reviewed_at": t.kyc_reviewed_at.isoformat() if t.kyc_reviewed_at else None}


@app.get("/api/admin/kyc", dependencies=[Depends(auth.require_admin)])
def admin_kyc(imported: int = 0):
    """Oczekujące wnioski + historia decyzji (approved/rejected) do przeglądania."""
    session = SessionLocal()
    try:
        pending = session.query(Trader).filter(Trader.kyc_status == "pending")
        history = (session.query(Trader)
                   .filter(Trader.kyc_status.in_(("approved", "rejected"))))
        if not imported:
            pending = pending.filter(_nie_import())
            history = history.filter(_nie_import())
        pending = pending.all()
        history = (history
                   .order_by(Trader.kyc_reviewed_at.desc().nullslast(), Trader.id.desc())
                   .limit(200).all())
        return {"pending": [_kyc_dict(t) for t in pending],
                "history": [_kyc_dict(t) for t in history]}
    finally:
        session.close()


# Ile próśb wychodzi z jednego kliknięcia. Maile lecą w tle TEGO wywołania
# funkcji (`_powiadomienia_po_odpowiedzi`), a jeden SMTP to ~0,7 s — cała lista
# w jednym strzale przekroczyłaby limit czasu funkcji i część maili przepadłaby
# bez śladu. Panel woła endpoint w pętli, aż `left` spadnie do zera.
KYC_PACZKA = 20


def _kanal_free(session):
    """Traderzy, którzy dostali darmowy challenge z kanału FREE.

    Rozpoznajemy po notatce, którą wystawia przycisk „Free account"
    (`notify.FREE_PROGRAM_NOTE`), a nie po samym `source="grant"`: tą samą
    drogą powstaje drugie konto z promocji BOGO i wiersz archiwalny z ewidencji
    wypłat, a to są klienci, którzy nam zapłacili.
    """
    return (session.query(Trader)
            .join(Account, Account.trader_id == Trader.id)
            .filter(Account.source == "grant",
                    func.lower(func.coalesce(Account.grant_note, ""))
                    == notify.FREE_PROGRAM_NOTE,
                    _nie_import())
            .order_by(Trader.id).distinct())


def _czeka_na_prosbe(tr: Trader) -> bool:
    """Czy tego tradera hurtowa wysyłka ma jeszcze dotknąć.

    `kyc_requested_at` jest jednocześnie znacznikiem wysłanej prośby, więc
    powtórne kliknięcie nie wyśle drugiego maila — a to jedyne zabezpieczenie,
    jakie ta operacja może mieć: raz wysłanego maila nie da się cofnąć.
    """
    return (tr.kyc_status not in ("approved", "pending")
            and not tr.kyc_requested_at)


def _kanal_free_wiersz(tr: Trader) -> dict:
    return {"id": tr.id, "email": tr.email, "name": tr.full_name or tr.email,
            "kyc_status": tr.kyc_status, "kyc_locked": bool(tr.kyc_locked),
            "kyc_requested_at": (tr.kyc_requested_at.isoformat()
                                 if tr.kyc_requested_at else None)}


# Obie trasy MUSZĄ stać przed `/api/admin/kyc/{trader_id}/…`: FastAPI dopasowuje
# w kolejności rejestracji, więc niżej „free-channel" wpadałoby w `{trader_id}`
# i kończyło się 422 (nie da się sparsować jako int).
@app.get("/api/admin/kyc/free-channel", dependencies=[Depends(auth.require_admin)])
def admin_free_channel_kyc():
    """Podgląd przed wysyłką: kto z kanału FREE dostanie prośbę o weryfikację.

    Hurtowa wysyłka maili jest nieodwracalna, więc panel pokazuje imienną listę
    ZANIM cokolwiek wyjdzie. `done` jest równie ważne co `waiting`: bez niego
    nie widać, że poprzednie kliknięcie zadziałało, i ta sama akcja odpalałaby
    się drugi raz „na wszelki wypadek".
    """
    session = SessionLocal()
    try:
        wszyscy = _kanal_free(session).all()
        czekaja = [t for t in wszyscy if _czeka_na_prosbe(t)]
        return {"waiting": [_kanal_free_wiersz(t) for t in czekaja],
                "done": [_kanal_free_wiersz(t) for t in wszyscy
                         if not _czeka_na_prosbe(t)],
                "batch": KYC_PACZKA}
    finally:
        session.close()


@app.post("/api/admin/kyc/free-channel/request",
          dependencies=[Depends(auth.require_admin)])
def admin_free_channel_kyc_request(limit: int = KYC_PACZKA):
    """Prośba o weryfikację do całego kanału FREE — paczkami, idempotentnie.

    Darmowy challenge dostaje ktoś, kogo jeszcze nie znamy, a prezent ściąga
    dublerów: jedna osoba na trzech adresach. Dlatego prośba idzie w komplecie
    ze wstrzymaniem portalu (`kyc_locked`) — konto na MT5 pracuje dalej, ale
    panel otwiera się dopiero po akceptacji dokumentów.

    Ktoś, kto ma już `pending` albo `approved`, wypada z listy: zrobił swoje.
    Kto dostał prośbę wcześniej, też — znacznik `kyc_requested_at` jest po to,
    żeby drugie kliknięcie nie wysłało drugiego maila.
    """
    limit = max(1, min(int(limit or KYC_PACZKA), KYC_PACZKA))
    session = SessionLocal()
    try:
        czekaja = [t for t in _kanal_free(session).all() if _czeka_na_prosbe(t)]
        paczka = czekaja[:limit]
        for tr in paczka:
            _popros_o_kyc(session, tr, wstrzymaj_portal=True)
        return {"sent": [t.email for t in paczka],
                "count": len(paczka), "left": len(czekaja) - len(paczka)}
    finally:
        session.close()


@app.delete("/api/admin/kyc/{trader_id}", dependencies=[Depends(auth.require_admin)])
def admin_kyc_delete(trader_id: int):
    """Kasuje weryfikację: dane KYC i WGRANE DOKUMENTY znikają z dysku.

    Wiersz wypada z historii, bo lista pokazuje traderów ze statusem
    approved/rejected — po wyczyszczeniu status wraca do "none" i trader może
    złożyć KYC od nowa. To także jedyny sposób usunięcia skanu dowodu z serwera,
    więc kasujemy pliki, a nie tylko odnośniki do nich.

    Nie mylić z Revert (POST .../revert), który tylko cofa decyzję do kolejki i
    zostawia komplet danych.
    """
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        katalog = UPLOADS / "kyc" / str(trader_id)
        usuniete = 0
        for nazwa in (tr.kyc_doc_front, tr.kyc_doc_back, tr.kyc_doc_residence):
            if not nazwa:
                continue
            plik = katalog / nazwa
            try:
                if plik.is_file():
                    plik.unlink()
                    usuniete += 1
            except OSError:                       # brak pliku nie blokuje czyszczenia bazy
                pass
        tr.kyc_fullname = tr.kyc_country = tr.kyc_doc_ref = None
        tr.kyc_dob = tr.kyc_address = tr.kyc_id_type = tr.kyc_id_number = None
        tr.kyc_doc_front = tr.kyc_doc_back = tr.kyc_doc_residence = None
        tr.kyc_status = "none"
        tr.kyc_submitted_at = tr.kyc_reviewed_at = None
        tr.kyc_reject_reason = None
        session.commit()
        return {"deleted": trader_id, "files_removed": usuniete}
    finally:
        session.close()


@app.post("/api/admin/kyc/{trader_id}/approve", dependencies=[Depends(auth.require_admin)])
def admin_approve_kyc(trader_id: int):
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        tr.kyc_status = "approved"
        tr.kyc_reviewed_at = datetime.now(timezone.utc)
        tr.kyc_reject_reason = None
        # Akceptacja to jedyne wyjście ze wstrzymanego portalu
        # (`auth.portal_wstrzymany`) — musi je zdjąć to samo kliknięcie, którym
        # admin zatwierdza dokumenty, bo osobnego przycisku „odblokuj" nie ma.
        tr.kyc_locked = False
        session.commit()
        notify.send("kyc_approved", tr.email, {"name": tr.full_name or tr.email})
        return {"approved": trader_id}
    finally:
        session.close()


class KycRejectIn(BaseModel):
    reason: str | None = None          # pokazywany traderowi (portal + mail)


@app.post("/api/admin/kyc/{trader_id}/reject", dependencies=[Depends(auth.require_admin)])
def admin_reject_kyc(trader_id: int, payload: KycRejectIn | None = None):
    """Odrzuca weryfikację — trader może poprawić dane i wysłać KYC ponownie."""
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        powod = ((payload.reason if payload else None) or "").strip()[:200] or None
        tr.kyc_status = "rejected"
        tr.kyc_reviewed_at = datetime.now(timezone.utc)
        tr.kyc_reject_reason = powod
        session.commit()
        notify.send("kyc_rejected", tr.email,
                    {"name": tr.full_name or tr.email, "reason": powod})
        return {"rejected": trader_id, "reason": powod}
    finally:
        session.close()


def _popros_o_kyc(session, tr: Trader, *, wstrzymaj_portal: bool) -> None:
    """Środek prośby o weryfikację: otwarcie KYC, mail, ślad w historii leada.

    Wspólny dla prośby z ręki, wysyłki hurtowej do kanału FREE i przyznania
    darmowego konta — trzy wejścia, jedna treść i jeden komplet skutków.
    Rozjazd między nimi znaczyłby, że klient dostaje inny mail w zależności od
    tego, którym przyciskiem admin go dotknął.

    `wstrzymaj_portal` zamyka panel do czasu akceptacji (`kyc_locked`). Włączamy
    go tam, gdzie konto powstało jako prezent — tożsamości takiego klienta nikt
    nie sprawdzał, a darmowy challenge ściąga dublerów.
    """
    tr.kyc_requested_at = datetime.now(timezone.utc)
    if wstrzymaj_portal:
        tr.kyc_locked = True
    session.commit()
    base = get_settings().app_base_url
    notify.send("kyc_requested", tr.email,
                {"name": tr.full_name or tr.email,
                 "again": tr.kyc_status == "rejected",
                 "locked": bool(tr.kyc_locked),
                 "portal_url": f"{base}/portal"})
    telemetry.track("kyc_requested", tr.id)
    # Ślad w historii leada, jak przy portal invite — „czy myśmy go o to
    # prosili?" pada tydzień później i musi mieć odpowiedź.
    lead = (session.query(Lead)
            .filter(func.lower(Lead.email) == tr.email.lower()).one_or_none())
    if lead:
        _zdarzenie(session, lead.id, "email", "KYC verification requested",
                   actor="panel")
        session.commit()


@app.post("/api/admin/kyc/{trader_id}/request", dependencies=[Depends(auth.require_admin)])
def admin_request_kyc(trader_id: int):
    """Prośba o weryfikację wysłana Z RĘKI — dla klienta, który nie złożył KYC.

    Sam z siebie panel o KYC nie przypomina: trader dostaje maila dopiero po
    decyzji (approve/reject), więc ktoś, kto przeszedł ewaluację i nigdy nie
    wszedł w zakładkę weryfikacji, siedzi cicho w nieskończoność — a bez KYC nie
    wypłacimy mu pieniędzy. Zwykle wychodzi to dopiero przy wniosku o wypłatę,
    którym trzeba wtedy odmówić.

    Prośba OTWIERA weryfikację (`kyc_requested_at` → `kyc_dostepne`), więc działa
    też dla klienta bez konta funded — a to najczęstszy przypadek, w którym ktoś
    sam prosi o weryfikację i odbija się od domyślnej bramki. Bez tego mail
    zapraszałby na ekran, który odpowie 403 (`KYC_WYMAGA_FUNDED`).

    `pending` i `approved` odpadają, bo nie ma o co prosić — dokumenty już są.
    Przy `rejected` prośba jest dozwolona: to ponaglenie do poprawki.
    """
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        if tr.kyc_status == "approved":
            raise HTTPException(400, "This client is already verified")
        if tr.kyc_status == "pending":
            raise HTTPException(400, "Documents are already in — the application "
                                     "is waiting for review")
        _popros_o_kyc(session, tr, wstrzymaj_portal=False)
        return {"ok": True, "email": tr.email, "kyc_status": tr.kyc_status}
    finally:
        session.close()


@app.post("/api/admin/kyc/{trader_id}/reset", dependencies=[Depends(auth.require_admin)])
def admin_reset_kyc(trader_id: int):
    """Cofa decyzję approve/reject — wniosek wraca do kolejki pending.

    Celowo bez maila/pusha do tradera: to korekta po stronie admina, nie nowa
    decyzja; trader zobaczy status "pending" w portalu."""
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        if tr.kyc_status not in ("approved", "rejected"):
            raise HTTPException(400, "There is no KYC decision to revert")
        tr.kyc_status = "pending"
        tr.kyc_reviewed_at = None
        tr.kyc_reject_reason = None
        session.commit()
        return {"reset": trader_id}
    finally:
        session.close()


@app.get("/api/admin/orders", dependencies=[Depends(auth.require_admin)])
def admin_orders():
    session = SessionLocal()
    try:
        rows = session.query(Order).order_by(Order.id.desc()).limit(100).all()
        # Maile jednym zapytaniem zamiast osobnego na kazde zamowienie: przy 100
        # pozycjach to bylo 101 round-tripow do bazy, a baza stoi za oceanem.
        maile = _maile_traderow(session, (o.trader_id for o in rows))
        out = []
        for o in rows:
            out.append({"id": o.id, "trader_email": maile.get(o.trader_id),
                        "product_key": o.product_key, "amount_usd": o.amount_usd,
                        "status": o.status, "provider": o.provider, "coupon": o.coupon,
                        "bogo": bool(getattr(o, "bogo", False)),
                        "flag": o.flag, "fail_reason": o.fail_reason,
                        "payment_address": o.payment_address,
                        "payment_network": o.payment_network,
                        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                        "account_id": o.account_id, "created_at": o.created_at.isoformat()})
        return out
    finally:
        session.close()


@app.delete("/api/admin/orders/{order_id}", dependencies=[Depends(auth.require_admin)])
def admin_order_delete(order_id: int):
    """Kasuje zamówienie z listy — porzucony koszyk, test, duplikat.

    Konto założone z tego zamówienia ZOSTAJE: żyje własnym życiem (trader na nim
    handluje) i kasowanie go razem z paragonem byłoby niespodzianką. Zamówienie
    opłacone znika też z przychodu w Overview, bo ten liczy się z tej listy.
    """
    session = SessionLocal()
    try:
        o = session.get(Order, order_id)
        if not o:
            raise HTTPException(404, "Order not found")
        # Kod za punkty i wpis w księdze kredytów wskazują na zamówienie (FK na
        # orders.id) i zostają — odpinamy je zamiast kasować. Kod ma dalej być
        # zużyty: `used_at` trzyma jednorazowość, a skasowanie paragonu nie może
        # go palić drugi raz. Kredyt to pieniądze klienta, nie paragon.
        for model in (RewardCode, CreditLedger):
            (session.query(model).filter(model.order_id == o.id)
             .update({model.order_id: None}, synchronize_session=False))
        session.delete(o)
        session.commit()
        return {"deleted": order_id}
    finally:
        session.close()


class ManualOrderIn(BaseModel):
    # Właściciel zamówienia przychodzi na jeden z dwóch sposobów: `trader_id` z
    # listy klientów albo sam `email` — z karty leada, gdzie konta jeszcze nie
    # ma i nie będzie, dopóki ktoś nie zapłaci.
    trader_id: int | None = None
    email: str | None = None
    product_key: str
    amount_usd: float | None = None
    flag: str = "awaiting_crypto"
    notify_trader: bool = True
    payment_address: str | None = None
    payment_network: str | None = None
    # Czy to sprzedaż po cenie partnerskiej. Kwotę liczy panel (widać ją, zanim
    # admin kliknie), tu zapada tylko decyzja, czy zamówienie ma nieść ślad umowy.
    partner_discount: bool = False
    # Buy 1 Get 1 Free per zamówienie: po opłaceniu provisioning dorzuci drugie
    # konto tego samego rozmiaru. Checkbox w panelu (pre-fill z globalnego
    # przełącznika) — tu przychodzi już ostateczna decyzja admina dla tego leada.
    bogo: bool = False


def _zapisz_adres_wplaty(o: Order, adres: str | None, siec: str | None) -> None:
    """Adres wpłaty podany przez admina. Pusty NIE kasuje zapisanego — klient
    dostał go już mailem i musi zostać w zamówieniu jako ślad, dokąd te
    pieniądze miały pójść."""
    if adres and adres.strip():
        o.payment_address = adres.strip()[:200]
    if siec and siec.strip():
        o.payment_network = siec.strip()[:40]


def _mail_oczekujemy_na_platnosc(session, o: Order) -> bool:
    """Mail „czekamy na Twoją wpłatę" — wspólny dla ręcznego zamówienia i flagi.

    Adres bierzemy z ZAMÓWIENIA (admin wpisuje go ręcznie, bo adresy są
    rotowane); gdy go tam nie ma, szablon sięga po stały z konfiguracji."""
    tr = session.get(Trader, o.trader_id)
    if tr is None or not tr.email:
        return False
    produkt = session.query(Product).filter(Product.key == o.product_key).first()
    notify.send("order_awaiting_payment", tr.email, {
        "name": tr.first_name or tr.full_name or "trader",
        "product_label": produkt.label if produkt else o.product_key,
        "amount": o.amount_usd,
        # Numer zamówienia w formie, którą klient wkleja w odpowiedzi — przelew
        # krypto nie niesie tytułu, więc to jedyne, co go łączy z zamówieniem.
        "reference": f"PTF-{o.id}",
        "wallet": o.payment_address or "",
        "network": o.payment_network or "",
        "bogo": bool(getattr(o, "bogo", False)),
    })
    return True


def _trader_po_mailu(session, email: str) -> tuple[Trader, bool]:
    """Trader o tym mailu; nie ma takiego — powstaje. Zwraca `(trader, świeżo założony)`.

    Istnieje dla leada bez konta. Sprzedaż dogaduje się na Telegramie i nikt
    nie zakłada konta po to, żeby dostać link do zapłaty albo darmowy
    challenge — a bez konta nie ma na czym powiesić ani zamówienia, ani
    grantu. Konto powstaje więc tutaj, dokładnie tak jak przy imporcie wypłat
    (`payout_import.zapisz`): hasła nie znamy i nie wymyślamy. Stąd
    `must_set_password` — dzięki niemu mail z poświadczeniami MT5 daje
    klientowi link do ustawienia hasła zamiast kazać mu „zalogować się" na
    konto, o którym pierwszy raz słyszy.

    To jest zarazem jedyna droga, żeby panel leadów kiedykolwiek pokazał
    „Bought". Ta kolumna nie jest zapisywana, tylko liczona przy odczycie —
    lead → trader po mailu → suma zapłaconych zamówień. Bez konta pieniądze nie
    miałyby jak wrócić na kartę leada, choćby wpłynęły.
    """
    email = (email or "").strip().lower()
    if not _EMAIL_RX.fullmatch(email):
        raise HTTPException(400, "Enter a valid e-mail address")
    # Po `lower()`, bo maile w bazie bywają z wielkiej litery (import, Google) i
    # dopasowanie 1:1 założyłoby drugie konto na ten sam adres.
    trader = session.query(Trader).filter(func.lower(Trader.email) == email).first()
    if trader:
        if trader.is_admin:
            raise HTTPException(404, "Trader not found")
        return trader, False

    # Nazwisko z leada, a nie puste: idzie stąd na konto MT5 i na certyfikaty.
    lead = session.query(Lead).filter(func.lower(Lead.email) == email).first()
    for _ in range(5):
        code = _gen_ref_code()
        if not session.query(Trader).filter(Trader.referral_code == code).first():
            break
    trader = Trader(email=email,
                    password_hash=auth.hash_password(secrets.token_urlsafe(24)),
                    must_set_password=True,
                    full_name=((lead.name if lead else "") or "")[:120],
                    referral_code=code)
    session.add(trader)
    session.flush()
    return trader, True


def _wlasciciel_zamowienia(session, payload: ManualOrderIn) -> tuple[Trader, bool]:
    """Trader, na którego idzie ręczne zamówienie. Zwraca `(trader, świeżo założony)`."""
    if payload.trader_id is not None:
        trader = session.get(Trader, payload.trader_id)
        if not trader or trader.is_admin:
            raise HTTPException(404, "Trader not found")
        return trader, False
    return _trader_po_mailu(session, payload.email or "")


@app.post("/api/admin/orders", dependencies=[Depends(auth.require_admin)])
def admin_order_create(payload: ManualOrderIn):
    """Ręcznie wystawione zamówienie — klient płaci poza Stripe'em (crypto, przelew).

    Zamówienie startuje jako `pending`: konto powstaje dopiero z Mark paid, tą
    samą drogą co po webhooku Stripe'a. Kwota to DOKŁADNIE ta podana (domyślnie
    cena z cennika) — kuponów i kredytów sklepowych tu nie liczymy, bo robi to
    checkout; przy ręcznym zamówieniu cenę ustala admin i nic nie schodzi
    z salda klienta przed wpłatą.

    Klienta wskazuje `trader_id` albo `email` — patrz `_wlasciciel_zamowienia`.
    """
    if payload.flag not in ("", "awaiting_crypto"):
        raise HTTPException(400, "Unknown flag")
    session = SessionLocal()
    try:
        trader, nowy_trader = _wlasciciel_zamowienia(session, payload)
        produkt = (session.query(Product)
                   .filter(Product.key == payload.product_key, Product.active == True)  # noqa: E712
                   .first())
        if not produkt:
            raise HTTPException(404, "Product not found")
        kwota = round(float(produkt.price_usd if payload.amount_usd is None
                            else payload.amount_usd), 2)
        if kwota < 0:
            raise HTTPException(400, "Amount cannot be negative")
        # Cena partnerska zostaje na zamówieniu jako STEMPEL, nie jako przelicznik:
        # kwotę i tak ustala admin (całe to zamówienie jest ręczne), a bez śladu
        # w bazie rabat rozpływa się w kwocie i po miesiącu nikt nie policzy, ile
        # kosztowała współpraca. Odmawiamy stempla bez umowy, żeby w raporcie nie
        # pojawiła się zniżka, której nikomu nie obiecano.
        znacznik = None
        if payload.partner_discount:
            if not settings.partner_discount_pct:
                raise HTTPException(400, "No partner discount is configured")
            znacznik = f"PARTNER{int(settings.partner_discount_pct)}"
        o = Order(trader_id=trader.id, product_key=produkt.key, amount_usd=kwota,
                  status="pending", provider="manual", flag=payload.flag or None,
                  credits_used=0.0, coupon=znacznik, bogo=bool(payload.bogo),
                  created_at=datetime.now(timezone.utc).replace(tzinfo=None))
        _zapisz_adres_wplaty(o, payload.payment_address, payload.payment_network)
        session.add(o)
        session.commit()
        wyslany = _mail_oczekujemy_na_platnosc(session, o) if payload.notify_trader else False
        return {"id": o.id, "trader_email": trader.email, "product_key": o.product_key,
                "amount_usd": o.amount_usd, "status": o.status, "flag": o.flag,
                "bogo": bool(o.bogo), "emailed": wyslany,
                # Panel mówi wprost, że przy okazji powstało konto — inaczej
                # nikt nie wie, że lead ma się logować przez „forgot password".
                "trader_created": nowy_trader,
                # Panel ma powiedzieć adminowi, że mail poszedł BEZ adresu portfela,
                # zamiast zostawiać go w przekonaniu, że klient wie, gdzie zapłacić.
                "payment_details": bool(o.payment_address or settings.crypto_wallet)}
    finally:
        session.close()


class BogoPromoIn(BaseModel):
    enabled: bool


@app.get("/api/admin/bogo-promo", dependencies=[Depends(auth.require_admin)])
def admin_bogo_promo_state():
    session = SessionLocal()
    try:
        return {"enabled": billing.bogo_active(session)}
    finally:
        session.close()


@app.post("/api/admin/bogo-promo", dependencies=[Depends(auth.require_admin)])
def admin_bogo_promo_set(payload: BogoPromoIn):
    """Globalny włącznik Buy 1 Get 1 Free — przycisk w ustawieniach panelu.

    Działa od TERAZ w przód: stempluje nowe checkouty (`Order.bogo`), pokazuje
    pasek na stronach publicznych. Zamówień już wystawionych nie rusza w żadną
    stronę — klient z linkiem w ręku zachowuje to, co mu obiecano, a wyłączenie
    promocji niczego mu nie zabiera (patrz komentarz przy `Order.bogo`).
    """
    session = SessionLocal()
    try:
        row = session.get(AppSetting, billing.BOGO_KEY)
        if row is None:
            row = AppSetting(key=billing.BOGO_KEY)
            session.add(row)
        row.value = "1" if payload.enabled else "0"
        session.commit()
        _BOGO_BAR_CACHE["ts"] = 0.0
        return {"enabled": payload.enabled}
    finally:
        session.close()


class OrderBogoIn(BaseModel):
    bogo: bool


@app.post("/api/admin/orders/{order_id}/bogo", dependencies=[Depends(auth.require_admin)])
def admin_order_bogo(order_id: int, payload: OrderBogoIn):
    """Decyzja per zamówienie: czy po opłaceniu dorzucamy drugie konto (BOGO).

    Tylko na nieopłaconych — opłacone przeszło już przez provisioning i ten
    przełącznik niczego by nie zrobił, a w panelu wyglądałby, jakby robił.
    """
    session = SessionLocal()
    try:
        o = session.get(Order, order_id)
        if not o:
            raise HTTPException(404, "Order not found")
        if o.status == "paid":
            raise HTTPException(400, "Order is already paid — grant the second account manually")
        o.bogo = payload.bogo
        session.commit()
        return {"id": o.id, "bogo": bool(o.bogo)}
    finally:
        session.close()


@app.get("/api/admin/partner-terms", dependencies=[Depends(auth.require_admin)])
def admin_partner_terms():
    """Procent rabatu z umowy partnerskiej — jedyna rzecz, której panel nie wie.

    Kwotę liczy sobie potem sam, żeby admin zobaczył cenę ZANIM kliknie „utwórz";
    cena, która zmienia się po fakcie, to przy pieniądzach zła niespodzianka.
    Zero znaczy „nie ma umowy" i okno zamówienia nie proponuje wtedy tej opcji.
    """
    return {"discount_pct": settings.partner_discount_pct}


@app.post("/api/admin/orders/{order_id}/pay-link", dependencies=[Depends(auth.require_admin)])
def admin_order_pay_link(order_id: int, request: Request):
    """Link do zapłaty kartą za konkretne zamówienie — do wysłania na Telegramie.

    Prowadzi na NASZĄ stronę (/pay/<token>), nie na goły adres Stripe'a: sesja
    kasy powstaje dopiero po kliknięciu, więc link nie wygasa po 24 h, klient
    widzi markę i kwotę przed zapłatą, a `stripe_session_id` przy zamówieniu
    dalej pilnuje webhooka. Token jest stały — ten sam przycisk daje ten sam
    adres, więc wysłanie go drugi raz niczego nie unieważnia.

    Gdy ustawione jest `PARTNER_PAY_BASE_URL`, wraca DODATKOWO ten sam token na
    domenie partnera — panel każe wtedy wybrać, którą stroną klient ma iść.
    Wyboru nie robimy za admina: to on wie, skąd ten człowiek przyszedł, a
    wysłanie klientowi partnera linku z naszą marką jest właśnie tym błędem,
    przed którym ten wybór ma chronić.
    """
    session = SessionLocal()
    try:
        o = session.get(Order, order_id)
        if not o:
            raise HTTPException(404, "Order not found")
        if o.status == "paid":
            raise HTTPException(400, "This order is already paid")
        if not o.pay_token:
            o.pay_token = secrets.token_urlsafe(16)[:32]
            session.commit()
        odp = {"id": o.id, "url": f"{_public_base(request)}/pay/{o.pay_token}"}
        if baza := settings.partner_pay_base_url:
            odp["partner_url"] = f"{baza}/pay/{o.pay_token}"
        return odp
    finally:
        session.close()


class OrderFlagIn(BaseModel):
    flag: str = ""
    payment_address: str | None = None
    payment_network: str | None = None


@app.post("/api/admin/orders/{order_id}/flag", dependencies=[Depends(auth.require_admin)])
def admin_flag_order(order_id: int, payload: OrderFlagIn):
    """Ręczna flaga płatności — np. „czekam na przelew crypto"."""
    if payload.flag not in ("", "awaiting_crypto"):
        raise HTTPException(400, "Unknown flag")
    session = SessionLocal()
    try:
        o = session.get(Order, order_id)
        if not o:
            raise HTTPException(404, "Order not found")
        poprzednia = o.flag
        o.flag = payload.flag or None
        _zapisz_adres_wplaty(o, payload.payment_address, payload.payment_network)
        session.commit()
        # Instrukcja wpłaty leci przy WEJŚCIU we flagę, nie przy każdym zapisie:
        # drugie kliknięcie tego samego przycisku nie ma wysyłać klientowi
        # drugiego maila o tej samej należności. Opłaconego zamówienia to nie
        # dotyczy — tam nie ma na co czekać.
        mail = bool(o.flag == "awaiting_crypto" and poprzednia != "awaiting_crypto"
                    and o.status == "pending"
                    and _mail_oczekujemy_na_platnosc(session, o))
        return {"id": o.id, "flag": o.flag, "emailed": mail,
                "payment_details": bool(o.payment_address or settings.crypto_wallet)}
    finally:
        session.close()


@app.post("/api/admin/orders/{order_id}/mark-paid", dependencies=[Depends(auth.require_admin)])
async def admin_mark_order_paid(order_id: int):
    """Ręczne domknięcie płatności (crypto/przelew poza Stripe).

    Ta sama ścieżka co webhook Stripe i mock — provisioning tworzy konto,
    ustawia status/paid_at, zdejmuje kredyty i wysyła traderowi poświadczenia."""
    session = SessionLocal()
    try:
        o = session.get(Order, order_id)
        if not o:
            raise HTTPException(404, "Order not found")
        if o.status == "paid":
            return {"already": True, "account_id": o.account_id}
        acc = provisioning.create_account_from_order(session, o, notify_admin=False)
        # Provisioning mógł właśnie oflagować zamówienie (BOGO grant nie wyszedł,
        # kredyty bez pokrycia) — tych flag nie wolno tu zetrzeć, to jedyny
        # trwały ślad dla admina.
        grant_failed = o.flag == "bogo_grant_failed"
        if o.flag not in ("bogo_grant_failed", "credits_shortfall"):
            o.flag = None
        o.fail_reason = None      # recovery: płatność jednak doszła
        session.commit()
        wynik = {"paid": o.id, "account_id": acc.id,
                 "bogo": bool(getattr(o, "bogo", False)),
                 "bogo_grant_ok": bool(getattr(o, "bogo", False)) and not grant_failed}
    finally:
        session.close()
    # Przy realnym MT5 konto wychodzi stąd w 'provisioning', a mail z poświadczeniami
    # dopiero przy przydziale rachunku — bez tego kopnięcia klient czekałby na dzienny
    # cron i admin słusznie zgłaszał „oznaczyłem paid i nie przyszedł mail".
    try:
        await poller.provision_kickoff()
    except Exception as e:  # najbliższy tick i tak dokończy
        print(f"[mark-paid] natychmiastowy provisioning nie wyszedł: {e}")
    return wynik


class OrderFailIn(BaseModel):
    reason: str | None = None


@app.post("/api/admin/orders/{order_id}/mark-failed", dependencies=[Depends(auth.require_admin)])
def admin_mark_order_failed(order_id: int, payload: OrderFailIn):
    """Ręczne ubicie nieopłaconego zamówienia (przelew nie doszedł, duplikat…).

    Zapłaconych nie ruszamy — mają już konto; odwrót w drugą stronę robi
    Mark paid, które czyści powód. Powód zostaje w panelu przy statusie,
    trader nie dostaje maila."""
    session = SessionLocal()
    try:
        o = session.get(Order, order_id)
        if not o:
            raise HTTPException(404, "Order not found")
        if o.status == "paid":
            raise HTTPException(400, "Paid orders cannot be marked as failed")
        o.status = "failed"
        o.fail_reason = (payload.reason or "").strip()[:200] or None
        o.flag = None
        session.commit()
        return {"id": o.id, "status": o.status, "fail_reason": o.fail_reason}
    finally:
        session.close()


@app.get("/api/admin/inbox", dependencies=[Depends(auth.require_admin)])
def admin_inbox():
    """Dzwonek w panelu: ostatnie „coś przyszło" ze wszystkich kolejek.

    Agregacja z istniejących tabel (bez osobnej tabeli powiadomień admina);
    co jest „nieprzeczytane" rozstrzyga frontend po localStorage."""
    session = SessionLocal()
    try:
        zamowienia = session.query(Order).order_by(Order.id.desc()).limit(15).all()
        kyc = (session.query(Trader).filter(Trader.kyc_status == "pending")
               .order_by(Trader.kyc_submitted_at.desc().nullslast()).limit(10).all())
        wnioski = (session.query(PayoutRequest).filter(PayoutRequest.status == "pending")
                   .order_by(PayoutRequest.id.desc()).limit(10).all())
        bilety = (session.query(TicketMessage, SupportTicket)
                  .join(SupportTicket, SupportTicket.id == TicketMessage.ticket_id)
                  .filter(TicketMessage.author == "trader")
                  .order_by(TicketMessage.id.desc()).limit(10).all())
        # Dzwonek odpytuje panel cyklicznie, wiec maile bierzemy jednym zapytaniem
        # po wszystkich kolejkach naraz zamiast dokladac round-trip na wiersz.
        maile = _maile_traderow(session, [o.trader_id for o in zamowienia]
                                + [r.trader_id for r in wnioski]
                                + [t.trader_id for _, t in bilety])

        def email_of(tid: int) -> str:
            return maile.get(tid) or "?"

        items = []
        for o in zamowienia:
            items.append({"type": "order", "ts": (o.paid_at or o.created_at).isoformat(),
                          "title": f"Order #{o.id} · {o.product_key} · {o.status}",
                          "body": email_of(o.trader_id), "view": "orders"})
        for t in kyc:
            if t.kyc_submitted_at:
                items.append({"type": "kyc", "ts": t.kyc_submitted_at.isoformat(),
                              "title": f"KYC pending · {t.kyc_fullname or t.email}",
                              "body": t.email, "view": "kyc"})
        for pr in wnioski:
            items.append({"type": "payout", "ts": pr.ts.isoformat(),
                          "title": f"Payout request ${pr.trader_share:,.2f}",
                          "body": email_of(pr.trader_id), "view": "payouts"})
        for m, t in bilety:
            items.append({"type": "ticket", "ts": m.ts.isoformat(),
                          "title": f"Ticket #{t.id}: {t.subject}",
                          "body": email_of(t.trader_id), "view": "tickets"})
        # Leady tą samą listą co reszta kolejek: historia zdarzeń już istnieje
        # (lead_events), więc dzwonek tylko ją czyta. `lead_id` pozwala frontowi
        # otworzyć od razu kartę leada zamiast gołej zakładki.
        zdarzenia_leadow = (session.query(LeadEvent, Lead)
                            .join(Lead, Lead.id == LeadEvent.lead_id)
                            .order_by(LeadEvent.id.desc()).limit(15).all())
        for z, l in zdarzenia_leadow:
            kto_lead = l.name or l.email
            tytul = {
                "applied": f"New lead: {kto_lead}",
                "claim": f"Lead {kto_lead}",
                "status": f"Lead {kto_lead}",
                "tier": f"Lead {kto_lead}",
                "bought": f"Lead {kto_lead}",
                "reminder": f"Follow-up: {kto_lead}",
            }.get(z.kind, f"Lead {kto_lead}")
            items.append({"type": "lead", "ts": z.created_at.isoformat(),
                          "title": tytul,
                          "body": (z.detail or z.kind)[:120], "view": "leads",
                          "lead_id": l.id})
        items.sort(key=lambda i: i["ts"], reverse=True)
        return {"items": items[:30]}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  PULA kont MT5 (pre-provisioning) — zasilana przez admina                   #
# --------------------------------------------------------------------------- #
class NewPoolAccount(BaseModel):
    """Wpis do puli — dokładnie to, co admin ma pod ręką z panelu brokera."""
    platform_login: str
    platform_password: str
    platform_server: str
    account_size: float


@app.get("/api/admin/pool", dependencies=[Depends(auth.require_admin)])
def admin_pool_list():
    """Stan puli + zamówienia, które na nią czekają.

    Do każdego przydzielonego rachunku dokładamy tradera i datę — panel ma
    odpowiadać na pytanie „czyj jest ten rachunek MT5" bez grzebania w bazie.
    `waiting` to konta w statusie `provisioning`, czyli opłacone zamówienia, dla
    których w puli zabrakło rachunku danego rozmiaru.
    """
    session = SessionLocal()
    try:
        rows = session.query(PoolAccount).order_by(PoolAccount.id.desc()).all()
        traderzy = {t.id: t for t in session.query(Trader).all()}
        konta = {a.id: a for a in session.query(Account).all()}
        pula = []
        for p in rows:
            tr = traderzy.get(p.claimed_by_trader_id)
            acc = konta.get(p.claimed_by_account_id)
            pula.append({
                "id": p.id, "platform_login": p.platform_login,
                # Hasło jest potrzebne w panelu: wygenerowanych (simulated) nikt
                # nigdy nie widział, a to admin przekazuje je dalej. Panel i tak
                # jest za logowaniem administratora.
                "platform_password": p.platform_password,
                "platform_server": p.platform_server, "account_size": p.account_size,
                "claimed": p.claimed, "claimed_by_account_id": p.claimed_by_account_id,
                "claimed_at": p.claimed_at.isoformat() if p.claimed_at else None,
                "retired_reason": p.retired_reason, "simulated": bool(p.simulated),
                "trader_email": tr.email if tr else None,
                "trader_name": (tr.full_name if tr else None),
                "account_status": acc.status if acc else None,
                "account_phase": acc.phase if acc else None,
            })

        czekajace = []
        for a in (session.query(Account).filter(Account.status == "provisioning")
                  .order_by(Account.id).all()):
            tr = traderzy.get(a.trader_id)
            czekajace.append({
                "account_id": a.id, "account_size": a.initial_balance,
                "product_key": a.product_key,
                "trader_email": tr.email if tr else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            })
        mozna, powod = _generator_status()
        # Rozmiary bierzemy z katalogu, a nie z listy w kodzie panelu — inaczej po
        # dodaniu nowego planu admin mogłby wrzucić do puli rachunek wielkości,
        # której nikt nie kupi, i taki wpis nigdy by się nie przydzielił.
        rozmiary = sorted({p.account_size for p in
                           session.query(Product).filter(Product.active == True).all()})  # noqa: E712
        return {"pool": pula, "waiting": czekajace, "sizes": rozmiary,
                "can_generate": mozna, "generate_hint": powod,
                "sim_fallback": provisioning.sim_fallback_enabled(session),
                "real_fallback": provisioning.real_fallback_enabled(session)}
    finally:
        session.close()


class SimGenerateIn(BaseModel):
    """Ile SYMULOWANYCH wpisów dodać do puli i jakiego rozmiaru."""
    account_size: float
    count: int = 1


@app.post("/api/admin/pool/generate-simulated", dependencies=[Depends(auth.require_admin)])
async def admin_pool_generate_simulated(payload: SimGenerateIn):
    """Dodaje do puli wpisy z wygenerowanymi u nas poświadczeniami w formacie
    MetaQuotes-Demo. Za takim wpisem nie stoi żaden serwer — konto, które go
    dostanie, ma mt5_backed=False i ruch generuje mu Trade BOT, nie realny feed."""
    if payload.account_size <= 0:
        raise HTTPException(400, "Enter an account size")
    if not 1 <= payload.count <= 50:
        raise HTTPException(400, "Count must be between 1 and 50")
    session = SessionLocal()
    try:
        created = []
        for _ in range(payload.count):
            p = PoolAccount(platform_login=provisioning._gen_login(),
                            platform_password=provisioning._gen_password(),
                            platform_server=provisioning.PLATFORM_SERVER,
                            account_size=payload.account_size, simulated=True)
            session.add(p)
            session.flush()
            created.append({"id": p.id, "platform_login": p.platform_login})
        session.commit()
    finally:
        session.close()
    # Jak przy ręcznym dosypaniu puli: czekające konta mają dostać poświadczenia
    # od razu, nie przy najbliższym cronie.
    try:
        await poller.provision_kickoff()
    except Exception as e:  # najbliższy tick i tak dokończy
        print(f"[pool] natychmiastowy provisioning nie wyszedł: {e}")
    return {"created": created}


class PayoutEngineIn(BaseModel):
    """Ustawienia Payout BOT-a. Wszystkie pola opcjonalne — panel zapisuje kartę
    w całości, ale przełącznik on/off woła ten sam endpoint z samym `enabled`."""
    enabled: bool | None = None
    win_from: int | None = None      # okno publikacji w godzinach US Eastern
    win_to: int | None = None
    lp_pct: float | None = None
    sizes: list[float] | None = None
    gross_min_pct: float | None = None
    gross_max_pct: float | None = None


@app.get("/api/admin/payout-engine", dependencies=[Depends(auth.require_admin)])
def admin_payout_engine():
    session = SessionLocal()
    try:
        cfg = payoutbot.ustawienia(session)
        czy, powod = payoutbot.nalezy_odpalic(session)
        wynik_row = session.get(AppSetting, payoutbot.KLUCZ_WYNIK)
        return {**cfg, "due": czy, "blocked_by": powod,
                # Wylosowana na dziś minuta publikacji — admin ma widzieć, na
                # którą godzinę silnik jest „uzbrojony", zamiast zgadywać.
                "today_slot_et": payoutbot.slot_dnia(cfg).strftime("%H:%M"),
                # Panel ma pokazać wprost, czego brakuje do publikacji — inaczej
                # admin włącza silnik i przez dobę nie wie, czemu kanał milczy.
                "telegram_ready": telegram.is_enabled(),
                "renderer_ready": certshot.is_enabled(),
                # Wynik OSTATNIEGO posta: "last run" bez tego mówi tylko, że
                # wypłata powstała — cicha porażka Telegrama wyglądała jak
                # „bot nie zadziałał" (kanał milczy, data się zgadza).
                "last_result": wynik_row.value if wynik_row else None}
    finally:
        session.close()


@app.post("/api/admin/payout-engine", dependencies=[Depends(auth.require_admin)])
def admin_payout_engine_save(payload: PayoutEngineIn):
    session = SessionLocal()
    try:
        try:
            return payoutbot.zapisz_ustawienia(session, **payload.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        session.close()


@app.post("/api/admin/payout-engine/run", dependencies=[Depends(auth.require_admin)])
def admin_payout_engine_run(request: Request):
    """Odpala silnik NATYCHMIAST, z pominięciem guardu godziny i dnia.

    Bez tego sprawdzenie konfiguracji (token bota, usługa zrzutu, wygląd posta)
    trwałoby dobę. Guard dnia i tak zostaje przestawiony, więc ręczny przebieg
    zastępuje dzisiejszy automatyczny, a nie dokłada się do niego.
    """
    session = SessionLocal()
    try:
        wynik = payoutbot.uruchom(session, force=True, base_url=_public_base(request))
        # Świeży certyfikat ma się pojawić na pasie od razu, a nie po minucie.
        _PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
        return wynik
    finally:
        session.close()


class ReachIn(BaseModel):
    enabled: bool | None = None
    svc_reactions: int | None = None
    qty_reactions: int | None = None
    svc_views: int | None = None
    qty_views: int | None = None
    min_balance: float | None = None


class ReachBoostIn(BaseModel):
    link: str
    # Ilości tylko na TEN jeden strzał; puste = jak dla kanału z linku.
    qty_reactions: int | None = None
    qty_views: int | None = None


class ReachChannelIn(BaseModel):
    username: str
    label: str | None = None
    on: bool = True
    # None = „jak globalnie". Zero jest osobną, legalną wartością.
    qty_reactions: int | None = None
    qty_views: int | None = None


class ReachChannelsIn(BaseModel):
    channels: list[ReachChannelIn]


@app.get("/api/admin/reach", dependencies=[Depends(auth.require_admin)])
def admin_reach():
    """Konfiguracja Reach BOT-a plus saldo u dostawcy.

    Saldo jest odpytywane na żywo, bo to jedyna liczba, której admin nie ma
    skąd wziąć — a od niej zależy, czy jutrzejszy post w ogóle dostanie zasięg.
    """
    session = SessionLocal()
    try:
        cfg = reach.ustawienia(session)
        # Cennik dostawcy ma ~1,5 MB, więc normalnie odświeża go dobowy tick.
        # Tutaj ciągniemy go RAZ, dopóki stawek nie ma — inaczej panel do
        # pierwszego ticku pokazywałby zgadywany koszt posta.
        # Nazwa usługi jest tu równie ważna co stawka: po niej widać, czy
        # zamawiamy wariant "Positive", więc brak nazwy też wymusza pobranie.
        if reach.is_enabled() and (cfg.get("cost_from") != "provider"
                                   or not cfg.get("name_reactions")):
            try:
                reach.odswiez_cennik(session)
                cfg = reach.ustawienia(session)
            except Exception as e:  # pragma: no cover - sieć
                print(f"[reach] cennik nie odświeżony: {e}")
        stan = reach.saldo_z_ustawien(session) if reach.is_enabled() else {"error": "not configured"}
        # Status per kanał: bez uprawnień admina Telegram nie przysyła postów,
        # więc automat po cichu nic nie robi — panel ma to powiedzieć wprost.
        lista = []
        for k in reach.kanaly(session):
            lista.append({**k, "bot_admin": telegram.jest_adminem("@" + k["username"])})
        return {**cfg, "provider_ready": reach.is_enabled(), "balance": stan,
                "channels": lista, "bot_username": telegram.bot_username()}
    finally:
        session.close()


@app.post("/api/admin/reach", dependencies=[Depends(auth.require_admin)])
def admin_reach_save(payload: ReachIn):
    session = SessionLocal()
    try:
        try:
            return reach.zapisz_ustawienia(session, **payload.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        session.close()


@app.post("/api/admin/reach/channels", dependencies=[Depends(auth.require_admin)])
def admin_reach_channels(payload: ReachChannelsIn):
    """Które kanały Reach BOT obsługuje. Kanał wypłat wraca na listę sam."""
    session = SessionLocal()
    try:
        try:
            lista = reach.zapisz_kanaly(session, [k.model_dump() for k in payload.channels])
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"channels": [{**k, "bot_admin": telegram.jest_adminem("@" + k["username"])}
                             for k in lista]}
    finally:
        session.close()


@app.post("/api/admin/reach/boost", dependencies=[Depends(auth.require_admin)])
def admin_reach_boost(payload: ReachBoostIn):
    """Ręczne zamówienie pod wklejonym linkiem — dla postów spoza Payout BOT-a."""
    session = SessionLocal()
    try:
        try:
            wynik = reach.zamow(session, payload.link.strip(), powod="panel",
                                wymagaj_wlaczenia=False,
                                qty_reactions=payload.qty_reactions,
                                qty_views=payload.qty_views)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if wynik.get("skipped"):
            raise HTTPException(400, wynik["skipped"])
        return wynik
    finally:
        session.close()


class SimFallbackIn(BaseModel):
    enabled: bool


@app.post("/api/admin/pool/sim-fallback", dependencies=[Depends(auth.require_admin)])
def admin_pool_sim_fallback(payload: SimFallbackIn):
    """Przełącznik: gdy pula nie ma wolnego rachunku, provisioning sam generuje
    symulowane poświadczenia zamiast trzymać opłacone konto w kolejce."""
    session = SessionLocal()
    try:
        row = session.get(AppSetting, provisioning.SIM_FALLBACK_KEY)
        if row is None:
            row = AppSetting(key=provisioning.SIM_FALLBACK_KEY)
            session.add(row)
        row.value = "1" if payload.enabled else "0"
        session.commit()
        return {"sim_fallback": payload.enabled}
    finally:
        session.close()


class RealFallbackIn(BaseModel):
    enabled: bool


@app.post("/api/admin/pool/real-fallback", dependencies=[Depends(auth.require_admin)])
def admin_pool_real_fallback(payload: RealFallbackIn):
    """Przełącznik: gdy pula nie ma wolnego rachunku, provisioning zakłada realne
    demo MT5 przez web.metatrader.app (przed ewentualnym fallbackiem symulowanym)."""
    session = SessionLocal()
    try:
        row = session.get(AppSetting, provisioning.REAL_FALLBACK_KEY)
        if row is None:
            row = AppSetting(key=provisioning.REAL_FALLBACK_KEY)
            session.add(row)
        row.value = "1" if payload.enabled else "0"
        session.commit()
        return {"real_fallback": payload.enabled}
    finally:
        session.close()


@app.post("/api/admin/pool", dependencies=[Depends(auth.require_admin)])
async def admin_pool_add(payload: NewPoolAccount):
    session = SessionLocal()
    try:
        p = PoolAccount(
            platform_login=payload.platform_login.strip(),
            platform_password=payload.platform_password.strip(),
            platform_server=payload.platform_server.strip(),
            account_size=payload.account_size,
        )
        session.add(p)
        session.commit()
        wynik = {"id": p.id, "account_size": p.account_size}
    finally:
        session.close()
    # Admin dosypuje pulę zwykle DLA konta, które już czeka — rachunek i mail
    # z poświadczeniami mają pójść teraz, nie przy najbliższym cronie.
    try:
        await poller.provision_kickoff()
    except Exception as e:  # najbliższy tick i tak dokończy
        print(f"[pool] natychmiastowy provisioning nie wyszedł: {e}")
    return wynik


def _generator_status() -> tuple[bool, str]:
    """Czy da się TU zakładać konta demo na MetaQuotes-Demo, a jeśli nie — dlaczego.

    Kanał steruje Playwrightem na `web.metatrader.app`, więc potrzebuje procesu
    z przeglądarką. Na hostingu bezserwerowym (Vercel) Chromium (~150 MB) nie ma
    prawa się znaleźć, dlatego panel musi umieć powiedzieć to wprost, zamiast
    dawać przycisk, który zawsze kończy się błędem.
    """
    # Komunikaty po angielsku — trafiają wprost do panelu admina, który jest EN.
    if not settings.metaquotes_web_enabled:
        return False, ("The MetaQuotes channel is off. Set METAQUOTES_WEB_ENABLED=true "
                       "on a host that has a browser.")
    if not metaquotes_web.chromium_available():
        return False, ("No browser available here. Serverless hosting cannot run Chromium, so "
                       "either point BROWSER_CDP_URL at a hosted browser (Browserless, "
                       "Browserbase, or your own Chrome started with --remote-debugging-port), "
                       "or generate accounts from your own machine: "
                       "python scripts/pool_generate.py --size 50000 --count 5 "
                       "--api <panel-url> --admin-token <token>")
    return True, ""


class PoolGenerateIn(BaseModel):
    """Ile kont demo MT5 zalozyc i jakiego rozmiaru."""
    account_size: float
    count: int = 1
    first_name: str = "Pro"
    last_name: str = "Trader"


@app.post("/api/admin/pool/generate", dependencies=[Depends(auth.require_admin)])
async def admin_pool_generate(payload: PoolGenerateIn):
    """Zakłada konta demo na MetaQuotes-Demo i wrzuca je do puli.

    Ta sama ścieżka, którą wcześniej szedł provisioning: formularz „Open Demo
    account" na web.metatrader.app sterowany Playwrightem. Różnica polega na tym,
    że konta lądują w puli ZAWCZASU, zamiast powstawać w chwili zakupu — trader
    nie czeka, aż przeglądarka przeklika formularz.
    """
    mozna, powod = _generator_status()
    if not mozna:
        raise HTTPException(400, powod)
    if payload.account_size <= 0:
        raise HTTPException(400, "Account size must be greater than zero")
    if not 1 <= payload.count <= 10:
        raise HTTPException(400, "You can open between 1 and 10 accounts at a time")

    opener = metaquotes_web.make_opener(settings)
    if opener is None:
        raise HTTPException(400, "The MetaQuotes channel is off")

    utworzone, bledy = [], []
    for _ in range(payload.count):
        spec = metaquotes_web.WebDemoSpec(
            first_name=payload.first_name, last_name=payload.last_name,
            email=settings.mail_from, phone=settings.metaquotes_web_default_phone,
            deposit=payload.account_size, leverage=settings.metaquotes_web_leverage,
            account_type=settings.metaquotes_web_account_type,
        )
        try:
            creds = await opener.open_demo_account(spec)
        except Exception as e:                      # kanał bywa kapryśny — resztę i tak zapisujemy
            bledy.append(str(e)[:200])
            continue
        session = SessionLocal()
        try:
            p = PoolAccount(platform_login=str(creds.login), platform_password=creds.password,
                            platform_server=creds.server, account_size=payload.account_size)
            session.add(p)
            session.commit()
            utworzone.append({"id": p.id, "platform_login": p.platform_login,
                              "platform_server": p.platform_server})
        finally:
            session.close()

    if not utworzone:
        raise HTTPException(502, "Could not open any account: " + "; ".join(bledy))
    return {"created": utworzone, "errors": bledy}


@app.post("/api/admin/accounts/{account_id}/provision-real",
          dependencies=[Depends(auth.require_admin)])
async def admin_account_provision_real(account_id: int):
    """Zakłada realne demo MT5 (web.metatrader.app) NA DANE tradera i przypina
    poświadczenia do konkretnego konta — omija pulę. Do dogenerowania rachunku
    dla czekającego zamówienia albo wymiany placeholderów."""
    mozna, powod = _generator_status()
    if not mozna:
        raise HTTPException(400, powod)

    opener = metaquotes_web.make_opener(settings)
    if opener is None:
        raise HTTPException(400, "The MetaQuotes channel is off")

    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        trader = session.get(Trader, acc.trader_id) if acc.trader_id else None
        if not trader:
            raise HTTPException(400, "Account has no trader — cannot open a demo in their name")

        spec = metaquotes_web.WebDemoSpec.from_trader(trader, acc, settings)
        try:
            creds = await opener.open_demo_account(spec)
        except Exception as e:
            raise HTTPException(502, f"Could not open demo account: {str(e)[:200]}") from e

        provisioning._apply_credentials(acc, {
            "login": creds.login,
            "password": creds.password,
            "server": creds.server,
        })
        if acc.status == "provisioning":
            acc.status = "funded" if acc.phase == "funded" else "active"
        session.commit()
        notify.send(provisioning._creds_event(acc), trader.email,
                    provisioning._creds_ctx(trader, acc))
        return {
            "account_id": acc.id,
            "platform_login": acc.platform_login,
            "platform_server": acc.platform_server,
            "status": acc.status,
            "mt5_backed": bool(acc.mt5_backed),
        }
    finally:
        session.close()


class PoolPatchIn(BaseModel):
    """Korekta wpisu w puli — wszystkie pola opcjonalne."""
    platform_login: str | None = None
    platform_password: str | None = None
    platform_server: str | None = None
    account_size: float | None = None


@app.patch("/api/admin/pool/{pool_id}", dependencies=[Depends(auth.require_admin)])
def admin_pool_edit(pool_id: int, payload: PoolPatchIn):
    """Poprawia dane rachunku w puli.

    Gdy rachunek jest już przydzielony, zmiana poświadczeń przechodzi TAKŻE na
    konto tradera — inaczej po zmianie hasła u brokera portal pokazywałby stare
    dane i trader nie zalogowałby się do terminala. Rozmiaru przydzielonego
    rachunku nie ruszamy: konto challenge ma już od niego policzone limity.
    """
    session = SessionLocal()
    try:
        p = session.get(PoolAccount, pool_id)
        if not p:
            raise HTTPException(404, "No such entry in the pool")
        if payload.account_size is not None:
            if p.claimed:
                raise HTTPException(400, "This account is assigned, so its size cannot be changed")
            if payload.account_size <= 0:
                raise HTTPException(400, "Account size must be greater than zero")
            p.account_size = payload.account_size
        for pole in ("platform_login", "platform_password", "platform_server"):
            wartosc = getattr(payload, pole)
            if wartosc is not None:
                nowa = wartosc.strip()
                if not nowa:
                    raise HTTPException(400, f"Field {pole} cannot be empty")
                setattr(p, pole, nowa)

        acc = session.get(Account, p.claimed_by_account_id) if p.claimed_by_account_id else None
        if acc is not None:
            acc.platform_login = p.platform_login
            acc.platform_password = p.platform_password
            acc.platform_server = p.platform_server
            acc.login = p.platform_login
        session.commit()
        return {"id": p.id, "platform_login": p.platform_login,
                "platform_server": p.platform_server, "account_size": p.account_size,
                "propagated_to_account": acc.id if acc else None}
    finally:
        session.close()


@app.delete("/api/admin/pool/{pool_id}", dependencies=[Depends(auth.require_admin)])
def admin_pool_delete(pool_id: int):
    """Usuwa wpis WOLNY albo WYCOFANY. Blokujemy tylko rachunek w żywym użyciu.

    Blokada obejmowała wcześniej wszystko, co `claimed` — a to zamykało panel na
    wpisy wycofane (`retired_reason`), czyli takie, których konto tradera już nie
    istnieje. Zostawały w tabeli na zawsze, bez żadnego przycisku.

    Wycofany wpis nie trzyma niczego przy życiu: na `pool_accounts.id` nie wskazuje
    ŻADEN klucz obcy, a powiązanie idzie w drugą stronę (`claimed_by_account_id`).
    Traci się jedną rzecz i panel mówi o tym wprost w pytaniu: ślad, że ten login
    u brokera był już komuś wydany, więc nie wolno go wpisać do puli ponownie.
    """
    session = SessionLocal()
    try:
        p = session.get(PoolAccount, pool_id)
        if not p:
            raise HTTPException(404, "No such entry in the pool")
        if p.claimed and not p.retired_reason:
            raise HTTPException(400, "This account is assigned to a trader and cannot be removed")
        session.delete(p)
        session.commit()
        return {"deleted": pool_id, "was_retired": bool(p.retired_reason)}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  Publiczne: konta (admin read), leaderboard, certyfikat                      #
# --------------------------------------------------------------------------- #
@app.get("/api/accounts", dependencies=[Depends(auth.require_admin)])
def list_accounts(imported: int = 0):
    session = SessionLocal()
    try:
        # Data płatności do kolumny "Paid" — jedno zapytanie zamiast N per konto.
        zaplacone = dict(session.query(Order.account_id, func.max(Order.paid_at))
                         .filter(Order.account_id.isnot(None), Order.paid_at.isnot(None))
                         .group_by(Order.account_id).all())
        emaile = dict(session.query(Trader.id, Trader.email).all())
        out = []
        # Najnowsze konto na górze: panel czyta tę listę jak oś czasu,
        # a świeżo otwarte konto to zwykle to, po które admin przyszedł.
        konta = session.query(Account)
        if not imported:
            konta = konta.filter(_konto_nie_import(session))
        for a in konta.order_by(Account.created_at.desc(), Account.id.desc()).all():
            d = _account_dict(a, with_credentials=True, admin_view=True)
            p = zaplacone.get(a.id)
            d["paid_at"] = p.isoformat() if p else None
            d["trader_email"] = emaile.get(a.trader_id)
            out.append(d)
        return out
    finally:
        session.close()


@app.get("/api/accounts/{account_id}/history", dependencies=[Depends(auth.require_admin)])
def account_history(account_id: int):
    """Timeline konta składany z istniejących wierszy (zamówienie, płatność,
    start, breach, payouty) — bez osobnej tabeli event-logu."""
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        items: list[dict] = []

        def add(ts, label, kind):
            if ts:
                items.append({"ts": ts.isoformat(), "label": label, "kind": kind})

        add(acc.created_at, "Account created", "account")
        if acc.started_at and acc.started_at != acc.created_at:
            add(acc.started_at, "Trading started", "account")
        for o in session.query(Order).filter(Order.account_id == acc.id).all():
            add(o.created_at,
                f"Order #{o.id} created: {o.product_key}, ${o.amount_usd:,.2f} ({o.status})",
                "order")
            add(o.paid_at, f"Order #{o.id} paid: ${o.amount_usd:,.2f} via {o.provider}",
                "payment")
        for b in session.query(Breach).filter(Breach.account_id == acc.id).all():
            add(b.ts, f"Breach: {b.type} — {b.detail}", "breach")
        for pr in (session.query(PayoutRequest)
                   .filter(PayoutRequest.account_id == acc.id).all()):
            add(pr.ts, f"Payout request ${pr.trader_share:,.2f} ({pr.status})", "payout")
        for p in session.query(Payout).filter(Payout.account_id == acc.id).all():
            add(p.ts, f"Payout ${p.trader_share:,.2f}" + (" — paid" if p.paid else ""),
                "payout")
        if acc.closed_at:
            add(acc.closed_at, f"Account closed ({acc.status})", "account")
        items.sort(key=lambda i: i["ts"], reverse=True)
        return {"items": items}
    finally:
        session.close()


@app.get("/api/accounts/{account_id}", dependencies=[Depends(auth.require_admin)])
def get_account(account_id: int):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")
        _ensure_cert_token(session, acc)
        return _account_detail(session, acc, admin_view=True)
    finally:
        session.close()


class NewAccount(BaseModel):
    """Challenge zakładany w całości ręcznie — z rachunku spoza puli.

    Poświadczenia MT5 wpisuje admin, więc nic nie jest brane z puli i nie rusza
    to jej stanu. Typ drawdownu bierze się z wybranego planu, a nie z osobnego
    pola: to plan go definiuje i dwa źródła prawdy tylko by się rozjeżdżały.
    """
    login: str
    platform_password: str | None = None
    platform_server: str | None = None
    trader_name: str = ""
    trader_email: str | None = None
    product_key: str = "2step-100k"
    note: str | None = None            # promocja / powód, jak przy Grant
    bogo_paid_key: str | None = None   # tier, za który klient faktycznie zapłacił


@app.post("/api/accounts", dependencies=[Depends(auth.require_admin)])
def create_account(payload: NewAccount):
    session = SessionLocal()
    try:
        prod = session.query(Product).filter(Product.key == payload.product_key).first()
        if not prod:
            raise HTTPException(404, "Product not found")

        # E-mail wiąże konto z zarejestrowanym traderem, żeby zobaczył je w portalu.
        # Gdy takiego konta nie ma, challenge i tak powstaje — tylko bez właściciela,
        # a panel to zgłasza, zamiast po cichu tworzyć osierocone konto.
        trader = None
        email = (payload.trader_email or "").strip().lower()
        if email:
            trader = session.query(Trader).filter(Trader.email == email).first()

        oplacony = None
        if payload.bogo_paid_key:
            p2 = session.query(Product).filter(Product.key == payload.bogo_paid_key).first()
            oplacony = p2.account_size if p2 else None

        bal = prod.account_size
        now = datetime.now(timezone.utc)
        acc = Account(
            login=payload.login, trader_name=(payload.trader_name or (trader.full_name if trader else "")),
            trader_id=(trader.id if trader else None),
            platform_login=payload.login,
            platform_password=(payload.platform_password or None),
            platform_server=(payload.platform_server or None),
            # Poświadczenia są prawdziwe (admin wziął je z terminala), więc feed
            # może się takim kontem zajmować — inaczej niż przy generowanych lokalnie.
            mt5_backed=bool(payload.platform_password and payload.platform_server),
            product_key=prod.key, preset=prod.key,
            initial_balance=bal, steps=prod.steps, profit_target_p1=prod.profit_target_p1,
            profit_target_p2=prod.profit_target_p2, max_daily_loss_pct=prod.max_daily_loss_pct,
            max_overall_loss_pct=prod.max_overall_loss_pct, min_trading_days=prod.min_trading_days,
            drawdown_type=prod.drawdown_type, profit_split_pct=prod.profit_split_pct,
            max_lots=getattr(prod, "max_lots", 0.0) or 0.0,
            source=("grant" if payload.note else "manual"),
            grant_note=(payload.note or None), bogo_paid_size=oplacony,
            phase=("funded" if prod.steps == 0 else "eval_1"),
            status=("funded" if prod.steps == 0 else "active"),
            balance=bal, equity=bal, peak_equity=bal, day_start_equity=bal, day_start_balance=bal,
            day_key=now.strftime("%Y-%m-%d"), created_at=now, started_at=now,
        )
        session.add(acc)
        session.commit()

        if trader and acc.platform_password:
            notify.send(provisioning._creds_event(acc), trader.email,
                        provisioning._creds_ctx(trader, acc))

        d = _account_dict(acc, with_credentials=True, admin_view=True)
        d["linked_trader"] = trader.email if trader else None
        d["email_unknown"] = bool(email and trader is None)
        return d
    finally:
        session.close()


@app.delete("/api/accounts/{account_id}", dependencies=[Depends(auth.require_admin)])
def delete_account(account_id: int):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Account not found")

        # Zamówienie to dokument płatności — przeżywa konto. Odpinamy je zamiast
        # kasować, inaczej Postgres blokuje usunięcie (orders.account_id ma FK).
        (session.query(Order).filter(Order.account_id == acc.id)
         .update({Order.account_id: None}, synchronize_session=False))
        # Ślad odebrania nagrody za odznaki też przeżywa konto — i ma przeżyć:
        # gdyby znikał razem z nim, trader odebrałby ten sam próg drugi raz.
        # Zostaje wpis bez wskaźnika (achievement_rewards.account_id ma FK).
        (session.query(AchievementReward).filter(AchievementReward.account_id == acc.id)
         .update({AchievementReward.account_id: None}, synchronize_session=False))
        # Transakcje, snapshoty, breachy, certyfikaty i wypłaty bez konta nie
        # znaczą nic — lecą razem z nim. Każda z tych tabel ma FK na accounts.id,
        # więc pozostawiona choć jedna blokuje DELETE (ForeignKeyViolation → 500).
        for model in (Trade, EquitySnapshot, Breach, Certificate, Payout, PayoutRequest):
            session.query(model).filter(model.account_id == acc.id).delete(synchronize_session=False)
        # Rachunek MT5 z puli NIE wraca do obiegu. Był już w rękach tradera: ma
        # historię transakcji, saldo dawno nie startowe, a poświadczenia zna
        # poprzedni właściciel. Przydzielenie go komuś innemu pokazałoby nowemu
        # klientowi cudze transakcje i dałoby staremu dostęp do jego konta.
        # Zostaje `claimed` i dostaje powód wycofania — panel ma to pokazać.
        (session.query(PoolAccount).filter(PoolAccount.claimed_by_account_id == acc.id)
         .update({PoolAccount.retired_reason: "account deleted"}, synchronize_session=False))

        session.delete(acc)
        session.commit()
        return {"deleted": account_id}
    finally:
        session.close()


@app.delete("/api/admin/traders/{trader_id}", dependencies=[Depends(auth.require_admin)])
def admin_delete_trader(trader_id: int):
    """Twarde usunięcie klienta z bazy — wszystko znika, e-mail wraca do puli
    i klient może zarejestrować się od nowa. Nieodwracalne."""
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        if tr.is_admin:
            raise HTTPException(400, "Administrator accounts cannot be deleted here")

        acc_ids = [a.id for a in session.query(Account.id)
                   .filter(Account.trader_id == trader_id).all()]
        if acc_ids:
            for model in (Trade, EquitySnapshot, Breach, Certificate, Payout, PayoutRequest):
                (session.query(model).filter(model.account_id.in_(acc_ids))
                 .delete(synchronize_session=False))
            # Rachunki MT5 z puli nie wracają do obiegu — patrz delete_account.
            (session.query(PoolAccount)
             .filter(PoolAccount.claimed_by_account_id.in_(acc_ids))
             .update({PoolAccount.retired_reason: "account deleted"}, synchronize_session=False))

        # KOLEJNOŚĆ JEST WARUNKIEM, nie stylem — Postgres pilnuje kluczy obcych
        # (SQLite lokalnie też, patrz db.py). Co na co wskazuje:
        #   reward_codes.order_id, credit_ledger.order_id -> orders.id
        #   achievement_rewards.account_id, orders.account_id -> accounts.id
        # Stąd: kody i księga przed zamówieniami, zamówienia i nagrody przed
        # kontami (te lecą na końcu). Dołożenie tabeli w złym miejscu tej listy
        # to 500 na „Delete client & all data" — i tylko na produkcji.
        for model in (RewardCode, CreditLedger, AchievementReward, Order, Notification,
                      PayoutRequest, JournalEntry, PushSubscription, KycFile, TelemetryEvent):
            (session.query(model).filter(model.trader_id == trader_id)
             .delete(synchronize_session=False))
        ticket_ids = [t.id for t in session.query(SupportTicket.id)
                      .filter(SupportTicket.trader_id == trader_id).all()]
        if ticket_ids:
            (session.query(TicketMessage).filter(TicketMessage.ticket_id.in_(ticket_ids))
             .delete(synchronize_session=False))
            (session.query(SupportTicket).filter(SupportTicket.id.in_(ticket_ids))
             .delete(synchronize_session=False))
        if acc_ids:
            (session.query(Account).filter(Account.id.in_(acc_ids))
             .delete(synchronize_session=False))

        email = tr.email
        session.delete(tr)
        session.commit()
        return {"deleted": trader_id, "email": email,
                "accounts_removed": len(acc_ids)}
    finally:
        session.close()


def _leaderboard_rows():
    """Pełna lista rankingu — realne konta, ale nazwiska MASKOWANE (RODO/prywatność).

    Ranking liczony WPROST z bieżącego equity (balance) kont FUNDED — bez
    doliczania wypłaconych zysków i bez kont w ewaluacji. Po wypłacie trader
    świadomie schodzi w rankingu: pokazujemy stan konta, nie życiorys.

    Wchodzą wyłącznie konta z zyskiem OSTRO powyżej zera. Ranking ma pokazywać
    tych, którzy zarabiają — konto na zerze albo pod kreską nie jest wynikiem,
    a przy pustej tablicy wypełniało listę zerami. Próg liczony na wartości
    zaokrąglonej, więc nic, co wyświetlałoby się jako „0.00%", nie przechodzi.

    Limit miejsc nakłada dopiero endpoint — testy obecności „czy konto W OGÓLE
    wchodzi do rankingu" korzystają z tej funkcji, żeby nie zależeć od sąsiadów
    z innych plików testowych.
    """
    session = SessionLocal()
    try:
        accs = session.query(Account).filter(Account.status == "funded").all()
        # Nazwisko bierzemy z tradera — ale JEDNYM zapytaniem po wszystkich
        # naraz. Osobny `session.get` na konto oznaczal tyle round-tripow, ile jest
        # kont funded (zmierzone: 1998 zapytan i 181 ms przy 2000 kont).
        # Kraju NIE podajemy wcale: nazwisko jest maskowane, a para
        # „inicjaly + kraj" potrafi zawezic osobe na tyle, ze maska przestaje
        # cokolwiek chronic. Publiczny ranking pokazuje stan konta, nie osobe.
        tr_ids = {a.trader_id for a in accs if a.trader_id}
        traderzy = {t.id: t for t in session.query(Trader)
                    .filter(Trader.id.in_(tr_ids)).all()} if tr_ids else {}
        rows = []
        for a in accs:
            equity_now = round(a.balance, 2)
            profit_pct = round((equity_now - a.initial_balance) / a.initial_balance * 100, 2)
            if profit_pct <= 0:
                continue
            tr = traderzy.get(a.trader_id)
            # Bez pola equity: wlasciciel nie chce pokazywac biezacego stanu
            # konta przy userach — publikujemy zysk (%, $) i rozmiar konta.
            rows.append({"trader": _mask_name(a.trader_name or (tr.full_name if tr else "")),
                         "phase": a.phase, "status": a.status,
                         "profit_pct": profit_pct,
                         "profit_usd": round(equity_now - a.initial_balance, 2),
                         "account_size": a.initial_balance})
        rows.sort(key=lambda r: r["profit_pct"], reverse=True)
        return rows
    finally:
        session.close()


# Decyzja wlasciciela: publiczny ranking pokazuje NARAZ najwyzej 10 osob.
LEADERBOARD_LIMIT = 10


@app.get("/api/leaderboard")
def leaderboard():
    """Publiczny ranking — czubek pelnej listy z `_leaderboard_rows`."""
    return _leaderboard_rows()[:LEADERBOARD_LIMIT]

def _qr_svg(url: str) -> str:
    """Kod QR do weryfikacji — inline SVG, żeby dokument był samowystarczalny
    (żadnego zewnętrznego generatora, który kiedyś padnie i zostawi puste pole).

    Ciemne moduły; białe tło i margines ciszy daje CSS. Odwrotny układ (jasne
    moduły na ciemnym) skanuje się znacznie gorzej.
    """
    try:
        import io
        import segno
        buf = io.BytesIO()
        segno.make(url, error="q").save(buf, kind="svg", border=0, dark="#050914",
                                        light=None, xmldecl=False, svgns=True, omitsize=True)
        return buf.getvalue().decode()
    except Exception as e:  # pragma: no cover - brak segno nie może wywalić certyfikatu
        print(f"[cert] nie udało się zbudować QR: {e}")
        return ""


def _public_base(request: Request) -> str:
    """Adres, pod którym dokument JEST serwowany — stąd bierze się link w QR.

    Świadomie NIE z APP_BASE_URL: gdyby ktoś zapomniał go przestawić po wdrożeniu
    na domenę, kod QR na wydanych certyfikatach prowadziłby na localhost. Host
    z żądania zawsze zgadza się z tym, co widzi odbiorca. Za proxy (Caddy/nginx)
    wartość bierze się z nagłówków X-Forwarded-*, które uvicorn honoruje przy
    `--proxy-headers`.
    """
    try:
        base = str(request.base_url).rstrip("/")
        if base:
            return base
    except Exception:  # pragma: no cover
        pass
    return settings.app_base_url.rstrip("/")


# Znaczniki pochodzenia wiersza — wyłącznie do ewidencji w panelu. Certyfikat
# jest dokumentem dla klienta i żaden z nich nie ma prawa się na nim wydrukować.
_NOTATKI_WEWNETRZNE = {payout_import.IMPORT_NOTE, payoutbot.NOTATKA}


def _cert_ctx(request, *, headline_plain, eyebrow, trader_name, amount_label, amount,
              blurb, meta, cert_token, seal, note=None, variant="pass", bare=False) -> dict:
    """Wspólny kontekst obu certyfikatów — jeden szablon, dwa warianty.

    Świadomie BEZ numeru rachunku MT5: dokument idzie na zewnątrz, a numer konta
    nikomu tam nie służy. Weryfikację zapewnia ID certyfikatu i kod QR.

    `bare` zdejmuje ze strony wszystko poza samą kartą (przyciski, stopkę, marginesy
    na całą wysokość okna). Dzięki temu ZWYKŁY zrzut całej strony jest już gotową
    grafiką certyfikatu i nie zależy od tego, czy zewnętrzna przeglądarka umie
    kadrować po selektorze — patrz `certshot.py`.
    """
    weryfikacja = f"{_public_base(request)}/verify/{cert_token}"
    return {
        "site_name": settings.site_name,
        "ga_id": settings.ga_measurement_id,
        "clarity_id": settings.clarity_project_id,
        "headline_plain": headline_plain,
        "eyebrow": eyebrow,
        "trader_name": trader_name or "—",
        "amount_label": amount_label, "amount": amount, "blurb": blurb,
        "meta": meta, "note": note, "bare": bool(bare),
        "cert_token": cert_token, "seal": seal, "variant": variant,
        "verify_url": f"/verify/{cert_token}",
        "verify_full_url": weryfikacja,
        "qr_svg": _qr_svg(weryfikacja),
        "signatory": settings.cert_signatory or None,
        "signatory_label": settings.cert_signatory_label,
    }


@app.middleware("http")
async def _html_no_cache(request: Request, call_next):
    """Strony HTML zawsze swieze: assety maja ?v=, ale sam HTML nie — przegladarka
    potrafila trzymac stara strone z linkami do starych wersji."""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/verify/{cert_token}")
def verify_api(request: Request, cert_token: str):
    """Dane certyfikatu dla podgladu na landingu.

    Token JEST przepustka do dokumentu (tak samo otwiera /certificate/{t}),
    wiec zwracamy dokladnie to, co widac na samym certyfikacie."""
    session = SessionLocal()
    try:
        cert = session.query(Certificate).filter(Certificate.cert_token == cert_token).first()
        if cert:
            acc = session.get(Account, cert.account_id)
            kind, when = cert.kind, cert.issued_at
        else:
            acc = session.query(Account).filter(Account.cert_token == cert_token).first()
            kind = when = None
            if acc and _cert_eligible(acc):
                kind = "funded" if acc.status == "funded" else "phase_1"
                when = acc.closed_at or acc.created_at
            else:
                acc = None
        weryfikacja = f"{_public_base(request)}/verify/{cert_token}"
        if acc:
            eyebrow, _seal = CERT_KINDS.get(kind, ("Evaluation passed", "Passed"))
            data = (when or datetime.now(timezone.utc)).strftime("%d %b %Y")
            return {"found": True, "variant": "pass", "eyebrow": eyebrow,
                    "amount_label": "Account size", "amount": f"${acc.initial_balance:,.0f}",
                    "trader_name": acc.trader_name or "—",
                    "meta": [{"value": data, "label": "Date"},
                             {"value": f"{acc.steps}-Step" if acc.steps else "Instant funding",
                              "label": "Program"}],
                    "cert_token": cert_token, "qr_svg": _qr_svg(weryfikacja),
                    "open_url": f"/certificate/{cert_token}"}
        payout = session.query(Payout).filter(Payout.cert_token == cert_token).first()
        if payout:
            pacc = session.get(Account, payout.account_id)
            kwota = (f"${payout.trader_share:,.0f}" if float(payout.trader_share).is_integer()
                     else f"${payout.trader_share:,.2f}")
            data = (payout.ts or datetime.now(timezone.utc)).strftime("%d %b %Y")
            return {"found": True, "variant": "payout", "eyebrow": "Payout",
                    "amount_label": "For the amount of", "amount": kwota,
                    "trader_name": (pacc.trader_name if pacc else None) or "—",
                    "meta": [{"value": data, "label": "Date"},
                             {"value": f"${(pacc.initial_balance if pacc else 0):,.0f}",
                              "label": "Account size"}],
                    "cert_token": cert_token, "qr_svg": _qr_svg(weryfikacja),
                    "open_url": f"/payout/{cert_token}"}
        raise HTTPException(404, "Certificate not found")
    finally:
        session.close()


@app.get("/certificate/{cert_token}", response_class=HTMLResponse)
def certificate(request: Request, cert_token: str):
    """Certyfikat po NIEODGADYWALNYM tokenie — link można udostępniać publicznie,
    ale nie da się enumerować cudzych certyfikatów po ID.

    Najpierw szukamy w tabeli `certificates` (osobny dokument za każdy etap),
    a dopiero potem w starym `Account.cert_token` — inaczej linki wydane
    wcześniej przestałyby działać.
    """
    session = SessionLocal()
    try:
        cert = session.query(Certificate).filter(Certificate.cert_token == cert_token).first()
        if cert:
            acc = session.get(Account, cert.account_id)
            kind, when = cert.kind, cert.issued_at
        else:
            acc = session.query(Account).filter(Account.cert_token == cert_token).first()
            if not acc or not _cert_eligible(acc):
                raise HTTPException(404, "Certificate not found")
            kind = "funded" if acc.status == "funded" else "phase_1"
            when = acc.closed_at or acc.created_at
        if not acc:
            raise HTTPException(404, "Certificate not found")

        data = (when or datetime.now(timezone.utc)).strftime("%d %b %Y")
        eyebrow, seal = CERT_KINDS.get(kind, ("Evaluation passed", "Passed"))
        blurb = {
            "phase_1": "This trader has passed Phase 1 of the evaluation, reaching the profit "
                       "objective while staying inside the daily loss and drawdown limits.",
            "phase_2": "This trader has passed Phase 2 of the evaluation, confirming the "
                       "consistency required before trading a funded account.",
            "funded":  "This trader has been granted a funded account after meeting every "
                       "objective of the evaluation, demonstrating consistency and disciplined "
                       "risk management.",
        }.get(kind, "This trader has passed the evaluation.")
        ctx = _cert_ctx(
            request, headline_plain="Certificate", eyebrow=eyebrow,
            trader_name=acc.trader_name,
            amount_label="Account size", amount=f"${acc.initial_balance:,.0f}",
            blurb=blurb,
            meta=[("Date", data),
                  ("Program", f"{acc.steps}-Step" if acc.steps else "Instant funding")],
            cert_token=cert_token, seal=seal,
        )
        return jinja.TemplateResponse(request, "certificate.html", ctx)
    finally:
        session.close()


@app.get("/payout/{cert_token}", response_class=HTMLResponse)
def payout_certificate(request: Request, cert_token: str, bare: int = 0):
    """Certyfikat wypłaty — publiczny link po nieodgadywalnym tokenie.

    Bez rozbicia na zysk/split/metodę: to są dane wewnętrzne rachunku, a dokument
    ma potwierdzać JEDNO — że wypłata w tej kwocie została zrealizowana.

    `?bare=1` zwraca samą kartę, bez przycisków i stopki — to jest widok, który
    zrzuca `certshot.py` przed wysłaniem grafiki na Telegrama.
    """
    session = SessionLocal()
    try:
        p = session.query(Payout).filter(Payout.cert_token == cert_token).first()
        if not p:
            raise HTTPException(404, "Certificate not found")
        acc = session.get(Account, p.account_id)
        when = (p.ts or datetime.now(timezone.utc)).strftime("%d %b %Y")
        # Kwota bez groszy, gdy okrągła — „$9,070" czyta się lepiej niż „$9,070.00",
        # ale przy 1 049,78 nie wolno zgubić reszty. Ta sama funkcja składa podpis
        # posta na Telegramie: rozjazd między dokumentem a podpisem byłby widoczny.
        kwota = payoutbot.kwota_txt(p.trader_share)
        ctx = _cert_ctx(
            request,
            headline_plain="Payout certificate",
            eyebrow="Payout",
            trader_name=(acc.trader_name if acc else None),
            amount_label="For the amount of", amount=kwota,
            blurb=("This trader has earned a payout, reflecting the discipline and risk "
                   "management required to trade a funded account. Their profit share has been "
                   "released in full."),
            meta=[("Date", when),
                  ("Account size", f"${(acc.initial_balance if acc else 0):,.0f}")],
            # Notatka księgowa jest dla nas, nie dla klienta — znaczniki pochodzenia
            # („imported from records", „payout bot") nie mają prawa wyjść na dokument.
            note=(None if (p.note or "").strip().lower() in _NOTATKI_WEWNETRZNE
                  else p.note),
            cert_token=cert_token, seal="Paid", variant="payout", bare=bool(bare),
        )
        return jinja.TemplateResponse(request, "certificate.html", ctx)
    finally:
        session.close()


# Odczyty, przy ktorych warto dogonic silnik: lista kont tradera (portal),
# lista kont admina i ranking. Reszta API zostaje szybka.
_LAZY_TICK_PATHS = {"/api/me/accounts", "/api/accounts", "/api/leaderboard"}
# Payout BOT dostaje do tego ANONIMOWY ruch z landingu: strona publiczna pobiera
# /api/public/stats przy kazdym wejsciu (site.js), a /api/leaderboard woła juz
# tylko zalogowany portal. Ciezki lazy-tick equity zostaje na starym zbiorze —
# guard payoutu to dwa male odczyty app_settings, wiec moze jechac szerzej.
_PAYOUT_TICK_PATHS = _LAZY_TICK_PATHS | {"/api/public/stats"}


async def _lazy_tick() -> None:
    """Dogania silnik przy wejściu na dashboard — dla hostingu bezserwerowego.

    Poller nie ma tam procesu, w którym mógłby się kręcić, a cron Vercela na
    koncie Hobby chodzi raz na dobę. Bez tego konta stoją w miejscu. Zamiast
    trzymać stan w pamięci (instancja i tak ginie po requeście) pytamy bazę
    o najświeższy snapshot: jeśli jest starszy niż `LAZY_TICK_SEC`, robimy jeden
    przebieg. Efekt: konta żyją dokładnie wtedy, gdy ktoś na nie patrzy.
    """
    okno = settings.lazy_tick_sec
    if okno <= 0:
        return
    session = SessionLocal()
    try:
        ostatni = session.query(func.max(EquitySnapshot.ts)).scalar()
    finally:
        session.close()
    if ostatni is not None:
        teraz = datetime.now(timezone.utc)
        # Postgres oddaje TIMESTAMP bez strefy — porównujemy w tej samej skali.
        if ostatni.tzinfo is None:
            teraz = teraz.replace(tzinfo=None)
        if (teraz - ostatni).total_seconds() < okno:
            return
    try:
        await poller.tick_once()
    except Exception as e:  # pragma: no cover - nie wywalamy odczytu przez tick
        print(f"[lazy-tick] błąd przebiegu: {e}")


@app.middleware("http")
async def _lazy_tick_middleware(request: Request, call_next):
    if request.url.path in _PAYOUT_TICK_PATHS:
        if settings.lazy_tick_sec > 0 and request.url.path in _LAZY_TICK_PATHS:
            await _lazy_tick()
        # Payout BOT łapie swój dzienny slot na ruchu strony — landing pobiera
        # /api/public/stats przy każdym wejściu, więc ruch z reklam publikuje
        # o WYLOSOWANEJ minucie, a nie o godzinie crona. Guard to dwa małe
        # odczyty app_settings; realny przebieg zdarza się raz na dobę. Celowo
        # NIEZALEŻNIE od LAZY_TICK_SEC: post nie może wisieć na włączeniu
        # doganiania silnika ryzyka. `PAYOUTBOT_ON_TRAFFIC=false` to wyłącznik
        # awaryjny (i furtka dla testów, które biją w te ścieżki bez intencji
        # tworzenia wypłat).
        if settings.payoutbot_on_traffic:
            await run_in_threadpool(_payout_bot_tick, _public_base(request))
        # Dzienny recap analogicznie z ruchu: pierwszy request po 06:00 czasu
        # polskiego go wysyła (godziny i guardu raz-na-dobę pilnuje
        # push.daily_recap), cron /api/tick o 15:00 UTC tylko dosyła w dni bez
        # wejść. Przed 06:00 to czysty test zegara — zero zapytań do bazy.
        if settings.recap_on_traffic:
            await run_in_threadpool(push.daily_recap)
        # Follow-upy leadów na tym samym ruchu: przypomnienia z terminem i nudge
        # „nikt nie wziął od 30 minut" liczą się w minutach, a cron Hobby chodzi
        # raz na dobę. Guard raz-na-LEADS_SWEEP_MIN siedzi w _lead_sweep_z_ruchu.
        if settings.leads_on_traffic:
            await run_in_threadpool(_lead_sweep_z_ruchu)
    return await call_next(request)


@app.middleware("http")
async def _powiadomienia_po_odpowiedzi(request: Request, call_next):
    """Maile i powiadomienia lecą PO odesłaniu odpowiedzi, nie w jej trakcie.

    SMTP to kilkanaście round-tripów do Brevo, a `notify.send` siedzi w środku
    POST-a, na który ktoś patrzy — sama rejestracja czekała przez to 706 ms.
    Runtime Pythona na Vercelu strumieniuje odpowiedź, a wywołanie funkcji żyje
    aż BackgroundTask się skończy: klient dostaje odpowiedź od razu, a mail i
    tak wychodzi w tym samym wywołaniu, więc nic nie przepada.

    Gdy handler rzuci wyjątkiem, zadania giną razem z nim — i o to chodzi:
    powiadomienie o operacji, która się nie udała, nie ma prawa wyjść.
    """
    with notify.kolejkuj() as zadania:
        response = await call_next(request)
    if zadania:
        wczesniejsze = response.background
        async def _wyslij() -> None:
            if wczesniejsze is not None:
                await wczesniejsze()
            for fn, args in zadania:
                await run_in_threadpool(fn, *args)
        response.background = BackgroundTask(_wyslij)
    return response


def _require_cron(x_admin_token: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)) -> None:
    """Wpuszcza crona (Bearer CRON_SECRET) albo admina (X-Admin-Token).

    Osobno od `auth.require_admin`, bo tam nagłówek Bearer jest interpretowany
    jako token tradera — cron ma własny sekret i nie ma konta w systemie.
    """
    if settings.admin_token and x_admin_token \
            and secrets.compare_digest(x_admin_token, settings.admin_token):
        return
    sekret = settings.cron_secret
    if sekret and authorization and authorization.lower().startswith("bearer "):
        if secrets.compare_digest(authorization.split(" ", 1)[1].strip(), sekret):
            return
    raise HTTPException(401, "Not allowed to trigger the risk engine")


@app.api_route("/api/tick", methods=["GET", "POST"], dependencies=[Depends(_require_cron)])
async def api_tick(request: Request):
    """Jeden przebieg silnika ryzyka — dla hostingu bez procesu w tle.

    Na zwykłym serwerze poller kręci się sam co `POLL_INTERVAL_SEC` i ten
    endpoint jest tylko awaryjnym „szturchnięciem". Na hostingu bezserwerowym
    (Vercel) proces nie żyje między requestami, więc to cron trzyma silnik przy
    życiu — wtedy ustaw POLLER_ENABLED=false i wal tu cronem. GET jest
    obsłużony, bo Vercel Cron uderza właśnie metodą GET.
    """
    # Payout BOT idzie PIERWSZY: poller przy wielu kontach potrafi zjeść cały
    # `maxDuration` funkcji i post nigdy by nie wyszedł. Dla payoutów cron jest
    # BACKSTOPEM (raz na dobę, po Hobby potrafi się spóźnić do godziny) —
    # publikuje od początku okna; wylosowany slot łapią ticki z ruchu strony.
    # Adres certyfikatu bierzemy z APP_BASE_URL, a NIE z hosta żądania — i tylko
    # tutaj. Ten link nie wraca do nikogo, kto ogląda odpowiedź: idzie do usługi
    # robiącej zrzut i stamtąd na publiczny kanał. Vercel woła crona pod adresem
    # wdrożenia (`*.vercel.app`), a ten jest za ochroną deploymentu — cron ma
    # własny sekret i wchodzi, ale przeglądarka usługi już nie i sfotografowała
    # ekran logowania Vercela. Taki „certyfikat" wyszedł na kanał 2026-08-08.
    baza = settings.app_base_url.rstrip("/")
    payout = _payout_bot_tick(baza if baza.startswith("https://") else _public_base(request),
                              backstop=True)
    wynik = await poller.tick_once()
    # Retencja snapshotów equity — tylko z crona (raz na dobę wystarcza),
    # nigdy z lazy-ticku, żeby ruch strony nie płacił za sprzątanie.
    sesja_prune = SessionLocal()
    try:
        pruned = poller.prune_equity_snapshots(sesja_prune)
    except Exception as e:  # pragma: no cover
        print(f"[poller] pruning nieudany: {e}")
        pruned = 0
    finally:
        sesja_prune.close()
    # Dzienny recap normalnie wychodzi z ruchu strony od 06:00 czasu polskiego
    # (middleware wyżej) — tu jest tylko zapasem na dzień bez wejść;
    # push.daily_recap() sam pilnuje godziny, guardu raz-na-dobę i ciszy bez
    # transakcji.
    recap = push.daily_recap()
    # „Scale your progress" raz w tygodniu (poniedzialek) — na tym samym cronie
    # z tego samego powodu co recap. Wlasny odstep 21 dni w `_upsell_nudge`
    # sprawia, ze recznie odpalony endpoint i ten przebieg sie nie dubluja.
    nudge = _upsell_nudge() if datetime.now(timezone.utc).weekday() == 0 else {"sent": 0}
    # Porzucone koszyki — tu samo zamowienie niesie znacznik `recovery_sent_at`,
    # wiec deduplikacja nie zalezy od tego, jak czesto ten cron chodzi.
    recovery = _checkout_recovery()
    # Przypomnienia o leadach — na czat dzialu, nie do klienta. Wlasna
    # deduplikacja po historii leada, wiec czestotliwosc crona nie ma znaczenia.
    leady = _lead_followups()
    # Saldo dostawcy zasięgu. Raz na dobę wystarczy: alert ma ostrzec ZANIM
    # konto zejdzie do zera, a nie dopiero przy odrzuconym zamówieniu.
    zasieg = _reach_saldo_tick()
    if isinstance(wynik, dict):
        return {**wynik, "daily_recap": recap, "upsell_nudge": nudge.get("sent", 0),
                "checkout_recovery": recovery.get("sent", 0), "payout_bot": payout,
                "lead_followups": leady.get("sent", 0), "snapshots_pruned": pruned,
                "reach": zasieg}
    return {"tick": wynik, "daily_recap": recap, "upsell_nudge": nudge.get("sent", 0),
            "checkout_recovery": recovery.get("sent", 0), "payout_bot": payout,
            "lead_followups": leady.get("sent", 0), "snapshots_pruned": pruned,
            "reach": zasieg}


def _reach_saldo_tick() -> dict:
    """Dobowe sprawdzenie salda dostawcy zasięgu. Nigdy nie wywraca ticka."""
    session = SessionLocal()
    try:
        return reach.sprawdz_saldo(session)
    except Exception as e:  # pragma: no cover - sieć/baza
        print(f"[reach] sprawdzenie salda nieudane: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def _payout_bot_tick(base_url: str | None = None, *,
                     backstop: bool = False) -> dict:
    """Przebieg Payout BOT-a — z crona (`backstop=True`) i z ruchu strony.
    NIGDY nie wywraca wywołującego — silnik ryzyka i odpowiedź dla klienta są
    ważniejsze niż post na kanale."""
    session = SessionLocal()
    try:
        wynik = payoutbot.uruchom(session, backstop=backstop, base_url=base_url)
        if wynik.get("created"):
            _PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
        return wynik
    except Exception as e:  # pragma: no cover
        print(f"[payoutbot] przebieg nieudany: {e}")
        return {"created": 0, "error": str(e)}
    finally:
        session.close()


@app.api_route("/api/cron/streak-reminder", methods=["GET", "POST"],
               dependencies=[Depends(_require_cron)])
def cron_streak_reminder():
    """Push „Twoja seria wygaśnie" — odpalany raz dziennie po południu UTC.

    Cel: seria >= 3 (jest czego bronić), check-in wczoraj, dziś jeszcze brak.
    Cron chodzi raz na dobę, więc nie potrzeba deduplikacji. To ta sama pętla
    dopaminowa co Duolingo: przypominamy o DYSCYPLINIE (wejściu do appki),
    nigdy o wynikach tradingu."""
    if not push.is_enabled():
        return {"sent": 0, "push": "disabled"}
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    session = SessionLocal()
    try:
        traders = (session.query(Trader)
                   .join(PushSubscription, PushSubscription.trader_id == Trader.id)
                   .filter(Trader.checkin_streak >= 3,
                           Trader.checkin_last == yesterday,
                           Trader.notify_updates != False)  # noqa: E712
                   .distinct().all())
        sent = 0
        for tr in traders:
            n = tr.checkin_streak or 0
            sent += push.send_to_trader(
                tr.id, f"Your {n}-day streak is on the line",
                "Check in before midnight UTC to keep it alive.", tag="streak")
        return {"sent": sent, "eligible": len(traders)}
    finally:
        session.close()


@app.api_route("/api/cron/checkout-recovery", methods=["GET", "POST"],
               dependencies=[Depends(_require_cron)])
def cron_checkout_recovery(min_minutes: int = 60, max_hours: int = 72):
    """Recznie/cronem — cala robota siedzi w `_checkout_recovery`."""
    return _checkout_recovery(min_minutes, max_hours)


def _checkout_recovery(min_minutes: int = 60, max_hours: int = 72) -> dict:
    """Mail do ludzi, ktorym platnosc NIE doszla do skutku.

    Jedno sito na wszystkie warianty porzuconego zakupu, bo kazdy zostawia
    dokladnie ten sam slad — zamowienie `pending`, ktore nigdy nie stalo sie
    `paid`: karta odrzucona, zamknieta strona Stripe'a, wreszcie blad przy
    zakladaniu sesji (od poprawki w `billing` zamowienie zostaje wtedy w bazie
    wlasnie po to).

    Dolne okno `min_minutes` chroni przed mailem do kogos, kto wlasnie wpisuje
    numer karty. Gorne `max_hours` — przed odkopaniem przy pierwszym przebiegu
    na produkcji zamowien sprzed tygodni; tam takich rekordow leza setki i
    poszedlby z tego jeden wielki spam.
    """
    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        kandydaci = (session.query(Order)
                     .filter(Order.status == "pending",
                             Order.recovery_sent_at.is_(None),
                             Order.created_at <= now - timedelta(minutes=min_minutes),
                             Order.created_at >= now - timedelta(hours=max_hours),
                             # Zamowienie czekajace na przelew krypto nie jest
                             # porzucone — klient placi, tylko poza Stripe'em.
                             Order.flag.is_(None),
                             # Zamowieniem wystawionym recznie zarzadza admin.
                             # Mail o porzuconym koszyku mowi „karta mogla zostac
                             # odrzucona" — o platnosci, ktorej klient nigdy nie
                             # zaczynal. To nieprawda i brzmi jak pomylka.
                             Order.provider != "manual")
                     .all())
        etykiety = {p.key: p.label for p in session.query(Product).all()}
        sent = 0
        for zam in kandydaci:
            zam.recovery_sent_at = now
            # Kupil pozniej cokolwiek innego — sprawa sama sie rozwiazala
            # i przypominanie o starym koszyku wyszloby glupio.
            if (session.query(Order)
                    .filter(Order.trader_id == zam.trader_id, Order.status == "paid",
                            Order.created_at >= zam.created_at).first()):
                continue
            tr = session.get(Trader, zam.trader_id)
            if tr is None or not tr.email:
                continue
            notify.send("checkout_recovery", tr.email, {
                "name": tr.first_name or tr.full_name or "trader",
                "product_label": etykiety.get(zam.product_key, zam.product_key),
                "amount": zam.amount_usd,
                "credits_used": zam.credits_used or 0,
            })
            sent += 1
        session.commit()
        return {"sent": sent, "candidates": len(kandydaci)}
    finally:
        session.close()


@app.api_route("/api/cron/upsell-nudge", methods=["GET", "POST"],
               dependencies=[Depends(_require_cron)])
def cron_upsell_nudge(min_days: int = 21):
    """Recznie/cronem — cala robota siedzi w `_upsell_nudge`."""
    return _upsell_nudge(min_days)


def _upsell_nudge(min_days: int = 21) -> dict:
    """Cykliczne „Scale your progress" — dzwonek + push z PRAWDZIWA matematyka.

    Ta sama regula co panel w portalu: bierzemy najlepsze zywe konto z dodatnim
    wynikiem i pokazujemy, ile ten sam procent dalby na najwiekszym rozmiarze w
    tej rodzinie. Zero obietnic — to przeliczenie wlasnego wyniku tradera.

    Bramki: kategoria „Daily Recap & Offers" (notify_marketing) oraz odstep
    min_days od poprzedniego takiego powiadomienia — to promocja, wiec nie moze
    wracac co dobe. Klikniecie prowadzi do zakladki Challenges z parametrem
    `upsell=1`, ktory przewija do panelu i go podswietla.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=min_days)
    session = SessionLocal()
    try:
        prods = session.query(Product).filter(Product.active == True).all()  # noqa: E712
        traders = (session.query(Trader)
                   .filter(Trader.is_admin == False,                          # noqa: E712
                           Trader.notify_marketing != False)                  # noqa: E712
                   .all())
        sent = 0
        for tr in traders:
            last = (session.query(Notification)
                    .filter(Notification.trader_id == tr.id,
                            Notification.event == "upsell_scale")
                    .order_by(Notification.id.desc()).first())
            if last is not None and last.created_at is not None:
                widziane = last.created_at
                if widziane.tzinfo is None:
                    widziane = widziane.replace(tzinfo=timezone.utc)
                if widziane > cutoff:
                    continue
            best, best_pct = None, 0.0
            for a in session.query(Account).filter(Account.trader_id == tr.id).all():
                if a.status in ("breached", "failed") or not a.initial_balance:
                    continue
                pct = float(_metrics_for(a).get("profit_pct") or 0)
                if pct > best_pct:
                    best, best_pct = a, pct
            if best is None:
                continue
            wieksze = [p for p in prods
                       if p.steps == best.steps and p.account_size > best.initial_balance]
            if not wieksze:
                continue
            top = max(wieksze, key=lambda p: p.account_size)
            zysk = top.account_size * best_pct / 100
            title = f"You'd have earned ${zysk:,.0f} on a {_size_label(top.account_size)} account"
            body = (f"Your +{best_pct:.2f}% on {best.login}. See what a larger "
                    f"account would have paid.")
            url = "/portal?view=accounts&upsell=1"
            push._center_row(session, tr.id, "upsell_scale", title, body, url)
            session.commit()
            push.send_to_trader(tr.id, title, body, url=url, tag="upsell_scale")
            sent += 1
        return {"sent": sent, "eligible": len(traders), "min_days": min_days}
    finally:
        session.close()


@app.get("/api/admin/mail-log", dependencies=[Depends(auth.require_admin)])
def admin_mail_log():
    """Dziennik prób wysyłki maili — ostatnie wpisy plus licznik porażek.

    SMTP pada po cichu (notify łapie wyjątek, żeby nie wywrócić requestu),
    więc to jedyne miejsce, w którym „mail nie wyszedł" jest widoczne,
    zanim zgłosi to klient.
    """
    session = SessionLocal()
    try:
        wpisy = (session.query(MailLog)
                 .order_by(MailLog.ts.desc()).limit(200).all())
        tydzien = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        porazki = (session.query(MailLog)
                   .filter(MailLog.ok == False,                              # noqa: E712
                           MailLog.ts >= tydzien).count())
        return {"failed_7d": porazki,
                "entries": [{"id": m.id,
                             "ts": m.ts.isoformat() if m.ts else None,
                             "event": m.event, "to": m.to_email,
                             "subject": m.subject, "ok": bool(m.ok),
                             "error": m.error}
                            for m in wpisy]}
    finally:
        session.close()


@app.get("/api/stats", dependencies=[Depends(auth.require_admin)])
def stats():
    """Statystyki OPERACYJNE (feed, tryb Stripe, pula) — tylko admin.

    Publiczna strona używa /api/public/stats, który nie ujawnia internali.
    """
    session = SessionLocal()
    try:
        # GROUP BY zamiast ciągnięcia wszystkich kont do Pythona: kafelki
        # potrzebują tylko liczników per status, a tabela rośnie bez końca.
        by_status: dict[str, int] = dict(
            session.query(Account.status, func.count(Account.id))
            .group_by(Account.status).all())
        # Licznik traderów brał WSZYSTKIE nieadministracyjne wiersze, a każda
        # wypłata z Payout BOT-a i z importu CSV zakłada własnego tradera —
        # przy jednej wypłacie dziennie ten kafelek rósł sam z siebie i nie
        # odpowiadał już na pytanie „ilu mam klientów". Rekordy techniczne mają
        # adres `…@imported.local` (patrz `payout_import._email_techniczny`),
        # więc rozdzielenie jest jednoznaczne, a nie zgadywane.
        klienci = (session.query(Trader)
                   .filter(Trader.is_admin == False,                       # noqa: E712
                           ~Trader.email.like(f"%{payout_import.TECHNICZNA_DOMENA}")).count())
        wewnetrzni = (session.query(Trader)
                      .filter(Trader.is_admin == False,                    # noqa: E712
                              Trader.email.like(f"%{payout_import.TECHNICZNA_DOMENA}")).count())
        return {"total": sum(by_status.values()), "by_status": by_status,
                "funded": by_status.get("funded", 0), "active": by_status.get("active", 0),
                "failed": by_status.get("failed", 0), "feed": settings.feed,
                "stripe": "live" if settings.stripe_enabled else "mock",
                # Oba kanały do leada chowają swój przycisk, gdy nie mają czym
                # wysłać — i to jest jedyne miejsce, w którym widać, DLACZEGO.
                # Bez tego brak konfiguracji wygląda dokładnie tak samo jak
                # zepsuta funkcja.
                "lead_sms_missing": sms.czego_brakuje(),
                "lead_mail_missing": lead_mail.czego_brakuje(),
                # Kanał do klienta (poświadczenia MT5, reset hasła, wypłaty) nie
                # ma przycisku, który mógłby się schować — bez tej listy jego brak
                # nie objawia się NICZYM aż do zgłoszenia „nie dostałem maila".
                "notify_mail_missing": notify.czego_brakuje(),
                "traders": klienci, "traders_internal": wewnetrzni,
                # Granty ($0, provider="grant") to nie sprzedaż — liczone tutaj
                # zawyżałyby "paid orders" o darmowe konta z promocji BOGO.
                "orders_paid": (session.query(Order)
                                .filter(Order.status == "paid", Order.provider != "grant").count()),
                "pool_free": session.query(PoolAccount).filter(PoolAccount.claimed == False).count(),  # noqa: E712
                "provisioning": by_status.get("provisioning", 0),
                # Kafelek na Overview pokazuje się tylko przy porażkach —
                # cicha awaria SMTP ma być widoczna bez wchodzenia w Mail.
                "mail_failed_7d": (session.query(MailLog)
                                   .filter(MailLog.ok == False,              # noqa: E712
                                           MailLog.ts >= datetime.now(timezone.utc).replace(tzinfo=None)
                                           - timedelta(days=7))
                                   .count())}
    finally:
        session.close()


_PUBLIC_STATS_CACHE: dict = {"ts": 0.0, "data": None}


@app.get("/api/public/stats")
def public_stats():
    """Wyselekcjonowane, PRAWDZIWE liczby dla strony publicznej.

    Zero internali (feed/stripe/pula). Landing ukrywa metryki zerowe, więc
    świeża instalacja nie świeci "0 payouts" — po prostu nie pokazuje kafelka.
    payouts_total_usd zawiera zwroty opłat (tak księgujemy trader_share).
    """
    now = monotonic()
    if _PUBLIC_STATS_CACHE["data"] is not None and now - _PUBLIC_STATS_CACHE["ts"] < 60:
        return _PUBLIC_STATS_CACHE["data"]
    session = SessionLocal()
    try:
        # Agregaty w SQL zamiast .all(): Payout rośnie codziennie (bot + import
        # CSV), a to woła landing — każda zimna instancja płaciła pełny transfer
        # tabeli z Supabase tylko po to, by policzyć sumę i maksimum w Pythonie.
        pay_cnt, pay_sum, pay_max = (
            session.query(func.count(Payout.id),
                          func.coalesce(func.sum(Payout.trader_share), 0.0),
                          func.coalesce(func.max(Payout.trader_share), 0.0))
            .filter(Payout.paid == True).one())  # noqa: E712
        countries_cnt = (
            session.query(func.count(func.distinct(func.lower(func.trim(Trader.kyc_country)))))
            .filter(Trader.kyc_status == "approved",
                    Trader.kyc_country.isnot(None),
                    func.trim(Trader.kyc_country) != "").scalar())
        data = {
            "accounts_total": session.query(Account).count(),
            "active_accounts": session.query(Account).filter(Account.status == "active").count(),
            "funded_accounts": session.query(Account).filter(Account.status == "funded").count(),
            "traders_total": session.query(Trader).filter(Trader.is_admin == False).count(),  # noqa: E712
            "payouts_count": pay_cnt,
            # Pełne dolary: ".96" przy sześciocyfrowej kwocie poszerzał kafel LP aż do obcięcia.
            "payouts_total_usd": int(round(pay_sum)),
            "largest_payout_usd": int(round(pay_max)),
            "countries_count": countries_cnt,
        }
        _PUBLIC_STATS_CACHE.update(ts=now, data=data)
        return data
    finally:
        session.close()


_PUBLIC_CERTS_CACHE: dict = {"ts": 0.0, "data": None}


@app.get("/api/public/certificates/recent")
def public_recent_certificates():
    """Pas "Recently issued" na landingu: PRAWDZIWE certyfikaty WYPŁAT.

    Wyłącznie wypłaty — certyfikaty za zaliczony etap 1/2 i za funded na pas nie
    idą. Zaliczony etap mówi, że ktoś przeszedł ewaluację; realnym dowodem, po
    który ludzie tu przychodzą, jest przelew, a mieszanie jednego z drugim
    rozwadnia pas i podbija licznik osiągnięciami, które nikogo nie przekonują.

    Nazwiska maskowane jak w rankingu, ZERO tokenów i ID — publikacja linku do
    cudzego certyfikatu to decyzja właściciela, nie nasza. Pusta baza -> [].
    """
    now = monotonic()
    if _PUBLIC_CERTS_CACHE["data"] is not None and now - _PUBLIC_CERTS_CACHE["ts"] < 60:
        return _PUBLIC_CERTS_CACHE["data"]
    session = SessionLocal()
    try:
        out = []
        pays = (session.query(Payout, Account, Trader)
                .join(Account, Payout.account_id == Account.id)
                .join(Trader, Account.trader_id == Trader.id)
                .filter(Payout.cert_token != None, Payout.paid == True,   # noqa: E711,E712
                        Payout.show_on_lp == True)                        # noqa: E712
                .order_by(Payout.ts.desc()).limit(24).all())
        for pay, acc, tr in pays:
            out.append({
                "kind": "payout",
                "kind_label": "Payout certificate",
                "account_size": acc.initial_balance,
                "amount_usd": int(round(pay.trader_share)),
                "trader": _mask_name(tr.full_name),
                "issued_at": pay.ts.isoformat() if pay.ts else None,
            })
        out.sort(key=lambda r: r["issued_at"] or "", reverse=True)
        data = out[:24]
        _PUBLIC_CERTS_CACHE.update(ts=now, data=data)
        return data
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  Leady z landingu                                                            #
# --------------------------------------------------------------------------- #
# Landing zbierający zgłoszenia stoi na osobnym hostingu i osobnej domenie.
# Wysyła tu leada POST-em i to cała integracja — nie ma dostępu do bazy, nie
# loguje się do panelu, nie wie o niczym poza adresem tego endpointu i swoim
# tokenem. Odwrotnie też: nic tutaj nie oddzwania na landing.
#
# Status „czy kupił" nie jest kolumną w `leads`, tylko wynikiem złączenia po
# mailu z `traders`/`orders` przy odczycie. Kolumna dawałaby się rozjechać
# z prawdą (ktoś kupuje w nocy, nikt nie odklika), a tak rozjechać się nie ma czym.


def _lead_wymaga_tokenu(podany: str | None) -> None:
    """Sekret landingu, celowo inny niż token admina.

    Landing jest publiczną stroną na cudzym hostingu; gdy wycieknie mu ten
    token, ktoś obcy może najwyżej dopisać leada. Gdyby to był ADMIN_TOKEN,
    ten sam wyciek oddawałby panel z kontami i wypłatami.
    """
    oczekiwany = settings.lead_ingest_token
    if not oczekiwany or not podany or not secrets.compare_digest(podany, oczekiwany):
        # Głośno, bo to jedyny ślad. Odmowa leci PRZED otwarciem sesji, więc po
        # tej stronie nie zostaje ani wiersz, ani zdarzenie — a rozjazd sekretów
        # z landingiem kasuje 100% leadów i z zewnątrz wygląda dokładnie tak samo
        # jak wyłączona kampania.
        print("[leads] ingest 401 — token "
              + ("nieustawiony po tej stronie" if not oczekiwany
                 else "nieprzysłany" if not podany else "niezgodny"))
        raise HTTPException(401, "Unauthorized")


class LeadIn(BaseModel):
    # `extra="allow"`, bo landing przysyła więcej pól, niż wystawiamy w kolumnach
    # (odpowiedzi z ankiety, uzasadnienie oceny, UA), a całość ląduje w
    # `payload_json`. Zawężenie modelu po cichu obcięłoby to, co dział czyta.
    model_config = {"extra": "allow"}

    email: str
    name: str = ""
    phone: str | None = None
    phoneIso: str | None = None
    telegram: str | None = None
    country: str | None = None
    source: str = ""
    ref: str | None = None
    outcome: str = "qualified"
    quality: dict | None = None


# Etykiety statusów biorą się z opisów przycisków, żeby wiadomość mówiła
# dokładnie to, co przed chwilą kliknięto. Dwa niezależne słowniki rozjeżdżały
# się przy pierwszej zmianie tekstu na guziku.
_ETYKIETY_STATUSU = {stan: opis for opis, stan in telegram.LEAD_BUTTONS}

# To samo dla powodu przegranej. Kody są wspólne z `models.LOST_REASONS`
# (pilnuje tego test) — tutaj chodzi wyłącznie o polski opis na kartę i w dymek.
_ETYKIETY_POWODU = {kod: opis for opis, kod in telegram.LOST_REASON_BUTTONS}


# Ten sam kształt, który waliduje landing i panel (`leadTgLink`
# w admin-panel.js). Nick spoza niego zostaje zwykłym tekstem: martwy link
# t.me wygląda jak kontakt, a nim nie jest — dział ma zobaczyć literówkę,
# a nie gonić za nią.
_TG_HANDLE_RX = re.compile(r"@?[A-Za-z][A-Za-z0-9_]{4,31}")


def _link_telegram(nick: str | None) -> str:
    """Nick jako klikalny profil. Zwraca HTML, więc escape'uje sam."""
    uchwyt = (nick or "").strip().lstrip("@")
    if not uchwyt:
        return ""
    if not _TG_HANDLE_RX.fullmatch(uchwyt):
        return html.escape(nick or "")
    return f'<a href="https://t.me/{uchwyt}">@{uchwyt}</a>'


def _tekst_alertu(lead: Lead, dane: dict) -> str:
    """Treść wiadomości na Telegram. WSZYSTKO od użytkownika przez html.escape:
    imię z formularza trafia do wiadomości z `parse_mode=HTML`, więc jeden
    nawias kątowy w nazwisku potrafiłby rozsypać cały alert. Jedyny wyjątek to
    nick Telegrama — `_link_telegram` robi z niego link i escape'uje po swojemu.

    Ta sama funkcja składa wiadomość przy zgłoszeniu i po każdym kliknięciu —
    post na kanale jest kartą leada, a nie powiadomieniem sprzed tygodnia.
    Dlatego niesie też to, co dopisała moderacja: kto go wziął, na czym stanęło
    i co ostatnio powiedział.

    Układ jest ten sam, którym landing publikował własne alerty. Do niedawna
    kanał dostawał o jednym człowieku dwie wiadomości: czytelną kartę bez
    przycisków stamtąd i tę, z przyciskami. Landing przestał pisać, a jego układ
    został tutaj — bo to jego dział czytał.

    Nagłówek, ocena i hashtag biorą się z `quality` przysłanego w zgłoszeniu, a
    nie z drugiej takiej mapy po tej stronie: skala mieszka na landingu i jej
    kopia rozjechałaby się przy pierwszej zmianie progów.
    """
    e = html.escape
    ocena = dane.get("quality") or {}
    odrzucony = lead.outcome == "not_qualified"

    if odrzucony:
        linie = ["🔴 <b>Not qualified</b>"]
    else:
        emoji = ocena.get("emoji") or {"high": "🔥", "warm": "🟡", "cold": "⚪️"}.get(lead.tier or "", "•")
        etykieta = ocena.get("label") or (lead.tier or "lead").upper()
        # Skala z landingu, gdy przyszła („8/9"). Bez niej sam wynik, bo goła
        # liczba bez maksimum i tak mówi więcej niż nic.
        maks = ocena.get("max")
        punkty = (f" · {lead.score}/{maks}" if isinstance(maks, int)
                  else f" · {lead.score}" if lead.score else "")
        linie = [f"{e(str(emoji))} <b>{e(str(etykieta))}</b>{punkty}", "🟢 Qualified"]

    # Stan moderacji zaraz pod nagłówkiem, NAD danymi kontaktowymi: przy
    # przewijaniu kanału to jedyne, czego się szuka („czy ktoś to już wziął"),
    # a ankieta jest kontekstem do przeczytania dopiero wtedy, gdy się bierze.
    # Po polsku, w odróżnieniu od reszty — to nie są dane od człowieka z
    # formularza, tylko dopiski działu, i mają się od nich odcinać.
    stan = []
    if lead.owner:
        stan.append(f"👤 Zajmuje się: <b>{e(lead.owner)}</b>")
    if lead.status != "new":
        powod = _ETYKIETY_POWODU.get(lead.lost_reason or "")
        # Powód dopisuje się do statusu, a nie do własnej linijki: „odpada"
        # i „za drogo" to jedno zdanie, a każda kolejna linia na karcie
        # spycha dane kontaktowe poza pierwszy ekran telefonu.
        stan.append(f"📌 {_ETYKIETY_STATUSU.get(lead.status, lead.status)}"
                    + (f" — {powod}" if powod else ""))
    if lead.applications > 1:
        stan.append(f"↻ Zgłasza się {lead.applications}. raz")
    if lead.note:
        # Ostatnia notatka, nie wszystkie: wiadomość ma limit 4096 znaków, a
        # notatki dopisują się jedna pod drugą przez cały czas życia leada.
        ostatnia = lead.note.strip().split("\n")[-1]
        stan.append(f"📝 {e(ostatnia[:300])}")
    if stan:
        linie += ["", *stan]

    kraj = f" ({lead.phone_iso})" if lead.phone and lead.phone_iso else ""
    # Wartości wchodzą tu JUŻ jako HTML, bo nick jest linkiem — każda inna
    # przechodzi przez `e()` w tym samym miejscu, w którym powstaje.
    wiersze = (("Name", e(lead.name or "")), ("Email", e(lead.email or "")),
               ("Telegram", _link_telegram(lead.telegram)),
               ("Phone", e(f"{lead.phone}{kraj}") if lead.phone else ""),
               ("Source", e(" / ".join(x for x in (lead.source, lead.ref) if x))))
    linie += ["", *(f"<b>{k}:</b> {v}" for k, v in wiersze if v)]

    # Uzasadnienie oceny. Bez tego przy nazwisku stoi goła liczba, której nie ma
    # jak sprawdzić — a to ona decyduje, do kogo pisze się pierwszego.
    def _powody(klucz: str) -> str:
        wartosc = ocena.get(klucz)
        if not isinstance(wartosc, list) or not wartosc:
            return ""
        return " · ".join(str(x) for x in wartosc)[:300]

    jakosc = []
    if plusy := _powody("reasons"):
        jakosc.append(f"<b>Why:</b> {e(plusy)}")
    if braki := _powody("gaps"):
        jakosc.append(f"<b>Gaps:</b> {e(braki)}")
    if flagi := _powody("penalties"):
        # Własna linijka i ostrzeżenie, bo to jedyne miejsce mówiące, że formularz
        # coś przyjął, ale nikt nie powinien brać tego za dobrą monetę. Decyduje,
        # czy pod ten numer w ogóle warto pisać.
        jakosc.append(f"⚠️ <b>Check:</b> {e(flagi)}")
    if jakosc:
        linie += ["", *jakosc]

    odpowiedzi = dane.get("answers") or {}
    if isinstance(odpowiedzi, dict) and odpowiedzi:
        # Cała ankieta, nie pierwsze cztery pytania: dział czyta kanał zamiast
        # panelu, a odpowiedź ucięta w połowie zestawu to ta, o którą pyta się
        # drugi raz. Dziesięć z zapasem starcza na ankietę mającą ich osiem.
        linie += ["", "<b>Answers</b>"]
        for pytanie, odp in list(odpowiedzi.items())[:10]:
            linie.append(f"• {e(str(pytanie)[:80])}\n   → <b>{e(str(odp)[:60])}</b>")

    # Klikalny w Telegramie: wyszukuje w kanale wszystko z tą samą oceną i to
    # najtańszy sposób, żeby wyciągnąć same gorące leady bez CRM-a.
    if tag := ("#lead_out" if odrzucony else ocena.get("tag")):
        linie += ["", e(str(tag))]
    return "\n".join(linie)


def _answers_z_payloadu(surowy: str | None) -> dict:
    """Odpowiedzi z ankiety wyjęte z JSON-a zgłoszenia. Zły JSON to pusty słownik,
    a nie 500 — panel ma pokazać resztę leada nawet gdy jedno pole jest śmieciem."""
    try:
        dane = json.loads(surowy or "{}")
    except ValueError:
        return {}
    odp = dane.get("answers")
    return odp if isinstance(odp, dict) else {}


# Pola kampanii, które landing parkuje przy wejściu i dokłada do zgłoszenia.
# Lista jest zamknięta, bo `payload_json` to surowe ciało POST-a: bez niej panel
# wyświetlałby dowolny klucz, który ktoś wstrzyknie w `attribution`.
_POLA_KAMPANII = ("utm_source", "utm_medium", "utm_campaign", "utm_content",
                  "fbclid", "gclid", "ttclid")


def _kampania_z_payloadu(surowy: str | None) -> dict:
    """Z której reklamy przyszedł ten człowiek.

    Wydobywane przy odczycie, a nie zapisywane w osobnej kolumnie: to pytanie
    zadawane raz na jakiś czas, przy dzieleniu budżetu, a nie coś, po czym
    filtruje się listę. Bez tego odpowiedź zostaje w JSON-ie, którego panel
    nigdzie nie pokazuje — czyli praktycznie tylko w bazie.
    """
    try:
        dane = json.loads(surowy or "{}")
    except ValueError:
        return {}
    pola = dane.get("attribution") if isinstance(dane, dict) else None
    if not isinstance(pola, dict):
        return {}
    return {k: str(pola[k])[:120] for k in _POLA_KAMPANII
            if isinstance(pola.get(k), str) and pola[k].strip()}


def _tresc_z_payloadu(surowy: str | None) -> str:
    """Treść wysłanej wiadomości z JSON-a zdarzenia. Zły JSON to pusty string,
    a nie 500 — historia leada ma się otworzyć nawet, gdy jeden wpis jest
    śmieciem."""
    try:
        dane = json.loads(surowy or "{}")
    except ValueError:
        return ""
    return str(dane.get("body") or "") if isinstance(dane, dict) else ""


def _lead_json(lead: Lead, trader_id: int | None, paid_usd: float,
               next_due: datetime | None = None, accounts: int = 0) -> dict:
    """Lead dla panelu. Wspólne dla listy i karty szczegółów, żeby karta nie
    zaczęła nazywać pól inaczej niż tabela, z której się ją otwiera."""
    zakwalifikowany = lead.outcome != "not_qualified"
    mail_temat, mail_tekst = lead_mail.tresc(lead.name,
                                             zakwalifikowany=zakwalifikowany)
    return {
        "id": lead.id, "email": lead.email, "name": lead.name,
        "phone": lead.phone, "phone_iso": lead.phone_iso, "telegram": lead.telegram,
        "country": lead.country, "source": lead.source, "ref": lead.ref,
        "outcome": lead.outcome, "tier": lead.tier, "score": lead.score,
        "status": lead.status, "note": lead.note, "owner": lead.owner,
        "lost_reason": lead.lost_reason,
        "bought": bool(lead.bought),
        "next_due": next_due.isoformat() if next_due else None,
        "applications": lead.applications,
        "answers": _answers_z_payloadu(lead.payload_json),
        "campaign": _kampania_z_payloadu(lead.payload_json),
        "contacted_at": lead.contacted_at.isoformat() if lead.contacted_at else None,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        # Wyliczane, nie przechowywane — patrz komentarz nad sekcją.
        "trader_id": trader_id,
        "paid_usd": round(float(paid_usd or 0), 2),
        # Ile kont ten człowiek już ma. Panel wyłącza po tym „darmowe konto":
        # przycisk zakłada PRAWDZIWY challenge, więc drugie kliknięcie to drugi
        # koszt. Serwer i tak sprawdza to jeszcze raz — panel ma tylko nie
        # zapraszać do pomyłki.
        "accounts": int(accounts or 0),
        # Czy SMS ma do kogo pójść. Liczone tutaj, bo panel nie zna ani
        # konfiguracji Twilio, ani tego, że numer bez kierunkowego jest
        # bezużyteczny — a przycisk, który zawsze odmawia, uczy go nie klikać.
        "sms_ready": bool(sms.is_enabled() and sms.numer_e164(lead.phone)),
        # Dokładnie ten tekst, który wyjdzie po kliknięciu — panel go POKAZUJE,
        # a nie składa. Wysyłka jest płatna i nieodwracalna, więc admin ma prawo
        # zobaczyć treść, zanim kliknie; gdyby panel składał ją u siebie, prędzej
        # czy później pokazywałby co innego, niż faktycznie idzie w świat.
        "sms_text": sms.tresc(lead.name, zakwalifikowany=zakwalifikowany),
        # Mail idzie tam, gdzie Telegram i SMS nie mają dokąd. Adres jest polem
        # wymaganym formularza, więc „ready" prawie zawsze jest prawdą — prawie,
        # bo część leadów dopisano z ręki i tam trafia się „brak".
        "mail_ready": bool(lead_mail.is_enabled() and lead_mail.adres(lead.email)),
        "mail_subject": mail_temat,
        "mail_text": mail_tekst,
    }


def _zdarzenie(session, lead_id: int, kind: str, detail: str = "",
               actor: str = "", payload: str | None = None) -> None:
    """Dopisek do historii leada — bez commita.

    Zdarzenie wchodzi tą samą transakcją co zmiana, którą opisuje, więc nie da
    się mieć w historii czegoś, co się nie stało (ani odwrotnie).
    """
    session.add(LeadEvent(lead_id=lead_id, kind=kind, detail=(detail or "")[:200],
                          actor=(actor or "")[:60], payload_json=payload))


def _zapisz_status(session, lead: Lead, status: str, actor: str) -> None:
    """Jedno miejsce zmiany statusu — woła to i panel, i przycisk z Telegrama.

    `contacted_at` ustawia się raz, przy pierwszym ruszeniu z „new": to moment
    pierwszego kontaktu, a nie ostatniego, więc kolejne kliknięcia go nie ruszają.

    Ustawienie tego samego statusu drugi raz nie zapisuje niczego — w Telegramie
    przycisk zostaje pod wiadomością i klika się go odruchowo, a historia ma
    pokazywać zmiany, nie kliknięcia.
    """
    if status not in LEAD_STATUSES:
        raise HTTPException(400, f"Unknown status: {status}")
    if status == lead.status:
        return
    poprzedni = lead.status
    if status != "new" and lead.contacted_at is None:
        lead.contacted_at = datetime.now(timezone.utc)
    lead.status = status
    # Lead wrócił do gry — powód przegranej przestał być prawdą i musi zniknąć,
    # bo inaczej raport policzy jako straconego kogoś, do kogo dział właśnie
    # znowu pisze. Ruch `rejected` ↔ `burned` powodu NIE rusza: to dwa odcienie
    # tej samej decyzji, a nie nowa.
    if status not in LEAD_LOST_STATUSES:
        lead.lost_reason = None
    lead.updated_at = datetime.now(timezone.utc)
    _zdarzenie(session, lead.id, "status", f"{poprzedni} → {status}", actor)
    if status == "burned":
        # Spalony lead ląduje w koszu — cron nie ma prawa szturchać działu
        # o człowieka, z którym świadomie skończyliśmy.
        (session.query(LeadReminder)
         .filter(LeadReminder.lead_id == lead.id, LeadReminder.active.is_(True))
         .update({"active": False}))


def _sms_do_leada(session, lead: Lead, actor: str, *,
                  wymuszaj: bool = False) -> tuple[bool, str]:
    """Wyślij leadowi SMS-a i zapisz to w jego historii. `(czy poszło, powód)`.

    Wysyłka kosztuje i jest nieodwracalna, więc domyślnie leci RAZ na leada.
    Status potrafi chodzić tam i z powrotem („messaged" → „new" → „messaged"),
    a każde takie odbicie bez tej blokady byłoby kolejnym SMS-em do tego samego
    człowieka — z jego strony wyglądałoby to jak natręctwo, z naszej jak rachunek
    bez powodu. `wymuszaj` to świadome kliknięcie admina, który wie, że pierwszy
    nie doszedł.

    Zdarzenie dopisujemy TYLKO po udanej wysyłce i tą samą transakcją co resztę
    zmiany: historia leada ma mówić, co się stało, a nie co próbowaliśmy zrobić.
    """
    if not wymuszaj and session.query(LeadEvent).filter(
            LeadEvent.lead_id == lead.id, LeadEvent.kind == "sms").first():
        return False, "SMS already went out to this lead"
    tresc = sms.tresc(lead.name, zakwalifikowany=lead.outcome != "not_qualified")
    poszlo, powod = sms.wyslij(lead.phone, tresc)
    if poszlo:
        _zdarzenie(session, lead.id, "sms", tresc, actor)
    return poszlo, powod


def _mail_do_leada(session, lead: Lead, actor: str, *,
                   wymuszaj: bool = False) -> tuple[bool, str]:
    """Wyślij leadowi maila i zapisz w historii, CO poszło. `(czy poszło, powód)`.

    Blokada „raz na leada" jest tu z innego powodu niż przy SMS-ie. Mail nie
    kosztuje, więc nie chroni rachunku — chroni jedyną rzecz, którą ten mail ma
    do sprzedania. Ten sam tekst drugi raz w tej samej skrzynce czyta się jak
    automat i unieważnia zdanie o tym, że aplikację czytał człowiek.

    W historii ląduje temat ORAZ pełna treść. `detail` jest ucięty do 200 znaków
    i mail się w nim nie mieści, a dział po tygodniu musi móc odtworzyć, co
    dokładnie ten człowiek przeczytał — inaczej odpowiedź „tak, jak pisaliście"
    nie ma do czego się odnieść.
    """
    if not wymuszaj and session.query(LeadEvent).filter(
            LeadEvent.lead_id == lead.id, LeadEvent.kind == "email").first():
        return False, "E-mail already went out to this lead"
    temat, tekst = lead_mail.tresc(
        lead.name, zakwalifikowany=lead.outcome != "not_qualified")
    poszlo, powod = lead_mail.wyslij(lead.email, temat, tekst)
    if poszlo:
        _zdarzenie(session, lead.id, "email", temat, actor,
                   payload=json.dumps({"body": tekst}, ensure_ascii=False))
    return poszlo, powod


def _kontakt_zastepczy(session, lead: Lead, actor: str) -> str:
    """Lead bez handle'a: SMS, a gdy nie ma czym — mail. Zwraca dopisek do dymka.

    Kolejność wynika z tego, co realnie dowozi: SMS czyta się w minutę, mail
    bywa otwarty wieczorem albo wcale. Ale mail ma dokąd pójść ZAWSZE — adres
    jest polem wymaganym formularza, numer i handle nie są — więc to on stoi
    między leadem a statusem „napisaliśmy", pod którym nikt nie napisał.

    Cisza, gdy żaden kanał nie jest skonfigurowany: klikający nie ma wtedy
    czego naprawić, a komunikat o brakującym kluczu przy każdym kliknięciu
    uczy tylko tego, żeby przestać czytać dymki.

    Numer, którego nie da się użyć, i tak przechodzi przez `_sms_do_leada` —
    Twilio nie zobaczy go, bo `sms.wyslij` odrzuca go u siebie, a powód („brak
    formy +…") wraca do klikającego. Pominięcie tej próby po cichu zabrałoby
    jedyne miejsce, w którym ktokolwiek dowie się, że numer w bazie jest zepsuty.
    """
    powody = []
    if sms.is_enabled() and (lead.phone or "").strip():
        poszlo, powod = _sms_do_leada(session, lead, actor)
        if poszlo:
            return "SMS poszedł"
        powody.append(powod)
    if lead_mail.is_enabled():
        poszlo, powod = _mail_do_leada(session, lead, actor)
        if poszlo:
            return "mail poszedł"
        powody.append(powod)
    return " · ".join(powody)


def _lead_push(lead_id: int, title: str, body: str = "", *,
               event: str = "lead_action") -> None:
    """Web push + dzwonek panelu do wszystkich adminów o TYM leadzie.

    `event` to kategoria preferencji (lead_new / lead_action / lead_reminder) —
    każdy admin wycisza kategorie w Settings → Notifications; wpis w dzwonku
    zostaje zawsze. Deep-link prowadzi prosto w kartę leada (panel czyta
    `?lead=` przy starcie), a tag skleja serię zdarzeń jednego leada w jedno
    powiadomienie na ekranie telefonu. notify_admins nigdy nie rzuca, a w
    trakcie requestu odkłada wysyłkę na po odpowiedzi — wolno to wołać zewsząd."""
    notify.notify_admins(event, title, body,
                         url=f"/admin?lead={lead_id}", tag=f"lead-{lead_id}")


def _ustaw_telefon(lead: Lead, telefon: str, iso_landing: str | None) -> None:
    """Telefon w E.164 bez spacji, a ISO uzupełnione/poprawione z prefiksu.

    Landing przysyła „+48 601 234 567" i osobno ISO zgadnięte ze strefy czasowej
    urządzenia — gdy się kłócą, prawdę mówi prefiks, bo to jego ktoś wybierze
    numerem. Numer bez prefiksu zostaje jak przyszedł: nie zgadujemy kraju,
    którego numer sam nie deklaruje.
    """
    telefon = (telefon or "").strip()
    cyfry = "".join(c for c in telefon if c.isdigit())
    if telefon.startswith("+") or cyfry.startswith("00"):
        lead.phone = ("+" + (cyfry[2:] if cyfry.startswith("00") else cyfry))[:40]
    else:
        lead.phone = telefon[:40] or None
    lead.phone_iso = countries.iso_from_e164(telefon) or (
        (iso_landing or "").strip().upper()[:2] or None)


@app.post("/api/leads/ingest")
def leads_ingest(payload: LeadIn,
                 x_lead_token: str | None = Header(default=None)):
    """Przyjmuje zgłoszenie z landingu. Jeden wiersz na maila.

    Ponowne zgłoszenie NADPISUJE dane kontaktowe i podbija licznik, ale nie
    dotyka `status`, `note` ani `contacted_at` — to praca działu i formularz
    wypełniony drugi raz nie ma prawa jej skasować.
    """
    _lead_wymaga_tokenu(x_lead_token)

    dane = payload.model_dump()
    email = (payload.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email")
    ocena = payload.quality or {}

    session = SessionLocal()
    try:
        def wpisz(lead: Lead) -> None:
            lead.name = (payload.name or "")[:120]
            _ustaw_telefon(lead, payload.phone or "", payload.phoneIso)
            lead.telegram = (payload.telegram or None)
            lead.country = (payload.country or None)
            lead.source = (payload.source or "")[:40]
            lead.ref = (payload.ref or None)
            lead.outcome = "not_qualified" if payload.outcome == "not_qualified" else "qualified"
            lead.tier = ocena.get("tier") or None
            lead.score = int(ocena.get("score") or 0)
            lead.payload_json = json.dumps(dane, ensure_ascii=False)[:20000]
            lead.updated_at = datetime.now(timezone.utc)

        lead = session.query(Lead).filter(Lead.email == email).one_or_none()
        nowy = lead is None
        if nowy:
            lead = Lead(email=email)
            session.add(lead)
        else:
            lead.applications = (lead.applications or 1) + 1
        wpisz(lead)

        # flush, bo nowy lead dostaje `id` dopiero z bazy, a zdarzenie musi je znać.
        try:
            session.flush()
        except IntegrityError:
            # Wyścig o UNIQUE(email): ten sam mail wjechał równolegle drugim
            # requestem, oba odczyty zobaczyły pustkę i oba próbują INSERT.
            # Przegrany dostawał 500, a landing przekazuje leada bez ponowienia,
            # więc zgłoszenie przepadało. To jest po prostu drugie zgłoszenie
            # tego człowieka i ma się zachować jak każde ponowne.
            session.rollback()
            lead = session.query(Lead).filter(Lead.email == email).one()
            nowy = False
            lead.applications = (lead.applications or 1) + 1
            wpisz(lead)
            session.flush()
        # Migawka TEGO zgłoszenia. `lead.payload_json` za chwilę nadpisze kolejne
        # i bez tej kopii nie dałoby się porównać, co człowiek odpowiadał wtedy,
        # a co teraz — a to jest cała treść tego, że zgłosił się drugi raz.
        # Opis po angielsku, bo `detail` idzie wprost na ekran panelu, a panel
        # jest po angielsku. Pozostałe rodzaje zdarzeń niosą klucze („new →
        # called", „bought") i to panel tłumaczy je na słowa.
        _zdarzenie(session, lead.id, "applied",
                   f"application #{lead.applications} — {lead.outcome}"
                   + (f", {lead.tier} {lead.score}" if lead.tier else ""),
                   actor="landing", payload=lead.payload_json)

        session.commit()
        lead_id, tekst = lead.id, _tekst_alertu(lead, dane)
        czat = telegram.lead_chat_id(lead.source)
        # Atrybuty do pusha czytane PRZED close(): potem instancja jest odpięta.
        kto = lead.name or lead.email
        if lead.outcome == "not_qualified":
            opis = "safe page lead — warm up" if lead.source == "safe" \
                else "failed the questionnaire"
        else:
            opis = f"{lead.tier or '—'} {lead.score or 0}/9 · {lead.source or '—'}"
        if lead.applications and lead.applications > 1:
            opis += f" · applied {lead.applications}×"
    finally:
        session.close()

    # Po commicie i best-effort: lead jest już zapisany, więc padnięty Telegram
    # nie ma prawa zwrócić landingowi błędu i kazać człowiekowi klikać drugi raz.
    # Push do adminów tak samo — telefon działu ma zabrzęczeć, ale landing nie
    # może czekać na push service ani oglądać jego błędów.
    _lead_push(lead_id, f"New lead: {kto}", opis, event="lead_new")
    _, powod, message_id = telegram.send_lead_alert(lead_id, tekst, chat_id=czat)
    _zapamietaj_wysylke(lead_id, message_id, czat or None, powod)
    return {"ok": True, "id": lead_id, "new": nowy}


def _zapamietaj_wysylke(lead_id: int, message_id: int | None,
                        chat_id: str | None, powod: str = "") -> None:
    """Osobny, króciutki zapis po wysyłce karty. Także wtedy, gdy nie poszła.

    Sukcesu nie da się zapisać w tamtej transakcji, bo id wiadomości powstaje
    dopiero w Telegramie — a bez niego odpowiedź na alert nie ma jak trafić do
    tego leada.

    Porażka jest tu z innego powodu. `send_lead_alert` zwraca powód odmowy
    wprost od Telegrama („chat not found", „bot is not a member of the group
    chat") i dotąd był on wyrzucany. Bez niego „karty nie wychodzą" wygląda
    w bazie identycznie jak „leadów nie ma" — a to dwie różne awarie i dwa
    różne telefony do wykonania. Pusty `tg_message_id` jest przy okazji
    kolejką dosyłek, którą przegląda `_lead_followups`.

    Wyłączony Telegram to nie awaria: bez `lead_alerts_on` każdy lead dostawałby
    zdarzenie o nieudanej karcie, choć nikt żadnej nie zamawiał.
    """
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            return
        if message_id:
            lead.tg_message_id = message_id
            lead.tg_chat_id = chat_id
        elif telegram.lead_alerts_on(chat_id):
            _zdarzenie(session, lead_id, "delivery",
                       f"telegram card failed: {powod or 'unknown'}", actor="system")
        session.commit()
    finally:
        session.close()


@app.get("/api/admin/leads", dependencies=[Depends(auth.require_admin)])
def admin_leads(status: str | None = None, q: str | None = None):
    """Lista leadów z doliczonym „czy kupił".

    Zapytania zbiorcze zamiast jednego na lead: leady, pasujący traderzy po
    mailu, suma zapłaconych zamówień, liczba kont. Przy setce leadów pętla
    z osobnym SELECT-em na każdego byłaby niezauważalna, przy tysiącu już nie.
    """
    session = SessionLocal()
    try:
        query = session.query(Lead)
        if status:
            query = query.filter(Lead.status == status)
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.filter(func.lower(Lead.email).like(like) |
                                 func.lower(Lead.name).like(like))
        leady = query.order_by(Lead.created_at.desc()).all()

        maile = [l.email for l in leady]
        traderzy: dict[str, int] = {}
        zaplacone: dict[int, float] = {}
        konta: dict[int, int] = {}
        if maile:
            traderzy = {e.lower(): i for i, e in
                        session.query(Trader.id, Trader.email)
                        .filter(func.lower(Trader.email).in_(maile)).all()}
            if traderzy:
                idki = list(traderzy.values())
                zaplacone = dict(session.query(Order.trader_id, func.sum(Order.amount_usd))
                                 .filter(Order.trader_id.in_(idki),
                                         Order.status == "paid")
                                 .group_by(Order.trader_id).all())
                # Tylko konta WRĘCZONE: panel gasi na tym przycisk „Free
                # account", a wiersz z ewidencji wypłat nie jest niczym, co ten
                # człowiek od nas dostał — jest tym, co my zapłaciliśmy jemu.
                konta = dict(session.query(Account.trader_id, func.count(Account.id))
                             .filter(Account.trader_id.in_(idki), _konto_wreczone())
                             .group_by(Account.trader_id).all())

        # Najbliższy otwarty termin per lead. Wiersz musi krzyczeć „na dziś",
        # bo inaczej przypomnienie widać dopiero po otwarciu karty — czyli
        # wtedy, gdy i tak się o nim pamiętało.
        terminy: dict[int, datetime] = {}
        for lid, kiedy in (session.query(LeadReminder.lead_id, LeadReminder.due_at)
                           .filter(LeadReminder.active.is_(True)).all()):
            if lid not in terminy or kiedy < terminy[lid]:
                terminy[lid] = kiedy

        wynik = []
        for l in leady:
            trader_id = traderzy.get(l.email)
            wynik.append(_lead_json(l, trader_id, zaplacone.get(trader_id, 0) or 0,
                                    terminy.get(l.id), konta.get(trader_id, 0)))
        return wynik
    finally:
        session.close()


def _komorka_csv(wartosc) -> str:
    """Jedna komórka, bezpieczna do otwarcia w arkuszu.

    Wartość zaczynająca się od `=`, `+`, `-` albo `@` nie jest dla Excela ani
    Arkuszy tekstem, tylko formułą. Notatka „=1+1" policzyłaby się zamiast
    pokazać, a `=HYPERLINK(...)` to już coś, co klika cudza ręka na cudzym
    komputerze. Apostrof z przodu zdejmuje im to znaczenie i sam w komórce nie
    widać. Płaci za to numer telefonu (`+48…`), który dostaje go bez potrzeby —
    tańsze niż lista wyjątków przy polu wpisywanym z ulicy.
    """
    tekst = "" if wartosc is None else str(wartosc)
    return "'" + tekst if tekst[:1] in ("=", "+", "-", "@") else tekst


def _data_csv(iso: str | None) -> str:
    """Data po warszawsku. W bazie zostaje UTC, ale plik czyta człowiek, który
    zestawia go z godziną rozmowy na Telegramie."""
    if not iso:
        return ""
    return (_utc(datetime.fromisoformat(iso)).astimezone(_WARSZAWA)
            .strftime("%Y-%m-%d %H:%M"))


# Nagłówek i wartość w jednej parze, więc kolumna nie ma jak rozjechać się
# z opisem przy dokładaniu następnej. Czytane z `_lead_json`, nie z modelu —
# ten sam słownik karmi listę w panelu, a plik ma mówić dokładnie to, co ekran.
_KOLUMNY_CSV = (
    ("created", lambda l: _data_csv(l["created_at"])),
    ("name", lambda l: l["name"]),
    ("email", lambda l: l["email"]),
    ("phone", lambda l: l["phone"]),
    ("telegram", lambda l: l["telegram"]),
    ("country", lambda l: l["country"]),
    ("source", lambda l: l["source"]),
    ("ref", lambda l: l["ref"]),
    ("utm_source", lambda l: l["campaign"].get("utm_source")),
    ("utm_medium", lambda l: l["campaign"].get("utm_medium")),
    ("utm_campaign", lambda l: l["campaign"].get("utm_campaign")),
    ("utm_content", lambda l: l["campaign"].get("utm_content")),
    # Jedna kolumna na trzy sieci: lead przychodzi z jednej reklamy, więc dwa
    # identyfikatory naraz się nie zdarzają, a trzy puste kolumny w każdym
    # wierszu spychałyby notatkę poza ekran.
    ("click_id", lambda l: next((v for k, v in l["campaign"].items()
                                 if k.endswith("clid")), "")),
    ("status", lambda l: l["status"]),
    ("lost_reason", lambda l: l["lost_reason"]),
    ("owner", lambda l: l["owner"]),
    ("tier", lambda l: l["tier"]),
    ("score", lambda l: l["score"]),
    ("outcome", lambda l: l["outcome"]),
    ("applications", lambda l: l["applications"]),
    ("paid_usd", lambda l: l["paid_usd"]),
    ("contacted", lambda l: _data_csv(l["contacted_at"])),
    ("note", lambda l: l["note"]),
)


@app.get("/api/admin/leads.csv", dependencies=[Depends(auth.require_admin)])
def admin_leads_csv():
    """Wszystkie leady do arkusza, razem z koszem.

    Bez filtrów, choć panel je ma: jego chipy i szukajka pracują na już
    pobranej liście i sięgają dalej niż `status`/`q` po stronie serwera, więc
    przepisanie ich tutaj dałoby plik węższy albo szerszy niż ekran, z którego
    się go pobiera. Arkusz filtruje lepiej niż panel — a raport „dlaczego
    przegrywamy" potrzebuje właśnie tych wierszy, które w panelu leżą w koszu.
    """
    leady = admin_leads()
    bufor = io.StringIO()
    zapis = csv.writer(bufor)
    zapis.writerow([nazwa for nazwa, _ in _KOLUMNY_CSV])
    for l in leady:
        zapis.writerow([_komorka_csv(f(l)) for _, f in _KOLUMNY_CSV])
    dzis = datetime.now(_WARSZAWA).strftime("%Y-%m-%d")
    # utf-8-sig, nie utf-8: Excel bez znacznika BOM czyta plik po windowsowemu
    # i „Zieliński" otwiera się jako „ZieliÅ„ski". Reszta świata BOM ignoruje.
    return Response(content=bufor.getvalue().encode("utf-8-sig"),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="leads-{dzis}.csv"'})


@app.get("/api/admin/leads/{lead_id}", dependencies=[Depends(auth.require_admin)])
def admin_lead_detail(lead_id: int):
    """Jeden lead z całą historią — to, czego lista pokazać nie może.

    Lista mówi, jak jest teraz. Tutaj widać, jak do tego doszło: kolejne
    zgłoszenia z odpowiedziami z tamtej chwili, zmiany statusu z podpisem, kto
    je klikał, notatki i przypomnienia wysłane działowi.
    """
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")

        trader = (session.query(Trader)
                  .filter(func.lower(Trader.email) == lead.email.lower()).one_or_none())
        zamowienia = []
        zaplacone = 0.0
        konta = 0
        if trader:
            konta = (session.query(func.count(Account.id))
                     .filter(Account.trader_id == trader.id).scalar() or 0)
            for o in (session.query(Order).filter(Order.trader_id == trader.id)
                      .order_by(Order.created_at.desc()).all()):
                if o.status == "paid":
                    zaplacone += float(o.amount_usd or 0)
                zamowienia.append({
                    "id": o.id, "product_key": o.product_key,
                    "amount_usd": round(float(o.amount_usd or 0), 2), "status": o.status,
                    "bogo": bool(getattr(o, "bogo", False)),
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                    "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                })

        zdarzenia = (session.query(LeadEvent).filter(LeadEvent.lead_id == lead.id)
                     .order_by(LeadEvent.created_at.desc(), LeadEvent.id.desc()).all())

        przypomnienia = (session.query(LeadReminder)
                         .filter(LeadReminder.lead_id == lead.id)
                         .order_by(LeadReminder.active.desc(), LeadReminder.due_at).all())
        otwarte = [r.due_at for r in przypomnienia if r.active]

        dane = _lead_json(lead, trader.id if trader else None, zaplacone,
                          min(otwarte) if otwarte else None, konta)
        # Tylko w szczegółach (jak orders/events): szuflada pokazuje przycisk
        # „wyślij zaproszenie do portalu" wyłącznie tam, gdzie ma to sens —
        # konto założone ZA klienta, który hasła jeszcze nie ustawił.
        dane["must_set_password"] = bool(trader.must_set_password) if trader else False
        # Przy pozostałych stanach przycisku nie ma i dział nie wiedział dlaczego
        # („czemu nie mogę mu wysłać zaproszenia?"). Stan mówi to wprost.
        dane["portal_state"] = ("none" if not trader
                                else "awaiting" if trader.must_set_password
                                else "google" if trader.google_sub
                                else "password")
        dane["reminders"] = [{
            "id": r.id, "text": r.text, "kind": r.kind, "active": r.active,
            "repeat_days": r.repeat_days, "sent_count": r.sent_count,
            "due_at": r.due_at.isoformat() if r.due_at else None,
            "last_sent_at": r.last_sent_at.isoformat() if r.last_sent_at else None,
            "created_by": r.created_by,
        } for r in przypomnienia]
        dane["orders"] = zamowienia
        dane["events"] = [{
            "id": z.id, "kind": z.kind, "detail": z.detail, "actor": z.actor,
            # Migawka zgłoszenia jest w bazie jednym stringiem; panel dostaje
            # gotowe pary pytanie/odpowiedź, bo tylko to z niej czyta.
            "answers": _answers_z_payloadu(z.payload_json),
            # Pełna treść wysłanego maila. `detail` niesie sam temat, bo jest
            # ucięty do 200 znaków — a pytanie „co dokładnie do niego poszło"
            # pada tydzień później i musi mieć odpowiedź.
            "body": _tresc_z_payloadu(z.payload_json),
            "created_at": z.created_at.isoformat() if z.created_at else None,
        } for z in zdarzenia]
        return dane
    finally:
        session.close()


class LeadManualIn(BaseModel):
    email: str
    name: str = ""
    phone: str | None = None
    telegram: str | None = None
    country: str | None = None
    note: str | None = None


@app.post("/api/admin/leads", dependencies=[Depends(auth.require_admin)])
def admin_lead_create(payload: LeadManualIn):
    """Lead wpisany z ręki — ktoś napisał na Telegramie prosto z reklamy.

    Bez tego taki człowiek nie istniał nigdzie w panelu: zamówienie dało się
    wystawić klientowi z listy kont albo leadowi z jego karty, a on nie był ani
    jednym, ani drugim.

    `source` to „manual" i to nie jest kosmetyka: gdyby ci ludzie mieli źródło
    puste albo takie samo jak formularz, konwersja landingu zaczęłaby liczyć
    zgłoszenia, których landing nigdy nie widział.

    Ankiety nie ma, więc nie ma też oceny — `tier` i `score` zostają puste
    zamiast dostać zmyśloną wartość. Karta na Telegramie idzie tak samo jak
    przy zgłoszeniu z landingu: dział pracuje na kanale i lead bez karty byłby
    jedynym, którego nie da się tam obsłużyć.
    """
    email = (payload.email or "").strip().lower()
    if not _EMAIL_RX.fullmatch(email):
        raise HTTPException(400, "Enter a valid e-mail address")

    session = SessionLocal()
    try:
        # `leads.email` jest UNIQUE, więc druga próba i tak skończyłaby się
        # błędem bazy. Zamiast tego oddajemy istniejącego leada — panel otworzy
        # jego kartę. Nic tu nie nadpisujemy: wpisujący nie wie, co dział zdążył
        # już ustalić, a jego trzy pola z formularza nie mają prawa zetrzeć
        # notatki i statusu.
        stary = session.query(Lead).filter(Lead.email == email).one_or_none()
        if stary:
            return {"id": stary.id, "existing": True, "owner": stary.owner,
                    "status": stary.status, "name": stary.name}

        lead = Lead(email=email, name=(payload.name or "")[:120],
                    source="manual", outcome="qualified",
                    note=(payload.note or "").strip()[:4000] or None)
        _ustaw_telefon(lead, payload.phone or "", None)
        lead.telegram = (payload.telegram or "").strip() or None
        lead.country = (payload.country or "").strip() or None
        session.add(lead)
        session.flush()
        _zdarzenie(session, lead.id, "applied", "added by hand", actor="panel")
        session.commit()
        lead_id, kto = lead.id, (lead.name or lead.email)
        tekst = _tekst_alertu(lead, {})
        czat = telegram.lead_chat_id(lead.source)
    finally:
        session.close()

    # Best-effort po commicie, dokładnie jak przy ingeście: padnięty Telegram
    # nie może cofnąć zapisanego leada ani kazać wpisywać go drugi raz.
    _lead_push(lead_id, f"New lead: {kto}", "added by hand", event="lead_new")
    _, powod, message_id = telegram.send_lead_alert(lead_id, tekst, chat_id=czat)
    _zapamietaj_wysylke(lead_id, message_id, czat or None, powod)
    return {"id": lead_id, "existing": False}


class LeadStatusIn(BaseModel):
    status: str | None = None
    note: str | None = None
    # Pusty string = zdejmij właściciela. `None` = nie ruszaj — inaczej zapis
    # samej notatki kasowałby to, kto się leadem zajmuje.
    owner: str | None = None
    tier: str | None = None
    # Powód przegranej. Pusty string = zdejmij, `None` = nie ruszaj — ta sama
    # umowa co przy `owner`. Serwer NIE wymaga powodu przy odrzuceniu, choć
    # panel wymaga: ten sam status ustawia jednym kliknięciem przycisk na
    # kanale, a wymuszanie tam drugiego kliknięcia kosztowałoby zapisany status
    # za każdym razem, gdy ktoś odejdzie od telefonu w połowie.
    lost_reason: str | None = None
    # Ręczne „kupił" (checkbox w tabeli) — zakupy zawarte poza sklepem.
    bought: bool | None = None


@app.post("/api/admin/leads/{lead_id}", dependencies=[Depends(auth.require_admin)])
def admin_lead_update(lead_id: int, payload: LeadStatusIn):
    """Zmiana statusu, notatki, właściciela i/lub oceny. Pominięte pole zostaje
    bez zmian — zapis notatki nie ma prawa cofnąć statusu ustawionego z telefonu."""
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        # Push idzie tylko o statusie i właścicielu — to jest „co robimy z tym
        # leadem". Notatka i ocena zmieniają się przy pisaniu na bieżąco i jako
        # push byłyby szumem, który uczy ignorować powiadomienia.
        zmiany: list[str] = []
        if payload.status is not None:
            przed = lead.status
            # actor „panel", bez nazwiska: do panelu wchodzi się stałym tokenem
            # (auth.require_admin), więc nie ma tu tożsamości do zapisania.
            _zapisz_status(session, lead, payload.status, actor="panel")
            if lead.status != przed:
                zmiany.append(f"marked {lead.status}")
        if payload.note is not None:
            nowa = payload.note[:4000] or None
            if nowa != lead.note:
                lead.note = nowa
                lead.updated_at = datetime.now(timezone.utc)
                _zdarzenie(session, lead.id, "note", nowa or "(skasowano)", actor="panel")
        if payload.owner is not None:
            nowy = payload.owner.strip()[:60] or None
            if nowy != lead.owner:
                lead.owner = nowy
                lead.owner_at = datetime.now(timezone.utc) if nowy else None
                lead.updated_at = datetime.now(timezone.utc)
                _zdarzenie(session, lead.id, "claim",
                           f"taken by {nowy}" if nowy else "released", actor="panel")
                zmiany.append(f"taken by {nowy}" if nowy else "released")
        if payload.tier is not None:
            if payload.tier not in ("high", "warm", "cold"):
                raise HTTPException(400, f"Unknown tier: {payload.tier}")
            if payload.tier != lead.tier:
                _zdarzenie(session, lead.id, "tier",
                           f"{lead.tier or '—'} → {payload.tier}", actor="panel")
                lead.tier = payload.tier
                lead.updated_at = datetime.now(timezone.utc)
        # Po `_zapisz_status`, i to jest tu istotne: tamten czyści powód przy
        # powrocie leada do gry, więc odwrotna kolejność kasowałaby powód
        # przysłany w tym samym żądaniu co odrzucenie.
        if payload.lost_reason is not None:
            powod = payload.lost_reason.strip().lower() or None
            if powod and powod not in LOST_REASONS:
                raise HTTPException(400, f"Unknown lost reason: {powod}")
            if powod and lead.status not in LEAD_LOST_STATUSES:
                raise HTTPException(
                    400, "A lost reason only belongs to a rejected or burned lead")
            if powod != lead.lost_reason:
                lead.lost_reason = powod
                lead.updated_at = datetime.now(timezone.utc)
                _zdarzenie(session, lead.id, "status",
                           f"lost: {powod}" if powod else "lost reason cleared",
                           actor="panel")
        if payload.bought is not None and bool(payload.bought) != bool(lead.bought):
            lead.bought = bool(payload.bought)
            lead.updated_at = datetime.now(timezone.utc)
            _zdarzenie(session, lead.id, "bought",
                       "marked bought" if lead.bought else "unmarked bought",
                       actor="panel")
            zmiany.append("marked bought" if lead.bought else "unmarked bought")
        session.commit()
        kto = lead.name or lead.email
        wynik = {"id": lead.id, "status": lead.status, "note": lead.note,
                 "owner": lead.owner, "tier": lead.tier, "bought": bool(lead.bought),
                 "lost_reason": lead.lost_reason,
                 "contacted_at": lead.contacted_at.isoformat() if lead.contacted_at else None}
    finally:
        session.close()
    if zmiany:
        _lead_push(lead_id, f"Panel: {', '.join(zmiany)}", kto)
    return wynik


@app.delete("/api/admin/leads/{lead_id}", dependencies=[Depends(auth.require_admin)])
def admin_lead_delete(lead_id: int):
    """Skasowanie leada razem z jego historią i przypomnieniami.

    Kasujemy naprawdę, bez kolumny „ukryty". Powód jest ten sam, dla którego ta
    funkcja w ogóle powstała: w tabeli siedzą zgłoszenia testowe i pomyłki, a
    ukryty wiersz dalej blokowałby mail — `leads.email` jest unikalny, więc
    człowiek, którego adresem ktoś testował formularz, nie mógłby się zapisać.

    Zamówienia i konto zostają nietknięte. To są osobne tabele powiązane mailem,
    nie kluczem, i tak ma być: kasujemy notatkę działu o człowieku, a nie jego
    historię płatności.
    """
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        mail, karta_mid, karta_czat = lead.email, lead.tg_message_id, lead.tg_chat_id
        # Dzieci najpierw — `lead_events.lead_id` i `lead_reminders.lead_id` to
        # klucze obce, więc Postgres odrzuciłby skasowanie rodzica.
        session.query(LeadReminder).filter(LeadReminder.lead_id == lead_id).delete()
        session.query(LeadEvent).filter(LeadEvent.lead_id == lead_id).delete()
        session.delete(lead)
        session.commit()
    finally:
        session.close()
    # Karta z kanału schodzi razem z leadem — best-effort PO commicie: wpis
    # testowy znikał z bazy, a jego karta wisiała na czacie jak sierota i dalej
    # dawała się klikać (w lead, którego już nie było). Padnięty Telegram nie
    # cofa kasowania.
    if karta_mid:
        telegram.delete_lead_card(karta_mid, chat_id=karta_czat)
    print(f"[leads] usunieto lead #{lead_id} ({mail})")
    return {"ok": True, "id": lead_id}


# Tyle obiecuje landing na /freeaccount i tylko to wolno stąd przyznać.
# Produkt NIE jest parametrem: ten przycisk jest po to, żeby dział nie musiał
# niczego wybierać, a pole w żądaniu prędzej czy później rozdałoby stutysięczne
# konta za darmo.
FREE_CHALLENGE_KEY = "2step-25k"


def _darmowe_konto_od_reki(session, acc: Account, trader: Trader) -> None:
    """Darmowe konto jest gotowe od kliknięcia — nie czeka na rachunek z puli.

    Przy `MT5_PROVISIONING=true` konto z zakupu ląduje w statusie `provisioning`
    i wisi tam, aż poller weźmie dla niego rachunek z puli MT5; dopiero wtedy
    wychodzi mail z poświadczeniami (`provisioning.py`, gałęzie 2-4). Dla
    darmowego challenge'a to była cisza w obie strony: lead nie dostawał nic,
    a pusta pula zatrzymywała go tam na zawsze. Za to konto nikt nie zapłacił,
    więc rachunek z puli mu się nie należy — dostaje poświadczenia wygenerowane
    u nas i maila od razu, w tym samym kliknięciu.

    Bot rusza WYŁĄCZNIE tutaj. Darmowe konto jest prezentem dla kogoś, kto nas
    jeszcze nie zna: ma na nim być ruch od pierwszej minuty, a nie pusty wykres.
    Konta kupione zostają przy decyzji właściciela.
    """
    if acc.status == "provisioning":
        provisioning._apply_local_credentials(acc)
        acc.status = "funded" if acc.phase == "funded" else "active"
        session.commit()
        notify.send(provisioning._creds_event(acc), trader.email,
                    provisioning._creds_ctx(trader, acc))
    tradebot.start(session, acc)


@app.post("/api/admin/leads/{lead_id}/free-account",
          dependencies=[Depends(auth.require_admin)])
def admin_lead_free_account(lead_id: int):
    """Darmowy challenge dla leada z /freeaccount — jednym kliknięciem z panelu.

    Konto idzie tą samą ścieżką co zakup (`billing.grant_challenge` →
    `provisioning.create_account_from_order`), a `_darmowe_konto_od_reki`
    dokłada to, czym darmowy challenge różni się od kupionego: poświadczenia
    od ręki zamiast kolejki po rachunek z puli, mail `challenge_granted`
    z linkiem do ustawienia hasła i włączony bot. Trader zakłada się po drodze,
    jeśli leada jeszcze nie ma w bazie klientów.

    Drugie kliknięcie odmawia (409). Panel wprawdzie chowa przycisk, gdy konto
    już jest, ale dwa kliknięcia w tę samą sekundę widzą jeszcze stary stan —
    a to jest koszt, nie literówka. Konta archiwalne z ewidencji wypłat guard
    pomija (`_konto_wreczone`) — one nie są niczym, co ten człowiek dostał.
    """
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        trader, nowy = _trader_po_mailu(session, lead.email)
        istniejace = (session.query(Account)
                      .filter(Account.trader_id == trader.id,
                              Account.source == "grant", _konto_wreczone()).first())
        if istniejace:
            raise HTTPException(409, f"Already granted — account {istniejace.login}")
        res = billing.grant_challenge(session, trader, FREE_CHALLENGE_KEY,
                                      notify.FREE_PROGRAM_NOTE)
        # Login czytamy z konta, nie z `res`: uzbrojenie nadaje świeży numer
        # rachunku, więc numer sprzed niego trafiłby i do dziennika, i do panelu.
        acc = session.get(Account, res["account_id"])
        _darmowe_konto_od_reki(session, acc, trader)
        _zdarzenie(session, lead.id, "granted",
                   f"{FREE_CHALLENGE_KEY} — login {acc.login}", actor="panel")
        session.commit()
        # Weryfikacja idzie w tym samym kliknięciu, co prezent: portal zostaje
        # wstrzymany do akceptacji dokumentów. Inaczej ta sama zaległość rosłaby
        # od nowa i za miesiąc znów trzeba by ją nadrabiać hurtem.
        if _czeka_na_prosbe(trader):
            _popros_o_kyc(session, trader, wstrzymaj_portal=True)
        return {"granted": True, "trader_created": nowy,
                "login": acc.login, "account_id": acc.id}
    finally:
        session.close()


@app.post("/api/admin/leads/{lead_id}/sms", dependencies=[Depends(auth.require_admin)])
def admin_lead_sms(lead_id: int, force: bool = False):
    """SMS do leada, z ręki — dla tych, do których Telegram nie dochodzi.

    Treści nie przyjmujemy z panelu, choć byłoby wygodniej: ta sama wiadomość
    wychodzi też automatem po kliknięciu statusu na kanale, a dwie kopie tekstu
    rozjeżdżają się przy pierwszej poprawce. Wersję dla przyjętego i odrzutu
    wybiera serwer z `outcome` — panel nie ma prawa się co do tego pomylić.

    Odmowa wraca jako 400 z powodem wprost od Twilio. To jedyne miejsce, gdzie
    admin dowie się, że numer jest bez kierunkowego albo że konto nie ma środków.

    Wysłany SMS to kontakt, więc lead przestaje być „new" TĄ SAMĄ transakcją —
    nie osobnym strzałem z przeglądarki, który może nie dojść. Lead bez śladu
    kontaktu to lead, do którego za tydzień ktoś napisze drugi raz. Dalsze
    statusy zostają nietknięte: cofanie „replied" do „napisano" byłoby cofaniem
    prawdy o rozmowie, która już się odbyła.
    """
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        poszlo, powod = _sms_do_leada(session, lead, "panel", wymuszaj=force)
        if not poszlo:
            raise HTTPException(400, powod)
        if lead.status == "new":
            _zapisz_status(session, lead, "messaged", actor="panel")
        session.commit()
        return {"ok": True, "id": lead_id, "status": lead.status}
    finally:
        session.close()


@app.post("/api/admin/leads/{lead_id}/email", dependencies=[Depends(auth.require_admin)])
def admin_lead_email(lead_id: int, force: bool = False):
    """Mail do leada, z ręki — dla tych, do których nie ma ani Telegrama, ani numeru.

    Treści, tak jak przy SMS-ie, nie przyjmujemy z panelu: ta sama wiadomość
    wychodzi automatem po kliknięciu statusu na kanale, a dwie kopie tekstu
    rozjeżdżają się przy pierwszej poprawce. Wersję dla przyjętego i dla odrzutu
    wybiera serwer z `outcome`.

    Wysłany mail to kontakt, więc lead przestaje być „new" TĄ SAMĄ transakcją,
    dokładnie jak przy SMS-ie. Dalsze statusy zostają nietknięte — cofanie
    „replied" byłoby cofaniem prawdy o rozmowie, która już się odbyła.
    """
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        poszlo, powod = _mail_do_leada(session, lead, "panel", wymuszaj=force)
        if not poszlo:
            raise HTTPException(400, powod)
        if lead.status == "new":
            _zapisz_status(session, lead, "messaged", actor="panel")
        session.commit()
        return {"ok": True, "id": lead_id, "status": lead.status}
    finally:
        session.close()


class LeadMailIn(BaseModel):
    subject: str
    body: str


@app.post("/api/admin/leads/{lead_id}/email-custom",
          dependencies=[Depends(auth.require_admin)])
def admin_lead_email_custom(lead_id: int, dane: LeadMailIn):
    """Mail do leada z treścią wpisaną w panelu — temat i tekst od człowieka.

    Osobny endpoint, a nie parametr przy `/email`, bo tamta droga ma odwrotny
    kontrakt: treść składa serwer z `outcome` i panel nie ma prawa jej podmienić.
    Tu jest na odwrót — treść JEST wolą klikającego, serwer tylko ubiera ją
    w papier firmowy marki (`_html_z_tekstu`) i pilnuje, żeby w historii leada
    został dokładnie ten tekst, który wyszedł.

    Bez blokady „raz na leada": tamta chroni przed DRUGĄ KOPIĄ tego samego
    automatu, a tu każdy mail admin pisze (i widzi w podglądzie) sam — powtórka
    jest jego świadomą decyzją, nie odbiciem statusu.

    Temat trafia do `detail` zdarzenia, więc webhook Brevo dopasuje doręczenie
    i odbicie tak samo jak przy automacie — po temacie zapisanym przy wysyłce.
    """
    temat = " ".join(dane.subject.split())
    tekst = dane.body.strip()
    if not temat or not tekst:
        raise HTTPException(400, "Subject and message are both required")
    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        poszlo, powod = lead_mail.wyslij(lead.email, temat, tekst)
        if not poszlo:
            raise HTTPException(400, powod)
        _zdarzenie(session, lead.id, "email", temat, "panel",
                   payload=json.dumps({"body": tekst}, ensure_ascii=False))
        if lead.status == "new":
            _zapisz_status(session, lead, "messaged", actor="panel")
        session.commit()
        return {"ok": True, "id": lead_id, "status": lead.status}
    finally:
        session.close()


class MailTemplateIn(BaseModel):
    name: str
    subject: str
    body: str


def _szablon_json(t: LeadMailTemplate) -> dict:
    return {"id": t.id, "name": t.name, "subject": t.subject, "body": t.body,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None}


@app.get("/api/admin/email-templates", dependencies=[Depends(auth.require_admin)])
def admin_email_templates():
    session = SessionLocal()
    try:
        return [_szablon_json(t) for t in
                session.query(LeadMailTemplate).order_by(LeadMailTemplate.name)]
    finally:
        session.close()


@app.post("/api/admin/email-templates", dependencies=[Depends(auth.require_admin)])
def admin_email_template_save(dane: MailTemplateIn):
    """Zapis szablonu; istniejąca nazwa nadpisuje.

    Nadpisanie zamiast błędu „already exists": jedyny scenariusz, w którym ktoś
    zapisuje pod starą nazwą, to poprawka treści — i ma wtedy dostać poprawiony
    szablon, a nie listę z dwoma wpisami „follow-up" różniącymi się literówką.
    """
    nazwa = " ".join(dane.name.split())[:80]
    temat = " ".join(dane.subject.split())[:200]
    tekst = dane.body.strip()
    if not nazwa or not temat or not tekst:
        raise HTTPException(400, "Name, subject and message are all required")
    session = SessionLocal()
    try:
        t = (session.query(LeadMailTemplate)
             .filter(LeadMailTemplate.name == nazwa).one_or_none())
        if t is None:
            t = LeadMailTemplate(name=nazwa)
            session.add(t)
        t.subject, t.body = temat, tekst
        t.updated_at = datetime.now(timezone.utc)
        session.commit()
        return _szablon_json(t)
    finally:
        session.close()


@app.delete("/api/admin/email-templates/{tpl_id}",
            dependencies=[Depends(auth.require_admin)])
def admin_email_template_delete(tpl_id: int):
    session = SessionLocal()
    try:
        t = session.get(LeadMailTemplate, tpl_id)
        if not t:
            raise HTTPException(404, "Template not found")
        session.delete(t)
        session.commit()
        return {"ok": True, "id": tpl_id}
    finally:
        session.close()


@app.post("/api/admin/traders/{trader_id}/portal-invite",
          dependencies=[Depends(auth.require_admin)])
def admin_trader_portal_invite(trader_id: int, send: bool = True):
    """Zaproszenie do portalu — dla konta założonego ZA klienta.

    Pierwszy link do ustawienia hasła jedzie w mailu z poświadczeniami MT5, ale
    bywa, że klient odezwie się po tygodniu, gdy link już wygasł. Tu powstaje
    świeży 7-dniowy token; poprzedni i tak jest jednorazowy (odcisk hasła),
    więc nic nie trzeba unieważniać.

    Per trader, nie per lead: konto zakładają też granty i import wypłat,
    a taki klient wiersza leada nie ma wcale. `send=false` zwraca sam link
    BEZ maila — maile bywają w spamie albo giną (SMTP pada u nas po cichu),
    a wtedy jedyną drogą jest wkleić link klientowi na Telegramie z ręki.

    Celowo tylko dla `must_set_password`: klientowi, który już ustawił hasło,
    nie mamy prawa fundować „ustaw hasło od nowa" z panelu — od tego jest
    zwykły „forgot password" po jego stronie. Ta sama bramka broni linku:
    panel nigdy nie wygeneruje wejściówki na konto z żyjącym hasłem.
    """
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader not found")
        if not tr.must_set_password:
            raise HTTPException(400, "Client already set a password — "
                                     "point them to “Forgot password”")
        base = get_settings().app_base_url
        setup_url = (f"{base}/portal"
                     f"?reset={auth.make_setup_token(tr.id, tr.password_hash)}")
        if send:
            notify.send("portal_invite", tr.email,
                        {"name": tr.full_name or tr.email,
                         "setup_url": setup_url, "portal_url": f"{base}/portal"})
        telemetry.track("portal_invite", tr.id, sent=bool(send))
        # Ślad w historii leada, o ile lead istnieje — „czy myśmy mu to
        # wysłali?" pada tydzień później i musi mieć odpowiedź. Kopiowanie
        # też się liczy: link poszedł innym kanałem, ale poszedł.
        lead = (session.query(Lead)
                .filter(func.lower(Lead.email) == tr.email.lower()).one_or_none())
        if lead:
            _zdarzenie(session, lead.id,
                       "email" if send else "note",
                       "portal invite (set password)" if send
                       else "portal invite link copied",
                       actor="panel")
            session.commit()
        return {"ok": True, "email": tr.email, "setup_url": setup_url}
    finally:
        session.close()


class LeadReminderIn(BaseModel):
    text: str
    # Termin idzie albo w dniach od teraz (tak działają szablony w panelu),
    # albo datą z kalendarza. Dni wygrywają, bo to jedyna droga z jednym klikiem.
    due_in_days: int | None = None
    due_at: str | None = None
    repeat_days: int | None = None


@app.post("/api/admin/leads/{lead_id}/reminders",
          dependencies=[Depends(auth.require_admin)])
def admin_lead_reminder_create(lead_id: int, payload: LeadReminderIn):
    """Zaplanowanie kontaktu: „odezwij się do tego człowieka wtedy i o tym".

    Wiadomość poleci na czat DZIAŁU, nie do leada — landing, przez który
    przyszedł, jest osobną marką i mail stąd powiedziałby mu, że to jedna firma.
    """
    tresc = (payload.text or "").strip()
    if not tresc:
        raise HTTPException(400, "Reminder text is required")
    if payload.due_in_days is None and not payload.due_at:
        raise HTTPException(400, "Reminder needs a date")
    if payload.repeat_days is not None and payload.repeat_days < 1:
        raise HTTPException(400, "repeat_days must be at least 1")

    if payload.due_in_days is not None:
        kiedy = datetime.now(timezone.utc) + timedelta(days=max(0, payload.due_in_days))
    else:
        try:
            kiedy = datetime.fromisoformat(payload.due_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Invalid date")
        if kiedy.tzinfo is None:
            kiedy = kiedy.replace(tzinfo=timezone.utc)

    session = SessionLocal()
    try:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        r = LeadReminder(lead_id=lead.id, text=tresc[:500], due_at=kiedy,
                         repeat_days=payload.repeat_days, kind="manual",
                         created_by="panel")
        session.add(r)
        session.commit()
        return {"id": r.id, "due_at": r.due_at.isoformat(), "text": r.text,
                "repeat_days": r.repeat_days, "active": r.active, "sent_count": 0,
                "kind": r.kind, "created_by": r.created_by, "last_sent_at": None}
    finally:
        session.close()


@app.post("/api/admin/leads/{lead_id}/reminders/{reminder_id}/cancel",
          dependencies=[Depends(auth.require_admin)])
def admin_lead_reminder_cancel(lead_id: int, reminder_id: int):
    """Wyłączenie przypomnienia. Wiersz ZOSTAJE — przy cyklicznym widać potem,
    ile razy poszło, zanim ktoś go zamknął."""
    session = SessionLocal()
    try:
        r = session.get(LeadReminder, reminder_id)
        if not r or r.lead_id != lead_id:
            raise HTTPException(404, "Reminder not found")
        r.active = False
        session.commit()
        return {"id": r.id, "active": False}
    finally:
        session.close()


@app.post("/api/admin/leads/{lead_id}/reminders/{reminder_id}/reactivate",
          dependencies=[Depends(auth.require_admin)])
def admin_lead_reminder_reactivate(lead_id: int, reminder_id: int):
    """Odwrotność cancel — pod Undo w panelu. Cancel niczego nie kasuje, więc
    wraca TEN SAM wiersz, z licznikiem wysyłek i terminem."""
    session = SessionLocal()
    try:
        r = session.get(LeadReminder, reminder_id)
        if not r or r.lead_id != lead_id:
            raise HTTPException(404, "Reminder not found")
        r.active = True
        session.commit()
        return {"id": r.id, "active": True}
    finally:
        session.close()


# Co z tego, co przysyła dostawca poczty, w ogóle zmienia wiedzę działu.
# Otwarcia i kliknięcia świadomie POMINIĘTE: piksel śledzący kłamie w obie
# strony (klient pocztowy pobiera go bez człowieka, a wyłączone obrazki chowają
# tego, kto przeczytał), więc „otwarł" nie jest faktem, na którym można oprzeć
# rozmowę. Tu ma być tylko to, co twarde: doszło albo nie doszło.
_BREVO_ZDARZENIA = {
    "delivered": "delivered",
    "soft_bounce": "soft bounce",
    "hard_bounce": "hard bounce",
    "blocked": "blocked",
    "invalid_email": "invalid address",
    "spam": "marked as spam",
    "error": "provider error",
}


@app.post("/api/brevo/webhook")
async def brevo_webhook(request: Request, token: str = ""):
    """Co dostawca poczty zrobił z mailem do leada — prosto do jego historii.

    Bez tego panel pokazuje „E-mail sent" w chwili oddania listu do Brevo i nic
    poza tym: mail odbity od nieistniejącej skrzynki wygląda w historii tak samo
    jak doręczony, a dział czeka na odpowiedź, która nie ma prawa przyjść.

    Sekret siedzi w ADRESIE, nie w nagłówku. To nie jest wygoda — Brevo nie
    podpisuje wywołań (żadnego HMAC-a) i nie pozwala dopiąć własnego nagłówka,
    więc nieodgadywalny adres jest jedyną kontrolą, jaka istnieje. Wchodzi za to
    do logów dostępowych hostingu; przy rotacji sekretów traktować jak jawny.

    Zawsze 200, tak jak przy Telegramie: na błąd Brevo ponawia, a zdarzenia,
    którego nie umiemy przypiąć, nie umiemy przypiąć też za piątym razem.
    """
    sekret = settings.brevo_webhook_secret
    if not sekret or not secrets.compare_digest(token, sekret):
        raise HTTPException(401, "Unauthorized")

    dane = await request.json() or {}
    opis = _BREVO_ZDARZENIA.get(str(dane.get("event") or ""))
    if not opis:
        return {"ok": True}
    powod = str(dane.get("reason") or "").strip()
    # Ucięcie TU, a nie dopiero w `_zdarzenie`: niżej ten sam tekst służy za
    # klucz deduplikacji, więc musi być dokładnie tym, co trafia do bazy.
    opis = (f"{opis}: {powod}" if powod else opis)[:200]
    adres = str(dane.get("email") or "").strip().lower()
    temat = str(dane.get("subject") or "").strip()

    session = SessionLocal()
    try:
        lead = session.query(Lead).filter(Lead.email == adres).one_or_none()
        if lead is None:
            return {"ok": True}
        # Dopasowanie po TEMACIE, nie po samym adresie. Tym samym kontem Brevo
        # wychodzą maile do traderów z `notify.py`, a jeden człowiek bywa
        # jednocześnie leadem i traderem — bez tego warunku potwierdzenie
        # zamówienia zapisałoby się jako doręczenie maila do leada. Temat
        # porównujemy z tym, co ZAPISALIŚMY przy wysyłce, więc przepisanie
        # treści maila niczego tu nie psuje; lista jest z definicji krótka,
        # bo mail do leada idzie raz.
        nasze = {z.detail for z in session.query(LeadEvent).filter(
            LeadEvent.lead_id == lead.id, LeadEvent.kind == "email").all()}
        if temat not in nasze:
            return {"ok": True}
        # Brevo ponawia wywołania i potrafi przysłać ten sam wynik kilka razy.
        # Dedup po treści wpisu, ALE tylko od ostatniej wysyłki: panel wysyła
        # do leada kolejne maile, a „delivered: sent" brzmi za każdym razem
        # identycznie — dedup po samej treści połykał wynik każdego maila poza
        # pierwszym i historia wyglądała, jakby Brevo zamilkło. Retry Brevo
        # zawsze przychodzi PO naszym wpisie z wysyłki, więc granica „nowszy
        # niż ostatni kind=email" odsiewa retry, nie kolejne maile.
        ostatnia_wysylka = (session.query(func.max(LeadEvent.created_at))
                            .filter(LeadEvent.lead_id == lead.id,
                                    LeadEvent.kind == "email").scalar())
        if session.query(LeadEvent).filter(
                LeadEvent.lead_id == lead.id, LeadEvent.kind == "delivery",
                LeadEvent.detail == opis,
                LeadEvent.created_at >= ostatnia_wysylka).first():
            return {"ok": True}
        _zdarzenie(session, lead.id, "delivery", opis, actor="brevo")
        session.commit()
    finally:
        session.close()
    return {"ok": True}


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request,
                           x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    """Kliknięcie przycisku pod alertem o leadzie.

    Adres webhooka zna każdy, kto podejrzy ruch bota, więc jedyną kontrolą jest
    sekret, który Telegram odsyła w nagłówku przy każdym update (parametr
    `secret_token` w setWebhook). Bez ustawionego sekretu endpoint nie działa
    wcale — otwarty przyjmowałby zmiany statusów od kogokolwiek.

    Zawsze odpowiada 200: przy błędzie Telegram ponawia update'y i zapętliłby
    się na wiadomości, której i tak nie umiemy obsłużyć.
    """
    sekret = settings.telegram_webhook_secret
    if not sekret or not x_telegram_bot_api_secret_token or \
            not secrets.compare_digest(x_telegram_bot_api_secret_token, sekret):
        raise HTTPException(401, "Unauthorized")

    update = await request.json()
    if (update or {}).get("callback_query"):
        return _telegram_przycisk(update["callback_query"])
    # Kanał oddaje posty jako `channel_post`, prywatny czat jako `message`.
    # Z prywatnego czatu interesuje nas parowanie konta (`/start <kod>`),
    # z obu — odpowiedź na alert, czyli notatka z rozmowy.
    wiadomosc = (update or {}).get("channel_post") or (update or {}).get("message") or {}
    if ((wiadomosc.get("chat") or {}).get("type") == "private"
            and str(wiadomosc.get("text") or "").startswith("/start")):
        return _telegram_start(wiadomosc)
    if wiadomosc.get("reply_to_message"):
        return _telegram_notatka(wiadomosc)
    # Nowy post na obserwowanym kanale — Reach BOT dokupuje pod nim zasięg.
    # Telegram przysyła te update'y tylko z kanałów, w których bot jest
    # administratorem; reszta i tak odpada na liście kanałów.
    if (update or {}).get("channel_post"):
        return {"ok": True, "reach": _reach_z_kanalu(update["channel_post"])}
    return {"ok": True}


def _reach_z_kanalu(post: dict) -> dict:
    """Best-effort: webhook MUSI oddać 2xx, inaczej Telegram ponawia w kółko."""
    session = SessionLocal()
    try:
        return reach.z_kanalu(session, post)
    except Exception as e:  # pragma: no cover - sieć/baza
        print(f"[reach] post z kanału nieobsłużony: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def _telegram_start(wiadomosc: dict) -> dict:
    """`/start <kod>` w DM z botem — sparowanie konta admina z Telegramem.

    Kod wydaje panel (POST /api/me/telegram-link). Od sparowania kliknięcia
    przycisków na kanale LEADS podpisują się mailem konta („bartek@s")
    zamiast imieniem z Telegrama, które bywa niejednoznaczne (dwóch Bartków).
    Jedno konto Telegram wskazuje najwyżej jednego admina — ponowne parowanie
    przepina, nie dokleja."""
    czat = str((wiadomosc.get("chat") or {}).get("id") or "")
    uid = str((wiadomosc.get("from") or {}).get("id") or "")
    czesci = str(wiadomosc.get("text") or "").split(maxsplit=1)
    kod = czesci[1].strip().upper() if len(czesci) > 1 else ""
    if not (czat and uid and kod):
        if czat:
            telegram.send_dm(czat, "Send /start <code> — you will find the code "
                                   "in the admin panel, under Settings → Notifications.")
        return {"ok": True}
    session = SessionLocal()
    try:
        tr = (session.query(Trader)
              .filter(Trader.telegram_link_code == kod, Trader.is_admin.is_(True))
              .one_or_none())
        if not tr:
            session.close()
            telegram.send_dm(czat, "Unknown or already used code. "
                                   "Generate a fresh one in the panel and try again.")
            return {"ok": True}
        for inny in session.query(Trader).filter(Trader.telegram_user_id == uid).all():
            inny.telegram_user_id = None
            inny.telegram_username = None
        tr.telegram_user_id = uid
        # Nick do panelu: po nim widać, KTÓRE konto Telegram się podpięło.
        nadawca = wiadomosc.get("from") or {}
        tr.telegram_username = (("@" + nadawca["username"]) if nadawca.get("username")
                                else (nadawca.get("first_name") or ""))[:40] or None
        tr.telegram_link_code = None
        session.commit()
        email = tr.email
    finally:
        session.close()
    telegram.send_dm(czat, f"Linked as {email}. Your clicks on the LEADS channel "
                           "now sign with this account.")
    return {"ok": True}


def _wykonaj_akcje(session, lead: Lead, akcja: str, kto: str) -> tuple[str, bool]:
    """Kliknięcie w przycisk. Zwraca `(tekst dymka, czy coś się zmieniło)`.

    Dymek to jedyna informacja zwrotna klikającego, więc wraca stąd ZAWSZE —
    także przy odmowie. Bez niego Telegram kręci kółkiem przez minutę i człowiek
    klika drugi raz, bo nie wie, czy pierwszy raz się liczył.
    """
    teraz = datetime.now(timezone.utc)

    if akcja == "claim":
        if lead.owner == kto:
            return "Już to masz", False
        # Przejęcie przechodzi zawsze, także spod kogoś. Kanał czyta wyłącznie
        # zespół, więc nie ma tu przed kim bronić leada — a blokada kosztowała
        # dokładnie tyle, ile trwało czekanie, aż nieobecny właściciel kliknie
        # „oddaję". Kto go miał, zostaje w historii, więc przejęcie nie jest
        # ciche, tylko po prostu nie wymaga niczyjej zgody.
        poprzedni = lead.owner
        lead.owner, lead.owner_at, lead.updated_at = kto, teraz, teraz
        _zdarzenie(session, lead.id, "claim",
                   f"taken by {kto}" + (f" from {poprzedni}" if poprzedni else ""),
                   actor=f"telegram:{kto}")
        return (f"Przejęte od {poprzedni}" if poprzedni else "Twój — pisz"), True

    if akcja == "release":
        if not lead.owner:
            return "Nikt tego nie ma", False
        if lead.owner != kto:
            return f"To lead, którego wziął {lead.owner}", False
        lead.owner, lead.owner_at, lead.updated_at = None, None, teraz
        _zdarzenie(session, lead.id, "claim", f"released by {kto}", actor=f"telegram:{kto}")
        return "Oddane — wraca do puli", True

    if akcja.startswith("tier_"):
        nowa = akcja[len("tier_"):]
        if nowa not in ("high", "warm", "cold"):
            return "Nieznana ocena", False
        if lead.tier == nowa:
            return f"Ocena już stoi na {nowa}", False
        # Ocena z ankiety zostaje w historii jako „high → cold": punktowała
        # deklaracje sprzed telefonu i to, że się rozjechała, jest informacją
        # o formularzu, nie tylko o tym leadzie.
        poprzednia = lead.tier or "—"
        lead.tier, lead.updated_at = nowa, teraz
        _zdarzenie(session, lead.id, "tier", f"{poprzednia} → {nowa}", actor=f"telegram:{kto}")
        return f"Ocena: {nowa}", True

    if akcja.startswith("why_"):
        kod = akcja[len("why_"):]
        if kod not in LOST_REASONS:
            return "Nieznany powód", False
        # Powód bez przegranej to wpis, którego raport nie ma gdzie policzyć.
        # Zdarza się realnie: ktoś klika stary alert, na którym lead stał na
        # „odpada", a w międzyczasie odpisał i wrócił do gry.
        if lead.status not in LEAD_LOST_STATUSES:
            return "Ten lead nie jest odrzucony", False
        if lead.lost_reason == kod:
            return "Ten powód już stoi", False
        lead.lost_reason, lead.updated_at = kod, teraz
        _zdarzenie(session, lead.id, "status", f"lost: {kod}",
                   actor=f"telegram:{kto}")
        return f"Powód: {_ETYKIETY_POWODU.get(kod, kod)}", True

    if akcja in LEAD_STATUSES:
        # Kto klika status na niczyim leadzie, ten go bierze. Alerty sprzed tej
        # zmiany mają pod sobą stare przyciski i bez tego zostawałyby na zawsze
        # bez właściciela.
        wzieto = False
        if not lead.owner:
            lead.owner, lead.owner_at = kto, teraz
            _zdarzenie(session, lead.id, "claim", f"taken by {kto}", actor=f"telegram:{kto}")
            wzieto = True
        poprzedni = lead.status
        _zapisz_status(session, lead, akcja, actor=f"telegram:{kto}")
        # „Napisaliśmy" do kogoś, kto nie podał handle'a, jest deklaracją bez
        # pokrycia: nie ma dokąd napisać. Wtedy — i tylko wtedy — idzie SMS
        # z linkiem z powrotem do Telegrama. Kto handle podał, dostaje
        # wiadomość tam, a SMS zostaje pod ręcznym przyciskiem w panelu.
        # Warunek brzmi „status WŁAŚNIE się zmienił", a nie „stoi na messaged",
        # i to jest tu istotne: wywołujący commituje tylko przy zmianie, więc
        # ponowny klik w ten sam przycisk wysłałby SMS-a, którego zapis o wysyłce
        # nie miałby czym trafić do bazy — i tak w kółko, na koszt konta Twilio.
        # Powód odmowy ląduje w DYMKU, nie w logu: klikający ma się dowiedzieć,
        # że do tego człowieka nic nie poszło. Inaczej zostaje w przekonaniu,
        # że kontakt nastąpił, i lead cicho umiera w „messaged".
        dopisek = ""
        if (akcja == "messaged" and poprzedni != "messaged"
                and lead.status == "messaged" and not lead.telegram):
            dopisek = _kontakt_zastepczy(session, lead, f"telegram:{kto}")
        etykieta = _ETYKIETY_STATUSU.get(akcja, akcja)
        return (f"Zapisano: {etykieta}" + (f" · {dopisek}" if dopisek else ""),
                wzieto or lead.status != poprzedni)

    return "Nieznany przycisk", False


def _telegram_przycisk(cb: dict) -> dict:
    czesci = str(cb.get("data") or "").split(":")
    if len(czesci) != 3 or czesci[0] != "lead":
        return {"ok": True}
    _, surowe_id, akcja = czesci
    dane_od = cb.get("from") or {}
    kto = (dane_od.get("first_name") or "?")[:60]

    session = SessionLocal()
    try:
        # Sparowane konto podpisuje się mailem admina; niesparowane zostaje
        # przy imieniu z Telegrama (fallback, nie błąd — parowanie jest opt-in).
        uid = str(dane_od.get("id") or "")
        if uid:
            sparowany = (session.query(Trader.email)
                         .filter(Trader.telegram_user_id == uid).first())
            if sparowany:
                kto = sparowany[0][:60]
        lead = session.get(Lead, int(surowe_id)) if surowe_id.isdigit() else None
        if not lead:
            telegram.answer_callback(cb.get("id", ""), "Nie znaleziono leada")
            return {"ok": True}
        dymek, zmiana = _wykonaj_akcje(session, lead, akcja, kto)
        # Karta sprzed kolumny `tg_chat_id` dopisuje sobie czat przy pierwszym
        # kliknięciu. Tylko tutaj: callback niesie id leada, więc czat, w którym
        # wisi jego karta, jest pewny — przy notatce byłby zgadywany po numerze
        # wiadomości, a ten potrafi się powtórzyć między czatami.
        czat_karty = str(((cb.get("message") or {}).get("chat") or {}).get("id") or "")
        uzupelniono = bool(czat_karty) and not lead.tg_chat_id
        if uzupelniono:
            lead.tg_chat_id = czat_karty
        # `zmiana` zostaje nietknięta: mówi, czy klikający coś zmienił, i to ona
        # decyduje o przepisaniu karty i pushu do reszty. Dopisany czat nie jest
        # niczyim kliknięciem i nie ma prawa wysłać powiadomienia.
        if zmiana or uzupelniono:
            session.commit()
        tekst, klawiatura = _stan_wiadomosci(lead)
        lead_id, kogo = lead.id, (lead.name or lead.email)
    finally:
        session.close()

    telegram.answer_callback(cb.get("id", ""), dymek)
    if not zmiana:
        return {"ok": True}          # odmowa albo kliknięcie w to samo drugi raz
    wiadomosc = cb.get("message") or {}
    czat = str((wiadomosc.get("chat") or {}).get("id") or "")
    if czat and wiadomosc.get("message_id"):
        telegram.edit_lead_message(czat, wiadomosc["message_id"], tekst, keyboard=klawiatura)
    # Reszta zespołu dowiaduje się z telefonu, kto co zrobił z leadem. Klikający
    # też to dostanie (imię z Telegrama nie mapuje się na konto w panelu, więc
    # nie ma go jak wykluczyć) — tag `lead-<id>` skleja to w jedno powiadomienie.
    opisy = {"claim": "took the lead", "release": "released the lead"}
    if akcja.startswith("tier_"):
        opis = f"grade → {akcja[len('tier_'):]}"
    elif akcja.startswith("why_"):
        opis = f"lost: {akcja[len('why_'):]}"
    else:
        opis = opisy.get(akcja) or f"marked {akcja}"
    _lead_push(lead_id, f"{kto}: {opis}", kogo)
    return {"ok": True}


def _stan_wiadomosci(lead: Lead) -> tuple[str, dict]:
    """Wiadomość i klawiatura odpowiadające temu, co jest teraz w bazie.
    Jedno miejsce, bo przepisuje ją i przycisk, i notatka z odpowiedzi."""
    try:
        dane = json.loads(lead.payload_json or "{}")
    except ValueError:
        dane = {}
    return _tekst_alertu(lead, dane), telegram.lead_keyboard(
        lead.id, owner=lead.owner, status=lead.status, tier=lead.tier,
        lost_reason=lead.lost_reason)


def _telegram_notatka(wiadomosc: dict) -> dict:
    """Odpowiedź na alert = notatka z rozmowy.

    Jedyny sposób pisania notatek, który ma szansę być używany: dział siedzi na
    kanale, nie w panelu. Powiązanie idzie po `message_id` wiadomości, na którą
    odpowiedziano, bo `reply` nie niesie niczego innego — stąd `tg_message_id`
    na leadzie.

    O AUTORZE: posty na kanale są anonimowe, dopóki właściciel nie włączy „Sign
    messages". Dopiero wtedy przychodzi `author_signature`. Bez podpisów notatka
    zapisze się jako „kanał" i nie da się powiedzieć, kto ją napisał — kanał
    moderuje kilka osób, więc to trzeba włączyć.
    """
    odpowiedz_na = (wiadomosc.get("reply_to_message") or {}).get("message_id")
    tresc = (wiadomosc.get("text") or wiadomosc.get("caption") or "").strip()
    if not odpowiedz_na or not tresc:
        return {"ok": True}
    autor = (wiadomosc.get("author_signature")
             or (wiadomosc.get("from") or {}).get("first_name") or "kanał")[:60]
    czat = str((wiadomosc.get("chat") or {}).get("id") or "")

    session = SessionLocal()
    try:
        # Numer wiadomości jest unikalny w czacie, nie u bota, więc karta z
        # czatu free i karta z czatu działu potrafią mieć ten sam. Stąd filtr
        # po czacie, a `first()` zamiast `one_or_none()`: kolizja rzuciłaby 500,
        # a Telegram ponawia nieodebrany update w kółko.
        # Furtka dla kart wysłanych, zanim `tg_chat_id` istniało — te wiszą
        # w czacie działu i innego nie miały.
        baza = session.query(Lead).filter(Lead.tg_message_id == odpowiedz_na)
        lead = (baza.filter(Lead.tg_chat_id == czat).first()
                or baza.filter(Lead.tg_chat_id.is_(None)).first())
        if not lead:
            return {"ok": True}      # odpowiedź na coś, co nie jest alertem o leadzie
        # Notatki DOPISUJĄ się, nie nadpisują: na kanale odpowiada kilka osób
        # i druga uwaga nie ma prawa skasować pierwszej. Przy przepełnieniu
        # wypadają całe najstarsze linie, a nie połowa zdania.
        linie = [x for x in (lead.note or "").split("\n") if x] + [f"{autor}: {tresc}"]
        while len("\n".join(linie)) > 4000 and len(linie) > 1:
            linie.pop(0)
        lead.note = "\n".join(linie)
        lead.updated_at = datetime.now(timezone.utc)
        _zdarzenie(session, lead.id, "note", tresc, actor=f"telegram:{autor}")
        session.commit()
        tekst, klawiatura = _stan_wiadomosci(lead)
        mid = lead.tg_message_id
    finally:
        session.close()

    if czat and mid:
        telegram.edit_lead_message(czat, mid, tekst, keyboard=klawiatura)
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Przypomnienia o leadach (cron)                                             #
# --------------------------------------------------------------------------- #
# Wszystkie trzy idą DO DZIAŁU, na czat z leadami — żadne nie jest wiadomością
# do klienta. Lead zgłosił się przez landing, który zna pod inną marką; mail
# „przypominamy o rozmowie" wysłany stąd powiedziałby mu, że to jedna firma.
# Do człowieka odzywa się człowiek i tym kanałem, którym ten człowiek przyszedł.
#
# Deduplikacja przez BRAK zdarzenia `reminder` z danym powodem — bez nowych
# kolumn-znaczników i przy okazji widoczna w historii leada („przypomniano").

LEAD_REMINDERS = ("no_contact", "bought", "stalled", "unclaimed")

# Co ile dni dopominać się o kontakt z kimś, kto już kupił challenge. Cykl,
# nie jednorazówka: człowiek z opłaconym kontem jest w połowie drogi do drugiego
# zakupu i o niego trzeba zahaczać co tydzień, a nie raz pogratulować.
BOUGHT_UPDATE_DAYS = 7

# Po ilu minutach ciszy mail do leada wychodzi SAM. Dłużej niż nudge „nikt nie
# wziął" (30 min) i to jest cały sens tej wartości: pierwszy strzał należy do
# człowieka, automat jest dopiero zabezpieczeniem na to, że nikt nie usiadł.
# Skrócenie tego do minut zamieniłoby mail w autoresponder, a on sprzedaje
# dokładnie jedno zdanie — że aplikację czytał ktoś żywy.
LEAD_AUTO_MAIL_MIN = 60

# Jak długo po zgłoszeniu wolno jeszcze dosłać kartę, która nie wyszła. Doba,
# bo to ratunek po awarii Telegrama, a nie archiwum: karta sprzed tygodnia
# przeczytałaby się na kanale jak świeże zgłoszenie. Okno chroni też przed
# wierszami sprzed kolumny `tg_message_id`, gdzie NULL nic nie znaczy.
LEAD_CARD_RETRY_H = 24

# Do kiedy lead BEZ karty ma się jeszcze upomnieć — jednym zdaniem, zamiast
# kolejnej próby wysłania. Po `LEAD_CARD_RETRY_H` dosyłka słusznie odpuszcza,
# ale wtedy taki człowiek staje się dla działu niewidzialny: kanał czyta się
# zamiast panelu, a nieudana wysyłka zostawia tylko wpis w historii, którego
# nikt nie ogląda. Dokładnie tak przepadł tydzień, gdy bota nie było w grupie.
# Górna granica pasma jest tu warunkiem, nie ozdobą: wiersze sprzed kolumny
# `tg_message_id` mają w niej NULL, które nic nie znaczy, i bez niej pierwszy
# przebieg po wdrożeniu wywołałby na czat całe archiwum.
LEAD_CARD_DEAD_H = 48

# Po ilu godzinach bez ANI JEDNEGO leada dział dostaje ostrzeżenie. Nie chodzi
# o słabą kampanię, tylko o zerwany łańcuch landing→ingest→czat: cisza z tego
# powodu wygląda dokładnie tak samo jak cisza z braku ruchu i raz kosztowała
# już tydzień. Doba, bo tyle wynosi najgorszy odstęp między przebiegami.
LEADS_SILENCE_H = 24


def _utc(d: datetime | None) -> datetime | None:
    """SQLite oddaje daty bez strefy, Postgres ze strefą. Porównanie jednego
    z drugim wywraca się na TypeError, więc wszystko schodzi do UTC."""
    if d is None:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def _tekst_martwej_karty(lead: Lead, wiek_min: float) -> str:
    """Lead, którego kanał nigdy nie zobaczył.

    Jedyna wiadomość, jaką dział dostanie o tym człowieku — więc niesie komplet
    do odezwania się i link do panelu. Bez linku byłaby informacją, z którą na
    telefonie nie da się nic zrobić, a to na telefonie się ją czyta.
    """
    e = html.escape
    linie = [f"🚨 <b>Karta nie wyszła</b> — {e(lead.name or lead.email)}",
             f"✉️ {e(lead.email)}"]
    if lead.telegram:
        linie.append(f"💬 {e(lead.telegram)}")
    if lead.phone:
        linie.append(f"📞 {e(lead.phone)}")
    linie.append(f"Zgłosił się {int(wiek_min // 60)} h temu i nie ma go na kanale. "
                 f"Weź go z panelu: /admin?lead={lead.id}")
    return "\n".join(linie)


def _tekst_przypomnienia(lead: Lead, powod: str, paid: float, dni: int) -> str:
    e = html.escape
    kto = f"<b>{e(lead.name or lead.email)}</b>"
    naglowek = {
        # Ręczne „bought" z panelu nie niesie kwoty — sklep tego zakupu nie widział.
        "bought": f"💸 {kto} KUPIŁ — ${paid:,.0f}" if paid > 0 else f"💸 {kto} KUPIŁ",
        "no_contact": f"⏰ {kto} czeka bez kontaktu",
        "stalled": f"🕐 {kto} — rozmowa bez ciągu dalszego",
    }.get(powod, kto)
    stopka = {
        "bought": ("Zapłacone zamówienie na ten mail. Przestań traktować jak leada."
                   if paid > 0 else
                   "Oznaczone ręcznie jako kupione. Przestań traktować jak leada."),
        "no_contact": f"Zgłosił się {dni} dni temu i nikt nie ruszył statusu.",
        "stalled": f"Pierwszy kontakt {dni} dni temu, od tego czasu nic.",
    }.get(powod, "")

    # Telegram nad telefonem, bo tamtędy idzie kontakt — numer jest tu zapasem
    # na wypadek, gdyby ktoś nie zostawił handle'a.
    linie = [naglowek, f"✉️ {e(lead.email)}"]
    if lead.telegram:
        linie.append(f"💬 {e(lead.telegram)}")
    if lead.phone:
        linie.append(f"📞 {e(lead.phone)}")
    if lead.note:
        linie.append(f"📝 {e(lead.note)[:200]}")
    if stopka:
        linie.append(stopka)
    return "\n".join(linie)


def _tekst_zaplanowanego(lead: Lead, r: LeadReminder) -> str:
    """Przypomnienie ustawione ręcznie w panelu albo cykl założony po zakupie.

    Treść pisze człowiek, więc idzie przez `html.escape` tak samo jak dane
    z formularza — panel jest po drugiej stronie tego samego `parse_mode=HTML`.
    """
    e = html.escape
    ile = f" ({r.sent_count + 1}. raz)" if r.repeat_days else ""
    linie = [f"🔔 <b>{e(lead.name or lead.email)}</b>{ile}", f"✉️ {e(lead.email)}"]
    if lead.phone:
        linie.append(f"📞 {e(lead.phone)}")
    if lead.telegram:
        linie.append(f"💬 {e(lead.telegram)}")
    if lead.owner:
        linie.append(f"👤 {e(lead.owner)}")
    linie.append(f"➡️ {e(r.text)}")
    return "\n".join(linie)


@app.api_route("/api/cron/lead-followups", methods=["GET", "POST"],
               dependencies=[Depends(_require_cron)])
def cron_lead_followups(no_contact_days: int = 3, stalled_days: int = 7):
    """Ręcznie/cronem — cała robota siedzi w `_lead_followups`."""
    return _lead_followups(no_contact_days, stalled_days)


def _wyslij_zaplanowane(session, now: datetime
                        ) -> tuple[list[tuple[str, str]], list[tuple[int, str, str]]]:
    """Przypomnienia z terminem, który już minął: ustawione ręcznie w panelu
    i cykle założone po zakupie.

    Cykliczne NIE gasną po wysłaniu, tylko przesuwają termin o `repeat_days` —
    dopóki ktoś ich nie wyłączy w panelu. Jednorazowe zamykają się same, żeby
    nie trzeba było po nich sprzątać.

    Zwraca dwie listy: wiadomości `(czat, tekst)` — czat, bo leady free mają
    własny i przypomnienie ma trafić tam, gdzie wisi karta — oraz pushe
    `(lead_id, tytuł, treść)` na telefony adminów; push jest czystym tekstem,
    bo HTML z Telegrama wyświetliłby się w notyfikacji dosłownie, ze znacznikami.

    Filtr terminu jest po stronie Pythona, a nie w zapytaniu: SQLite oddaje daty
    bez strefy, więc porównanie kolumny z `now` ze strefą potrafi w tej samej
    bazie zwrócić coś innego niż w Postgresie.
    """
    teksty: list[tuple[str, str]] = []
    pushy: list[tuple[int, str, str]] = []
    for r in session.query(LeadReminder).filter(LeadReminder.active.is_(True)).all():
        if (_utc(r.due_at) or now) > now:
            continue
        lead = session.get(Lead, r.lead_id)
        if not lead:
            r.active = False
            continue
        teksty.append((telegram.lead_chat_id(lead.source), _tekst_zaplanowanego(lead, r)))
        pushy.append((lead.id, f"Reminder: {r.text[:80]}", lead.name or lead.email))
        r.sent_count = (r.sent_count or 0) + 1
        r.last_sent_at = now
        _zdarzenie(session, lead.id, "reminder", f"planned: {r.text}"[:200], actor="cron")
        if r.repeat_days:
            r.due_at = now + timedelta(days=r.repeat_days)
        else:
            r.active = False
    return teksty, pushy


def _lead_followups(no_contact_days: int = 3, stalled_days: int = 7) -> dict:
    """Przypomnienia dla działu o leadach, o których zrobiło się cicho.

    Dwa źródła. Pierwsze to terminy ustawione ręcznie („oddzwonić za tydzień")
    i cykle po zakupie — te wiedzą, kiedy mają zadzwonić, bo ktoś to wpisał.
    Drugie to trzy reguły poniżej, które łapią leady, o których NIKT nie
    pomyślał; pierwsza pasująca wygrywa, bo o jednym człowieku ma przyjść jedna
    wiadomość, a nie trzy:

    * `bought`   — na maila leada jest zapłacone zamówienie. To jedyny powód
                   ważniejszy od statusu w panelu: dział ma przestać dzwonić do
                   kogoś, kto już zapłacił, a status przy takim leadzie potrafi
                   miesiącami stać na „new", bo nikt go nie odklikał.
    * `no_contact` — wisi na „new" dłużej niż `no_contact_days`.
    * `stalled`  — odebrał telefon, minęło `stalled_days` i nic z tego nie ma.
    """
    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        do_wyslania, pushy = _wyslij_zaplanowane(session, now)
        do_maila: list[int] = []
        do_kart: list[tuple[int, str, str, dict]] = []
        # Własny licznik, a nie długość `do_wyslania`: tamta lista wchodzi tu już
        # z zaplanowanymi przypomnieniami i przy dziesięciu terminach na dziś
        # limit zjadłby wszystkie alerty o brakujących kartach.
        martwych = 0
        leady = session.query(Lead).all()

        maile = [l.email for l in leady]
        traderzy: dict[str, int] = {}
        if maile:
            traderzy = {e.lower(): i for i, e in
                        session.query(Trader.id, Trader.email)
                        .filter(func.lower(Trader.email).in_(maile)).all()}
        zaplacone: dict[int, float] = {}
        if traderzy:
            zaplacone = dict(session.query(Order.trader_id, func.sum(Order.amount_usd))
                             .filter(Order.trader_id.in_(list(traderzy.values())),
                                     Order.status == "paid")
                             .group_by(Order.trader_id).all())
        juz = {(i, d) for i, d in session.query(LeadEvent.lead_id, LeadEvent.detail)
               .filter(LeadEvent.kind == "reminder").all()}

        for l in leady:
            paid = float(zaplacone.get(traderzy.get(l.email), 0) or 0)
            wiek = (now - (_utc(l.created_at) or now)).days
            od_kontaktu = (now - (_utc(l.contacted_at) or now)).days
            # Nudge „nikt nie wziął" — OSOBNO od łańcucha powodów niżej, bo
            # tamten myśli w dniach i wybiera jeden powód, a ten w minutach
            # i ma prawo zbiec się z każdym z nich. Sam push, bez wpisu na
            # czacie: karta leada i tak wisi na kanale, brzęczeć ma telefon.
            # Dedup tym samym zdarzeniem `reminder`, więc raz na lead.
            niczyj = (l.owner is None and l.status == "new" and paid == 0
                      and not l.bought and l.outcome != "not_qualified")
            wiek_min = (now - (_utc(l.created_at) or now)).total_seconds() / 60
            if niczyj and wiek_min >= 30 and (l.id, "unclaimed") not in juz:
                _zdarzenie(session, l.id, "reminder", "unclaimed", actor="cron")
                pushy.append((l.id, "Unclaimed lead (30 min+)", l.name or l.email))
            # Karta, która nie wyszła. Wysyłka przy zgłoszeniu jest po commicie
            # i best-effort, więc minutowa awaria Telegrama zostawiała leada
            # w bazie i nic na kanale — do tej pory bezterminowo. Pusty
            # `tg_message_id` jest tu jednocześnie warunkiem i kolejką: udana
            # dosyłka wypełnia pole i wiersz sam wypada z następnego przebiegu.
            # Bez dedupu przez `juz`, bo ten liczy raz na życie leada, a nieudana
            # wysyłka ma prawo powtórzyć się tyle razy, ile trwa awaria.
            czat_karty = telegram.lead_chat_id(l.source)
            if (l.tg_message_id is None and wiek_min <= LEAD_CARD_RETRY_H * 60
                    and l.outcome == "qualified" and len(do_kart) < 20
                    # Przy wyłączonych alertach każdy lead ma puste pole i cała
                    # baza wchodzi do kolejki, która nigdy się nie opróżni.
                    and telegram.lead_alerts_on(czat_karty)):
                tekst_karty, klawiatura = _stan_wiadomosci(l)
                do_kart.append((l.id, czat_karty, tekst_karty, klawiatura))
            # Okno dosyłek minęło, karty nadal nie ma. Dalsze próby nie mają
            # sensu, ale milczenie tym bardziej: to jedyny moment, w którym
            # ktokolwiek dowie się, że ten człowiek nigdy nie trafił na kanał.
            # `elif`, bo lead jest albo w kolejce, albo poza nią — nigdy w obu.
            # Dedup przez `juz` (raz na życie wiersza) i limit na przebieg, żeby
            # dzień awarii nie zszedł na czat jedną ścianą tekstu.
            elif (l.tg_message_id is None and l.outcome == "qualified"
                    and wiek_min <= LEAD_CARD_DEAD_H * 60
                    and (l.id, "card_dead") not in juz
                    and martwych < 10
                    and telegram.lead_alerts_on(czat_karty)):
                _zdarzenie(session, l.id, "reminder", "card_dead", actor="cron")
                do_wyslania.append((czat_karty, _tekst_martwej_karty(l, wiek_min)))
                martwych += 1
            # Ten sam stan godzinę później: skoro nikt nie usiadł, niech
            # przynajmniej lead przestanie czekać w ciszy. Tylko do kogoś BEZ
            # handle'a — kto go zostawił, ma dostać wiadomość tam, gdzie na nią
            # czeka, i to zostaje robotą człowieka; ten sam podział pilnuje
            # `_kontakt_zastepczy`. SMS-a automat nie rusza: kosztuje i jest
            # zgodą, której nikt świadomie nie wydał, a mail nie kosztuje nic.
            if (niczyj and wiek_min >= LEAD_AUTO_MAIL_MIN
                    and not (l.telegram or "").strip() and lead_mail.is_enabled()):
                do_maila.append(l.id)
            if paid > 0 or l.bought:
                # Ręczny checkbox „bought" liczy się jak zapłacone zamówienie:
                # przestań dzwonić jak do leada, zacznij pilnować jak klienta.
                powod, dni = "bought", wiek
            elif l.status == "new" and wiek >= no_contact_days:
                powod, dni = "no_contact", wiek
            # Oba stany rozmowy w toku: „napisaliśmy i cisza" tak samo jak
            # „odpisał i utknęło". `no_reply` i `rejected` są zamknięte — tam
            # przypominanie byłoby nagabywaniem, nie pilnowaniem tematu.
            elif l.status in ("messaged", "replied") and l.contacted_at and od_kontaktu >= stalled_days:
                powod, dni = "stalled", od_kontaktu
            else:
                continue
            if (l.id, powod) in juz:
                continue
            _zdarzenie(session, l.id, "reminder", powod, actor="cron")
            do_wyslania.append((telegram.lead_chat_id(l.source),
                                _tekst_przypomnienia(l, powod, paid, dni)))
            tytul = {
                "bought": f"{l.name or l.email} bought — ${paid:,.0f}",
                "no_contact": f"No contact yet: {l.name or l.email} ({dni}d)",
                "stalled": f"Stalled: {l.name or l.email} ({dni}d quiet)",
            }[powod]
            pushy.append((l.id, tytul, l.email))
            if powod == "bought":
                # Jednorazowe „ten człowiek kupił" załatwia moment, w którym
                # trzeba przestać dzwonić jak do leada. Ale klient z opłaconym
                # kontem potrzebuje kontaktu W KÓŁKO, więc ta sama chwila zakłada
                # cykl. Bez tego dopisek o zakupie przychodził raz i temat gasł.
                session.add(LeadReminder(
                    lead_id=l.id, kind="bought", repeat_days=BOUGHT_UPDATE_DAYS,
                    due_at=now + timedelta(days=BOUGHT_UPDATE_DAYS), created_by="cron",
                    text="Update konta: jak idzie challenge, czy zasady są jasne, "
                         "jak blisko limitu straty."))
        # Cisza całkowita. Każda reguła wyżej mówi o leadzie, który JEST w bazie,
        # więc przy zerze zgłoszeń wszystkie milczą — a to jest dokładnie ta
        # awaria, która raz kosztowała tydzień. Znacznik w `app_settings`, a nie
        # zdarzenie na leadzie, bo tu z definicji nie ma leada, przy którym
        # dałoby się to zapisać; bez niego ostrzeżenie wracałoby co przebieg.
        ostatni = max((_utc(l.created_at) for l in leady if l.created_at), default=None)
        cisza = (now - ostatni).total_seconds() if ostatni else 0
        if ostatni and cisza >= LEADS_SILENCE_H * 3600:
            wpis = session.get(AppSetting, "leadbot_cisza_alert")
            poprzednio = None
            if wpis and wpis.value:
                try:
                    poprzednio = _utc(datetime.fromisoformat(wpis.value))
                except ValueError:
                    pass
            if poprzednio is None or (now - poprzednio).total_seconds() >= LEADS_SILENCE_H * 3600:
                if wpis is None:
                    wpis = AppSetting(key="leadbot_cisza_alert", value="")
                    session.add(wpis)
                wpis.value = now.isoformat()
                do_wyslania.append((
                    telegram.lead_chat_id(""),
                    f"⚠️ Zero nowych leadów od {int(cisza // 3600)} h. "
                    "Sprawdź łańcuch: formularz → LEAD_WEBHOOK → /api/leads/ingest → czat."))

        # Commit PRZED wysyłką, jak przy porzuconych koszykach: jak Telegram
        # padnie, przypomnienie przepada. Gdyby zapis szedł po wysyłce, padnięta
        # wysyłka wracałaby przy każdym przebiegu crona i dział dostałby to samo
        # przypomnienie dziesięć razy — a to jest gorsze niż jedno utracone.
        session.commit()
        # Maile POJEDYNCZO i każdy z własnym commitem — odwrotnie niż wpisy
        # wyżej. Tam commit chronił dział przed powtórką na czacie; tu jedyną
        # blokadą przed drugą kopią W SKRZYNCE człowieka jest wpis w historii,
        # który `_mail_do_leada` sprawdza. Jeden commit na końcu rundy znaczyłby,
        # że przerwany przebieg powtórzy WSZYSTKIE maile z tej rundy, a nie ten
        # jeden, przy którym się wywrócił.
        wyslane_maile = 0
        for lead_id in do_maila:
            lead = session.get(Lead, lead_id)
            if lead and _mail_do_leada(session, lead, "cron:unclaimed")[0]:
                session.commit()
                wyslane_maile += 1
    finally:
        session.close()

    for czat, tekst in do_wyslania:
        telegram.send_lead_message(tekst, chat_id=czat)
    # Dosyłka kart osobno od przypomnień, bo idzie `send_lead_alert` — karta ma
    # mieć pod sobą przyciski, inaczej dział zobaczy leada, którego nie da się
    # wziąć z czatu. Zapis wyniku tą samą funkcją co przy zgłoszeniu: sukces
    # zdejmuje wiersz z kolejki, porażka dopisuje powód do historii.
    doslane = 0
    for lead_id, czat, tekst, klawiatura in do_kart:
        _, powod, message_id = telegram.send_lead_alert(
            lead_id, tekst, keyboard=klawiatura, chat_id=czat)
        _zapamietaj_wysylke(lead_id, message_id, czat or None, powod)
        doslane += 1 if message_id else 0
    # Po commicie, jak wysyłka na czat: padnięty push service nie cofnie zapisu,
    # a dedup wyżej gwarantuje, że kolejny przebieg nie wyśle tego drugi raz.
    for lead_id, tytul, tresc in pushy:
        _lead_push(lead_id, tytul, tresc, event="lead_reminder")
    return {"sent": len(do_wyslania), "checked": len(leady), "pushed": len(pushy),
            "mailed": wyslane_maile, "cards": doslane}


# Ruch strony przebiega follow-upy najwyżej co tyle minut. Wartość z sufitu
# świadomie: częściej = szybszy nudge „nikt nie wziął", ale każdy przebieg to
# pełny przegląd tabeli leadów.
LEADS_SWEEP_MIN = 10


def _lead_sweep_z_ruchu() -> None:
    """Przebieg follow-upów z ruchu strony — cron Hobby chodzi raz na dobę,
    a przypomnienie ustawione „za 2 dni o czasie" i nudge liczony w minutach
    potrzebują czegoś częstszego niż doba.

    Guard jak w payout bocie: jeden odczyt app_settings na request, realny
    przebieg kilka razy dziennie. Znacznik commitowany PRZED robotą, żeby dwa
    równoległe requesty nie zrobiły dwóch przebiegów. NIGDY nie rzuca —
    odpowiedź dla klienta jest ważniejsza niż przypomnienie."""
    try:
        now = datetime.now(timezone.utc)
        session = SessionLocal()
        try:
            row = session.get(AppSetting, "leadbot_last_sweep")
            if row and row.value:
                try:
                    ostatni = _utc(datetime.fromisoformat(row.value))
                    if ostatni and (now - ostatni).total_seconds() < LEADS_SWEEP_MIN * 60:
                        return
                except ValueError:
                    pass
            if row is None:
                row = AppSetting(key="leadbot_last_sweep", value="")
                session.add(row)
            row.value = now.isoformat()
            session.commit()
        finally:
            session.close()
        _lead_followups()
    except Exception as e:  # pragma: no cover
        print(f"[leadbot] sweep z ruchu: {e}")


# --------------------------------------------------------------------------- #
#  Strony                                                                      #
# --------------------------------------------------------------------------- #
# Strony publiczne renderuje Jinja (wspólny base.html: nav + footer + disclaimery).
# Portal i admin zostają jako samodzielne SPA serwowane z pliku.
jinja = Jinja2Templates(directory=str(TEMPLATES))


# Wersja assetów w linkach ?v= — bez tego przeglądarka potrafi trzymać stary
# CSS/JS po deployu i "naprawione" style nigdy nie docierają do użytkownika.
# Na Vercelu SHA commita jest stały per deploy; lokalnie wystarczy czas startu.
ASSET_V = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")[:10] or str(int(time.time()))


def _promo_ctx() -> dict | None:
    """Kontekst promocji dla stron publicznych albo None, gdy nie obowiązuje.

    Ta sama bramka (`catalog.promo_active`) rządzi mechaniką w checkoucie, więc
    pasek nie może obiecywać czegoś, czego kasa nie zrobi.
    """
    if not catalog.promo_active():
        return None
    do_kiedy = None
    if settings.promo_upgrade_ends:
        try:
            do_kiedy = date.fromisoformat(settings.promo_upgrade_ends).strftime("%d %b")
        except ValueError:
            do_kiedy = None
    # Kod jedzie do szablonu, bo belka pre-fill'uje nim input — „Apply promo"
    # aplikuje promocje JEDNYM klikiem (kod nie jest sekretem, to marketing).
    return {"name": catalog.PROMO_NAME, "ends": do_kiedy,
            "code": settings.promo_upgrade_code}


# Stan przełącznika BOGO dla stron publicznych. Krótki TTL zamiast zapytania
# przy KAŻDYM renderze — baza stoi za oceanem. POST /api/admin/bogo-promo
# zeruje ts, więc instancja obsługująca panel pokazuje zmianę od razu;
# pozostałe dociągają ją najpóźniej po minucie.
_BOGO_BAR_CACHE: dict = {"ts": 0.0, "val": False}


def _bogo_promo_active() -> bool:
    now = time.time()
    if now - _BOGO_BAR_CACHE["ts"] > 60:
        s = SessionLocal()
        try:
            _BOGO_BAR_CACHE.update(ts=now, val=billing.bogo_active(s))
        finally:
            s.close()
    return _BOGO_BAR_CACHE["val"]


def _page(request: Request, template: str, **extra):
    ctx = {"site_name": settings.site_name, "support_email": settings.support_email,
           "base_url": _public_base(request), "asset_v": ASSET_V,
           "ga_id": settings.ga_measurement_id,
           "clarity_id": settings.clarity_project_id, "promo": _promo_ctx(),
           "bogo_promo": _bogo_promo_active(),
           "google_client_id": settings.google_client_id, **extra}
    return jinja.TemplateResponse(request, template, ctx)


def _size_label(v: float) -> str:
    """Odbicie sizeLabel z site.js — serwerowy render musi dawać ten sam tekst."""
    return f"${v / 1e6:g}M" if v >= 1_000_000 else f"${round(v / 1000)}K"


def _cfg_ctx(prods: list[dict]) -> dict | None:
    """Domyślny stan konfiguratora hero renderowany SERWEROWO.

    Inline'owe dane (#pf-products) zdjęły czekanie na API, ale render dalej
    robił site.js ładowany po gsap na końcu body — pierwszy paint łapał puste
    "—". Serwer maluje panel od razu; site.js po starcie robi idempotentny
    re-render (identyczne wartości), więc nic nie mruga. Logika wyboru 1:1
    z renderConfigurator(): rodzina 2-step, rozmiar 100k albo największy.
    """
    g2 = sorted((p for p in prods if p["steps"] == 2 and p["price_usd"] > 0),
                key=lambda p: p["account_size"])
    gi = sorted((p for p in prods if p["steps"] == 0 and p["price_usd"] > 0),
                key=lambda p: p["account_size"])
    items = g2 or gi
    if not items:
        return None
    sel = next((p for p in items if p["account_size"] == 100_000), items[-1])
    tabs = []
    for grupa, gid, nazwa in ((g2, "2step", "2-Step Evaluation"), (gi, "instant", "Instant Funding")):
        if grupa:
            tabs.append({"id": gid, "name": nazwa, "on": grupa is items,
                         "from_usd": f"{min(p['price_usd'] for p in grupa):,.0f}"})
    instant = sel["steps"] == 0
    return {
        "tabs": tabs,
        "sizes": [{"key": p["key"], "label": _size_label(p["account_size"]),
                   "on": p["key"] == sel["key"]} for p in items],
        "fee": f"{round(sel['price_usd']):,}", "fee_raw": round(sel["price_usd"]),
        "instant": instant,
        "target_pct": f"{sel['profit_target_p1']:g}",
        "target_usd": f"{round(sel['account_size'] * sel['profit_target_p1'] / 100):,}",
        "target_usd_raw": round(sel["account_size"] * sel["profit_target_p1"] / 100),
        "split": f"{sel['profit_split_pct']:g}",
        "cta_key": sel["key"],
        "cta_label": f"Start with {_size_label(sel['account_size'])} → ${round(sel['price_usd']):,}",
        "promo_any": any(p["promo_upgrade_size"] for p in prods),
    }


@app.get("/")
def home(request: Request):
    """Publiczna strona sprzedażowa (cennik/objectives z /api/products)."""
    # QR w podglądzie certyfikatu prowadzi na REALNĄ stronę weryfikacji —
    # atrapa kodu na landingu byłaby obietnicą bez pokrycia.
    # Katalog wstrzyknięty w HTML + konfigurator hero renderowany serwerowo:
    # panel jest kompletny w pierwszym paincie, bez błysku pustych "—".
    session = SessionLocal()
    try:
        prods = _products_payload(session)
    finally:
        session.close()
    return _page(request, "home.html",
                 sample_qr=_qr_svg(f"{_public_base(request)}/verify"),
                 products=prods, cfg=_cfg_ctx(prods))


@app.get("/faq")
def faq_page(request: Request):
    return _page(request, "faq.html")


@app.get("/academy")
def academy_page(request: Request):
    return _page(request, "academy.html")


@app.get("/affiliate")
def affiliate_page(request: Request):
    return _page(request, "affiliate.html")


@app.get("/install")
def install_page(request: Request):
    """Instrukcja instalacji PWA (iOS: Dodaj do ekranu, Android: beforeinstallprompt)."""
    return _page(request, "install.html")


@app.get("/objectives")
def objectives_page(request: Request):
    """Tabela zasad, wcześniej sekcja na landingu.

    Katalog wstrzyknięty w HTML tak samo jak na `/`: tabelę wypełnia
    `renderObjectives()` z site.js, więc liczby nie mogą rozjechać się z tym,
    co sprzedaje sklep.
    """
    session = SessionLocal()
    try:
        return _page(request, "objectives.html", products=_products_payload(session))
    finally:
        session.close()


@app.get("/terms")
def terms_page(request: Request):
    return _page(request, "legal/terms.html")


@app.get("/privacy")
def privacy_page(request: Request):
    return _page(request, "legal/privacy.html")


@app.get("/risk-disclosure")
def risk_page(request: Request):
    return _page(request, "legal/risk-disclosure.html")


@app.get("/refund-policy")
def refund_page(request: Request):
    return _page(request, "legal/refund-policy.html")


def _zamowienie_po_tokenie(session, token: str) -> Order:
    o = (session.query(Order).filter(Order.pay_token == token).first()
         if token else None)
    if not o:
        raise HTTPException(404, "Payment link not found")
    return o


def _pozycja_zamowienia(session, o: Order) -> str:
    """Nazwa pozycji na paragonie Stripe'a i na stronie płatności."""
    produkt = session.query(Product).filter(Product.key == o.product_key).first()
    return f"{produkt.label if produkt else o.product_key} challenge"


@app.get("/pay/{token}")
def pay_page(request: Request, token: str):
    """Strona płatności za konkretne zamówienie, wystawiona ręcznie z panelu.

    Trafia tu klient, który dogadał zakup poza sklepem (np. na Telegramie).
    ZERO danych osobowych na stronie — link bywa przeklejany, a jedyne, co ma
    pokazywać, to za co i ile. Kasę Stripe'a otwiera dopiero przycisk, więc
    strona nie wygasa i można ją wysłać raz na zawsze.
    """
    session = SessionLocal()
    try:
        o = (session.query(Order).filter(Order.pay_token == token).first()
             if token else None)
        if not o:
            # Strona, nie goły JSON: pod tym adresem stoi KLIENT, a zamówienie
            # mogło zostać skasowane w panelu po wysłaniu mu linku.
            odp = _page(request, "pay.html", status="missing", promo=None,
                        item="", amount="", reference="", pay_token=token)
            odp.status_code = 404
            return odp
        # Otwarcie linku to jedyny sygnał między „wysłałem mu linka" a wpłatą —
        # w dzienniku klienta odpowiada na „widział w ogóle tę stronę?".
        if o.trader_id:
            telemetry.track("pay_link_opened", o.trader_id, order=o.id)
        # Rabat partnera widać na stronie partnera (JSON niżej), a na NASZEJ
        # stronie płatności tego samego zamówienia nie było go wcale — klient
        # z tym samym linkiem widział inną obietnicę zależnie od domeny.
        rabat = _rabat_partnera(session, o)
        return _page(request, "pay.html",
                     item=_pozycja_zamowienia(session, o),
                     amount=f"{o.amount_usd:,.2f}".removesuffix(".00"),
                     reference=f"PTF-{o.id}", status=o.status, pay_token=token,
                     bogo=bool(getattr(o, "bogo", False)),
                     rabat_list=(f"{rabat['list_amount_usd']:,.2f}".removesuffix(".00")
                                 if rabat else None),
                     rabat_usd=(f"{rabat['discount_usd']:,.2f}".removesuffix(".00")
                                if rabat else None),
                     rabat_pct=rabat.get("discount_pct"),
                     # Belka „kup challenge, dostaniesz rozmiar wyżej" obiecuje
                     # mechanikę checkoutu, której to zamówienie NIE dostanie —
                     # kwota jest ustalona ręcznie. Na tej stronie jej nie ma.
                     promo=None)
    finally:
        session.close()


def _rabat_partnera(session, o: Order) -> dict:
    """Cena z cennika i to, co z niej zeszło — tylko dla zamówień ze stemplem partnera.

    Rabat liczymy z LICZB tego zamówienia (cennik minus kwota), a nie z aktualnego
    `PARTNER_DISCOUNT_PCT`. Procent w env zmienia się szybciej, niż żyją zamówienia,
    a pokazanie dzisiejszej stawki na wczorajszym linku byłoby nieprawdą na stronie,
    gdzie ktoś zaraz wpisze numer karty.

    Pusty słownik, gdy nie ma czego pokazać: kwotę ustala ręcznie admin, więc może
    wyjść równa cennikowi albo wyższa (dopłaty) — a przekreślona cena NIŻSZA od
    płaconej to najgorszy możliwy komunikat na stronie płatności.
    """
    if not (o.coupon or "").strip().upper().startswith("PARTNER"):
        return {}
    prod = session.query(Product).filter(Product.key == o.product_key).first()
    if not prod:
        return {}
    katalog = round(float(prod.price_usd or 0), 2)
    kwota = round(float(o.amount_usd or 0), 2)
    if katalog <= kwota:
        return {}
    wynik = {"list_amount_usd": katalog, "discount_usd": round(katalog - kwota, 2)}
    pct = round((katalog - kwota) / katalog * 100)
    # Rabat drobniejszy niż pół procenta zaokrągla się do zera, a „−0%" przy
    # kwocie wygląda jak błąd — wtedy zostaje sama kwota, procent pomijamy.
    if pct >= 1:
        wynik["discount_pct"] = pct
    return wynik


@app.get("/api/pay/{token}")
def pay_order_public(request: Request, token: str,
                     x_partner_token: str | None = Header(default=None)):
    """To samo, co pokazuje `/pay/<token>`, tyle że w JSON — dla strony partnera.

    Partner sprzedaje pod własną marką i jego klient nie ma powodu lądować na
    naszej domenie w połowie zakupu. Jego serwer pyta tu o pozycję i kwotę,
    rysuje to u siebie, a przycisk woła zwykły `/api/pay/<token>/start`.

    Wychodzi dokładnie tyle, co ze strony płatności: pozycja, kwota, numer
    i status. ŻADNYCH danych osobowych — po drugiej stronie stoi cudzy serwer,
    a zamówienie zna mail i nazwisko kupującego.

    Sekret w nagłówku, mimo że sam token już jest wstępem do strony: to jest
    wejście dla jednej maszyny, nie dla przeglądarki. Bez nagłówka wyciekniony
    link stawałby się od razu API, po którym da się skryptować. CORS-a nie ma
    w całej aplikacji, więc przeglądarka i tak tu nie wejdzie.
    """
    oczekiwany = settings.partner_api_token
    if not oczekiwany or not x_partner_token or not secrets.compare_digest(
            x_partner_token, oczekiwany):
        raise HTTPException(401, "Unauthorized")
    # Limit na nieistniejące tokeny też przechodzi tędy — inaczej ktoś z ważnym
    # sekretem mógłby przez nas przelecieć listę zgadywanych linków.
    _rate_limit(request, "partner_pay", 60)

    session = SessionLocal()
    try:
        o = _zamowienie_po_tokenie(session, token)
        return {"item": _pozycja_zamowienia(session, o),
                "amount_usd": round(float(o.amount_usd or 0), 2),
                "currency": "USD",
                "reference": f"PTF-{o.id}",
                "status": o.status,
                # Jak z rabatem niżej: obiecane drugie konto, którego nie widać
                # na stronie płatności, istnieje tylko w rozmowie na Telegramie.
                "bogo": bool(getattr(o, "bogo", False)),
                # Klient partnera dostał cenę niższą od cennikowej i ma prawo to
                # ZOBACZYĆ. Bez tego rabat istnieje wyłącznie w rozmowie na
                # Telegramie i w naszym raporcie — czyli dla kupującego wcale.
                **_rabat_partnera(session, o)}
    finally:
        session.close()


@app.post("/api/pay/{token}/start")
def pay_start(request: Request, token: str,
              x_partner_token: str | None = Header(default=None)):
    """Otwiera kasę dla linku /pay/<token>. Bez logowania — token JEST wstępem.

    Zamówienie ma już właściciela (admin wystawił je konkretnemu traderowi),
    więc płatność i tak wyląduje na jego koncie; wymaganie tu sesji zamieniłoby
    jeden klik w „najpierw się zaloguj" i psuło cały sens linku.

    Nagłówek z sekretem partnera jest OPCJONALNY i nie decyduje o dostępie —
    mówi tylko, KTO kliknął: nasza strona płatności czy strona partnera. Od tego
    zależy jedno, adres powrotu ze Stripe'a. Bez tego klient partnera po zapłacie
    ląduje na naszej domenie, pod marką, o której nikt mu nie mówił. Adresu nie
    bierzemy z żądania, tylko z własnej konfiguracji — inaczej ten nagłówek
    byłby otwartym przekierowaniem z kasy.
    """
    # Publiczny POST bez logowania: bez limitu każdy z linkiem (albo zgadujący
    # tokeny) mógłby taśmowo otwierać sesje Stripe. 10/min starcza człowiekowi
    # z nawiązką, a spamera zatrzymuje.
    _rate_limit(request, "pay_start", 10)
    session = SessionLocal()
    try:
        o = _zamowienie_po_tokenie(session, token)
        if o.status == "paid":
            raise HTTPException(400, "This order has already been paid.")
        if o.status != "pending":
            raise HTTPException(400, "This payment link is no longer active.")
        sekret = settings.partner_api_token
        od_partnera = bool(sekret and x_partner_token
                           and secrets.compare_digest(x_partner_token, sekret))
        powrot = (f"{settings.partner_pay_base_url}/pay/{o.pay_token}"
                  if od_partnera and settings.partner_pay_base_url else None)
        if not settings.stripe_enabled:
            # Dev/mock: domknięcie robi portal (wymaga zalogowania), tak samo
            # jak przy zwykłym checkoucie bez kluczy Stripe'a.
            return {"checkout_url": f"{settings.app_base_url}/portal?mock_order={o.id}"}
        return {"checkout_url": billing.open_stripe_session(
            session, o, _pozycja_zamowienia(session, o), powrot=powrot)}
    finally:
        session.close()


def _cert_preview(request, cert_token: str, trader_masked: str, acc: Account | None, *,
                  eyebrow: str, when, payout_share: float | None = None) -> dict:
    """Dane repliki dokumentu pokazywanej obok formularza weryfikacji.

    Dokładnie to, co widnieje na samym certyfikacie, z JEDNYM wyjątkiem:
    nazwisko jest maskowane. Certyfikat ma `noindex`, a /verify jest publicznie
    indeksowalne — pełne nazwisko zostaje na dokumencie.
    """
    data = (when or datetime.now(timezone.utc)).strftime("%d %b %Y")
    rozmiar = f"${(acc.initial_balance if acc else 0):,.0f}"
    if payout_share is not None:
        kwota = (f"${payout_share:,.0f}" if float(payout_share).is_integer()
                 else f"${payout_share:,.2f}")
        return {"variant": "payout", "eyebrow": eyebrow, "amount_label": "For the amount of",
                "amount": kwota, "person": trader_masked,
                "meta": [{"value": data, "label": "Date"},
                         {"value": rozmiar, "label": "Account size"}],
                "token": cert_token,
                "qr_svg": _qr_svg(f"{_public_base(request)}/verify/{cert_token}")}
    return {"variant": "pass", "eyebrow": eyebrow, "amount_label": "Account size",
            "amount": rozmiar, "person": trader_masked,
            "meta": [{"value": data, "label": "Date"},
                     {"value": f"{acc.steps}-Step" if acc and acc.steps else "Instant funding",
                      "label": "Program"}],
            "token": cert_token,
            "qr_svg": _qr_svg(f"{_public_base(request)}/verify/{cert_token}")}


@app.get("/verify")
@app.get("/verify/{cert_token}")
def verify_page(request: Request, cert_token: str | None = None):
    """Weryfikacja certyfikatu po ID — sygnał wiarygodności (wzorzec FTMO/OTF),
    u nas na PRAWDZIWEJ bazie. Nazwisko maskowane; pełne jest na samym certyfikacie.

    Sprawdzamy OBA rodzaje dokumentów: certyfikat konta (zaliczenie/funded) i
    certyfikat wypłaty. Numer z certyfikatu wypłaty też musi się tu weryfikować —
    inaczej odbiorca, który go dostał, słyszy „nie znaleziono".
    """
    result = None
    podglad = None   # replika dokumentu obok formularza (te same klasy co certyfikat)
    if cert_token:
        session = SessionLocal()

        def etykieta(a: Account | None) -> str:
            """Nazwa planu, nie klucz z bazy — „2-Step 100K", nie „2step-100k"."""
            if not a:
                return "—"
            prod = session.query(Product).filter(Product.key == a.product_key).first()
            return prod.label if prod else a.product_key

        try:
            cert = session.query(Certificate).filter(Certificate.cert_token == cert_token).first()
            acc = (session.get(Account, cert.account_id) if cert
                   else session.query(Account).filter(Account.cert_token == cert_token).first())
            payout = (session.query(Payout).filter(Payout.cert_token == cert_token).first()
                      if not acc else None)
            if cert and acc:
                result = {"found": True, "kind": "account", "open_url": f"/certificate/{cert_token}",
                          "trader": _mask_name(acc.trader_name),
                          "size": acc.initial_balance, "product": etykieta(acc),
                          "status": CERT_KINDS.get(cert.kind, ("Evaluation passed",))[0],
                          "token": cert_token}
                podglad = _cert_preview(request, cert_token, result["trader"], acc,
                                        eyebrow=CERT_KINDS.get(cert.kind, ("Evaluation passed",))[0],
                                        when=cert.issued_at)
            elif acc and _cert_eligible(acc):
                rodzaj = "funded" if acc.status == "funded" else "phase_1"
                result = {"found": True, "kind": "account", "open_url": f"/certificate/{cert_token}",
                          "trader": _mask_name(acc.trader_name),
                          "size": acc.initial_balance, "product": etykieta(acc),
                          "status": "Funded Trader" if acc.status == "funded" else "Evaluation Passed",
                          "token": cert_token}
                podglad = _cert_preview(request, cert_token, result["trader"], acc,
                                        eyebrow=CERT_KINDS[rodzaj][0],
                                        when=acc.closed_at or acc.created_at)
            elif payout:
                pacc = session.get(Account, payout.account_id)
                result = {"found": True, "kind": "payout", "open_url": f"/payout/{cert_token}",
                          "trader": _mask_name(pacc.trader_name if pacc else ""),
                          "size": (pacc.initial_balance if pacc else 0.0),
                          "product": etykieta(pacc),
                          "status": "Payout paid",
                          "amount": round(payout.trader_share, 2),
                          "issued": payout.ts.strftime("%d %b %Y") if payout.ts else None,
                          "token": cert_token}
                podglad = _cert_preview(request, cert_token, result["trader"], pacc,
                                        eyebrow="Payout", when=payout.ts,
                                        payout_share=payout.trader_share)
            else:
                result = {"found": False}
        finally:
            session.close()
    return _page(request, "verify.html", result=result, preview=podglad,
                 token=cert_token or "",
                 signatory=settings.cert_signatory or None,
                 signatory_label=settings.cert_signatory_label.replace(":", ""),
                 sample_qr=_qr_svg(f"{_public_base(request)}/verify"))


def _admin_z_ciasteczka(request: Request) -> Trader | None:
    """Trader-administrator z ciasteczka sesji albo None.

    Ciasteczko slozy WYLACZNIE do wpuszczania na strone panelu — API dalej
    uwierzytelnia sie naglowkiem Authorization. Dzieki temu samo istnienie
    ciasteczka nie otwiera drogi do zadan CSRF: cudza strona moze je dolaczyc
    do requestu, ale nie do naglowka.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    tid = auth.parse_token(token)
    if tid is None:
        return None
    session = SessionLocal()
    try:
        tr = session.get(Trader, tid)
        return tr if (tr and tr.is_admin) else None
    finally:
        session.close()


@app.get("/admin")
def dashboard(request: Request):
    """Panel admina — dla kogokolwiek innego ta strona po prostu nie istnieje.

    Zwracamy 404, a nie 401/403 i nie przekierowanie: odpowiedz „brak dostepu"
    potwierdzalaby, ze pod tym adresem cos jest. Przy 404 osoba bez sesji
    administratora nie dowie sie nawet, ze panel istnieje, ani jak wyglada jego
    nawigacja.

    Wyjatek `?pwa=1`: zainstalowana na telefonie PWA admina startuje z
    `/admin?pwa=1` (manifest-admin.json), a na iOS instalacja ma WLASNY,
    pusty slojik ciasteczek — zimny start bez tej furtki to martwy ekran 404
    bez paska adresu. Furtka oddaje wylacznie goly szkielet: renderuje sie
    niewidoczny (`visibility:hidden`) i admin-panel.js bez waznego tokenu
    natychmiast odbija na /portal?next=/admin. Struktura panelu i tak jest
    jawna w /static/js/admin-panel.js, wiec 404 chroni fakt istnienia adresu,
    nie tresc — a adres z `?pwa=1` zna tylko ten, kto zna panel.
    """
    if _admin_z_ciasteczka(request) is None \
            and request.query_params.get("pwa") != "1":
        raise HTTPException(404, "Not Found")
    # Render przez Jinja (nie FileResponse), żeby nazwa marki szła z SITE_NAME
    # zamiast być wpisana na sztywno w dwóch dodatkowych miejscach.
    return _page(request, "dashboard.html")


@app.get("/docs", include_in_schema=False)
def api_docs(request: Request):
    """Swagger tylko dla admina — ta sama bramka i ten sam 404 co /admin.

    Publiczne /docs to darmowa mapa całego API (łącznie z endpointami
    administracyjnymi) dla każdego ciekawskiego.
    """
    if _admin_z_ciasteczka(request) is None:
        raise HTTPException(404, "Not Found")
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json",
                               title=f"{settings.site_name} API")


@app.get("/openapi.json", include_in_schema=False)
def api_openapi(request: Request):
    if _admin_z_ciasteczka(request) is None:
        raise HTTPException(404, "Not Found")
    return app.openapi()


@app.get("/portal")
def portal(request: Request):
    # Konto administratora ma WYLACZNIE panel admina — portal tradera dla niego
    # nie istnieje, wiec zalogowany admin jest odsylany od progu. Bez sesji
    # admina strona dziala normalnie: to tu jest jedyny ekran logowania,
    # takze dla administratorow.
    if _admin_z_ciasteczka(request) is not None:
        return RedirectResponse("/admin", status_code=302)
    return _page(request, "portal.html")
