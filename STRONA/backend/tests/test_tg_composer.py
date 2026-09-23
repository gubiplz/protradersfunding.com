"""Wiadomość na Telegram z konta ADMINA — okno w Clients i przycisk „Message" w Leads.

Panel nie wysyła nic sam: otwiera czat `t.me/<handle>?text=…` w aplikacji
admina, a „wyślij" naciska człowiek z konta, na którym jest zalogowany.
Automat piszący z konta użytkownika to dokładnie to, za co Telegram zamraża
konta — poprzednie konto admina tak padło. Testy pilnują: szablonów TG
z wariantami (żeby dziesięć DM-ów nie było identycznych), śladu w historii
leada po otwarciu czatu i tego, że guzik siedzi w Clients, a nie w Leads.
"""
import json
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, mail_templates  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, LeadEvent, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))


def _trader(email=None, name="Ada Obi"):
    s = SessionLocal()
    tr = Trader(email=email or f"tg{next(LICZNIK)}@test.pl",
                password_hash=auth.hash_password("haslo12345"), full_name=name,
                referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit(); dane = (tr.id, tr.email); s.close()
    return dane


def _lead(email, telegram=None, status="new"):
    s = SessionLocal()
    lead = Lead(email=email, name="Ada Obi", source="free", outcome="qualified",
                status=status, telegram=telegram)
    s.add(lead); s.commit(); lid = lead.id; s.close()
    return lid


# --- szablony ------------------------------------------------------------------------

def test_szablony_tg_maja_warianty_i_imie():
    lista = mail_templates.lista_tg()
    assert len(lista) >= 5
    for t in lista:
        assert t["sender"] == "tg" and t["builtin"] and str(t["id"]).startswith("b:tg-")
        assert t["body"] == t["variants"][0]
        assert all("{name}" in w for w in t["variants"])
        assert all("http" not in w for w in t["variants"]), "DM bez linkow"
    assert any(len(t["variants"]) >= 3 for t in lista)


def test_szablony_tg_sa_w_liscie_ale_poza_selektorem_maila():
    lista = client.get("/api/admin/email-templates", headers=ADMIN).json()
    tg = [t for t in lista if t["sender"] == "tg"]
    assert tg and all(t["builtin"] for t in tg)
    assert all("variants" in t for t in tg)


def test_wlasny_szablon_tg_zapisuje_sie_z_nadawca_tg():
    r = client.post("/api/admin/email-templates", headers=ADMIN,
                    json={"name": "Moje TG", "subject": "(telegram)",
                          "body": "Hey {name}, quick one.", "sender": "tg"})
    assert r.status_code == 200, r.text
    assert r.json()["sender"] == "tg" and r.json()["builtin"] is False
    client.delete(f"/api/admin/email-templates/{r.json()['id']}", headers=ADMIN)


# --- slad po wyslaniu ------------------------------------------------------------------

def test_slad_w_historii_leada_i_status_messaged():
    tid, email = _trader()
    lid = _lead(email.upper(), telegram=None)
    r = client.post(f"/api/admin/traders/{tid}/telegram-note", headers=ADMIN,
                    json={"text": "Hey Ada, this is the desk.", "handle": "@ada_obi"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "lead_id": lid, "handle": "ada_obi"}
    s = SessionLocal()
    try:
        zd = s.query(LeadEvent).filter(LeadEvent.lead_id == lid, LeadEvent.kind == "telegram").one()
        assert zd.detail.startswith("@ada_obi: Hey Ada")
        assert json.loads(zd.payload_json) == {"body": "Hey Ada, this is the desk.", "handle": "ada_obi"}
        lead = s.get(Lead, lid)
        assert lead.status == "messaged"
        assert lead.telegram == "ada_obi", "uchwyt wpisany w oknie uzupelnia leada"
    finally:
        s.close()


def test_slad_bez_leada_tylko_w_telemetrii():
    tid, _ = _trader()
    r = client.post(f"/api/admin/traders/{tid}/telegram-note", headers=ADMIN,
                    json={"text": "Hey there."})
    assert r.status_code == 200 and r.json()["lead_id"] is None


def test_pusty_tekst_to_400():
    tid, _ = _trader()
    assert client.post(f"/api/admin/traders/{tid}/telegram-note", headers=ADMIN,
                       json={"text": "   "}).status_code == 400
    assert client.post("/api/admin/traders/999999/telegram-note", headers=ADMIN,
                       json={"text": "x"}).status_code == 404


# --- panel -----------------------------------------------------------------------------

def test_panel_ma_okno_tg_w_clients_a_w_leads_osobny_przycisk():
    kod = client.get("/static/js/admin-panel.js").text
    assert "async function openTgComposer(id,opts)" in kod
    assert 'onclick="openTgComposer(${t.id})"' in kod          # wiersz klienta
    assert "'/api/admin/traders/'+c.trader.id+'/telegram-note'" in kod
    assert "'/api/admin/leads/'+c.lead.id+'/telegram-note'" in kod
    assert "https://t.me/'+encodeURIComponent(h)+'?text='" in kod
    assert "Another wording" in kod and "function tgShuffle()" in kod
    # Leads: szybkie przyciski karty zostają; okno z szablonami otwiera
    # OSOBNY przycisk „Message", nie render listy wprost.
    poczatek = kod.index("function renderLeads(")
    koniec = kod.index("function ", poczatek + 20)
    assert "openTgComposer" not in kod[poczatek:koniec]
    assert 'onclick="openLeadMessage(${l.id})"' in kod
