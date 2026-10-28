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
        odpowiedz = _ZDANIE_O_PIENIADZACH.sub("", odpowiedz).strip()
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
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "sdk"
    if os.getenv("HELPDESK_LOCAL_CLI") == "1" and not os.getenv("VERCEL") and shutil.which("claude"):
        return "cli"
    return None


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


def odpowiedz(thread: list[dict], system: str) -> dict:
    """Następna tura asystenta: {reply, escalate, category, subject, summary}.

    `thread` to cała rozmowa (ostatnia wiadomość = tradera). Każdy błąd albo
    brak dostawcy kończy się przekazaniem do człowieka."""
    ostatnia = next((m["body"] for m in reversed(thread) if m["author"] == "trader"), "")
    pierwsza = next((m["body"] for m in thread if m["author"] == "trader"), ostatnia)
    awaryjnie = {"reply": BEZ_ASYSTENTA, "escalate": True, "category": "other",
                 "subject": pierwsza[:80], "summary": ""}
    kto = dostawca()
    if kto is None:
        return awaryjnie
    try:
        msgs = _historia_dla_modelu(thread)
        wynik = (_przez_sdk if kto == "sdk" else _przez_cli)(system, msgs)
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
