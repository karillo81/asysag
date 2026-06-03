from pathlib import Path

from config import settings

from .autosys_client import AutoSysAPIError
from .base import AdapterError, AutoSysAdapter, JobNotFound
from .live_adapter import LiveAdapter
from .mock_adapter import MockAdapter

__all__ = [
    "AdapterError",
    "AutoSysAdapter",
    "AutoSysAPIError",
    "JobNotFound",
    "LiveAdapter",
    "MockAdapter",
    "get_adapter",
]


def get_adapter(mode: str, mock_data_dir: Path | None = None) -> AutoSysAdapter:
    if mode == "mock":
        if mock_data_dir is None:
            raise ValueError("mock_data_dir is required when mode='mock'")
        return MockAdapter(mock_data_dir)
    if mode == "live":
        missing = [
            name
            for name, value in (
                ("AUTOSYS_BASE_URL", settings.autosys_base_url),
                ("AUTOSYS_USER", settings.autosys_user),
                ("AUTOSYS_PASS", settings.autosys_pass),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "live mode requires: " + ", ".join(missing) + " in environment"
            )
        return LiveAdapter(
            base_url=settings.autosys_base_url,
            username=settings.autosys_user,
            password=settings.autosys_pass,
            verify_tls=settings.autosys_verify_tls,
            timeout_seconds=settings.autosys_timeout_seconds,
            log_forwarder_url_template=settings.log_forwarder_url_template,
            log_mount_root=settings.autosys_log_mount_root,
            status_code_overrides=settings.status_code_overrides,
            autorep_history_strategy=settings.autorep_history_strategy,
        )
    raise ValueError(f"unknown AUTOSYS_MODE: {mode!r} (expected 'mock' or 'live')")
