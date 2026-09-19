"""Mail do klienta z ręki i ponowienie automatu — z panelu, nie z prywatnej skrzynki.

Dotąd z panelu szły do klienta wyłącznie automaty, więc „nie mogę znaleźć linku
do hasła" kończyło się mailem z prywatnej skrzynki właściciela: poza dziennikiem
wysyłek i poza historią klienta. Testy pilnują trzech rzeczy, na których to stoi:
że człowiek przy przycisku dowiaduje się o porażce SMTP OD RAZU (a nie z dziennika
dwa dni później), że ponowienie składa linki OD NOWA zamiast kopiować martwe,
i że mail z poświadczeniami nigdy nie trafi na cudze konto.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, MailLog, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))


@pytest.fixture
def poczta(monkeypatch):
    """Atrapa gniazda SMTP — wysyłka idzie PRAWDZIWĄ ścieżką `_send_teraz`,
    bo to w niej siedzi wszystko, co te testy sprawdzają (dziennik, bramki,
    zwrot błędu). Podstawka wyżej przepuściłaby każdą regresję."""
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


def _klient(*, bez_hasla: bool = False, email: str | None = None) -> int:
    email = email or f"mail{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Jan Klient", referral_code=auth.secrets.token_hex(3),
                must_set_password=bez_hasla)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _konto(tid: int, login: str) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Jan Klient",
                  product_key="2step-100k", initial_balance=100_000.0, steps=2,
                  status="active", phase="eval_1", platform_login=login,
                  platform_password="tajne123", platform_server="PTF-Demo",
                  balance=100_000.0, equity=100_000.0)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _wpis(event: str, email: str, temat: str) -> int:
    s = SessionLocal()
    m = MailLog(event=event, to_email=email, subject=temat, ok=True)
    s.add(m); s.commit(); mid = m.id; s.close()
    return mid


# --------------------------------------------------------------- mail z ręki

def test_recznny_mail_wychodzi_i_zostawia_slad(poczta):
    tid = _klient()
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN_H,
                    json={"subject": "Twój link", "body": "Hi Jan,\n\nhttps://ptf.test/portal?reset=abc"})
    assert r.status_code == 200, r.text

    msg = poczta[-1]
    assert msg["Subject"] == "Twój link"
    tresc = msg.get_payload()[1].get_payload(decode=True).decode()
    # Akapit będący samym adresem ma być przyciskiem, nie linijką do przepisania
    assert 'href="https://ptf.test/portal?reset=abc"' in tresc
    assert "Open the Link" in tresc

    s = SessionLocal()
    wpis = (s.query(MailLog).filter(MailLog.event == "admin_message")
            .order_by(MailLog.id.desc()).first())
    s.close()
    assert wpis is not None and wpis.ok is True


def test_pusty_temat_lub_tresc_odpada(poczta):
    tid = _klient()
    assert client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN_H,
                       json={"subject": "  ", "body": "cokolwiek"}).status_code == 400
    assert client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN_H,
                       json={"subject": "Temat", "body": "   "}).status_code == 400
    assert poczta == []


def test_padniety_smtp_konczy_sie_bledem_a_nie_cichym_ok(monkeypatch):
    """Automat może przegrać po cichu — dziennik go złapie. Człowiek przy
    przycisku musi zobaczyć porażkę od razu, inaczej powie klientowi
    „wysłałem" i oboje będą czekać na coś, czego nie ma."""
    tid = _klient()
    monkeypatch.setattr(notify.settings, "smtp_host", "")   # brak konfiguracji
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN_H,
                    json={"subject": "Temat", "body": "Treść"})
    assert r.status_code == 400
    assert "SMTP_HOST" in r.json()["detail"]


def test_adres_z_importu_nie_dostaje_maila(poczta):
    tid = _klient(email=f"imp{next(LICZNIK)}@imported.local")
    r = client.post(f"/api/admin/traders/{tid}/email", headers=ADMIN_H,
                    json={"subject": "Temat", "body": "Treść"})
    assert r.status_code == 400 and "import" in r.json()["detail"]
    assert poczta == []


# ------------------------------------------------------------- reset hasła

def test_reset_leci_do_klienta_a_link_nie_wraca_do_panelu(poczta):
    tid = _klient()
    r = client.post(f"/api/admin/traders/{tid}/password-reset", headers=ADMIN_H)
    assert r.status_code == 200
    assert "reset_url" not in r.json(), "panel nie ma trzymać wejściówki do cudzego konta"
    assert poczta[-1]["Subject"] == "Reset your password"


def test_nieodebrane_konto_dostaje_zaproszenie_a_nie_reset(poczta):
    tid = _klient(bez_hasla=True)
    r = client.post(f"/api/admin/traders/{tid}/password-reset", headers=ADMIN_H)
    assert r.status_code == 400 and "invite" in r.json()["detail"]


# --------------------------------------------------------------- ponowienie

def test_ponowione_zaproszenie_niesie_swiezy_link(poczta):
    tid = _klient(bez_hasla=True)
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    mid = _wpis("portal_invite", email, "Your portal access")
    r = client.post(f"/api/admin/mail-log/{mid}/resend", headers=ADMIN_H)
    assert r.status_code == 200, r.text
    tresc = poczta[-1].get_payload()[1].get_payload(decode=True).decode()
    assert "?reset=" in tresc, "ponowienie ma zbudować link od nowa, nie skopiować martwy"


def test_ponowienie_zaproszenia_odpada_gdy_haslo_juz_jest(poczta):
    tid = _klient()
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    mid = _wpis("portal_invite", email, "Your portal access")
    r = client.post(f"/api/admin/mail-log/{mid}/resend", headers=ADMIN_H)
    assert r.status_code == 400 and "password reset" in r.json()["detail"]


def test_poswiadczenia_wracaja_na_wlasciwe_konto(poczta):
    """Klient z dwoma kontami: login z tematu rozstrzyga, które hasło MT5
    ma wyjść. Pomyłka tutaj to cudze poświadczenia w cudzej skrzynce."""
    tid = _klient()
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    _konto(tid, "700000111")
    _konto(tid, "700000222")
    mid = _wpis("credentials", email, "Your challenge account 700000222 is ready ⚡")
    r = client.post(f"/api/admin/mail-log/{mid}/resend", headers=ADMIN_H)
    assert r.status_code == 200, r.text
    tresc = poczta[-1].get_payload()[0].get_payload(decode=True).decode()
    assert "700000222" in tresc and "700000111" not in tresc


def test_dwa_konta_bez_loginu_w_temacie_to_odmowa(poczta):
    tid = _klient()
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    _konto(tid, "700000333")
    _konto(tid, "700000444")
    mid = _wpis("challenge_granted", email, "Your BOGO challenge is live")
    r = client.post(f"/api/admin/mail-log/{mid}/resend", headers=ADMIN_H)
    assert r.status_code == 400 and "which account" in r.json()["detail"]
    assert poczta == []


def test_mail_z_danymi_chwili_nie_daje_sie_ponowic(poczta):
    tid = _klient()
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    mid = _wpis("verify_email", email, "123456 is your code")
    r = client.post(f"/api/admin/mail-log/{mid}/resend", headers=ADMIN_H)
    assert r.status_code == 400 and "by hand" in r.json()["detail"]
    assert poczta == []


def test_dziennik_mowi_panelowi_co_da_sie_ponowic():
    tid = _klient()
    s = SessionLocal(); email = s.get(Trader, tid).email; s.close()
    _wpis("welcome", email, "Welcome")
    _wpis("flash_offer", email, "Flash sale")
    wpisy = client.get("/api/admin/mail-log", headers=ADMIN_H).json()["entries"]
    po = {w["event"]: w["can_resend"] for w in wpisy}
    assert po["welcome"] is True and po["flash_offer"] is False
