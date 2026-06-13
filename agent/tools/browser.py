"""The Browser_Automation tool: Playwright-controlled browsing (Requirement 16).

Drives a headless browser through Playwright's synchronous API. A single page
is maintained across calls so navigation and subsequent interactions act on the
same document. Operations (each returns a uniform
:class:`~agent.models.ToolResult`):

* :meth:`BrowserAutomation.browser_open` -- navigate to a URL, bounded by a
  30 second navigation timeout (R16.1).
* :meth:`BrowserAutomation.browser_click` -- click the element matching a
  selector (R16.2).
* :meth:`BrowserAutomation.browser_type` -- type text (<= 10000 characters)
  into the element matching a selector (R16.3).
* :meth:`BrowserAutomation.browser_extract_text` -- return the text content of
  the element matching a selector, or an empty string when it has no text
  (R16.5).
* :meth:`BrowserAutomation.browser_screenshot` -- capture a screenshot and
  return the saved image file path (R16.6).

Any failure returns an error describing the failure and identifying the
selector or URL, leaving the page state unchanged (R16.7). The Playwright
runtime is started lazily on first use so importing this module never requires
browser binaries to be installed.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.models import ToolResult

# Navigation timeout in milliseconds: 30 seconds (R16.1).
NAV_TIMEOUT_MS: int = 30_000

# Default action (click/type) timeout in milliseconds.
ACTION_TIMEOUT_MS: int = 30_000

# Maximum number of characters accepted by ``browser_type`` (R16.3).
MAX_TYPE_CHARS: int = 10_000


class BrowserAutomationError(Exception):
    """Raised when the Playwright runtime cannot be started."""


class BrowserAutomation:
    """A Playwright-controlled headless browser exposing page operations."""

    def __init__(
        self,
        *,
        headless: bool = True,
        browser_name: str = "chromium",
        screenshot_dir: Optional[str] = None,
        nav_timeout_ms: int = NAV_TIMEOUT_MS,
        action_timeout_ms: int = ACTION_TIMEOUT_MS,
    ) -> None:
        """Create a Browser_Automation controller (does not launch a browser).

        Args:
            headless: run the browser without a visible UI.
            browser_name: ``chromium`` (default), ``firefox``, or ``webkit``.
            screenshot_dir: directory for captured screenshots; defaults to a
                temporary directory.
            nav_timeout_ms / action_timeout_ms: navigation and action timeouts.
        """
        self._headless = headless
        self._browser_name = browser_name
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else Path(
            tempfile.gettempdir()
        )
        self._nav_timeout_ms = nav_timeout_ms
        self._action_timeout_ms = action_timeout_ms
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    # -- Lifecycle --------------------------------------------------------- #

    def _ensure_page(self) -> Any:
        """Start Playwright and a page on first use; reuse it thereafter."""
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - import guarded
            raise BrowserAutomationError(
                f"Playwright is not installed: {exc}"
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            browser_type = getattr(self._playwright, self._browser_name)
            self._browser = browser_type.launch(headless=self._headless)
            self._page = self._browser.new_page()
            self._page.set_default_timeout(self._action_timeout_ms)
            self._page.set_default_navigation_timeout(self._nav_timeout_ms)
        except Exception as exc:  # noqa: BLE001 - surface a clear startup error
            self.close()
            raise BrowserAutomationError(
                f"Failed to launch the {self._browser_name} browser: {exc}"
            ) from exc
        return self._page

    def close(self) -> None:
        """Close the page, browser, and Playwright runtime (best effort)."""
        for closer in (
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - shutdown must not raise
                    pass
        self._page = None
        self._browser = None
        self._playwright = None

    def __enter__(self) -> "BrowserAutomation":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- Operations -------------------------------------------------------- #

    def browser_open(self, url: str) -> ToolResult:
        """Navigate to ``url`` within the navigation timeout (R16.1, R16.7)."""
        try:
            page = self._ensure_page()
        except BrowserAutomationError as exc:
            return ToolResult.fail(str(exc))
        try:
            response = page.goto(url, timeout=self._nav_timeout_ms)
        except Exception as exc:  # noqa: BLE001 - Playwright raises many types
            return ToolResult.fail(
                f"Failed to open URL {url!r}: {exc}; the page state was left unchanged."
            )
        status = response.status if response is not None else None
        return ToolResult.ok({"url": url, "status": status, "title": page.title()})

    def browser_click(self, selector: str) -> ToolResult:
        """Click the element matching ``selector`` (R16.2, R16.7)."""
        try:
            page = self._ensure_page()
        except BrowserAutomationError as exc:
            return ToolResult.fail(str(exc))
        try:
            page.click(selector, timeout=self._action_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(
                f"Failed to click selector {selector!r}: {exc}; the page state "
                "was left unchanged."
            )
        return ToolResult.ok({"selector": selector})

    def browser_type(self, selector: str, text: str) -> ToolResult:
        """Type ``text`` into the element matching ``selector`` (R16.3, R16.7)."""
        if len(text) > MAX_TYPE_CHARS:
            return ToolResult.fail(
                f"Refusing to type {len(text)} characters into {selector!r}: the "
                f"limit is {MAX_TYPE_CHARS} characters."
            )
        try:
            page = self._ensure_page()
        except BrowserAutomationError as exc:
            return ToolResult.fail(str(exc))
        try:
            page.fill(selector, text, timeout=self._action_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(
                f"Failed to type into selector {selector!r}: {exc}; the page "
                "state was left unchanged."
            )
        return ToolResult.ok({"selector": selector, "length": len(text)})

    def browser_extract_text(self, selector: str) -> ToolResult:
        """Return the element's text, or an empty string when it has none (R16.5)."""
        try:
            page = self._ensure_page()
        except BrowserAutomationError as exc:
            return ToolResult.fail(str(exc))
        try:
            element = page.query_selector(selector)
            if element is None:
                return ToolResult.fail(
                    f"No element matches selector {selector!r}; the page state "
                    "was left unchanged."
                )
            text = element.text_content()
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(
                f"Failed to extract text for selector {selector!r}: {exc}."
            )
        return ToolResult.ok({"selector": selector, "text": text or ""})

    def browser_screenshot(self) -> ToolResult:
        """Capture a screenshot and return the saved file path (R16.6, R16.7)."""
        try:
            page = self._ensure_page()
        except BrowserAutomationError as exc:
            return ToolResult.fail(str(exc))
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self._screenshot_dir / f"screenshot-{uuid.uuid4().hex}.png"
        try:
            page.screenshot(path=str(path))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Failed to capture screenshot: {exc}.")
        return ToolResult.ok({"path": str(path)})
