"""Integration tests for the Deployment_Manager pipeline (Requirement 17).

The four composed tools (File_Manager, SSH_Manager, Terminal_Executor,
Browser_Automation) are replaced with lightweight configurable doubles so each
stage can be exercised for both success and failure without any external
services. Covers build, upload, deploy, restart, and verify (R17.1, R17.3,
R17.5, R17.6, R17.8) plus Owner notification on failure.
"""

from __future__ import annotations

from typing import Optional

from agent.models import ToolResult
from agent.tools.deployment import (
    BuildSpec,
    DeploymentManager,
    DeploymentTarget,
)


class _FakeFiles:
    def __init__(self, *, exists: bool = True) -> None:
        self._exists = exists

    def list_directory(self, path) -> ToolResult:
        if self._exists:
            return ToolResult.ok({"path": str(path), "entries": ["app"]})
        return ToolResult.fail(f"Path not found: {path!r}.")

    def read_file(self, path) -> ToolResult:
        if self._exists:
            return ToolResult.ok({"path": str(path), "content": "x"})
        return ToolResult.fail(f"Path not found: {path!r}.")


class _FakeSSH:
    def __init__(self, *, sync_ok: bool = True, exit_code: int = 0, connect_ok: bool = True) -> None:
        self._sync_ok = sync_ok
        self._exit_code = exit_code
        self._connect_ok = connect_ok
        self.commands: list[tuple[str, Optional[float]]] = []

    def sync_project(self, server_name, local_dir, remote_dir) -> ToolResult:
        if self._sync_ok:
            return ToolResult.ok({"files_transferred": ["app"]})
        return ToolResult.fail("transfer interrupted; destination unchanged")

    def run_remote_command(self, server_name, command, *, timeout=None) -> ToolResult:
        self.commands.append((command, timeout))
        if not self._connect_ok:
            return ToolResult.fail("could not establish an SSH connection")
        return ToolResult.ok({"stdout": "done", "stderr": "", "exit_code": self._exit_code})


class _FakeTerminal:
    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok
        self.calls: list[tuple[str, float]] = []

    def run_command(self, command, timeout=300.0) -> ToolResult:
        self.calls.append((command, timeout))
        if self._ok:
            return ToolResult.ok({"stdout": "built", "stderr": "", "exit_code": 0, "timed_out": False})
        return ToolResult.fail("Command exited with non-zero status 1: boom", data={"exit_code": 1})


class _FakeBrowser:
    def __init__(self, *, status: Optional[int] = 200, ok: bool = True) -> None:
        self._status = status
        self._ok = ok

    def browser_open(self, url) -> ToolResult:
        if not self._ok:
            return ToolResult.fail(f"Failed to open URL {url!r}: timeout")
        return ToolResult.ok({"url": url, "status": self._status, "title": "OK"})


def _target(**kwargs) -> DeploymentTarget:
    base = dict(
        server_name="web",
        local_path="/build/out",
        remote_path="/srv/app",
        deploy_command="systemctl reload app",
        restart_command="systemctl restart app",
        verify_url="https://app.example.com/health",
    )
    base.update(kwargs)
    return DeploymentTarget(**base)


def _manager(*, files=None, ssh=None, terminal=None, browser=None, notifier=None) -> DeploymentManager:
    return DeploymentManager(
        file_manager=files or _FakeFiles(),
        ssh_manager=ssh or _FakeSSH(),
        terminal_executor=terminal or _FakeTerminal(),
        browser_automation=browser or _FakeBrowser(),
        notifier=notifier,
    )


def test_build_success_within_timeout() -> None:
    """A successful build returns the artifact path and uses the 600 s timeout (R17.1, R17.7)."""
    terminal = _FakeTerminal(ok=True)
    manager = _manager(terminal=terminal)
    result = manager.build(BuildSpec("make build", "/build/out"))
    assert result.success is True
    assert terminal.calls[0][1] == 600.0


def test_build_failure_notifies_owner() -> None:
    """A failed build leaves artifacts unchanged and notifies the Owner (R17.6)."""
    notices: list[str] = []
    manager = _manager(terminal=_FakeTerminal(ok=False), notifier=notices.append)
    result = manager.build(BuildSpec("make build", "/build/out"))
    assert result.success is False
    assert "left unchanged" in result.error
    assert notices and "build failed" in notices[0]


def test_upload_success_and_failure() -> None:
    """Upload succeeds via sync and fails cleanly when the transfer fails (R17.2)."""
    assert _manager(ssh=_FakeSSH(sync_ok=True)).upload(_target()).success is True

    failed = _manager(ssh=_FakeSSH(sync_ok=False)).upload(_target())
    assert failed.success is False
    assert "transfer failed" in failed.error


def test_deploy_success_and_remote_nonzero_exit() -> None:
    """Deploy succeeds on exit 0 and fails on a non-zero remote exit (R17.3, R17.8)."""
    assert _manager(ssh=_FakeSSH(exit_code=0)).deploy(_target()).success is True

    failed = _manager(ssh=_FakeSSH(exit_code=2)).deploy(_target())
    assert failed.success is False
    assert "status 2" in failed.error


def test_restart_uses_120s_timeout() -> None:
    """Restart issues the restart command with the 120 s timeout (R17.4, R17.9)."""
    ssh = _FakeSSH(exit_code=0)
    result = _manager(ssh=ssh).restart_service(_target())
    assert result.success is True
    assert ("systemctl restart app", 120.0) in ssh.commands


def test_verify_success_and_bad_status() -> None:
    """Verify succeeds on a 2xx/3xx response and fails on a 5xx (R17.5, R17.6)."""
    assert _manager(browser=_FakeBrowser(status=200)).verify(_target()).success is True

    failed = _manager(browser=_FakeBrowser(status=503)).verify(_target())
    assert failed.success is False
    assert "503" in failed.error


def test_verify_unreachable_notifies_owner() -> None:
    """An unreachable site fails verification and notifies the Owner (R17.6)."""
    notices: list[str] = []
    manager = _manager(browser=_FakeBrowser(ok=False), notifier=notices.append)
    result = manager.verify(_target())
    assert result.success is False
    assert notices and "verify failed" in notices[0]


def test_run_pipeline_happy_path() -> None:
    """The full pipeline runs every stage in order on success."""
    result = _manager().run_pipeline(BuildSpec("make", "/build/out"), _target())
    assert result.success is True
    assert result.data["completed_stages"] == ["build", "upload", "deploy", "restart", "verify"]


def test_run_pipeline_stops_at_first_failure_leaving_prior_intact() -> None:
    """A pipeline failure stops early and reports the completed stages (R17.6)."""
    manager = _manager(ssh=_FakeSSH(sync_ok=False))
    result = manager.run_pipeline(BuildSpec("make", "/build/out"), _target())
    assert result.success is False
    assert "upload" in result.error
    assert result.data["completed_stages"] == ["build"]
