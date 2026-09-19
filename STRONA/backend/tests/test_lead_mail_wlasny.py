"""Mail do leada z treścią pisaną w panelu + szablony wielokrotnego użytku.

Automat z `lead_mail.tresc()` ma jedną, kontrolowaną wersję przypiętą do wyniku
ankiety — i taki zostaje. Tu testujemy drugą drogę: temat i tekst wpisuje
człowiek, a serwer pilnuje tego, co przy wolnej treści psuje się najłatwiej:

* że wychodzi DOKŁADNIE wpisany tekst, w tym samym papierze firmowym marki,
* że w historii leada zostaje temat i pełna treść (odpowiedź „tak, jak
  pisaliście" musi mieć do czego się odnieść),
* że webhook Brevo dopasuje doręczenie po własnym temacie tak samo jak po
  automatycznym,
* że szablon zapisany pod istniejącą nazwą NADPISUJE, a nie mnoży wpisy,
* że wolna treść nie omija bramek kanału (konfiguracja, sensowny adres).
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import lead_mail  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, LeadEvent, LeadMailTemplate  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))
NADAWCA = "Forex Passing <desk@forexpassing.test>"

TEMAT = "Quick question about your setup"
TEKST = ("Hi Anna,\n\nSaw your application and one thing stood out.\n\n"
         "https://t.me/probe_desk\n\n--\nForex Passing\nReply \"stop\" to opt out.")


@pytest.fixture
def poczta(monkeypatch):
    """Konfiguracja kanału i transport-atrapa. Zwraca listę maili."""
    for pole, wartosc in (("smtp_host", "smtp.probe.test"),
                          ("lead_mail_from", NADAWCA),
                          ("lead_telegram_channel_url", "https://t.me/probe_channel"),
                          ("sms_telegram_url", "https://t.me/probe_desk")):
        monkeypatch.setattr(lead_mail.settings, pole, wartosc)
    poszly = []
    monkeypatch.setattr(lead_mail, "_smtp_transport", poszly.append)
    return poszly


def _lead(*, email=None, name="Anna Nowak", status="new"):
    s = SessionLocal()
    lead = Lead(email=email or f"wlasny{next(LICZNIK)}@probe.test", name=name,
                outcome="qualified", status=status)
    s.add(lead); s.commit()
    lid = lead.id; s.close()
    return lid


def _tresc(msg) -> str:
    return msg.get_body(preferencelist=("plain",)).get_payload(
        decode=True).decode("utf-8")


def _wyslij(lid, temat=TEMAT, tekst=TEKST):
    return client.post(f"/api/admin/leads/{lid}/email-custom", headers=ADMIN,
                       json={"subject": temat, "body": tekst})


def _sprzatnij_szablony():
    s = SessionLocal()
    s.query(LeadMailTemplate).delete(); s.commit(); s.close()


# --- wysyłka -------------------------------------------------------------------

def test_wychodzi_dokladnie_wpisany_tekst(poczta):
    lid = _lead()
    assert _wyslij(lid).status_code == 200
    msg = poczta[0]
    assert msg["Subject"] == TEMAT
    assert _tresc(msg).strip() == TEKST
    # Ten sam papier firmowy co automat: goły link staje się przyciskiem.
    html = msg.get_body(preferencelist=("html",)).get_payload(
        decode=True).decode("utf-8")
    assert 'href="https://t.me/probe_desk"' in html
    assert msg["From"] == NADAWCA


def test_historia_dostaje_temat_i_pelna_tresc(poczta):
    lid = _lead()
    _wyslij(lid)
    zdarzenia = client.get(f"/api/admin/leads/{lid}", headers=ADMIN).json()["events"]
    mail = next(z for z in zdarzenia if z["kind"] == "email")
    assert mail["detail"] == TEMAT and mail["body"] == TEKST


def test_pusty_temat_albo_tresc_nie_leci(poczta):
    lid = _lead()
    for temat, tekst in (("", TEKST), ("   ", TEKST), (TEMAT, ""), (TEMAT, "  \n ")):
        assert _wyslij(lid, temat, tekst).status_code == 400
    assert poczta == []


def test_temat_gubi_zlamania_linii(poczta):
    """Temat z wklejki potrafi nieść \\n — w nagłówku SMTP to wstrzyknięcie
    kolejnych nagłówków, więc serwer składa go do jednej linii."""
    lid = _lead()
    _wyslij(lid, "Quick\nquestion  about\tyour setup")
    assert poczta[0]["Subject"] == "Quick question about your setup"


def test_wlasny_mail_zdejmuje_leada_ze_stanu_new(poczta):
    lid = _lead(status="new")
    assert _wyslij(lid).json()["status"] == "messaged"


def test_dalsze_statusy_zostaja_nietkniete(poczta):
    lid = _lead(status="replied")
    assert _wyslij(lid).json()["status"] == "replied"


def test_bez_blokady_raz_na_leada(poczta):
    """Blokada przy automacie chroni przed DRUGĄ KOPIĄ tego samego tekstu.
    Tu każdy mail admin pisze i zatwierdza sam — powtórka to jego decyzja."""
    lid = _lead()
    client.post(f"/api/admin/leads/{lid}/email", headers=ADMIN)  # automat
    assert _wyslij(lid).status_code == 200
    assert _wyslij(lid, "Follow-up", "Second thought.").status_code == 200
    assert len(poczta) == 3


def test_zly_adres_wraca_powodem(poczta):
    # Nie „brak": pod jednym przebiegiem pytest ten plik dzieli bazę z
    # `test_lead_mail.py`, a tam lead o tym adresie już jest (email ma UNIQUE).
    lid = _lead(email="niema-adresu")
    r = _wyslij(lid)
    assert r.status_code == 400 and "e-mail address" in r.json()["detail"]
    assert poczta == []


def test_bez_konfiguracji_kanal_stoi(poczta, monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "")
    assert _wyslij(_lead()).status_code == 400
    assert poczta == []


def test_nieznany_lead_to_404(poczta):
    assert _wyslij(999999).status_code == 404


def test_webhook_brevo_dopasowuje_wlasny_temat(poczta, monkeypatch):
    """Doręczenia i odbicia mają wchodzić do historii także dla własnych maili —
    dopasowanie idzie po temacie ZAPISANYM przy wysyłce, nie po liście
    tematów automatu."""
    monkeypatch.setattr(lead_mail.settings, "brevo_webhook_secret", "sekret")
    lid = _lead()
    adres = client.get(f"/api/admin/leads/{lid}", headers=ADMIN).json()["email"]
    _wyslij(lid)
    r = client.post("/api/brevo/webhook?token=sekret",
                    json={"event": "delivered", "email": adres, "subject": TEMAT})
    assert r.status_code == 200
    s = SessionLocal()
    dostawy = s.query(LeadEvent).filter(LeadEvent.lead_id == lid,
                                        LeadEvent.kind == "delivery").count()
    s.close()
    assert dostawy == 1


# --- szablony ------------------------------------------------------------------

def _zapisz(name="Follow-up", subject=TEMAT, body=TEKST):
    return client.post("/api/admin/email-templates", headers=ADMIN,
                       json={"name": name, "subject": subject, "body": body})


def test_szablon_zapisuje_sie_i_wraca_na_liscie():
    _sprzatnij_szablony()
    r = _zapisz()
    assert r.status_code == 200
    lista = client.get("/api/admin/email-templates", headers=ADMIN).json()
    assert [(t["name"], t["subject"], t["body"]) for t in lista] \
        == [("Follow-up", TEMAT, TEKST)]


def test_ta_sama_nazwa_nadpisuje_a_nie_mnozy():
    """Jedyny scenariusz zapisu pod starą nazwą to poprawka treści — lista z
    dwoma wpisami „Follow-up" różniącymi się literówką byłaby gorsza niż brak
    szablonów wcale."""
    _sprzatnij_szablony()
    stary = _zapisz().json()
    nowy = _zapisz(subject="Better subject", body="Better body.").json()
    assert nowy["id"] == stary["id"]
    lista = client.get("/api/admin/email-templates", headers=ADMIN).json()
    assert len(lista) == 1 and lista[0]["subject"] == "Better subject"


def test_szablony_wracaja_alfabetycznie():
    _sprzatnij_szablony()
    for nazwa in ("Zimny kontakt", "Follow-up", "Ostatnia próba"):
        _zapisz(name=nazwa)
    lista = client.get("/api/admin/email-templates", headers=ADMIN).json()
    assert [t["name"] for t in lista] \
        == ["Follow-up", "Ostatnia próba", "Zimny kontakt"]


def test_szablon_bez_kompletu_pol_to_400():
    for dane in ({"name": "", "subject": TEMAT, "body": TEKST},
                 {"name": "X", "subject": " ", "body": TEKST},
                 {"name": "X", "subject": TEMAT, "body": ""}):
        assert client.post("/api/admin/email-templates", headers=ADMIN,
                           json=dane).status_code == 400


def test_kasowanie_szablonu():
    _sprzatnij_szablony()
    tid = _zapisz().json()["id"]
    assert client.delete(f"/api/admin/email-templates/{tid}",
                         headers=ADMIN).status_code == 200
    assert client.get("/api/admin/email-templates", headers=ADMIN).json() == []
    assert client.delete(f"/api/admin/email-templates/{tid}",
                         headers=ADMIN).status_code == 404


def test_bez_tokenu_admina_ani_kroku(poczta):
    lid = _lead()
    assert client.post(f"/api/admin/leads/{lid}/email-custom",
                       json={"subject": TEMAT, "body": TEKST}).status_code in (401, 403)
    assert client.get("/api/admin/email-templates").status_code in (401, 403)
    assert client.post("/api/admin/email-templates",
                       json={"name": "X", "subject": "Y", "body": "Z"}).status_code in (401, 403)
    assert client.delete("/api/admin/email-templates/1").status_code in (401, 403)
    assert poczta == []


# --- panel ---------------------------------------------------------------------

def test_panel_ma_composer_i_szablony():
    """Backend bez przycisku jest endpointem, którego nikt nie kliknie: ołówek
    zapięty na `mail_ready` otwiera composer, strzały idą pod oba nowe adresy,
    a `{name}` podmienia się PRZED podglądem — admin zatwierdza dokładnie to,
    co wyjdzie."""
    kod = client.get("/static/js/admin-panel.js").text
    assert "openLeadMail(" in kod
    assert "'/api/admin/leads/'+id+'/email-custom'" in kod
    assert "'/api/admin/email-templates'" in kod
    assert "mailFill(" in kod and "'{name}'" in kod
    assert kod.count("l.mail_ready") >= 2
