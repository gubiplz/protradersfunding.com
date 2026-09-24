"""Usuwanie w dzwonku admina: usunięte nie zjadają limitu, „Delete all" czyści.

Dawniej limit zapytań (60 zdarzeń leadów) liczył się RAZEM z usuniętymi — po
usunięciu serii dzwonek pustoszał — a „Delete all" ukrywał tylko widoczne id,
więc na ich miejsce wjeżdżały starsze pozycje i wyglądało to jak powrót
usuniętych.
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

from app import auth, main  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminInboxMark, Lead, LeadEvent, Trader  # noqa: E402

init_db()
client = TestClient(app)


def _admin(email: str) -> tuple[dict, int]:
    s = SessionLocal()
    t = Trader(email=email, password_hash=auth.hash_password("x"), is_admin=True,
               referral_code=auth.secrets.token_hex(3))
    s.add(t)
    s.commit()
    h = {"Authorization": f"Bearer {auth.make_token(t.id, t.password_hash)}"}
    tid = t.id
    s.close()
    return h, tid


def _lead(ile: int, kiedy: datetime | None = None, source: str = "meta") -> tuple[int, list[str]]:
    """Lead z `ile` zdarzeniami, najstarsze pierwsze; zwraca id pozycji dzwonka."""
    kiedy = kiedy or datetime.now(timezone.utc)
    s = SessionLocal()
    lead = Lead(email=f"clr-{kiedy.timestamp()}-{ile}@x.test", name="Clear Test",
                source=source, status="new")
    s.add(lead)
    s.flush()
    zd = []
    for n in range(ile):
        e = LeadEvent(lead_id=lead.id, kind="status", detail=f"s{n}", actor="t",
                      created_at=kiedy - timedelta(seconds=ile - n))
        s.add(e)
        zd.append(e)
    s.commit()
    lid, ids = lead.id, [f"lead:{e.id}" for e in zd]
    s.close()
    return lid, ids


def _widoczne(h: dict, lead_id: int) -> list[str]:
    items = client.get("/api/admin/inbox", headers=h).json()["items"]
    return [i["id"] for i in items if i.get("lead_id") == lead_id]


def test_usuniete_nie_zjadaja_limitu():
    h, _ = _admin("clr-limit@test")
    lid, ids = _lead(80)
    najnowsze = ids[-45:]
    r = client.post("/api/admin/inbox/mark", headers=h, json={"ids": najnowsze, "hidden": True})
    assert r.status_code == 200
    widac = _widoczne(h, lid)
    # budżet desku to 30 — cały wypełniony tym, co NIE jest usunięte
    assert len(widac) == 30 and not set(widac) & set(najnowsze)


def test_delete_all_czysci_kategorie_do_granicy():
    h, _ = _admin("clr-all@test")
    teraz = datetime.now(timezone.utc)
    # desk „free" ma własny budżet 30 pozycji — zdarzenia z innych testów go nie zapchają
    lid, ids = _lead(5, teraz - timedelta(minutes=5), source="free")
    assert len(_widoczne(h, lid)) == 5
    # granica = najnowsza widziana pozycja; zdarzenie, które przyszło później
    # (w oknie Undo), zostaje
    s = SessionLocal()
    ostatnie = s.get(LeadEvent, int(ids[-1].split(":")[1])).created_at
    pozne = LeadEvent(lead_id=lid, kind="note", detail="late", actor="t",
                      created_at=teraz - timedelta(minutes=1))
    s.add(pozne)
    s.commit()
    pozne_id = f"lead:{pozne.id}"
    s.close()
    r = client.post("/api/admin/inbox/clear", headers=h,
                    json={"cat": "free", "before": ostatnie.isoformat()})
    assert r.status_code == 200
    assert _widoczne(h, lid) == [pozne_id]
    # inne kategorie nie czyszczą tej
    client.post("/api/admin/inbox/clear", headers=h, json={"cat": "prop"})
    client.post("/api/admin/inbox/clear", headers=h, json={"cat": "leads"})
    assert _widoczne(h, lid) == [pozne_id]
    # „all" bez granicy = do teraz
    client.post("/api/admin/inbox/clear", headers=h, json={"cat": "all"})
    assert _widoczne(h, lid) == []
    # nowe zdarzenie po wyczyszczeniu się pokazuje
    s = SessionLocal()
    nowe = LeadEvent(lead_id=lid, kind="note", detail="new", actor="t",
                     created_at=datetime.now(timezone.utc) + timedelta(seconds=2))
    s.add(nowe)
    s.commit()
    nowe_id = f"lead:{nowe.id}"
    s.close()
    assert _widoczne(h, lid) == [nowe_id]


def test_granica_sie_nie_cofa_i_przezywa_przycinanie(monkeypatch):
    h, kto = _admin("clr-trim@test")
    client.post("/api/admin/inbox/clear", headers=h, json={"cat": "prop"})
    client.post("/api/admin/inbox/clear", headers=h,
                json={"cat": "prop", "before": "2020-01-01T00:00:00"})
    monkeypatch.setattr(main, "INBOX_MARKI_LIMIT", 2)
    _, ids = _lead(5)
    client.post("/api/admin/inbox/mark", headers=h, json={"ids": ids, "hidden": True})
    s = SessionLocal()
    m = (s.query(AdminInboxMark)
         .filter(AdminInboxMark.admin_id == kto, AdminInboxMark.item_id == "clear:prop").one())
    assert m.updated_at.year >= 2026
    s.close()


def test_zla_kategoria_odrzucona():
    h, _ = _admin("clr-bad@test")
    assert client.post("/api/admin/inbox/clear", headers=h, json={"cat": "x"}).status_code == 422
