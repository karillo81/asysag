import pytest

from users import (
    InvalidUserInput,
    LastAdminError,
    UserExists,
    UserNotFound,
    UserStore,
    hash_password,
    verify_password,
)


@pytest.fixture
def store(tmp_path):
    s = UserStore(tmp_path / "auth.sqlite")
    yield s
    s.close()


# -- hashing ------------------------------------------------------------

def test_hash_is_not_plaintext_and_verifies():
    enc = hash_password("s3cret-password")
    assert "s3cret-password" not in enc
    assert enc.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-password", enc)
    assert not verify_password("wrong", enc)


def test_hash_is_salted_unique_per_call():
    assert hash_password("same") != hash_password("same")


def test_verify_rejects_malformed_encoding():
    assert not verify_password("x", "not-an-encoded-hash")
    assert not verify_password("x", "pbkdf2_sha256$bad")


# -- bootstrap ----------------------------------------------------------

def test_bootstrap_seeds_once(store):
    assert store.bootstrap_admin("root", "changeme") is True
    # Second call is a no-op because the table is no longer empty.
    assert store.bootstrap_admin("root", "other") is False
    root = store.get("root")
    assert root["role"] == "admin" and root["is_active"]
    # Original bootstrap password still valid (not overwritten).
    assert store.verify("root", "changeme") is not None


def test_bootstrap_allows_short_default_password(store):
    # Env default 'changeme' is < 8 chars; bootstrap must still seed it.
    assert store.bootstrap_admin("root", "changeme") is True


def test_bootstrap_refuses_empty_creds(store):
    assert store.bootstrap_admin("", "") is False
    assert store.list() == []


# -- create / verify ----------------------------------------------------

def test_create_and_verify(store):
    store.bootstrap_admin("root", "rootpass8")
    user = store.create("alice", "alice-password", "operator", created_by="root")
    assert user == {
        "username": "alice",
        "role": "operator",
        "is_active": True,
        "created_at": user["created_at"],
        "created_by": "root",
    }
    assert store.verify("alice", "alice-password")["role"] == "operator"
    assert store.verify("alice", "nope") is None


def test_create_duplicate_rejected(store):
    store.create("alice", "alice-password", "operator", created_by="root")
    with pytest.raises(UserExists):
        store.create("alice", "another-password", "admin", created_by="root")


def test_create_validates_input(store):
    with pytest.raises(InvalidUserInput):
        store.create("bad name!", "long-enough", "operator", created_by="root")
    with pytest.raises(InvalidUserInput):
        store.create("bob", "short", "operator", created_by="root")
    with pytest.raises(InvalidUserInput):
        store.create("bob", "long-enough", "superuser", created_by="root")


def test_list_never_exposes_hash(store):
    store.create("alice", "alice-password", "operator", created_by="root")
    rows = store.list()
    assert all("password_hash" not in r for r in rows)


# -- deactivate / password / role --------------------------------------

def test_deactivated_user_cannot_log_in(store):
    store.bootstrap_admin("root", "rootpass8")
    store.create("alice", "alice-password", "operator", created_by="root")
    store.set_active("alice", False)
    assert store.verify("alice", "alice-password") is None
    store.set_active("alice", True)
    assert store.verify("alice", "alice-password") is not None


def test_set_password_changes_login(store):
    store.create("alice", "alice-password", "operator", created_by="root")
    store.set_password("alice", "brand-new-password")
    assert store.verify("alice", "alice-password") is None
    assert store.verify("alice", "brand-new-password") is not None


def test_mutations_on_missing_user_raise(store):
    with pytest.raises(UserNotFound):
        store.set_password("ghost", "long-enough")
    with pytest.raises(UserNotFound):
        store.set_role("ghost", "admin")
    with pytest.raises(UserNotFound):
        store.delete("ghost")


# -- last-admin invariant ----------------------------------------------

def test_cannot_delete_last_admin(store):
    store.bootstrap_admin("root", "rootpass8")
    with pytest.raises(LastAdminError):
        store.delete("root")


def test_cannot_demote_last_admin(store):
    store.bootstrap_admin("root", "rootpass8")
    with pytest.raises(LastAdminError):
        store.set_role("root", "operator")


def test_cannot_deactivate_last_admin(store):
    store.bootstrap_admin("root", "rootpass8")
    with pytest.raises(LastAdminError):
        store.set_active("root", False)


def test_can_remove_admin_when_another_admin_exists(store):
    store.bootstrap_admin("root", "rootpass8")
    store.create("admin2", "admin2-password", "admin", created_by="root")
    # Two admins now — deleting one is allowed.
    store.delete("admin2")
    assert store.get("admin2") is None
    # root is the last admin again, so it is protected once more.
    with pytest.raises(LastAdminError):
        store.delete("root")


def test_can_demote_admin_when_another_admin_exists(store):
    store.bootstrap_admin("root", "rootpass8")
    store.create("admin2", "admin2-password", "admin", created_by="root")
    # Demoting one of two admins is allowed; root remains an active admin.
    store.set_role("admin2", "operator")
    assert store.get("admin2")["role"] == "operator"


def test_inactive_admin_does_not_count_toward_quorum(store):
    store.bootstrap_admin("root", "rootpass8")
    store.create("admin2", "admin2-password", "admin", created_by="root")
    store.set_active("admin2", False)
    # admin2 is inactive, so root is the only *active* admin and is protected.
    with pytest.raises(LastAdminError):
        store.delete("root")
