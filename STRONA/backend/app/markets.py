"""Rynki terminala PTF i ceny z Hyperliquid po stronie serwera.

Terminal w przeglądarce bierze dane prosto z giełd (WebSocket), a serwer pyta
o cenę tylko wtedy, gdy musi coś rozliczyć: wypełnić zlecenie, sprawdzić
TP/SL, policzyć equity do reguł. Rozliczamy po cenach Hyperliquid, bo to
jedyne z trzech źródeł osiągalne z serwerów Vercela w USA — Binance i Bybit
odcinają amerykańskie IP (451/403). Wykres i księgę z Binance albo Bybit
trader może sobie włączyć, ale fill i tak idzie po Hyperliquid.

Nazwy symboli są jak w MT5 (te same co na landingu), żeby `openTerminal(sym)`
z karty rynku trafiał w ten sam instrument.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import httpx

HL_INFO = "https://api.hyperliquid.xyz/info"
_TIMEOUT = httpx.Timeout(4.0, connect=3.0)


@dataclass(frozen=True)
class Market:
    sym: str            # symbol PTF/MT5, np. BTCUSD, XAUUSD
    name: str
    cls: str            # crypto|metal|energy|index|fx
    hl: str             # moneta Hyperliquid (HIP-3: "xyz:GOLD")
    bn: str | None      # Binance USDⓈ-M
    by: str | None      # Bybit linear
    contract: float     # jednostek na 1 lot (limit lotów konta)
    px_dec: int         # miejsca po przecinku ceny
    qty_dec: int        # miejsca po przecinku ilości
    max_lev: int
    icon: str           # klucz ikony (static/img/markets) albo "" = inicjały
    quote_inverse: bool = False   # USDJPY: zysk w JPY, przeliczany na USD po cenie zamknięcia
    base: str = ""      # jednostka ilości w UI (BTC, oz, bbl…)


def _c(sym, name, hl, bn, by, px_dec, qty_dec, icon, max_lev=10, base=None):
    return Market(sym, name, "crypto", hl, bn, by, 1.0, px_dec, qty_dec, max_lev, icon,
                  base=base or sym[:-3])


MARKETS: list[Market] = [
    _c("BTCUSD", "Bitcoin", "BTC", "BTCUSDT", "BTCUSDT", 1, 5, "btc"),
    _c("ETHUSD", "Ethereum", "ETH", "ETHUSDT", "ETHUSDT", 2, 4, "eth"),
    _c("SOLUSD", "Solana", "SOL", "SOLUSDT", "SOLUSDT", 3, 2, "sol", 5),
    _c("XRPUSD", "XRP", "XRP", "XRPUSDT", "XRPUSDT", 4, 0, "xrp", 5),
    _c("LTCUSD", "Litecoin", "LTC", "LTCUSDT", "LTCUSDT", 2, 2, "ltc", 5),
    _c("DOGEUSD", "Dogecoin", "DOGE", "DOGEUSDT", "DOGEUSDT", 5, 0, "", 5),
    _c("BNBUSD", "BNB", "BNB", "BNBUSDT", "BNBUSDT", 2, 3, "", 5),
    _c("ADAUSD", "Cardano", "ADA", "ADAUSDT", "ADAUSDT", 4, 0, "", 5),
    _c("AVAXUSD", "Avalanche", "AVAX", "AVAXUSDT", "AVAXUSDT", 3, 2, "", 5),
    _c("LINKUSD", "Chainlink", "LINK", "LINKUSDT", "LINKUSDT", 3, 1, "", 5),
    _c("SUIUSD", "Sui", "SUI", "SUIUSDT", "SUIUSDT", 4, 1, "", 5),
    _c("HYPEUSD", "Hyperliquid", "HYPE", "HYPEUSDT", "HYPEUSDT", 3, 2, "", 5),
    Market("XAUUSD", "Gold", "metal", "xyz:GOLD", "XAUUSDT", "XAUUSDT", 100.0, 2, 4, 50, "xau", base="oz"),
    Market("XAGUSD", "Silver", "metal", "xyz:SILVER", "XAGUSDT", None, 5000.0, 3, 2, 50, "xag", base="oz"),
    Market("XPTUSD", "Platinum", "metal", "xyz:PLATINUM", "XPTUSDT", None, 100.0, 1, 3, 50, "xpt", base="oz"),
    Market("USOIL", "WTI Crude Oil", "energy", "xyz:CL", "CLUSDT", None, 1000.0, 2, 3, 20, "oil", base="bbl"),
    Market("UKOIL", "Brent Crude Oil", "energy", "xyz:BRENTOIL", None, None, 1000.0, 2, 3, 20, "oil", base="bbl"),
    Market("NATGAS", "Natural Gas", "energy", "xyz:NATGAS", None, None, 10000.0, 3, 1, 20, "gas", base="MMBtu"),
    Market("SPX500", "S&P 500", "index", "xyz:SP500", None, None, 1.0, 1, 3, 50, "us", base="units"),
    Market("NAS100", "Nasdaq 100", "index", "xyz:XYZ100", None, None, 1.0, 1, 3, 50, "us", base="units"),
    Market("JPN225", "Nikkei 225", "index", "xyz:JP225", None, None, 1.0, 0, 3, 50, "jp", base="units"),
    Market("EURUSD", "Euro / US Dollar", "fx", "xyz:EUR", None, None, 100_000.0, 5, 0, 100, "eu-us", base="EUR"),
    Market("GBPUSD", "British Pound / US Dollar", "fx", "xyz:GBP", None, None, 100_000.0, 5, 0, 100, "gb-us", base="GBP"),
    Market("USDJPY", "US Dollar / Japanese Yen", "fx", "xyz:JPY", None, None, 100_000.0, 3, 0, 100, "us-jp",
           quote_inverse=True, base="USD"),
]
BY_SYM: dict[str, Market] = {m.sym: m for m in MARKETS}
BY_COIN: dict[str, Market] = {m.hl: m for m in MARKETS}


def get(sym: str | None) -> Market | None:
    return BY_SYM.get((sym or "").strip().upper())


def public_list() -> list[dict]:
    return [asdict(m) for m in MARKETS]


# --------------------------------------------------------------------------- #
#  P&L i wielkości                                                             #
# --------------------------------------------------------------------------- #
def notional_usd(m: Market, qty: float, px: float) -> float:
    """Wartość pozycji w USD. USDJPY: ilość jest w dolarach."""
    return qty if m.quote_inverse else qty * px


def pnl_usd(m: Market, side: str, qty: float, entry: float, px: float) -> float:
    ruch = (px - entry) if side == "buy" else (entry - px)
    if m.quote_inverse:
        return qty * ruch / px if px else 0.0
    return qty * ruch


def lots(m: Market, qty: float) -> float:
    return qty / m.contract if m.contract else qty


# --------------------------------------------------------------------------- #
#  Hyperliquid — zapytania info                                                #
# --------------------------------------------------------------------------- #
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=_TIMEOUT, headers={"Content-Type": "application/json"})
    return _client


def _post(body: dict):
    r = _http().post(HL_INFO, json=body)
    r.raise_for_status()
    return r.json()


_MIDS_CACHE: dict[str, tuple[float, dict]] = {}
_MIDS_TTL = 1.0


def mids(coins: set[str] | list[str]) -> dict[str, float]:
    """Mid każdej z monet. Główna giełda i HIP-3 (`xyz:`) to osobne zapytania."""
    out: dict[str, float] = {}
    dexy = {c.split(":", 1)[0] if ":" in c else "" for c in coins}
    teraz = time.monotonic()
    for dex in dexy:
        hit = _MIDS_CACHE.get(dex)
        if hit and teraz - hit[0] < _MIDS_TTL:
            dane = hit[1]
        else:
            body = {"type": "allMids", "dex": dex} if dex else {"type": "allMids"}
            dane = {k: float(v) for k, v in (_post(body) or {}).items()}
            _MIDS_CACHE[dex] = (teraz, dane)
        for c in coins:
            if c in dane:
                out[c] = dane[c]
    return out


def book(coin: str) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """(bids, asks) jako listy (cena, ilość), najlepsze pierwsze."""
    d = _post({"type": "l2Book", "coin": coin}) or {}
    lv = d.get("levels") or [[], []]
    bids = [(float(x["px"]), float(x["sz"])) for x in lv[0]]
    asks = [(float(x["px"]), float(x["sz"])) for x in lv[1]]
    return bids, asks


def candles_1m(coin: str, start_ms: int, end_ms: int) -> list[dict]:
    """Świece 1m [start, end] jako {t, o, h, l, c}. Hyperliquid oddaje do 5000 sztuk."""
    d = _post({"type": "candleSnapshot",
               "req": {"coin": coin, "interval": "1m", "startTime": int(start_ms), "endTime": int(end_ms)}})
    return [{"t": int(x["t"]), "o": float(x["o"]), "h": float(x["h"]),
             "l": float(x["l"]), "c": float(x["c"])} for x in (d or [])]
