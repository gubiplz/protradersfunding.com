"""Poller — pętla równań equity. Co `POLL_INTERVAL_SEC` sekund:
  1. pobiera snapshot z feedu (sim/MetaApi) dla każdego aktywnego konta,
  2. przepuszcza go przez silnik reguł (rules.evaluate),
  3. zapisuje snapshot + ewentualne breache, aktualizuje stan konta,
  4. obsługuje przejścia faz (eval_1 -> eval_2 -> funded).

Stan żyje w bazie (Account.*), więc restart procesu nie gubi postępu.
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .config import get_settings
from .db import SessionLocal
from .feed import Feed, make_feed
from .models import Account, AppSetting, Breach, EquitySnapshot, Trade
from . import catalog, notify, provisioning, push, rules, tradebot
from .rules import AccountRuntime, EquityTick, Phase, Status

# Plan skalowania kont funded: po +SCALE_TRIGGER% trader WYBIERA — wypłata albo
# wyższy plan z cennika. Skalowanie nie dzieje się samo (patrz `scale_offer` /
# `apply_scale_up`): jedno wyklucza drugie, bo zysk albo idzie do kieszeni, albo
# zamienia się w kapitał. Krok to NASTĘPNY tier z katalogu, nie procent — konto
# po skalowaniu ma być zwykłym planem z cennika, a nie rozmiarem, którego nigdzie
# nie ma w ofercie (poprzednia wersja robiła 150k/225k/337k).
SCALE_TRIGGER_PCT = 15.0

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

    None = oferta jeszcze nie przysługuje albo konto jest już na szczycie
    drabiny (2M). Warunek liczymy w locie z salda, więc nie ma osobnej kolumny
    „należy się", która mogłaby rozjechać się ze stanem konta po wypłacie albo
    po breachu.
    """
    if acc.status != Status.FUNDED.value:
        return None
    # Zaokrąglenie obu stron jest konieczne: 100000 * 1.15 to w floatach
    # 114999.99999999999, więc konto DOKŁADNIE na progu nie dostawało oferty.
    prog = round(acc.initial_balance * (1 + SCALE_TRIGGER_PCT / 100.0), 2)
    if round(acc.balance, 2) < prog:
        return None
    return catalog.next_size_up(acc.steps, acc.initial_balance)


def apply_scale_up(session, acc: Account) -> float:
    """Wgrywa traderowi NASTĘPNY plan z cennika: od zera, ale od razu funded.

    Konta nie da się powiększyć w miejscu i to nie jest kwestia gustu. Saldo nie
    należy do nas: MetaApiFeed czyta je z brokera, MetaQuotesWebFeed z terminala,
    a SimulatedFeed trzyma własny stan per login. Samo podbicie `initial_balance`
    w bazie żyje więc do pierwszego ticku pollera — potem saldo wraca do realnego,
    a próg max DD (rules._overall_floor liczy go od `initial_balance`) stoi już
    wysoko nad nim i risk engine ubija konto. Dlatego skalowanie wydaje NOWY
    rachunek o docelowym rozmiarze, zamiast udawać, że stary urósł.

    Trader oddaje zysk, dostaje kapitał: konto startuje od salda nowego planu,
    z jego limitami i limitem wolumenu (stary mechanizm zostawiał `max_lots` ze
    starego rozmiaru, więc konto 150k dalej miało cap 100k).
    """
    prod = catalog.next_product(session, acc.steps, acc.initial_balance)
    if prod is None:
        raise ValueError("no larger plan available")

    # Cały plan z katalogu, nie tylko rozmiar — inaczej konto zostaje z regułami
    # poprzedniego tieru i przestaje odpowiadać czemukolwiek z cennika.
    acc.product_key = prod.key
    acc.preset = prod.key
    acc.initial_balance = prod.account_size
    acc.profit_target_p1 = prod.profit_target_p1
    acc.profit_target_p2 = prod.profit_target_p2
    acc.max_daily_loss_pct = prod.max_daily_loss_pct
    acc.max_overall_loss_pct = prod.max_overall_loss_pct
    acc.min_trading_days = prod.min_trading_days
    acc.drawdown_type = prod.drawdown_type
    acc.profit_split_pct = prod.profit_split_pct
    acc.max_lots = getattr(prod, "max_lots", 0.0) or 0.0

    # Runtime od zera, na nowym rozmiarze.
    bal = prod.account_size
    acc.phase = Phase.FUNDED.value
    acc.balance = bal
    acc.equity = bal
    acc.peak_equity = bal
    acc.day_start_equity = bal
    acc.day_start_balance = bal
    acc.open_pnl = 0.0
    acc.best_day_profit = 0.0
    acc.trading_days_count = 0
    acc.last_counted_trading_day = ""
    acc.breach_reason = None
    acc.started_at = datetime.now(timezone.utc)
    acc.closed_at = None

    # Nowy rachunek MT5. Login MUSI się zmienić: SimulatedFeed cache'uje stan po
    # loginie, więc pod starym numerem wróciłoby stare saldo i konto poszłoby w
    # breach na pierwszym ticku. Poświadczenia zdejmujemy, bo stary rachunek ma
    # na sobie stary kapitał — resztę dokończy provisioning.provision_pending,
    # który dobiera z puli rachunek o pasującym rozmiarze i wysyła maila.
    acc.login = provisioning._gen_login()
    acc.platform_login = None
    acc.platform_password = None
    acc.platform_server = None
    acc.metaapi_account_id = None
    acc.status = "provisioning"
    acc.scale_count = (acc.scale_count or 0) + 1
    return float(bal)


# Próg ostrzeżenia „zbliżasz się do limitu" (procent WYKORZYSTANIA limitu,
# nie procent straty) — jak u FTMO, zanim konto realnie pęknie.
LIMIT_WARN_PCT = 80.0


def _limit_warnings_due(acc: Account, metrics: dict, day_key: str) -> list[str]:
    """Tytuły należnych ostrzeżeń o limitach; przy okazji stawia strażniki.

    Raz dziennie na typ limitu (kolumny limit_warn_*_day). Na serverless tick
    budzi się z ruchem albo dziennym cronem, więc ostrzeżenie może przyjść
    z opóźnieniem — nadal lepsze niż cisza aż do breachu.
    """
    tytuly = []
    daily = float(metrics.get("daily_loss_used_pct") or 0)
    dd = float(metrics.get("overall_dd_used_pct") or 0)
    if daily >= LIMIT_WARN_PCT and acc.limit_warn_daily_day != day_key:
        acc.limit_warn_daily_day = day_key
        tytuly.append(f"{acc.login}: {int(round(daily))}% of daily loss limit used")
    if dd >= LIMIT_WARN_PCT and acc.limit_warn_dd_day != day_key:
        acc.limit_warn_dd_day = day_key
        tytuly.append(f"{acc.login}: {int(round(dd))}% of max drawdown used")
    return tytuly


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
        # Skalowanie NIE dzieje się tu samo: przy +15% trader wybiera w portalu
        # między wypłatą a wyższym planem (POST /api/accounts/{id}/scale-up).
        ostrzezenia = _limit_warnings_due(acc, res.metrics, tick.day_key)
        session.commit()
        # Push + centrum powiadomień, BEZ maila (jak daily_recap) — mail o
        # „prawie stracie" o 3 nad ranem to panika, nie pomoc.
        email = acc.trader.email if acc.trader else None
        for tytul in ostrzezenia:
            try:
                push.send_event("limit_warning", email, tytul)
            except Exception as e:  # pragma: no cover
                print(f"[poller] limit warning: {e}", flush=True)


BACKFILL_LOCK_KEY = "bot_backfill_lock"
BACKFILL_LOCK_TTL_SEC = 15 * 60
# Backfill kończy godzinę przed „teraz" i od tego momentu konto przejmuje żywy
# poller. Bufor jest też filtrem kursora: snapshoty dopisane przez lazy-tick
# W TRAKCIE dogrywania mają ts przy „teraz", więc nie zafałszują wznowienia.
BACKFILL_MARGIN = timedelta(hours=1)


def _backfill_locked_id(session) -> int | None:
    """Konto, któremu backfill właśnie dogrywa historię — poller je omija.

    Bez zamka lazy-tick z ruchu publicznego wpycha się między odtwarzane kroki
    z dzisiejszym `day_key` i saldem sprzed dwóch tygodni: baseline'y dnia
    skaczą tam i z powrotem, licznik dni handlowych rośnie podwójnie, a wykres
    dostaje samotny punkt „dziś" w środku przeszłości. Zamek siedzi w bazie,
    bo na serverless backfill i lazy-tick to osobne procesy. Stary zamek
    (proces ubity w połowie) wygasa po kwadransie — konto nie może zostać
    wyłączone z silnika na zawsze."""
    row = session.get(AppSetting, BACKFILL_LOCK_KEY)
    if not row or not row.value:
        return None
    try:
        aid, ts = row.value.split(":", 1)
        if time.time() - float(ts) > BACKFILL_LOCK_TTL_SEC:
            return None
        return int(aid)
    except ValueError:
        return None


def backfill_bot(session, acc: Account, days: int, *, chunk_days: float = 3.0,
                 step_min: float = 20.0) -> dict:
    """Dogrywa kontu botowemu historię WSTECZ, jakby bot działał od `days` dni.

    Odtwarza przeszłość TYM SAMYM silnikiem co żywy przebieg (`tradebot.tick`
    + `rules.evaluate` + snapshot), tylko z zegarem przestawionym w tył — konto
    dostaje pełną tabelę transakcji, krzywą equity i naliczone dni handlowe,
    a nie gołe saldo. Pierwsze wywołanie cofa też metrykę startu konta
    (`created_at`/`started_at`/`bot_started_at`) — historia starsza niż konto
    zdradzałaby dogrywkę na pierwszy rzut oka.

    Jeden request odtwarza najwyżej `chunk_days` dni symulacji: pełne
    kilkadziesiąt dni to tysiące zapytań do bazy i na serverless wypada z limitu
    czasu. `done=False` w odpowiedzi znaczy „wołaj jeszcze raz" — kursor wznowień
    to ostatni snapshot sprzed marginesu, więc kolejne wywołania są odporne na
    powtórki i przerwany proces."""
    now = datetime.now(timezone.utc)
    koniec = now - BACKFILL_MARGIN
    ostatni = (session.query(func.max(EquitySnapshot.ts))
               .filter(EquitySnapshot.account_id == acc.id,
                       EquitySnapshot.ts < koniec.replace(tzinfo=None)).scalar())
    if ostatni is None:
        # Czysta karta przed cofnięciem zegara: żywy tick (lazy-tick z ruchu
        # publicznego) potrafi między startem bota a pierwszym backfillem otworzyć
        # pozycję z DZISIEJSZĄ datą i zamknięciem w realnej przyszłości — replay
        # nigdy jej nie domknie i przesiedzi całą dogrywkę bez jednej transakcji.
        session.query(Trade).filter(Trade.account_id == acc.id).delete()
        session.query(EquitySnapshot).filter(EquitySnapshot.account_id == acc.id).delete()
        acc.balance = acc.equity = acc.peak_equity = acc.initial_balance
        acc.day_start_equity = acc.day_start_balance = acc.initial_balance
        acc.open_pnl = 0.0
        acc.best_day_profit = 0.0
        acc.trading_days_count = 0
        acc.last_counted_trading_day = ""
        start = now - timedelta(days=days)
        acc.bot_started_at = start
        acc.created_at = start
        acc.started_at = start
        acc.day_key = server_day_key(start + timedelta(hours=settings.server_utc_offset_hours))
        kursor = start
    else:
        kursor = ostatni if ostatni.tzinfo else ostatni.replace(tzinfo=timezone.utc)
    stop = min(kursor + timedelta(days=chunk_days), koniec)

    rng = random.Random(f"{acc.bot_seed}:backfill:{kursor.isoformat()}")
    t, kroki, padlo = kursor, 0, False
    while True:
        # Krok z jitterem: sztywna siatka co równe 20 minut wyglądałaby jak
        # stempel generatora — żywe ticki przychodzą z ruchem i cronem, nierówno.
        t = t + timedelta(minutes=step_min * rng.uniform(0.7, 1.3))
        if t > stop:
            break
        snap = tradebot.tick(session, acc, t)
        srv = t + timedelta(hours=settings.server_utc_offset_hours)
        started = acc.started_at if acc.started_at.tzinfo else acc.started_at.replace(tzinfo=timezone.utc)
        cfg = rules.config_from_account(acc)
        rt = _runtime_from(acc)
        tick = EquityTick(
            equity=snap.equity, balance=snap.balance, open_pnl=snap.open_pnl,
            volume_lots=snap.volume_lots, volume_known=snap.volume_known,
            day_key=server_day_key(srv), has_open_position=snap.has_open_position,
            days_elapsed=(srv - started).days,
        )
        res = rules.evaluate(cfg, rt, tick)
        _write_runtime(acc, rt)
        session.add(EquitySnapshot(account_id=acc.id, ts=t.replace(tzinfo=None),
                                   balance=snap.balance, equity=snap.equity,
                                   open_pnl=snap.open_pnl, day_key=tick.day_key))
        kroki += 1
        for btype, detail, eq in res.breaches:
            session.add(Breach(account_id=acc.id, type=btype.value, detail=detail,
                               equity_at_breach=eq))
        if res.failed:
            acc.closed_at = t
            padlo = True
            break

    done = padlo or stop >= koniec
    zamek = session.get(AppSetting, BACKFILL_LOCK_KEY)
    if zamek is None:
        zamek = AppSetting(key=BACKFILL_LOCK_KEY, value="")
        session.add(zamek)
    zamek.value = "" if done else f"{acc.id}:{time.time()}"
    session.commit()
    transakcje = (session.query(func.count(Trade.id))
                  .filter(Trade.account_id == acc.id).scalar() or 0)
    return {"done": done, "failed": padlo, "steps": kroki, "trades": int(transakcje),
            "simulated_to": min(t, stop).isoformat(),
            "balance": round(acc.balance, 2),
            "profit_pct": round((acc.balance - acc.initial_balance)
                                / acc.initial_balance * 100, 2)}


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
        w_dogrywce = _backfill_locked_id(session)
        bledy = 0
        for acc in accounts:
            if acc.id == w_dogrywce:
                continue
            # Awaria jednego konta (feed, broker, baza) nie może zatrzymać
            # przetwarzania pozostałych — na serverless kolejna szansa jest
            # dopiero za dobę. Rollback czyści sesję z niedokończonego stanu.
            try:
                await process_account(session, acc, _feed)
            except Exception as e:
                session.rollback()
                bledy += 1
                print(f"[poller] konto {acc.login}: przebieg nieudany: {e}", flush=True)
        wynik = {"accounts": len(accounts), "feed": settings.feed}
        if bledy:
            wynik["errors"] = bledy
        return wynik
    finally:
        session.close()


def prune_equity_snapshots(session, dni_pelne: int = 30, partia: int = 500,
                           budzet_s: float = 10.0) -> int:
    """Retencja wykresu equity: dni starsze niż `dni_pelne` chudną do jednego
    (ostatniego) snapshotu na (konto, dzień) — wykres dzienny tego nie widzi,
    a tabela przestaje rosnąć bez końca.

    Kasowanie idzie partiami z budżetem czasu i commitem po każdej partii:
    na serverless (Vercel, limit 60 s) przerwany przebieg nie traci pracy,
    resztę dokończy następny cron. Woła to handler /api/tick PO tick_once —
    celowo nie sam tick_once, bo jego odpala też lazy-tick z ruchu strony.
    """
    # Naiwny UTC jak kolumna `ts` — aware vs naive wywraca porównanie na Postgresie.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dni_pelne)
    keepers = (select(func.max(EquitySnapshot.id))
               .where(EquitySnapshot.ts < cutoff)
               .group_by(EquitySnapshot.account_id, EquitySnapshot.day_key))
    start = time.monotonic()
    usuniete = 0
    while time.monotonic() - start < budzet_s:
        ids = [i for (i,) in session.query(EquitySnapshot.id)
               .filter(EquitySnapshot.ts < cutoff, EquitySnapshot.id.not_in(keepers))
               .limit(partia).all()]
        if not ids:
            break
        session.query(EquitySnapshot).filter(EquitySnapshot.id.in_(ids)) \
            .delete(synchronize_session=False)
        session.commit()
        usuniete += len(ids)
        if len(ids) < partia:
            break
    return usuniete


async def provision_kickoff() -> None:
    """Natychmiastowa próba uzbrojenia kont czekających w 'provisioning'.

    Wołane zaraz po zaksięgowaniu płatności (mark-paid, webhook Stripe) — na
    serverless najbliższy pełny tick może przyjść dopiero z dziennego crona,
    a mail z poświadczeniami wychodzi dopiero przy przydziale rachunku."""
    global _feed
    if _feed is None:
        _feed = make_feed()
    await provisioning.provision_pending(SessionLocal, _feed)


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
