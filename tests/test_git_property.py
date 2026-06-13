"""Property-based test for the Git_Manager (Property 16)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.tools.git import GitManager

_name = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=12,
)
_content = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=50
)


# Feature: ai-devops-coding-agent, Property 16: Clone into non-empty destination is rejected
# For any destination path that already exists and is not empty, clone_repo is
# rejected with an error identifying the destination and the existing contents
# of the destination are left unchanged.
@settings(max_examples=100, deadline=None)
@given(file_name=_name, content=_content, url=st.text(min_size=1, max_size=30))
def test_property_16_clone_into_non_empty_destination_rejected(
    file_name, content, url
) -> None:
    manager = GitManager()
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "destination"
        dest.mkdir()
        existing = dest / (file_name + ".txt")
        existing.write_text(content, encoding="utf-8")
        before = sorted(p.name for p in dest.iterdir())

        result = manager.clone_repo(url, dest)

        assert result.success is False
        assert result.error is not None
        # The error identifies the destination path.
        assert os.fspath(dest) in result.error
        # The existing contents are left unchanged.
        assert sorted(p.name for p in dest.iterdir()) == before
        assert existing.read_text(encoding="utf-8") == content
