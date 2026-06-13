"""Unit tests for the Logger, including write-failure continuation (R20.7)."""

from __future__ import annotations

import os

import pytest

from agent.logger import Logger, Severity


def test_logs_directory_created_before_first_write(tmp_path) -> None:
    """The logs/ directory is created lazily before the first write (R20.6)."""
    log_dir = tmp_path / "logs"
    assert not log_dir.exists()

    logger = Logger(log_dir=str(log_dir))
    outcome = logger.info("starting up")

    assert outcome.success is True
    assert log_dir.is_dir()
    assert os.path.isfile(logger.log_path)


def test_entries_are_appended_one_per_line(tmp_path) -> None:
    logger = Logger(log_dir=str(tmp_path / "logs"))
    logger.log_command("ls -la")
    logger.log_command_result(exit_code=0, output="total 0")
    logger.log_tool("write_file", {"path": "/tmp/x", "content": "hi"})

    with open(logger.log_path, encoding="utf-8") as handle:
        lines = [line for line in handle.read().splitlines() if line]

    assert len(lines) == 3
    assert "command='ls -la'" in lines[0]
    assert "exit_code=0" in lines[1]
    assert "tool=write_file" in lines[2]


def test_write_failure_returns_error_and_does_not_raise(tmp_path) -> None:
    """An unwritable logs/ path yields a failed outcome, not a crash (R20.7)."""
    # Create a *file* where the logger expects a directory, so makedirs and the
    # subsequent open both fail with OSError.
    clashing = tmp_path / "logs"
    clashing.write_text("not a directory")

    logger = Logger(log_dir=str(clashing))

    outcome = logger.error("this should fail to write", category="system")

    assert outcome.success is False
    assert outcome.error is not None
    assert str(clashing) in outcome.error or "log" in outcome.error.lower()
    # The record is still available for inspection even though the write failed.
    assert outcome.record is not None
    assert outcome.record.severity is Severity.ERROR


def test_subsequent_logging_continues_after_a_failure(tmp_path) -> None:
    """A write failure must not prevent later successful writes."""
    good_dir = tmp_path / "logs"
    logger = Logger(log_dir=str(good_dir))

    # First, force a failure by pointing at an unwritable target.
    broken = tmp_path / "broken"
    broken.write_text("file-not-dir")
    broken_logger = Logger(log_dir=str(broken))
    assert broken_logger.warning("will fail").success is False

    # The healthy logger still works afterwards.
    assert logger.info("still alive").success is True


def test_category_is_recorded_as_supplied(tmp_path) -> None:
    # The logger records whatever category is supplied without rejecting it.
    logger = Logger(log_dir=str(tmp_path / "logs"))
    outcome = logger.log(Severity.INFO, "command", "msg")
    assert outcome.record.category == "command"
