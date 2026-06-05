import logging
from pathlib import Path

from config import settings

from .autosys_client import AutoSysAPIError
from .base import AdapterError, AutoSysAdapter, JobNotFound
from .live_adapter import LiveAdapter
from .log_orchestrator import LogOrchestrator, load_host_map
from .mock_adapter import MockAdapter

logger = logging.getLogger(__name__)

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
            log_orchestrator=_build_log_orchestrator(),
            status_code_overrides=settings.status_code_overrides,
            autorep_history_strategy=settings.autorep_history_strategy,
        )
    raise ValueError(f"unknown AUTOSYS_MODE: {mode!r} (expected 'mock' or 'live')")


def _build_log_orchestrator() -> LogOrchestrator | None:
    """Construct the SFTP log orchestrator from settings, or None if disabled.

    A missing/invalid host-map file disables the feature with a warning rather
    than failing startup — get_job_log just keeps its path-only fallback.
    """
    config_path = settings.autosys_log_ssh_config
    if not config_path:
        return None
    try:
        host_map = load_host_map(config_path)
    except (OSError, ValueError) as e:
        logger.warning(
            "AUTOSYS_LOG_SSH_CONFIG=%s could not be loaded (%s); "
            "SFTP log fetch disabled.", config_path, e,
        )
        return None
    return LogOrchestrator(
        host_map,
        max_bytes=settings.autosys_log_ssh_max_bytes,
        connect_timeout=settings.autosys_log_ssh_timeout_seconds,
        cache_dir=settings.state_dir / "log_cache",
        cache_ttl_seconds=settings.autosys_log_cache_ttl_seconds,
        known_hosts_path=settings.autosys_log_ssh_known_hosts,
        skip_host_key_check=settings.autosys_log_ssh_insecure_skip_host_key,
    )
