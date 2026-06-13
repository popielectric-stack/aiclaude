"""Database layer for the AI DevOps & Coding Agent.

Wraps the standard-library ``sqlite3`` module, owns the schema, and provides a
transaction helper so each persistence operation runs inside a single
transaction with rollback on failure.
"""

from agent.database.connection import (
    Database,
    DatabaseError,
    open_database,
)

__all__ = ["Database", "DatabaseError", "open_database"]
