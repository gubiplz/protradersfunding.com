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
def make_token(trader_id: int) -> str:
    return _serializer.dumps({"tid": trader_id})


def parse_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=TOKEN_MAX_AGE)
        return int(data["tid"])
    except (BadSignature, Exception):
        return None


# --- FastAPI dependencies ---
def current_trader(authorization: str | None = Header(default=None)) -> Trader:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Brak tokenu (zaloguj się)")
    tid = parse_token(authorization.split(" ", 1)[1].strip())
    if tid is None:
        raise HTTPException(401, "Token nieprawidłowy lub wygasł")
    session = SessionLocal()
    try:
        trader = session.get(Trader, tid)
        # Konto usunięte (Danger Zone) jest zanonimizowane, ale wiersz zostaje —
        # stary token nie może dalej działać.
        if not trader or trader.email.endswith("@removed.invalid"):
            raise HTTPException(401, "Trader nie istnieje")
        session.expunge(trader)
        return trader
    finally:
        session.close()


def require_admin(
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Admin przez stały token (panel) ALBO przez konto z is_admin."""
    if x_admin_token and secrets.compare_digest(x_admin_token, settings.admin_token):
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
    raise HTTPException(403, "Wymagane uprawnienia administratora")
