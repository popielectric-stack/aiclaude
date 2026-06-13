"""Integration and unit tests for the cPanel_Manager (Requirement 14).

Covers authentication-failure (R14.2), missing-path (R14.8), per-operation
timeout (R14.9), and the 100 MB upload boundary (R14.3) against mocked HTTP
endpoints (``requests_mock``).
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from agent.tools.cpanel import MAX_UPLOAD_BYTES, CPanelManager

BASE = "https://host.example.com:2083"


def _manager(session: requests.Session, *, token: str | None = "tok", **kwargs) -> CPanelManager:
    return CPanelManager(BASE, "owner", token, session=session, **kwargs)


def _make_sized_file(path: Path, size: int) -> Path:
    """Create a sparse file of exactly ``size`` bytes without consuming disk."""
    with open(path, "wb") as handle:
        if size > 0:
            handle.seek(size - 1)
            handle.write(b"\0")
    return path


def test_upload_success(requests_mock, tmp_path: Path) -> None:
    """A small file uploads successfully through the File Manager API (R14.3)."""
    session = requests.Session()
    requests_mock.post(
        f"{BASE}/execute/Fileman/upload_files",
        json={"status": 1, "errors": None, "data": {}},
    )
    local = tmp_path / "index.html"
    local.write_text("<html></html>", encoding="utf-8")
    result = _manager(session).upload_file(str(local), "public_html")
    assert result.success is True
    assert result.data["dir"] == "public_html"


def test_missing_api_token_aborts_without_request(tmp_path: Path) -> None:
    """A missing token aborts every operation without a network call (R14.2)."""
    local = tmp_path / "f.txt"
    local.write_text("x", encoding="utf-8")
    result = _manager(requests.Session(), token=None).upload_file(str(local), "public_html")
    assert result.success is False
    assert "no API token" in result.error


def test_authentication_failure(requests_mock, tmp_path: Path) -> None:
    """A rejected token yields an authentication failure (R14.2)."""
    session = requests.Session()
    requests_mock.post(f"{BASE}/execute/Fileman/upload_files", status_code=403)
    local = tmp_path / "f.txt"
    local.write_text("x", encoding="utf-8")
    result = _manager(session).upload_file(str(local), "public_html")
    assert result.success is False
    assert "authentication failed" in result.error.lower()


def test_upload_missing_local_path_rejected(tmp_path: Path) -> None:
    """A missing local source path is rejected without modifying files (R14.8)."""
    result = _manager(requests.Session()).upload_file(str(tmp_path / "nope.txt"), "public_html")
    assert result.success is False
    assert "does not exist" in result.error


def test_download_missing_path_rejected(requests_mock) -> None:
    """A 404 on download is reported as a missing path (R14.8)."""
    session = requests.Session()
    requests_mock.get(f"{BASE}/execute/Fileman/get_file_content", status_code=404)
    result = _manager(session).download_file("public_html/missing.txt")
    assert result.success is False
    assert "does not exist" in result.error


def test_operation_timeout(requests_mock, tmp_path: Path) -> None:
    """An operation that exceeds the 30 s timeout is treated as failed (R14.9)."""
    session = requests.Session()
    requests_mock.get(
        f"{BASE}/execute/Mysql/create_database", exc=requests.Timeout
    )
    result = _manager(session).create_database("appdb")
    assert result.success is False
    assert "timed out" in result.error


def test_upload_exactly_100mb_is_allowed(requests_mock, tmp_path: Path) -> None:
    """A file of exactly 100 MB is at the boundary and is allowed (R14.3)."""
    session = requests.Session()
    requests_mock.post(
        f"{BASE}/execute/Fileman/upload_files",
        json={"status": 1, "errors": None, "data": {}},
    )
    local = _make_sized_file(tmp_path / "big.bin", MAX_UPLOAD_BYTES)
    assert local.stat().st_size == MAX_UPLOAD_BYTES
    result = _manager(session).upload_file(str(local), "public_html")
    assert result.success is True


def test_upload_over_100mb_is_rejected(tmp_path: Path) -> None:
    """A file one byte over 100 MB is rejected without a request (R14.3)."""
    local = _make_sized_file(tmp_path / "toobig.bin", MAX_UPLOAD_BYTES + 1)
    result = _manager(requests.Session()).upload_file(str(local), "public_html")
    assert result.success is False
    assert "exceeds" in result.error


def test_create_subdomain_success(requests_mock) -> None:
    """A subdomain is created through the cPanel API (R14.6)."""
    session = requests.Session()
    requests_mock.get(
        f"{BASE}/execute/SubDomain/addsubdomain",
        json={"status": 1, "errors": None, "data": {}},
    )
    result = _manager(session).create_subdomain("api", "example.com", "public_html/api")
    assert result.success is True
    assert result.data["subdomain"] == "api.example.com"


def test_uapi_error_is_returned(requests_mock) -> None:
    """A UAPI-level error body is surfaced as a failure (R14.10)."""
    session = requests.Session()
    requests_mock.get(
        f"{BASE}/execute/Mysql/create_database",
        json={"status": 0, "errors": ["database already exists"], "data": None},
    )
    result = _manager(session).create_database("appdb")
    assert result.success is False
    assert "already exists" in result.error
