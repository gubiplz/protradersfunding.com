"""Czat pomocy: asystent AI na FAQ, a sprawy o pieniądzach i wynikach — człowiek.

Model w testach jest podmieniony (`helpdesk._przez_sdk` + `dostawca`), więc
sprawdzamy to, co jest w KODZIE: bramkę „tylko człowiek", przekazanie do
zespołu, ukrycie rozmów asystenta przed adminem, notatkę widoczną tylko dla
zespołu i to, że błąd modelu nie zostawia klienta bez odpowiedzi.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, helpdesk  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import TEMPLATES, app  # noqa: E402
from app.models import SupportTicket, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))


@pytest.fixture
def klient():
    numer = next(LICZNIK)
    s = SessionLocal()
    tr = Trader(email=f"czat{numer}@test.pl", password_hash=auth.hash_password("haslo1234"),
                full_name="Czat Tester", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    yield {"Authorization": f"Bearer {auth.make_token(tid)}"}, tid


@pytest.fixture
def model(monkeypatch):
    """Podmieniony model: zwraca to, co test ustawi w `odp`, i liczy wywołania."""
    stan = {"odp": None, "wywolania": 0}

    def udawany(system, msgs):
        stan["wywolania"] += 1
        stan["system"], stan["msgs"] = system, msgs
        if isinstance(stan["odp"], Exception):
            raise stan["odp"]
        return stan["odp"]

    monkeypatch.setattr(helpdesk, "dostawca", lambda: "sdk")
    monkeypatch.setattr(helpdesk, "_przez_sdk", udawany)
    return stan


def _odp(reply="Open the MT5 app and log in with the credentials from your dashboard.",
         resolution="answered", category="platform", subject="MT5 login", summary=""):
    return {"reply": reply, "resolution": resolution, "category": category,
            "subject": subject, "summary": summary}


def _wyslij(h, tekst):
    r = client.post("/api/me/chat", headers=h, json={"message": tekst})
    assert r.status_code == 200, r.text
    return r.json()["chat"]


def _admin_ids():
    return {t["id"] for t in client.get("/api/admin/tickets", headers=ADMIN).json()}


def test_asystent_odpowiada_a_admin_nie_widzi_rozmowy(klient, model):
    h, _ = klient
    model["odp"] = _odp()
    chat = _wyslij(h, "How do I log in to MT5?")
    assert chat["mode"] == "assistant" and chat["status"] == "bot"
    assert [m["author"] for m in chat["thread"]] == ["trader", "ai"]
    assert "MT5" in chat["thread"][-1]["body"]
    assert chat["id"] not in _admin_ids()


def test_nierozwiazane_idzie_do_admina_ze_streszczeniem(klient, model):
    h, _ = klient
    model["odp"] = _odp(reply="I've passed this on.", resolution="needs_human", category="other",
                        subject="Charts not loading", summary="Charts stay blank after login on Safari.")
    chat = _wyslij(h, "My charts do not load at all")
    assert chat["mode"] == "team" and chat["status"] == "open"
    assert chat["id"] in _admin_ids()
    # Notatka ze streszczeniem jest dla zespołu, nie dla klienta.
    assert all(m["author"] != "ai_note" for m in chat["thread"])
    pelny = client.get(f"/api/admin/tickets/{chat['id']}", headers=ADMIN).json()
    notatki = [m["body"] for m in pelny["thread"] if m["author"] == "ai_note"]
    assert notatki and "Charts stay blank" in notatki[0]
    assert pelny["subject"] == "Charts not loading"


@pytest.mark.parametrize("tekst", [
    "Where is my payout?", "I want a refund", "Why did my account fail?",
    "This is a scam", "Can I talk to a human?",
])
def test_pieniadze_i_wyniki_ida_do_czlowieka_bez_modelu(klient, model, tekst):
    h, _ = klient
    model["odp"] = _odp()
    chat = _wyslij(h, tekst)
    assert model["wywolania"] == 0, "takie tematy nie trafiają do modelu"
    assert chat["status"] == "open"
    assert chat["thread"][-1]["body"] == helpdesk.PRZEKAZANIE


def test_kategoria_pieniedzy_przekazuje_nawet_gdy_model_odpowiedzial(klient, model):
    """Model „odpowiedział" na temat zastrzeżony dla zespołu — klient tej odpowiedzi nie dostaje."""
    h, _ = klient
    model["odp"] = _odp(reply="Your money arrives tomorrow.", category="billing")
    chat = _wyslij(h, "Question about the invoice layout")
    assert chat["status"] == "open"
    assert chat["thread"][-1]["body"] == helpdesk.PRZEKAZANIE
    assert "tomorrow" not in str(chat["thread"])


@pytest.mark.parametrize("zepsute", [RuntimeError("API down"), {"reply": ""}, "not json"])
def test_blad_modelu_przekazuje_do_zespolu(klient, model, zepsute):
    h, _ = klient
    model["odp"] = zepsute
    chat = _wyslij(h, "How do I change my email address?")
    assert chat["status"] == "open"
    assert chat["thread"][-1]["body"] == helpdesk.BEZ_ASYSTENTA


def test_po_przekazaniu_asystent_milknie(klient, model):
    h, _ = klient
    model["odp"] = _odp(resolution="needs_human", category="other")
    _wyslij(h, "Something odd with the leaderboard")
    model["wywolania"] = 0
    chat = _wyslij(h, "Any news?")
    assert model["wywolania"] == 0
    assert chat["thread"][-1]["author"] == "trader" and chat["status"] == "open"


def test_przycisk_czlowiek_i_nowa_rozmowa(klient, model):
    h, _ = klient
    model["odp"] = _odp()
    pierwszy = _wyslij(h, "How do I reset my MT5 password?")
    r = client.post("/api/me/chat/human", headers=h).json()["chat"]
    assert r["status"] == "open" and r["id"] == pierwszy["id"]
    # Sprawa czekająca na zespół nie znika po „nowej rozmowie".
    client.post("/api/me/chat/new", headers=h)
    assert client.get("/api/me/chat", headers=h).json()["chat"]["id"] == pierwszy["id"]
    # Po odpowiedzi admina nowa rozmowa zamyka starą.
    client.post(f"/api/admin/tickets/{pierwszy['id']}/reply", headers=ADMIN, json={"message": "Done."})
    client.post("/api/me/chat/new", headers=h)
    assert client.get("/api/me/chat", headers=h).json()["chat"] is None


def test_bez_dostawcy_od_razu_czlowiek(klient, monkeypatch):
    monkeypatch.setattr(helpdesk, "dostawca", lambda: None)
    h, _ = klient
    chat = _wyslij(h, "How do I install MT5 on Android?")
    assert chat["status"] == "open"
    assert client.get("/api/me/chat", headers=h).json()["assistant"] is False


def test_cudzy_czat_niewidoczny(klient, model):
    h, _ = klient
    model["odp"] = _odp()
    mine = _wyslij(h, "How do I log in to MT5?")
    s = SessionLocal()
    obcy = Trader(email="obcy-czat@test.pl", password_hash="x", full_name="Obcy", referral_code="OBCYCZ")
    s.add(obcy); s.commit()
    oid = obcy.id
    s.close()
    inny = client.get("/api/me/chat", headers={"Authorization": f"Bearer {auth.make_token(oid)}"}).json()
    assert inny["chat"] is None or inny["chat"]["id"] != mine["id"]


def test_wiedza_asystenta_bez_obietnic_wyplat():
    """FAQ dla asystenta: bez pytań o wypłaty/zwroty i bez zdań o nich w innych odpowiedziach."""
    wpisy = helpdesk.faq_wpisy(TEMPLATES / "faq.html", {"site_name": "PTF", "support_email": "s@x"})
    assert len(wpisy) >= 10
    tekst = " ".join(q + " " + a for q, a in wpisy).lower()
    for slowo in ("payout", "refund", "fee back"):
        assert slowo not in tekst
    s = SessionLocal()
    try:
        from app.models import Product
        plany = helpdesk.zasady_planow(s.query(Product).all())
    finally:
        s.close()
    assert "split" not in plany.lower()


def test_konsultant_przejmuje_i_online_z_aktywnosci_panelu(klient, model, monkeypatch):
    """Nagłówek czatu: „taken_over" dopiero gdy admin odpisze; „online" tylko gdy
    panel był aktywny w ostatnich 5 minutach — nie na stałe."""
    import time as _time
    from app import main as m
    h, _ = klient
    model["odp"] = _odp(resolution="needs_human", category="other")
    chat = _wyslij(h, "The leaderboard does not show my name")
    assert chat["taken_over"] is False
    client.post(f"/api/admin/tickets/{chat['id']}/reply", headers=ADMIN, json={"message": "Fixed, thanks!"})
    r = client.get("/api/me/chat", headers=h).json()
    assert r["chat"]["taken_over"] is True
    assert r["agent"]["name"] == m.SUPPORT_AGENT_NAME and r["agent"]["online"] is True
    # Sześć minut później, bez aktywności panelu — już nie online.
    teraz = _time.time()
    monkeypatch.setattr(m.time, "time", lambda: teraz + 6 * 60)
    assert client.get("/api/me/chat", headers=h).json()["agent"]["online"] is False


def test_cli_tylko_lokalnie(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("HELPDESK_LOCAL_CLI", "1")
    monkeypatch.setenv("VERCEL", "1")
    assert helpdesk.dostawca() is None
