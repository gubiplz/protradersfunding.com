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
from app.config import get_settings  # noqa: E402
from app.models import Account, Breach, TerminalLogin, TerminalOrder, Trade, Trader  # noqa: E402

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


def _sesja(aid):
    """Token jak po logowaniu danymi MT5: wiersz sesji + podpisany token konta."""
    s = SessionLocal()
    a = s.get(Account, aid)
    w = TerminalLogin(login=a.platform_login, account_id=aid, trader_id=a.trader_id, ok=True)
    s.add(w); s.commit()
    t = auth.make_terminal_token(aid, a.platform_password, sid=w.id)
    s.close()
    return t


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
    return aid, {"Authorization": f"Bearer {_sesja(aid)}"}


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
        oplata_wejscia = 50_000.5 * terminal.FEE_PCT
        assert st["account"]["balance"] == pytest.approx(100_000 - oplata_wejscia, abs=0.01)
        # Cena rośnie o 1000 — zamknięcie po bid.
        rynek.px["BTC"] = 51_000.0
        r = c.post(f"/api/terminal/positions/{p['id']}/close", headers=h, json={"account_id": aid})
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["positions"] == []
        brutto = 51_000 - 0.5 - 50_000.5
        oplaty = oplata_wejscia + (51_000 - 0.5) * terminal.FEE_PCT
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
        assert "daily loss floor" in r.json()["detail"]
        # Konto zostaje w terminalu jako nieaktywne, z powodem — nie znika w „No account".
        konto = next(a for a in c.get("/api/terminal/accounts", headers=h).json()["accounts"] if a["id"] == aid)
        assert not konto["tradable"] and "daily loss floor" in konto["reason"]


def test_logowanie_danymi_mt5_i_tylko_to_konto(rynek):
    """Do terminala wchodzi się danymi MT5. Hasło i sesja portalu tu nie działają."""
    aid, _ = _konto()
    inne, _ = _konto()
    a = _acc(aid)
    s = SessionLocal(); tr = s.get(Trader, a.trader_id); portal = auth.make_token(tr.id, tr.password_hash); s.close()
    naglowki = {"x-real-ip": "203.0.113.7", "x-vercel-ip-country": "PL", "x-vercel-ip-city": "Rzesz%C3%B3w",
                "user-agent": "Mozilla/5.0 (Macintosh; Mac OS X) Chrome/120 Safari/537"}
    with TestClient(app) as c:
        assert c.get("/api/terminal/accounts").status_code == 401
        assert c.get("/api/terminal/accounts", headers={"Authorization": f"Bearer {portal}"}).status_code == 401
        assert c.post("/api/terminal/login", json={"login": a.platform_login, "password": "haslo1234", "server": "PTF-Demo"}).status_code == 401
        assert c.post("/api/terminal/login", json={"login": a.platform_login, "password": "Pw-123456", "server": "Inny-Serwer"}).status_code == 401
        r = c.post("/api/terminal/login", json={"login": a.platform_login, "password": "Pw-123456", "server": "ptf-demo", "device": "abc123"},
                   headers=naglowki)
        assert r.status_code == 200
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        assert [k["id"] for k in c.get("/api/terminal/accounts", headers=h).json()["accounts"]] == [aid]
        assert c.get(f"/api/terminal/state?account_id={inne}", headers=h).status_code == 404
        assert c.get(f"/api/terminal/state?account_id={aid}", headers=h).status_code == 200
        # token terminala nie otwiera portalu
        assert c.get("/api/auth/me", headers=h).status_code == 401
        c.post("/api/terminal/logout", headers=h)
        assert c.get("/api/terminal/accounts", headers=h).status_code == 401
    s = SessionLocal()
    w = s.query(TerminalLogin).filter_by(account_id=aid, ok=True).order_by(TerminalLogin.id.desc()).first()
    assert (w.ip, w.country, w.city, w.device_id) == ("203.0.113.7", "PL", "Rzeszów", "abc123")
    s.close()


def test_zmiana_hasla_mt5_wylogowuje(rynek):
    aid, h = _konto()
    s = SessionLocal(); s.get(Account, aid).platform_password = "Nowe-654321"; s.commit(); s.close()
    with TestClient(app) as c:
        assert c.get("/api/terminal/accounts", headers=h).status_code == 401


def test_limit_nieudanych_prob(rynek, monkeypatch):
    monkeypatch.setattr(terminal, "_TEMPO_OFF", False)
    aid, _ = _konto()
    a = _acc(aid)
    with TestClient(app) as c:
        for _i in range(terminal.PROBY_LOGIN):
            assert c.post("/api/terminal/login", json={"login": a.platform_login, "password": "zle", "server": "PTF-Demo"}).status_code == 401
        assert c.post("/api/terminal/login", json={"login": a.platform_login, "password": "Pw-123456", "server": "PTF-Demo"}).status_code == 429


def test_panel_admina_blokada_flagi_i_wstrzymanie(rynek):
    aid, h = _konto()
    a = _acc(aid)
    s = SessionLocal()
    s.get(Trader, a.trader_id).signup_country = "PL"
    s.add(TerminalLogin(login=a.platform_login, account_id=aid + 1000, trader_id=a.trader_id + 1000, ok=True, ip="198.51.100.9"))
    s.commit(); s.close()
    adm = {"X-Admin-Token": get_settings().admin_token}
    with TestClient(app) as c:
        assert c.get("/api/admin/terminal/overview").status_code in (401, 403)
        c.post("/api/terminal/login", json={"login": a.platform_login, "password": "Pw-123456", "server": "PTF-Demo"},
               headers={"x-real-ip": "198.51.100.9", "x-vercel-ip-country": "DE"})
        d = c.get("/api/admin/terminal/overview", headers=adm).json()
        wiersz = next(r for r in d["accounts"] if r["id"] == aid)
        assert {f["key"] for f in wiersz["flags"]} >= {"country", "shared_ip"}
        assert wiersz["trader"]["id"] == a.trader_id and wiersz["last_login"]["ip"] == "198.51.100.9"
        # wstrzymane zlecenia: nowe odbite, zamykanie działa
        c.post("/api/admin/terminal/orders-paused", json={"paused": True}, headers=adm)
        assert _zlec(c, h, aid).status_code == 503
        c.post("/api/admin/terminal/orders-paused", json={"paused": False}, headers=adm)
        # podgląd admina nie handluje
        v = c.post(f"/api/admin/terminal/accounts/{aid}/view", headers=adm).json()["token"]
        imp = {"Authorization": f"Bearer {v}"}
        assert c.get(f"/api/terminal/state?account_id={aid}", headers=imp).status_code == 200
        assert _zlec(c, imp, aid).status_code == 403
        # blokada wylogowuje i nie wpuszcza
        c.post(f"/api/admin/terminal/accounts/{aid}/block", json={"blocked": True}, headers=adm)
        assert c.get("/api/terminal/accounts", headers=h).status_code == 401
        assert c.post("/api/terminal/login", json={"login": a.platform_login, "password": "Pw-123456", "server": "PTF-Demo"}).status_code == 403


def test_minimum_i_krok_0_01_lota(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        r = _zlec(c, h, aid, qty=0.005)                                # 0,005 lota BTC
        assert r.status_code == 400 and "Minimum size is 0.01 lots" in r.json()["detail"]
        r = _zlec(c, h, aid, qty=0.015)
        assert r.status_code == 400 and "steps of 0.01 lots" in r.json()["detail"]
        st = _zlec(c, h, aid, qty=0.02).json()
        p = st["positions"][0]
        # Częściowe zamknięcie nie może zostawić mniej niż 0,01 lota…
        r = c.post(f"/api/terminal/positions/{p['id']}/close", headers=h, json={"account_id": aid, "qty": 0.015})
        assert r.status_code == 400
        r = c.post(f"/api/terminal/positions/{p['id']}/close", headers=h, json={"account_id": aid, "qty": 0.01})
        assert r.status_code == 200 and r.json()["positions"][0]["qty"] == pytest.approx(0.01)
        # …a zlecenie zamykające dokładnie całą pozycję przechodzi zawsze.
        r = _zlec(c, h, aid, side="sell", qty=0.01)
        assert r.status_code == 200 and r.json()["positions"] == []


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


@pytest.mark.parametrize("akcja", ["order", "tpsl", "cancel-all"])
def test_akcja_po_niepoliczonym_breachu(rynek, akcja):
    """Pierwsza akcja po powrocie widzi breach z okna, zanim cokolwiek zrobi:
    nowe zlecenie nie wypełnia się na koncie, które już straciło dzienny limit."""
    aid, h = _konto()
    with TestClient(app) as c:
        pid = _zlec(c, h, aid, qty=5).json()["positions"][0]["id"]
        _cofnij_przeglad(aid, 3)
        rynek.swiece["BTC"] = [_swieca(2, 50_000, 50_000, 48_900, 49_950)]
        rynek.px["BTC"] = 50_000.0
        if akcja == "order":
            r = _zlec(c, h, aid, qty=1)
        elif akcja == "tpsl":
            r = c.patch(f"/api/terminal/positions/{pid}", headers=h, json={"account_id": aid, "sl": 49_000})
        else:
            r = c.post("/api/terminal/cancel-all", headers=h, json={"account_id": aid})
        assert r.status_code == 409 and "closed for trading" in r.json()["detail"]
    assert _acc(aid).status == "failed"
    assert [t[2:4] for t in _trades(aid)] == [("closed", "breach")]


def _polnoc_serwera(monkeypatch, minut_temu):
    """Strefa serwera, w której jego północ wypadła `minut_temu` minut temu."""
    teraz = datetime.now(timezone.utc)
    przes = (minut_temu - (teraz.hour * 60 + teraz.minute)) % 1440
    monkeypatch.setattr(terminal.settings, "server_utc_offset_hours", przes / 60)
    serwer = teraz + timedelta(minutes=przes)
    return (serwer - timedelta(days=1)).strftime("%Y-%m-%d"), serwer.strftime("%Y-%m-%d")


def _ocena_wczoraj(aid, wczoraj, minut_temu):
    """Ostatnia ocena reguł była wczoraj (doby serwera), przy equity 100k."""
    s = SessionLocal()
    a = s.get(Account, aid)
    a.day_key = wczoraj
    a.day_start_equity = a.day_start_balance = 100_000.0
    a.terminal_eval_at = datetime.now(timezone.utc) - timedelta(minutes=minut_temu)
    s.commit(); s.close()


def test_dzienna_strata_sprzed_polnocy_nie_ginie(rynek, monkeypatch):
    """Nikt nie patrzył na konto przez północ: wczorajszy dołek liczy się do
    WCZORAJSZEGO limitu, a nie znika przy baseline nowej doby."""
    wczoraj, _ = _polnoc_serwera(monkeypatch, 30)
    aid, h = _konto()
    with TestClient(app) as c:
        assert _zlec(c, h, aid, qty=5).status_code == 200
        _cofnij_przeglad(aid, 60)
        _ocena_wczoraj(aid, wczoraj, 60)
        # 23:40 serwera: -1100 na 5 BTC (-5,5k przy limicie 5k); o 00:10 cena już wróciła.
        rynek.swiece["BTC"] = [_swieca(50, 50_000, 50_000, 48_900, 49_950),
                               _swieca(20, 50_000, 50_000, 50_000, 50_000)]
        rynek.px["BTC"] = 50_000.0
        c.get(f"/api/terminal/state?account_id={aid}", headers=h)
    assert _acc(aid).status == "failed"
    s = SessionLocal()
    try:
        assert s.query(Breach).filter(Breach.account_id == aid).first().type == "daily_loss"
    finally:
        s.close()


def test_baseline_doby_to_equity_o_polnocy_serwera(rynek, monkeypatch):
    """Dzienna strata liczy się od equity o północy serwera, nie od chwili,
    w której ktoś pierwszy raz w tej dobie otworzył stronę."""
    wczoraj, dzis = _polnoc_serwera(monkeypatch, 30)
    aid, h = _konto()
    with TestClient(app) as c:
        st = _zlec(c, h, aid, qty=5).json()
        saldo, wejscie = st["account"]["balance"], st["positions"][0]["entry"]
        _cofnij_przeglad(aid, 60)
        _ocena_wczoraj(aid, wczoraj, 60)
        # Północ przy 50 000, potem zjazd do 49 200: -4k od północy, limit 5k stoi.
        rynek.swiece["BTC"] = [_swieca(40, 50_000, 50_000, 50_000, 50_000),
                               _swieca(29, 50_000, 50_000, 49_200, 49_200),
                               _swieca(10, 49_200, 49_200, 49_200, 49_200)]
        rynek.px["BTC"] = 49_200.0
        c.get(f"/api/terminal/state?account_id={aid}", headers=h)
        a = _acc(aid)
        assert a.status == "active" and a.day_key == dzis
        assert abs(a.day_start_equity - (saldo + 5 * (50_000 - wejscie))) < 1
        # Jeszcze 300 w dół: -5,5k od północy, choć od pierwszego przeglądu doby tylko -1,5k.
        rynek.px["BTC"] = 48_900.0
        c.get(f"/api/terminal/state?account_id={aid}", headers=h)
    assert _acc(aid).status == "failed"


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


def test_retencja_logowan(rynek):
    s = SessionLocal()
    stary = TerminalLogin(login="stary-1", ok=False, created_at=datetime.now(timezone.utc) - timedelta(days=terminal.LOGOWANIA_DNI + 1))
    nowy = TerminalLogin(login="nowy-1", ok=False)
    s.add_all([stary, nowy]); s.commit()
    assert terminal.wyczysc_logowania()["deleted"] >= 1
    assert s.query(TerminalLogin).filter_by(login="stary-1").count() == 0
    assert s.query(TerminalLogin).filter_by(login="nowy-1").count() == 1
    s.close()


def test_trailing_i_break_even_i_przesuniecie_zlecenia(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        st = _zlec(c, h, aid, qty=0.01).json()
        p = st["positions"][0]
        # break-even tylko na zysku
        r = c.post(f"/api/terminal/positions/{p['id']}/break-even", json={"account_id": aid}, headers=h)
        assert r.status_code == 400 and "in profit" in r.json()["detail"]
        # trailing ustawia SL poniżej ceny i nie cofa go
        r = c.patch(f"/api/terminal/positions/{p['id']}", json={"account_id": aid, "tp": None, "sl": None, "trail": 100}, headers=h)
        assert r.status_code == 200
        q = r.json()["positions"][0]
        assert q["trail"] == 100 and q["sl"] is not None and q["sl"] < q["entry"] + 1
    s = SessionLocal()
    pos = s.get(Trade, p["id"])
    sl0 = pos.sl
    terminal._podciagnij_sl(pos, {"h": sl0 + 500, "l": sl0 + 150})
    assert pos.sl == sl0 + 400
    terminal._podciagnij_sl(pos, {"h": sl0 + 200, "l": sl0 + 150})
    assert pos.sl == sl0 + 400, "SL nie cofa się"
    s.close()


def test_notatka_i_historia(rynek):
    aid, h = _konto()
    with TestClient(app) as c:
        p = _zlec(c, h, aid, qty=0.01).json()["positions"][0]
        c.post(f"/api/terminal/positions/{p['id']}/close", json={"account_id": aid, "max_slippage_pct": 2}, headers=h)
        d = c.get(f"/api/terminal/history?account_id={aid}", headers=h).json()
        tid = d["history"][0]["id"]
        assert c.put(f"/api/terminal/trades/{tid}/note", json={"account_id": aid, "note": "  plan ok "}, headers=h).json()["note"] == "plan ok"
        assert c.get(f"/api/terminal/history?account_id={aid}", headers=h).json()["history"][0]["note"] == "plan ok"


def test_admin_widzi_hasla_mt5_i_zamyka_wszystkie_pozycje(rynek):
    aid, h = _konto(platform_investor_password="Inv-9")
    adm = {"X-Admin-Token": get_settings().admin_token}
    with TestClient(app) as c:
        assert _zlec(c, h, aid).status_code == 200
        wiersz = next(r for r in c.get("/api/admin/terminal/overview", headers=adm).json()["accounts"] if r["id"] == aid)
        assert wiersz["password"] == "Pw-123456" and wiersz["investor_password"] == "Inv-9" and wiersz["open_positions"] == 1
        rynek.px["BTC"] = 50_500.0
        r = c.post(f"/api/admin/terminal/accounts/{aid}/close-all", headers=adm)
        assert r.status_code == 200 and r.json()["closed"] == 1
        st = c.get(f"/api/terminal/state?account_id={aid}", headers=h).json()
        assert st["positions"] == [] and st["history"][0]["reason"] == "admin" and st["history"][0]["close"] == 50_500.0
        assert c.post("/api/admin/terminal/accounts/999999/close-all", headers=adm).status_code == 404
        assert c.post(f"/api/admin/terminal/accounts/{aid}/close-all").status_code in (401, 403)
