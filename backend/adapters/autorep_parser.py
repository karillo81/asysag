"""Parser for `autorep -j {name} -w ...` stdout.

AutoSys has no native job-history REST endpoint; the documented workaround is
to POST /AEWS/api/command/run invoking the CLI `autorep` and parse stdout
into the same shape the mock emits — {start, end, status, duration_seconds,
return_code, message}. See dev-plan M7 sub-task 7.

The two-letter status codes here (SU/FA/RU/...) are a separate surface from
the numeric REST codes in status_codes.py; do not merge the tables.

When the parser sees an unrecognised format it raises AutorepParseError with
a single short message asking the customer to share their raw output. The
chat layer surfaces `str(exc)` verbatim, so the message is the user-facing
text.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


class AutorepParseError(Exception):
    """Raised when autorep stdout does not match a recognised AutoSys format."""


PARSE_ERROR_MESSAGE = (
    "AutoSys returned autorep output the parser does not recognise. "
    "Share the raw output (anonymised) so we can extend the parser — "
    "see development plan M7 sub-task 7."
)


_AUTOREP_STATUS_MAP: dict[str, str] = {
    "SU": "SUCCESS",
    "FA": "FAILURE",
    "RU": "RUNNING",
    "TE": "TERMINATED",
    "ST": "STARTING",
    "IN": "INACTIVE",
    "OI": "ON_ICE",
    "OH": "ON_HOLD",
    "QW": "QUE_WAIT",
}


_ROW_RE = re.compile(
    r"^(?P<name>\S+)\s+"
    r"(?P<start>\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2})\s+"
    r"(?P<end>\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}|-+)\s+"
    r"(?P<status>[A-Z]{2})(?:/(?P<status_exit>-?\d+))?"
    r"(?:\s+\S+)?"                         # Run/Ntry — uninteresting
    r"(?:\s+(?P<pri_xit>\S+))?"            # Pri/Xit — may carry the exit code
    r"(?:\s+.*)?$"
)


def _is_skip_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s.startswith("Job Name") or s.startswith("Last Start"):
        return True
    if set(s) <= {"_", "-", "=", " "}:
        return True
    # `autorep -j BOX -w` lists child jobs as indented rows beneath the box
    # parent. We return the queried job's own history, not its children — so
    # any line that starts with whitespace is treated as a child to skip.
    if line[0].isspace():
        return True
    return False


# `autorep -d` follows the summary table with a per-event detail section
# headed by `Status/[Event]`. We only parse the summary; stop here.
_DETAIL_SECTION_MARKERS = ("Status/[Event]", "Event/Status")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%m/%d/%Y %H:%M:%S")


def _extract_return_code(
    status_exit: str | None,
    pri_xit: str | None,
    canonical_status: str,
) -> int | None:
    """Pick the exit code out of whichever column carries it.

    Documented layout puts it after the `/` in the `ST/Ex` column (e.g.
    `FA/137`). Real AutoSys 24.x prints `ST/Ex` as just the 2-letter status
    and stuffs the exit code into `Pri/Xit` — either as `5/137`
    (priority/exit) or a bare number when priority defaults to 0. We honour
    both. Running/intermediate states don't have a meaningful exit code yet,
    so we leave them as None.
    """
    if status_exit is not None:
        return int(status_exit)
    if canonical_status in ("SUCCESS", "FAILURE", "TERMINATED") and pri_xit:
        candidate = pri_xit.split("/", 1)[-1] if "/" in pri_xit else pri_xit
        if candidate.lstrip("-").isdigit():
            return int(candidate)
    if canonical_status == "SUCCESS":
        return 0
    return None


def parse_autorep_wide(
    text: str,
    days: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Parse `autorep -w` stdout into history records, filtered to `days`.

    Times in autorep output are tz-naive (scheduler local time). We compare
    them naively to `now` — close enough for "last 7 days" semantics.
    Test code passes a fixed `now` for determinism.
    """
    # autorep stamps are tz-naive (scheduler local time). Compare against a
    # naive `now` to avoid TypeError when the caller doesn't pin one.
    reference = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = reference - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if any(stripped.startswith(m) for m in _DETAIL_SECTION_MARKERS):
            break
        if _is_skip_line(raw):
            continue
        match = _ROW_RE.match(raw.rstrip())
        if match is None:
            raise AutorepParseError(PARSE_ERROR_MESSAGE)
        status_code = match.group("status")
        if status_code not in _AUTOREP_STATUS_MAP:
            raise AutorepParseError(PARSE_ERROR_MESSAGE)
        try:
            start = _parse_timestamp(match.group("start"))
        except ValueError as e:
            raise AutorepParseError(PARSE_ERROR_MESSAGE) from e
        end_raw = match.group("end")
        end: datetime | None
        if end_raw.startswith("-"):
            end = None
        else:
            try:
                end = _parse_timestamp(end_raw)
            except ValueError as e:
                raise AutorepParseError(PARSE_ERROR_MESSAGE) from e
        if start < cutoff:
            continue
        canonical = _AUTOREP_STATUS_MAP[status_code]
        return_code = _extract_return_code(
            match.group("status_exit"),
            match.group("pri_xit"),
            canonical,
        )
        rows.append({
            "start": start.isoformat(),
            "end": end.isoformat() if end is not None else None,
            "status": canonical,
            "duration_seconds": int((end - start).total_seconds()) if end else None,
            "return_code": return_code,
            "message": None,
        })
    return rows
