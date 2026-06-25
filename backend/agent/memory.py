"""Persistent agent memory (SQLite).

Owns the incidents knowledge base — past failures plus their resolutions.
On first init, seeds from `mock-data/incidents/incident_history.json` so the
agent has prior cases to reason about. New incidents recorded by the agent
after this point would join the same table (record API not yet exposed).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    date TEXT,
    job_name TEXT,
    error_signature TEXT,
    error_excerpt TEXT,
    summary TEXT,
    resolution TEXT,
    resolved_by TEXT,
    resolution_minutes INTEGER,
    runbook_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_job ON incidents(job_name);
CREATE INDEX IF NOT EXISTS idx_incidents_signature ON incidents(error_signature);
"""


class Memory:
    def __init__(self, db_path: Path, seed_incidents_path: Path | None = None):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # The single connection is shared across the FastAPI threadpool (sync
        # tools run in worker threads). Serialise access so a streamed
        # investigation can't trigger SQLITE_MISUSE ("bad parameter or other
        # API misuse") by touching the connection from two threads at once.
        self._lock = threading.Lock()
        if seed_incidents_path and self._table_empty("incidents"):
            self._seed_incidents(seed_incidents_path)

    def _table_empty(self, table: str) -> bool:
        cur = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return cur.fetchone()["n"] == 0

    def _seed_incidents(self, path: Path) -> None:
        if not path.exists():
            logger.warning("memory: incident seed file missing: %s", path)
            return
        with path.open(encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("incidents", [])
        if not rows:
            return
        cols = (
            "id",
            "date",
            "job_name",
            "error_signature",
            "error_excerpt",
            "summary",
            "resolution",
            "resolved_by",
            "resolution_minutes",
            "runbook_ref",
        )
        values = [
            tuple(row.get(c) for c in cols) for row in rows
        ]
        placeholders = ", ".join("?" for _ in cols)
        self._conn.executemany(
            f"INSERT OR REPLACE INTO incidents ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        logger.info("memory: seeded %d incidents from %s", len(rows), path)

    def find_similar_incidents(
        self,
        job_name: str | None = None,
        error_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        if not job_name and not error_pattern:
            raise ValueError("supply at least one of job_name or error_pattern")
        clauses: list[str] = []
        params: list[str] = []
        if job_name:
            clauses.append("LOWER(job_name) LIKE ?")
            params.append(f"%{job_name.lower()}%")
        if error_pattern:
            clauses.append("(LOWER(error_signature) LIKE ? OR LOWER(error_excerpt) LIKE ?)")
            params.extend([f"%{error_pattern.lower()}%", f"%{error_pattern.lower()}%"])
        sql = (
            "SELECT * FROM incidents WHERE "
            + " AND ".join(clauses)
            + " ORDER BY date DESC"
        )
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
