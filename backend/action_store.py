"""Audit store for job write-actions (SQLite).

Every write action the agent proposes is recorded here and moves through a
small lifecycle:

    proposed --> executed | failed   (a human approved it)
             --> rejected            (a human declined it)

This is the data layer for the propose -> human-confirm -> execute flow. Phase 1
provides the store; Phase 2 wires the agent tool and the confirm/execute
endpoints on top. The full history (who proposed, who approved, the exact
params sent, the result) is the audit trail for a write-capable control plane.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = ("proposed", "executed", "failed", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL,
    target TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    tier TEXT NOT NULL,
    destructive INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'proposed',
    proposed_by TEXT,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);
"""


class ActionError(Exception):
    pass


class ActionNotFound(ActionError):
    pass


class ActionStateError(ActionError):
    """An action was resolved when it was no longer pending."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ActionStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    # -- create ----------------------------------------------------------

    def propose(
        self,
        action_key: str,
        target: str,
        *,
        tier: str,
        destructive: bool,
        params: dict[str, Any] | None,
        proposed_by: str | None,
    ) -> dict[str, Any]:
        action_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO actions (id, action_key, target, params, tier, "
                "destructive, status, proposed_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
                (
                    action_id,
                    action_key,
                    target,
                    json.dumps(params or {}),
                    tier,
                    1 if destructive else 0,
                    proposed_by,
                    _now_iso(),
                ),
            )
            self._conn.commit()
        return self.get(action_id)  # type: ignore[return-value]

    # -- read ------------------------------------------------------------

    def get(self, action_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        return self._public(row) if row else None

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM actions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM actions ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [self._public(r) for r in cur.fetchall()]

    # -- resolve ---------------------------------------------------------

    def mark_executed(self, action_id: str, approved_by: str, result: Any) -> dict[str, Any]:
        return self._resolve(action_id, "executed", approved_by, result=result)

    def mark_failed(self, action_id: str, approved_by: str, error: str) -> dict[str, Any]:
        return self._resolve(action_id, "failed", approved_by, error=error)

    def mark_rejected(self, action_id: str, approved_by: str) -> dict[str, Any]:
        return self._resolve(action_id, "rejected", approved_by)

    def _resolve(
        self,
        action_id: str,
        status: str,
        approved_by: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM actions WHERE id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise ActionNotFound(action_id)
            if row["status"] != "proposed":
                raise ActionStateError(
                    f"action {action_id} is already {row['status']}"
                )
            self._conn.execute(
                "UPDATE actions SET status = ?, approved_by = ?, resolved_at = ?, "
                "result = ?, error = ? WHERE id = ?",
                (
                    status,
                    approved_by,
                    _now_iso(),
                    json.dumps(result) if result is not None else None,
                    error,
                    action_id,
                ),
            )
            self._conn.commit()
        return self.get(action_id)  # type: ignore[return-value]

    # -- internals -------------------------------------------------------

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["destructive"] = bool(d["destructive"])
        d["params"] = json.loads(d["params"]) if d.get("params") else {}
        if d.get("result"):
            try:
                d["result"] = json.loads(d["result"])
            except (TypeError, ValueError):
                pass
        return d

    def close(self) -> None:
        self._conn.close()
