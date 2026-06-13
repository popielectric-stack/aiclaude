"""Unit tests for the Terminal_Executor timeout and command-failure handling."""

from __future__ import annotations

from agent.tools.terminal import TerminalExecutor


def test_run_command_terminates_on_timeout_with_partial_output() -> None:
    """A command exceeding the timeout is terminated, returning partial output (R9.7)."""
    executor = TerminalExecutor()
    result = executor.run_command("echo start; sleep 5", timeout=0.5)

    assert result.success is False
    assert result.data["timed_out"] is True
    assert "timeout" in result.error.lower()
    # Output produced before termination is preserved.
    assert "start" in result.data["stdout"]


def test_run_command_reports_non_zero_exit_with_stderr() -> None:
    """A non-zero exit returns the exit code and stderr content (R9.6)."""
    executor = TerminalExecutor()
    result = executor.run_command("echo oops 1>&2; exit 3")

    assert result.success is False
    assert result.data["exit_code"] == 3
    assert "oops" in result.data["stderr"]


def test_unrunnable_test_command_returns_error() -> None:
    """An unrunnable command yields a failure result (R18.11)."""
    executor = TerminalExecutor()
    result = executor.run_command("this_command_does_not_exist_xyz --run-tests")

    assert result.success is False
    assert result.data["exit_code"] != 0
    assert result.error is not None


def test_resource_reporters_return_successful_results() -> None:
    """Process/disk/memory reporters return well-formed successful results."""
    executor = TerminalExecutor()

    processes = executor.get_processes()
    assert processes.success is True
    assert isinstance(processes.data["processes"], list)

    disk = executor.check_disk_usage()
    assert disk.success is True
    assert isinstance(disk.data["filesystems"], list)

    memory = executor.check_memory_usage()
    assert memory.success is True
    assert memory.data["total_mb"] >= 0
    assert memory.data["available_mb"] >= 0
