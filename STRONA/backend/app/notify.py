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
            f"and start your path to a funded account.",
        ),
        "credentials": (
            f"Your challenge account {login} is ready ⚡",
            f"Hi {name}!\n\nYour challenge account has been created automatically.\n\n"
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
        "kyc_rejected": (
            "Identity verification — action needed",
            f"{name}, we could not verify your identity with the documents provided. "
            f"Please review your details and submit the verification again from your dashboard.",
        ),
        "ticket_reply": (
            f"Support replied to your ticket #{ctx.get('ticket_id')} 💬",
            f"{name}, our support team replied to your ticket "
            f"\"{ctx.get('subject')}\". Log in to the portal to read the answer.",
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
    }
    subject, body = T.get(event, (f"Notification: {event}", json.dumps(ctx, ensure_ascii=False)))
    return subject, body + footer


def _render_html(event: str, ctx: dict, subject: str) -> str | None:
    """Wersja HTML dla maili z poświadczeniami (reszta leci plain textem).

    Inline'owany CSS i tabele — klienty pocztowe nie mają nowoczesnego layoutu.
    """
    if event not in ("credentials", "challenge_granted"):
        return None
    brand = settings.site_name
    granted = event == "challenge_granted"
    badge = (ctx.get("grant_note") or "BOGO activation complete") if granted else "Account ready"
    headline = (("Your upgraded challenge is live" if _bogo_upgrade(ctx) else "Your BOGO challenge is live")
                if granted else "Your challenge account is ready")
    steps = ctx.get("steps")
    kind = f"{steps}-Step challenge on MT5" if steps else "Challenge on MT5"
    lead = _bogo_intro(ctx)
    intro = (f"Hi {ctx.get('name')}, {lead[0].lower()}{lead[1:]}" if granted
             else f"Hi {ctx.get('name')}, your account has been created and is ready to trade.")
    cell = lambda label, value: f"""
      <td width="50%" style="padding:6px">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e7eaf3;border-radius:12px;background:#fbfcfe">
          <tr><td style="padding:14px 16px">
            <div style="font:600 11px/1.4 Arial,Helvetica,sans-serif;letter-spacing:.08em;color:#94a3b8;text-transform:uppercase">{label}</div>
            <div style="font:600 16px/1.4 'SFMono-Regular',Consolas,monospace;color:#0f172a;margin-top:4px">{value}</div>
          </td></tr>
        </table></td>"""
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f6f7fb">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f6f7fb;padding:28px 12px">
 <tr><td align="center">
  <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border:1px solid #e7eaf3;border-radius:18px">
   <tr><td style="padding:34px 34px 8px">
     <div style="display:inline-block;background:#eef0ff;border-radius:999px;padding:8px 16px;
       font:700 12px/1 Arial,Helvetica,sans-serif;letter-spacing:.1em;color:#4f46e5;text-transform:uppercase">{badge}</div>
     <h1 style="font:700 30px/1.2 Arial,Helvetica,sans-serif;color:#0f172a;margin:18px 0 10px">{headline}</h1>
     <p style="font:400 15px/1.6 Arial,Helvetica,sans-serif;color:#64748b;margin:0 0 22px">{intro}</p>
   </td></tr>
   <tr><td style="padding:0 34px">
     <table width="100%" cellpadding="0" cellspacing="0"
       style="background:#5b5bd6;background-image:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:16px">
       <tr><td style="padding:26px 28px">
         <div style="font:700 12px/1 Arial,Helvetica,sans-serif;letter-spacing:.12em;color:#dcd9fb;text-transform:uppercase">Account allocation</div>
         <div style="font:800 40px/1.1 Arial,Helvetica,sans-serif;color:#ffffff;margin:10px 0 6px">${_num(ctx.get('initial_balance'))}</div>
         <div style="font:400 14px/1.4 Arial,Helvetica,sans-serif;color:#dcd9fb">{kind}</div>
       </td></tr>
     </table>
   </td></tr>
   <tr><td style="padding:16px 28px 0">
     <table width="100%" cellpadding="0" cellspacing="0">
       <tr>{cell('Platform', 'MetaTrader 5')}{cell('Server', ctx.get('platform_server') or '—')}</tr>
       <tr>{cell('MT5 login', ctx.get('platform_login') or '—')}{cell('MT5 password', ctx.get('platform_password') or '—')}</tr>
       <tr>{cell('Leverage', '1:100')}{cell('Profit split', str(ctx.get('profit_split_pct') or '—') + '%')}</tr>
     </table>
   </td></tr>
   <tr><td align="center" style="padding:26px 34px 8px">
     <a href="{settings.app_base_url}/portal" style="display:inline-block;background:#5b5bd6;
       background-image:linear-gradient(135deg,#6366f1,#8b5cf6);color:#ffffff;text-decoration:none;
       font:700 15px/1 Arial,Helvetica,sans-serif;padding:16px 34px;border-radius:999px">View Dashboard</a>
   </td></tr>
   <tr><td style="padding:18px 34px 30px">
     <p style="font:400 13px/1.6 Arial,Helvetica,sans-serif;color:#94a3b8;margin:0">
       Sign in to the portal with your e-mail{f" <a href='mailto:{ctx.get('email')}' style='color:#6366f1'>{ctx.get('email')}</a>" if ctx.get('email') else ''}.
       MT5 credentials are only for the trading platform.</p>
     <p style="font:400 11px/1.6 Arial,Helvetica,sans-serif;color:#b6bdcc;margin:14px 0 0">
       {brand} · demo account with virtual funds in a simulated environment. Rewards are performance-based and discretionary.</p>
   </td></tr>
  </table>
 </td></tr>
</table></body></html>"""


# Kategoria preferencji per zdarzenie (Settings -> Notification Preferences).
_PREF_BY_EVENT = {
    "welcome": "notify_updates", "credentials": "notify_updates",
    "kyc_approved": "notify_updates", "kyc_rejected": "notify_updates",
    "ticket_reply": "notify_updates",
    "challenge_granted": "notify_updates",
    "phase_passed": "notify_trading", "account_funded": "notify_trading",
    "breached": "notify_trading",
    "payout_requested": "notify_payouts", "payout_approved": "notify_payouts",
    "payout_rejected": "notify_payouts",
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
