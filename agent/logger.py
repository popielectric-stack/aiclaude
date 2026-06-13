"""File-based activity Logger (writes under ``logs/``).

Responsibilities (Requirement 20):

* Create the ``logs/`` directory if absent before the first write (R20.6).
* Record command text + timestamp (R20.2).
* Record a command result with its exit code and output, capping the output at
  1,000,000 characters and appending a truncation indicator when the original
  output exceeds that limit (R20.3).
* Record a tool's name, arguments, and timestamp (R20.4).
* Stamp every entry with a timestamp and exactly one severity from
  {info, warning, error} plus a category (R20.5).
* On a write failure, return an error indication identifying the failed write
  and let the Agent keep running (R20.7).
"""

from __future__ import annotations

import enum
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

# Maximum number of characters of command output retained in a single log
# record (Requirement 20.3 / Property 27).
MAX_LOG_OUTPUT_CHARS: int = 1_000_000

# Marker appended to a recorded message when the output was truncated.
TRUNCATION_INDICATOR: str = "...[truncated]"

# Categories used to classify log entries (design's LogRecord model).
VALID_CATEGORIES: frozenset[str] = frozenset(
    {"command", "tool", "access", "reconnect", "system"}
)


class Severity(str, enum.Enum):
    """The three permitted severity levels (Requirement 20.5)."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class LogRecord:
    """A single structured log entry.

    Exactly one :class:`Severity` is carried per entry (Requirement 20.5). The
    optional fields capture the structured detail required for command and
    tool executions (Requirements 20.2, 20.3, 20.4).
    """

    timestamp: str
    severity: Severity
    category: str
    message: str
    command: Optional[str] = None
    tool_name: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    exit_code: Optional[int] = None

    def render(self) -> str:
        """Render the record as a single-line, file-friendly string."""
        parts = [self.timestamp, self.severity.value.upper(), self.category]
        if self.command is not None:
            parts.append(f"command={self.command!r}")
        if self.tool_name is not None:
            parts.append(f"tool={self.tool_name}")
        if self.arguments is not None:
            parts.append(f"args={json.dumps(self.arguments, sort_keys=True, default=str)}")
        if self.exit_code is not None:
            parts.append(f"exit_code={self.exit_code}")
        parts.append(f"message={self.message}")
        # Newlines inside the message would break the one-entry-per-line format.
        return " | ".join(parts).replace("\n", "\\n").replace("\r", "\\r")


@dataclass(frozen=True)
class LogOutcome:
    """Result of an attempt to write a log entry (Requirement 20.7).

    ``success`` is ``False`` when the write failed; ``error`` then carries a
    human-readable description and ``record`` is still populated so callers can
    inspect what was intended to be written.
    """

    success: bool
    record: Optional[LogRecord] = None
    error: Optional[str] = None


def cap_output(output: str) -> tuple[str, bool]:
    """Cap ``output`` at :data:`MAX_LOG_OUTPUT_CHARS` characters.

    Returns a ``(message, truncated)`` pair. When truncation occurs the
    returned message is exactly :data:`MAX_LOG_OUTPUT_CHARS` characters long,
    ending with :data:`TRUNCATION_INDICATOR` (Requirement 20.3 / Property 27).
    """
    if len(output) <= MAX_LOG_OUTPUT_CHARS:
        return output, False
    keep = MAX_LOG_OUTPUT_CHARS - len(TRUNCATION_INDICATOR)
    return output[:keep] + TRUNCATION_INDICATOR, True


class Logger:
    """Append-only file logger writing one entry per line under ``logs/``."""

    def __init__(self, log_dir: str = "logs", filename: str = "agent.log") -> None:
        self._log_dir = log_dir
        self._log_path = os.path.join(log_dir, filename)
        # Serialize concurrent writes so interleaved threads cannot corrupt a
        # line in the asyncio + thread-pool execution model.
        self._lock = threading.Lock()

    @property
    def log_path(self) -> str:
        """Absolute or relative path of the active log file."""
        return self._log_path

    @staticmethod
    def _timestamp() -> str:
        """Return an ISO-8601 timestamp for the current instant (UTC)."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _coerce_severity(severity: "Severity | str") -> Severity:
        """Coerce a severity argument to a :class:`Severity` member."""
        if isinstance(severity, Severity):
            return severity
        return Severity(str(severity).lower())

    def _write(self, record: LogRecord) -> LogOutcome:
        """Write ``record`` to the log file, creating ``logs/`` if needed.

        Never raises: a write failure is converted into a failed
        :class:`LogOutcome` so Agent operation can continue (Requirement 20.7).
        """
        try:
            with self._lock:
                os.makedirs(self._log_dir, exist_ok=True)
                with open(self._log_path, "a", encoding="utf-8") as handle:
                    handle.write(record.render() + "\n")
            return LogOutcome(success=True, record=record)
        except OSError as exc:
            return LogOutcome(
                success=False,
                record=record,
                error=(
                    f"Failed to write log entry to {self._log_path!r}: {exc}"
                ),
            )

    def log(
        self,
        severity: "Severity | str",
        category: str,
        message: str,
        *,
        command: Optional[str] = None,
        tool_name: Optional[str] = None,
        arguments: Optional[Mapping[str, Any]] = None,
        exit_code: Optional[int] = None,
    ) -> LogOutcome:
        """Record a general activity entry and return the write outcome."""
        record = LogRecord(
            timestamp=self._timestamp(),
            severity=self._coerce_severity(severity),
            category=category,
            message=message,
            command=command,
            tool_name=tool_name,
            arguments=dict(arguments) if arguments is not None else None,
            exit_code=exit_code,
        )
        return self._write(record)

    # -- Severity-named convenience wrappers ------------------------------- #

    def info(self, message: str, *, category: str = "system") -> LogOutcome:
        """Record an informational entry."""
        return self.log(Severity.INFO, category, message)

    def warning(self, message: str, *, category: str = "system") -> LogOutcome:
        """Record a warning entry."""
        return self.log(Severity.WARNING, category, message)

    def error(self, message: str, *, category: str = "system") -> LogOutcome:
        """Record an error entry."""
        return self.log(Severity.ERROR, category, message)

    # -- Domain-specific helpers ------------------------------------------- #

    def log_command(
        self, command: str, *, severity: "Severity | str" = Severity.INFO
    ) -> LogOutcome:
        """Record an executed command's text + timestamp (Requirement 20.2)."""
        return self.log(
            severity,
            "command",
            message=command,
            command=command,
        )

    def log_command_result(
        self,
        exit_code: int,
        output: str,
        *,
        severity: "Severity | str" = Severity.INFO,
    ) -> LogOutcome:
        """Record a command result with exit code and capped output (R20.3)."""
        message, _truncated = cap_output(output)
        return self.log(
            severity,
            "command",
            message=message,
            exit_code=exit_code,
        )

    def log_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        severity: "Severity | str" = Severity.INFO,
    ) -> LogOutcome:
        """Record a tool selection/execution (Requirement 20.4)."""
        return self.log(
            severity,
            "tool",
            message=f"executed tool {tool_name}",
            tool_name=tool_name,
            arguments=dict(arguments),
        )
