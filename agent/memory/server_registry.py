"""The Server_Registry: a logical view over the ``servers`` SQLite table.

Validates and persists Registered_Server records and lists them without ever
exposing stored secrets (Requirement 11).

Behaviour:

* :meth:`ServerRegistry.register` validates the name (1-100 chars), host
  (non-empty), port (integer 1-65535), username (1-100 chars), and that at
  least one credential (SSH key or password) is present; rejects duplicate
  names; encrypts the secret through the Security_Manager before persistence;
  and enforces a capacity of at most :data:`MAX_SERVERS` (Requirements
  11.1-11.5, 11.7, 11.8).
* :meth:`ServerRegistry.list_servers` returns each server's name, host, port,
  and username while excluding the stored SSH key/password (Requirements 11.6,
  4.6).
* :meth:`ServerRegistry.has_server` backs the Security_Manager's
  server-target authorization (Requirements 12.8, 21.8).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from agent.database.connection import Database
from agent.models import ToolResult
from agent.security import EncryptionError, SecurityManager

# Field length and capacity bounds from Requirement 11.
NAME_MIN_LEN: int = 1
NAME_MAX_LEN: int = 100
USERNAME_MIN_LEN: int = 1
USERNAME_MAX_LEN: int = 100
PORT_MIN: int = 1
PORT_MAX: int = 65535
MAX_SERVERS: int = 100

# Authentication types persisted in the ``servers.auth_type`` column.
AUTH_TYPE_KEY: str = "key"
AUTH_TYPE_PASSWORD: str = "password"


def _now_iso() -> str:
    """Return an ISO-8601 timestamp for the current instant (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _is_nonempty_str(value: object) -> bool:
    """Return ``True`` when ``value`` is a non-empty string."""
    return isinstance(value, str) and len(value) >= 1


class ServerRegistry:
    """Persistent registry of remote servers, layered over :class:`Database`."""

    def __init__(self, database: Database, security: SecurityManager) -> None:
        self._db = database
        self._security = security

    # -- Registration (Requirements 11.1-11.5, 11.7, 11.8) ----------------- #

    def register(
        self,
        name: str,
        host: str,
        port: int,
        username: str,
        *,
        ssh_key: Optional[str] = None,
        password: Optional[str] = None,
    ) -> ToolResult:
        """Validate, encrypt, and persist a Registered_Server.

        Returns a successful :class:`~agent.models.ToolResult` carrying the new
        row id, or a failure result that identifies the invalid/missing value
        without persisting anything (Requirement 11.7).
        """
        validation_error = self._validate(name, host, port, username, ssh_key, password)
        if validation_error is not None:
            return ToolResult.fail(validation_error)

        # Prefer key-based auth when both credentials are supplied.
        if _is_nonempty_str(ssh_key):
            auth_type, secret = AUTH_TYPE_KEY, ssh_key
        else:
            auth_type, secret = AUTH_TYPE_PASSWORD, password
        assert secret is not None  # guaranteed by validation

        # Reject duplicate names without overwriting the existing record (R11.8).
        if self.has_server(name):
            return ToolResult.fail(
                f"A server named {name!r} already exists; registration rejected."
            )

        # Enforce the capacity ceiling (Requirement 11.5).
        if self.count() >= MAX_SERVERS:
            return ToolResult.fail(
                f"Server registry is full: at most {MAX_SERVERS} servers may be "
                "registered."
            )

        # Encrypt the secret before any write; a failure aborts the store and
        # never persists plaintext (Requirements 11.4, 21.1, 21.9).
        try:
            secret_encrypted = self._security.encrypt(secret)
        except EncryptionError as exc:
            return ToolResult.fail(f"Failed to secure server credential: {exc}")

        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO servers "
                    "(name, host, port, username, auth_type, secret_encrypted, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (name, host, port, username, auth_type, secret_encrypted, _now_iso()),
                )
                new_id = cursor.lastrowid
        except sqlite3.Error as exc:
            return ToolResult.fail(f"Failed to persist server {name!r}: {exc}")

        return ToolResult.ok({"id": new_id, "name": name, "auth_type": auth_type})

    @staticmethod
    def _validate(
        name: str,
        host: str,
        port: int,
        username: str,
        ssh_key: Optional[str],
        password: Optional[str],
    ) -> Optional[str]:
        """Return an error message for the first violated constraint, else None."""
        if not isinstance(name, str) or not (NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN):
            return (
                f"Invalid server name: must be {NAME_MIN_LEN}-{NAME_MAX_LEN} "
                "characters and non-empty."
            )
        if not _is_nonempty_str(host):
            return "Invalid host: the host address must be non-empty."
        if isinstance(port, bool) or not isinstance(port, int) or not (PORT_MIN <= port <= PORT_MAX):
            return f"Invalid port: must be an integer in {PORT_MIN}-{PORT_MAX}."
        if not isinstance(username, str) or not (
            USERNAME_MIN_LEN <= len(username) <= USERNAME_MAX_LEN
        ):
            return (
                f"Invalid username: must be {USERNAME_MIN_LEN}-{USERNAME_MAX_LEN} "
                "characters and non-empty."
            )
        if not _is_nonempty_str(ssh_key) and not _is_nonempty_str(password):
            return (
                "Invalid credentials: either an SSH private key or a password "
                "must be provided."
            )
        return None

    # -- Queries (Requirements 11.6, 4.6) ---------------------------------- #

    def list_servers(self) -> list[dict[str, object]]:
        """Return each server's public fields, excluding stored secrets (R11.6)."""
        rows = self._db.connection.execute(
            "SELECT name, host, port, username FROM servers ORDER BY id ASC"
        ).fetchall()
        return [
            {
                "name": row["name"],
                "host": row["host"],
                "port": row["port"],
                "username": row["username"],
            }
            for row in rows
        ]

    def has_server(self, name: str) -> bool:
        """Return ``True`` when a server named ``name`` is registered."""
        row = self._db.connection.execute(
            "SELECT 1 FROM servers WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return row is not None

    def count(self) -> int:
        """Return the number of Registered_Servers currently stored."""
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS c FROM servers"
        ).fetchone()
        return int(row["c"])

    def get_credential(self, name: str) -> ToolResult:
        """Return the decrypted credential for ``name`` (in-memory use only).

        Used by the SSH_Manager to authenticate connections. The decrypted
        value is never persisted (Requirement 21.2). Returns a failure result
        when the server is unknown or the stored secret cannot be decrypted
        (Requirement 21.10).
        """
        row = self._db.connection.execute(
            "SELECT name, host, port, username, auth_type, secret_encrypted "
            "FROM servers WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return ToolResult.fail(f"Server {name!r} is not registered.")
        try:
            secret = self._security.decrypt(row["secret_encrypted"])
        except Exception as exc:  # noqa: BLE001 - decrypt failure aborts the op
            return ToolResult.fail(
                f"Failed to decrypt credential for server {name!r}: {exc}"
            )
        return ToolResult.ok(
            {
                "name": row["name"],
                "host": row["host"],
                "port": row["port"],
                "username": row["username"],
                "auth_type": row["auth_type"],
                "secret": secret,
            }
        )
