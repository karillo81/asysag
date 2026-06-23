from abc import ABC, abstractmethod
from typing import Any


class AdapterError(Exception):
    """Raised by adapters for any expected failure (job not found, upstream unreachable, etc.)."""


class JobNotFound(AdapterError):
    pass


class AutoSysAdapter(ABC):
    """Abstract data-source interface. Mock and live implementations must be drop-in compatible."""

    @abstractmethod
    def get_job_status(self, job_name: str) -> dict[str, Any]:
        """Return the current status snapshot for one job. Raise JobNotFound if unknown."""

    @abstractmethod
    def list_jobs(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        """Return current-status snapshots for all jobs, optionally filtered by substring match on name."""

    @abstractmethod
    def get_job_history(self, job_name: str, days: int = 7) -> list[dict[str, Any]]:
        """Return historical runs for the job within the trailing window. Raise JobNotFound if unknown."""

    @abstractmethod
    def get_dependencies(self, job_name: str) -> dict[str, Any]:
        """Return upstream and downstream job names for the given job. Raise JobNotFound if unknown."""

    @abstractmethod
    def get_job_log(self, job_name: str, stream: str = "err") -> str:
        """Return the stderr (or stdout) excerpt from the most recent run.

        stream: 'err' or 'out'. Returns an empty string if no log is available.
        Raise JobNotFound if the job itself is unknown.
        """

    @abstractmethod
    def send_job_event(
        self, action_key: str, target: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a write action (sendevent-style) against AutoSys.

        `action_key` is a key from adapters.job_actions.ACTIONS; `target` is the
        job/machine name it applies to (ignored for scheduler-wide actions).
        Returns a result dict. Raises AdapterError on an unknown action or a
        failed request. THIS MUTATES AUTOSYS — callers must gate it behind the
        propose -> human-confirm flow.
        """

    def reset(self) -> None:
        """Reinitialise from the underlying data source. Optional; default no-op."""
