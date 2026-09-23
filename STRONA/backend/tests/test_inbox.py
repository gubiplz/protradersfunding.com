"""Maile odebrane w zakładce Mail — z Resend Receiving, z podziałem na marki.

Marka to domena ODBIORCY wzięta z ustawień (platforma: SUPPORT_EMAIL /
MAIL_FROM, landing: nadawca lead_mail). API Resenda jest podstawione, więc
test nie wychodzi do sieci; sprawdza podział, dopasowanie nadawcy do klienta
i leada, błędy bez wywracania listy i to, że panel ma przełączniki.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, inbox  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
PTF_DOM = "platforma-test.com"
FX_DOM = "biurko-test.com"


@pytest.fixture
def resend(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "resend_api_key", "re_test", raising=False)
    monkeypatch.setattr(s, "resend_api_key_ptf", "", raising=False)
    monkeypatch.setattr(s, "support_email", f"support@{PTF_DOM}", raising=False)
    monkeypatch.setattr(s, "mail_from", f"no-reply@{PTF_DOM}", raising=False)
    monkeypatch.setattr(s, "lead_mail_from", f"Desk <contact@{FX_DOM}>", raising=False)
    wolania = []
    odpowiedzi = {}

    def falszywe(sciezka, klucz):
        wolania.append((sciezka, klucz))
        wynik = odpowiedzi.get(sciezka)
        if isinstance(wynik, Exception):
            raise wynik
        return wynik or {"data": []}
    monkeypatch.setattr(inbox, "_http_get", falszywe)
    return odpowiedzi, wolania


def _lista(*maile):
    return {"object": "list", "has_more": False, "data": list(maile)}


def test_podzial_na_marki_po_domenie_odbiorcy(resend):
    odp, wolania = resend
    odp["/emails/receiving?limit=100"] = _lista(
        {"id": "a1", "to": [f"support@{PTF_DOM}"], "from": "Jan <jan@x.pl>",
         "subject": "Payout?", "created_at": "2026-09-23T10:00:00Z", "attachments": []},
        {"id": "b2", "to": [f"contact@{FX_DOM}"], "from": "ada@y.com",
         "subject": "Hello", "created_at": "2026-09-23T11:00:00Z",
         "attachments": [{"id": "z", "filename": "id.pdf"}]},
    )
    wszystko = client.get("/api/admin/mail/inbox", headers=ADMIN).json()
    assert [m["id"] for m in wszystko["items"]] == ["b2", "a1"]      # najnowsze pierwsze
    assert {m["id"]: m["brand"] for m in wszystko["items"]} == {"a1": "ptf", "b2": "fx"}
    assert wszystko["items"][0]["attachments"] == 1
    assert wszystko["domains"] == {"ptf": [PTF_DOM], "fx": [FX_DOM]}
    fx = client.get("/api/admin/mail/inbox?brand=fx", headers=ADMIN).json()
    assert [m["id"] for m in fx["items"]] == ["b2"]
    ptf = client.get("/api/admin/mail/inbox?brand=ptf", headers=ADMIN).json()
    assert [m["id"] for m in ptf["items"]] == ["a1"]
    assert all(k == "re_test" for _s, k in wolania)
    assert client.get("/api/admin/mail/inbox?brand=xyz", headers=ADMIN).status_code == 400


def test_nadawca_dopasowany_do_klienta_i_leada(resend):
    odp, _ = resend
    s = SessionLocal()
    tr = Trader(email="klient.inbox@x.pl", password_hash=auth.hash_password("haslo12345"),
                full_name="Klient Inbox", referral_code=auth.secrets.token_hex(3))
    lead = Lead(email="lead.inbox@x.pl", name="Lead Inbox", source="money", status="new")
    s.add_all([tr, lead]); s.commit(); tid, lid = tr.id, lead.id; s.close()
    odp["/emails/receiving?limit=100"] = _lista(
        {"id": "c1", "to": [f"support@{PTF_DOM}"], "from": "Klient <Klient.Inbox@x.pl>",
         "subject": "Hi", "created_at": "2026-09-23T09:00:00Z"},
        {"id": "c2", "to": [f"contact@{FX_DOM}"], "from": "lead.inbox@x.pl",
         "subject": "Hi", "created_at": "2026-09-23T08:00:00Z"},
    )
    items = {m["id"]: m for m in client.get("/api/admin/mail/inbox", headers=ADMIN).json()["items"]}
    assert items["c1"]["trader_id"] == tid and items["c1"]["trader_name"] == "Klient Inbox"
    assert items["c2"]["lead_id"] == lid and items["c2"]["trader_id"] is None


def test_tresc_jednego_maila(resend):
    odp, _ = resend
    odp["/emails/receiving/b2"] = {"id": "b2", "to": [f"contact@{FX_DOM}"], "from": "ada@y.com",
                                   "subject": "Hello", "text": "Hi there", "html": "<p>Hi</p>",
                                   "created_at": "2026-09-23T11:00:00Z",
                                   "attachments": [{"filename": "id.pdf", "size": 1200}]}
    m = client.get("/api/admin/mail/inbox/b2?k=0", headers=ADMIN).json()
    assert m["brand"] == "fx" and m["text"] == "Hi there" and m["html"] == "<p>Hi</p>"
    assert m["attachments"] == [{"filename": "id.pdf", "size": 1200}]
    odp["/emails/receiving/zly"] = RuntimeError("resend 404: Email not found")
    r = client.get("/api/admin/mail/inbox/zly", headers=ADMIN)
    assert r.status_code == 502 and "not found" in r.json()["detail"]


def test_blad_resenda_nie_wywraca_listy(resend):
    odp, _ = resend
    odp["/emails/receiving?limit=100"] = RuntimeError("resend 401: API key is invalid")
    d = client.get("/api/admin/mail/inbox", headers=ADMIN).json()
    assert d["items"] == [] and d["errors"] == ["resend 401: API key is invalid"]
    assert d["configured"] is True


def test_drugi_klucz_dla_platformy_czytany_osobno(resend, monkeypatch):
    odp, wolania = resend
    monkeypatch.setattr(get_settings(), "resend_api_key_ptf", "re_ptf", raising=False)
    odp["/emails/receiving?limit=100"] = _lista(
        {"id": "d1", "to": [f"support@{PTF_DOM}"], "from": "a@b.c", "created_at": "2026-09-23"})
    d = client.get("/api/admin/mail/inbox", headers=ADMIN).json()
    assert {k for _s, k in wolania} == {"re_test", "re_ptf"}
    assert [m["id"] for m in d["items"]] == ["d1"]                    # ten sam id raz


def test_bez_klucza_mowi_ze_nie_podlaczone(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", "", raising=False)
    monkeypatch.setattr(get_settings(), "resend_api_key_ptf", "", raising=False)
    d = client.get("/api/admin/mail/inbox", headers=ADMIN).json()
    assert d["configured"] is False and d["items"] == []


def test_wyslane_z_resenda_ze_statusem_i_odbiorca(resend):
    odp, _ = resend
    s = SessionLocal()
    lead = Lead(email="wyslany.lead@x.pl", name="Wyslany", source="money", status="new")
    s.add(lead); s.commit(); lid = lead.id; s.close()
    odp["/emails?limit=100"] = _lista(
        {"id": "s1", "to": ["wyslany.lead@x.pl"], "from": f"Desk <contact@{FX_DOM}>",
         "subject": "Your account", "created_at": "2026-09-23T12:00:00Z", "last_event": "delivered"},
        {"id": "s2", "to": ["ktos@y.com"], "from": f"no-reply@{PTF_DOM}",
         "subject": "Reset", "created_at": "2026-09-23T13:00:00Z", "last_event": "bounced"},
    )
    d = client.get("/api/admin/mail/sent", headers=ADMIN).json()
    assert [m["id"] for m in d["items"]] == ["s2", "s1"]
    s1 = d["items"][1]
    assert s1["brand"] == "fx" and s1["last_event"] == "delivered" and s1["lead_id"] == lid
    assert d["items"][0]["brand"] == "ptf"                     # marka po NADAWCY
    fx = client.get("/api/admin/mail/sent?brand=fx", headers=ADMIN).json()
    assert [m["id"] for m in fx["items"]] == ["s1"]
    odp["/emails/s1"] = {"id": "s1", "to": ["wyslany.lead@x.pl"], "from": f"contact@{FX_DOM}",
                         "subject": "Your account", "html": "<p>x</p>", "last_event": "opened"}
    m = client.get("/api/admin/mail/sent/s1", headers=ADMIN).json()
    assert m["brand"] == "fx" and m["last_event"] == "opened" and m["lead_id"] == lid


def test_panel_ma_received_sent_z_resenda_i_przelacznik_marki():
    kod = client.get("/static/js/admin-panel.js").text
    assert "function renderMailInbox()" in kod and "if(window._mailBox==='in')return renderMailInbox();" in kod
    assert "'/api/admin/mail/inbox':'/api/admin/mail/sent'" in kod
    assert "[['all','All'],['ptf','PTF'],['fx','Forex Passing']]" in kod
    assert "localStorage.setItem('pf_admin_mailbrand'" in kod
    assert '<iframe sandbox=""' in kod                                 # HTML obcego bez skryptów
    assert "function replyMailItem()" in kod and "from:m.brand||'ptf'" in kod
    assert "const from0=ctx.from||(l?'fx':'ptf');" in kod
    # Kopia z dziennika chowana, gdy Resend oddał listę (jeden wiersz na mail).
    assert "!(rsOk&&m.event==='admin_message_fx')" in kod


def test_panel_nie_ma_dwoch_funkcji_o_tej_samej_nazwie():
    """Funkcje panelu są globalne: druga deklaracja po cichu nadpisuje pierwszą.
    Tak `loadInbox` dzwonka powiadomień zjadł ładowanie maili odebranych —
    zakładka Received kręciła się bez końca."""
    import collections
    import re
    kod = client.get("/static/js/admin-panel.js").text
    nazwy = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", kod, re.M)
    dubel = [n for n, c in collections.Counter(nazwy).items() if c > 1]
    assert dubel == [], dubel
