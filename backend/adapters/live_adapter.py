from typing import Any

from .base import AutoSysAdapter


class LiveAdapter(AutoSysAdapter):
    """Placeholder for the AutoSys REST API adapter. Implemented in milestone M7."""

    def __init__(self, base_url: str | None = None, api_token: str | None = None):
        self._base_url = base_url
        self._api_token = api_token

    def _not_yet(self) -> None:
        raise NotImplementedError(
            "live adapter not yet implemented — set AUTOSYS_MODE=mock or wait for M7"
        )

    def get_job_status(self, job_name: str) -> dict[str, Any]:
        self._not_yet()
        return {}

    def list_jobs(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        self._not_yet()
        return []

    def get_job_history(self, job_name: str, days: int = 7) -> list[dict[str, Any]]:
        self._not_yet()
        return []

    def get_dependencies(self, job_name: str) -> dict[str, Any]:
        self._not_yet()
        return {}

    def get_job_log(self, job_name: str, stream: str = "err") -> str:
        self._not_yet()
        return ""
