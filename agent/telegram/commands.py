"""The Telegram slash-command dispatcher (Requirement 4).

:class:`CommandDispatcher` turns a slash-command message into a textual reply
(and, for ``/deploy``, optionally begins a deployment Task). It is deliberately
free of any networking so it can be unit-tested directly and reused by the
asyncio :class:`~agent.telegram.interface.TelegramInterface`.

Supported commands:

* ``/start``   -- list the available commands with a description of each (R4.1).
* ``/status``  -- uptime, registered-server count, project count, in-progress
  task count (R4.2).
* ``/memory``  -- the 10 most recent conversations and task-history records
  (R4.3).
* ``/clear``   -- delete the conversation history and confirm (R4.4).
* ``/projects``-- the stored projects (R4.5).
* ``/servers`` -- the registered servers, excluding secrets (R4.6).
* ``/deploy <id>`` -- begin a deployment Task for a stored project; a missing
  identifier is rejected (R4.8) and an unknown identifier is rejected by name
  without starting a deployment (R4.9, Property 32).
* ``/logs``    -- the 20 most recent activity records (R4.10).
* anything else -> an "unrecognized command" reply listing the commands (R4.11).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

# Limits per Requirement 4.
MEMORY_LIMIT: int = 10
LOGS_LIMIT: int = 20


class _SupportsMemory(Protocol):
    def get_recent_conversations(self, limit: int = ...) -> list[Any]: ...

    def get_recent_tasks(self, limit: int = ...) -> list[Any]: ...

    def list_projects(self) -> list[Any]: ...

    def get_project(self, identifier: str) -> Optional[Any]: ...

    def clear_conversations(self) -> Any: ...


class _SupportsRegistry(Protocol):
    def list_servers(self) -> list[dict[str, Any]]: ...

    def count(self) -> int: ...


class _SupportsLogReading(Protocol):
    def read_recent(self, limit: int = ...) -> list[str]: ...


# Begins a deployment Task for a stored project; returns an acknowledgement
# string. Injected by the interface so the dispatcher stays transport-free.
DeployStarter = Callable[[Any], str]

# Returns the current in-progress task count for ``/status`` (R4.2).
InProgressCounter = Callable[[], int]


# Command -> short description, used by ``/start`` and the unknown-command reply.
COMMAND_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("/start", "Show this help message listing all available commands."),
    ("/status", "Show uptime, server count, project count, and in-progress tasks."),
    ("/memory", "Show the 10 most recent conversations and tasks."),
    ("/clear", "Delete the stored conversation history."),
    ("/projects", "List the stored projects."),
    ("/servers", "List the registered servers (secrets excluded)."),
    ("/deploy", "Deploy a stored project: /deploy <project_id>."),
    ("/logs", "Show the 20 most recent activity log records."),
)


@dataclass(frozen=True)
class CommandResult:
    """The result of dispatching a slash command.

    ``reply`` is the text to send to the Owner. ``is_command`` is ``False`` when
    the input was not a slash command at all (free text to be routed to the
    Agent_Loop). ``deploy_started`` names the project identifier when a
    deployment Task was begun (R4.7).
    """

    reply: str
    is_command: bool = True
    deploy_started: Optional[str] = None


class CommandDispatcher:
    """Maps slash commands to replies and optional deployment Tasks."""

    def __init__(
        self,
        *,
        memory_store: _SupportsMemory,
        server_registry: _SupportsRegistry,
        logger: _SupportsLogReading,
        deploy_starter: Optional[DeployStarter] = None,
        in_progress_counter: Optional[InProgressCounter] = None,
        start_time: Optional[float] = None,
    ) -> None:
        self._memory = memory_store
        self._registry = server_registry
        self._logger = logger
        self._deploy_starter = deploy_starter
        self._in_progress = in_progress_counter
        self._start_time = start_time if start_time is not None else time.monotonic()

    def set_deploy_starter(self, deploy_starter: DeployStarter) -> None:
        """Inject the deployment starter after construction (wiring cycle)."""
        self._deploy_starter = deploy_starter

    def set_in_progress_counter(self, counter: InProgressCounter) -> None:
        """Inject the in-progress task counter after construction."""
        self._in_progress = counter

    # -- Entry point ------------------------------------------------------ #

    def dispatch(self, text: str) -> CommandResult:
        """Dispatch ``text`` to the matching command handler.

        Returns ``is_command=False`` when ``text`` is not a slash command, so
        the caller can route it to the Agent_Loop as a free-text instruction.
        """
        stripped = (text or "").strip()
        if not stripped.startswith("/"):
            return CommandResult(reply="", is_command=False)

        parts = stripped.split()
        command = parts[0].lower()
        # Telegram allows "/command@botname"; normalize away the bot suffix.
        command = command.split("@", 1)[0]
        args = parts[1:]

        handlers: dict[str, Callable[[list[str]], CommandResult]] = {
            "/start": self._cmd_start,
            "/help": self._cmd_start,
            "/status": self._cmd_status,
            "/memory": self._cmd_memory,
            "/clear": self._cmd_clear,
            "/projects": self._cmd_projects,
            "/servers": self._cmd_servers,
            "/deploy": self._cmd_deploy,
            "/logs": self._cmd_logs,
        }
        handler = handlers.get(command)
        if handler is None:
            return self._cmd_unknown(command)
        return handler(args)

    # -- Individual commands ---------------------------------------------- #

    def _cmd_start(self, _args: list[str]) -> CommandResult:
        """List the available commands and their descriptions (R4.1)."""
        lines = ["Available commands:"]
        for name, description in COMMAND_DESCRIPTIONS:
            lines.append(f"{name} - {description}")
        return CommandResult(reply="\n".join(lines))

    def _cmd_status(self, _args: list[str]) -> CommandResult:
        """Report uptime and resource counts (R4.2)."""
        uptime_seconds = max(0.0, time.monotonic() - self._start_time)
        server_count = self._registry.count()
        project_count = len(self._memory.list_projects())
        in_progress = self._in_progress() if self._in_progress is not None else 0
        reply = (
            "Agent status:\n"
            f"- Uptime: {self._format_uptime(uptime_seconds)}\n"
            f"- Registered servers: {server_count}\n"
            f"- Stored projects: {project_count}\n"
            f"- In-progress tasks: {in_progress}"
        )
        return CommandResult(reply=reply)

    def _cmd_memory(self, _args: list[str]) -> CommandResult:
        """Summarize the 10 most recent conversations and tasks (R4.3)."""
        conversations = self._memory.get_recent_conversations(MEMORY_LIMIT)
        tasks = self._memory.get_recent_tasks(MEMORY_LIMIT)
        lines = [f"Recent conversations ({len(conversations)}):"]
        if conversations:
            for convo in conversations:
                lines.append(
                    f"- [{convo.created_at}] {convo.message} -> {convo.response}"
                )
        else:
            lines.append("- (none)")
        lines.append(f"Recent tasks ({len(tasks)}):")
        if tasks:
            for record in tasks:
                lines.append(
                    f"- [{record.created_at}] {record.description} "
                    f"({record.status}): {record.outcome}"
                )
        else:
            lines.append("- (none)")
        return CommandResult(reply="\n".join(lines))

    def _cmd_clear(self, _args: list[str]) -> CommandResult:
        """Delete the conversation history and confirm (R4.4)."""
        result = self._memory.clear_conversations()
        if getattr(result, "success", True):
            return CommandResult(reply="Conversation history cleared.")
        error = getattr(result, "error", "unknown error")
        return CommandResult(reply=f"Failed to clear conversation history: {error}")

    def _cmd_projects(self, _args: list[str]) -> CommandResult:
        """List the stored projects (R4.5)."""
        projects = self._memory.list_projects()
        if not projects:
            return CommandResult(reply="No projects are stored.")
        lines = ["Stored projects:"]
        for project in projects:
            lines.append(
                f"- {project.identifier}: {project.name} (path: {project.path})"
            )
        return CommandResult(reply="\n".join(lines))

    def _cmd_servers(self, _args: list[str]) -> CommandResult:
        """List the registered servers without secrets (R4.6)."""
        servers = self._registry.list_servers()
        if not servers:
            return CommandResult(reply="No servers are registered.")
        lines = ["Registered servers:"]
        for server in servers:
            lines.append(
                f"- {server['name']}: {server['username']}@{server['host']}:{server['port']}"
            )
        return CommandResult(reply="\n".join(lines))

    def _cmd_deploy(self, args: list[str]) -> CommandResult:
        """Begin a deployment Task for a stored project (R4.7-R4.9)."""
        if not args:
            # Missing identifier: reject without starting a deployment (R4.8).
            return CommandResult(
                reply=(
                    "A project identifier is required. Usage: /deploy <project_id>."
                )
            )
        identifier = args[0]
        project = self._memory.get_project(identifier)
        if project is None:
            # Unknown identifier: reject by name, no deployment (R4.9, Property 32).
            return CommandResult(
                reply=(
                    f"Unknown project identifier {identifier!r}: no matching "
                    "stored project was found. No deployment was started."
                )
            )
        if self._deploy_starter is None:
            return CommandResult(
                reply="Deployment is not available: no deployment handler is configured."
            )
        acknowledgement = self._deploy_starter(project)
        return CommandResult(reply=acknowledgement, deploy_started=identifier)

    def _cmd_logs(self, _args: list[str]) -> CommandResult:
        """Return the 20 most recent activity records (R4.10)."""
        records = self._logger.read_recent(LOGS_LIMIT)
        if not records:
            return CommandResult(reply="No activity has been logged yet.")
        return CommandResult(
            reply="Recent activity (newest first):\n" + "\n".join(records)
        )

    def _cmd_unknown(self, command: str) -> CommandResult:
        """Report an unrecognized command and list the available ones (R4.11)."""
        lines = [
            f"Unrecognized command {command!r}. Available commands:",
        ]
        for name, description in COMMAND_DESCRIPTIONS:
            lines.append(f"{name} - {description}")
        return CommandResult(reply="\n".join(lines))

    # -- Helpers ---------------------------------------------------------- #

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Render an uptime duration as ``HHh MMm SSs``."""
        total = int(seconds)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours}h {minutes}m {secs}s"
