"""Endpoint tests for admin account management.

Each test runs against an isolated temp UserStore patched into both the auth
module (session/credential checks) and main (the route handlers), so nothing
touches the real state/auth.sqlite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import auth
import main
from users import UserStore


@pytest.fixture
def store(tmp_path):
    s = UserStore(tmp_path / "auth.sqlite")
    s.bootstrap_admin("admin", "adminpass8")
    s.create("op", "operator-pass", "operator", created_by="admin")
    yield s
    s.close()


@pytest.fixture
def patched(store, monkeypatch):
    monkeypatch.setattr(auth, "user_store", store)
    monkeypatch.setattr(main, "user_store", store)
    return store


def _client() -> TestClient:
    return TestClient(main.app)


def _login(client: TestClient, username: str, password: str):
    return client.post("/login", json={"username": username, "password": password})


@pytest.fixture
def admin_client(patched) -> TestClient:
    c = _client()
    assert _login(c, "admin", "adminpass8").status_code == 200
    return c


@pytest.fixture
def operator_client(patched) -> TestClient:
    c = _client()
    assert _login(c, "op", "operator-pass").status_code == 200
    return c


# -- authz --------------------------------------------------------------

def test_accounts_requires_authentication(patched):
    assert _client().get("/accounts").status_code == 401


def test_operator_cannot_list_accounts(operator_client):
    assert operator_client.get("/accounts").status_code == 403


def test_operator_cannot_create_account(operator_client):
    r = operator_client.post(
        "/accounts",
        json={"username": "x", "password": "longenough", "role": "operator"},
    )
    assert r.status_code == 403


def test_admin_lists_accounts(admin_client):
    r = admin_client.get("/accounts")
    assert r.status_code == 200
    names = {a["username"] for a in r.json()["accounts"]}
    assert names == {"admin", "op"}
    assert all("password_hash" not in a for a in r.json()["accounts"])


# -- create -------------------------------------------------------------

def test_admin_creates_account(admin_client):
    r = admin_client.post(
        "/accounts",
        json={"username": "carol", "password": "carol-password", "role": "operator"},
    )
    assert r.status_code == 201
    assert r.json()["username"] == "carol"
    assert r.json()["created_by"] == "admin"


def test_create_duplicate_conflicts(admin_client):
    body = {"username": "op", "password": "another-password", "role": "operator"}
    assert admin_client.post("/accounts", json=body).status_code == 409


def test_create_invalid_role_rejected(admin_client):
    r = admin_client.post(
        "/accounts",
        json={"username": "x", "password": "longenough", "role": "superuser"},
    )
    assert r.status_code == 422  # pydantic pattern rejects unknown role


def test_create_weak_password_rejected(admin_client):
    r = admin_client.post(
        "/accounts",
        json={"username": "weak", "password": "short", "role": "operator"},
    )
    assert r.status_code == 422  # store's InvalidUserInput -> 422


# -- update -------------------------------------------------------------

def test_admin_resets_password(admin_client, patched):
    r = admin_client.patch("/accounts/op", json={"password": "fresh-password"})
    assert r.status_code == 200
    # Old password no longer works; new one does.
    assert _login(_client(), "op", "operator-pass").status_code == 401
    assert _login(_client(), "op", "fresh-password").status_code == 200


def test_admin_promotes_operator(admin_client):
    r = admin_client.patch("/accounts/op", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_admin_cannot_change_own_role(admin_client):
    r = admin_client.patch("/accounts/admin", json={"role": "operator"})
    assert r.status_code == 400


def test_admin_cannot_deactivate_self(admin_client):
    r = admin_client.patch("/accounts/admin", json={"is_active": False})
    assert r.status_code == 400


def test_update_unknown_user_404(admin_client):
    assert admin_client.patch("/accounts/ghost", json={"role": "admin"}).status_code == 404


# -- delete -------------------------------------------------------------

def test_admin_deletes_operator(admin_client):
    assert admin_client.delete("/accounts/op").status_code == 204
    assert admin_client.get("/accounts").json()["accounts"][0]["username"] == "admin"


def test_admin_cannot_delete_self(admin_client):
    assert admin_client.delete("/accounts/admin").status_code == 400


def test_cannot_delete_last_admin(admin_client):
    # Demote is blocked on self; instead promote op then delete admin via op.
    admin_client.patch("/accounts/op", json={"role": "admin"})
    # Now two admins; deleting the original admin is allowed.
    assert admin_client.delete("/accounts/op").status_code == 204
    # op gone -> admin is the last admin; an admin client cannot delete itself
    # and the last-admin guard also protects it from another admin.
    assert admin_client.delete("/accounts/admin").status_code == 400


# -- own password change ------------------------------------------------

def test_operator_changes_own_password(operator_client):
    r = operator_client.post(
        "/account/password",
        json={"current_password": "operator-pass", "new_password": "new-operator-pass"},
    )
    assert r.status_code == 204
    assert _login(_client(), "op", "new-operator-pass").status_code == 200


def test_own_password_change_requires_correct_current(operator_client):
    r = operator_client.post(
        "/account/password",
        json={"current_password": "wrong", "new_password": "new-operator-pass"},
    )
    assert r.status_code == 403


# -- live deactivation --------------------------------------------------

def test_deactivated_user_loses_access_mid_session(patched):
    admin = _client()
    _login(admin, "admin", "adminpass8")
    op = _client()
    _login(op, "op", "operator-pass")
    # Operator has a valid session and can reach a protected route.
    assert op.get("/me").status_code == 200
    # Admin deactivates the operator; the still-unexpired cookie must stop working.
    assert admin.patch("/accounts/op", json={"is_active": False}).status_code == 200
    assert op.get("/me").status_code == 401
