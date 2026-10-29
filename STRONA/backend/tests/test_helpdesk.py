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


@pytest.fixture(autouse=True)
def bez_lokalnego_modelu(monkeypatch):
    """Testy nie mogą zależeć od lokalnego .env (np. z kluczem Groq) ani wysyłać
    prawdziwych zapytań do API — test, który potrzebuje API, ustawia je sam."""
    for zm in ("HELPDESK_LLM_URL", "HELPDESK_LLM_MODEL", "HELPDESK_LLM_KEY", "HELPDESK_LOCAL_CLI",
               "HELPDESK_ENGINE", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(zm, raising=False)


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


def _wyslij(h, tekst, chat_id=None):
    r = client.post("/api/me/chat", headers=h, json={"message": tekst, "chat_id": chat_id})
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
    model["odp"] = _odp(reply="Your money arrives tomorrow.", category="payout")
    chat = _wyslij(h, "Question about the invoice layout")
    assert chat["status"] == "open"
    assert chat["thread"][-1]["body"] == helpdesk.PRZEKAZANIE
    assert "tomorrow" not in str(chat["thread"])


def test_pytanie_o_cene_nie_idzie_do_zespolu_a_podwojna_oplata_tak(klient, model):
    """Cena planu sklasyfikowana jako „billing" zostaje u asystenta; „charged twice" — zespół."""
    h, _ = klient
    model["odp"] = _odp(reply="Instant 50K costs $529.", category="billing")
    chat = _wyslij(h, "and the instant one?")
    assert chat["status"] == "bot" and chat["thread"][-1]["body"] == "Instant 50K costs $529."
    model["wywolania"] = 0
    chat = _wyslij(h, "I was charged twice for my challenge")
    assert model["wywolania"] == 0 and chat["status"] == "open"


@pytest.mark.parametrize("zepsute", [RuntimeError("API down"), {"reply": ""}, "not json"])
def test_blad_modelu_zapasowo_silnik_faq(klient, model, zepsute):
    """Model nie odpowiedział — odpowiada silnik FAQ, rozmowa zostaje u asystenta."""
    h, _ = klient
    model["odp"] = zepsute
    chat = _wyslij(h, "How do I change or reset my password?")
    assert chat["status"] == "bot"
    assert "Change Password" in chat["thread"][-1]["body"]


def test_gdy_zawiedzie_model_i_silnik_faq_to_zespol(klient, model, monkeypatch):
    h, _ = klient
    model["odp"] = RuntimeError("API down")
    monkeypatch.setattr(helpdesk, "odpowiedz_z_faq", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    chat = _wyslij(h, "How do I change or reset my password?")
    assert chat["status"] == "open"
    assert chat["thread"][-1]["body"] == helpdesk.BEZ_ASYSTENTA


def test_api_w_formacie_openai(monkeypatch):
    """Ollama / Groq / Gemini / OpenRouter: ten sam format; JSON bywa w ```json```."""
    import httpx
    wyslane = {}

    class _Odp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '```json\n{"reply": "Hi", "resolution": "answered", '
                                             '"category": "how_to", "subject": "Hello", "summary": ""}\n```'}}]}

    def _post(url, headers=None, json=None, timeout=None):
        wyslane.update(url=url, headers=headers, body=json)
        return _Odp()

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HELPDESK_ENGINE", raising=False)
    monkeypatch.setenv("HELPDESK_LLM_URL", "http://127.0.0.1:11434/v1/")
    monkeypatch.setenv("HELPDESK_LLM_MODEL", "llama3.1:8b")
    monkeypatch.setenv("HELPDESK_LLM_KEY", "test-key")
    assert helpdesk.dostawca() == "openai"
    w = helpdesk._przez_openai("SYSTEM", [{"role": "user", "content": "hi"}])
    assert w["reply"] == "Hi" and w["category"] == "how_to"
    assert wyslane["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert wyslane["headers"]["Authorization"] == "Bearer test-key"
    assert wyslane["body"]["model"] == "llama3.1:8b"
    assert wyslane["body"]["messages"][0]["role"] == "system" and "JSON" in wyslane["body"]["messages"][0]["content"]
    # Klucz Claude ma pierwszeństwo przed API w formacie OpenAI.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert helpdesk.dostawca() == "sdk"


def test_czat_przez_api_openai_prompt_kompaktowy(klient, silnik_faq, monkeypatch):
    """Darmowe API (np. Groq 8K tokenów/min): prompt z wiedzą dobraną do pytania
    i kontekstem, dużo mniejszy niż pełne FAQ; odpowiedź modelu trafia do tradera."""
    monkeypatch.setenv("HELPDESK_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("HELPDESK_LLM_KEY", "test-key")
    wyslane = []

    def udawany(system, msgs):
        wyslane.append((system, msgs))
        return {"reply": "1. Open Credentials…", "resolution": "answered", "category": "platform",
                "subject": "MT5 login", "summary": ""}

    monkeypatch.setattr(helpdesk, "_przez_openai", udawany)
    h, _ = klient
    chat = _wyslij(h, "How do I log in to MT5?")
    chat = _wyslij(h, "how to do it on my phone", chat["id"])
    assert chat["thread"][-1]["body"] == "1. Open Credentials…" and chat["title"] == "MT5 login"
    system, msgs = wyslane[-1]
    assert "log in to MT5" in system and "Plan facts" in system
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]   # kontekst rozmowy
    pelny = helpdesk.prompt_systemowy("PTF", "s@x", str(TEMPLATES / "faq.html"), "", None)
    assert len(system) < len(pelny) / 2
    # Cennik z katalogu jest w prompcie (bez niego model odsyłał pytania o cenę do zespołu).
    s = SessionLocal()
    try:
        from app.models import Product
        p = s.query(Product).filter(Product.account_size == 50000, Product.steps >= 2, Product.active).first()
    finally:
        s.close()
    assert f"$50K ${p.price_usd:,.0f}" in system and "Plan prices, fees and rules listed below are public" in system


def test_po_przekazaniu_asystent_milknie(klient, model):
    h, _ = klient
    model["odp"] = _odp(resolution="needs_human", category="other")
    pierwszy = _wyslij(h, "Something odd with the leaderboard")
    model["wywolania"] = 0
    chat = _wyslij(h, "Any news?", pierwszy["id"])
    assert model["wywolania"] == 0
    assert chat["thread"][-1]["author"] == "trader" and chat["status"] == "open"


def test_przycisk_czlowiek_i_lista_rozmow(klient, model):
    h, _ = klient
    model["odp"] = _odp(subject="MT5 password reset")
    pierwszy = _wyslij(h, "How do I reset my MT5 password?")
    # Asystent nadał rozmowie tytuł przy pierwszej odpowiedzi.
    assert pierwszy["title"] == "MT5 password reset"
    r = client.post(f"/api/me/chat/{pierwszy['id']}/human", headers=h).json()["chat"]
    assert r["status"] == "open" and r["id"] == pierwszy["id"]
    # Druga, równoległa rozmowa — bez chat_id powstaje nowa.
    model["odp"] = _odp(subject="KYC documents")
    drugi = _wyslij(h, "Which documents do I need for KYC?")
    assert drugi["id"] != pierwszy["id"]
    lista = client.get("/api/me/chats", headers=h).json()
    assert [c["id"] for c in lista["chats"]][:2] == [drugi["id"], pierwszy["id"]]
    assert lista["chats"][0]["title"] == "KYC documents" and lista["chats"][0]["mode"] == "assistant"
    assert lista["chats"][1]["mode"] == "team"
    assert lista["chats"][0]["last"]["author"] == "ai"
    # Zamknięta przez zespół rozmowa jest tylko do odczytu.
    client.post(f"/api/admin/tickets/{pierwszy['id']}/reply", headers=ADMIN,
                json={"message": "Done.", "close": True})
    r = client.post("/api/me/chat", headers=h, json={"message": "one more thing", "chat_id": pierwszy["id"]})
    assert r.status_code == 400
    assert client.get(f"/api/me/chat/{pierwszy['id']}", headers=h).json()["chat"]["status"] == "closed"


def test_bez_dostawcy_od_razu_czlowiek(klient, monkeypatch):
    monkeypatch.setattr(helpdesk, "dostawca", lambda: None)
    h, _ = klient
    chat = _wyslij(h, "How do I install MT5 on Android?")
    assert chat["status"] == "open"
    assert client.get("/api/me/chats", headers=h).json()["assistant"] is False


def test_cudzy_czat_niewidoczny(klient, model):
    h, _ = klient
    model["odp"] = _odp()
    mine = _wyslij(h, "How do I log in to MT5?")
    s = SessionLocal()
    obcy = Trader(email="obcy-czat@test.pl", password_hash="x", full_name="Obcy", referral_code="OBCYCZ")
    s.add(obcy); s.commit()
    oid = obcy.id
    s.close()
    obcy_h = {"Authorization": f"Bearer {auth.make_token(oid)}"}
    assert client.get(f"/api/me/chat/{mine['id']}", headers=obcy_h).status_code == 404
    assert all(c["id"] != mine["id"] for c in client.get("/api/me/chats", headers=obcy_h).json()["chats"])
    r = client.post("/api/me/chat", headers=obcy_h, json={"message": "hi", "chat_id": mine["id"]})
    assert r.status_code == 404


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
    r = client.get(f"/api/me/chat/{chat['id']}", headers=h).json()
    assert r["chat"]["taken_over"] is True
    assert r["agent"]["name"] == m.SUPPORT_AGENT_NAME and r["agent"]["online"] is True
    # Sześć minut później, bez aktywności panelu — już nie online.
    teraz = _time.time()
    monkeypatch.setattr(m.time, "time", lambda: teraz + 6 * 60)
    assert client.get("/api/me/chats", headers=h).json()["agent"]["online"] is False


def test_pisanie_w_panelu_przejmuje_rozmowe(klient, model, monkeypatch):
    """Konsultant pojawia się u tradera dopiero, gdy zacznie pisać w panelu."""
    import time as _time
    from app import main as m
    h, _ = klient
    model["odp"] = _odp(resolution="needs_human", category="other")
    chat = _wyslij(h, "Charts freeze on the dashboard")
    assert chat["taken_over"] is False and chat["agent_typing"] is False
    assert client.post(f"/api/admin/tickets/{chat['id']}/typing").status_code in (401, 403)
    assert client.post("/api/admin/tickets/999999/typing", headers=ADMIN).status_code == 404
    assert client.post(f"/api/admin/tickets/{chat['id']}/typing", headers=ADMIN).status_code == 200
    r = client.get(f"/api/me/chat/{chat['id']}", headers=h).json()["chat"]
    assert r["taken_over"] is True and r["agent_typing"] is True
    lista = client.get("/api/me/chats", headers=h).json()["chats"]
    assert next(c for c in lista if c["id"] == chat["id"])["taken_over"] is True
    # Po kilku sekundach bez sygnału „pisze" gaśnie, ale przejęcie zostaje.
    teraz = _time.time()
    monkeypatch.setattr(m.time, "time", lambda: teraz + 10)
    r = client.get(f"/api/me/chat/{chat['id']}", headers=h).json()["chat"]
    assert r["taken_over"] is True and r["agent_typing"] is False


def test_chmura_bez_klucza_to_silnik_faq_a_lokalna_ollama_bez_klucza_dziala(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HELPDESK_ENGINE", raising=False)
    monkeypatch.delenv("HELPDESK_LOCAL_CLI", raising=False)
    monkeypatch.setenv("HELPDESK_LLM_KEY", "")
    monkeypatch.setenv("HELPDESK_LLM_URL", "https://api.groq.com/openai/v1")
    assert helpdesk.dostawca() == "faq"
    monkeypatch.setenv("HELPDESK_LLM_URL", "http://127.0.0.1:11434/v1")
    assert helpdesk.dostawca() == "openai"


def test_cli_tylko_lokalnie_a_bez_klucza_silnik_faq(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("HELPDESK_LOCAL_CLI", "1")
    monkeypatch.setenv("VERCEL", "1")
    assert helpdesk.dostawca() == "faq"
    monkeypatch.setenv("HELPDESK_ENGINE", "off")
    assert helpdesk.dostawca() is None


# ---------------- silnik FAQ (bez API) ----------------
@pytest.fixture
def silnik_faq(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HELPDESK_LOCAL_CLI", raising=False)
    monkeypatch.delenv("HELPDESK_ENGINE", raising=False)
    assert helpdesk.dostawca() == "faq"
    from app import catalog
    s = SessionLocal()
    try:
        catalog.seed_products(s)
    finally:
        s.close()


def test_faq_odpowiada_bez_api(klient, silnik_faq):
    h, _ = klient
    chat = _wyslij(h, "How is the daily loss limit calculated?")
    assert chat["status"] == "bot"
    assert "equity at the start of the trading day" in chat["thread"][-1]["body"]
    assert chat["title"] == "How is the daily loss limit calculated?"
    chat = _wyslij(h, "Where do I upload my passport?", chat["id"])
    assert "Open KYC in the menu" in chat["thread"][-1]["body"]


def test_faq_liczby_planu_z_katalogu(klient, silnik_faq):
    h, _ = klient
    s = SessionLocal()
    try:
        from app.models import Product
        p = s.query(Product).filter(Product.account_size == 25000, Product.steps >= 2, Product.active).first()
    finally:
        s.close()
    chat = _wyslij(h, "What is the max daily loss on the 25k 2-step?")
    assert f"max daily loss {p.max_daily_loss_pct:g}%" in chat["thread"][-1]["body"]


def test_faq_bez_trafienia_przycisk_a_przekazanie_dopiero_po_trzech(klient, silnik_faq):
    """Nie wie: przycisk „Talk to a person". Sam przekazuje dopiero przy trzecim
    sensownym pytaniu bez odpowiedzi."""
    h, _ = klient
    chat = _wyslij(h, "What leverage do you offer?")
    assert chat["status"] == "bot" and chat["thread"][-1]["body"].startswith(helpdesk.NIE_WIEM)
    assert chat["offer_human"] is True
    chat = _wyslij(h, "Leverage for indices please", chat["id"])
    assert chat["status"] == "bot"                     # drugi raz — nadal tylko przycisk
    chat = _wyslij(h, "Which brokers liquidity provider is behind the server", chat["id"])
    assert chat["status"] == "open"                    # trzecie sensowne pytanie bez odpowiedzi
    pelny = client.get(f"/api/admin/tickets/{chat['id']}", headers=ADMIN).json()
    assert any("without a help-article match" in m["body"] for m in pelny["thread"] if m["author"] == "ai_note")


def test_odpowiedzi_zgodne_z_kodem_nie_z_nieaktualnym_faq(klient, silnik_faq):
    """FAQ mówi „typically 3–4" i „executed a trade"; kod: otwarta pozycja i dni z katalogu."""
    h, _ = klient
    s = SessionLocal()
    try:
        from app.models import Product
        f = helpdesk.fakty(s.query(Product).all())
    finally:
        s.close()
    odp = _wyslij(h, "What counts as a trading day?")["thread"][-1]["body"]
    assert "open position" in odp and "3–4" not in odp
    assert f"{f.dni_2step} per phase on 2-Step" in odp and f"{f.dni_instant} on Instant" in odp
    odp = _wyslij(h, "Can I use an EA?")["thread"][-1]["body"]
    assert "lots" in odp and "Weekend Trading add-on" in odp
    odp = _wyslij(h, "What happens when I break a rule?")["thread"][-1]["body"]
    assert "same code applies to everyone" not in odp and "discretionary" not in odp


@pytest.mark.parametrize("smiec", ["asdfghjkl", "kkkkkkkkk", "xqzvbnmtrk wpl", "??"])
def test_belkot_ignorowany(klient, model, smiec):
    """Bełkot nie idzie do asystenta ani do zespołu, nie zakłada rozmowy i nie
    liczy się do przycisku ani przekazania."""
    h, _ = klient
    r = client.post("/api/me/chat", headers=h, json={"message": smiec})
    assert r.status_code == 400                        # nowej rozmowy z bełkotu nie ma
    model["odp"] = _odp()
    chat = _wyslij(h, "How do I log in to MT5?")
    model["wywolania"] = 0
    chat = _wyslij(h, smiec, chat["id"])
    assert model["wywolania"] == 0 and chat["status"] == "bot"
    assert chat["thread"][-1]["body"].startswith(helpdesk.NIE_ROZUMIEM)
    for _ in range(3):
        chat = _wyslij(h, smiec + "x", chat["id"])
    assert chat["status"] == "bot" and chat["offer_human"] is False
    assert chat["id"] not in _admin_ids()


def test_powtorka_bez_nowej_odpowiedzi(klient, model):
    h, _ = klient
    model["odp"] = _odp()
    chat = _wyslij(h, "How do I log in to MT5?")
    model["wywolania"] = 0
    chat = _wyslij(h, "how do i log in to mt5?", chat["id"])
    assert model["wywolania"] == 0 and chat["thread"][-1]["body"] == helpdesk.DUPLIKAT


def test_limit_tempa_i_limit_godzinny_bez_przerzucania_na_zespol(klient, model, monkeypatch):
    from app import main as m
    h, _ = klient
    model["odp"] = _odp()
    chat = _wyslij(h, "How do I log in to MT5?")
    monkeypatch.setattr(m, "CHAT_NA_MINUTE", 3)
    _wyslij(h, "What about the web terminal?", chat["id"])
    _wyslij(h, "And on Android?", chat["id"])
    r = client.post("/api/me/chat", headers=h, json={"message": "And on iOS?", "chat_id": chat["id"]})
    assert r.status_code == 429
    monkeypatch.setattr(m, "CHAT_NA_MINUTE", 100)
    monkeypatch.setattr(m, "CHAT_AI_LIMIT_H", 2)
    chat = _wyslij(h, "And on a Mac?", chat["id"])
    assert chat["thread"][-1]["body"] == helpdesk.LIMIT_AI
    assert chat["status"] == "bot" and chat["offer_human"] is True and chat["id"] not in _admin_ids()


def test_bez_sensu_rozroznia_pytania():
    for ok in ["How do I log in to MT5?", "jak sie zalogowac do mt5", "Bonjour, comment ça marche?", "ea?", "25k?"]:
        assert helpdesk.bez_sensu(ok) is False, ok
    for zle in ["asdfgh", "qwertyuiop", "kkkkkkk", "xzcvbnm", "!!!", "sdfkjhsdkfjh"]:
        assert helpdesk.bez_sensu(zle) is True, zle


def test_przycisk_czlowiek_dopiero_pozniej(klient, silnik_faq, monkeypatch):
    from app import main as m
    h, _ = klient
    chat = _wyslij(h, "How do I change my password?")
    assert chat["offer_human"] is False          # pierwsza odpowiedź trafiona — bez przycisku
    chat = _wyslij(h, "Can I use an EA?", chat["id"])
    assert chat["offer_human"] is False
    chat = _wyslij(h, "What counts as a trading day?", chat["id"])
    assert chat["offer_human"] is True           # trzecia wiadomość tradera
    # Po 2 minutach od początku rozmowy — też.
    drugi = _wyslij(h, "Can I run multiple challenges at once?")
    assert drugi["offer_human"] is False
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    przyszlosc = _dt.now(_tz.utc) + _td(minutes=3)

    class _Zegar(_dt):
        @classmethod
        def now(cls, tz=None):
            return przyszlosc if tz else przyszlosc.replace(tzinfo=None)

    monkeypatch.setattr(m, "datetime", _Zegar)
    assert client.get(f"/api/me/chat/{drugi['id']}", headers=h).json()["chat"]["offer_human"] is True


# --- Powiadomienia supportu: tylko dymek czatu (+ mail/push), nigdy dzwonek ---

@pytest.fixture
def wysylki(monkeypatch):
    from app import notify
    stan = {"send": [], "admins": []}
    monkeypatch.setattr(notify, "send", lambda ev, to, ctx=None: stan["send"].append((ev, to, ctx or {})))
    monkeypatch.setattr(notify, "notify_admins",
                        lambda ev, title, body="", url="/admin", tag=None: stan["admins"].append((ev, url, tag)))
    return stan


def test_odpowiedz_supportu_mail_i_push_do_rozmowy_bez_dzwonka(klient, model, wysylki):
    h, tid = klient
    chat = _wyslij(h, "I want a refund for my order")
    # Push admina prowadzi do tej rozmowy w dymku panelu.
    assert wysylki["admins"][-1] == ("admin_ticket", f"/admin?chat={chat['id']}", f"chat-{chat['id']}")
    client.post(f"/api/admin/tickets/{chat['id']}/reply", headers=ADMIN, json={"message": "Checking it now."})
    ev, _, ctx = wysylki["send"][-1]
    assert ev == "ticket_reply" and ctx["chat_id"] == chat["id"]

    from app import push
    assert push.event_url("ticket_reply", {"chat_id": chat["id"]}) == f"/portal?view=support&chat={chat['id']}"
    # Nawet gdyby wpis istniał (stary wiersz), dzwonek tradera go nie pokazuje.
    from app.models import Notification
    s = SessionLocal()
    s.add(Notification(trader_id=tid, event="ticket_reply", title="Support replied", body="", url="/portal"))
    s.commit(); s.close()
    dzwonek = client.get("/api/me/notifications", headers=h).json()
    assert all(i["event"] != "ticket_reply" for i in dzwonek["items"]) and dzwonek["unread"] == 0


def test_push_odpowiedzi_bez_wpisu_w_dzwonku(klient, monkeypatch):
    from app import push
    from app.models import Notification
    _, tid = klient
    s = SessionLocal()
    email = s.get(Trader, tid).email
    s.close()
    monkeypatch.setattr(push, "send_to_trader", lambda *a, **k: 1)
    assert push.send_event("ticket_reply", email, "Support replied", {"chat_id": 1}) == 1
    s = SessionLocal()
    assert s.query(Notification).filter(Notification.trader_id == tid).count() == 0
    s.close()


def test_przelacznik_support_chat_wylacza_mail_i_push(klient, model, wysylki):
    h, _ = klient
    assert client.get("/api/auth/me", headers=h).json()["notify"]["support"] is True
    r = client.patch("/api/me", headers=h, json={"notify_support": False}).json()
    assert r["notify"]["support"] is False and r["ui_prefs"]["notify_support"] is False
    # Cały ui_prefs z panelu (np. sortowanie) razem z przełącznikiem — przełącznik zostaje.
    r = client.patch("/api/me", headers=h, json={"ui_prefs": {"sort": "x"}, "notify_support": False}).json()
    assert r["ui_prefs"] == {"sort": "x", "notify_support": False}
    chat = _wyslij(h, "I want a refund for my order")
    ile = len(wysylki["send"])
    client.post(f"/api/admin/tickets/{chat['id']}/reply", headers=ADMIN, json={"message": "Done."})
    assert len(wysylki["send"]) == ile
    r = client.patch("/api/me", headers=h, json={"notify_support": True}).json()
    assert r["notify"]["support"] is True and "notify_support" not in r["ui_prefs"]


def test_dymek_admina_liczy_czekajace_a_dzwonek_bez_supportu(klient, model, wysylki):
    h, _ = klient
    assert client.get("/api/admin/support/chats").status_code in (401, 403)
    przed = client.get("/api/admin/support/chats", headers=ADMIN).json()
    chat = _wyslij(h, "Please talk to a human about my payout")
    po = client.get("/api/admin/support/chats", headers=ADMIN).json()
    assert po["waiting"] == przed["waiting"] + 1 and po["last_id"] > przed["last_id"]
    wpis = next(c for c in po["chats"] if c["id"] == chat["id"])
    assert wpis["waiting"] and wpis["trader_name"] == "Czat Tester" and po["chats"][0]["waiting"]
    # Rozmowy z asystentem (status bot) nie trafiają do dymka zespołu.
    model["odp"] = _odp()
    sam = _wyslij(h, "How do I log in to MT5?")
    assert all(c["id"] != sam["id"] for c in client.get("/api/admin/support/chats", headers=ADMIN).json()["chats"])
    # Dzwonek panelu nie dostaje już wiadomości supportu.
    inbox = client.get("/api/admin/inbox", headers=ADMIN).json()
    assert all(i.get("type") != "ticket" for i in inbox["items"])
    client.post(f"/api/admin/tickets/{chat['id']}/reply", headers=ADMIN, json={"message": "On it."})
    assert client.get("/api/admin/support/chats", headers=ADMIN).json()["waiting"] == przed["waiting"]
