"""AutoSys numeric status codes <-> string mapping.

Values verified against Broadcom TechDocs + community references for AutoSys
12.x / 24.x. Customers on intermediate releases occasionally have shifts; the
STATUS_CODE_OVERRIDES env var lets an installer patch the table without code
changes (format: "4=SUCCESS,5=FAILURE,99=CUSTOM").
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

# Default mapping. Values agreed across the public AutoSys documentation we
# have. Treat anything not in this table as "UNKNOWN(<n>)" rather than guessing.
_DEFAULT_CODES: dict[int, str] = {
    1: "RUNNING",
    4: "SUCCESS",
    5: "FAILURE",
    7: "TERMINATED",
    8: "STARTING",
    9: "INACTIVE",
    11: "QUE_WAIT",
    12: "ON_NOEXEC",
    13: "ON_HOLD",
    14: "ON_ICE",
}


def parse_overrides(spec: str | None) -> dict[int, str]:
    """Parse 'k=v,k=v' override syntax into a {int: str} dict.

    Silently skips malformed entries with a warning log — we never want a
    parse error to take the whole live adapter down.
    """
    if not spec:
        return {}
    out: dict[int, str] = {}
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            logger.warning("status_codes: malformed override %r (no =)", chunk)
            continue
        k, v = chunk.split("=", 1)
        try:
            out[int(k.strip())] = v.strip().upper()
        except ValueError:
            logger.warning("status_codes: non-numeric key in %r", chunk)
    return out


def build_table(overrides: str | None = None) -> dict[int, str]:
    """Merged code table: defaults overlaid with parsed overrides."""
    table = dict(_DEFAULT_CODES)
    table.update(parse_overrides(overrides))
    return table


def translate(code: int | str, table: dict[int, str] | None = None) -> str:
    """Numeric or stringified-numeric -> our canonical status string."""
    if table is None:
        table = _DEFAULT_CODES
    try:
        n = int(code)
    except (TypeError, ValueError):
        return f"UNKNOWN({code!r})"
    if n in table:
        return table[n]
    logger.warning("status_codes: unmapped numeric code %d", n)
    return f"UNKNOWN({n})"


def known_codes(table: dict[int, str] | None = None) -> Iterable[int]:
    return (table or _DEFAULT_CODES).keys()
