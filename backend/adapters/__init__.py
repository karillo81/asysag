from pathlib import Path

from .base import AdapterError, AutoSysAdapter, JobNotFound
from .live_adapter import LiveAdapter
from .mock_adapter import MockAdapter

__all__ = [
    "AdapterError",
    "AutoSysAdapter",
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
        return LiveAdapter()
    raise ValueError(f"unknown AUTOSYS_MODE: {mode!r} (expected 'mock' or 'live')")
