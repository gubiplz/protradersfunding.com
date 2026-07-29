"""Trade BOT — silnik generujący aktywność handlową na koncie challenge.

Najważniejszy test w tym pliku to `test_boty_nie_sa_klonami`: wymóg brzmiał
wprost, żeby na leaderboardzie nie wylądowała lista botów z tymi samymi
transakcjami i tymi samymi wynikami.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, poller, rules, tradebot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, EquitySnapshot, Trade, Trader  # noqa: E402
from app.rules import EquityTick  # noqa: E402

init_db()
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

# poniedziałek 09:00 UTC — w środku sesji także dla tempa „realistic"
START = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)


def _account(login: str, *, size=100_000.0, max_lots=6.0, style="balanced",
             pace="active", target_pct=0.0) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Bot Tester", product_key="2step-100k",
                  initial_balance=size, steps=2, max_lots=max_lots,
                  balance=size, equity=size, peak_equity=size,
                  day_start_equity=size, day_start_balance=size,
                  started_at=START.replace(tzinfo=None))
    s.add(acc); s.commit()
    tradebot.start(s, acc, style=style, pace=pace, target_pct=target_pct)
    # Seed z SAMEGO loginu: seed_for() miesza acc.id, a auto-increment zalezy od
    # tego, ile kont zalozyly WCZESNIEJSZE pliki testow — trajektoria bota
    # zmieniala sie od niepowiazanych testow i progi robily sie loteria.
    import hashlib
    acc.bot_seed = int.from_bytes(hashlib.sha256(login.encode()).digest()[:4], "big") & 0x7FFFFFFF
    s.commit()
    aid = acc.id
    s.close()
    return aid


def _drive(account_id: int, ticks: int, step_sec: int = 45):
    """Przepuszcza bota przez PRAWDZIWY silnik reguł — tak samo jak poller.

    Zwraca (konto, lista snapshotów) po zamknięciu sesji.
    """
    s = SessionLocal()
    acc = s.get(Account, account_id)
    now = START
    snaps = []
    for _ in range(ticks):
        snap = tradebot.tick(s, acc, now=now)
        snaps.append(snap)
        cfg = rules.config_from_account(acc)
        rt = poller._runtime_from(acc)
        res = rules.evaluate(cfg, rt, EquityTick(
            equity=snap.equity, balance=snap.balance, open_pnl=snap.open_pnl,
            volume_lots=snap.volume_lots, volume_known=snap.volume_known,
            day_key=poller.server_day_key(now), has_open_position=snap.has_open_position,
            days_elapsed=(now - START).days,
        ))
        poller._write_runtime(acc, rt)
        s.add(EquitySnapshot(account_id=acc.id, ts=now.replace(tzinfo=None),
                             balance=snap.balance, equity=snap.equity,
                             open_pnl=snap.open_pnl, day_key=poller.server_day_key(now)))
        if res.passed_phase:
            poller._advance_phase(acc, rt)
        s.commit()
        now += timedelta(seconds=step_sec)
    s.refresh(acc)
    s.close()
    return acc, snaps


def _trades(account_id: int, only_closed=True):
    s = SessionLocal()
    q = s.query(Trade).filter(Trade.account_id == account_id)
    if only_closed:
        q = q.filter(Trade.status == "closed")
    out = q.order_by(Trade.id).all()
    s.close()
    return out


# --------------------------------------------------------------------------- #
#  Bezpieczeństwo: bot nie może wywrócić konta, którym się opiekuje            #
# --------------------------------------------------------------------------- #
def test_bot_nie_lamie_regul_na_dlugim_biegu():
    aid = _account("bot-safe-1")
    acc, snaps = _drive(aid, 600)
    assert acc.status != "failed", f"bot wywrócił konto: {acc.breach_reason}"
    assert all(s.volume_lots <= 6.0 + 1e-9 for s in snaps)
    # equity nigdy nie zbliża się do dziennej podłogi (5% ze 100k = 95k)
    assert min(s.equity for s in snaps) > 96_000


def test_wolumen_przyciety_do_limitu_konta():
    """Konto z niskim max_lots musi dostać przycięte pozycje, nie breach."""
    aid = _account("bot-lots-1", size=200_000, max_lots=0.05)
    acc, snaps = _drive(aid, 200)
    assert acc.status != "failed"
    assert max(s.volume_lots for s in snaps) <= 0.05 + 1e-9


def test_bot_zatrzymuje_sie_na_docelowym_zysku():
    aid = _account("bot-target-1", target_pct=1.0)
    acc, _ = _drive(aid, 1200)
    profit_pct = (acc.balance - acc.initial_balance) / acc.initial_balance * 100
    # cel 1% — bot przestaje otwierać nowe pozycje po jego osiągnięciu,
    # więc zysk zatrzymuje się tuż nad progiem, a nie leci w kosmos
    assert 0.9 <= profit_pct < 2.5


# --------------------------------------------------------------------------- #
#  Wymóg usera: żadnego „leaderboardu klonów"                                  #
# --------------------------------------------------------------------------- #
def test_boty_nie_sa_klonami():
    ids = [_account(f"bot-uniq-{i}") for i in range(8)]
    for aid in ids:
        _drive(aid, 400)

    koszyki, sekwencje, wyniki = [], [], []
    for aid in ids:
        tr = _trades(aid)
        assert len(tr) >= 5, "za mało transakcji, żeby ocenić rozjazd"
        koszyki.append(frozenset(t.symbol for t in tr))
        sekwencje.append(tuple((t.symbol, t.side, round(t.pnl, 2)) for t in tr[:10]))
        wyniki.append(round(sum(t.pnl for t in tr), 2))

    assert len(set(sekwencje)) == len(ids), "dwa boty zagrały identyczną serię"
    assert len(set(wyniki)) == len(ids), "dwa boty mają identyczny wynik"
    assert len(set(koszyki)) >= len(ids) - 1, "boty handlują na tych samych instrumentach"
    # różnorodność instrumentów w całej populacji
    assert len({s for k in koszyki for s in k}) >= 8


def test_seed_miesci_sie_w_int4_postgresa():
    """Regresja: seed z 6 bajtow SHA-256 przechodzil na SQLite, a na Supabase
    wywalal `NumericValueOutOfRange` — kolumna bot_seed to INTEGER (int4)."""
    INT4_MAX = 2_147_483_647
    s = SessionLocal()
    seeds = set()
    for i in range(200):
        acc = Account(id=10_000 + i, login=f"seed-{i}-{'x' * (i % 7)}")
        v = tradebot.seed_for(acc)
        assert 0 <= v <= INT4_MAX, f"seed {v} nie zmiesci sie w kolumnie INTEGER"
        seeds.add(v)
    s.close()
    # maska nie moze robic kolizji na realnej skali kont
    assert len(seeds) == 200


def test_persona_jest_deterministyczna():
    s = SessionLocal()
    acc = s.get(Account, _account("bot-det-1"))
    a, b = tradebot.persona_for(acc), tradebot.persona_for(acc)
    s.close()
    assert a == b


# --------------------------------------------------------------------------- #
#  Liczby muszą się zgadzać, gdy ktoś je sprawdzi kalkulatorem                 #
# --------------------------------------------------------------------------- #
def test_pnl_zgadza_sie_z_cenami():
    aid = _account("bot-math-1")
    _drive(aid, 400)
    trades = _trades(aid)
    assert trades
    for t in trades:
        inst = tradebot.INSTRUMENTS[t.symbol]
        pv = tradebot._point_value(inst, t.open_price)
        sign = 1 if t.side == "buy" else -1
        expected = sign * (t.close_price - t.open_price) * t.lots * pv
        assert abs(expected - t.pnl) < 0.01, f"{t.symbol}: {expected} != {t.pnl}"


def test_balans_zgadza_sie_z_suma_transakcji():
    aid = _account("bot-sum-1")
    acc, _ = _drive(aid, 500)
    zamkniete = sum(t.pnl for t in _trades(aid))
    otwarte = [t for t in _trades(aid, only_closed=False) if t.status == "open"]
    # balans = kapitał + zrealizowane; otwarta pozycja siedzi w equity, nie w balansie
    assert abs(acc.balance - (acc.initial_balance + zamkniete)) < 0.02
    assert abs(acc.equity - acc.balance - sum(t.pnl for t in otwarte)) < 0.02


def test_krzywa_equity_rosnie():
    """Pro trader ma zarabiać — inaczej cała funkcja nie ma sensu."""
    zyskowne = 0
    for i in range(6):
        aid = _account(f"bot-curve-{i}")
        acc, _ = _drive(aid, 700)
        if acc.balance > acc.initial_balance:
            zyskowne += 1
    assert zyskowne >= 5, f"tylko {zyskowne}/6 kont na plusie"


def test_sa_i_wygrane_i_przegrane():
    """Sama seria wygranych wygląda jak wyklejona — mają być też stratne trady.

    Liczone na kilku kontach naraz: pojedyncza persona może mieć szczęśliwą serię
    i asercja na jednym koncie bywała krucha (seed zależy od id konta, a te
    przesuwa się, gdy inny plik testów dołoży wiersze do wspólnej bazy).
    """
    tr = []
    for i in range(4):
        aid = _account(f"bot-mix-{i}")
        _drive(aid, 700)
        tr += _trades(aid)
    assert any(t.pnl > 0 for t in tr) and any(t.pnl < 0 for t in tr)
    win_rate = sum(1 for t in tr if t.pnl > 0) / len(tr)
    assert 0.45 < win_rate < 0.95


# --------------------------------------------------------------------------- #
#  Sterowanie                                                                  #
# --------------------------------------------------------------------------- #
def test_stop_domyka_otwarta_pozycje():
    aid = _account("bot-stop-1")
    _drive(aid, 60)
    s = SessionLocal()
    acc = s.get(Account, aid)
    otwarte_przed = s.query(Trade).filter(Trade.account_id == aid, Trade.status == "open").count()
    tradebot.stop(s, acc)
    assert acc.bot_enabled is False
    assert s.query(Trade).filter(Trade.account_id == aid, Trade.status == "open").count() == 0
    assert acc.open_pnl == 0.0
    s.close()
    assert otwarte_przed <= 1


def test_start_nadaje_seed_i_ustawienia():
    aid = _account("bot-start-1", style="swing", pace="realistic", target_pct=7.5)
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.bot_enabled is True and acc.bot_seed is not None
    assert acc.bot_style == "swing" and acc.bot_pace == "realistic"
    assert acc.bot_target_pct == 7.5
    s.close()


def test_tempo_realistic_nie_handluje_w_weekend():
    aid = _account("bot-weekend-1", pace="realistic")
    s = SessionLocal()
    acc = s.get(Account, aid)
    sobota = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    for i in range(50):
        tradebot.tick(s, acc, now=sobota + timedelta(minutes=5 * i))
    s.commit()
    assert s.query(Trade).filter(Trade.account_id == aid).count() == 0
    s.close()


# --------------------------------------------------------------------------- #
#  API admina                                                                  #
# --------------------------------------------------------------------------- #
client = TestClient(app)


def _trader_z_kontem(email: str):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Bot Owner", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    acc = Account(login=f"api-{tr.id}", trader_name="Bot Owner", trader_id=tr.id,
                  product_key="2step-100k", initial_balance=100_000, steps=2, max_lots=6.0,
                  balance=100_000, equity=100_000, peak_equity=100_000,
                  day_start_equity=100_000, day_start_balance=100_000)
    s.add(acc); s.commit()
    out = (tr.id, acc.id, {"Authorization": f"Bearer {auth.make_token(tr.id)}"})
    s.close()
    return out


def test_endpointy_bota_wymagaja_admina():
    _, aid, _ = _trader_z_kontem("bot-auth@x.pl")
    assert client.post(f"/api/admin/accounts/{aid}/bot", json={}).status_code in (401, 403)
    assert client.delete(f"/api/admin/accounts/{aid}/bot").status_code in (401, 403)


def test_start_i_stop_przez_api():
    _, aid, _ = _trader_z_kontem("bot-api@x.pl")
    r = client.post(f"/api/admin/accounts/{aid}/bot",
                    json={"style": "scalper", "pace": "demo", "target_pct": 4}, headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["bot_enabled"] is True
    assert client.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()["bot_enabled"] is True

    r = client.delete(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["bot_enabled"] is False
    assert client.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()["bot_enabled"] is False


def test_pauza_wstrzymuje_nowe_wejscia_ale_nie_oddaje_konta():
    """Pauza ≠ Stop: konto zostaje pod botem, saldo trzyma, brak nowych pozycji."""
    aid = _account("bot-pause-1", pace="demo")
    _drive(aid, 200, step_sec=30)

    s = SessionLocal()
    acc = s.get(Account, aid)
    tradebot.set_paused(s, acc, True)
    saldo = acc.balance
    s.close()
    przed = len(_trades(aid))

    _drive(aid, 400, step_sec=30)          # dużo ticków na pauzie
    s = SessionLocal(); acc = s.get(Account, aid)
    assert acc.bot_enabled is True and acc.bot_paused is True   # konto NADAL botowe
    assert s.query(Trade).filter(Trade.account_id == aid, Trade.status == "open").count() == 0
    s.close()
    # otwarta pozycja mogła się jeszcze domknąć, ale nowych wejść już nie ma
    assert len(_trades(aid)) - przed <= 1
    # Konto trzyma dorobek: pauza NIE cofa salda do kapitału startowego (to robi Stop).
    # Sama kwota może drgnąć o jedną domykaną pozycję — także w dół, bo zawisłej
    # transakcji świadomie nie porzucamy w połowie.
    assert acc.balance > acc.initial_balance
    assert abs(acc.balance - saldo) < acc.initial_balance * 0.01

    # Wznowienie sprawdzamy na świeżym dniu: po 5 h ciągłego handlu persona ma
    # wyczerpany dzienny budżet i nie otwierałaby nic również bez pauzy — taka
    # asercja mierzyłaby limit dzienny, a nie pauzę.
    s = SessionLocal()
    acc = s.get(Account, aid)
    p = tradebot.persona_for(acc)
    nowy_dzien = START + timedelta(days=3)
    assert tradebot._should_open(s, acc, p, acc.balance, nowy_dzien) is False   # wciąż pauza
    tradebot.set_paused(s, acc, False)
    assert tradebot._should_open(s, acc, p, acc.balance, nowy_dzien) is True    # znów handluje
    s.close()


def test_pauza_przez_api_i_wznowienie():
    _, aid, _ = _trader_z_kontem("bot-pause-api@x.pl")
    client.post(f"/api/admin/accounts/{aid}/bot", json={"pace": "demo"}, headers=ADMIN_H)

    r = client.patch(f"/api/admin/accounts/{aid}/bot", json={"paused": True}, headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["bot_paused"] is True
    assert client.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()["bot_paused"] is True

    r = client.patch(f"/api/admin/accounts/{aid}/bot", json={"paused": False}, headers=ADMIN_H)
    assert r.json()["bot_paused"] is False
    # stop czyści też pauzę
    client.patch(f"/api/admin/accounts/{aid}/bot", json={"paused": True}, headers=ADMIN_H)
    client.delete(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H)
    d = client.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()
    assert d["bot_enabled"] is False and d["bot_paused"] is False


def test_pauza_wymaga_uruchomionego_bota_i_admina():
    _, aid, _ = _trader_z_kontem("bot-pause-guard@x.pl")
    assert client.patch(f"/api/admin/accounts/{aid}/bot", json={"paused": True}).status_code in (401, 403)
    # bot nie wystartowany -> czytelny 400, nie ciche „ok"
    assert client.patch(f"/api/admin/accounts/{aid}/bot", json={"paused": True},
                        headers=ADMIN_H).status_code == 400


def test_trader_nie_widzi_pauzy():
    _, aid, h = _trader_z_kontem("bot-pause-hidden@x.pl")
    client.post(f"/api/admin/accounts/{aid}/bot", json={}, headers=ADMIN_H)
    client.patch(f"/api/admin/accounts/{aid}/bot", json={"paused": True}, headers=ADMIN_H)
    assert "bot_paused" not in client.get(f"/api/me/accounts/{aid}", headers=h).json()


def test_bot_nie_dziala_na_koncie_w_provisioningu():
    _, aid, _ = _trader_z_kontem("bot-prov@x.pl")
    s = SessionLocal(); s.get(Account, aid).status = "provisioning"; s.commit(); s.close()
    r = client.post(f"/api/admin/accounts/{aid}/bot", json={}, headers=ADMIN_H)
    assert r.status_code == 400


def test_trader_nie_widzi_ze_konto_jedzie_z_bota():
    """Dashboard tradera ma wyglądać jak dashboard tradera — bez pola `bot_enabled`."""
    _, aid, h = _trader_z_kontem("bot-hidden@x.pl")
    client.post(f"/api/admin/accounts/{aid}/bot", json={}, headers=ADMIN_H)

    lista = client.get("/api/me/accounts", headers=h).json()
    assert lista and all("bot_enabled" not in a for a in lista)
    detal = client.get(f"/api/me/accounts/{aid}", headers=h).json()
    assert "bot_enabled" not in detal and "bot_style" not in detal
    # admin natomiast widzi to od razu
    assert client.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()["bot_enabled"] is True


def test_activity_oddaje_trady_wlascicielowi():
    _, aid, h = _trader_z_kontem("bot-act@x.pl")
    s = SessionLocal()
    acc = s.get(Account, aid)
    tradebot.start(s, acc, style="scalper", pace="demo")
    s.close()
    _drive(aid, 300, step_sec=30)

    act = client.get(f"/api/me/accounts/{aid}/activity", headers=h).json()
    assert act["ledger"], "brak transakcji w activity"
    t = act["ledger"][0]
    assert t["symbol"] in tradebot.INSTRUMENTS and t["side"] in ("buy", "sell")
    assert t["lots"] > 0
    assert act["days"], "brak dni w kalendarzu"


def test_activity_nie_zdradza_godziny_ani_poziomow():
    """Historia ma być jak u OneTraders: sama data, bez Entry/Exit.
    Ukrywanie poziomów w tabeli przy zostawieniu ich w JSON-ie nie miałoby sensu."""
    _, aid, h = _trader_z_kontem("bot-noprice@x.pl")
    s = SessionLocal()
    tradebot.start(s, s.get(Account, aid), style="scalper", pace="demo")
    s.close()
    _drive(aid, 300, step_sec=30)

    trades = client.get(f"/api/me/accounts/{aid}/activity", headers=h).json()["ledger"]
    assert trades
    for t in trades:
        assert "open_price" not in t and "close_price" not in t
        assert "ts" not in t
        assert len(t["day"]) == 10 and t["day"].count("-") == 2   # YYYY-MM-DD, zero godziny


def test_activity_obcego_konta_daje_404():
    _, aid, _ = _trader_z_kontem("bot-owner@x.pl")
    _, _, obcy = _trader_z_kontem("bot-intruder@x.pl")
    assert client.get(f"/api/me/accounts/{aid}/activity", headers=obcy).status_code == 404


def test_activity_bez_tradow_ma_pusta_liste():
    """Konto z realnym feedem MT5 nie ma per-trade danych — brak regresji."""
    _, aid, h = _trader_z_kontem("bot-notrades@x.pl")
    act = client.get(f"/api/me/accounts/{aid}/activity", headers=h).json()
    assert act["ledger"] == [] and "days" in act


def test_poller_nie_dotyka_feedu_dla_bota():
    """Konto botowe nie może odpalać logowania do web terminala MetaQuotes."""
    import asyncio

    class SpyFeed:
        called = False

        async def snapshot(self, *a, **kw):
            SpyFeed.called = True
            return None

    aid = _account("bot-feed-1")
    s = SessionLocal()
    acc = s.get(Account, aid)
    asyncio.run(poller.process_account(s, acc, SpyFeed()))
    s.close()
    assert SpyFeed.called is False
