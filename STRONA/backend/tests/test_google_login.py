"""„Sign in with Google" — /api/auth/google.

Feature jest env-gated (GOOGLE_CLIENT_ID): bez konfiguracji endpoint zwraca
404 jak /admin. Testy podmieniają settings w locie (monkeypatch) i stubują
_google_tokeninfo — zero sieci, a walidacja aud/iss/email_verified zostaje
po naszej stronie, więc to ją właśnie sprawdzamy.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app import main as main_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()

CID = "test-client-id.apps.googleusercontent.com"


def _claims(**nadpisz):
    c = {"aud": CID, "iss": "https://accounts.google.com",
         "email": "guser@gmail.com", "email_verified": "true",
         "sub": "108123456789012345678", "name": "Google User"}
    c.update(nadpisz)
    return c


def _wlacz(monkeypatch, claims):
    monkeypatch.setattr(main_mod.settings, "google_client_id", CID)
    monkeypatch.setattr(main_mod, "_google_tokeninfo", lambda cred: dict(claims))


def test_bez_konfiguracji_endpoint_nie_istnieje():
    with TestClient(app) as c:
        r = c.post("/api/auth/google", json={"credential": "cokolwiek"})
    assert r.status_code == 404


def test_google_zaklada_konto_i_loguje(monkeypatch):
    _wlacz(monkeypatch, _claims())
    with TestClient(app) as c:
        r = c.post("/api/auth/google", json={"credential": "stub"})
        assert r.status_code == 200
        dane = r.json()
        assert dane["trader"]["email"] == "guser@gmail.com"
        assert dane["trader"]["referral_code"], "konto Google też dostaje kod polecający"
        # token działa jak po zwykłym logowaniu
        me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {dane['token']}"})
        assert me.status_code == 200 and me.json()["email"] == "guser@gmail.com"
        # adres potwierdził Google — bramka weryfikacyjna pominięta,
        # regulamin zaakceptowany klauzulą pod przyciskiem
        s = SessionLocal()
        tr = s.query(Trader).filter(Trader.email == "guser@gmail.com").first()
        assert tr.email_verified is True
        assert tr.terms_accepted_at is not None
        assert tr.google_sub == "108123456789012345678"
        s.close()
        # hasła nie ma — losowego hasha nie da się trafić zwykłym loginem
        zle = c.post("/api/auth/login", json={"email": "guser@gmail.com", "password": "haslo12345"})
        assert zle.status_code == 401


def test_google_podpina_sie_do_istniejacego_konta(monkeypatch):
    s = SessionLocal()
    s.add(Trader(email="stary@gmail.com", password_hash=auth.hash_password("haslo12345"),
                 full_name="Stary Trader", referral_code="STARY1", email_verified=False))
    s.commit()
    stary_id = s.query(Trader).filter(Trader.email == "stary@gmail.com").first().id
    s.close()
    _wlacz(monkeypatch, _claims(email="stary@gmail.com", sub="sub-starego-konta"))
    with TestClient(app) as c:
        r = c.post("/api/auth/google", json={"credential": "stub"})
    assert r.status_code == 200
    assert r.json()["trader"]["id"] == stary_id, "istniejące konto ma być podpięte, nie zdublowane"
    s = SessionLocal()
    tr = s.get(Trader, stary_id)
    assert tr.google_sub == "sub-starego-konta"
    assert tr.email_verified is True, "Google potwierdził adres — bramka znika"
    # stare hasło dalej działa (podpięcie niczego nie odbiera)
    s.close()
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"email": "stary@gmail.com",
                                               "password": "haslo12345"}).status_code == 200


def test_zla_publicznosc_tokenu_odrzucona(monkeypatch):
    """Token wystawiony dla CUDZEJ aplikacji (inne aud) nie loguje."""
    _wlacz(monkeypatch, _claims(aud="ktos-inny.apps.googleusercontent.com"))
    with TestClient(app) as c:
        assert c.post("/api/auth/google", json={"credential": "stub"}).status_code == 401


def test_niezweryfikowany_email_google_odrzucony(monkeypatch):
    _wlacz(monkeypatch, _claims(email_verified="false"))
    with TestClient(app) as c:
        assert c.post("/api/auth/google", json={"credential": "stub"}).status_code == 401


def test_google_signup_honoruje_kod_polecajacy(monkeypatch):
    s = SessionLocal()
    s.add(Trader(email="partner@test.pl", password_hash=auth.hash_password("haslo12345"),
                 full_name="Partner", referral_code="PARTNER9"))
    s.commit(); s.close()
    _wlacz(monkeypatch, _claims(email="polecony@gmail.com", sub="sub-poleconego"))
    with TestClient(app) as c:
        r = c.post("/api/auth/google", json={"credential": "stub", "referral": "partner9"})
    assert r.status_code == 200
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == "polecony@gmail.com").first()
    assert tr.referred_by == "PARTNER9"
    s.close()


def test_przycisk_google_tylko_z_konfiguracja(monkeypatch):
    with TestClient(app) as c:
        assert "accounts.google.com/gsi/client" not in c.get("/portal").text
    monkeypatch.setattr(main_mod.settings, "google_client_id", CID)
    with TestClient(app) as c:
        html = c.get("/portal").text
    assert "accounts.google.com/gsi/client" in html and 'id="g-btn"' in html
