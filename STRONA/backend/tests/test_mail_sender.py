"""Wybór nadawcy w oknie maila: adres platformy (ptf) albo marka landingu (fx).

Do 2026-09 klient dostawał mail wyłącznie spod platformy, a lead wyłącznie
spod marki landingu — dwa okna, dwie drogi, zero wyboru. Teraz jedno okno
(Clients, Leads, zakładka Mail) i jedna wysyłka `_wyslij_z_panelu`, a nadawca
jest polem. Testy pilnują trzech rzeczy, na których to stoi:

* `fx` idzie przez `lead_mail` (Resend, gdy jest klucz; papeteria marki) i
  zostawia wpis w dzienniku Mail pod `admin_message_fx`; `ptf` idzie przez
  `notify` jak dotąd — istniejące testy tego nie zauważają,
* zły nadawca to 400, nieskonfigurowany nadawca fx to 400 z nazwą zmiennej,
* mail na goły adres z zakładki Mail dopasowuje klienta i leada po e-mailu,
  więc ślad ląduje w tej samej historii, co mail z ich kart.
"""
import json
import os
import tempfile
import urllib.error
from io import BytesIO

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, lead_mail, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, LeadEvent, MailLog, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))
NADAWCA = "Forex Passing <desk@partner.test>"
TEMAT = "Quick note"
TEKST = "Hi Anna,\n\nOne thing.\n\nhttps://example.test/portal\n\n--\nDesk"


@pytest.fixture
def smtp(monkeypatch):
    """Atrapa SMTP dla `notify` (nadawca ptf). Zwraca listę wysłanych."""
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


@pytest.fixture
def resend(monkeypatch):
    """Nadawca fx przez Resend: klucz ustawiony, SMTP `lead_mail` NIE ma prawa
    być tknięty. Zwraca listę żądań HTTP (url, nagłówki, JSON)."""
    zadania = []
    monkeypatch.setattr(lead_mail.settings, "resend_api_key", "re_test_123")
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from", NADAWCA)
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "")
    monkeypatch.setattr(lead_mail.settings, "sms_telegram_url", "")
    monkeypatch.setattr(lead_mail.settings, "lead_telegram_channel_url", "")

    def _smtp_zabroniony(msg):
        raise AssertionError("SMTP użyty mimo klucza Resend")
    monkeypatch.setattr(lead_mail, "_smtp_transport", _smtp_zabroniony)

    class _Odp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"id":"x"}'

    def _urlopen(req, timeout=0):
        zadania.append((req.full_url, dict(req.header_items()),
                        json.loads(req.data.decode("utf-8"))))
        return _Odp()
    monkeypatch.setattr(lead_mail.urllib.request, "urlopen", _urlopen)
    return zadania


def _trader(email=None, name="Jan Klient"):
    s = SessionLocal()
    tr = Trader(email=email or f"sender{next(LICZNIK)}@test.pl",
                password_hash=auth.hash_password("haslo12345"), full_name=name,
                referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit(); dane = (tr.id, tr.email); s.close()
    return dane


def _lead(email=None, name="Anna Nowak"):
    s = SessionLocal()
    lead = Lead(email=email or f"sender-lead{next(LICZNIK)}@test.pl", name=name,
                outcome="qualified", status="new")
    s.add(lead); s.commit(); dane = (lead.id, lead.email); s.close()
    return dane


def _dziennik(email):
    s = SessionLocal()
    try:
        return [(m.event, m.ok, m.error) for m in
                s.query(MailLog).filter(MailLog.to_email == email).all()]
    finally:
        s.close()


# --- transport Resend ------------------------------------------------------------

def test_klucz_resend_kieruje_lead_mail_do_resenda(resend):
    poszlo, powod = lead_mail.wyslij("anna@test.pl", TEMAT, TEKST, tylko_nadawca=True)
    assert poszlo, powod
    (url, naglowki, dane), = resend
    assert url == lead_mail.RESEND_URL
    assert naglowki.get("Authorization") == "Bearer re_test_123"
    # Bez własnego User-Agenta Cloudflare przed Resendem odpowiada 403/1010,
    # zanim żądanie dotrze do API — pierwszy mail z produkcji padł na tym.
    assert naglowki.get("User-agent") == lead_mail.RESEND_UA
    assert "python-urllib" not in naglowki.get("User-agent", "").lower()
    assert dane["from"] == NADAWCA and dane["reply_to"] == NADAWCA
    assert dane["to"] == ["anna@test.pl"] and dane["subject"] == TEMAT
    assert dane["text"].strip() == TEKST
    assert "Open the Link" in dane["html"]          # link spoza Telegrama
    assert "Forex Passing" in dane["html"]


def test_bez_klucza_resend_zostaje_smtp(monkeypatch):
    poszly = []
    monkeypatch.setattr(lead_mail.settings, "resend_api_key", "")
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "smtp.probe.test")
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from", NADAWCA)
    monkeypatch.setattr(lead_mail, "_smtp_transport", poszly.append)
    assert lead_mail.wyslij("anna@test.pl", TEMAT, TEKST, tylko_nadawca=True)[0]
    assert len(poszly) == 1 and poszly[0]["From"] == NADAWCA


def test_odmowa_resenda_wraca_jako_powod_a_nie_wyjatek(resend, monkeypatch):
    def _urlopen(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 422, "Unprocessable", {},
                                     BytesIO(b'{"message":"domain not verified"}'))
    monkeypatch.setattr(lead_mail.urllib.request, "urlopen", _urlopen)
    poszlo, powod = lead_mail.wyslij("anna@test.pl", TEMAT, TEKST, tylko_nadawca=True)
    assert poszlo is False
    assert "resend 422" in powod and "domain not verified" in powod


def test_nadawca_gotowy_nie_wymaga_telegrama(resend):
    """Automat (`is_enabled`) dalej wymaga URL-i Telegrama, mail z ręki nie."""
    assert lead_mail.nadawca_gotowy() is True
    assert lead_mail.is_enabled() is False
    assert lead_mail.czego_brakuje_nadawcy() == []


def test_bez_nadawcy_i_bez_drogi_mowi_czego_brakuje(monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "resend_api_key", "")
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "")
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from", "")
    assert lead_mail.czego_brakuje_nadawcy() == ["SMTP_HOST", "LEAD_MAIL_FROM"]
    assert lead_mail.wyslij("a@b.pl", TEMAT, TEKST, tylko_nadawca=True) \
        == (False, "lead e-mail is not configured")


def test_etykieta_guzika_zalezy_od_adresu(monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "lead_telegram_channel_url",
                        "https://t.me/kanal")
    html = lead_mail._html_z_tekstu("Hi\n\nhttps://t.me/kanal")
    assert "Join us on Telegram" in html
    html = lead_mail._html_z_tekstu("Hi\n\nhttps://t.me/desk_probe")
    assert "Message the desk on Telegram" in html
    html = lead_mail._html_z_tekstu("Hi\n\nhttps://example.test/portal")
    assert "Open the Link" in html and "Telegram" not in html


# --- wybór nadawcy w endpointach --------------------------------------------------

def test_klient_z_nadawca_fx_idzie_przez_resend_i_do_dziennika(resend, smtp):
    tid, email = _trader()
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN,
                    json={"subject": TEMAT, "body": TEKST, "sender": "fx"})
    assert r.status_code == 200, r.text
    assert r.json()["sender"] == "fx"
    assert len(resend) == 1 and smtp == []          # nie przez notify
    assert _dziennik(email) == [("admin_message_fx", True, None)]


def test_klient_bez_pola_sender_idzie_jak_dotad_przez_platforme(resend, smtp):
    tid, email = _trader()
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN,
                    json={"subject": TEMAT, "body": TEKST})
    assert r.status_code == 200, r.text
    assert r.json()["sender"] == "ptf"
    assert len(smtp) == 1 and resend == []
    assert smtp[0]["From"] == notify.settings.mail_from
    assert _dziennik(email) == [("admin_message", True, None)]


def test_zly_nadawca_to_400_i_nic_nie_wychodzi(resend, smtp):
    tid, _ = _trader()
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN,
                    json={"subject": TEMAT, "body": TEKST, "sender": "xyz"})
    assert r.status_code == 400 and "sender" in r.json()["detail"]
    assert resend == [] and smtp == []


def test_nadawca_fx_bez_konfiguracji_mowi_ktorej_zmiennej_brakuje(smtp, monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "resend_api_key", "")
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "")
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from", "")
    tid, email = _trader()
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN,
                    json={"subject": TEMAT, "body": TEKST, "sender": "fx"})
    assert r.status_code == 400
    assert "LEAD_MAIL_FROM" in r.json()["detail"] and "SMTP_HOST" in r.json()["detail"]
    assert smtp == [] and _dziennik(email) == []


def test_lead_z_nadawca_ptf_idzie_przez_platforme_i_zostawia_nadawce_w_historii(resend, smtp):
    lid, email = _lead()
    r = client.post(f"/api/admin/leads/{lid}/email-custom", headers=ADMIN,
                    json={"subject": TEMAT, "body": TEKST, "sender": "ptf"})
    assert r.status_code == 200, r.text
    assert len(smtp) == 1 and resend == []
    s = SessionLocal()
    try:
        zd = s.query(LeadEvent).filter(LeadEvent.lead_id == lid,
                                       LeadEvent.kind == "email").one()
        assert json.loads(zd.payload_json)["sender"] == "ptf"
        assert s.get(Lead, lid).status == "messaged"
    finally:
        s.close()


def test_lead_domyslnie_spod_marki_landingu(resend, smtp):
    lid, email = _lead()
    r = client.post(f"/api/admin/leads/{lid}/email-custom", headers=ADMIN,
                    json={"subject": TEMAT, "body": TEKST})
    assert r.status_code == 200, r.text
    assert r.json()["sender"] == "fx" and len(resend) == 1 and smtp == []


def test_status_panelu_zna_gotowosc_nadawcy_fx(resend):
    dane = client.get("/api/stats", headers=ADMIN).json()
    assert dane["fx_sender_ready"] is True and dane["fx_sender_missing"] == []


# --- goły adres z zakładki Mail ---------------------------------------------------

def test_mail_na_adres_dopasowuje_klienta_i_leada_po_mailu(resend, smtp):
    email = f"oboje{next(LICZNIK)}@test.pl"
    tid, _ = _trader(email=email)
    lid, _ = _lead(email=email.upper())
    r = client.post("/api/admin/mail/send", headers=ADMIN,
                    json={"to": f"  {email.upper()} ", "subject": TEMAT,
                          "body": TEKST, "sender": "fx"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "email": email, "sender": "fx",
                        "trader_id": tid, "lead_id": lid}
    assert len(resend) == 1 and resend[0][2]["to"] == [email]
    s = SessionLocal()
    try:
        assert s.query(LeadEvent).filter(LeadEvent.lead_id == lid,
                                         LeadEvent.kind == "email").count() == 1
    finally:
        s.close()


def test_mail_na_obcy_adres_zostawia_slad_tylko_w_dzienniku(resend, smtp):
    email = f"obcy{next(LICZNIK)}@test.pl"
    r = client.post("/api/admin/mail/send", headers=ADMIN,
                    json={"to": email, "subject": TEMAT, "body": TEKST})
    assert r.status_code == 200, r.text
    assert r.json()["trader_id"] is None and r.json()["lead_id"] is None
    assert r.json()["sender"] == "ptf" and len(smtp) == 1
    assert _dziennik(email) == [("admin_message", True, None)]


def test_mail_na_zly_adres_to_400(resend, smtp):
    for zly in ("", "bez-malpy", "a@b"):
        r = client.post("/api/admin/mail/send", headers=ADMIN,
                        json={"to": zly, "subject": TEMAT, "body": TEKST})
        assert r.status_code == 400, zly
    assert resend == [] and smtp == []


def test_lista_nadawcow_dla_selektora(resend):
    dane = client.get("/api/admin/mail/senders", headers=ADMIN).json()
    assert set(dane) == {"ptf", "fx"}
    assert dane["fx"] == {"label": "Forex Passing", "ready": True, "missing": []}
    assert dane["ptf"]["label"] and isinstance(dane["ptf"]["ready"], bool)


def test_panel_ma_jedno_okno_maila_z_nadawca_i_compose():
    """Karta klienta, ołówek leada i przycisk w zakładce Mail otwierają TO SAMO
    okno; nadawca to pole; goły adres idzie pod /api/admin/mail/send."""
    kod = client.get("/static/js/admin-panel.js").text
    assert kod.count("openMailComposer(") >= 4          # def + 3 wejścia
    assert "function openLeadMail(id)" in kod and "function openClientMail(id)" in kod
    assert 'onclick="openMailCompose()"' in kod
    assert 'id="lm-from"' in kod and 'id="lm-to"' in kod
    assert "'/api/admin/mail/send'" in kod and "'/api/admin/mail/senders'" in kod
    assert "JSON.stringify({subject,body,sender})" in kod
    assert "JSON.stringify({name,subject,body,sender})" in kod
    assert "t.builtin" in kod                           # wbudowane bez Delete
    assert "client-mail-modal" not in kod and "lead-mail-modal" not in kod


# --- emoji ----------------------------------------------------------------------------

EMOJI_TEMAT = "Your FREE Challenge Account is Ready! 🚀"
EMOJI_TEKST = "Hi Anna,\n\nGreat news! 🎉 Your account is live.\n\nhttps://example.test/portal\n\n--\nDesk ✅"


def test_emoji_przechodza_przez_resend_bez_zmian(resend):
    """Temat i treść z emoji (tak wysyła dział od zawsze) mają dojść do Resenda
    jako te same znaki — w JSON-ie UTF-8, w HTML-u bez podmiany na encje."""
    assert lead_mail.wyslij("anna@test.pl", EMOJI_TEMAT, EMOJI_TEKST, tylko_nadawca=True)[0]
    (_, _, dane), = resend
    assert dane["subject"] == EMOJI_TEMAT
    assert "🎉" in dane["text"] and "✅" in dane["text"]
    assert "🎉" in dane["html"] and "✅" in dane["html"]


def test_emoji_przechodza_przez_smtp_marki_landingu(monkeypatch):
    poszly = []
    monkeypatch.setattr(lead_mail.settings, "resend_api_key", "")
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "smtp.probe.test")
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from", NADAWCA)
    monkeypatch.setattr(lead_mail, "_smtp_transport", poszly.append)
    assert lead_mail.wyslij("anna@test.pl", EMOJI_TEMAT, EMOJI_TEKST, tylko_nadawca=True)[0]
    msg = poszly[0]
    # Nagłówek jest kodowany (RFC 2047) w drodze, ale po odkodowaniu to ten sam temat.
    assert str(msg["Subject"]) == EMOJI_TEMAT
    assert "🎉" in msg.get_body(preferencelist=("plain",)).get_content()
    assert "🎉" in msg.get_body(preferencelist=("html",)).get_content()
    # Serializacja do bajtów (to, co idzie po drucie) nie może się wywrócić,
    # a po drugiej stronie temat ma wrócić jako te same znaki. Polityka
    # `email` koduje TYLKO fragment z emoji, nie cały nagłówek — stąd
    # sprawdzamy odczyt, a nie kształt zakodowanego słowa.
    import email
    from email.policy import default
    z_drutu = email.message_from_bytes(msg.as_bytes(), policy=default)
    assert z_drutu["Subject"] == EMOJI_TEMAT
    assert "🎉" in z_drutu.get_body(preferencelist=("plain",)).get_content()


def test_emoji_przechodza_przez_adres_platformy(smtp):
    tid, email = _trader()
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN,
                    json={"subject": EMOJI_TEMAT, "body": EMOJI_TEKST})
    assert r.status_code == 200, r.text
    msg = smtp[0]
    assert str(msg["Subject"]) == EMOJI_TEMAT
    assert "🎉" in msg.get_body(preferencelist=("plain",)).get_content()
    assert "🎉" in msg.get_body(preferencelist=("html",)).get_content()
    msg.as_bytes()


def test_szablon_z_emoji_wraca_z_bazy_bez_zmian():
    r = client.post("/api/admin/email-templates", headers=ADMIN,
                    json={"name": "Emoji test 🚀", "subject": EMOJI_TEMAT,
                          "body": EMOJI_TEKST, "sender": "fx"})
    assert r.status_code == 200, r.text
    assert r.json()["subject"] == EMOJI_TEMAT and r.json()["name"] == "Emoji test 🚀"
    lista = client.get("/api/admin/email-templates", headers=ADMIN).json()
    assert any(t["subject"] == EMOJI_TEMAT and "🎉" in t["body"] for t in lista)
    client.delete(f"/api/admin/email-templates/{r.json()['id']}", headers=ADMIN)


# --- nadawca z konfiguracji ---------------------------------------------------------------

def _config_z_env(**env):
    """Świeży import `app.config` w osobnym procesie, z podanym środowiskiem."""
    import subprocess
    import sys
    kod = ("from app.config import get_settings; "
           "print(get_settings().lead_mail_from)")
    srodowisko = {**os.environ, "LEAD_MAIL_FROM": "", "RESEND_FROM": "",
                  "RESEND_API_KEY": "", **env}
    return subprocess.run([sys.executable, "-c", kod], capture_output=True, text=True,
                          env=srodowisko, cwd=os.path.dirname(os.path.dirname(__file__))
                          ).stdout.strip()


def test_resend_from_wygrywa_gdy_jest_klucz_resend():
    """Na Vercelu obok siebie stoją stary LEAD_MAIL_FROM (pod SMTP) i nowy
    RESEND_FROM (domena zweryfikowana u Resenda). Z kluczem Resend nadawcą ma
    być ten drugi — inaczej Resend odmówi podpisania cudzej domeny."""
    assert _config_z_env(LEAD_MAIL_FROM="Old <old@smtp.test>", RESEND_FROM="New <new@resend.test>",
                         RESEND_API_KEY="re_x") == "New <new@resend.test>"
    assert _config_z_env(LEAD_MAIL_FROM="Old <old@smtp.test>", RESEND_FROM="New <new@resend.test>") \
        == "Old <old@smtp.test>"
    assert _config_z_env(RESEND_FROM="New <new@resend.test>") == "New <new@resend.test>"
    assert _config_z_env(LEAD_MAIL_FROM="Old <old@smtp.test>", RESEND_API_KEY="re_x") \
        == "Old <old@smtp.test>"


def test_bez_tokenu_admina_ani_kroku(resend, smtp):
    assert client.post("/api/admin/mail/send",
                       json={"to": "a@b.pl", "subject": TEMAT, "body": TEKST}
                       ).status_code in (401, 403)
    assert resend == [] and smtp == []
