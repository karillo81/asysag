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


settings = Settings()
