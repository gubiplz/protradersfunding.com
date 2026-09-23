"""Update o kontach klienta z PRAWDZIWYCH liczb — pod wiadomość na Telegram.

Szablon „Weekly update" w mailu miał nawiasy do ręcznego uzupełnienia i wyszedł
raz z pustymi nawiasami. Tu liczby bierze serwer z tego, co panel i tak wie:
metryki reguł (`rules.display_metrics`), licznik dni handlowych, zamknięte
transakcje z księgi, wypłaty. Tekst składany z klocków, każdy w kilku ujęciach
(otwarcie, stan, cel, drawdown, wynik, co dalej, zakończenie, układ akapitów),
żeby „Another wording" zmieniało całą wiadomość i żeby dziesięć update'ów nie
brzmiało jak jeden szablon. Bez etykiet, punktorów i ciągu pauz: to znaki AI.

Zasady treści jak w `lead_mail.tresc()`: liczby, nie przymiotniki; jedno
zdanie o tym, co dalej; zero obietnic. Konto, które padło, dostaje zdanie
wprost — nie da się napisać „update", który tego nie zauważa.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
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


def _usd_znak(x: float) -> str:
    return ("+" if x >= 0 else "-") + _usd(abs(x))


def _proc(x: float) -> str:
    return ("+" if x >= 0 else "") + f"{x:.1f}%"


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


# Klocki tekstu. Każde zdanie ma kilka ujęć; ujęcie wybiera `Ujecie` (indeks
# per zdanie), więc „Another wording" zmienia naraz otwarcie, zdania o stanie,
# o transakcjach, o dalszym kroku, zakończenie i układ akapitów, a nie tylko
# pierwsze zdanie. Styl jak pisze człowiek na Telegramie: akapity zamiast
# etykiet i punktorów, najwyżej jedna pauza w wiadomości (jest tylko w jednym
# ujęciu stanu konta). Dni handlowych celowo nie pokazujemy.

@dataclass(frozen=True)
class Ujecie:
    otw: int
    stan: int
    cel: int
    dd: int
    wynik: int
    dalej: int
    zak: int
    uklad: int


def _naglowek(k: KontoInfo, u: Ujecie) -> str:
    """Pierwsza linia bloku konta: numer i faza, bez ozdobników."""
    return [f"Account {k.login}, {_faza(k)}", f"{k.login} ({_faza(k)})"][u.otw % 2]


def _stan(k: KontoInfo, u: Ujecie, wiele: bool, *, blok: bool = False, pauza: bool = True) -> str:
    """Stan konta: saldo, wynik od startu, faza, cel, drawdown, wypłaty.
    `blok` = nad zdaniem stoi nagłówek z numerem i fazą, więc zdanie ich nie
    powtarza. `pauza=False` wyłącza jedyne ujęcie z pauzą (limit na wiadomość)."""
    faza = _faza(k)
    if k.status == "failed":
        powod = f" ({k.breach_reason})" if k.breach_reason else ""
        if blok:
            return [f"Hit a rule and got closed{powod}. Ended at {_usd(k.balance)} "
                    f"from {_usd(k.initial_balance)}.",
                    f"Closed after it hit a rule{powod}, finishing at {_usd(k.balance)} "
                    f"on {_usd(k.initial_balance)}."][u.stan % 2]
        return [
            f"Account {k.login} hit a rule and is closed{powod}. It finished at "
            f"{_usd(k.balance)} from the {_usd(k.initial_balance)} start.",
            f"Account {k.login} is closed after it hit a rule{powod}, ending at "
            f"{_usd(k.balance)} on a {_usd(k.initial_balance)} start.",
        ][u.stan % 2]
    wzrost = "up" if k.profit_pct >= 0 else "down"
    p = f"{abs(k.profit_pct):.1f}%"
    twoje = "Account" if wiele else "Your account"
    if blok:
        ujecia = [
            f"At {_usd(k.balance)}, {wzrost} {p} from the {_usd(k.initial_balance)} start.",
            f"Balance {_usd(k.balance)}, {_proc(k.profit_pct)} on {_usd(k.initial_balance)}.",
            f"Sitting at {_usd(k.balance)} – {_proc(k.profit_pct)} overall.",
            f"{_usd(k.balance)} now, {wzrost} {p} since the start.",
        ]
    else:
        ujecia = [
        f"{twoje} {k.login} is at {_usd(k.balance)}, {wzrost} {p} from the "
        f"{_usd(k.initial_balance)} start. It's in {faza}.",
        f"Account {k.login} is sitting at {_usd(k.balance)} – that's "
        f"{_proc(k.profit_pct)} on {_usd(k.initial_balance)}, still in {faza}.",
        f"{k.login} ({faza}) is on {_usd(k.balance)} right now, {wzrost} {p} overall.",
        f"Balance on {k.login} is {_usd(k.balance)}, so {_proc(k.profit_pct)} since the start. "
        f"We're in {faza}.",
        ]
    i = u.stan % 4
    if i == (2 if blok else 1) and not pauza:
        i = 0
    zd = [ujecia[i]]
    if faza != "funded" and k.target_pct > 0:
        zostalo = k.target_pct - k.profit_pct
        if zostalo <= 0:
            zd.append(["The profit target is already hit.",
                       "Target is done.",
                       f"The {k.target_pct:.0f}% target is already reached."][u.cel % 3])
        else:
            zd.append([f"{zostalo:.1f}% left to the {k.target_pct:.0f}% target.",
                       f"The {k.target_pct:.0f}% target is {zostalo:.1f}% away.",
                       f"About {zostalo:.1f}% more gets it to the target."][u.cel % 3])
    if k.dd_used_pct > 0:
        dd = f"{k.dd_used_pct:.0f}%"
        if k.dd_used_pct >= 70:
            zd.append([f"We've used {dd} of the drawdown limit, so size stays small.",
                       f"Drawdown is at {dd} of the limit, which is why we're trading smaller.",
                       f"{dd} of the max loss is used, so we're being careful with size."][u.dd % 3])
        elif k.dd_used_pct < 40:
            zd.append([f"Only {dd} of the drawdown limit used, so there's plenty of room.",
                       f"Drawdown used is {dd} of the limit, lots of room left.",
                       f"Risk is fine, {dd} of the max loss used."][u.dd % 3])
        else:
            zd.append([f"We've used {dd} of the drawdown limit.",
                       f"Drawdown is at {dd} of the limit.",
                       f"{dd} of the max loss used so far."][u.dd % 3])
    if faza == "funded":
        if k.payouts_usd > 0:
            zd.append([f"Paid out so far: {_usd(k.payouts_usd)}, your share at {k.split_pct:.0f}%.",
                       f"You've had {_usd(k.payouts_usd)} paid out so far ({k.split_pct:.0f}% split)."]
                      [u.cel % 2])
        if k.payout_days_left > 0:
            zd.append([f"First payout window opens in {k.payout_days_left} trading days.",
                       f"{k.payout_days_left} more trading days until the first payout window."]
                      [u.dd % 2])
    return " ".join(zd)


def _wynik(k: KontoInfo, u: Ujecie) -> str:
    """Co zrobiliśmy: z zamkniętych transakcji; bez nich mówi to wprost."""
    if k.status == "failed":
        return ["The last positions went against us and the limit closed it before it could turn.",
                "The last few trades went the wrong way and the limit kicked in before they came back."
                ][u.wynik % 2]
    if not k.trades:
        return ["Positions are going on, but nothing closed yet, so no result to show.",
                "Nothing closed yet, so there's no trade result to report.",
                "Trades are open and nothing closed yet. I'll have numbers next time."][u.wynik % 3]
    wr = f"{100.0 * k.wins / k.trades:.0f}%"
    net = _usd_znak(k.net_pnl)
    skad = k.best_symbol if k.best_symbol and k.net_pnl > 0 else None
    zd = [[
        f"{k.trades} trades closed, {wr} of them winners, net {net}."
        + (f" Most of it came from {skad}." if skad else ""),
        f"We've closed {k.trades} trades so far. {k.wins} won, net result {net}"
        + (f", mostly from {skad}." if skad else "."),
        f"So far {k.trades} closed trades at a {wr} win rate, {net} net."
        + (f" {skad} did the most work." if skad else ""),
    ][u.wynik % 3]]
    if k.streak >= 3:
        zd.append([f"The last {k.streak} were all winners.",
                   f"Last {k.streak} in a row went our way."][u.wynik % 2])
    elif k.streak <= -3:
        zd.append([f"The last {-k.streak} went against us, so size is down until it turns.",
                   f"{-k.streak} losers in a row, so we've cut size for now."][u.wynik % 2])
    if k.daily_used_pct >= 50:
        zd.append(f"Today already used {k.daily_used_pct:.0f}% of the daily limit, "
                  "so we're done for the day.")
    return " ".join(zd)


def _dalej(konta: list[KontoInfo], u: Ujecie) -> str:
    """Jedno zdanie o najbliższym kroku (bez dni handlowych)."""
    zywe = [k for k in konta if k.status != "failed"]
    if not zywe:
        return ["If you want to go again, say the word and I'll set the next account up.",
                "If you want to go again, tell me and the next one gets set up."][u.dalej % 2]
    def przed_celem(x: KontoInfo) -> bool:
        return _faza(x) != "funded" and x.target_pct > 0 and x.profit_pct < x.target_pct

    brakuje = [x for x in zywe if przed_celem(x)]
    if len(zywe) > 1 and brakuje and len(brakuje) < len(zywe):
        loginy = " and ".join(x.login for x in brakuje)
        return [f"Next step is getting {loginy} over the target as well.",
                f"{loginy} still needs the target, then everything moves on.",
                f"Once {loginy} hits the target too, everything moves to the next stage."
                ][u.dalej % 3]
    if len(zywe) > 1 and brakuje:
        return ["Next step is the profit target on each of them.",
                "From here it's getting each account to its target without forcing it.",
                "Once the targets are in, the accounts move to the next stage."][u.dalej % 3]
    k = brakuje[0] if brakuje else zywe[0]
    faza = _faza(k)
    if przed_celem(k):
        return ["Next step is the profit target, then it moves on.",
                "From here it's just getting to the target without forcing it.",
                "Once the target is in, the account moves to the next stage."][u.dalej % 3]
    if faza != "funded":
        return ["Target's done, so next is moving it to the next stage.",
                "With the target in, it goes to the next stage from here.",
                "Next up is the move to the next stage."][u.dalej % 3]
    if k.payout_days_left > 0:
        return ["Next up is the first payout window.",
                "Now it's about getting to the first payout.",
                "The first payout is the next thing on the list."][u.dalej % 3]
    return ["From here we keep it steady.",
            "Plan stays the same, steady and small.",
            "Nothing changes from here, we keep it steady."][u.dalej % 3]


_OTWARCIA = [
    "Hey {name}, quick update on your {konto}.",
    "{name}, here's how your {konto} {jest} doing.",
    "Hi {name}, short update from the desk.",
    "Hey {name}, where things are with your {konto}.",
    "{name}, quick one on your {konto}.",
]
_ZAKONCZENIA = [
    "Any questions, just ask.",
    "I'll message again when something moves.",
    "If you want the full trade list, just say.",
    "That's it for now.",
    "Let me know if you want more detail on any of it.",
]
_OTWARCIA_MAIL = [
    "Hi {name},\n\nQuick update on the {konto} we manage for you.",
    "Hi {name},\n\nHere is where your {konto} {jest} this week.",
    "Hi {name},\n\nA short update from the desk on your {konto}.",
]
_ZAKONCZENIA_MAIL = [
    "Any questions, the desk is on Telegram:",
    "If you want more detail, message the desk on Telegram:",
    "You can always reach the desk on Telegram:",
]

# Ile ujęć oddajemy panelowi. Kombinacji jest dużo więcej; bierzemy stały,
# rozrzucony podzbiór (to samo ziarno = te same teksty między odświeżeniami).
_ILE_UJEC = 48
_ILE_UJEC_MAIL = 24


def _ujecia(ile: int, n_otw: int, n_zak: int, ziarno: int) -> list[Ujecie]:
    los = random.Random(ziarno)
    wszystkie = [Ujecie(o, s, c, d, w, n, z, l)
                 for o in range(n_otw) for s in range(4) for c in range(3) for d in range(3)
                 for w in range(3) for n in range(3) for z in range(n_zak) for l in range(4)]
    los.shuffle(wszystkie)
    return wszystkie[:ile]


def _tresc(imie: str, konta: list[KontoInfo], u: Ujecie, otwarcie: str, zakonczenie: str) -> str:
    wiele = len(konta) > 1
    otw = (otwarcie.replace("{name}", imie)
           .replace("{konto}", "accounts" if wiele else "account")
           .replace("{jest}", "are" if wiele else "is"))
    dalej = _dalej(konta, u)
    if wiele:
        # Kilka kont: każde to blok — nagłówek, linia stanu, linia transakcji —
        # bez punktorów. Kolejne konto dostaje przesunięte ujęcia, żeby dwa
        # bloki nie zaczynały się tym samym zdaniem; pauza najwyżej w pierwszym.
        srodek = []
        for i, k in enumerate(konta):
            ui = replace(u, stan=u.stan + i, cel=u.cel + i, dd=u.dd + i, wynik=u.wynik + i,
                         otw=u.otw)
            srodek.append(f"{_naglowek(k, ui)}\n{_stan(k, ui, True, blok=True, pauza=i == 0)}"
                          f"\n{_wynik(k, ui)}")
        akapity = [otw, *srodek, dalej, zakonczenie]
    else:
        k = konta[0]
        stan, wynik = _stan(k, u, False), _wynik(k, u)
        if u.uklad == 3:
            blok = f"{_naglowek(k, u)}\n{_stan(k, u, False, blok=True)}\n{wynik}"
            akapity = [otw, blok, dalej, zakonczenie]
        elif u.uklad == 0:
            akapity = [otw, stan, wynik, dalej, zakonczenie]
        elif u.uklad == 1:
            akapity = [f"{otw} {stan}", wynik, f"{dalej} {zakonczenie}"]
        else:
            akapity = [otw, stan, f"{wynik} {dalej}", zakonczenie]
    return "\n\n".join(a for a in akapity if a)


def zloz(name: str | None, konta: list[KontoInfo]) -> list[str]:
    """Ujęcia DM-a na Telegram: otwarcie, stan, wynik, co dalej, zakończenie
    i układ akapitów losowane osobno, liczby zawsze te same."""
    imie = (name or "").strip().split(" ")[0] or "there"
    if not konta:
        return []
    out: list[str] = []
    for u in _ujecia(_ILE_UJEC, len(_OTWARCIA), len(_ZAKONCZENIA), 7):
        t = _tresc(imie, konta, u, _OTWARCIA[u.otw], _ZAKONCZENIA[u.zak])
        if t not in out:
            out.append(t)
    return out


def zloz_mail(name: str | None, konta: list[KontoInfo], telegram_url: str) -> list[str]:
    """Wersja MAILOWA „Weekly update": ten sam środek co w DM-ie, akapity,
    link do desku jako guzik i stopka po `--`."""
    imie = (name or "").strip().split(" ")[0] or "there"
    if not konta:
        return []
    out: list[str] = []
    for u in _ujecia(_ILE_UJEC_MAIL, len(_OTWARCIA_MAIL), len(_ZAKONCZENIA_MAIL), 11):
        ogon = (f"{_ZAKONCZENIA_MAIL[u.zak]}\n\n{telegram_url}" if telegram_url
                else "Any questions, just reply to this e-mail.")
        # W mailu układ 1 (wszystko w trzech gęstych akapitach) odpada.
        u_mail = replace(u, uklad=0 if u.uklad == 1 else u.uklad)
        t = _tresc(imie, konta, u_mail, _OTWARCIA_MAIL[u.otw], ogon) + "\n\n--\nForex Passing"
        if t not in out:
            out.append(t)
    return out


def dla_tradera(session, trader_id: int, name: str | None, telegram_url: str = "") -> Insights:
    konta = (session.query(Account)
             .filter(Account.trader_id == trader_id, Account.status.in_(_STATUSY_ZYWE))
             .order_by(Account.created_at.desc(), Account.id.desc()).all())
    infos = [_konto(session, a) for a in konta]
    return Insights(name=(name or "").strip().split(" ")[0] or "there",
                    accounts=infos, variants=zloz(name, infos),
                    mail_variants=zloz_mail(name, infos, telegram_url))
