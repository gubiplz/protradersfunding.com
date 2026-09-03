"""Impersonacja z panelu: „zobacz portal oczami klienta" bez znajomości hasła.

Hasła w bazie to jednostronne skróty PBKDF2 — nie da się ich nikomu pokazać.
Zamiast tego panel dostaje pełnoprawny token sesji klienta. Pilnujemy tu
trzech granic: token wystawia wyłącznie admin, po zmianie hasła klienta stary
token umiera (odcisk pwf jak w każdej sesji), a podgląd nie zostawia śladów
w telemetrii — „wejścia do portalu" w dzienniku muszą znaczyć wejścia KLIENTA,
inaczej „loguje się codziennie" bywa echem naszych własnych zaglądań.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import TelemetryEvent, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

LICZNIK = iter(range(1000))


def _klient(email: str) -> int:
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Podglądany Klient",
                referral_code=f"IMP{next(LICZNIK):04d}")
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _wejsciowka(tid: int) -> str:
    r = client.post(f"/api/admin/traders/{tid}/impersonate", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_token_z_panelu_dziala_jak_sesja_klienta():
    tid = _klient("podgladany@test.pl")
    tok = _wejsciowka(tid)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "podgladany@test.pl"
    assert not me.json().get("is_admin")


def test_wystawia_tylko_admin():
    tid = _klient("nie-dla-kazdego@test.pl")
    assert client.post(f"/api/admin/traders/{tid}/impersonate").status_code in (401, 403)
    # Zwykły klient też nie wystawi sobie wejściówki na cudze konto.
    tok = auth.make_token(tid)
    r = client.post(f"/api/admin/traders/{tid}/impersonate",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)


def test_zmiana_hasla_uniewaznia_podglad():
    """Odcisk hasła w tokenie: klient zmienia hasło → wejściówka umiera razem
    ze wszystkimi starymi sesjami. Bez tego kopia tokenu żyłaby 2 godziny
    niezależnie od tego, co zrobi klient."""
    tid = _klient("zmienia-haslo@test.pl")
    tok = _wejsciowka(tid)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    tr.password_hash = auth.hash_password("calkiem-nowe-haslo")
    s.commit(); s.close()
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_podglad_nie_smieci_w_dzienniku():
    tid = _klient("czysty-dziennik@test.pl")
    tok = _wejsciowka(tid)
    r = client.post("/api/telemetry", json={"name": "view_open"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200

    s = SessionLocal()
    po_podgladzie = (s.query(TelemetryEvent)
                     .filter(TelemetryEvent.trader_id == tid).count())
    s.close()
    assert po_podgladzie == 0, "podgląd admina zapisał się jako aktywność klienta"

    # Ten sam ruch z PRAWDZIWĄ sesją klienta ma normalnie trafić do dziennika.
    zwykly = auth.make_token(tid)
    client.post("/api/telemetry", json={"name": "view_open"},
                headers={"Authorization": f"Bearer {zwykly}"})
    s = SessionLocal()
    po_kliencie = (s.query(TelemetryEvent)
                   .filter(TelemetryEvent.trader_id == tid).count())
    s.close()
    assert po_kliencie == 1


def test_brak_tradera_to_404():
    assert client.post("/api/admin/traders/999999/impersonate",
                       headers=ADMIN).status_code == 404
