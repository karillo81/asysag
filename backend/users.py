"""Persistent multi-user account store (SQLite).

Replaces the original single-env-user login gate. Each account has a PBKDF2
password hash, a role (`admin` or `operator`), and an active flag. Admins can
manage accounts; operators only use the app.

Passwords are hashed with stdlib PBKDF2-HMAC-SHA256 (no extra dependency).
The encoded hash carries its own algorithm/iterations/salt so the cost can be
raised later without a schema change:

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

On first init the table is seeded from the env bootstrap credentials so the
existing `root` login keeps working and the box is never left with zero admins.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROLES = ("admin", "operator")
_PBKDF2_ITERATIONS = 240_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MIN_PASSWORD_LEN = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT
);
"""


class UserError(Exception):
    """Base for account-management errors (callers map these to HTTP 4xx)."""


class UserNotFound(UserError):
    pass


class UserExists(UserError):
    pass


class InvalidUserInput(UserError):
    pass


class LastAdminError(UserError):
    """Raised when an operation would leave the system with no active admin."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = encoded.split("$")
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(digest, expected)


def validate_username(username: str) -> None:
    if not _USERNAME_RE.match(username or ""):
        raise InvalidUserInput(
            "username must be 1-64 chars: letters, digits, dot, dash, underscore"
        )


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LEN:
        raise InvalidUserInput(
            f"password must be at least {_MIN_PASSWORD_LEN} characters"
        )


def validate_role(role: str) -> None:
    if role not in ROLES:
        raise InvalidUserInput(f"role must be one of {ROLES}")


class UserStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # One shared connection across the FastAPI threadpool; serialise writes
        # (and read-modify-write invariants like the last-admin guard) with a lock.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------

    def bootstrap_admin(self, username: str, password: str) -> bool:
        """Seed the first admin from env creds if the table is empty.

        Returns True if a seed happened. Validation is relaxed here: the
        bootstrap account must work even if the env password is short (e.g. the
        default 'changeme'); we only refuse an empty username/password.
        """
        with self._lock:
            if self._count_rows() > 0:
                return False
            if not username or not password:
                logger.warning("user store: cannot seed admin — empty bootstrap creds")
                return False
            self._conn.execute(
                "INSERT INTO users (username, password_hash, role, is_active, created_at, created_by) "
                "VALUES (?, ?, 'admin', 1, ?, ?)",
                (username, hash_password(password), _now_iso(), "bootstrap"),
            )
            self._conn.commit()
            logger.info("user store: seeded bootstrap admin '%s'", username)
            return True

    # -- queries ---------------------------------------------------------

    def get(self, username: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_account(self, username: str) -> dict[str, Any] | None:
        """Public account dict (no password hash) or None if absent."""
        row = self.get(username)
        return self._public(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        """All accounts as public dicts (never includes password hashes)."""
        cur = self._conn.execute("SELECT * FROM users ORDER BY username")
        return [self._public(dict(r)) for r in cur.fetchall()]

    def verify(self, username: str, password: str) -> dict[str, Any] | None:
        """Return the public account dict on correct password + active, else None."""
        row = self.get(username)
        if row is None or not row["is_active"]:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return self._public(row)

    # -- mutations -------------------------------------------------------

    def create(
        self, username: str, password: str, role: str, *, created_by: str | None
    ) -> dict[str, Any]:
        validate_username(username)
        validate_password(password)
        validate_role(role)
        with self._lock:
            if self.get(username) is not None:
                raise UserExists(f"user already exists: {username}")
            self._conn.execute(
                "INSERT INTO users (username, password_hash, role, is_active, created_at, created_by) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (username, hash_password(password), role, _now_iso(), created_by),
            )
            self._conn.commit()
        return self._public(self.get(username))  # type: ignore[arg-type]

    def set_password(self, username: str, password: str) -> None:
        validate_password(password)
        with self._lock:
            if self.get(username) is None:
                raise UserNotFound(username)
            self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (hash_password(password), username),
            )
            self._conn.commit()

    def set_role(self, username: str, role: str) -> None:
        validate_role(role)
        with self._lock:
            row = self.get(username)
            if row is None:
                raise UserNotFound(username)
            if row["role"] == "admin" and role != "admin":
                self._guard_last_admin(username)
            self._conn.execute(
                "UPDATE users SET role = ? WHERE username = ?", (role, username)
            )
            self._conn.commit()

    def set_active(self, username: str, active: bool) -> None:
        with self._lock:
            row = self.get(username)
            if row is None:
                raise UserNotFound(username)
            if row["role"] == "admin" and not active:
                self._guard_last_admin(username)
            self._conn.execute(
                "UPDATE users SET is_active = ? WHERE username = ?",
                (1 if active else 0, username),
            )
            self._conn.commit()

    def delete(self, username: str) -> None:
        with self._lock:
            row = self.get(username)
            if row is None:
                raise UserNotFound(username)
            if row["role"] == "admin":
                self._guard_last_admin(username)
            self._conn.execute("DELETE FROM users WHERE username = ?", (username,))
            self._conn.commit()

    # -- internals -------------------------------------------------------

    def _count_rows(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def _active_admin_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()["n"]

    def _guard_last_admin(self, username: str) -> None:
        """Refuse an op that removes/demotes/deactivates the only active admin.

        Caller holds the lock and has confirmed `username` is currently an
        active admin being targeted.
        """
        row = self.get(username)
        target_is_active_admin = (
            row is not None and row["role"] == "admin" and bool(row["is_active"])
        )
        if target_is_active_admin and self._active_admin_count() <= 1:
            raise LastAdminError("cannot remove or demote the last active admin")

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": row["username"],
            "role": row["role"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "created_by": row["created_by"],
        }

    def close(self) -> None:
        self._conn.close()
