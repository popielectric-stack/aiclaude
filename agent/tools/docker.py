"""The Docker_Manager tool: container and image operations (Requirement 15).

Drives the ``docker`` command-line interface through an injectable command
runner (the design permits "the ``docker`` SDK or the ``docker`` CLI via the
Terminal_Executor"). The runner abstraction keeps the manager fully testable
with a mocked Docker client and no real Docker daemon.

Operations (each returns a uniform :class:`~agent.models.ToolResult`):

* :meth:`DockerManager.list_containers` -- every container in any run state with
  its id, name, and run state, or an empty list when none exist (R15.1, R15.8).
* :meth:`DockerManager.start` / :meth:`DockerManager.stop` /
  :meth:`DockerManager.restart` -- change a container's run state and return the
  resulting state; stop/restart bound their wait to <= 10 seconds (R15.2).
* :meth:`DockerManager.build_image` -- build an image from a context directory
  and tag; a failed build creates and tags nothing (R15.3, R15.7).
* :meth:`DockerManager.deploy_container` -- run a container from a tag and return
  its container id (R15.4).

A missing container or image returns a not-found error and makes no change
(R15.6); any docker error leaves the target unchanged (R15.5).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from agent.models import ToolResult

# Maximum seconds Docker waits for a container to stop before killing it; keeps
# stop/restart within the <= 10 second bound (R15.2).
STOP_TIMEOUT_SECONDS: int = 10

# Subprocess timeout headroom (slightly above the docker stop timeout) so a hung
# CLI invocation cannot block indefinitely.
SUBPROCESS_TIMEOUT_SECONDS: float = 30.0

# The set of valid container run states (Property 29 / R15.1).
VALID_RUN_STATES: frozenset[str] = frozenset(
    {"created", "running", "restarting", "paused", "exited", "dead", "removing"}
)


@dataclass(frozen=True)
class DockerCommandResult:
    """The outcome of a single ``docker`` CLI invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# A runner takes the docker argument vector (without the leading ``docker``) and
# an optional timeout, and returns a :class:`DockerCommandResult`.
Runner = Callable[[list[str], Optional[float]], DockerCommandResult]


def _default_runner(args: list[str], timeout: Optional[float]) -> DockerCommandResult:
    """Invoke the real ``docker`` CLI with ``args``."""
    try:
        proc = subprocess.run(  # noqa: S603,S607 - docker invocation is the tool's purpose
            ["docker", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return DockerCommandResult(
            proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        return DockerCommandResult(124, "", f"docker {' '.join(args)} timed out")
    except FileNotFoundError as exc:
        return DockerCommandResult(127, "", str(exc))


class DockerManager:
    """Manages Docker containers and images via the docker CLI."""

    def __init__(self, runner: Optional[Runner] = None) -> None:
        """Create a Docker_Manager.

        Args:
            runner: a callable that executes a docker argument vector. Defaults
                to invoking the real ``docker`` CLI; tests inject a mock client.
        """
        self._run = runner or _default_runner

    # -- Listing (R15.1, R15.8) -------------------------------------------- #

    def list_containers(self) -> ToolResult:
        """Return every container with its id, name, and run state (R15.1, R15.8)."""
        result = self._run(
            ["ps", "-a", "--no-trunc", "--format", "{{json .}}"],
            SUBPROCESS_TIMEOUT_SECONDS,
        )
        if not result.ok:
            return ToolResult.fail(
                f"Failed to list containers: {result.stderr.strip() or 'docker error'}."
            )
        containers: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            containers.append(
                {
                    "id": record.get("ID", ""),
                    "name": record.get("Names", ""),
                    "state": self._normalize_state(record.get("State", "")),
                }
            )
        return ToolResult.ok({"containers": containers})

    # -- Power operations (R15.2, R15.5, R15.6) ---------------------------- #

    def start(self, container: str) -> ToolResult:
        """Start ``container`` and return its resulting run state (R15.2)."""
        return self._power(container, ["start", container])

    def stop(self, container: str) -> ToolResult:
        """Stop ``container`` (<= 10 s wait) and return its run state (R15.2)."""
        return self._power(
            container, ["stop", "-t", str(STOP_TIMEOUT_SECONDS), container]
        )

    def restart(self, container: str) -> ToolResult:
        """Restart ``container`` (<= 10 s wait) and return its run state (R15.2)."""
        return self._power(
            container, ["restart", "-t", str(STOP_TIMEOUT_SECONDS), container]
        )

    def _power(self, container: str, args: list[str]) -> ToolResult:
        """Run a power command after verifying the container exists (R15.6)."""
        if not self._container_exists(container):
            return ToolResult.fail(
                f"Container {container!r} not found; no change was made."
            )
        result = self._run(args, SUBPROCESS_TIMEOUT_SECONDS)
        if not result.ok:
            return ToolResult.fail(
                f"Docker {args[0]} of container {container!r} failed: "
                f"{result.stderr.strip() or 'docker error'}; the container was "
                "left unchanged."
            )
        state = self._inspect_state(container)
        return ToolResult.ok({"container": container, "state": state})

    # -- Build (R15.3, R15.7) ---------------------------------------------- #

    def build_image(self, context: str, tag: str) -> ToolResult:
        """Build an image from ``context`` tagged ``tag`` (R15.3, R15.7)."""
        context_path = Path(context)
        if not context_path.exists() or not context_path.is_dir():
            return ToolResult.fail(
                f"Docker build failed: build context {context!r} does not exist; "
                "no image was created or tagged."
            )
        result = self._run(
            ["build", "-t", tag, context], SUBPROCESS_TIMEOUT_SECONDS
        )
        if not result.ok:
            return ToolResult.fail(
                f"Docker build of {tag!r} failed: "
                f"{result.stderr.strip() or result.stdout.strip() or 'build error'}; "
                "no image was created or tagged.",
                data={"tag": tag, "context": context},
            )
        return ToolResult.ok({"tag": tag, "context": context})

    # -- Deploy (R15.4, R15.6) --------------------------------------------- #

    def deploy_container(self, tag: str, *, name: Optional[str] = None) -> ToolResult:
        """Run a detached container from ``tag`` and return its id (R15.4)."""
        if not self._image_exists(tag):
            return ToolResult.fail(
                f"Image {tag!r} not found; no container was deployed."
            )
        args = ["run", "-d"]
        if name:
            args += ["--name", name]
        args.append(tag)
        result = self._run(args, SUBPROCESS_TIMEOUT_SECONDS)
        if not result.ok:
            return ToolResult.fail(
                f"Docker deploy of image {tag!r} failed: "
                f"{result.stderr.strip() or 'docker error'}; no container was started."
            )
        container_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        return ToolResult.ok({"tag": tag, "container_id": container_id})

    # -- Existence checks -------------------------------------------------- #

    def _container_exists(self, container: str) -> bool:
        """Return ``True`` when ``container`` exists on the host."""
        result = self._run(
            ["inspect", "--type", "container", container], SUBPROCESS_TIMEOUT_SECONDS
        )
        return result.ok

    def _image_exists(self, tag: str) -> bool:
        """Return ``True`` when image ``tag`` exists on the host."""
        result = self._run(
            ["image", "inspect", tag], SUBPROCESS_TIMEOUT_SECONDS
        )
        return result.ok

    def _inspect_state(self, container: str) -> str:
        """Return the normalized run state of ``container``."""
        result = self._run(
            ["inspect", "-f", "{{.State.Status}}", container],
            SUBPROCESS_TIMEOUT_SECONDS,
        )
        if not result.ok:
            return "unknown"
        return self._normalize_state(result.stdout.strip())

    @staticmethod
    def _normalize_state(state: str) -> str:
        """Normalize a docker state string to one of :data:`VALID_RUN_STATES`."""
        normalized = (state or "").strip().lower()
        return normalized if normalized in VALID_RUN_STATES else "unknown"
