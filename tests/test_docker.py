"""Unit/integration tests for the Docker_Manager (Requirement 15).

Use a mocked docker command runner so no real Docker daemon is required. The
focus is the build-failure path (R15.7) plus representative power/deploy
success paths (R15.2, R15.4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from agent.tools.docker import DockerCommandResult, DockerManager


def _runner_from(mapping):
    """Build a runner that dispatches on the docker verb / arg prefix."""

    def runner(args: list[str], timeout: Optional[float]) -> DockerCommandResult:
        for prefix, result in mapping:
            if args[: len(prefix)] == prefix:
                return result
        return DockerCommandResult(0, "", "")

    return runner


def test_build_failure_creates_and_tags_nothing(tmp_path: Path) -> None:
    """A failing build returns a build-failure error and no image (R15.7)."""
    context = tmp_path / "ctx"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\nRUN false\n", encoding="utf-8")

    runner = _runner_from(
        [(["build"], DockerCommandResult(1, "", "Step 2/2 : RUN false\nreturned a non-zero code: 1"))]
    )
    manager = DockerManager(runner=runner)
    result = manager.build_image(str(context), "myapp:latest")

    assert result.success is False
    assert "no image was created or tagged" in result.error


def test_build_rejects_missing_context(tmp_path: Path) -> None:
    """A non-existent build context is rejected before invoking docker (R15.7)."""
    manager = DockerManager(runner=_runner_from([]))
    result = manager.build_image(str(tmp_path / "absent"), "myapp:latest")
    assert result.success is False
    assert "does not exist" in result.error


def test_build_success(tmp_path: Path) -> None:
    """A successful build returns the tag (R15.3)."""
    context = tmp_path / "ctx"
    context.mkdir()
    runner = _runner_from([(["build"], DockerCommandResult(0, "Successfully tagged myapp:latest", ""))])
    manager = DockerManager(runner=runner)
    result = manager.build_image(str(context), "myapp:latest")
    assert result.success is True
    assert result.data["tag"] == "myapp:latest"


def test_stop_returns_resulting_state() -> None:
    """Stopping an existing container returns its resulting run state (R15.2)."""
    runner = _runner_from(
        [
            (["inspect", "--type", "container"], DockerCommandResult(0, "ok", "")),
            (["stop"], DockerCommandResult(0, "web", "")),
            (["inspect", "-f"], DockerCommandResult(0, "exited\n", "")),
        ]
    )
    manager = DockerManager(runner=runner)
    result = manager.stop("web")
    assert result.success is True
    assert result.data["state"] == "exited"


def test_deploy_container_returns_id() -> None:
    """Deploying from an existing image returns the new container id (R15.4)."""
    runner = _runner_from(
        [
            (["image", "inspect"], DockerCommandResult(0, "ok", "")),
            (["run"], DockerCommandResult(0, "c0ffee1234\n", "")),
        ]
    )
    manager = DockerManager(runner=runner)
    result = manager.deploy_container("myapp:latest")
    assert result.success is True
    assert result.data["container_id"] == "c0ffee1234"
