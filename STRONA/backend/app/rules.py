"""Silnik equity management / reguł challenge'a — SERCE systemu.

Logika w stylu FTMO / 2-stopniowej ewaluacji:
  - Profit target (np. +8% / +5%)
  - Max daily loss (% kapitału startowego, liczone od equity na starcie dnia)
  - Max overall drawdown — static lub trailing
  - Min trading days
  - (opcjonalnie) limit dni kalendarzowych

Moduł jest CZYSTY: zero zależności od bazy/ORM/HTTP — operuje na dataclassach,
dzięki czemu da się go w pełni przetestować jednostkowo (patrz tests/test_rules.py).
Poller (poller.py) tłumaczy wynik na zapis w DB i zmianę stanu konta.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    EVAL_1 = "eval_1"
    EVAL_2 = "eval_2"
    FUNDED = "funded"


class Status(str, Enum):
    ACTIVE = "active"
    PASSED = "passed"   # przeszedł fazę ewaluacji (przejście dalej)
    FAILED = "failed"   # złamanie reguły -> konto zablokowane
    FUNDED = "funded"   # konto fundowane, handluje "na żywo"


class BreachType(str, Enum):
    DAILY_LOSS = "daily_loss"
    MAX_DRAWDOWN = "max_drawdown"
    TIME_LIMIT = "time_limit"
    MAX_LOTS = "max_lots"


class DrawdownType(str, Enum):
    STATIC = "static"
    TRAILING = "trailing"


@dataclass
class ChallengeConfig:
    """Reguły konta. Procenty liczone od initial_balance."""
    initial_balance: float
    profit_target_pct: float          # cel zysku tej fazy (0 dla funded)
    max_daily_loss_pct: float         # np. 5.0
    max_overall_loss_pct: float       # np. 10.0
    min_trading_days: int = 0
    max_days: int | None = None       # limit dni kalendarzowych (None = brak)
    drawdown_type: DrawdownType = DrawdownType.STATIC
    consistency_pct: float = 0.0      # 0 = wyłączona; np. 40 = best-day <= 40% zysku
    max_lots: float = 0.0             # 0 = wyłączony; limit LACZNEGO wolumenu pozycji


@dataclass
class AccountRuntime:
    """Zmienny stan konta aktualizowany przy każdym ticku."""
    phase: Phase = Phase.EVAL_1
    status: Status = Status.ACTIVE
    balance: float = 0.0
    equity: float = 0.0
    open_pnl: float = 0.0
    peak_equity: float = 0.0          # do trailing DD
    day_key: str = ""                 # bieżący "dzień serwera"
    day_start_equity: float = 0.0     # baseline daily loss
    day_start_balance: float = 0.0    # baseline do reguły konsystencji
    best_day_profit: float = 0.0      # największy dzienny zysk (best-day rule)
    trading_days_count: int = 0
    last_counted_trading_day: str = ""
    days_elapsed: int = 0
    breach_reason: str | None = None


@dataclass
class EquityTick:
    equity: float
    balance: float
    open_pnl: float
    day_key: str                      # np. "2026-05-29" wg czasu serwera
    has_open_position: bool = False
    days_elapsed: int = 0             # dni od startu konta
    volume_lots: float = 0.0          # laczny wolumen otwartych pozycji
    volume_known: bool = False        # czy odczyt wolumenu jest wiarygodny


@dataclass
class EvalResult:
    breaches: list[tuple[BreachType, str, float]] = field(default_factory=list)
    passed_phase: bool = False
    failed: bool = False
    metrics: dict = field(default_factory=dict)


def _daily_loss_floor(cfg: ChallengeConfig, rt: AccountRuntime) -> float:
    limit_amount = cfg.max_daily_loss_pct / 100.0 * cfg.initial_balance
    return rt.day_start_equity - limit_amount


def _overall_floor(cfg: ChallengeConfig, rt: AccountRuntime) -> float:
    limit_amount = cfg.max_overall_loss_pct / 100.0 * cfg.initial_balance
    if cfg.drawdown_type == DrawdownType.TRAILING:
        # trailing: podłoga podąża za szczytem equity
        return rt.peak_equity - limit_amount
    # static: podłoga liczona od kapitału startowego
    return cfg.initial_balance - limit_amount


def profit_target_equity(cfg: ChallengeConfig) -> float:
    # round() niweluje artefakty zmiennoprzecinkowe (np. 110000.00000000001)
    return round(cfg.initial_balance * (1 + cfg.profit_target_pct / 100.0), 2)


def evaluate(cfg: ChallengeConfig, rt: AccountRuntime, tick: EquityTick) -> EvalResult:
    """Aktualizuje `rt` na podstawie `tick` i zwraca wynik (breache / pass).

    UWAGA: funkcja mutuje przekazany `rt` (peak, baseline dnia, liczniki).
    Konta ze statusem FAILED/PASSED są pomijane (no-op).
    """
    res = EvalResult()
    if rt.status in (Status.FAILED, Status.PASSED):
        return res

    # --- 1. Reset baseline na zmianie dnia (+ finalizacja best-day) ---
    if rt.day_start_balance == 0.0:
        rt.day_start_balance = tick.balance        # inicjalizacja przy pierwszym ticku
    if tick.day_key != rt.day_key:
        if rt.day_key:  # zamknij poprzedni dzień: zapisz jego zysk do best-day
            prev_day_profit = rt.balance - rt.day_start_balance
            if prev_day_profit > rt.best_day_profit:
                rt.best_day_profit = prev_day_profit
        rt.day_start_balance = tick.balance
        rt.day_key = tick.day_key
        rt.day_start_equity = tick.equity

    # --- 2. Aktualizacja bieżących wartości + szczytu ---
    rt.balance = tick.balance
    rt.equity = tick.equity
    rt.open_pnl = tick.open_pnl
    rt.days_elapsed = tick.days_elapsed
    if tick.equity > rt.peak_equity:
        rt.peak_equity = tick.equity

    # --- 3. Liczenie dni handlowych ---
    if tick.has_open_position and rt.last_counted_trading_day != tick.day_key:
        rt.last_counted_trading_day = tick.day_key
        rt.trading_days_count += 1

    # --- 4. Wyliczenie progów ---
    daily_floor = _daily_loss_floor(cfg, rt)
    overall_floor = _overall_floor(cfg, rt)
    target_equity = profit_target_equity(cfg)

    res.metrics = {
        "daily_floor": round(daily_floor, 2),
        "overall_floor": round(overall_floor, 2),
        "target_equity": round(target_equity, 2) if cfg.profit_target_pct > 0 else None,
        "daily_loss_used_pct": _used_pct(rt.day_start_equity, tick.equity, cfg.initial_balance, cfg.max_daily_loss_pct),
        "overall_dd_used_pct": _overall_used_pct(cfg, rt, tick.equity),
        "profit_pct": round((tick.balance - cfg.initial_balance) / cfg.initial_balance * 100, 2),
        "trading_days": rt.trading_days_count,
        "peak_equity": round(rt.peak_equity, 2),
        "max_lots": cfg.max_lots,
        "open_lots": round(tick.volume_lots, 2) if tick.volume_known else None,
    }

    # --- 5. Sprawdzenie breachy (kolejność = priorytet) ---
    # equity <= floor => złamanie. Używamy equity (uwzględnia floating PnL).
    if tick.equity <= daily_floor:
        res.failed = True
        res.breaches.append((
            BreachType.DAILY_LOSS,
            f"Equity ${tick.equity:,.2f} fell to or below the daily loss floor of ${daily_floor:,.2f}",
            tick.equity,
        ))

    if tick.equity <= overall_floor:
        res.failed = True
        res.breaches.append((
            BreachType.MAX_DRAWDOWN,
            f"Equity ${tick.equity:,.2f} fell to or below the {cfg.drawdown_type.value} max drawdown floor of ${overall_floor:,.2f}",
            tick.equity,
        ))

    # Limit ekspozycji: chroni firme przed „fullportem” — jednym zagraniem za
    # caly depozyt. Egzekwujemy TYLKO gdy odczyt wolumenu jest wiarygodny;
    # przy niepewnym odczycie nie karzemy tradera za nasz problem techniczny.
    if cfg.max_lots > 0 and tick.volume_known and tick.volume_lots > cfg.max_lots + 1e-9:
        res.failed = True
        res.breaches.append((
            BreachType.MAX_LOTS,
            f"Open volume {tick.volume_lots:.2f} lots exceeds the {cfg.max_lots:.2f} lot limit",
            tick.equity,
        ))

    if cfg.max_days is not None and tick.days_elapsed > cfg.max_days and not _target_reached(cfg, tick, rt):
        res.failed = True
        res.breaches.append((
            BreachType.TIME_LIMIT,
            f"Time limit of {cfg.max_days} days exceeded without reaching the profit target",
            tick.equity,
        ))

    if res.failed:
        rt.status = Status.FAILED
        rt.breach_reason = res.breaches[0][1]
        return res

    # --- 6. Reguła konsystencji (best-day <= X% całego zysku) ---
    consistency_ok = True
    if cfg.consistency_pct > 0:
        total_profit = tick.balance - cfg.initial_balance
        cur_day_profit = tick.balance - rt.day_start_balance
        best = max(rt.best_day_profit, cur_day_profit)
        if total_profit > 0 and best > cfg.consistency_pct / 100.0 * total_profit:
            consistency_ok = False
    res.metrics["consistency_ok"] = consistency_ok

    # --- 7. Sprawdzenie zaliczenia fazy (tylko fazy ewaluacyjne) ---
    if cfg.profit_target_pct > 0 and _target_reached(cfg, tick, rt):
        if rt.trading_days_count >= cfg.min_trading_days and consistency_ok:
            res.passed_phase = True
            rt.status = Status.PASSED

    return res


def _target_reached(cfg: ChallengeConfig, tick: EquityTick, rt: AccountRuntime) -> bool:
    # cel liczony od BALANSU (zysk z zamkniętych transakcji), nie floating equity
    return tick.balance >= profit_target_equity(cfg)


def _used_pct(day_start_equity: float, equity: float, initial: float, max_pct: float) -> float:
    """Ile % dziennego limitu już wykorzystano (0..100+)."""
    limit_amount = max_pct / 100.0 * initial
    if limit_amount <= 0:
        return 0.0
    used = max(0.0, day_start_equity - equity)
    return round(used / limit_amount * 100, 1)


def _overall_used_pct(cfg: ChallengeConfig, rt: AccountRuntime, equity: float) -> float:
    limit_amount = cfg.max_overall_loss_pct / 100.0 * cfg.initial_balance
    if limit_amount <= 0:
        return 0.0
    ref = rt.peak_equity if cfg.drawdown_type == DrawdownType.TRAILING else cfg.initial_balance
    used = max(0.0, ref - equity)
    return round(used / limit_amount * 100, 1)


# --- Presety challenge'y (typowe konfiguracje 2-step) -----------------------
PRESETS: dict[str, dict] = {
    "2step-10k": {
        "label": "2-Step 10k",
        "initial_balance": 10_000,
        "phases": {
            Phase.EVAL_1: dict(profit_target_pct=8, max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=3),
            Phase.EVAL_2: dict(profit_target_pct=5, max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=3),
            Phase.FUNDED: dict(profit_target_pct=0, max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=0),
        },
    },
    "2step-100k": {
        "label": "2-Step 100k",
        "initial_balance": 100_000,
        "phases": {
            Phase.EVAL_1: dict(profit_target_pct=8, max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=4),
            Phase.EVAL_2: dict(profit_target_pct=5, max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=4),
            Phase.FUNDED: dict(profit_target_pct=0, max_daily_loss_pct=5, max_overall_loss_pct=10, min_trading_days=0),
        },
    },
}


def display_metrics(cfg: ChallengeConfig, *, balance: float, equity: float,
                    peak_equity: float, day_start_equity: float, trading_days: int) -> dict:
    """Metryki do pokazania na dashboardzie — BEZ mutacji stanu, bez ticku."""
    daily_floor = day_start_equity - cfg.max_daily_loss_pct / 100.0 * cfg.initial_balance
    if cfg.drawdown_type == DrawdownType.TRAILING:
        overall_floor = peak_equity - cfg.max_overall_loss_pct / 100.0 * cfg.initial_balance
    else:
        overall_floor = cfg.initial_balance - cfg.max_overall_loss_pct / 100.0 * cfg.initial_balance
    return {
        "daily_floor": round(daily_floor, 2),
        "overall_floor": round(overall_floor, 2),
        "target_equity": round(profit_target_equity(cfg), 2) if cfg.profit_target_pct > 0 else None,
        "daily_loss_used_pct": _used_pct(day_start_equity, equity, cfg.initial_balance, cfg.max_daily_loss_pct),
        "overall_dd_used_pct": _overall_used_pct(cfg, AccountRuntime(peak_equity=peak_equity), equity),
        "profit_pct": round((balance - cfg.initial_balance) / cfg.initial_balance * 100, 2),
        "profit_target_pct": cfg.profit_target_pct,
        "max_daily_loss_pct": cfg.max_daily_loss_pct,
        "max_overall_loss_pct": cfg.max_overall_loss_pct,
        "min_trading_days": cfg.min_trading_days,
        "trading_days": trading_days,
    }


def config_from_account(acc) -> ChallengeConfig:
    """Buduje ChallengeConfig z bieżącej fazy konta (pola zapisane przy zakupie).
    Obsługuje 1-step (eval_1 -> funded) i 2-step (eval_1 -> eval_2 -> funded)."""
    phase = Phase(acc.phase)
    if phase == Phase.EVAL_1:
        target = acc.profit_target_p1
    elif phase == Phase.EVAL_2:
        target = acc.profit_target_p2
    else:
        target = 0.0
    return ChallengeConfig(
        initial_balance=acc.initial_balance,
        profit_target_pct=target,
        max_daily_loss_pct=acc.max_daily_loss_pct,
        max_overall_loss_pct=acc.max_overall_loss_pct,
        min_trading_days=acc.min_trading_days,
        drawdown_type=DrawdownType(acc.drawdown_type),
        consistency_pct=getattr(acc, "consistency_pct", 0.0) or 0.0,
        max_lots=getattr(acc, "max_lots", 0.0) or 0.0,
    )


def config_for(preset_key: str, phase: Phase, initial_balance: float,
               drawdown_type: DrawdownType = DrawdownType.STATIC) -> ChallengeConfig:
    """Zbuduj ChallengeConfig dla danego presetu i fazy."""
    preset = PRESETS.get(preset_key, PRESETS["2step-100k"])
    p = preset["phases"][phase]
    return ChallengeConfig(
        initial_balance=initial_balance,
        profit_target_pct=p["profit_target_pct"],
        max_daily_loss_pct=p["max_daily_loss_pct"],
        max_overall_loss_pct=p["max_overall_loss_pct"],
        min_trading_days=p["min_trading_days"],
        drawdown_type=drawdown_type,
    )
