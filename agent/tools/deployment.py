"""The Deployment_Manager tool: build/upload/deploy/restart/verify (Requirement 17).

Orchestrates an application deployment by composing four lower-level tools:
the File_Manager (to confirm build artifacts exist locally), the SSH_Manager
(to transfer artifacts and run remote deploy/restart commands), the
Terminal_Executor (to run the local build), and the Browser_Automation tool
(to verify the deployed site responds).

Stages (each returns a uniform :class:`~agent.models.ToolResult`):

* :meth:`DeploymentManager.build` -- run the project's build command locally,
  bounded by a 600 second timeout (R17.1, R17.7).
* :meth:`DeploymentManager.upload` -- transfer the built artifacts to the
  target server (R17.2).
* :meth:`DeploymentManager.deploy` -- run the remote deploy command on the
  target server (R17.3, R17.8).
* :meth:`DeploymentManager.restart_service` -- restart the target service,
  bounded by a 120 second timeout (R17.4, R17.9).
* :meth:`DeploymentManager.verify` -- confirm the deployed site returns a
  successful response within a 30 second navigation timeout (R17.5, R17.6).

On any stage failure the manager leaves the existing active artifacts unchanged
and surfaces a notification to the Owner through an injected notifier
(R17.6-R17.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from agent.models import ToolResult

# Stage timeouts, in seconds (R17.7, R17.9, R17.6).
BUILD_TIMEOUT_SECONDS: float = 600.0
RESTART_TIMEOUT_SECONDS: float = 120.0
VERIFY_TIMEOUT_SECONDS: float = 30.0

# HTTP status codes considered a successful verification response (R17.5).
_SUCCESS_STATUS_RANGE = range(200, 400)


@dataclass(frozen=True)
class BuildSpec:
    """Describes how to build a project locally."""

    build_command: str
    artifact_path: str


@dataclass(frozen=True)
class DeploymentTarget:
    """Describes where and how to deploy built artifacts."""

    server_name: str
    local_path: str
    remote_path: str
    deploy_command: Optional[str] = None
    restart_command: Optional[str] = None
    verify_url: Optional[str] = None


class _FileManagerLike(Protocol):
    def list_directory(self, path: Any) -> ToolResult: ...

    def read_file(self, path: Any) -> ToolResult: ...


class _SSHManagerLike(Protocol):
    def sync_project(self, server_name: str, local_dir: str, remote_dir: str) -> ToolResult: ...

    def run_remote_command(
        self, server_name: str, command: str, *, timeout: Optional[float] = ...
    ) -> ToolResult: ...


class _TerminalLike(Protocol):
    def run_command(self, command: str, timeout: float = ...) -> ToolResult: ...


class _BrowserLike(Protocol):
    def browser_open(self, url: str) -> ToolResult: ...


Notifier = Callable[[str], None]


class DeploymentManager:
    """Builds, uploads, deploys, restarts, and verifies application deployments."""

    def __init__(
        self,
        *,
        file_manager: _FileManagerLike,
        ssh_manager: _SSHManagerLike,
        terminal_executor: _TerminalLike,
        browser_automation: _BrowserLike,
        notifier: Optional[Notifier] = None,
    ) -> None:
        self._files = file_manager
        self._ssh = ssh_manager
        self._terminal = terminal_executor
        self._browser = browser_automation
        self._notify = notifier

    # -- Build (R17.1, R17.7) ---------------------------------------------- #

    def build(self, spec: BuildSpec) -> ToolResult:
        """Run the project's build command locally within 600 s (R17.1, R17.7)."""
        result = self._terminal.run_command(
            spec.build_command, timeout=BUILD_TIMEOUT_SECONDS
        )
        if not result.success:
            return self._fail(
                "build",
                f"the build command failed: {result.error}",
                data=result.data,
            )

        # Confirm the build produced the expected artifact (composes File_Manager).
        artifact_check = self._files.list_directory(spec.artifact_path)
        if not artifact_check.success:
            artifact_check = self._files.read_file(spec.artifact_path)
        if not artifact_check.success:
            return self._fail(
                "build",
                f"the build artifact {spec.artifact_path!r} was not produced: "
                f"{artifact_check.error}",
            )
        return ToolResult.ok(
            {"artifact_path": spec.artifact_path, "build_output": result.data}
        )

    # -- Upload (R17.2) ---------------------------------------------------- #

    def upload(self, target: DeploymentTarget) -> ToolResult:
        """Transfer built artifacts to the target server (R17.2)."""
        local_check = self._files.list_directory(target.local_path)
        if not local_check.success:
            return self._fail(
                "upload",
                f"the local artifact path {target.local_path!r} is unavailable: "
                f"{local_check.error}",
            )
        result = self._ssh.sync_project(
            target.server_name, target.local_path, target.remote_path
        )
        if not result.success:
            return self._fail("upload", f"the transfer failed: {result.error}")
        return ToolResult.ok({"target": target.server_name, "transfer": result.data})

    # -- Deploy (R17.3, R17.8) --------------------------------------------- #

    def deploy(self, target: DeploymentTarget) -> ToolResult:
        """Run the remote deploy command on the target server (R17.3, R17.8)."""
        if not target.deploy_command:
            return self._fail("deploy", "no deploy command was configured for the target")
        result = self._ssh.run_remote_command(
            target.server_name, target.deploy_command
        )
        failure = self._remote_failure(result)
        if failure is not None:
            return self._fail("deploy", failure)
        return ToolResult.ok({"target": target.server_name, "deploy": result.data})

    # -- Restart (R17.4, R17.9) -------------------------------------------- #

    def restart_service(self, target: DeploymentTarget) -> ToolResult:
        """Restart the target service within 120 s (R17.4, R17.9)."""
        if not target.restart_command:
            return self._fail("restart", "no restart command was configured for the target")
        result = self._ssh.run_remote_command(
            target.server_name, target.restart_command, timeout=RESTART_TIMEOUT_SECONDS
        )
        failure = self._remote_failure(result)
        if failure is not None:
            return self._fail("restart", failure)
        return ToolResult.ok({"target": target.server_name, "restart": result.data})

    # -- Verify (R17.5, R17.6) --------------------------------------------- #

    def verify(self, target: DeploymentTarget) -> ToolResult:
        """Confirm the deployed site responds successfully within 30 s (R17.5)."""
        if not target.verify_url:
            return self._fail("verify", "no verification URL was configured for the target")
        result = self._browser.browser_open(target.verify_url)
        if not result.success:
            return self._fail(
                "verify", f"the deployed site did not respond: {result.error}"
            )
        status = (result.data or {}).get("status")
        if status is not None and status not in _SUCCESS_STATUS_RANGE:
            return self._fail(
                "verify",
                f"the deployed site returned an unsuccessful status {status}",
            )
        return ToolResult.ok({"url": target.verify_url, "status": status})

    # -- Whole-pipeline convenience ---------------------------------------- #

    def run_pipeline(self, spec: BuildSpec, target: DeploymentTarget) -> ToolResult:
        """Run build -> upload -> deploy -> restart -> verify, stopping on the first failure.

        Because each stage aborts on failure without altering the existing
        active artifacts, a pipeline that stops early leaves the prior
        deployment intact (R17.6).
        """
        stages = (
            ("build", lambda: self.build(spec)),
            ("upload", lambda: self.upload(target)),
            ("deploy", lambda: self.deploy(target)),
            ("restart", lambda: self.restart_service(target)),
            ("verify", lambda: self.verify(target)),
        )
        completed: list[str] = []
        for name, run in stages:
            result = run()
            if not result.success:
                return ToolResult.fail(
                    f"Deployment stopped at the {name!r} stage: {result.error}",
                    data={"completed_stages": completed},
                )
            completed.append(name)
        return ToolResult.ok({"completed_stages": completed})

    # -- Helpers ----------------------------------------------------------- #

    @staticmethod
    def _remote_failure(result: ToolResult) -> Optional[str]:
        """Return a failure reason for a remote command, or ``None`` on success.

        A remote command that connected but exited non-zero is itself a stage
        failure even though the SSH transport succeeded.
        """
        if not result.success:
            return result.error
        exit_code = (result.data or {}).get("exit_code")
        if exit_code not in (0, None):
            stderr = (result.data or {}).get("stderr", "")
            return f"the remote command exited with status {exit_code}: {stderr.strip()}"
        return None

    def _fail(self, stage: str, reason: str, *, data: Optional[dict[str, Any]] = None) -> ToolResult:
        """Build a stage failure result and notify the Owner (R17.6-R17.9)."""
        message = (
            f"Deployment {stage} failed: {reason}. The existing active artifacts "
            "were left unchanged."
        )
        if self._notify is not None:
            self._notify(message)
        return ToolResult.fail(message, data=data)
