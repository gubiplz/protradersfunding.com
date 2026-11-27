"""Odpowiedź z panelu na odebrany mail ląduje u klienta w TYM SAMYM wątku.

Reply w zakładce Mail → Received wysyła Message-ID oryginału; obie drogi
wysyłki (platforma przez SMTP, landing przez Resend) dopisują z niego
`In-Reply-To` i `References`. Śmieci w polach nie trafiają do nagłówków.
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

from app import lead_mail, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import _WatekIn, _naglowki_watku, app  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
MID = "<abc-123@email.amazonses.com>"


@pytest.fixture
def poczta(monkeypatch):
    wyslane = []

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): wyslane.append(msg)

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.atrapa")
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    return wyslane


def test_naglowki_tylko_z_poprawnych_message_id():
    assert _naglowki_watku(_WatekIn()) == {}
    assert _naglowki_watku(_WatekIn(in_reply_to="nie-id")) == {}
    assert _naglowki_watku(_WatekIn(in_reply_to="<a@b>\r\nBcc: x@y.z")) == {}
    h = _naglowki_watku(_WatekIn(in_reply_to=MID, references="<r1@x> smieci <r2@x> " + MID))
    assert h == {"In-Reply-To": MID, "References": f"<r1@x> <r2@x> {MID}"}


def test_reply_z_platformy_ma_naglowki_watku(poczta):
    adres = f"klient{uuid.uuid4().hex[:6]}@test.pl"
    r = client.post("/api/admin/mail/send", headers=ADMIN_H,
                    json={"to": adres, "subject": "Re: Pomoc", "body": "Odpowiedź",
                          "sender": "ptf", "in_reply_to": MID, "references": "<r1@x>"})
    assert r.status_code == 200, r.text
    (msg,) = poczta
    assert msg["In-Reply-To"] == MID and msg["References"] == f"<r1@x> {MID}"


def test_mail_bez_reply_nie_ma_naglowkow(poczta):
    r = client.post("/api/admin/mail/send", headers=ADMIN_H,
                    json={"to": f"k{uuid.uuid4().hex[:6]}@test.pl", "subject": "Hej",
                          "body": "Treść", "sender": "ptf"})
    assert r.status_code == 200, r.text
    assert poczta[0]["In-Reply-To"] is None


def test_landing_przez_resend_dostaje_headers(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "lead_mail_from", "Desk <contact@biurko-test.com>", raising=False)
    monkeypatch.setattr(s, "resend_api_key", "re_test", raising=False)
    zlapane = []
    ok, _ = lead_mail.wyslij("k@test.pl", "Re: x", "treść", tylko_nadawca=True,
                             transport=zlapane.append,
                             naglowki={"In-Reply-To": MID, "References": MID})
    assert ok and zlapane[0]["In-Reply-To"] == MID

    wolania = []

    class _Odp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def urlopen(req, timeout=None):
        import json
        wolania.append(json.loads(req.data))
        return _Odp()

    monkeypatch.setattr(lead_mail.urllib.request, "urlopen", urlopen)
    lead_mail._resend_transport(zlapane[0])
    assert wolania[0]["headers"] == {"In-Reply-To": MID, "References": MID}
