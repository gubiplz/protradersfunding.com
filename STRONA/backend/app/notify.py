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
from contextlib import contextmanager
from contextvars import ContextVar
from email.message import EmailMessage
from pathlib import Path

from .config import get_settings

settings = get_settings()


def _num(v) -> str:
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v or "—")


def _kwota(v) -> str:
    """Kwota DO ZAPŁATY — z groszami.

    `_num` zaokrągla do pełnych dolarów, a w instrukcji przelewu to znaczy, że
    klient wyśle inną kwotę, niż jest winien: 199,50 wychodziło jako „$200"."""
    try:
        return f"{float(v):,.2f}"
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


def _payment_lines(ctx: dict | None = None) -> list[tuple[str, str]]:
    """Dane do wpłaty spoza Stripe'a: adres z ZAMÓWIENIA, a gdy go nie ma —
    stały z konfiguracji (CRYPTO_WALLET).

    Adresy są rotowane i admin wpisuje je ręcznie przy zamówieniu, więc to
    zamówienie jest źródłem prawdy. Pusta lista = nikt adresu nie podał; mail
    go wtedy NIE zmyśla (klient wysłałby pieniądze w nicość), tylko zapowiada,
    że dane przyjdą osobno."""
    ctx = ctx or {}
    adres = (ctx.get("wallet") or settings.crypto_wallet or "").strip()
    if not adres:
        return []
    siec = (ctx.get("network") or settings.crypto_network or "").strip()
    wiersze = []
    if siec:
        wiersze.append(("Network", siec))
    wiersze.append(("Wallet address", adres))
    if settings.crypto_memo:
        wiersze.append(("Memo / tag", settings.crypto_memo))
    return wiersze


def _bogo_upgrade(ctx: dict) -> bool:
    """Czy to realny upgrade: klient zapłacił za mniejszy tier, dostał większy."""
    paid, got = ctx.get("bogo_paid_size"), ctx.get("initial_balance")
    try:
        return bool(paid) and float(paid) < float(got)
    except (TypeError, ValueError):
        return False


# Notatka, którą panel wpisuje grantowi z /freeaccount. Ta sama treść maila
# obsługuje wszystkie granty, a domyślna mówi o promocji „buy one, get one
# free" — do kogoś, kto niczego nie kupił, byłoby to po prostu nieprawdą.
FREE_PROGRAM_NOTE = "free program"


def _z_darmowego_programu(ctx: dict) -> bool:
    return (ctx.get("grant_note") or "").strip().lower() == FREE_PROGRAM_NOTE


def _bogo_subject(ctx: dict) -> str:
    if _z_darmowego_programu(ctx):
        return "Your free challenge account is open 🎉"
    return "Your upgraded challenge is live 🎁" if _bogo_upgrade(ctx) else "Your BOGO challenge is live 🎁"


def _bogo_intro(ctx: dict) -> str:
    """Konto przyznane przez admina opisujemy jako promocję BOGO, nigdy jako
    „aktywowane przez nasz zespół". Gdy znamy opłacony tier, mówimy o upgrade —
    to zdanie jest wtedy prawdziwe. Gdy go nie znamy, nie sugerujemy płatności."""
    if _z_darmowego_programu(ctx):
        return ("Your free challenge account is open and ready to trade. "
                "Same rules and the same profit split as a purchased account.")
    if _bogo_upgrade(ctx):
        return (f"Your promotion has been applied. You paid for the "
                f"{_tier(ctx.get('bogo_paid_size'))} tier and we upgraded your allocation to "
                f"${_num(ctx.get('initial_balance'))}.")
    # Zdanie zaczyna sie od „Your", bo wersja HTML doklada powitanie i zamienia
    # pierwsza litere na mala — „buy one, get one free" nie moze wtedy wypasc
    # na poczatku, bo wygladaloby jak polecenie zamiast nazwy promocji.
    return ("Your bonus challenge is live: buy one, get one free. "
            "Same rules and the same profit split as a purchased account.")


def _bez_hasla_txt(ctx: dict) -> str:
    """Akapit dla klienta, któremu nie mintujemy linku do ustawienia hasła.

    Ten mail bywa pierwszym powodem, żeby wejść do portalu, a `setup_url`
    dostaje wyłącznie konto z flagą `must_set_password`. Bez tej furtki reszta
    czyta „zaloguj się" i nie ma czym: flagi nie ma na koncie starszym niż ona
    sama ani na przejętym przez Google, a hasło MT5 z tabelki wyżej do portalu
    nie pasuje — to najczęstsza pomyłka w zgłoszeniach do supportu.
    """
    if not ctx.get("forgot_url"):
        return ""
    return (f"\nThe password above is for MetaTrader 5 only. If you have never set a "
            f"password for the {settings.site_name} portal, get one here:\n"
            f"{ctx['forgot_url']}\n")


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
            f"The portal installs as an app straight from your browser, no app store needed:\n"
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
            + (f"\nWe opened your portal account for you, so it has no password yet. "
               f"Set one here (the link works for 7 days; after that use \"Forgot password\" "
               f"on the sign-in screen):\n{ctx.get('setup_url')}\n"
               if ctx.get("setup_url") else _bez_hasla_txt(ctx))
            + f"Good luck. Track your progress in the dashboard.",
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
        "account_scaled": (
            f"Moving up to a ${_num(ctx.get('new_size'))} account 📈",
            f"{name}, you chose the bigger account instead of the payout. "
            f"We are setting up a new ${_num(ctx.get('new_size'))} account for you, funded from "
            f"day one, with the limits of that plan. Your login and password arrive in a "
            f"separate email as soon as it is ready.",
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
        "portal_invite": (
            f"Your {brand} portal access",
            f"{name}, your {brand} account is ready — we opened it for you, so it "
            f"has no password yet.\n\nSet one here (the link works for 7 days):\n"
            f"{ctx.get('setup_url')}\n\n"
            f"After that you can sign in any time at {ctx.get('portal_url')} — "
            f"if the link expires, use \"Forgot password\" on the sign-in screen.",
        ),
        "kyc_rejected": (
            "Identity verification — action needed",
            f"{name}, we could not verify your identity with the documents provided."
            + (f"\n\nReason: {ctx.get('reason')}" if ctx.get("reason") else "")
            + "\n\nPlease review your details and submit the verification again from your dashboard.",
        ),
        "kyc_requested": (
            "Action needed: verify your identity"
            if ctx.get("locked") else "Verify your identity to unlock payouts",
            f"{name}, "
            + ("we need to confirm who you are before you keep using the "
               "portal. Your trading account keeps running as normal — the "
               "dashboard is paused until we have checked your documents, and "
               "we do that within one business day."
               if ctx.get("locked") else
               "identity verification is now open on your account — it is "
               "the step we need before we can send you a payout.")
            + ("\n\nYour previous submission could not be verified, so please "
               "check the details and send it again."
               if ctx.get("again") else "")
            + f"\n\nOpen the Verification tab in your portal and upload your "
              f"documents — it takes a couple of minutes:\n"
              f"{ctx.get('portal_url')}?view=kyc",
        ),
        "ticket_reply": (
            f"Support replied to your ticket #{ctx.get('ticket_id')} 💬",
            f"{name}, our support team replied to your ticket "
            f"\"{ctx.get('subject')}\". Log in to the portal to read the answer.",
        ),
        "credits_granted": (
            f"Store credit added: ${_num(ctx.get('amount'))} 🎁",
            f"{name}, we've just added ${_num(ctx.get('amount'))} of store credit to your "
            f"account. Your balance is now ${_num(ctx.get('balance'))} and it is applied "
            f"automatically at your next checkout.",
        ),
        "challenge_granted": (
            _bogo_subject(ctx),
            f"Hi {name}!\n\n{_bogo_intro(ctx)}\n\n"
            f"  Allocation: ${_num(ctx.get('initial_balance'))}\n"
            f"  MT5 login:  {ctx.get('platform_login')}\n"
            f"  Password:   {ctx.get('platform_password')}\n"
            f"  Server:     {ctx.get('platform_server')}\n"
            + (f"\nWe opened your portal account for you, so it has no password yet. "
               f"Set one here (the link works for 7 days; after that use \"Forgot password\" "
               f"on the sign-in screen):\n{ctx.get('setup_url')}\n"
               if ctx.get("setup_url") else _bez_hasla_txt(ctx))
            + f"\nLog in to the portal to see your objectives and progress."
            + (f"\n{ctx.get('portal_url')}" if ctx.get("portal_url") else ""),
        ),
        "verify_email": (
            f"{ctx.get('code')} is your {brand} verification code",
            f"Hi {name}!\n\nConfirm your e-mail address to finish setting up your "
            f"{brand} account.\n\n  Verification code: {ctx.get('code')}\n\n"
            f"Enter the code in the portal, or open this link:\n{ctx.get('verify_url')}\n\n"
            f"The code and link are valid for 24 hours. If you didn't create an "
            f"account, you can safely ignore this e-mail.",
        ),
        "checkout_recovery": (
            "Your challenge is still waiting",
            f"Hi {name},\n\nyou started checkout for the {ctx.get('product_label')} "
            f"challenge but the payment never came through — the card may have been "
            f"declined, or the page was simply closed.\n\n"
            f"  Challenge: {ctx.get('product_label')}\n"
            f"  Amount:    ${_num(ctx.get('amount'))}\n\n"
            # Najczestsze pytanie po porzuconym koszyku: „czy zjadlo mi kredyty".
            # Nie zjadlo (schodza dopiero przy domknieciu platnosci) i mail musi
            # to powiedziec sam, zanim klient napisze do supportu.
            + (f"Your ${_num(ctx.get('credits_used'))} of store credit was NOT used — "
               f"it is still on your balance and will be applied when you complete "
               f"the purchase.\n\n" if (ctx.get("credits_used") or 0) > 0 else "")
            + f"Nothing was charged. Pick up where you left off:\n"
            f"{settings.app_base_url}/portal?view=store\n\n"
            f"If you ran into an error on the payment page, reply to this e-mail "
            f"and we'll sort it out.",
        ),
        "flash_offer": (
            ctx.get("title") or f"Flash sale: {_num(ctx.get('pct'))}% off — limited time",
            f"Hi {name},\n\n"
            f"a limited-time discount just went live on your account:\n\n"
            f"  Offer:     {ctx.get('title') or 'Flash sale'}\n"
            f"  Discount:  {_num(ctx.get('pct'))}% off {ctx.get('plans') or 'selected challenges'}\n"
            f"  Ends:      {ctx.get('ends')}\n\n"
            f"The discount is applied automatically at checkout — no code needed:\n"
            f"{ctx.get('url') or settings.app_base_url + '/portal?view=store'}\n\n"
            f"After the deadline the regular price applies.",
        ),
        "order_awaiting_payment": (
            f"Awaiting your payment — {ctx.get('product_label')}",
            f"Hi {name},\n\nwe've put your {ctx.get('product_label')} challenge on hold "
            f"and we're waiting for the payment to arrive.\n\n"
            f"  Challenge:  {ctx.get('product_label')}\n"
            f"  Amount due: ${_kwota(ctx.get('amount'))}\n"
            f"  Reference:  {ctx.get('reference')}\n"
            + ("  Included:   a second account of the same size — free (Buy 1 Get 1)\n"
               if ctx.get("bogo") else "")
            + "\n"
            + ("Send the amount above to:\n"
               + "".join(f"  {k}: {v}\n" for k, v in _payment_lines(ctx))
               + "\nWhen it's on its way, reply to this e-mail with the transaction "
                 "hash and the reference above — that is the fastest way for us to "
                 "match it.\n\n"
               if _payment_lines(ctx) else
               "We'll send you the payment details in a separate message shortly.\n\n")
            + (f"The moment we confirm the payment both of your accounts are created "
               f"automatically and two sets of MT5 credentials land in your inbox.\n\n"
               if ctx.get("bogo") else
               f"The moment we confirm the payment your account is created "
               f"automatically and the MT5 credentials land in your inbox.\n\n")
            + f"Questions, or changed your mind? Just reply to this e-mail.",
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
        # break-all, bo adres portfela to 34-44 znaki bez spacji: bez tego
        # rozpychał maila na 591 px i na telefonie trzeba było go przewijać
        # w bok, żeby zobaczyć koniec adresu.
        f'<td align="right" style="padding:13px 0;border-top:1px solid {_HAIR};'
        f'font:500 15px/1.4 {_MONO};color:{_INK};word-break:break-all">{v}</td></tr>'
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
        if granted and _z_darmowego_programu(ctx):
            badge, headline = "Free challenge", "Your free challenge account is open"
        elif granted:
            badge = ctx.get("grant_note") or "BOGO activation complete"
            headline = "Your upgraded challenge is live" if upgraded else "Your BOGO challenge is live"
        else:
            badge = f"{_promo_name()} applied" if upgraded else "Account ready"
            headline = ("Your upgraded challenge account is ready" if upgraded
                        else "Your challenge account is ready")
        steps = ctx.get("steps")
        kind = f"{steps}-Step challenge on MT5" if steps else "Challenge on MT5"
        lead = _bogo_intro(ctx)
        if granted:
            intro = f"Hi {name}, {lead[0].lower()}{lead[1:]}"
        elif upgraded:
            intro = (f"Hi {name}, your account is ready, and your {_promo_name()} "
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
        ]
        # Konto założone ZA klienta nie ma jeszcze hasła do portalu — „View
        # Dashboard" byłby wtedy przyciskiem pod drzwi bez klucza.
        if ctx.get("setup_url"):
            parts += [
                _button_html("Set Your Password", ctx["setup_url"]),
                _note_html("We opened the portal account for you, so it has no password yet — "
                           "the button above sets one (the link works for 7 days; after that use "
                           "“Forgot password” on the sign-in screen). The credentials above are "
                           "only for the MetaTrader 5 platform."),
            ]
        else:
            # Brak `setup_url` nie znaczy „ten klient ma hasło" — znaczy tylko
            # tyle, że nie mamy prawa mu go nadać (patrz `_bez_hasla_txt`).
            # Dlatego obok przycisku musi stać druga droga, ta samoobsługowa.
            zapomniane = (f" Never set one? <a href=\"{ctx['forgot_url'].replace('&', '&amp;')}\" "
                          f"style=\"color:{_GOLD}\">Get a portal password</a> — "
                          f"we will e-mail you a link." if ctx.get("forgot_url") else "")
            parts += [
                _button_html("View Dashboard", f"{portal}?view=accounts"),
                _note_html("Sign in to the portal with your e-mail address and portal "
                           "password — the credentials above are only for the "
                           "MetaTrader 5 platform." + zapomniane),
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
         The portal installs as an app straight from your browser, no app store needed.<br>
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
                       f"Great job {name}. Account {login} moved from "
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
    elif event == "account_scaled":
        parts = [
            _head_html("Moving up", "You picked the bigger account",
                       f"{name}, instead of taking the payout you moved up a plan. We are setting "
                       f"up a new account at the higher size, funded from day one. Your login and "
                       f"password arrive in a separate email as soon as it is ready."),
            _stat_html("New account size", f"${_num(ctx.get('new_size'))}",
                       f"was ${_num(ctx.get('previous_size'))}"),
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
                       f"{', including your challenge fee refund' if ctx.get('fee_refund') else ''}. "
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
    elif event == "kyc_requested":
        zablokowany = bool(ctx.get("locked"))
        parts = [
            _head_html("Verification",
                       "Verify your identity to reopen your dashboard"
                       if zablokowany else "One step left before your payout",
                       (f"{name}, we need to confirm who you are before you keep "
                        f"using the portal. Your trading account keeps running as "
                        f"normal — only the dashboard is paused, and we review "
                        f"documents within one business day."
                        if zablokowany else
                        f"{name}, identity verification is now open on your account "
                        f"— it is the last thing we need before sending you money.")
                       + (" Your previous submission could not be verified, so "
                          "please check the details and send it again."
                          if ctx.get("again") else "")),
            _button_html("Verify Your Identity", f"{portal}?view=kyc"),
            _note_html("It takes a couple of minutes: your ID document and proof "
                       "of address."
                       + (" Your dashboard opens as soon as it's approved."
                          if zablokowany else
                          " Payout requests are released once it's done.")),
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
    elif event == "portal_invite":
        parts = [
            _head_html(None, "Your portal access is ready",
                       f"{name}, your {brand} account is ready — we opened it for "
                       f"you, so it has no password yet."),
            _button_html("Set Your Password", ctx.get("setup_url") or portal),
            _note_html("The link works for 7 days. If it expires, use "
                       "“Forgot password” on the sign-in screen to get "
                       "a fresh one."),
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
                       f"{name}, we've just added store credit to your account. "
                       f"It is applied automatically at your next checkout."),
            _stat_html("Credit added", f"${_num(ctx.get('amount'))}",
                       f"Current balance ${_num(ctx.get('balance'))}"),
            _button_html("Browse Challenges", f"{portal}?view=store"),
        ]
    elif event == "verify_email":
        parts = [
            _head_html("Verify", "Confirm your e-mail",
                       f"Hi {name}, enter this code in the portal, or tap the "
                       f"button below, to confirm your e-mail address."),
            _stat_html("Verification code", str(ctx.get("code") or "")),
            _button_html("Verify E-mail", ctx.get("verify_url") or portal),
            _note_html("The code and link are valid for 24 hours. If you didn't "
                       "create an account, you can safely ignore this e-mail."),
        ]
    elif event == "checkout_recovery":
        kredyt = float(ctx.get("credits_used") or 0)
        parts = [
            _head_html("Unfinished checkout", "Your challenge is still waiting",
                       f"Hi {name}, you started checkout but the payment never went "
                       f"through — the card may have been declined, or the page was "
                       f"simply closed. Nothing was charged."),
            _stat_html("Challenge", str(ctx.get("product_label") or ""),
                       f"${_num(ctx.get('amount'))}"),
            _button_html("Complete Your Purchase", f"{portal}?view=store"),
            _note_html(
                (f"Your ${_num(kredyt)} of store credit was not used — it is still "
                 f"on your balance and applies automatically when you complete the "
                 f"purchase. " if kredyt > 0 else "")
                + "Ran into an error on the payment page? Reply to this e-mail and "
                  "we'll sort it out."),
        ]
    elif event == "flash_offer":
        parts = [
            _head_html("Flash sale", str(ctx.get("title") or "Limited-time offer"),
                       f"Hi {name}, a limited-time discount just went live on your "
                       f"account. It is applied automatically at checkout — no code "
                       f"needed."),
            _stat_html("Discount", f"-{_num(ctx.get('pct'))}%",
                       str(ctx.get("plans") or "selected challenges")),
            _button_html("See the Offer", ctx.get("url") or f"{portal}?view=store"),
            _note_html(f"The offer ends {ctx.get('ends')}. After that the regular "
                       f"price applies."),
        ]
    elif event == "order_awaiting_payment":
        dane = _payment_lines(ctx)
        parts = [
            _head_html("Awaiting payment", "Your challenge is on hold",
                       f"Hi {name}, your order is in and we're waiting for the payment "
                       f"to arrive. Nothing has been charged — send it over and we'll "
                       f"take it from there."),
            _stat_html("Challenge", str(ctx.get("product_label") or ""),
                       f"Amount due ${_kwota(ctx.get('amount'))}"),
            _rows_html([("Reference", ctx.get("reference") or ""),
                        *([("Included", "+ second account, same size — free (Buy 1 Get 1)")]
                          if ctx.get("bogo") else []),
                        *dane]),
            _note_html(
                "When the payment is on its way, reply to this e-mail with the "
                "transaction hash and the reference above — that is the fastest way "
                "for us to match it. "
                if dane else
                "We'll send you the payment details in a separate message shortly. "),
            _note_html("Both of your accounts are created automatically the moment we "
                       "confirm the payment — this order includes a free second account "
                       "of the same size — and the MT5 credentials go straight to this "
                       "inbox."
                       if ctx.get("bogo") else
                       "Your account is created automatically the moment we confirm "
                       "the payment, and the MT5 credentials go straight to this "
                       "inbox."),
        ]
    else:
        return None
    return _shell(parts)


# Kategoria preferencji per zdarzenie (Settings -> Notification Preferences).
# Zdarzenia TRANSAKCYJNE (welcome, credentials/challenge_granted z poświadczeniami
# MT5 za opłacony produkt, verify_email, password_reset, order_awaiting_payment
# z instrukcją wpłaty do WŁASNEGO zamówienia klienta) celowo NIE mają wpisu —
# muszą dojść zawsze, niezależnie od preferencji. Tak samo `kyc_requested`:
# wysyła je admin ręcznie, jednej osobie, żeby odblokować JEJ pieniądze —
# zjedzenie tego przez przełącznik „updates" znaczyłoby, że klient czeka na
# wypłatę, a my myślimy, że poprosiliśmy.
_PREF_BY_EVENT = {
    "kyc_approved": "notify_updates", "kyc_rejected": "notify_updates",
    "ticket_reply": "notify_updates",
    "credits_granted": "notify_updates",
    "phase_passed": "notify_trading", "account_funded": "notify_trading",
    "account_scaled": "notify_trading",
    "breached": "notify_trading",
    # tylko push+centrum (push.send_event z pollera) — maila do tego zdarzenia nie ma
    "limit_warning": "notify_trading",
    "payout_requested": "notify_payouts", "payout_approved": "notify_payouts",
    "payout_rejected": "notify_payouts",
    # recap idzie tylko przez push/centrum (push.daily_recap), nie mailem
    "daily_recap": "notify_marketing",
    # Przypomnienie o porzuconym koszyku dotyczy wlasnego zakupu klienta, ale
    # jest zachęta do kupna — wiec pod marketingiem, jak recap. Kto wypisal sie
    # z ofert, nie dostaje tez tego.
    "checkout_recovery": "notify_marketing",
    # Oferta flash to czysty marketing — kto wypisal sie z ofert, nie dostaje.
    "flash_offer": "notify_marketing",
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


# Wysyłka jest sieciowa i wolna: sam SMTP to kilkanaście round-tripów do Brevo,
# a każde `send()` siedzi w środku POST-a, na który ktoś patrzy (rejestracja
# zmierzona: 706 ms, z czego ~700 ms to SMTP). Gdy trwa request, zlecenia lądują
# w tej kolejce, a `main` odpala je jako BackgroundTask — czyli PO odesłaniu
# odpowiedzi. Poza requestem (cron, poller, testy wołające notify wprost)
# kolejki nie ma i wysyłka idzie od razu, tak jak dotąd.
_kolejka: ContextVar[list | None] = ContextVar("notify_kolejka", default=None)


@contextmanager
def kolejkuj():
    """Na czas bloku zbiera wysyłki zamiast je wykonywać; oddaje listę zadań."""
    zadania: list = []
    token = _kolejka.set(zadania)
    try:
        yield zadania
    finally:
        _kolejka.reset(token)


def _odloz(fn, *args) -> bool:
    zadania = _kolejka.get()
    if zadania is None:
        return False
    zadania.append((fn, args))
    return True


def _zapisz_w_dzienniku(event: str, to_email: str, subject: str,
                        blad: str | None) -> None:
    """Ślad każdej próby wysyłki. Best-effort: dziennik nie ma prawa wywrócić
    ani wysyłki, ani requestu, w którego tle leci — stąd goły `except`."""
    try:
        from .db import SessionLocal
        from .models import MailLog
        session = SessionLocal()
        try:
            session.add(MailLog(event=event, to_email=to_email,
                                subject=(subject or "")[:200],
                                ok=blad is None, error=blad))
            session.commit()
        finally:
            session.close()
    except Exception:  # pragma: no cover
        pass


def czego_brakuje() -> list[str]:
    """Nazwy zmiennych, bez których kanał do KLIENTA stoi — dla paska w panelu.

    Odpowiednik `lead_mail.czego_brakuje()`, tyle że dla wysyłki, którą idzie
    wszystko, co klient dostaje od platformy: poświadczenia MT5, reset hasła,
    potwierdzenie wypłaty. Kanał leadowy miał ten wskaźnik od początku, ten nie
    — i dlatego „maile w ogóle nie dochodzą" dawało się przeoczyć tygodniami.

    `MAIL_FROM` liczy się jako brak także wtedy, gdy zostało na domyślnym
    `@propfunding.local`: żaden dostawca nie podpisze nadawcy w domenie `.local`,
    więc taki adres nie jest konfiguracją, tylko jej brakiem w przebraniu.
    """
    nadawca = (settings.mail_from or "").strip()
    return [nazwa for nazwa, wartosc in (
        ("SMTP_HOST", settings.smtp_host),
        ("MAIL_FROM", "" if nadawca.endswith(".local") else nadawca))
        if not wartosc]


def send(event: str, to_email: str | None, ctx: dict | None = None) -> None:
    if _odloz(_send_teraz, event, to_email, ctx):
        return
    _send_teraz(event, to_email, ctx)


def _send_teraz(event: str, to_email: str | None, ctx: dict | None = None) -> None:
    ctx = ctx or {}
    subject, body = _render(event, ctx)
    adres = to_email          # oryginal dla kanalu push/centrum (pref sprawdza sam)

    # 1) e-mail
    if to_email and to_email.lower().endswith("@imported.local"):
        # Konta z importu nie mają skrzynek — każda taka wysyłka to twardy
        # bounce w Brevo, a seria bounców psuje reputację nadawcy.
        print(f"[notify] pominięto mail '{event}' do {to_email} (adres importowy)")
        to_email = None
    if to_email and not _email_allowed(event, to_email):
        print(f"[notify] pominięto mail '{event}' do {to_email} (preferencje tradera)")
        to_email = None
    if to_email:
        html = _render_html(event, ctx, subject)
        blad = None
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
                blad = str(e)[:500]
        else:
            # Bez hosta mail NIGDZIE nie idzie — a do 2026-08-19 lądował w dzienniku
            # jako `ok=True`, więc panel pokazywał zielone „wysłane" dla czegoś, co
            # nie opuściło serwera, a kafelek porażek stał na zerze. Cisza ważniejsza
            # od wygody deva: brak konfiguracji to porażka wysyłki i ma być widoczna.
            blad = "SMTP_HOST nie ustawiony — mail nigdzie nie poszedł"
            print(f"\n📧 [MAIL → {to_email}] {subject}\n{body}\n")
            if html:
                # dev: podglad wersji HTML w przegladarce, bez SMTP
                try:
                    out = Path(tempfile.gettempdir()) / f"propfunding-mail-{event}.html"
                    out.write_text(html, encoding="utf-8")
                    print(f"   ↳ HTML preview: {out}")
                except Exception:  # pragma: no cover
                    pass
        _zapisz_w_dzienniku(event, to_email, subject, blad)

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


def notify_admins(event: str, title: str, body: str = "",
                  url: str = "/admin", tag: str | None = None) -> None:
    """Dzwonek + web push do wszystkich kont is_admin (url -> panel /admin).

    Osobna ścieżka od send(): bez maila i bez bramki preferencji tradera —
    to sygnał operacyjny „coś przyszło". NIGDY nie rzuca: zdarzenie admina
    nie może wywrócić requestu tradera, który je wywołał.

    `url` pozwala zdarzeniu wskazać konkretne miejsce w panelu (deep-link
    `/admin?lead=…`), a `tag` skleić serię powiadomień o tym samym obiekcie
    w jedno na ekranie telefonu — przeglądarka podmienia notyfikację o tym
    samym tagu zamiast układać stos."""
    if _odloz(_notify_admins_teraz, event, title, body, url, tag):
        return
    _notify_admins_teraz(event, title, body, url, tag)


def _notify_admins_teraz(event: str, title: str, body: str = "",
                         url: str = "/admin", tag: str | None = None) -> None:
    try:
        from . import push
        from .db import SessionLocal
        from .models import Trader

        session = SessionLocal()
        try:
            admini = [(t.id, t.ui_prefs) for t in
                      session.query(Trader).filter(Trader.is_admin.is_(True)).all()]
            # Dzwonek w panelu dostaje wpis ZAWSZE — preferencje wyciszają
            # brzęczenie telefonu, nie dostęp do informacji.
            for tid, _prefs in admini:
                push._center_row(session, tid, event, title, body, url)
            session.commit()
        finally:
            session.close()
        for tid in _chca_push(admini, event):
            push.send_to_trader(tid, title, body, url=url, tag=tag or event)
    except Exception as e:  # pragma: no cover
        print(f"[notify] admin błąd: {e}")


def _chca_push(admini: list[tuple[int, str | None]], event: str) -> list[int]:
    """Adminy, którzy NIE wyciszyli tej kategorii web pushy.

    Preferencje siedzą w `ui_prefs.admin_push` (panel, Settings → Notifications)
    jako mapa {kategoria: false} — brak wpisu znaczy „chcę", więc nowa kategoria
    zdarzeń brzęczy u wszystkich, dopóki ktoś jej świadomie nie zgasi."""
    wybrani: list[int] = []
    for tid, prefs_json in admini:
        try:
            prefs = (json.loads(prefs_json or "{}") or {}).get("admin_push") or {}
        except ValueError:
            prefs = {}
        if prefs.get(event) is False:
            continue
        wybrani.append(tid)
    return wybrani
