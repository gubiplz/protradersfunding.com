"""Zrzut track recordu kont prowadzonych przez Trade BOT.

Statystyki liczone są tu na wartościach dobranych tak, żeby wynik dało się
sprawdzić w pamięci — inaczej test potwierdzałby wyłącznie, że kod się wykonał.
"""
import io
import os
import tempfile
import zipfile
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import trackexport  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trade  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
START = datetime(2026, 3, 2, 9, 0, 0)

# Sześć transakcji: 4 zyskowne (+300 razem), 2 stratne (-100 razem).
# Stąd: win rate 66,67%, profit factor 3.0, net +200, seria wygranych 2.
WYNIKI = [100.0, -40.0, 50.0, 100.0, -60.0, 50.0]


def _konto(session, *, bot=True, login="900001") -> Account:
    acc = Account(login=login, trader_name="Jan Testowy", initial_balance=100_000.0,
                  balance=100_200.0, equity=100_200.0, peak_equity=100_300.0,
                  day_start_equity=100_000.0, phase="eval_1", status="active",
                  bot_enabled=bot, bot_style="balanced", bot_pace="steady")
    session.add(acc)
    session.flush()
    for i, pnl in enumerate(WYNIKI):
        otw = START + timedelta(days=i, hours=1)
        session.add(Trade(account_id=acc.id, symbol="XAUUSD",
                          side="buy" if i % 2 == 0 else "sell", lots=1.0,
                          open_price=2000.0, close_price=2001.0, pnl=pnl,
                          opened_at=otw, closed_at=otw + timedelta(minutes=30),
                          status="closed", source="bot"))
    # Jedna pozycja otwarta — nie może wejść do statystyk.
    session.add(Trade(account_id=acc.id, symbol="EURUSD", side="buy", lots=2.0,
                      open_price=1.1, close_price=None, pnl=999.0,
                      opened_at=START + timedelta(days=9), closed_at=None,
                      status="open", source="bot"))
    session.commit()
    return acc


@pytest.fixture()
def konto():
    s = SessionLocal()
    try:
        acc = _konto(s, login=f"9{datetime.now().microsecond:06d}")
        yield acc.id
    finally:
        s.close()


def _zrzut(account_id):
    s = SessionLocal()
    try:
        return trackexport.zbierz(s, account_id=account_id)
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Statystyki                                                                  #
# --------------------------------------------------------------------------- #
def test_statystyki_licza_sie_z_zamknietych(konto):
    p = _zrzut(konto)["accounts"][0]["summary"]
    assert p["trades_total"] == 7 and p["trades_open"] == 1
    assert p["trades_closed"] == 6
    assert p["wins"] == 4 and p["losses"] == 2
    assert p["win_rate_pct"] == 66.67
    assert p["gross_profit"] == 300.0 and p["gross_loss"] == -100.0
    assert p["net_pnl"] == 200.0
    # Otwarta pozycja niesie pnl 999 — gdyby weszła do sumy, byłoby 1199.
    assert p["net_pnl"] != 1199.0


def test_profit_factor_i_pochodne(konto):
    p = _zrzut(konto)["accounts"][0]["summary"]
    assert p["profit_factor"] == 3.0            # 300 / 100
    assert p["expectancy"] == 33.33             # 200 / 6
    assert p["avg_win"] == 75.0 and p["avg_loss"] == -50.0
    assert p["payoff_ratio"] == 1.5
    assert p["largest_win"] == 100.0 and p["largest_loss"] == -60.0


def test_serie_i_obsuniecie(konto):
    p = _zrzut(konto)["accounts"][0]["summary"]
    # +100, -40, +50, +100, -60, +50 → najdłuższa seria wygranych to 2, strat 1.
    assert p["max_consecutive_wins"] == 2
    assert p["max_consecutive_losses"] == 1
    # Krzywa: 100100 → 100060 → ... szczyt 100250 przed stratą 60.
    assert p["max_drawdown_usd"] == 60.0
    assert p["green_days"] == 4 and p["red_days"] == 2


def test_profit_factor_pusty_bez_strat():
    """Brak stratnej transakcji to BRAK DANYCH do tej miary, nie wynik idealny."""
    stat = trackexport._statystyki(
        [Trade(pnl=10.0, status="closed", lots=1.0,
               opened_at=START, closed_at=START + timedelta(minutes=5))], 100_000.0)
    assert stat["profit_factor"] is None
    assert stat["avg_loss"] is None
    assert stat["win_rate_pct"] == 100.0


def test_konto_bez_transakcji_nie_wywraca():
    assert trackexport._statystyki([], 100_000.0)["trades_closed"] == 0


# --------------------------------------------------------------------------- #
#  Dobór kont                                                                  #
# --------------------------------------------------------------------------- #
def test_bierze_tylko_konta_z_botem():
    s = SessionLocal()
    try:
        bez = _konto(s, bot=False, login=f"8{datetime.now().microsecond:06d}")
        dane = trackexport.zbierz(s)
    finally:
        s.close()
    assert dane["accounts"], "zrzut nie może być pusty"
    assert all(k["summary"]["bot_enabled"] == 1 for k in dane["accounts"])
    assert bez.id not in [k["summary"]["account_id"] for k in dane["accounts"]]


# --------------------------------------------------------------------------- #
#  Formaty i endpoint                                                          #
# --------------------------------------------------------------------------- #
def test_zip_ma_komplet_plikow_i_slownik(konto):
    r = client.get(f"/api/admin/export/track-record?account_id={konto}", headers=ADMIN)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        nazwy = set(z.namelist())
        assert {"CZYTAJ-TO-NAJPIERW.md", "accounts.csv", "trades.csv", "daily.csv",
                "monthly.csv", "payouts.csv", "breaches.csv", "certificates.csv",
                "track-record.json"} <= nazwy
        opis = z.read("CZYTAJ-TO-NAJPIERW.md").decode("utf-8")
        # Opis ma tłumaczyć KAŻDE pole z tabeli kont — inaczej model zgaduje.
        assert "`profit_factor`" in opis and "`max_drawdown_pct`" in opis
        konta_csv = z.read("accounts.csv").decode("utf-8-sig")
        assert "win_rate_pct" in konta_csv.splitlines()[0]


def test_csv_ma_bom_dla_excela(konto):
    r = client.get(f"/api/admin/export/track-record?format=csv&account_id={konto}",
                   headers=ADMIN)
    assert r.status_code == 200
    assert r.content.startswith(b"\xef\xbb\xbf")


def test_json_niesie_slownik(konto):
    r = client.get(f"/api/admin/export/track-record?format=json&account_id={konto}",
                   headers=ADMIN)
    dane = r.json()
    assert dane["accounts_count"] == 1
    assert dane["dictionary"]["profit_factor"]["unit"] == "—"
    assert "Trade BOT" in dane["criterion"]
    # Każde pole tabeli kont musi mieć wpis w słowniku.
    braki = [k for k in dane["accounts"][0]["summary"]
             if k not in dane["dictionary"] and k not in ("status", "trades_truncated")]
    assert not braki, f"pola bez opisu: {braki}"


def test_zly_format_odrzucony(konto):
    assert client.get("/api/admin/export/track-record?format=pdf",
                      headers=ADMIN).status_code == 400


def test_nieznane_konto_to_404():
    assert client.get("/api/admin/export/track-record?account_id=999999",
                      headers=ADMIN).status_code == 404


def test_eksport_wymaga_admina():
    assert client.get("/api/admin/export/track-record").status_code == 403
