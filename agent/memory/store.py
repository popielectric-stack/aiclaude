"""The Memory_Store: SQLite-backed persistence (Requirement 19).

Persists conversation records, project records, and task-history records (and
exposes the raw server rows used by the Server_Registry and restart loading).

Key behaviors:

* ``save_conversation`` / ``save_task`` / ``save_project`` each run inside a
  single transaction and, on failure, roll back and return an error indication
  that preserves prior records without partial modification (Requirement 19.7).
* ``get_recent_context`` / ``get_recent_conversations`` / ``get_recent_tasks``
  return at most ``n`` records ordered most-recent-first (``ORDER BY id DESC``)
  and all records when fewer than ``n`` exist (Requirement 19.4).
* ``clear_conversations`` deletes only conversation records, retaining servers,
  projects, task history, and credentials (Requirement 19.5).
* ``load_all`` returns every persisted record for restart loading
  (Requirement 19.6).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from agent.database.connection import Database

# Default retrieval limit for recent context (Requirement 19.4).
DEFAULT_CONTEXT_LIMIT: int = 20


class PersistenceError(Exception):
    """Raised internally to trigger transaction rollback on bad input."""


@dataclass(frozen=True)
class StoreResult:
    """Result of a persistence operation (Requirement 19.7).

    ``success`` is ``False`` when the operation failed and was rolled back;
    ``error`` then carries a human-readable reason. ``data`` carries the new
    row id on success.
    """

    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @classmethod
    def ok(cls, data: Optional[dict[str, Any]] = None) -> "StoreResult":
        return cls(success=True, data=data, error=None)

    @classmethod
    def fail(cls, error: str) -> "StoreResult":
        return cls(success=False, data=None, error=error)


@dataclass(frozen=True)
class ConversationRecord:
    """A persisted conversation (message + response)."""

    id: int
    message: str
    response: str
    created_at: str


@dataclass(frozen=True)
class TaskRecord:
    """A persisted task-history entry."""

    id: int
    description: str
    executed_tools: list[str]
    outcome: str
    status: str
    created_at: str


@dataclass(frozen=True)
class ProjectRecord:
    """A persisted project."""

    id: int
    identifier: str
    name: str
    path: str
    metadata: Optional[dict[str, Any]]
    created_at: str


@dataclass(frozen=True)
class RecentContext:
    """The bundle returned by :meth:`MemoryStore.get_recent_context`."""

    conversations: list[ConversationRecord] = field(default_factory=list)
    tasks: list[TaskRecord] = field(default_factory=list)


@dataclass(frozen=True)
class FullSnapshot:
    """Everything persisted, loaded on restart (Requirement 19.6)."""

    conversations: list[ConversationRecord]
    tasks: list[TaskRecord]
    projects: list[ProjectRecord]
    servers: list[dict[str, Any]]


def _now_iso() -> str:
    """Return an ISO-8601 timestamp for the current instant (UTC)."""
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """SQLite-backed persistent memory."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- Write operations -------------------------------------------------- #

    def save_conversation(self, message: str, response: str) -> StoreResult:
        """Persist a single conversation record (Requirement 19.2)."""
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO conversations (message, response, created_at) "
                    "VALUES (?, ?, ?)",
                    (message, response, _now_iso()),
                )
                new_id = cursor.lastrowid
            return StoreResult.ok({"id": new_id})
        except sqlite3.Error as exc:
            return StoreResult.fail(f"Failed to save conversation: {exc}")

    def save_task(
        self,
        description: str,
        tools: Sequence[str],
        outcome: str,
        status: str = "COMPLETE",
    ) -> StoreResult:
        """Persist a task-history record (Requirement 19.3).

        ``tools`` is stored as a JSON array of tool names.
        """
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO task_history "
                    "(description, executed_tools, outcome, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        description,
                        json.dumps(list(tools)),
                        outcome,
                        status,
                        _now_iso(),
                    ),
                )
                new_id = cursor.lastrowid
            return StoreResult.ok({"id": new_id})
        except sqlite3.Error as exc:
            return StoreResult.fail(f"Failed to save task: {exc}")

    def save_project(
        self,
        identifier: str,
        name: str,
        path: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> StoreResult:
        """Persist a project record (referenced by ``/projects`` and ``/deploy``)."""
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO projects (identifier, name, path, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        identifier,
                        name,
                        path,
                        json.dumps(metadata) if metadata is not None else None,
                        _now_iso(),
                    ),
                )
                new_id = cursor.lastrowid
            return StoreResult.ok({"id": new_id})
        except sqlite3.Error as exc:
            return StoreResult.fail(f"Failed to save project: {exc}")

    def clear_conversations(self) -> StoreResult:
        """Delete all conversation records, retaining everything else (R19.5)."""
        try:
            with self._db.transaction() as conn:
                conn.execute("DELETE FROM conversations")
            return StoreResult.ok()
        except sqlite3.Error as exc:
            return StoreResult.fail(f"Failed to clear conversations: {exc}")

    # -- Read operations --------------------------------------------------- #

    def get_recent_conversations(
        self, limit: int = DEFAULT_CONTEXT_LIMIT
    ) -> list[ConversationRecord]:
        """Return up to ``limit`` conversations, most-recent-first (R19.4)."""
        if limit <= 0:
            return []
        rows = self._db.connection.execute(
            "SELECT id, message, response, created_at FROM conversations "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            ConversationRecord(
                id=row["id"],
                message=row["message"],
                response=row["response"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_recent_tasks(self, limit: int = DEFAULT_CONTEXT_LIMIT) -> list[TaskRecord]:
        """Return up to ``limit`` task-history records, most-recent-first."""
        if limit <= 0:
            return []
        rows = self._db.connection.execute(
            "SELECT id, description, executed_tools, outcome, status, created_at "
            "FROM task_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_recent_context(self, n: int = DEFAULT_CONTEXT_LIMIT) -> RecentContext:
        """Return the recent conversations and tasks bundle (Requirement 19.4)."""
        return RecentContext(
            conversations=self.get_recent_conversations(n),
            tasks=self.get_recent_tasks(n),
        )

    def list_projects(self) -> list[ProjectRecord]:
        """Return all stored projects, most-recent-first."""
        rows = self._db.connection.execute(
            "SELECT id, identifier, name, path, metadata, created_at "
            "FROM projects ORDER BY id DESC"
        ).fetchall()
        return [self._row_to_project(row) for row in rows]

    def get_project(self, identifier: str) -> Optional[ProjectRecord]:
        """Return the project with ``identifier`` or ``None`` if absent."""
        row = self._db.connection.execute(
            "SELECT id, identifier, name, path, metadata, created_at "
            "FROM projects WHERE identifier = ?",
            (identifier,),
        ).fetchone()
        return self._row_to_project(row) if row is not None else None

    def load_all(self) -> FullSnapshot:
        """Load every persisted record for restart (Requirement 19.6)."""
        conversations = self.get_recent_conversations(self._count("conversations") or 1)
        tasks = self.get_recent_tasks(self._count("task_history") or 1)
        projects = self.list_projects()
        server_rows = self._db.connection.execute(
            "SELECT id, name, host, port, username, auth_type, created_at "
            "FROM servers ORDER BY id DESC"
        ).fetchall()
        servers = [dict(row) for row in server_rows]
        return FullSnapshot(
            conversations=conversations,
            tasks=tasks,
            projects=projects,
            servers=servers,
        )

    # -- Internal helpers -------------------------------------------------- #

    def _count(self, table: str) -> int:
        """Return the number of rows in ``table`` (internal use only).

        ``table`` is restricted to a known allow-list of identifiers so it can
        never carry untrusted input into the query string.
        """
        allowed = {"conversations", "task_history", "projects", "servers"}
        if table not in allowed:
            raise ValueError(f"Unknown table: {table!r}")
        row = self._db.connection.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return int(row["c"])

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            description=row["description"],
            executed_tools=list(json.loads(row["executed_tools"])),
            outcome=row["outcome"],
            status=row["status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> ProjectRecord:
        raw_metadata = row["metadata"]
        return ProjectRecord(
            id=row["id"],
            identifier=row["identifier"],
            name=row["name"],
            path=row["path"],
            metadata=json.loads(raw_metadata) if raw_metadata is not None else None,
            created_at=row["created_at"],
        )
