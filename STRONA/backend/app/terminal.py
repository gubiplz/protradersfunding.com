"""Terminal PTF — zlecenia i pozycje z zakładki Trade.

To jest księga konta challenge'u: pozycja otwarta tutaj trafia do tej samej
tabeli `trades` co historia z dashboardu, a zysk i strata zmieniają to samo
saldo, które liczą reguły (daily loss, max drawdown, target). Do terminala
loguje się wyłącznie kontem PTF (ta sama sesja co portal) — danymi MT5 nie.

Rozliczenie idzie po cenach Hyperliquid (patrz `markets.py`):
  - zlecenie rynkowe przechodzi przez księgę zleceń (VWAP), z limitem
    poślizgu i opłatą od wartości;
  - marża jak w MT5: dźwignia jest z góry ustalona dla rynku (trader jej nie
    wybiera), marża jest wspólna dla całego konta, a gdy equity spadnie do
    połowy użytej marży, pozycje zamyka stop-out (bez ceny likwidacji per pozycja);
  - limit, stop, TP i SL rozstrzyga `sync()`: przegląda świece 1m
    od ostatniego przeglądu, więc nic nie ucieka, gdy nikt nie patrzy;
  - pozycje są netowane per symbol, jak na giełdach perp: zlecenie w drugą
    stronę zmniejsza, zamyka albo odwraca pozycję.

Weekend: rynki inne niż krypto stoją (sobota–niedziela czasu serwera), więc
nie przyjmujemy na nie zleceń, nie wyzwalamy TP/SL i wyceniamy je po cenie
z zamknięcia tygodnia. Krypto w weekend tylko z dodatkiem Weekend Trading
(ta sama zasada co u Trade BOT-a).
"""
from __future__ import annotations

import asyncio
import copy
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm.exc import StaleDataError

from . import auth, catalog, markets, rules, tradebot
from .config import get_settings
from .db import SessionLocal
from .feed import MarketSnapshot
from .markets import Market
from .models import Account, AppSetting, PoolAccount, TerminalCandle, TerminalLogin, TerminalOrder, Trade, Trader

settings = get_settings()

FEE_PER_LOT_USD = 9         # $9 / lot / side (standardowy lot FX ≈ $100k)
FEE_PCT = 0.00009           # 0,009% wartości przy otwarciu i przy zamknięciu (= $9/lot)
MAX_SLIPPAGE = 0.01         # domyślny limit poślizgu (1%)
STOP_OUT = 0.5              # stop-out: equity ≤ 50% użytej marży (poziom jak u brokerów MT5)
CANDLE_GAP_S = 50           # dłuższa przerwa od przeglądu = przegląd świecami 1m
MAX_WINDOW_MS = 4800 * 60_000   # Hyperliquid oddaje do 5000 świec
EVAL_EVERY_S = 30           # reguły ze snapshotem equity najwyżej co tyle (poza akcjami)
EPS = 1e-9
TRADABLE = ("active", "funded")

router = APIRouter(prefix="/api/terminal")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(dt: datetime | None) -> int:
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _dzien_serwera(ms: int) -> str:
    """Doba serwera MT5 dla chwili `ms` — ta sama co `poller.server_day_key`."""
    return (_dt(ms) + timedelta(hours=settings.server_utc_offset_hours)).strftime("%Y-%m-%d")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# --------------------------------------------------------------------------- #
#  Godziny rynku                                                               #
# --------------------------------------------------------------------------- #
def market_closed(m: Market, acc: Account, now: datetime | None = None) -> str | None:
    """Powód, dla którego TERAZ nie da się otworzyć pozycji, albo None."""
    if not tradebot.is_weekend(now):
        return None
    if m.cls != "crypto":
        return "Market closed for the weekend. It reopens Monday 00:00 server time."
    if not bool(getattr(acc, "weekend_trading", False)):
        return "Weekend trading needs the Weekend Trading add-on on this account."
    return None


_ZAMROZONE: dict[tuple[str, int], float] = {}
_ZAMROZONE_BLAD: dict[tuple[str, int], float] = {}   # klucz → kiedy (monotonic) ostatnio się nie udało


def _cena_weekendowa(coin: str, now: datetime) -> float | None:
    """Cena z zamknięcia tygodnia — po niej wyceniamy rynki inne niż krypto
    w weekend (Hyperliquid notuje je 24/7, prawdziwy rynek stoi)."""
    koniec = _ms(tradebot._week_close(now))
    if koniec > _ms(now):          # zamknięcie tygodnia jeszcze przed nami
        koniec -= 7 * 86_400_000
    klucz = (coin, koniec)
    if klucz not in _ZAMROZONE:
        # Nieudane pobranie pamiętamy przez minutę: przegląd pyta o tę cenę
        # w każdej minucie okna, a bez tego awaria to tysiące zapytań w jednym requeście.
        if time.monotonic() - _ZAMROZONE_BLAD.get(klucz, -1e9) < 60:
            return None
        try:
            sw = markets.candles_1m(coin, koniec - 30 * 60_000, koniec - 1)
        except Exception:
            sw = None
        if not sw:
            _ZAMROZONE_BLAD[klucz] = time.monotonic()
            return None
        _ZAMROZONE[klucz] = sw[-1]["c"]
    return _ZAMROZONE[klucz]


def _zamrozony(m: Market, t_ms: int) -> bool:
    return m.cls != "crypto" and tradebot.is_weekend(_dt(t_ms))


# --------------------------------------------------------------------------- #
#  Wycena i marża                                                              #
# --------------------------------------------------------------------------- #
def stop_out_due(fig: dict) -> bool:
    """Czy konto wpadło w stop-out: equity nie pokrywa już połowy użytej marży."""
    return fig["margin_used"] > 0 and fig["equity"] <= STOP_OUT * fig["margin_used"]


def _open_positions(session, acc: Account) -> list[Trade]:
    return (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "open",
                    or_(Trade.source == "terminal", Trade.venue == tradebot.OGON))
            .order_by(Trade.id).all())


def _open_orders(session, acc: Account) -> list[TerminalOrder]:
    return (session.query(TerminalOrder)
            .filter(TerminalOrder.account_id == acc.id, TerminalOrder.status == "open")
            .order_by(TerminalOrder.id).all())


def _inne_otwarte(session, acc: Account) -> tuple[float, float]:
    """(floating, loty) pozycji spoza terminala (Trade BOT): floating siedzi w equity,
    loty w tym samym limicie ekspozycji konta."""
    # Plan Trade BOT-a na żywych cenach (venue='plan') nie jest pozycją — transakcja
    # powstaje przy zamknięciu; jego lotów nie liczymy, klient ich nie widzi.
    rows = (session.query(Trade.pnl, Trade.lots)
            .filter(Trade.account_id == acc.id, Trade.status == "open", Trade.source != "terminal",
                    or_(Trade.venue.is_(None), Trade.venue.notin_(("plan", tradebot.OGON))))
            .all())
    return sum(float(p or 0.0) for p, _ in rows), sum(float(lt or 0.0) for _, lt in rows)


def _inne_otwarte_pnl(session, acc: Account) -> float:
    return _inne_otwarte(session, acc)[0]


def _marks(session, acc: Account, poz: list[Trade], now: datetime) -> dict[str, float]:
    """Cena wyceny per symbol: mid Hyperliquid, w weekend zamrożona dla nie-krypto."""
    if not poz:
        return {}
    syms = {t.symbol for t in poz}
    rynki = [markets.get(s) for s in syms if markets.get(s)]
    try:
        mids = markets.mids({m.hl for m in rynki})
    except Exception:
        mids = {}
    out: dict[str, float] = {}
    for m in rynki:
        px = None
        if _zamrozony(m, _ms(now)):
            px = _cena_weekendowa(m.hl, now)
        if px is None:
            px = mids.get(m.hl)
        if px is not None:
            out[m.sym] = px
    return out


def account_figures(session, acc: Account, poz: list[Trade], marks: dict[str, float]) -> dict:
    floating = 0.0
    margin = 0.0
    lots = 0.0
    for t in poz:
        m = markets.get(t.symbol)
        px = marks.get(t.symbol)
        if m is None:
            continue
        if px is not None:
            t.pnl = round(markets.pnl_usd(m, t.side, t.qty or 0.0, t.open_price, px), 2)
        floating += float(t.pnl or 0.0)
        margin += float(t.margin or 0.0)
        lots += markets.lots(m, t.qty or 0.0)
    inne, inne_loty = _inne_otwarte(session, acc)
    balance = float(acc.balance or acc.initial_balance or 0.0)
    equity = balance + floating + inne
    return {"balance": round(balance, 2), "floating": round(floating, 2), "other_floating": round(inne, 2),
            "equity": round(equity, 2), "margin_used": round(margin, 2),
            "available": round(max(0.0, equity - margin), 2), "open_lots": round(lots, 4),
            "other_lots": round(inne_loty, 4)}


# --------------------------------------------------------------------------- #
#  Wykonanie                                                                   #
# --------------------------------------------------------------------------- #
def _vwap(m: Market, side: str, qty: float, max_slip: float) -> tuple[float, float, float]:
    """Cena wypełnienia z księgi Hyperliquid: (vwap, mid, poślizg)."""
    try:
        bids, asks = markets.book(m.hl)
    except Exception:
        raise HTTPException(503, "Price feed unavailable. Try again in a moment.")
    if not bids or not asks:
        raise HTTPException(503, "No price for this market right now.")
    mid = (bids[0][0] + asks[0][0]) / 2
    poziomy = asks if side == "buy" else bids
    zostalo, koszt = qty, 0.0
    for px, sz in poziomy:
        bierz = min(zostalo, sz)
        koszt += bierz * px
        zostalo -= bierz
        if zostalo <= EPS:
            break
    if zostalo > EPS:                       # księga płytsza niż zlecenie: reszta po najgorszym poziomie
        koszt += zostalo * poziomy[-1][0]
    vwap = koszt / qty
    slip = abs(vwap - mid) / mid if mid else 0.0
    if slip > max_slip + 1e-12:
        raise HTTPException(409, f"Price impact {slip * 100:.2f}% is above your max slippage "
                                 f"{max_slip * 100:.2f}%. Lower the size or raise the slippage limit.")
    return vwap, mid, slip


def _close_part(session, acc: Account, pos: Trade, qty: float, px: float, reason: str,
                when: datetime) -> Trade:
    """Zamyka `qty` z pozycji po cenie `px`. Zwraca wiersz z zamkniętą częścią."""
    m = markets.get(pos.symbol)
    qty = min(qty, pos.qty or 0.0)
    brutto = markets.pnl_usd(m, pos.side, qty, pos.open_price, px)
    oplata = markets.notional_usd(m, qty, px) * FEE_PCT
    udzial = qty / pos.qty if pos.qty else 1.0
    acc.balance = round(float(acc.balance or 0.0) + brutto - oplata, 2)
    if udzial >= 1 - 1e-9:
        pos.close_price = px
        pos.closed_at = when
        pos.status = "closed"
        pos.close_reason = reason
        pos.fee = round(float(pos.fee or 0.0) + oplata, 4)
        pos.pnl = round(brutto - pos.fee, 2)
        if pos.venue == tradebot.OGON:
            pos.venue = "hyperliquid"
        session.flush()     # bez autoflush: kolejne zapytania o otwarte pozycje muszą to widzieć
        return pos
    fee_otwarcia = float(pos.fee or 0.0) * udzial
    czesc = Trade(
        account_id=acc.id, symbol=pos.symbol, side=pos.side, lots=markets.lots(m, qty),
        open_price=pos.open_price, close_price=px, opened_at=pos.opened_at, closed_at=when,
        status="closed", source="terminal", venue=pos.venue, qty=qty, leverage=pos.leverage,
        margin=round(float(pos.margin or 0.0) * udzial, 2), tp=pos.tp, sl=pos.sl,
        liq_price=pos.liq_price, fee=round(fee_otwarcia + oplata, 4), slippage=pos.slippage,
        close_reason=reason, pnl=round(brutto - fee_otwarcia - oplata, 2),
    )
    session.add(czesc)
    session.flush()
    pos.qty = (pos.qty or 0.0) - qty
    pos.lots = markets.lots(m, pos.qty)
    pos.margin = round(float(pos.margin or 0.0) * (1 - udzial), 2)
    pos.fee = round(float(pos.fee or 0.0) - fee_otwarcia, 4)
    session.flush()
    return czesc


def _fill(session, acc: Account, m: Market, side: str, qty: float, px: float, lev: float,
          tp: float | None, sl: float | None, reduce_only: bool, when: datetime,
          slip: float = 0.0) -> Trade | None:
    """Wypełnia zlecenie po `px`: zmniejsza/zamyka/odwraca albo otwiera/dokłada.
    Zwraca pozycję, która po wypełnieniu jest otwarta (albo None)."""
    pos = next((t for t in _open_positions(session, acc) if t.symbol == m.sym), None)
    zostalo = qty
    if pos is not None and pos.side != side:
        zamknij = min(zostalo, pos.qty or 0.0)
        _close_part(session, acc, pos, zamknij, px, "reduce" if zamknij < (pos.qty or 0) - EPS else "close", when)
        zostalo -= zamknij
        if pos.status == "closed":
            pos = None
        if zostalo <= EPS or reduce_only:
            return pos
    elif reduce_only:
        raise HTTPException(409, "Reduce-only order would open or increase a position.")
    if zostalo <= EPS:
        return pos
    wartosc = markets.notional_usd(m, zostalo, px)
    oplata = wartosc * FEE_PCT
    acc.balance = round(float(acc.balance or 0.0) - oplata, 2)
    if pos is None:
        pos = Trade(
            account_id=acc.id, symbol=m.sym, side=side, lots=markets.lots(m, zostalo),
            open_price=px, pnl=0.0, opened_at=when, status="open", source="terminal",
            venue="hyperliquid", qty=zostalo, leverage=lev, margin=round(wartosc / lev, 2),
            tp=tp, sl=sl, liq_price=None, fee=round(oplata, 4),
            slippage=round(slip * 100, 4), tpsl_at=when if (tp or sl) else None,
        )
        session.add(pos)
        session.flush()     # sesja bez autoflush: następne wypełnienie musi tę pozycję zobaczyć
    else:
        nowa = (pos.qty or 0.0) + zostalo
        pos.open_price = (pos.open_price * (pos.qty or 0.0) + px * zostalo) / nowa
        pos.qty = nowa
        pos.lots = markets.lots(m, nowa)
        pos.margin = round(float(pos.margin or 0.0) + wartosc / (pos.leverage or lev), 2)
        pos.fee = round(float(pos.fee or 0.0) + oplata, 4)
        if tp is not None or sl is not None:
            pos.tp = tp if tp is not None else pos.tp
            pos.sl = sl if sl is not None else pos.sl
            pos.tpsl_at = when
    return pos


def _check_open(session, acc: Account, m: Market, side: str, qty: float, px: float, lev: float) -> None:
    """Czy konto udźwignie nową ekspozycję: marża i limit lotów."""
    poz = _open_positions(session, acc)
    istn = next((t for t in poz if t.symbol == m.sym), None)
    przyrost = qty
    if istn is not None and istn.side != side:
        przyrost = max(0.0, qty - (istn.qty or 0.0))
    if przyrost <= EPS:
        return
    marks = _marks(session, acc, poz, _now())
    fig = account_figures(session, acc, poz, marks)
    wolne, loty = fig["available"], fig["open_lots"] + fig["other_lots"]   # limit wspólny z Trade BOT-em
    if istn is not None and istn.side != side:
        # Odwrócenie: stara pozycja najpierw się zamyka, więc jej marża i loty
        # się zwalniają — inaczej 6 lotów long → 6 short odbijało się od limitu.
        wolne += float(istn.margin or 0.0)      # jej P&L już siedzi w equity
        loty = max(0.0, loty - markets.lots(m, istn.qty or 0.0))
    potrzeba = markets.notional_usd(m, przyrost, px) / lev * (1 + FEE_PCT * lev)
    if potrzeba > wolne + 0.01:
        raise HTTPException(409, f"Not enough available margin: this order needs ${potrzeba:,.2f}, "
                                 f"you have ${max(0.0, wolne):,.2f}.")
    limit = float(acc.max_lots or catalog.max_lots_for(acc.initial_balance or 0.0))
    if limit > 0 and loty + markets.lots(m, przyrost) > limit + 1e-9:
        raise HTTPException(409, f"Exposure cap: {limit:g} lots open in total on this account "
                                 f"(you have {loty:.2f}).")


MIN_LOTS = 0.01            # najmniejsze zlecenie i krok wolumenu, jak w MT5


def _wolumen_ok(m: Market, qty: float, pozostaje: float | None = None) -> None:
    """Zlecenie co najmniej 0,01 lota, w krokach co 0,01 lota. `pozostaje` = ile lotów
    zostanie z pozycji po częściowym zamknięciu (też co najmniej 0,01 albo zero)."""
    loty = markets.lots(m, qty)
    if loty < MIN_LOTS - 1e-9:
        raise HTTPException(400, f"Minimum size is {MIN_LOTS:g} lots ({MIN_LOTS * m.contract:g} {m.base}).")
    if abs(loty / MIN_LOTS - round(loty / MIN_LOTS)) > 1e-6:
        raise HTTPException(400, f"Size goes in steps of {MIN_LOTS:g} lots.")
    if pozostaje is not None and 1e-9 < pozostaje < MIN_LOTS - 1e-9:
        raise HTTPException(400, f"At least {MIN_LOTS:g} lots must stay open — close the whole position instead.")


def _tp_sl_ok(side: str, ref: float, tp: float | None, sl: float | None) -> None:
    if tp is not None and tp > 0 and ((side == "buy" and tp <= ref) or (side == "sell" and tp >= ref)):
        raise HTTPException(400, "Take profit must be above the entry for a long and below it for a short.")
    if sl is not None and sl > 0 and ((side == "buy" and sl >= ref) or (side == "sell" and sl <= ref)):
        raise HTTPException(400, "Stop loss must be below the entry for a long and above it for a short.")


# --------------------------------------------------------------------------- #
#  Przegląd: limity, stopy, TP/SL, likwidacje                                  #
# --------------------------------------------------------------------------- #
def _zdarzenia_pozycji(pos: Trade, m: Market, c: dict) -> tuple[float, str] | None:
    """Czy świeca `c` zamyka pozycję — najpierw scenariusz niekorzystny."""
    o, h, low = c["o"], c["h"], c["l"]
    if pos.side == "buy":
        if pos.sl and low <= pos.sl:
            return min(pos.sl, o), "sl"
        if pos.liq_price and low <= pos.liq_price:
            return pos.liq_price, "liquidation"
        if pos.tp and h >= pos.tp:
            return max(pos.tp, o), "tp"
    else:
        if pos.sl and h >= pos.sl:
            return max(pos.sl, o), "sl"
        if pos.liq_price and h >= pos.liq_price:
            return pos.liq_price, "liquidation"
        if pos.tp and low <= pos.tp:
            return min(pos.tp, o), "tp"
    return None


def _podciagnij_sl(pos: Trade, c: dict) -> None:
    """Trailing stop: po sprawdzeniu świecy SL idzie za najlepszą ceną, nigdy wstecz.
    Dopiero po sprawdzeniu, bo w świecy nie wiemy, czy szczyt był przed dołkiem."""
    if not pos.trail:
        return
    if pos.side == "buy":
        nowy = c["h"] - pos.trail
        if not pos.sl or nowy > pos.sl:
            pos.sl = nowy
    else:
        nowy = c["l"] + pos.trail
        if not pos.sl or nowy < pos.sl:
            pos.sl = nowy


def _wyzwolenie_zlecenia(z: TerminalOrder, c: dict) -> float | None:
    o, h, low = c["o"], c["h"], c["l"]
    if z.kind == "limit":
        if z.side == "buy" and low <= z.price:
            return min(z.price, o)
        if z.side == "sell" and h >= z.price:
            return max(z.price, o)
    elif z.kind == "stop":
        if z.side == "buy" and h >= z.price:
            return max(z.price, o)
        if z.side == "sell" and low <= z.price:
            return min(z.price, o)
    return None


def _wypelnij_oczekujace(session, acc: Account, z: TerminalOrder, px: float, when: datetime) -> None:
    m = markets.get(z.symbol)
    try:
        if not z.reduce_only:
            _check_open(session, acc, m, z.side, z.qty, px, z.leverage or 1.0)
        pos = _fill(session, acc, m, z.side, z.qty, px, z.leverage or 1.0, z.tp, z.sl, z.reduce_only, when)
    except HTTPException as e:
        z.status, z.done_at, z.note = "rejected", when, str(e.detail)[:160]
        return
    z.status, z.done_at, z.fill_price = "filled", when, px
    z.trade_id = pos.id if pos is not None else None


def sync(session, acc: Account, now: datetime | None = None,
         dokladnie: bool = False) -> tuple[list[Trade], dict[str, float], dict[str, dict]]:
    """Rozstrzyga wszystko, co wydarzyło się od ostatniego przeglądu.

    Zwraca (otwarte pozycje, ceny wyceny, doby okna). Doba serwera → najniższe
    equity (`low`), saldo na końcu (`end_bal`), czy była otwarta pozycja (`pos`)
    i — gdy doba zaczęła się w oknie — equity i saldo z północy (`start_eq`,
    `start_bal`). Najniższe equity liczymy z ekstremów świec po stronie
    niekorzystnej — reguły sprawdzają je osobno, bo dzienna strata liczy się
    w każdej minucie, a nie tylko w chwili, w której ktoś akurat otworzył stronę.

    Świece 1m idą od `terminal_candle_at` (ostatni PEŁNY przegląd świecami), nie
    od ostatniego midu: przy odświeżaniu co kilkanaście sekund knot między
    odczytami inaczej nigdy nie trafiłby na SL, limit ani dołek equity. Znacznik
    rusza się tylko wtedy, gdy świece wszystkich rynków przyszły — awaria
    Hyperliquid nie gubi okna, następny przegląd je powtórzy. `dokladnie`
    (akcje tradera) pobiera świece zawsze: zanim ktoś przesunie SL albo zamknie
    pozycję, sprawdzamy, czy rynek już jej nie zamknął.
    """
    now = now or _now()
    poz = _open_positions(session, acc)
    zlec = _open_orders(session, acc)
    if not poz and not zlec:
        acc.terminal_open = False
        acc.terminal_sync_at = acc.terminal_candle_at = now
        return [], {}, {}
    teraz_ms = _ms(now)
    pocz = acc.terminal_sync_at or min([t.opened_at for t in poz] + [z.created_at for z in zlec])
    od_swiec = min(_ms(acc.terminal_candle_at or pocz), _ms(pocz))
    start_ms = max(od_swiec - 60_000, teraz_ms - MAX_WINDOW_MS)
    coins = {markets.get(s).hl for s in {t.symbol for t in poz} | {z.symbol for z in zlec} if markets.get(s)}

    swiece: dict[str, dict[int, dict]] = {}
    swiece_ok = dokladnie or teraz_ms - od_swiec > CANDLE_GAP_S * 1000
    if swiece_ok:
        for coin in coins:
            try:
                swiece[coin] = {c["t"]: c for c in markets.candles_1m(coin, start_ms, teraz_ms)}
            except Exception as e:  # pragma: no cover - siec
                swiece_ok = False
                print(f"[terminal] świece {coin}: {e}", flush=True)
    try:
        mids = markets.mids(coins)
    except Exception:
        mids = {}
    # Bieżąca chwila jako ostatnia „świeca" z samym midem — łapie to, co stało
    # się od ostatniej pełnej minuty.
    for coin, px in mids.items():
        swiece.setdefault(coin, {})[teraz_ms - 1] = {"t": teraz_ms - 1, "o": px, "h": px, "l": px, "c": px}

    # Cena startowa każdego rynku: otwarcie pierwszej świecy okna. Bez niej
    # pozycja na rynku, który akurat nie miał świecy w danej minucie, wypadałaby
    # z equity — i dołek wychodził niższy (albo wyższy), niż był naprawdę.
    ostatnia: dict[str, float] = {coin: sw[min(sw)]["o"] for coin, sw in swiece.items() if sw}
    inne = _inne_otwarte_pnl(session, acc)
    weekend_bez_dodatku = not bool(getattr(acc, "weekend_trading", False))

    def _equity(t: int, przy_otwarciu: bool) -> tuple[float, bool]:
        """Equity w minucie `t`: na otwarciu albo na najgorszym ekstremum.
        Drugi element mówi, czy w tej minucie była jakaś otwarta pozycja."""
        ruch, byla = 0.0, False
        for pos in poz:
            m = markets.get(pos.symbol)
            # Pozycja otwarta w tej minucie albo później: ekstremum mogło być
            # przed wejściem, więc nie liczymy go jako jej straty.
            if t != teraz_ms - 1 and _ms(pos.opened_at) >= t:
                continue
            byla = True
            c = swiece.get(m.hl, {}).get(t) if m else None
            if m is None or (c is None and m.hl not in ostatnia):
                ruch += float(pos.pnl or 0.0)     # brak ceny z okna: ostatnia znana wycena
                continue
            if _zamrozony(m, t):
                px = _cena_weekendowa(m.hl, now) or ostatnia.get(m.hl)
            elif c is not None:
                px = c["o"] if przy_otwarciu else (c["l"] if pos.side == "buy" else c["h"])
            else:
                px = ostatnia[m.hl]
            if px:
                ruch += markets.pnl_usd(m, pos.side, pos.qty or 0.0, pos.open_price, px)
        return float(acc.balance or 0.0) + ruch + inne, byla

    # Okno osobno dla każdej doby serwera: dzienna strata liczy się od equity
    # z jej początku, więc dołek sprzed północy nie może trafić do nowej doby.
    dni: dict[str, dict] = {}
    poprzedni_dzien: str | None = None
    # Listy w pamięci: zapytanie do bazy tylko po zdarzeniu (okno ma do 4800 minut).
    zl_lista = zlec
    for t in sorted({t for sw in swiece.values() for t in sw}):
        kiedy = min(_dt(t + 59_999), now) if t != teraz_ms - 1 else now
        dzien = _dzien_serwera(t)
        if poprzedni_dzien is not None and dzien != poprzedni_dzien:
            # Doba zaczęła się w tym oknie: jej baseline to equity z otwarcia
            # pierwszej minuty (jak u brokera o północy), a nie z chwili, w której
            # ktoś następnym razem otworzy stronę.
            dni[dzien] = {"start_eq": _equity(t, True)[0], "start_bal": float(acc.balance or 0.0)}
        poprzedni_dzien = dzien
        zmiana = False
        for coin, sw in swiece.items():
            c = sw.get(t)
            if c is None:
                continue
            m = markets.BY_COIN.get(coin)
            if m is None:
                continue
            ostatnia[coin] = c["c"]
            if _zamrozony(m, t):
                continue
            # Krypto w weekend tylko z dodatkiem: oczekujące zlecenie, które coś
            # otwiera, czeka do poniedziałku (tak jak przy składaniu zlecenia).
            tylko_redukcja = m.cls == "crypto" and weekend_bez_dodatku and tradebot.is_weekend(_dt(t))
            for z in zl_lista:
                if z.status == "open" and z.symbol == m.sym and _ms(z.created_at) < t:
                    if tylko_redukcja and not z.reduce_only:
                        continue
                    px = _wyzwolenie_zlecenia(z, c)
                    if px is not None:
                        _wypelnij_oczekujace(session, acc, z, px, kiedy)
                        zmiana = True
                        poz = _open_positions(session, acc)
            for pos in poz:
                if pos.status != "open" or pos.symbol != m.sym or _ms(pos.opened_at) >= t:
                    continue
                zd = _zdarzenia_pozycji(pos, m, c)
                ustawione_wczesniej = _ms(pos.tpsl_at or pos.opened_at) < t
                if zd is None:
                    if ustawione_wczesniej:
                        _podciagnij_sl(pos, c)
                    continue
                cena, powod = zd
                if powod in ("tp", "sl") and not ustawione_wczesniej:
                    continue
                _close_part(session, acc, pos, pos.qty or 0.0, cena, powod, kiedy)
                zmiana = True
        if zmiana:
            session.flush()
            poz = _open_positions(session, acc)
        # equity na najgorszym ekstremum tej minuty
        eq, byla = _equity(t, False)
        d = dni.setdefault(dzien, {})
        d["low"] = min(d.get("low", eq), eq)
        d["end_bal"] = float(acc.balance or 0.0)
        d["pos"] = d.get("pos", False) or byla

    poz = _open_positions(session, acc)
    acc.terminal_open = bool(poz or _open_orders(session, acc))
    acc.terminal_sync_at = now
    if swiece_ok:
        acc.terminal_candle_at = now
    marks = _marks(session, acc, poz, now)
    if poz and stop_out_due(account_figures(session, acc, poz, marks)):
        # Marża wspólna dla konta: zamykamy wszystko po bieżących cenach (MT5
        # zamyka po kolei od najbardziej stratnej — przy jednym progu to samo).
        flatten(session, acc, "stop_out", cancel_orders=False)
        poz, marks = [], {}
    # Doby okna zostają przy obiekcie do końca requestu: reguły (`snapshot_for`)
    # robią własny przegląd, który widzi już tylko ostatnie sekundy — bez tego
    # dołek z pierwszego przeglądu by przepadł.
    zebrane = acc.__dict__.setdefault("_terminal_dni", {})
    for dzien, d in dni.items():
        s = zebrane.setdefault(dzien, {})
        for k in ("start_eq", "start_bal"):       # początek doby: z wcześniejszego przeglądu
            if k in d:
                s.setdefault(k, d[k])
        if "low" in d:
            s["low"] = min(s.get("low", d["low"]), d["low"])
            s["end_bal"] = d["end_bal"]
            s["pos"] = s.get("pos", False) or d["pos"]
    return poz, marks, dni


def snapshot_for(session, acc: Account, bot_snap: MarketSnapshot | None) -> tuple[MarketSnapshot, dict | None]:
    """Snapshot equity dla pollera: księga terminala (+ bot, jeśli konto go ma).

    Drugi element to doby okna (patrz `sync`) do `evaluate_window`."""
    przed = float(acc.balance or 0.0)
    poz, marks, _ = sync(session, acc)
    dni = acc.__dict__.pop("_terminal_dni", None) or None
    dzis = dni.get(_dzien_serwera(_ms(_now()))) if dni else None
    fig = account_figures(session, acc, poz, marks)
    lots = fig["open_lots"]
    # Pozycja zamknięta przez TP/SL pod nieobecność też robi z tej doby dzień handlowy.
    byla = bool(poz) or bool(dzis and dzis.get("pos"))
    if bot_snap is not None:
        # Bot policzył saldo z `acc.balance` sprzed przeglądu (plus swoje
        # zamknięcie); dokładamy to, co terminal zrealizował w tym przeglądzie,
        # i floating terminala.
        saldo = round(bot_snap.balance + float(acc.balance or 0.0) - przed, 2)
        if dzis and "low" in dzis:
            # Dołek liczył floating bota z jego wiersza (już po ticku bota),
            # brakuje mu tylko zamknięcia bota, którego nie ma jeszcze w saldzie.
            dzis["low"] += saldo - float(acc.balance or 0.0)
        snap = MarketSnapshot(
            balance=saldo, equity=round(saldo + bot_snap.open_pnl + fig["floating"], 2),
            open_pnl=round(bot_snap.open_pnl + fig["floating"], 2),
            has_open_position=bot_snap.has_open_position or byla,
            volume_lots=round(bot_snap.volume_lots + lots, 4), volume_known=True)
    else:
        snap = MarketSnapshot(balance=fig["balance"], equity=fig["equity"], open_pnl=fig["floating"],
                              has_open_position=byla, volume_lots=lots, volume_known=True)
    return snap, dni


def _cena_z_wyceny(pos: Trade) -> float:
    """Cena, przy której pozycja miała ostatnio zapisany P&L (odwrotność `markets.pnl_usd`)."""
    m = markets.get(pos.symbol)
    q, e, pnl = float(pos.qty or 0.0), float(pos.open_price or 0.0), float(pos.pnl or 0.0)
    if m is None or q <= 0 or not e:
        return e
    if m.quote_inverse:
        mian = q - pnl if pos.side == "buy" else q + pnl
        return q * e / mian if mian > 0 else e
    return e + pnl / q if pos.side == "buy" else e - pnl / q


def flatten(session, acc: Account, reason: str, cancel_orders: bool = True) -> int:
    """Zamyka wszystko po bieżących cenach; przy breachu anuluje też zlecenia
    (stop-out ich nie rusza — konto dalej handluje)."""
    now = _now()
    poz = _open_positions(session, acc)
    marks = _marks(session, acc, poz, now)
    n = 0
    for pos in poz:
        px = marks.get(pos.symbol)
        if px is None:
            # Bez ceny z rynku zamykamy po ostatniej wycenie pozycji: na koncie
            # po breachu nikt już jej nie zamknie, a otwarta dalej ruszałaby saldo.
            px = _cena_z_wyceny(pos)
        _close_part(session, acc, pos, pos.qty or 0.0, px, reason, now)
        n += 1
    if cancel_orders:
        for z in _open_orders(session, acc):
            z.status, z.done_at, z.note = "cancelled", now, "Account closed"
    session.flush()
    acc.terminal_open = bool(_open_positions(session, acc) or _open_orders(session, acc))
    return n


def evaluate_window(cfg, rt, tick, dni: dict | None):
    """Reguły na przebiegu okna świec — doba po dobie serwera.

    Okno może przejść przez północ (nikt nie patrzył na konto): wtedy minione
    doby dostają SWÓJ dołek i SWÓJ baseline, a nowa — baseline z equity
    o północy, nie z chwili przeglądu. Minione doby idą na prawdziwym stanie
    (to już historia: ich saldo, best-day, dzień handlowy), bieżąca na KOPII —
    dołek bez breachu nie może podbić szczytu ani ruszyć baseline'u. Zwraca
    (wynik, stan), gdy któraś doba rozstrzyga konto (breach, zaliczenie fazy),
    inaczej None i zwykła ocena bieżącego ticku."""
    if not dni:
        return None
    for dzien in sorted(dni):
        if dzien > tick.day_key or (rt.day_key and dzien < rt.day_key):
            continue
        d = dni[dzien]
        if dzien != rt.day_key:
            if "start_eq" in d:
                rules.start_day(rt, dzien, d["start_bal"], d["start_eq"])
            else:
                # Północ wypadła przed oknem: najbliższy znany stan sprzed doby.
                rules.start_day(rt, dzien, rt.balance or tick.balance, rt.equity or tick.equity)
        if "low" not in d:
            continue
        if dzien < tick.day_key:
            dol = copy.copy(tick)
            dol.day_key = dzien
            dol.balance = round(d["end_bal"], 2)
            dol.equity = round(d["low"], 2)
            dol.open_pnl = round(d["low"] - d["end_bal"], 2)
            dol.has_open_position = bool(d.get("pos"))
            res = rules.evaluate(cfg, rt, dol)
            if res.failed or res.passed_phase:
                return res, rt
        elif d["low"] < tick.equity - 0.005:
            kopia = copy.deepcopy(rt)
            dol = copy.copy(tick)
            dol.equity = round(d["low"], 2)
            dol.open_pnl = round(d["low"] - tick.balance, 2)
            res = rules.evaluate(cfg, kopia, dol)
            if res.failed:
                return res, kopia
    return None


# --------------------------------------------------------------------------- #
#  Dostęp: dane MT5 konta dają sesję tego jednego konta                        #
# --------------------------------------------------------------------------- #
class Who:
    def __init__(self, trader_id: int, account_id: int, imp: bool = False):
        self.trader_id = trader_id
        self.account_id = account_id
        self.imp = imp


SESJA_ODSWIEZ_S = 60


def who(request: Request, authorization: str | None = Header(default=None)) -> Who:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Connect your MT5 account")
    dane = auth.parse_terminal_token(authorization.split(" ", 1)[1].strip())
    if dane is None:
        raise HTTPException(401, "Session expired. Connect again.")
    session = SessionLocal()
    try:
        acc = session.get(Account, int(dane["aid"]))
        if acc is None or acc.trader_id is None or acc.terminal_blocked \
                or dane.get("pwf") != auth.terminal_pw_fp(acc.platform_password):
            raise HTTPException(401, "Session expired. Connect again.")
        sid = dane.get("sid")
        if sid:
            wpis = session.get(TerminalLogin, int(sid))
            if wpis is None or wpis.revoked_at is not None:
                raise HTTPException(401, "Session expired. Connect again.")
            widziany = wpis.last_seen_at
            if widziany is not None and widziany.tzinfo is None:
                widziany = widziany.replace(tzinfo=timezone.utc)
            if widziany is None or _now() - widziany > timedelta(seconds=SESJA_ODSWIEZ_S):
                wpis.last_seen_at = _now()
                session.commit()
        elif not dane.get("imp"):
            raise HTTPException(401, "Session expired. Connect again.")
        return Who(acc.trader_id, acc.id, bool(dane.get("imp")))
    finally:
        session.close()


_TEMPO: dict[int, list[float]] = {}
_TEMPO_OFF = os.getenv("RATE_LIMIT_OFF", "false").lower() == "true"
TEMPO_LIMIT, TEMPO_OKNO_S = 20, 10


def who_trade(request: Request, authorization: str | None = Header(default=None)) -> Who:
    """Jak `who`, ale dla zleceń i zamknięć: nie w podglądzie admina „jako klient"
    (tam handel byłby na cudzym koncie) i z limitem tempa na tradera — każde
    zlecenie to zapytanie do Hyperliquid i pełna ocena reguł."""
    w = who(request, authorization)
    if w.imp:
        raise HTTPException(403, "Trading is off while viewing the account as admin.")
    if not _TEMPO_OFF:
        teraz = time.monotonic()
        ost = [t for t in _TEMPO.get(w.trader_id, []) if teraz - t < TEMPO_OKNO_S]
        if len(ost) >= TEMPO_LIMIT:
            raise HTTPException(429, "Too many requests. Wait a few seconds and try again.")
        ost.append(teraz)
        _TEMPO[w.trader_id] = ost
    return w


def _konto(session, w: Who, account_id: int | None) -> Account:
    if not account_id:
        raise HTTPException(400, "Choose an account")
    acc = session.get(Account, int(account_id))
    if acc is None or acc.id != w.account_id:
        raise HTTPException(404, "Account not found")
    return acc


def _blokada_handlu(acc: Account) -> str | None:
    if acc.status not in TRADABLE:
        if acc.status == "failed" and acc.breach_reason:
            # Trader ma wiedzieć, która reguła zamknęła konto, a nie tylko że jest zamknięte.
            return f"This account is closed for trading. {acc.breach_reason.rstrip('.')}."
        return "This account is closed for trading."
    if acc.metaapi_account_id:
        return "This account is tracked live from MT5. Place trades in MT5."
    return None


# --------------------------------------------------------------------------- #
#  Widoki                                                                      #
# --------------------------------------------------------------------------- #
def _pos_dict(t: Trade, marks: dict[str, float]) -> dict:
    m = markets.get(t.symbol)
    px = marks.get(t.symbol)
    wart = markets.notional_usd(m, t.qty or 0.0, px or t.open_price) if m else 0.0
    return {
        "id": t.id, "symbol": t.symbol, "side": t.side, "qty": t.qty, "lots": round(t.lots or 0.0, 4),
        "entry": t.open_price, "mark": px, "value": round(wart, 2), "pnl": round(t.pnl or 0.0, 2),
        "roe": round((t.pnl or 0.0) / t.margin * 100, 2) if t.margin else None,
        "leverage": t.leverage, "margin": t.margin, "liq": t.liq_price, "tp": t.tp, "sl": t.sl, "trail": t.trail,
        "managed": t.venue == tradebot.OGON,
        "fee": round(t.fee or 0.0, 2), "slippage": t.slippage, "opened_at": _iso(t.opened_at),
    }


def _closed_dict(t: Trade, managed: bool = False) -> dict:
    d = {"id": t.id, "symbol": t.symbol, "side": t.side, "qty": t.qty, "entry": t.open_price,
         "close": t.close_price, "pnl": round(t.pnl or 0.0, 2), "fee": round(t.fee or 0.0, 2),
         "reason": t.close_reason, "leverage": t.leverage, "opened_at": _iso(t.opened_at),
         "closed_at": _iso(t.closed_at), "lots": round(t.lots or 0.0, 4), "note": t.journal,
         "managed": managed and t.source == "bot"}
    if t.source == "bot" and not t.qty:     # końcówka ma prawdziwą ilość, opłatę i powód
        # W historii zwykła zamknięta transakcja (decyzja właściciela, 2026-11-01): lot
        # w mierze konta (jak w portalu i MT5), ilość z wartości punktu, powód „Closed".
        inst = tradebot.INSTRUMENTS.get(t.symbol)
        d["qty"] = round((t.lots or 0.0) * inst.point_value, 6) if inst else None
        d["reason"] = "close"
        # Opłata i dźwignia jak przy transakcji z terminala (0,03% przy wejściu i wyjściu,
        # dźwignia rynku) — zero w kolumnie Fee odróżniałoby ją od reszty historii.
        # P&L zostaje z planu: traktujemy go jako wynik po opłatach.
        m = markets.get(t.symbol)
        if m and d["qty"]:
            d["fee"] = round((markets.notional_usd(m, d["qty"], t.open_price or 0.0)
                              + markets.notional_usd(m, d["qty"], t.close_price or 0.0)) * FEE_PCT, 2)
            d["leverage"] = float(m.max_lev)
    return d


def _bot_orders(d: dict) -> list[dict]:
    """Zlecenia Order History dla transakcji Trade BOT-a: market na wejściu i market
    reduce-only na wyjściu, jak przy pozycji otwartej i zamkniętej w terminalu.
    Bez nich historia pozycji miałaby transakcje, których nie poprzedziło żadne zlecenie."""
    wspolne = {"symbol": d["symbol"], "kind": "market", "qty": d["qty"], "price": None, "tp": None,
               "sl": None, "leverage": d.get("leverage"), "status": "filled", "note": ""}
    return [
        {**wspolne, "id": f"b{d['id']}c", "side": "sell" if d["side"] == "buy" else "buy", "reduce_only": True,
         "fill_price": d["close"], "created_at": d["closed_at"], "done_at": d["closed_at"]},
        {**wspolne, "id": f"b{d['id']}o", "side": d["side"], "reduce_only": False,
         "fill_price": d["entry"], "created_at": d["opened_at"], "done_at": d["opened_at"]},
    ]


def _order_dict(z: TerminalOrder) -> dict:
    return {"id": z.id, "symbol": z.symbol, "side": z.side, "kind": z.kind, "qty": z.qty, "price": z.price,
            "tp": z.tp, "sl": z.sl, "reduce_only": bool(z.reduce_only), "leverage": z.leverage,
            "status": z.status, "fill_price": z.fill_price, "note": z.note,
            "created_at": _iso(z.created_at), "done_at": _iso(z.done_at)}


def _konto_dict(acc: Account) -> dict:
    return {"id": acc.id, "login": acc.platform_login or acc.login, "server": acc.platform_server,
            "size": acc.initial_balance, "phase": acc.phase, "status": acc.status,
            "balance": round(float(acc.balance or 0.0), 2), "equity": round(float(acc.equity or 0.0), 2),
            "tradable": _blokada_handlu(acc) is None, "reason": _blokada_handlu(acc),
            "weekend_trading": bool(acc.weekend_trading),
            "managed_offer": bool(acc.bot_enabled and acc.bot_mode == "profit"),
            "managed_consent": _iso(acc.managed_consent_at)}


def state_dict(session, acc: Account, poz: list[Trade], marks: dict[str, float]) -> dict:
    fig = account_figures(session, acc, poz, marks)
    cfg = rules.config_from_account(acc)
    met = rules.display_metrics(cfg, balance=fig["balance"], equity=fig["equity"],
                                peak_equity=float(acc.peak_equity or acc.initial_balance or 0.0),
                                day_start_equity=float(acc.day_start_equity or acc.initial_balance or 0.0),
                                trading_days=int(acc.trading_days_count or 0))
    # Historia: transakcje z terminala i Trade BOT-a na żywych cenach (venue='hyperliquid' —
    # ich wejście i wyjście leżą na świecach wykresu; stare syntetyczne zostają tylko w portalu).
    zamk = (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "closed",
                    or_(Trade.source == "terminal", and_(Trade.source == "bot", Trade.venue == "hyperliquid")))
            .order_by(Trade.closed_at.desc(), Trade.id.desc()).limit(100).all())
    hist_z = (session.query(TerminalOrder)
              .filter(TerminalOrder.account_id == acc.id, TerminalOrder.status != "open")
              .order_by(TerminalOrder.id.desc()).limit(100).all())
    historia = [_closed_dict(t, bool(acc.managed_consent_at)) for t in zamk]
    zlecenia = [_order_dict(z) for z in hist_z]
    zlecenia += [o for t, d in zip(zamk, historia) if t.source == "bot" for o in _bot_orders(d)]
    zlecenia.sort(key=lambda o: o["done_at"] or o["created_at"] or "", reverse=True)
    return {
        "account": {**_konto_dict(acc), **fig, "metrics": met,
                    "max_lots": float(acc.max_lots or catalog.max_lots_for(acc.initial_balance or 0.0)),
                    "day_start_equity": round(float(acc.day_start_equity or 0.0), 2)},
        "positions": [_pos_dict(t, marks) for t in poz],
        "orders": [_order_dict(z) for z in _open_orders(session, acc)],
        "history": historia,
        "order_history": zlecenia[:100],
        "fee_pct": round(FEE_PCT * 100, 3), "server_time": _iso(_now()),
    }


def _najwyzsza_podloga(acc: Account) -> float:
    """Wyższa z dwóch podłóg equity (dzienna strata, max drawdown)."""
    cfg = rules.config_from_account(acc)
    met = rules.display_metrics(cfg, balance=float(acc.balance or 0.0), equity=float(acc.equity or 0.0),
                                peak_equity=float(acc.peak_equity or acc.initial_balance or 0.0),
                                day_start_equity=float(acc.day_start_equity or acc.initial_balance or 0.0),
                                trading_days=0)
    return max(met["daily_floor"], met["overall_floor"])


def _ocen(session, acc: Account) -> None:
    """Reguły po zmianie w księdze (wypełnienie, zamknięcie) albo co EVAL_EVERY_S."""
    from . import poller
    from .feed import NullFeed
    acc.terminal_eval_at = _now()
    asyncio.run(poller.process_account(session, acc, NullFeed()))
    acc.__dict__["_terminal_ocenione"] = True


def _stan_ksiegi(session, acc: Account) -> tuple:
    return (round(float(acc.balance or 0.0), 2), len(_open_positions(session, acc)),
            len(_open_orders(session, acc)))


def _okno_rozstrzyga(acc: Account, dni: dict) -> bool:
    """Przegląd sięgnął innej doby serwera niż ostatnia ocena albo dołek dotknął
    podłogi — reguły muszą to zobaczyć w tym samym requeście, bo doby okna
    żyją tylko do jego końca."""
    if not dni:
        return False
    if any(dzien != (acc.day_key or "") for dzien in dni):
        return True
    dolek = min((d["low"] for d in dni.values() if "low" in d), default=None)
    return dolek is not None and dolek <= _najwyzsza_podloga(acc)


def _przeglad_przed_akcja(session, acc: Account) -> None:
    """Przegląd świecami przed akcją tradera — i ocena reguł, gdy coś rozstrzygnął.

    Akcja nie może wyprzedzić tego, co rynek już zrobił: zlecenie złożone po
    breachu, którego nikt jeszcze nie policzył, wypełniłoby się i zaraz zamknęło
    z breachem (z opłatami), a po trafionym celu poszłoby jeszcze do starej fazy.
    Anulowanie czy zmiana TP/SL nie mają własnej oceny, więc bez tego dołek
    z okna przepadłby razem z requestem."""
    przed = _stan_ksiegi(session, acc)
    _, _, dni = sync(session, acc, dokladnie=True)
    if acc.terminal_used and (_stan_ksiegi(session, acc) != przed or _okno_rozstrzyga(acc, dni)):
        session.flush()
        _ocen(session, acc)
    if acc.status not in TRADABLE:
        session.commit()
        raise HTTPException(409, _blokada_handlu(acc))


def _zapisz_i_odmow(session, acc: Account, kod: int, tekst: str):
    """Przegląd przed akcją coś rozstrzygnął (SL, wypełnienie), więc akcja nie ma
    już sensu — ale wynik przeglądu zapisujemy i oceniamy, zamiast cofać go wyjątkiem."""
    session.flush()
    if acc.terminal_used and not acc.__dict__.get("_terminal_ocenione"):
        _ocen(session, acc)
    session.commit()
    raise HTTPException(kod, tekst)


def _z_ponowieniem(fn):
    """Blokada optymistyczna konta: przy kolizji jedna powtórka na świeżych danych."""
    for proba in (1, 2):
        session = SessionLocal()
        try:
            return fn(session)
        except StaleDataError:
            session.rollback()
            if proba == 2:
                raise HTTPException(409, "The account changed in the meantime. Try again.")
        finally:
            session.close()


# --------------------------------------------------------------------------- #
#  Endpointy                                                                   #
# --------------------------------------------------------------------------- #
# --- świece M1 z próbkowanego mid dla cienkich rynków ------------------------
PROBKA_S = 1.0                  # co tyle sekund próbka mid
SWIECE_KLUCZ = "candles_tick_at"
SWIECE_ODSTEP_S = 45.0          # drugi przebieg w tej samej minucie czeka
_SWIECE_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
              "1h": 3_600_000, "4h": 14_400_000}


def _zajmij_przebieg(session, klucz: str, min_odstep_s: float) -> bool:
    """Jeden przebieg naraz na hostingu bezserwerowym: warunkowy UPDATE w bazie,
    jak `poller._zajmij_tick_ryzyka`. True = można liczyć."""
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError
    teraz = time.time()
    row = session.get(AppSetting, klucz)
    if row is None:
        try:
            session.add(AppSetting(key=klucz, value=str(teraz)))
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False
    poprzedni = row.value
    try:
        if teraz - float(poprzedni) < min_odstep_s:
            return False
    except (TypeError, ValueError):
        pass
    wynik = session.execute(update(AppSetting).where(AppSetting.key == klucz, AppSetting.value == poprzedni)
                            .values(value=str(teraz)))
    session.commit()
    return wynik.rowcount == 1


def _zapisz_swiece(swiece: dict) -> int:
    """Scala próbki z wierszami w bazie: otwarcie zostaje, H/L rozszerzają się, zamknięcie
    i liczba próbek idą dalej. Zwraca liczbę dotkniętych wierszy."""
    if not swiece:
        return 0
    session = SessionLocal()
    try:
        n = 0
        for (coin, minuta), (o, h, l, c, k) in sorted(swiece.items(), key=lambda x: (x[0][0], x[0][1])):
            row = session.query(TerminalCandle).filter(TerminalCandle.coin == coin, TerminalCandle.ts == minuta).first()
            if row is None:
                session.add(TerminalCandle(coin=coin, ts=minuta, o=o, h=h, l=l, c=c, n=k))
            else:
                row.h, row.l, row.c, row.n = max(row.h, h), min(row.l, l), c, (row.n or 0) + k
            n += 1
        session.commit()
        return n
    finally:
        session.close()


def probkuj_swiece(budzet_s: float) -> dict:
    """Przez `budzet_s` sekund co `PROBKA_S` bierze mid rynków `markets.SAMPLED` i składa
    z tego świece M1. Zapis raz, na końcu; drugi przebieg w tej samej minucie odpada."""
    session = SessionLocal()
    try:
        if not _zajmij_przebieg(session, SWIECE_KLUCZ, SWIECE_ODSTEP_S):
            return {"skipped": True, "reason": "inny przebieg w toku"}
    finally:
        session.close()
    coins = sorted(markets.SAMPLED)
    start = time.monotonic()
    swiece: dict[tuple[str, datetime], list] = {}
    proby = 0
    while True:
        try:
            ceny = markets.mids(coins)
        except Exception:
            ceny = {}
        minuta = _now().replace(second=0, microsecond=0, tzinfo=None)
        for coin, px in ceny.items():
            s = swiece.get((coin, minuta))
            if s is None:
                swiece[(coin, minuta)] = [px, px, px, px, 1]
            else:
                s[1], s[2], s[3], s[4] = max(s[1], px), min(s[2], px), px, s[4] + 1
        proby += 1
        if time.monotonic() - start >= budzet_s:
            break
        time.sleep(PROBKA_S)
    return {"coins": len(coins), "samples": proby, "rows": _zapisz_swiece(swiece)}


@router.get("/candles")
def api_candles(symbol: str, interval: str = "1m", end: int | None = None, limit: int = 500):
    """Świece z próbkowanego mid dla rynku z `markets.SAMPLED`, złożone do żądanego
    interwału; inne rynki i interwały dzienne dostają pustą listę."""
    m = markets.get(symbol)
    krok = _SWIECE_MS.get(interval)
    if m is None or m.hl not in markets.SAMPLED or krok is None:
        return {"candles": []}
    limit = max(1, min(int(limit), 5000))
    koniec = int(end) if end else _ms(_now())
    od = datetime.fromtimestamp((koniec - krok * limit) / 1000, tz=timezone.utc).replace(tzinfo=None)
    session = SessionLocal()
    try:
        rows = (session.query(TerminalCandle)
                .filter(TerminalCandle.coin == m.hl, TerminalCandle.ts >= od,
                        TerminalCandle.ts <= datetime.fromtimestamp(koniec / 1000, tz=timezone.utc).replace(tzinfo=None))
                .order_by(TerminalCandle.ts).all())
    finally:
        session.close()
    out: list[dict] = []
    for r in rows:
        t = (int(r.ts.replace(tzinfo=timezone.utc).timestamp() * 1000) // krok) * krok
        if out and out[-1]["timestamp"] == t:
            k = out[-1]
            k["high"], k["low"], k["close"] = max(k["high"], r.h), min(k["low"], r.l), r.c
        else:
            out.append({"timestamp": t, "open": r.o, "high": r.h, "low": r.l, "close": r.c, "volume": 0.0})
    return {"candles": out}


@router.get("/markets")
def api_markets():
    return {"markets": markets.public_list(), "fee_pct": round(FEE_PCT * 100, 3),
            "max_slippage_pct": MAX_SLIPPAGE * 100, "weekend": tradebot.is_weekend()}


# --- logowanie danymi MT5 -----------------------------------------------------
PROBY_LOGIN, PROBY_IP, PROBY_OKNO_MIN = 8, 40, 15
LOGOWANIA_DNI = 365


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)
    server: str = Field(min_length=1, max_length=64)
    device: str | None = Field(default=None, max_length=32)


def _ip(request: Request) -> str | None:
    """Adres klienta. Na Vercelu `x-real-ip`/`x-vercel-forwarded-for` ustawia platforma;
    lewy wpis `x-forwarded-for` klient mógłby sobie dopisać sam."""
    h = request.headers
    ip = h.get("x-real-ip") or h.get("x-vercel-forwarded-for") or h.get("x-forwarded-for") \
        or (request.client.host if request.client else "")
    return ip.split(",")[0].strip()[:64] or None


def _geo(request: Request) -> dict:
    h = request.headers
    kraj = (h.get("x-vercel-ip-country") or "").strip().upper()
    return {"country": kraj if len(kraj) == 2 and kraj.isalpha() and kraj != "XX" else None,
            "region": unquote(h.get("x-vercel-ip-country-region") or "")[:64] or None,
            "city": unquote(h.get("x-vercel-ip-city") or "")[:80] or None}


def _zapisz_probe(session, request: Request, login: str, device: str | None, acc: Account | None) -> TerminalLogin:
    wpis = TerminalLogin(login=login[:64], account_id=acc.id if acc else None,
                         trader_id=acc.trader_id if acc else None, ok=acc is not None, ip=_ip(request),
                         user_agent=(request.headers.get("user-agent") or "")[:255] or None,
                         device_id=device, last_seen_at=_now() if acc else None, **_geo(request))
    session.add(wpis)
    session.commit()
    return wpis


def _limit_prob(session, login: str, ip: str | None) -> None:
    if _TEMPO_OFF:
        return
    od = _now() - timedelta(minutes=PROBY_OKNO_MIN)
    baza = session.query(TerminalLogin).filter(TerminalLogin.ok.is_(False), TerminalLogin.created_at >= od)
    if baza.filter(TerminalLogin.login == login).count() >= PROBY_LOGIN \
            or (ip and baza.filter(TerminalLogin.ip == ip).count() >= PROBY_IP):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")


@router.post("/login")
def api_login(payload: LoginIn, request: Request):
    """Logowanie danymi MT5 z maila po zakupie. Konto PTF nie jest tu potrzebne,
    a hasło do dashboardu tu nie działa."""
    login = payload.login.strip()
    device = "".join(z for z in (payload.device or "") if z.isalnum())[:32] or None
    session = SessionLocal()
    try:
        _limit_prob(session, login, _ip(request))
        # Login nie ma constraintu unique, więc para login + hasło rozstrzyga o koncie.
        trafione = [a for a in session.query(Account).filter(Account.platform_login == login).all()
                    if a.trader_id and a.platform_password
                    and (a.platform_server or "").strip().lower() == payload.server.strip().lower()
                    and secrets.compare_digest(a.platform_password.encode(), payload.password.encode())]
        trafione.sort(key=lambda a: _blokada_handlu(a) is not None)
        acc = trafione[0] if trafione else None
        if acc is None:
            _zapisz_probe(session, request, login, device, None)
            raise HTTPException(401, "Wrong login, password or server")
        if acc.terminal_blocked:
            raise HTTPException(403, "Terminal access for this account is paused. Contact support.")
        wpis = _zapisz_probe(session, request, login, device, acc)
        return {"token": auth.make_terminal_token(acc.id, acc.platform_password, sid=wpis.id),
                "account": _konto_dict(acc)}
    finally:
        session.close()


@router.post("/logout")
def api_logout(authorization: str | None = Header(default=None)):
    dane = auth.parse_terminal_token((authorization or "").split(" ", 1)[-1].strip())
    if dane and dane.get("sid"):
        session = SessionLocal()
        try:
            wpis = session.get(TerminalLogin, int(dane["sid"]))
            if wpis is not None and wpis.revoked_at is None:
                wpis.revoked_at = _now()
                session.commit()
        finally:
            session.close()
    return {"ok": True}


@router.get("/accounts")
def api_accounts(w: Who = Depends(who)):
    session = SessionLocal()
    try:
        return {"accounts": [_konto_dict(session.get(Account, w.account_id))]}
    finally:
        session.close()


@router.get("/state")
def api_state(account_id: int | None = None, w: Who = Depends(who)):
    def run(session):
        acc = _konto(session, w, account_id)
        if acc.terminal_open:
            przed = _stan_ksiegi(session, acc)
            poz, marks, dni = sync(session, acc)
            po = _stan_ksiegi(session, acc)
            ostatnia = acc.terminal_eval_at
            if ostatnia is not None and ostatnia.tzinfo is None:
                ostatnia = ostatnia.replace(tzinfo=timezone.utc)
            ocenic = (po != przed or ostatnia is None
                      or _now() - ostatnia > timedelta(seconds=EVAL_EVERY_S)
                      or _okno_rozstrzyga(acc, dni))
            session.flush()
            if ocenic and acc.terminal_used:
                _ocen(session, acc)
            session.commit()
        elif acc.bot_enabled:
            # Konto Trade BOT-a bez pozycji z terminala: nikt inny nie pcha go do przodu,
            # gdy trader wchodzi tylko danymi MT5 (portal i jego odczyty nie są potrzebne).
            ostatnia = acc.terminal_eval_at
            if ostatnia is not None and ostatnia.tzinfo is None:
                ostatnia = ostatnia.replace(tzinfo=timezone.utc)
            if ostatnia is None or _now() - ostatnia > timedelta(seconds=EVAL_EVERY_S):
                _ocen(session, acc)
                session.commit()
        poz = _open_positions(session, acc)
        marks = _marks(session, acc, poz, _now())
        return state_dict(session, acc, poz, marks)
    return _z_ponowieniem(run)


# Liczby z JSON-a: bez Infinity/NaN (json.loads je przepuszcza, a NaN w saldzie
# psuje każdy kolejny /state) i z rozsądnym sufitem.
_LICZBA = dict(allow_inf_nan=False, lt=1e12)


class OrderIn(BaseModel):
    account_id: int | None = None
    symbol: str
    side: str = Field(pattern="^(buy|sell)$")
    kind: str = Field(default="market", pattern="^(market|limit|stop)$")
    qty: float = Field(gt=0, **_LICZBA)
    price: float | None = Field(default=None, gt=0, **_LICZBA)
    leverage: float | None = None      # ignorowane: dźwignia jest z góry ustalona dla rynku
    tp: float | None = Field(default=None, ge=0, **_LICZBA)
    sl: float | None = Field(default=None, ge=0, **_LICZBA)
    reduce_only: bool = False
    max_slippage_pct: float = Field(default=MAX_SLIPPAGE * 100, gt=0, le=10, allow_inf_nan=False)


@router.post("/orders")
def api_order(payload: OrderIn, w: Who = Depends(who_trade)):
    if _zlecenia_wstrzymane() and not payload.reduce_only:
        raise HTTPException(503, "New orders are paused for maintenance. You can still manage open positions.")
    m = markets.get(payload.symbol)
    if m is None:
        raise HTTPException(404, "Unknown market")
    lev = float(m.max_lev)      # trader nie wybiera dźwigni — ta z klienta nie ma znaczenia
    tp = payload.tp or None
    sl = payload.sl or None

    def run(session):
        acc = _konto(session, w, payload.account_id)
        powod = _blokada_handlu(acc)
        if powod:
            raise HTTPException(409, powod)
        bylo_otwarte = bool(acc.terminal_open)
        if bylo_otwarte:
            _przeglad_przed_akcja(session, acc)
        teraz = _now()
        if payload.kind == "market" and _zamrozony(m, _ms(teraz)):
            # W weekend pozycję na tym rynku zamyka tylko „Close" — po cenie z piątku.
            # Zlecenie rynkowe wypełniłoby się po żywej księdze Hyperliquid.
            raise HTTPException(409, "Market closed for the weekend. Close the position from the Positions tab.")
        pos = next((t for t in _open_positions(session, acc) if t.symbol == m.sym), None)
        if pos is not None and pos.venue == tradebot.OGON:
            raise HTTPException(409, "This market has an open managed position. Close it first.")
        zmniejsza = pos is not None and pos.side != payload.side and payload.qty <= (pos.qty or 0) + EPS
        if not (payload.reduce_only or zmniejsza):
            zamk = market_closed(m, acc, teraz)
            if zamk:
                raise HTTPException(409, zamk)
        qty = round(payload.qty, m.qty_dec + 3)
        if payload.reduce_only and pos is not None and pos.side != payload.side:
            qty = min(qty, pos.qty or 0.0)      # poślizg liczony na tym, co naprawdę się zamknie
        # Zlecenie zamykające dokładnie całą pozycję przechodzi zawsze (resztki sprzed limitu).
        caly = pos is not None and pos.side != payload.side and abs(qty - (pos.qty or 0.0)) <= EPS
        if not caly:
            zostaje = None
            if pos is not None and pos.side != payload.side and qty < (pos.qty or 0.0):
                zostaje = markets.lots(m, (pos.qty or 0.0) - qty)
            _wolumen_ok(m, qty, zostaje)
        if payload.kind == "market":
            vwap, mid, slip = _vwap(m, payload.side, qty, payload.max_slippage_pct / 100)
            _tp_sl_ok(payload.side, vwap, tp, sl)
            if not payload.reduce_only:
                _check_open(session, acc, m, payload.side, qty, vwap, lev)
            z = TerminalOrder(account_id=acc.id, symbol=m.sym, side=payload.side, kind="market", qty=qty,
                              leverage=lev, tp=tp, sl=sl, reduce_only=payload.reduce_only,
                              created_at=teraz)
            session.add(z)
            p = _fill(session, acc, m, payload.side, qty, vwap, lev, tp, sl,
                      payload.reduce_only, teraz, slip)
            session.flush()
            z.status, z.done_at, z.fill_price = "filled", teraz, vwap
            z.trade_id = p.id if p is not None else None
        else:
            if not payload.price:
                raise HTTPException(400, "Set a price for this order.")
            try:
                mid = markets.mids({m.hl}).get(m.hl)
            except Exception:
                mid = None
            if mid is None:
                raise HTTPException(503, "No price for this market right now.")
            if payload.kind == "stop" and ((payload.side == "buy" and payload.price <= mid)
                                           or (payload.side == "sell" and payload.price >= mid)):
                raise HTTPException(400, "Stop price is already crossed. Use a market order.")
            if payload.kind == "limit" and ((payload.side == "buy" and payload.price >= mid)
                                            or (payload.side == "sell" and payload.price <= mid)):
                raise HTTPException(400, "Limit price is at or through the market. Use a market order.")
            _tp_sl_ok(payload.side, payload.price, tp, sl)
            if not payload.reduce_only:
                _check_open(session, acc, m, payload.side, qty, payload.price, lev)
            session.add(TerminalOrder(account_id=acc.id, symbol=m.sym, side=payload.side, kind=payload.kind,
                                      qty=qty, price=payload.price, leverage=lev, tp=tp, sl=sl,
                                      reduce_only=payload.reduce_only, created_at=teraz))
        acc.terminal_used = True
        acc.terminal_open = True
        if not bylo_otwarte:
            # Nic nie było otwarte, więc nie ma czego przeglądać wstecz. Stary
            # znacznik kazałby przeglądowi wycenić nową pozycję na świecach sprzed jej otwarcia.
            acc.terminal_sync_at = acc.terminal_candle_at = teraz
        session.flush()
        _ocen(session, acc)
        session.commit()
        session.refresh(acc)
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


@router.delete("/orders/{order_id}")
def api_cancel(order_id: int, account_id: int | None = None, w: Who = Depends(who_trade)):
    def run(session):
        acc = _konto(session, w, account_id)
        # Najpierw przegląd: zlecenie, które rynek już przeciął, jest wypełnione,
        # a nie do anulowania. Przegląd zapisuje też konto (row_version), więc
        # równoległe wypełnienie i anulowanie nie nadpiszą się po cichu.
        if acc.terminal_open:
            _przeglad_przed_akcja(session, acc)
        z = session.get(TerminalOrder, order_id)
        if z is None or z.account_id != acc.id:
            raise HTTPException(404, "Order not found")
        if z.status != "open":
            _zapisz_i_odmow(session, acc, 409, "This order is no longer open (it was filled or cancelled).")
        z.status, z.done_at, z.note = "cancelled", _now(), "Cancelled by you"
        acc.terminal_open = bool(_open_positions(session, acc) or
                                 [o for o in _open_orders(session, acc) if o.id != z.id])
        session.commit()
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


class CloseIn(BaseModel):
    account_id: int | None = None
    qty: float | None = Field(default=None, gt=0, **_LICZBA)
    max_slippage_pct: float = Field(default=MAX_SLIPPAGE * 100, gt=0, le=10, allow_inf_nan=False)


def _zamknij_rynkowo(session, acc: Account, pos: Trade, qty: float, max_slip: float) -> None:
    m = markets.get(pos.symbol)
    strona = "sell" if pos.side == "buy" else "buy"
    teraz = _now()
    if _zamrozony(m, _ms(teraz)):
        px = _cena_weekendowa(m.hl, teraz)
        if px is None:
            raise HTTPException(503, "No price for this market right now.")
    else:
        px, _, _ = _vwap(m, strona, qty, max_slip)
    session.add(TerminalOrder(account_id=acc.id, symbol=m.sym, side=strona, kind="market", qty=qty,
                              leverage=pos.leverage or 1.0, reduce_only=True, status="filled",
                              created_at=teraz, done_at=teraz, fill_price=px, trade_id=pos.id))
    _close_part(session, acc, pos, qty, px, "close" if qty >= (pos.qty or 0) - EPS else "reduce", teraz)


@router.post("/positions/{trade_id}/close")
def api_close(trade_id: int, payload: CloseIn, w: Who = Depends(who_trade)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
        if acc.status not in TRADABLE:
            raise HTTPException(409, "This account is closed for trading.")
        _przeglad_przed_akcja(session, acc)
        pos = session.get(Trade, trade_id)
        if pos is None or pos.account_id != acc.id or (pos.source != "terminal" and pos.venue != tradebot.OGON):
            raise HTTPException(404, "Position not found")
        if pos.status != "open":
            _zapisz_i_odmow(session, acc, 409, "Position already closed")
        qty = min(payload.qty or pos.qty or 0.0, pos.qty or 0.0)
        if pos.venue == tradebot.OGON and qty < (pos.qty or 0.0) - EPS:
            raise HTTPException(400, "A managed position closes in full.")
        mp = markets.get(pos.symbol)
        if mp is not None and qty < (pos.qty or 0.0) - EPS:
            _wolumen_ok(mp, qty, markets.lots(mp, (pos.qty or 0.0) - qty))
        _zamknij_rynkowo(session, acc, pos, qty, payload.max_slippage_pct / 100)
        acc.terminal_open = bool(_open_positions(session, acc) or _open_orders(session, acc))
        session.flush()
        _ocen(session, acc)
        session.commit()
        session.refresh(acc)
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


class AllIn(BaseModel):
    account_id: int | None = None


@router.post("/close-all")
def api_close_all(payload: AllIn, w: Who = Depends(who_trade)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
        if acc.status not in TRADABLE:
            raise HTTPException(409, "This account is closed for trading.")
        _przeglad_przed_akcja(session, acc)
        for pos in _open_positions(session, acc):
            _zamknij_rynkowo(session, acc, pos, pos.qty or 0.0, 0.05)
        acc.terminal_open = bool(_open_orders(session, acc))
        session.flush()
        _ocen(session, acc)
        session.commit()
        session.refresh(acc)
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


@router.post("/cancel-all")
def api_cancel_all(payload: AllIn, w: Who = Depends(who_trade)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
        if acc.terminal_open:
            _przeglad_przed_akcja(session, acc)
        teraz = _now()
        for z in _open_orders(session, acc):
            z.status, z.done_at, z.note = "cancelled", teraz, "Cancelled by you"
        acc.terminal_open = bool(_open_positions(session, acc))
        session.commit()
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


class TpSlIn(BaseModel):
    account_id: int | None = None
    tp: float | None = Field(default=None, ge=0, **_LICZBA)
    sl: float | None = Field(default=None, ge=0, **_LICZBA)
    trail: float | None = Field(default=None, ge=0, **_LICZBA)


@router.post("/positions/{trade_id}/break-even")
def api_break_even(trade_id: int, payload: AllIn, w: Who = Depends(who_trade)):
    """SL na cenę wejścia. Tylko na zysku: inaczej stop od razu leżałby po złej stronie ceny."""
    def run(session):
        acc = _konto(session, w, payload.account_id)
        if acc.terminal_open:
            _przeglad_przed_akcja(session, acc)
        pos = session.get(Trade, trade_id)
        if pos is None or pos.account_id != acc.id or (pos.source != "terminal" and pos.venue != tradebot.OGON):
            raise HTTPException(404, "Position not found")
        if pos.status != "open":
            _zapisz_i_odmow(session, acc, 409, "Position already closed")
        m = markets.get(pos.symbol)
        mid = markets.mids({m.hl}).get(m.hl)
        if not mid or (pos.side == "buy" and mid <= pos.open_price) or (pos.side == "sell" and mid >= pos.open_price):
            raise HTTPException(400, "The position must be in profit to move the stop to break-even.")
        pos.sl, pos.tpsl_at = pos.open_price, _now()
        session.commit()
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


class OrderMoveIn(BaseModel):
    account_id: int | None = None
    price: float = Field(gt=0, **_LICZBA)


@router.patch("/orders/{order_id}")
def api_move_order(order_id: int, payload: OrderMoveIn, w: Who = Depends(who_trade)):
    """Nowa cena oczekującego zlecenia (przeciągnięcie linii na wykresie)."""
    def run(session):
        acc = _konto(session, w, payload.account_id)
        if acc.terminal_open:
            _przeglad_przed_akcja(session, acc)
        z = session.get(TerminalOrder, order_id)
        if z is None or z.account_id != acc.id:
            raise HTTPException(404, "Order not found")
        if z.status != "open":
            _zapisz_i_odmow(session, acc, 409, "This order is no longer open.")
        m = markets.get(z.symbol)
        mid = markets.mids({m.hl}).get(m.hl)
        if not mid:
            raise HTTPException(503, "No price for this market right now.")
        if z.kind == "stop" and ((z.side == "buy" and payload.price <= mid) or (z.side == "sell" and payload.price >= mid)):
            raise HTTPException(400, "Stop price is already crossed. Use a market order.")
        if z.kind == "limit" and ((z.side == "buy" and payload.price >= mid) or (z.side == "sell" and payload.price <= mid)):
            raise HTTPException(400, "Limit price is at or through the market. Use a market order.")
        _tp_sl_ok(z.side, payload.price, z.tp, z.sl)
        if not z.reduce_only:
            _check_open(session, acc, m, z.side, z.qty, payload.price, float(z.leverage or m.max_lev))
        # Czas złożenia = teraz: starsze świece nie mogą wypełnić zlecenia po nowej cenie.
        z.price, z.created_at = payload.price, _now()
        session.commit()
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


ZGODA_WERSJA = "2026-11"


class ManagedIn(BaseModel):
    account_id: int | None = None
    accept: bool


@router.post("/managed-consent")
def api_managed_consent(payload: ManagedIn, request: Request, w: Who = Depends(who_trade)):
    """Zgodę daje i cofa sam klient, w swojej sesji (podgląd admina odpada w `who_trade`).
    Po cofnięciu otwarta końcówka dochodzi do końca, nowa już nie startuje."""
    session = SessionLocal()
    try:
        acc = _konto(session, w, payload.account_id)
        acc.managed_consent_at = _now() if payload.accept else None
        acc.managed_consent_ip = _ip(request) if payload.accept else None
        acc.managed_consent_version = ZGODA_WERSJA if payload.accept else None
        session.commit()
        return {"ok": True, "managed_consent": _iso(acc.managed_consent_at)}
    finally:
        session.close()


@router.get("/history")
def api_history(account_id: int | None = None, limit: int = 2000, w: Who = Depends(who)):
    """Pełna historia do raportu konta i eksportu (state oddaje tylko ostatnie 100)."""
    session = SessionLocal()
    try:
        acc = _konto(session, w, account_id)
        zamk = (session.query(Trade)
                .filter(Trade.account_id == acc.id, Trade.status == "closed",
                        or_(Trade.source == "terminal", and_(Trade.source == "bot", Trade.venue == "hyperliquid")))
                .order_by(Trade.closed_at.desc(), Trade.id.desc()).limit(min(limit, 5000)).all())
        return {"history": [_closed_dict(t, bool(acc.managed_consent_at)) for t in zamk], "initial_balance": acc.initial_balance}
    finally:
        session.close()


class NoteIn(BaseModel):
    account_id: int | None = None
    note: str = Field(default="", max_length=500)


@router.put("/trades/{trade_id}/note")
def api_note(trade_id: int, payload: NoteIn, w: Who = Depends(who_trade)):
    session = SessionLocal()
    try:
        acc = _konto(session, w, payload.account_id)
        t = session.get(Trade, trade_id)
        if t is None or t.account_id != acc.id:
            raise HTTPException(404, "Trade not found")
        t.journal = payload.note.strip() or None
        session.commit()
        return {"ok": True, "note": t.journal}
    finally:
        session.close()


@router.patch("/positions/{trade_id}")
def api_tpsl(trade_id: int, payload: TpSlIn, w: Who = Depends(who_trade)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
        # Najpierw przegląd świecami: SL, który rynek już trafił, zamyka pozycję —
        # nie da się go przesunąć po fakcie, patrząc na knot na wykresie.
        if acc.terminal_open:
            _przeglad_przed_akcja(session, acc)
        pos = session.get(Trade, trade_id)
        if pos is None or pos.account_id != acc.id or (pos.source != "terminal" and pos.venue != tradebot.OGON):
            raise HTTPException(404, "Position not found")
        if pos.status != "open":
            _zapisz_i_odmow(session, acc, 409, "Position already closed")
        m = markets.get(pos.symbol)
        try:
            mid = markets.mids({m.hl}).get(m.hl) or pos.open_price
        except Exception:
            mid = pos.open_price
        tp = payload.tp or None
        sl = payload.sl or None
        if "trail" in payload.model_fields_set:
            pos.trail = payload.trail or None
            if pos.trail:
                start = mid - pos.trail if pos.side == "buy" else mid + pos.trail
                lepszy = sl and ((pos.side == "buy" and sl > start) or (pos.side == "sell" and sl < start))
                sl = sl if lepszy else start
        elif sl is None:
            pos.trail = None        # bez SL nie ma czego podciągać
        _tp_sl_ok(pos.side, mid, tp, sl)
        pos.tp, pos.sl, pos.tpsl_at = tp, sl, _now()
        session.commit()
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


# --------------------------------------------------------------------------- #
#  Panel admina: zakładka Built-in Terminal                                    #
# --------------------------------------------------------------------------- #
admin_router = APIRouter(prefix="/api/admin/terminal", dependencies=[Depends(auth.require_admin)])
KLUCZ_WSTRZYMANE = "terminal_orders_paused"
OKNO_FLAG_DNI = 30
MAX_URZADZEN = 2


def _zlecenia_wstrzymane() -> bool:
    session = SessionLocal()
    try:
        wpis = session.get(AppSetting, KLUCZ_WSTRZYMANE)
        return bool(wpis and wpis.value == "1")
    finally:
        session.close()


def _urzadzenie(ua: str | None) -> str:
    ua = ua or ""
    przegl = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                  ("Safari/", "Safari")) if k in ua), "Browser")
    sys_ = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Mac OS", "macOS"),
                                ("Windows", "Windows"), ("Linux", "Linux")) if k in ua), "unknown OS")
    return f"{przegl} on {sys_}"


def _login_dict(w: TerminalLogin) -> dict:
    return {"id": w.id, "login": w.login, "account_id": w.account_id, "ok": bool(w.ok), "ip": w.ip,
            "country": w.country, "region": w.region, "city": w.city, "device": _urzadzenie(w.user_agent),
            "device_id": (w.device_id or "")[:6] or None, "at": _iso(w.created_at), "last_seen": _iso(w.last_seen_at),
            "revoked": w.revoked_at is not None}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def _flagi(acc: Account, tr: Trader | None, wpisy: list[TerminalLogin], ip_traderow: dict[str, set[int]]) -> list[dict]:
    """Sygnały dla admina. Niczego nie blokują: VPN albo wyjazd wyglądają tak samo."""
    flagi = []
    od = _now() - timedelta(days=OKNO_FLAG_DNI)
    nowe = [w for w in wpisy if _utc(w.created_at) >= od]
    kraje = {w.country for w in nowe if w.country}
    znane = {k for k in ((tr.signup_country, tr.last_login_country) if tr else ()) if k}
    if znane and kraje - znane:
        flagi.append({"key": "country", "label": "Country differs from portal",
                      "detail": f"Terminal {', '.join(sorted(kraje))}, portal {', '.join(sorted(znane))}"})
    if len(kraje) > 1:
        flagi.append({"key": "countries", "label": f"{len(kraje)} countries in {OKNO_FLAG_DNI} days",
                      "detail": ", ".join(sorted(kraje))})
    urzadzenia = {w.device_id for w in nowe if w.device_id}
    # Dodatek Copytrading wprost pozwala na wiele urządzeń (regulamin, sekcja 6).
    if len(urzadzenia) > MAX_URZADZEN and not acc.copytrading:
        flagi.append({"key": "devices", "label": f"{len(urzadzenia)} devices in {OKNO_FLAG_DNI} days", "detail": ""})
    inni = set().union(*(ip_traderow.get(w.ip, set()) for w in nowe if w.ip), set()) - {acc.trader_id}
    if inni:
        flagi.append({"key": "shared_ip", "label": "IP shared with another trader",
                      "detail": "Trader ID " + ", ".join(map(str, sorted(inni)))})
    return flagi


@admin_router.get("/overview")
def admin_overview():
    session = SessionLocal()
    try:
        wpisy = (session.query(TerminalLogin)
                 .filter(TerminalLogin.ok.is_(True), TerminalLogin.created_at >= _now() - timedelta(days=OKNO_FLAG_DNI))
                 .order_by(TerminalLogin.id.desc()).all())
        po_koncie: dict[int, list[TerminalLogin]] = {}
        ip_traderow: dict[str, set[int]] = {}
        for w in wpisy:
            po_koncie.setdefault(w.account_id, []).append(w)
            if w.ip:
                ip_traderow.setdefault(w.ip, set()).add(w.trader_id)
        konta = (session.query(Account).filter(Account.platform_login.isnot(None), Account.trader_id.isnot(None))
                 .order_by(Account.id.desc()).limit(1000).all())
        traderzy = {t.id: t for t in session.query(Trader).filter(Trader.id.in_({a.trader_id for a in konta})).all()}
        pula = {p.claimed_by_account_id: p for p in session.query(PoolAccount)
                .filter(PoolAccount.claimed_by_account_id.isnot(None)).all()}
        otwarte: dict[int, int] = {}
        for (aid,) in session.query(Trade.account_id).filter(Trade.status == "open", or_(Trade.source == "terminal", Trade.venue == tradebot.OGON)).all():
            otwarte[aid] = otwarte.get(aid, 0) + 1
        ostatnie = dict(session.query(TerminalLogin.account_id, func.max(TerminalLogin.id))
                        .filter(TerminalLogin.ok.is_(True)).group_by(TerminalLogin.account_id).all())
        ost_wpisy = {w.account_id: w for w in session.query(TerminalLogin)
                     .filter(TerminalLogin.id.in_(ostatnie.values())).all()} if ostatnie else {}
        wiersze = []
        for a in konta:
            tr, moje, ost, p = traderzy.get(a.trader_id), po_koncie.get(a.id, []), ost_wpisy.get(a.id), pula.get(a.id)
            wiersze.append({
                "id": a.id, "login": a.platform_login, "server": a.platform_server, "size": a.initial_balance,
                "phase": a.phase, "status": a.status, "balance": round(float(a.balance or 0.0), 2),
                "equity": round(float(a.equity or 0.0), 2), "open_positions": otwarte.get(a.id, 0),
                "blocked": bool(a.terminal_blocked), "bot": bool(a.bot_enabled), "managed_consent": _iso(a.managed_consent_at), "used": bool(a.terminal_used),
                "trader": {"id": a.trader_id, "name": tr.full_name if tr else None, "email": tr.email if tr else None,
                           "signup_country": tr.signup_country if tr else None,
                           "portal_country": tr.last_login_country if tr else None},
                "pool": {"id": p.id, "simulated": bool(p.simulated)} if p else None,
                "pool_mismatch": bool(p and (p.claimed_by_trader_id != a.trader_id
                                             or p.platform_login != a.platform_login)),
                "last_login": _login_dict(ost) if ost else None, "logins_30d": len(moje),
                "flags": _flagi(a, tr, moje, ip_traderow),
            })
        wiersze.sort(key=lambda r: (not r["flags"], not r["last_login"], -r["id"]))
        doba = _now() - timedelta(days=1)
        logi = session.query(TerminalLogin).filter(TerminalLogin.created_at >= doba)
        return {"accounts": wiersze, "orders_paused": _zlecenia_wstrzymane(), "stats": {
            "accounts": len(wiersze), "signed_in": sum(1 for r in wiersze if r["last_login"]),
            "flagged": sum(1 for r in wiersze if r["flags"]), "blocked": sum(1 for r in wiersze if r["blocked"]),
            "open_positions": sum(otwarte.values()),
            "logins_24h": logi.filter(TerminalLogin.ok.is_(True)).count(),
            "failed_24h": logi.filter(TerminalLogin.ok.is_(False)).count()}}
    finally:
        session.close()


@admin_router.get("/logins")
def admin_logins(account_id: int | None = None, failed: bool = False, limit: int = 200):
    session = SessionLocal()
    try:
        q = session.query(TerminalLogin)
        if account_id:
            q = q.filter(TerminalLogin.account_id == account_id)
        if failed:
            q = q.filter(TerminalLogin.ok.is_(False))
        return {"logins": [_login_dict(w) for w in q.order_by(TerminalLogin.id.desc()).limit(min(limit, 500)).all()]}
    finally:
        session.close()


class AdminBlockIn(BaseModel):
    blocked: bool


class AdminPauseIn(BaseModel):
    paused: bool


def _cofnij_sesje(session, account_id: int) -> int:
    return (session.query(TerminalLogin)
            .filter(TerminalLogin.account_id == account_id, TerminalLogin.ok.is_(True),
                    TerminalLogin.revoked_at.is_(None))
            .update({TerminalLogin.revoked_at: _now()}, synchronize_session=False))


@admin_router.post("/accounts/{account_id}/block")
def admin_block(account_id: int, payload: AdminBlockIn):
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if acc is None:
            raise HTTPException(404, "Account not found")
        acc.terminal_blocked = payload.blocked
        if payload.blocked:
            _cofnij_sesje(session, account_id)
        session.commit()
        return {"ok": True, "blocked": payload.blocked}
    finally:
        session.close()


@admin_router.post("/accounts/{account_id}/sign-out")
def admin_sign_out(account_id: int):
    session = SessionLocal()
    try:
        n = _cofnij_sesje(session, account_id)
        session.commit()
        return {"ok": True, "signed_out": n}
    finally:
        session.close()


@admin_router.post("/accounts/{account_id}/view")
def admin_view(account_id: int):
    """Podgląd konta oczami klienta: bez handlu, 2 godziny, nie zapisuje się w logowaniach."""
    session = SessionLocal()
    try:
        acc = session.get(Account, account_id)
        if acc is None or not acc.platform_password:
            raise HTTPException(404, "Account has no MT5 credentials")
        return {"token": auth.make_terminal_token(acc.id, acc.platform_password, imp=True)}
    finally:
        session.close()


@admin_router.post("/orders-paused")
def admin_orders_paused(payload: AdminPauseIn):
    session = SessionLocal()
    try:
        wpis = session.get(AppSetting, KLUCZ_WSTRZYMANE)
        if wpis is None:
            session.add(AppSetting(key=KLUCZ_WSTRZYMANE, value="1" if payload.paused else "0"))
        else:
            wpis.value = "1" if payload.paused else "0"
        session.commit()
        return {"ok": True, "orders_paused": payload.paused}
    finally:
        session.close()


def wyczysc_logowania() -> dict:
    """Retencja z polityki prywatności: logowania starsze niż LOGOWANIA_DNI znikają."""
    session = SessionLocal()
    try:
        n = (session.query(TerminalLogin)
             .filter(TerminalLogin.created_at < _now() - timedelta(days=LOGOWANIA_DNI))
             .delete(synchronize_session=False))
        session.commit()
        return {"deleted": n}
    finally:
        session.close()
