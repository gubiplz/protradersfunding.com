"""Zatwierdzenie posta, który ma już termin, od razu go uzbraja.

Usterka z produkcji: sześć postów odtworzonych z archiwum przyszło z importu
z ustawionym terminem i statusem `draft`. Człowiek klikał Approve, panel
pokazywał zielony status i widoczną datę — a post nie wychodził NIGDY, bo
`wyslij_zaplanowane` bierze wyłącznie `scheduled`. Stan „approved z datą" nie
znaczył dla crona nic, a wyglądał dokładnie jak gotowy do publikacji.

Drugi klik w Schedule był potrzebny, ale nic w interfejsie o tym nie mówiło:
data już tam stała.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ChannelPost  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

TRESC = "Timeless copy with no figures in it at all."


@pytest.fixture
def sprzataj():
    stworzone: list[int] = []
    yield stworzone
    s = SessionLocal()
    try:
        if stworzone:
            s.query(ChannelPost).filter(ChannelPost.id.in_(stworzone)).delete(
                synchronize_session=False)
            s.commit()
    finally:
        s.close()


def _post(sprzataj, *, termin=None):
    s = SessionLocal()
    try:
        p = ChannelPost(channel="mgmt", kind="text", body=TRESC, proof="",
                        status="draft", origin="panel", scheduled_for=termin)
        s.add(p)
        s.commit()
        sprzataj.append(p.id)
        return p.id
    finally:
        s.close()


def _stan(post_id):
    s = SessionLocal()
    try:
        p = s.get(ChannelPost, post_id)
        return p.status, p.scheduled_for
    finally:
        s.close()


def test_post_z_terminem_po_zatwierdzeniu_jest_uzbrojony(sprzataj):
    """Sedno usterki: termin już jest, więc Approve ma go uzbroić, a nie
    zostawić w stanie, który dla crona nie znaczy nic."""
    termin = datetime.now(timezone.utc) + timedelta(days=1)
    pid = _post(sprzataj, termin=termin)

    odp = client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)

    assert odp.status_code == 200
    assert odp.json()["status"] == "scheduled"
    assert _stan(pid)[0] == "scheduled"


def test_termin_nie_jest_przy_tym_ruszany(sprzataj):
    """Uzbrojenie ma zmienić STATUS, nie datę — inaczej Approve po cichu
    przesuwałby publikację."""
    termin = datetime(2026, 10, 15, 14, 30, tzinfo=timezone.utc)
    pid = _post(sprzataj, termin=termin)

    client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)

    zapisany = _stan(pid)[1]
    assert zapisany.replace(tzinfo=timezone.utc) == termin


def test_post_bez_terminu_zostaje_approved(sprzataj):
    """Bez daty nie ma czego uzbrajać — człowiek dopiero wybierze termin."""
    pid = _post(sprzataj)

    odp = client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)

    assert odp.json()["status"] == "approved"
    assert _stan(pid)[0] == "approved"


def test_zalegly_termin_tez_uzbraja(sprzataj):
    """Post z przeszłą datą ma wyjść przy najbliższym przebiegu, a nie zostać
    w zawieszeniu — to jest dokładnie ten przypadek, który wywołał zgłoszenie."""
    termin = datetime.now(timezone.utc) - timedelta(hours=2)
    pid = _post(sprzataj, termin=termin)

    client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)

    assert _stan(pid)[0] == "scheduled"


def test_odmowa_walidatora_nie_uzbraja(sprzataj):
    """Kwota bez dowodu ma zatrzymać post niezależnie od tego, że ma termin."""
    termin = datetime.now(timezone.utc) + timedelta(days=1)
    s = SessionLocal()
    try:
        p = ChannelPost(channel="mgmt", kind="text", proof="",
                        body="We paid out $6,180 last week.",
                        status="draft", origin="panel", scheduled_for=termin)
        s.add(p)
        s.commit()
        sprzataj.append(p.id)
        pid = p.id
    finally:
        s.close()

    odp = client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)

    assert odp.status_code == 400
    assert _stan(pid)[0] == "draft", "odrzucony post nie ma prawa byc uzbrojony"
