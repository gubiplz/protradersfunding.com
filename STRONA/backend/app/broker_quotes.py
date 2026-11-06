"""Ostatni bid/ask brokera (cTrader) dla rynków, które Hyperliquid kwotuje za szeroko.

Książka xyz FX na Hyperliquid ma kilka pipsów spreadu i parę tysięcy jednostek na
poziomie, więc 1 lot GBPUSD kosztował od razu ~9 pipsów. Feed cTradera (`ctrader_feed`,
w cronie świec) co ZAPIS_CO_S zapisuje tu ostatni bid/ask każdego rynku; `markets.mids`
i `markets.book` biorą je zamiast Hyperliquida:

* wycena (mark, PnL, SL/TP, stop-out): mid brokera, gdy ma najwyżej MARK_MAX_S; starszy
  — mid Hyperliquida, żeby pozycje dalej się wyceniały;
* wypełnienie: bid/ask brokera bez poślizgu, gdy ma najwyżej FILL_MAX_S; starszy —
  zlecenie odrzucone (`Stale`), nie wypełniamy po cenie Hyperliquida.

Czas to chwila zapisu przy żywym połączeniu: spokojna cena dalej jest aktualna.
"""
from __future__ import annotations

import json
import time

KLUCZ = "ctrader_quotes"      # AppSetting: {"at": unix s, "q": {coin: [bid, ask]}}
FILL_MAX_S = 15.0
MARK_MAX_S = 60.0
_TTL = 1.0
_cache: list = [0.0, None]    # [monotonic, dane]


class Stale(RuntimeError):
    """Rynek ma kwotowanie brokera, ale za stare, żeby po nim wypełnić zlecenie."""


def zapisz(kwoty: dict[str, tuple[float, float]], teraz: float | None = None) -> None:
    """Dopisuje kwotowania (coin → (bid, ask)) do zapisanych; `at` = teraz."""
    from .db import SessionLocal
    from .models import AppSetting
    if not kwoty:
        return
    session = SessionLocal()
    try:
        w = session.get(AppSetting, KLUCZ)
        try:
            dane = json.loads(w.value) if w and w.value else {}
        except (ValueError, TypeError):
            dane = {}
        q = dane.get("q") or {}
        q.update({c: [round(b, 6), round(a, 6)] for c, (b, a) in kwoty.items() if b and a and a >= b})
        wartosc = json.dumps({"at": teraz or time.time(), "q": q})
        if w is None:
            session.add(AppSetting(key=KLUCZ, value=wartosc))
        else:
            w.value = wartosc
        session.commit()
    finally:
        session.close()
    wyczysc()


def wyczysc() -> None:
    _cache[0], _cache[1] = 0.0, None


def _dane() -> dict:
    teraz = time.monotonic()
    if _cache[1] is not None and teraz - _cache[0] < _TTL:
        return _cache[1]
    dane: dict = {}
    try:
        from .db import SessionLocal
        from .models import AppSetting
        session = SessionLocal()
        try:
            w = session.get(AppSetting, KLUCZ)
            dane = json.loads(w.value) if w and w.value else {}
        finally:
            session.close()
    except Exception:
        dane = {}
    _cache[0], _cache[1] = teraz, dane
    return dane


def kwoty(coins) -> dict[str, tuple[float, float, float]]:
    """coin → (bid, ask, wiek w s) dla monet, które broker kwotuje."""
    d = _dane()
    q, at = d.get("q") or {}, float(d.get("at") or 0.0)
    wiek = max(0.0, time.time() - at) if at else 1e9
    return {c: (float(q[c][0]), float(q[c][1]), wiek) for c in coins if c in q}
