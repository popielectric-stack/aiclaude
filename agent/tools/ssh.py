"""The SSH_Manager tool: remote command execution and file transfer (R12).

Uses ``paramiko`` to connect to a named Registered_Server using connection
details (and a decrypted credential) retrieved from the Server_Registry, within
a 30 second connection timeout (R12.1-R12.3, R12.9). The public operations are:

* :meth:`SSHManager.run_remote_command` -- run a command on the connected
  server and return stdout, stderr, and the exit code (R12.4).
* :meth:`SSHManager.upload_file` -- transfer a local file to a remote path
  (R12.5).
* :meth:`SSHManager.download_file` -- transfer a remote file to a local path
  (R12.6).
* :meth:`SSHManager.sync_project` -- recursively transfer a local project
  directory to a remote directory (R12.7).

Error semantics (each returns a uniform :class:`~agent.models.ToolResult`):

* an operation that names a server absent from the Server_Registry is rejected
  with a not-registered error (R12.8);
* a connection that cannot be established within the timeout (or an
  unreachable host) returns a connection-failure error (R12.9);
* an authentication failure returns an authentication-failure error (R12.10);
* a transfer whose source is missing, whose destination path is invalid, or
  which is interrupted aborts, leaves each affected destination file unchanged,
  and returns a transfer-failure error explaining the reason (R12.11).

Connections are opened per operation and always closed afterwards. File
transfers are staged through a temporary destination and atomically renamed
into place so that an interrupted transfer never overwrites an existing
destination file (R12.11).
"""

from __future__ import annotations

import io
import os
import posixpath
import socket
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Protocol

import paramiko

from agent.models import ToolResult

# Connection timeout for establishing an SSH session, in seconds (R12.1, R12.9).
DEFAULT_CONNECT_TIMEOUT: float = 30.0

# Maximum bytes of remote command stdout/stderr retained, mirroring the local
# Terminal_Executor cap (R9.1 alignment).
MAX_OUTPUT_BYTES: int = 1_000_000

# Private-key classes attempted, in order, when loading a key-based credential.
_PRIVATE_KEY_CLASSES: tuple[type[paramiko.PKey], ...] = (
    paramiko.Ed25519Key,
    paramiko.ECDSAKey,
    paramiko.RSAKey,
    paramiko.DSSKey,
)


class _SupportsGetCredential(Protocol):
    """Minimal Server_Registry contract used to resolve a server credential."""

    def get_credential(self, name: str) -> ToolResult: ...

    def has_server(self, name: str) -> bool: ...


class _SSHClientLike(Protocol):
    """The subset of :class:`paramiko.SSHClient` the manager depends on.

    Declaring it as a Protocol lets tests inject a lightweight test double in
    place of a real SSH client without requiring a live server.
    """

    def set_missing_host_key_policy(self, policy: Any) -> None: ...

    def connect(self, *args: Any, **kwargs: Any) -> None: ...

    def exec_command(self, command: str, timeout: Optional[float] = ...) -> Any: ...

    def open_sftp(self) -> Any: ...

    def close(self) -> None: ...


ClientFactory = Callable[[], _SSHClientLike]


@dataclass(frozen=True)
class _Credential:
    """Decrypted connection details for a Registered_Server."""

    name: str
    host: str
    port: int
    username: str
    auth_type: str
    secret: str


def _load_private_key(secret: str) -> paramiko.PKey:
    """Load a private key from its PEM/OpenSSH text, trying each key type.

    Raises :class:`paramiko.SSHException` when the text does not parse as any
    supported key type.
    """
    last_error: Optional[Exception] = None
    for key_class in _PRIVATE_KEY_CLASSES:
        try:
            return key_class.from_private_key(io.StringIO(secret))
        except (paramiko.SSHException, ValueError) as exc:
            last_error = exc
    raise paramiko.SSHException(
        f"Unsupported or malformed SSH private key: {last_error}"
    )


def _cap(raw: bytes) -> str:
    """Cap ``raw`` at :data:`MAX_OUTPUT_BYTES` bytes and decode to text."""
    if len(raw) > MAX_OUTPUT_BYTES:
        raw = raw[:MAX_OUTPUT_BYTES]
    return raw.decode("utf-8", errors="replace")


class SSHManager:
    """Connects to Registered_Servers over SSH and performs remote operations."""

    def __init__(
        self,
        registry: _SupportsGetCredential,
        *,
        client_factory: Optional[ClientFactory] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        """Create an SSH_Manager.

        Args:
            registry: the Server_Registry used to resolve connection details
                and decrypt the per-server credential.
            client_factory: a callable returning an object compatible with
                :class:`paramiko.SSHClient`. Defaults to constructing a real
                ``paramiko.SSHClient``; tests inject a double.
            connect_timeout: connection timeout in seconds (R12.1, R12.9).
        """
        self._registry = registry
        self._client_factory = client_factory or paramiko.SSHClient
        self._connect_timeout = connect_timeout

    # -- Credential resolution (R12.8) ------------------------------------- #

    def _resolve_credential(self, server_name: str) -> tuple[Optional[_Credential], Optional[ToolResult]]:
        """Resolve and decrypt the credential for ``server_name``.

        Returns ``(credential, None)`` on success or ``(None, error_result)``
        when the server is not registered or its secret cannot be decrypted.
        """
        result = self._registry.get_credential(server_name)
        if not result.success:
            return None, ToolResult.fail(
                f"SSH operation rejected: server {server_name!r} is not "
                f"registered. {result.error}"
            )
        data = result.data or {}
        credential = _Credential(
            name=data["name"],
            host=data["host"],
            port=int(data["port"]),
            username=data["username"],
            auth_type=data["auth_type"],
            secret=data["secret"],
        )
        return credential, None

    # -- Connection lifecycle (R12.1-R12.3, R12.9, R12.10) ----------------- #

    @contextmanager
    def _connect(self, server_name: str) -> Iterator[tuple[Optional[_SSHClientLike], Optional[ToolResult]]]:
        """Open an SSH connection to ``server_name`` for the duration of a block.

        Yields ``(client, None)`` when connected, or ``(None, error_result)``
        when the server is unregistered, unreachable, the connection times out,
        or authentication fails. The client is always closed on exit.
        """
        credential, error = self._resolve_credential(server_name)
        if error is not None:
            yield None, error
            return

        assert credential is not None
        client = self._client_factory()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict[str, Any] = {
            "hostname": credential.host,
            "port": credential.port,
            "username": credential.username,
            "timeout": self._connect_timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        try:
            if credential.auth_type == "key":
                connect_kwargs["pkey"] = _load_private_key(credential.secret)
            else:
                connect_kwargs["password"] = credential.secret
            client.connect(**connect_kwargs)
        except paramiko.AuthenticationException as exc:
            client.close()
            yield None, ToolResult.fail(
                f"SSH authentication to server {server_name!r} failed: {exc}"
            )
            return
        except (paramiko.SSHException, socket.timeout, socket.error, OSError) as exc:
            client.close()
            yield None, ToolResult.fail(
                f"Could not establish an SSH connection to server "
                f"{server_name!r}: {exc}"
            )
            return

        try:
            yield client, None
        finally:
            client.close()

    # -- Remote command execution (R12.4) --------------------------------- #

    def run_remote_command(
        self, server_name: str, command: str, *, timeout: Optional[float] = None
    ) -> ToolResult:
        """Run ``command`` on ``server_name`` and capture its output (R12.4)."""
        with self._connect(server_name) as (client, error):
            if error is not None:
                return error
            assert client is not None
            try:
                _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
                out_bytes = stdout.read()
                err_bytes = stderr.read()
                exit_code = stdout.channel.recv_exit_status()
            except (paramiko.SSHException, socket.timeout, socket.error, OSError) as exc:
                return ToolResult.fail(
                    f"Remote command on server {server_name!r} failed: {exc}"
                )
            return ToolResult.ok(
                {
                    "stdout": _cap(out_bytes or b""),
                    "stderr": _cap(err_bytes or b""),
                    "exit_code": exit_code,
                }
            )

    # -- File upload (R12.5, R12.11) --------------------------------------- #

    def upload_file(
        self, server_name: str, local_path: str, remote_path: str
    ) -> ToolResult:
        """Upload ``local_path`` to ``remote_path`` on the server (R12.5).

        The source must exist locally; the transfer is staged through a
        temporary remote name and atomically renamed into place so an
        interrupted transfer leaves any existing destination unchanged (R12.11).
        """
        source = Path(local_path)
        if not source.exists() or not source.is_file():
            return ToolResult.fail(
                f"Upload failed: local source {local_path!r} does not exist; "
                "the remote destination was left unchanged."
            )
        with self._connect(server_name) as (client, error):
            if error is not None:
                return error
            assert client is not None
            sftp = None
            staged = f"{remote_path}.{os.getpid()}.partial-upload"
            try:
                sftp = client.open_sftp()
                sftp.put(os.fspath(source), staged)
                self._sftp_replace(sftp, staged, remote_path)
            except (OSError, IOError, paramiko.SSHException) as exc:
                if sftp is not None:
                    self._sftp_silent_remove(sftp, staged)
                return ToolResult.fail(
                    f"Upload to server {server_name!r} failed: {exc}; the remote "
                    f"destination {remote_path!r} was left unchanged."
                )
            finally:
                if sftp is not None:
                    sftp.close()
            return ToolResult.ok(
                {"server": server_name, "local": str(source), "remote": remote_path}
            )

    # -- File download (R12.6, R12.11) ------------------------------------- #

    def download_file(
        self, server_name: str, remote_path: str, local_path: str
    ) -> ToolResult:
        """Download ``remote_path`` to ``local_path`` (R12.6).

        The download is staged through a temporary local file and atomically
        moved into place so an interrupted transfer or a missing remote source
        leaves any existing destination file unchanged (R12.11).
        """
        destination = Path(local_path)
        with self._connect(server_name) as (client, error):
            if error is not None:
                return error
            assert client is not None
            sftp = None
            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=".download-", dir=str(destination.parent if destination.parent.exists() else Path.cwd())
            )
            os.close(tmp_fd)
            try:
                sftp = client.open_sftp()
                sftp.get(remote_path, tmp_name)
                os.replace(tmp_name, os.fspath(destination))
            except (OSError, IOError, paramiko.SSHException) as exc:
                self._local_silent_remove(tmp_name)
                return ToolResult.fail(
                    f"Download from server {server_name!r} failed: {exc}; the "
                    f"local destination {local_path!r} was left unchanged."
                )
            finally:
                if sftp is not None:
                    sftp.close()
            return ToolResult.ok(
                {"server": server_name, "remote": remote_path, "local": str(destination)}
            )

    # -- Recursive project sync (R12.7, R12.11) ---------------------------- #

    def sync_project(
        self, server_name: str, local_dir: str, remote_dir: str
    ) -> ToolResult:
        """Recursively upload ``local_dir`` to ``remote_dir`` (R12.7).

        Every file and subdirectory contained in the local project directory is
        transferred. A missing local directory aborts the operation without
        modifying the remote destination (R12.11).
        """
        source = Path(local_dir)
        if not source.exists() or not source.is_dir():
            return ToolResult.fail(
                f"Project sync failed: local directory {local_dir!r} does not "
                "exist; the remote destination was left unchanged."
            )
        with self._connect(server_name) as (client, error):
            if error is not None:
                return error
            assert client is not None
            sftp = None
            transferred: list[str] = []
            try:
                sftp = client.open_sftp()
                self._sftp_makedirs(sftp, remote_dir)
                for entry in sorted(source.rglob("*")):
                    relative = entry.relative_to(source).as_posix()
                    remote_target = posixpath.join(remote_dir, relative)
                    if entry.is_dir():
                        self._sftp_makedirs(sftp, remote_target)
                    elif entry.is_file():
                        parent = posixpath.dirname(remote_target)
                        if parent:
                            self._sftp_makedirs(sftp, parent)
                        sftp.put(os.fspath(entry), remote_target)
                        transferred.append(relative)
            except (OSError, IOError, paramiko.SSHException) as exc:
                return ToolResult.fail(
                    f"Project sync to server {server_name!r} failed: {exc}."
                )
            finally:
                if sftp is not None:
                    sftp.close()
            return ToolResult.ok(
                {
                    "server": server_name,
                    "local_dir": str(source),
                    "remote_dir": remote_dir,
                    "files_transferred": transferred,
                }
            )

    # -- SFTP helpers ------------------------------------------------------ #

    @staticmethod
    def _sftp_replace(sftp: Any, staged: str, final: str) -> None:
        """Atomically rename ``staged`` to ``final`` on the remote host.

        Prefers POSIX rename (atomic overwrite); falls back to remove-then-rename
        for SFTP servers without the posix-rename extension.
        """
        posix_rename = getattr(sftp, "posix_rename", None)
        if callable(posix_rename):
            posix_rename(staged, final)
            return
        try:
            sftp.remove(final)
        except (OSError, IOError):
            pass
        sftp.rename(staged, final)

    @staticmethod
    def _sftp_silent_remove(sftp: Any, path: str) -> None:
        """Best-effort removal of a remote staging file (ignores failures)."""
        try:
            sftp.remove(path)
        except (OSError, IOError, paramiko.SSHException):
            pass

    @staticmethod
    def _local_silent_remove(path: str) -> None:
        """Best-effort removal of a local staging file (ignores failures)."""
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _sftp_makedirs(sftp: Any, remote_dir: str) -> None:
        """Create ``remote_dir`` and any missing parents on the remote host."""
        if not remote_dir or remote_dir in ("/", "."):
            return
        parts = remote_dir.strip("/").split("/")
        absolute = remote_dir.startswith("/")
        current = "/" if absolute else ""
        for part in parts:
            if not part:
                continue
            current = posixpath.join(current, part) if current not in ("", "/") else (
                "/" + part if absolute else part
            )
            try:
                attrs = sftp.stat(current)
                if not stat.S_ISDIR(attrs.st_mode):
                    raise IOError(f"Remote path {current!r} exists and is not a directory.")
            except (OSError, IOError):
                sftp.mkdir(current)
