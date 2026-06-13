"""Persistent memory for the AI DevOps & Coding Agent.

Exposes the :class:`~agent.memory.store.MemoryStore`, the SQLite-backed
component that persists conversations, projects, and task history.
"""

from agent.memory.store import MemoryStore, PersistenceError

__all__ = ["MemoryStore", "PersistenceError"]
