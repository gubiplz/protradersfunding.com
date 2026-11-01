"""Terminal PTF (zakładka Trade): zlecenia, netowanie, limity, TP/SL ze świec,
breach z dołka equity, login danymi MT5 i weekend.

Ceny z Hyperliquid są podmienione na sterowany „rynek", więc testy nie
dotykają sieci.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

_DB = os.path.join(tempfile.gettempdir(), "pf_test_terminal.db")
if not os.environ.get("TEST_DATABASE_URL") and os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + _DB
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, markets, notify, push, terminal, tradebot  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Breach, TerminalOrder, Trade, Trader  # noqa: E402

init_db()


class Rynek:
    """Mid per moneta, księga z wąskim spreadem i świece 1m na żądanie."""

    def __init__(self):
        self.px = {"BTC": 50_000.0, "xyz:GOLD": 2_000.0, "xyz:EUR": 1.10}
        self.swiece: dict[str, list[dict]] = {}

    def book(self, coin):
        p = self.px[coin]
        return [(p - 0.5, 1e6)], [(p + 0.5, 1e6)]

    def mids(self, coins):
        return {c: self.px[c] for c in coins if c in self.px}

    def candles_1m(self, coin, start_ms, end_ms):
        return [c for c in self.swiece.get(coin, []) if start_ms <= c["t"] <= end_ms]


@pytest.fixture
def rynek(monkeypatch):
    r = Rynek()
    monkeypatch.setattr(markets, "book", r.book)
    monkeypatch.setattr(markets, "mids", r.mids)
    monkeypatch.setattr(markets, "candles_1m", r.candles_1m)
    monkeypatch.setattr(tradebot, "is_weekend", lambda now=None: False)
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(push, "send_event", lambda *a, **k: None)
    return r


_n = [0]


def _konto(**pola):
    _n[0] += 1
    s = SessionLocal()
    tr = Trader(email=f"term{_n[0]}-{os.getpid()}@test.pl", password_hash=auth.hash_password("haslo1234"),
                full_name="Term Inal", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    dane = dict(login=f"77{tr.id:06d}", trader_id=tr.id, trader_name="Term Inal", initial_balance=100_000.0,
                balance=100_000.0, equity=100_000.0, peak_equity=100_000.0, day_start_equity=100_000.0,
                day_start_balance=100_000.0, platform_login=f"77{tr.id:06d}", platform_password="Pw-123456",
                platform_server="PTF-Demo", max_lots=6.0)
    dane.update(pola)
    acc = Account(**dane)
    s.add(acc); s.commit()
    aid, tid = acc.id, tr.id
    s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _acc(aid):
    s = SessionLocal()
    try:
        a = s.get(Account, aid)
        s.expunge(a)
        return a
    finally:
        s.close()


def _trades(aid):
    s = SessionLocal()
    try:
        return [(t.side, round(t.qty, 6), t.status, t.close_reason, t.pnl)
                for t in s.query(Trade).filter(Trade.account_id == aid).order_by(Trade.id)]
    finally:
        s.close()


def _cofnij_przeglad(aid, minut):
    """Udaje, że od ostatniego przeglądu minęło `minut` minut (pozycje też starsze)."""
    s = SessionLocal()
    a = s.get(Account, aid)
    cofka = timedelta(minutes=minut)
    a.terminal_sync_at = datetime.now(timezone.utc) - cofka
    for t in s.query(Trade).filter(Trade.account_id == aid, Trade.status == "open"):
        t.opened_at = t.opened_at - cofka - timedelta(minutes=1)
        if t.tpsl_at:
            t.tpsl_at = t.tpsl_at - cofka - timedelta(minutes=1)
    for z in s.query(TerminalOrder).filter(TerminalOrder.account_id == aid, TerminalOrder.status == "open"):
        z.created_at = z.created_at - cofka - timedelta(minutes=1)
    s.commit(); s.close()


def _swieca(minut_temu, o, h, low, c):
    t = int((datetime.now(timezone.utc) - timedelta(minutes=minut_temu)).timestamp() * 1000)
    t -= t % 60_000
    return {"t": t, "o": o, "h": h, "l": low, "c": c}


def _zlec(c, h, aid, **kw):
    body = {"account_id": aid, "symbol": "BTCUSD", "side": "buy", "qty": 1, "leverage": 10, **kw}
    return c.post("/api/terminal/orders", headers=h, json=body)


def test_rynkowe_otwarcie_i_zamkniecie_z_oplatami(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        r = _zlec(c, h, aid)
        assert r.status_code == 200, r.text
        st = r.json()
        p = st["positions"][0]
        assert p["side"] == "buy" and p["qty"] == 1 and p["entry"] == 50_000.5
        assert p["margin"] == pytest.approx(5_000.05, abs=0.01)
        oplata_wejscia = 50_000.5 * 0.0003
        assert st["account"]["balance"] == pytest.approx(100_000 - oplata_wejscia, abs=0.01)
        # Cena rośnie o 1000 — zamknięcie po bid.
        rynek.px["BTC"] = 51_000.0
        r = c.post(f"/api/terminal/positions/{p['id']}/close", headers=h, json={"account_id": aid})
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["positions"] == []
        brutto = 51_000 - 0.5 - 50_000.5
        oplaty = oplata_wejscia + (51_000 - 0.5) * 0.0003
        zamk = st["history"][0]
        assert zamk["pnl"] == pytest.approx(brutto - oplaty, abs=0.02) and zamk["reason"] == "close"
        assert st["account"]["balance"] == pytest.approx(100_000 + brutto - oplaty, abs=0.02)
        assert [o["kind"] for o in st["order_history"]] == ["market", "market"]
    a = _acc(aid)
    assert a.terminal_used and not a.terminal_open


def test_netowanie_zmniejsza_i_odwraca_pozycje(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        assert _zlec(c, h, aid, qty=2).status_code == 200
        st = _zlec(c, h, aid, side="sell", qty=0.5).json()
        assert [(p["side"], p["qty"]) for p in st["positions"]] == [("buy", 1.5)]
        st = _zlec(c, h, aid, side="sell", qty=2).json()
        assert [(p["side"], p["qty"]) for p in st["positions"]] == [("sell", 0.5)]
        # Reduce-only nie otworzy nowej pozycji w tę samą stronę.
        r = _zlec(c, h, aid, side="sell", qty=1, reduce_only=True)
        assert r.status_code == 409 and "Reduce-only" in r.json()["detail"]
    # Pierwszy wiersz to pierwotna pozycja (domknięta resztą 1.5), drugi to
    # zamknięta wcześniej część 0.5, trzeci — odwrócona pozycja short.
    assert [t[:3] for t in _trades(aid)] == [("buy", 1.5, "closed"), ("buy", 0.5, "closed"), ("sell", 0.5, "open")]


def test_limit_lotow_i_marzy(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        r = _zlec(c, h, aid, qty=7)
        assert r.status_code == 409 and "Exposure cap" in r.json()["detail"]
        r = _zlec(c, h, aid, tp=49_000)              # TP poniżej wejścia na longu
        assert r.status_code == 400
        # Dźwignia z klienta nie ma znaczenia — zawsze maksimum rynku (BTC 10x), bez ceny likwidacji.
        st = _zlec(c, h, aid, qty=1, leverage=1).json()
        assert st["positions"][0]["leverage"] == 10 and st["positions"][0]["liq"] is None
    # Marża: 1 BTC przy 10x to ~5000 USD, a konto ma 2000 USD.
    male, hm = _konto(balance=2_000.0, equity=2_000.0)
    with TestClient(app) as c:
        r = _zlec(c, hm, male, qty=1)
        assert r.status_code == 409 and "available margin" in r.json()["detail"]


def test_stop_out_przy_polowie_marzy():
    assert not terminal.stop_out_due({"margin_used": 0.0, "equity": -5.0})
    assert not terminal.stop_out_due({"margin_used": 10_000.0, "equity": 5_001.0})
    assert terminal.stop_out_due({"margin_used": 10_000.0, "equity": 5_000.0})


def test_stop_loss_ze_swiecy_pod_nieobecnosc(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        st = _zlec(c, h, aid, sl=49_000, tp=52_000).json()
        assert st["positions"][0]["sl"] == 49_000
        _cofnij_przeglad(aid, 5)
        rynek.swiece["BTC"] = [_swieca(4, 50_000, 50_100, 49_900, 50_000),
                               _swieca(3, 49_950, 49_960, 48_900, 49_100)]
        rynek.px["BTC"] = 49_500.0      # odbiło, ale SL już padł
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
        assert st["positions"] == []
        assert st["history"][0]["reason"] == "sl" and st["history"][0]["close"] == 49_000


def test_zlecenie_limit_wypelnia_sie_przy_przecieciu(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        r = _zlec(c, h, aid, kind="limit", price=49_500)
        assert r.status_code == 200, r.text
        assert r.json()["orders"][0]["kind"] == "limit" and r.json()["positions"] == []
        # Limit kupna nad rynkiem to zlecenie rynkowe w przebraniu — odmowa.
        assert _zlec(c, h, aid, kind="limit", price=50_100).status_code == 400
        _cofnij_przeglad(aid, 3)
        rynek.swiece["BTC"] = [_swieca(2, 49_800, 49_850, 49_400, 49_600)]
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
        assert st["orders"] == []
        assert st["positions"][0]["entry"] == 49_500
        assert st["order_history"][0]["status"] == "filled"


def test_dolek_equity_w_oknie_lamie_dzienna_strate(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        assert _zlec(c, h, aid, qty=5).status_code == 200
        _cofnij_przeglad(aid, 3)
        # Dołek -1100 na 5 BTC = -5500 > 5% z 100k; potem cena wraca.
        rynek.swiece["BTC"] = [_swieca(2, 50_000, 50_000, 48_900, 49_950)]
        rynek.px["BTC"] = 50_000.0
        c.get(f"/api/terminal/state?account_id={aid}", headers=h)
    a = _acc(aid)
    assert a.status == "failed" and not a.terminal_open
    s = SessionLocal()
    try:
        assert s.query(Breach).filter(Breach.account_id == aid).first().type == "daily_loss"
        assert all(t.status == "closed" for t in s.query(Trade).filter(Trade.account_id == aid))
    finally:
        s.close()
    with TestClient(app) as c:
        r = _zlec(c, h, aid)
        assert r.status_code == 409 and "closed for trading" in r.json()["detail"]


def test_tylko_konto_ptf_i_tylko_wlasne_konta(rynek):
    """Do terminala wchodzi się wyłącznie sesją konta PTF — logowania danymi MT5 nie ma."""
    aid, h = _konto()
    inne, _ = _konto()
    a = _acc(aid)
    with TestClient(app) as c:
        assert c.post("/api/terminal/login",
                      json={"login": a.platform_login, "password": "Pw-123456"}).status_code in (404, 405)
        assert c.get("/api/terminal/accounts").status_code == 401
        konta = c.get("/api/terminal/accounts", headers=h).json()
        assert [k["id"] for k in konta["accounts"]] == [aid]
        assert c.get(f"/api/terminal/state?account_id={inne}", headers=h).status_code == 404
        assert _zlec(c, h, inne).status_code == 404
        assert _zlec(c, h, None).status_code == 400


def test_weekend_zamyka_rynki_poza_krypto(rynek, monkeypatch):
    monkeypatch.setattr(tradebot, "is_weekend", lambda now=None: True)
    monkeypatch.setattr(terminal, "_cena_weekendowa", lambda coin, now: rynek.px[coin])
    aid, h = _konto()
    ww, hw = _konto(weekend_trading=True)
    with TestClient(app) as c:
        r = c.post("/api/terminal/orders", headers=hw,
                   json={"account_id": ww, "symbol": "XAUUSD", "side": "buy", "qty": 1, "leverage": 10})
        assert r.status_code == 409 and "weekend" in r.json()["detail"]
        r = _zlec(c, h, aid)
        assert r.status_code == 409 and "Weekend Trading add-on" in r.json()["detail"]
        assert _zlec(c, hw, ww).status_code == 200


def test_kontrakty_krypto_daja_rozsadny_lot():
    # 1 lot na każdym rynku krypto to tysiące dolarów, nie jedna moneta.
    ceny = {"BTCUSD": 77_000, "ETHUSD": 2_250, "SOLUSD": 96, "XRPUSD": 1.25, "LTCUSD": 55, "DOGEUSD": 0.075,
            "BNBUSD": 706, "ADAUSD": 0.19, "AVAXUSD": 8.5, "LINKUSD": 11.5, "SUIUSD": 0.9, "HYPEUSD": 88}
    for sym, px in ceny.items():
        m = markets.get(sym)
        assert 4_000 <= m.contract * px <= 100_000, (sym, m.contract * px)


def test_bot_nie_widzi_pozycji_z_terminala(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        assert _zlec(c, h, aid).status_code == 200
    s = SessionLocal()
    try:
        assert tradebot._open_trade(s, s.get(Account, aid)) is None
    finally:
        s.close()


def _przesun(aid, **kiedy):
    """Ustawia znaczniki przeglądu konta (minuty temu) i postarza pozycje o `pozycje` minut."""
    s = SessionLocal()
    a = s.get(Account, aid)
    teraz = datetime.now(timezone.utc)
    if "sync" in kiedy:
        a.terminal_sync_at = teraz - timedelta(minutes=kiedy["sync"])
    if "swiece" in kiedy:
        a.terminal_candle_at = teraz - timedelta(minutes=kiedy["swiece"])
    if "pozycje" in kiedy:
        for t in s.query(Trade).filter(Trade.account_id == aid, Trade.status == "open"):
            t.opened_at = teraz - timedelta(minutes=kiedy["pozycje"])
            t.tpsl_at = teraz - timedelta(minutes=kiedy["pozycje"]) if t.tpsl_at else None
    s.commit(); s.close()


def test_knot_miedzy_odswiezeniami_trafia_sl(rynek):
    """Strona odświeża stan co kilkanaście sekund — knot z ostatnich minut i tak trafia SL."""
    aid, h = _konto()
    with TestClient(app) as c:
        assert _zlec(c, h, aid, sl=49_000).status_code == 200
        # Ostatni przegląd midem 20 s temu, pełny przegląd świecami 3 min temu.
        _przesun(aid, sync=0.33, swiece=3, pozycje=4)
        rynek.swiece["BTC"] = [_swieca(2, 50_000, 50_010, 48_800, 49_900)]
        rynek.px["BTC"] = 49_900.0
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
        assert st["positions"] == [] and st["history"][0]["reason"] == "sl"


def test_pierwsze_zlecenie_nie_widzi_starych_swiec(rynek):
    """Konto bez nic otwartego od godzin: nowa pozycja nie jest wyceniana na krachu sprzed jej otwarcia."""
    aid, h = _konto()
    _przesun(aid, sync=180, swiece=180)
    rynek.swiece["BTC"] = [_swieca(30, 50_000, 50_000, 40_000, 50_000)]
    with TestClient(app) as c:
        assert _zlec(c, h, aid, qty=1).status_code == 200
        assert c.get(f"/api/terminal/state?account_id={aid}", headers=h).status_code == 200
    assert _acc(aid).status == "active"


def test_sl_nie_da_sie_przesunac_po_trafieniu(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        p = _zlec(c, h, aid, sl=49_000).json()["positions"][0]
        _przesun(aid, sync=0.2, swiece=3, pozycje=4)
        rynek.swiece["BTC"] = [_swieca(1, 50_000, 50_000, 48_500, 49_800)]
        rynek.px["BTC"] = 49_800.0
        r = c.patch(f"/api/terminal/positions/{p['id']}", headers=h, json={"account_id": aid, "sl": None})
        assert r.status_code == 409
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
        assert st["positions"] == [] and st["history"][0]["reason"] == "sl"


def test_liczby_spoza_zakresu_i_za_male_zlecenie(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        # Surowy JSON z Infinity/NaN (json.loads po stronie serwera je przepuszcza).
        for zle in ('"qty": Infinity', '"qty": 1, "tp": NaN', '"qty": 1e300'):
            r = c.post("/api/terminal/orders", headers={**h, "Content-Type": "application/json"},
                       content='{"account_id": %d, "symbol": "BTCUSD", "side": "buy", %s}' % (aid, zle))
            assert r.status_code == 422, (zle, r.text)
        r = c.post("/api/terminal/orders", headers=h,
                   json={"account_id": aid, "symbol": "EURUSD", "side": "buy", "qty": 0.0004})
        assert r.status_code == 400 and "Minimum size" in r.json()["detail"]
    assert _acc(aid).balance == 100_000.0


def test_podglad_admina_nie_handluje(rynek):
    aid, _ = _konto()
    a = _acc(aid)
    s = SessionLocal()
    tr = s.get(Trader, a.trader_id)
    imp = {"Authorization": f"Bearer {auth.make_impersonation_token(tr.id, tr.password_hash)}"}
    s.close()
    with TestClient(app) as c:
        assert c.get(f"/api/terminal/state?account_id={aid}", headers=imp).status_code == 200
        assert _zlec(c, imp, aid).status_code == 403


def test_odwrocenie_zwalnia_limit_lotow(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        assert _zlec(c, h, aid, qty=6).status_code == 200            # cały limit 6 lotów
        st = _zlec(c, h, aid, side="sell", qty=12).json()           # 6 long → 6 short
        assert [(p["side"], p["qty"]) for p in st["positions"]] == [("sell", 6)]


def test_limit_lotow_wspolny_z_botem(rynek):
    aid, h = _konto()
    s = SessionLocal()
    s.add(Trade(account_id=aid, symbol="EURUSD", side="buy", lots=5.0, open_price=1.1, pnl=0.0,
                opened_at=datetime.now(timezone.utc), status="open", source="bot"))
    s.commit()
    a = s.get(Account, aid)
    assert tradebot._wolne_loty(s, a) == pytest.approx(6.0)        # bot liczy tylko terminal
    s.close()
    with TestClient(app) as c:
        r = _zlec(c, h, aid, qty=2)
        assert r.status_code == 409 and "Exposure cap" in r.json()["detail"]
        st = _zlec(c, h, aid, qty=1).json()
        assert st["account"]["other_lots"] == 5.0
    s = SessionLocal()
    try:
        assert tradebot._wolne_loty(s, s.get(Account, aid)) == pytest.approx(5.0)
    finally:
        s.close()


def test_zaliczenie_fazy_zamyka_pozycje_terminala(rynek):
    aid, h = _konto(balance=110_000.0, equity=110_000.0, peak_equity=110_000.0, trading_days_count=3,
                    steps=2)
    with TestClient(app) as c:
        assert _zlec(c, h, aid).status_code == 200
    a = _acc(aid)
    assert a.phase == "eval_2" and a.balance == 100_000.0 and not a.terminal_open
    assert [t[2:4] for t in _trades(aid)] == [("closed", "phase")]


def test_dzwignia_zgodna_z_tym_co_obiecuje_ptf():
    """Strona i portal mówią „Up to 1:100", z taką dźwignią provisioning zakłada konta
    MT5, a tabele zasad pokazują rozbicie na rynki z tej samej listy co terminal."""
    from app.config import get_settings
    s = get_settings()
    assert markets.MAX_LEVERAGE == s.metaquotes_web_leverage == s.metaapi_demo_leverage == 100
    assert all(1 <= m.max_lev <= markets.MAX_LEVERAGE for m in markets.MARKETS)
    assert markets.leverage_text().startswith("Forex 1:100")
    with TestClient(app) as c:
        html = c.get("/objectives").text
    assert 'data-leverage-max="100"' in html and f'data-leverage="{markets.leverage_text()}"' in html
    sciezka = os.path.join(os.path.dirname(__file__), "..", "static", "js", "site.js")
    with open(sciezka, encoding="utf-8") as f:
        assert "'Up to 1:100'" not in f.read()     # bez ręcznych kopii — wartość idzie z markets.py


def test_strona_trade_i_rynki():
    with TestClient(app) as c:
        r = c.get("/trade")
        assert r.status_code == 200 and 'id="terminal"' in r.text and 'data-symbol="BTCUSD"' in r.text
        assert 'data-symbol="XAUUSD"' in c.get("/trade/xauusd").text
        r = c.get("/trade/AUDUSD")
        assert 'data-missing="AUDUSD"' in r.text
        rynki = c.get("/api/terminal/markets").json()["markets"]
        assert {m["sym"] for m in rynki} >= {"BTCUSD", "XAUUSD", "EURUSD", "SPX500", "USOIL"}
        assert "/trade" in c.get("/sitemap.xml").text
        # „See the full list" na stronie głównej prowadzi do terminala z listą rynków.
        assert 'href="/trade?list=all"' in c.get("/").text
