"""Terminal PTF — zlecenia i pozycje z zakładki Trade.

To jest księga konta challenge'u: pozycja otwarta tutaj trafia do tej samej
tabeli `trades` co historia z dashboardu, a zysk i strata zmieniają to samo
saldo, które liczą reguły (daily loss, max drawdown, target). Do terminala
loguje się wyłącznie kontem PTF (ta sama sesja co portal) — danymi MT5 nie.

Rozliczenie idzie po cenach Hyperliquid (patrz `markets.py`):
  - zlecenie rynkowe przechodzi przez księgę zleceń (VWAP), z limitem
    poślizgu, opłatą od wartości i marżą izolowaną (wartość / dźwignia);
  - limit, stop, TP, SL i likwidację rozstrzyga `sync()`: przegląda świece 1m
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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm.exc import StaleDataError

from . import auth, catalog, markets, rules, tradebot
from .config import get_settings
from .db import SessionLocal
from .feed import MarketSnapshot
from .markets import Market
from .models import Account, TerminalOrder, Trade

settings = get_settings()

FEE_PCT = 0.0003            # 0,03% wartości przy otwarciu i przy zamknięciu
MAX_SLIPPAGE = 0.01         # domyślny limit poślizgu (1%)
MMR = 0.005                 # marża utrzymaniowa (do ceny likwidacji)
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


def _cena_weekendowa(coin: str, now: datetime) -> float | None:
    """Cena z zamknięcia tygodnia — po niej wyceniamy rynki inne niż krypto
    w weekend (Hyperliquid notuje je 24/7, prawdziwy rynek stoi)."""
    koniec = _ms(tradebot._week_close(now))
    if koniec > _ms(now):          # zamknięcie tygodnia jeszcze przed nami
        koniec -= 7 * 86_400_000
    klucz = (coin, koniec)
    if klucz not in _ZAMROZONE:
        try:
            sw = markets.candles_1m(coin, koniec - 30 * 60_000, koniec - 1)
        except Exception:
            return None
        if not sw:
            return None
        _ZAMROZONE[klucz] = sw[-1]["c"]
    return _ZAMROZONE[klucz]


def _zamrozony(m: Market, t_ms: int) -> bool:
    return m.cls != "crypto" and tradebot.is_weekend(_dt(t_ms))


# --------------------------------------------------------------------------- #
#  Wycena i marża                                                              #
# --------------------------------------------------------------------------- #
def liq_price(m: Market, side: str, entry: float, lev: float) -> float:
    """Cena, przy której izolowana marża (bez utrzymaniowej) jest stracona."""
    k = max(0.0, 1.0 / max(lev, 1.0) - MMR)
    if m.quote_inverse:
        return entry / (1 + k) if side == "buy" else (entry / (1 - k) if k < 1 else float("inf"))
    return entry * (1 - k) if side == "buy" else entry * (1 + k)


def _open_positions(session, acc: Account) -> list[Trade]:
    return (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "open", Trade.source == "terminal")
            .order_by(Trade.id).all())


def _open_orders(session, acc: Account) -> list[TerminalOrder]:
    return (session.query(TerminalOrder)
            .filter(TerminalOrder.account_id == acc.id, TerminalOrder.status == "open")
            .order_by(TerminalOrder.id).all())


def _inne_otwarte_pnl(session, acc: Account) -> float:
    """Floating pozycji spoza terminala (Trade BOT) — też siedzi w equity."""
    rows = (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "open", Trade.source != "terminal")
            .all())
    return sum(float(t.pnl or 0.0) for t in rows)


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
    inne = _inne_otwarte_pnl(session, acc)
    balance = float(acc.balance or acc.initial_balance or 0.0)
    equity = balance + floating + inne
    return {"balance": round(balance, 2), "floating": round(floating, 2), "other_floating": round(inne, 2),
            "equity": round(equity, 2), "margin_used": round(margin, 2),
            "available": round(max(0.0, equity - margin), 2), "open_lots": round(lots, 4)}


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
            tp=tp, sl=sl, liq_price=liq_price(m, side, px, lev), fee=round(oplata, 4),
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
        pos.liq_price = liq_price(m, side, pos.open_price, pos.leverage or lev)
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
    potrzeba = markets.notional_usd(m, przyrost, px) / lev * (1 + FEE_PCT * lev)
    if potrzeba > fig["available"] + 0.01:
        raise HTTPException(409, f"Not enough available margin: this order needs ${potrzeba:,.2f}, "
                                 f"you have ${fig['available']:,.2f}.")
    limit = float(acc.max_lots or catalog.max_lots_for(acc.initial_balance or 0.0))
    if limit > 0 and fig["open_lots"] + markets.lots(m, przyrost) > limit + 1e-9:
        raise HTTPException(409, f"Exposure cap: {limit:g} lots open in total on this account "
                                 f"(you have {fig['open_lots']:.2f}).")


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


def sync(session, acc: Account, now: datetime | None = None) -> tuple[list[Trade], dict[str, float], float | None]:
    """Rozstrzyga wszystko, co wydarzyło się od ostatniego przeglądu.

    Zwraca (otwarte pozycje, ceny wyceny, najniższe equity w oknie albo None).
    Najniższe equity liczymy z ekstremów świec po stronie niekorzystnej — reguły
    sprawdzają je osobno, bo dzienna strata liczy się w każdej minucie, a nie
    tylko w chwili, w której ktoś akurat otworzył stronę.
    """
    now = now or _now()
    poz = _open_positions(session, acc)
    zlec = _open_orders(session, acc)
    if not poz and not zlec:
        acc.terminal_open = False
        acc.terminal_sync_at = now
        return [], {}, None
    teraz_ms = _ms(now)
    pocz = acc.terminal_sync_at or min([t.opened_at for t in poz] + [z.created_at for z in zlec])
    start_ms = max(_ms(pocz) - 60_000, teraz_ms - MAX_WINDOW_MS)
    coins = {markets.get(s).hl for s in {t.symbol for t in poz} | {z.symbol for z in zlec} if markets.get(s)}

    swiece: dict[str, dict[int, dict]] = {}
    if teraz_ms - _ms(pocz) > CANDLE_GAP_S * 1000:
        for coin in coins:
            try:
                swiece[coin] = {c["t"]: c for c in markets.candles_1m(coin, start_ms, teraz_ms)}
            except Exception as e:  # pragma: no cover - siec
                print(f"[terminal] świece {coin}: {e}", flush=True)
    try:
        mids = markets.mids(coins)
    except Exception:
        mids = {}
    # Bieżąca chwila jako ostatnia „świeca" z samym midem — łapie to, co stało
    # się od ostatniej pełnej minuty.
    for coin, px in mids.items():
        swiece.setdefault(coin, {})[teraz_ms - 1] = {"t": teraz_ms - 1, "o": px, "h": px, "l": px, "c": px}

    najnizsze: float | None = None
    ostatnia: dict[str, float] = {}
    inne = _inne_otwarte_pnl(session, acc)
    # Listy w pamięci: zapytanie do bazy tylko po zdarzeniu (okno ma do 4800 minut).
    zl_lista = zlec
    for t in sorted({t for sw in swiece.values() for t in sw}):
        kiedy = min(_dt(t + 59_999), now) if t != teraz_ms - 1 else now
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
            for z in zl_lista:
                if z.status == "open" and z.symbol == m.sym and _ms(z.created_at) < t:
                    px = _wyzwolenie_zlecenia(z, c)
                    if px is not None:
                        _wypelnij_oczekujace(session, acc, z, px, kiedy)
                        zmiana = True
                        poz = _open_positions(session, acc)
            for pos in poz:
                if pos.status != "open" or pos.symbol != m.sym or _ms(pos.opened_at) >= t:
                    continue
                zd = _zdarzenia_pozycji(pos, m, c)
                if zd is None:
                    continue
                cena, powod = zd
                if powod in ("tp", "sl") and _ms(pos.tpsl_at or pos.opened_at) >= t:
                    continue
                _close_part(session, acc, pos, pos.qty or 0.0, cena, powod, kiedy)
                zmiana = True
        if zmiana:
            session.flush()
            poz = _open_positions(session, acc)
        # equity na najgorszym ekstremum tej minuty
        ruch = 0.0
        for pos in poz:
            m = markets.get(pos.symbol)
            c = swiece.get(m.hl, {}).get(t) if m else None
            if m is None or (c is None and m.hl not in ostatnia):
                continue
            if _zamrozony(m, t):
                px = _cena_weekendowa(m.hl, now) or ostatnia.get(m.hl)
            elif c is not None:
                px = c["l"] if pos.side == "buy" else c["h"]
            else:
                px = ostatnia[m.hl]
            if px:
                ruch += markets.pnl_usd(m, pos.side, pos.qty or 0.0, pos.open_price, px)
        eq = float(acc.balance or 0.0) + ruch + inne
        najnizsze = eq if najnizsze is None else min(najnizsze, eq)

    poz = _open_positions(session, acc)
    acc.terminal_open = bool(poz or _open_orders(session, acc))
    acc.terminal_sync_at = now
    marks = _marks(session, acc, poz, now)
    # Dołek zostaje przy obiekcie do końca requestu: reguły (`snapshot_for`)
    # robią własny przegląd, który widzi już tylko ostatnie sekundy — bez tego
    # dołek z pierwszego przeglądu by przepadł.
    if najnizsze is not None:
        stary = acc.__dict__.get("_terminal_dolek")
        acc.__dict__["_terminal_dolek"] = najnizsze if stary is None else min(stary, najnizsze)
    return poz, marks, najnizsze


def snapshot_for(session, acc: Account, bot_snap: MarketSnapshot | None) -> tuple[MarketSnapshot, float | None]:
    """Snapshot equity dla pollera: księga terminala (+ bot, jeśli konto go ma)."""
    przed = float(acc.balance or 0.0)
    poz, marks, _ = sync(session, acc)
    najnizsze = acc.__dict__.pop("_terminal_dolek", None)
    fig = account_figures(session, acc, poz, marks)
    lots = fig["open_lots"]
    if bot_snap is not None:
        # Bot policzył saldo z `acc.balance` sprzed przeglądu (plus swoje
        # zamknięcie); dokładamy to, co terminal zrealizował w tym przeglądzie,
        # i floating terminala.
        saldo = round(bot_snap.balance + float(acc.balance or 0.0) - przed, 2)
        if najnizsze is not None:
            # Dołek liczył floating bota z jego wiersza (już po ticku bota),
            # brakuje mu tylko zamknięcia bota, którego nie ma jeszcze w saldzie.
            najnizsze += saldo - float(acc.balance or 0.0)
        snap = MarketSnapshot(
            balance=saldo, equity=round(saldo + bot_snap.open_pnl + fig["floating"], 2),
            open_pnl=round(bot_snap.open_pnl + fig["floating"], 2),
            has_open_position=bot_snap.has_open_position or bool(poz),
            volume_lots=round(bot_snap.volume_lots + lots, 4), volume_known=True)
    else:
        snap = MarketSnapshot(balance=fig["balance"], equity=fig["equity"], open_pnl=fig["floating"],
                              has_open_position=bool(poz), volume_lots=lots, volume_known=True)
    return snap, najnizsze


def flatten(session, acc: Account, reason: str) -> int:
    """Zamyka wszystko po bieżących cenach i anuluje zlecenia (breach)."""
    now = _now()
    poz = _open_positions(session, acc)
    marks = _marks(session, acc, poz, now)
    n = 0
    for pos in poz:
        px = marks.get(pos.symbol)
        if px is None:
            continue
        _close_part(session, acc, pos, pos.qty or 0.0, px, reason, now)
        n += 1
    for z in _open_orders(session, acc):
        z.status, z.done_at, z.note = "cancelled", now, "Account closed"
    session.flush()
    acc.terminal_open = bool(_open_positions(session, acc))
    return n


def evaluate_low(cfg, rt, tick, najnizsze: float | None):
    """Reguły na najniższym equity okna — na KOPII stanu konta.

    Gdy dołek nie łamie limitu, nic się nie zmienia (bez fałszywego szczytu
    czy baseline'u dnia). Gdy łamie — wynik i stan z kopii zastępują zwykły."""
    if najnizsze is None or najnizsze >= tick.equity - 0.005:
        return None
    kopia = copy.deepcopy(rt)
    dol = copy.copy(tick)
    dol.equity = round(najnizsze, 2)
    dol.open_pnl = round(najnizsze - tick.balance, 2)
    res = rules.evaluate(cfg, kopia, dol)
    return (res, kopia) if res.failed else None


# --------------------------------------------------------------------------- #
#  Dostęp: tylko sesja konta PTF (ta sama co portal)                           #
# --------------------------------------------------------------------------- #
class Who:
    def __init__(self, trader_id: int):
        self.trader_id = trader_id


def who(request: Request, authorization: str | None = Header(default=None)) -> Who:
    return Who(auth.current_trader(request, authorization).id)


def _konto(session, w: Who, account_id: int | None) -> Account:
    if not account_id:
        raise HTTPException(400, "Choose an account")
    acc = session.get(Account, int(account_id))
    if acc is None or acc.trader_id != w.trader_id:
        raise HTTPException(404, "Account not found")
    return acc


def _blokada_handlu(acc: Account) -> str | None:
    if acc.status not in TRADABLE:
        return "This account is closed for trading."
    if acc.metaapi_account_id:
        return "This account is tracked live from MetaTrader 5. Place trades in MT5."
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
        "leverage": t.leverage, "margin": t.margin, "liq": t.liq_price, "tp": t.tp, "sl": t.sl,
        "fee": round(t.fee or 0.0, 2), "slippage": t.slippage, "opened_at": _iso(t.opened_at),
    }


def _closed_dict(t: Trade) -> dict:
    return {"id": t.id, "symbol": t.symbol, "side": t.side, "qty": t.qty, "entry": t.open_price,
            "close": t.close_price, "pnl": round(t.pnl or 0.0, 2), "fee": round(t.fee or 0.0, 2),
            "reason": t.close_reason, "leverage": t.leverage, "opened_at": _iso(t.opened_at),
            "closed_at": _iso(t.closed_at)}


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
            "weekend_trading": bool(acc.weekend_trading)}


def state_dict(session, acc: Account, poz: list[Trade], marks: dict[str, float]) -> dict:
    fig = account_figures(session, acc, poz, marks)
    cfg = rules.config_from_account(acc)
    met = rules.display_metrics(cfg, balance=fig["balance"], equity=fig["equity"],
                                peak_equity=float(acc.peak_equity or acc.initial_balance or 0.0),
                                day_start_equity=float(acc.day_start_equity or acc.initial_balance or 0.0),
                                trading_days=int(acc.trading_days_count or 0))
    zamk = (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.source == "terminal", Trade.status == "closed")
            .order_by(Trade.closed_at.desc(), Trade.id.desc()).limit(100).all())
    hist_z = (session.query(TerminalOrder)
              .filter(TerminalOrder.account_id == acc.id, TerminalOrder.status != "open")
              .order_by(TerminalOrder.id.desc()).limit(100).all())
    return {
        "account": {**_konto_dict(acc), **fig, "metrics": met,
                    "max_lots": float(acc.max_lots or catalog.max_lots_for(acc.initial_balance or 0.0)),
                    "day_start_equity": round(float(acc.day_start_equity or 0.0), 2)},
        "positions": [_pos_dict(t, marks) for t in poz],
        "orders": [_order_dict(z) for z in _open_orders(session, acc)],
        "history": [_closed_dict(t) for t in zamk],
        "order_history": [_order_dict(z) for z in hist_z],
        "fee_pct": FEE_PCT * 100, "server_time": _iso(_now()),
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
@router.get("/markets")
def api_markets():
    return {"markets": markets.public_list(), "fee_pct": FEE_PCT * 100, "max_slippage_pct": MAX_SLIPPAGE * 100,
            "weekend": tradebot.is_weekend()}


@router.get("/accounts")
def api_accounts(w: Who = Depends(who)):
    session = SessionLocal()
    try:
        konta = (session.query(Account).filter(Account.trader_id == w.trader_id)
                 .order_by(Account.id.desc()).all())
        return {"accounts": [_konto_dict(a) for a in konta if a.status in TRADABLE + ("passed",)]}
    finally:
        session.close()


@router.get("/state")
def api_state(account_id: int | None = None, w: Who = Depends(who)):
    def run(session):
        acc = _konto(session, w, account_id)
        if acc.terminal_open:
            przed = (round(float(acc.balance or 0.0), 2), len(_open_positions(session, acc)),
                     len(_open_orders(session, acc)))
            poz, marks, dolek = sync(session, acc)
            po = (round(float(acc.balance or 0.0), 2), len(poz), len(_open_orders(session, acc)))
            ostatnia = acc.terminal_eval_at
            if ostatnia is not None and ostatnia.tzinfo is None:
                ostatnia = ostatnia.replace(tzinfo=timezone.utc)
            ocenic = (po != przed or ostatnia is None
                      or _now() - ostatnia > timedelta(seconds=EVAL_EVERY_S)
                      or (dolek is not None and dolek <= _najwyzsza_podloga(acc)))
            session.flush()
            if ocenic and acc.terminal_used:
                _ocen(session, acc)
            session.commit()
        poz = _open_positions(session, acc)
        marks = _marks(session, acc, poz, _now())
        return state_dict(session, acc, poz, marks)
    return _z_ponowieniem(run)


class OrderIn(BaseModel):
    account_id: int | None = None
    symbol: str
    side: str = Field(pattern="^(buy|sell)$")
    kind: str = Field(default="market", pattern="^(market|limit|stop)$")
    qty: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    leverage: float = Field(default=1.0, ge=1)
    tp: float | None = Field(default=None, ge=0)
    sl: float | None = Field(default=None, ge=0)
    reduce_only: bool = False
    max_slippage_pct: float = Field(default=MAX_SLIPPAGE * 100, gt=0, le=10)


@router.post("/orders")
def api_order(payload: OrderIn, w: Who = Depends(who)):
    m = markets.get(payload.symbol)
    if m is None:
        raise HTTPException(404, "Unknown market")
    if payload.leverage > m.max_lev:
        raise HTTPException(400, f"Max leverage on {m.sym} is {m.max_lev}x.")
    tp = payload.tp or None
    sl = payload.sl or None

    def run(session):
        acc = _konto(session, w, payload.account_id)
        powod = _blokada_handlu(acc)
        if powod:
            raise HTTPException(409, powod)
        if acc.terminal_open:
            sync(session, acc)
        teraz = _now()
        pos = next((t for t in _open_positions(session, acc) if t.symbol == m.sym), None)
        zmniejsza = pos is not None and pos.side != payload.side and payload.qty <= (pos.qty or 0) + EPS
        if not (payload.reduce_only or zmniejsza):
            zamk = market_closed(m, acc, teraz)
            if zamk:
                raise HTTPException(409, zamk)
        qty = round(payload.qty, m.qty_dec + 3)
        if payload.kind == "market":
            vwap, mid, slip = _vwap(m, payload.side, qty, payload.max_slippage_pct / 100)
            _tp_sl_ok(payload.side, vwap, tp, sl)
            if not payload.reduce_only:
                _check_open(session, acc, m, payload.side, qty, vwap, payload.leverage)
            z = TerminalOrder(account_id=acc.id, symbol=m.sym, side=payload.side, kind="market", qty=qty,
                              leverage=payload.leverage, tp=tp, sl=sl, reduce_only=payload.reduce_only,
                              created_at=teraz)
            session.add(z)
            p = _fill(session, acc, m, payload.side, qty, vwap, payload.leverage, tp, sl,
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
                _check_open(session, acc, m, payload.side, qty, payload.price, payload.leverage)
            session.add(TerminalOrder(account_id=acc.id, symbol=m.sym, side=payload.side, kind=payload.kind,
                                      qty=qty, price=payload.price, leverage=payload.leverage, tp=tp, sl=sl,
                                      reduce_only=payload.reduce_only, created_at=teraz))
        acc.terminal_used = True
        acc.terminal_open = True
        acc.terminal_sync_at = acc.terminal_sync_at or teraz
        session.flush()
        _ocen(session, acc)
        session.commit()
        session.refresh(acc)
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


@router.delete("/orders/{order_id}")
def api_cancel(order_id: int, account_id: int | None = None, w: Who = Depends(who)):
    def run(session):
        acc = _konto(session, w, account_id)
        z = session.get(TerminalOrder, order_id)
        if z is None or z.account_id != acc.id or z.status != "open":
            raise HTTPException(404, "Order not found")
        z.status, z.done_at, z.note = "cancelled", _now(), "Cancelled by you"
        acc.terminal_open = bool(_open_positions(session, acc) or
                                 [o for o in _open_orders(session, acc) if o.id != z.id])
        session.commit()
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)


class CloseIn(BaseModel):
    account_id: int | None = None
    qty: float | None = Field(default=None, gt=0)
    max_slippage_pct: float = Field(default=MAX_SLIPPAGE * 100, gt=0, le=10)


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
def api_close(trade_id: int, payload: CloseIn, w: Who = Depends(who)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
        if acc.status not in TRADABLE:
            raise HTTPException(409, "This account is closed for trading.")
        sync(session, acc)
        pos = session.get(Trade, trade_id)
        if pos is None or pos.account_id != acc.id or pos.status != "open" or pos.source != "terminal":
            raise HTTPException(404, "Position already closed")
        qty = min(payload.qty or pos.qty or 0.0, pos.qty or 0.0)
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
def api_close_all(payload: AllIn, w: Who = Depends(who)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
        if acc.status not in TRADABLE:
            raise HTTPException(409, "This account is closed for trading.")
        sync(session, acc)
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
def api_cancel_all(payload: AllIn, w: Who = Depends(who)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
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
    tp: float | None = Field(default=None, ge=0)
    sl: float | None = Field(default=None, ge=0)


@router.patch("/positions/{trade_id}")
def api_tpsl(trade_id: int, payload: TpSlIn, w: Who = Depends(who)):
    def run(session):
        acc = _konto(session, w, payload.account_id)
        pos = session.get(Trade, trade_id)
        if pos is None or pos.account_id != acc.id or pos.status != "open" or pos.source != "terminal":
            raise HTTPException(404, "Position already closed")
        m = markets.get(pos.symbol)
        try:
            mid = markets.mids({m.hl}).get(m.hl) or pos.open_price
        except Exception:
            mid = pos.open_price
        tp = payload.tp or None
        sl = payload.sl or None
        _tp_sl_ok(pos.side, mid, tp, sl)
        pos.tp, pos.sl, pos.tpsl_at = tp, sl, _now()
        session.commit()
        poz = _open_positions(session, acc)
        return state_dict(session, acc, poz, _marks(session, acc, poz, _now()))
    return _z_ponowieniem(run)
