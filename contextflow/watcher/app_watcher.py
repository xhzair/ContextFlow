"""Window tracker — enumerates open windows, maps them to categories.

Uses win32gui (pywin32) for stable window enumeration.
Always-on; no session concept. Privacy: window titles NOT stored to disk.
"""

import time
import os
from collections import deque
from dataclasses import dataclass, field

import win32gui
import win32process
import psutil


# ── data types ────────────────────────────────────────────────────────

@dataclass
class WindowInfo:
    """A single open window."""
    hwnd: int
    title: str
    app_class: str          # Win32 window class (stable ID)
    exe_path: str           # Executable path (via psutil)
    pid: int
    rect: tuple[int, int, int, int]   # (left, top, right, bottom)
    is_minimized: bool
    is_visible: bool
    category: str           # editor / browser / terminal / comm / ...
    app_name: str           # Human-readable app name (e.g. "VS Code")


@dataclass
class AppRecord:
    """Lightweight foreground-app observation (for polling history)."""
    ts: float
    app_class: str
    app_title: str
    app_name: str
    category: str


@dataclass
class Snapshot:
    """Full desktop snapshot: all visible top-level windows."""
    ts: float
    windows: list[WindowInfo] = field(default_factory=list)
    foreground_app: str = ""


# ── app-class → (category, app_name) mapping ──────────────────────────

DEFAULT_APP_MAP: dict[str, tuple[str, str]] = {
    # Editors / IDEs
    "VSCode":            ("editor",    "VS Code"),
    "Code":              ("editor",    "VS Code"),
    "Notepad++":         ("editor",    "Notepad++"),
    "Notepad":           ("editor",    "Notepad"),
    "WINDOWCLASS":       ("editor",    "Sublime Text"),
    "SunAwtFrame":       ("editor",    "IntelliJ / JetBrains"),
    # Browsers
    "Chrome_WidgetWin_1":    ("browser",   "Chrome"),
    "MozillaWindowClass":    ("browser",   "Firefox"),
    # Terminals
    "ConsoleWindowClass":    ("terminal",  "Command Prompt"),
    "CascadiaWindowClass":   ("terminal",  "Windows Terminal"),
    "PuTTYConfirmation":     ("terminal",  "PuTTY"),
    "PuTTY":                 ("terminal",  "PuTTY"),
    # Microsoft Office
    "EXCEL7":            ("office",    "Excel"),
    "OPWATCLASS":        ("office",    "Word"),
    "PPTFrameClass":     ("office",    "PowerPoint"),
    "rctrl_renwnd32":    ("office",    "Outlook"),
    # Communication
    "WeChatMainWndForPC":   ("comm",      "WeChat"),
    "TXGuiFoundation":      ("comm",      "QQ / TIM"),
    "DingTalk":             ("comm",      "DingTalk"),
    "TelegramDesktop":      ("comm",      "Telegram"),
    "discord":              ("comm",      "Discord"),
    "TeamsWnd":             ("comm",      "Microsoft Teams"),
    # Entertainment
    "Spotify":           ("entertainment", "Spotify"),
    "VLC":               ("entertainment", "VLC"),
    "Steam":             ("entertainment", "Steam"),
    # System / Shell
    "ApplicationFrameWindow": ("system",   "UWP Application"),
    "TaskManagerWindow":  ("system",    "Task Manager"),
    "CabinetWClass":      ("system",    "File Explorer"),
    "Progman":            ("system",    "Desktop"),
    "Shell_TrayWnd":      ("system",    "Taskbar"),
}

# Windows that should never be in a snapshot
_SYSTEM_CLASSES = frozenset({
    "Shell_TrayWnd", "Progman", "TaskManagerWindow",
    "Button", "Static", "tooltips_class32",
    "Windows.UI.Core.CoreWindow",
})

# Known browser executables (lowercase) — everything else with
# Chrome_WidgetWin_1 class is an Electron / CEF app
_BROWSER_EXES = frozenset({
    "chrome.exe", "msedge.exe", "brave.exe", "chromium.exe",
    "opera.exe", "vivaldi.exe", "arc.exe",
})

# Our own windows — export a set that can be extended at runtime
OWN_CLASSES: set[str] = set()


# ── app watcher ────────────────────────────────────────────────────────

class AppWatcher:
    """Tracks open windows and foreground-app history.

    Features:
        - Enumerate all visible top-level windows (take_snapshot)
        - Poll foreground window for app-switch tracking (poll)
        - Map windows to human-readable categories / names
        - Always-on — no session start/stop required
    """

    def __init__(self):
        self._records: deque[AppRecord] = deque(maxlen=3600)  # 1h @ 1/s poll
        self._last_class = ""
        self._last_switch_ts = 0.0
        self._app_map: dict[str, tuple[str, str]] = dict(DEFAULT_APP_MAP)

        # Warm up the map immediately
        self._app_map = dict(DEFAULT_APP_MAP)

    # ── foreground polling (for auto-discovery data) ─────────────────

    def poll(self) -> AppRecord | None:
        """Poll current foreground window. Call periodically (e.g. 1s)."""
        hwnd = win32gui.GetForegroundWindow()
        if hwnd == 0:
            return None

        app_class = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        category, app_name = self._app_map.get(app_class, ("other", "Unknown"))

        # Try to extract a better name from the exe path
        if app_name == "Unknown" or app_class == "Chrome_WidgetWin_1":
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                exe = psutil.Process(pid).exe()
                if app_class == "Chrome_WidgetWin_1":
                    app_name = self._exe_to_name(exe)
                    exe_name = os.path.basename(exe).lower()
                    category = "browser" if exe_name in _BROWSER_EXES else "editor"
                elif app_name == "Unknown":
                    app_name = self._exe_to_name(exe)
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                pass

        record = AppRecord(
            ts=time.time(),
            app_class=app_class,
            app_title=title,
            app_name=app_name,
            category=category,
        )
        self._records.append(record)

        if app_class != self._last_class:
            self._last_switch_ts = record.ts
            self._last_class = app_class

        return record

    # ── window enumeration (for snapshots) ───────────────────────────

    def take_snapshot(self) -> Snapshot:
        """Capture all visible non-system top-level windows."""
        windows: list[WindowInfo] = []
        foreground_hwnd = win32gui.GetForegroundWindow()
        foreground_name = ""

        def enum_callback(hwnd: int, _ctx: int) -> bool:
            nonlocal foreground_name

            # Basic visibility filter
            if not win32gui.IsWindowVisible(hwnd):
                return True

            title = win32gui.GetWindowText(hwnd)
            app_class = win32gui.GetClassName(hwnd)

            # Skip system / invisible / own windows
            if app_class in _SYSTEM_CLASSES or not title.strip():
                return True
            if app_class in OWN_CLASSES:
                return True

            rect = win32gui.GetWindowRect(hwnd)
            is_minimized = win32gui.IsIconic(hwnd)
            category, app_name = self._app_map.get(app_class, ("other", "Unknown"))

            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                exe = psutil.Process(pid).exe()
                # Resolve Chrome_WidgetWin_1: could be real browser or Electron app
                if app_class == "Chrome_WidgetWin_1":
                    app_name = self._exe_to_name(exe)
                    exe_name = os.path.basename(exe).lower()
                    if exe_name in _BROWSER_EXES:
                        category = "browser"
                    else:
                        category = "editor"
                elif app_name == "Unknown":
                    app_name = self._exe_to_name(exe)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                exe = ""
                app_name = "Unknown"

            wi = WindowInfo(
                hwnd=hwnd,
                title=title,
                app_class=app_class,
                exe_path=exe,
                pid=pid,
                rect=rect,
                is_minimized=is_minimized,
                is_visible=not is_minimized and title.strip() != "",
                category=category,
                app_name=app_name,
            )
            windows.append(wi)

            if hwnd == foreground_hwnd:
                foreground_name = app_name

            return True

        win32gui.EnumWindows(enum_callback, 0)

        return Snapshot(
            ts=time.time(),
            windows=windows,
            foreground_app=foreground_name,
        )

    # ── history queries ──────────────────────────────────────────────

    def get_app_co_occurrence(self, window_sec: float = 3600.0) -> dict[str, list[str]]:
        """Return set of app_names active in the last window_sec seconds.
        Returns a mapping of snapshot_time -> app_name_list for clustering.
        """
        cutoff = time.time() - window_sec
        return self._dedup_by_window([r for r in self._records if r.ts >= cutoff])

    def get_record_count(self) -> int:
        return len(self._records)

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _exe_to_name(exe_path: str) -> str:
        """Extract a friendly name from an executable path."""
        import os
        name = os.path.basename(exe_path)
        name = os.path.splitext(name)[0]  # remove .exe
        # Common renames
        renames = {
            "chrome": "Chrome",
            "msedge": "Edge",
            "firefox": "Firefox",
            "code": "VS Code",
            "devenv": "Visual Studio",
        }
        lower = name.lower()
        return renames.get(lower, name.title())

    @staticmethod
    def _dedup_by_window(records: list[AppRecord], window_sec: float = 60.0):
        """Group records by time windows for co-occurrence analysis."""
        windows: dict[int, set[str]] = {}
        for r in records:
            bucket = int(r.ts / window_sec)
            if bucket not in windows:
                windows[bucket] = set()
            windows[bucket].add(r.app_name)
        return {k: sorted(v) for k, v in windows.items()}

    def update_app_map(self, app_class: str, category: str, app_name: str):
        """Allow runtime customization of app→category mapping."""
        self._app_map[app_class] = (category, app_name)
