"""`/api/tick` — silnik ryzyka wyzwalany z zewnątrz.

Na hostingu bezserwerowym (Vercel) nie ma procesu, który utrzymałby pętlę
pollera między requestami, więc silnik napędza cron uderzający w ten endpoint.
Skoro jedno wywołanie rusza saldami kont, przelicza reguły i potrafi oznaczyć
konto jako failed, to endpoint MUSI być zamknięty — te testy pilnują głównie
tego, żeby nikt z ulicy nie mógł tykać cudzych kont.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("CRON_SECRET", "sekret-crona")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, EquitySnapshot  # noqa: E402

init_db()
client = TestClient(app)
USTAWIENIA = get_settings()


def _konto(login: str) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Tick Tester", product_key="2step-50k",
                  initial_balance=50_000.0, steps=2, balance=50_000.0, equity=50_000.0,
                  peak_equity=50_000.0, day_start_equity=50_000.0, day_start_balance=50_000.0,
                  status="active", phase="eval_1")
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


def test_bez_naglowka_nie_wpuszcza():
    assert client.post("/api/tick").status_code == 401
    assert client.get("/api/tick").status_code == 401


def test_zly_sekret_i_zly_token_odbijaja_sie():
    assert client.post("/api/tick", headers={"Authorization": "Bearer nie-ten"}).status_code == 401
    assert client.post("/api/tick", headers={"X-Admin-Token": "nie-ten"}).status_code == 401


def test_cron_wyzwala_tick_sekretem():
    """Vercel Cron wysyła sekret w nagłówku Authorization i uderza GET-em."""
    aid = _konto("tick-cron")
    r = client.get("/api/tick", headers={"Authorization": f"Bearer {USTAWIENIA.cron_secret}"})
    assert r.status_code == 200
    assert r.json()["accounts"] >= 1

    # przebieg naprawdę policzył konto: powstał snapshot equity
    s = SessionLocal()
    ile = s.query(EquitySnapshot).filter(EquitySnapshot.account_id == aid).count()
    s.close()
    assert ile >= 1


def test_admin_tez_moze_szturchnac_silnik():
    _konto("tick-admin")
    r = client.post("/api/tick", headers={"X-Admin-Token": USTAWIENIA.admin_token})
    assert r.status_code == 200
    assert r.json()["feed"] == "sim"
