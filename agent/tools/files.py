"""The File_Manager tool: file and directory operations (Requirement 8, 18).

Every operation returns a uniform :class:`~agent.models.ToolResult` (R8.9). The
operations are:

* :meth:`FileManager.read_file` -- return a file's contents (R8.1).
* :meth:`FileManager.write_file` -- create missing parent directories and
  create/overwrite the file (R8.2).
* :meth:`FileManager.append_file` -- append to an existing file, preserving its
  prior content (R8.3).
* :meth:`FileManager.delete_file` -- remove a file (R8.4).
* :meth:`FileManager.create_directory` -- idempotently create a directory and
  any missing parents (R8.5).
* :meth:`FileManager.list_directory` -- list the direct entries of a directory,
  returning an empty list when it has none (R8.6).
* :meth:`FileManager.search_files` -- recursively find files whose name or
  contents contain a term as a case-insensitive substring (R8.7).
* :meth:`FileManager.create_file` / :meth:`FileManager.create_folder` --
  create a new file/folder, rejecting the request when the target already
  exists (R18.2, R18.10).

Operations on a missing path return an error identifying the path and leave the
filesystem unchanged (R8.8, R18.9).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from agent.models import ToolResult

# Type alias for the accepted path argument.
PathLike = Union[str, "os.PathLike[str]"]


class FileManager:
    """Performs file and directory operations on the local VPS filesystem."""

    # -- Reads ------------------------------------------------------------- #

    def read_file(self, path: PathLike) -> ToolResult:
        """Return the contents of the file at ``path`` (R8.1, R8.8)."""
        target = Path(path)
        if not target.exists():
            return ToolResult.fail(f"Path not found: {os.fspath(path)!r}.")
        if not target.is_file():
            return ToolResult.fail(f"Path is not a file: {os.fspath(path)!r}.")
        try:
            with open(target, "r", encoding="utf-8", errors="replace", newline="") as handle:
                content = handle.read()
        except OSError as exc:
            return ToolResult.fail(f"Failed to read {os.fspath(path)!r}: {exc}")
        return ToolResult.ok({"path": str(target), "content": content})

    def list_directory(self, path: PathLike) -> ToolResult:
        """List the direct entries of the directory at ``path`` (R8.6, R8.8)."""
        target = Path(path)
        if not target.exists():
            return ToolResult.fail(f"Path not found: {os.fspath(path)!r}.")
        if not target.is_dir():
            return ToolResult.fail(f"Path is not a directory: {os.fspath(path)!r}.")
        try:
            entries = sorted(entry.name for entry in target.iterdir())
        except OSError as exc:
            return ToolResult.fail(f"Failed to list {os.fspath(path)!r}: {exc}")
        return ToolResult.ok({"path": str(target), "entries": entries})

    def search_files(self, term: str, directory: PathLike) -> ToolResult:
        """Return files under ``directory`` matching ``term`` (R8.7, R8.8).

        A file matches when its name or its contents contain ``term`` as a
        case-insensitive substring. The search is recursive over all nested
        subdirectories.
        """
        base = Path(directory)
        if not base.exists():
            return ToolResult.fail(f"Path not found: {os.fspath(directory)!r}.")
        if not base.is_dir():
            return ToolResult.fail(
                f"Path is not a directory: {os.fspath(directory)!r}."
            )

        needle = term.lower()
        matches: list[str] = []
        for root, _dirs, files in os.walk(base):
            for file_name in files:
                file_path = Path(root) / file_name
                if needle in file_name.lower():
                    matches.append(str(file_path))
                    continue
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore", newline="") as handle:
                        contents = handle.read()
                except OSError:
                    contents = ""
                if needle in contents.lower():
                    matches.append(str(file_path))
        return ToolResult.ok(
            {"term": term, "directory": str(base), "matches": sorted(matches)}
        )

    # -- Writes ------------------------------------------------------------ #

    def write_file(self, path: PathLike, content: str) -> ToolResult:
        """Create missing parents and create/overwrite the file (R8.2)."""
        target = Path(path)
        try:
            if target.parent and not target.parent.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
        except OSError as exc:
            return ToolResult.fail(f"Failed to write {os.fspath(path)!r}: {exc}")
        return ToolResult.ok({"path": str(target), "bytes_written": len(content)})

    def append_file(self, path: PathLike, content: str) -> ToolResult:
        """Append ``content`` to an existing file, preserving prior content (R8.3)."""
        target = Path(path)
        if not target.exists():
            return ToolResult.fail(f"Path not found: {os.fspath(path)!r}.")
        if not target.is_file():
            return ToolResult.fail(f"Path is not a file: {os.fspath(path)!r}.")
        try:
            with open(target, "a", encoding="utf-8", newline="") as handle:
                handle.write(content)
        except OSError as exc:
            return ToolResult.fail(f"Failed to append to {os.fspath(path)!r}: {exc}")
        return ToolResult.ok({"path": str(target), "appended": len(content)})

    def delete_file(self, path: PathLike) -> ToolResult:
        """Remove the file at ``path`` (R8.4, R8.8)."""
        target = Path(path)
        if not target.exists():
            return ToolResult.fail(f"Path not found: {os.fspath(path)!r}.")
        if not target.is_file():
            return ToolResult.fail(f"Path is not a file: {os.fspath(path)!r}.")
        try:
            target.unlink()
        except OSError as exc:
            return ToolResult.fail(f"Failed to delete {os.fspath(path)!r}: {exc}")
        return ToolResult.ok({"path": str(target)})

    # -- Directory creation ------------------------------------------------ #

    def create_directory(self, path: PathLike) -> ToolResult:
        """Idempotently create a directory and any missing parents (R8.5).

        Succeeds whether or not the directory already exists. Fails only when a
        non-directory already occupies the path.
        """
        target = Path(path)
        if target.exists() and not target.is_dir():
            return ToolResult.fail(
                f"Cannot create directory: a non-directory exists at "
                f"{os.fspath(path)!r}."
            )
        already_existed = target.is_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult.fail(
                f"Failed to create directory {os.fspath(path)!r}: {exc}"
            )
        return ToolResult.ok({"path": str(target), "created": not already_existed})

    # -- Create-new (existence-checked) operations (R18.2, R18.10) --------- #

    def create_file(self, path: PathLike, content: str = "") -> ToolResult:
        """Create a new file, rejecting the request if it already exists (R18.10)."""
        target = Path(path)
        if target.exists():
            return ToolResult.fail(
                f"Cannot create file: {os.fspath(path)!r} already exists."
            )
        try:
            if target.parent and not target.parent.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
        except OSError as exc:
            return ToolResult.fail(f"Failed to create {os.fspath(path)!r}: {exc}")
        return ToolResult.ok({"path": str(target)})

    def create_folder(self, path: PathLike) -> ToolResult:
        """Create a new folder, rejecting the request if it already exists (R18.10)."""
        target = Path(path)
        if target.exists():
            return ToolResult.fail(
                f"Cannot create folder: {os.fspath(path)!r} already exists."
            )
        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            return ToolResult.fail(
                f"Failed to create folder {os.fspath(path)!r}: {exc}"
            )
        return ToolResult.ok({"path": str(target)})
