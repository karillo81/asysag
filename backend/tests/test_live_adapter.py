"""LiveAdapter tests against a synthetic AutoSys server via httpx.MockTransport.

These can't catch every real-world quirk (numeric status edge cases, JIL
formatting variants, unusual `boxName` casing) but they prove the happy
path and the major error mappings work without needing a real AutoSys.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from adapters import JobNotFound
from adapters.autorep_parser import PARSE_ERROR_MESSAGE, AutorepParseError
from adapters.autosys_client import AutoSysAPIError, AutoSysClient
from adapters.live_adapter import LiveAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _make_adapter(handler):
    transport = httpx.MockTransport(handler)
    client = AutoSysClient(
        base_url="https://autosys.example/AEWS/",
        username="ejmcommander",
        password="ejmcommander",
    )
    # Replace the real underlying transport with the mock.
    client._client = httpx.Client(
        base_url="https://autosys.example/AEWS/",
        auth=("ejmcommander", "ejmcommander"),
        transport=transport,
        headers={"Accept": "application/json"},
    )
    return LiveAdapter(
        base_url="ignored",
        username="ignored",
        password="ignored",
        client=client,
    )


def test_get_job_status_translates_numeric_status_and_renames_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/AEWS/job/etl_load_facts"
        return httpx.Response(
            200,
            json={
                "job": [{
                    "name": "etl_load_facts",
                    "boxName": "etl_box_daily",
                    "jobType": "CMD",
                    "status": "5",
                    "machine": "etl-prod-01.internal",
                }]
            },
        )

    adapter = _make_adapter(handler)
    job = adapter.get_job_status("etl_load_facts")
    assert job["status"] == "FAILURE"
    assert job["box_name"] == "etl_box_daily"
    assert job["job_type"] == "CMD"


def test_get_job_status_404_raises_job_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    adapter = _make_adapter(handler)
    with pytest.raises(JobNotFound):
        adapter.get_job_status("nope")


def test_list_jobs_translates_each_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"job": [
                {"name": "a", "status": "4"},
                {"name": "b", "status": "5"},
                {"name": "c", "status": "1"},
            ]},
        )

    adapter = _make_adapter(handler)
    jobs = adapter.list_jobs()
    statuses = {j["name"]: j["status"] for j in jobs}
    assert statuses == {"a": "SUCCESS", "b": "FAILURE", "c": "RUNNING"}


def test_list_jobs_filters_by_substring_client_side():
    # AEWS rejects wildcard/camelCase filter expressions with HTTP 400, so the
    # adapter fetches the full corpus and filters by substring itself. It must
    # NOT send a server-side `filter` param, and must return only name matches.
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json={"job": [
            {"name": "etl_load_facts", "status": "4"},
            {"name": "etl_extract", "status": "1"},
            {"name": "billing_run", "status": "5"},
        ]})

    adapter = _make_adapter(handler)
    jobs = adapter.list_jobs(name_filter="etl")
    assert "filter" not in seen_params
    assert {j["name"] for j in jobs} == {"etl_load_facts", "etl_extract"}


def test_get_dependencies_parses_jil_condition_and_finds_downstream():
    """Two requests: targeted /jil/job?name= for upstream, full /jil/job for downstream."""
    full_jil = (
        "insert_job: etl_load_facts   job_type: CMD\n"
        "   condition: s(etl_load_dimensions)\n"
        "insert_job: etl_load_dimensions   job_type: CMD\n"
        "insert_job: etl_validate_daily   job_type: CMD\n"
        "   condition: s(etl_load_facts)\n"
        "insert_job: report_revenue   job_type: CMD\n"
        "   condition: s(etl_validate_daily)\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/AEWS/jil/job":
            if request.url.params.get("name") == "etl_load_facts":
                return httpx.Response(
                    200,
                    text=(
                        "insert_job: etl_load_facts   job_type: CMD\n"
                        "   box_name: etl_box_daily\n"
                        "   condition: s(etl_load_dimensions)\n"
                        "   command: /opt/etl/bin/load.sh facts\n"
                    ),
                )
            # No name param -> full-corpus walk for downstream detection.
            return httpx.Response(200, text=full_jil)
        return httpx.Response(404)

    adapter = _make_adapter(handler)
    deps = adapter.get_dependencies("etl_load_facts")
    assert deps["job"] == "etl_load_facts"
    assert deps["upstream"] == ["etl_load_dimensions"]
    assert deps["downstream"] == ["etl_validate_daily"]


def test_get_dependencies_box_jil_does_not_leak_child_condition_as_upstream():
    """Regression: `?name=BOX` returns the box JIL **plus every child inlined**.

    The first `condition:` line in that blob belongs to a CHILD (test7's
    `condition: s(test6)`). The old parser falsely reported test6 as the
    box's upstream. Scoping to the queried `insert_job:` section fixes it.
    """
    box_jil = (
        "insert_job: testbox1   job_type: BOX\n"
        " owner: x\n"
        "insert_job: test6   job_type: CMD\n"
        " box_name: testbox1\n"
        "insert_job: test7   job_type: CMD\n"
        " box_name: testbox1\n"
        " condition: s(test6)\n"
        "insert_job: test8   job_type: CMD\n"
        " box_name: testbox1\n"
        " condition: s(test7)\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/AEWS/jil/job":
            # Both the targeted and corpus calls return the same blob in
            # this test — the box's `?name=` response inlines its children,
            # and the full-corpus call returns the same shape.
            return httpx.Response(200, text=box_jil)
        return httpx.Response(404)

    adapter = _make_adapter(handler)
    deps = adapter.get_dependencies("testbox1")
    assert deps["upstream"] == []
    assert deps["downstream"] == []


def test_get_dependencies_uses_full_jil_walk_not_list_jobs():
    """Regression: /AEWS/job omits `condition` on most AutoSys versions.

    Downstream detection must come from a JIL walk, not list_jobs.
    """
    visited_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited_paths.append(request.url.path)
        if request.url.path == "/AEWS/jil/job":
            if request.url.params.get("name") == "test6":
                return httpx.Response(200, text="insert_job: test6   job_type: CMD\n")
            return httpx.Response(
                200,
                text=(
                    "insert_job: test6   job_type: CMD\n"
                    "insert_job: test7   job_type: CMD\n"
                    " condition: s(test6)\n"
                ),
            )
        return httpx.Response(404)

    adapter = _make_adapter(handler)
    deps = adapter.get_dependencies("test6")
    assert deps["downstream"] == ["test7"]
    # We did NOT walk via /AEWS/job — only the JIL endpoint was touched.
    assert "/AEWS/job" not in visited_paths


def _make_strategy_adapter(handler, strategy: str = "walk-runs") -> LiveAdapter:
    transport = httpx.MockTransport(handler)
    client = AutoSysClient(
        base_url="https://autosys.example/AEWS/",
        username="x",
        password="x",
    )
    client._client = httpx.Client(
        base_url="https://autosys.example/AEWS/",
        auth=("x", "x"),
        transport=transport,
        headers={"Accept": "application/json"},
    )
    return LiveAdapter(
        base_url="ignored",
        username="ignored",
        password="ignored",
        autorep_history_strategy=strategy,
        client=client,
    )


def _walk_handler_factory(runs: list[dict]):
    """Build a handler that responds based on `-r N` in the posted command.

    `runs` is a list of dicts, one per run number (index N), each:
      {"start": "MM/DD/YYYY HH:MM:SS", "end": "...", "status": "SU", "exit": 0}
    A None entry simulates a missing run number (CAUAJM_E_10323).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        cmd = json.loads(request.content.decode())["command"]
        # `autorep -j NAME -w -r N` -> extract N
        n = int(cmd.rsplit(" ", 1)[-1])
        if n >= len(runs) or runs[n] is None:
            return httpx.Response(
                200,
                json={"stdOut": [
                    "CAUAJM_E_10323 Failed to retrieve details. "
                    "The run number specified is invalid."
                ]},
            )
        r = runs[n]
        row = (
            "Job Name                                                         "
            "Last Start           Last End             ST/Ex Run/Ntry Pri/Xit\n"
            "________________________________________________________________ "
            "____________________ ____________________ _____ ________ _______\n"
            f"etl_load_facts                                                   "
            f"{r['start']:<19}  {r['end']:<19}  {r['status']:<5} 1/1      {r.get('exit', 0)}"
        )
        return httpx.Response(200, json={"stdOut": row.splitlines()})
    return handler


def test_walk_strategy_iterates_runs_until_no_more_signal():
    runs = [
        {"start": "06/03/2026 11:00:00", "end": "06/03/2026 11:05:00", "status": "SU", "exit": 0},
        {"start": "06/02/2026 11:00:00", "end": "06/02/2026 11:04:00", "status": "FA", "exit": 137},
        {"start": "06/01/2026 11:00:00", "end": "06/01/2026 11:05:00", "status": "SU", "exit": 0},
    ]
    handler = _walk_handler_factory(runs)
    adapter = _make_strategy_adapter(handler)
    history = adapter.get_job_history("etl_load_facts", days=365)
    assert [r["status"] for r in history] == ["SUCCESS", "FAILURE", "SUCCESS"]
    assert history[0]["start"] == "2026-06-03T11:00:00"
    assert history[1]["return_code"] == 137
    assert history[2]["return_code"] == 0


def test_walk_strategy_stops_at_days_cutoff():
    """Once a run starts before the days window, walking halts."""
    runs = [
        {"start": "06/03/2026 11:00:00", "end": "06/03/2026 11:05:00", "status": "SU"},
        {"start": "01/15/2026 11:00:00", "end": "01/15/2026 11:05:00", "status": "SU"},
        {"start": "01/14/2026 11:00:00", "end": "01/14/2026 11:05:00", "status": "SU"},
    ]
    call_count = 0
    base = _walk_handler_factory(runs)

    def counting(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return base(request)

    adapter = _make_strategy_adapter(counting)
    # 7-day window; assuming "now" is around 2026-06-04, only run 0 is in.
    history = adapter.get_job_history("etl_load_facts", days=7)
    assert len(history) == 1
    # Walk should have stopped after the second call (out-of-window).
    assert call_count == 2


def test_walk_strategy_unknown_job_on_first_call_raises_job_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"stdErr": ["CAUAJM_E_50027 Invalid Job Name: nope"]},
        )

    adapter = _make_strategy_adapter(handler)
    with pytest.raises(JobNotFound):
        adapter.get_job_history("nope")


def test_days_flag_strategy_single_call_with_d_flag():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode())
        fixture = (FIXTURES / "autorep_wide_24x.txt").read_text(encoding="utf-8")
        return httpx.Response(200, json={"stdOut": fixture.splitlines()})

    adapter = _make_strategy_adapter(handler, strategy="days-flag")
    history = adapter.get_job_history("etl_load_facts", days=365)

    assert captured["path"] == "/AEWS/command/run"
    assert captured["body"] == {
        "command": "autorep -j etl_load_facts -w -d 365"
    }
    assert len(history) == 6
    assert history[0]["status"] == "SUCCESS"
    assert history[-1]["status"] == "RUNNING"


def test_days_flag_strategy_accepts_raw_text_response():
    """Older / customised AutoSys installs return raw stdout instead of JSON."""
    def handler(request: httpx.Request) -> httpx.Response:
        fixture = (FIXTURES / "autorep_wide_12x.txt").read_text(encoding="utf-8")
        return httpx.Response(200, text=fixture)

    adapter = _make_strategy_adapter(handler, strategy="days-flag")
    history = adapter.get_job_history("etl_load_facts", days=365)
    assert len(history) == 6


def test_days_flag_strategy_parse_failure_propagates_actionable_message():
    """Lock the user-facing wording: stream layer renders `str(exc)` verbatim."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"stdOut": [
                "Job Name        Last Start         Last End           ST/Ex Run/Ntry Pri/Xit",
                "______________  _________________  _________________  _____ ________ _______",
                "this row has wrong number of fields and no dates",
            ]},
        )

    adapter = _make_strategy_adapter(handler, strategy="days-flag")
    with pytest.raises(AutorepParseError) as exc:
        adapter.get_job_history("etl_load_facts")
    assert str(exc.value) == PARSE_ERROR_MESSAGE


def test_get_job_log_path_fallback_reads_from_jil_and_substitutes_variable():
    """std_*_file lives in JIL only; substitute $AUTO_JOB_NAME for a real path."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/AEWS/jil/job":
            assert request.url.params.get("name") == "test9"
            return httpx.Response(
                200,
                text=(
                    "insert_job: test9   job_type: CMD\n"
                    " machine: autosys-test\n"
                    ' std_err_file: "/opt/autosys/logs/$AUTO_JOB_NAME.err"\n'
                    ' std_out_file: "/opt/autosys/logs/${AUTO_JOB_NAME}.out"\n'
                ),
            )
        return httpx.Response(404)

    adapter = _make_adapter(handler)

    err = adapter.get_job_log("test9", stream="err")
    assert "/opt/autosys/logs/test9.err" in err
    assert "$AUTO_JOB_NAME" not in err
    assert "autosys-test" in err

    out = adapter.get_job_log("test9", stream="out")
    assert "/opt/autosys/logs/test9.out" in out
    assert "${AUTO_JOB_NAME}" not in out


def test_get_job_log_reads_local_mount_when_configured(tmp_path):
    """With AUTOSYS_LOG_MOUNT_ROOT set, return the file's content."""
    log_file = tmp_path / "test9.err"
    log_file.write_text("the actual log content\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/AEWS/jil/job":
            return httpx.Response(
                200,
                text=(
                    "insert_job: test9   job_type: CMD\n"
                    ' std_err_file: "/opt/autosys/logs/$AUTO_JOB_NAME.err"\n'
                ),
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = AutoSysClient(
        base_url="https://autosys.example/AEWS/",
        username="x",
        password="x",
    )
    client._client = httpx.Client(
        base_url="https://autosys.example/AEWS/",
        auth=("x", "x"),
        transport=transport,
        headers={"Accept": "application/json"},
    )
    adapter = LiveAdapter(
        base_url="ignored",
        username="ignored",
        password="ignored",
        log_mount_root=str(tmp_path),
        client=client,
    )
    assert adapter.get_job_log("test9") == "the actual log content\n"


def test_get_job_log_local_mount_miss_falls_through_to_path_message(tmp_path):
    """If the file isn't at the mount root, return the path-only message."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/AEWS/jil/job":
            return httpx.Response(
                200,
                text=(
                    "insert_job: test9   job_type: CMD\n"
                    ' std_err_file: "/opt/autosys/logs/$AUTO_JOB_NAME.err"\n'
                ),
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = AutoSysClient(
        base_url="https://autosys.example/AEWS/",
        username="x",
        password="x",
    )
    client._client = httpx.Client(
        base_url="https://autosys.example/AEWS/",
        auth=("x", "x"),
        transport=transport,
        headers={"Accept": "application/json"},
    )
    adapter = LiveAdapter(
        base_url="ignored",
        username="ignored",
        password="ignored",
        log_mount_root=str(tmp_path),  # empty directory — no test9.err here
        client=client,
    )
    result = adapter.get_job_log("test9")
    assert "/opt/autosys/logs/test9.err" in result
    assert "REST API does not expose log content" in result


def test_get_job_log_box_job_returns_useful_message_not_missing_path():
    """BOX jobs never have a std_err_file. Don't pretend it's a config gap."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/AEWS/jil/job":
            return httpx.Response(
                200,
                text="insert_job: testbox1   job_type: BOX\n owner: x\n",
            )
        return httpx.Response(404)

    adapter = _make_adapter(handler)
    result = adapter.get_job_log("testbox1")
    assert "BOX" in result
    assert "child job" in result


def test_get_job_log_unknown_job_raises_job_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="CAUAJM_E_50027 Invalid Job Name")

    adapter = _make_adapter(handler)
    with pytest.raises(JobNotFound):
        adapter.get_job_log("nope")


def test_get_job_log_invalid_stream_raises():
    adapter = _make_adapter(lambda r: httpx.Response(200, json={"job": []}))
    with pytest.raises(ValueError):
        adapter.get_job_log("any", stream="bogus")


def _jil_with_machine_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/AEWS/jil/job":
        return httpx.Response(
            200,
            text=(
                "insert_job: test9   job_type: CMD\n"
                " machine: etl-prod-01.internal\n"
                ' std_err_file: "/opt/autosys/logs/$AUTO_JOB_NAME.err"\n'
            ),
        )
    return httpx.Response(404)


def _orchestrator_returning(content: str):
    """A LogOrchestrator whose SFTP transport always yields `content`."""
    from contextlib import contextmanager

    from adapters.log_orchestrator import LogOrchestrator

    class _File:
        def __init__(self, data): self._data = data
        def seek(self, n): pass
        def read(self): return self._data
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Stat:
        st_size = len(content.encode())

    class _SFTP:
        def stat(self, path): return _Stat()
        def open(self, path, mode="rb"): return _File(content.encode())
        def __enter__(self): return self
        def __exit__(self, *a): return False

    @contextmanager
    def factory(endpoint):
        yield _SFTP()

    host_map = {"default": {"user": "autosys", "key_path": "/k"}}
    return LogOrchestrator(host_map, sftp_factory=factory)


def test_get_job_log_uses_orchestrator_when_configured():
    """With an SFTP orchestrator and no mount, return the fetched content."""
    transport = httpx.MockTransport(_jil_with_machine_handler)
    client = AutoSysClient(base_url="https://autosys.example/AEWS/", username="x", password="x")
    client._client = httpx.Client(
        base_url="https://autosys.example/AEWS/",
        auth=("x", "x"),
        transport=transport,
        headers={"Accept": "application/json"},
    )
    adapter = LiveAdapter(
        base_url="ignored",
        username="ignored",
        password="ignored",
        log_orchestrator=_orchestrator_returning("real stderr from the agent host\n"),
        client=client,
    )
    assert adapter.get_job_log("test9") == "real stderr from the agent host\n"


def test_get_job_log_orchestrator_runs_after_mount_miss(tmp_path):
    """Mount configured but file absent -> fall through to the orchestrator."""
    transport = httpx.MockTransport(_jil_with_machine_handler)
    client = AutoSysClient(base_url="https://autosys.example/AEWS/", username="x", password="x")
    client._client = httpx.Client(
        base_url="https://autosys.example/AEWS/",
        auth=("x", "x"),
        transport=transport,
        headers={"Accept": "application/json"},
    )
    adapter = LiveAdapter(
        base_url="ignored",
        username="ignored",
        password="ignored",
        log_mount_root=str(tmp_path),  # empty -> mount miss
        log_orchestrator=_orchestrator_returning("fetched over sftp\n"),
        client=client,
    )
    assert adapter.get_job_log("test9") == "fetched over sftp\n"


def test_transport_error_surfaces_as_autosys_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = _make_adapter(handler)
    with pytest.raises(AutoSysAPIError) as exc:
        adapter.list_jobs()
    assert "ConnectError" in str(exc.value)
