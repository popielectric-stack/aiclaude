"""The Git_Manager tool: Git version-control operations (Requirement 10).

Each operation shells out to the ``git`` executable and returns a uniform
:class:`~agent.models.ToolResult` carrying Git's output (R10.6, R10.7):

* :meth:`GitManager.clone_repo` -- clone a repository, rejecting a destination
  that already exists and is not empty (R10.1, R10.9).
* :meth:`GitManager.commit_changes` -- stage modified files and commit (R10.2).
* :meth:`GitManager.push_changes` -- push a branch to the remote (R10.3).
* :meth:`GitManager.pull_changes` -- pull a branch; a merge conflict returns an
  error listing the conflicting files and preserves local changes (R10.4,
  R10.10).
* :meth:`GitManager.create_branch` -- create a branch (R10.5).

``clone_repo``, ``push_changes``, and ``pull_changes`` are bounded by a 300 s
timeout; exceeding it terminates the operation and returns a timeout error
identifying the affected operation (R10.8).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from agent.models import ToolResult

# Default timeout for clone/push/pull operations in seconds (R10.8).
DEFAULT_GIT_TIMEOUT: float = 300.0

PathLike = Union[str, "os.PathLike[str]"]


@dataclass(frozen=True)
class _RunOutcome:
    """The outcome of a single ``git`` invocation."""

    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _decode(raw: Optional[bytes]) -> str:
    """Decode subprocess output to text, tolerating invalid bytes."""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


class GitManager:
    """Performs Git operations by invoking the system ``git`` executable."""

    def __init__(self, git_executable: str = "git") -> None:
        self._git = git_executable

    def _run(
        self,
        args: list[str],
        *,
        cwd: Optional[PathLike] = None,
        timeout: Optional[float] = None,
    ) -> _RunOutcome:
        """Invoke ``git`` with ``args`` and capture its output."""
        try:
            proc = subprocess.run(  # noqa: S603 - git invocation is the tool's purpose
                [self._git, *args],
                cwd=os.fspath(cwd) if cwd is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
            return _RunOutcome(proc.returncode, _decode(proc.stdout), _decode(proc.stderr), False)
        except subprocess.TimeoutExpired as exc:
            return _RunOutcome(None, _decode(exc.stdout), _decode(exc.stderr), True)
        except FileNotFoundError as exc:
            return _RunOutcome(127, "", str(exc), False)

    @staticmethod
    def _data(outcome: _RunOutcome) -> dict[str, object]:
        return {
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "exit_code": outcome.returncode,
        }

    # -- clone (R10.1, R10.8, R10.9) --------------------------------------- #

    def clone_repo(
        self, url: str, dest: PathLike, timeout: float = DEFAULT_GIT_TIMEOUT
    ) -> ToolResult:
        """Clone ``url`` into ``dest``; reject a non-empty existing dest (R10.9)."""
        dest_path = Path(dest)
        if dest_path.exists():
            is_nonempty_dir = dest_path.is_dir() and any(dest_path.iterdir())
            is_file = dest_path.is_file()
            if is_nonempty_dir or is_file:
                return ToolResult.fail(
                    f"Destination {os.fspath(dest)!r} already exists and is not "
                    "empty; clone rejected and existing contents left unchanged."
                )

        outcome = self._run(["clone", url, os.fspath(dest)], timeout=timeout)
        if outcome.timed_out:
            return self._timeout_result("clone_repo", outcome)
        if not outcome.ok:
            return ToolResult.fail(
                f"Git clone failed: {outcome.stderr.strip() or outcome.stdout.strip()}",
                data=self._data(outcome),
            )
        return ToolResult.ok(self._data(outcome))

    # -- commit (R10.2) ---------------------------------------------------- #

    def commit_changes(self, message: str, repo_path: PathLike = ".") -> ToolResult:
        """Stage all modified files and create a commit with ``message`` (R10.2)."""
        add = self._run(["add", "-A"], cwd=repo_path)
        if not add.ok:
            return ToolResult.fail(
                f"Git add failed: {add.stderr.strip() or add.stdout.strip()}",
                data=self._data(add),
            )
        commit = self._run(["commit", "-m", message], cwd=repo_path)
        if not commit.ok:
            return ToolResult.fail(
                f"Git commit failed: {commit.stderr.strip() or commit.stdout.strip()}",
                data=self._data(commit),
            )
        return ToolResult.ok(self._data(commit))

    # -- push (R10.3, R10.8) ----------------------------------------------- #

    def push_changes(
        self, branch: str, repo_path: PathLike = ".", timeout: float = DEFAULT_GIT_TIMEOUT
    ) -> ToolResult:
        """Push ``branch`` to the configured ``origin`` remote (R10.3)."""
        outcome = self._run(["push", "origin", branch], cwd=repo_path, timeout=timeout)
        if outcome.timed_out:
            return self._timeout_result("push_changes", outcome)
        if not outcome.ok:
            return ToolResult.fail(
                f"Git push failed: {outcome.stderr.strip() or outcome.stdout.strip()}",
                data=self._data(outcome),
            )
        return ToolResult.ok(self._data(outcome))

    # -- pull (R10.4, R10.8, R10.10) --------------------------------------- #

    def pull_changes(
        self, branch: str, repo_path: PathLike = ".", timeout: float = DEFAULT_GIT_TIMEOUT
    ) -> ToolResult:
        """Pull ``branch`` from ``origin``; handle merge conflicts (R10.4, R10.10)."""
        outcome = self._run(
            ["pull", "--no-rebase", "origin", branch], cwd=repo_path, timeout=timeout
        )
        if outcome.timed_out:
            return self._timeout_result("pull_changes", outcome)

        conflicts = self._unmerged_files(repo_path)
        if conflicts:
            # Abort the in-progress merge so local changes are preserved (R10.10).
            self._run(["merge", "--abort"], cwd=repo_path)
            return ToolResult.fail(
                "Git pull produced a merge conflict in: "
                + ", ".join(conflicts)
                + ". The merge was aborted and local changes were preserved.",
                data={**self._data(outcome), "conflicts": conflicts},
            )
        if not outcome.ok:
            return ToolResult.fail(
                f"Git pull failed: {outcome.stderr.strip() or outcome.stdout.strip()}",
                data=self._data(outcome),
            )
        return ToolResult.ok(self._data(outcome))

    def _unmerged_files(self, repo_path: PathLike) -> list[str]:
        """Return the paths of files with unresolved merge conflicts."""
        outcome = self._run(
            ["diff", "--name-only", "--diff-filter=U"], cwd=repo_path
        )
        if not outcome.ok:
            return []
        return [line for line in outcome.stdout.splitlines() if line.strip()]

    # -- create branch (R10.5) -------------------------------------------- #

    def create_branch(self, name: str, repo_path: PathLike = ".") -> ToolResult:
        """Create the named branch (R10.5)."""
        outcome = self._run(["branch", name], cwd=repo_path)
        if not outcome.ok:
            return ToolResult.fail(
                f"Git branch creation failed: "
                f"{outcome.stderr.strip() or outcome.stdout.strip()}",
                data=self._data(outcome),
            )
        return ToolResult.ok(self._data(outcome))

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _timeout_result(operation: str, outcome: _RunOutcome) -> ToolResult:
        """Build a timeout failure result identifying ``operation`` (R10.8)."""
        return ToolResult.fail(
            f"Git operation {operation!r} timed out and was terminated.",
            data={
                "stdout": outcome.stdout,
                "stderr": outcome.stderr,
                "exit_code": None,
                "timed_out": True,
                "operation": operation,
            },
        )
