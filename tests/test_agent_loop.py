"""Property tests for the Agent_Loop (Properties 5, 6).

The LLM_Client, tools, Memory_Store, Security_Manager, and Logger are all
replaced with in-memory doubles so the loop logic is exercised purely and
cheaply across many generated inputs.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.agent_loop import MAX_ITERATIONS, AgentLoop
from agent.models import Decision, ToolResult, TaskStatus


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class FakeMemory:
    def __init__(self) -> None:
        self.conversations: list[tuple[str, str]] = []
        self.tasks: list[dict[str, Any]] = []

    def get_recent_context(self, n: int = 20) -> Any:
        return type("Ctx", (), {"conversations": [], "tasks": []})()

    def save_conversation(self, message: str, response: str) -> Any:
        self.conversations.append((message, response))
        return ToolResult.ok()

    def save_task(self, description, tools, outcome, status="COMPLETE") -> Any:
        self.tasks.append(
            {"description": description, "tools": list(tools), "outcome": outcome, "status": status}
        )
        return ToolResult.ok()


class FakeSecurity:
    def __init__(self, destructive: bool = False) -> None:
        self._destructive = destructive

    def is_destructive(self, tool_name: str, args: Any) -> bool:
        return self._destructive


class FakeLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []
        self.tools: list[str] = []

    def log(self, severity: Any, category: str, message: str, **_kwargs: Any) -> None:
        self.entries.append((str(severity), category, message))

    def log_tool(self, tool_name: str, arguments: Any, **_kwargs: Any) -> None:
        self.tools.append(tool_name)


class FakeLLM:
    """A scripted LLM: decide() starts, analyze() drives the next decision."""

    def __init__(
        self,
        *,
        first: Decision,
        next_decision: Callable[[ToolResult], Decision],
    ) -> None:
        self._first = first
        self._next = next_decision
        self.analyzed: list[ToolResult] = []

    def decide(self, task_context: dict, tool_catalog: list) -> Decision:
        return self._first

    def analyze(self, tool_result: ToolResult) -> Decision:
        self.analyzed.append(tool_result)
        return self._next(tool_result)


def _ok_tool(**_kwargs: Any) -> ToolResult:
    return ToolResult.ok({"echoed": True})


# --------------------------------------------------------------------------- #
# Property 5: Agent-loop iteration bound
# --------------------------------------------------------------------------- #

# Feature: ai-devops-coding-agent, Property 5: Agent-loop iteration bound
# For any task for which the model never reports completion, the Agent_Loop
# performs at most 25 tool executions and then stops the task and produces a
# partial-result report.
@settings(max_examples=100)
@given(
    tool_names=st.lists(
        st.sampled_from(["run_command", "read_file", "list_directory", "get_processes"]),
        min_size=1,
        max_size=5,
    ),
    instruction=st.text(min_size=0, max_size=40),
)
def test_property_5_iteration_bound(tool_names: list[str], instruction: str) -> None:
    # The model cycles through tools forever, never reporting completion.
    counter = {"i": 0}

    def never_complete(_result: ToolResult) -> Decision:
        name = tool_names[counter["i"] % len(tool_names)]
        counter["i"] += 1
        return Decision.select_tool(name, {})

    first = Decision.select_tool(tool_names[0], {})
    llm = FakeLLM(first=first, next_decision=never_complete)
    tools = {name: _ok_tool for name in tool_names}

    loop = AgentLoop(
        llm_client=llm,
        memory_store=FakeMemory(),
        security_manager=FakeSecurity(),
        logger=FakeLogger(),
        tools=tools,
        catalog=[],
    )
    report = loop.run_task(instruction)

    # At most 25 tool executions, and stopped with a partial-result report.
    assert report.iteration_count == MAX_ITERATIONS
    assert report.iteration_count <= MAX_ITERATIONS
    assert report.status is TaskStatus.STOPPED_LIMIT
    assert "limit" in report.report.lower() or "partial" in report.report.lower()


# --------------------------------------------------------------------------- #
# Property 6: Tool-error resilience
# --------------------------------------------------------------------------- #

# Feature: ai-devops-coding-agent, Property 6: Tool-error resilience
# For any tool that raises an error during execution, the Agent_Loop records the
# failure through the Logger, feeds a failure result identifying the tool back
# to the LLM_Client, and the Agent process continues running to accept
# subsequent tasks.
@settings(max_examples=100)
@given(
    tool_name=st.sampled_from(["run_command", "read_file", "docker_start", "browser_open"]),
    error_message=st.text(min_size=1, max_size=40),
)
def test_property_6_tool_error_resilience(tool_name: str, error_message: str) -> None:
    def raising_tool(**_kwargs: Any) -> ToolResult:
        raise RuntimeError(error_message)

    # After the failing tool, the model reports completion so the loop ends.
    def complete_after(_result: ToolResult) -> Decision:
        return Decision.task_complete("done")

    llm = FakeLLM(
        first=Decision.select_tool(tool_name, {"x": 1}),
        next_decision=complete_after,
    )
    logger = FakeLogger()
    loop = AgentLoop(
        llm_client=llm,
        memory_store=FakeMemory(),
        security_manager=FakeSecurity(),
        logger=logger,
        tools={tool_name: raising_tool},
        catalog=[],
    )

    report = loop.run_task("attempt the failing tool")

    # The failure was recorded through the Logger.
    assert any(
        sev == "error" and tool_name in msg for sev, _cat, msg in logger.entries
    )
    # A failure result identifying the tool was fed back to the model.
    assert llm.analyzed, "analyze() must be called with the tool result"
    fed_back = llm.analyzed[0]
    assert fed_back.success is False
    assert tool_name in (fed_back.error or "")
    # The loop kept running and accepted the subsequent completion.
    assert report.status is TaskStatus.COMPLETE


def test_run_task_survives_unexpected_internal_error() -> None:
    """An unexpected error inside the loop is isolated at the task boundary (R2.4)."""

    class ExplodingLLM:
        def decide(self, *_a: Any, **_k: Any) -> Decision:
            raise ValueError("boom")

        def analyze(self, *_a: Any, **_k: Any) -> Decision:  # pragma: no cover
            raise AssertionError("not reached")

    logger = FakeLogger()
    loop = AgentLoop(
        llm_client=ExplodingLLM(),
        memory_store=FakeMemory(),
        security_manager=FakeSecurity(),
        logger=logger,
        tools={},
        catalog=[],
    )
    report = loop.run_task("anything")
    assert report.status is TaskStatus.FAILED
    assert any(sev == "error" for sev, _c, _m in logger.entries)


def test_destructive_action_requires_confirmation() -> None:
    """A destructive action is not executed when confirmation is denied (R21.7)."""
    executed = {"ran": False}

    def destructive_tool(**_kwargs: Any) -> ToolResult:  # pragma: no cover
        executed["ran"] = True
        return ToolResult.ok()

    llm = FakeLLM(
        first=Decision.select_tool("delete_file", {"path": "/x"}),
        next_decision=lambda _r: Decision.task_complete("done"),
    )
    loop = AgentLoop(
        llm_client=llm,
        memory_store=FakeMemory(),
        security_manager=FakeSecurity(destructive=True),
        logger=FakeLogger(),
        tools={"delete_file": destructive_tool},
        catalog=[],
        confirm_handler=lambda _desc: False,  # Owner declines
    )
    report = loop.run_task("delete it")
    assert executed["ran"] is False
    # The cancellation result was fed back to the model.
    assert llm.analyzed and llm.analyzed[0].success is False
    assert report.status is TaskStatus.COMPLETE
