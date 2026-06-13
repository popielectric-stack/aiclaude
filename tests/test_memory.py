"""Unit tests for the database layer and Memory_Store.

Focuses on persistence-failure rollback: a failing transaction preserves prior
records without partial modification (Requirement 19.7).
"""

from __future__ import annotations

import pytest

from agent.database.connection import open_database
from agent.memory.store import MemoryStore


def _conversation_count(db) -> int:
    return db.connection.execute("SELECT COUNT(*) AS c FROM conversations").fetchone()["c"]


def _project_count(db) -> int:
    return db.connection.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]


def test_schema_tables_exist_after_open() -> None:
    db = open_database(":memory:")
    try:
        rows = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        names = {row["name"] for row in rows}
        assert {"conversations", "servers", "projects", "task_history", "credentials"} <= names
    finally:
        db.close()


def test_transaction_rolls_back_on_exception_preserving_prior_records() -> None:
    """A raising transaction body rolls back without partial modification."""
    db = open_database(":memory:")
    store = MemoryStore(db)
    try:
        # Prior record committed in its own transaction.
        assert store.save_conversation("prior", "record").success is True
        assert _conversation_count(db) == 1

        # A transaction that inserts a row then raises must leave nothing behind.
        with pytest.raises(RuntimeError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO conversations (message, response, created_at) "
                    "VALUES (?, ?, ?)",
                    ("inside", "txn", "2024-01-01T00:00:00+00:00"),
                )
                raise RuntimeError("boom mid-transaction")

        # The prior record survives; the in-transaction insert was rolled back.
        assert _conversation_count(db) == 1
        remaining = store.get_recent_conversations(limit=10)
        assert [(c.message, c.response) for c in remaining] == [("prior", "record")]
    finally:
        db.close()


def test_failed_persistence_returns_error_and_preserves_prior_records() -> None:
    """A constraint violation returns an error indication and rolls back (R19.7)."""
    db = open_database(":memory:")
    store = MemoryStore(db)
    try:
        first = store.save_project("dup-id", "Project One", "/srv/one")
        assert first.success is True
        assert _project_count(db) == 1

        # Duplicate identifier violates the UNIQUE constraint.
        second = store.save_project("dup-id", "Project Two", "/srv/two")
        assert second.success is False
        assert second.error is not None

        # No partial modification: still exactly one project, the original one.
        assert _project_count(db) == 1
        stored = store.get_project("dup-id")
        assert stored is not None
        assert stored.name == "Project One"
        assert stored.path == "/srv/one"
    finally:
        db.close()


def test_clear_conversations_is_safe_on_empty_store() -> None:
    db = open_database(":memory:")
    store = MemoryStore(db)
    try:
        assert store.clear_conversations().success is True
        assert store.get_recent_conversations() == []
    finally:
        db.close()


def test_get_recent_context_bundles_conversations_and_tasks() -> None:
    db = open_database(":memory:")
    store = MemoryStore(db)
    try:
        store.save_conversation("hi", "hello")
        store.save_task("do thing", ["write_file"], "done")
        context = store.get_recent_context(n=20)
        assert len(context.conversations) == 1
        assert len(context.tasks) == 1
        assert context.tasks[0].executed_tools == ["write_file"]
    finally:
        db.close()
