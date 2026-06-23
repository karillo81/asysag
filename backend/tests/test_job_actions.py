import json

import httpx
import pytest

from action_store import (
    ActionNotFound,
    ActionStateError,
    ActionStore,
)
from adapters.job_actions import ACTIONS, actions_for_role, get_action
from adapters.live_adapter import LiveAdapter
from adapters.base import AdapterError, JobNotFound


# -- catalog ------------------------------------------------------------

def test_catalog_covers_all_tiers_and_endpoints_are_unique():
    assert {a.tier for a in ACTIONS.values()} == {"A", "B", "C"}
    endpoints = [a.endpoint for a in ACTIONS.values()]
    assert len(endpoints) == len(set(endpoints))  # no dupes


def test_destructive_actions_are_all_tier_c_admin_only():
    for a in ACTIONS.values():
        if a.destructive:
            assert a.tier == "C"
            assert a.min_role == "admin"


def test_role_visibility():
    op = {a.key for a in actions_for_role("operator")}
    admin = {a.key for a in actions_for_role("admin")}
    assert "on_hold" in op and "delete_job" not in op
    assert "delete_job" in admin and admin.issuperset(op)


def test_stop_demon_is_scheduler_scoped():
    assert get_action("stop_demon").target == "scheduler"
    assert get_action("machine_offline").target == "machine"
    assert get_action("on_hold").target == "job"


# -- live adapter: posts the right endpoint/body (no real AEWS) ---------

def _live_with(handler):
    adapter = LiveAdapter("https://aews.test:9443/AEWS/", "u", "p", verify_tls=False)
    adapter._client._client = httpx.Client(
        base_url="https://aews.test:9443/AEWS/",
        transport=httpx.MockTransport(handler),
    )
    return adapter


def test_send_job_event_posts_job_endpoint_and_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "OK"})

    adapter = _live_with(handler)
    out = adapter.send_job_event("force_start_job", "etl_load_facts", {"comment": "rerun"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/AEWS/api/event/force-start-job")
    assert json.loads(seen["body"]) == {"jobName": "etl_load_facts", "comment": "rerun"}
    assert out["response"] == {"status": "OK"}


def test_send_job_event_uses_machine_field_for_machine_actions():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "OK"})

    adapter = _live_with(handler)
    adapter.send_job_event("machine_offline", "autosys-test")
    assert seen["url"].endswith("/AEWS/api/event/machine-offline")
    assert json.loads(seen["body"]) == {"machineName": "autosys-test"}


def test_send_job_event_unknown_action_raises():
    adapter = _live_with(lambda r: httpx.Response(200, json={}))
    with pytest.raises(AdapterError):
        adapter.send_job_event("nuke_everything", "x")


def test_send_job_event_wraps_api_error():
    def handler(request):
        return httpx.Response(500, text="boom")

    adapter = _live_with(handler)
    with pytest.raises(AdapterError):
        adapter.send_job_event("on_hold", "etl_load_facts")


# -- mock adapter: records, validates job ------------------------------

def test_mock_send_job_event_records(mock_adapter):
    out = mock_adapter.send_job_event("on_hold", _a_known_job(mock_adapter))
    assert out["simulated"] is True
    assert len(mock_adapter.sent_events) == 1
    assert mock_adapter.sent_events[0]["action"] == "on_hold"


def test_mock_send_job_event_unknown_job_raises(mock_adapter):
    with pytest.raises(JobNotFound):
        mock_adapter.send_job_event("on_hold", "no_such_job_xyz")


def _a_known_job(adapter):
    return adapter.list_jobs()[0]["name"]


@pytest.fixture
def mock_adapter():
    from pathlib import Path
    from adapters import get_adapter
    return get_adapter("mock", Path(__file__).resolve().parents[2] / "mock-data")


# -- action store lifecycle --------------------------------------------

@pytest.fixture
def store(tmp_path):
    s = ActionStore(tmp_path / "actions.sqlite")
    yield s
    s.close()


def test_propose_then_execute(store):
    a = store.propose("on_hold", "etl_load_facts", tier="A", destructive=False,
                      params={"comment": "x"}, proposed_by="op1")
    assert a["status"] == "proposed" and a["params"] == {"comment": "x"}
    done = store.mark_executed(a["id"], "op1", {"status": "OK"})
    assert done["status"] == "executed"
    assert done["approved_by"] == "op1" and done["result"] == {"status": "OK"}


def test_propose_then_reject(store):
    a = store.propose("delete_job", "etl_load_facts", tier="C", destructive=True,
                      params=None, proposed_by="op1")
    assert a["destructive"] is True
    r = store.mark_rejected(a["id"], "admin1")
    assert r["status"] == "rejected" and r["approved_by"] == "admin1"


def test_cannot_resolve_twice(store):
    a = store.propose("on_hold", "j", tier="A", destructive=False, params=None,
                      proposed_by="op1")
    store.mark_executed(a["id"], "op1", {"ok": True})
    with pytest.raises(ActionStateError):
        store.mark_rejected(a["id"], "op1")


def test_resolve_unknown_action_raises(store):
    with pytest.raises(ActionNotFound):
        store.mark_executed("nope", "op1", {})


def test_list_filters_by_status(store):
    store.propose("on_hold", "j1", tier="A", destructive=False, params=None, proposed_by="op")
    b = store.propose("on_ice", "j2", tier="A", destructive=False, params=None, proposed_by="op")
    store.mark_executed(b["id"], "op", {"ok": 1})
    assert len(store.list(status="proposed")) == 1
    assert len(store.list(status="executed")) == 1
    assert len(store.list()) == 2
