"""Update o kontach klienta z PRAWDZIWYCH liczb — pod wiadomość na Telegram.

Szablon „Weekly update" w mailu miał nawiasy do ręcznego uzupełnienia i wyszedł
raz z pustymi nawiasami. Tu liczby bierze serwer z tego, co panel i tak wie:
metryki reguł (`rules.display_metrics`), licznik dni handlowych, zamknięte
transakcje z księgi, wypłaty. Tekst składany w KILKU ujęciach (otwarcie ×
zakończenie × styl podsumowania), żeby „Another wording" miało z czego wybierać
i żeby dziesięć update'ów nie brzmiało jak jeden szablon.

Zasady treści jak w `lead_mail.tresc()`: liczby, nie przymiotniki; jedno
zdanie o tym, co dalej; zero obietnic. Konto, które padło, dostaje zdanie
wprost — nie da się napisać „update", który tego nie zauważa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func

from . import rules
from .models import Account, Payout, Trade

# Konta „domowe" (boty firmy) i pula bez tradera nie są niczyim kontem.
_STATUSY_ZYWE = ("active", "funded", "failed", "passed", "provisioning")


@dataclass
class KontoInfo:
    login: str
    plan: str
    phase: str
    status: str
    initial_balance: float
    balance: float
    equity: float
    profit_pct: float
    target_pct: float
    days: int
    min_days: int
    dd_used_pct: float
    daily_used_pct: float
    trades: int = 0
    wins: int = 0
    net_pnl: float = 0.0
    best_symbol: str | None = None
    streak: int = 0
    payouts_usd: float = 0.0
    split_pct: float = 0.0
    payout_days_left: int = 0
    breach_reason: str | None = None

    def json(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class Insights:
    name: str
    accounts: list[KontoInfo] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)        # DM na Telegram
    mail_variants: list[str] = field(default_factory=list)   # mail „Weekly update"

    def json(self) -> dict:
        return {"name": self.name, "accounts": [a.json() for a in self.accounts],
                "variants": list(self.variants), "mail_variants": list(self.mail_variants)}


def _konto(session, acc: Account) -> KontoInfo:
    cfg = rules.config_from_account(acc)
    m = rules.display_metrics(cfg, balance=acc.balance, equity=acc.equity,
                              peak_equity=acc.peak_equity, day_start_equity=acc.day_start_equity,
                              trading_days=acc.trading_days_count)
    start = float(acc.initial_balance or 0) or 1.0
    trades = (session.query(Trade)
              .filter(Trade.account_id == acc.id, Trade.status == "closed").all())
    wins = [t for t in trades if (t.pnl or 0) > 0]
    # Seria z końca historii: +n = n zysków z rzędu, -n = n strat.
    streak = 0
    for t in reversed(sorted(trades, key=lambda x: (x.closed_at or x.opened_at or datetime.min))):
        pnl = t.pnl or 0
        if pnl > 0 and streak >= 0:
            streak += 1
        elif pnl < 0 and streak <= 0:
            streak -= 1
        else:
            break
    najlepszy = None
    if trades:
        po_symbolu: dict[str, float] = {}
        for t in trades:
            po_symbolu[t.symbol] = po_symbolu.get(t.symbol, 0.0) + float(t.pnl or 0)
        najlepszy = max(po_symbolu, key=po_symbolu.get)
    wyplaty = (session.query(func.coalesce(func.sum(Payout.trader_share), 0.0))
               .filter(Payout.account_id == acc.id, Payout.paid.is_(True)).scalar() or 0.0)
    from . import poller  # lokalnie: poller importuje modele, nie ten moduł
    return KontoInfo(
        login=str(acc.login), plan=acc.product_key, phase=acc.phase, status=acc.status,
        initial_balance=float(acc.initial_balance or 0),
        balance=float(acc.balance or 0), equity=float(acc.equity or 0),
        profit_pct=float(m.get("profit_pct", (float(acc.balance or 0) - start) / start * 100)),
        target_pct=float(cfg.profit_target_pct or 0),
        days=int(acc.trading_days_count or 0), min_days=int(cfg.min_trading_days or 0),
        dd_used_pct=float(m.get("overall_dd_used_pct", 0) or 0),
        daily_used_pct=float(m.get("daily_loss_used_pct", 0) or 0),
        trades=len(trades), wins=len(wins), net_pnl=float(sum((t.pnl or 0) for t in trades)),
        best_symbol=najlepszy, streak=streak, payouts_usd=float(wyplaty),
        split_pct=float(acc.profit_split_pct or 0),
        payout_days_left=int(poller.payout_days_left(acc)),
        breach_reason=acc.breach_reason,
    )


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def _faza(k: KontoInfo) -> str:
    if k.status == "failed":
        return "closed"
    if k.phase == "funded" or k.status == "funded":
        return "funded"
    if k.phase == "eval_1":
        return "phase 1"
    if k.phase == "eval_2":
        return "phase 2"
    return k.phase or "evaluation"


def _gdzie_stoi(k: KontoInfo, styl: int) -> str:
    """„Where it stands" — stan konta w jednym z dwóch stylów (liczby te same)."""
    znak = "+" if k.profit_pct >= 0 else ""
    faza = _faza(k)
    if k.status == "failed":
        powod = f" ({k.breach_reason})" if k.breach_reason else ""
        return (f"account {k.login} hit a rule and is closed{powod}; "
                f"balance ended at {_usd(k.balance)} from {_usd(k.initial_balance)}.")
    czesci = []
    if styl == 0:
        czesci.append(f"account {k.login} ({faza}) at {_usd(k.balance)}, "
                      f"{znak}{k.profit_pct:.1f}% from the {_usd(k.initial_balance)} start.")
    else:
        czesci.append(f"{k.login} is in {faza}, balance {_usd(k.balance)} "
                      f"({znak}{k.profit_pct:.1f}% on {_usd(k.initial_balance)}).")
    if faza != "funded" and k.target_pct > 0:
        do_celu = max(0.0, k.target_pct - k.profit_pct)
        czesci.append("Profit target is done." if do_celu <= 0
                      else f"{do_celu:.1f}% left to the {k.target_pct:.0f}% target.")
    if k.min_days:
        czesci.append(f"{k.days} trading days done (minimum {k.min_days} covered)."
                      if k.days >= k.min_days
                      else f"{k.days} of {k.min_days} minimum trading days done.")
    if k.dd_used_pct > 0:
        czesci.append(f"Drawdown used: {k.dd_used_pct:.0f}% of the limit"
                      + (", plenty of room." if k.dd_used_pct < 40
                         else ", so size stays small." if k.dd_used_pct >= 70 else "."))
    if faza == "funded":
        if k.payouts_usd > 0:
            czesci.append(f"Paid out so far: {_usd(k.payouts_usd)} (your share {k.split_pct:.0f}%).")
        if k.payout_days_left > 0:
            czesci.append(f"{k.payout_days_left} trading days to the first payout window.")
    return " ".join(czesci)


def _co_zrobilismy(k: KontoInfo) -> str:
    """„What we did" — z zamkniętych transakcji; bez nich mówi to wprost."""
    if k.status == "failed":
        return "the last positions went against us and the limit closed the account before it could turn."
    if not k.trades:
        return "positions are being placed; nothing closed yet, so no result to report."
    wr = 100.0 * k.wins / k.trades
    zd = [f"{k.trades} closed trades, {wr:.0f}% winners, net "
          f"{'+' if k.net_pnl >= 0 else ''}{_usd(k.net_pnl)}"
          + (f", best on {k.best_symbol}." if k.best_symbol else ".")]
    if k.streak >= 3:
        zd.append(f"Last {k.streak} in a row were winners.")
    elif k.streak <= -3:
        zd.append(f"Last {-k.streak} went against us, so size is down until it turns.")
    if k.daily_used_pct >= 50:
        zd.append(f"Today used {k.daily_used_pct:.0f}% of the daily limit, so we stopped for the day.")
    return " ".join(zd)


_OTWARCIA = [
    "Hey {name}, quick update on your account.",
    "{name}, here's where your account stands right now.",
    "Hi {name} — a short update from the desk.",
    "{name}, update time. Straight numbers, no fluff:",
]
_ZAKONCZENIA = [
    "Any questions, just ask.",
    "I'll send the next one when something moves.",
    "Shout if you want the full trade list.",
    "That's it for now — more when there's more.",
]


def _dalej(konta: list[KontoInfo]) -> str:
    """Jedno zdanie „co dalej" z najbliższego kamienia milowego."""
    zywe = [k for k in konta if k.status != "failed"]
    if not zywe:
        return "If you want to go again, say the word and I'll set the next account up."
    k = zywe[0]
    if _faza(k) != "funded" and k.target_pct > 0 and k.profit_pct < k.target_pct:
        return "Next milestone: the profit target, then the account moves on."
    if k.min_days and k.days < k.min_days:
        return "Next milestone: the minimum trading days, then we can move on."
    if _faza(k) == "funded" and k.payout_days_left > 0:
        return "Next milestone: the first payout window."
    return "Next: keep it steady and let the numbers do the work."


def zloz(name: str | None, konta: list[KontoInfo]) -> list[str]:
    """Wszystkie ujęcia update'u w układzie „Weekly update" z maila:
    Where it stands / What we did / What's next — otwarcie × styl × zakończenie."""
    imie = (name or "").strip().split(" ")[0] or "there"
    if not konta:
        return []
    wersje = []
    for styl in (0, 1):
        if len(konta) == 1:
            stoi = _gdzie_stoi(konta[0], styl)
            zrobilismy = _co_zrobilismy(konta[0])
        else:
            stoi = "\n".join(f"• {_gdzie_stoi(k, styl)}" for k in konta)
            zrobilismy = "\n".join(f"• {k.login}: {_co_zrobilismy(k)}" for k in konta)
        srodek = (f"Where it stands: {stoi}\n\n"
                  f"What we did: {zrobilismy}\n\n"
                  f"What's next: {_dalej(konta)}")
        for otw in _OTWARCIA:
            for zak in _ZAKONCZENIA:
                wersje.append(f"{otw.replace('{name}', imie)}\n\n{srodek}\n\n{zak}")
    return wersje


_OTWARCIA_MAIL = [
    "Hi {name},\n\nQuick update on the account we manage for you.",
    "Hi {name},\n\nHere is where your account stands this week.",
    "Hi {name},\n\nShort update from the desk — numbers first, then what comes next.",
]


def zloz_mail(name: str | None, konta: list[KontoInfo], telegram_url: str) -> list[str]:
    """Wersja MAILOWA „Weekly update": ten sam środek co w DM-ie, w układzie
    z szablonu (akapity, link do desku jako guzik, stopka po `--`)."""
    imie = (name or "").strip().split(" ")[0] or "there"
    if not konta:
        return []
    ogon = ("\n\nAny questions, the desk is on Telegram:\n\n" + telegram_url
            if telegram_url else "") + "\n\n--\nForex Passing"
    wersje = []
    for styl in (0, 1):
        if len(konta) == 1:
            stoi, zrobilismy = _gdzie_stoi(konta[0], styl), _co_zrobilismy(konta[0])
        else:
            stoi = "\n".join(f"• {_gdzie_stoi(k, styl)}" for k in konta)
            zrobilismy = "\n".join(f"• {k.login}: {_co_zrobilismy(k)}" for k in konta)
        srodek = (f"Where it stands: {stoi}\n\n"
                  f"What we did: {zrobilismy}\n\n"
                  f"What is next: {_dalej(konta)}")
        for otw in _OTWARCIA_MAIL:
            wersje.append(f"{otw.replace('{name}', imie)}\n\n{srodek}{ogon}")
    return wersje


def dla_tradera(session, trader_id: int, name: str | None, telegram_url: str = "") -> Insights:
    konta = (session.query(Account)
             .filter(Account.trader_id == trader_id, Account.status.in_(_STATUSY_ZYWE))
             .order_by(Account.created_at.desc(), Account.id.desc()).all())
    infos = [_konto(session, a) for a in konta]
    return Insights(name=(name or "").strip().split(" ")[0] or "there",
                    accounts=infos, variants=zloz(name, infos),
                    mail_variants=zloz_mail(name, infos, telegram_url))
