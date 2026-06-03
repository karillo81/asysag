"""Tests for the `autorep -w` text parser.

Fixtures live in backend/tests/fixtures/ and are synthetic, modelled on the
documented Broadcom column layout for AutoSys 12.x and 24.x. Replace with
captured production output when available — see dev-plan M7 sub-task 7.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from adapters.autorep_parser import (
    PARSE_ERROR_MESSAGE,
    AutorepParseError,
    parse_autorep_wide,
)


FIXTURES = Path(__file__).parent / "fixtures"

# All fixture rows fall in May–June 2026; anchor "now" so day-window math is
# deterministic regardless of when the suite runs.
FIXED_NOW = datetime(2026, 6, 4, 0, 0, 0)


@pytest.mark.parametrize("fixture", ["autorep_wide_12x.txt", "autorep_wide_24x.txt"])
def test_parses_both_documented_layouts(fixture: str) -> None:
    text = (FIXTURES / fixture).read_text(encoding="utf-8")

    # Use a window wide enough that nothing is filtered out — assert on the
    # full row set so the test pins both column-layout flavours.
    rows = parse_autorep_wide(text, days=365, now=FIXED_NOW)

    assert [r["status"] for r in rows] == [
        "SUCCESS", "SUCCESS", "FAILURE", "SUCCESS", "FAILURE", "RUNNING"
    ]
    assert [r["return_code"] for r in rows] == [0, 0, 137, 0, 137, None]
    assert all(r["message"] is None for r in rows)
    assert rows[-1]["end"] is None
    assert rows[-1]["duration_seconds"] is None

    # First (SUCCESS) row: confirm exact shape parity with mock records.
    first = rows[0]
    assert first == {
        "start": "2026-05-22T03:42:00",
        "end": "2026-05-22T04:08:11",
        "status": "SUCCESS",
        "duration_seconds": 1571,
        "return_code": 0,
        "message": None,
    }


def test_days_window_filters_old_rows() -> None:
    text = (FIXTURES / "autorep_wide_12x.txt").read_text(encoding="utf-8")
    # 7-day window from 2026-06-04 → cutoff 2026-05-28 → keep only the
    # 05-28 FAILURE and the 06-03 RUNNING row.
    rows = parse_autorep_wide(text, days=7, now=FIXED_NOW)
    assert [r["status"] for r in rows] == ["FAILURE", "RUNNING"]


def test_unknown_status_code_raises_actionable_message() -> None:
    bad = (
        "Job Name             Last Start         Last End           ST/Ex   Run/Ntry  Pri/Xit\n"
        "___________________  _________________  _________________  ______  ________  _______\n"
        "etl_load_facts       05/28/2026 03:42:30 05/28/2026 04:11:48 ZZ/0   12345/1   5/0\n"
    )
    with pytest.raises(AutorepParseError) as exc:
        parse_autorep_wide(bad, days=7, now=FIXED_NOW)
    assert str(exc.value) == PARSE_ERROR_MESSAGE


def test_malformed_row_raises_actionable_message() -> None:
    bad = "etl_load_facts not-a-date\n"
    with pytest.raises(AutorepParseError) as exc:
        parse_autorep_wide(bad, days=7, now=FIXED_NOW)
    assert str(exc.value) == PARSE_ERROR_MESSAGE


def test_garbage_input_raises_actionable_message() -> None:
    with pytest.raises(AutorepParseError) as exc:
        parse_autorep_wide("complete nonsense from a different command", days=7, now=FIXED_NOW)
    assert str(exc.value) == PARSE_ERROR_MESSAGE


def test_empty_output_returns_no_rows() -> None:
    """No history (e.g. brand-new job) is not an error."""
    assert parse_autorep_wide("", days=7, now=FIXED_NOW) == []


def test_header_only_output_returns_no_rows() -> None:
    header_only = (
        "Job Name             Last Start         Last End           ST/Ex   Run/Ntry  Pri/Xit\n"
        "___________________  _________________  _________________  ______  ________  _______\n"
    )
    assert parse_autorep_wide(header_only, days=7, now=FIXED_NOW) == []


def test_box_job_output_skips_indented_child_rows() -> None:
    """`autorep -j BOX -w` lists child jobs as indented rows under the box.

    The parser must return only the queried box's own history row, since
    `get_job_history` is scoped to a single job — children aren't asked for.
    The exit code (`1`) lives in `Pri/Xit` rather than after `ST/Ex` in this
    real-AutoSys layout; pull it from there.
    """
    text = (FIXTURES / "autorep_box_with_children.txt").read_text(encoding="utf-8")
    rows = parse_autorep_wide(text, days=365, now=FIXED_NOW)
    assert rows == [{
        "start": "2026-06-03T11:14:16",
        "end": "2026-06-03T11:14:38",
        "status": "FAILURE",
        "duration_seconds": 22,
        "return_code": 1,
        "message": None,
    }]


def test_real_autorep_with_detail_section_stops_at_event_table() -> None:
    """Real `autorep -j X -w -d 7` returns summary + Status/[Event] detail.

    Lock the behaviour: parse only the summary row, stop before the events.
    Real success rows print `SU` (no `/0`); normalise return_code to 0 for
    shape parity with mock fixtures.
    """
    text = (FIXTURES / "autorep_real_with_detail.txt").read_text(encoding="utf-8")
    rows = parse_autorep_wide(text, days=365, now=FIXED_NOW)
    assert rows == [{
        "start": "2026-06-03T11:24:49",
        "end": "2026-06-03T11:24:49",
        "status": "SUCCESS",
        "duration_seconds": 0,
        "return_code": 0,
        "message": None,
    }]
