"""LiveAdapter — talks to a real AutoSys REST API.

Reads come from the older `/AEWS/job/...` + `/AEWS/jil/...` surface. Status
codes returned by AutoSys are numeric; we translate to the same string set
the mock emits so the agent sees a stable shape.

History (`get_job_history`) has no native REST endpoint, so we POST an
`autorep -j {name} -w ...` command to `/AEWS/api/command/run` and parse the
stdout — see autorep_parser.py.

`get_job_log` has no REST endpoint at all; we either consult
LOG_FORWARDER_URL_TEMPLATE if configured, or return the JIL path so the
operator can fetch it themselves.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .autorep_parser import parse_autorep_wide
from .autosys_client import AutoSysAPIError, AutoSysClient
from .base import AutoSysAdapter, JobNotFound
from .jil_parser import referenced_jobs
from .status_codes import build_table, translate

logger = logging.getLogger(__name__)

# Hard cap so a misbehaving autorep cannot make us issue unbounded requests.
_MAX_HISTORY_WALK = 200


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
        autorep_history_strategy: str | None = None,
        log_mount_root: str | None = None,
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
        self._log_mount_root = Path(log_mount_root) if log_mount_root else None
        self._history_strategy = autorep_history_strategy or "walk-runs"

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
        if self._history_strategy == "days-flag":
            return self._history_via_days_flag(job_name, days)
        return self._history_via_walk(job_name, days)

    def _history_via_days_flag(self, job_name: str, days: int) -> list[dict[str, Any]]:
        text = self._client.post_command(f"autorep -j {job_name} -w -d {days}")
        if _is_unknown_job(text):
            raise JobNotFound(f"unknown job: {job_name}")
        return parse_autorep_wide(text, days=days)

    def _history_via_walk(self, job_name: str, days: int) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        history: list[dict[str, Any]] = []
        for run_n in range(_MAX_HISTORY_WALK):
            text = self._client.post_command(f"autorep -j {job_name} -w -r {run_n}")
            if _is_unknown_job(text):
                if run_n == 0:
                    raise JobNotFound(f"unknown job: {job_name}")
                break
            # CAUAJM_E_10323 = "the run number specified is invalid" — i.e. we
            # walked past the oldest retained run. Normal terminator.
            if "CAUAJM_E_10323" in text:
                break
            # Parse with an effectively-infinite window (capped to avoid
            # timedelta overflow); we apply our own cutoff below so we can
            # use it as an early-stop signal.
            rows = parse_autorep_wide(text, days=50_000)
            if not rows:
                break
            row = rows[0]
            if datetime.fromisoformat(row["start"]) < cutoff:
                break
            history.append(row)
        return history

    def get_dependencies(self, job_name: str) -> dict[str, Any]:
        # JIL endpoint returns the textual job definition including condition.
        try:
            jil = self._client.get_text("jil/job", params={"name": job_name})
        except AutoSysAPIError as e:
            if "HTTP 404" in str(e):
                raise JobNotFound(f"unknown job: {job_name}") from e
            raise
        # The `?name=BOX` JIL response inlines all child jobs as additional
        # `insert_job:` blocks; the queried job's own attributes are only
        # those between its `insert_job:` line and the next one.
        target_section = _extract_job_section(jil, job_name) or jil
        upstream = referenced_jobs(_find_attribute(target_section, "condition"))

        # Downstream: every job in the corpus whose condition references us.
        # The /AEWS/job/* read surface omits the `condition` field on most
        # AutoSys versions, so we must walk JIL instead — one GET for the
        # whole corpus, then a section-by-section scan.
        downstream: list[str] = []
        try:
            all_jil = self._client.get_text("jil/job")
        except AutoSysAPIError as e:
            logger.warning(
                "downstream JIL fetch for %s failed (%s); returning upstream only",
                job_name, e,
            )
            return {"job": job_name, "upstream": upstream, "downstream": downstream}

        for other_name, section in _iter_jil_sections(all_jil):
            if other_name == job_name:
                continue
            cond = _find_attribute(section, "condition")
            if cond and job_name in referenced_jobs(cond):
                downstream.append(other_name)

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

        # The std_*_file attributes live in JIL, not in /AEWS/job/{name} —
        # the read surface strips them. Always go through JIL here.
        try:
            jil = self._client.get_text("jil/job", params={"name": job_name})
        except AutoSysAPIError as e:
            if "HTTP 404" in str(e):
                raise JobNotFound(f"unknown job: {job_name}") from e
            raise

        section = _extract_job_section(jil, job_name) or jil
        attr = "std_err_file" if stream == "err" else "std_out_file"
        path = _find_attribute(section, attr)
        machine = _find_attribute(section, "machine")
        job_type = _find_attribute(section, "job_type")

        if path:
            # AutoSys substitutes $AUTO_JOB_NAME at runtime; do the same so
            # the operator gets a concrete path they can `cat`.
            path = path.replace("${AUTO_JOB_NAME}", job_name)
            path = path.replace("$AUTO_JOB_NAME", job_name)

            # Local-mount fast path: if the operator has mounted the agent's
            # job_logs directory on the backend host, read the file directly.
            if self._log_mount_root is not None:
                local = self._log_mount_root / Path(path).name
                try:
                    return local.read_text(encoding="utf-8", errors="replace")
                except FileNotFoundError:
                    logger.warning("log file not found at %s", local)
                except OSError as e:
                    logger.warning("failed to read log %s: %s", local, e)
                # Fall through to path-only message.

            machine_str = f" on host '{machine}'" if machine else ""
            return (
                f"AutoSys REST API does not expose log content. "
                f"Configured std_{stream}_file{machine_str}: {path}. "
                f"Fetch this file from the agent host directly, or set "
                f"LOG_FORWARDER_URL_TEMPLATE / AUTOSYS_LOG_MOUNT_ROOT."
            )

        if job_type and job_type.upper() == "BOX":
            return (
                f"{job_name} is a BOX job — boxes don't execute a command "
                f"of their own and therefore don't produce a log. Ask for a "
                f"child job's log instead."
            )
        return (
            f"No std_{stream}_file is configured in the JIL for {job_name}. "
            f"Either add std_{stream}_file to the job's JIL or set "
            f"LOG_FORWARDER_URL_TEMPLATE to fetch the log via your log "
            f"aggregator."
        )


def _is_unknown_job(autorep_response: str) -> bool:
    return (
        "CAUAJM_E_50027" in autorep_response
        or "Invalid Job Name" in autorep_response
    )


_INSERT_JOB_RE = re.compile(r"^\s*insert_job\s*:\s*(\S+)", re.IGNORECASE)
_INLINE_JOB_TYPE_RE = re.compile(r"\bjob_type\s*:\s*(\S+)", re.IGNORECASE)


def _iter_jil_sections(jil_text: str) -> Iterator[tuple[str, str]]:
    """Yield (job_name, section_text) for each `insert_job:` block in JIL.

    A section starts at an `insert_job:` line and ends at the next one (or
    EOF). Decorative `/* ... */` banners between blocks are absorbed into
    the *following* section, which is fine since `_find_attribute` ignores
    anything that doesn't match its needle. `job_type:` typically sits
    inline on the `insert_job:` line, so promote it to its own synthetic
    line for the attribute scanner.
    """
    current_name: str | None = None
    current_lines: list[str] = []
    for line in jil_text.splitlines():
        m = _INSERT_JOB_RE.match(line)
        if m:
            if current_name is not None:
                yield current_name, "\n".join(current_lines)
            current_name = m.group(1)
            current_lines = [line]
            jt = _INLINE_JOB_TYPE_RE.search(line)
            if jt:
                current_lines.append(f"job_type: {jt.group(1)}")
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        yield current_name, "\n".join(current_lines)


def _extract_job_section(jil_text: str, job_name: str) -> str | None:
    for name, section in _iter_jil_sections(jil_text):
        if name == job_name:
            return section
    return None


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
