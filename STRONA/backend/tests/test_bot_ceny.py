"""Trade BOT na prawdziwych cenach (`bot_ceny`): wynik z planu, a wejście
i wyjście leżą na świecach wykresu — z tymi samymi godzinami. Plan w toku
jest dla klienta niewidoczny, transakcja pojawia się po zamknięciu.

Świece są podstawione (deterministyczna fala), więc testy nie dotykają sieci.
"""
import hashlib
import math
import os
import random
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, bot_ceny, markets, terminal, tradebot  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, TerminalLogin, Trade, Trader  # noqa: E402

init_db()

BAZA = {"BTC": 77_000.0, "ETH": 2_270.0, "xyz:GOLD": 3_900.0, "xyz:EUR": 1.11, "xyz:GBP": 1.30,
        "xyz:JPY": 157.0, "xyz:XYZ100": 28_500.0, "xyz:CL": 62.0}
MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def _cena(coin, ms, fala=1.0):
    b = BAZA[coin]
    return b * (1 + fala * (0.012 * math.sin(ms / 3.7e6) + 0.005 * math.sin(ms / 9.1e5 + 1)
                            + 0.002 * math.sin(ms / 1.7e5 + 2)))


def _swiece_z(fala):
    def swiece(coin, interval, start_ms, end_ms):
        krok = MS[interval]
        t = start_ms - start_ms % krok
        out = []
        while t <= end_ms:
            probki = [_cena(coin, t + krok * i / 6, fala) for i in range(7)]
            out.append({"t": t, "o": probki[0], "h": max(probki), "l": min(probki), "c": probki[-1]})
            t += krok
        return out
    return swiece


@pytest.fixture
def zywe(monkeypatch):
    monkeypatch.setenv("BOT_LIVE_PRICES", "true")
    monkeypatch.setattr(tradebot, "is_weekend", lambda now=None: False)
    monkeypatch.setattr(tradebot, "market_closed_for", lambda acc, now=None: False)
    # Test jedzie po ostatnich 30 godzinach (świece 1m sięgają 3,5 dnia wstecz), a te
    # bywają weekendem — zamknięcie tygodnia nie może przycinać planów forex/indeksów.
    monkeypatch.setattr(tradebot, "_week_close", lambda now: now + timedelta(days=7))
    monkeypatch.setattr(markets, "candles", _swiece_z(1.0))
    bot_ceny._CACHE.clear()
    yield monkeypatch
    bot_ceny._CACHE.clear()


def _portal(aid):
    s = SessionLocal()
    t = auth.make_token(s.get(Account, aid).trader_id)
    s.close()
    return {"Authorization": f"Bearer {t}"}


def _sesja(aid):
    s = SessionLocal()
    a = s.get(Account, aid)
    w = TerminalLogin(login=a.platform_login or a.login, account_id=aid, trader_id=a.trader_id, ok=True)
    s.add(w); s.commit()
    t = auth.make_terminal_token(aid, a.platform_password, sid=w.id)
    s.close()
    return t


def _konto(login, **pola):
    s = SessionLocal()
    tr = Trader(email=f"{login}@botceny.test", password_hash=auth.hash_password("haslo1234"),
                full_name="Bot Ceny", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=30)
    acc = Account(login=login, trader_id=tr.id, trader_name="Bot Ceny", product_key="2step-100k",
                  initial_balance=100_000.0, steps=2, max_lots=6.0, balance=100_000.0, equity=100_000.0,
                  peak_equity=100_000.0, day_start_equity=100_000.0, day_start_balance=100_000.0,
                  started_at=start.replace(tzinfo=None), **pola)
    s.add(acc); s.commit()
    tradebot.start(s, acc, style="scalper", pace="busy")
    acc.bot_seed = int.from_bytes(hashlib.sha256(login.encode()).digest()[:4], "big") & 0x7FFFFFFF
    s.commit()
    aid, tid = acc.id, tr.id
    s.close()
    return aid, {"Authorization": f"Bearer {_sesja(aid)}"}, start


def _swieca(coin, kiedy, interval="1m"):
    ms = int(kiedy.replace(tzinfo=kiedy.tzinfo or timezone.utc).timestamp() * 1000)
    krok = MS[interval]
    return _swiece_z(1.0)(coin, interval, ms - ms % krok, ms - ms % krok)[0]


def test_transakcje_bota_na_wykresie_z_wynikiem_z_planu(zywe):
    aid, h, start = _konto("botceny1")
    s = SessionLocal()
    widziane_w_toku = 0
    with TestClient(app) as c:
        t = start
        koniec = datetime.now(timezone.utc) - timedelta(hours=1)
        while t < koniec:
            acc = s.get(Account, aid)
            snap = tradebot.tick(s, acc, t)
            acc.balance = snap.balance
            s.commit()
            otwarta = tradebot._open_trade(s, acc)
            if otwarta is not None:
                assert otwarta.venue == tradebot.PLAN
                assert snap.has_open_position and snap.volume_lots == 0 and snap.open_pnl == 0
                if widziane_w_toku < 3:            # klient planu nie widzi
                    assert c.get(f"/api/me/accounts/{aid}/positions", headers=_portal(aid)).json() == []
                    widziane_w_toku += 1
            t += timedelta(minutes=3)
    zamkniete = s.query(Trade).filter(Trade.account_id == aid, Trade.status == "closed").all()
    assert len(zamkniete) >= 5
    for tr in zamkniete:
        inst = tradebot.INSTRUMENTS[tr.symbol]
        m = bot_ceny.rynek(tr.symbol)
        assert m is not None and tr.venue == "hyperliquid"
        pv = tradebot._point_value(inst, tr.open_price)
        tolerancja = inst.tick * tr.lots * pv + 0.011
        assert abs(tr.pnl - tr.plan_pnl) <= tolerancja, (tr.symbol, tr.pnl, tr.plan_pnl)
        assert abs(tr.pnl) <= abs(tr.plan_pnl) + 0.011          # nigdy ponad plan
        sign = 1 if tr.side == "buy" else -1
        assert tr.pnl == pytest.approx(round(sign * (tr.close_price - tr.open_price) * tr.lots * pv, 2), abs=0.011)
        we, wy = _swieca(m.hl, tr.opened_at), _swieca(m.hl, tr.closed_at)
        assert we["l"] - inst.tick <= tr.open_price <= we["h"] + inst.tick, (tr.symbol, tr.open_price, we)
        assert wy["l"] - inst.tick <= tr.close_price <= wy["h"] + inst.tick, (tr.symbol, tr.close_price, wy)
        assert (tr.closed_at - tr.opened_at) >= timedelta(minutes=5)
    acc = s.get(Account, aid)
    assert acc.balance == pytest.approx(100_000.0 + sum(t.pnl for t in zamkniete), abs=0.05)
    s.close()


def test_stop_zdejmuje_plan_bez_sladu(zywe):
    aid, _, start = _konto("botceny2")
    s = SessionLocal()
    acc = s.get(Account, aid)
    t = start
    while tradebot._open_trade(s, acc) is None and t < start + timedelta(hours=6):
        tradebot.tick(s, acc, t); s.commit()
        t += timedelta(minutes=5)
    assert tradebot._plan_w_toku(tradebot._open_trade(s, acc))
    tradebot.stop(s, acc, t)
    assert s.query(Trade).filter(Trade.account_id == aid).count() == 0
    assert acc.balance == 100_000.0 and not acc.bot_enabled
    s.close()


def test_plan_nie_liczy_sie_do_limitu_lotow_terminala(zywe):
    aid, _, start = _konto("botceny3")
    s = SessionLocal()
    acc = s.get(Account, aid)
    t = start
    while tradebot._open_trade(s, acc) is None and t < start + timedelta(hours=6):
        tradebot.tick(s, acc, t); s.commit()
        t += timedelta(minutes=5)
    assert tradebot._plan_w_toku(tradebot._open_trade(s, acc))
    fig = terminal.account_figures(s, acc, [], {})
    assert fig["other_lots"] == 0 and fig["other_floating"] == 0
    s.close()


def test_terminal_pokazuje_transakcje_bota_a_planu_nie(zywe):
    aid, h, start = _konto("botceny4")
    s = SessionLocal()
    teraz = datetime.now(timezone.utc).replace(microsecond=0)
    s.add(Trade(account_id=aid, symbol="BTCUSD", side="buy", lots=0.3, open_price=77_000.0, close_price=77_500.0,
                pnl=150.0, plan_pnl=150.0, opened_at=teraz - timedelta(hours=3), closed_at=teraz - timedelta(hours=2),
                status="closed", source="bot", venue="hyperliquid"))
    s.add(Trade(account_id=aid, symbol="XAUUSD", side="sell", lots=0.2, open_price=2_385.0, close_price=2_380.0,
                pnl=100.0, opened_at=teraz - timedelta(hours=5), closed_at=teraz - timedelta(hours=4),
                status="closed", source="bot"))                       # starsza, bez venue — też w track recordzie
    s.add(Trade(account_id=aid, symbol="ETHUSD", side="buy", lots=1.0, open_price=3_550.0, pnl=0.0,
                plan_pnl=80.0, plan_close_at=teraz + timedelta(hours=1), opened_at=teraz - timedelta(minutes=20),
                status="open", source="bot", venue=tradebot.PLAN))
    s.commit(); s.close()
    with TestClient(app) as c:
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
        assert st["positions"] == [] and st["account"]["other_lots"] == 0
        # Zwykłe zamknięte transakcje — bez znacznika, że zrobił je bot. Starsza (bez venue)
        # też: terminal pokazuje cały track record konta, jak dashboard (2026-11-06).
        assert [(x["symbol"], x["reason"], x["lots"]) for x in st["history"]] == [("BTCUSD", "close", 0.3), ("XAUUSD", "close", 0.2)]
        assert "by" not in st["history"][0]
        assert st["history"][1]["qty"] == 20.0 and st["history"][1]["fee"] > 0
        # Opłata i dźwignia jak w terminalu (0,03% × (wejście + wyjście)), P&L z planu.
        zh = st["history"][0]
        assert zh["fee"] == pytest.approx(round((77_000.0 + 77_500.0) * 0.3 * tradebot.OPLATA, 2))
        assert zh["leverage"] == 10.0 and zh["pnl"] == 150.0
        # Order History: market na wejściu i market reduce-only na wyjściu.
        assert [(o["side"], o["kind"], o["reduce_only"], o["status"], o["fill_price"]) for o in st["order_history"]] == [
            ("sell", "market", True, "filled", 77_500.0), ("buy", "market", False, "filled", 77_000.0),
            ("buy", "market", True, "filled", 2_380.0), ("sell", "market", False, "filled", 2_385.0)]
        assert c.get(f"/api/me/accounts/{aid}/positions", headers=_portal(aid)).json() == []


def test_plaski_rynek_czeka_a_potem_wieksza_pozycja(monkeypatch):
    """Bez ruchu w oknie nie da się trafić planu — `zamknij` odmawia (bot czeka)."""
    monkeypatch.setattr(markets, "candles", _swiece_z(0.0))
    bot_ceny._CACHE.clear()

    class T:
        pass
    tr = T()
    teraz = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    tr.symbol, tr.lots, tr.opened_at, tr.plan_close_at = "BTCUSD", 0.5, teraz - timedelta(hours=2), teraz
    assert not bot_ceny.zamknij(tr, tradebot.INSTRUMENTS["BTCUSD"], tradebot._point_value, 350.0,
                                teraz, 6.0, random.Random(1))
    # Z ruchem, ale za małym na 0,5 lota: większy wolumen (do limitu) trafia plan.
    # Fala 0,2 o każdej porze: szybki składnik daje w oknie ≥ ~30 w jedną stronę
    # (≤ ~12 lotów przy limicie 20), a całość ≤ ~520 — za mało na 0,5 lota (700).
    monkeypatch.setattr(markets, "candles", _swiece_z(0.2))
    bot_ceny._CACHE.clear()
    assert bot_ceny.zamknij(tr, tradebot.INSTRUMENTS["BTCUSD"], tradebot._point_value, 350.0,
                            teraz, 20.0, random.Random(1))
    assert tr.lots > 0.5 and abs(tr.pnl - 350.0) <= 0.1 * tr.lots + 0.011
    bot_ceny._CACHE.clear()


def test_koszyk_tylko_z_notowanymi_rynkami(zywe):
    p = tradebot.Persona(style="balanced", pace="steady", win_rate=0.6, avg_r=1.5, risk_pct=0.3,
                         daily_target_pct=0.5, red_day_odds=0.2, trades_per_day=6,
                         symbols=("US30", "GER40", "XAUUSD", "AUDUSD"), weights=(1, 1, 1, 1))
    syms, _ = tradebot._tradable(p, False)
    assert syms == ("XAUUSD",)
    p2 = tradebot.Persona(**{**p.__dict__, "symbols": ("US30", "GER40"), "weights": (1, 1)})
    syms2, _ = tradebot._tradable(p2, False)
    assert set(syms2) == {s for s in tradebot.INSTRUMENTS if bot_ceny.rynek(s)}


def test_stan_terminala_pcha_konto_bota(zywe, monkeypatch):
    """Trader wchodzi tylko danymi MT5: sam odczyt stanu ma ruszać bota, ale nie przy każdym odświeżeniu."""
    aid, h, _ = _konto("botceny9")
    wywolania = []

    def ocena(session, acc):
        wywolania.append(acc.id)
        acc.terminal_eval_at = terminal._now()
    monkeypatch.setattr(terminal, "_ocen", ocena)
    with TestClient(app) as c:
        assert c.get(f"/api/terminal/state?account_id={aid}", headers=h).status_code == 200
        assert c.get(f"/api/terminal/state?account_id={aid}", headers=h).status_code == 200
    assert wywolania == [aid]


def _plan_na_ogon(aid, zgoda=True, mode="profit", pnl=120.0, symbol="BTCUSD"):
    """Konto z planem w oknie końcówki; zwraca id transakcji."""
    s = SessionLocal()
    acc = s.get(Account, aid)
    teraz = datetime.now(timezone.utc)
    if zgoda:
        acc.managed_consent_at = teraz.replace(tzinfo=None)
    acc.bot_mode = mode
    tr = Trade(account_id=aid, symbol=symbol, side="buy", lots=0.1, open_price=1.0, pnl=0.0, status="open",
               source="bot", venue=tradebot.PLAN, plan_pnl=pnl, opened_at=(teraz - timedelta(minutes=50)).replace(tzinfo=None),
               plan_close_at=(teraz + timedelta(seconds=175)).replace(tzinfo=None))
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid


OGON_CENA = 77_000.0


def _mid(monkeypatch, cena=None):
    """Stały mid i szerokie świece: wynik testu nie zależy od godziny ani od sieci."""
    px = OGON_CENA if cena is None else cena

    def swiece(coin, interval, start_ms, end_ms):
        krok = MS[interval]
        t, out = start_ms - start_ms % krok, []
        while t <= end_ms:
            out.append({"t": t, "o": OGON_CENA, "h": OGON_CENA + 3_000, "l": OGON_CENA - 3_000, "c": OGON_CENA})
            t += krok
        return out
    monkeypatch.setattr(markets, "mids", lambda coins: {c: px for c in coins})
    monkeypatch.setattr(markets, "candles", swiece)
    monkeypatch.setattr(markets, "candles_1m", lambda coin, start, end: [])
    bot_ceny._CACHE.clear()


@pytest.fixture(autouse=True)
def _posprzataj_koncowki():
    """Otwarte końcówki nie mogą zostać dla testów, które liczą konta z terminal_open."""
    yield
    s = SessionLocal()
    konta = s.query(Account).filter(Account.login.like("ogon%"))
    # Numeracja transakcji wpływa na losowanie bota w innych testach, więc zostawiamy ją jak była.
    s.query(Trade).filter(Trade.account_id.in_([a.id for a in konta])).delete(synchronize_session=False)
    konta.update({Account.terminal_open: False}, synchronize_session=False)
    s.commit()
    s.close()


def _tick(aid):
    s = SessionLocal()
    acc = s.get(Account, aid)
    snap = tradebot.tick(s, acc, datetime.now(timezone.utc))
    s.commit()
    s.close()
    return snap


def _tr(tid):
    s = SessionLocal()
    t = s.get(Trade, tid)
    s.expunge(t)
    s.close()
    return t


def test_bez_zgody_i_w_doom_nie_ma_koncowki(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon1")
    t1 = _plan_na_ogon(aid, zgoda=False)
    _tick(aid)
    assert _tr(t1).venue == tradebot.PLAN
    aid2, _, _ = _konto("ogon2")
    t2 = _plan_na_ogon(aid2, zgoda=True, mode="doom")
    _tick(aid2)
    assert _tr(t2).venue == tradebot.PLAN


def test_koncowka_widoczna_jako_managed_raz_w_equity_i_lotach(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon3")
    tid = _plan_na_ogon(aid)
    snap = _tick(aid)
    t = _tr(tid)
    assert t.venue == tradebot.OGON and t.qty and t.margin and t.status == "open"
    assert snap.open_pnl == 0.0 and snap.volume_lots == 0.0, "floating i loty liczy księga terminala"
    with TestClient(app) as c:
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
    assert [p["id"] for p in st["positions"]] == [tid] and st["positions"][0]["managed"] is True
    assert st["account"]["open_lots"] == t.lots
    assert abs(st["positions"][0]["pnl"] - 120.0 * tradebot.OGON_CEL) < 40


def test_koncowka_zamyka_sie_na_celu_i_w_terminie(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon4")
    tid = _plan_na_ogon(aid)
    _tick(aid)
    t = _tr(tid)
    sign = 1 if t.side == "buy" else -1
    # cel: wynik doszedł do planu
    _mid(zywe, t.open_price + sign * 130.0 / t.qty)
    s = SessionLocal(); bal0 = s.get(Account, aid).balance; s.close()
    snap = _tick(aid)
    z = _tr(tid)
    assert z.status == "closed" and z.venue == "hyperliquid" and z.close_reason == "close"
    assert snap.balance == round(bal0 + z.pnl, 2)
    # termin: bez dojścia do celu zamyka po rynku
    aid2, _, _ = _konto("ogon5")
    _mid(zywe)
    t2 = _plan_na_ogon(aid2)
    _tick(aid2)
    w = _tr(t2)
    _mid(zywe, w.open_price)
    s = SessionLocal(); s.get(Trade, t2).plan_close_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).replace(tzinfo=None); s.commit(); s.close()
    _tick(aid2)
    assert _tr(t2).status == "closed" and _tr(t2).close_price == w.open_price


def test_reczne_zamkniecie_zostawia_wynik_klienta(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon6")
    tid = _plan_na_ogon(aid)
    _tick(aid)
    t = _tr(tid)
    sign = 1 if t.side == "buy" else -1
    _mid(zywe, t.open_price - sign * 50.0 / t.qty)     # klient zamyka na stracie, plan był na plus
    zywe.setattr(terminal, "_zamknij_rynkowo", lambda session, acc, pos, qty, slip: terminal._close_part(session, acc, pos, qty, t.open_price - sign * 50.0 / t.qty, "close", terminal._now()))
    with TestClient(app) as c:
        r = c.post(f"/api/terminal/positions/{tid}/close", json={"account_id": aid, "max_slippage_pct": 2, "qty": t.qty / 2}, headers=h)
        assert r.status_code == 400, "częściowo się nie zamyka"
        r = c.post(f"/api/terminal/positions/{tid}/close", json={"account_id": aid, "max_slippage_pct": 2}, headers=h)
        assert r.status_code == 200, r.text
        st = r.json()
    z = _tr(tid)
    assert z.status == "closed" and z.pnl < 0 and z.venue == "hyperliquid"
    assert st["positions"] == [] and st["history"][0]["managed"] is True
    bal = st["account"]["balance"]
    _tick(aid)
    assert _tr(tid).pnl == z.pnl, "bot nie zamyka drugi raz i nie wyrównuje"
    s = SessionLocal(); assert abs(s.get(Account, aid).balance - bal) < 0.01; s.close()


def test_tp_na_koncowce_i_zlecenie_na_tym_rynku(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon7")
    tid = _plan_na_ogon(aid)
    _tick(aid)
    t = _tr(tid)
    sign = 1 if t.side == "buy" else -1
    with TestClient(app) as c:
        r = c.post("/api/terminal/orders", json={"account_id": aid, "symbol": "BTCUSD", "side": "buy", "kind": "market", "qty": 0.01, "max_slippage_pct": 2}, headers=h)
        assert r.status_code == 409 and "managed" in r.json()["detail"]
        mid = markets.mids({"BTC"})["BTC"]
        r = c.patch(f"/api/terminal/positions/{tid}", json={"account_id": aid, "tp": mid + sign * 100, "sl": mid - sign * 100}, headers=h)
        assert r.status_code == 200, r.text
    assert _tr(tid).tp is not None


def test_stop_bota_zamyka_koncowke_po_rynku(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon8")
    tid = _plan_na_ogon(aid)
    _tick(aid)
    s = SessionLocal()
    acc = s.get(Account, aid)
    tradebot.stop(s, acc)
    s.close()
    z = _tr(tid)
    assert z.status == "closed" and z.close_reason == "close" and z.venue == "hyperliquid"


def test_brak_swiecy_zostawia_plan(zywe):
    aid, h, _ = _konto("ogon9")
    tid = _plan_na_ogon(aid, pnl=5_000_000.0)       # żadna świeca nie zawiera takiej ceny
    _mid(zywe)
    _tick(aid)
    assert _tr(tid).venue == tradebot.PLAN


def test_zgoda_daje_i_cofa_klient_nie_podglad_admina(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon10")
    s = SessionLocal(); a = s.get(Account, aid); a.platform_password = "x"; imp = auth.make_terminal_token(aid, "x", imp=True)
    w = TerminalLogin(login="ogon10", account_id=aid, trader_id=a.trader_id, ok=True); s.add(w); s.commit()
    h2 = {"Authorization": f"Bearer {auth.make_terminal_token(aid, 'x', sid=w.id)}"}
    s.close()
    with TestClient(app) as c:
        assert c.post("/api/terminal/managed-consent", json={"account_id": aid, "accept": True}, headers={"Authorization": f"Bearer {imp}"}).status_code == 403
        r = c.post("/api/terminal/managed-consent", json={"account_id": aid, "accept": True}, headers={**h2, "x-real-ip": "203.0.113.5"})
        assert r.status_code == 200 and r.json()["managed_consent"]
        s = SessionLocal(); a = s.get(Account, aid); assert a.managed_consent_ip == "203.0.113.5" and a.managed_consent_version; s.close()
        assert c.post("/api/terminal/managed-consent", json={"account_id": aid, "accept": False}, headers=h2).json()["managed_consent"] is None
    tid = _plan_na_ogon(aid, zgoda=False)
    _tick(aid)
    assert _tr(tid).venue == tradebot.PLAN, "po cofnięciu nowa końcówka nie startuje"


def _pelny(aid, h, c):
    """Odczyt stanu tak, jak robi to terminal; reguły liczone od razu."""
    s = SessionLocal(); s.get(Account, aid).terminal_eval_at = None; s.commit(); s.close()
    r = c.get(f"/api/terminal/state?account_id={aid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_koncowka_przez_caly_silnik_saldo_i_historia(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon11")
    tid = _plan_na_ogon(aid)
    with TestClient(app) as c:
        _pelny(aid, h, c)
        st = _pelny(aid, h, c)
        assert [p["managed"] for p in st["positions"]] == [True]
        t = _tr(tid)
        sign = 1 if t.side == "buy" else -1
        _mid(zywe, t.open_price + sign * 130.0 / t.qty)
        st = _pelny(aid, h, c)
        z = _tr(tid)
        assert z.status == "closed" and st["positions"] == []
        s = SessionLocal(); a = s.get(Account, aid); saldo, otwarte = a.balance, a.terminal_open; s.close()
        assert abs(saldo - (100_000.0 + z.pnl)) < 0.01, "wynik wchodzi do salda dokładnie raz"
        assert st["history"][0]["id"] == tid and st["history"][0]["managed"] is True
        assert st["history"][0]["fee"] > 0 and st["history"][0]["close"] == z.close_price
        st2 = _pelny(aid, h, c)
        s = SessionLocal(); assert abs(s.get(Account, aid).balance - saldo) < 0.01, "kolejne odczyty nic nie zmieniają"; s.close()
        assert otwarte in (True, False)
        assert len(st2["history"]) >= 1


def test_tp_klienta_zamyka_koncowke_przez_silnik(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon12")
    tid = _plan_na_ogon(aid)
    with TestClient(app) as c:
        _pelny(aid, h, c)
        t = _tr(tid)
        assert t.venue == tradebot.OGON
        sign = 1 if t.side == "buy" else -1
        mid = markets.mids({"BTC"})["BTC"]
        r = c.patch(f"/api/terminal/positions/{tid}", json={"account_id": aid, "tp": mid + sign * 5, "sl": None}, headers=h)
        assert r.status_code == 200, r.text
        s = SessionLocal(); s.get(Trade, tid).plan_pnl = 100_000.0; s.get(Trade, tid).tpsl_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None); s.commit(); s.close()
        _mid(zywe, mid + sign * 6)
        _pelny(aid, h, c)
    z = _tr(tid)
    assert z.status == "closed" and z.close_reason == "tp", (z.status, z.close_reason)


def test_breach_przy_otwartej_koncowce(zywe):
    _mid(zywe)
    aid, h, _ = _konto("ogon13")
    tid = _plan_na_ogon(aid)
    with TestClient(app) as c:
        _pelny(aid, h, c)
        t = _tr(tid)
        sign = 1 if t.side == "buy" else -1
        s = SessionLocal(); s.get(Trade, tid).plan_pnl = 100_000.0; s.commit(); s.close()   # nie zamknie się na celu
        _mid(zywe, t.open_price - sign * 20_000.0 / t.qty)
        _pelny(aid, h, c)
    s = SessionLocal(); a = s.get(Account, aid); status = a.status; s.close()
    z = _tr(tid)
    assert status == "failed", status
    assert z.status == "closed", "breach zamyka też końcówkę"
