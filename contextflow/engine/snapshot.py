"""Workspace snapshot capture and restore engine.

Captures: open windows (position, size, app), browser tabs (optional), editor files (optional).
Restores: launches apps, repositions windows, reopens tabs.
"""

import subprocess
import time
import os
import sys
from dataclasses import dataclass, field

import win32gui
import win32con
import psutil

from contextflow.watcher.app_watcher import AppWatcher, WindowInfo, Snapshot
from contextflow.engine.tab_capture import TabCapture, BrowserTab


@dataclass
class RestoreResult:
    """Outcome of a restore operation."""
    success: bool = True
    launched: list[str] = field(default_factory=list)
    repositioned: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SnapshotEngine:
    """Captures and restores workspace snapshots."""

    def __init__(self, app_watcher: AppWatcher | None = None):
        self._watcher = app_watcher or AppWatcher()
        self._tab_capture = TabCapture()

    def capture(self) -> Snapshot:
        """Take a full snapshot of all open windows and foreground app."""
        return self._watcher.take_snapshot()

    def capture_tabs(self) -> list[dict]:
        """Capture current browser tabs as plain dicts for storage."""
        results = self._tab_capture.capture()
        tabs = []
        for r in results:
            for t in r.tabs:
                tabs.append({
                    "browser": t.browser,
                    "tab_title": t.title,
                    "tab_url": t.url,
                    "is_pinned": t.is_pinned,
                })
        return tabs

    def windows_to_dicts(self, windows: list[WindowInfo]) -> list[dict]:
        """Convert WindowInfo objects to plain dicts for storage."""
        result = []
        for w in windows:
            left, top, right, bottom = w.rect
            result.append({
                "app_exe": w.exe_path,
                "app_name": w.app_name,
                "app_class": w.app_class,
                "rect_left": left,
                "rect_top": top,
                "rect_width": max(1, right - left),
                "rect_height": max(1, bottom - top),
                "is_minimized": w.is_minimized,
            })
        return result

    # ── restore ──────────────────────────────────────────────────────

    def restore(self, windows: list[dict], tabs: list[dict] | None = None,
                current_windows: list[WindowInfo] | None = None) -> RestoreResult:
        """Restore a saved workspace.

        Args:
            windows: List of window dicts from DB (app_exe, rect_*, etc.)
            tabs: Optional browser tabs to restore.
            current_windows: Current open windows (to avoid duplicate launches).

        Returns:
            RestoreResult with launch/reposition/failure info.
        """
        result = RestoreResult()

        # ── 1. build a lookup of what's already open ──
        if current_windows is None:
            current_windows = self._watcher.take_snapshot().windows

        open_by_exe: dict[str, list[WindowInfo]] = {}
        for cw in current_windows:
            exe_lower = cw.exe_path.lower()
            if exe_lower not in open_by_exe:
                open_by_exe[exe_lower] = []
            open_by_exe[exe_lower].append(cw)

        # ── 2. launch missing apps ──
        launched_exes: dict[str, int] = {}  # exe_lower → pid

        for w in windows:
            exe = w.get("app_exe", "")
            if not exe or not os.path.isfile(exe):
                continue

            exe_lower = exe.lower()

            # Already open?
            if exe_lower in open_by_exe and open_by_exe[exe_lower]:
                continue

            # Already launched this round?
            if exe_lower in launched_exes:
                continue

            try:
                # Use os.startfile for simpler Windows launch
                # (subprocess.Popen for apps that need args)
                proc = subprocess.Popen(
                    [exe],
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                launched_exes[exe_lower] = proc.pid
                result.launched.append(w.get("app_name", exe))
            except Exception as e:
                result.failed.append(f"{w.get('app_name', exe)}: {e}")

        # ── 3. wait briefly for windows to appear ──
        if launched_exes:
            self._wait_for_windows(launched_exes, open_by_exe, max_wait=5.0)

        # ── 4. reposition windows ──
        # Refresh current windows after launches
        all_open = self._watcher.take_snapshot().windows
        open_by_exe = {}
        for cw in all_open:
            exe_lower = cw.exe_path.lower()
            if exe_lower not in open_by_exe:
                open_by_exe[exe_lower] = []
            open_by_exe[exe_lower].append(cw)

        # Match each saved window to an open window of the same exe
        for w in windows:
            exe = w.get("app_exe", "")
            exe_lower = exe.lower()
            if exe_lower not in open_by_exe:
                continue

            candidates = open_by_exe[exe_lower]
            if not candidates:
                continue

            # Pick the best match: prefer not-repositioned yet, similar title
            target = candidates[0]
            left = w.get("rect_left", 100)
            top = w.get("rect_top", 100)
            width = w.get("rect_width", 800)
            height = w.get("rect_height", 600)

            try:
                # Restore if minimized
                if win32gui.IsIconic(target.hwnd):
                    win32gui.ShowWindow(target.hwnd, win32con.SW_RESTORE)

                # Move and resize
                win32gui.SetWindowPos(
                    target.hwnd, 0,
                    left, top, width, height,
                    win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                )
                result.repositioned.append(w.get("app_name", exe))

                # Remove from candidates (don't reposition same window twice)
                open_by_exe[exe_lower].remove(target)

            except Exception as e:
                result.warnings.append(f"Could not reposition {w.get('app_name', exe)}: {e}")

        # ── 5. restore browser tabs ──
        if tabs:
            restored = self._tab_capture.restore_tabs([
                BrowserTab(
                    browser=t.get("browser", "chrome"),
                    title=t.get("tab_title", ""),
                    url=t.get("tab_url", ""),
                    is_pinned=bool(t.get("is_pinned")),
                )
                for t in tabs
            ])
            total = sum(len(urls) for urls in restored.values())
            if total > 0:
                result.repositioned.append(f"{total} browser tab(s)")
            else:
                result.warnings.append("CDP not available for tab restore. "
                    "Start Chrome with --remote-debugging-port=9222")

        return result

    # ── helpers ──────────────────────────────────────────────────────

    def _wait_for_windows(self, launched_exes: dict[str, int],
                          open_by_exe: dict[str, list[WindowInfo]],
                          max_wait: float = 5.0, interval: float = 0.5):
        """Wait for launched apps to create visible windows."""
        waited = 0.0
        while waited < max_wait:
            remaining = {exe: pid for exe, pid in launched_exes.items()
                         if exe not in open_by_exe}
            if not remaining:
                break

            time.sleep(interval)
            waited += interval

            # Refresh
            snapshot = self._watcher.take_snapshot()
            for cw in snapshot.windows:
                exe_lower = cw.exe_path.lower()
                if exe_lower not in open_by_exe:
                    open_by_exe[exe_lower] = []
                open_by_exe[exe_lower].append(cw)
