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

Każda faza to osobny track record. Awans zeruje saldo, a transakcje zostają
w jednej tabeli dla całego konta — bez odcięcia konto funded na $100,035
szło do klienta z „80 closed trades, +$15,683 net" z obu ewaluacji. Liczymy
tylko transakcje bieżącej fazy (`_transakcje_fazy`), tą samą granicą co
widok konta w portalu (`main._phase_window`).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

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
    # Tylko w danych dla panelu. Do tekstu nie trafia: klient na funded nie
    # dostaje odliczania dni handlu do okna wypłaty.
    payout_days_left: int = 0
    breach_reason: str | None = None
    # False = nie da się ustalić, które transakcje należą do bieżącej fazy.
    # Tekst pomija wtedy zdanie o transakcjach, zamiast pokazać wynik
    # z poprzednich faz jako dzisiejszy.
    trades_known: bool = True

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


def _bez_strefy(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _kiedy(t: Trade) -> datetime | None:
    # Ten sam znacznik co `main._w_oknie`: transakcja należy do fazy, w której
    # się zamknęła — to wtedy jej wynik trafił na saldo.
    return _bez_strefy(t.closed_at or t.opened_at)


def _cele_zaliczonych_faz(acc: Account) -> list[float]:
    """Cele (w %) faz, które konto musiało zaliczyć, żeby być tam, gdzie jest."""
    p1, p2 = float(acc.profit_target_p1 or 0), float(acc.profit_target_p2 or 0)
    if acc.phase == "eval_2":
        return [p1]
    if acc.phase == "funded":
        return [p1, p2] if (acc.steps or 1) >= 2 else [p1]
    return []


def _transakcje_fazy(acc: Account, zamkniete: list[Trade],
                     zdjete_wyplatami: float) -> list[Trade] | None:
    """Zamknięte transakcje BIEŻĄCEJ fazy, od najstarszej; None = nie wiadomo.

    Granica to `phase_started_at` — stawia ją każdy awans (poller i ręczne
    przestawienie fazy w panelu) i reset historii. Pierwsza faza bez daty to
    całe konto. Konto awansowane, zanim ta kolumna powstała, daty nie ma:
    granicę odtwarzamy z księgi, bo na koncie botowym saldo fazy to suma jej
    transakcji (plus zysk zdjęty wypłatami z resetem salda)."""
    zamkniete = sorted(zamkniete, key=lambda t: _kiedy(t) or datetime.min)
    od = _bez_strefy(acc.phase_started_at)
    if od is not None:
        return [t for t in zamkniete if (_kiedy(t) or datetime.min) >= od]
    if acc.phase == "eval_1":
        return zamkniete
    start = float(acc.initial_balance or 0)
    wynik_fazy = float(acc.balance or 0) - start + zdjete_wyplatami
    pnl = [float(t.pnl or 0) for t in zamkniete]
    ogon = [0.0] * (len(pnl) + 1)
    for i in range(len(pnl) - 1, -1, -1):
        ogon[i] = ogon[i + 1] + pnl[i]

    def zgodne(i: int) -> bool:
        return abs(ogon[i] - wynik_fazy) < 1.0

    if zgodne(0):
        return zamkniete          # całe konto w tej fazie (np. otwarte od razu jako funded)
    cele = _cele_zaliczonych_faz(acc)
    if not cele or not start:
        return None
    # Każda wcześniejsza faza skończyła się co najmniej na swoim celu, więc
    # przed granicą leży zysk nie mniejszy niż suma celów — to odsiewa ogony,
    # które zgadzają się z saldem przypadkiem.
    prog = start * sum(cele) / 100.0
    glowa = 0.0
    for i, x in enumerate(pnl):
        glowa += x
        if glowa >= prog - 0.01 and zgodne(i + 1):
            return zamkniete[i + 1:]
    # Księga się nie zgadza (np. ręczna korekta salda): granica tam, gdzie
    # saldo każdej fazy doszło do jej celu — tak awansuje silnik reguł.
    faza, saldo = 0, start
    for i, x in enumerate(pnl):
        saldo += x
        if saldo >= round(start * (1 + cele[faza] / 100.0), 2):
            faza, saldo = faza + 1, start
            if faza == len(cele):
                return zamkniete[i + 1:]
    return None


def _konto(session, acc: Account) -> KontoInfo:
    cfg = rules.config_from_account(acc)
    m = rules.display_metrics(cfg, balance=acc.balance, equity=acc.equity,
                              peak_equity=acc.peak_equity, day_start_equity=acc.day_start_equity,
                              trading_days=acc.trading_days_count)
    start = float(acc.initial_balance or 0) or 1.0
    zamkniete = (session.query(Trade)
                 .filter(Trade.account_id == acc.id, Trade.status == "closed").all())
    zdjete = 0.0
    if acc.phase == "funded" and acc.phase_started_at is None:
        zdjete = float(session.query(func.coalesce(func.sum(Payout.profit_amount), 0.0))
                       .filter(Payout.account_id == acc.id, Payout.balance_reset.is_(True))
                       .scalar() or 0.0)
    trades = _transakcje_fazy(acc, zamkniete, zdjete)
    znane = trades is not None
    trades = trades or []
    wins = [t for t in trades if (t.pnl or 0) > 0]
    # Seria z końca historii: +n = n zysków z rzędu, -n = n strat.
    streak = 0
    for t in reversed(trades):
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
        # Wprost z salda, nie z `display_metrics`: tam wynik jest już
        # zaokrąglony do setnych, a drugie zaokrąglenie przy druku psuło
        # połówki ($100,035 na $100,000 wychodziło jako 0.03 albo 0.0).
        profit_pct=(float(acc.balance or 0) - start) / start * 100,
        target_pct=float(cfg.profit_target_pct or 0),
        days=int(acc.trading_days_count or 0), min_days=int(cfg.min_trading_days or 0),
        dd_used_pct=float(m.get("overall_dd_used_pct", 0) or 0),
        daily_used_pct=float(m.get("daily_loss_used_pct", 0) or 0),
        trades=len(trades), wins=len(wins), net_pnl=float(sum((t.pnl or 0) for t in trades)),
        best_symbol=najlepszy, streak=streak, payouts_usd=float(wyplaty),
        split_pct=float(acc.profit_split_pct or 0),
        payout_days_left=int(poller.payout_days_left(acc)),
        breach_reason=acc.breach_reason,
        trades_known=znane,
    )


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def _usd_znak(x: float) -> str:
    return ("+" if x >= 0 else "-") + _usd(abs(x))


def _pc(x: float) -> str:
    """Wartość procentu bez znaku: 0.035 → „0.04", 4.80 → „4.8", 8.0 → „8".

    Dwa miejsca po przecinku, połówki w górę przez Decimal — float 0.035 to
    0.03499…, więc zwykłe round() dawało 0.03. Ruch mniejszy niż setna dostaje
    trzecie miejsce: konto na plusie nie może czytać się jako „up 0%"."""
    d = Decimal(repr(round(abs(x), 6)))
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == 0 and d > 0:
        q = d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    s = format(q, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _proc(x: float) -> str:
    return ("+" if x >= 0 else "-") + _pc(x) + "%"


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
# ujęciu stanu konta). Dni handlowych celowo nie pokazujemy — także na funded
# nie ma odliczania do okna wypłaty.
#
# Indeksy w `Ujecie` są duże i losowe, a klocek bierze swój modulo długości
# puli (`_z`). Pule mogą więc mieć różne długości i rosnąć bez przeliczania
# kombinacji — pełny iloczyn przy obecnych pulach miałby miliony pozycji.

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


# NWW liczb 1..10: indeks modulo długości każdej puli do 10 rozkłada się równo.
_ZAKRES = 2520


def _z(pula: list[str], i: int) -> str:
    return pula[i % len(pula)]


def _naglowek(k: KontoInfo, u: Ujecie) -> str:
    """Pierwsza linia bloku konta: numer i faza, bez ozdobników. Styl nagłówka
    bierze się z `uklad // 4`, więc przy kilku kontach wszystkie bloki jednej
    wiadomości mają nagłówek w tej samej formie."""
    f = _faza(k)
    return _z([f"Account {k.login}, {f}", f"{k.login} ({f})",
               f"Account {k.login} ({f})", f"{k.login}, {f}"], u.uklad // 4)


def _stan(k: KontoInfo, u: Ujecie, wiele: bool, *, blok: bool = False, pauza: bool = True) -> str:
    """Stan konta: saldo, wynik od startu, faza, cel, drawdown, wypłaty.
    `blok` = nad zdaniem stoi nagłówek z numerem i fazą, więc zdanie ich nie
    powtarza. `pauza=False` wyłącza ujęcia z pauzą (limit na wiadomość)."""
    faza = _faza(k)
    bal, start = _usd(k.balance), _usd(k.initial_balance)
    if k.status == "failed":
        powod = f" ({k.breach_reason})" if k.breach_reason else ""
        if blok:
            return _z([f"Hit a rule and got closed{powod}. Ended at {bal} from {start}.",
                       f"Closed after it hit a rule{powod}, finishing at {bal} on {start}.",
                       f"This one is closed, it hit a rule{powod}. Final balance {bal} on a "
                       f"{start} start.",
                       f"Got closed on a rule{powod}, ending at {bal} from {start}."], u.stan)
        return _z([
            f"Account {k.login} hit a rule and is closed{powod}. It finished at {bal} from "
            f"the {start} start.",
            f"Account {k.login} is closed after it hit a rule{powod}, ending at {bal} on a "
            f"{start} start.",
            f"{k.login} got closed after hitting a rule{powod}. It ended at {bal} from {start}.",
            f"Bad news on {k.login}, it hit a rule and is closed{powod}. Final balance {bal} "
            f"on a {start} start.",
        ], u.stan)
    wzrost = "up" if k.profit_pct >= 0 else "down"
    p, pr = f"{_pc(k.profit_pct)}%", _proc(k.profit_pct)
    twoje = "Account" if wiele else "Your account"
    if blok:
        ujecia = [
            f"At {bal}, {wzrost} {p} from the {start} start.",
            f"Balance {bal}, {pr} on {start}.",
            f"Sitting at {bal} – {pr} overall.",
            f"{bal} now, {wzrost} {p} since the start.",
            f"Now on {bal}, which is {pr} from {start}.",
            f"{bal} on the account, {wzrost} {p} overall.",
        ]
    else:
        ujecia = [
            f"{twoje} {k.login} is at {bal}, {wzrost} {p} from the {start} start. "
            f"It's in {faza}.",
            f"Account {k.login} is sitting at {bal} – that's {pr} on {start}, still in {faza}.",
            f"{k.login} ({faza}) is on {bal} right now, {wzrost} {p} overall.",
            f"Balance on {k.login} is {bal}, so {pr} since the start. We're in {faza}.",
            f"{k.login} is in {faza} and on {bal}, {wzrost} {p} from {start}.",
            f"Right now {k.login} shows {bal}, that's {pr} on the {start} start ({faza}).",
        ]
    zdanie = _z(ujecia, u.stan)
    if not pauza and "–" in zdanie:
        zdanie = ujecia[0]
    zd = [zdanie]
    if faza != "funded" and k.target_pct > 0:
        zostalo = k.target_pct - k.profit_pct
        cel = f"{_pc(k.target_pct)}%"
        if zostalo <= 0:
            zd.append(_z(["The profit target is already hit.",
                          "Target is done.",
                          f"The {cel} target is already reached.",
                          f"It's already past the {cel} target.",
                          "The target is in."], u.cel))
        else:
            z = f"{_pc(zostalo)}%"
            zd.append(_z([f"{z} left to the {cel} target.",
                          f"The {cel} target is {z} away.",
                          f"About {z} more gets it to the target.",
                          f"Still {z} to go for the {cel} target.",
                          f"{z} more and the {cel} target is done."], u.cel))
    if k.dd_used_pct > 0:
        dd = f"{_pc(k.dd_used_pct)}%"
        if k.dd_used_pct >= 70:
            zd.append(_z([f"We've used {dd} of the drawdown limit, so size stays small.",
                          f"Drawdown is at {dd} of the limit, which is why we're trading smaller.",
                          f"{dd} of the max loss is used, so we're being careful with size.",
                          f"With {dd} of the drawdown limit used, positions stay small for now.",
                          f"{dd} of the loss limit is gone, so risk per position is cut."], u.dd))
        elif k.dd_used_pct < 40:
            zd.append(_z([f"Only {dd} of the drawdown limit used, so there's plenty of room.",
                          f"Drawdown used is {dd} of the limit, lots of room left.",
                          f"Risk is fine, {dd} of the max loss used.",
                          f"The drawdown limit is barely touched, {dd} used.",
                          f"{dd} of the max loss used, so there's a good cushion."], u.dd))
        else:
            zd.append(_z([f"We've used {dd} of the drawdown limit.",
                          f"Drawdown is at {dd} of the limit.",
                          f"{dd} of the max loss used so far.",
                          f"The drawdown limit is {dd} used.",
                          f"{dd} of the loss limit is used, so we're keeping an eye on it."], u.dd))
    if faza == "funded" and k.payouts_usd > 0:
        # Fakt z księgi, nie zapowiedź: pojawia się dopiero po realnej wypłacie.
        zd.append(_z([f"Paid out so far: {_usd(k.payouts_usd)}, your share at {k.split_pct:.0f}%.",
                      f"You've had {_usd(k.payouts_usd)} paid out so far ({k.split_pct:.0f}% split)."],
                     u.cel // 5))
    return " ".join(zd)


def _wynik(k: KontoInfo, u: Ujecie) -> str:
    """Co zrobiliśmy: z zamkniętych transakcji; bez nich mówi to wprost."""
    if k.status == "failed":
        return _z(["The last positions went against us and the limit closed it before it could turn.",
                   "The last few trades went the wrong way and the limit kicked in before they came back.",
                   "A run of losing positions took it to the limit before it could recover."], u.wynik)
    if not k.trades_known:
        return ""
    if not k.trades:
        return _z(["Positions are going on, but nothing closed yet, so no result to show.",
                   "Nothing closed yet, so there's no trade result to report.",
                   "Trades are open and nothing closed yet. I'll have numbers next time.",
                   "Nothing closed yet on this one, so no trade numbers so far.",
                   "We're in positions but nothing closed yet, results come once they do."], u.wynik)
    n, wins = k.trades, k.wins
    tr = "trade" if n == 1 else "trades"
    wr = f"{100.0 * wins / n:.0f}%"
    net = _usd_znak(k.net_pnl)
    skad = k.best_symbol if k.best_symbol and k.net_pnl > 0 else None
    zd = [_z([
        f"{n} {tr} closed, {wr} of them winners, net {net}."
        + (f" Most of it came from {skad}." if skad else ""),
        f"We've closed {n} {tr} so far. {wins} won, net result {net}"
        + (f", mostly from {skad}." if skad else "."),
        f"So far {n} closed {tr} at a {wr} win rate, {net} net."
        + (f" {skad} did the most work." if skad else ""),
        f"{n} {tr} closed so far, {wins} of them green, {net} net."
        + (f" {skad} brought in the most." if skad else ""),
        f"There are {n} closed {tr} with a {wr} win rate, {net} overall."
        + (f" {skad} was the strongest." if skad else ""),
        f"Net result is {net} from {n} closed {tr}, {wins} of them winners."
        + (f" Most of that came from {skad}." if skad else ""),
    ], u.wynik)]
    if k.streak >= 3:
        zd.append(_z([f"The last {k.streak} were all winners.",
                      f"Last {k.streak} in a row went our way.",
                      f"{k.streak} winners in a row at the moment.",
                      f"The last {k.streak} all closed green."], u.wynik // 7))
    elif k.streak <= -3:
        s = -k.streak
        zd.append(_z([f"The last {s} went against us, so size is down until it turns.",
                      f"{s} losers in a row, so we've cut size for now.",
                      f"The last {s} closed red, so we're trading smaller until that changes.",
                      f"{s} losing ones in a row, size is reduced for now."], u.wynik // 7))
    if k.daily_used_pct >= 50:
        d = f"{_pc(k.daily_used_pct)}%"
        zd.append(_z([f"Today already used {d} of the daily limit, so we're done for the day.",
                      f"{d} of today's loss limit is used, so we stop here for today."], u.dd // 5))
    return " ".join(zd)


def _dalej(konta: list[KontoInfo], u: Ujecie) -> str:
    """Jedno zdanie o najbliższym kroku (bez dni handlowych)."""
    zywe = [k for k in konta if k.status != "failed"]
    if not zywe:
        return _z(["If you want to go again, say the word and I'll set the next account up.",
                   "If you want to go again, tell me and the next one gets set up.",
                   "If you want to go again, just say and we'll get a new account going."], u.dalej)

    def przed_celem(x: KontoInfo) -> bool:
        return _faza(x) != "funded" and x.target_pct > 0 and x.profit_pct < x.target_pct

    brakuje = [x for x in zywe if przed_celem(x)]
    if len(zywe) > 1 and brakuje and len(brakuje) < len(zywe):
        loginy = " and ".join(x.login for x in brakuje)
        return _z([f"Next step is getting {loginy} over the target as well.",
                   f"{loginy} still needs the target, then everything moves on.",
                   f"Once {loginy} hits the target too, everything moves to the next stage.",
                   f"Main focus now is the target on {loginy}.",
                   f"The target on {loginy} is the one thing left for now."], u.dalej)
    if len(zywe) > 1 and brakuje:
        return _z(["Next step is the profit target on each of them.",
                   "From here it's getting each account to its target without forcing it.",
                   "Once the targets are in, the accounts move to the next stage.",
                   "Each of them still needs its target, so that's the focus.",
                   "The targets are the job now, one account at a time."], u.dalej)
    k = brakuje[0] if brakuje else zywe[0]
    faza = _faza(k)
    if przed_celem(k):
        return _z(["Next step is the profit target, then it moves on.",
                   "From here it's just getting to the target without forcing it.",
                   "Once the target is in, the account moves to the next stage.",
                   "The target is the only thing on the list now.",
                   "We keep working toward the target, no rushing it."], u.dalej)
    if faza != "funded":
        return _z(["Target's done, so next is moving it to the next stage.",
                   "With the target in, it goes to the next stage from here.",
                   "Next up is the move to the next stage.",
                   "With the target reached, the next stage is what comes next.",
                   "The move to the next stage is next, now that the target is reached."],
                  u.dalej)
    return _dalej_funded(zywe, u)


# Co dalej na funded: sposób prowadzenia konta, nigdy wypłata — „teraz chodzi
# o pierwszą wypłatę" czytało się jak obietnica i wracało w każdym update'cie.
# Pula jest większa niż przy ewaluacji, bo funded dostaje update'y najdłużej,
# a trzy zdania na zmianę po kilku tygodniach brzmią jak automat.
_DALEJ_FUNDED = [
    "From here we keep it steady.",
    "Plan stays the same, steady and small.",
    "Next few sessions we stick to the same setups and keep size where it is.",
    "More of the same from here, clean entries and small risk.",
    "We keep risk per position where it is and stay patient.",
    "Coming days we're on the same markets and only taking the clean setups.",
    "No rush from here, we take the good setups and skip the rest.",
    "The focus now is a low drawdown and careful entries.",
    "Same approach every day from here, nothing fancy.",
    "Nothing changes in the plan, we keep doing what's been working.",
]
# Konto funded pod startem: zdanie o odrabianiu, bez „wygramy to z powrotem".
_DALEJ_FUNDED_POD_KRESKA = [
    "Next step is working {it} back above the start, without chasing it.",
    "From here it's getting {it} back over the start slowly, no forcing.",
    "We'll work {it} back up with smaller size rather than trying to win it back in one go.",
    "The plan is to grind {it} back above the start, one clean position at a time.",
    "Priority now is getting {it} back over the start without adding risk.",
]


def _dalej_funded(zywe: list[KontoInfo], u: Ujecie) -> str:
    pula = (_DALEJ_FUNDED_POD_KRESKA if any(k.profit_pct < 0 for k in zywe)
            else _DALEJ_FUNDED)
    return _z(pula, u.dalej).replace("{it}", "them" if len(zywe) > 1 else "it")


_OTWARCIA = [
    "Hey {name}, quick update on your {konto}.",
    "{name}, here's how your {konto} {jest} doing.",
    "Hi {name}, short update from the desk.",
    "Hey {name}, where things are with your {konto}.",
    "{name}, quick one on your {konto}.",
    "Hi {name}, here's the latest on your {konto}.",
    "Hey {name}, checking in with the numbers.",
    "{name}, a quick look at your {konto}.",
    "Hi {name}, update on how your {konto} {jest} going.",
    "Hey {name}, fresh numbers from the desk.",
]
_ZAKONCZENIA = [
    "Any questions, just ask.",
    "I'll message again when something moves.",
    "If you want the full trade list, just say.",
    "That's it for now.",
    "Let me know if you want more detail on any of it.",
    "Shout if anything's unclear.",
    "More soon.",
    "Happy to go through any of it if you want.",
    "Talk soon.",
    "I'll keep you posted.",
]
_OTWARCIA_MAIL = [
    "Hi {name},\n\nQuick update on the {konto} we manage for you.",
    "Hi {name},\n\nHere is where your {konto} {jest} this week.",
    "Hi {name},\n\nA short update from the desk on your {konto}.",
    "Hi {name},\n\nHere's the latest on the {konto} we trade for you.",
    "Hi {name},\n\nYour update from the desk, with the current numbers.",
    "Hi {name},\n\nChecking in with where your {konto} {jest} right now.",
]
_ZAKONCZENIA_MAIL = [
    "Any questions, the desk is on Telegram:",
    "If you want more detail, message the desk on Telegram:",
    "You can always reach the desk on Telegram:",
    "Happy to go through any of it, just message the desk:",
    "For anything else, the desk is on Telegram:",
    "If you want the full trade list, ask the desk on Telegram:",
]

# Ile ujęć oddajemy panelowi. Kombinacji jest dużo więcej; bierzemy stały,
# rozrzucony podzbiór (to samo ziarno = te same teksty między odświeżeniami).
# „Another wording" losuje z tych samych, więc im więcej, tym dłużej nie wraca
# do zdania, które admin już wysłał temu klientowi.
_ILE_UJEC = 120
_ILE_UJEC_MAIL = 60


def _ujecia(ile: int, ziarno: int) -> list[Ujecie]:
    los = random.Random(ziarno)
    out: list[Ujecie] = []
    byly: set[Ujecie] = set()
    while len(out) < ile:
        u = Ujecie(*(los.randrange(_ZAKRES) for _ in range(8)))
        if u not in byly:
            byly.add(u)
            out.append(u)
    return out


def _zlacz(sep: str, *czesci: str) -> str:
    # Zdanie o transakcjach bywa puste (faza nie do ustalenia) — bez tego
    # zostawałaby po nim pusta linia albo spacja na początku akapitu.
    return sep.join(c for c in czesci if c)


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
            ui = replace(u, stan=u.stan + i, cel=u.cel + i, dd=u.dd + i, wynik=u.wynik + i)
            srodek.append(_zlacz("\n", _naglowek(k, ui),
                                 _stan(k, ui, True, blok=True, pauza=i == 0), _wynik(k, ui)))
        akapity = [otw, *srodek, dalej, zakonczenie]
    else:
        k = konta[0]
        stan, wynik = _stan(k, u, False), _wynik(k, u)
        uklad = u.uklad % 4
        if uklad == 3:
            blok = _zlacz("\n", _naglowek(k, u), _stan(k, u, False, blok=True), wynik)
            akapity = [otw, blok, dalej, zakonczenie]
        elif uklad == 0:
            akapity = [otw, stan, wynik, dalej, zakonczenie]
        elif uklad == 1:
            akapity = [f"{otw} {stan}", wynik, f"{dalej} {zakonczenie}"]
        else:
            akapity = [otw, stan, _zlacz(" ", wynik, dalej), zakonczenie]
    return "\n\n".join(a for a in akapity if a)


def zloz(name: str | None, konta: list[KontoInfo]) -> list[str]:
    """Ujęcia DM-a na Telegram: otwarcie, stan, wynik, co dalej, zakończenie
    i układ akapitów losowane osobno, liczby zawsze te same."""
    imie = (name or "").strip().split(" ")[0] or "there"
    if not konta:
        return []
    out: list[str] = []
    for u in _ujecia(_ILE_UJEC, 7):
        t = _tresc(imie, konta, u, _z(_OTWARCIA, u.otw), _z(_ZAKONCZENIA, u.zak))
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
    for u in _ujecia(_ILE_UJEC_MAIL, 11):
        ogon = (f"{_z(_ZAKONCZENIA_MAIL, u.zak)}\n\n{telegram_url}" if telegram_url
                else "Any questions, just reply to this e-mail.")
        # W mailu układ 1 (wszystko w trzech gęstych akapitach) odpada. Cofamy
        # o jeden, a nie zerujemy: `uklad // 4` wybiera styl nagłówka.
        u_mail = replace(u, uklad=u.uklad - 1) if u.uklad % 4 == 1 else u
        t = (_tresc(imie, konta, u_mail, _z(_OTWARCIA_MAIL, u.otw), ogon)
             + "\n\n--\nForex Passing")
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
