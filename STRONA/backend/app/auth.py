"""Autoryzacja traderów — onboarding/login.

Lekko, bez ciężkich zależności:
  - hasła: pbkdf2_hmac (stdlib),
  - tokeny: itsdangerous (podpisany, z czasem ważności) w nagłówku Bearer.
Admin rozpoznawany przez flagę is_admin LUB nagłówek X-Admin-Token (panel admina).
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import get_settings
from .db import SessionLocal
from .models import Trader

settings = get_settings()
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="trader-auth")
TOKEN_MAX_AGE = 60 * 60 * 24 * 14  # 14 dni


# --- hasła ---
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2$200000${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
        return secrets.compare_digest(dk.hex(), h)
    except Exception:
        return False


# --- tokeny ---
def _pw_fp(password_hash: str) -> str:
    """Odcisk aktualnego hasha hasła wszywany do tokenów.

    Zmiana/reset hasła zmienia odcisk, więc: (a) stare tokeny sesji przestają
    działać, (b) link resetu jest de facto jednorazowy — po użyciu hash się
    zmienia i ten sam link ma już nieaktualny odcisk. Tokeny bez pola "pwf"
    (sprzed wdrożenia) są honorowane do naturalnego wygaśnięcia.
    """
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:12]


def make_token(trader_id: int, password_hash: str | None = None) -> str:
    payload: dict = {"tid": trader_id}
    if password_hash:
        payload["pwf"] = _pw_fp(password_hash)
    return _serializer.dumps(payload)


def _parse_session(token: str) -> dict | None:
    try:
        data = _serializer.loads(token, max_age=TOKEN_MAX_AGE)
        int(data["tid"])
        return data
    except (BadSignature, Exception):
        return None


def parse_token(token: str) -> int | None:
    data = _parse_session(token)
    return int(data["tid"]) if data else None


# Osobna sol: token resetu nie moze dzialac jako token sesji (i odwrotnie).
_reset_serializer = URLSafeTimedSerializer(settings.secret_key, salt="pw-reset")
RESET_MAX_AGE = 60 * 60  # godzina


def make_reset_token(trader_id: int, password_hash: str | None = None) -> str:
    payload: dict = {"tid": trader_id}
    if password_hash:
        payload["pwf"] = _pw_fp(password_hash)
    return _reset_serializer.dumps(payload)


def parse_reset_token(token: str) -> tuple[int, str | None] | None:
    """Zwraca (trader_id, odcisk_hasla_z_tokenu|None) albo None gdy nieważny."""
    try:
        data = _reset_serializer.loads(token, max_age=RESET_MAX_AGE)
        return int(data["tid"]), data.get("pwf")
    except (BadSignature, Exception):
        return None


# Zaproszenie do konta założonego ZA klienta (must_set_password): mechanika jak
# przy resecie (odcisk hasła w tokenie = jednorazowość), ale ważność 7 dni.
# Godzinny link umierał, zanim klient doczytał mail z poświadczeniami, i cała
# furtka kończyła się na „forgot password". Ryzyko bez zmian: konto nie ma
# hasła znanego komukolwiek, więc link wart jest dokładnie tyle, co dostęp do
# skrzynki — jak każdy reset. Osobna sól, żeby godzinnego resetu i zaproszenia
# nie dało się użyć zamiennie.
_setup_serializer = URLSafeTimedSerializer(settings.secret_key, salt="pw-setup")
SETUP_MAX_AGE = 60 * 60 * 24 * 7  # 7 dni


def make_setup_token(trader_id: int, password_hash: str | None = None) -> str:
    payload: dict = {"tid": trader_id}
    if password_hash:
        payload["pwf"] = _pw_fp(password_hash)
    return _setup_serializer.dumps(payload)


def parse_setup_token(token: str) -> tuple[int, str | None] | None:
    try:
        data = _setup_serializer.loads(token, max_age=SETUP_MAX_AGE)
        return int(data["tid"]), data.get("pwf")
    except (BadSignature, Exception):
        return None


_verify_serializer = URLSafeTimedSerializer(settings.secret_key, salt="email-verify")
VERIFY_MAX_AGE = 60 * 60 * 24


def make_verify_token(trader_id: int) -> str:
    return _verify_serializer.dumps({"tid": trader_id})


def parse_verify_token(token: str) -> int | None:
    try:
        return int(_verify_serializer.loads(token, max_age=VERIFY_MAX_AGE)["tid"])
    except (BadSignature, Exception):
        return None


# --- FastAPI dependencies ---
def current_trader(authorization: str | None = Header(default=None)) -> Trader:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing token (sign in)")
    data = _parse_session(authorization.split(" ", 1)[1].strip())
    if data is None:
        raise HTTPException(401, "Invalid or expired token")
    session = SessionLocal()
    try:
        trader = session.get(Trader, int(data["tid"]))
        # Konto usunięte (Danger Zone) jest zanonimizowane, ale wiersz zostaje —
        # stary token nie może dalej działać.
        if not trader or trader.email.endswith("@removed.invalid"):
            raise HTTPException(401, "Trader not found")
        # Token z odciskiem hasła: reset/zmiana hasła uniewaznia stare sesje.
        pwf = data.get("pwf")
        if pwf and pwf != _pw_fp(trader.password_hash):
            raise HTTPException(401, "Invalid or expired token")
        session.expunge(trader)
        return trader
    finally:
        session.close()


def require_admin(
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Admin przez stały token (panel) ALBO przez konto z is_admin."""
    # Pusty ADMIN_TOKEN = tor tokenowy wyłączony; bez tego pierwszego warunku
    # instalacja bez env porównywałaby z pustym stringiem / dawnym "admin".
    if settings.admin_token and x_admin_token \
            and secrets.compare_digest(x_admin_token, settings.admin_token):
        return
    if authorization and authorization.lower().startswith("bearer "):
        tid = parse_token(authorization.split(" ", 1)[1].strip())
        if tid is not None:
            session = SessionLocal()
            try:
                tr = session.get(Trader, tid)
                if tr and tr.is_admin:
                    return
            finally:
                session.close()
    raise HTTPException(403, "Administrator rights required")
