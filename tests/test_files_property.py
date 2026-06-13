"""Property-based tests for the File_Manager (Properties 7-13).

Each example uses a fresh temporary directory (created inside the test body)
so Hypothesis's many generated examples remain isolated from one another.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.tools.files import FileManager

# Text safe for round-tripping through UTF-8 files: exclude surrogates and NUL.
_content = st.text(
    alphabet=st.characters(min_codepoint=1, blacklist_categories=("Cs",)),
    max_size=300,
)

# A single path segment: printable, non-empty, no separators, and free of
# characters that repr() would escape (so the path appears verbatim in errors).
_segment = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=126,
        blacklist_characters="/\\'\"",
    ),
    min_size=1,
    max_size=12,
).filter(lambda s: s not in {".", ".."} and s.strip() != "" and s == s.strip())


def _make_manager() -> FileManager:
    return FileManager()


# Feature: ai-devops-coding-agent, Property 7: File write/read round trip
# For any file path (including arbitrarily nested paths) and content, invoking
# write_file then read_file returns the written content, and all missing parent
# directories named in the path are created.
@settings(max_examples=100)
@given(segments=st.lists(_segment, min_size=1, max_size=5), content=_content)
def test_property_7_write_read_round_trip(segments, content) -> None:
    fm = _make_manager()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp).joinpath(*segments)

        write = fm.write_file(target, content)
        assert write.success is True
        # Every parent directory named in the path was created.
        assert target.is_file()
        assert target.parent.is_dir()

        read = fm.read_file(target)
        assert read.success is True
        assert read.data["content"] == content


# Feature: ai-devops-coding-agent, Property 8: Append preserves prior content
# For any existing file with content C and any appended content A, reading the
# file after append_file(A) returns exactly C followed by A.
@settings(max_examples=100)
@given(prior=_content, appended=_content)
def test_property_8_append_preserves_prior_content(prior, appended) -> None:
    fm = _make_manager()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "file.txt"
        assert fm.write_file(target, prior).success is True

        assert fm.append_file(target, appended).success is True

        read = fm.read_file(target)
        assert read.success is True
        assert read.data["content"] == prior + appended


# Feature: ai-devops-coding-agent, Property 9: Directory creation idempotence
# For any directory path, create_directory succeeds whether or not the
# directory already exists, and applying it twice yields the same result as
# applying it once (creating any missing parents).
@settings(max_examples=100)
@given(segments=st.lists(_segment, min_size=1, max_size=5))
def test_property_9_directory_creation_idempotence(segments) -> None:
    fm = _make_manager()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp).joinpath(*segments)

        first = fm.create_directory(target)
        assert first.success is True
        assert target.is_dir()
        entries_after_first = sorted(p.name for p in target.iterdir())

        second = fm.create_directory(target)
        assert second.success is True
        assert target.is_dir()
        entries_after_second = sorted(p.name for p in target.iterdir())

        # Applying twice yields the same state as applying once.
        assert entries_after_first == entries_after_second


# Feature: ai-devops-coding-agent, Property 10: Directory listing fidelity
# For any set of entries created directly within a directory, list_directory
# returns exactly the names of those entries, and returns an empty list when
# the directory contains no entries.
@settings(max_examples=100)
@given(
    file_names=st.sets(_segment, max_size=8),
    dir_names=st.sets(_segment, max_size=8),
)
def test_property_10_directory_listing_fidelity(file_names, dir_names) -> None:
    fm = _make_manager()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "listing"
        base.mkdir()

        # Ensure file and directory names do not collide.
        dir_names = {d for d in dir_names if d not in file_names}
        for name in file_names:
            (base / name).write_text("x", encoding="utf-8")
        for name in dir_names:
            (base / name).mkdir()

        result = fm.list_directory(base)
        assert result.success is True
        assert set(result.data["entries"]) == file_names | dir_names
        assert len(result.data["entries"]) == len(file_names) + len(dir_names)


# Feature: ai-devops-coding-agent, Property 11: Search soundness and completeness
# For any directory tree and search term, search_files returns exactly the set
# of file paths within the directory and its nested subdirectories whose file
# name or file contents contain the term as a case-insensitive substring.
@settings(max_examples=100)
@given(
    files=st.dictionaries(
        keys=st.lists(_segment, min_size=1, max_size=3).map(lambda parts: "/".join(parts)),
        values=_content,
        max_size=10,
    ),
    term=st.text(
        alphabet=st.characters(min_codepoint=1, blacklist_categories=("Cs",)),
        min_size=1,
        max_size=4,
    ),
)
def test_property_11_search_soundness_and_completeness(files, term) -> None:
    fm = _make_manager()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "tree"
        base.mkdir()

        created: dict[str, str] = {}
        for rel, content in files.items():
            target = base / rel
            # Skip entries whose parent collides with an existing file.
            if any(parent.exists() and parent.is_file() for parent in target.parents):
                continue
            if target.exists() and target.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created[rel] = content

        needle = term.lower()
        expected: set[str] = set()
        for rel, content in created.items():
            name = Path(rel).name
            if needle in name.lower() or needle in content.lower():
                expected.add(str((base / rel)))

        result = fm.search_files(term, base)
        assert result.success is True
        assert set(result.data["matches"]) == expected


# Feature: ai-devops-coding-agent, Property 12: Missing-path safety
# For any path that does not exist, a read_file, append_file, delete_file,
# list_directory, or search_files operation returns an error identifying the
# missing path and leaves all existing files and directories unchanged.
@settings(max_examples=100)
@given(missing=st.lists(_segment, min_size=1, max_size=4))
def test_property_12_missing_path_safety(missing) -> None:
    fm = _make_manager()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # An existing sentinel file that must remain untouched.
        sentinel = root / "sentinel.txt"
        sentinel.write_text("keep-me", encoding="utf-8")

        target = root / "absent"
        target = target.joinpath(*missing)
        assert not target.exists()

        before = sorted(p.name for p in root.iterdir())
        missing_repr = os.fspath(target)

        for result in (
            fm.read_file(target),
            fm.append_file(target, "data"),
            fm.delete_file(target),
            fm.list_directory(target),
            fm.search_files("term", target),
        ):
            assert result.success is False
            assert result.error is not None
            assert missing_repr in result.error

        # Filesystem unchanged: sentinel intact and no new entries created.
        assert sentinel.read_text(encoding="utf-8") == "keep-me"
        assert sorted(p.name for p in root.iterdir()) == before


# Feature: ai-devops-coding-agent, Property 13: Create-existing safety
# For any file or folder that already exists, an instruction to create it is
# rejected with an error indicating it already exists, and the existing file or
# folder is left unchanged.
@settings(max_examples=100)
@given(name=_segment, content=_content, extra=_content)
def test_property_13_create_existing_safety(name, content, extra) -> None:
    fm = _make_manager()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        existing_file = root / (name + "_file")
        with open(existing_file, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        existing_dir = root / (name + "_dir")
        existing_dir.mkdir()
        with open(existing_dir / "child.txt", "w", encoding="utf-8", newline="") as handle:
            handle.write(content)

        file_result = fm.create_file(existing_file, extra)
        assert file_result.success is False
        assert "already exists" in file_result.error
        # Existing file content is unchanged.
        with open(existing_file, "r", encoding="utf-8", newline="") as handle:
            assert handle.read() == content

        folder_result = fm.create_folder(existing_dir)
        assert folder_result.success is False
        assert "already exists" in folder_result.error
        # Existing folder and its child are unchanged.
        with open(existing_dir / "child.txt", "r", encoding="utf-8", newline="") as handle:
            assert handle.read() == content
