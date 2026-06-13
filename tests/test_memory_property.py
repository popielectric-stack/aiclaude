"""Property-based tests for the Memory_Store (Properties 23, 24, 25, 26)."""

from __future__ import annotations

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.database.connection import open_database
from agent.memory.store import MemoryStore, _now_iso

_text = st.text(max_size=40)
_tool_name = st.text(min_size=1, max_size=15)


def _insert_server(db, name: str) -> None:
    """Insert a raw server row directly (Server_Registry is out of scope here)."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO servers "
            "(name, host, port, username, auth_type, secret_encrypted, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, "10.0.0.1", 22, "root", "password", b"ciphertext", _now_iso()),
        )


# Feature: ai-devops-coding-agent, Property 23: Conversation persistence round trip across restart
# For any message and response saved as a conversation record, retrieving
# conversations returns the same content, and the record remains available after
# the Memory_Store is closed and reopened (simulating an Agent restart).
@settings(max_examples=100)
@given(pairs=st.lists(st.tuples(_text, _text), min_size=1, max_size=12))
def test_property_23_conversation_round_trip_across_restart(tmp_path, pairs) -> None:
    db_path = str(tmp_path / "memory.db")

    db = open_database(db_path)
    store = MemoryStore(db)
    for message, response in pairs:
        assert store.save_conversation(message, response).success is True
    db.close()

    # Reopen to simulate an Agent restart.
    reopened = open_database(db_path)
    reopened_store = MemoryStore(reopened)
    loaded = reopened_store.get_recent_conversations(limit=len(pairs))
    reopened.close()

    assert Counter((c.message, c.response) for c in loaded) == Counter(pairs)


# Feature: ai-devops-coding-agent, Property 24: Task-history persistence round trip
# For any completed task, saving its task-history record and then retrieving it
# returns the same task description, the same set of executed tools, and the
# same final outcome.
@settings(max_examples=100)
@given(
    description=_text,
    tools=st.lists(_tool_name, max_size=10),
    outcome=_text,
)
def test_property_24_task_history_round_trip(description, tools, outcome) -> None:
    db = open_database(":memory:")
    store = MemoryStore(db)
    try:
        assert store.save_task(description, tools, outcome).success is True
        retrieved = store.get_recent_tasks(limit=1)
        assert len(retrieved) == 1
        record = retrieved[0]
        assert record.description == description
        assert record.executed_tools == list(tools)
        assert record.outcome == outcome
    finally:
        db.close()


# Feature: ai-devops-coding-agent, Property 25: Recent-context ordering and limit
# For any number of stored records and any retrieval limit L, the store returns
# at most L records ordered from most recent to least recent, and returns all
# available records when fewer than L exist.
@settings(max_examples=100)
@given(
    count=st.integers(min_value=0, max_value=30),
    limit=st.integers(min_value=1, max_value=25),
)
def test_property_25_recent_context_ordering_and_limit(count, limit) -> None:
    db = open_database(":memory:")
    store = MemoryStore(db)
    try:
        inserted_ids: list[int] = []
        for i in range(count):
            result = store.save_conversation(f"msg-{i}", f"resp-{i}")
            assert result.success is True
            inserted_ids.append(result.data["id"])

        returned = store.get_recent_conversations(limit=limit)

        # At most L records, and all available when fewer than L exist.
        assert len(returned) == min(count, limit)

        # Ordered most-recent-first (descending id == reverse insertion order).
        expected_ids = list(reversed(inserted_ids))[:limit]
        assert [c.id for c in returned] == expected_ids
    finally:
        db.close()


# Feature: ai-devops-coding-agent, Property 26: Clear retains non-conversation records
# For any store state, executing /clear deletes all conversation records and
# leaves the Registered_Server records, project records, and task-history
# records unchanged.
@settings(max_examples=100)
@given(
    n_conv=st.integers(min_value=0, max_value=8),
    n_proj=st.integers(min_value=0, max_value=8),
    n_task=st.integers(min_value=0, max_value=8),
    n_server=st.integers(min_value=0, max_value=8),
)
def test_property_26_clear_retains_non_conversation_records(
    n_conv, n_proj, n_task, n_server
) -> None:
    db = open_database(":memory:")
    store = MemoryStore(db)
    try:
        for i in range(n_conv):
            assert store.save_conversation(f"m{i}", f"r{i}").success is True
        for i in range(n_proj):
            assert store.save_project(f"proj-{i}", f"name-{i}", f"/srv/{i}").success is True
        for i in range(n_task):
            assert store.save_task(f"task-{i}", [f"tool{i}"], "ok").success is True
        for i in range(n_server):
            _insert_server(db, f"server-{i}")

        # Sanity: conversations were stored before clearing.
        assert len(store.get_recent_conversations(limit=1000)) == n_conv

        assert store.clear_conversations().success is True

        snapshot = store.load_all()
        assert snapshot.conversations == []
        assert len(snapshot.projects) == n_proj
        assert len(snapshot.tasks) == n_task
        assert len(snapshot.servers) == n_server
    finally:
        db.close()
