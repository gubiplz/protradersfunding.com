"""Tick ryzyka: najpierw ocena kont, budżet czasu i rotacja listy.

Przy wolnym MetaApi funkcja (60 s) umierała po 1–2 kontach — i zawsze tych
samych, bo lista szła od początku. Teraz przerwany przebieg zapisuje kursor,
a następny zaczyna od kolejnego konta.
"""
import asyncio
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app import poller, provisioning  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Account, AppSetting  # noqa: E402

init_db()


def _konta(n):
    s = SessionLocal()
    s.query(Account).filter(Account.metaapi_account_id.isnot(None)) \
        .update({Account.status: "archived"}, synchronize_session=False)
    ids = []
    for i in range(n):
        a = Account(login=f"93{i:05d}", trader_name="Rot", product_key="2step-50k", preset="2step-50k",
                    initial_balance=50_000.0, steps=2, phase="eval_1", status="active",
                    balance=50_000.0, equity=50_000.0, peak_equity=50_000.0,
                    day_start_equity=50_000.0, day_start_balance=50_000.0,
                    metaapi_account_id=f"rot-{i}")
        s.add(a); s.flush(); ids.append(a.id)
    s.commit(); s.close()
    return ids


def test_przerwany_przebieg_zaczyna_od_nastepnego_konta(monkeypatch):
    ids = _konta(5)
    s = SessionLocal()
    row = s.get(AppSetting, poller.RISK_CURSOR_KEY)
    if row:
        s.delete(row)
    s.commit(); s.close()
    widziane = []

    async def licz(session, acc, feed):
        widziane.append(acc.id)
        if len(widziane) % 2 == 0:
            monkeypatch.setattr(poller, "RISK_BUDZET_S", -1.0)   # „skończył się czas"

    async def nic(*a, **k):
        return None

    provisioning_wolane = []

    async def prov(*a, **k):
        provisioning_wolane.append(1)

    monkeypatch.setattr(poller, "process_account", licz)
    monkeypatch.setattr(poller, "dokoncz_odciecia", nic)
    monkeypatch.setattr(provisioning, "provision_pending", prov)
    monkeypatch.setattr(provisioning, "dopnij_brakujace_rejestracje", nic)

    monkeypatch.setattr(poller, "RISK_BUDZET_S", 40.0)
    w1 = asyncio.run(poller.tick_ryzyka(min_odstep_s=0))
    assert widziane == ids[:2] and w1["evaluated"] == 2
    assert provisioning_wolane == [], "bez czasu provisioning czeka na następny tick"

    monkeypatch.setattr(poller, "RISK_BUDZET_S", 40.0)
    asyncio.run(poller.tick_ryzyka(min_odstep_s=0))
    assert widziane[2:] == ids[2:4], "drugi przebieg startuje za kursorem"

    async def wszystkie(session, acc, feed):
        widziane.append(acc.id)
    monkeypatch.setattr(poller, "process_account", wszystkie)
    monkeypatch.setattr(poller, "RISK_BUDZET_S", 40.0)
    widziane.clear()
    w3 = asyncio.run(poller.tick_ryzyka(min_odstep_s=0))
    assert widziane == [ids[4]] + ids[:4], "po końcu listy wraca na początek"
    assert "evaluated" not in w3
    assert provisioning_wolane == [1], "pełny przebieg w budżecie → provisioning po ocenie"
