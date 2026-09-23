"""Opublikowany post w kolejce: link do posta, edycja i kasowanie PRZEZ Telegram.

Do 2026-09 „Preview" opublikowanego posta pokazywało makietę zamiast prowadzić
na kanał, kanał prywatny nie dostawał linku wcale, edycja była zablokowana
(409), a Delete kasował sam wiersz — post wisiał na kanale dalej, a panel
twierdził, że go nie ma. Teraz: `post_url` ma wariant `t.me/c/…`, PATCH tekstu
idzie na Telegram i dopiero po jego zgodzie do bazy, DELETE najpierw kasuje
wiadomość na kanale (z `?force=1` jako świadomym wyjątkiem), a Approve
opublikowanego to 409, żeby cron nie wysłał go drugi raz.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import contentbot, telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ChannelPost  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}


@pytest.fixture(autouse=True)
def czysto():
    s = SessionLocal()
    s.query(ChannelPost).delete()
    s.commit()
    s.close()


@pytest.fixture
def tg(monkeypatch):
    """Atrapa Telegrama: zapisuje wywołania, odpowiada tym, co ustawi test."""
    log = {"edit": [], "delete": [], "ok": True, "reason": ""}
    monkeypatch.setattr(contentbot, "chat_id", lambda kanal: "-1001234567890")

    def edit(chat, mid, text, *, kind="text", token=None, transport=None):
        log["edit"].append((chat, mid, text, kind))
        return log["ok"], log["reason"]

    def delete(chat, mid, *, token=None, transport=None):
        log["delete"].append((chat, mid))
        return log["ok"], log["reason"]
    monkeypatch.setattr(telegram, "edit_content", edit)
    monkeypatch.setattr(telegram, "delete_content", delete)
    return log


def _post(status="published", kind="text", body="Hello <b>world</b>",
          media_url=None, message_id=77, post_url="https://t.me/kanal/77"):
    s = SessionLocal()
    p = ChannelPost(channel="mgmt", kind=kind, body=body, media_url=media_url,
                    status=status, message_id=message_id if status == "published" else None,
                    post_url=post_url if status == "published" else "")
    s.add(p)
    s.commit()
    pid = p.id
    s.close()
    return pid


def _wczytaj(pid):
    s = SessionLocal()
    try:
        return s.get(ChannelPost, pid)
    finally:
        s.close()


def _patch(pid, **zmiany):
    dane = {"channel": "mgmt", "kind": "text", "body": "Hello <b>world</b>",
            "media_url": None, "proof": "", "scheduled_for": None}
    dane.update(zmiany)
    return client.patch(f"/api/admin/channel-posts/{pid}", headers=ADMIN, json=dane)


# --- link do posta ------------------------------------------------------------------

def test_kanal_publiczny_ma_link_z_nazwa():
    assert telegram.post_url({"message_id": 5, "chat": {"username": "kanal"}}) \
        == "https://t.me/kanal/5"


def test_kanal_prywatny_ma_link_dla_czlonkow():
    assert telegram.post_url({"message_id": 5, "chat": {"id": -1001234567890}}) \
        == "https://t.me/c/1234567890/5"


def test_bez_id_wiadomosci_nie_ma_linku():
    assert telegram.post_url({"chat": {"username": "kanal"}}) == ""
    assert telegram.post_url({"message_id": 5, "chat": {"id": 12345}}) == ""


# --- edycja opublikowanego ----------------------------------------------------------

def test_edycja_tekstu_idzie_na_telegram_i_zostaje_published(tg):
    pid = _post()
    r = _patch(pid, body="Hello <b>everyone</b>")
    assert r.status_code == 200, r.text
    assert tg["edit"] == [("-1001234567890", 77, "Hello <b>everyone</b>", "text")]
    p = _wczytaj(pid)
    assert p.status == "published" and p.body == "Hello <b>everyone</b>"
    assert p.message_id == 77 and p.post_url == "https://t.me/kanal/77"


def test_podpis_pod_zdjeciem_edytuje_sie_jako_caption(tg):
    pid = _post(kind="photo", media_url="https://cdn.test/a.png")
    r = _patch(pid, kind="photo", media_url="https://cdn.test/a.png", body="New caption")
    assert r.status_code == 200, r.text
    assert tg["edit"][0][3] == "photo"


def test_ta_sama_tresc_nie_woła_telegrama(tg):
    pid = _post()
    assert _patch(pid).status_code == 200
    assert tg["edit"] == []


def test_zmiana_grafiki_lub_kanalu_opublikowanego_to_409(tg):
    pid = _post()
    assert _patch(pid, media_url="https://cdn.test/nowe.png", kind="photo").status_code == 409
    assert _patch(pid, channel="payouts").status_code == 409
    assert tg["edit"] == [] and _wczytaj(pid).body == "Hello <b>world</b>"


def test_odmowa_telegrama_nie_zmienia_bazy(tg):
    tg["ok"], tg["reason"] = False, "message can't be edited"
    pid = _post()
    r = _patch(pid, body="Changed")
    assert r.status_code == 502 and "message can't be edited" in r.json()["detail"]
    assert _wczytaj(pid).body == "Hello <b>world</b>"


def test_opublikowany_bez_message_id_nie_da_sie_edytowac(tg):
    pid = _post(message_id=None)
    assert _patch(pid, body="Changed").status_code == 409
    assert tg["edit"] == []


def test_edycja_szkicu_dalej_cofa_do_szkicu_bez_telegrama(tg):
    pid = _post(status="draft")
    r = _patch(pid, body="Draft text")
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert tg["edit"] == []


# --- kasowanie ---------------------------------------------------------------------------

def test_delete_opublikowanego_kasuje_z_kanalu_i_z_kolejki(tg):
    pid = _post()
    r = client.delete(f"/api/admin/channel-posts/{pid}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["removed_from_channel"] is True
    assert tg["delete"] == [("-1001234567890", 77)]
    assert _wczytaj(pid) is None


def test_odmowa_telegrama_zostawia_wiersz(tg):
    tg["ok"], tg["reason"] = False, "message to delete not found"
    pid = _post()
    r = client.delete(f"/api/admin/channel-posts/{pid}", headers=ADMIN)
    assert r.status_code == 502 and "Telegram refused" in r.json()["detail"]
    assert _wczytaj(pid) is not None


def test_force_kasuje_sam_wiersz_bez_telegrama(tg):
    tg["ok"] = False
    pid = _post()
    r = client.delete(f"/api/admin/channel-posts/{pid}?force=1", headers=ADMIN)
    assert r.status_code == 200 and r.json()["removed_from_channel"] is False
    assert tg["delete"] == [] and _wczytaj(pid) is None


def test_delete_szkicu_nie_dotyka_telegrama(tg):
    pid = _post(status="draft")
    assert client.delete(f"/api/admin/channel-posts/{pid}", headers=ADMIN).status_code == 200
    assert tg["delete"] == []


# --- approve -------------------------------------------------------------------------------

def test_approve_opublikowanego_to_409(tg):
    pid = _post()
    r = client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)
    assert r.status_code == 409
    assert _wczytaj(pid).status == "published"


# --- panel ---------------------------------------------------------------------------------

def test_panel_preview_prowadzi_do_posta_a_edycja_i_delete_sa_dla_opublikowanych():
    kod = client.get("/static/js/admin-panel.js").text
    assert "window.open(p.post_url,'_blank','noopener')" in kod
    assert "function editPost(id)" in kod and 'onclick="editPost(${p.id})"' in kod
    assert "Save to Telegram" in kod
    assert "?force=1" in kod
    assert "Published posts\n      cannot be edited" not in kod
    # Delete stoi przy KAŻDYM statusie, także published.
    assert kod.count('onclick="deletePost(${p.id})"') == 1
