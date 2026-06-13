"""Property and unit tests for the Telegram command dispatcher (Property 32; R4.1, 4.8, 4.9, 4.11)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.telegram.commands import COMMAND_DESCRIPTIONS, CommandDispatcher


@dataclass(frozen=True)
class _Project:
    identifier: str
    name: str
    path: str


class FakeMemory:
    def __init__(self, projects: Optional[dict[str, _Project]] = None) -> None:
        self._projects = projects or {}
        self.cleared = False

    def get_recent_conversations(self, limit: int = 10) -> list[Any]:
        return []

    def get_recent_tasks(self, limit: int = 10) -> list[Any]:
        return []

    def list_projects(self) -> list[Any]:
        return list(self._projects.values())

    def get_project(self, identifier: str) -> Optional[_Project]:
        return self._projects.get(identifier)

    def clear_conversations(self) -> Any:
        self.cleared = True
        return type("R", (), {"success": True, "error": None})()


class FakeRegistry:
    def __init__(self, servers: Optional[list[dict[str, Any]]] = None) -> None:
        self._servers = servers or []

    def list_servers(self) -> list[dict[str, Any]]:
        return list(self._servers)

    def count(self) -> int:
        return len(self._servers)


class FakeLogger:
    def __init__(self, records: Optional[list[str]] = None) -> None:
        self._records = records or []

    def read_recent(self, limit: int = 20) -> list[str]:
        return self._records[:limit]


def _dispatcher(
    *,
    projects: Optional[dict[str, _Project]] = None,
    servers: Optional[list[dict[str, Any]]] = None,
    deploy_calls: Optional[list[Any]] = None,
) -> CommandDispatcher:
    def deploy_starter(project: Any) -> str:
        if deploy_calls is not None:
            deploy_calls.append(project)
        return f"Deployment of project {project.identifier!r} has started."

    return CommandDispatcher(
        memory_store=FakeMemory(projects),
        server_registry=FakeRegistry(servers),
        logger=FakeLogger(["record-1", "record-2"]),
        deploy_starter=deploy_starter,
        in_progress_counter=lambda: 0,
    )


# --------------------------------------------------------------------------- #
# Property 32: Unknown deploy target rejection
# --------------------------------------------------------------------------- #

_ident = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=16
)


# Feature: ai-devops-coding-agent, Property 32: Unknown deploy target rejection
# For any project identifier that does not match a stored project, the /deploy
# command returns an error identifying the unknown identifier and does not begin
# a deployment task.
@settings(max_examples=100)
@given(
    stored=st.lists(_ident, max_size=6, unique=True),
    target=_ident,
)
def test_property_32_unknown_deploy_target_rejection(
    stored: list[str], target: str
) -> None:
    projects = {
        ident: _Project(identifier=ident, name=ident, path=f"/srv/{ident}")
        for ident in stored
    }
    deploy_calls: list[Any] = []
    dispatcher = _dispatcher(projects=projects, deploy_calls=deploy_calls)

    result = dispatcher.dispatch(f"/deploy {target}")

    if target in projects:
        # A known target begins a deployment and is acknowledged.
        assert result.deploy_started == target
        assert len(deploy_calls) == 1
    else:
        # An unknown target is rejected by name with no deployment started.
        assert result.deploy_started is None
        assert deploy_calls == []
        assert target in result.reply
        assert "unknown" in result.reply.lower() or "no matching" in result.reply.lower()


# --------------------------------------------------------------------------- #
# Unit tests: command help and unknown-command handling (R4.1, 4.11)
# --------------------------------------------------------------------------- #


def test_start_lists_commands_with_descriptions() -> None:
    """/start lists every command together with a description (R4.1)."""
    result = _dispatcher().dispatch("/start")
    assert result.is_command
    for name, description in COMMAND_DESCRIPTIONS:
        assert name in result.reply
        assert description in result.reply


def test_unknown_command_lists_available_commands() -> None:
    """An unrecognized command is reported and the commands are listed (R4.11)."""
    result = _dispatcher().dispatch("/frobnicate now")
    assert result.is_command
    assert "unrecognized" in result.reply.lower()
    for name, _description in COMMAND_DESCRIPTIONS:
        assert name in result.reply


def test_deploy_without_identifier_is_rejected() -> None:
    """/deploy with no identifier is rejected and starts no deployment (R4.8)."""
    deploy_calls: list[Any] = []
    result = _dispatcher(deploy_calls=deploy_calls).dispatch("/deploy")
    assert result.deploy_started is None
    assert deploy_calls == []
    assert "identifier is required" in result.reply.lower()


def test_clear_invokes_memory_clear() -> None:
    dispatcher = _dispatcher()
    result = dispatcher.dispatch("/clear")
    assert "cleared" in result.reply.lower()


def test_free_text_is_not_a_command() -> None:
    result = _dispatcher().dispatch("buatkan sebuah file baru")
    assert result.is_command is False


def test_logs_returns_recent_records() -> None:
    result = _dispatcher().dispatch("/logs")
    assert "record-1" in result.reply


def test_command_with_bot_suffix_is_dispatched() -> None:
    result = _dispatcher().dispatch("/start@my_bot")
    assert result.is_command
    assert "/status" in result.reply
