"""Thin httpx wrapper for the AutoSys Engine Web Services REST API.

Encapsulates: Basic Auth, configurable TLS verification, timeout, and a single
request method that surfaces meaningful exceptions instead of opaque httpx
errors. Centralising this keeps the LiveAdapter simple and makes the API
shape easy to swap (e.g. X-AUTH-TOKEN later) in one place.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AutoSysAPIError(Exception):
    """Any non-2xx response or transport error from the AutoSys REST API."""


class AutoSysClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_tls: bool = True,
        timeout_seconds: float = 15.0,
    ):
        if not base_url.endswith("/"):
            base_url = base_url + "/"
        # No default Accept: per-call header drives content negotiation.
        # Some AutoSys surfaces (notably /AEWS/jil/...) return text/plain and
        # refuse with HTTP 406 if the client demands application/json.
        self._client = httpx.Client(
            base_url=base_url,
            auth=(username, password),
            verify=verify_tls,
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AutoSysClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._send("GET", path, params=params, expect_json=True)

    def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        return self._send("GET", path, params=params, expect_json=False)

    def post_json(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._send("POST", path, params=params, json=json, expect_json=True)

    def post_event(self, path: str, json: dict[str, Any]) -> str:
        """POST a write event (e.g. event/hold-job). Returns the response body
        text (often empty on a 201). Raises AutoSysAPIError on any non-2xx."""
        return self._send("POST", path, json=json, expect_json=False)

    def post_command(self, command: str) -> str:
        """POST /AEWS/command/run and return the stdout (or stderr) text.

        The observed real-AutoSys response is one of:
          {"stdOut": ["line", "line", ...]}    — success path
          {"stdErr": ["CAUAJM_E_... message"]} — autorep-level error
                                                 (HTTP is still 200)
        Some installs / Swagger surfaces alternately return {"output": "..."}.
        Returns the joined text in all cases; callers inspect the body for
        the `CAUAJM_E_*` patterns to distinguish JobNotFound from data.
        """
        raw = self._send(
            "POST",
            "command/run",
            json={"command": command},
            expect_json=False,
        )
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return raw
        if isinstance(payload, dict):
            for field in ("stdOut", "stdErr", "output"):
                if field in payload:
                    value = payload[field]
                    if isinstance(value, list):
                        return "\n".join(str(x) for x in value)
                    return str(value)
        return raw

    def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        # The AutoSys REST surface is rooted under /AEWS/... so callers pass
        # relative paths like "job/etl_load_facts" or "jil/job".
        accept = "application/json" if expect_json else "text/plain, */*"
        try:
            response = self._client.request(
                method, path, params=params, json=json, headers={"Accept": accept}
            )
        except httpx.RequestError as e:
            # Connection refused, DNS failure, TLS handshake, timeout, etc.
            logger.warning("autosys client: %s %s failed: %s", method, path, e)
            raise AutoSysAPIError(f"{method} {path}: {type(e).__name__}: {e}") from e

        if response.status_code >= 400:
            # Don't log the body — it can contain operational data we'd rather
            # keep out of generic logs. The path + code are enough to diagnose.
            logger.warning(
                "autosys client: %s %s -> HTTP %d", method, path, response.status_code
            )
            raise AutoSysAPIError(
                f"{method} {path}: HTTP {response.status_code} {response.reason_phrase}"
            )

        if not expect_json:
            return response.text
        try:
            return response.json()
        except ValueError as e:
            raise AutoSysAPIError(f"{method} {path}: response was not valid JSON") from e
