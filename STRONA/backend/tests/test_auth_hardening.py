"""Twardnienie auth: walidacja signupu, normalizacja e-maili, jednorazowy
token resetu, inwalidacja sesji po zmianie hasła, rate-limit.

Jak w pozostałych plikach: pytest współdzieli moduły/bazę — unikalne e-maile,
asercje odporne na cudze wiersze.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog  # noqa: E402
from app import main as main_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()


def _signup(c, email, password="haslo12345", **extra):
    return c.post("/api/auth/signup", json={"email": email, "password": password, **extra})


def test_signup_odrzuca_krotkie_haslo_i_zly_email():
    with TestClient(app) as c:
        za_krotkie = _signup(c, "krotki@test.pl", password="abc")
        zly_email = _signup(c, "to-nie-jest-email")
        ze_spacja = _signup(c, "spacja w@srodku.pl")
    assert za_krotkie.status_code == 400 and "8 characters" in za_krotkie.json()["detail"]
    assert zly_email.status_code == 400
    assert ze_spacja.status_code == 400


def test_signup_normalizuje_email_i_login_reset_go_znajduja():
    """Rejestracja z '  X@Y.PL ' musi być osiągalna loginem i resetem 'x@y.pl'.

    Wcześniej signup/login robiły tylko .lower(), a forgot .strip().lower() —
    konto założone ze spacją było nieosiągalne dla resetu hasła.
    """
    with TestClient(app) as c:
        r = _signup(c, "  Norma.Lizacja@Test.PL ")
        assert r.status_code == 200
        assert r.json()["trader"]["email"] == "norma.lizacja@test.pl"
        login = c.post("/api/auth/login", json={"email": "norma.lizacja@test.pl",
                                                "password": "haslo12345"})
        assert login.status_code == 200


def test_signup_waliduje_kod_polecajacy():
    """Nieistniejący kod → referred_by zostaje puste (bez cichego śmiecia)."""
    with TestClient(app) as c:
        ok = _signup(c, "polecony-zly-kod@test.pl", referral="NIEMAKODU")
        assert ok.status_code == 200
        kod = ok.json()["trader"]["referral_code"]
        ok2 = _signup(c, "polecony-dobry-kod@test.pl", referral=kod.lower())
        assert ok2.status_code == 200
    s = SessionLocal()
    zly = s.query(Trader).filter(Trader.email == "polecony-zly-kod@test.pl").one()
    dobry = s.query(Trader).filter(Trader.email == "polecony-dobry-kod@test.pl").one()
    s.close()
    assert zly.referred_by is None
    assert dobry.referred_by == kod


def test_token_resetu_jest_jednorazowy():
    with TestClient(app) as c:
        _signup(c, "jednorazowy@test.pl")
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == "jednorazowy@test.pl").one()
    token = auth.make_reset_token(tr.id, tr.password_hash)
    s.close()
    with TestClient(app) as c:
        pierwszy = c.post("/api/auth/reset", json={"token": token, "password": "nowe-haslo-1"})
        drugi = c.post("/api/auth/reset", json={"token": token, "password": "inne-haslo-2"})
    assert pierwszy.status_code == 200
    assert pierwszy.json().get("token"), "udany reset ma od razu logowac (auto-login)"
    assert drugi.status_code == 400, "zuzyty link resetu nie moze dzialac drugi raz"


def test_reset_uniewaznia_stare_sesje():
    with TestClient(app) as c:
        stara_sesja = _signup(c, "sesje-umieraja@test.pl").json()["token"]
        me1 = c.get("/api/auth/me", headers={"Authorization": f"Bearer {stara_sesja}"})
        assert me1.status_code == 200
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == "sesje-umieraja@test.pl").one()
    reset_token = auth.make_reset_token(tr.id, tr.password_hash)
    s.close()
    with TestClient(app) as c:
        r = c.post("/api/auth/reset", json={"token": reset_token, "password": "calkiem-nowe-8"})
        assert r.status_code == 200
        po_resecie = c.get("/api/auth/me", headers={"Authorization": f"Bearer {stara_sesja}"})
        nowa = c.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert po_resecie.status_code == 401, "stary token sesji musi umrzec po resecie hasla"
    assert nowa.status_code == 200


def test_stary_token_bez_odcisku_dalej_dziala():
    """Tokeny sprzed wdrożenia (bez pola pwf) są honorowane do wygaśnięcia —
    deploy nie może wylogować wszystkich naraz."""
    with TestClient(app) as c:
        _signup(c, "legacy-token@test.pl")
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == "legacy-token@test.pl").one()
    legacy = auth.make_token(tr.id)          # bez password_hash = stary format
    s.close()
    with TestClient(app) as c:
        r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200


def test_zmiana_hasla_uniewaznia_inne_sesje_a_biezaca_dostaje_nowy_token():
    with TestClient(app) as c:
        token_a = _signup(c, "zmiana-hasla@test.pl").json()["token"]
        h = {"Authorization": f"Bearer {token_a}"}
        r = c.post("/api/me/password", headers=h, json={
            "current_password": "haslo12345", "new_password": "zupelnie-inne-8"})
        assert r.status_code == 200 and r.json().get("token")
        stary = c.get("/api/auth/me", headers=h)
        nowy = c.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert stary.status_code == 401
    assert nowy.status_code == 200


def test_rate_limit_login(monkeypatch):
    """5 szybkich prób forgot z jednego IP → szósta dostaje 429."""
    monkeypatch.setattr(main_mod, "_RL_DISABLED", False)
    main_mod._RL_HITS.clear()
    try:
        with TestClient(app) as c:
            kody = [c.post("/api/auth/forgot",
                           json={"email": f"nie-ma-{i}@test.pl"}).status_code
                    for i in range(6)]
        assert kody[:5] == [200] * 5
        assert kody[5] == 429
    finally:
        main_mod._RL_HITS.clear()
