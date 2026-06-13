"""Property-based tests for the Terminal_Executor (Properties 14, 15)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.tools.terminal import MAX_OUTPUT_BYTES, TerminalExecutor


# Feature: ai-devops-coding-agent, Property 14: Command result capping and exit-code fidelity
# For any command, run_command returns standard output and standard error each
# capped at 1 megabyte and preserves the command's exit code, including
# non-zero exit codes returned together with the standard-error content.
@settings(max_examples=100, deadline=None)
@given(
    out_size=st.sampled_from([0, 1, 1000, MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES + 250_000]),
    err_size=st.sampled_from([0, 1, 500, MAX_OUTPUT_BYTES + 100_000]),
    code=st.integers(min_value=0, max_value=255),
)
def test_property_14_command_capping_and_exit_code_fidelity(out_size, err_size, code) -> None:
    executor = TerminalExecutor()
    command = (
        f"head -c {out_size} /dev/zero | tr '\\0' 'a'; "
        f"head -c {err_size} /dev/zero | tr '\\0' 'e' 1>&2; "
        f"exit {code}"
    )
    result = executor.run_command(command)

    data = result.data
    assert data["timed_out"] is False
    # Exit code is preserved exactly, including non-zero codes (R9.6).
    assert data["exit_code"] == code
    assert result.success is (code == 0)

    stdout, stderr = data["stdout"], data["stderr"]
    # Each stream is capped at 1 megabyte.
    assert len(stdout) == min(out_size, MAX_OUTPUT_BYTES)
    assert len(stderr) == min(err_size, MAX_OUTPUT_BYTES)
    # The captured content is exactly the produced bytes (up to the cap).
    assert set(stdout) <= {"a"}
    assert set(stderr) <= {"e"}
    # A non-zero exit returns the stderr content in the result envelope.
    if code != 0 and err_size > 0:
        assert "e" in result.error or stderr != ""


# Feature: ai-devops-coding-agent, Property 15: CPU utilization bound
# For any sample, check_cpu_usage returns a percentage value in the closed
# interval [0, 100].
@settings(max_examples=100, deadline=None)
@given(interval=st.floats(min_value=0.0, max_value=0.05))
def test_property_15_cpu_utilization_bound(interval) -> None:
    executor = TerminalExecutor()
    result = executor.check_cpu_usage(interval=interval)
    assert result.success is True
    cpu = result.data["cpu_percent"]
    assert 0.0 <= cpu <= 100.0
