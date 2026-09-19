"""Sterowanie wynikiem challenge'u: sufit zysku i to, co panel z niego czyta.

Sedno wymagania brzmialo „czesto chce, zeby brakowalo setnych procenta". Do tej
pory `bot_target_pct` byl MIEKKIM stopem: bot przestawal otwierac nowe pozycje
dopiero PO przekroczeniu celu, wiec ostatnia transakcja przeskakiwala ponad prog
fazy i konto zdawalo przez przypadek. Tutaj pilnujemy, ze sufit jest twardy
i liczony w dolarach — bo `Float` nie trzyma 9,97 dokladnie i porownanie samych
procentow rozjezdzaloby sie na ostatnim miejscu po przecinku.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import hashlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import poller, rules, tradebot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trade  # noqa: E402
from app.rules import EquityTick  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

# poniedzialek 09:00 UTC — dlugie biegi maja zaczynac przy otwartym rynku
START = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
# Tyle tickow po 10 minut (~41 dni) potrzebuje bot, zeby dowiezc +8% na kazdej
# personie. Krotszy bieg staje w polowie drogi i test o suficie nic nie dowodzi.
DLUGI_BIEG = 6000


def _konto(login: str, *, cel: float, size: float = 100_000.0,
           target_p1: float = 8.0, dni: int = 4, pace: str = "busy") -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Outcome", product_key="2step-100k",
                  preset="2step-100k", initial_balance=size, steps=2,
                  status="active", phase="eval_1", profit_target_p1=target_p1,
                  min_trading_days=dni, max_lots=6.0,
                  balance=size, equity=size, peak_equity=size,
                  day_start_equity=size, day_start_balance=size,
                  started_at=START.replace(tzinfo=None))
    s.add(acc); s.commit()
    tradebot.start(s, acc, pace=pace, target_pct=cel)
    # Seed z samego loginu — `seed_for()` miesza acc.id, a ten zalezy od tego,
    # ile kont zalozyly wczesniejsze pliki testow. Bez tego trajektoria bota
    # zmienia sie od niepowiazanych testow i progi robia sie loteria.
    acc.bot_seed = int.from_bytes(hashlib.sha256(login.encode()).digest()[:4], "big") & 0x7FFFFFFF
    s.commit()
    aid = acc.id
    s.close()
    return aid


def _drive(account_id: int, ticks: int, *, od: datetime = START,
           do_otwarcia: bool = False, step_sec: int = 600):
    """Przepuszcza bota przez PRAWDZIWY silnik regul — dokladnie jak poller.

    Zwraca `(konto, snapshoty, kolejny_moment)`, zeby drugi przejazd na tym samym
    koncie ruszal tam, gdzie skonczyl pierwszy, a nie odtwarzal te same dni.
    `do_otwarcia` konczy bieg na pierwszym ticku z otwarta pozycja.
    """
    s = SessionLocal()
    acc = s.get(Account, account_id)
    now = od
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
        if res.passed_phase:
            poller._advance_phase(acc, rt)
        s.commit()
        now += timedelta(seconds=step_sec)
        if do_otwarcia and snap.has_open_position:
            break
    s.refresh(acc)
    s.close()
    return acc, snaps, now


def _zamkniete(account_id: int) -> list[Trade]:
    s = SessionLocal()
    out = (s.query(Trade).filter(Trade.account_id == account_id,
                                 Trade.status == "closed").order_by(Trade.id).all())
    s.close()
    return out


# --------------------------------------------------------------------------- #
#  Sufit jest TWARDY                                                           #
# --------------------------------------------------------------------------- #
def test_saldo_nie_przekracza_sufitu_ani_o_cent():
    """Cel fazy to +8%, sufit +7.97% — konto ma stanac 0,03 pp ponizej progu."""
    aid = _konto("out-sufit", cel=7.97)
    acc, snaps, _ = _drive(aid, DLUGI_BIEG)

    sufit = round(100_000.0 * 1.0797, 2)
    assert acc.bot_target_pct == 7.97
    assert acc.balance <= sufit, f"saldo {acc.balance} ponad sufitem {sufit}"
    assert max(s.balance for s in snaps) <= sufit
    # ...i faza sie NIE zmienila, mimo dlugiego biegu i uzbieranych dni
    assert acc.phase == "eval_1" and acc.status == "active"
    assert acc.trading_days_count >= 4, "bieg za krotki, zeby test cos udowodnil"


def test_zadna_transakcja_nie_konczy_sie_zerowym_pnl():
    """Transakcja-widmo (`pnl = 0.00`) nie rusza salda, wiec bot probuje w kolko.

    Powstaje przy suficie: resztka rzedu centow nie przezywa zaokraglenia ceny
    zamkniecia do ticka instrumentu — cena wychodzi rowna cenie otwarcia.
    """
    aid = _konto("out-widmo", cel=7.97)
    _drive(aid, DLUGI_BIEG)
    trades = _zamkniete(aid)
    assert len(trades) > 30, "za malo transakcji, zeby zlapac przypadek brzegowy"
    assert all(abs(t.pnl) >= 0.005 for t in trades), \
        [(t.id, t.symbol, t.volume_lots, t.pnl) for t in trades if abs(t.pnl) < 0.005][:5]


def test_bot_dochodzi_do_sufitu_a_nie_staje_w_polowie():
    """Sufit ma byc celem, nie hamulcem — inaczej „ma zabraknac 0,03 pp" klamie."""
    aid = _konto("out-dobija", cel=7.97)
    acc, _, _ = _drive(aid, DLUGI_BIEG)
    sufit = round(100_000.0 * 1.0797, 2)
    # ostatnie wejscie ladu je NA suficie, z dokladnoscia do progu MIN_FILL
    assert acc.balance >= sufit - tradebot.MIN_FILL


def test_cel_ponad_progiem_fazy_awansuje_konto():
    """Druga strona tego samego pokretla: +8.02% ma fazę zdac."""
    aid = _konto("out-zdaje", cel=8.02)
    acc, _, _ = _drive(aid, DLUGI_BIEG)
    assert acc.phase != "eval_1", "sufit ponad progiem, a konto zostalo w fazie 1"
    assert acc.status in ("active", "funded")


# --------------------------------------------------------------------------- #
#  Zmiana celu w locie                                                         #
# --------------------------------------------------------------------------- #
def test_obnizenie_celu_przy_otwartej_pozycji_nie_rusza_equity():
    """Stop resynchronizuje saldo do feedu i robi uskok. Zmiana celu NIE MOZE."""
    aid = _konto("out-wlocie", cel=20.0)
    _drive(aid, 300, do_otwarcia=True)

    s = SessionLocal()
    acc = s.get(Account, aid)
    otwarta = s.query(Trade).filter(Trade.account_id == aid, Trade.status == "open").count()
    equity_przed = float(acc.equity)
    s.close()
    assert otwarta == 1, "test ma sens tylko z otwarta pozycja"

    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"target_pct": 0.5})
    assert r.status_code == 200

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert abs(float(acc.equity) - equity_przed) < 0.01, \
        f"uskok na krzywej: {equity_przed} -> {acc.equity}"
    assert acc.bot_enabled is True
    s.close()


def test_podniesienie_celu_wznawia_bota_stojacego_na_suficie():
    aid = _konto("out-wznow", cel=1.0)
    acc, _, dalej = _drive(aid, 1500)
    saldo_na_suficie = float(acc.balance)
    assert 101_000.0 - tradebot.MIN_FILL <= saldo_na_suficie <= 101_000.0

    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 3.0})
    acc, _, _ = _drive(aid, 1500, od=dalej)
    assert float(acc.balance) > saldo_na_suficie, "bot nie ruszyl po podniesieniu celu"
    assert float(acc.balance) <= 103_000.0


# --------------------------------------------------------------------------- #
#  Co z tego widzi panel                                                       #
# --------------------------------------------------------------------------- #
def test_outcome_nazywa_brakujace_setne():
    aid = _konto("out-opis", cel=7.97)
    o = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"target_pct": 7.97}).json()["bot_outcome"]
    assert o["cap_pct"] == 7.97
    assert o["cap_equity"] == 107_970.0
    assert o["phase_target_pct"] == 8.0
    assert o["phase_target_equity"] == 108_000.0
    assert o["gap_pp"] == -0.03
    assert o["will_pass"] is False
    assert o["cap_overshot"] is False
    assert o["mode"] == "profit"


def test_outcome_liczy_brakujace_dni_handlu():
    """Sufit ponad progiem, a konto i tak nie zda — bo faza wymaga 4 dni handlu.

    Bez tego pola panel milczy, a konto „nie zdaje bez powodu".
    """
    aid = _konto("out-dni", cel=9.0, dni=5)
    o = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"target_pct": 9.0}).json()["bot_outcome"]
    assert o["will_pass"] is True
    assert o["min_trading_days"] == 5
    assert o["trading_days"] == 0
    assert o["days_missing"] == 5


def test_outcome_przyznaje_sie_gdy_saldo_jest_juz_ponad_sufitem():
    """Cofnac sie nie da — panel ma to powiedziec wprost, zamiast udawac."""
    aid = _konto("out-przestrzal", cel=0.0)
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.balance = acc.equity = 106_000.0
    s.commit(); s.close()

    o = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"target_pct": 3.0}).json()["bot_outcome"]
    assert o["cap_equity"] == 103_000.0
    assert o["cap_overshot"] is True


def test_bez_sufitu_outcome_nie_wrozy():
    aid = _konto("out-bezsufitu", cel=0.0)
    o = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"target_pct": 0}).json()["bot_outcome"]
    assert o["cap_equity"] is None
    assert o["will_pass"] is None and o["gap_pp"] is None


def test_outcome_nie_wycieka_do_portalu_klienta():
    """Pola bota sa celowo odciete od widoku klienta — nowy blok tez tam nie moze."""
    from app.main import _account_dict
    s = SessionLocal()
    acc = s.get(Account, _konto("out-portal", cel=7.97))
    klient = _account_dict(acc, admin_view=False)
    admin = _account_dict(acc, admin_view=True)
    s.close()
    assert "bot_outcome" not in klient and "bot_target_pct" not in klient
    assert "bot_outcome" in admin
