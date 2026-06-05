"""LogOrchestrator tests with a fake SFTP transport injected via sftp_factory.

The real paramiko session is replaced with a context manager that yields a
canned in-memory filesystem, so these exercise endpoint resolution, the tail
logic, caching, and fail-soft behaviour without needing a live SSH server.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from adapters.log_orchestrator import LogOrchestrator, SSHEndpoint


class _FakeStat:
    def __init__(self, size: int):
        self.st_size = size


class _FakeFile:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def seek(self, pos: int) -> None:
        self._pos = pos

    def read(self) -> bytes:
        return self._data[self._pos:]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeSFTP:
    def __init__(self, files: dict[str, bytes]):
        self._files = files

    def stat(self, path: str) -> _FakeStat:
        if path not in self._files:
            raise FileNotFoundError(path)
        return _FakeStat(len(self._files[path]))

    def open(self, path: str, mode: str = "rb") -> _FakeFile:
        return _FakeFile(self._files[path])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _factory(files: dict[str, bytes], *, calls: list | None = None, raises: Exception | None = None):
    @contextmanager
    def factory(endpoint: SSHEndpoint):
        if calls is not None:
            calls.append(endpoint)
        if raises is not None:
            raise raises
        yield _FakeSFTP(files)

    return factory


DEFAULT_MAP = {
    "default": {"user": "autosys", "key_path": "/keys/id_logs", "port": 22},
    "etl-prod-01.internal": {"host": "10.0.4.11"},
    "fin-prod-02.internal": {"host": "10.0.4.12", "user": "svc_autosys"},
}


def test_disabled_when_no_host_map():
    orch = LogOrchestrator({}, sftp_factory=_factory({}))
    assert orch.enabled is False
    assert orch.fetch("etl-prod-01.internal", "/var/log/a.err") is None


def test_resolves_per_host_override():
    calls: list[SSHEndpoint] = []
    files = {"/var/log/a.err": b"boom"}
    orch = LogOrchestrator(DEFAULT_MAP, sftp_factory=_factory(files, calls=calls))

    assert orch.fetch("fin-prod-02.internal", "/var/log/a.err") == "boom"
    ep = calls[-1]
    assert ep.host == "10.0.4.12"        # explicit host override
    assert ep.user == "svc_autosys"      # per-host user overrides default
    assert ep.key_path == "/keys/id_logs"  # inherited from default


def test_unlisted_machine_uses_name_as_host():
    calls: list[SSHEndpoint] = []
    files = {"/p.err": b"x"}
    orch = LogOrchestrator(DEFAULT_MAP, sftp_factory=_factory(files, calls=calls))

    orch.fetch("agent99.internal", "/p.err")
    assert calls[-1].host == "agent99.internal"   # falls back to machine name
    assert calls[-1].user == "autosys"            # from default


def test_no_endpoint_when_default_lacks_credentials():
    # default has no user/key_path -> nothing resolvable.
    orch = LogOrchestrator(
        {"default": {"port": 22}}, sftp_factory=_factory({"/p": b"x"})
    )
    assert orch.enabled is True
    assert orch.fetch("whatever", "/p") is None


def test_tail_returns_only_last_max_bytes():
    body = b"".join(f"line{i}\n".encode() for i in range(1000))
    files = {"/big.err": body}
    orch = LogOrchestrator(DEFAULT_MAP, max_bytes=50, sftp_factory=_factory(files))

    out = orch.fetch("etl-prod-01.internal", "/big.err")
    assert out == body[-50:].decode()
    assert len(out) == 50


def test_small_file_returns_whole_content():
    files = {"/small.err": b"just a little"}
    orch = LogOrchestrator(DEFAULT_MAP, max_bytes=65536, sftp_factory=_factory(files))
    assert orch.fetch("etl-prod-01.internal", "/small.err") == "just a little"


def test_missing_file_returns_none():
    orch = LogOrchestrator(DEFAULT_MAP, sftp_factory=_factory({}))
    assert orch.fetch("etl-prod-01.internal", "/nope.err") is None


def test_connection_failure_returns_none():
    orch = LogOrchestrator(
        DEFAULT_MAP, sftp_factory=_factory({}, raises=OSError("connection refused"))
    )
    assert orch.fetch("etl-prod-01.internal", "/a.err") is None


def test_empty_path_returns_none():
    orch = LogOrchestrator(DEFAULT_MAP, sftp_factory=_factory({"/a": b"x"}))
    assert orch.fetch("etl-prod-01.internal", "") is None


def test_cache_hit_avoids_second_fetch(tmp_path):
    calls: list[SSHEndpoint] = []
    files = {"/a.err": b"cached body"}
    orch = LogOrchestrator(
        DEFAULT_MAP,
        cache_dir=tmp_path,
        cache_ttl_seconds=300,
        sftp_factory=_factory(files, calls=calls),
    )

    first = orch.fetch("etl-prod-01.internal", "/a.err")
    second = orch.fetch("etl-prod-01.internal", "/a.err")
    assert first == second == "cached body"
    assert len(calls) == 1   # second call served from disk cache


def test_cache_expired_refetches(tmp_path):
    calls: list[SSHEndpoint] = []
    files = {"/a.err": b"body"}
    orch = LogOrchestrator(
        DEFAULT_MAP,
        cache_dir=tmp_path,
        cache_ttl_seconds=0,   # everything is immediately stale
        sftp_factory=_factory(files, calls=calls),
    )

    orch.fetch("etl-prod-01.internal", "/a.err")
    orch.fetch("etl-prod-01.internal", "/a.err")
    assert len(calls) == 2   # TTL 0 -> never a cache hit
