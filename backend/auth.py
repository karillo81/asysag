"""Authentication: account-backed login gate + signed-cookie sessions.

Credentials live in a persistent UserStore (SQLite); sessions are a signed,
timed token in an HttpOnly cookie carrying only the username. Each request
re-checks the account against the store so a disabled or deleted user loses
access immediately even though the cookie itself is still unexpired.

Roles: `admin` (manages accounts/settings) and `operator` (uses the app).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, TimedSerializer

from config import settings
from users import UserStore

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"

_serializer = TimedSerializer(settings.session_secret, salt="autosys-agent-session")

# Composition root for the account store. Seeding the first admin from the env
# bootstrap creds keeps the existing `root` login working and guarantees the
# box never starts with zero admins.
user_store = UserStore(settings.state_dir / "auth.sqlite")
if user_store.bootstrap_admin(settings.auth_username, settings.auth_password):
    logger.info("auth: bootstrapped first admin from env credentials")


def verify_credentials(username: str, password: str) -> dict | None:
    """Return the public account dict on success (active + correct password)."""
    return user_store.verify(username, password)


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


def current_account(request: Request) -> dict:
    """FastAPI dependency. Returns the live public account for a valid session.

    Raises 401 if there is no cookie, the token is invalid/expired, or the
    account has since been removed or deactivated.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated"
        )
    username = read_session_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired"
        )
    account = user_store.get_account(username)
    if account is None or not account["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account disabled"
        )
    return account


def current_user(request: Request) -> str:
    """FastAPI dependency. The authenticated username (back-compat helper)."""
    return current_account(request)["username"]


def require_admin(request: Request) -> dict:
    """FastAPI dependency. Like current_account but 403s for non-admins."""
    account = current_account(request)
    if account["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    return account
