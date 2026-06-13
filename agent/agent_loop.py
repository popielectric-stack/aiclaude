"""The Agent_Loop: task orchestration (Requirements 2.4, 7).

:meth:`AgentLoop.run_task` turns a single Owner instruction into a
:class:`Task`, gathers recent context from the Memory_Store, and then drives the
decision loop:

    request decision -> (confirm if destructive) -> execute tool -> analyze
    result -> repeat

The loop terminates when:

* the model reports the task complete (R7.5);
* 25 tool executions have been performed without completion, in which case the
  task is stopped and a partial-result report is produced (R7.6, R7.7);
* a selected tool raises, in which case the failure is logged, a failure result
  identifying the tool is fed back to the model, and the loop keeps running so
  the Agent continues to accept subsequent tasks (R2.4, R7.8);
* the model reports a tool-selection or result-analysis failure (or an invalid
  response), in which case the task is stopped and the failure is reported
  (R7.9).

On completion the conversation record and the task-history record are persisted
through the Memory_Store (R19.2, R19.3). The whole run is wrapped in a
task-boundary try/except so an unexpected error marks the task failed, logs it,
and lets the process continue (R2.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from agent.models import Decision, DecisionKind, Task, TaskStatus, ToolResult

# The maximum number of tool executions for a single task (R7.6, Property 5).
MAX_ITERATIONS: int = 25


class _SupportsDecide(Protocol):
    def decide(
        self, task_context: dict[str, Any], tool_catalog: list[dict[str, Any]]
    ) -> Decision: ...

    def analyze(self, tool_result: Any) -> Decision: ...


class _SupportsMemory(Protocol):
    def get_recent_context(self, n: int = ...) -> Any: ...

    def save_conversation(self, message: str, response: str) -> Any: ...

    def save_task(
        self, description: str, tools: Any, outcome: str, status: str = ...
    ) -> Any: ...


class _SupportsLogging(Protocol):
    def log(self, severity: Any, category: str, message: str, **kwargs: Any) -> Any: ...

    def log_tool(self, tool_name: str, arguments: Any, **kwargs: Any) -> Any: ...


class _SupportsSecurity(Protocol):
    def is_destructive(self, tool_name: str, args: Any) -> bool: ...


# A tool implementation: accepts keyword arguments and returns a ToolResult.
ToolCallable = Callable[..., ToolResult]

# A confirmation gate: given a human-readable description of a destructive
# action, returns True iff the Owner confirmed it (R21.3-R21.7, R21.11).
ConfirmHandler = Callable[[str], bool]

# An Owner notifier used for reporting the final task outcome.
Notifier = Callable[[str], None]


@dataclass
class TaskReport:
    """The outcome of a completed (or stopped) task."""

    task_id: str
    instruction: str
    status: TaskStatus
    report: str
    executed_tools: list[str] = field(default_factory=list)
    iteration_count: int = 0


class AgentLoop:
    """Drives tool selection and execution until a task completes or stops."""

    def __init__(
        self,
        *,
        llm_client: _SupportsDecide,
        memory_store: _SupportsMemory,
        security_manager: _SupportsSecurity,
        logger: _SupportsLogging,
        tools: dict[str, ToolCallable],
        catalog: list[dict[str, Any]],
        confirm_handler: Optional[ConfirmHandler] = None,
        notifier: Optional[Notifier] = None,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self._llm = llm_client
        self._memory = memory_store
        self._security = security_manager
        self._logger = logger
        self._tools = tools
        self._catalog = catalog
        self._confirm = confirm_handler
        self._notify = notifier
        self._max_iterations = max_iterations

    def set_confirm_handler(self, handler: ConfirmHandler) -> None:
        """Inject the destructive-action confirmation gate after construction.

        Used to break the Agent_Loop <-> Telegram_Interface wiring cycle: the
        interface provides a blocking confirmation call once both objects exist.
        """
        self._confirm = handler

    def set_notifier(self, notifier: Notifier) -> None:
        """Inject the Owner notifier used to deliver task reports."""
        self._notify = notifier

    # -- Public entry point ----------------------------------------------- #

    def run_task(self, instruction: str) -> TaskReport:
        """Execute ``instruction`` to completion, the limit, or a failure.

        Wrapped in a task-boundary guard: any unexpected error marks the task
        failed, is logged, and is reported without terminating the process
        (R2.4).
        """
        task = Task(instruction=instruction)
        try:
            return self._drive(task)
        except Exception as exc:  # noqa: BLE001 - task-boundary isolation (R2.4)
            self._logger.log(
                "error",
                "system",
                f"Unhandled error while processing task {task.id}: {exc}",
            )
            task.status = TaskStatus.FAILED
            task.result_report = (
                f"The task failed due to an unexpected internal error: {exc}"
            )
            self._persist(task)
            self._notify_owner(task.result_report)
            return self._report(task)

    # -- Core loop -------------------------------------------------------- #

    def _drive(self, task: Task) -> TaskReport:
        """Run the decision/execution loop for ``task``."""
        context = self._build_context(task)
        decision = self._llm.decide(context, self._catalog)

        while True:
            kind = decision.kind

            if kind is DecisionKind.FEATURE_UNAVAILABLE:
                task.status = TaskStatus.FAILED
                task.result_report = decision.message or (
                    "Language-model features are unavailable."
                )
                break

            if kind in (DecisionKind.FAILURE, DecisionKind.INVALID_RESPONSE):
                # A failed/invalid decision stops the task and is reported (R7.9).
                task.status = TaskStatus.FAILED
                task.result_report = decision.message or "The model request failed."
                break

            if kind is DecisionKind.TASK_COMPLETE:
                task.status = TaskStatus.COMPLETE
                task.result_report = decision.message or "Task completed."
                break

            # kind is SELECT_TOOL from here on.
            if task.iteration_count >= self._max_iterations:
                # The 25-execution safety limit: stop with a partial report
                # (R7.6, R7.7, Property 5).
                task.status = TaskStatus.STOPPED_LIMIT
                task.result_report = self._partial_report(task)
                break

            tool_name = decision.tool_name or ""
            arguments = decision.arguments or {}

            # Gate destructive actions behind explicit confirmation (R21.3-R21.7).
            if self._security.is_destructive(tool_name, arguments):
                if not self._confirm_destructive(tool_name, arguments):
                    result = ToolResult.fail(
                        f"The destructive action {tool_name!r} was cancelled "
                        "because the Owner did not confirm it."
                    )
                    task.record_tool_execution(tool_name)
                    decision = self._llm.analyze(result)
                    continue

            result = self._execute_tool(tool_name, arguments)
            task.record_tool_execution(tool_name)
            decision = self._llm.analyze(result)

        self._persist(task)
        self._notify_owner(task.result_report)
        return self._report(task)

    # -- Tool execution --------------------------------------------------- #

    def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a selected tool, converting a raise into a failure result.

        A tool that raises is logged and a failure result identifying the tool
        is synthesized and returned so it can be fed back to the model; the
        process keeps running (R7.8, Property 6).
        """
        self._logger.log_tool(tool_name, arguments)

        impl = self._tools.get(tool_name)
        if impl is None:
            message = f"No implementation is registered for tool {tool_name!r}."
            self._logger.log("error", "tool", message)
            return ToolResult.fail(message)

        try:
            result = impl(**arguments)
        except Exception as exc:  # noqa: BLE001 - resilience to tool errors (R7.8)
            message = f"Tool {tool_name!r} raised an error during execution: {exc}"
            self._logger.log("error", "tool", message, tool_name=tool_name)
            return ToolResult.fail(message)

        if not isinstance(result, ToolResult):
            # Defensive: a tool must honour the uniform contract.
            return ToolResult.fail(
                f"Tool {tool_name!r} returned a non-ToolResult value."
            )
        return result

    def _confirm_destructive(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Ask the Owner to confirm a destructive action (R21.3)."""
        description = self._describe_action(tool_name, arguments)
        if self._confirm is None:
            # No interactive confirmation channel: deny by default so a
            # destructive action is never executed unconfirmed.
            self._logger.log(
                "warning",
                "system",
                f"Destructive action {tool_name!r} cancelled: no confirmation "
                "channel is available.",
            )
            return False
        return bool(self._confirm(description))

    @staticmethod
    def _describe_action(tool_name: str, arguments: dict[str, Any]) -> str:
        """Build a human-readable description of a destructive action (R21.3)."""
        if arguments:
            arg_text = ", ".join(f"{k}={v!r}" for k, v in sorted(arguments.items()))
            return f"Run destructive action {tool_name!r} with {arg_text}?"
        return f"Run destructive action {tool_name!r}?"

    # -- Context, persistence, reporting ---------------------------------- #

    def _build_context(self, task: Task) -> dict[str, Any]:
        """Gather recent context from the Memory_Store for the model."""
        history_text = ""
        try:
            recent = self._memory.get_recent_context()
            history_text = self._format_history(recent)
        except Exception as exc:  # noqa: BLE001 - context is best-effort
            self._logger.log(
                "warning", "system", f"Failed to load recent context: {exc}"
            )
        return {"instruction": task.instruction, "history": history_text}

    @staticmethod
    def _format_history(recent: Any) -> str:
        """Render the recent-context bundle as compact text for the prompt."""
        lines: list[str] = []
        conversations = getattr(recent, "conversations", []) or []
        tasks = getattr(recent, "tasks", []) or []
        for convo in reversed(list(conversations)):
            lines.append(f"- User: {convo.message}\n  Agent: {convo.response}")
        for record in reversed(list(tasks)):
            tools = ", ".join(record.executed_tools)
            lines.append(
                f"- Past task: {record.description} "
                f"(tools: {tools}; outcome: {record.outcome})"
            )
        return "\n".join(lines)

    @staticmethod
    def _partial_report(task: Task) -> str:
        """Build the partial-result report sent at the iteration limit (R7.7)."""
        tools = ", ".join(task.executed_tools) if task.executed_tools else "none"
        return (
            f"The task was stopped after reaching the {MAX_ITERATIONS}-step "
            f"safety limit without completing. Tools executed ({task.iteration_count}): "
            f"{tools}. Partial progress has been preserved; please refine the "
            "instruction or continue in a follow-up message."
        )

    def _persist(self, task: Task) -> None:
        """Persist the conversation and task-history records (R19.2, R19.3)."""
        try:
            self._memory.save_conversation(task.instruction, task.result_report)
            self._memory.save_task(
                description=task.instruction,
                tools=list(task.executed_tools),
                outcome=task.result_report,
                status=task.status.value,
            )
        except Exception as exc:  # noqa: BLE001 - persistence failure must not crash
            self._logger.log(
                "error", "system", f"Failed to persist task {task.id}: {exc}"
            )

    def _notify_owner(self, message: str) -> None:
        """Deliver the final/partial report to the Owner when a notifier exists."""
        if self._notify is not None and message:
            self._notify(message)

    @staticmethod
    def _report(task: Task) -> TaskReport:
        """Build the :class:`TaskReport` returned to the caller."""
        return TaskReport(
            task_id=task.id,
            instruction=task.instruction,
            status=task.status,
            report=task.result_report,
            executed_tools=list(task.executed_tools),
            iteration_count=task.iteration_count,
        )
