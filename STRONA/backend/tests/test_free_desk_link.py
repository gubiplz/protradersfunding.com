"""Darmowy lejek ma swój desk — link w SMS-ie, w automacie mailowym i w szablonie.

Do 2026-09-23 wszystko szło na jeden `SMS_TELEGRAM_URL`, więc lead z /freeaccount
dostawał link do PŁATNEGO desku (inne konto, inni ludzie, inny bot). Teraz
`FREE_TELEGRAM_URL` obsługuje `source=free*`, a bez niego link spada na płatny —
brak zmiennej nie może zostawić leada bez żadnego adresu.
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

from app import lead_mail, mail_templates, sms  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
PLATNY = "https://t.me/probe_desk"
DARMOWY = "https://t.me/probe_free"
LICZNIK = iter(range(10000))


@pytest.fixture
def linki(monkeypatch):
    u = get_settings()
    monkeypatch.setattr(u, "sms_telegram_url", PLATNY)
    monkeypatch.setattr(u, "free_telegram_url", DARMOWY)
    monkeypatch.setattr(u, "lead_telegram_channel_url", "https://t.me/probe_channel")
    monkeypatch.setattr(u, "lead_mail_from", "Forex Passing <desk@partner.test>")
    monkeypatch.setattr(u, "smtp_host", "smtp.probe.test")
    return u


def _lead(source):
    s = SessionLocal()
    lead = Lead(email=f"desk{next(LICZNIK)}@probe.test", name="Ada", source=source,
                outcome="qualified", status="new")
    s.add(lead)
    s.commit()
    lid = lead.id
    s.close()
    return lid


def test_sms_do_darmowego_leada_prowadzi_na_darmowy_desk(linki):
    assert DARMOWY in sms.tresc("Ada", zakwalifikowany=True, free=True)
    assert PLATNY in sms.tresc("Ada", zakwalifikowany=True)
    assert PLATNY in sms.tresc("Ada", zakwalifikowany=True, free=False)


def test_bez_free_telegram_url_darmowy_spada_na_platny(linki, monkeypatch):
    monkeypatch.setattr(linki, "free_telegram_url", "")
    assert PLATNY in sms.tresc("Ada", zakwalifikowany=True, free=True)
    _, tekst = lead_mail.tresc("Ada", zakwalifikowany=True, free=True)
    assert PLATNY in tekst


def test_automat_mailowy_zakwalifikowanego_z_free_idzie_na_darmowy_desk(linki):
    _, darmowy = lead_mail.tresc("Ada", zakwalifikowany=True, free=True)
    _, platny = lead_mail.tresc("Ada", zakwalifikowany=True)
    assert DARMOWY in darmowy and PLATNY not in darmowy
    assert PLATNY in platny and DARMOWY not in platny
    # Odrzucony idzie na KANAŁ niezależnie od lejka — tam się tylko dołącza.
    _, odrzucony = lead_mail.tresc("Ada", zakwalifikowany=False, free=True)
    assert "probe_channel" in odrzucony and DARMOWY not in odrzucony


def test_karta_leada_pokazuje_link_wlasciwego_desku(linki):
    darmowy, platny = _lead("freeaccount"), _lead("money")
    dane = {x["id"]: x for x in client.get("/api/admin/leads", headers=ADMIN).json()}
    assert DARMOWY in dane[darmowy]["sms_text"] and DARMOWY in dane[darmowy]["mail_text"]
    assert PLATNY in dane[platny]["sms_text"] and PLATNY in dane[platny]["mail_text"]


def test_szablon_darmowego_konta_ma_darmowy_desk_a_reszta_platny(linki):
    lista = {t["id"]: t for t in mail_templates.lista()}
    assert DARMOWY in lista["b:fx-free-ready"]["body"]
    assert PLATNY not in lista["b:fx-free-ready"]["body"]
    assert PLATNY in lista["b:fx-accepted"]["body"]
    assert all("{free_telegram_url}" not in t["body"] and "{telegram_url}" not in t["body"]
               for t in lista.values())
