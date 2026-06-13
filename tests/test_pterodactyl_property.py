"""Property test for the Pterodactyl transfer retry policy (Property 31)."""

from __future__ import annotations

from typing import Any

import requests
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.tools.pterodactyl import (
    MAX_TRANSFER_ATTEMPTS,
    RETRY_DELAY_SECONDS,
    PterodactylManager,
)


class _FakeResponse:
    """A minimal stand-in for :class:`requests.Response`."""

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, Any]:
        return {}


class _TransientSession:
    """A session that fails every attempt with a generated transient mode.

    ``timeout`` raises :class:`requests.Timeout` (simulating an attempt that
    exceeds the 30 s per-attempt cap); ``conn`` raises a connection error; an
    integer yields a 5xx response. All are transient and must be retried.
    """

    def __init__(self, behaviors: list[Any]) -> None:
        self._behaviors = behaviors
        self.attempts = 0

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        index = self.attempts
        self.attempts += 1
        behavior = self._behaviors[index % len(self._behaviors)]
        if behavior == "timeout":
            raise requests.Timeout("attempt exceeded per-attempt cap")
        if behavior == "conn":
            raise requests.ConnectionError("connection reset")
        return _FakeResponse(int(behavior))


# Feature: ai-devops-coding-agent, Property 31: Pterodactyl transfer retry policy
# For any sequence of transient transfer failures, the Pterodactyl_Manager makes
# at most 3 attempts, waits at least 2 seconds between attempts, treats any
# single attempt exceeding 30 seconds as failed, and reports a failure result
# after the attempts are exhausted.
@settings(max_examples=100)
@given(
    behaviors=st.lists(
        st.sampled_from(["timeout", "conn", 500, 502, 503, 504]),
        min_size=1,
        max_size=8,
    ),
    operation=st.sampled_from(["upload", "download"]),
)
def test_transfer_retry_policy(behaviors: list[Any], operation: str) -> None:
    sleeps: list[float] = []
    session = _TransientSession(behaviors)
    manager = PterodactylManager(
        "https://panel.example.com",
        "valid-key",
        session=session,
        sleep=sleeps.append,
    )

    if operation == "upload":
        result = manager.upload_file("srv1", "/home/container/app.cfg", "data")
    else:
        result = manager.download_file("srv1", "/home/container/app.cfg")

    # At most 3 attempts are made, and exactly 3 when every attempt is transient.
    assert session.attempts == MAX_TRANSFER_ATTEMPTS
    assert session.attempts <= MAX_TRANSFER_ATTEMPTS
    # One fewer sleep than attempts, each at least the 2-second minimum.
    assert len(sleeps) == MAX_TRANSFER_ATTEMPTS - 1
    assert all(delay >= RETRY_DELAY_SECONDS for delay in sleeps)
    # The exhausted retries are reported as a failure.
    assert result.success is False
    assert "after 3 attempts" in result.error
