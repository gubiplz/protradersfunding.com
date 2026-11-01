"""Trade BOT na prawdziwych cenach: wejście i wyjście z już narysowanego wykresu.

Wynik każdej transakcji bota zostaje z planu (`plan_pnl`) co do centa, a cena
i godzina wejścia oraz wyjścia leżą na prawdziwych świecach Hyperliquid — tych
samych, które rysuje terminal. Razem da się to mieć tylko z perspektywy po
fakcie: na rynku, którego przyszłości nie znamy, wynik otwartej pozycji jest
losowy. Dlatego w chwili zamknięcia bot patrzy na wykres od startu planu
i wybiera moment wejścia, z którego ruch do obecnej ceny daje dokładnie
zaplanowany wynik. W trakcie trzymania pozycji klient jej nie widzi — pojawia
się w historii po zamknięciu (decyzja właściciela, 2026-11-01).

Instrumenty bez notowań w terminalu (AUDUSD, USDCAD, US30, GER40) bot w tym
trybie pomija — ich ceny nie miałyby się z czym zgadzać.
"""
from __future__ import annotations

import math
import os
import random
import time
from datetime import datetime, timedelta, timezone

from . import markets

# Ile świec 1m Hyperliquid trzyma wstecz (5000 sztuk ≈ 3,47 dnia) — starsze okna
# (dogrywka historii) idą na grubszych świecach, tak jak wykres terminala.
_ZASIEG = (("1m", 3.3 * 86_400_000), ("5m", 16.5 * 86_400_000), ("15m", 50 * 86_400_000), ("1h", 200 * 86_400_000))
_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}
_DZIEN_MS = 86_400_000

# Najkrótsze trzymanie: wejście co najmniej tyle przed wyjściem (i ułamek planu).
MIN_HOLD_MS = 5 * 60_000
MIN_HOLD_UDZIAL = 0.25


def wlaczone() -> bool:
    """Tryb żywych cen (domyślnie włączony). Testy silnika bota wyłączają go
    w conftest — tam ceny są syntetyczne i bez sieci."""
    return os.getenv("BOT_LIVE_PRICES", "true").lower() == "true"


def rynek(symbol: str) -> markets.Market | None:
    """Rynek terminala dla instrumentu bota albo None (instrument bez notowań)."""
    return markets.get(symbol)


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# --------------------------------------------------------------------------- #
#  Świece z pamięcią: dogrywka pyta o te same dni dziesiątki razy              #
# --------------------------------------------------------------------------- #
_CACHE: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
_CACHE_MAX = 600
_SWIEZE_S = 30.0          # dzień „dzisiejszy" (świece wciąż dochodzą) — krótko


def _interwal(start_ms: int, teraz_ms: int) -> str:
    wiek = teraz_ms - start_ms
    for iv, zasieg in _ZASIEG:
        if wiek <= zasieg:
            return iv
    return "1h"


def _dzien(coin: str, iv: str, dzien: int, teraz_ms: int) -> list[dict]:
    klucz = (coin, iv, dzien)
    koniec = (dzien + 1) * _DZIEN_MS
    hit = _CACHE.get(klucz)
    zamkniety = koniec < teraz_ms - _MS[iv]
    if hit and (zamkniety or time.monotonic() - hit[0] < _SWIEZE_S):
        return hit[1]
    dane = markets.candles(coin, iv, dzien * _DZIEN_MS, koniec - 1)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[klucz] = (time.monotonic(), dane)
    return dane


def swiece(coin: str, start_ms: int, end_ms: int, teraz_ms: int | None = None) -> tuple[str, list[dict]]:
    """(interwał, świece z [start, end]) — interwał dobrany do wieku okna."""
    teraz_ms = teraz_ms or int(time.time() * 1000)
    iv = _interwal(start_ms, teraz_ms)
    out: list[dict] = []
    for dzien in range(start_ms // _DZIEN_MS, end_ms // _DZIEN_MS + 1):
        out.extend(c for c in _dzien(coin, iv, dzien, teraz_ms) if start_ms - _MS[iv] < c["t"] <= end_ms)
    out.sort(key=lambda c: c["t"])
    return iv, out


# --------------------------------------------------------------------------- #
#  Zamknięcie: wejście z wykresu, wyjście po bieżącej cenie, wynik z planu      #
# --------------------------------------------------------------------------- #
def _krok(step: float) -> int:
    return max(0, -int(math.floor(math.log10(step) + 1e-9)))


def _do_ticka(v: float, step: float, w_gore: bool) -> float:
    n = v / step
    n = math.ceil(n - 1e-9) if w_gore else math.floor(n + 1e-9)
    return round(n * step, _krok(step))


def zamknij(tr, inst, pv_fn, pnl: float, wyjscie: datetime, max_lots: float | None,
            rng: random.Random, teraz: datetime | None = None) -> bool:
    """Zamyka plan `tr` na prawdziwych cenach z wynikiem `pnl`.

    Wyjście: cena świecy, w której wypada `wyjscie`. Wejście: moment w oknie
    [start planu, wyjście − minimalne trzymanie], którego świeca zawiera cenę
    dającą dokładnie `pnl` (strona do wyboru — w górę albo w dół). Gdy ruch
    w oknie jest za mały, rośnie wolumen (do limitu lotów), bo mniejszy ruch na
    większej pozycji daje ten sam wynik. Zwraca False, gdy nie ma danych albo
    wyniku nie da się osiągnąć — wtedy wołający czeka na kolejny tick.
    """
    m = rynek(tr.symbol)
    if m is None:
        return False
    start_ms, wyj_ms = _ms(tr.opened_at), _ms(wyjscie)
    teraz_ms = _ms(teraz) if teraz else int(time.time() * 1000)
    try:
        iv, sw = swiece(m.hl, start_ms, wyj_ms, teraz_ms)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[bot-ceny] świece {m.hl}: {e}", flush=True)
        return False
    if len(sw) < 2:
        return False
    krok_ms = _MS[iv]
    ost = next((c for c in reversed(sw) if c["t"] <= wyj_ms), None)
    if ost is None:
        return False
    step = inst.tick
    p_x = round(round(ost["c"] / step) * step, _krok(step))
    trzymanie = max(MIN_HOLD_MS, int((wyj_ms - start_ms) * MIN_HOLD_UDZIAL))
    okno = [c for c in sw if c["t"] >= start_ms - krok_ms + 1 and c["t"] + krok_ms <= wyj_ms - trzymanie]
    if not okno:
        return False

    def proba(lots: float) -> tuple[str, dict, float] | None:
        strony = ["buy", "sell"]
        rng.shuffle(strony)
        for strona in strony:
            sign = 1 if strona == "buy" else -1
            if not lots:
                return None
            # pnl = sign·(p_x − p_e)·lots·pv(p_e)  →  p_e = p_x − sign·pnl/(lots·pv).
            # pv zależy od ceny tylko na USDJPY — dwa przybliżenia wystarczą.
            p_e_dokl = p_x
            for _ in range(2):
                pv = pv_fn(inst, p_e_dokl)
                if not pv:
                    return None
                p_e_dokl = p_x - sign * pnl / (lots * pv)
            if p_e_dokl <= 0:
                continue
            # Wejście zaokrąglone w stronę wyjścia: wynik nigdy nie przebija planu
            # (pod sufitem fazy to decyduje, czy konto zda — jak w `_close_trade`).
            p_e = _do_ticka(p_e_dokl, step, p_e_dokl < p_x)
            pasujace = [c for c in okno if c["l"] <= p_e <= c["h"]]
            if pasujace:
                # Wcześniejsze wejścia częściej — trzymanie bliższe planowanemu.
                wagi = [1.0 + 2.0 * (1 - i / max(1, len(pasujace))) for i in range(len(pasujace))]
                return strona, rng.choices(pasujace, weights=wagi, k=1)[0], p_e
        return None

    lots = float(tr.lots or 0.0)
    wynik = proba(lots) if abs(pnl) >= 0.005 else None
    if wynik is None and abs(pnl) >= 0.005:
        # Za mały ruch w oknie: wolumen tak duży, żeby największy dostępny ruch
        # (w którąkolwiek stronę) dał plan — w górę do 0,01 lota, więc ruch
        # potrzebny jest nieco mniejszy od dostępnego.
        zakres = max(max(c["h"] for c in okno) - p_x, p_x - min(c["l"] for c in okno), 0.0)
        pv = pv_fn(inst, p_x)
        if zakres > step and pv:
            potrzebne = math.ceil(abs(pnl) / (zakres * 0.97 * pv) * 100) / 100
            sufit = float(max_lots) if max_lots else potrzebne
            if lots < potrzebne <= sufit:
                lots = potrzebne
                wynik = proba(lots)
    if abs(pnl) < 0.005:
        # Wynik zerowy (np. zatrzymanie bota na starcie planu): wejście i wyjście w tej samej cenie.
        pasujace = [c for c in okno if c["l"] <= p_x <= c["h"]]
        if not pasujace:
            return False
        wynik = (rng.choice(["buy", "sell"]), rng.choice(pasujace), p_x)
    if wynik is None:
        return False

    strona, c_we, p_e = wynik
    sign = 1 if strona == "buy" else -1
    # Godzina wejścia w obrębie świecy, z której pochodzi cena (i nie przed startem planu).
    lo = max(start_ms, c_we["t"])
    hi = max(lo + 1, min(c_we["t"] + krok_ms - 1_000, wyj_ms - trzymanie))
    t_we = rng.randint(lo, hi)
    tr.side = strona
    tr.lots = lots
    tr.open_price = p_e
    tr.opened_at = _dt(t_we)
    tr.close_price = p_x
    tr.closed_at = wyjscie
    tr.pnl = round(sign * (p_x - p_e) * lots * pv_fn(inst, p_e), 2)
    tr.status = "closed"
    tr.venue = "hyperliquid"
    return True


def ostatnia_cena(symbol: str, kiedy: datetime, teraz: datetime | None = None) -> float | None:
    """Cena instrumentu w chwili `kiedy` (zamknięcie świecy) — do wycen awaryjnych."""
    m = rynek(symbol)
    if m is None:
        return None
    k = _ms(kiedy)
    try:
        _, sw = swiece(m.hl, k - 3_600_000, k, _ms(teraz) if teraz else None)
    except Exception:  # pragma: no cover - sieć
        return None
    ost = next((c for c in reversed(sw) if c["t"] <= k), None)
    return ost["c"] if ost else None


def okno_wyjscia(tr, now: datetime, koniec_tygodnia: datetime | None) -> datetime:
    """Chwila wyjścia: zaplanowane zamknięcie (dogrywka przeskakuje ticki co
    ~20 min), nie później niż teraz i — poza krypto — niż zamknięcie tygodnia."""
    wyj = now
    if tr.plan_close_at is not None and _ms(tr.plan_close_at) < _ms(now):
        wyj = tr.plan_close_at if tr.plan_close_at.tzinfo else tr.plan_close_at.replace(tzinfo=timezone.utc)
    if koniec_tygodnia is not None and _ms(koniec_tygodnia) < _ms(wyj):
        wyj = koniec_tygodnia - timedelta(minutes=1)
    return wyj
