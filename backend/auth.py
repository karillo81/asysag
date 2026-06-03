"""Single-user login gate.

This is intentionally minimal: one username/password pair driven by env vars,
plus a signed timed token stored in an HttpOnly cookie. Not a multi-tenant
identity system — see noble-cooking-swan plan, "Out of scope".
"""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, TimedSerializer

from config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"

_serializer = TimedSerializer(settings.session_secret, salt="autosys-agent-session")


def verify_credentials(username: str, password: str) -> bool:
    """Constant-time username + password check."""
    user_ok = hmac.compare_digest(username.encode(), settings.auth_username.encode())
    pass_ok = hmac.compare_digest(password.encode(), settings.auth_password.encode())
    return user_ok and pass_ok


def make_session_token(username: str) -> str:
    return _serializer.dumps({"u": username})


def read_session_token(token: str) -> str | None:
    """Return the username inside a valid, non-expired token, else None."""
    try:
        payload = _serializer.loads(token, max_age=settings.session_ttl_seconds)
    except (SignatureExpired, BadSignature, Exception):
        return None
    if not isinstance(payload, dict):
        return None
    user = payload.get("u")
    return user if isinstance(user, str) else None


def current_user(request: Request) -> str:
    """FastAPI dependency. Raises 401 unless a valid session cookie is present."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user = read_session_token(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    return user
