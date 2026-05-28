"""LiveAdapter tests against a synthetic AutoSys server via httpx.MockTransport.

These can't catch every real-world quirk (numeric status edge cases, JIL
formatting variants, unusual `boxName` casing) but they prove the happy
path and the major error mappings work without needing a real AutoSys.
"""

from __future__ import annotations

import httpx
import pytest

from adapters import JobNotFound
from adapters.autosys_client import AutoSysAPIError, AutoSysClient
from adapters.live_adapter import LiveAdapter


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


def test_list_jobs_passes_filter_param():
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json={"job": []})

    adapter = _make_adapter(handler)
    adapter.list_jobs(name_filter="etl")
    assert "filter" in seen_params
    assert "*etl*" in seen_params["filter"]


def test_get_dependencies_parses_jil_condition_and_finds_downstream():
    """Two requests: GET /jil/job for upstream, GET /job for downstream walk."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/AEWS/jil/job":
            assert request.url.params.get("name") == "etl_load_facts"
            return httpx.Response(
                200,
                text=(
                    "insert_job: etl_load_facts   job_type: CMD\n"
                    "   box_name: etl_box_daily\n"
                    "   condition: s(etl_load_dimensions)\n"
                    "   command: /opt/etl/bin/load.sh facts\n"
                ),
            )
        if path == "/AEWS/job":
            return httpx.Response(200, json={"job": [
                {"name": "etl_load_facts", "status": "5"},
                {"name": "etl_validate_daily", "status": "9", "condition": "s(etl_load_facts)"},
                {"name": "report_revenue", "status": "9", "condition": "s(etl_validate_daily)"},
            ]})
        return httpx.Response(404)

    adapter = _make_adapter(handler)
    deps = adapter.get_dependencies("etl_load_facts")
    assert deps["job"] == "etl_load_facts"
    assert deps["upstream"] == ["etl_load_dimensions"]
    assert deps["downstream"] == ["etl_validate_daily"]


def test_get_job_history_is_explicitly_not_implemented():
    adapter = _make_adapter(lambda r: httpx.Response(500))
    with pytest.raises(NotImplementedError) as exc:
        adapter.get_job_history("etl_load_facts")
    assert "sub-task 7" in str(exc.value)


def test_get_job_log_path_fallback_when_no_forwarder():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"job": [{
                "name": "etl_load_facts",
                "status": "5",
                "std_err_file": "/var/log/autosys/etl_load_facts.err",
            }]},
        )

    adapter = _make_adapter(handler)
    result = adapter.get_job_log("etl_load_facts")
    assert "REST API does not expose log content" in result
    assert "/var/log/autosys/etl_load_facts.err" in result


def test_get_job_log_invalid_stream_raises():
    adapter = _make_adapter(lambda r: httpx.Response(200, json={"job": []}))
    with pytest.raises(ValueError):
        adapter.get_job_log("any", stream="bogus")


def test_transport_error_surfaces_as_autosys_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = _make_adapter(handler)
    with pytest.raises(AutoSysAPIError) as exc:
        adapter.list_jobs()
    assert "ConnectError" in str(exc.value)
