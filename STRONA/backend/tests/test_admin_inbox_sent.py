"""Dzwonek admina nie pokazuje wiadomości WYSŁANYCH przez dział.

„Email sent" / SMS / Telegram do leada to własna akcja admina, nie nowina —
zaśmiecała listę. Domyślnie ukryte; przełącznik „Messages you sent"
w ustawieniach dzwonka (`ui_prefs.inbox.sent`) włącza je z powrotem.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, LeadEvent, Trader  # noqa: E402

init_db()
client = TestClient(app)


def _admin(email: str, prefs: dict | None = None) -> dict:
    s = SessionLocal()
    t = Trader(email=email, password_hash=auth.hash_password("x"), is_admin=True,
               referral_code=auth.secrets.token_hex(3),
               ui_prefs=json.dumps(prefs) if prefs is not None else None)
    s.add(t)
    s.commit()
    h = {"Authorization": f"Bearer {auth.make_token(t.id, t.password_hash)}"}
    s.close()
    return h


def _lead() -> int:
    kiedy = datetime.now(timezone.utc)
    s = SessionLocal()
    lead = Lead(email=f"sent-{kiedy.timestamp()}@x.test", name="Sent Test", source="meta", status="new")
    s.add(lead)
    s.flush()
    for kind in ("applied", "email", "sms", "telegram"):
        s.add(LeadEvent(lead_id=lead.id, kind=kind, detail="x", actor="t", created_at=kiedy))
    s.commit()
    lid = lead.id
    s.close()
    return lid


def _rodzaje(h: dict, lead_id: int) -> set[str]:
    items = client.get("/api/admin/inbox", headers=h).json()["items"]
    return {i["kind"] for i in items if i.get("lead_id") == lead_id}


def test_wyslane_domyslnie_ukryte():
    lid = _lead()
    assert _rodzaje(_admin("sent-a@test"), lid) == {"applied"}


def test_wyslane_po_wlaczeniu_widoczne():
    lid = _lead()
    h = _admin("sent-b@test", {"inbox": {"sent": True}})
    assert _rodzaje(h, lid) == {"applied", "email", "sms", "telegram"}


def test_inne_ustawienia_listy_nie_wlaczaja_wyslanych():
    lid = _lead()
    h = _admin("sent-c@test", {"inbox": {"group": False, "seen": True}})
    assert _rodzaje(h, lid) == {"applied"}
