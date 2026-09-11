"""Ranking dyscypliny — `_discipline_rows` + `/api/discipline`.

Ta lista ma mówić co innego niż ranking zysku: nie „kto zarobił najwięcej", tylko
„kto nie łamał reguł". Dlatego pilnujemy, żeby konto z jednym wielkim strzałem
przegrywało z kontem nudnym, żeby krótka seria nie wchodziła na listę w ogóle
i żeby konta z MT5 (bez wierszy w `trades`) liczyły się ze snapshotów.
"""
import os
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.NamedTemporaryFile(
    suffix=".db", delete=False).name)
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import (DISCIPLINE_MIN_DAYS, _DISCIPLINE_CACHE,  # noqa: E402
                      _discipline_rows, app)
from app.models import Account, EquitySnapshot, Trade, Trader  # noqa: E402

init_db()
client = TestClient(app)

START = datetime(2026, 6, 1, 12, 0)


def _konto(nazwa: str, *, status: str = "active", saldo: float = 25_000.0,
           peak: float | None = None) -> int:
    """Konto z własnym traderem; `nazwa` jest jednoczłonowa, więc przechodzi
    przez maskowanie nietknięta i da się po niej znaleźć wiersz rankingu."""
    s = SessionLocal()
    tr = Trader(email=f"{uuid4().hex[:12]}@example.com",
                password_hash=auth.hash_password("haslo1234"),
                full_name=nazwa, referral_code=uuid4().hex[:10].upper())
    s.add(tr); s.commit()
    acc = Account(login=uuid4().hex[:10], trader_id=tr.id, trader_name=nazwa,
                  platform_login=uuid4().hex[:10], platform_password="x",
                  platform_server="MetaQuotes-Demo", product_key="2step-25k",
                  initial_balance=25_000.0, balance=saldo, equity=saldo,
                  peak_equity=peak if peak is not None else max(saldo, 25_000.0),
                  day_start_equity=saldo, day_start_balance=saldo, steps=2,
                  profit_target_p1=8.0, profit_target_p2=5.0,
                  max_daily_loss_pct=5.0, max_overall_loss_pct=10.0,
                  min_trading_days=4, status=status, phase="eval_1")
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _dni(aid: int, pnle: list[float], *, od: int = 0) -> None:
    """Po jednej zamkniętej transakcji na każdy kolejny dzień, licząc od `od`."""
    s = SessionLocal()
    for i, pnl in enumerate(pnle):
        kiedy = START + timedelta(days=od + i)
        s.add(Trade(account_id=aid, symbol="EURUSD", side="buy", lots=1.0,
                    open_price=1.1, close_price=1.1, pnl=pnl, status="closed",
                    opened_at=kiedy, closed_at=kiedy))
    s.commit(); s.close()


def _wiersz(nazwa: str) -> dict | None:
    return next((r for r in _discipline_rows() if r["trader"] == nazwa), None)


def _bez_cache() -> None:
    """Endpointy trzymają listę 60 s — w teście dane zmieniają się co chwilę."""
    _DISCIPLINE_CACHE.update(ts=0.0, data=None)


def test_krotka_seria_nie_wchodzi_na_liste():
    # Dziewięć spokojnych dni to jeszcze nie dyscyplina — inaczej świeże konto
    # z trzema dniami bez straty siadałoby na szczycie listy.
    aid = _konto("Krotki")
    _dni(aid, [10.0] * (DISCIPLINE_MIN_DAYS - 1))
    assert _wiersz("Krotki") is None
    _dni(aid, [10.0] * 3, od=DISCIPLINE_MIN_DAYS - 1)
    assert _wiersz("Krotki") is not None


def test_nudne_konto_bije_konto_jednego_strzalu():
    # Ten sam zysk i ta sama liczba dni. Różnica: jedno rozłożyło wynik po dniach,
    # drugie wisi na jednym zagraniu. Ranking zysku nie widzi tu żadnej różnicy.
    rowne = _konto("Rowny", saldo=26_200.0)
    _dni(rowne, [100.0] * 12)
    strzal = _konto("Strzal", saldo=26_200.0)
    _dni(strzal, [1_190.0] + [1.0] * 11)

    a, b = _wiersz("Rowny"), _wiersz("Strzal")
    assert a and b
    assert a["consist_pts"] > b["consist_pts"]
    assert a["score"] > b["score"]
    # Udział najlepszego dnia to sedno różnicy i musi być widoczny na ekranie.
    assert a["top_day_share"] < 20 and b["top_day_share"] > 90


def test_dzien_zjadajacy_polowe_budzetu_nie_jest_czysty():
    # Dzienny limit to 5% z 25k = $1250. Strata $700 mieści się w regule, ale
    # zjada ponad połowę budżetu — to przetrwanie, nie dyscyplina.
    aid = _konto("Ostry")
    _dni(aid, [-700.0] * 4 + [50.0] * 8)
    r = _wiersz("Ostry")
    assert r and r["trading_days"] == 12 and r["clean_days"] == 8
    # Cztery dni po zeru i osiem po komplecie: 40 × 8/12 = 26,7.
    assert r["clean_pts"] == 27


def test_punkty_za_dni_to_udzial_a_nie_liczba_dni():
    # Bez tego 40 punktów mierzy staż: konto bez jednego potknięcia przez 12 dni
    # stało niżej niż niechlujne konto, które po prostu handluje dłużej.
    krotkie = _konto("Bezbledny")
    _dni(krotkie, [30.0] * 12)
    dlugie = _konto("Dlugi")
    _dni(dlugie, ([-700.0] + [30.0] * 3) * 10)   # 40 dni, co czwarty ostry

    a, b = _wiersz("Bezbledny"), _wiersz("Dlugi")
    assert a and b
    assert a["clean_pts"] == 40 and a["trading_days"] == 12
    assert b["trading_days"] == 40 and b["clean_pts"] < a["clean_pts"]


def test_plytka_strata_liczy_sie_lepiej_niz_gleboka():
    # Oba konta mają komplet dni „czystych" w sensie progu połowy budżetu, więc
    # przy ocenie zero-jedynkowej wyglądałyby identycznie. −$450 to 36% budżetu,
    # −$50 to 4% — to nie jest to samo zachowanie.
    plytkie = _konto("Plytki")
    _dni(plytkie, [-50.0] * 3 + [80.0] * 9)
    glebokie = _konto("Gleboki")
    _dni(glebokie, [-450.0] * 3 + [80.0] * 9)

    a, b = _wiersz("Plytki"), _wiersz("Gleboki")
    assert a and b
    assert a["clean_days"] == b["clean_days"] == 12
    assert a["clean_pts"] > b["clean_pts"]


def test_zjedzony_drawdown_zabiera_punkty_zapasu():
    # Dwa konta, ta sama historia dni; jedno stoi $1875 pod kreską, czyli zużyło
    # 75% z 10-procentowego limitu straty.
    pelne = _konto("Pelny")
    _dni(pelne, [20.0] * 12)
    zjedzone = _konto("Zjedzony", saldo=23_125.0)
    _dni(zjedzone, [20.0] * 12)

    a, b = _wiersz("Pelny"), _wiersz("Zjedzony")
    assert a and b
    assert a["buffer_pts"] == 30 and a["buffer_pct"] == 100
    assert b["buffer_pct"] == 25 and b["buffer_pts"] == 8
    assert a["score"] > b["score"]


def test_konto_oblane_nie_stoi_w_rankingu_dyscypliny():
    aid = _konto("Oblany", status="failed")
    _dni(aid, [10.0] * 12)
    assert _wiersz("Oblany") is None


def test_konto_z_mt5_liczy_sie_ze_snapshotow():
    # Feed MT5 oddaje samo equity — takie konto nie ma ANI JEDNEGO wiersza
    # w `trades` i bez zapasu ze snapshotów wypadłoby z rankingu po cichu.
    aid = _konto("Empetka", saldo=25_600.0)
    s = SessionLocal()
    saldo = 25_000.0
    for i in range(12):
        dzien = (START + timedelta(days=i)).strftime("%Y-%m-%d")
        s.add(EquitySnapshot(account_id=aid, ts=START + timedelta(days=i),
                             balance=saldo, equity=saldo, day_key=dzien))
        saldo += 50.0
        s.add(EquitySnapshot(account_id=aid, ts=START + timedelta(days=i, hours=8),
                             balance=saldo, equity=saldo, day_key=dzien))
    s.commit(); s.close()

    r = _wiersz("Empetka")
    assert r and r["trading_days"] == 12 and r["clean_days"] == 12


def test_punkty_sumuja_sie_do_wyniku_i_nie_wychodza_poza_skale():
    _dni(_konto("Skala"), [40.0] * 14)
    for r in _discipline_rows():
        assert r["score"] == r["clean_pts"] + r["buffer_pts"] + r["consist_pts"]
        assert 0 <= r["score"] <= 100
        assert 0 <= r["clean_pts"] <= 40
        assert 0 <= r["buffer_pts"] <= 30
        assert 0 <= r["consist_pts"] <= 30


def test_wlasne_miejsce_zna_serwer_a_nie_ekran():
    # Lista jest maskowana i ucięta do dziesiątki, więc z niej samej nie da się
    # wyczytać, czy trader w ogóle się zakwalifikował.
    s = SessionLocal()
    tr = Trader(email=f"{uuid4().hex[:12]}@example.com",
                password_hash=auth.hash_password("haslo1234"),
                full_name="Swoj Czlowiek", referral_code=uuid4().hex[:10].upper())
    s.add(tr); s.commit(); tid = tr.id; s.close()
    naglowek = {"Authorization": f"Bearer {auth.make_token(tid)}"}

    _bez_cache()
    r = client.get("/api/me/discipline", headers=naglowek)
    assert r.status_code == 200 and r.json() == {"ranked": False, "min_days": DISCIPLINE_MIN_DAYS}

    s = SessionLocal()
    acc = Account(login=uuid4().hex[:10], trader_id=tid, trader_name="Swoj Czlowiek",
                  platform_login=uuid4().hex[:10], platform_password="x",
                  platform_server="MetaQuotes-Demo", product_key="2step-25k",
                  initial_balance=25_000.0, balance=25_360.0, equity=25_360.0,
                  peak_equity=25_360.0, day_start_equity=25_360.0,
                  day_start_balance=25_360.0, steps=2, profit_target_p1=8.0,
                  profit_target_p2=5.0, max_daily_loss_pct=5.0,
                  max_overall_loss_pct=10.0, min_trading_days=4,
                  status="active", phase="eval_1")
    s.add(acc); s.commit(); aid = acc.id; s.close()
    _dni(aid, [30.0] * 12)

    _bez_cache()
    d = client.get("/api/me/discipline", headers=naglowek).json()
    assert d["ranked"] is True and d["trading_days"] == 12
    assert d["rank"] >= 1 and d["rank"] <= d["of"]


def test_endpoint_maskuje_nazwiska_i_nie_oddaje_identyfikatorow():
    _dni(_konto("Anna Kowalska Nowak"), [30.0] * 12)
    assert _wiersz("Anna N.") is not None

    _bez_cache()
    r = client.get("/api/discipline")
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d) <= 10
    # Login konta to numer rachunku na platformie, a `account_id`/`trader_id`
    # zestawione z rankingiem zysku rozmaskowałyby, kto jest kim.
    for x in d:
        for zakazane in ("login", "email", "account_id", "trader_id"):
            assert zakazane not in x
