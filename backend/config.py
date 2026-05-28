import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
    # substituted. If unset, get_job_log returns the JIL path instead.
    log_forwarder_url_template: str | None = os.getenv("LOG_FORWARDER_URL_TEMPLATE")
    # Optional override for the numeric status code table; format
    # "4=SUCCESS,5=FAILURE,..." merged on top of the defaults.
    status_code_overrides: str | None = os.getenv("STATUS_CODE_OVERRIDES")


settings = Settings()
