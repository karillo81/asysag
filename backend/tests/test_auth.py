"""Tests for the single-user login gate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config import settings
from main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def good_creds() -> dict[str, str]:
    return {"username": settings.auth_username, "password": settings.auth_password}


def test_health_is_unauthenticated(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_login_rejects_wrong_password(client, good_creds):
    bad = {"username": good_creds["username"], "password": "definitely-wrong"}
    response = client.post("/login", json=bad)
    assert response.status_code == 401
    assert "session" not in response.cookies


def test_login_rejects_wrong_username(client, good_creds):
    bad = {"username": "not-root", "password": good_creds["password"]}
    response = client.post("/login", json=bad)
    assert response.status_code == 401


def test_login_accepts_correct_credentials(client, good_creds):
    response = client.post("/login", json=good_creds)
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == good_creds["username"]
    assert body["role"] == "admin"  # bootstrap seeds the env user as admin
    assert response.cookies.get("session")


def test_protected_route_requires_session(client):
    # Use a fresh client to avoid the test-client's persistent cookie jar.
    fresh = TestClient(app)
    response = fresh.get("/jobs")
    assert response.status_code == 401


def test_protected_route_with_session_succeeds(client, good_creds):
    client.post("/login", json=good_creds)
    response = client.get("/jobs")
    # 200 in mock mode; 501 only if the adapter is genuinely missing a tool.
    # Either way it's not 401 — that's the assertion.
    assert response.status_code != 401


def test_me_returns_username_when_authenticated(client, good_creds):
    client.post("/login", json=good_creds)
    response = client.get("/me")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == good_creds["username"]
    assert body["role"] == "admin"


def test_me_returns_401_without_session(client):
    fresh = TestClient(app)
    response = fresh.get("/me")
    assert response.status_code == 401


def test_logout_clears_session(client, good_creds):
    client.post("/login", json=good_creds)
    response = client.post("/logout")
    assert response.status_code == 204
    # The server instructs the browser to drop the session cookie; the
    # actual eviction is the browser's job (httpx's test cookie jar does
    # not honour Max-Age=0). Verify the instruction is correct.
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "session=" in set_cookie
    assert "max-age=0" in set_cookie or "1970" in set_cookie
