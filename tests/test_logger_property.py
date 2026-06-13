"""Property-based tests for the Logger (Properties 27 and 28)."""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.logger import (
    MAX_LOG_OUTPUT_CHARS,
    TRUNCATION_INDICATOR,
    Logger,
    Severity,
)

# Lengths biased toward the 1,000,000-character boundary so the truncation
# branch is exercised without allocating huge strings for every example.
_length_strategy = st.one_of(
    st.integers(min_value=0, max_value=16),
    st.integers(min_value=MAX_LOG_OUTPUT_CHARS - 8, max_value=MAX_LOG_OUTPUT_CHARS + 8),
)


# Feature: ai-devops-coding-agent, Property 27: Log output capping
# For any command output, the recorded log message has length at most 1,000,000
# characters and includes a truncation indicator if and only if the original
# output exceeded 1,000,000 characters.
@settings(max_examples=100)
@given(length=_length_strategy, fill=st.sampled_from(["a", "b", "X", "9", " "]))
def test_property_27_log_output_capping(tmp_path, length: int, fill: str) -> None:
    output = fill * length
    logger = Logger(log_dir=str(tmp_path / "logs"))

    outcome = logger.log_command_result(exit_code=0, output=output)

    assert outcome.success is True
    message = outcome.record.message
    assert len(message) <= MAX_LOG_OUTPUT_CHARS

    exceeded = len(output) > MAX_LOG_OUTPUT_CHARS
    if exceeded:
        assert message.endswith(TRUNCATION_INDICATOR)
        assert len(message) == MAX_LOG_OUTPUT_CHARS
    else:
        # Not truncated: the message is the original output verbatim.
        assert message == output


_json_scalar = st.one_of(
    st.text(max_size=20),
    st.integers(),
    st.booleans(),
    st.none(),
)


# Feature: ai-devops-coding-agent, Property 28: Log record completeness
# For any logged command or tool execution, the recorded entry includes the
# command text or tool name, the tool arguments (for tool executions), a
# timestamp, and exactly one severity level drawn from {informational, warning,
# error}.
@settings(max_examples=100)
@given(
    is_tool=st.booleans(),
    command_text=st.text(min_size=1, max_size=40),
    tool_name=st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != ""),
    arguments=st.dictionaries(
        keys=st.text(min_size=1, max_size=10), values=_json_scalar, max_size=5
    ),
    severity=st.sampled_from(list(Severity)),
)
def test_property_28_log_record_completeness(
    tmp_path,
    is_tool: bool,
    command_text: str,
    tool_name: str,
    arguments: dict[str, Any],
    severity: Severity,
) -> None:
    logger = Logger(log_dir=str(tmp_path / "logs"))

    if is_tool:
        outcome = logger.log_tool(tool_name, arguments, severity=severity)
    else:
        outcome = logger.log_command(command_text, severity=severity)

    record = outcome.record
    assert outcome.success is True

    # Timestamp present and non-empty.
    assert isinstance(record.timestamp, str) and record.timestamp != ""

    # Exactly one severity drawn from the permitted set.
    assert record.severity in set(Severity)
    assert record.severity is severity

    if is_tool:
        # Tool executions include the tool name and the arguments.
        assert record.tool_name == tool_name
        assert record.arguments == arguments
    else:
        # Command executions include the command text.
        assert record.command == command_text
