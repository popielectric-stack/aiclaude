"""SQLite connection management, schema creation, and transactions.

Uses the standard-library :mod:`sqlite3` module (Requirements 19.1, 22.5). The
schema mirrors the design's *SQLite Schema* section: ``conversations``,
``servers``, ``projects``, ``task_history``, and ``credentials`` tables, each
created with ``CREATE TABLE IF NOT EXISTS`` and the documented CHECK
constraints.

The :meth:`Database.transaction` context manager runs each persistence
operation inside a single transaction, committing on success and rolling back
on any exception so prior records are preserved without partial modification
(Requirement 19.7).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

# Schema statements applied (idempotently) when a database is opened.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        message     TEXT NOT NULL,
        response    TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS servers (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        name             TEXT NOT NULL UNIQUE,
        host             TEXT NOT NULL,
        port             INTEGER NOT NULL,
        username         TEXT NOT NULL,
        auth_type        TEXT NOT NULL,
        secret_encrypted BLOB NOT NULL,
        created_at       TEXT NOT NULL,
        CHECK (port BETWEEN 1 AND 65535),
        CHECK (length(name) BETWEEN 1 AND 100),
        CHECK (length(username) BETWEEN 1 AND 100),
        CHECK (auth_type IN ('key', 'password'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier  TEXT NOT NULL UNIQUE,
        name        TEXT NOT NULL,
        path        TEXT NOT NULL,
        metadata    TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_history (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        description    TEXT NOT NULL,
        executed_tools TEXT NOT NULL,
        outcome        TEXT NOT NULL,
        status         TEXT NOT NULL,
        created_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS credentials (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL UNIQUE,
        value_encrypted BLOB NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
)


class DatabaseError(Exception):
    """Raised for unrecoverable database setup failures."""


class Database:
    """Thin wrapper around a :class:`sqlite3.Connection`.

    Rows are returned as :class:`sqlite3.Row` objects so callers can access
    columns by name. Foreign-key enforcement is enabled and the connection is
    placed in autocommit mode so :meth:`transaction` can manage explicit
    ``BEGIN`` / ``COMMIT`` / ``ROLLBACK`` boundaries.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        # ``isolation_level=None`` -> autocommit; transactions are explicit.
        # ``check_same_thread=False`` allows use from the bounded thread pool
        # used for blocking tool calls; writes are serialized by the caller.
        self._conn = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    @property
    def path(self) -> str:
        """Filesystem path (or ``":memory:"``) backing this database."""
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying sqlite3 connection (for read queries)."""
        return self._conn

    def _create_schema(self) -> None:
        """Create all tables idempotently."""
        try:
            with self.transaction() as conn:
                for statement in _SCHEMA_STATEMENTS:
                    conn.execute(statement)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise DatabaseError(f"Failed to initialize database schema: {exc}") from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside a single transaction.

        Commits when the block exits normally; rolls back and re-raises if the
        block raises, leaving prior records unchanged (Requirement 19.7).
        """
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def open_database(path: str = ":memory:") -> Database:
    """Open (creating if necessary) a database at ``path`` and apply the schema."""
    return Database(path)
