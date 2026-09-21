"""Ręczny breach z panelu: egzekwuje u brokera i szanuje kupiony add-on.

Dwie rzeczy, które bolą dopiero wtedy, gdy za kontem stoi PRAWDZIWY rachunek
MT5 (add-on Copytrading):

  1. do tej pory ta ścieżka ustawiała status u nas i wysyłała maila, ale nie
     zamykała pozycji ani nie odcinała konta — trader handlował dalej;
  2. panel ma „Copy trading between accounts" na liście gotowych powodów,
     a my właśnie sprzedajemy zgodę na kopiowanie. Jedno kliknięcie z rozpędu
     i klient ma rację, a my zwrot $299.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import poller  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.feed import Feed  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Breach  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}


class _Broker(Feed):
    def __init__(self):
        self.zamkniete, self.zablokowane = [], []

    async def snapshot(self, login, metaapi_account_id, initial_balance, *,
                       password=None, server=None):
        return None

    async def close_all_positions(self, metaapi_account_id, *, login=None, password=None):
        self.zamkniete.append(metaapi_account_id)
        return 2

    async def lock(self, metaapi_account_id, *, login=None, password=None):
        self.zablokowane.append(metaapi_account_id)


@pytest.fixture
def broker(monkeypatch):
    b = _Broker()
    monkeypatch.setattr(poller, "_feed", b)
    return b


def _konto(login, *, copytrading=False, metaapi_id=None) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Breach Tester", product_key="2step-25k",
                  preset="2step-25k", initial_balance=25_000.0, steps=2,
                  phase="eval_1", status="active", balance=25_000.0, equity=25_000.0,
                  peak_equity=25_000.0, day_start_equity=25_000.0, day_start_balance=25_000.0,
                  platform_login=login, platform_password="haslo123",
                  metaapi_account_id=metaapi_id, mt5_backed=bool(metaapi_id),
                  copytrading=copytrading)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


# --------------------------------------------------------------------------- #
#  Egzekwowanie
# --------------------------------------------------------------------------- #
def test_realne_konto_zostaje_odciete_u_brokera(broker):
    aid = _konto("800001", metaapi_id="acc-1")

    r = client.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN,
                    json={"reason": "News trading violation"})

    assert r.status_code == 200
    assert r.json()["positions_closed"] == 2
    assert broker.zamkniete == ["acc-1"], "pozycje muszą zostać zamknięte"
    assert broker.zablokowane == ["acc-1"], "konto ma stracić dostęp do handlu"
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.status == "failed" and acc.breach_reason == "News trading violation"
    assert s.query(Breach).filter(Breach.account_id == aid).count() == 1
    s.close()


def test_milczacy_broker_nie_cofa_breachu(monkeypatch):
    """Stan u nas ma być zapisany nawet wtedy, gdy broker nie odpowiada."""
    class _Padajacy(Feed):
        async def snapshot(self, *a, **k):
            return None

        async def close_all_positions(self, metaapi_account_id, *, login=None, password=None):
            raise RuntimeError("broker down")

    monkeypatch.setattr(poller, "_feed", _Padajacy())
    aid = _konto("800002", metaapi_id="acc-2")

    r = client.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN,
                    json={"reason": "Prohibited trading strategy"})

    assert r.status_code == 200 and r.json()["positions_closed"] == 0
    s = SessionLocal()
    assert s.get(Account, aid).status == "failed"
    s.close()


def test_konto_bez_rachunku_nie_dzwoni_do_brokera(broker):
    """65 kont produkcji stoi na poświadczeniach lokalnych — nie ma tam czego
    zamykać, a próba logowania tylko odbiłaby się od serwera."""
    aid = _konto("800003")
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.platform_password = None       # konto sprzed provisioningu
    s.commit(); s.close()

    client.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN, json={"reason": "x"})

    assert broker.zamkniete == [] and broker.zablokowane == []


# --------------------------------------------------------------------------- #
#  Add-on
# --------------------------------------------------------------------------- #
def test_nie_wolno_zbreachowac_za_to_co_mu_sprzedalismy(broker):
    aid = _konto("800004", copytrading=True, metaapi_id="acc-4")

    r = client.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN,
                    json={"reason": "Copy trading between accounts"})

    assert r.status_code == 400
    assert "Copytrading add-on" in r.json()["detail"]
    s = SessionLocal()
    assert s.get(Account, aid).status == "active", "konto ma zostać nietknięte"
    s.close()
    assert broker.zamkniete == [], "nic nie wolno zamknąć przy odrzuconym breachu"


def test_odbija_tez_wlasne_sformulowanie_admina(broker):
    """Pole jest wolnego tekstu, więc dopasowanie idzie po fragmencie."""
    aid = _konto("800005", copytrading=True, metaapi_id="acc-5")

    r = client.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN,
                    json={"reason": "copytrading on more than one device"})

    assert r.status_code == 400


def test_inne_powody_dzialaja_normalnie_takze_z_addonem(broker):
    """Dodatek nie jest immunitetem — dotyczy kopiowania i urządzeń, nic więcej."""
    aid = _konto("800006", copytrading=True, metaapi_id="acc-6")

    r = client.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN,
                    json={"reason": "Daily loss limit exceeded"})

    assert r.status_code == 200
    assert broker.zablokowane == ["acc-6"]


def test_konto_bez_addonu_dalej_mozna_zbreachowac_za_kopiowanie(broker):
    aid = _konto("800007", metaapi_id="acc-7")

    r = client.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN,
                    json={"reason": "Copy trading between accounts"})

    assert r.status_code == 200
