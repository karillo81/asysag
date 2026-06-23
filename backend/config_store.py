"""Editable runtime settings: a curated, persistent overlay on top of env.

Settings are frozen at import (see config.py), and in Docker the base values
are baked into the container at creation time. To let an admin change settings
from the app, we keep a small writable overlay file in the persistent state
volume (`state/overrides.env`). `config.py` applies it over the process
environment *before* building Settings, and a service restart re-reads it.

Only a curated whitelist is editable — structural keys (host/port/paths,
SESSION_SECRET) are deliberately excluded so the app can't be configured into
a broken or unreachable state from the UI. Secrets are write-only over the API:
their values are masked on read and only overwritten when a new value is sent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class InvalidSettingInput(Exception):
    """A settings update referenced an unknown key or an invalid value."""


@dataclass(frozen=True)
class Spec:
    key: str            # environment variable name
    label: str          # human label for the UI
    group: str          # UI grouping
    kind: str = "str"   # str | bool | int | float
    secret: bool = False
    attr: str | None = None  # Settings attribute for the effective value;
    #                          None => read the effective value from os.environ
    help: str = ""


# Curated whitelist. Order is the display order within each group.
SPECS: tuple[Spec, ...] = (
    # -- LLM ------------------------------------------------------------
    Spec("LITELLM_MODEL", "Model", "LLM", attr="litellm_model",
         help="LiteLLM model id. Gemma 4 via Google AI Studio (reuses GEMINI_API_KEY, no endpoint): gemini/gemma-4-31b-it"),
    Spec("LITELLM_API_BASE", "Endpoint URL", "LLM", attr="litellm_api_base",
         help="OpenAI-compatible endpoint (vLLM/TGI/HF Endpoint); blank uses the provider default"),
    Spec("LITELLM_API_KEY", "Endpoint API key", "LLM", secret=True,
         attr="litellm_api_key", help="Bearer token the endpoint expects (HF token / vLLM key)"),
    Spec("ANTHROPIC_API_KEY", "Anthropic API key", "LLM", secret=True,
         attr="anthropic_api_key"),
    Spec("GEMINI_API_KEY", "Gemini API key", "LLM", secret=True),
    Spec("OPENAI_API_KEY", "OpenAI API key", "LLM", secret=True),
    Spec("AZURE_API_KEY", "Azure OpenAI key", "LLM", secret=True),
    # -- AutoSys --------------------------------------------------------
    Spec("AUTOSYS_MODE", "Mode (mock/live)", "AutoSys", attr="autosys_mode"),
    Spec("AUTOSYS_BASE_URL", "AEWS base URL", "AutoSys", attr="autosys_base_url"),
    Spec("AUTOSYS_USER", "AEWS username", "AutoSys", attr="autosys_user"),
    Spec("AUTOSYS_PASS", "AEWS password", "AutoSys", secret=True, attr="autosys_pass"),
    Spec("AUTOSYS_VERIFY_TLS", "Verify TLS", "AutoSys", kind="bool",
         attr="autosys_verify_tls"),
    Spec("AUTOSYS_TIMEOUT_SECONDS", "Request timeout (s)", "AutoSys", kind="float",
         attr="autosys_timeout_seconds"),
    # -- Logs -----------------------------------------------------------
    Spec("LOG_FORWARDER_URL_TEMPLATE", "Log forwarder URL template", "Logs",
         attr="log_forwarder_url_template"),
    Spec("AUTOSYS_LOG_MOUNT_ROOT", "Log mount root", "Logs",
         attr="autosys_log_mount_root"),
    # -- Behaviour ------------------------------------------------------
    Spec("STATUS_CODE_OVERRIDES", "Status code overrides", "Behaviour",
         attr="status_code_overrides", help='e.g. "4=SUCCESS,5=FAILURE"'),
    Spec("AUTOSYS_AUTOREP_HISTORY_STRATEGY", "History strategy", "Behaviour",
         attr="autorep_history_strategy", help="walk-runs | days-flag"),
    Spec("SESSION_TTL_SECONDS", "Session lifetime (s)", "Behaviour", kind="int",
         attr="session_ttl_seconds"),
    Spec("CAPTURE_TRAINING_DATA", "Capture LLM calls for training", "Behaviour",
         kind="bool", attr="capture_training_data",
         help="Log every (redacted) LLM request/response to the distillation dataset"),
    # -- Write actions --------------------------------------------------
    Spec("WRITES_ENABLED", "Enable job write-actions", "Writes", kind="bool",
         attr="writes_enabled",
         help="Master switch. OFF = read-only. ON enables non-destructive actions; destructive still need the allowlist. Every write is human-confirmed."),
    Spec("WRITES_ALLOWLIST", "Action allowlist", "Writes", attr="writes_allowlist",
         help="Comma-separated action keys. Empty = all non-destructive allowed, destructive blocked. e.g. delete_job,stop_demon to enable those."),
)

_BY_KEY = {s.key: s for s in SPECS}
EDITABLE_KEYS = frozenset(_BY_KEY)


# -- overlay file I/O ----------------------------------------------------

def read_overrides(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE overlay file (blank/`#` lines ignored)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in EDITABLE_KEYS:
            out[key] = value
    return out


def apply_overrides(path: Path) -> dict[str, str]:
    """Overlay the file's whitelisted keys onto os.environ (override wins).

    Called from config.py before Settings is built. Returns what was applied.
    """
    overrides = read_overrides(path)
    for key, value in overrides.items():
        os.environ[key] = value
    return overrides


def write_overrides(path: Path, updates: dict[str, Any]) -> list[str]:
    """Merge validated updates into the overlay file. Returns changed keys.

    - Unknown/structural keys raise InvalidSettingInput.
    - Secret keys whose incoming value is empty/None are left unchanged
      (write-only semantics: blank means "keep the current secret").
    """
    current = read_overrides(path)
    changed: list[str] = []
    for key, raw in updates.items():
        spec = _BY_KEY.get(key)
        if spec is None:
            raise InvalidSettingInput(f"not an editable setting: {key}")
        if spec.secret and (raw is None or str(raw) == ""):
            continue  # keep existing secret
        normalised = _normalise(spec, raw)
        if current.get(key) != normalised:
            current[key] = normalised
            changed.append(key)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{k}={current[k]}\n" for k in sorted(current))
        header = "# Managed by AutoSys Agent settings UI. Do not hand-edit while running.\n"
        path.write_text(header + body, encoding="utf-8")
    return changed


def _normalise(spec: Spec, raw: Any) -> str:
    value = "" if raw is None else str(raw).strip()
    if spec.kind == "bool":
        if value.lower() in ("1", "true", "yes", "on"):
            return "true"
        if value.lower() in ("0", "false", "no", "off"):
            return "false"
        raise InvalidSettingInput(f"{spec.key} must be a boolean")
    if spec.kind == "int":
        try:
            return str(int(value))
        except ValueError:
            raise InvalidSettingInput(f"{spec.key} must be an integer")
    if spec.kind == "float":
        try:
            return str(float(value))
        except ValueError:
            raise InvalidSettingInput(f"{spec.key} must be a number")
    return value


# -- masked view for the API --------------------------------------------

def _mask(value: str | None) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) > 8 else ""
    return "••••" + tail


def masked_view(settings: Any, path: Path) -> list[dict[str, Any]]:
    """Effective settings for the admin UI; secret values never leave masked."""
    overrides = read_overrides(path)
    rows: list[dict[str, Any]] = []
    for spec in SPECS:
        if spec.attr is not None:
            effective = getattr(settings, spec.attr, None)
        else:
            effective = os.environ.get(spec.key)
        effective_str = "" if effective is None else str(effective)
        row: dict[str, Any] = {
            "key": spec.key,
            "label": spec.label,
            "group": spec.group,
            "kind": spec.kind,
            "secret": spec.secret,
            "help": spec.help,
            "is_overridden": spec.key in overrides,
        }
        if spec.secret:
            row["is_set"] = bool(effective_str)
            row["value"] = _mask(effective_str)
        else:
            row["value"] = effective_str
        rows.append(row)
    return rows


def grouped_view(settings: Any, path: Path) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in masked_view(settings, path):
        groups.setdefault(row["group"], []).append(row)
    return groups


def validate_keys(keys: Iterable[str]) -> None:
    unknown = [k for k in keys if k not in EDITABLE_KEYS]
    if unknown:
        raise InvalidSettingInput(f"not editable: {', '.join(unknown)}")
