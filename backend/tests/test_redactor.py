"""M2 done-when criterion: an email in mock data never appears in the assembled LLM prompt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.redactor import (
    DENYLISTED_FIELDS,
    EMAIL_PATTERN,
    EMAIL_PLACEHOLDER,
    PHONE_PATTERN,
    PHONE_PLACEHOLDER,
    redact,
)
from config import settings
from adapters import get_adapter

MOCK_DIR = settings.mock_data_dir


def _all_strings(node):
    """Yield every string in a nested structure."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _all_strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _all_strings(v)


def test_emails_in_strings_are_replaced():
    payload = {
        "owner": "data-eng",
        "notes": "page john.doe@acme.com or fallback to ops@acme.example",
    }
    cleaned, counts = redact(payload)
    assert counts.emails == 2
    assert EMAIL_PLACEHOLDER in cleaned["notes"]
    for s in _all_strings(cleaned):
        assert not EMAIL_PATTERN.search(s), f"email leaked through: {s!r}"


def test_phones_are_replaced():
    payload = {"contact": "call +44 20 7946 0958 or 415.555.0123"}
    cleaned, counts = redact(payload)
    assert counts.phones == 2
    assert PHONE_PLACEHOLDER in cleaned["contact"]


def test_dates_and_ids_are_not_treated_as_phones():
    """Regression: phone regex used to swallow ticket IDs and ISO dates."""
    payload = {
        "incident_id": "INC-2026-05-25-001",
        "date": "2026-05-25",
        "timestamp": "2026-05-28T03:42:30Z",
        "host_port": "db-warehouse-prod-01.internal:1521",
        "return_code": "exit 137",
    }
    cleaned, counts = redact(payload)
    assert counts.phones == 0
    assert cleaned == payload


def test_denylisted_fields_are_dropped():
    payload = {
        "owner": "data-eng",
        "owner_email": "data-eng@acme.example",
        "owner_name": "Jane Smith",
        "nested": {"contact_email": "x@y.z", "kept": "yes"},
    }
    cleaned, counts = redact(payload)
    assert "owner_email" not in cleaned
    assert "owner_name" not in cleaned
    assert "contact_email" not in cleaned["nested"]
    assert cleaned["nested"]["kept"] == "yes"
    assert counts.fields == 3


def test_non_pii_operational_data_passes_through():
    payload = {
        "name": "etl_load_facts",
        "status": "FAILURE",
        "return_code": 137,
        "message": "ORA-12541: TNS:no listener (db-warehouse-prod-01.internal:1521)",
        "machine": "etl-prod-01.internal",
    }
    cleaned, counts = redact(payload)
    assert counts.total == 0
    assert cleaned == payload


def test_returns_new_object_does_not_mutate():
    payload = {"owner_email": "a@b.c", "notes": "ping me at x@y.z"}
    snapshot = json.dumps(payload, sort_keys=True)
    redact(payload)
    assert json.dumps(payload, sort_keys=True) == snapshot


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_job_status", ("etl_load_facts",)),
        ("list_jobs", (None,)),
        ("get_job_history", ("etl_load_facts", 30)),
        ("get_dependencies", ("etl_load_facts",)),
    ],
)
def test_no_email_leaks_through_any_tool(method, args):
    adapter = get_adapter("mock", MOCK_DIR)
    raw = getattr(adapter, method)(*args)
    cleaned, _ = redact(raw)
    for s in _all_strings(cleaned):
        assert not EMAIL_PATTERN.search(s), f"email leaked through {method}: {s!r}"


def test_pii_carrying_tools_actually_have_pii_to_redact():
    """Guard against silently breaking the leak test by removing PII from mock fixtures."""
    adapter = get_adapter("mock", MOCK_DIR)
    for method, args in [("get_job_status", ("etl_load_facts",)), ("list_jobs", (None,))]:
        raw = getattr(adapter, method)(*args)
        _, counts = redact(raw)
        assert counts.total > 0, (
            f"{method} fixtures contain no PII — leak test would be vacuous. "
            "Re-add owner_email or similar to mock data."
        )
