"""Shared data models and the uniform tool contract.

This module defines the small set of value objects that flow between the
control layer (``Agent_Loop``, ``LLM_Client``) and the tool layer:

* :class:`ToolResult` -- the uniform ``success`` / ``data`` / ``error``
  envelope returned by every tool (Requirements 8.9, 10.6).
* :class:`Decision` -- the classification the ``LLM_Client`` returns to the
  ``Agent_Loop`` (Requirement 7.3 and the design's Data Models section).
* :class:`Task` -- the in-memory unit of work derived from a single Owner
  instruction.

The constructors :meth:`ToolResult.ok` and :meth:`ToolResult.fail` are the
helpers all tools use to build success and error envelopes.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


class DecisionKind(str, enum.Enum):
    """The kinds of decision the ``LLM_Client`` can return.

    Inherits from ``str`` so values serialize cleanly and compare equal to
    their string form, which keeps logging and JSON encoding straightforward.
    """

    SELECT_TOOL = "SELECT_TOOL"
    TASK_COMPLETE = "TASK_COMPLETE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    FAILURE = "FAILURE"
    FEATURE_UNAVAILABLE = "FEATURE_UNAVAILABLE"


class TaskStatus(str, enum.Enum):
    """The lifecycle states of a :class:`Task`."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    STOPPED_LIMIT = "STOPPED_LIMIT"


@dataclass(frozen=True)
class ToolResult:
    """Uniform envelope returned by every tool.

    Attributes:
        success: ``True`` when the operation completed successfully.
        data: Operation output on success; ``None`` otherwise.
        error: Human-readable reason on failure; ``None`` on success.
    """

    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @classmethod
    def ok(cls, data: Optional[dict[str, Any]] = None) -> "ToolResult":
        """Build a successful result with optional ``data`` payload."""
        return cls(success=True, data=data, error=None)

    @classmethod
    def fail(cls, error: str, data: Optional[dict[str, Any]] = None) -> "ToolResult":
        """Build a failure result carrying a human-readable ``error``.

        An optional ``data`` payload may accompany the error (for example a
        command's partial stdout/stderr captured before a timeout).
        """
        if not error:
            raise ValueError("A failure ToolResult requires a non-empty error message.")
        return cls(success=False, data=data, error=error)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict view suitable for logging or model analysis."""
        return {"success": self.success, "data": self.data, "error": self.error}


@dataclass(frozen=True)
class Decision:
    """A decision returned by the ``LLM_Client`` to the ``Agent_Loop``.

    ``tool_name`` and ``arguments`` are populated only when ``kind`` is
    :attr:`DecisionKind.SELECT_TOOL`. ``message`` carries completion text or an
    error explanation for the other kinds.
    """

    kind: DecisionKind
    tool_name: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    message: Optional[str] = None

    @classmethod
    def select_tool(cls, tool_name: str, arguments: dict[str, Any]) -> "Decision":
        """Build a ``SELECT_TOOL`` decision naming a tool and its arguments."""
        if not tool_name:
            raise ValueError("SELECT_TOOL requires a non-empty tool name.")
        return cls(
            kind=DecisionKind.SELECT_TOOL,
            tool_name=tool_name,
            arguments=dict(arguments),
        )

    @classmethod
    def task_complete(cls, message: str) -> "Decision":
        """Build a ``TASK_COMPLETE`` decision with the completion text."""
        return cls(kind=DecisionKind.TASK_COMPLETE, message=message)

    @classmethod
    def invalid_response(cls, message: str) -> "Decision":
        """Build an ``INVALID_RESPONSE`` decision explaining the problem."""
        return cls(kind=DecisionKind.INVALID_RESPONSE, message=message)

    @classmethod
    def failure(cls, message: str) -> "Decision":
        """Build a ``FAILURE`` decision explaining the failure."""
        return cls(kind=DecisionKind.FAILURE, message=message)

    @classmethod
    def feature_unavailable(cls, message: str) -> "Decision":
        """Build a ``FEATURE_UNAVAILABLE`` decision (LLM features disabled)."""
        return cls(kind=DecisionKind.FEATURE_UNAVAILABLE, message=message)


@dataclass
class Task:
    """A unit of work derived from a single Owner instruction.

    The ``Agent_Loop`` executes the task to completion (or until the safety
    limit) and, on completion, summarizes it into a task-history record.
    """

    instruction: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = TaskStatus.IN_PROGRESS
    executed_tools: list[str] = field(default_factory=list)
    iteration_count: int = 0
    result_report: str = ""

    def record_tool_execution(self, tool_name: str) -> None:
        """Record one executed tool and advance the iteration counter."""
        self.executed_tools.append(tool_name)
        self.iteration_count += 1
