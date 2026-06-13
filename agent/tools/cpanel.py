"""The cPanel_Manager tool: cPanel hosting operations (Requirement 14).

Drives the cPanel UAPI / File Manager API with ``requests``, authenticated by a
stored API token (R14.1). Operations:

* :meth:`CPanelManager.upload_file` -- upload a local file (<= 100 MB) to a
  named directory through the File Manager API (R14.3).
* :meth:`CPanelManager.download_file` -- retrieve a file's contents from a
  named path (R14.4).
* :meth:`CPanelManager.create_database` -- create a MySQL database (R14.5).
* :meth:`CPanelManager.create_subdomain` -- create a subdomain (R14.6).
* :meth:`CPanelManager.deploy_website` -- upload website files to a document
  root (R14.7).

Error semantics (each returns a uniform :class:`~agent.models.ToolResult`):

* a missing/invalid/expired token aborts without modifying hosting state and
  returns an authentication failure (R14.2);
* an upload/download/deploy whose local source path does not exist is rejected
  without modifying files (R14.8);
* each API request is bounded by a 30 second timeout; exceeding it is treated
  as a failure (R14.9);
* an API error is returned as a failure while independent operations remain
  unaffected (R14.10).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Protocol

import requests

from agent.models import ToolResult

# Maximum upload size in bytes: 100 megabytes (R14.3).
MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024

# Per-operation request timeout, in seconds (R14.9).
DEFAULT_TIMEOUT_SECONDS: float = 30.0


class _SessionLike(Protocol):
    """The subset of :class:`requests.Session` the manager depends on."""

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response: ...


class CPanelManager:
    """Performs hosting operations through the cPanel API."""

    def __init__(
        self,
        base_url: str,
        username: Optional[str],
        api_token: Optional[str],
        *,
        session: Optional[_SessionLike] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a cPanel_Manager.

        Args:
            base_url: the cPanel base URL (e.g. ``https://host.example.com:2083``).
            username: the cPanel account username used for token auth.
            api_token: the stored API token; ``None``/empty aborts every
                operation with an authentication failure (R14.2).
            session: a ``requests``-compatible session (injected in tests).
            timeout: per-operation request timeout in seconds (R14.9).
        """
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._api_token = api_token.strip() if isinstance(api_token, str) else api_token
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    # -- File operations --------------------------------------------------- #

    def upload_file(self, local_path: str, remote_dir: str) -> ToolResult:
        """Upload ``local_path`` into ``remote_dir`` (<= 100 MB) (R14.3, R14.8)."""
        auth_error = self._require_token()
        if auth_error is not None:
            return auth_error

        source = Path(local_path)
        if not source.exists() or not source.is_file():
            return ToolResult.fail(
                f"cPanel upload rejected: local source {local_path!r} does not "
                "exist; no files were modified."
            )
        size = source.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            return ToolResult.fail(
                f"cPanel upload rejected: {local_path!r} is {size} bytes, which "
                f"exceeds the {MAX_UPLOAD_BYTES}-byte (100 MB) limit."
            )

        try:
            with open(source, "rb") as handle:
                response = self._session.request(
                    "POST",
                    f"{self._base_url}/execute/Fileman/upload_files",
                    headers=self._headers(),
                    params={"dir": remote_dir},
                    files={"file-1": (source.name, handle)},
                    timeout=self._timeout,
                )
        except requests.Timeout:
            return self._timeout_failure("upload")
        except requests.RequestException as exc:
            return ToolResult.fail(f"cPanel upload request failed: {exc}")

        return self._uapi_result(
            response,
            context="upload",
            on_success=lambda _data: ToolResult.ok(
                {"local": str(source), "dir": remote_dir, "bytes": size}
            ),
        )

    def download_file(self, remote_path: str) -> ToolResult:
        """Retrieve the contents of ``remote_path`` (R14.4, R14.8)."""
        auth_error = self._require_token()
        if auth_error is not None:
            return auth_error

        directory, _, name = remote_path.rpartition("/")
        try:
            response = self._session.request(
                "GET",
                f"{self._base_url}/execute/Fileman/get_file_content",
                headers=self._headers(),
                params={"dir": directory or "/", "file": name or remote_path},
                timeout=self._timeout,
            )
        except requests.Timeout:
            return self._timeout_failure("download")
        except requests.RequestException as exc:
            return ToolResult.fail(f"cPanel download request failed: {exc}")

        def _on_success(data: dict[str, Any]) -> ToolResult:
            return ToolResult.ok(
                {"path": remote_path, "content": data.get("content", "")}
            )

        return self._uapi_result(
            response, context="download", on_success=_on_success, missing_path=remote_path
        )

    # -- Provisioning operations ------------------------------------------ #

    def create_database(self, name: str) -> ToolResult:
        """Create a MySQL database named ``name`` (R14.5)."""
        auth_error = self._require_token()
        if auth_error is not None:
            return auth_error
        try:
            response = self._session.request(
                "GET",
                f"{self._base_url}/execute/Mysql/create_database",
                headers=self._headers(),
                params={"name": name},
                timeout=self._timeout,
            )
        except requests.Timeout:
            return self._timeout_failure("create_database")
        except requests.RequestException as exc:
            return ToolResult.fail(f"cPanel create_database request failed: {exc}")
        return self._uapi_result(
            response,
            context="create_database",
            on_success=lambda _data: ToolResult.ok({"database": name}),
        )

    def create_subdomain(self, subdomain: str, domain: str, document_root: str) -> ToolResult:
        """Create a subdomain ``subdomain.domain`` rooted at ``document_root`` (R14.6)."""
        auth_error = self._require_token()
        if auth_error is not None:
            return auth_error
        try:
            response = self._session.request(
                "GET",
                f"{self._base_url}/execute/SubDomain/addsubdomain",
                headers=self._headers(),
                params={
                    "domain": subdomain,
                    "rootdomain": domain,
                    "dir": document_root,
                },
                timeout=self._timeout,
            )
        except requests.Timeout:
            return self._timeout_failure("create_subdomain")
        except requests.RequestException as exc:
            return ToolResult.fail(f"cPanel create_subdomain request failed: {exc}")
        return self._uapi_result(
            response,
            context="create_subdomain",
            on_success=lambda _data: ToolResult.ok(
                {"subdomain": f"{subdomain}.{domain}", "document_root": document_root}
            ),
        )

    def deploy_website(self, local_dir: str, document_root: str) -> ToolResult:
        """Upload every file under ``local_dir`` to ``document_root`` (R14.7, R14.8).

        Each file is uploaded independently; a failure on one file is reported
        while the files that uploaded successfully remain in place (R14.10).
        """
        auth_error = self._require_token()
        if auth_error is not None:
            return auth_error

        source = Path(local_dir)
        if not source.exists() or not source.is_dir():
            return ToolResult.fail(
                f"cPanel website deployment rejected: local directory "
                f"{local_dir!r} does not exist; no files were modified."
            )

        uploaded: list[str] = []
        failures: list[dict[str, str]] = []
        for entry in sorted(source.rglob("*")):
            if not entry.is_file():
                continue
            relative = entry.relative_to(source)
            remote_dir = (
                f"{document_root.rstrip('/')}/{relative.parent.as_posix()}"
                if str(relative.parent) != "."
                else document_root
            )
            result = self.upload_file(str(entry), remote_dir)
            if result.success:
                uploaded.append(relative.as_posix())
            else:
                failures.append({"file": relative.as_posix(), "error": result.error or ""})

        if failures:
            return ToolResult.fail(
                f"cPanel website deployment completed with {len(failures)} "
                f"failed file(s); {len(uploaded)} uploaded successfully.",
                data={"uploaded": uploaded, "failures": failures},
            )
        return ToolResult.ok({"document_root": document_root, "uploaded": uploaded})

    # -- Helpers ----------------------------------------------------------- #

    def _require_token(self) -> Optional[ToolResult]:
        """Return an auth-failure result when the token is missing (R14.2)."""
        if not self._api_token or not self._username:
            return ToolResult.fail(
                "cPanel authentication failed: no API token is configured; the "
                "operation was aborted without modifying any hosting state."
            )
        return None

    def _headers(self) -> dict[str, str]:
        """Build the cPanel token-authenticated headers (R14.1)."""
        return {"Authorization": f"cpanel {self._username}:{self._api_token}"}

    def _timeout_failure(self, operation: str) -> ToolResult:
        """Build the failure result for an operation that exceeded the timeout (R14.9)."""
        return ToolResult.fail(
            f"cPanel {operation} timed out after {self._timeout:g}s and was "
            "treated as failed; no hosting state was modified."
        )

    def _uapi_result(
        self,
        response: requests.Response,
        *,
        context: str,
        on_success,
        missing_path: Optional[str] = None,
    ) -> ToolResult:
        """Interpret a UAPI response into a :class:`ToolResult`.

        Maps 401/403 to an authentication failure (R14.2), 404 to a missing
        path when applicable (R14.8), other HTTP errors and UAPI-level
        ``errors`` to a failure (R14.10), and delegates a success body to
        ``on_success``.
        """
        status = response.status_code
        if status in (401, 403):
            return ToolResult.fail(
                f"cPanel authentication failed for {context}: the API token was "
                "rejected; no hosting state was modified."
            )
        if status == 404 and missing_path is not None:
            return ToolResult.fail(
                f"cPanel {context} rejected: path {missing_path!r} does not "
                "exist; no files were modified."
            )
        if status >= 400:
            return ToolResult.fail(f"cPanel API error for {context}: HTTP {status}.")

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            errors = payload.get("errors")
            status_field = payload.get("status")
            if errors:
                joined = "; ".join(str(item) for item in errors)
                # A "not found" UAPI error on a path operation is a missing path.
                if missing_path is not None and "not" in joined.lower() and "exist" in joined.lower():
                    return ToolResult.fail(
                        f"cPanel {context} rejected: path {missing_path!r} does "
                        "not exist; no files were modified."
                    )
                return ToolResult.fail(f"cPanel API error for {context}: {joined}.")
            if status_field == 0:
                return ToolResult.fail(
                    f"cPanel API error for {context}: the operation reported failure."
                )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        else:
            data = {}
        return on_success(data)
