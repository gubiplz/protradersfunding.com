"""Kopia odebranych maili na MAIL_FORWARD_TO (webhook Resenda email.received).

API Resenda jest podstawione: test sprawdza, że webhook pobiera mail po ID
własnym kluczem, wysyła kopię z Reply-To nadawcy, nie dubluje jej przy
powtórce, a przy błędzie wysyłki zwraca 502 (Resend ponowi).
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import uuid  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import mail_forward  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)


@pytest.fixture
def resend(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "resend_api_key", "re_test", raising=False)
    monkeypatch.setattr(s, "resend_api_key_ptf", "", raising=False)
    monkeypatch.setattr(s, "lead_mail_from", "Desk <contact@biurko-test.com>", raising=False)
    monkeypatch.setattr(s, "mail_forward_to", "kopia@example.com, kopia@example.com", raising=False)
    stan = {"wyslane": [], "blad": None, "zal": []}

    def get(sciezka, klucz):
        assert klucz == "re_test"
        if sciezka.endswith("/attachments"):
            return {"data": stan["zal"]}
        return {"id": sciezka.rsplit("/", 1)[1], "from": "Jan <jan@klient.com>",
                "to": ["support@platforma-test.com"], "subject": "Pomoc",
                "text": "treść", "html": "<p>treść</p>"}

    def post(dane):
        if stan["blad"] and (stan["blad"] == "zawsze" or "attachments" in dane):
            raise RuntimeError("resend 422: nope")
        stan["wyslane"].append(dane)

    monkeypatch.setattr(mail_forward, "_http_get", get)
    monkeypatch.setattr(mail_forward, "_post", post)
    return stan


def _hook(email_id):
    return client.post("/api/mail/inbound",
                       json={"type": "email.received", "data": {"email_id": email_id}})


def test_kopia_z_reply_to_i_bez_duplikatu(resend):
    eid = str(uuid.uuid4())
    r = _hook(eid)
    assert r.status_code == 200 and r.json()["status"] == "forwarded"
    (m,) = resend["wyslane"]
    assert m["to"] == ["kopia@example.com"]
    assert m["reply_to"] == "Jan <jan@klient.com>"
    assert m["from"] == "Inbox <contact@biurko-test.com>"
    assert m["subject"] == "[support@platforma-test.com] Pomoc"
    assert "jan@klient.com" in m["text"] and "<p>treść</p>" in m["html"]
    assert _hook(eid).json()["status"] == "already forwarded"
    assert len(resend["wyslane"]) == 1


def test_zalaczniki_a_gdy_odmowa_to_bez_nich(resend):
    resend["zal"] = [{"filename": "a.pdf", "download_url": "https://x/a.pdf"}]
    resend["blad"] = "tylko_zal"
    assert _hook(str(uuid.uuid4())).status_code == 200
    (m,) = resend["wyslane"]
    assert "attachments" not in m and "a.pdf" in m["text"]


def test_blad_wysylki_502_i_ponowienie_dziala(resend):
    eid = str(uuid.uuid4())
    resend["blad"] = "zawsze"
    assert _hook(eid).status_code == 502
    resend["blad"] = None
    assert _hook(eid).json()["status"] == "forwarded"


def test_inne_zdarzenia_i_wylaczone(resend, monkeypatch):
    assert client.post("/api/mail/inbound", json={"type": "email.sent"}).json() == {"ok": True}
    monkeypatch.setattr(get_settings(), "mail_forward_to", "", raising=False)
    assert _hook(str(uuid.uuid4())).json()["status"] == "forwarding off"
    assert resend["wyslane"] == []
