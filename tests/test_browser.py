"""Integration and unit tests for the Browser_Automation tool (Requirement 16).

The live browser tests exercise open / click / type / extract / screenshot
against a static local page served via the ``file://`` scheme, plus the
empty-text and 10,000-character edges and a no-match selector error. They are
gated behind an availability check: if Playwright's browser binaries cannot be
launched in this environment, the live tests are skipped so the suite still
passes, while the production implementation remains unchanged.

The input-validation edges that do not require a browser (the >10,000-character
rejection) are tested unconditionally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.tools.browser import MAX_TYPE_CHARS, BrowserAutomation


def _browser_available() -> bool:
    """Return ``True`` when a headless browser can actually be launched."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:  # noqa: BLE001 - any launch failure means "unavailable"
        return False


_LIVE = pytest.mark.skipif(
    not _browser_available(),
    reason="Playwright browser binaries are not runnable in this environment.",
)

_PAGE_HTML = """<!doctype html>
<html>
  <head><title>Test Page</title></head>
  <body>
    <h1 id="heading">Hello Browser</h1>
    <p id="empty"></p>
    <input id="field" type="text" />
    <button id="btn" onclick="document.getElementById('heading').textContent='Clicked'">Go</button>
  </body>
</html>
"""


@pytest.fixture()
def page_url(tmp_path: Path) -> str:
    page = tmp_path / "page.html"
    page.write_text(_PAGE_HTML, encoding="utf-8")
    return page.as_uri()


# -- Unit edge that needs no browser --------------------------------------- #


def test_type_rejects_text_over_limit() -> None:
    """Typing more than 10,000 characters is rejected before any action (R16.3)."""
    browser = BrowserAutomation()
    result = browser.browser_type("#field", "x" * (MAX_TYPE_CHARS + 1))
    assert result.success is False
    assert "limit" in result.error


# -- Live browser integration tests (gated) -------------------------------- #


@_LIVE
def test_open_and_extract_text(page_url: str) -> None:
    """Open a page and extract an element's text (R16.1, R16.4)."""
    with BrowserAutomation() as browser:
        opened = browser.browser_open(page_url)
        assert opened.success is True
        assert opened.data["title"] == "Test Page"

        extracted = browser.browser_extract_text("#heading")
        assert extracted.success is True
        assert extracted.data["text"] == "Hello Browser"


@_LIVE
def test_extract_empty_element_returns_empty_string(page_url: str) -> None:
    """An element with no text yields an empty string (R16.5)."""
    with BrowserAutomation() as browser:
        browser.browser_open(page_url)
        result = browser.browser_extract_text("#empty")
        assert result.success is True
        assert result.data["text"] == ""


@_LIVE
def test_click_changes_page(page_url: str) -> None:
    """Clicking a button updates the page (R16.2)."""
    with BrowserAutomation() as browser:
        browser.browser_open(page_url)
        clicked = browser.browser_click("#btn")
        assert clicked.success is True
        assert browser.browser_extract_text("#heading").data["text"] == "Clicked"


@_LIVE
def test_type_at_max_length_is_accepted(page_url: str) -> None:
    """Typing exactly 10,000 characters is accepted (R16.3 boundary)."""
    with BrowserAutomation() as browser:
        browser.browser_open(page_url)
        result = browser.browser_type("#field", "a" * MAX_TYPE_CHARS)
        assert result.success is True
        assert result.data["length"] == MAX_TYPE_CHARS


@_LIVE
def test_no_match_selector_returns_error(page_url: str) -> None:
    """A selector matching nothing returns an error identifying it (R16.7)."""
    with BrowserAutomation() as browser:
        browser.browser_open(page_url)
        result = browser.browser_extract_text("#does-not-exist")
        assert result.success is False
        assert "#does-not-exist" in result.error


@_LIVE
def test_screenshot_returns_path(page_url: str, tmp_path: Path) -> None:
    """A screenshot is captured and its file path returned (R16.6)."""
    with BrowserAutomation(screenshot_dir=str(tmp_path / "shots")) as browser:
        browser.browser_open(page_url)
        result = browser.browser_screenshot()
        assert result.success is True
        assert Path(result.data["path"]).is_file()
