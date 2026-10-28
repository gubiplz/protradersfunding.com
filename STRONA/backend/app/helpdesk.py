"""Czat pomocy w portalu: asystent AI na FAQ, a gdy nie pomoże — człowiek.

Rozmowa to zwykły `SupportTicket` ze statusem `bot`: admin go nie widzi, dopóki
asystent nie przekaże sprawy (status `open`, powiadomienie jak przy nowym
bilecie). Od tej chwili odpowiada człowiek, a asystent milknie w tej rozmowie.

Granica jest w KODZIE, nie tylko w prompcie:
- asystent nie widzi danych konta (wyników, sald, statusu) ani wypłat — zna
  tylko FAQ bez pytań o pieniądze i zasady planów z katalogu;
- wiadomość o pieniądzach, wyniku konta, reklamacji albo z prośbą o człowieka
  idzie do admina od razu, cokolwiek odpowie model (`_TYLKO_CZLOWIEK`);
- błąd, brak klucza albo niepoprawna odpowiedź modelu = przekazanie do admina,
  nigdy cisza.

Model: oficjalne SDK `anthropic` z kluczem (`ANTHROPIC_API_KEY`). Lokalnie bez
klucza można włączyć `HELPDESK_LOCAL_CLI=1` — wtedy idzie przez `claude -p`
(tylko podgląd na własnej maszynie, nigdy na Vercelu).
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("helpdesk")

MODEL = os.getenv("HELPDESK_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("HELPDESK_EFFORT", "low")   # czat: niski wysiłek wystarcza, szybciej
TIMEOUT_S = float(os.getenv("HELPDESK_TIMEOUT_S", "60"))

KATEGORIE = ["how_to", "platform", "rules", "kyc", "account_result", "payout",
             "billing", "complaint", "other"]
# Te kategorie zawsze idą do człowieka: asystent nie zna danych konta ani wypłat.
KATEGORIE_CZLOWIEKA = {"account_result", "payout", "billing", "complaint"}

# Twarda bramka na treść wiadomości klienta — niezależna od modelu.
_TYLKO_CZLOWIEK = re.compile(
    r"\b(payout|withdraw\w*|refund\w*|chargeback|money back|reimburs\w*|"
    r"scam\w*|fraud\w*|lawyer|legal action|police|dispute|"
    r"breach\w*|fail\w*|lost|closed|terminat\w*|blocked|blown|disabled|banned|suspend\w*|"
    r"rigged|manipulat\w*|human|real person|agent|manager|admin)\b",
    re.I)

SCHEMAT = {
    "type": "object",
    "properties": {
        "reply": {"type": "string",
                  "description": "Message shown to the trader, plain text, English."},
        "resolution": {"type": "string", "enum": ["answered", "needs_human"]},
        "category": {"type": "string", "enum": KATEGORIE},
        "subject": {"type": "string",
                    "description": "Short ticket subject for the support team (max 80 chars)."},
        "summary": {"type": "string",
                    "description": "2-4 sentences for the support team: the problem, what the trader "
                                   "already tried, what they need. Empty when answered."},
    },
    "required": ["reply", "resolution", "category", "subject", "summary"],
    "additionalProperties": False,
}

# Pytania z FAQ, których asystent NIE dostaje: dotyczą pieniędzy (wypłaty,
# zwrot opłaty, skalowanie po zysku). Takie sprawy rozstrzyga człowiek.
_FAQ_POMIN = re.compile(r"payout|fee back|refund|scaling", re.I)
# Zdania o wypłatach/zwrotach wycinane z pozostałych odpowiedzi.
_ZDANIE_O_PIENIADZACH = re.compile(r"[^.]*\b(payout|refund\w*|reward\w*)\b[^.]*\.\s*", re.I)


def _tekst(fragment: str, kontekst: dict) -> str:
    fragment = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(kontekst.get(m.group(1), "")), fragment)
    fragment = re.sub(r"\{%.*?%\}", "", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def faq_wpisy(sciezka: Path, kontekst: dict) -> list[tuple[str, str]]:
    """Pary (pytanie, odpowiedź) z faq.html — ta sama treść, którą widzą klienci,
    bez pytań o pieniądze i bez zdań o wypłatach w pozostałych odpowiedziach."""
    src = sciezka.read_text(encoding="utf-8")
    wpisy = []
    for q, a in re.findall(r"<summary>(.*?)</summary>\s*<div class=\"a\">(.*?)</div>", src, re.S):
        pytanie, odpowiedz = _tekst(q, kontekst), _tekst(a, kontekst)
        if _FAQ_POMIN.search(pytanie):
            continue
        odpowiedz = re.sub(r"\s+", " ", _ZDANIE_O_PIENIADZACH.sub(" ", odpowiedz)).strip()
        if odpowiedz:
            wpisy.append((pytanie, odpowiedz))
    return wpisy


def zasady_planow(produkty) -> str:
    """Aktywne plany z katalogu (wiersze `Product`). Podział zysku celowo pominięty:
    to obietnica wypłaty, a o wypłatach rozmawia człowiek."""
    wiersze = []
    for p in sorted(produkty, key=lambda p: (p.steps, p.account_size)):
        if not p.active:
            continue
        cel = (f"{p.profit_target_p1:g}% phase 1 / {p.profit_target_p2:g}% phase 2" if p.steps >= 2
               else f"{p.profit_target_p1:g}%")
        wiersze.append(
            f"- {p.label}: account size ${p.account_size:,.0f}, one-time fee ${p.price_usd:,.0f}, "
            f"profit target {cel}, max daily loss {p.max_daily_loss_pct:g}%, max overall loss "
            f"{p.max_overall_loss_pct:g}% ({p.drawdown_type}), min trading days {p.min_trading_days}, "
            f"max open volume {p.max_lots:g} lots")
    return "\n".join(wiersze)


@lru_cache(maxsize=4)
def prompt_systemowy(site_name: str, support_email: str, faq_path: str, plany: str) -> str:
    """Stały prompt (cache'owany po stronie API): rola, granice, FAQ, zasady planów.
    Zero danych zmiennych (daty, imiona) — każda zmiana bajtu psuje cache."""
    faq = "\n\n".join(f"Q: {q}\nA: {a}" for q, a in
                      faq_wpisy(Path(faq_path), {"site_name": site_name, "support_email": support_email}))
    return f"""You are the AI support assistant in the trader portal of {site_name}, a prop trading challenge \
platform. You chat with logged-in traders. You are an AI, not a person: say so if asked, and never pretend \
to be a member of the team.

Your job: answer questions you can answer fully from the knowledge below (getting started, the MT5 \
platform, trading rules, KYC steps, portal features). Keep replies short: 1-5 sentences, plain text, no \
markdown headings. If one clarifying question would let you answer, ask it.

Hard limits — hand the conversation to the support team (resolution "needs_human") instead of answering:
- anything about a specific account's results, status, balance, breach or why it failed or closed. You \
cannot see account data and must not guess or explain an outcome;
- payouts, withdrawals, refunds, the challenge fee coming back, charges, invoices or any money question;
- complaints, accusations, disputes, legal threats, or a request to talk to a person;
- anything the knowledge below does not answer, or when the trader says your answer did not help.
When you hand over, tell the trader in one or two sentences that their message went to the support team, \
who will reply in this chat, and do not promise a time or an outcome. Write a short subject and a summary \
for the team.

Never invent rules, numbers, prices, dates or policies. Never promise payouts, refunds, approvals or \
timelines. If the knowledge is silent, hand over. The support email is {support_email}.

Every account on the platform is a demo account trading simulated capital; never describe accounts as live \
or as holding real money.

<knowledge source="FAQ">
{faq}
</knowledge>

<knowledge source="Plans">
{plany}
</knowledge>"""


def _historia_dla_modelu(thread: list[dict]) -> list[dict]:
    """Wiadomości tradera i asystenta jako tura user/assistant (bez notatek i admina)."""
    msgs = []
    for m in thread:
        rola = {"trader": "user", "ai": "assistant"}.get(m["author"])
        if rola:
            msgs.append({"role": rola, "content": m["body"]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def _przez_sdk(system: str, msgs: list[dict]) -> dict:
    import anthropic   # leniwie: bez klucza moduł nie jest potrzebny

    client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=1)
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMAT}},
        messages=msgs,
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"model stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def _przez_cli(system: str, msgs: list[dict]) -> dict:
    """Podgląd lokalny bez klucza: `claude -p` bez narzędzi, w katalogu tymczasowym
    (inaczej czyta CLAUDE.md projektu i zachowuje się jak asystent kodowania)."""
    zapis = "\n\n".join(("Trader: " if m["role"] == "user" else "Assistant: ") + m["content"] for m in msgs)
    prompt = ("Conversation so far (the last message is the trader's newest one):\n\n" + zapis +
              "\n\nWrite the assistant's next turn.")
    out = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--system-prompt", system, "--tools", "",
         "--no-session-persistence", "--output-format", "json",
         "--json-schema", json.dumps(SCHEMAT), prompt],
        cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=TIMEOUT_S + 60)
    if out.returncode != 0:
        raise RuntimeError(f"claude -p exited {out.returncode}: {out.stderr[-300:]}")
    koperta = json.loads(out.stdout)
    wynik = koperta.get("structured_output")
    if wynik is None:
        wynik = json.loads(koperta.get("result") or "")
    return wynik


def dostawca() -> str | None:
    """Kto odpowiada: model przez API (klucz), lokalne CLI (tylko podgląd) albo —
    domyślnie, bez klucza i bez kosztów — silnik FAQ. `HELPDESK_ENGINE=off`
    wyłącza asystenta (wszystko od razu do zespołu)."""
    if os.getenv("HELPDESK_ENGINE") == "off":
        return None
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "sdk"
    if os.getenv("HELPDESK_LOCAL_CLI") == "1" and not os.getenv("VERCEL") and shutil.which("claude"):
        return "cli"
    return "faq"


# --------------------------------------------------------------------------- #
#  Silnik FAQ — asystent bez API: dopasowuje pytanie do bazy wiedzy PTF        #
# --------------------------------------------------------------------------- #
# Przewodnik po portalu: tylko to, co faktycznie jest w portalu (nazwy z menu
# i przycisków), bez obietnic wypłat.
PORTAL = [
    ("Where do I find my MT5 login, password and server?",
     "Open Challenges, click Dashboard on your account and then Credentials. Your MT5 login, password "
     "and server are listed there. You also received them by email when the account was created."),
    ("How do I complete KYC identity verification?",
     "Open KYC in the menu, fill in your details and upload a photo ID (passport, national ID or driver's "
     "license) and a proof of residence. You only need to do it once."),
    ("How do I change or reset my password?",
     "Open Settings in the menu and use Change Password. If you are logged out, use \"Forgot password?\" "
     "on the login screen and follow the email."),
    ("How do I change my notification settings or email preferences?",
     "Open Settings in the menu and go to Notification Preferences."),
    ("Where are my invoices and payment history?",
     "Open Invoice in the menu. It lists your payment history, delivered accounts and invoices."),
    ("How do I buy or start a new challenge in the portal?",
     "Click + New challenge at the top right of the portal, pick a plan and size, and complete checkout. "
     "Your MT5 credentials are created automatically."),
    ("What is the leaderboard?",
     "Leaderboard shows the top traders across the platform, ranked by profit and by discipline."),
    ("What is the trading journal?",
     "Journal in the menu is your private space for trading notes. Only you can see it."),
    ("Where can I see my daily P&L and statistics?",
     "Open Analytics in the menu. It shows your daily P&L computed from your account history."),
    ("What are loyalty points and how do I use them?",
     "Open Loyalty in the menu. You can trade your points for a discount code."),
    ("Where can I see my certificates?",
     "Open Certificates in the menu. It lists your verifiable documents for the evaluation stages you passed."),
]

_STOP = set("""a an the i me my we our you your it its is are was were be been am do does did doing to of in
on at for with from by about as into over under and or but if then than so that this these those what which
who whom how when where why can could would should will shall may might must there here have has had get got
please pls hi hey hello thanks thank tell know want need just like any some all also not no yes ok okay""".split())
# Sprowadzenie odmian i synonimów do jednego tokenu.
_SYNONIMY = {
    "login": "log", "logon": "log", "signin": "log", "sign": "log", "credential": "log", "credentials": "log",
    "password": "password", "pass": "password", "pwd": "password",
    "mt5": "metatrader", "metatrader5": "metatrader", "metatrader": "metatrader", "mt": "metatrader",
    "terminal": "metatrader", "platform": "metatrader",
    "dd": "drawdown", "drawdown": "drawdown",
    "ea": "ea", "eas": "ea", "expert": "ea", "advisor": "ea", "advisors": "ea", "bot": "ea", "bots": "ea",
    "robot": "ea", "algo": "ea",
    "kyc": "kyc", "verification": "kyc", "verify": "kyc", "identity": "kyc", "id": "kyc", "passport": "kyc",
    "weekend": "weekend", "weekends": "weekend", "overnight": "weekend", "hold": "weekend", "holding": "weekend",
    "invoice": "invoice", "invoices": "invoice", "receipt": "invoice", "billing": "invoice",
    "fee": "price", "price": "price", "cost": "price", "costs": "price", "much": "price",
    "rule": "rule", "rules": "rule", "objective": "rule", "objectives": "rule", "limit": "limit", "limits": "limit",
    "lose": "loss", "losing": "loss", "loss": "loss", "losses": "loss",
    "day": "day", "days": "day", "daily": "daily",
    "challenge": "challenge", "challenges": "challenge", "evaluation": "challenge", "account": "account",
    "accounts": "account", "trial": "trial", "free": "trial", "coupon": "coupon", "promo": "coupon",
    "code": "coupon", "discount": "coupon", "scale": "scale",
    "news": "news", "phone": "mobile", "mobile": "mobile", "android": "mobile", "iphone": "mobile", "ios": "mobile",
    "app": "app", "desktop": "metatrader", "laptop": "metatrader", "pc": "metatrader",
}


def _tokeny(tekst: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", (tekst or "").lower().replace("'", "")):
        if w in _STOP or len(w) < 2 and not w.isdigit():
            continue
        if w in _SYNONIMY:
            out.append(_SYNONIMY[w]); continue
        for kon in ("ing", "ed", "es", "s"):   # bardzo prosty stemming
            if len(w) > 4 and w.endswith(kon):
                w = w[: -len(kon)]
                break
        if w in _STOP:   # „whats" → „what"
            continue
        out.append(_SYNONIMY.get(w, w))
    return out


class BazaWiedzy:
    """BM25 po wpisach (pytanie liczone podwójnie). Mała baza, liczona raz."""

    def __init__(self, wpisy: list[tuple[str, str]]):
        self.wpisy = wpisy
        self.dok = [_tokeny(q) * 2 + _tokeny(a) for q, a in wpisy]
        n = len(self.dok)
        self.srednia = sum(map(len, self.dok)) / max(n, 1)
        df: dict[str, int] = {}
        for d in self.dok:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        import math
        self.idf = {t: math.log(1 + (n - c + .5) / (c + .5)) for t, c in df.items()}

    def szukaj(self, pytanie: str, k: int = 3) -> list[tuple[float, int, float]]:
        """[(wynik, indeks wpisu, pokrycie)] — pokrycie to część słów pytania,
        które wpis zawiera; chroni przed trafieniem po jednym ogólnym słowie."""
        q = set(_tokeny(pytanie))
        wyniki = []
        for i, d in enumerate(self.dok):
            s, dl, trafione = 0.0, len(d), 0
            for t in q:
                f = d.count(t)
                if f:
                    trafione += 1
                    s += self.idf.get(t, 0) * f * 2.2 / (f + 1.2 * (.25 + .75 * dl / self.srednia))
            if s > 0:
                wyniki.append((s, i, trafione / len(q)))
        return sorted(wyniki, reverse=True)[:k]


@lru_cache(maxsize=4)
def baza(site_name: str, support_email: str, faq_path: str) -> BazaWiedzy:
    return BazaWiedzy(faq_wpisy(Path(faq_path), {"site_name": site_name, "support_email": support_email}) + PORTAL)


_ROZMIAR = re.compile(r"\$?\s?(\d{1,3}(?:[.,]\d{3})+|\d+(?:\.\d+)?\s?[km])\b", re.I)
_CECHY = [  # (słowa w pytaniu, nazwa, funkcja z wiersza Product)
    (("daily",), "max daily loss", lambda p: f"{p.max_daily_loss_pct:g}%"),
    (("overall", "max drawdown", "maximum drawdown", "total"), "max overall loss",
     lambda p: f"{p.max_overall_loss_pct:g}% ({p.drawdown_type})"),
    (("target", "profit"), "profit target",
     lambda p: f"{p.profit_target_p1:g}% in phase 1 and {p.profit_target_p2:g}% in phase 2" if p.steps >= 2
     else f"{p.profit_target_p1:g}%" if p.profit_target_p1 else "none (no evaluation phase)"),
    (("fee", "price", "cost", "how much"), "one-time fee", lambda p: f"${p.price_usd:,.0f}"),
    (("trading day", "minimum day", "min day", "how many day"), "minimum trading days",
     lambda p: str(p.min_trading_days)),
    (("lot", "volume", "position size"), "max open volume", lambda p: f"{p.max_lots:g} lots"),
]


def _kwota(s: str) -> float | None:
    s = s.lower().replace("$", "").replace(" ", "")
    mnoznik = 1000 if s.endswith("k") else 1_000_000 if s.endswith("m") else 1
    s = s.rstrip("km").replace(",", "")
    try:
        v = float(s) * mnoznik
    except ValueError:
        return None
    return v if v >= 1000 else None


def _odpowiedz_o_planie(pytanie: str, produkty) -> str | None:
    """„Max daily loss on the 25k 2-step?" — liczby wprost z katalogu planów."""
    m = _ROZMIAR.search(pytanie)
    rozmiar = _kwota(m.group(1)) if m else None
    if not rozmiar:
        return None
    low = pytanie.lower()
    pasujace = [p for p in produkty if p.active and abs(p.account_size - rozmiar) < 1]
    if not pasujace:
        return None
    if "instant" in low:
        pasujace = [p for p in pasujace if p.steps <= 1] or pasujace
    elif "2" in low and "step" in low or "two" in low:
        pasujace = [p for p in pasujace if p.steps >= 2] or pasujace
    cechy = [(nazwa, f) for slowa, nazwa, f in _CECHY if any(s in low for s in slowa)]
    zdania = []
    for p in pasujace[:2]:
        if cechy:
            zdania.append(f"{p.label}: " + ", ".join(f"{n} {f(p)}" for n, f in cechy) + ".")
        else:
            zdania.append(f"{p.label}: one-time fee ${p.price_usd:,.0f}, profit target "
                          f"{_CECHY[2][2](p)}, max daily loss {p.max_daily_loss_pct:g}%, max overall loss "
                          f"{p.max_overall_loss_pct:g}% ({p.drawdown_type}), minimum {p.min_trading_days} "
                          f"trading days, max open volume {p.max_lots:g} lots.")
    return " ".join(zdania)


NIE_WIEM = "I couldn't find that in our help articles."
_POWITANIE = re.compile(r"^\s*(hi|hey|hello|yo|good (morning|afternoon|evening)|hiya|sup)\b[\s!.,?]*$", re.I)
_DZIEKI = re.compile(r"^\s*(thanks?|thank you|thx|ty|cheers|great|perfect|ok(ay)?|got it|cool)\b[\s!.,]*$", re.I)


def odpowiedz_z_faq(thread: list[dict], baza_wiedzy: BazaWiedzy, produkty) -> dict:
    """Asystent bez modelu. Zwraca ten sam kształt co model:
    {reply, resolution, category, subject, summary}."""
    pytanie = next((m["body"] for m in reversed(thread) if m["author"] == "trader"), "")
    wynik = lambda reply, res="answered", cat="how_to", subj="", summ="": {  # noqa: E731
        "reply": reply, "resolution": res, "category": cat, "subject": subj or pytanie[:80], "summary": summ}
    if _POWITANIE.match(pytanie):
        return wynik("Hi! Ask me anything about getting started, MT5, the trading rules or the portal — "
                     "for example \"How do I log in to MT5?\" or \"What is the max daily loss?\"", subj="Hello")
    if _DZIEKI.match(pytanie):
        return wynik("Happy to help! If anything else comes up, just ask here.")

    plan = _odpowiedz_o_planie(pytanie, produkty)
    if plan:
        return wynik(plan, cat="rules", subj=pytanie[:80])

    trafienia = baza_wiedzy.szukaj(pytanie)
    if trafienia and trafienia[0][0] >= 2.2 and trafienia[0][2] >= .5:
        q, a = baza_wiedzy.wpisy[trafienia[0][1]]
        return wynik(a, cat="how_to", subj=q[:80])

    # Bez trafienia: raz prosimy o doprecyzowanie z podpowiedziami, za drugim razem człowiek.
    poprzednia_ai = next((m["body"] for m in reversed(thread) if m["author"] == "ai"), "")
    if poprzednia_ai.startswith(NIE_WIEM):
        return wynik("I still can't find an answer to that, so I'm handing it to our support team. "
                     "They'll reply here in this chat and by email.", res="needs_human", cat="other",
                     summ=f"No help-article match for: {pytanie[:300]}")
    podobne = [baza_wiedzy.wpisy[i][0] for s, i, _ in trafienia if s >= 0.8][:3]
    tekst = NIE_WIEM + " Could you rephrase it?"
    if podobne:
        tekst += " Related topics: " + "; ".join(f'"{q}"' for q in podobne) + "."
    return wynik(tekst, cat="other")


def _poprawny(wynik) -> bool:
    return (isinstance(wynik, dict) and isinstance(wynik.get("reply"), str) and wynik["reply"].strip()
            and wynik.get("resolution") in ("answered", "needs_human")
            and wynik.get("category") in KATEGORIE)


def do_czlowieka(wiadomosc: str) -> bool:
    return bool(_TYLKO_CZLOWIEK.search(wiadomosc or ""))


PRZEKAZANIE = ("I've passed this to our support team. They'll reply here in this chat, "
               "and you'll also get an email when they do.")
BEZ_ASYSTENTA = ("Thanks — your message is with our support team. They'll reply here in this chat, "
                 "and you'll also get an email when they do.")


def odpowiedz(thread: list[dict], system, wiedza) -> dict:
    """Następna tura asystenta: {reply, escalate, category, subject, summary}.

    `thread` to cała rozmowa (ostatnia wiadomość = tradera). `system()` daje prompt
    dla modelu, `wiedza()` — (BazaWiedzy, plany) dla silnika FAQ; liczone dopiero,
    gdy są potrzebne. Każdy błąd albo brak dostawcy kończy się przekazaniem do człowieka."""
    ostatnia = next((m["body"] for m in reversed(thread) if m["author"] == "trader"), "")
    pierwsza = next((m["body"] for m in thread if m["author"] == "trader"), ostatnia)
    awaryjnie = {"reply": BEZ_ASYSTENTA, "escalate": True, "category": "other",
                 "subject": pierwsza[:80], "summary": ""}
    kto = dostawca()
    if kto is None:
        return awaryjnie
    try:
        if kto == "faq":
            wynik = odpowiedz_z_faq(thread, *wiedza())
        else:
            wynik = (_przez_sdk if kto == "sdk" else _przez_cli)(system(), _historia_dla_modelu(thread))
    except Exception as e:   # model niedostępny / limit / timeout — klient nie może zostać bez odpowiedzi
        log.warning("helpdesk: model call failed (%s): %s", kto, e)
        return awaryjnie
    if not _poprawny(wynik):
        log.warning("helpdesk: invalid model output: %r", wynik)
        return awaryjnie
    eskaluj = (wynik["resolution"] == "needs_human" or wynik["category"] in KATEGORIE_CZLOWIEKA
               or do_czlowieka(ostatnia))
    reply = wynik["reply"].strip()
    if eskaluj and wynik["resolution"] != "needs_human":
        # Model odpowiedział na temat, którego nie wolno mu rozstrzygać — klient
        # dostaje zwykłe przekazanie zamiast tej odpowiedzi.
        reply = PRZEKAZANIE
    return {"reply": reply[:4000], "escalate": eskaluj, "category": wynik["category"],
            "subject": (wynik.get("subject") or pierwsza)[:80].strip() or "Chat",
            "summary": (wynik.get("summary") or "").strip()[:2000]}
