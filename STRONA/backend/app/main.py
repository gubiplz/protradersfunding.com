"""FastAPI: REST API + dashboardy.

Strony:
  /         -> landing sprzedażowy (hero, cennik z /api/products, zasady, FAQ)
  /admin    -> panel admina (konta, payouty, KYC, zamówienia)
  /portal   -> portal tradera (rejestracja/logowanie, sklep, moje konta, KYC, wypłaty)
  /docs     -> API docs

Uruchomienie:  uvicorn app.main:app --reload  (z katalogu backend/)
"""
from __future__ import annotations

import json
import os
import random
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func

from . import auth, billing, catalog, metaquotes_web, notify, poller, provisioning, push, rules, telemetry, tradebot
from .config import get_settings
from .db import SessionLocal, init_db
from .models import (Account, AppSetting, Breach, Certificate, CreditLedger, EquitySnapshot,
                     JournalEntry, KycFile, Notification, Order, Payout, PayoutRequest,
                     PoolAccount, Product, PushSubscription, SupportTicket, TelemetryEvent,
                     TicketMessage, Trade, Trader)

settings = get_settings()
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
STATIC = Path(__file__).resolve().parent.parent / "static"
UPLOADS = Path(__file__).resolve().parent.parent / "uploads"


# --------------------------------------------------------------------------- #
#  Seed demo (admin, produkty, traderzy + konta)                              #
# --------------------------------------------------------------------------- #
def seed_demo() -> None:
    session = SessionLocal()
    try:
        catalog.seed_products(session)

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
                ("peter@demo.test", "Peter Wagner", "2step-10k", "static"),
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _migruj_login_admina()
    if settings.auto_seed:
        seed_demo()   # produkty + admin zawsze; konta demo tylko w trybie sim
    _warn_if_placeholder_provisioning()
    if settings.poller_enabled:
        poller.start()
    yield
    await poller.stop()


# Swagger/OpenAPI schowane za bramka admina (routy nizej) — publiczne /docs
# wystawialoby cala mape API kazdemu.
app = FastAPI(title=f"{settings.site_name} API", version="0.7.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return FileResponse(str(STATIC / "robots.txt"), media_type="text/plain")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(STATIC / "img" / "favicon.png"), media_type="image/png")


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
    }
    if admin_view:
        d["mt5_backed"] = bool(getattr(acc, "mt5_backed", True))
        d["bot_enabled"] = bool(getattr(acc, "bot_enabled", False))
        d["bot_paused"] = bool(getattr(acc, "bot_paused", False))
        d["bot_style"] = getattr(acc, "bot_style", None)
        d["bot_pace"] = getattr(acc, "bot_pace", None)
        d["bot_target_pct"] = getattr(acc, "bot_target_pct", 0.0) or 0.0
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
    d["equity_curve"] = _equity_curve(session, acc)
    d["breaches"] = [{"ts": b.ts.isoformat(), "type": b.type, "detail": b.detail} for b in breaches]
    d["payouts"] = [{"ts": p.ts.isoformat(), "profit_amount": p.profit_amount,
                     "trader_share": p.trader_share, "paid": p.paid} for p in payouts]
    d["payout_requests"] = [{"id": r.id, "profit_amount": r.profit_amount,
                             "trader_share": r.trader_share, "status": r.status} for r in preqs]
    return d


def _product_dict(p: Product) -> dict:
    return {"key": p.key, "label": p.label, "account_size": p.account_size, "steps": p.steps,
            "price_usd": p.price_usd, "profit_target_p1": p.profit_target_p1,
            "profit_target_p2": p.profit_target_p2, "max_daily_loss_pct": p.max_daily_loss_pct,
            "max_overall_loss_pct": p.max_overall_loss_pct, "drawdown_type": p.drawdown_type,
            "min_trading_days": p.min_trading_days, "profit_split_pct": p.profit_split_pct,
            "max_lots": getattr(p, "max_lots", 0.0) or 0.0}


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
_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _rate_limit(request: Request, bucket: str, limit: int, window: int = 60) -> None:
    if _RL_DISABLED:
        return
    ip = (request.headers.get("x-forwarded-for")
          or (request.client.host if request.client else "?")).split(",")[0].strip()
    now = time.time()
    key = (bucket, ip)
    hits = [t for t in _RL_HITS.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Too many attempts — try again in a minute.")
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
    """Zawsze 200 — odpowiedź nie może zdradzać, czy e-mail istnieje w bazie."""
    _rate_limit(request, "forgot", 5)
    session = SessionLocal()
    try:
        tr = session.query(Trader).filter(Trader.email == payload.email.strip().lower()).first()
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
    parsed = auth.parse_reset_token(payload.token)
    if parsed is None:
        raise HTTPException(400, "This reset link is invalid or has expired — request a new one")
    tid, pwf = parsed
    if len(payload.password) < 8:
        raise HTTPException(400, "The password must be at least 8 characters long")
    session = SessionLocal()
    try:
        tr = session.get(Trader, tid)
        if not tr:
            raise HTTPException(400, "This reset link is invalid or has expired — request a new one")
        if pwf and pwf != auth._pw_fp(tr.password_hash):
            # Hash już inny niż przy wysyłce linku — link zużyty albo hasło
            # zmienione w międzyczasie. (Tokeny bez pwf: krotkie okno przejsciowe.)
            raise HTTPException(400, "This reset link has already been used — request a new one")
        tr.password_hash = auth.hash_password(payload.password)
        session.commit()
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
            raise HTTPException(400, "This verification link is invalid or has expired — request a new one")
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
                raise HTTPException(400, "Wrong code — check the e-mail we sent you")
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
            raise HTTPException(400, "Your e-mail is already verified — contact support to change it")
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
    session = SessionLocal()
    try:
        if session.query(Trader).filter(Trader.email == email).first():
            raise HTTPException(400, "An account with this e-mail already exists")
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
            full_name=payload.full_name.strip(), referral_code=code,
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
        raise HTTPException(401, "Google sign-in failed — please try again")
    if (claims.get("aud") != settings.google_client_id
            or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
            or claims.get("email_verified") not in (True, "true")
            or not claims.get("email")):
        raise HTTPException(401, "Google sign-in failed — please try again")
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
                # Klauzula pod przyciskiem: kontynuacja przez Google = zgoda.
                terms_accepted_at=datetime.now(timezone.utc),
            )
            session.add(tr)
        # Google ręczy za adres — bramka weryfikacyjna nie ma już czego pilnować.
        tr.email_verified = True
        session.commit()
        telemetry.track("signup" if nowy else "login", tr.id, google=True)
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


@app.get("/api/auth/me")
def me(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        referred = session.query(Trader).filter(Trader.referred_by == trader.referral_code).count()
        # Granty (BOGO) niosą amount_usd opłaconego tieru dla faktury, ale to
        # NIE jest druga płatność — bez tego filtra prowizja liczyłaby się 2×.
        paid_orders = (session.query(Order)
                       .join(Trader, Trader.id == Order.trader_id)
                       .filter(Trader.referred_by == trader.referral_code, Order.status == "paid",
                               Order.provider != "grant").all())
        commission = round(sum(o.amount_usd for o in paid_orders) * catalog.AFFILIATE_COMMISSION_PCT / 100.0, 2)
        return {"id": trader.id, "email": trader.email, "full_name": trader.full_name,
                "is_admin": trader.is_admin, "kyc_status": trader.kyc_status,
                "email_verified": trader.email_verified is not False,
                # potrzebne, żeby formularz KYC podświetlił zapisany kraj na liście
                "kyc_country": trader.kyc_country,
                "kyc_reject_reason": trader.kyc_reject_reason,
                "ui_prefs": _ui_prefs_dict(trader),
                "first_name": trader.first_name, "last_name": trader.last_name, "phone": trader.phone,
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
                              "commission_earned": commission}}
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
    use_credits: bool = True           # False = zostaw kredyty sklepowe na później
    # Dane potrzebne do założenia konta demo MT5 na nazwisko klienta.
    # Zbierane w kroku płatności; zapisywane na profilu tradera.
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


@app.get("/api/coupon/{code}")
def coupon_preview(code: str):
    """Podgląd rabatu dla konfiguratora na landingu — sam procent, bez listy kodów."""
    pct = catalog.COUPONS.get(code.strip().upper())
    if not pct:
        raise HTTPException(404, "Unknown coupon code")
    return {"code": code.strip().upper(), "pct": pct}


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


@app.post("/api/checkout")
def checkout(payload: CheckoutIn, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        billing.save_customer_details(
            session, trader,
            first_name=payload.first_name, last_name=payload.last_name, phone=payload.phone,
        )
        return billing.create_checkout(session, trader, payload.product_key, payload.coupon,
                                       promo_code=payload.promo_code,
                                       weekend_trading=payload.weekend_trading,
                                       use_credits=payload.use_credits)
    finally:
        session.close()


@app.get("/api/checkout/preview")
def checkout_preview(product_key: str, coupon: str | None = None, promo_code: str | None = None,
                     weekend: bool = False, use_credits: bool = True,
                     trader: Trader = Depends(auth.current_trader)):
    """Podgląd rozbicia ceny dla modala zakupu — dokładnie ta sama matematyka
    co realny checkout (billing.compute_price), tylko bez tworzenia zamówienia.
    Serwer i tak liczy wszystko ponownie przy POST /api/checkout."""
    session = SessionLocal()
    try:
        q = billing.compute_price(session, trader, product_key, coupon,
                                  promo_code=promo_code, weekend_trading=weekend,
                                  use_credits=use_credits)
        return {"plan_price_usd": q["plan_price_usd"], "discount_pct": q["discount_pct"],
                "discount_usd": q["discount_usd"], "weekend_fee_usd": q["weekend_fee_usd"],
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
        return billing.handle_webhook(session, payload, sig)
    finally:
        session.close()


@app.get("/api/orders")
def my_orders(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        orders = session.query(Order).filter(Order.trader_id == trader.id).order_by(Order.id.desc()).all()
        produkty = {p.key: p for p in session.query(Product).all()}
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
            acc = session.get(Account, o.account_id) if o.account_id else None
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
        raise HTTPException(400, "Unknown payout method — use usdt, bank or wise")
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
            raise HTTPException(400, "Unsupported USDT network — use TRC-20, BEP-20 or Polygon")
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


@app.post("/api/me/kyc")
def submit_kyc(payload: KycIn, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        tr.kyc_status = "pending"
        tr.kyc_fullname = payload.full_name
        tr.kyc_country = payload.country
        tr.kyc_dob = payload.dob
        tr.kyc_address = payload.address
        tr.kyc_id_type = payload.id_type
        tr.kyc_id_number = payload.id_number
        tr.kyc_doc_ref = payload.doc_ref or payload.id_number or ""
        tr.kyc_submitted_at = datetime.now(timezone.utc)
        session.commit()
        telemetry.track("kyc_submitted", trader.id)
        notify.notify_admins("admin_kyc", "New KYC submission",
                             tr.kyc_fullname or tr.email)
        return {"kyc_status": tr.kyc_status}
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
            "ledger": [{k: v for k, v in r.items() if k != "ts"} for r in ledger[:100]],
        }
    finally:
        session.close()


@app.get("/api/me/achievements")
def my_achievements(trader: Trader = Depends(auth.current_trader)):
    """Odznaki liczone z REALNYCH zdarzeń na platformie — zero generowania."""
    session = SessionLocal()
    try:
        accs = session.query(Account).filter(Account.trader_id == trader.id).all()
        acc_ids = [a.id for a in accs]
        paid_order = session.query(Order).filter(Order.trader_id == trader.id,
                                                 Order.status == "paid").first() is not None
        payout = bool(acc_ids) and session.query(Payout).filter(
            Payout.account_id.in_(acc_ids)).first() is not None
        referred = session.query(Trader).filter(Trader.referred_by == trader.referral_code).count()
        prod_sizes = {p.key: p.account_size for p in session.query(Product).all()}
        scaled = any(a.status == "funded" and a.initial_balance > prod_sizes.get(a.product_key, a.initial_balance)
                     for a in accs)
        badges = [
            ("first_challenge", "First Challenge", "Purchase your first evaluation", paid_order),
            ("phase_passed", "Phase Passed", "Advance past Phase 1 of any challenge",
             any(a.phase in ("eval_2", "funded") or a.status in ("passed", "funded") for a in accs)),
            ("funded", "Funded Trader", "Get any account to funded status",
             any(a.status == "funded" for a in accs)),
            ("first_payout", "First Payout", "Receive your first performance reward", payout),
            ("days_5", "Consistent Trader", "Log 5 trading days on one account",
             any(a.trading_days_count >= 5 for a in accs)),
            ("scaled", "Scaled Up", "Trigger the +25% scaling plan on a funded account", scaled),
            ("referrer", "Ambassador", "Refer your first trader", referred >= 1),
            ("kyc", "Verified", "Complete identity verification", trader.kyc_status == "approved"),
        ]
        return [{"key": k, "name": n, "desc": d, "unlocked": bool(u)} for (k, n, d, u) in badges]
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
    """Dorobienie certyfikatu do WLASNEJ wyplaty (starsze wyplaty nie maja tokenu)."""
    session = SessionLocal()
    try:
        pay = session.get(Payout, payout_id)
        if not pay:
            raise HTTPException(404, "Payout not found")
        _own_account(session, trader, pay.account_id)   # rzuca 404 gdy nie jego
        if not pay.cert_token:
            pay.cert_token = secrets.token_urlsafe(16)[:32]
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
            },
            "requests": [{"id": r.id, "ts": r.ts.isoformat(),
                          "account": by_id[r.account_id].login if r.account_id in by_id else "?",
                          "profit_amount": r.profit_amount, "trader_share": r.trader_share,
                          "method": r.method, "status": r.status,
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


def _ticket_dict(session, t: SupportTicket, with_thread: bool = False) -> dict:
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
        return [_ticket_dict(session, t) for t in rows]
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
        out = []
        for t in rows:
            tr = session.get(Trader, t.trader_id)
            d = _ticket_dict(session, t)
            d["trader_email"] = tr.email if tr else None
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
            tr.full_name = payload.full_name.strip()[:120]
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


# --- Telemetria -------------------------------------------------------------
class TelemetryIn(BaseModel):
    name: str
    props: dict | None = None


# Whitelist zdarzeń klienckich: endpoint wymaga logowania, ale i tak nie
# pozwalamy klientowi wstrzykiwać dowolnych nazw do statystyk admina.
# Ruch marketingowy (strona publiczna) łapie GA4 — tu tylko produkt.
_TELEMETRY_CLIENT_EVENTS = {"view_open", "pwa_install"}


@app.post("/api/telemetry")
def telemetry_ingest(payload: TelemetryIn, trader: Trader = Depends(auth.current_trader)):
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
            q = q.filter(func.date(TelemetryEvent.created_at) == day)
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
        rows = session.query(PayoutRequest).order_by(PayoutRequest.id.desc()).all()
        out = []
        for r in rows:
            acc = session.get(Account, r.account_id)
            tr = session.get(Trader, r.trader_id)
            try:
                details = json.loads(r.details) if r.details else {}
            except ValueError:
                details = {}
            out.append({"id": r.id, "account_login": acc.login if acc else None,
                        "trader_email": tr.email if tr else None, "profit_amount": r.profit_amount,
                        "trader_share": r.trader_share, "method": r.method, "details": details,
                        "status": r.status, "reject_reason": r.reject_reason,
                        "ts": r.ts.isoformat()})
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

        # zwrot opłaty za challenge przy PIERWSZEJ wypłacie (jak FTMO/The5ers)
        first_payout = session.query(Payout).filter(Payout.account_id == acc.id).count() == 0
        fee_refund = 0.0
        if first_payout:
            order = (session.query(Order).filter(Order.account_id == acc.id, Order.status == "paid")
                     .order_by(Order.id).first())
            fee_refund = round(order.amount_usd, 2) if order else 0.0

        session.add(Payout(account_id=acc.id, profit_amount=r.profit_amount,
                           trader_share=round(r.trader_share + fee_refund, 2), paid=True,
                           balance_reset=True, method=r.method))
        r.status = "paid"
        # Trader mógł poprosić o CZĘŚĆ dostępnej działki — z salda schodzi profit
        # proporcjonalny do wypłaconej kwoty (kwota / split). Pełna kwota sprowadza
        # konto dokładnie do salda startowego, jak dotychczasowy reset.
        split = acc.profit_split_pct / 100.0 if acc.profit_split_pct else 1.0
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
        r.status = "rejected"
        r.reject_reason = (payload.reason or "").strip()[:200] or None
        acc = session.get(Account, r.account_id)
        tr = session.get(Trader, r.trader_id)
        session.commit()
        if tr:
            notify.send("payout_rejected", tr.email, {
                "name": tr.full_name or tr.email, "login": acc.login if acc else "",
                "trader_share": r.trader_share,
                "reason": r.reject_reason or "not specified"})
        return {"rejected": req_id, "reason": r.reject_reason}
    finally:
        session.close()


class IssuePayoutIn(BaseModel):
    amount: float | None = None       # kwota dla tradera; None = pełny udział z zysku
    method: str = "bank"
    note: str | None = None
    reset_balance: bool = True        # jak przy zatwierdzeniu wniosku: zysk wypłacony


def _payout_dict(p: Payout, acc: Account | None = None) -> dict:
    return {"id": p.id, "ts": p.ts.isoformat() if p.ts else None,
            "profit_amount": round(p.profit_amount, 2),
            "trader_share": round(p.trader_share, 2), "paid": p.paid,
            "method": p.method, "note": p.note, "cert_token": p.cert_token,
            "cert_url": f"/payout/{p.cert_token}" if p.cert_token else None,
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
def admin_payout_certificate(payout_id: int):
    """Dorabia certyfikat do wypłaty, która powstała wcześniej (np. z wniosku)."""
    session = SessionLocal()
    try:
        p = session.get(Payout, payout_id)
        if not p:
            raise HTTPException(404, "Payout not found")
        if not p.cert_token:
            p.cert_token = secrets.token_urlsafe(16)[:32]
            session.commit()
        return _payout_dict(p, session.get(Account, p.account_id))
    finally:
        session.close()


@app.get("/api/admin/traders", dependencies=[Depends(auth.require_admin)])
def admin_traders(q: str | None = None):
    """Lista klientów (do wyszukiwarki przy przyznawaniu challenge'u)."""
    session = SessionLocal()
    try:
        query = session.query(Trader).filter(Trader.is_admin == False)  # noqa: E712
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.filter(func.lower(Trader.email).like(like) |
                                 func.lower(Trader.full_name).like(like))
        rows = query.order_by(Trader.id.desc()).limit(50).all()
        counts = dict(session.query(Account.trader_id, func.count(Account.id))
                      .group_by(Account.trader_id).all())
        return [{"id": t.id, "email": t.email, "full_name": t.full_name,
                 "kyc_status": t.kyc_status, "accounts": counts.get(t.id, 0),
                 "credits_usd": round(float(t.credits_usd or 0), 2),
                 "created_at": t.created_at.isoformat() if t.created_at else None} for t in rows]
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
    pace: str = "active"         # realistic | active | demo
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
            raise HTTPException(400, f"Account status is '{acc.status}' — the bot only runs "
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
def admin_kyc():
    """Oczekujące wnioski + historia decyzji (approved/rejected) do przeglądania."""
    session = SessionLocal()
    try:
        pending = session.query(Trader).filter(Trader.kyc_status == "pending").all()
        history = (session.query(Trader)
                   .filter(Trader.kyc_status.in_(("approved", "rejected")))
                   .order_by(Trader.kyc_reviewed_at.desc().nullslast(), Trader.id.desc())
                   .limit(200).all())
        return {"pending": [_kyc_dict(t) for t in pending],
                "history": [_kyc_dict(t) for t in history]}
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
        out = []
        for o in rows:
            tr = session.get(Trader, o.trader_id)
            out.append({"id": o.id, "trader_email": tr.email if tr else None,
                        "product_key": o.product_key, "amount_usd": o.amount_usd,
                        "status": o.status, "provider": o.provider, "coupon": o.coupon,
                        "flag": o.flag,
                        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                        "account_id": o.account_id, "created_at": o.created_at.isoformat()})
        return out
    finally:
        session.close()


class OrderFlagIn(BaseModel):
    flag: str = ""


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
        o.flag = payload.flag or None
        session.commit()
        return {"id": o.id, "flag": o.flag}
    finally:
        session.close()


@app.post("/api/admin/orders/{order_id}/mark-paid", dependencies=[Depends(auth.require_admin)])
def admin_mark_order_paid(order_id: int):
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
        o.flag = None
        session.commit()
        return {"paid": o.id, "account_id": acc.id}
    finally:
        session.close()


@app.get("/api/admin/inbox", dependencies=[Depends(auth.require_admin)])
def admin_inbox():
    """Dzwonek w panelu: ostatnie „coś przyszło" ze wszystkich kolejek.

    Agregacja z istniejących tabel (bez osobnej tabeli powiadomień admina);
    co jest „nieprzeczytane" rozstrzyga frontend po localStorage."""
    session = SessionLocal()
    try:
        emails: dict[int, str] = {}

        def email_of(tid: int) -> str:
            if tid not in emails:
                t = session.get(Trader, tid)
                emails[tid] = t.email if t else "?"
            return emails[tid]

        items = []
        for o in session.query(Order).order_by(Order.id.desc()).limit(15).all():
            items.append({"type": "order", "ts": (o.paid_at or o.created_at).isoformat(),
                          "title": f"Order #{o.id} · {o.product_key} · {o.status}",
                          "body": email_of(o.trader_id), "view": "orders"})
        for t in (session.query(Trader).filter(Trader.kyc_status == "pending")
                  .order_by(Trader.kyc_submitted_at.desc().nullslast()).limit(10).all()):
            if t.kyc_submitted_at:
                items.append({"type": "kyc", "ts": t.kyc_submitted_at.isoformat(),
                              "title": f"KYC pending · {t.kyc_fullname or t.email}",
                              "body": t.email, "view": "kyc"})
        for pr in (session.query(PayoutRequest).filter(PayoutRequest.status == "pending")
                   .order_by(PayoutRequest.id.desc()).limit(10).all()):
            items.append({"type": "payout", "ts": pr.ts.isoformat(),
                          "title": f"Payout request ${pr.trader_share:,.2f}",
                          "body": email_of(pr.trader_id), "view": "payouts"})
        for m, t in (session.query(TicketMessage, SupportTicket)
                     .join(SupportTicket, SupportTicket.id == TicketMessage.ticket_id)
                     .filter(TicketMessage.author == "trader")
                     .order_by(TicketMessage.id.desc()).limit(10).all()):
            items.append({"type": "ticket", "ts": m.ts.isoformat(),
                          "title": f"Ticket #{t.id}: {t.subject}",
                          "body": email_of(t.trader_id), "view": "tickets"})
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
                "sim_fallback": provisioning.sim_fallback_enabled(session)}
    finally:
        session.close()


class SimGenerateIn(BaseModel):
    """Ile SYMULOWANYCH wpisów dodać do puli i jakiego rozmiaru."""
    account_size: float
    count: int = 1


@app.post("/api/admin/pool/generate-simulated", dependencies=[Depends(auth.require_admin)])
def admin_pool_generate_simulated(payload: SimGenerateIn):
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
        return {"created": created}
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


@app.post("/api/admin/pool", dependencies=[Depends(auth.require_admin)])
def admin_pool_add(payload: NewPoolAccount):
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
        return {"id": p.id, "account_size": p.account_size}
    finally:
        session.close()


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
                raise HTTPException(400, "This account is assigned — its size cannot be changed")
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
    """Usuwa WOLNY wpis z puli. Przydzielonego nie ruszamy — stoi za nim konto tradera."""
    session = SessionLocal()
    try:
        p = session.get(PoolAccount, pool_id)
        if not p:
            raise HTTPException(404, "No such entry in the pool")
        if p.claimed:
            raise HTTPException(400, "This account is assigned to a trader and cannot be removed")
        session.delete(p)
        session.commit()
        return {"deleted": pool_id}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  Publiczne: konta (admin read), leaderboard, certyfikat                      #
# --------------------------------------------------------------------------- #
@app.get("/api/accounts", dependencies=[Depends(auth.require_admin)])
def list_accounts():
    session = SessionLocal()
    try:
        # Data płatności do kolumny "Paid" — jedno zapytanie zamiast N per konto.
        zaplacone = dict(session.query(Order.account_id, func.max(Order.paid_at))
                         .filter(Order.account_id.isnot(None), Order.paid_at.isnot(None))
                         .group_by(Order.account_id).all())
        emaile = dict(session.query(Trader.id, Trader.email).all())
        out = []
        for a in session.query(Account).order_by(Account.id).all():
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
                f"Order #{o.id} created — {o.product_key}, ${o.amount_usd:,.2f} ({o.status})",
                "order")
            add(o.paid_at, f"Order #{o.id} paid — ${o.amount_usd:,.2f} via {o.provider}",
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

        # Zamówienia przed kontami: orders.account_id ma FK na accounts.id.
        for model in (Order, Notification, CreditLedger, PayoutRequest,
                      JournalEntry, PushSubscription, KycFile, TelemetryEvent):
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


@app.get("/api/leaderboard")
def leaderboard():
    """Publiczny ranking — realne konta, ale nazwiska MASKOWANE (RODO/prywatność).

    Ranking liczony WPROST z bieżącego equity (balance) kont FUNDED — bez
    doliczania wypłaconych zysków i bez kont w ewaluacji. Po wypłacie trader
    świadomie schodzi w rankingu: pokazujemy stan konta, nie życiorys.
    """
    session = SessionLocal()
    try:
        accs = session.query(Account).filter(Account.status == "funded").all()
        rows = []
        for a in accs:
            equity_now = round(a.balance, 2)
            profit_pct = round((equity_now - a.initial_balance) / a.initial_balance * 100, 2)
            tr = session.get(Trader, a.trader_id) if a.trader_id else None
            country = tr.kyc_country if (tr and tr.kyc_status == "approved" and tr.kyc_country) else None
            rows.append({"trader": _mask_name(a.trader_name or (tr.full_name if tr else "")),
                         "country": country, "phase": a.phase, "status": a.status,
                         "equity": equity_now, "profit_pct": profit_pct,
                         "account_size": a.initial_balance})
        rows.sort(key=lambda r: r["profit_pct"], reverse=True)
        return rows[:20]
    finally:
        session.close()

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


def _cert_ctx(request, *, headline_plain, eyebrow, trader_name, amount_label, amount,
              blurb, meta, cert_token, seal, note=None, variant="pass") -> dict:
    """Wspólny kontekst obu certyfikatów — jeden szablon, dwa warianty.

    Świadomie BEZ numeru rachunku MT5: dokument idzie na zewnątrz, a numer konta
    nikomu tam nie służy. Weryfikację zapewnia ID certyfikatu i kod QR.
    """
    weryfikacja = f"{_public_base(request)}/verify/{cert_token}"
    return {
        "site_name": settings.site_name,
        "headline_plain": headline_plain,
        "eyebrow": eyebrow,
        "trader_name": trader_name or "—",
        "amount_label": amount_label, "amount": amount, "blurb": blurb,
        "meta": meta, "note": note,
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
def payout_certificate(request: Request, cert_token: str):
    """Certyfikat wypłaty — publiczny link po nieodgadywalnym tokenie.

    Bez rozbicia na zysk/split/metodę: to są dane wewnętrzne rachunku, a dokument
    ma potwierdzać JEDNO — że wypłata w tej kwocie została zrealizowana.
    """
    session = SessionLocal()
    try:
        p = session.query(Payout).filter(Payout.cert_token == cert_token).first()
        if not p:
            raise HTTPException(404, "Certificate not found")
        acc = session.get(Account, p.account_id)
        when = (p.ts or datetime.now(timezone.utc)).strftime("%d %b %Y")
        # Kwota bez groszy, gdy okrągła — „$9,070" czyta się lepiej niż „$9,070.00",
        # ale przy 1 049,78 nie wolno zgubić reszty.
        kwota = (f"${p.trader_share:,.0f}" if float(p.trader_share).is_integer()
                 else f"${p.trader_share:,.2f}")
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
            note=p.note, cert_token=cert_token, seal="Paid", variant="payout",
        )
        return jinja.TemplateResponse(request, "certificate.html", ctx)
    finally:
        session.close()


# Odczyty, przy ktorych warto dogonic silnik: lista kont tradera (portal),
# lista kont admina i ranking. Reszta API zostaje szybka.
_LAZY_TICK_PATHS = {"/api/me/accounts", "/api/accounts", "/api/leaderboard"}


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
    if settings.lazy_tick_sec > 0 and request.url.path in _LAZY_TICK_PATHS:
        await _lazy_tick()
    return await call_next(request)


def _require_cron(x_admin_token: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)) -> None:
    """Wpuszcza crona (Bearer CRON_SECRET) albo admina (X-Admin-Token).

    Osobno od `auth.require_admin`, bo tam nagłówek Bearer jest interpretowany
    jako token tradera — cron ma własny sekret i nie ma konta w systemie.
    """
    if x_admin_token and secrets.compare_digest(x_admin_token, settings.admin_token):
        return
    sekret = settings.cron_secret
    if sekret and authorization and authorization.lower().startswith("bearer "):
        if secrets.compare_digest(authorization.split(" ", 1)[1].strip(), sekret):
            return
    raise HTTPException(401, "Not allowed to trigger the risk engine")


@app.api_route("/api/tick", methods=["GET", "POST"], dependencies=[Depends(_require_cron)])
async def api_tick():
    """Jeden przebieg silnika ryzyka — dla hostingu bez procesu w tle.

    Na zwykłym serwerze poller kręci się sam co `POLL_INTERVAL_SEC` i ten
    endpoint jest tylko awaryjnym „szturchnięciem". Na hostingu bezserwerowym
    (Vercel) proces nie żyje między requestami, więc to cron trzyma silnik przy
    życiu — wtedy ustaw POLLER_ENABLED=false i wal tu cronem. GET jest
    obsłużony, bo Vercel Cron uderza właśnie metodą GET.
    """
    wynik = await poller.tick_once()
    # Dzienny recap jedzie na tym samym cronie (Vercel Hobby = 1 strzał/dobę);
    # push.daily_recap() sam pilnuje guardu raz-na-dobę i ciszy bez transakcji.
    recap = push.daily_recap()
    if isinstance(wynik, dict):
        return {**wynik, "daily_recap": recap}
    return {"tick": wynik, "daily_recap": recap}


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


@app.get("/api/stats", dependencies=[Depends(auth.require_admin)])
def stats():
    """Statystyki OPERACYJNE (feed, tryb Stripe, pula) — tylko admin.

    Publiczna strona używa /api/public/stats, który nie ujawnia internali.
    """
    session = SessionLocal()
    try:
        accs = session.query(Account).all()
        by_status: dict[str, int] = {}
        for a in accs:
            by_status[a.status] = by_status.get(a.status, 0) + 1
        return {"total": len(accs), "by_status": by_status,
                "funded": by_status.get("funded", 0), "active": by_status.get("active", 0),
                "failed": by_status.get("failed", 0), "feed": settings.feed,
                "stripe": "live" if settings.stripe_enabled else "mock",
                "traders": session.query(Trader).filter(Trader.is_admin == False).count(),  # noqa: E712
                "orders_paid": session.query(Order).filter(Order.status == "paid").count(),
                "pool_free": session.query(PoolAccount).filter(PoolAccount.claimed == False).count(),  # noqa: E712
                "provisioning": by_status.get("provisioning", 0)}
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
        payouts = session.query(Payout).filter(Payout.paid == True).all()  # noqa: E712
        countries = {t.kyc_country.strip().lower() for t in
                     session.query(Trader).filter(Trader.kyc_status == "approved").all()
                     if t.kyc_country}
        data = {
            "accounts_total": session.query(Account).count(),
            "active_accounts": session.query(Account).filter(Account.status == "active").count(),
            "funded_accounts": session.query(Account).filter(Account.status == "funded").count(),
            "traders_total": session.query(Trader).filter(Trader.is_admin == False).count(),  # noqa: E712
            "payouts_count": len(payouts),
            # Pełne dolary: ".96" przy sześciocyfrowej kwocie poszerzał kafel LP aż do obcięcia.
            "payouts_total_usd": int(round(sum(p.trader_share for p in payouts))),
            "largest_payout_usd": int(round(max((p.trader_share for p in payouts), default=0.0))),
            "countries_count": len(countries),
        }
        _PUBLIC_STATS_CACHE.update(ts=now, data=data)
        return data
    finally:
        session.close()


_PUBLIC_CERTS_CACHE: dict = {"ts": 0.0, "data": None}


@app.get("/api/public/certificates/recent")
def public_recent_certificates():
    """Pas "Recently issued" na landingu: PRAWDZIWE certyfikaty.

    Nazwiska maskowane jak w rankingu, ZERO tokenów i ID — publikacja linku do
    cudzego certyfikatu to decyzja właściciela, nie nasza. Pusta baza -> [].
    """
    now = monotonic()
    if _PUBLIC_CERTS_CACHE["data"] is not None and now - _PUBLIC_CERTS_CACHE["ts"] < 60:
        return _PUBLIC_CERTS_CACHE["data"]
    session = SessionLocal()
    try:
        out = []
        rows = (session.query(Certificate, Account, Trader)
                .join(Account, Certificate.account_id == Account.id)
                .join(Trader, Account.trader_id == Trader.id)
                .order_by(Certificate.issued_at.desc()).limit(12).all())
        for cert, acc, tr in rows:
            out.append({
                "kind": cert.kind,
                "kind_label": CERT_KINDS.get(cert.kind, (cert.kind,))[0],
                "account_size": acc.initial_balance,
                "trader": _mask_name(tr.full_name),
                "issued_at": cert.issued_at.isoformat() if cert.issued_at else None,
            })
        pays = (session.query(Payout, Account, Trader)
                .join(Account, Payout.account_id == Account.id)
                .join(Trader, Account.trader_id == Trader.id)
                .filter(Payout.cert_token != None, Payout.paid == True)  # noqa: E711,E712
                .order_by(Payout.ts.desc()).limit(12).all())
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
        data = out[:12]
        _PUBLIC_CERTS_CACHE.update(ts=now, data=data)
        return data
    finally:
        session.close()


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


def _page(request: Request, template: str, **extra):
    ctx = {"site_name": settings.site_name, "support_email": settings.support_email,
           "base_url": _public_base(request), "asset_v": ASSET_V,
           "ga_id": settings.ga_measurement_id, "promo": _promo_ctx(),
           "google_client_id": settings.google_client_id, **extra}
    return jinja.TemplateResponse(request, template, ctx)


@app.get("/")
def home(request: Request):
    """Publiczna strona sprzedażowa (cennik/objectives z /api/products)."""
    # QR w podglądzie certyfikatu prowadzi na REALNĄ stronę weryfikacji —
    # atrapa kodu na landingu byłaby obietnicą bez pokrycia.
    # Katalog wstrzyknięty w HTML: konfigurator w hero renderuje się w tym
    # samym paincie co reszta strony, bez pół sekundy pustych "—".
    session = SessionLocal()
    try:
        prods = _products_payload(session)
    finally:
        session.close()
    return _page(request, "home.html",
                 sample_qr=_qr_svg(f"{_public_base(request)}/verify"),
                 products=prods)


@app.get("/faq")
def faq_page(request: Request):
    return _page(request, "faq.html")


@app.get("/affiliate")
def affiliate_page(request: Request):
    return _page(request, "affiliate.html")


@app.get("/install")
def install_page(request: Request):
    """Instrukcja instalacji PWA (iOS: Dodaj do ekranu, Android: beforeinstallprompt)."""
    return _page(request, "install.html")


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
    """
    if _admin_z_ciasteczka(request) is None:
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
    return _page(request, "portal.html")
