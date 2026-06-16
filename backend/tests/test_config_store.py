import os

import pytest

import config_store
from config_store import (
    InvalidSettingInput,
    apply_overrides,
    grouped_view,
    masked_view,
    read_overrides,
    write_overrides,
)


class FakeSettings:
    """Stand-in for the frozen Settings object."""

    litellm_model = "gemini/gemini-3-flash-preview"
    anthropic_api_key = None
    autosys_mode = "live"
    autosys_base_url = "https://aews.example:9443/AEWS/"
    autosys_user = "svc"
    autosys_pass = "super-secret-password"
    autosys_verify_tls = False
    autosys_timeout_seconds = 15.0
    log_forwarder_url_template = None
    autosys_log_mount_root = None
    status_code_overrides = None
    autorep_history_strategy = None
    session_ttl_seconds = 604800


@pytest.fixture
def path(tmp_path):
    return tmp_path / "overrides.env"


# -- read / write round-trip -------------------------------------------

def test_write_then_read_roundtrip(path):
    changed = write_overrides(path, {"LITELLM_MODEL": "openai/gpt-4o"})
    assert changed == ["LITELLM_MODEL"]
    assert read_overrides(path) == {"LITELLM_MODEL": "openai/gpt-4o"}


def test_write_rejects_unknown_key(path):
    with pytest.raises(InvalidSettingInput):
        write_overrides(path, {"BACKEND_PORT": "9999"})  # structural, excluded


def test_write_normalises_typed_values(path):
    write_overrides(
        path,
        {
            "AUTOSYS_VERIFY_TLS": "yes",
            "AUTOSYS_TIMEOUT_SECONDS": "30",
            "SESSION_TTL_SECONDS": "3600",
        },
    )
    saved = read_overrides(path)
    assert saved["AUTOSYS_VERIFY_TLS"] == "true"
    assert saved["AUTOSYS_TIMEOUT_SECONDS"] == "30.0"
    assert saved["SESSION_TTL_SECONDS"] == "3600"


def test_write_rejects_bad_typed_values(path):
    with pytest.raises(InvalidSettingInput):
        write_overrides(path, {"AUTOSYS_VERIFY_TLS": "maybe"})
    with pytest.raises(InvalidSettingInput):
        write_overrides(path, {"SESSION_TTL_SECONDS": "not-a-number"})


def test_write_reports_only_actual_changes(path):
    write_overrides(path, {"LITELLM_MODEL": "openai/gpt-4o"})
    # Same value again -> no change reported.
    assert write_overrides(path, {"LITELLM_MODEL": "openai/gpt-4o"}) == []


# -- secret write-only semantics ---------------------------------------

def test_blank_secret_keeps_existing(path):
    write_overrides(path, {"AUTOSYS_PASS": "first-secret"})
    # An empty submission for a secret must not wipe it.
    assert write_overrides(path, {"AUTOSYS_PASS": ""}) == []
    assert read_overrides(path)["AUTOSYS_PASS"] == "first-secret"


def test_secret_can_be_replaced(path):
    write_overrides(path, {"AUTOSYS_PASS": "first-secret"})
    assert write_overrides(path, {"AUTOSYS_PASS": "second-secret"}) == ["AUTOSYS_PASS"]
    assert read_overrides(path)["AUTOSYS_PASS"] == "second-secret"


# -- apply overlay onto os.environ -------------------------------------

def test_apply_overrides_sets_environ(path, monkeypatch):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    write_overrides(path, {"LITELLM_MODEL": "openai/gpt-4o"})
    applied = apply_overrides(path)
    assert applied["LITELLM_MODEL"] == "openai/gpt-4o"
    assert os.environ["LITELLM_MODEL"] == "openai/gpt-4o"


def test_apply_overrides_wins_over_existing_env(path, monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "base/from-container")
    write_overrides(path, {"LITELLM_MODEL": "override/model"})
    apply_overrides(path)
    assert os.environ["LITELLM_MODEL"] == "override/model"


def test_read_overrides_ignores_non_whitelisted_lines(path):
    path.write_text(
        "# comment\nLITELLM_MODEL=openai/gpt-4o\nBACKEND_PORT=1\nbroken-line\n",
        encoding="utf-8",
    )
    assert read_overrides(path) == {"LITELLM_MODEL": "openai/gpt-4o"}


# -- masked view --------------------------------------------------------

def test_masked_view_never_exposes_secret(path):
    settings = FakeSettings()
    rows = {r["key"]: r for r in masked_view(settings, path)}
    pw = rows["AUTOSYS_PASS"]
    assert pw["secret"] is True
    assert pw["is_set"] is True
    assert "super-secret-password" not in pw["value"]
    assert pw["value"].startswith("••••")


def test_masked_view_shows_plain_nonsecret(path):
    settings = FakeSettings()
    rows = {r["key"]: r for r in masked_view(settings, path)}
    assert rows["LITELLM_MODEL"]["value"] == "gemini/gemini-3-flash-preview"
    assert rows["AUTOSYS_BASE_URL"]["value"] == "https://aews.example:9443/AEWS/"


def test_masked_view_marks_overridden(path):
    settings = FakeSettings()
    write_overrides(path, {"LITELLM_MODEL": "openai/gpt-4o"})
    rows = {r["key"]: r for r in masked_view(settings, path)}
    assert rows["LITELLM_MODEL"]["is_overridden"] is True
    assert rows["AUTOSYS_BASE_URL"]["is_overridden"] is False


def test_grouped_view_groups_by_section(path):
    groups = grouped_view(FakeSettings(), path)
    assert "LLM" in groups and "AutoSys" in groups
    assert any(r["key"] == "AUTOSYS_PASS" for r in groups["AutoSys"])
