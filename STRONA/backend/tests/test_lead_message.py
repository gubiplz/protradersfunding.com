"""Przycisk „Message" w Leads — szablony Telegram / e-mail bez ruszania statusu.

Status zmienia się TYLKO przy pierwszym kontakcie z leadem `new`, którego
nikt nie wziął: wtedy `messaged` i powiadomienie dla działu. Każda kolejna
wiadomość (lead przejęty albo już ruszony) zostaje wyłącznie w historii.
Stara droga (ołówek maila bez `mark`) działa jak dotąd.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import lead_mail, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, LeadEvent  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(100000))


@pytest.fixture
def powiadomienia(monkeypatch):
    wyslane = []
    monkeypatch.setattr(notify, "notify_admins",
                        lambda event, title, body="", **kw: wyslane.append((event, title, body)))
    return wyslane


@pytest.fixture
def poczta(monkeypatch):
    for pole, wartosc in (("smtp_host", "smtp.probe.test"),
                          ("lead_mail_from", "Desk <desk@probe.test>"),
                          ("resend_api_key", "")):
        monkeypatch.setattr(lead_mail.settings, pole, wartosc)
    poszly = []
    monkeypatch.setattr(lead_mail, "_smtp_transport", poszly.append)
    return poszly


def _lead(*, status="new", owner=None, telegram=None):
    s = SessionLocal()
    lead = Lead(email=f"msg{next(LICZNIK)}@probe.test", name="Ada Obi", outcome="qualified",
                status=status, owner=owner, telegram=telegram, source="money")
    s.add(lead); s.commit(); lid = lead.id; s.close()
    return lid


def _stan(lid):
    s = SessionLocal()
    lead = s.get(Lead, lid)
    wynik = (lead.status, lead.owner, lead.telegram,
             [e.kind for e in s.query(LeadEvent).filter(LeadEvent.lead_id == lid)
              .order_by(LeadEvent.id)])
    s.close()
    return wynik


def _tg(lid, text="Hey Ada, quick one", handle="@ada_obi"):
    return client.post(f"/api/admin/leads/{lid}/telegram-note", headers=ADMIN,
                       json={"text": text, "handle": handle})


def test_pierwszy_kontakt_z_niczyim_leadem_oznacza_messaged_i_powiadamia(powiadomienia):
    lid = _lead()
    r = _tg(lid)
    assert r.status_code == 200 and r.json()["marked"] is True and r.json()["status"] == "messaged"
    status, owner, telegram, zdarzenia = _stan(lid)
    assert status == "messaged" and owner is None and telegram == "ada_obi"
    assert "telegram" in zdarzenia and "status" in zdarzenia
    assert [t for _e, t, _b in powiadomienia] == ["Panel: marked messaged"]
    # Druga wiadomość: tylko historia, bez drugiego powiadomienia.
    r2 = _tg(lid, "Hey Ada, following up")
    assert r2.json()["marked"] is False
    assert _stan(lid)[3].count("telegram") == 2 and len(powiadomienia) == 1


def test_przejety_lead_nie_zmienia_statusu(powiadomienia):
    lid = _lead(owner="Kasia")
    r = _tg(lid)
    assert r.json()["marked"] is False and r.json()["status"] == "new"
    status, owner, _t, zdarzenia = _stan(lid)
    assert status == "new" and owner == "Kasia" and zdarzenia == ["telegram"]
    assert powiadomienia == []


def test_lead_juz_ruszony_zostaje_na_swoim_statusie(powiadomienia):
    lid = _lead(status="replied")
    assert _tg(lid).json()["marked"] is False
    assert _stan(lid)[0] == "replied" and powiadomienia == []


def test_telegram_note_waliduje(powiadomienia):
    assert _tg(_lead(), text="   ").status_code == 400
    assert _tg(999999).status_code == 404


def test_mail_z_przycisku_message_tylko_pierwszy_kontakt(poczta, powiadomienia):
    przejety = _lead(owner="Kasia")
    r = client.post(f"/api/admin/leads/{przejety}/email-custom", headers=ADMIN,
                    json={"subject": "Hello", "body": "Hi Ada", "sender": "fx", "mark": "first"})
    assert r.status_code == 200, r.text
    assert r.json()["marked"] is False and _stan(przejety)[0] == "new" and len(poczta) == 1
    niczyj = _lead()
    r = client.post(f"/api/admin/leads/{niczyj}/email-custom", headers=ADMIN,
                    json={"subject": "Hello", "body": "Hi Ada", "sender": "fx", "mark": "first"})
    assert r.json()["marked"] is True and _stan(niczyj)[0] == "messaged"
    assert [t for _e, t, _b in powiadomienia] == ["Panel: marked messaged"]


def test_olowek_bez_mark_dziala_jak_dotad(poczta, powiadomienia):
    lid = _lead(owner="Kasia")
    r = client.post(f"/api/admin/leads/{lid}/email-custom", headers=ADMIN,
                    json={"subject": "Hello", "body": "Hi Ada", "sender": "fx"})
    assert r.status_code == 200 and _stan(lid)[0] == "messaged"


def test_panel_ma_przycisk_message_w_leads():
    kod = client.get("/static/js/admin-panel.js").text
    assert "function openLeadMessage(id)" in kod and "function leadMsgEmail(id)" in kod
    assert "openMailComposer({lead:l,mark:'first'})" in kod
    assert "mark:c.mark||undefined" in kod
    assert "window.open('https://t.me/+'+tel" in kod              # czat po numerze
