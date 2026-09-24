"""`/api/tick`: jedno zadanie nie wywraca crona, a ogon ma budżet czasu.

Cron chodzi raz na dobę, a Vercel ubija funkcję po 60 s. Wcześniej wyjątek
w recapie dawał 500 i reszta doby (leady, treści, sprzątanie) przepadała;
wolny ostatni krok zabijał całą funkcję.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("CRON_SECRET", "sekret-crona")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod, push  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)
CRON = {"Authorization": f"Bearer {get_settings().cron_secret}"}


def test_wyjatek_w_jednym_zadaniu_nie_wywraca_crona(monkeypatch):
    def padaj():
        raise RuntimeError("recap padł")
    wolane = []
    monkeypatch.setattr(push, "daily_recap", padaj)
    monkeypatch.setattr(main_mod, "_lead_followups", lambda: (wolane.append("leady"), {"sent": 2})[1])
    monkeypatch.setattr(main_mod, "_content_tick", lambda: (wolane.append("tresc"), {"sent": 1})[1])
    r = client.get("/api/tick", headers=CRON)
    assert r.status_code == 200
    j = r.json()
    assert j["daily_recap"] == {"sent": 0}
    assert j["lead_followups"] == 2 and j["channel_posts"] == 1
    assert wolane == ["leady", "tresc"], "zadania po padniętym idą dalej"


def test_po_budzecie_ogon_jest_pomijany(monkeypatch):
    wolane = []
    monkeypatch.setattr(main_mod, "TICK_BUDZET_S", 0.0)
    monkeypatch.setattr(main_mod, "_lead_followups", lambda: (wolane.append("leady"), {"sent": 5})[1])
    monkeypatch.setattr(main_mod, "_content_tick", lambda: (wolane.append("tresc"), {"sent": 1})[1])
    r = client.get("/api/tick", headers=CRON)
    assert r.status_code == 200
    assert wolane == [] and r.json()["lead_followups"] == 0
