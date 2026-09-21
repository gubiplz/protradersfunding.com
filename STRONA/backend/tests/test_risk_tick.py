"""Silnik ryzyka co minutę: wąski przebieg, jeden naraz, tylko realne konta.

Cron Vercela (Hobby) chodzi raz na dobę i oba sloty są zajęte, więc ten
przebieg woła zewnętrzny scheduler. Trzy rzeczy muszą być pewne:

  1. bez sekretu nikt go nie odpali — to endpoint, który zamyka pozycje,
  2. dwa nakładające się wywołania liczą ruch RAZ (na hostingu bezserwerowym
     każde żądanie to inna instancja, więc pamięć procesu niczego nie pilnuje),
  3. nie dotyka 65 kont produkcji, które stoją na poświadczeniach lokalnych.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("CRON_SECRET", "sekret-crona")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import asyncio  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import poller  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.feed import Feed, MarketSnapshot  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, AppSetting, EquitySnapshot  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
CRON = {"Authorization": "Bearer sekret-crona"}


class _FeedLiczacy(Feed):
    """Zapisuje, o które konta pytano — po tym poznajemy zasięg przebiegu."""

    def __init__(self):
        self.pytania = []

    async def snapshot(self, login, metaapi_account_id, initial_balance, *,
                       password=None, server=None):
        self.pytania.append(login)
        return MarketSnapshot(balance=initial_balance, equity=initial_balance,
                              open_pnl=0.0, has_open_position=False)


def _konto(login, *, metaapi_id=None, saldo=25_000.0) -> int:
    """`day_key` z dzisiaj nie jest ozdobą: bez niego pierwszy tick jest dla
    silnika OTWARCIEM DNIA, więc baza dnia siada na bieżącym equity i żadna
    strata nie zdąży się policzyć (rules.py, gałąź resetu dnia)."""
    s = SessionLocal()
    acc = Account(login=login, trader_name="Risk Tester", product_key="2step-25k",
                  preset="2step-25k", initial_balance=saldo, steps=2,
                  phase="eval_1", status="active", balance=saldo, equity=saldo,
                  peak_equity=saldo, day_start_equity=saldo, day_start_balance=saldo,
                  day_key=poller.server_day_key(),
                  metaapi_account_id=metaapi_id, mt5_backed=bool(metaapi_id))
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


@pytest.fixture
def feed(monkeypatch):
    f = _FeedLiczacy()
    monkeypatch.setattr(poller, "_feed", f)
    # Provisioning nie ma tu nic do roboty, a chodziłby do sieci.
    async def _nic(*a, **k):
        return None
    monkeypatch.setattr(poller.provisioning, "provision_pending", _nic)
    return f


def _tylko_to_konto(monkeypatch, aid: int):
    """Zawęża przebieg do jednego konta — w bazie leżą jeszcze konta
    z poprzednich testów, a ten sprawdza SKUTEK breachu, nie zasięg."""
    oryginal = poller._active_query
    monkeypatch.setattr(poller, "_active_query",
                        lambda session: oryginal(session).filter(Account.id == aid))


def _odblokuj():
    s = SessionLocal()
    row = s.get(AppSetting, poller.RISK_TICK_KEY)
    if row:
        s.delete(row); s.commit()
    s.close()


# --------------------------------------------------------------------------- #
#  Dostęp
# --------------------------------------------------------------------------- #
def test_bez_sekretu_nie_wpuszcza():
    """Ten adres zamyka pozycje na cudzych kontach — nie może być otwarty."""
    assert client.post("/api/cron/risk").status_code == 401
    assert client.post("/api/cron/risk",
                       headers={"Authorization": "Bearer nie-ten"}).status_code == 401


def test_cron_i_admin_moga_szturchnac(feed):
    _odblokuj()
    assert client.get("/api/cron/risk", headers=CRON).status_code == 200
    _odblokuj()
    assert client.post("/api/cron/risk", headers=ADMIN).status_code == 200


# --------------------------------------------------------------------------- #
#  Zasięg
# --------------------------------------------------------------------------- #
def test_liczy_tylko_konta_z_realnym_rachunkiem(feed):
    realne = _konto("900001", metaapi_id="acc-real")
    _konto("900002")                      # poświadczenia lokalne — jak cała produkcja
    _odblokuj()

    r = client.post("/api/cron/risk", headers=CRON)

    assert r.status_code == 200
    assert "900001" in feed.pytania
    assert "900002" not in feed.pytania, "konto bez rachunku nie ma czego odpytywać"
    s = SessionLocal()
    assert s.query(EquitySnapshot).filter(EquitySnapshot.account_id == realne).count() == 1
    s.close()


def test_konto_zbreachowane_wypada_z_przebiegu(feed):
    _konto("900003", metaapi_id="acc-failed")
    s = SessionLocal()
    acc = s.query(Account).filter(Account.login == "900003").first()
    acc.status = "failed"; s.commit(); s.close()
    _odblokuj()

    client.post("/api/cron/risk", headers=CRON)

    assert "900003" not in feed.pytania


# --------------------------------------------------------------------------- #
#  Zamek
# --------------------------------------------------------------------------- #
def test_drugie_wywolanie_w_oknie_nie_liczy_drugi_raz(feed):
    _konto("900004", metaapi_id="acc-lock")
    _odblokuj()

    pierwsze = client.post("/api/cron/risk", headers=CRON).json()
    po_pierwszym = list(feed.pytania)
    drugie = client.post("/api/cron/risk", headers=CRON).json()

    assert pierwsze.get("accounts", 0) >= 1
    assert drugie.get("skipped") is True, "drugi przebieg w oknie ma odpaść"
    assert feed.pytania == po_pierwszym, "ruch policzony raz, nie dwa razy"
    assert po_pierwszym.count("900004") == 1


def test_zamek_przepuszcza_dokladnie_jednego_z_dwoch_rownoleglych():
    """Samo „przeczytaj, porównaj, zapisz" by nie wystarczyło — obie instancje
    przeczytałyby tę samą starą wartość. Stąd warunkowy UPDATE."""
    _odblokuj()
    a, b = SessionLocal(), SessionLocal()
    try:
        # Obie sesje widzą ten sam stan wyjściowy (albo jego brak).
        wyniki = [poller._zajmij_tick_ryzyka(a, 20.0),
                  poller._zajmij_tick_ryzyka(b, 20.0)]
    finally:
        a.close(); b.close()

    assert wyniki.count(True) == 1, f"dokładnie jeden przebieg ma wejść, było {wyniki}"


def test_stary_znacznik_nie_blokuje_na_zawsze(feed):
    _konto("900005", metaapi_id="acc-old")
    s = SessionLocal()
    row = s.get(AppSetting, poller.RISK_TICK_KEY)
    if row is None:
        row = AppSetting(key=poller.RISK_TICK_KEY); s.add(row)
    row.value = "1"          # rok 1970 — dawno poza oknem
    s.commit(); s.close()

    client.post("/api/cron/risk", headers=CRON)

    assert "900005" in feed.pytania, "przeterminowany znacznik nie może blokować"


def test_zepsuty_znacznik_nie_zatrzymuje_silnika(feed):
    _konto("900006", metaapi_id="acc-broken")
    s = SessionLocal()
    row = s.get(AppSetting, poller.RISK_TICK_KEY)
    if row is None:
        row = AppSetting(key=poller.RISK_TICK_KEY); s.add(row)
    row.value = "śmieć"
    s.commit(); s.close()

    client.post("/api/cron/risk", headers=CRON)

    assert "900006" in feed.pytania


# --------------------------------------------------------------------------- #
#  Egzekwowanie
# --------------------------------------------------------------------------- #
def test_przekroczony_limit_dzienny_konczy_konto_i_zamyka_pozycje(monkeypatch):
    """Cały sens tego przebiegu: strata ma zostać zauważona w minutach."""
    aid = _konto("900007", metaapi_id="acc-breach")
    zamkniete, zablokowane = [], []

    class _Padajacy(Feed):
        async def snapshot(self, login, metaapi_account_id, initial_balance, *,
                           password=None, server=None):
            # -6% w jednym ticku przy limicie dziennym 5%
            return MarketSnapshot(balance=23_500.0, equity=23_500.0,
                                  open_pnl=0.0, has_open_position=True)

        async def close_all_positions(self, metaapi_account_id, *, login=None, password=None):
            zamkniete.append(metaapi_account_id)
            return 1

        async def lock(self, metaapi_account_id, *, login=None, password=None):
            zablokowane.append(metaapi_account_id)

    monkeypatch.setattr(poller, "_feed", _Padajacy())
    _tylko_to_konto(monkeypatch, aid)
    async def _nic(*a, **k):
        return None
    monkeypatch.setattr(poller.provisioning, "provision_pending", _nic)
    _odblokuj()

    asyncio.run(poller.tick_ryzyka())

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.status == "failed"
    assert "daily" in (acc.breach_reason or "").lower()
    s.close()
    assert zamkniete == ["acc-breach"], "pozycje muszą zostać zamknięte u brokera"
    assert zablokowane == ["acc-breach"], "konto ma zostać odcięte od handlu"
