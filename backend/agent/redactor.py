"""PII redaction for LLM-bound payloads.

Runs between adapter/tool output and the LLM prompt. The technician's UI still
sees raw data — only data crossing into the LLM context is sanitised.

Scope (first pass, see M2 in the development plan):
  - Email addresses (regex)
  - Phone numbers (E.164 + common national formats)
  - Field denylist: any dict key in DENYLISTED_FIELDS is dropped entirely

Out of scope for this pass:
  - Free-text person-name detection (would need NER — flagged in open Q5)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Phone matcher — must include at least one of: `+` prefix, parentheses,
# whitespace, or a dot separator. Pure digit-dash sequences (e.g. dates like
# 2026-05-25, ticket IDs like INC-2026-05-25-001) are NOT treated as phones,
# which costs us NA-style "415-555-0123" coverage. That's deliberate: false
# positives on operational data hurt technicians more than missing a few real
# phone formats.
PHONE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"\+\d[\d\s().\-]{7,}\d"             # E.164 with + prefix
    r"|"
    r"\(\d+\)[\d\s().\-]{6,}\d"          # has parentheses
    r"|"
    r"\d{1,4}[\s.][\d\s.()\-]{6,}\d"     # has whitespace or dot separator
    r")"
    r"(?![A-Za-z0-9])"
)

DENYLISTED_FIELDS: frozenset[str] = frozenset(
    {
        "owner_email",
        "owner_name",
        "assignee_email",
        "assignee_name",
        "contact_email",
        "contact_phone",
        "email",
        "phone",
    }
)

EMAIL_PLACEHOLDER = "[REDACTED_EMAIL]"
PHONE_PLACEHOLDER = "[REDACTED_PHONE]"


@dataclass
class RedactionCounts:
    emails: int = 0
    phones: int = 0
    fields: int = 0

    @property
    def total(self) -> int:
        return self.emails + self.phones + self.fields


def _scrub_string(text: str, counts: RedactionCounts) -> str:
    def _email_sub(_m: re.Match[str]) -> str:
        counts.emails += 1
        return EMAIL_PLACEHOLDER

    def _phone_sub(_m: re.Match[str]) -> str:
        counts.phones += 1
        return PHONE_PLACEHOLDER

    text = EMAIL_PATTERN.sub(_email_sub, text)
    text = PHONE_PATTERN.sub(_phone_sub, text)
    return text


def _walk(value: Any, counts: RedactionCounts) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            if k in DENYLISTED_FIELDS:
                counts.fields += 1
                continue
            cleaned[k] = _walk(v, counts)
        return cleaned
    if isinstance(value, list):
        return [_walk(item, counts) for item in value]
    if isinstance(value, tuple):
        return tuple(_walk(item, counts) for item in value)
    if isinstance(value, str):
        return _scrub_string(value, counts)
    return value


def redact(payload: Any) -> tuple[Any, RedactionCounts]:
    """Return a sanitised copy of payload plus a count of redactions.

    The original payload is not mutated. Counts are an aggregate only —
    never the redacted values themselves.
    """
    counts = RedactionCounts()
    cleaned = _walk(payload, counts)
    if counts.total:
        logger.info(
            "redactor: %d field(s) dropped, %d email(s), %d phone(s) scrubbed",
            counts.fields,
            counts.emails,
            counts.phones,
        )
    return cleaned, counts
