"""LiveAdapter — talks to a real AutoSys REST API.

Reads come from the older `/AEWS/job/...` + `/AEWS/jil/...` surface. Status
codes returned by AutoSys are numeric; we translate to the same string set
the mock emits so the agent sees a stable shape.

What this adapter cannot do (see the dev plan, M7 sub-tasks 7 + log gap):
  - get_job_history: needs `autorep -j {name} -w` text parsing — deferred
  - get_job_log: AutoSys REST API has no log endpoint; we either consult
    LOG_FORWARDER_URL_TEMPLATE if configured, or return the JIL path so the
    operator can fetch it themselves.
"""

from __future__ import annotations

import logging
from typing import Any

from .autosys_client import AutoSysAPIError, AutoSysClient
from .base import AutoSysAdapter, JobNotFound
from .jil_parser import referenced_jobs
from .status_codes import build_table, translate

logger = logging.getLogger(__name__)


class LiveAdapter(AutoSysAdapter):
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_tls: bool = True,
        timeout_seconds: float = 15.0,
        log_forwarder_url_template: str | None = None,
        status_code_overrides: str | None = None,
        client: AutoSysClient | None = None,
    ):
        # Allow callers to inject a pre-built client (used by tests with
        # httpx.MockTransport). Production path constructs it from settings.
        self._client = client or AutoSysClient(
            base_url=base_url,
            username=username,
            password=password,
            verify_tls=verify_tls,
            timeout_seconds=timeout_seconds,
        )
        self._status_table = build_table(status_code_overrides)
        self._log_forwarder = log_forwarder_url_template

    @staticmethod
    def _first_job(payload: Any) -> dict[str, Any]:
        """AutoSys wraps single-job responses as {'job': [{...}]}. Normalise."""
        if isinstance(payload, dict) and "job" in payload:
            items = payload["job"]
            if isinstance(items, list) and items:
                return items[0]
            if isinstance(items, dict):
                return items
        if isinstance(payload, dict):
            return payload
        raise AutoSysAPIError(f"unexpected payload shape: {type(payload).__name__}")

    @staticmethod
    def _job_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and "job" in payload:
            items = payload["job"]
            if isinstance(items, list):
                return items
            if isinstance(items, dict):
                return [items]
        if isinstance(payload, list):
            return payload
        raise AutoSysAPIError(f"unexpected list payload shape: {type(payload).__name__}")

    def _decorate(self, job: dict[str, Any]) -> dict[str, Any]:
        """Translate numeric status to canonical string, normalise field names."""
        out = dict(job)
        if "status" in out:
            out["status"] = translate(out["status"], self._status_table)
        # AutoSys uses camelCase (boxName, jobType); mock data uses snake_case.
        # Provide both so the agent's prompt experience is identical either way.
        rename = {"boxName": "box_name", "jobType": "job_type"}
        for src, dst in rename.items():
            if src in out and dst not in out:
                out[dst] = out[src]
        return out

    def get_job_status(self, job_name: str) -> dict[str, Any]:
        try:
            payload = self._client.get_json(f"job/{job_name}")
        except AutoSysAPIError as e:
            if "HTTP 404" in str(e):
                raise JobNotFound(f"unknown job: {job_name}") from e
            raise
        return self._decorate(self._first_job(payload))

    def list_jobs(self, name_filter: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if name_filter:
            # AutoSys filter syntax: jobName==*foo*. Use substring contains.
            params["filter"] = f"jobName==*{name_filter}*"
        payload = self._client.get_json("job", params=params or None)
        return [self._decorate(j) for j in self._job_list(payload)]

    def get_job_history(self, job_name: str, days: int = 7) -> list[dict[str, Any]]:
        # See M7 sub-task 7 in the dev plan — needs sample `autorep -j X -w`
        # output to write a robust text parser. Refusing rather than guessing.
        raise NotImplementedError(
            "get_job_history is not yet wired to AutoSys live mode "
            "(needs sample `autorep -j X -w` output to build the text parser — "
            "see development plan M7 sub-task 7)"
        )

    def get_dependencies(self, job_name: str) -> dict[str, Any]:
        # JIL endpoint returns the textual job definition including condition.
        try:
            jil = self._client.get_text("jil/job", params={"name": job_name})
        except AutoSysAPIError as e:
            if "HTTP 404" in str(e):
                raise JobNotFound(f"unknown job: {job_name}") from e
            raise
        upstream = _extract_upstream_from_jil(jil)

        # Downstream = the set of all jobs whose condition references our job.
        # One walk over list_jobs is enough; we don't cache because list_jobs
        # already hits a single endpoint and the agent typically asks about
        # a handful of jobs per conversation.
        downstream: list[str] = []
        try:
            for other in self.list_jobs():
                cond = other.get("condition")
                if not cond:
                    continue
                if job_name in referenced_jobs(cond):
                    other_name = other.get("name") or other.get("jobName")
                    if other_name and other_name != job_name:
                        downstream.append(other_name)
        except AutoSysAPIError as e:
            logger.warning(
                "downstream lookup for %s failed (%s); returning upstream only",
                job_name, e,
            )

        return {"job": job_name, "upstream": upstream, "downstream": downstream}

    def get_job_log(self, job_name: str, stream: str = "err") -> str:
        if stream not in ("err", "out"):
            raise ValueError("stream must be 'err' or 'out'")

        if self._log_forwarder:
            url = self._log_forwarder.format(job=job_name, stream=stream)
            try:
                with AutoSysClient(
                    base_url=url,
                    username="",
                    password="",
                    verify_tls=True,
                ) as forwarder:
                    return forwarder.get_text("")
            except AutoSysAPIError as e:
                logger.warning("log forwarder lookup for %s failed: %s", job_name, e)
                # Fall through to path-only response below.

        # Path-only fallback. Look up the job to surface its JIL path.
        try:
            job = self.get_job_status(job_name)
        except JobNotFound:
            raise
        path_field = "std_err_file" if stream == "err" else "std_out_file"
        path = job.get(path_field) or job.get(path_field.replace("_", ""))
        return (
            f"AutoSys REST API does not expose log content. "
            f"Configured stderr path: {path or '(not set in JIL)'}. "
            f"Either configure LOG_FORWARDER_URL_TEMPLATE or fetch this file "
            f"directly from the agent host."
        )


def _extract_upstream_from_jil(jil_text: str) -> list[str]:
    """Pull the value of `condition:` out of a JIL definition and resolve refs.

    JIL uses key: value lines; condition lines may span until the next
    `attribute:` token. We tolerate quoted and unquoted forms.
    """
    condition = _find_attribute(jil_text, "condition")
    return referenced_jobs(condition)


def _find_attribute(jil_text: str, attr: str) -> str | None:
    """Return the raw value text of a JIL attribute, or None if absent.

    JIL is forgiving of whitespace and quoting; we keep the parser equally
    forgiving.
    """
    lines = jil_text.splitlines()
    needle = attr.lower() + ":"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.lower().startswith(needle):
            continue
        value = stripped[len(needle):].strip()
        # Continuation: subsequent lines that do NOT match `\w+:` belong to us.
        for cont in lines[i + 1:]:
            cont_stripped = cont.strip()
            if not cont_stripped:
                break
            head = cont_stripped.split(":", 1)[0]
            if head and head.replace("_", "").isalpha():
                break
            value += " " + cont_stripped
        # Strip surrounding quotes commonly seen on condition values.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return value
    return None
