"""Dzwonek admina: stabilne id, rodzaj zdarzenia, przeczytane i usunięte per admin.

Wcześniej „nowe" rozstrzygał localStorage jednej przeglądarki, a usunąć nie dało
się niczego. Teraz stan leży na serwerze, więc telefon i laptop widzą to samo,
a usunięta pozycja nie wraca przy następnym odświeżeniu.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminInboxMark, Lead, LeadEvent, Trader  # noqa: E402

init_db()
client = TestClient(app)


def _admin(email: str) -> tuple[int, dict]:
    s = SessionLocal()
    t = s.query(Trader).filter(Trader.email == email).first()
    if not t:
        t = Trader(email=email, password_hash=auth.hash_password("x"), is_admin=True,
                   referral_code=auth.secrets.token_hex(3))
        s.add(t)
        s.commit()
    tid, h = t.id, {"Authorization": f"Bearer {auth.make_token(t.id, t.password_hash)}"}
    s.close()
    return tid, h


def _lead_ze_zdarzeniami(nazwa: str, kiedy: datetime) -> int:
    s = SessionLocal()
    l = Lead(email=f"{nazwa}-{kiedy.timestamp()}@x.test", name=nazwa, source="meta", status="new")
    s.add(l)
    s.flush()
    s.add(LeadEvent(lead_id=l.id, kind="applied", detail="form", actor="t", created_at=kiedy))
    s.add(LeadEvent(lead_id=l.id, kind="claim", detail="released", actor="t", created_at=kiedy))
    s.commit()
    lid = l.id
    s.close()
    return lid


def _wpisy(h: dict, lead_id: int) -> list[dict]:
    items = client.get("/api/admin/inbox", headers=h).json()["items"]
    return [i for i in items if i.get("lead_id") == lead_id]


def test_pozycje_maja_id_rodzaj_i_nazwe():
    _, h = _admin("inbox-a@test")
    lid = _lead_ze_zdarzeniami("Clarisse Castro", datetime.now(timezone.utc))
    wpisy = _wpisy(h, lid)
    assert {i["kind"] for i in wpisy} == {"applied", "claim"}
    assert all(i["id"].startswith("lead:") and i["who"] == "Clarisse Castro" for i in wpisy)
    assert len({i["id"] for i in wpisy}) == len(wpisy)


def test_pierwsze_otwarcie_nie_zapala_starych_pozycji():
    """Wdrożenie nie może zapalić setki dawnych zdarzeń jako nowych."""
    _, h = _admin("inbox-b@test")
    lid = _lead_ze_zdarzeniami("Stary", datetime.now(timezone.utc) - timedelta(hours=2))
    assert all(i["read"] for i in _wpisy(h, lid))
    nowy = _lead_ze_zdarzeniami("Nowy", datetime.now(timezone.utc) + timedelta(seconds=5))
    assert not any(i["read"] for i in _wpisy(h, nowy))


def test_przeczytane_i_usuniete_sa_per_admin():
    tid_a, a = _admin("inbox-c@test")
    _, b = _admin("inbox-d@test")
    client.get("/api/admin/inbox", headers=a)
    client.get("/api/admin/inbox", headers=b)
    lid = _lead_ze_zdarzeniami("Wspólny", datetime.now(timezone.utc) + timedelta(seconds=5))
    ids = [i["id"] for i in _wpisy(a, lid)]

    r = client.post("/api/admin/inbox/mark", headers=a, json={"ids": ids[:1], "read": True})
    assert r.status_code == 200 and r.json()["changed"] == 1
    assert {i["id"]: i["read"] for i in _wpisy(a, lid)}[ids[0]] is True
    assert not any(i["read"] for i in _wpisy(b, lid)), "drugi admin czyta dzwonek osobno"

    client.post("/api/admin/inbox/mark", headers=a, json={"ids": ids, "hidden": True})
    assert _wpisy(a, lid) == [], "usunięta pozycja nie wraca przy odświeżeniu"
    assert len(_wpisy(b, lid)) == 2

    client.post("/api/admin/inbox/mark", headers=a, json={"ids": ids, "hidden": False})
    assert len(_wpisy(a, lid)) == 2, "cofnięcie przywraca pozycje"

    s = SessionLocal()
    assert s.query(AdminInboxMark).filter_by(admin_id=tid_a).count() >= 3
    s.close()


def test_nieprzeczytane_mimo_znaku_wodnego():
    """„Mark unread" na starej pozycji musi przeżyć znak wodny."""
    _, h = _admin("inbox-e@test")
    lid = _lead_ze_zdarzeniami("Wraca", datetime.now(timezone.utc) - timedelta(hours=3))
    ids = [i["id"] for i in _wpisy(h, lid)]
    client.post("/api/admin/inbox/mark", headers=h, json={"ids": ids, "read": False})
    assert not any(i["read"] for i in _wpisy(h, lid))


def test_znak_wodny_nie_do_nadpisania_i_puste_zadanie():
    _, h = _admin("inbox-f@test")
    assert client.post("/api/admin/inbox/mark", headers=h,
                       json={"ids": ["*"], "read": False}).json()["changed"] == 0
    assert client.post("/api/admin/inbox/mark", headers=h,
                       json={"ids": ["lead:1"]}).json()["changed"] == 0


def test_bez_uprawnien_admina_nie_wolno():
    assert client.post("/api/admin/inbox/mark", json={"ids": ["lead:1"], "read": True}).status_code == 403
