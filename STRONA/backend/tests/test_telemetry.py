"""Telemetria produktowa: track() nigdy nie rzuca i zapisuje wiersz,
POST /api/telemetry ma whitelist + wymaga logowania, admin dostaje agregację.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, telemetry  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import TelemetryEvent, Trader  # noqa: E402

init_db()
client = TestClient(app)

ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(1000))


def _trader():
    email = f"tele{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Tele Tester", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, email, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _wiersze(name, tid=None):
    s = SessionLocal()
    q = s.query(TelemetryEvent).filter(TelemetryEvent.name == name)
    if tid is not None:
        q = q.filter(TelemetryEvent.trader_id == tid)
    rows = q.all()
    s.close()
    return rows


def test_track_zapisuje_wiersz_z_propsami():
    tid, _, _ = _trader()
    telemetry.track("unit_zdarzenie", tid, foo="bar", n=7)
    rows = _wiersze("unit_zdarzenie", tid)
    assert len(rows) == 1
    assert '"foo": "bar"' in rows[0].props and '"n": 7' in rows[0].props


def test_track_nigdy_nie_rzuca(monkeypatch):
    import app.telemetry as t
    from app import db
    monkeypatch.setattr(db, "SessionLocal", None)  # symulacja awarii bazy
    t.track("boom", 1)  # nie może wybuchnąć


def test_login_i_signup_zostawiaja_slad():
    n = next(LICZNIK)
    email = f"tele-auth{n}@test.pl"
    r = client.post("/api/auth/signup", json={
        "email": email, "password": "haslo1234", "full_name": "Tele Auth"})
    assert r.status_code == 200
    tid = r.json()["trader"]["id"]
    assert len(_wiersze("signup", tid)) == 1
    r = client.post("/api/auth/login", json={"email": email, "password": "haslo1234"})
    assert r.status_code == 200
    assert len(_wiersze("login", tid)) == 1


def test_endpoint_wymaga_auth_i_whitelisty():
    assert client.post("/api/telemetry",
                       json={"name": "view_open"}).status_code in (401, 403)
    tid, _, h = _trader()
    ok = client.post("/api/telemetry", headers=h,
                     json={"name": "view_open", "props": {"view": "store"}})
    assert ok.status_code == 200
    assert len(_wiersze("view_open", tid)) == 1
    zle = client.post("/api/telemetry", headers=h,
                      json={"name": "dowolna_nazwa"})
    assert zle.status_code == 400
    assert _wiersze("dowolna_nazwa") == []


def test_admin_agregacja_liczy_per_dzien():
    tid1, _, _ = _trader()
    tid2, _, _ = _trader()
    for _ in range(2):
        telemetry.track("agg_zdarzenie", tid1)
    telemetry.track("agg_zdarzenie", tid2)
    assert client.get("/api/admin/telemetry").status_code in (401, 403)
    r = client.get("/api/admin/telemetry", headers=ADMIN)
    assert r.status_code == 200
    wiersz = next(i for i in r.json()["items"] if i["name"] == "agg_zdarzenie")
    assert wiersz["count"] == 3 and wiersz["traders"] == 2
    assert len(wiersz["day"]) == 10  # YYYY-MM-DD
