"""Trade BOT — generator aktywnosci handlowej dla konta challenge.

Admin wlacza bota na konkretnym koncie, a ten od tego momentu prowadzi na nim
transakcje tak, jakby handlowal pro trader: rozne instrumenty, przewaga wygranych,
rosnaca krzywa equity. Poller zamiast czytac MT5 bierze snapshot stad — reszta
lancucha (rules.evaluate, snapshoty, breache, przejscia faz) zostaje bez zmian,
wiec bot przechodzi fazy PRAWDZIWYM silnikiem regul, a nie obok niego.

Trzy zasady, ktore trzymaja to w ryzach:

1. **Kazdy bot jest inny.** Persona wyprowadzana jest deterministycznie z konta
   (`bot_seed`): wlasny koszyk instrumentow, win rate, wielkosc pozycji, dzienny
   cel, szansa na dzien stratny. Dwa konta nie moga wygenerowac tej samej serii.

2. **Liczby sie zgadzaja.** Wolumen wynika z ryzyka i dystansu stopa, a cena
   zamkniecia jest policzona WSTECZ z P&L:
   `close = open ± pnl / (lots * point_value)`. Kto sprawdzi kalkulatorem,
   dostanie dokladnie te kwote, ktora widzi w tabeli.

3. **Bot nie lamie regul konta.** Dzienny cel jest ulamkiem limitu straty, wolumen
   jest przycinany do `max_lots`, a wychylenie floatingu ma twardy sufit. To nie
   jest „mam nadzieje, ze sie zmiesci" — to ograniczenia przy planowaniu pozycji.

Pozycja otwarta zyje w tabeli `trades` (`status='open'`), nie w pamieci procesu,
wiec restart serwera nie gubi transakcji ani ciaglosci krzywej.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .feed import MarketSnapshot
from .models import Account, Trade

settings = get_settings()

MIN_LOT = 0.01
STYLES = ("scalper", "balanced", "swing")
PACES = ("realistic", "active", "demo")


# --------------------------------------------------------------------------- #
#  Instrumenty                                                                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Instrument:
    """`point_value` = USD na 1.0 lota przy ruchu ceny o 1.0.

    `stop_move` to typowy dystans stop-lossa w jednostkach ceny — z niego wychodzi
    wolumen, a z wolumenu cena zamkniecia. Dzieki temu ruch ceny na kazdym
    instrumencie wyglada wiarygodnie niezaleznie od wielkosci konta.
    """
    symbol: str
    price: float
    tick: float
    point_value: float
    stop_move: float
    usd_base: bool = False      # USDJPY/USDCAD: wartosc punktu zalezy od kursu


INSTRUMENTS: dict[str, Instrument] = {i.symbol: i for i in [
    Instrument("XAUUSD", 2385.00, 0.01, 100, 6.5),
    Instrument("EURUSD", 1.08500, 0.00001, 100_000, 0.0028),
    Instrument("GBPUSD", 1.27200, 0.00001, 100_000, 0.0034),
    Instrument("USDJPY", 157.400, 0.001, 100_000, 0.420, usd_base=True),
    Instrument("AUDUSD", 0.66500, 0.00001, 100_000, 0.0022),
    Instrument("USDCAD", 1.36900, 0.00001, 100_000, 0.0030, usd_base=True),
    Instrument("NAS100", 19850.0, 0.1, 10, 62.0),
    Instrument("US30", 39420.0, 0.1, 10, 105.0),
    Instrument("GER40", 18320.0, 0.1, 10, 48.0),
    Instrument("USOIL", 78.500, 0.01, 1000, 0.55),
    Instrument("BTCUSD", 68500.0, 0.1, 1, 520.0),
    Instrument("ETHUSD", 3550.00, 0.01, 10, 34.0),
]}


def _point_value(inst: Instrument, price: float) -> float:
    return (inst.point_value / price) if inst.usd_base else inst.point_value


def _decimals(step: float) -> int:
    return max(0, -int(math.floor(math.log10(step) + 1e-9)))


def _round_to(value: float, step: float) -> float:
    return round(round(value / step) * step, _decimals(step))


# --------------------------------------------------------------------------- #
#  Persona — charakter konkretnego bota                                        #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Persona:
    style: str
    pace: str
    win_rate: float
    avg_r: float
    risk_pct: float          # % balansu ryzykowane na jedna pozycje
    daily_target_pct: float  # dzienny cel zysku w % konta
    red_day_odds: float
    trades_per_day: int
    symbols: tuple[str, ...]
    weights: tuple[float, ...]
    session_start_h: int
    session_end_h: int


# hold/gap w sekundach: (hold_min, hold_max, gap_min, gap_max)
PACE_TIMING: dict[str, tuple[int, int, int, int]] = {
    "realistic": (45 * 60, 120 * 60, 20 * 60, 70 * 60),
    "active": (6 * 60, 15 * 60, 3 * 60, 8 * 60),
    # „Fast demo" ma zapelniac historie na oczach admina — pelny cykl ~45 s.
    "demo": (20, 45, 8, 20),
}

# Tempo zmienia nie tylko czas trzymania pozycji, ale i intensywnosc dnia:
# (mnoznik dziennego celu, mnoznik liczby transakcji). Liczba transakcji rosnie
# szybciej niz cel — „Fast demo" ma zapelnic historie, a nie wystrzelic krzywa.
PACE_ACTIVITY: dict[str, tuple[float, float]] = {
    "realistic": (1.0, 1.0),
    "active": (1.6, 2.5),
    "demo": (3.0, 10.0),
}

# Mnozniki stylu: (win_rate, avg_r, risk_pct, trades_per_day)
_STYLE_TUNING: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[int, int]]] = {
    "scalper": ((0.66, 0.78), (1.1, 1.7), (0.12, 0.28), (5, 9)),
    "balanced": ((0.60, 0.72), (1.5, 2.2), (0.18, 0.38), (3, 6)),
    "swing": ((0.52, 0.64), (2.1, 3.2), (0.25, 0.50), (2, 4)),
}


def seed_for(acc: Account) -> int:
    """Stabilny, unikalny seed konta — decyduje o calym charakterze bota.

    Maska 31 bitow jest OBOWIAZKOWA: kolumna `bot_seed` to INTEGER, czyli int4
    w PostgreSQL (max 2 147 483 647). SQLite typuje dynamicznie i przyjmowal
    szersza wartosc bez mrugniecia, wiec na Supabase wychodzilo dopiero
    `NumericValueOutOfRange` przy starcie bota. 2 mld wariantow w zupelnosci
    wystarcza, zeby persony sie nie powtarzaly.
    """
    raw = f"{acc.id}:{acc.login}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") & 0x7FFFFFFF


def persona_for(acc: Account) -> Persona:
    """Deterministyczna persona konta. To ona gwarantuje, ze leaderboard nie
    zamieni sie w liste klonow z identycznymi transakcjami."""
    rng = random.Random(acc.bot_seed if acc.bot_seed is not None else seed_for(acc))
    style = acc.bot_style if acc.bot_style in STYLES else "balanced"
    pace = acc.bot_pace if acc.bot_pace in PACES else "active"
    wr, ar, rp, tpd = _STYLE_TUNING[style]

    pool = list(INSTRUMENTS)
    rng.shuffle(pool)
    symbols = tuple(pool[:rng.randint(4, 6)])
    # Wagi: jeden-dwa instrumenty „ulubione", reszta rzadziej — jak u ludzi.
    weights = tuple(rng.uniform(0.5, 3.0) for _ in symbols)

    goal_mult, count_mult = PACE_ACTIVITY[pace]
    return Persona(
        style=style,
        pace=pace,
        win_rate=rng.uniform(*wr),
        avg_r=rng.uniform(*ar),
        risk_pct=rng.uniform(*rp),
        daily_target_pct=rng.uniform(0.25, 1.10) * goal_mult,
        red_day_odds=rng.uniform(0.08, 0.20),
        trades_per_day=max(2, round(rng.randint(*tpd) * count_mult)),
        symbols=symbols,
        weights=weights,
        session_start_h=rng.choice([7, 8, 9, 13, 14]),
        session_end_h=rng.choice([17, 18, 20, 21]),
    )


# --------------------------------------------------------------------------- #
#  Czas serwera (ta sama strefa co poller, bez importu poller -> brak cyklu)    #
# --------------------------------------------------------------------------- #
def _server_now(now: datetime | None = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base + timedelta(hours=settings.server_utc_offset_hours)


def _day_key(now: datetime | None = None) -> str:
    return _server_now(now).strftime("%Y-%m-%d")


def _naive(dt: datetime) -> datetime:
    """Baza trzyma daty bez strefy — porownania musza byc na wspolnym typie."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# --------------------------------------------------------------------------- #
#  Sterowanie botem                                                            #
# --------------------------------------------------------------------------- #
def start(session, acc: Account, *, style: str = "balanced", pace: str = "active",
          target_pct: float = 0.0) -> None:
    acc.bot_enabled = True
    acc.bot_style = style if style in STYLES else "balanced"
    acc.bot_pace = pace if pace in PACES else "active"
    acc.bot_target_pct = max(0.0, float(target_pct or 0.0))
    acc.bot_paused = False
    if acc.bot_seed is None:
        acc.bot_seed = seed_for(acc)
    acc.bot_started_at = datetime.now(timezone.utc)
    session.commit()


def stop(session, acc: Account, now: datetime | None = None) -> None:
    """Wylacza bota. Otwarta pozycja jest domykana po BIEZACYM floatingu — trader
    widzial juz to equity, wiec balans nie moze sie cofnac."""
    now = now or datetime.now(timezone.utc)
    open_tr = _open_trade(session, acc)
    if open_tr is not None:
        _close_trade(open_tr, _floating_pnl(open_tr, now), now)
        acc.balance = round((acc.balance or 0.0) + open_tr.pnl, 2)
        acc.equity = acc.balance
        acc.open_pnl = 0.0
    acc.bot_enabled = False
    acc.bot_paused = False
    session.commit()


def set_paused(session, acc: Account, paused: bool) -> None:
    """Pauza vs Stop: pauza wstrzymuje tylko OTWIERANIE nowych pozycji. Konto
    zostaje pod kontrolą bota, więc saldo nie resynchronizuje się do feedu i
    krzywa equity nie dostaje uskoku — po wznowieniu bot płynnie wraca."""
    acc.bot_paused = bool(paused)
    session.commit()


def set_target(session, acc: Account, target_pct: float) -> float:
    """Zmienia docelowy zysk DZIAŁAJĄCEGO bota i zwraca nową wartość.

    Po osiągnięciu celu bot przestaje otwierać pozycje, ale zostaje włączony —
    bez tej funkcji jedynym wyjściem było zatrzymanie go (co resynchronizuje
    saldo do feedu i robi uskok na krzywej) albo pauza, która niczego nie zmienia.
    Podniesienie celu puszcza bota dalej z tego samego miejsca, bez uskoku.

    0 znosi limit — bot handluje bez górnej granicy zysku.
    """
    acc.bot_target_pct = max(0.0, round(float(target_pct or 0.0), 1))
    session.commit()
    return acc.bot_target_pct


def _open_trade(session, acc: Account) -> Trade | None:
    return (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "open")
            .order_by(Trade.id.desc()).first())


# --------------------------------------------------------------------------- #
#  Rdzen — jeden tick pollera                                                  #
# --------------------------------------------------------------------------- #
def tick(session, acc: Account, now: datetime | None = None) -> MarketSnapshot:
    """Prowadzi/otwiera/zamyka pozycje i oddaje snapshot w formacie feedu."""
    now = now or datetime.now(timezone.utc)
    p = persona_for(acc)
    balance = float(acc.balance or acc.initial_balance)
    open_tr = _open_trade(session, acc)

    if open_tr is not None:
        if open_tr.plan_close_at is not None and _naive(now) >= _naive(open_tr.plan_close_at):
            # Balans dopisujemy z `tr.pnl` PO zamknieciu, bo tam siedzi kwota
            # przeliczona z zaokraglonej ceny — inaczej tabela nie zgadzalaby sie
            # o kilka groszy z krzywa balansu.
            _close_trade(open_tr, open_tr.plan_pnl, now)
            balance = round(balance + open_tr.pnl, 2)
            open_tr = None
        else:
            floating = _floating_pnl(open_tr, now)
            open_tr.pnl = round(floating, 2)

    if open_tr is None and _should_open(session, acc, p, balance, now):
        open_tr = _open_new(session, acc, p, balance, now)

    floating = round(_floating_pnl(open_tr, now), 2) if open_tr is not None else 0.0
    lots = open_tr.lots if open_tr is not None else 0.0
    return MarketSnapshot(
        balance=round(balance, 2),
        equity=round(balance + floating, 2),
        open_pnl=floating,
        has_open_position=open_tr is not None,
        volume_lots=lots,
        volume_known=True,
    )


def _floating_pnl(tr: Trade, now: datetime) -> float:
    """P&L otwartej pozycji: gladkie dojscie do zaplanowanego wyniku plus szum,
    ktory wygasa do zera na zamknieciu (krzywa nie ma skoku przy realizacji)."""
    if tr.plan_close_at is None:
        return tr.pnl or 0.0
    span = (_naive(tr.plan_close_at) - _naive(tr.opened_at)).total_seconds()
    if span <= 0:
        return tr.plan_pnl
    t = min(1.0, max(0.0, (_naive(now) - _naive(tr.opened_at)).total_seconds() / span))
    smooth = t * t * (3 - 2 * t)
    ph = (tr.id or 1) * 0.618
    wobble = abs(tr.plan_pnl) * 0.55 * (1 - t) * (
        0.6 * math.sin(9.7 * t + ph) + 0.4 * math.sin(21.3 * t + 2 * ph))
    return tr.plan_pnl * smooth + wobble


def _close_trade(tr: Trade, pnl: float, now: datetime) -> None:
    inst = INSTRUMENTS.get(tr.symbol)
    pv = _point_value(inst, tr.open_price) if inst else 1.0
    sign = 1 if tr.side == "buy" else -1
    move = pnl / (tr.lots * pv) if tr.lots and pv else 0.0
    step = inst.tick if inst else 0.01
    tr.close_price = _round_to(tr.open_price + sign * move, step)
    # P&L liczymy z ZAOKRAGLONEJ ceny, zeby tabela zgadzala sie co do centa.
    tr.pnl = round(sign * (tr.close_price - tr.open_price) * tr.lots * pv, 2)
    tr.closed_at = now
    tr.status = "closed"


def _today_realized(session, acc: Account, now: datetime) -> float:
    """Zysk zrealizowany dzisiaj. Liczony z balansu (darmowe), z zabezpieczeniem
    na pierwszy tick, gdy silnik regul nie ustawil jeszcze baseline'u dnia."""
    if not acc.day_start_balance or acc.day_key != _day_key(now):
        return 0.0
    return float(acc.balance or 0.0) - float(acc.day_start_balance)


def _day_target(acc: Account, p: Persona, balance: float, now: datetime) -> float:
    """Cel na dzisiaj. Czasem ujemny — pro trader tez ma stratne dni, a krzywa
    bez ani jednego minusa wyglada jak wyklejona."""
    rng = random.Random(f"{acc.bot_seed}:{_day_key(now)}")
    if rng.random() < p.red_day_odds:
        return -balance * rng.uniform(0.10, 0.45) / 100.0
    return balance * p.daily_target_pct / 100.0


def _should_open(session, acc: Account, p: Persona, balance: float, now: datetime) -> bool:
    # 0) pauza — bez nowych wejsc. Otwarta pozycja dochodzi do konca normalnie,
    #    tak jak przy wylaczeniu EA: nie porzuca sie transakcji w polowie.
    if getattr(acc, "bot_paused", False):
        return False

    # 1) osiagniety docelowy zysk konta -> bot przestaje handlowac
    if acc.bot_target_pct and acc.initial_balance:
        profit_pct = (balance - acc.initial_balance) / acc.initial_balance * 100.0
        if profit_pct >= acc.bot_target_pct:
            return False

    # 2) dzienny cel zrobiony (w obie strony) -> koniec sesji
    target = _day_target(acc, p, balance, now)
    realized = _today_realized(session, acc, now)
    if (target >= 0 and realized >= target) or (target < 0 and realized <= target):
        return False

    # 3) odstep po ostatniej transakcji
    last = (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "closed")
            .order_by(Trade.id.desc()).first())
    if last is not None and last.closed_at is not None:
        _, _, gap_min, gap_max = PACE_TIMING[p.pace]
        gap = random.Random((last.id or 0) * 31 + 7).uniform(gap_min, gap_max)
        if (_naive(now) - _naive(last.closed_at)).total_seconds() < gap:
            return False

    # 4) godziny handlu — tylko tempo „realistic" udaje sesje rynkowa.
    #    Active/demo maja jechac od razu po wcisnieciu Start, tez w weekend.
    if p.pace == "realistic":
        srv = _server_now(now)
        if srv.weekday() >= 5 or not (p.session_start_h <= srv.hour < p.session_end_h):
            return False
    return True


def _open_new(session, acc: Account, p: Persona, balance: float, now: datetime) -> Trade:
    """Wolumen wynika z ryzyka i dystansu stopa; wynik z win-rate persony."""
    n = session.query(Trade).filter(Trade.account_id == acc.id).count()
    rng = random.Random(f"{acc.bot_seed}:{n}")

    symbol = rng.choices(p.symbols, weights=p.weights, k=1)[0]
    # Bez tego wagi potrafia wygenerowac cztery wejscia z rzedu w ten sam symbol,
    # a mial byc widoczny KOSZYK instrumentow. Jedno przelosowanie wystarczy.
    if len(p.symbols) > 1:
        prev = (session.query(Trade.symbol)
                .filter(Trade.account_id == acc.id)
                .order_by(Trade.id.desc()).limit(1).scalar())
        if prev == symbol:
            symbol = rng.choices(p.symbols, weights=p.weights, k=1)[0]
    inst = INSTRUMENTS[symbol]
    side = "buy" if rng.random() < 0.5 else "sell"
    sign = 1 if side == "buy" else -1

    open_price = _round_to(inst.price * (1 + rng.gauss(0, 0.004)), inst.tick)
    pv = _point_value(inst, open_price)

    # Wielkosc pozycji wynika z dziennego celu rozlozonego na planowana liczbe
    # wejsc — inaczej dwie wygrane realizowalyby caly dzien i historia bylaby
    # pusta. `risk_pct` zostaje juz tylko jako sufit bezpieczenstwa.
    day_goal = balance * p.daily_target_pct / 100.0
    edge = max(0.15, p.win_rate * p.avg_r - (1 - p.win_rate) * 0.8)
    risk_usd = min(day_goal / (p.trades_per_day * edge), balance * p.risk_pct / 100.0)
    lots = max(MIN_LOT, round(risk_usd / (inst.stop_move * pv), 2))
    if acc.max_lots and acc.max_lots > 0:
        lots = min(lots, round(acc.max_lots, 2))

    win = rng.random() < p.win_rate
    r = p.avg_r * rng.uniform(0.7, 1.35) if win else -rng.uniform(0.55, 1.0)
    plan_pnl = lots * inst.stop_move * pv * r

    # Przyciecie do dziennego celu: bot nie „przestrzeliwuje" planu na dzien,
    # dzieki czemu krzywa rosnie rownomiernie zamiast skakac.
    target = _day_target(acc, p, balance, now)
    realized = _today_realized(session, acc, now)
    if target > 0 and plan_pnl > 0:
        plan_pnl = min(plan_pnl, max(target - realized, target * 0.15))

    hold_min, hold_max, _, _ = PACE_TIMING[p.pace]
    hold = rng.uniform(hold_min, hold_max)

    tr = Trade(
        account_id=acc.id, symbol=symbol, side=side, lots=lots,
        open_price=open_price, pnl=0.0, opened_at=now, status="open", source="bot",
        plan_pnl=round(plan_pnl, 2),
        plan_close_at=now + timedelta(seconds=hold),
    )
    session.add(tr)
    session.flush()   # nadaje tr.id — potrzebne fazie szumu floatingu
    return tr
