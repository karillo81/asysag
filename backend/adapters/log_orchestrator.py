"""LogOrchestrator — fetch AutoSys job log content over SFTP.

The AutoSys REST API exposes no log *content* — only the JIL-configured
`std_err_file` / `std_out_file` *path*. When those logs live on agent hosts
the backend can reach over SSH, this component connects to the host that ran
the job, tails the configured log file, and returns the text so
`LiveAdapter.get_job_log` can hand real content to the agent.

This is the "configured SSH jump per machine" option the tech-stack doc flags
as privileged and security-sensitive — so the defaults here are conservative:
host-key verification is on, the fetch path comes only from JIL (never from
user/LLM input), reads are byte-bounded, and every failure path returns None
so the adapter falls back to its existing path-only message rather than
raising into the agent loop.

Design notes:
  * On-demand: invoked from get_job_log after the local-mount fast path and
    before the path-only fallback.
  * Multiple machines: a JSON host map translates the JIL `machine` name to an
    SSH endpoint. Per-machine entries inherit from a `default` entry; a machine
    not in the map falls back to `default` with its own name used verbatim as
    the hostname (covers the common "agent hostnames == AutoSys machine names,
    one shared key" topology).
  * Tail, not whole file: logs can be GB-scale; we read only the last
    `max_bytes` so the agent gets the relevant end without OOM risk.
  * Cache: a short-TTL disk cache under state/log_cache keeps repeated
    questions in one investigation from re-opening an SSH connection.

paramiko is an optional dependency (`pip install '.[ssh]'`) and is imported
lazily. If it isn't installed, or no host map is configured, the orchestrator
is simply disabled and nothing about the existing behaviour changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSHEndpoint:
    host: str
    user: str
    key_path: str
    port: int = 22
    passphrase: str | None = None


def load_host_map(path: str | Path) -> dict[str, dict]:
    """Load the JSON host map. Raises OSError / ValueError on a bad file.

    Shape::

        {
          "default":              {"user": "autosys", "key_path": "/.../id_logs", "port": 22},
          "etl-prod-01.internal": {"host": "10.0.4.11"},
          "fin-prod-02.internal": {"host": "10.0.4.12", "user": "svc_autosys"}
        }
    """
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("log SSH config must be a JSON object")
    return data


class LogOrchestrator:
    def __init__(
        self,
        host_map: dict[str, dict],
        *,
        max_bytes: int = 65536,
        connect_timeout: float = 10.0,
        cache_dir: str | Path | None = None,
        cache_ttl_seconds: float = 60.0,
        known_hosts_path: str | None = None,
        skip_host_key_check: bool = False,
        sftp_factory: Callable[[SSHEndpoint], Any] | None = None,
    ):
        self._host_map = host_map or {}
        self._default = self._host_map.get("default", {})
        self._max_bytes = max_bytes
        self._connect_timeout = connect_timeout
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_ttl = cache_ttl_seconds
        self._known_hosts_path = known_hosts_path
        self._skip_host_key_check = skip_host_key_check
        # Seam for tests: a callable(endpoint) -> context manager yielding an
        # object exposing paramiko-SFTP-shaped `.stat(path)` and
        # `.open(path, "rb")`. Defaults to a real paramiko session.
        self._sftp_factory = sftp_factory or self._paramiko_session

    @property
    def enabled(self) -> bool:
        """True if any host config exists. A lone `default` entry still serves
        unlisted machines (their name becomes the hostname)."""
        return bool(self._host_map)

    def fetch(self, machine: str | None, path: str) -> str | None:
        """Return the tail of `path` from `machine`, or None to fall back.

        Never raises: any resolution / connection / read failure is logged and
        returns None so the caller uses its path-only message instead.
        """
        if not self.enabled or not path:
            return None

        endpoint = self._resolve(machine)
        if endpoint is None:
            logger.warning(
                "log orchestrator: no usable SSH endpoint for machine %r "
                "(need host + user + key_path)", machine,
            )
            return None

        cached = self._cache_get(endpoint, path)
        if cached is not None:
            return cached

        try:
            data = self._download_tail(endpoint, path)
        except Exception as e:  # paramiko raises a wide variety; fail soft.
            logger.warning(
                "log orchestrator: fetch of %s from %s failed: %s",
                path, endpoint.host, e,
            )
            return None

        if data is None:
            return None

        text = data.decode("utf-8", errors="replace")
        self._cache_put(endpoint, path, text)
        return text

    # -- endpoint resolution -------------------------------------------------

    def _resolve(self, machine: str | None) -> SSHEndpoint | None:
        entry = self._host_map.get(machine, {}) if machine else {}
        merged = {**self._default, **entry}
        host = merged.get("host") or machine
        user = merged.get("user")
        key_path = merged.get("key_path")
        if not host or not user or not key_path:
            return None
        return SSHEndpoint(
            host=host,
            user=user,
            key_path=key_path,
            port=int(merged.get("port", 22)),
            passphrase=merged.get("passphrase"),
        )

    # -- download ------------------------------------------------------------

    def _download_tail(self, endpoint: SSHEndpoint, path: str) -> bytes | None:
        with self._sftp_factory(endpoint) as sftp:
            try:
                st = sftp.stat(path)
            except FileNotFoundError:
                logger.warning(
                    "log orchestrator: %s not found on %s", path, endpoint.host
                )
                return None
            size = int(getattr(st, "st_size", 0) or 0)
            with sftp.open(path, "rb") as fh:
                if size > self._max_bytes:
                    fh.seek(size - self._max_bytes)
                return fh.read()

    @contextmanager
    def _paramiko_session(self, endpoint: SSHEndpoint) -> Iterator[Any]:
        import paramiko  # lazy: optional dependency

        client = paramiko.SSHClient()
        if self._skip_host_key_check:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            if self._known_hosts_path:
                client.load_host_keys(self._known_hosts_path)
            else:
                client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=endpoint.host,
            port=endpoint.port,
            username=endpoint.user,
            key_filename=endpoint.key_path,
            passphrase=endpoint.passphrase,
            timeout=self._connect_timeout,
            # Use only the configured key — predictable, no agent/key probing.
            allow_agent=False,
            look_for_keys=False,
        )
        try:
            sftp = client.open_sftp()
            try:
                yield sftp
            finally:
                sftp.close()
        finally:
            client.close()

    # -- cache ---------------------------------------------------------------

    def _cache_path(self, endpoint: SSHEndpoint, path: str) -> Path | None:
        if not self._cache_dir:
            return None
        key = f"{endpoint.host}:{endpoint.port}:{path}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.log"

    def _cache_get(self, endpoint: SSHEndpoint, path: str) -> str | None:
        cp = self._cache_path(endpoint, path)
        if not cp or not cp.exists():
            return None
        if time.time() - cp.stat().st_mtime > self._cache_ttl:
            return None
        try:
            return cp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _cache_put(self, endpoint: SSHEndpoint, path: str, text: str) -> None:
        cp = self._cache_path(endpoint, path)
        if not cp:
            return
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(text, encoding="utf-8")
        except OSError as e:
            logger.warning("log orchestrator: cache write failed: %s", e)
