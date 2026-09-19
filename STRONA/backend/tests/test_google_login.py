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

from datetime import datetime  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app import db as db_mod  # noqa: E402
from app import main as main_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import TelemetryEvent, Trader  # noqa: E402

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


def test_google_odbiera_konto_zalozone_za_klienta(monkeypatch):
    """Konto założone ZA klienta: wejście przez Google to odbiór konta —
    Google ręczy za adres tak samo jak klik w link zaproszenia. Bez tego
    klient korzystał z portalu, a panel wiecznie pokazywał „Awaiting Claim"."""
    s = SessionLocal()
    s.add(Trader(email="zalozone@gmail.com", password_hash=auth.hash_password("haslo12345"),
                 full_name="Za Klienta", referral_code="ZALOZ1", must_set_password=True))
    s.commit()
    tid = s.query(Trader).filter(Trader.email == "zalozone@gmail.com").first().id
    s.close()
    _wlacz(monkeypatch, _claims(email="zalozone@gmail.com", sub="sub-zalozone"))
    with TestClient(app) as c:
        assert c.post("/api/auth/google", json={"credential": "stub"}).status_code == 200
        # drugie wejście to już zwykłe logowanie, nie kolejny claim
        assert c.post("/api/auth/google", json={"credential": "stub"}).status_code == 200
    s = SessionLocal()
    assert s.get(Trader, tid).must_set_password is False
    claimy = s.query(TelemetryEvent).filter(
        TelemetryEvent.trader_id == tid,
        TelemetryEvent.name == "account_claimed").all()
    assert len(claimy) == 1 and "google" in (claimy[0].props or "")
    s.close()


def test_migracja_odbiera_konta_google_wstecznie():
    """Ofiary sprzed poprawki: google_sub ustawia tylko ścieżka Google, więc
    (must_set_password, google_sub) wskazuje je jednoznacznie. Claim dostaje
    datę pierwszego wejścia przez Google, nie datę naprawy."""
    s = SessionLocal()
    tr = Trader(email="wstecz@gmail.com", password_hash=auth.hash_password("haslo12345"),
                full_name="Wstecz", referral_code="WSTECZ1",
                must_set_password=True, google_sub="sub-wstecz")
    s.add(tr); s.commit(); tid = tr.id
    pierwsze = datetime(2026, 8, 1, 12, 0)
    s.add(TelemetryEvent(trader_id=tid, name="login",
                         props='{"google": true}', created_at=pierwsze))
    s.commit(); s.close()
    db_mod._odbierz_konta_google()
    s = SessionLocal()
    assert s.get(Trader, tid).must_set_password is False
    claim = s.query(TelemetryEvent).filter(
        TelemetryEvent.trader_id == tid,
        TelemetryEvent.name == "account_claimed").one()
    assert claim.created_at == pierwsze
    assert "google" in (claim.props or "")
    s.close()
    # idempotencja — drugi przebieg nie dokłada drugiego claimu
    db_mod._odbierz_konta_google()
    s = SessionLocal()
    assert s.query(TelemetryEvent).filter(
        TelemetryEvent.trader_id == tid,
        TelemetryEvent.name == "account_claimed").count() == 1
    s.close()


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


def test_google_signup_wysyla_welcome_a_login_nie_spamuje(monkeypatch):
    """Konto z Google omija bramkę kodu, więc welcome idzie od razu przy
    rejestracji (wspólna ścieżka _potwierdz_email). Kolejne logowania nie
    wysyłają nic — flaga email_verified czyni ścieżkę idempotentną."""
    wyslane = []
    monkeypatch.setattr(main_mod.notify, "send",
                        lambda ev, to, ctx=None: wyslane.append((ev, to)))
    _wlacz(monkeypatch, _claims(email="welcome-g@gmail.com", sub="sub-welcome-1"))
    with TestClient(app) as c:
        assert c.post("/api/auth/google", json={"credential": "stub"}).status_code == 200
    assert ("welcome", "welcome-g@gmail.com") in wyslane
    assert not any(ev == "verify_email" for ev, _ in wyslane), \
        "kod weryfikacyjny nie ma sensu — adres potwierdził Google"
    wyslane.clear()
    with TestClient(app) as c:
        assert c.post("/api/auth/google", json={"credential": "stub"}).status_code == 200
    assert wyslane == [], "zwykłe logowanie Google nie może słać maili"


def test_google_link_niezweryfikowanego_konta_dosyla_welcome(monkeypatch):
    """Trader zarejestrowany hasłem, który nigdy nie kliknął w link, po wejściu
    przez Google dostaje welcome (weryfikację załatwił Google), a kod znika."""
    s = SessionLocal()
    s.add(Trader(email="niezweryfikowany@gmail.com",
                 password_hash=auth.hash_password("haslo12345"),
                 full_name="Bez Weryfikacji", referral_code="BEZWER1",
                 email_verified=False, email_verify_code="123456"))
    s.commit(); s.close()
    wyslane = []
    monkeypatch.setattr(main_mod.notify, "send",
                        lambda ev, to, ctx=None: wyslane.append((ev, to)))
    _wlacz(monkeypatch, _claims(email="niezweryfikowany@gmail.com", sub="sub-niezw"))
    with TestClient(app) as c:
        assert c.post("/api/auth/google", json={"credential": "stub"}).status_code == 200
    assert ("welcome", "niezweryfikowany@gmail.com") in wyslane
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == "niezweryfikowany@gmail.com").first()
    assert tr.email_verified is True and tr.email_verify_code is None
    s.close()


def test_przycisk_google_tylko_z_konfiguracja(monkeypatch):
    with TestClient(app) as c:
        assert "accounts.google.com/gsi/client" not in c.get("/portal").text
    monkeypatch.setattr(main_mod.settings, "google_client_id", CID)
    with TestClient(app) as c:
        html = c.get("/portal").text
    assert "accounts.google.com/gsi/client" in html and 'id="g-btn"' in html
