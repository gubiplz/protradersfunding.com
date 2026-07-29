"""Panel admina otwiera KONTO, nie wspólny token.

Wcześniej wejście do panelu dawał nagłówek `X-Admin-Token` wpisywany w UI —
jeden sekret dla wszystkich, bez śladu kto co zrobił. Teraz admin loguje się
normalnie i dostaje ten sam token sesji co trader, a uprawnienia daje flaga
`is_admin`. Te testy pilnują granicy: zwykły trader nie może wejść w admina.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
client = TestClient(app)


def _trader(email: str, haslo: str, admin: bool) -> None:
    s = SessionLocal()
    if not s.query(Trader).filter(Trader.email == email).first():
        s.add(Trader(email=email, password_hash=auth.hash_password(haslo),
                     full_name="Kto To", is_admin=admin, referral_code=email[:8].upper()))
        s.commit()
    s.close()


def _zaloguj(email: str, haslo: str):
    return client.post("/api/auth/login", json={"email": email, "password": haslo})


def test_admin_wchodzi_do_panelu_swoimi_danymi():
    _trader("szef@firma.pl", "tajne123", admin=True)
    r = _zaloguj("szef@firma.pl", "tajne123")
    assert r.status_code == 200
    dane = r.json()
    assert dane["trader"]["is_admin"] is True

    naglowek = {"Authorization": f"Bearer {dane['token']}"}
    assert client.get("/api/accounts", headers=naglowek).status_code == 200
    assert client.get("/api/auth/me", headers=naglowek).json()["is_admin"] is True


def test_zwykly_trader_nie_wejdzie_do_admina():
    _trader("zwykly@firma.pl", "tajne123", admin=False)
    token = _zaloguj("zwykly@firma.pl", "tajne123").json()["token"]
    r = client.get("/api/accounts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_bez_logowania_admin_jest_zamkniety():
    assert client.get("/api/accounts").status_code in (401, 403)


def test_zle_haslo_nie_wpuszcza():
    _trader("szef2@firma.pl", "tajne123", admin=True)
    assert _zaloguj("szef2@firma.pl", "nie-to-haslo").status_code == 401


def test_login_bez_malpy_dziala():
    """Pierwsze konto zakładane skryptem ma login 'admin', nie adres e-mail."""
    _trader("admin", "admin", admin=True)
    r = _zaloguj("admin", "admin")
    assert r.status_code == 200 and r.json()["trader"]["is_admin"] is True
