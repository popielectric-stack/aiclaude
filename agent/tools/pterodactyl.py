"""The Pterodactyl_Manager tool: Pterodactyl panel operations (Requirement 13).

Drives the Pterodactyl *client* API with ``requests``, authenticated by a
stored API key (R13.1). Operations:

* :meth:`PterodactylManager.list_servers` -- identifier + name of each server
  available to the API key, or an empty list when none (R13.2).
* :meth:`PterodactylManager.upload_file` -- write a file to a server path
  (R13.3).
* :meth:`PterodactylManager.download_file` -- read a file's contents from a
  server path (R13.4).
* :meth:`PterodactylManager.power` -- send a ``start`` / ``stop`` / ``restart``
  power signal (R13.5).
* :meth:`PterodactylManager.console_output` -- the most recent console output,
  capped at the last 100 lines (R13.6).
* :meth:`PterodactylManager.send_console_command` -- send a console command
  (R13.7).

Transfer reliability (R13.8): file uploads and downloads retry up to
:data:`MAX_TRANSFER_ATTEMPTS` attempts, waiting at least
:data:`RETRY_DELAY_SECONDS` seconds between attempts, treating any single
attempt that exceeds :data:`ATTEMPT_TIMEOUT_SECONDS` seconds as failed, before
reporting a failure result.

A missing or rejected API key aborts the operation without modifying any server
state and reports an authentication failure (R13.9). A reference to a server
identifier or file path that does not exist (or is not available to the API
key) is rejected without modifying state (R13.10). Authentication and
invalid-reference errors are never retried.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

import requests

from agent.models import ToolResult

# Maximum number of transfer attempts before reporting failure (R13.8).
MAX_TRANSFER_ATTEMPTS: int = 3

# Minimum delay, in seconds, between successive transfer attempts (R13.8).
RETRY_DELAY_SECONDS: float = 2.0

# Per-attempt timeout, in seconds; an attempt exceeding it is treated as failed
# (R13.8).
ATTEMPT_TIMEOUT_SECONDS: float = 30.0

# Maximum number of console output lines returned (R13.6).
MAX_CONSOLE_LINES: int = 100

# Default per-request timeout for non-transfer operations, in seconds.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# Valid power signals (R13.5).
_VALID_POWER_SIGNALS: frozenset[str] = frozenset({"start", "stop", "restart", "kill"})


class _SessionLike(Protocol):
    """The subset of :class:`requests.Session` the manager depends on."""

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response: ...


@dataclass(frozen=True)
class _Attempt:
    """The outcome of a single HTTP attempt classified for the retry policy."""

    response: Optional[requests.Response]
    transient_error: Optional[str]


class PterodactylManager:
    """Controls servers through the Pterodactyl panel client API."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str],
        *,
        session: Optional[_SessionLike] = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = MAX_TRANSFER_ATTEMPTS,
        retry_delay: float = RETRY_DELAY_SECONDS,
        attempt_timeout: float = ATTEMPT_TIMEOUT_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a Pterodactyl_Manager.

        Args:
            base_url: panel base URL (e.g. ``https://panel.example.com``).
            api_key: the stored client API key; ``None``/empty disables all
                operations with an authentication failure (R13.9).
            session: a ``requests``-compatible session (injected in tests).
            sleep: the delay function used between retries; injected so the
                retry policy can be tested with a controllable clock (R13.8).
            max_attempts / retry_delay / attempt_timeout: the retry policy
                parameters (R13.8).
            timeout: per-request timeout for non-transfer operations.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip() if isinstance(api_key, str) else api_key
        self._session = session if session is not None else requests.Session()
        self._sleep = sleep
        self._max_attempts = max(1, int(max_attempts))
        self._retry_delay = retry_delay
        self._attempt_timeout = attempt_timeout
        self._timeout = timeout

    # -- Public operations ------------------------------------------------- #

    def list_servers(self) -> ToolResult:
        """Return ``[{identifier, name}, ...]`` for available servers (R13.2)."""
        auth_error = self._require_api_key()
        if auth_error is not None:
            return auth_error
        try:
            response = self._session.request(
                "GET",
                f"{self._base_url}/api/client",
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            return ToolResult.fail(f"Pterodactyl server list request failed: {exc}")

        classified = self._classify(response, context="server list")
        if classified is not None:
            return classified

        payload = self._json(response)
        servers: list[dict[str, Any]] = []
        for item in payload.get("data", []) or []:
            attributes = item.get("attributes", {}) if isinstance(item, dict) else {}
            servers.append(
                {
                    "identifier": attributes.get("identifier"),
                    "name": attributes.get("name"),
                }
            )
        return ToolResult.ok({"servers": servers})

    def power(self, server_id: str, signal: str) -> ToolResult:
        """Send a power ``signal`` to ``server_id`` (R13.5)."""
        auth_error = self._require_api_key()
        if auth_error is not None:
            return auth_error
        normalized = (signal or "").strip().lower()
        if normalized not in _VALID_POWER_SIGNALS:
            return ToolResult.fail(
                f"Invalid power signal {signal!r}: expected one of "
                f"{sorted(_VALID_POWER_SIGNALS)}."
            )
        try:
            response = self._session.request(
                "POST",
                f"{self._base_url}/api/client/servers/{server_id}/power",
                headers=self._headers(),
                json={"signal": normalized},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            return ToolResult.fail(
                f"Pterodactyl power signal request failed: {exc}"
            )
        classified = self._classify(response, context=f"server {server_id!r}")
        if classified is not None:
            return classified
        return ToolResult.ok({"server": server_id, "signal": normalized})

    def send_console_command(self, server_id: str, command: str) -> ToolResult:
        """Send ``command`` to the server console (R13.7)."""
        auth_error = self._require_api_key()
        if auth_error is not None:
            return auth_error
        try:
            response = self._session.request(
                "POST",
                f"{self._base_url}/api/client/servers/{server_id}/command",
                headers=self._headers(),
                json={"command": command},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            return ToolResult.fail(f"Pterodactyl console command request failed: {exc}")
        classified = self._classify(response, context=f"server {server_id!r}")
        if classified is not None:
            return classified
        return ToolResult.ok({"server": server_id, "command": command})

    def console_output(self, server_id: str) -> ToolResult:
        """Return the most recent console output, last 100 lines max (R13.6)."""
        auth_error = self._require_api_key()
        if auth_error is not None:
            return auth_error
        try:
            response = self._session.request(
                "GET",
                f"{self._base_url}/api/client/servers/{server_id}/console",
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            return ToolResult.fail(f"Pterodactyl console output request failed: {exc}")
        classified = self._classify(response, context=f"server {server_id!r}")
        if classified is not None:
            return classified
        payload = self._json(response)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        lines = data.get("lines") if isinstance(data, dict) else None
        if lines is None:
            lines = []
        capped = list(lines)[-MAX_CONSOLE_LINES:]
        return ToolResult.ok({"server": server_id, "lines": capped})

    def upload_file(self, server_id: str, remote_path: str, content: str) -> ToolResult:
        """Write ``content`` to ``remote_path`` on ``server_id`` (R13.3, R13.8)."""
        auth_error = self._require_api_key()
        if auth_error is not None:
            return auth_error

        def attempt() -> requests.Response:
            return self._session.request(
                "POST",
                f"{self._base_url}/api/client/servers/{server_id}/files/write",
                headers={**self._headers(), "Content-Type": "text/plain"},
                params={"file": remote_path},
                data=content.encode("utf-8"),
                timeout=self._attempt_timeout,
            )

        return self._transfer_with_retry(
            attempt,
            context=f"upload to server {server_id!r} path {remote_path!r}",
            on_success=lambda _resp: ToolResult.ok(
                {"server": server_id, "path": remote_path, "transferred": True}
            ),
        )

    def download_file(self, server_id: str, remote_path: str) -> ToolResult:
        """Read the contents of ``remote_path`` on ``server_id`` (R13.4, R13.8)."""
        auth_error = self._require_api_key()
        if auth_error is not None:
            return auth_error

        def attempt() -> requests.Response:
            return self._session.request(
                "GET",
                f"{self._base_url}/api/client/servers/{server_id}/files/contents",
                headers=self._headers(),
                params={"file": remote_path},
                timeout=self._attempt_timeout,
            )

        return self._transfer_with_retry(
            attempt,
            context=f"download from server {server_id!r} path {remote_path!r}",
            on_success=lambda resp: ToolResult.ok(
                {"server": server_id, "path": remote_path, "content": resp.text}
            ),
        )

    # -- Retry policy (R13.8) ---------------------------------------------- #

    def _transfer_with_retry(
        self,
        attempt: Callable[[], requests.Response],
        *,
        context: str,
        on_success: Callable[[requests.Response], ToolResult],
    ) -> ToolResult:
        """Execute ``attempt`` under the retry policy (R13.8).

        At most :data:`max_attempts` attempts are made, waiting at least
        ``retry_delay`` seconds between attempts. A timeout (an attempt
        exceeding ``attempt_timeout``), a connection error, or a 5xx response is
        treated as transient and retried; an authentication failure (401/403) or
        an invalid reference (404) aborts immediately without retrying (R13.9,
        R13.10).
        """
        last_error = "no attempt was made"
        for index in range(self._max_attempts):
            if index > 0:
                self._sleep(self._retry_delay)
            try:
                response = self._session_attempt(attempt)
            except requests.Timeout:
                last_error = (
                    f"attempt exceeded the {self._attempt_timeout:g}s timeout"
                )
                continue
            except requests.RequestException as exc:
                last_error = f"transport error: {exc}"
                continue

            terminal = self._classify(response, context=context)
            if terminal is not None:
                # Auth (401/403) and invalid-reference (404) are terminal and
                # are returned immediately without consuming further attempts.
                if response.status_code in (401, 403, 404):
                    return terminal
                last_error = terminal.error or "server error"
                continue
            return on_success(response)

        return ToolResult.fail(
            f"Pterodactyl {context} failed after {self._max_attempts} attempts: "
            f"{last_error}."
        )

    @staticmethod
    def _session_attempt(attempt: Callable[[], requests.Response]) -> requests.Response:
        """Invoke a single transfer attempt (separated for clarity/testing)."""
        return attempt()

    # -- Helpers ----------------------------------------------------------- #

    def _require_api_key(self) -> Optional[ToolResult]:
        """Return an auth-failure result when the API key is missing (R13.9)."""
        if not self._api_key:
            return ToolResult.fail(
                "Pterodactyl authentication failed: no API key is configured; "
                "the operation was aborted without modifying any server state."
            )
        return None

    def _headers(self) -> dict[str, str]:
        """Build the authenticated request headers (R13.1)."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

    def _classify(
        self, response: requests.Response, *, context: str
    ) -> Optional[ToolResult]:
        """Map an error status code to a terminal failure, else ``None``.

        * 401/403 -> authentication failure (R13.9).
        * 404 -> invalid reference (R13.10).
        * other >= 400 -> generic API error.
        A success status returns ``None`` so the caller proceeds.
        """
        status = response.status_code
        if status in (401, 403):
            return ToolResult.fail(
                f"Pterodactyl authentication failed for {context}: the API key "
                "was rejected; the operation was aborted without modifying any "
                "server state."
            )
        if status == 404:
            return ToolResult.fail(
                f"Pterodactyl invalid reference for {context}: the server "
                "identifier or file path does not exist or is not available to "
                "the API key; no server state was modified."
            )
        if status >= 400:
            return ToolResult.fail(
                f"Pterodactyl API error for {context}: HTTP {status}."
            )
        return None

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        """Decode a JSON response body, tolerating an empty/invalid body."""
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}
