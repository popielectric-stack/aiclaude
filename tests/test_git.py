"""Unit tests for Git_Manager merge-conflict and timeout handling (R10.8, R10.10)."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

from agent.tools.git import GitManager


def _git(args: list[str], cwd: str) -> None:
    """Run a git command in ``cwd``, raising on failure (test setup helper)."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def test_pull_merge_conflict_lists_files_and_preserves_local_changes() -> None:
    """A pull conflict returns the conflicting files and preserves local state (R10.10)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "remote.git"
        remote.mkdir()
        _git(["init", "--bare", "-b", "main", str(remote)], cwd=str(root))

        # Author clone seeds the shared file and pushes it.
        author = root / "author"
        author.mkdir()
        _git(["clone", str(remote), str(author)], cwd=str(root))
        (author / "file.txt").write_text("base\n", encoding="utf-8")
        _git(["add", "-A"], cwd=str(author))
        _git(["commit", "-m", "base"], cwd=str(author))
        _git(["push", "origin", "main"], cwd=str(author))

        # Local clone diverges with its own committed change to the same line.
        local = root / "local"
        local.mkdir()
        _git(["clone", str(remote), str(local)], cwd=str(root))
        (local / "file.txt").write_text("local-change\n", encoding="utf-8")
        _git(["add", "-A"], cwd=str(local))
        _git(["commit", "-m", "local"], cwd=str(local))

        # Author publishes a conflicting change to the same line.
        (author / "file.txt").write_text("remote-change\n", encoding="utf-8")
        _git(["add", "-A"], cwd=str(author))
        _git(["commit", "-m", "remote"], cwd=str(author))
        _git(["push", "origin", "main"], cwd=str(author))

        manager = GitManager()
        result = manager.pull_changes("main", repo_path=str(local))

        assert result.success is False
        assert "file.txt" in result.data["conflicts"]
        assert "file.txt" in result.error
        # Local committed change is preserved (the merge was aborted).
        assert (local / "file.txt").read_text(encoding="utf-8") == "local-change\n"
        # The working tree has no lingering conflict markers / merge in progress.
        assert not (local / ".git" / "MERGE_HEAD").exists()


def _make_fake_git(directory: Path) -> str:
    """Create an executable stand-in for ``git`` that hangs (for timeout tests)."""
    fake = directory / "fake-git"
    fake.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(fake)


def test_clone_timeout_returns_timeout_error_identifying_operation() -> None:
    """A clone that exceeds the timeout returns a timeout error naming it (R10.8)."""
    with tempfile.TemporaryDirectory() as tmp:
        manager = GitManager(git_executable=_make_fake_git(Path(tmp)))
        dest = Path(tmp) / "clone-dest"  # does not exist -> passes the empty check
        result = manager.clone_repo("https://example.com/repo.git", dest, timeout=0.5)

        assert result.success is False
        assert result.data["timed_out"] is True
        assert "clone_repo" in result.error


def test_pull_timeout_returns_timeout_error_identifying_operation() -> None:
    """A pull that exceeds the timeout returns a timeout error naming it (R10.8)."""
    with tempfile.TemporaryDirectory() as tmp:
        manager = GitManager(git_executable=_make_fake_git(Path(tmp)))
        result = manager.pull_changes("main", repo_path=tmp, timeout=0.5)

        assert result.success is False
        assert result.data["timed_out"] is True
        assert "pull_changes" in result.error
