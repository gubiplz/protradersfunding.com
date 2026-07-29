"""FastAPI: REST API + dashboardy.

Strony:
  /         -> landing sprzedażowy (hero, cennik z /api/products, zasady, FAQ)
  /admin    -> panel admina (konta, payouty, KYC, zamówienia)
  /portal   -> portal tradera (rejestracja/logowanie, sklep, moje konta, KYC, wypłaty)
  /docs     -> API docs

Uruchomienie:  uvicorn app.main:app --reload  (z katalogu backend/)
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func

from . import auth, billing, catalog, metaquotes_web, notify, poller, provisioning, rules, tradebot
from .config import get_settings
from .db import SessionLocal, init_db
from .models import (Account, Breach, Certificate, EquitySnapshot, JournalEntry, Order, Payout,
                     PayoutRequest, PoolAccount, Product, SupportTicket, TicketMessage,
                     Trade, Trader)

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
                ("jan@demo.pl", "Jan Kowalski", "2step-100k", "static"),
                ("anna@demo.pl", "Anna Nowak", "2step-100k", "trailing"),
                ("piotr@demo.pl", "Piotr Wiśniewski", "2step-10k", "static"),
                ("maria@demo.pl", "Maria Lewandowska", "2step-100k", "static"),
                ("tomek@demo.pl", "Tomasz Zieliński", "1step-100k", "trailing"),
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
    if settings.auto_seed:
        seed_demo()   # produkty + admin zawsze; konta demo tylko w trybie sim
    _warn_if_placeholder_provisioning()
    if settings.poller_enabled:
        poller.start()
    yield
    await poller.stop()


app = FastAPI(title=f"{settings.site_name} API", version="0.7.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return FileResponse(str(STATIC / "robots.txt"), media_type="text/plain")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(STATIC / "img" / "favicon.png"), media_type="image/png")


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


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/signup")
def signup(payload: SignupIn):
    session = SessionLocal()
    try:
        if session.query(Trader).filter(Trader.email == payload.email.lower()).first():
            raise HTTPException(400, "Konto z tym e-mailem już istnieje")
        tr = Trader(
            email=payload.email.lower(), password_hash=auth.hash_password(payload.password),
            full_name=payload.full_name, referral_code=_gen_ref_code(),
            referred_by=(payload.referral or None),
        )
        session.add(tr)
        session.commit()
        notify.send("welcome", tr.email, {"name": tr.full_name or tr.email})
        return {"token": auth.make_token(tr.id), "trader": {"id": tr.id, "email": tr.email,
                "full_name": tr.full_name, "referral_code": tr.referral_code}}
    finally:
        session.close()


@app.post("/api/auth/login")
def login(payload: LoginIn):
    session = SessionLocal()
    try:
        tr = session.query(Trader).filter(Trader.email == payload.email.lower()).first()
        if not tr or not auth.verify_password(payload.password, tr.password_hash):
            raise HTTPException(401, "Błędny e-mail lub hasło")
        return {"token": auth.make_token(tr.id), "trader": {"id": tr.id, "email": tr.email,
                "full_name": tr.full_name, "is_admin": tr.is_admin, "referral_code": tr.referral_code}}
    finally:
        session.close()


@app.get("/api/auth/me")
def me(trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        referred = session.query(Trader).filter(Trader.referred_by == trader.referral_code).count()
        paid_orders = (session.query(Order)
                       .join(Trader, Trader.id == Order.trader_id)
                       .filter(Trader.referred_by == trader.referral_code, Order.status == "paid").all())
        commission = round(sum(o.amount_usd for o in paid_orders) * catalog.AFFILIATE_COMMISSION_PCT / 100.0, 2)
        return {"id": trader.id, "email": trader.email, "full_name": trader.full_name,
                "is_admin": trader.is_admin, "kyc_status": trader.kyc_status,
                # potrzebne, żeby formularz KYC podświetlił zapisany kraj na liście
                "kyc_country": trader.kyc_country,
                "first_name": trader.first_name, "last_name": trader.last_name, "phone": trader.phone,
                "referral_code": trader.referral_code,
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
    # Dane potrzebne do założenia konta demo MT5 na nazwisko klienta.
    # Zbierane w kroku płatności; zapisywane na profilu tradera.
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


@app.get("/api/products")
def list_products():
    session = SessionLocal()
    try:
        prods = session.query(Product).filter(Product.active == True).order_by(Product.steps, Product.account_size).all()  # noqa: E712
        return [_product_dict(p) for p in prods]
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
        return billing.create_checkout(session, trader, payload.product_key, payload.coupon)
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
            raise HTTPException(404, "Konto nie istnieje")
        _ensure_cert_token(session, acc)
        return _account_detail(session, acc)
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
        return {"kyc_status": tr.kyc_status}
    finally:
        session.close()


@app.post("/api/accounts/{account_id}/payout-request")
def request_payout(account_id: int, payload: PayoutReqIn, trader: Trader = Depends(auth.current_trader)):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc or acc.trader_id != trader.id:
            raise HTTPException(404, "Konto nie istnieje")
        if acc.status != "funded":
            raise HTTPException(400, "Wypłata tylko dla konta funded")
        tr = session.get(Trader, trader.id)
        if tr.kyc_status != "approved":
            raise HTTPException(403, "Najpierw przejdź weryfikację KYC")
        profit = round(acc.balance - acc.initial_balance, 2)
        if profit <= 0:
            raise HTTPException(400, "Brak zysku do wypłaty")
        share = round(profit * acc.profit_split_pct / 100.0, 2)
        pr = PayoutRequest(account_id=acc.id, trader_id=tr.id, profit_amount=profit,
                           trader_share=share, method=payload.method, status="pending")
        session.add(pr)
        session.commit()
        notify.send("payout_requested", tr.email, {"name": tr.full_name or tr.email,
                    "login": acc.login, "profit_amount": profit, "trader_share": share})
        return {"id": pr.id, "profit_amount": profit, "trader_share": share, "status": "pending"}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  PANEL KLIENTA — activity, journal, tickety, payouts, achievements, settings #
# --------------------------------------------------------------------------- #
def _own_account(session, trader: Trader, account_id: int) -> Account:
    acc = session.get(Account, account_id)
    if not acc or acc.trader_id != trader.id:
        raise HTTPException(404, "Konto nie istnieje")
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
        raise HTTPException(400, "Nieznany rodzaj certyfikatu")
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
            raise HTTPException(404, "Wypłata nie istnieje")
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
                          "status": r.status} for r in reqs],
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
        raise HTTPException(400, "Tytuł wpisu jest wymagany")
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
            raise HTTPException(404, "Wpis nie istnieje")
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
        raise HTTPException(400, "Temat i treść są wymagane")
    session = SessionLocal()
    try:
        t = SupportTicket(trader_id=trader.id, subject=subject[:200])
        session.add(t)
        session.flush()
        session.add(TicketMessage(ticket_id=t.id, author="trader", body=message[:20000]))
        session.commit()
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
            raise HTTPException(404, "Ticket nie istnieje")
        return _ticket_dict(session, t, with_thread=True)
    finally:
        session.close()


@app.post("/api/me/tickets/{ticket_id}/reply")
def ticket_reply(ticket_id: int, payload: TicketReplyIn, trader: Trader = Depends(auth.current_trader)):
    body = payload.message.strip()
    if not body:
        raise HTTPException(400, "Treść odpowiedzi jest wymagana")
    session = SessionLocal()
    try:
        t = session.get(SupportTicket, ticket_id)
        if not t or t.trader_id != trader.id:
            raise HTTPException(404, "Ticket nie istnieje")
        if t.status == "closed":
            raise HTTPException(400, "Ticket jest zamknięty")
        session.add(TicketMessage(ticket_id=t.id, author="trader", body=body[:20000]))
        t.status = "open"
        session.commit()
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
            raise HTTPException(404, "Ticket nie istnieje")
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
            raise HTTPException(404, "Ticket nie istnieje")
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
        raise HTTPException(400, "Nowe hasło musi mieć min. 8 znaków")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        if not auth.verify_password(payload.current_password, tr.password_hash):
            raise HTTPException(400, "Obecne hasło jest nieprawidłowe")
        tr.password_hash = auth.hash_password(payload.new_password)
        session.commit()
        return {"ok": True}
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
            raise HTTPException(400, "Hasło jest nieprawidłowe")
        if tr.is_admin:
            raise HTTPException(400, "Konta administratora nie można usunąć z portalu")
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
        raise HTTPException(400, "Nie przesłano żadnego pliku")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader.id)
        saved = []
        for kind, up in files.items():
            if up is None:
                continue
            ext = _KYC_MIME.get(up.content_type)
            if not ext:
                raise HTTPException(400, f"{kind}: dozwolone formaty to JPG/PNG/PDF")
            data = await up.read()
            if len(data) > _KYC_MAX_BYTES:
                raise HTTPException(400, f"{kind}: plik przekracza 5 MB")
            dirp = UPLOADS / "kyc" / str(tr.id)
            dirp.mkdir(parents=True, exist_ok=True)
            fname = f"{kind}-{secrets.token_hex(6)}{ext}"
            (dirp / fname).write_bytes(data)
            setattr(tr, _KYC_KINDS[kind], fname)
            saved.append(kind)
        session.commit()
        return {"uploaded": saved}
    finally:
        session.close()


@app.get("/api/admin/kyc/{trader_id}/doc/{kind}", dependencies=[Depends(auth.require_admin)])
def admin_kyc_doc(trader_id: int, kind: str):
    if kind not in _KYC_KINDS:
        raise HTTPException(404, "Nieznany rodzaj dokumentu")
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        fname = getattr(tr, _KYC_KINDS[kind], None) if tr else None
        if not fname:
            raise HTTPException(404, "Brak dokumentu")
        path = UPLOADS / "kyc" / str(trader_id) / fname
        if not path.exists():
            raise HTTPException(404, "Plik nie istnieje")
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
            out.append({"id": r.id, "account_login": acc.login if acc else None,
                        "trader_email": tr.email if tr else None, "profit_amount": r.profit_amount,
                        "trader_share": r.trader_share, "method": r.method, "status": r.status,
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
            raise HTTPException(404, "Wniosek nie istnieje lub już rozpatrzony")
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
                           balance_reset=True))
        r.status = "paid"
        # po wypłacie konto wraca do salda startowego (wypłacamy zysk)
        acc.balance = acc.initial_balance
        acc.equity = acc.initial_balance
        acc.peak_equity = acc.initial_balance
        acc.day_start_equity = acc.initial_balance
        acc.day_start_balance = acc.initial_balance
        acc.best_day_profit = 0.0
        session.commit()
        notify.send("payout_approved", tr.email, {"name": tr.full_name or tr.email,
                    "login": acc.login, "trader_share": round(r.trader_share + fee_refund, 2),
                    "fee_refund": bool(fee_refund)})
        return {"approved": req_id, "fee_refund": fee_refund,
                "total_paid": round(r.trader_share + fee_refund, 2)}
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
def admin_issue_payout(account_id: int, payload: IssuePayoutIn):
    """Wystawia wypłatę i od razu certyfikat — bez czekania na wniosek tradera.

    Idzie tą samą ścieżką księgową co zatwierdzenie wniosku: powstaje wiersz
    `Payout`, a konto wraca do salda startowego (zysk został wypłacony).
    """
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Konto nie istnieje")

        if acc.status != "funded":
            raise HTTPException(400, "Wypłatę można wystawić tylko na koncie funded — "
                                     f"to konto ma status '{acc.status}'")
        profit = round(max(0.0, acc.balance - acc.initial_balance), 2)
        share = payload.amount if payload.amount is not None else round(
            profit * acc.profit_split_pct / 100.0, 2)
        share = round(float(share), 2)
        if share <= 0:
            raise HTTPException(400, "Kwota wypłaty musi być dodatnia "
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
                         "cert_url": f"{settings.app_base_url}/payout/{p.cert_token}"})
        return _payout_dict(p, acc)
    finally:
        session.close()


@app.get("/api/admin/accounts/{account_id}/payouts", dependencies=[Depends(auth.require_admin)])
def admin_account_payouts(account_id: int):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Konto nie istnieje")
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
            raise HTTPException(404, "Wypłata nie istnieje")
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
                 "created_at": t.created_at.isoformat() if t.created_at else None} for t in rows]
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
            raise HTTPException(404, "Trader nie istnieje")
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
            raise HTTPException(404, "Konto nie istnieje")
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
        raise HTTPException(400, "Nieznany rodzaj certyfikatu")
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Konto nie istnieje")
        if not _cert_kind_available(acc, payload.kind):
            etap = CERT_KINDS[payload.kind][0]
            raise HTTPException(400, f"Konto nie osiągnęło etapu: {etap}. "
                                     f"Przestaw najpierw fazę konta.")
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
        raise HTTPException(400, "Nieznana faza")
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Konto nie istnieje")
        if payload.phase == "eval_2" and acc.steps < 2:
            raise HTTPException(400, "Ten plan nie ma drugiego etapu")
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
            raise HTTPException(404, "Konto nie istnieje")
        if acc.status == "failed":
            raise HTTPException(400, "To konto jest już zamknięte")

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
            raise HTTPException(404, "Konto nie istnieje")
        if acc.status not in ("active", "funded"):
            raise HTTPException(400, f"Konto ma status '{acc.status}' — bot działa tylko "
                                     f"na kontach aktywnych i funded")
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
            raise HTTPException(404, "Konto nie istnieje")
        if not acc.bot_enabled:
            raise HTTPException(400, "Trade BOT nie jest uruchomiony na tym koncie")
        if payload.paused is None and payload.target_pct is None:
            raise HTTPException(400, "Podaj 'paused' albo 'target_pct'")
        if payload.target_pct is not None:
            if payload.target_pct < 0:
                raise HTTPException(400, "Cel nie może być ujemny")
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
            raise HTTPException(404, "Konto nie istnieje")
        tradebot.stop(session, acc)
        return {"bot_enabled": False, "login": acc.login, "balance": round(acc.balance, 2)}
    finally:
        session.close()


@app.get("/api/admin/kyc", dependencies=[Depends(auth.require_admin)])
def admin_kyc():
    session = SessionLocal()
    try:
        rows = session.query(Trader).filter(Trader.kyc_status == "pending").all()
        return [{"trader_id": t.id, "email": t.email, "full_name": t.kyc_fullname,
                 "country": t.kyc_country, "doc_ref": t.kyc_doc_ref,
                 "dob": t.kyc_dob, "address": t.kyc_address,
                 "id_type": t.kyc_id_type, "id_number": t.kyc_id_number,
                 "docs": [k for k, col in _KYC_KINDS.items() if getattr(t, col, None)],
                 "submitted_at": t.kyc_submitted_at.isoformat() if t.kyc_submitted_at else None}
                for t in rows]
    finally:
        session.close()


@app.post("/api/admin/kyc/{trader_id}/approve", dependencies=[Depends(auth.require_admin)])
def admin_approve_kyc(trader_id: int):
    session = SessionLocal()
    try:
        tr = session.get(Trader, trader_id)
        if not tr:
            raise HTTPException(404, "Trader nie istnieje")
        tr.kyc_status = "approved"
        session.commit()
        notify.send("kyc_approved", tr.email, {"name": tr.full_name or tr.email})
        return {"approved": trader_id}
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
                        "account_id": o.account_id, "created_at": o.created_at.isoformat()})
        return out
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
                "platform_server": p.platform_server, "account_size": p.account_size,
                "claimed": p.claimed, "claimed_by_account_id": p.claimed_by_account_id,
                "claimed_at": p.claimed_at.isoformat() if p.claimed_at else None,
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
                "can_generate": mozna, "generate_hint": powod}
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
        raise HTTPException(400, "Rozmiar konta musi być dodatni")
    if not 1 <= payload.count <= 10:
        raise HTTPException(400, "Na raz można założyć od 1 do 10 kont")

    opener = metaquotes_web.make_opener(settings)
    if opener is None:
        raise HTTPException(400, "Kanał MetaQuotes jest wyłączony")

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
        raise HTTPException(502, "Nie udało się założyć żadnego konta: " + "; ".join(bledy))
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
            raise HTTPException(404, "Nie ma takiego wpisu w puli")
        if payload.account_size is not None:
            if p.claimed:
                raise HTTPException(400, "Rachunek jest przydzielony — rozmiaru nie można zmienić")
            if payload.account_size <= 0:
                raise HTTPException(400, "Rozmiar konta musi być dodatni")
            p.account_size = payload.account_size
        for pole in ("platform_login", "platform_password", "platform_server"):
            wartosc = getattr(payload, pole)
            if wartosc is not None:
                nowa = wartosc.strip()
                if not nowa:
                    raise HTTPException(400, f"Pole {pole} nie może być puste")
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
            raise HTTPException(404, "Nie ma takiego wpisu w puli")
        if p.claimed:
            raise HTTPException(400, "Ten rachunek jest przydzielony traderowi — nie można go usunąć")
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
        return [_account_dict(a, with_credentials=True, admin_view=True)
                for a in session.query(Account).order_by(Account.id).all()]
    finally:
        session.close()


@app.get("/api/accounts/{account_id}", dependencies=[Depends(auth.require_admin)])
def get_account(account_id: int):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Konto nie istnieje")
        _ensure_cert_token(session, acc)
        return _account_detail(session, acc, admin_view=True)
    finally:
        session.close()


class NewAccount(BaseModel):
    login: str
    trader_name: str = ""
    product_key: str = "2step-100k"
    drawdown_type: str = "static"
    metaapi_account_id: str | None = None


@app.post("/api/accounts", dependencies=[Depends(auth.require_admin)])
def create_account(payload: NewAccount):
    session = SessionLocal()
    try:
        prod = session.query(Product).filter(Product.key == payload.product_key).first()
        if not prod:
            raise HTTPException(404, "Produkt nie istnieje")
        bal = prod.account_size
        now = datetime.now(timezone.utc)
        acc = Account(
            login=payload.login, trader_name=payload.trader_name, product_key=prod.key, preset=prod.key,
            initial_balance=bal, steps=prod.steps, profit_target_p1=prod.profit_target_p1,
            profit_target_p2=prod.profit_target_p2, max_daily_loss_pct=prod.max_daily_loss_pct,
            max_overall_loss_pct=prod.max_overall_loss_pct, min_trading_days=prod.min_trading_days,
            drawdown_type=payload.drawdown_type, profit_split_pct=prod.profit_split_pct,
            metaapi_account_id=payload.metaapi_account_id, phase="eval_1", status="active",
            balance=bal, equity=bal, peak_equity=bal, day_start_equity=bal, day_start_balance=bal,
            day_key=now.strftime("%Y-%m-%d"), created_at=now, started_at=now,
        )
        session.add(acc)
        session.commit()
        return _account_dict(acc, with_credentials=True)
    finally:
        session.close()


@app.delete("/api/accounts/{account_id}", dependencies=[Depends(auth.require_admin)])
def delete_account(account_id: int):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if not acc:
            raise HTTPException(404, "Konto nie istnieje")

        # Zamówienie to dokument płatności — przeżywa konto. Odpinamy je zamiast
        # kasować, inaczej Postgres blokuje usunięcie (orders.account_id ma FK).
        (session.query(Order).filter(Order.account_id == acc.id)
         .update({Order.account_id: None}, synchronize_session=False))
        # Transakcje bez konta nie znaczą nic — lecą razem z nim.
        session.query(Trade).filter(Trade.account_id == acc.id).delete(synchronize_session=False)
        # Zwolnij slot w puli MT5, żeby opłacone konto nie przepadło jako 'claimed'.
        (session.query(PoolAccount).filter(PoolAccount.claimed_by_account_id == acc.id)
         .update({PoolAccount.claimed: False, PoolAccount.claimed_by_account_id: None},
                 synchronize_session=False))

        session.delete(acc)
        session.commit()
        return {"deleted": account_id}
    finally:
        session.close()


@app.get("/api/leaderboard")
def leaderboard():
    """Publiczny ranking — realne konta, ale nazwiska MASKOWANE (RODO/prywatność).

    Zysk liczony narastająco: obecny profit + suma już wypłaconych zysków.
    Bez tego trader po zatwierdzonej wypłacie spadałby na 0%, bo wypłata
    resetuje balance do salda startowego.
    """
    session = SessionLocal()
    try:
        # Ranking pokazuje wylacznie konta FUNDED — to jest osiagniecie, ktore
        # ma znaczenie. Konta w ewaluacji nie konkuruja z tymi, ktore ja przeszly.
        accs = session.query(Account).filter(Account.status == "funded").all()
        paid_profit: dict[int, float] = {}
        for p in session.query(Payout).all():
            paid_profit[p.account_id] = paid_profit.get(p.account_id, 0.0) + p.profit_amount
        rows = []
        for a in accs:
            profit = (a.balance - a.initial_balance) + paid_profit.get(a.id, 0.0)
            profit_pct = round(profit / a.initial_balance * 100, 2)
            tr = session.get(Trader, a.trader_id) if a.trader_id else None
            country = tr.kyc_country if (tr and tr.kyc_status == "approved" and tr.kyc_country) else None
            rows.append({"trader": _mask_name(a.trader_name or (tr.full_name if tr else "")),
                         "country": country, "phase": a.phase, "status": a.status,
                         "profit_pct": profit_pct, "account_size": a.initial_balance})
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
              blurb, meta, cert_token, seal, note=None) -> dict:
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
        "cert_token": cert_token, "seal": seal,
        "verify_url": f"/verify/{cert_token}",
        "verify_full_url": weryfikacja,
        "qr_svg": _qr_svg(weryfikacja),
        "signatory": settings.cert_signatory or None,
        "signatory_label": settings.cert_signatory_label,
    }


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
                raise HTTPException(404, "Certyfikat nie istnieje")
            kind = "funded" if acc.status == "funded" else "phase_1"
            when = acc.closed_at or acc.created_at
        if not acc:
            raise HTTPException(404, "Certyfikat nie istnieje")

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
            raise HTTPException(404, "Certyfikat nie istnieje")
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
            note=p.note, cert_token=cert_token, seal="Paid",
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
    raise HTTPException(401, "Brak uprawnień do wyzwalania ticku")


@app.api_route("/api/tick", methods=["GET", "POST"], dependencies=[Depends(_require_cron)])
async def api_tick():
    """Jeden przebieg silnika ryzyka — dla hostingu bez procesu w tle.

    Na zwykłym serwerze poller kręci się sam co `POLL_INTERVAL_SEC` i ten
    endpoint jest tylko awaryjnym „szturchnięciem". Na hostingu bezserwerowym
    (Vercel) proces nie żyje między requestami, więc to cron trzyma silnik przy
    życiu — wtedy ustaw POLLER_ENABLED=false i wal tu cronem. GET jest
    obsłużony, bo Vercel Cron uderza właśnie metodą GET.
    """
    return await poller.tick_once()


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
            "payouts_total_usd": round(sum(p.trader_share for p in payouts), 2),
            "largest_payout_usd": round(max((p.trader_share for p in payouts), default=0.0), 2),
            "countries_count": len(countries),
        }
        _PUBLIC_STATS_CACHE.update(ts=now, data=data)
        return data
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  Strony                                                                      #
# --------------------------------------------------------------------------- #
# Strony publiczne renderuje Jinja (wspólny base.html: nav + footer + disclaimery).
# Portal i admin zostają jako samodzielne SPA serwowane z pliku.
jinja = Jinja2Templates(directory=str(TEMPLATES))


def _page(request: Request, template: str, **extra):
    ctx = {"site_name": settings.site_name, "support_email": settings.support_email,
           "base_url": settings.app_base_url, **extra}
    return jinja.TemplateResponse(request, template, ctx)


@app.get("/")
def home(request: Request):
    """Publiczna strona sprzedażowa (cennik/objectives z /api/products)."""
    # QR w podglądzie certyfikatu prowadzi na REALNĄ stronę weryfikacji —
    # atrapa kodu na landingu byłaby obietnicą bez pokrycia.
    return _page(request, "home.html",
                 sample_qr=_qr_svg(f"{_public_base(request)}/verify"))


@app.get("/faq")
def faq_page(request: Request):
    return _page(request, "faq.html")


@app.get("/affiliate")
def affiliate_page(request: Request):
    return _page(request, "affiliate.html")


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
    if cert_token:
        session = SessionLocal()
        try:
            cert = session.query(Certificate).filter(Certificate.cert_token == cert_token).first()
            acc = (session.get(Account, cert.account_id) if cert
                   else session.query(Account).filter(Account.cert_token == cert_token).first())
            payout = (session.query(Payout).filter(Payout.cert_token == cert_token).first()
                      if not acc else None)
            if cert and acc:
                result = {"found": True, "kind": "account", "open_url": f"/certificate/{cert_token}",
                          "trader": _mask_name(acc.trader_name),
                          "size": acc.initial_balance, "product": acc.product_key,
                          "status": CERT_KINDS.get(cert.kind, ("Evaluation passed",))[0],
                          "token": cert_token}
            elif acc and _cert_eligible(acc):
                result = {"found": True, "kind": "account", "open_url": f"/certificate/{cert_token}",
                          "trader": _mask_name(acc.trader_name),
                          "size": acc.initial_balance, "product": acc.product_key,
                          "status": "Funded Trader" if acc.status == "funded" else "Evaluation Passed",
                          "token": cert_token}
            elif payout:
                pacc = session.get(Account, payout.account_id)
                result = {"found": True, "kind": "payout", "open_url": f"/payout/{cert_token}",
                          "trader": _mask_name(pacc.trader_name if pacc else ""),
                          "size": (pacc.initial_balance if pacc else 0.0),
                          "product": (pacc.product_key if pacc else "—"),
                          "status": "Payout paid",
                          "amount": round(payout.trader_share, 2),
                          "issued": payout.ts.strftime("%d %b %Y") if payout.ts else None,
                          "token": cert_token}
            else:
                result = {"found": False}
        finally:
            session.close()
    return _page(request, "verify.html", result=result, token=cert_token or "")


@app.get("/admin")
def dashboard(request: Request):
    # Render przez Jinja (nie FileResponse), żeby nazwa marki szła z SITE_NAME
    # zamiast być wpisana na sztywno w dwóch dodatkowych miejscach.
    return _page(request, "dashboard.html")


@app.get("/portal")
def portal(request: Request):
    return _page(request, "portal.html")
