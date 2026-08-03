"""Poller — pętla równań equity. Co `POLL_INTERVAL_SEC` sekund:
  1. pobiera snapshot z feedu (sim/MetaApi) dla każdego aktywnego konta,
  2. przepuszcza go przez silnik reguł (rules.evaluate),
  3. zapisuje snapshot + ewentualne breache, aktualizuje stan konta,
  4. obsługuje przejścia faz (eval_1 -> eval_2 -> funded).

Stan żyje w bazie (Account.*), więc restart procesu nie gubi postępu.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .db import SessionLocal
from .feed import Feed, make_feed
from .models import Account, Breach, EquitySnapshot
from . import notify, provisioning, rules, tradebot
from .rules import AccountRuntime, EquityTick, Phase, Status

# Plan skalowania kont funded: po +SCALE_TRIGGER% trader WYBIERA — wypłata albo
# powiększenie konta o SCALE_STEP%. Skalowanie nie dzieje się już samo (patrz
# `scale_offer` / `apply_scale_up`): jedno wyklucza drugie, bo zysk albo idzie
# do kieszeni, albo zamienia się w kapitał.
SCALE_TRIGGER_PCT = 10.0
SCALE_STEP_PCT = 50.0

settings = get_settings()
_feed: Feed | None = None
_task: asyncio.Task | None = None


def server_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.server_utc_offset_hours)


def server_day_key(now: datetime | None = None) -> str:
    return (now or server_now()).strftime("%Y-%m-%d")


def _runtime_from(acc: Account) -> AccountRuntime:
    return AccountRuntime(
        phase=Phase(acc.phase),
        status=Status(acc.status),
        balance=acc.balance,
        equity=acc.equity,
        open_pnl=acc.open_pnl,
        peak_equity=acc.peak_equity,
        day_key=acc.day_key,
        day_start_equity=acc.day_start_equity,
        day_start_balance=acc.day_start_balance,
        best_day_profit=acc.best_day_profit,
        trading_days_count=acc.trading_days_count,
        last_counted_trading_day=acc.last_counted_trading_day,
    )


def _write_runtime(acc: Account, rt: AccountRuntime) -> None:
    acc.phase = rt.phase.value
    acc.status = rt.status.value
    acc.balance = rt.balance
    acc.equity = rt.equity
    acc.open_pnl = rt.open_pnl
    acc.peak_equity = rt.peak_equity
    acc.day_key = rt.day_key
    acc.day_start_equity = rt.day_start_equity
    acc.day_start_balance = rt.day_start_balance
    acc.best_day_profit = rt.best_day_profit
    acc.trading_days_count = rt.trading_days_count
    acc.last_counted_trading_day = rt.last_counted_trading_day
    acc.breach_reason = rt.breach_reason


def _advance_phase(acc: Account, rt: AccountRuntime) -> None:
    """Przejście po zaliczeniu fazy. 1-step: eval_1 -> funded.
    2-step: eval_1 -> eval_2 -> funded. Nowa faza startuje od initial_balance."""
    now = server_now()
    if rt.phase == Phase.EVAL_1 and acc.steps >= 2:
        acc.phase = Phase.EVAL_2.value
        acc.status = Status.ACTIVE.value
    else:  # 1-step po eval_1, lub 2-step po eval_2 -> funded
        acc.phase = Phase.FUNDED.value
        acc.status = Status.FUNDED.value
    # reset metryk dla nowej fazy
    acc.balance = acc.initial_balance
    acc.equity = acc.initial_balance
    acc.open_pnl = 0.0
    acc.peak_equity = acc.initial_balance
    acc.day_key = server_day_key(now)
    acc.day_start_equity = acc.initial_balance
    acc.day_start_balance = acc.initial_balance
    acc.best_day_profit = 0.0
    acc.trading_days_count = 0
    acc.last_counted_trading_day = ""
    acc.breach_reason = None


def scale_offer(acc: Account) -> float | None:
    """Rozmiar, na jaki urośnie konto, jeśli trader wybierze skalowanie.

    None = oferta jeszcze nie przysługuje. Warunek liczymy w locie z salda,
    więc nie ma osobnej kolumny „należy się", która mogłaby rozjechać się ze
    stanem konta po wypłacie albo po breachu.
    """
    if acc.status != Status.FUNDED.value:
        return None
    # Zaokrąglenie obu stron jest konieczne: 100000 * 1.1 to w floatach
    # 110000.00000000001, więc konto DOKŁADNIE na progu nie dostawało oferty.
    prog = round(acc.initial_balance * (1 + SCALE_TRIGGER_PCT / 100.0), 2)
    if round(acc.balance, 2) < prog:
        return None
    return round(acc.initial_balance * (1 + SCALE_STEP_PCT / 100.0), 2)


def apply_scale_up(acc: Account) -> float:
    """Zamienia wypracowany zysk na WIĘKSZE konto (wybór zamiast wypłaty).

    Saldo jedzie w górę razem z rozmiarem i to nie jest hojność, tylko warunek
    poprawności. Próg max DD liczy się od `initial_balance` (rules._overall_floor),
    więc samo podniesienie rozmiaru przy nietkniętym saldzie stawia konto OD RAZU
    pod progiem i risk engine ubija je na następnym ticku: przy kroku 50% próg
    wynosi 135k, a saldo dalej 110k. Poprzednia wersja robiła dokładnie to (krok
    25%: próg 112,5k vs saldo 110k), więc reklamowany plan skalowania kończył
    konto zamiast je powiększać.

    Trader oddaje zysk, dostaje kapitał: dzień i szczyt equity startują od nowa
    z nowego rozmiaru, żeby limit dzienny nie liczył się od starego salda.
    """
    nowy = round(acc.initial_balance * (1 + SCALE_STEP_PCT / 100.0), 2)
    acc.initial_balance = nowy
    acc.balance = nowy
    acc.equity = nowy
    acc.open_pnl = 0.0
    acc.peak_equity = nowy
    acc.day_start_equity = nowy
    acc.day_start_balance = nowy
    acc.best_day_profit = 0.0
    return nowy


def _notify(acc: Account, event: str, extra: dict | None = None) -> None:
    """Best-effort powiadomienie tradera (e-mail/webhook)."""
    try:
        trader = acc.trader
        if not trader:
            return
        ctx = {"name": trader.full_name or trader.email, "login": acc.login,
               "split": acc.profit_split_pct}
        ctx.update(extra or {})
        notify.send(event, trader.email, ctx)
    except Exception as e:  # pragma: no cover
        print(f"[poller] notify błąd: {e}")


async def process_account(session, acc: Account, feed: Feed) -> None:
    bot_driven = bool(getattr(acc, "bot_enabled", False))
    if bot_driven:
        # Gałąź MUSI być przed feed.snapshot: MetaQuotesWebFeed odpala w tle
        # logowanie do web terminala już przy samym zapytaniu o snapshot.
        snap = tradebot.tick(session, acc)
    elif (not getattr(acc, "mt5_backed", True)
          and settings.feed in ("metaquotes_web", "metaapi")):
        # Poświadczenia wygenerowane lokalnie — na MT5 takiego rachunku nie ma,
        # więc kanały logujące się per konto tylko odbiłyby się od serwera.
        # Zawężone do nich celowo: sim i stuby w testach mają działać normalnie.
        return
    else:
        snap = await feed.snapshot(acc.login, acc.metaapi_account_id, acc.initial_balance,
                                   password=acc.platform_password, server=acc.platform_server)
    if snap is None:
        return

    now = server_now()
    cfg = rules.config_from_account(acc)
    rt = _runtime_from(acc)
    tick = EquityTick(
        equity=snap.equity,
        balance=snap.balance,
        open_pnl=snap.open_pnl,
        volume_lots=getattr(snap, "volume_lots", 0.0),
        volume_known=getattr(snap, "volume_known", False),
        day_key=server_day_key(now),
        has_open_position=snap.has_open_position,
        days_elapsed=(now - (acc.started_at.replace(tzinfo=timezone.utc) if acc.started_at.tzinfo is None else acc.started_at)).days,
    )

    res = rules.evaluate(cfg, rt, tick)
    _write_runtime(acc, rt)

    # snapshot do wykresu
    session.add(EquitySnapshot(
        account_id=acc.id, ts=datetime.now(timezone.utc),
        balance=snap.balance, equity=snap.equity, open_pnl=snap.open_pnl, day_key=tick.day_key,
    ))

    # breache
    for btype, detail, eq in res.breaches:
        session.add(Breach(account_id=acc.id, type=btype.value, detail=detail, equity_at_breach=eq))

    if res.failed:
        acc.closed_at = datetime.now(timezone.utc)
        maid = acc.metaapi_account_id
        login, pw = acc.platform_login, acc.platform_password
        session.commit()
        # ENFORCEMENT: konto przestaje się liczyć (status `failed` ustawiają reguły),
        # a dodatkowo próbujemy odciąć tradera na platformie. Kanał MetaQuotes-Demo
        # nie daje uprawnień serwerowych, więc tam zamykamy tylko pozycje.
        # Konto botowe nie ma realnych pozycji na MT5 — nie ma czego zamykać.
        if not bot_driven and (maid or (login and pw)):
            try:
                closed = await feed.close_all_positions(maid, login=login, password=pw)
                await feed.lock(maid, login=login, password=pw)
                print(f"[poller] ENFORCEMENT {acc.login}: konto FAILED "
                      f"({acc.breach_reason}) — zamknięto {closed} pozycji", flush=True)
            except Exception as e:  # pragma: no cover
                print(f"[poller] enforcement błąd: {e}", flush=True)
        _notify(acc, "breached", {"reason": acc.breach_reason})
    elif res.passed_phase:
        _advance_phase(acc, rt)
        session.commit()
        _notify(acc, "phase_passed", {"from_phase": rt.phase.value, "to_phase": acc.phase})
        if acc.status == Status.FUNDED.value:
            _notify(acc, "account_funded")
    else:
        # Skalowanie NIE dzieje się tu samo: przy +10% trader wybiera w portalu
        # między wypłatą a powiększeniem konta (POST /api/accounts/{id}/scale-up).
        session.commit()


def _active_query(session):
    return session.query(Account).filter(Account.status.in_(["active", "funded"]))


async def tick_once() -> dict:
    """Jeden pełny przebieg pollera — provisioning + equity wszystkich kont.

    Wydzielone z pętli, żeby dokładnie tę samą pracę dało się wywołać z zewnątrz
    (`POST /api/tick`). Na hostingu bezserwerowym nie ma procesu, który mógłby
    kręcić `_loop`, więc silnik napędza tam cron uderzający w endpoint. Zwraca
    licznik przetworzonych kont — cron ma po czym poznać, że coś się dzieje.
    """
    global _feed
    if _feed is None:
        _feed = make_feed()
    await provisioning.provision_pending(SessionLocal, _feed)
    session = SessionLocal()
    try:
        accounts = _active_query(session).all()
        for acc in accounts:
            await process_account(session, acc, _feed)
        return {"accounts": len(accounts), "feed": settings.feed}
    finally:
        session.close()


async def _loop() -> None:
    global _feed
    _feed = make_feed()
    print(f"[poller] start — feed={settings.feed}, interval={settings.poll_interval_sec}s")
    while True:
        try:
            await tick_once()
        except Exception as e:  # pragma: no cover
            print(f"[poller] błąd pętli: {e}")
        await asyncio.sleep(settings.poll_interval_sec)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task, _feed
    if _task:
        _task.cancel()
        _task = None
    if _feed:
        await _feed.close()
        _feed = None
