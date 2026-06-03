import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _resolve_session_secret() -> str:
    value = os.getenv("SESSION_SECRET")
    if value:
        return value
    generated = secrets.token_urlsafe(32)
    logger.warning(
        "SESSION_SECRET not set; generated an ephemeral secret. Sessions will "
        "not survive a backend restart. Set SESSION_SECRET in .env for stable sessions."
    )
    return generated

_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent
_DEFAULT_MOCK_DATA_DIR = _PROJECT_ROOT / "mock-data"
_DEFAULT_DOCS_DIR = _PROJECT_ROOT / "docs"
_DEFAULT_STATE_DIR = _BACKEND_DIR / "state"


def _str_to_bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    autosys_mode: str = os.getenv("AUTOSYS_MODE", "mock")
    litellm_model: str = os.getenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-5")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    backend_host: str = os.getenv("BACKEND_HOST", "127.0.0.1")
    backend_port: int = int(os.getenv("BACKEND_PORT", "8000"))
    mock_data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("MOCK_DATA_DIR", str(_DEFAULT_MOCK_DATA_DIR)))
    )
    docs_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DOCS_DIR", str(_DEFAULT_DOCS_DIR)))
    )
    state_dir: Path = field(
        default_factory=lambda: Path(os.getenv("STATE_DIR", str(_DEFAULT_STATE_DIR)))
    )

    # Live mode — only consulted when autosys_mode == "live".
    autosys_base_url: str | None = os.getenv("AUTOSYS_BASE_URL")
    autosys_user: str | None = os.getenv("AUTOSYS_USER")
    autosys_pass: str | None = os.getenv("AUTOSYS_PASS")
    autosys_verify_tls: bool = field(
        default_factory=lambda: _str_to_bool(os.getenv("AUTOSYS_VERIFY_TLS"), True)
    )
    autosys_timeout_seconds: float = float(os.getenv("AUTOSYS_TIMEOUT_SECONDS", "15"))
    # Optional Splunk/ELK/S3 log forwarder. Template gets {job} and {stream}
    # substituted. If unset, get_job_log falls back to local filesystem
    # read (see AUTOSYS_LOG_MOUNT_ROOT) and then to a path-only message.
    log_forwarder_url_template: str | None = os.getenv("LOG_FORWARDER_URL_TEMPLATE")
    # Optional local-filesystem root where the AutoSys agent's `job_logs/`
    # directory is mounted/synced/symlinked. When set, get_job_log reads
    # `{AUTOSYS_LOG_MOUNT_ROOT}/{filename}` (filename = basename of the JIL
    # `std_*_file` after `$AUTO_JOB_NAME` substitution).
    autosys_log_mount_root: str | None = os.getenv("AUTOSYS_LOG_MOUNT_ROOT")

    # Single-user login gate. See backend/auth.py.
    auth_username: str = os.getenv("AUTH_USERNAME", "root")
    auth_password: str = os.getenv("AUTH_PASSWORD", "changeme")
    session_secret: str = field(default_factory=_resolve_session_secret)
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", "604800"))
    # Optional override for the numeric status code table; format
    # "4=SUCCESS,5=FAILURE,..." merged on top of the defaults.
    status_code_overrides: str | None = os.getenv("STATUS_CODE_OVERRIDES")
    # Strategy for get_job_history. One of:
    #   "walk-runs" (default) — iterate `autorep -j NAME -w -r N` for N=0..MAX
    #     until autorep returns CAUAJM_E_10323. Works on every AutoSys we've
    #     tested; one HTTP request per historical run.
    #   "days-flag" — single `autorep -j NAME -w -d {days}` call. Documented
    #     in M7 sub-task 7, but field-tested instances return only the latest
    #     run summary regardless of days. Faster, less useful.
    autorep_history_strategy: str | None = os.getenv("AUTOSYS_AUTOREP_HISTORY_STRATEGY")


settings = Settings()

if settings.auth_password == "changeme":
    logger.warning(
        "AUTH_PASSWORD is the default 'changeme'. Change it in .env before "
        "exposing the app beyond your local machine."
    )
