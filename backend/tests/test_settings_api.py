"""Endpoint tests for admin settings + restart.

Reuses the isolated-store pattern from test_accounts and points the settings
overlay at a temp file so nothing touches the real state dir.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import auth
import config_store
import main
from users import UserStore


@pytest.fixture
def patched(tmp_path, monkeypatch):
    store = UserStore(tmp_path / "auth.sqlite")
    store.bootstrap_admin("admin", "adminpass8")
    store.create("op", "operator-pass", "operator", created_by="admin")
    monkeypatch.setattr(auth, "user_store", store)
    monkeypatch.setattr(main, "user_store", store)
    # Redirect the settings overlay to a temp file.
    monkeypatch.setattr(main, "OVERRIDES_PATH", tmp_path / "overrides.env")
    yield tmp_path
    store.close()


def _client():
    return TestClient(main.app)


def _login(client, username, password):
    return client.post("/login", json={"username": username, "password": password})


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


# -- authz --------------------------------------------------------------

def test_settings_requires_admin(operator_client):
    assert operator_client.get("/admin/settings").status_code == 403


def test_settings_requires_auth(patched):
    assert _client().get("/admin/settings").status_code == 401


def test_restart_requires_admin(operator_client):
    assert operator_client.post("/admin/restart").status_code == 403


# -- read ---------------------------------------------------------------

def test_get_settings_returns_grouped_masked(admin_client):
    r = admin_client.get("/admin/settings")
    assert r.status_code == 200
    groups = r.json()["groups"]
    assert "LLM" in groups
    # The AutoSys password row must never carry a real value.
    autosys = {row["key"]: row for row in groups["AutoSys"]}
    assert autosys["AUTOSYS_PASS"]["secret"] is True
    assert "value" in autosys["AUTOSYS_PASS"]
    assert "password" not in autosys["AUTOSYS_PASS"]["value"].lower()


# -- write --------------------------------------------------------------

def test_put_settings_writes_and_flags_restart(admin_client, patched):
    r = admin_client.put(
        "/admin/settings", json={"updates": {"LITELLM_MODEL": "openai/gpt-4o"}}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] == ["LITELLM_MODEL"]
    assert body["restart_required"] is True
    # Persisted to the overlay file.
    assert config_store.read_overrides(patched / "overrides.env") == {
        "LITELLM_MODEL": "openai/gpt-4o"
    }


def test_put_rejects_unknown_key(admin_client):
    r = admin_client.put("/admin/settings", json={"updates": {"BACKEND_PORT": "1"}})
    assert r.status_code == 422


def test_put_rejects_bad_value(admin_client):
    r = admin_client.put(
        "/admin/settings", json={"updates": {"AUTOSYS_VERIFY_TLS": "maybe"}}
    )
    assert r.status_code == 422


def test_put_blank_secret_is_noop(admin_client, patched):
    admin_client.put("/admin/settings", json={"updates": {"AUTOSYS_PASS": "secret1"}})
    r = admin_client.put("/admin/settings", json={"updates": {"AUTOSYS_PASS": ""}})
    assert r.json()["changed"] == []
    assert config_store.read_overrides(patched / "overrides.env")["AUTOSYS_PASS"] == "secret1"


# -- restart ------------------------------------------------------------

def test_restart_returns_202_and_schedules(admin_client, monkeypatch):
    called = {}
    monkeypatch.setattr(main, "_schedule_restart", lambda *a, **k: called.setdefault("yes", True))
    r = admin_client.post("/admin/restart")
    assert r.status_code == 202
    assert called.get("yes") is True
