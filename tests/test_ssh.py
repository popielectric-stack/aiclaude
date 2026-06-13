"""Integration tests for the SSH_Manager (Requirement 12).

These exercise connect / command / upload / download / sync plus the
connection-failure, authentication-failure, and unregistered-server paths
against an in-process ``paramiko`` test double, so no real SSH server is
required. The real Server_Registry (over an in-memory SQLite database) resolves
and decrypts credentials, so credential resolution is covered end to end.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import paramiko
import pytest
from cryptography.fernet import Fernet

from agent.database.connection import open_database
from agent.memory.server_registry import ServerRegistry
from agent.security import SecurityManager
from agent.tools.ssh import SSHManager


# --------------------------------------------------------------------------- #
# Test doubles standing in for paramiko's SSHClient / SFTPClient.
# --------------------------------------------------------------------------- #


class _FakeChannel:
    def __init__(self, exit_status: int) -> None:
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self._exit_status


class _FakeStream:
    def __init__(self, payload: bytes, exit_status: int = 0) -> None:
        self._payload = payload
        self.channel = _FakeChannel(exit_status)

    def read(self) -> bytes:
        return self._payload


class _FakeSFTP:
    """Records SFTP operations against an in-memory view of the remote host."""

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store
        self.put_calls: list[tuple[str, str]] = []
        self.renamed: list[tuple[str, str]] = []
        self.made_dirs: list[str] = []

    def put(self, local: str, remote: str) -> None:
        self.put_calls.append((local, remote))
        self._store.setdefault("files", {})[remote] = Path(local).read_bytes()

    def posix_rename(self, src: str, dst: str) -> None:
        self.renamed.append((src, dst))
        files = self._store.setdefault("files", {})
        if src in files:
            files[dst] = files.pop(src)

    def get(self, remote: str, local: str) -> None:
        files = self._store.setdefault("files", {})
        if remote not in files:
            raise IOError(f"No such remote file: {remote}")
        Path(local).write_bytes(files[remote])

    def remove(self, path: str) -> None:
        self._store.setdefault("files", {}).pop(path, None)

    def stat(self, path: str) -> Any:
        if path in self._store.setdefault("dirs", set()):
            return type("Attrs", (), {"st_mode": 0o040755})()
        raise IOError(f"No such directory: {path}")

    def mkdir(self, path: str) -> None:
        self.made_dirs.append(path)
        self._store.setdefault("dirs", set()).add(path)

    def close(self) -> None:
        pass


class _FakeClient:
    """A configurable stand-in for :class:`paramiko.SSHClient`."""

    def __init__(
        self,
        *,
        store: dict[str, Any],
        connect_error: Exception | None = None,
        command_output: tuple[bytes, bytes, int] = (b"ok\n", b"", 0),
    ) -> None:
        self._store = store
        self._connect_error = connect_error
        self._command_output = command_output
        self.connected = False
        self.closed = False

    def set_missing_host_key_policy(self, policy: Any) -> None:
        self._policy = policy

    def connect(self, **kwargs: Any) -> None:
        if self._connect_error is not None:
            raise self._connect_error
        self.connected = True

    def exec_command(self, command: str, timeout: float | None = None) -> Any:
        out, err, code = self._command_output
        return None, _FakeStream(out, code), _FakeStream(err)

    def open_sftp(self) -> _FakeSFTP:
        return _FakeSFTP(self._store)

    def close(self) -> None:
        self.closed = True


def _make_registry() -> ServerRegistry:
    db = open_database(":memory:")
    security = SecurityManager(fernet_key=Fernet.generate_key())
    registry = ServerRegistry(db, security)
    registry.register("web", "10.0.0.5", 22, "deploy", password="s3cret")
    return registry


def _factory(client: _FakeClient):
    return lambda: client


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_run_remote_command_returns_output_and_exit_code() -> None:
    """Connect + run a command, returning stdout/stderr/exit code (R12.1, R12.4)."""
    store: dict[str, Any] = {}
    client = _FakeClient(store=store, command_output=(b"hello\n", b"warn\n", 0))
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    result = manager.run_remote_command("web", "echo hello")

    assert result.success is True
    assert result.data["stdout"] == "hello\n"
    assert result.data["stderr"] == "warn\n"
    assert result.data["exit_code"] == 0
    assert client.connected is True
    assert client.closed is True


def test_run_remote_command_preserves_nonzero_exit_code() -> None:
    """A non-zero remote exit code is reported back (R12.4)."""
    client = _FakeClient(store={}, command_output=(b"", b"boom\n", 7))
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    result = manager.run_remote_command("web", "false")

    assert result.success is True
    assert result.data["exit_code"] == 7
    assert result.data["stderr"] == "boom\n"


def test_unregistered_server_is_rejected(tmp_path: Path) -> None:
    """An operation on an unregistered server is rejected (R12.8)."""
    client = _FakeClient(store={})
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    result = manager.run_remote_command("ghost", "echo hi")

    assert result.success is False
    assert "not registered" in result.error
    assert client.connected is False


def test_connection_failure_is_reported() -> None:
    """A connection timeout / unreachable host returns a connection error (R12.9)."""
    client = _FakeClient(store={}, connect_error=socket.timeout("timed out"))
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    result = manager.run_remote_command("web", "echo hi")

    assert result.success is False
    assert "could not establish" in result.error.lower()
    assert client.closed is True


def test_authentication_failure_is_reported() -> None:
    """An authentication failure returns an auth-failure error (R12.10)."""
    client = _FakeClient(
        store={}, connect_error=paramiko.AuthenticationException("bad creds")
    )
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    result = manager.run_remote_command("web", "echo hi")

    assert result.success is False
    assert "authentication" in result.error.lower()
    assert client.closed is True


def test_upload_file_transfers_and_renames_atomically(tmp_path: Path) -> None:
    """Upload stages a temp file then renames it into place (R12.5, R12.11)."""
    store: dict[str, Any] = {}
    sftp_holder: dict[str, _FakeSFTP] = {}

    class _CapturingClient(_FakeClient):
        def open_sftp(self) -> _FakeSFTP:
            sftp = _FakeSFTP(self._store)
            sftp_holder["sftp"] = sftp
            return sftp

    client = _CapturingClient(store=store)
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    local = tmp_path / "artifact.txt"
    local.write_text("payload", encoding="utf-8")

    result = manager.upload_file("web", str(local), "/srv/app/artifact.txt")

    assert result.success is True
    sftp = sftp_holder["sftp"]
    assert sftp.put_calls and sftp.put_calls[0][1].endswith(".partial-upload")
    assert sftp.renamed and sftp.renamed[0][1] == "/srv/app/artifact.txt"
    assert store["files"]["/srv/app/artifact.txt"] == b"payload"


def test_upload_missing_local_source_leaves_destination_unchanged(tmp_path: Path) -> None:
    """A missing upload source aborts without touching the destination (R12.11)."""
    client = _FakeClient(store={})
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    result = manager.upload_file("web", str(tmp_path / "nope.txt"), "/srv/app/x")

    assert result.success is False
    assert "does not exist" in result.error
    # No connection should even be attempted for a missing source.
    assert client.connected is False


def test_download_file_writes_destination(tmp_path: Path) -> None:
    """Download retrieves a remote file to a local path (R12.6)."""
    store: dict[str, Any] = {"files": {"/srv/app/data.bin": b"remote-bytes"}}
    client = _FakeClient(store=store)
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    dest = tmp_path / "data.bin"
    result = manager.download_file("web", "/srv/app/data.bin", str(dest))

    assert result.success is True
    assert dest.read_bytes() == b"remote-bytes"


def test_download_missing_remote_leaves_existing_destination_unchanged(tmp_path: Path) -> None:
    """A missing remote source leaves an existing local file unchanged (R12.11)."""
    store: dict[str, Any] = {"files": {}}
    client = _FakeClient(store=store)
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    dest = tmp_path / "keep.txt"
    dest.write_text("original", encoding="utf-8")

    result = manager.download_file("web", "/srv/app/missing.txt", str(dest))

    assert result.success is False
    assert "left unchanged" in result.error
    assert dest.read_text(encoding="utf-8") == "original"


def test_sync_project_transfers_tree_recursively(tmp_path: Path) -> None:
    """sync_project transfers every nested file under the project dir (R12.7)."""
    store: dict[str, Any] = {}
    client = _FakeClient(store=store)
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    project = tmp_path / "proj"
    (project / "sub").mkdir(parents=True)
    (project / "app.py").write_text("print(1)", encoding="utf-8")
    (project / "sub" / "util.py").write_text("x = 2", encoding="utf-8")

    result = manager.sync_project("web", str(project), "/srv/app")

    assert result.success is True
    assert set(result.data["files_transferred"]) == {"app.py", "sub/util.py"}
    assert store["files"]["/srv/app/app.py"] == b"print(1)"
    assert store["files"]["/srv/app/sub/util.py"] == b"x = 2"


def test_sync_project_missing_local_dir_is_rejected(tmp_path: Path) -> None:
    """A missing local project directory aborts the sync (R12.11)."""
    client = _FakeClient(store={})
    manager = SSHManager(_make_registry(), client_factory=_factory(client))

    result = manager.sync_project("web", str(tmp_path / "absent"), "/srv/app")

    assert result.success is False
    assert "does not exist" in result.error
    assert client.connected is False
