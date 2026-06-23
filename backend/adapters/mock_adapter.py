import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .base import AdapterError, AutoSysAdapter, JobNotFound
from .job_actions import get_action


DEFAULT_SCENARIO = "etl_failure"


class MockAdapter(AutoSysAdapter):
    """Reads operational state from on-disk JSON.

    Job definitions and dependencies are shared across scenarios (they
    describe the structure of the jobs themselves). Per-scenario overrides
    are:
      - job_status.json      — required
      - job_history.json     — optional; falls back to mock-data/jobs/
      - logs/{job}.{err|out} — optional; falls back to mock-data/logs/
    """

    def __init__(self, data_dir: Path, scenario: str = DEFAULT_SCENARIO):
        self._dir = data_dir
        self._scenario = scenario
        # Records every write action so tests/scenarios can assert on them
        # without touching a real scheduler.
        self.sent_events: list[dict[str, Any]] = []
        self._load_all()

    @property
    def scenario(self) -> str:
        return self._scenario

    def _scenario_dir(self) -> Path:
        return self._dir / "scenarios" / self._scenario

    def _load_all(self) -> None:
        self._definitions = self._load_path(self._dir / "jobs" / "job_definitions.json")
        self._dependencies = self._load_path(self._dir / "jobs" / "job_dependencies.json")

        scenario_dir = self._scenario_dir()
        status_path = scenario_dir / "job_status.json"
        if not status_path.exists():
            raise FileNotFoundError(
                f"scenario '{self._scenario}' is missing job_status.json at {status_path}"
            )
        self._status = self._load_path(status_path)

        history_path = scenario_dir / "job_history.json"
        if not history_path.exists():
            history_path = self._dir / "jobs" / "job_history.json"
        self._history = self._load_path(history_path, default={})

    def reset(self) -> None:
        """Reload the currently active scenario from disk."""
        self._load_all()

    def load_scenario(self, name: str) -> None:
        """Switch to a different scenario and reload all state from disk."""
        self._scenario = name
        self._load_all()

    def send_job_event(
        self, action_key: str, target: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Simulate a write action: validate + record it, never touch AutoSys.

        For job-targeted actions the target must be a known job, mirroring how
        the live adapter would fail on an unknown job.
        """
        action = get_action(action_key)
        if action is None:
            raise AdapterError(f"unknown action: {action_key}")
        if action.target == "job":
            self._require(target)  # raises JobNotFound if unknown
        record = {
            "action": action_key,
            "endpoint": action.endpoint,
            "target": target,
            "params": dict(params or {}),
        }
        self.sent_events.append(record)
        return {"status": "ok", "simulated": True, **record}

    def _load_path(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            if default is not None:
                return default
            raise FileNotFoundError(f"mock data missing: {path}")
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def _require(self, job_name: str) -> dict[str, Any]:
        snapshot = self._status.get(job_name)
        if snapshot is None:
            raise JobNotFound(f"unknown job: {job_name}")
        return snapshot

    def get_job_status(self, job_name: str) -> dict[str, Any]:
        snapshot = self._require(job_name)
        definition = self._definitions.get(job_name, {})
        return {**definition, **snapshot}

    def list_jobs(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for name, snapshot in self._status.items():
            if name_filter and name_filter.lower() not in name.lower():
                continue
            definition = self._definitions.get(name, {})
            results.append({**definition, **snapshot})
        return results

    def get_job_history(self, job_name: str, days: int = 7) -> list[dict[str, Any]]:
        self._require(job_name)
        runs = self._history.get(job_name, [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filtered: list[dict[str, Any]] = []
        for run in runs:
            try:
                start = datetime.fromisoformat(run["start"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if start >= cutoff:
                filtered.append(run)
        return filtered

    def get_dependencies(self, job_name: str) -> dict[str, Any]:
        self._require(job_name)
        edges = self._dependencies.get("edges", [])
        upstream = [src for src, dst in edges if dst == job_name]
        downstream = [dst for src, dst in edges if src == job_name]
        return {"job": job_name, "upstream": upstream, "downstream": downstream}

    def get_job_log(self, job_name: str, stream: str = "err") -> str:
        self._require(job_name)
        if stream not in ("err", "out"):
            raise ValueError("stream must be 'err' or 'out'")
        candidates = [
            self._scenario_dir() / "logs" / f"{job_name}.{stream}",
            self._dir / "logs" / f"{job_name}.{stream}",
        ]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""
