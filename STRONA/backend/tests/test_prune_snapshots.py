"""Retencja EquitySnapshot: stare dni chudną do jednego wpisu, świeże zostają.

Tabela snapshotów rośnie z każdym tickiem bez końca — po miesiącach to główny
balast bazy. Pruning ma zostawić ostatni snapshot na (konto, dzień) dla dni
starszych niż okno, nie ruszać świeżych i dać się bezpiecznie przerwać
(commit per partia — Vercel może uciąć crona po 60 s).
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Account, EquitySnapshot, Trader  # noqa: E402
from app.poller import prune_equity_snapshots  # noqa: E402

init_db()


def _konto(session, login: str) -> Account:
    trader = Trader(email=f"{login}@prune.test", full_name="Prune Test",
                    password_hash="x")
    session.add(trader)
    session.flush()
    acc = Account(trader_id=trader.id, login=login, preset="instant-10k",
                  product_key="instant-10k", initial_balance=10000.0,
                  balance=10000.0, equity=10000.0, peak_equity=10000.0,
                  day_start_equity=10000.0, day_start_balance=10000.0,
                  phase="funded", status="funded")
    session.add(acc)
    session.flush()
    return acc


def _snap(session, acc: Account, dni_temu: int, godzina: int) -> EquitySnapshot:
    ts = (datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
          - timedelta(days=dni_temu))
    ts = ts.replace(hour=godzina)
    s = EquitySnapshot(account_id=acc.id, ts=ts, balance=10000.0,
                       equity=10000.0 + godzina, day_key=ts.strftime("%Y-%m-%d"))
    session.add(s)
    session.flush()
    return s


def test_stare_dni_chudna_do_ostatniego_snapshotu():
    session = SessionLocal()
    try:
        acc = _konto(session, "PRUNE-1")
        # dzień -40: trzy snapshoty, zostać ma ostatni (godz. 15)
        for h in (9, 12, 15):
            _snap(session, acc, 40, h)
        # dzień -35: dwa snapshoty
        for h in (10, 14):
            _snap(session, acc, 35, h)
        # dzień -3 (świeży): trzy snapshoty — nie wolno ruszać
        for h in (9, 12, 15):
            _snap(session, acc, 3, h)
        session.commit()

        usuniete = prune_equity_snapshots(session)
        assert usuniete == 3  # 2 z dnia -40 + 1 z dnia -35

        zostale = (session.query(EquitySnapshot)
                   .filter(EquitySnapshot.account_id == acc.id)
                   .order_by(EquitySnapshot.ts).all())
        assert len(zostale) == 5
        stare = [s for s in zostale if s.day_key != zostale[-1].day_key]
        # z każdego starego dnia został dokładnie jeden — ten najpóźniejszy
        po_dniach = {}
        for s in stare:
            po_dniach.setdefault(s.day_key, []).append(s)
        assert all(len(v) == 1 for v in po_dniach.values())
        assert all(v[0].equity == 10015.0 or v[0].equity == 10014.0
                   for v in po_dniach.values())
    finally:
        session.close()


def test_pruning_nie_miesza_kont():
    session = SessionLocal()
    try:
        a = _konto(session, "PRUNE-A")
        b = _konto(session, "PRUNE-B")
        for h in (9, 15):
            _snap(session, a, 45, h)
            _snap(session, b, 45, h)
        session.commit()

        prune_equity_snapshots(session)

        for acc in (a, b):
            dnia = (session.query(EquitySnapshot)
                    .filter(EquitySnapshot.account_id == acc.id).all())
            assert len(dnia) == 1
            assert dnia[0].equity == 10015.0
    finally:
        session.close()


def test_drugi_przebieg_nic_nie_kasuje():
    session = SessionLocal()
    try:
        assert prune_equity_snapshots(session) == 0
    finally:
        session.close()


def test_partia_mniejsza_niz_zaleglosci():
    """Kasowanie partiami: mała partia w kilku obrotach pętli zbiera całość."""
    session = SessionLocal()
    try:
        acc = _konto(session, "PRUNE-BATCH")
        for h in range(7):
            _snap(session, acc, 60, 9 + h)
        session.commit()

        usuniete = prune_equity_snapshots(session, partia=2)
        assert usuniete == 6
        zostal = (session.query(EquitySnapshot)
                  .filter(EquitySnapshot.account_id == acc.id).all())
        assert len(zostal) == 1
    finally:
        session.close()
