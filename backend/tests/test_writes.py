"""Phase 2: the propose -> human-confirm -> execute flow and its gates.

Nothing here touches a live AutoSys — the endpoints use a MockAdapter whose
send_job_event just records the call. The focus is the authorization surface:
master switch, allowlist, role, and the destructive double-confirm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import config
import main
from action_store import ActionStore
from adapters import get_adapter
from agent.actor_context import reset_actor, set_actor
from agent.tools import make_tools
from users import UserStore

MOCK_DIR = Path(__file__).resolve().parents[2] / "mock-data"


def _set_writes(enabled: bool, allowlist=None):
    object.__setattr__(config.settings, "writes_enabled", enabled)
    object.__setattr__(config.settings, "writes_allowlist", allowlist)


@pytest.fixture
def writes_on():
    _set_writes(True, None)
    yield
    _set_writes(False, None)


@pytest.fixture
def a_job():
    return get_adapter("mock", MOCK_DIR).list_jobs()[0]["name"]


# -- the propose tool stages but never executes -------------------------

def test_make_tools_includes_propose_only_when_enabled(tmp_path):
    adapter = get_adapter("mock", MOCK_DIR)
    store = ActionStore(tmp_path / "a.sqlite")
    _set_writes(False)
    names = [t.name for t in make_tools(adapter, None, None, store)]
    assert "propose_job_action" not in names
    _set_writes(True)
    names = [t.name for t in make_tools(adapter, None, None, store)]
    assert "propose_job_action" in names
    _set_writes(False)


def test_propose_tool_stages_does_not_execute(tmp_path, writes_on, a_job):
    adapter = get_adapter("mock", MOCK_DIR)
    store = ActionStore(tmp_path / "a.sqlite")
    tools = {t.name: t for t in make_tools(adapter, None, None, store)}
    token = set_actor("op1")
    try:
        out = json.loads(tools["propose_job_action"].invoke(
            {"action": "on_hold", "target": a_job}
        ))
    finally:
        reset_actor(token)
    assert out["status"] == "proposed"
    assert out["destructive"] is False
    assert store.list(status="proposed")[0]["proposed_by"] == "op1"
    assert adapter.sent_events == []  # nothing executed


def test_propose_tool_rejects_destructive_not_in_allowlist(tmp_path, a_job):
    _set_writes(True, None)  # empty allowlist -> destructive blocked
    try:
        adapter = get_adapter("mock", MOCK_DIR)
        store = ActionStore(tmp_path / "a.sqlite")
        tools = {t.name: t for t in make_tools(adapter, None, None, store)}
        out = json.loads(tools["propose_job_action"].invoke(
            {"action": "delete_job", "target": a_job}
        ))
        assert out["error"] == "action_not_allowed"
        assert store.list() == []
    finally:
        _set_writes(False)


def test_propose_tool_unknown_job(tmp_path, writes_on):
    adapter = get_adapter("mock", MOCK_DIR)
    store = ActionStore(tmp_path / "a.sqlite")
    tools = {t.name: t for t in make_tools(adapter, None, None, store)}
    out = json.loads(tools["propose_job_action"].invoke(
        {"action": "on_hold", "target": "no_such_job"}
    ))
    assert out["error"] == "unknown_job"


# -- endpoints: authz gates --------------------------------------------

@pytest.fixture
def patched(tmp_path, monkeypatch):
    users = UserStore(tmp_path / "auth.sqlite")
    users.bootstrap_admin("admin", "adminpass8")
    users.create("op", "operator-pass", "operator", created_by="admin")
    store = ActionStore(tmp_path / "actions.sqlite")
    adapter = get_adapter("mock", MOCK_DIR)
    monkeypatch.setattr(auth, "user_store", users)
    monkeypatch.setattr(main, "user_store", users)
    monkeypatch.setattr(main, "action_store", store)
    monkeypatch.setattr(main, "adapter", adapter)
    yield store, adapter
    users.close()
    store.close()


def _client():
    return TestClient(main.app)


def _login(c, u, p):
    return c.post("/login", json={"username": u, "password": p})


@pytest.fixture
def admin_client(patched):
    c = _client()
    assert _login(c, "admin", "adminpass8").status_code == 200
    return c


@pytest.fixture
def operator_client(patched):
    c = _client()
    assert _login(c, "op", "operator-pass").status_code == 200
    return c


def _stage(store, action, target, *, tier, destructive, by="op"):
    return store.propose(action, target, tier=tier, destructive=destructive,
                         params=None, proposed_by=by)


def test_actions_list_requires_auth(patched):
    assert _client().get("/actions").status_code == 401


def test_confirm_unknown_action_404(admin_client):
    assert admin_client.post("/actions/nope/confirm", json={}).status_code == 404


def test_confirm_blocked_when_writes_disabled(patched, admin_client, a_job):
    store, _ = patched
    _set_writes(False)
    a = _stage(store, "on_hold", a_job, tier="A", destructive=False)
    assert admin_client.post(f"/actions/{a['id']}/confirm", json={}).status_code == 403


def test_operator_confirms_tier_a_executes(patched, operator_client, writes_on, a_job):
    store, adapter = patched
    a = _stage(store, "on_hold", a_job, tier="A", destructive=False)
    r = operator_client.post(f"/actions/{a['id']}/confirm", json={})
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert adapter.sent_events[0]["action"] == "on_hold"  # actually executed (mock)


def test_operator_cannot_confirm_tier_c(patched, operator_client, a_job):
    store, _ = patched
    _set_writes(True, "delete_job")  # destructive allowlisted
    try:
        a = _stage(store, "delete_job", a_job, tier="C", destructive=True)
        r = operator_client.post(
            f"/actions/{a['id']}/confirm", json={"confirm_destructive": True}
        )
        assert r.status_code == 403  # needs admin
    finally:
        _set_writes(False)


def test_destructive_requires_double_confirm(patched, admin_client, a_job):
    store, adapter = patched
    _set_writes(True, "delete_job")
    try:
        a = _stage(store, "delete_job", a_job, tier="C", destructive=True)
        # Without the flag -> 400, nothing executed.
        assert admin_client.post(f"/actions/{a['id']}/confirm", json={}).status_code == 400
        assert adapter.sent_events == []
        # With the flag -> executes.
        r = admin_client.post(
            f"/actions/{a['id']}/confirm", json={"confirm_destructive": True}
        )
        assert r.status_code == 200 and r.json()["status"] == "executed"
        assert adapter.sent_events[0]["action"] == "delete_job"
    finally:
        _set_writes(False)


def test_confirm_destructive_blocked_if_not_allowlisted(patched, admin_client, a_job):
    store, adapter = patched
    _set_writes(True, None)  # empty allowlist -> destructive blocked even for admin
    try:
        a = _stage(store, "delete_job", a_job, tier="C", destructive=True)
        r = admin_client.post(
            f"/actions/{a['id']}/confirm", json={"confirm_destructive": True}
        )
        assert r.status_code == 403
        assert adapter.sent_events == []
    finally:
        _set_writes(False)


def test_cannot_confirm_twice(patched, operator_client, writes_on, a_job):
    store, _ = patched
    a = _stage(store, "on_hold", a_job, tier="A", destructive=False)
    operator_client.post(f"/actions/{a['id']}/confirm", json={})
    assert operator_client.post(f"/actions/{a['id']}/confirm", json={}).status_code == 409


def test_reject_marks_rejected_without_executing(patched, operator_client, a_job):
    store, adapter = patched
    a = _stage(store, "force_start_job", a_job, tier="B", destructive=False)
    r = operator_client.post(f"/actions/{a['id']}/reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert adapter.sent_events == []
