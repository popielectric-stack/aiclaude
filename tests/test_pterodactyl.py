"""Integration tests for the Pterodactyl_Manager against mocked HTTP endpoints.

Covers success, authentication-failure, and invalid-reference paths using
``requests_mock`` (R13.2, R13.5, R13.9, R13.10), plus the missing-API-key abort
(R13.9) and the retry-then-recover behaviour for transient transfer errors
(R13.8).
"""

from __future__ import annotations

import requests

from agent.tools.pterodactyl import PterodactylManager

BASE = "https://panel.example.com"


def _manager(session: requests.Session, **kwargs) -> PterodactylManager:
    # ``sleep`` is a no-op so retry tests do not actually wait.
    return PterodactylManager(BASE, "valid-key", session=session, sleep=lambda _s: None, **kwargs)


def test_list_servers_success(requests_mock) -> None:
    """list_servers returns identifier + name for each available server (R13.2)."""
    session = requests.Session()
    requests_mock.get(
        f"{BASE}/api/client",
        json={
            "data": [
                {"attributes": {"identifier": "abc123", "name": "Survival"}},
                {"attributes": {"identifier": "def456", "name": "Creative"}},
            ]
        },
    )
    result = _manager(session).list_servers()
    assert result.success is True
    assert result.data["servers"] == [
        {"identifier": "abc123", "name": "Survival"},
        {"identifier": "def456", "name": "Creative"},
    ]


def test_list_servers_empty(requests_mock) -> None:
    """An account with no servers yields an empty list (R13.2)."""
    session = requests.Session()
    requests_mock.get(f"{BASE}/api/client", json={"data": []})
    result = _manager(session).list_servers()
    assert result.success is True
    assert result.data["servers"] == []


def test_power_signal_success(requests_mock) -> None:
    """A power signal is sent through the panel API (R13.5)."""
    session = requests.Session()
    requests_mock.post(f"{BASE}/api/client/servers/abc123/power", status_code=204)
    result = _manager(session).power("abc123", "restart")
    assert result.success is True
    assert result.data == {"server": "abc123", "signal": "restart"}
    assert requests_mock.last_request.json() == {"signal": "restart"}


def test_power_invalid_signal_rejected() -> None:
    """An unknown power signal is rejected before any request (R13.5)."""
    result = _manager(requests.Session()).power("abc123", "explode")
    assert result.success is False
    assert "Invalid power signal" in result.error


def test_authentication_failure_aborts(requests_mock) -> None:
    """A rejected API key aborts without modifying state (R13.9)."""
    session = requests.Session()
    requests_mock.post(f"{BASE}/api/client/servers/abc123/power", status_code=403)
    result = _manager(session).power("abc123", "stop")
    assert result.success is False
    assert "authentication failed" in result.error.lower()


def test_missing_api_key_aborts_without_request() -> None:
    """A missing API key aborts every operation without a network call (R13.9)."""
    manager = PterodactylManager(BASE, None, session=requests.Session())
    result = manager.list_servers()
    assert result.success is False
    assert "no API key" in result.error


def test_invalid_reference_rejected(requests_mock) -> None:
    """A 404 server/path reference is rejected without state change (R13.10)."""
    session = requests.Session()
    requests_mock.get(
        f"{BASE}/api/client/servers/ghost/files/contents", status_code=404
    )
    result = _manager(session).download_file("ghost", "/home/container/app.cfg")
    assert result.success is False
    assert "invalid reference" in result.error.lower()


def test_upload_retries_then_succeeds(requests_mock) -> None:
    """A transient 500 is retried and a subsequent success completes (R13.8)."""
    session = requests.Session()
    requests_mock.post(
        f"{BASE}/api/client/servers/abc123/files/write",
        [{"status_code": 500}, {"status_code": 204}],
    )
    result = _manager(session).upload_file("abc123", "/home/container/x", "data")
    assert result.success is True
    assert result.data["transferred"] is True
    assert requests_mock.call_count == 2


def test_download_returns_contents(requests_mock) -> None:
    """download_file returns the raw file contents on success (R13.4)."""
    session = requests.Session()
    requests_mock.get(
        f"{BASE}/api/client/servers/abc123/files/contents",
        text="server-config-body",
    )
    result = _manager(session).download_file("abc123", "/home/container/app.cfg")
    assert result.success is True
    assert result.data["content"] == "server-config-body"


def test_console_output_capped_to_100_lines(requests_mock) -> None:
    """console_output returns at most the last 100 lines (R13.6)."""
    session = requests.Session()
    lines = [f"line-{i}" for i in range(250)]
    requests_mock.get(
        f"{BASE}/api/client/servers/abc123/console",
        json={"data": {"lines": lines}},
    )
    result = _manager(session).console_output("abc123")
    assert result.success is True
    assert len(result.data["lines"]) == 100
    assert result.data["lines"][0] == "line-150"
    assert result.data["lines"][-1] == "line-249"
