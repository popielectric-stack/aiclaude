"""Startup and component wiring for the AI DevOps & Coding Agent (Requirement 22).

This module is the single entry point. :func:`main` runs the Python-version
guard, loads the configuration, and constructs the whole component graph through
:func:`build_agent`, then runs the Telegram interface as a single long-lived
process (Requirement 2.1). Task-boundary isolation lives in the Agent_Loop: an
unhandled task error marks the task failed, logs it, and the process keeps
running (Requirement 2.4).

:func:`build_agent` performs the wiring (Logger, database, Memory_Store,
Server_Registry, Security_Manager, LLM_Client, all nine tools, Agent_Loop, and
Telegram_Interface) without contacting the network, so it can be exercised by
the startup smoke test (Requirement 22.7).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

from agent.agent_loop import AgentLoop
from agent.config import (
    Config,
    ConfigError,
    check_python_version,
    load as load_config,
)
from agent.database import Database, open_database
from agent.llm.catalog import CATALOG, TOOL_SPECS
from agent.llm.client import LLMClient
from agent.logger import Logger
from agent.memory.server_registry import ServerRegistry
from agent.memory.store import MemoryStore
from agent.security import SecurityManager
from agent.telegram.commands import CommandDispatcher
from agent.telegram.interface import TelegramInterface
from agent.tools.browser import BrowserAutomation
from agent.tools.cpanel import CPanelManager
from agent.tools.deployment import BuildSpec, DeploymentManager, DeploymentTarget
from agent.tools.docker import DockerManager
from agent.tools.files import FileManager
from agent.tools.git import GitManager
from agent.tools.pterodactyl import PterodactylManager
from agent.tools.ssh import SSHManager
from agent.tools.terminal import TerminalExecutor
from agent.models import ToolResult

# Default SQLite database path (Requirement 19.1).
DEFAULT_DB_PATH: str = os.environ.get("AGENT_DB_PATH", "agent.db")

# Environment variable that supplies the credential-encryption secret.
ENCRYPTION_SECRET_VAR: str = "ENCRYPTION_SECRET"


@dataclass
class Agent:
    """The fully-wired component graph produced by :func:`build_agent`."""

    config: Config
    logger: Logger
    database: Database
    memory: MemoryStore
    security: SecurityManager
    registry: ServerRegistry
    llm: LLMClient
    tools: dict[str, Any]
    dispatch: dict[str, Callable[..., ToolResult]]
    agent_loop: AgentLoop
    commands: CommandDispatcher
    interface: TelegramInterface

    def close(self) -> None:
        """Release resources (used by tests and graceful shutdown)."""
        try:
            self.database.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def _encryption_secret(config: Config, logger: Logger) -> str:
    """Resolve the credential-encryption secret (Requirement 21.1).

    Uses ``ENCRYPTION_SECRET`` when provided; otherwise falls back to deriving a
    key from the Telegram bot token so startup can proceed, logging a warning
    that an explicit secret is recommended for stable credential storage.
    """
    secret = os.environ.get(ENCRYPTION_SECRET_VAR)
    if secret is not None and secret.strip():
        return secret
    logger.warning(
        f"{ENCRYPTION_SECRET_VAR} is not set; deriving the credential-"
        "encryption key from the Telegram bot token. Set "
        f"{ENCRYPTION_SECRET_VAR} for stable, independent credential storage.",
        category="system",
    )
    return config.telegram_bot_token


def _build_tool_dispatch(
    components: dict[str, Any], deployment: DeploymentManager
) -> dict[str, Callable[..., ToolResult]]:
    """Build the name -> callable dispatch table for the Agent_Loop.

    Each catalog entry maps to a callable accepting the model's JSON arguments
    as keyword arguments and returning a :class:`~agent.models.ToolResult`.
    Deployment entries are wrapped to construct their dataclass arguments.
    """
    dispatch: dict[str, Callable[..., ToolResult]] = {}
    for spec in TOOL_SPECS:
        if spec.component == "deployment":
            continue
        component = components[spec.component]
        dispatch[spec.name] = getattr(component, spec.method)

    def _target(
        server_name: str,
        local_path: str,
        remote_path: str,
        deploy_command: Optional[str] = None,
        restart_command: Optional[str] = None,
        verify_url: Optional[str] = None,
    ) -> DeploymentTarget:
        return DeploymentTarget(
            server_name=server_name,
            local_path=local_path,
            remote_path=remote_path,
            deploy_command=deploy_command,
            restart_command=restart_command,
            verify_url=verify_url,
        )

    dispatch["deployment_build"] = lambda build_command, artifact_path: deployment.build(
        BuildSpec(build_command=build_command, artifact_path=artifact_path)
    )
    dispatch["deployment_upload"] = lambda **kwargs: deployment.upload(_target(**kwargs))
    dispatch["deployment_deploy"] = lambda **kwargs: deployment.deploy(_target(**kwargs))
    dispatch["deployment_restart_service"] = lambda **kwargs: deployment.restart_service(
        _target(**kwargs)
    )
    dispatch["deployment_verify"] = lambda **kwargs: deployment.verify(_target(**kwargs))
    return dispatch


def build_agent(
    config: Config,
    *,
    logger: Optional[Logger] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Agent:
    """Construct and wire every component without contacting the network (R22.7).

    Initializes the database, memory, telegram, llm, and tools modules. Returns
    an :class:`Agent` holding the wired graph. Raising is reserved for genuine
    setup failures; ordinary operation is resilient per the Error Handling
    design.
    """
    logger = logger or Logger()
    logger.info("Initializing AI DevOps & Coding Agent.", category="system")

    # Persistence & observability layer.
    database = open_database(db_path)
    memory = MemoryStore(database)
    snapshot = memory.load_all()  # Load prior records on restart (R19.6).
    logger.info(
        "Loaded persisted state: "
        f"{len(snapshot.conversations)} conversations, "
        f"{len(snapshot.tasks)} tasks, {len(snapshot.projects)} projects, "
        f"{len(snapshot.servers)} servers.",
        category="system",
    )

    # Control layer: security and the server registry.
    security = SecurityManager(
        owner_id=config.owner_id,
        secret=_encryption_secret(config, logger),
        logger=logger,
    )
    registry = ServerRegistry(database, security)
    security.set_server_name_lookup(registry.has_server)

    # Tool layer (nine components).
    files = FileManager()
    terminal = TerminalExecutor(logger=logger)
    git = GitManager()
    ssh = SSHManager(registry)
    pterodactyl = PterodactylManager(
        base_url=os.environ.get("PTERODACTYL_BASE_URL", "https://panel.example/api"),
        api_key=os.environ.get("PTERODACTYL_API_KEY"),
    )
    cpanel = CPanelManager(
        base_url=os.environ.get("CPANEL_BASE_URL", "https://cpanel.example:2083"),
        username=os.environ.get("CPANEL_USERNAME"),
        api_token=os.environ.get("CPANEL_API_TOKEN"),
    )
    docker = DockerManager()
    browser = BrowserAutomation()
    deployment = DeploymentManager(
        file_manager=files,
        ssh_manager=ssh,
        terminal_executor=terminal,
        browser_automation=browser,
    )

    components = {
        "files": files,
        "terminal": terminal,
        "git": git,
        "ssh": ssh,
        "pterodactyl": pterodactyl,
        "cpanel": cpanel,
        "docker": docker,
        "browser": browser,
    }
    tools = dict(components)
    tools["deployment"] = deployment
    dispatch = _build_tool_dispatch(components, deployment)

    # Control layer: language model and the agent loop.
    llm = LLMClient(config, logger=logger)
    agent_loop = AgentLoop(
        llm_client=llm,
        memory_store=memory,
        security_manager=security,
        logger=logger,
        tools=dispatch,
        catalog=CATALOG,
    )

    # Interface layer: command dispatcher and the Telegram interface.
    commands = CommandDispatcher(
        memory_store=memory,
        server_registry=registry,
        logger=logger,
    )
    interface = TelegramInterface(
        config=config,
        security_manager=security,
        command_dispatcher=commands,
        agent_loop=agent_loop,
        logger=logger,
    )

    # Resolve the wiring cycles between the interface and the control layer.
    llm.set_notifier(interface.notify_owner)
    agent_loop.set_confirm_handler(interface.confirm_blocking)
    commands.set_deploy_starter(interface.start_deployment)
    commands.set_in_progress_counter(interface.in_progress_count)

    # Prepare the Telegram application and handlers (no network contact).
    interface.build_application()

    logger.info("Agent initialization complete.", category="system")
    return Agent(
        config=config,
        logger=logger,
        database=database,
        memory=memory,
        security=security,
        registry=registry,
        llm=llm,
        tools=tools,
        dispatch=dispatch,
        agent_loop=agent_loop,
        commands=commands,
        interface=interface,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """Run the Python guard, load config, build the Agent, and run forever.

    Returns a process exit code: ``0`` on a clean shutdown, ``2`` on a fatal
    configuration error.
    """
    try:
        check_python_version()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    logger = Logger()
    try:
        config = load_config(logger=logger)
    except ConfigError as exc:
        # Missing TELEGRAM_BOT_TOKEN (R1.2): abort before connecting.
        print(str(exc), file=sys.stderr)
        return 2

    agent = build_agent(config, logger=logger)

    try:
        asyncio.run(_run_forever(agent))
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - signal path
        agent.logger.info("Received stop signal; shutting down.", category="system")
    finally:
        agent.close()
    return 0


async def _run_forever(agent: Agent) -> None:  # pragma: no cover - live runtime
    """Connect the Telegram interface and keep the process alive (R2.1)."""
    await agent.interface.run()
    # Block forever; the updater keeps receiving messages until a stop signal.
    await asyncio.Event().wait()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
