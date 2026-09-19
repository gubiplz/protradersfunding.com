"""Tryb zjazdu (`bot_mode='doom'`) — sterowana porazka konta.

Ubicie konta jednym klikiem juz istnialo (`POST .../breach`), ale zostawia
w bazie `Breach(type='manual')` i konto, ktore w jednej sekundzie przeszlo
z zysku na dno. Zjazd ma wygladac jak trader, ktory sie posypal: strata
rozlozona na dni, a breach ma paść Z REKI SILNIKA REGUL — dokladnie tym samym
kodem, ktory lapie prawdziwe konta. Dlatego testy jada przez `process_account`,
a nie przez skrot w tresci testu.
"""
import asyncio
import hashlib
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import poller, tradebot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Breach, Trade  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

START = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)   # poniedzialek, rynek otwarty


class Zegar:
    """Sterowany czas serwera. `process_account` czyta zegar sam z siebie, a zjazd
    z zalozenia trwa dniami — bez tego nie da sie go przejechac w tescie."""

    def __init__(self, start: datetime):
        self.teraz = start

    def przesun(self, sekundy: int) -> None:
        self.teraz += timedelta(seconds=sekundy)


@contextmanager
def _czas(zegar: Zegar):
    """Podstawia zegar wszedzie, gdzie poller siega po „teraz".

    `tradebot.tick()` wolany przez pollera nie dostaje momentu w argumencie
    i schodzi do `datetime.now()` — bez tej podmianki symulacja jechalaby po
    prawdziwej dacie, a w weekend bot w ogole nie otwiera pozycji.
    """
    org_tick, org_poll = tradebot.tick, poller.server_now
    tradebot.tick = lambda s, a, now=None: org_tick(s, a, now=now or zegar.teraz)
    poller.server_now = lambda: zegar.teraz
    try:
        yield
    finally:
        tradebot.tick, poller.server_now = org_tick, org_poll


class NullFeed:
    """Konta botowego poller i tak nie pyta o snapshot — to jest asercja sama w sobie."""

    async def snapshot(self, *a, **kw):
        raise AssertionError("poller poszedl po feed dla konta botowego")


def _konto(login: str, *, limit: str = "overall", dni: float = 3.0,
           size: float = 100_000.0) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Doom", product_key="2step-100k",
                  preset="2step-100k", initial_balance=size, steps=2,
                  status="active", phase="eval_1", profit_target_p1=8.0,
                  max_daily_loss_pct=5.0, max_overall_loss_pct=10.0,
                  min_trading_days=4, max_lots=6.0,
                  balance=size, equity=size, peak_equity=size,
                  day_start_equity=size, day_start_balance=size,
                  started_at=START.replace(tzinfo=None))
    s.add(acc); s.commit()
    tradebot.start(s, acc, pace="busy", mode="doom", doom_days=dni, doom_limit=limit)
    # Termin zjazdu przestawiamy na zegar symulacji — `start()` liczy go od
    # prawdziwego „teraz", a bieg jedzie po lipcu 2026: dystans do podlogi
    # rozlozylby sie na dziesiatki dni i konto nigdy by nie polegio.
    acc.bot_doom_deadline = (START + timedelta(days=dni)).replace(tzinfo=None)
    acc.bot_seed = int.from_bytes(hashlib.sha256(login.encode()).digest()[:4], "big") & 0x7FFFFFFF
    s.commit()
    aid = acc.id
    s.close()
    return aid


def _zjedz(account_id: int, maks_tickow: int = 3000, step_sec: int = 600):
    """Krec pollerem, az konto polegnie. Zwraca (konto, minimalne equity, dni)."""
    zegar = Zegar(START)
    s = SessionLocal()
    acc = s.get(Account, account_id)
    min_eq = float(acc.equity)
    dni = set()
    with _czas(zegar):
        for _ in range(maks_tickow):
            asyncio.run(poller.process_account(s, acc, NullFeed()))
            min_eq = min(min_eq, float(acc.equity or 0.0))
            dni.add(zegar.teraz.strftime("%Y-%m-%d"))
            if acc.status == "failed":
                break
            zegar.przesun(step_sec)
    s.refresh(acc)
    s.close()
    return acc, min_eq, len(dni)


def _breache(account_id: int) -> list[Breach]:
    s = SessionLocal()
    out = s.query(Breach).filter(Breach.account_id == account_id).order_by(Breach.id).all()
    s.close()
    return out


# --------------------------------------------------------------------------- #
#  Zjazd konczy sie prawdziwym breachem                                        #
# --------------------------------------------------------------------------- #
def test_zjazd_lamie_limit_calkowity_reka_silnika_regul():
    aid = _konto("doom-overall", limit="overall", dni=3.0)
    acc, _, _ = _zjedz(aid)

    assert acc.status == "failed", "zjazd nie dowiozl konta na podloge"
    typy = [b.type for b in _breache(aid)]
    assert typy, "konto polegio bez ani jednego wpisu Breach"
    assert "manual" not in typy, "breach zdradza, ze to admin, a nie rynek"
    assert typy[0] == "max_drawdown", f"mial pasc limit calkowity, padl {typy[0]}"


def test_zjazd_na_limit_dzienny_lamie_wlasnie_ten_limit():
    aid = _konto("doom-daily", limit="daily", dni=1.0)
    acc, _, _ = _zjedz(aid)
    assert acc.status == "failed"
    assert _breache(aid)[0].type == "daily_loss"


def test_zjazd_na_calkowity_nie_lamie_po_drodze_dziennego():
    """Bez klampu porcji pierwszy dzien zjazdu oddalby caly dystans do podlogi
    calkowitej i breach padlby z niewlasciwego powodu."""
    aid = _konto("doom-bezdziennego", limit="overall", dni=4.0)
    acc, _, dni = _zjedz(aid)
    assert acc.status == "failed"
    assert [b.type for b in _breache(aid)] == ["max_drawdown"]
    assert dni >= 2, "zjazd zmiescil sie w jednym dniu — to nie jest 'rozlozony w czasie'"


def test_po_breachu_nie_zostaje_otwarta_pozycja():
    """Poller przetwarza tylko konta active/funded, wiec nikt by jej juz nie domknal
    i sterczalaby w portalu klienta w nieskonczonosc."""
    aid = _konto("doom-sprzatanie", dni=3.0)
    acc, _, _ = _zjedz(aid)
    s = SessionLocal()
    otwarte = s.query(Trade).filter(Trade.account_id == aid, Trade.status == "open").count()
    s.close()
    assert acc.status == "failed"
    assert otwarte == 0


def test_po_breachu_bot_jest_zgaszony():
    """Inaczej po recznym wskrzeszeniu konta zjazd ruszylby od nowa."""
    aid = _konto("doom-gasi", dni=3.0)
    acc, _, _ = _zjedz(aid)
    assert acc.bot_enabled is False
    assert acc.bot_mode == "profit"
    assert acc.bot_doom_deadline is None


def test_zjazd_zostawia_wiarygodna_historie():
    """Kilka wygranych w serii strat to roznica miedzy 'trader sie posypal'
    a 'admin kliknal ubij konto'."""
    aid = _konto("doom-historia", dni=4.0)
    _zjedz(aid)
    s = SessionLocal()
    trades = s.query(Trade).filter(Trade.account_id == aid, Trade.status == "closed").all()
    s.close()
    assert len(trades) >= 20
    assert any(t.pnl > 0 for t in trades), "same straty — historia wyglada na wyklejona"
    assert any(t.pnl < 0 for t in trades)
    assert all(abs(t.pnl) >= 0.005 for t in trades), "transakcje-widma z zerowym P&L"


# --------------------------------------------------------------------------- #
#  API                                                                         #
# --------------------------------------------------------------------------- #
def _konto_z_zyskiem(login: str) -> int:
    """Zwykly bot na zysk, gotowy do przelaczenia na zjazd."""
    s = SessionLocal()
    acc = Account(login=login, trader_name="Doom API", product_key="2step-100k",
                  preset="2step-100k", initial_balance=100_000.0, steps=2,
                  status="active", phase="eval_1", balance=100_000.0, equity=100_000.0,
                  peak_equity=100_000.0, day_start_equity=100_000.0,
                  day_start_balance=100_000.0)
    s.add(acc); s.commit()
    tradebot.start(s, acc, target_pct=5.0)
    aid = acc.id
    s.close()
    return aid


def test_przelaczenie_na_zjazd_kasuje_sufit_zysku():
    """Sufit i zjazd wykluczaja sie: jedno mowi 'stan na +5%', drugie 'zjedz na dno'."""
    aid = _konto_z_zyskiem("doom-api-przel")
    o = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"mode": "doom", "doom_days": 4}).json()["bot_outcome"]
    assert o["mode"] == "doom"
    assert o["cap_pct"] == 0.0 and o["cap_equity"] is None
    assert o["doom_deadline"] and o["doom_limit"] == "overall"
    assert o["doom_floor"] == 90_000.0      # static DD 10% ze 100k


def test_powrot_na_zysk_kasuje_termin_zjazdu():
    aid = _konto_z_zyskiem("doom-api-powrot")
    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"mode": "doom"})
    o = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"mode": "profit"}).json()["bot_outcome"]
    assert o["mode"] == "profit" and o["doom_deadline"] is None


def test_sufit_razem_ze_zjazdem_jest_odrzucany():
    aid = _konto_z_zyskiem("doom-api-oba")
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"mode": "doom", "target_pct": 7.0})
    assert r.status_code == 400


def test_zjazdu_nie_da_sie_zlecic_na_martwym_koncie():
    aid = _konto_z_zyskiem("doom-api-martwe")
    s = SessionLocal()
    acc = s.get(Account, aid); acc.status = "failed"; s.commit(); s.close()

    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"mode": "doom"})
    assert r.status_code == 400


def test_zjazd_da_sie_zlecic_od_razu_przy_starcie_bota():
    s = SessionLocal()
    acc = Account(login="doom-api-start", trader_name="D", product_key="2step-100k",
                  preset="2step-100k", initial_balance=100_000.0, steps=2, status="active",
                  phase="eval_1", balance=100_000.0, equity=100_000.0, peak_equity=100_000.0,
                  day_start_equity=100_000.0, day_start_balance=100_000.0)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    r = client.post(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                    json={"mode": "doom", "doom_days": 2, "doom_limit": "daily"})
    assert r.status_code == 200
    o = r.json()["bot_outcome"]
    assert o["mode"] == "doom" and o["doom_limit"] == "daily"
    assert o["doom_floor"] == 95_000.0      # limit dzienny 5% ze 100k


def test_czyszczenie_historii_kasuje_tryb_zjazdu():
    """Inaczej wyczyszczone konto rusza od zera i od razu jedzie na dno."""
    aid = _konto_z_zyskiem("doom-clear")
    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"mode": "doom"})
    r = client.post(f"/api/admin/accounts/{aid}/clear-history", headers=ADMIN_H, json={})
    assert r.status_code == 200

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.bot_mode == "profit" and acc.bot_doom_deadline is None
    s.close()


def test_stop_bota_kasuje_tryb_zjazdu():
    aid = _konto_z_zyskiem("doom-stop")
    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"mode": "doom"})
    client.delete(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H)

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.bot_enabled is False and acc.bot_mode == "profit"
    s.close()
