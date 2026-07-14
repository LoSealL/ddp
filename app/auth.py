import secrets
import hashlib
from datetime import datetime, timezone, timedelta

from fastapi import Cookie, HTTPException

from . import db

SESSION_DURATION = timedelta(days=7)


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100000)
    return h.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100000)
    return secrets.compare_digest(h.hex(), stored_hash)


def create_session_for_user(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + SESSION_DURATION).isoformat()
    db.create_session(token, user_id, expires)
    return token


async def get_current_user(session: str | None = Cookie(None)) -> dict:
    if not session:
        raise HTTPException(401, "Not authenticated")
    user = db.get_user_by_session(session)
    if not user:
        raise HTTPException(401, "Invalid or expired session")
    return user
