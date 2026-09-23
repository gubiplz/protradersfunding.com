"""Wbudowane szablony maili pisanych z ręki w panelu — per nadawca.

Do 2026-09 panel miał wyłącznie szablony zapisywane przez dział (tabela
`lead_mail_templates`), więc każdy pisał swoje i każde wdrożenie na nowej bazie
zaczynało od pustej listy. Te tutaj są STAŁE w kodzie: nie da się ich skasować
ani nadpisać, a „Save template" na zmienionej treści robi kopię pod własną
nazwą. Dzięki temu poprawka tekstu jest commitem, a nie edycją w bazie, której
nikt nie zobaczy w przeglądzie.

Podział po NADAWCY, bo to nie jest to samo pismo w dwóch kolorach: mail spod
platformy mówi o portalu, KYC, wypłacie i regułach; mail spod marki landingu
mówi o zgłoszeniu, desku i koncie, którym zarządzamy. Panel pokazuje tylko
szablony pasujące do wybranego nadawcy (plus zapisane bez nadawcy).

Placeholdery: `{name}` podstawia PRZEGLĄDARKA przed podglądem (admin widzi
dokładnie to, co wyjdzie); `{portal_url}`, `{telegram_url}`, `{support_email}`
podstawia serwer w GET, z ustawień — bo domena marki partnerskiej nie ma prawa
siedzieć w kodzie (repo jest publiczne), a portal ma inny adres na każdym
środowisku. Akapit będący samym pustym placeholderem wypada w całości, żeby
mail bez skonfigurowanego Telegrama nie wychodził z pustym guzikiem.

Zasady treści, te same co w `lead_mail.tresc()`: werdykt w pierwszym zdaniu,
jedno wyjście (jeden link), zero zmyślonej personalizacji, temat bez sprzedaży.
Stopka po `--` to szara stopka w HTML-u obu papeterii.
"""
from __future__ import annotations

from .config import get_settings

settings = get_settings()

PTF = "ptf"
FX = "fx"

# (klucz, nadawca, nazwa na liście, temat, treść)
WBUDOWANE: list[tuple[str, str, str, str, str]] = [
    # ------------------------------------------------------------------ PTF --
    ("ptf-credentials", PTF, "Account credentials — follow-up",
     "Your account login details",
     "Hi {name},\n\n"
     "Your account is set up and the login details went out in a separate "
     "e-mail. If it has not arrived, check the spam folder first — that is "
     "where it usually ends up.\n\n"
     "Everything about the account (balance, rules, payouts) is in the portal:\n\n"
     "{portal_url}\n\n"
     "If the details are still missing after that, reply to this e-mail and we "
     "will send them again.\n\n"
     "--\nSupport · {support_email}"),

    ("ptf-kyc", PTF, "KYC verification needed",
     "One step before your payout: identity check",
     "Hi {name},\n\n"
     "Before the first payout can go out we need to verify your identity. "
     "It is a one-time step and takes a few minutes.\n\n"
     "What to prepare: a photo of your ID (passport, national ID or driving "
     "licence) and your address.\n\n"
     "Upload it in the portal under KYC:\n\n"
     "{portal_url}\n\n"
     "We review requests within one business day and confirm by e-mail.\n\n"
     "--\nSupport · {support_email}"),

    ("ptf-payout-approved", PTF, "Payout approved",
     "Your payout has been approved",
     "Hi {name},\n\n"
     "Your payout request has been approved. The transfer goes out within the "
     "next business days to the details you gave in the portal.\n\n"
     "Trading on the account continues as normal — the approved amount is "
     "already deducted from the balance you see.\n\n"
     "You can check the status any time here:\n\n"
     "{portal_url}\n\n"
     "--\nSupport · {support_email}"),

    ("ptf-breach", PTF, "Rule breach — what happened",
     "About your account: a rule was hit",
     "Hi {name},\n\n"
     "Straight answer: the account hit one of the trading rules and is now "
     "closed for trading. The exact rule, the time and the equity at that "
     "moment are shown on the account card in the portal.\n\n"
     "{portal_url}\n\n"
     "This is about the account, not about you. If you want to go again, the "
     "same plan is available and your KYC (if done) carries over.\n\n"
     "If you think the breach was recorded in error, reply to this e-mail with "
     "the account number and we will look at the log together.\n\n"
     "--\nSupport · {support_email}"),

    ("ptf-inactive", PTF, "Inactivity check-in",
     "Your account is waiting",
     "Hi {name},\n\n"
     "Your account is open and has not seen a trade for a while. Nothing is "
     "wrong with it — this is just a reminder that it is there.\n\n"
     "If something is holding you back (platform login, a rule you are unsure "
     "about, a question about payouts), reply to this e-mail. Most of these "
     "take one message to sort out.\n\n"
     "{portal_url}\n\n"
     "--\nSupport · {support_email}"),

    ("ptf-portal-help", PTF, "Portal access help",
     "Getting into your portal",
     "Hi {name},\n\n"
     "If you cannot get into the portal, the fastest fix is “Forgot password” "
     "on the login page — the reset link is valid for one hour and arrives "
     "within a minute.\n\n"
     "{portal_url}\n\n"
     "Log in with the e-mail this message was sent to. If you signed up with "
     "Google, use the Google button instead of a password.\n\n"
     "Still stuck? Reply here and tell us what you see on the screen.\n\n"
     "--\nSupport · {support_email}"),

    # ------------------------------------------------------------------- FX --
    ("fx-free-ready", FX, "Free account is ready",
     "Your FREE challenge account is ready",
     "Hi {name},\n\n"
     "Good news: your free challenge account has been created and is active.\n\n"
     "Our desk manages the positions on it — you do not need to place any "
     "trades yourself. If the markets are closed right now (weekend), the "
     "first positions go in when they open.\n\n"
     "The login details for the trading platform come in a separate e-mail "
     "from the platform. Check the spam folder if you do not see it.\n\n"
     "Questions go to the desk on Telegram:\n\n"
     "{free_telegram_url}\n\n"
     "--\nForex Passing\n"
     "You are getting this because you applied on our site."),

    ("fx-accepted", FX, "Application accepted — next steps",
     "You're in — what happens next",
     "Hi {name},\n\n"
     "Your application is a yes. Here is what happens now, in order:\n\n"
     "1. You send us the login to the challenge account we will manage (or "
     "we set one up for you, if that is what you applied for).\n\n"
     "2. Our desk takes over the trading. You keep full read access and see "
     "every position as it happens.\n\n"
     "3. Once the account is funded and pays out, the split is settled after "
     "each payout — never before.\n\n"
     "Everything else is one short conversation on Telegram:\n\n"
     "{telegram_url}\n\n"
     "--\nForex Passing\n"
     "You are getting this because you applied on our site."),

    ("fx-need-login", FX, "We need your platform login",
     "One thing we need from you: the account login",
     "Hi {name},\n\n"
     "We are ready to start, and the only thing missing is the login to the "
     "challenge account.\n\n"
     "Send us three things: the account number, the password (trading, not "
     "investor) and the server name — exactly as the prop firm sent them to "
     "you. Send them on Telegram, not by e-mail:\n\n"
     "{telegram_url}\n\n"
     "As soon as we have them, the desk logs in and confirms the same day.\n\n"
     "--\nForex Passing"),

    ("fx-started", FX, "Management has started",
     "We are on your account",
     "Hi {name},\n\n"
     "The desk has logged in and management has started. From here on you "
     "do not need to do anything on the account — please do not place or "
     "close trades yourself, it would cut across the plan.\n\n"
     "You can follow every position live on the platform with your own login.\n\n"
     "We send a short update when something worth knowing happens; you can "
     "always ask on Telegram:\n\n"
     "{telegram_url}\n\n"
     "--\nForex Passing"),

    ("fx-weekly", FX, "Weekly update",
     "This week on your account",
     "Hi {name},\n\n"
     "Quick update on the account we manage for you.\n\n"
     "Where it stands: [current balance / phase / days traded].\n\n"
     "What we did: [one or two sentences — trades taken, what worked, what "
     "did not].\n\n"
     "What is next: [next milestone — profit target, minimum days, payout "
     "window].\n\n"
     "Any questions, the desk is on Telegram:\n\n"
     "{telegram_url}\n\n"
     "--\nForex Passing"),

    ("fx-payout-split", FX, "Payout & split reminder",
     "Your payout — and how the split works",
     "Hi {name},\n\n"
     "A payout from your managed account is on the way. Here is how the split "
     "is settled, so there are no surprises:\n\n"
     "The prop firm pays the full amount to you. Once it lands, our share is "
     "settled from it — we never take anything before you have been paid.\n\n"
     "Reply to this e-mail or message the desk when the payout arrives and we "
     "will send the settlement details:\n\n"
     "{telegram_url}\n\n"
     "--\nForex Passing"),

    ("fx-reengage", FX, "Re-engagement",
     "Still interested?",
     "Hi {name},\n\n"
     "You applied a while ago and we did not manage to get started. No "
     "problem — this is a check whether the timing is better now.\n\n"
     "If yes, one message on Telegram is enough and we pick up where we left "
     "off:\n\n"
     "{telegram_url}\n\n"
     "If not, no hard feelings and nobody will chase you.\n\n"
     "--\nForex Passing\n"
     "You are getting this because you applied on our site."),
]


TG = "tg"

# Wiadomości na Telegram pisane Z KONTA ADMINA (nie bota): panel otwiera czat
# z gotowym tekstem, człowiek naciska „wyślij". Krótko, bez linków, bez
# podpisu firmy — DM od człowieka nie ma stopki. Każdy szablon ma KILKA
# wariantów tej samej treści; panel losuje jeden przy otwarciu i ma przycisk
# „inne ujęcie", żeby dziesięć osób nie dostało dziesięciu identycznych
# wiadomości — to jedyna rzecz, po której DM z konta wygląda na automat.
# (klucz, nazwa, [warianty])
WBUDOWANE_TG: list[tuple[str, str, list[str]]] = [
    ("tg-hello", "First message after the application", [
        "Hey {name}, this is the Forex Passing desk — your application just landed with me. "
        "Ready to walk you through the next step when you are.",
        "Hi {name}, Forex Passing desk here. I've got your application in front of me — "
        "want to go through the next step now, or later today?",
        "{name}, hi — desk at Forex Passing. Saw your application come in. "
        "Got a couple of minutes to sort the next step?",
    ]),
    ("tg-free-ready", "Free account is ready", [
        "Hey {name}, good news — your free challenge account is set up and live. "
        "Login details are in your e-mail (check spam too). We're managing the positions, "
        "so nothing for you to do on the account. Any questions, I'm here.",
        "{name}, your free account is ready. The platform sent the login to your e-mail — "
        "have a look in spam if it's not in the inbox. The desk runs the trades, you just watch. "
        "Shout if anything's unclear.",
        "Hi {name} — account's live. Login went out by e-mail a moment ago. "
        "We take it from here on the trading side; ping me if you don't see the e-mail.",
    ]),
    ("tg-need-login", "Need the platform login", [
        "Hey {name}, we're ready to start — the only thing missing is the login to the "
        "challenge account. Send me the account number, the trading password and the server, "
        "exactly as the prop firm gave them to you.",
        "{name}, one thing before we start: I need the login for the challenge account. "
        "Account number, trading password (not investor) and the server name — "
        "paste them here and I'll confirm the same day.",
        "Hi {name} — can you send the challenge account login when you get a sec? "
        "Number, password, server. Then we're off.",
    ]),
    ("tg-started", "Management has started", [
        "{name}, we're logged in and the desk has started on your account. "
        "Please don't place or close trades yourself from now — it cuts across the plan. "
        "You can follow everything live with your own login.",
        "Hey {name} — management is on. From here you don't need to touch the account "
        "(please don't, it clashes with what the desk is doing). I'll send a note when "
        "something worth knowing happens.",
    ]),
    ("tg-checkin", "Check-in / no reply", [
        "Hey {name}, just checking in — did you get my last message? No rush, "
        "just want to make sure it didn't get lost.",
        "{name}, quick one: still up for this? If the timing's off, no problem — "
        "tell me and I'll park it.",
        "Hi {name} — haven't heard back, so one more nudge from me. "
        "If you have questions, ask away; if not, we can start whenever you say.",
    ]),
    ("tg-payout", "Payout on the way", [
        "{name}, good news — a payout from your account is on the way. "
        "Once it lands on your side, message me and I'll send the settlement details for the split.",
        "Hey {name}, payout's been approved. The prop firm pays you the full amount first; "
        "when it arrives, let me know and we settle our share from it — never before.",
    ]),
    ("tg-breach", "Account hit a rule", [
        "{name}, straight answer: the account hit a rule and is closed for trading. "
        "Not on you — it happens. If you want to go again, tell me and I'll set the next one up.",
        "Hey {name} — the account breached and got closed. I'd rather tell you now than "
        "let you find out on the platform. Say the word if you want another go.",
    ]),
]


def lista_tg() -> list[dict]:
    """Szablony Telegrama w kształcie szablonu maila: `body` = pierwszy
    wariant, `variants` = wszystkie; `sender="tg"` trzyma je poza selektorem
    maila (ten filtruje po ptf/fx)."""
    return [{"id": f"b:{klucz}", "name": nazwa, "sender": TG,
             "subject": "", "body": warianty[0], "variants": list(warianty),
             "builtin": True, "updated_at": None}
            for klucz, nazwa, warianty in WBUDOWANE_TG]


def _wartosci() -> dict[str, str]:
    """Placeholdery podstawiane przez serwer — z ustawień, nie z kodu."""
    baza = (settings.app_base_url or "").rstrip("/")
    return {
        "{portal_url}": f"{baza}/portal" if baza else "",
        "{telegram_url}": settings.telegram_url_desku(False),
        # Desk darmowego lejka — inne konto niż płatny; szablon o darmowym
        # koncie ma prowadzić tam, gdzie ten człowiek i tak już pisał.
        "{free_telegram_url}": settings.telegram_url_desku(True),
        "{support_email}": settings.support_email or "",
    }


def _podstaw(tekst: str, wartosci: dict[str, str]) -> str:
    """Podstawia wartości; akapit, który był SAMYM pustym placeholderem, wypada
    (guzik bez adresu jest gorszy niż brak guzika)."""
    akapity = []
    for akapit in tekst.split("\n\n"):
        if akapit.strip() in wartosci and not wartosci[akapit.strip()]:
            continue
        for klucz, wartosc in wartosci.items():
            akapit = akapit.replace(klucz, wartosc)
        akapity.append(akapit)
    return "\n\n".join(akapity)


def lista() -> list[dict]:
    """Wbudowane szablony w tym samym kształcie, co zapisane — plus `builtin`."""
    wartosci = _wartosci()
    return [{"id": f"b:{klucz}", "name": nazwa, "sender": nadawca,
             "subject": _podstaw(temat, wartosci), "body": _podstaw(tresc, wartosci),
             "builtin": True, "updated_at": None}
            for klucz, nadawca, nazwa, temat, tresc in WBUDOWANE] + lista_tg()
