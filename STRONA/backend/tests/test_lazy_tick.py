"""Leniwy tick — silnik dogania się przy wejściu na dashboard.

Na hostingu bezserwerowym nie ma procesu, w którym kręciłby się poller, a cron
Vercela na koncie Hobby chodzi raz na dobę. Bez tego konta stoją w miejscu i
dashboard jest stop-klatką. `LAZY_TICK_SEC` mówi, jak stary może być najświeższy
odczyt, zanim wejście na listę kont samo wykona jeden przebieg.

Test pilnuje obu stron kontraktu: że stare dane wyzwalają przebieg i że świeże
NIE wyzwalają (inaczej każdy odczyt portalu ciągnąłby silnik i portal zwolniłby
do tempa ticku).
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
# LAZY_TICK_SEC celowo NIE przez srodowisko: `get_settings()` jest cache'owane,
# wiec przy pelnym przebiegu obiekt ustawien powstaje w innym module wczesniej.
# Kazdy test podmienia pole przez monkeypatch — przy okazji nie zaraza pozostalych
# plikow testowych tickiem, ktory ruszalby saldami ich kont.

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, main  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, EquitySnapshot, Trader  # noqa: E402

init_db()
client = TestClient(app)


def _trader_z_kontem(email: str) -> str:
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == email).first()
    if tr is None:
        tr = Trader(email=email, password_hash=auth.hash_password("tajne123"),
                    full_name="Lazy Tester", referral_code=email[:8].upper())
        s.add(tr); s.commit()
    if not s.query(Account).filter(Account.trader_id == tr.id).first():
        s.add(Account(login=f"lazy-{tr.id}", trader_name="Lazy Tester", trader_id=tr.id,
                      product_key="2step-50k", initial_balance=50_000.0, steps=2,
                      balance=50_000.0, equity=50_000.0, peak_equity=50_000.0,
                      day_start_equity=50_000.0, day_start_balance=50_000.0,
                      status="active", phase="eval_1"))
        s.commit()
    token = auth.make_token(tr.id)
    s.close()
    return token


def _snapshotow() -> int:
    s = SessionLocal()
    n = s.query(EquitySnapshot).count()
    s.close()
    return n


def _postarz_snapshoty(godzin: int = 2) -> None:
    s = SessionLocal()
    s.query(EquitySnapshot).update(
        {EquitySnapshot.ts: datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=godzin)})
    s.commit(); s.close()


def test_stare_dane_wyzwalaja_przebieg_silnika(monkeypatch):
    token = _trader_z_kontem("lazy1@firma.pl")
    _postarz_snapshoty()
    monkeypatch.setattr(main.settings, "lazy_tick_sec", 30)
    przed = _snapshotow()

    r = client.get("/api/me/accounts", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    # najswiezszy odczyt byl starszy niz okno -> silnik wykonal przebieg
    assert _snapshotow() > przed


def test_swieze_dane_nie_ciagna_silnika(monkeypatch):
    """Inaczej KAZDY odczyt portalu ciagnalby silnik i portal zwolnilby do tempa ticku."""
    token = _trader_z_kontem("lazy2@firma.pl")
    monkeypatch.setattr(main.settings, "lazy_tick_sec", 30)
    s = SessionLocal()
    acc = s.query(Account).filter(Account.status == "active").first()
    s.add(EquitySnapshot(account_id=acc.id, ts=datetime.now(timezone.utc).replace(tzinfo=None),
                         balance=acc.balance, equity=acc.equity))
    s.commit(); s.close()
    przed = _snapshotow()

    client.get("/api/me/accounts", headers={"Authorization": f"Bearer {token}"})

    assert _snapshotow() == przed


def test_wylaczony_lazy_tick_nic_nie_robi(monkeypatch):
    """LAZY_TICK_SEC=0 (domyslnie, lokalnie) — od odczytu nic sie nie dzieje."""
    token = _trader_z_kontem("lazy3@firma.pl")
    _postarz_snapshoty()
    monkeypatch.setattr(main.settings, "lazy_tick_sec", 0)
    przed = _snapshotow()

    client.get("/api/me/accounts", headers={"Authorization": f"Bearer {token}"})

    assert _snapshotow() == przed
