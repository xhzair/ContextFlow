"""Browser tab capture and restore via Chrome DevTools Protocol (CDP).

Primary channel: CDP (requires Chrome/Edge with --remote-debugging-port).
Fallback: Browser extension writes tabs to local JSON file.

Protocol reference: https://chromedevtools.github.io/devtools-protocol/
"""

import json
import time
import os
import urllib.request
import urllib.error
import subprocess
import re
from dataclasses import dataclass, field
from pathlib import Path


# ── data types ──────────────────────────────────────────────────────

@dataclass
class BrowserTab:
    """A single browser tab."""
    browser: str        # "chrome" | "edge" | "firefox"
    title: str
    url: str
    is_pinned: bool = False


@dataclass
class TabCaptureResult:
    """Result of a tab capture operation."""
    success: bool
    browser: str
    tabs: list[BrowserTab] = field(default_factory=list)
    error: str = ""


# ── CDP client ───────────────────────────────────────────────────────

# Default debug ports to try for each browser
CDP_PORTS = {
    "chrome": [9222, 9223, 9224],
    "edge": [9222, 9223, 9224],
    "brave": [9222],
}

# Browser executable names (for detecting running processes)
BROWSER_NAMES = {
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "brave": ["brave.exe"],
    "firefox": ["firefox.exe"],
}

# Where the extension writes its tab data
EXTENSION_DATA_DIR = Path.home() / "AppData" / "Local" / "ContextFlow"
EXTENSION_TABS_FILE = "browser_tabs.json"


class TabCapture:
    """Capture and restore browser tabs.

    Tries CDP first, then falls back to extension data.
    """

    def __init__(self):
        self._timeout = 3.0  # HTTP timeout seconds

    # ── capture ──────────────────────────────────────────────────────

    def capture(self) -> list[TabCaptureResult]:
        """Capture tabs from all detected running browsers."""
        results: list[TabCaptureResult] = []

        for browser_name, exe_names in BROWSER_NAMES.items():
            if not self._is_browser_running(exe_names):
                continue

            # Try CDP
            result = self._capture_via_cdp(browser_name)
            if result.success and result.tabs:
                results.append(result)
                continue

            # Fallback: try extension data
            result = self._capture_via_extension(browser_name)
            if result.success:
                results.append(result)

        return results

    def _capture_via_cdp(self, browser: str) -> TabCaptureResult:
        """Capture tabs via Chrome DevTools Protocol."""
        ports = CDP_PORTS.get(browser, [9222])
        debug_url = None

        for port in ports:
            url = f"http://localhost:{port}/json"
            try:
                req = urllib.request.Request(url)
                req.add_header("Host", f"localhost:{port}")
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    pages = json.loads(resp.read().decode())
                    # CDP returns ALL targets including DevTools, extensions, etc.
                    # Filter: only normal web pages (type="page" with non-empty url)
                    tabs = []
                    for p in pages:
                        if p.get("type") == "page" and p.get("url") and not p["url"].startswith("chrome://"):
                            tabs.append(BrowserTab(
                                browser=browser,
                                title=p.get("title", ""),
                                url=p["url"],
                            ))
                    if tabs:
                        return TabCaptureResult(success=True, browser=browser, tabs=tabs)
            except (urllib.error.URLError, urllib.error.HTTPError,
                    ConnectionRefusedError, OSError, json.JSONDecodeError):
                continue

        return TabCaptureResult(
            success=False, browser=browser,
            error=f"CDP not available on ports {ports}. Start browser with --remote-debugging-port=9222"
        )

    def _capture_via_extension(self, browser: str) -> TabCaptureResult:
        """Fallback: read tabs from the extension's data file."""
        data_path = EXTENSION_DATA_DIR / f"{browser}_{EXTENSION_TABS_FILE}"
        if not data_path.exists():
            return TabCaptureResult(
                success=False, browser=browser,
                error="Extension data not found. Install the ContextFlow browser extension."
            )

        try:
            with open(data_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            tabs = []
            for t in data.get("tabs", []):
                url = t.get("url", "")
                if url and not url.startswith("chrome://") and not url.startswith("about:"):
                    tabs.append(BrowserTab(
                        browser=browser,
                        title=t.get("title", ""),
                        url=url,
                    ))

            return TabCaptureResult(success=True, browser=browser, tabs=tabs)
        except (json.JSONDecodeError, OSError) as e:
            return TabCaptureResult(success=False, browser=browser, error=str(e))

    # ── restore ──────────────────────────────────────────────────────

    def restore_tabs(self, tabs: list[BrowserTab]) -> dict[str, list[str]]:
        """Restore browser tabs. Returns {browser: [urls_restored]}."""
        restored: dict[str, list[str]] = {}

        for tab in tabs:
            browser = tab.browser
            ports = CDP_PORTS.get(browser, [9222])

            opened = False
            for port in ports:
                try:
                    self._cdp_new_tab(port, tab.url)
                    opened = True
                    break
                except Exception:
                    continue

            if browser not in restored:
                restored[browser] = []
            restored[browser].append(tab.url if opened else f"FAILED: {tab.url}")

        return restored

    def _cdp_new_tab(self, port: int, url: str) -> dict:
        """Open a new tab via CDP PUT /json/new?url=..."""
        target_url = f"http://localhost:{port}/json/new?{urllib.parse.urlencode({'url': url})}"
        req = urllib.request.Request(target_url, method="PUT")
        req.add_header("Host", f"localhost:{port}")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode())

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _is_browser_running(exe_names: list[str]) -> bool:
        """Check if a browser is running by scanning process names."""
        try:
            import psutil
            running = {p.name().lower() for p in psutil.process_iter(["name"])}
            for name in exe_names:
                if name.lower() in running:
                    return True
        except Exception:
            # Fallback: try CDP directly (assume might be running)
            return True
        return False

    @staticmethod
    def get_cdp_help(browser: str = "chrome") -> str:
        """Return instructions for enabling CDP."""
        if browser in ("chrome", "brave"):
            return (
                "To enable browser tab capture:\n\n"
                f'1. Right-click your {browser.title()} shortcut → Properties\n'
                f'2. In the "Target" field, add at the end:\n'
                f'   --remote-debugging-port=9222\n'
                f'3. Example: "...chrome.exe" --remote-debugging-port=9222\n\n'
                f"4. Restart {browser.title()} completely (check Task Manager)"
            )
        elif browser == "edge":
            return (
                "To enable browser tab capture:\n\n"
                f'1. Right-click your Edge shortcut → Properties\n'
                f'2. In the "Target" field, add at the end:\n'
                f'   --remote-debugging-port=9222\n'
                f'3. Example: "...msedge.exe" --remote-debugging-port=9222\n\n'
                f"4. Restart Edge completely (check Task Manager)"
            )
        return "CDP not supported for this browser."
