"""Automatyczne powiadomienia (e-mail) na całym cyklu życia tradera.

Kanały (best-effort, nigdy nie wywracają requestu):
  - SMTP, jeśli skonfigurowany; w przeciwnym razie wydruk na konsolę (tryb 0 zł),
  - opcjonalny webhook (np. Make/Telegram) gdy NOTIFY_WEBHOOK_URL ustawione.

Szablony dla zdarzeń: welcome, credentials, phase_passed, account_funded,
breached, payout_requested, payout_approved, kyc_approved.
"""
from __future__ import annotations

import json
import smtplib
import tempfile
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from .config import get_settings

settings = get_settings()


def _num(v) -> str:
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v or "—")


def _tier(value) -> str:
    """„$25K" / „$250K" — skrót używany w komunikacji promocji."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"${v / 1000:.0f}K" if v >= 1000 else f"${v:,.0f}"


def _promo_name() -> str:
    """Nazwa promocji z katalogu — mail i strona mówią to samo."""
    from . import catalog
    return catalog.PROMO_NAME


def _bogo_upgrade(ctx: dict) -> bool:
    """Czy to realny upgrade: klient zapłacił za mniejszy tier, dostał większy."""
    paid, got = ctx.get("bogo_paid_size"), ctx.get("initial_balance")
    try:
        return bool(paid) and float(paid) < float(got)
    except (TypeError, ValueError):
        return False


def _bogo_subject(ctx: dict) -> str:
    return "Your upgraded challenge is live 🎁" if _bogo_upgrade(ctx) else "Your BOGO challenge is live 🎁"


def _bogo_intro(ctx: dict) -> str:
    """Konto przyznane przez admina opisujemy jako promocję BOGO, nigdy jako
    „aktywowane przez nasz zespół". Gdy znamy opłacony tier, mówimy o upgrade —
    to zdanie jest wtedy prawdziwe. Gdy go nie znamy, nie sugerujemy płatności."""
    if _bogo_upgrade(ctx):
        return (f"Your promotion has been applied. You paid for the "
                f"{_tier(ctx.get('bogo_paid_size'))} tier and we upgraded your allocation to "
                f"${_num(ctx.get('initial_balance'))}.")
    # Zdanie zaczyna sie od „Your", bo wersja HTML doklada powitanie i zamienia
    # pierwsza litere na mala — „buy one, get one free" nie moze wtedy wypasc
    # na poczatku, bo wygladaloby jak polecenie zamiast nazwy promocji.
    return ("Your bonus challenge is live — buy one, get one free. "
            "Same rules and the same profit split as a purchased account.")


def _render(event: str, ctx: dict) -> tuple[str, str]:
    """Maile klienckie są po angielsku (produkt EN); brand z SITE_NAME."""
    brand = settings.site_name
    name = ctx.get("name") or "trader"
    login = ctx.get("login", "")
    footer = f"\n\n—\n{brand} · simulated trading environment · {settings.support_email}"
    T = {
        "welcome": (
            f"Welcome to {brand} 🎉",
            f"Hi {name}!\n\nYour {brand} account is ready. Pick a challenge in the portal "
            f"and start your path to a funded account.\n\n"
            f"📱 Get the mobile app\n"
            f"The portal installs as an app straight from your browser — no app store:\n"
            f"  iPhone / iPad:  open {settings.app_base_url}/portal in Safari → Share → Add to Home Screen\n"
            f"  Android:        open the portal in Chrome → menu ⋮ → Install app\n"
            f"Step-by-step guide: {settings.app_base_url}/install\n\n"
            f"🔔 Turn on notifications\n"
            f"Open the dashboard and tap “Enable” on the notifications banner (or go to "
            f"Settings → Notification Preferences) to get instant alerts about your MT5 "
            f"credentials, phase passes and payouts. On iPhone, install the app first — "
            f"Safari alone cannot receive push notifications.",
        ),
        "credentials": (
            f"Your challenge account {login} is ready ⚡",
            f"Hi {name}!\n\nYour challenge account has been created automatically."
            # Zakup z promocja: konto jest WIEKSZE, niz tier, za ktory klient
            # zaplacil — mail musi to powiedziec, inaczej klient szuka bledu.
            + (f"\n\nYour {_promo_name()} promotion is applied: you paid for the "
               f"{_tier(ctx.get('bogo_paid_size'))} tier and your account was created at "
               f"${_num(ctx.get('initial_balance'))}." if _bogo_upgrade(ctx) else "")
            + "\n\n"
            f"  MT5 login:  {ctx.get('platform_login')}\n"
            f"  Password:   {ctx.get('platform_password')}\n"
            f"  Server:     {ctx.get('platform_server')}\n"
            f"  Capital:    {ctx.get('initial_balance')}\n\n"
            f"Log in with MetaTrader 5 (desktop, mobile or web) using the server above.\n"
            f"Good luck — track your progress in the dashboard.",
        ),
        "phase_passed": (
            f"Congratulations — phase passed! ✅ ({login})",
            f"Great job {name}! Account {login} moved from "
            f"{ctx.get('from_phase')} to {ctx.get('to_phase')}.",
        ),
        "account_funded": (
            f"Your account is FUNDED 💰 ({login})",
            f"{name}, your account {login} is now funded! "
            f"Profit split: {ctx.get('split')}%. Complete KYC and you can request payouts.",
        ),
        "breached": (
            f"Account {login} — rule breached ⛔",
            f"{name}, account {login} has been closed: {ctx.get('reason')}. "
            f"You can review the breach details in your dashboard and start a new challenge anytime.",
        ),
        "payout_requested": (
            f"Payout request received ({login})",
            f"{name}, we registered your payout request for {ctx.get('trader_share')} "
            f"(profit {ctx.get('profit_amount')}). Status: under review.",
        ),
        "payout_approved": (
            f"Payout approved 🤑 ({login})",
            f"{name}, your payout of {ctx.get('trader_share')} has been approved"
            f"{' (including your challenge fee refund)' if ctx.get('fee_refund') else ''}. "
            f"Funds are on the way.",
        ),
        "payout_rejected": (
            f"Payout request declined ({login})",
            f"{name}, your payout request for {ctx.get('trader_share')} was declined. "
            f"Reason: {ctx.get('reason')}. "
            f"You can submit a new request from your dashboard at any time.",
        ),
        "kyc_approved": (
            "Identity verification approved ✅",
            f"{name}, your KYC verification has been accepted. You can now request payouts.",
        ),
        "password_reset": (
            "Reset your password",
            f"{name}, someone (hopefully you) requested a password reset for your "
            f"{brand} account.\n\nSet a new password here (the link is valid for 1 hour):\n"
            f"{ctx.get('reset_url')}\n\n"
            f"If you didn't request this, you can safely ignore this e-mail.",
        ),
        "kyc_rejected": (
            "Identity verification — action needed",
            f"{name}, we could not verify your identity with the documents provided."
            + (f"\n\nReason: {ctx.get('reason')}" if ctx.get("reason") else "")
            + "\n\nPlease review your details and submit the verification again from your dashboard.",
        ),
        "ticket_reply": (
            f"Support replied to your ticket #{ctx.get('ticket_id')} 💬",
            f"{name}, our support team replied to your ticket "
            f"\"{ctx.get('subject')}\". Log in to the portal to read the answer.",
        ),
        "credits_granted": (
            f"Store credit added: ${_num(ctx.get('amount'))} 🎁",
            f"{name}, we've just added ${_num(ctx.get('amount'))} of store credit to your "
            f"account. Your balance is now ${_num(ctx.get('balance'))} — it will be applied "
            f"automatically at your next checkout.",
        ),
        "challenge_granted": (
            _bogo_subject(ctx),
            f"Hi {name}!\n\n{_bogo_intro(ctx)}\n\n"
            f"  Allocation: ${_num(ctx.get('initial_balance'))}\n"
            f"  MT5 login:  {ctx.get('platform_login')}\n"
            f"  Password:   {ctx.get('platform_password')}\n"
            f"  Server:     {ctx.get('platform_server')}\n"
            f"\nLog in to the portal to see your objectives and progress.",
        ),
        "verify_email": (
            f"{ctx.get('code')} is your {brand} verification code",
            f"Hi {name}!\n\nConfirm your e-mail address to finish setting up your "
            f"{brand} account.\n\n  Verification code: {ctx.get('code')}\n\n"
            f"Enter the code in the portal, or open this link:\n{ctx.get('verify_url')}\n\n"
            f"The code and link are valid for 24 hours. If you didn't create an "
            f"account, you can safely ignore this e-mail.",
        ),
    }
    subject, body = T.get(event, (f"Notification: {event}", json.dumps(ctx, ensure_ascii=False)))
    return subject, body + footer


# ---------------------------------------------------------------------------
# Szablony HTML — wspólny minimalistyczny layout (styl Apple/Dyson): biel,
# dużo światła, czerń, złoto z logo jako jedyny akcent. Wyłącznie inline CSS
# i tabele — klienty pocztowe nie mają nowoczesnego layoutu.
# ---------------------------------------------------------------------------
_FONT = "-apple-system,BlinkMacSystemFont,'Helvetica Neue',Helvetica,Arial,sans-serif"
_MONO = "'SF Mono',SFMono-Regular,Menlo,Consolas,monospace"
_INK, _MUTE, _FAINT = "#1d1d1f", "#6e6e73", "#86868b"
_HAIR, _GOLD, _GOLD_BG = "#e8e8ed", "#b7924e", "#f7f1e6"


def _head_html(badge: str | None, title: str, intro: str) -> str:
    b = (f'<div style="display:inline-block;background:{_GOLD_BG};color:{_GOLD};border-radius:999px;'
         f'padding:8px 15px;font:600 11px/1 {_FONT};letter-spacing:.14em;text-transform:uppercase;'
         f'margin:0 0 20px">{badge}</div>' if badge else "")
    return f"""
   <tr><td align="center" style="padding:6px 44px 0">
     {b}
     <h1 style="font:600 28px/1.25 {_FONT};letter-spacing:-.4px;color:{_INK};margin:0 0 12px">{title}</h1>
     <p style="font:400 15px/1.7 {_FONT};color:{_MUTE};margin:0">{intro}</p>
   </td></tr>"""


def _stat_html(label: str, value: str, sub: str | None = None) -> str:
    s = (f'<div style="font:400 13px/1.5 {_FONT};color:{_MUTE};margin-top:8px">{sub}</div>' if sub else "")
    return f"""
   <tr><td align="center" style="padding:32px 44px 4px">
     <div style="font:600 11px/1 {_FONT};letter-spacing:.14em;color:{_GOLD};text-transform:uppercase">{label}</div>
     <div style="font:700 46px/1.1 {_FONT};letter-spacing:-1.2px;color:{_INK};margin-top:10px">{value}</div>
     {s}
   </td></tr>"""


def _rows_html(pairs: list[tuple[str, object]]) -> str:
    rows = "".join(
        f'<tr><td style="padding:13px 0;border-top:1px solid {_HAIR};'
        f'font:400 12px/1.4 {_FONT};letter-spacing:.08em;color:{_FAINT};text-transform:uppercase">{k}</td>'
        f'<td align="right" style="padding:13px 0;border-top:1px solid {_HAIR};'
        f'font:500 15px/1.4 {_MONO};color:{_INK}">{v}</td></tr>'
        for k, v in pairs if v)
    if not rows:
        return ""
    return f"""
   <tr><td style="padding:28px 44px 4px">
     <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
   </td></tr>"""


def _button_html(label: str, url: str) -> str:
    return f"""
   <tr><td align="center" style="padding:32px 44px 4px">
     <a href="{url}" style="display:inline-block;background:{_INK};color:#ffffff;text-decoration:none;
       font:600 15px/1 {_FONT};padding:16px 36px;border-radius:999px">{label}</a>
   </td></tr>"""


def _note_html(text: str) -> str:
    return f"""
   <tr><td align="center" style="padding:22px 44px 0">
     <p style="font:400 12px/1.7 {_FONT};color:{_FAINT};margin:0">{text}</p>
   </td></tr>"""


def _shell(parts: list[str]) -> str:
    brand = settings.site_name
    logo = f"{settings.app_base_url}/static/img/logo.png"
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f5f5f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f7;padding:44px 14px">
 <tr><td align="center">
  <table role="presentation" width="560" cellpadding="0" cellspacing="0"
    style="max-width:560px;width:100%;background:#ffffff;border-radius:22px">
   <tr><td align="center" style="padding:46px 44px 28px">
     <img src="{logo}" width="46" height="46" alt="{brand}" style="display:block">
   </td></tr>
   {''.join(parts)}
   <tr><td style="padding:0 0 46px"></td></tr>
  </table>
  <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%">
   <tr><td align="center" style="padding:26px 30px 0">
     <p style="font:400 12px/1.7 {_FONT};color:{_FAINT};margin:0">
       {brand} · <a href="mailto:{settings.support_email}" style="color:{_GOLD};text-decoration:none">{settings.support_email}</a></p>
     <p style="font:400 11px/1.7 {_FONT};color:#b0b0b5;margin:6px 0 0">
       Demo accounts with virtual funds in a simulated trading environment.
       Rewards are performance-based and discretionary.</p>
   </td></tr>
  </table>
 </td></tr>
</table></body></html>"""


def _render_html(event: str, ctx: dict, subject: str) -> str | None:
    """Wersja HTML każdego maila — wspólny layout, treść per zdarzenie."""
    brand = settings.site_name
    name = ctx.get("name") or "trader"
    login = ctx.get("login", "")
    portal = f"{settings.app_base_url}/portal"

    if event in ("credentials", "challenge_granted"):
        granted = event == "challenge_granted"
        # Zakup z promocja tez jest upgrade'em — badge i tekst musza to powiedziec,
        # bo konto jest wieksze niz tier, ktory klient widzial w koszyku.
        upgraded = _bogo_upgrade(ctx)
        badge = ((ctx.get("grant_note") or "BOGO activation complete") if granted
                 else (f"{_promo_name()} applied" if upgraded else "Account ready"))
        headline = (("Your upgraded challenge is live" if upgraded else "Your BOGO challenge is live")
                    if granted else
                    ("Your upgraded challenge account is ready" if upgraded
                     else "Your challenge account is ready"))
        steps = ctx.get("steps")
        kind = f"{steps}-Step challenge on MT5" if steps else "Challenge on MT5"
        lead = _bogo_intro(ctx)
        if granted:
            intro = f"Hi {name}, {lead[0].lower()}{lead[1:]}"
        elif upgraded:
            intro = (f"Hi {name}, your account is ready — and your {_promo_name()} "
                     f"promotion is applied: you paid for the {_tier(ctx.get('bogo_paid_size'))} tier "
                     f"and we created the account at ${_num(ctx.get('initial_balance'))}.")
        else:
            intro = f"Hi {name}, your account has been created and is ready to trade."
        parts = [
            _head_html(badge, headline, intro),
            _stat_html("Account allocation", f"${_num(ctx.get('initial_balance'))}", kind),
            _rows_html([
                ("Platform", "MetaTrader 5"),
                ("Server", ctx.get("platform_server")),
                ("Login", ctx.get("platform_login")),
                ("Password", ctx.get("platform_password")),
                ("Leverage", "1:100"),
                ("Profit split", f"{ctx.get('profit_split_pct')}%" if ctx.get("profit_split_pct") else None),
            ]),
            _button_html("View Dashboard", f"{portal}?view=accounts"),
            _note_html("Sign in to the portal with your e-mail address. "
                       "The credentials above are only for the MetaTrader 5 platform."),
        ]
    elif event == "welcome":
        mobile = f"""
   <tr><td align="center" style="padding:40px 44px 0">
     <div style="border-top:1px solid {_HAIR};padding-top:34px">
       <div style="display:inline-block;background:{_GOLD_BG};color:{_GOLD};border-radius:999px;
         padding:8px 15px;font:600 11px/1 {_FONT};letter-spacing:.14em;text-transform:uppercase;
         margin:0 0 16px">Mobile app</div>
       <h2 style="font:600 20px/1.3 {_FONT};letter-spacing:-.2px;color:{_INK};margin:0 0 10px">
         Install {brand} on your phone</h2>
       <p style="font:400 14px/1.8 {_FONT};color:{_MUTE};margin:0">
         The portal installs as an app straight from your browser — no app store.<br>
         <b style="color:{_INK}">iPhone / iPad:</b> Safari → Share → Add to Home Screen<br>
         <b style="color:{_INK}">Android:</b> Chrome → menu ⋮ → Install app</p>
     </div>
   </td></tr>"""
        parts = [
            _head_html("Welcome", f"Welcome to {brand}",
                       f"Hi {name}, your account is ready. Pick a challenge in the portal "
                       f"and start your path to a funded account."),
            _button_html("Open the Portal", f"{portal}?view=store"),
            mobile,
            _button_html("Open the Install Guide", f"{settings.app_base_url}/install"),
            _note_html("🔔 Turn on notifications: open the dashboard and tap “Enable” on the "
                       "notifications banner to get instant alerts about your MT5 credentials, "
                       "phase passes and payouts. On iPhone, notifications only work from the "
                       "installed app."),
        ]
    elif event == "phase_passed":
        parts = [
            _head_html("Milestone", "Phase passed",
                       f"Great job {name} — account {login} moved from "
                       f"{ctx.get('from_phase')} to {ctx.get('to_phase')}."),
            _button_html("View Progress", f"{portal}?view=accounts"),
        ]
    elif event == "account_funded":
        parts = [
            _head_html("Funded", "Your account is funded",
                       f"{name}, account {login} is now funded. "
                       f"Complete KYC and you can request payouts."),
            _stat_html("Profit split", f"{ctx.get('split')}%", f"Account {login}"),
            _button_html("View Dashboard", f"{portal}?view=accounts"),
        ]
    elif event == "breached":
        parts = [
            _head_html("Account closed", f"Account {login} was closed",
                       f"{name}, the account breached a rule: {ctx.get('reason')}. "
                       f"You can review the details in your dashboard and start a new challenge anytime."),
            _button_html("Start a New Challenge", f"{portal}?view=store"),
        ]
    elif event == "payout_requested":
        parts = [
            _head_html("Payout", "Payout request received",
                       f"{name}, we registered your payout request. Our team is reviewing it now."),
            _rows_html([("Your share", ctx.get("trader_share")),
                        ("Profit", ctx.get("profit_amount")),
                        ("Status", "Under review")]),
            _button_html("View Payouts", f"{portal}?view=payouts"),
        ]
    elif event == "payout_approved":
        parts = [
            _head_html("Payout", "Payout approved",
                       f"{name}, your payout has been approved"
                       f"{' — including your challenge fee refund' if ctx.get('fee_refund') else ''}. "
                       f"Funds are on the way."),
            _stat_html("Your payout", str(ctx.get("trader_share") or "—")),
            _button_html("View Dashboard", f"{portal}?view=payouts"),
        ]
    elif event == "payout_rejected":
        parts = [
            _head_html("Payout", "Payout request declined",
                       f"{name}, your payout request for {ctx.get('trader_share')} was declined. "
                       f"Reason: {ctx.get('reason')}. You can submit a new request anytime."),
            _button_html("Go to Dashboard", f"{portal}?view=payouts"),
        ]
    elif event == "kyc_approved":
        parts = [
            _head_html("Verification", "Identity verified",
                       f"{name}, your KYC verification has been accepted. You can now request payouts."),
            _button_html("Request a Payout", f"{portal}?view=payouts"),
        ]
    elif event == "kyc_rejected":
        parts = [
            _head_html("Verification", "Verification needs another look",
                       f"{name}, we could not verify your identity with the documents provided."
                       + (f" Reason: {ctx.get('reason')}." if ctx.get("reason") else "")
                       + " Please review your details and submit the verification again."),
            _button_html("Retry Verification", f"{portal}?view=kyc"),
        ]
    elif event == "password_reset":
        parts = [
            _head_html(None, "Reset your password",
                       f"{name}, someone (hopefully you) requested a password reset "
                       f"for your {brand} account."),
            _button_html("Set a New Password", ctx.get("reset_url") or portal),
            _note_html("The link is valid for 1 hour. If you didn't request this, "
                       "you can safely ignore this e-mail."),
        ]
    elif event == "ticket_reply":
        parts = [
            _head_html("Support", "New reply to your ticket",
                       f"{name}, our support team replied to “{ctx.get('subject')}”."),
            _button_html("Read the Reply", f"{portal}?view=support"),
        ]
    elif event == "credits_granted":
        parts = [
            _head_html("Store credit", "Credit added to your account",
                       f"{name}, we've just added store credit to your account — "
                       f"it is applied automatically at your next checkout."),
            _stat_html("Credit added", f"${_num(ctx.get('amount'))}",
                       f"Current balance ${_num(ctx.get('balance'))}"),
            _button_html("Browse Challenges", f"{portal}?view=store"),
        ]
    elif event == "verify_email":
        parts = [
            _head_html("Verify", "Confirm your e-mail",
                       f"Hi {name}, enter this code in the portal — or tap the "
                       f"button below — to confirm your e-mail address."),
            _stat_html("Verification code", str(ctx.get("code") or "")),
            _button_html("Verify E-mail", ctx.get("verify_url") or portal),
            _note_html("The code and link are valid for 24 hours. If you didn't "
                       "create an account, you can safely ignore this e-mail."),
        ]
    else:
        return None
    return _shell(parts)


# Kategoria preferencji per zdarzenie (Settings -> Notification Preferences).
# Zdarzenia TRANSAKCYJNE (welcome, credentials/challenge_granted z poświadczeniami
# MT5 za opłacony produkt, verify_email, password_reset) celowo NIE mają wpisu —
# muszą dojść zawsze, niezależnie od preferencji.
_PREF_BY_EVENT = {
    "kyc_approved": "notify_updates", "kyc_rejected": "notify_updates",
    "ticket_reply": "notify_updates",
    "credits_granted": "notify_updates",
    "phase_passed": "notify_trading", "account_funded": "notify_trading",
    "breached": "notify_trading",
    "payout_requested": "notify_payouts", "payout_approved": "notify_payouts",
    "payout_rejected": "notify_payouts",
    # recap idzie tylko przez push/centrum (push.daily_recap), nie mailem
    "daily_recap": "notify_marketing",
}


def _email_allowed(event: str, to_email: str) -> bool:
    """Best-effort: trader mógł wyłączyć daną kategorię maili w ustawieniach."""
    pref = _PREF_BY_EVENT.get(event)
    if not pref:
        return True
    try:
        from .db import SessionLocal
        from .models import Trader
        session = SessionLocal()
        try:
            tr = session.query(Trader).filter(Trader.email == to_email).first()
            if tr is None:
                return True
            val = getattr(tr, pref, True)
            return True if val is None else bool(val)
        finally:
            session.close()
    except Exception:  # pragma: no cover
        return True


def send(event: str, to_email: str | None, ctx: dict | None = None) -> None:
    ctx = ctx or {}
    subject, body = _render(event, ctx)
    adres = to_email          # oryginal dla kanalu push/centrum (pref sprawdza sam)

    # 1) e-mail
    if to_email and not _email_allowed(event, to_email):
        print(f"[notify] pominięto mail '{event}' do {to_email} (preferencje tradera)")
        to_email = None
    if to_email:
        html = _render_html(event, ctx, subject)
        if settings.smtp_host:
            try:
                msg = EmailMessage()
                msg["From"] = settings.mail_from
                msg["To"] = to_email
                msg["Subject"] = subject
                msg.set_content(body)
                if html:
                    msg.add_alternative(html, subtype="html")
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
                    s.starttls()
                    if settings.smtp_user:
                        s.login(settings.smtp_user, settings.smtp_pass)
                    s.send_message(msg)
            except Exception as e:  # pragma: no cover
                print(f"[notify] SMTP błąd: {e}")
        else:
            print(f"\n📧 [MAIL → {to_email}] {subject}\n{body}\n")
            if html:
                # dev: podglad wersji HTML w przegladarce, bez SMTP
                try:
                    out = Path(tempfile.gettempdir()) / f"propfunding-mail-{event}.html"
                    out.write_text(html, encoding="utf-8")
                    print(f"   ↳ HTML preview: {out}")
                except Exception:  # pragma: no cover
                    pass

    # 2) webhook (Make/Telegram itp.)
    if settings.notify_webhook_url:
        try:
            payload = json.dumps({"event": event, "to": to_email, "subject": subject, **ctx}).encode()
            req = urllib.request.Request(
                settings.notify_webhook_url, data=payload,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8)
        except Exception as e:  # pragma: no cover
            print(f"[notify] webhook błąd: {e}")

    # 3) web push + centrum powiadomien w portalu — tytuł = temat maila, treść
    # z push._BODY (nigdy z maila: credentials zawiera hasło MT5). `to_email`
    # jest już po bramce preferencji, więc wyłączona kategoria blokuje mail,
    # push i wpis w centrum jednym przełącznikiem; awaria pusha nie może
    # wywrócić requestu, który zdarzenie wywołał (np. approve payoutu).
    if to_email:
        try:
            from . import push
            push.send_event(event, to_email, subject, ctx)
        except Exception as e:  # pragma: no cover
            print(f"[notify] push błąd: {e}")


def notify_admins(event: str, title: str, body: str = "") -> None:
    """Dzwonek + web push do wszystkich kont is_admin (url -> panel /admin).

    Osobna ścieżka od send(): bez maila i bez bramki preferencji tradera —
    to sygnał operacyjny „coś przyszło". NIGDY nie rzuca: zdarzenie admina
    nie może wywrócić requestu tradera, który je wywołał."""
    try:
        from . import push
        from .db import SessionLocal
        from .models import Trader

        session = SessionLocal()
        try:
            admin_ids = [t.id for t in
                         session.query(Trader).filter(Trader.is_admin.is_(True)).all()]
            for tid in admin_ids:
                push._center_row(session, tid, event, title, body, "/admin")
            session.commit()
        finally:
            session.close()
        for tid in admin_ids:
            push.send_to_trader(tid, title, body, url="/admin", tag=event)
    except Exception as e:  # pragma: no cover
        print(f"[notify] admin błąd: {e}")
