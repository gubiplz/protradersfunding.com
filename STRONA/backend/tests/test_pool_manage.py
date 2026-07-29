"""Zarządzanie pulą MT5: korekta danych i generowanie kont demo.

Dwie rzeczy, które muszą działać poprawnie:

* Zmiana hasła rachunku, ktory jest JUZ PRZYDZIELONY, musi trafic takze na konto
  tradera — inaczej portal pokazuje stare dane i trader nie zaloguje sie do MT5.
* Przycisk „Auto-generate" nie moze udawac, ze dziala tam, gdzie nie ma
  przegladarki. Na hostingu bezserwerowym Chromium nie istnieje, wiec endpoint
  ma odmowic z wyjasnieniem, a nie wysypac sie w polowie.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as app_main  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, PoolAccount  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}


def _wpis(login: str, rozmiar: float = 50_000.0, przydzielony_do: int | None = None) -> int:
    s = SessionLocal()
    p = PoolAccount(platform_login=login, platform_password="Stare123",
                    platform_server="GOMarketsLtd-Demo", account_size=rozmiar,
                    claimed=przydzielony_do is not None,
                    claimed_by_account_id=przydzielony_do)
    s.add(p); s.commit()
    pid = p.id
    s.close()
    return pid


def _konto(login: str) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Edit Tester", product_key="2step-50k",
                  preset="2step-50k", initial_balance=50_000.0, steps=2, status="active",
                  phase="eval_1", platform_login=login, platform_password="Stare123",
                  platform_server="GOMarketsLtd-Demo", balance=50_000.0, equity=50_000.0,
                  peak_equity=50_000.0, day_start_equity=50_000.0, day_start_balance=50_000.0)
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


def test_zmiana_hasla_wolnego_wpisu():
    pid = _wpis("8100001")
    r = client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H,
                     json={"platform_password": "Nowe456", "platform_server": "InnyBroker-Demo"})
    assert r.status_code == 200 and r.json()["propagated_to_account"] is None

    s = SessionLocal(); p = s.get(PoolAccount, pid)
    assert p.platform_password == "Nowe456" and p.platform_server == "InnyBroker-Demo"
    s.close()


def test_zmiana_poswiadczen_przydzielonego_idzie_na_konto_tradera():
    """Bez tego trader zostaje ze starym haslem i nie wejdzie do terminala."""
    aid = _konto("8200001")
    pid = _wpis("8200001", przydzielony_do=aid)

    r = client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H,
                     json={"platform_password": "ZmienioneUBrokera9"})
    assert r.status_code == 200 and r.json()["propagated_to_account"] == aid

    s = SessionLocal(); acc = s.get(Account, aid)
    assert acc.platform_password == "ZmienioneUBrokera9"
    s.close()


def test_rozmiaru_przydzielonego_rachunku_nie_da_sie_zmienic():
    """Konto challenge ma juz od niego policzone limity ryzyka."""
    aid = _konto("8300001")
    pid = _wpis("8300001", przydzielony_do=aid)
    r = client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H, json={"account_size": 200_000})
    assert r.status_code == 400


def test_puste_pole_jest_odrzucane():
    pid = _wpis("8400001")
    assert client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H,
                        json={"platform_login": "   "}).status_code == 400


def test_edycja_nieistniejacego_wpisu_to_404():
    assert client.patch("/api/admin/pool/999999", headers=ADMIN_H,
                        json={"platform_server": "X"}).status_code == 404


def test_generowanie_odmawia_bez_przegladarki(monkeypatch):
    """Na hostingu bezserwerowym nie ma Chromium — endpoint ma to powiedziec wprost."""
    monkeypatch.setattr(app_main.metaquotes_web, "chromium_available", lambda: False)
    monkeypatch.setattr(app_main.settings, "metaquotes_web_enabled", True)

    r = client.post("/api/admin/pool/generate", headers=ADMIN_H,
                    json={"account_size": 50_000, "count": 1})
    assert r.status_code == 400
    assert "Chromium" in r.json()["detail"]

    # panel dostaje te sama informacje, zeby nie pokazywac przycisku, ktory nie zadziala
    dane = client.get("/api/admin/pool", headers=ADMIN_H).json()
    assert dane["can_generate"] is False and dane["generate_hint"]


def test_generowanie_pilnuje_limitow(monkeypatch):
    monkeypatch.setattr(app_main.metaquotes_web, "chromium_available", lambda: True)
    monkeypatch.setattr(app_main.settings, "metaquotes_web_enabled", True)

    assert client.post("/api/admin/pool/generate", headers=ADMIN_H,
                       json={"account_size": 0, "count": 1}).status_code == 400
    assert client.post("/api/admin/pool/generate", headers=ADMIN_H,
                       json={"account_size": 50_000, "count": 99}).status_code == 400


def test_generowanie_zapisuje_konta_z_kanalu(monkeypatch):
    """Kanal jest zamockowany — sprawdzamy, ze zwrocone poswiadczenia ladują w puli."""
    class _FakeCreds:
        def __init__(self, login):
            self.login, self.password, self.server = login, "WygenerowaneX1", "MetaQuotes-Demo"

    class _FakeOpener:
        def __init__(self): self.n = 0
        async def open_demo_account(self, spec):
            self.n += 1
            return _FakeCreds(f"9900{self.n:03d}")

    monkeypatch.setattr(app_main.metaquotes_web, "chromium_available", lambda: True)
    monkeypatch.setattr(app_main.settings, "metaquotes_web_enabled", True)
    monkeypatch.setattr(app_main.metaquotes_web, "make_opener", lambda s=None: _FakeOpener())

    r = client.post("/api/admin/pool/generate", headers=ADMIN_H,
                    json={"account_size": 25_000, "count": 2})
    assert r.status_code == 200
    assert len(r.json()["created"]) == 2

    s = SessionLocal()
    wpisy = s.query(PoolAccount).filter(PoolAccount.account_size == 25_000).all()
    assert len(wpisy) == 2
    assert all(w.platform_server == "MetaQuotes-Demo" and not w.claimed for w in wpisy)
    s.close()
