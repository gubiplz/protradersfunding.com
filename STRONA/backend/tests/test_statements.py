"""Wyciąg z kont do Excela — jeden plik, wszystkie prowadzone rachunki.

Testy pilnują trzech rzeczy, których po otwarciu pliku w Excelu nie widać na
pierwszy rzut oka: że darmowe rejestracje NIE wyciekają do eksportu, że liczby
są liczbami (a nie tekstem, który AI przeczyta jako 1.234), i że statystyki
liczą się z pozycji ZAMKNIĘTYCH — bo pozycja otwarta ma wynik pływający.
"""
import io
import os
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL",
                      f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import statements  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Account, EquitySnapshot, Order, Trade, Trader)  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

START = datetime(2026, 9, 1, 9, 0)


def _trejd(acc_id, pnl, *, minuta=0, symbol="EURUSD", status="closed"):
    otw = START + timedelta(minutes=minuta)
    return Trade(account_id=acc_id, symbol=symbol, side="buy", lots=1.0,
                 open_price=1.1, close_price=1.2, pnl=pnl, opened_at=otw,
                 closed_at=(otw + timedelta(minutes=30)) if status == "closed" else None,
                 status=status, source="bot")


@pytest.fixture
def swiat():
    """Dwa rachunki: jeden płacącego klienta, jeden z darmowej rejestracji.

    Fixture NIE czyści globalnych tabel: cały pakiet dzieli jedną bazę, więc
    `query(Account).delete()` zabierało dane innym testom i wywracało klucze
    obce. Sprząta wyłącznie to, co samo założyło.
    """
    s = SessionLocal()
    stworzone = {"konta": [], "traderzy": []}
    try:
        placacy = Trader(email="wyciag-placacy@example.com", password_hash="x")
        darmowy = Trader(email="wyciag-darmowy@example.com", password_hash="x")
        s.add_all([placacy, darmowy])
        s.flush()
        stworzone["traderzy"] = [placacy.id, darmowy.id]

        platne = Account(login="wyciag-100001", trader_name="Paying Client",
                         trader_id=placacy.id, initial_balance=100000.0,
                         balance=103000.0, equity=103000.0, phase="eval_1",
                         status="active", created_at=START)
        darmo = Account(login="wyciag-900001", trader_name="Free Signup",
                        trader_id=darmowy.id, initial_balance=100000.0,
                        balance=100000.0, equity=100000.0, phase="eval_1",
                        status="active", created_at=START)
        s.add_all([platne, darmo])
        s.flush()
        stworzone["konta"] = [platne.id, darmo.id]

        s.add(Order(trader_id=placacy.id, account_id=platne.id,
                    product_key="2step-100k", amount_usd=499.0, status="paid",
                    paid_at=START))
        s.add_all([
            _trejd(platne.id, 400.0, minuta=0),
            _trejd(platne.id, -150.0, minuta=60),
            _trejd(platne.id, 250.0, minuta=120),
            _trejd(platne.id, 999.0, minuta=180, status="open"),
            _trejd(darmo.id, 50.0, minuta=0),
        ])
        s.add_all([
            EquitySnapshot(account_id=platne.id, ts=START, balance=100000.0,
                           equity=100000.0, day_key="2026-09-01"),
            EquitySnapshot(account_id=platne.id, ts=START + timedelta(hours=1),
                           balance=100400.0, equity=100400.0, day_key="2026-09-01"),
            EquitySnapshot(account_id=platne.id, ts=START + timedelta(hours=2),
                           balance=100250.0, equity=100250.0, day_key="2026-09-01"),
        ])
        s.commit()
        yield {"platne": platne.id, "darmo": darmo.id,
               "login_platne": "wyciag-100001", "login_darmo": "wyciag-900001",
               "stworzone": stworzone}
    finally:
        try:
            ids = [a.id for a in s.query(Account)
                   .filter(Account.login.like("wyciag-%")).all()]
            if ids:
                s.query(Trade).filter(Trade.account_id.in_(ids)).delete(
                    synchronize_session=False)
                s.query(EquitySnapshot).filter(
                    EquitySnapshot.account_id.in_(ids)).delete(
                    synchronize_session=False)
                s.query(Order).filter(Order.account_id.in_(ids)).delete(
                    synchronize_session=False)
                s.query(Account).filter(Account.id.in_(ids)).delete(
                    synchronize_session=False)
            if stworzone["traderzy"]:
                s.query(Trader).filter(
                    Trader.id.in_(stworzone["traderzy"])).delete(
                    synchronize_session=False)
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()


def _konta(s):
    from app.main import _konto_nie_import, _placacy_id
    return statements.konta_do_wyciagu(s, placacy_id=_placacy_id(s),
                                       filtr_nie_import=_konto_nie_import(s))


# --------------------------------------------------------------------------- #
#  Kogo obejmuje wyciąg                                                        #
# --------------------------------------------------------------------------- #
def test_darmowa_rejestracja_nie_trafia_do_wyciagu(swiat):
    """Nie prowadzimy tych rachunków tak jak płatnych, więc ich historia
    zafałszowałaby obraz usługi."""
    s = SessionLocal()
    try:
        loginy = [a.login for a in _konta(s)]
        assert swiat["login_platne"] in loginy
        assert swiat["login_darmo"] not in loginy
    finally:
        s.close()


def test_rachunek_bez_wlasciciela_nie_trafia_do_wyciagu(swiat):
    """Pula to magazyn, nie człowiek — wiersz o rachunku bez tradera niczego
    nie opowiada, a w panelu widać ją tylko po to, by nie zgubić kolejki."""
    s = SessionLocal()
    try:
        s.add(Account(login="wyciag-700001", trader_name="", trader_id=None,
                      initial_balance=50000.0, balance=50000.0, equity=50000.0,
                      phase="eval_1", status="active", created_at=START))
        s.commit()
        assert "wyciag-700001" not in [a.login for a in _konta(s)]
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Liczenie                                                                    #
# --------------------------------------------------------------------------- #
def test_statystyki_pomijaja_pozycje_otwarta(swiat):
    """Pozycja otwarta ma wynik PŁYWAJĄCY — wliczona do win rate policzyłaby
    jako wygraną coś, co jeszcze nie wygrało."""
    s = SessionLocal()
    try:
        trejdy = s.query(Trade).filter(Trade.account_id == swiat["platne"]).all()
        st = statements.statystyki_trejdow(trejdy)
        assert st["trades_total"] == 4 and st["trades_closed"] == 3
        assert st["trades_open"] == 1
        assert st["wins"] == 2 and st["losses"] == 1
        assert st["net_pnl"] == 500.0          # 400 - 150 + 250, BEZ 999
        assert st["win_rate_pct"] == pytest.approx(66.67, abs=0.01)
        assert st["profit_factor"] == pytest.approx(4.33, abs=0.01)
    finally:
        s.close()


def test_brak_strat_daje_pusty_profit_factor():
    """Dzielenie przez zero to nie „nieskończona skuteczność", tylko brak
    mianownika — a `inf` w arkuszu psuje każde dalsze liczenie."""
    st = statements.statystyki_trejdow([_trejd(1, 100.0), _trejd(1, 50.0)])
    assert st["profit_factor"] is None
    assert st["avg_loss"] is None


def test_obsuniecie_liczone_od_szczytu(swiat):
    s = SessionLocal()
    try:
        krzywa = (s.query(EquitySnapshot)
                  .filter(EquitySnapshot.account_id == swiat["platne"]).all())
        dd = statements.obsuniecie(krzywa)
        assert dd["peak_equity"] == 100400.0
        assert dd["max_drawdown"] == 150.0
        assert dd["max_drawdown_pct"] == pytest.approx(0.15, abs=0.01)
    finally:
        s.close()


def test_dzien_bierze_sie_z_day_key_nie_z_kalendarza(swiat):
    """Doba handlowa zaczyna się o godzinie serwera; liczona kalendarzowo
    dałaby wynik dnia niezgodny z tym, na czym pękają limity."""
    s = SessionLocal()
    try:
        trejdy = s.query(Trade).filter(Trade.account_id == swiat["platne"]).all()
        krzywa = (s.query(EquitySnapshot)
                  .filter(EquitySnapshot.account_id == swiat["platne"]).all())
        dni = statements.dni_handlowe(trejdy, krzywa)
        assert [d["day"] for d in dni] == ["2026-09-01"]
        assert dni[0]["trades"] == 3          # otwarta nie ma jeszcze swojego dnia
        assert dni[0]["net_pnl"] == 500.0
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Plik                                                                        #
# --------------------------------------------------------------------------- #
def test_skoroszyt_ma_wszystkie_arkusze(swiat):
    openpyxl = pytest.importorskip("openpyxl")
    s = SessionLocal()
    try:
        dane = statements.zbuduj(s, _konta(s))
    finally:
        s.close()
    wb = openpyxl.load_workbook(io.BytesIO(dane))
    assert wb.sheetnames == list(statements.ARKUSZE)
    assert wb["Dictionary"]["A1"].value == "sheet"


def test_kwoty_sa_liczbami_a_nie_tekstem(swiat):
    """AI, które dostanie „$1,234.00", musi to parsować i potrafi przeczytać
    jako 1.234. W arkuszu ma być liczba, a formatowanie osobno."""
    openpyxl = pytest.importorskip("openpyxl")
    s = SessionLocal()
    try:
        dane = statements.zbuduj(s, _konta(s))
    finally:
        s.close()
    ws = openpyxl.load_workbook(io.BytesIO(dane))["Trades"]
    naglowki = [c.value for c in ws[1]]
    kol_pnl = naglowki.index("pnl") + 1
    wartosci = [ws.cell(row=r, column=kol_pnl).value for r in range(2, ws.max_row + 1)]
    assert all(isinstance(v, (int, float)) for v in wartosci), wartosci
    assert ws.cell(row=2, column=kol_pnl).number_format == statements.PIENIADZ


def test_kazdy_arkusz_niesie_login_konta(swiat):
    """Konto jest KOLUMNĄ, nie zakładką — inaczej policzenie czegokolwiek na
    przekroju wymaga najpierw sklejenia arkuszy."""
    openpyxl = pytest.importorskip("openpyxl")
    s = SessionLocal()
    try:
        dane = statements.zbuduj(s, _konta(s))
    finally:
        s.close()
    wb = openpyxl.load_workbook(io.BytesIO(dane))
    for nazwa in wb.sheetnames:
        if nazwa == "Dictionary":
            continue
        assert [c.value for c in wb[nazwa][1]][0] == "account_login", nazwa


def test_trejdy_darmowego_konta_nie_wyciekaja_do_pliku(swiat):
    openpyxl = pytest.importorskip("openpyxl")
    s = SessionLocal()
    try:
        dane = statements.zbuduj(s, _konta(s))
    finally:
        s.close()
    ws = openpyxl.load_workbook(io.BytesIO(dane))["Trades"]
    loginy = {ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)}
    assert swiat["login_darmo"] not in loginy


# --------------------------------------------------------------------------- #
#  Endpoint                                                                    #
# --------------------------------------------------------------------------- #
def test_endpoint_wymaga_admina():
    assert client.get("/api/admin/statements.xlsx").status_code == 403


def test_endpoint_oddaje_plik_xlsx(swiat):
    pytest.importorskip("openpyxl")
    r = client.get("/api/admin/statements.xlsx", headers=ADMIN)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert ".xlsx" in r.headers["content-disposition"]
    # Każdy plik xlsx jest archiwum ZIP — sygnatura to najtańsze sprawdzenie,
    # że nie odesłaliśmy strony błędu z nagłówkiem Excela.
    assert r.content[:2] == b"PK"
    assert int(r.headers["x-accounts-exported"]) >= 1
