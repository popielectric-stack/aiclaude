"""Smoke tests for project structure and startup (Requirement 22).

Validates the package layout (R22.1), the tool modules (R22.2), the presence of
``logs/``, ``requirements.txt``, and ``README.md`` (R22.3), the absence of
placeholders/stubs/TODO markers (R22.6), and that launching the Agent through
``main.build_agent`` initializes the database, memory, telegram, llm, and tools
modules without raising unhandled exceptions (R22.7, R20.6).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from agent import main
from agent.config import Config

# Project root is the parent of the ``tests/`` directory.
ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agent"


# --------------------------------------------------------------------------- #
# Package layout (R22.1, R22.2)
# --------------------------------------------------------------------------- #


def test_agent_package_layout() -> None:
    """The agent/ package contains the required modules and sub-packages (R22.1)."""
    assert (AGENT / "main.py").is_file()
    assert (AGENT / "config.py").is_file()
    for subpackage in ("database", "memory", "telegram", "llm", "tools"):
        assert (AGENT / subpackage).is_dir(), f"missing package: {subpackage}"
        assert (AGENT / subpackage / "__init__.py").is_file()


def test_tools_module_contains_required_tools() -> None:
    """The tools/ module contains every required tool file (R22.2)."""
    tools = AGENT / "tools"
    for name in (
        "ssh.py",
        "git.py",
        "files.py",
        "terminal.py",
        "pterodactyl.py",
        "cpanel.py",
        "docker.py",
        "browser.py",
    ):
        assert (tools / name).is_file(), f"missing tool module: {name}"


def test_project_includes_logs_requirements_and_readme() -> None:
    """logs/, requirements.txt, and README.md are present (R22.3)."""
    assert (ROOT / "logs").is_dir()
    assert (ROOT / "requirements.txt").is_file()
    assert (ROOT / "README.md").is_file()


# --------------------------------------------------------------------------- #
# No placeholders, stubs, TODO markers, or pseudocode (R22.6)
# --------------------------------------------------------------------------- #

_FORBIDDEN = re.compile(
    r"\bTODO\b|\bFIXME\b|\bXXX\b|raise NotImplementedError|pseudocode|placeholder",
    re.IGNORECASE,
)


def test_no_placeholders_or_stubs_in_source() -> None:
    """No source file under agent/ contains a placeholder/stub/TODO (R22.6)."""
    offenders: list[str] = []
    for path in AGENT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if _FORBIDDEN.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"placeholder/stub markers found in: {offenders}"


# --------------------------------------------------------------------------- #
# Startup wiring (R22.7, R20.6)
# --------------------------------------------------------------------------- #


def test_build_agent_initializes_all_modules_without_exceptions(tmp_path) -> None:
    """build_agent initializes database/memory/telegram/llm/tools cleanly (R22.7)."""
    config = Config(telegram_bot_token="123:ABCDEF", owner_id=42)
    log_dir = tmp_path / "logs"
    from agent.logger import Logger

    logger = Logger(log_dir=str(log_dir))
    agent = main.build_agent(
        config, logger=logger, db_path=str(tmp_path / "agent.db")
    )
    try:
        # Database/memory.
        assert agent.database is not None
        assert agent.memory.load_all() is not None
        # LLM (disabled here -> no network client constructed).
        assert agent.llm.enabled is False
        # Telegram application built without contacting the network.
        assert agent.interface.application is not None
        # Every catalog tool has a dispatch implementation.
        from agent.llm.catalog import tool_names

        assert set(agent.dispatch) == set(tool_names())
        # The logs/ directory was created before the first write (R20.6).
        logger.info("startup smoke", category="system")
        assert log_dir.is_dir()
    finally:
        agent.close()


def test_build_agent_with_llm_enabled_does_not_make_network_calls(tmp_path) -> None:
    """A configuration with LLM features enabled still builds offline (R22.7)."""
    config = Config(
        telegram_bot_token="123:ABCDEF",
        owner_id=42,
        api_key="key",
        base_url="https://endpoint.invalid/v1",
        model="claude-opus-4.8",
    )
    from agent.logger import Logger

    logger = Logger(log_dir=str(tmp_path / "logs"))
    agent = main.build_agent(config, logger=logger, db_path=str(tmp_path / "a.db"))
    try:
        assert agent.llm.enabled is True
        assert agent.interface.application is not None
    finally:
        agent.close()


def test_main_halts_on_unsupported_python(monkeypatch) -> None:
    """main() returns a non-zero exit code on an unsupported Python runtime (R22.9)."""
    from agent import config as config_module

    def _raise(*_args, **_kwargs):
        raise config_module.UnsupportedPythonVersionError("unsupported")

    monkeypatch.setattr(main, "check_python_version", _raise)
    assert main.main([]) == 2
