"""ContextFlow main application — tray icon, sidebar, workspace management.

Wires together: AppWatcher, SnapshotEngine, ContextFlowDB, MiniSidebar, system tray.
"""

import sys
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QInputDialog, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox, QLabel,
    QScrollArea, QWidget, QFrame, QDialogButtonBox,
)
from PySide6.QtGui import QAction, QPixmap, QPainter, QColor, QIcon, QFont
from PySide6.QtCore import QTimer, Qt, QRect

from contextflow.watcher.app_watcher import AppWatcher, OWN_CLASSES
from contextflow.engine.snapshot import SnapshotEngine
from contextflow.engine.discovery import DiscoveryEngine
from contextflow.storage.db import ContextFlowDB
from contextflow.ui.sidebar import EdgeSidebar, get_handle


# ── tray icon color palette ───────────────────────────────────────
TRAY_COLOR = QColor("#4A90D9")


class ContextFlowApp:
    """Main application controller."""

    def __init__(self, restore_name: str | None = None):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self._restore_on_start = restore_name

        # Data dir
        self._data_dir = Path(__file__).resolve().parent.parent / "data"
        self._data_dir.mkdir(exist_ok=True)

        # Core components
        self.app_watcher = AppWatcher()
        self.snapshot_engine = SnapshotEngine(self.app_watcher)
        self.db = ContextFlowDB(self._data_dir / "contextflow.db")
        self.discovery = DiscoveryEngine(self.db)

        # UI
        self.sidebar = EdgeSidebar()
        self.tray: QSystemTrayIcon | None = None

        # State
        self._current_context_id: int | None = None
        self._current_context_name: str = ""
        self._current_context_color: str = "#999"

        # Timers
        self._poll_timer = QTimer()
        self._discovery_timer = QTimer()
        self._discovery_check_timer = QTimer()

        # Startup
        self.db.connect()
        self._setup_tray()
        self._setup_sidebar()
        self._start_timers()

        # Register our own windows so we don't capture ourselves
        OWN_CLASSES.add(self.sidebar.metaObject().className())

        # CLI restore mode
        if self._restore_on_start:
            QTimer.singleShot(500, self._restore_by_name)
            QTimer.singleShot(5000, self._quit)

    # ── tray ────────────────────────────────────────────────────────

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self.app)
        self.tray.setIcon(self._make_tray_icon(TRAY_COLOR))
        self.tray.setToolTip("ContextFlow — Idle")
        self._rebuild_tray_menu()
        self.tray.show()

    def _make_tray_icon(self, color: QColor) -> QIcon:
        # Try to use AI-generated icon first
        icon_path = Path(__file__).resolve().parent / "assets" / "icon_v0.png"
        if icon_path.exists():
            return QIcon(str(icon_path))
        # Fallback: drawn colored square
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
        painter.end()
        return QIcon(pixmap)

    def _rebuild_tray_menu(self):
        if self.tray is None:
            return

        menu = QMenu()

        # Save workspace
        save_action = QAction("Save New Workspace", self.app)
        save_action.triggered.connect(self._save_workspace)
        menu.addAction(save_action)

        # Update current workspace (if active)
        if self._current_context_id is not None:
            update_action = QAction(f"Update '{self._current_context_name}'", self.app)
            update_action.triggered.connect(self._update_workspace)
            menu.addAction(update_action)

        menu.addSeparator()

        # Switch to workspace
        switch_menu = QMenu("Switch To", menu)
        contexts = self.db.list_contexts()
        if contexts:
            for ctx in contexts:
                name = ctx["name"]
                if ctx["is_auto"]:
                    name = f"★ {name}"
                action = QAction(name, self.app)
                ctx_id = ctx["id"]

                def make_handler(cid):
                    return lambda: self._switch_workspace(cid)

                action.triggered.connect(make_handler(ctx_id))
                switch_menu.addAction(action)
        else:
            no_ws = QAction("(no saved workspaces)", self.app)
            no_ws.setEnabled(False)
            switch_menu.addAction(no_ws)
        menu.addMenu(switch_menu)

        # Delete workspace
        if contexts:
            delete_menu = QMenu("Delete", menu)
            for ctx in contexts:
                action = QAction(f"{ctx['name']}", self.app)
                ctx_id = ctx["id"]

                def make_del_handler(cid):
                    return lambda: self._delete_workspace(cid)

                action.triggered.connect(make_del_handler(ctx_id))
                delete_menu.addAction(action)
            menu.addMenu(delete_menu)

        menu.addSeparator()

        # Show sidebar if hidden
        if self.sidebar.x() >= self.sidebar.hidden_x - 10:
            show_action = QAction("Show Sidebar", self.app)
            show_action.triggered.connect(self.sidebar.slide_in)
            menu.addAction(show_action)

        menu.addSeparator()

        quit_action = QAction("Quit", self.app)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

    # ── sidebar ─────────────────────────────────────────────────────

    def _setup_sidebar(self):
        self.sidebar.switch_requested.connect(self._switch_workspace)
        self.sidebar.save_requested.connect(self._save_workspace)
        self.sidebar.save_form_submitted.connect(self._on_save_form_done)
        self.sidebar.save_form_cancelled.connect(lambda: None)
        self.sidebar.update_requested.connect(self._update_workspace)
        self.sidebar.delete_requested.connect(self._delete_workspace)
        self.sidebar.rename_requested.connect(self._rename_workspace)
        self.sidebar.shortcut_requested.connect(self._create_desktop_shortcut)
        self.sidebar.suggestion_accepted.connect(self._accept_suggestion)
        self.sidebar.suggestion_dismissed.connect(self._dismiss_suggestion)
        self._refresh_sidebar_workspaces()

        # Create handle widget
        self._handle = get_handle()
        self._handle.panel_requested.connect(self._on_panel_show)
        self._handle.hide_requested.connect(self._on_panel_hide)
        self._handle.set_panel_ref(self.sidebar)  # for mouse polling
        self._handle.show()

        self.sidebar.show()
        self.sidebar.slide_out()

        # ── first-run check ──
        if self.db.get_setting("first_run_done") != "1":
            QTimer.singleShot(500, self._show_first_run_wizard)

    def _on_panel_show(self):
        """Hover trigger — force panel to visible position."""
        r = self.sidebar.geometry()
        self.sidebar.setGeometry(
            QRect(self.sidebar.visible_x, r.y(), r.width(), r.height())
        )
        self.sidebar.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.sidebar.show()
        self.sidebar.raise_()
        self.sidebar.activateWindow()
        self._handle.set_panel_visible(True)

    def _on_panel_hide(self):
        """Mouse left — force panel off-screen."""
        r = self.sidebar.geometry()
        self.sidebar.setGeometry(
            QRect(self.sidebar.hidden_x, r.y(), r.width(), r.height())
        )
        self._handle.set_panel_visible(False)
        self._handle._poll_timer.stop()

    def _refresh_sidebar_workspaces(self):
        self.sidebar.set_workspaces(self.db.list_contexts())

    # ── workspace CRUD ──────────────────────────────────────────────

    def _dark_input(self, title: str, label: str, default: str = "") -> tuple[str, bool]:
        """Dark-styled text input dialog. Returns (text, ok)."""
        dlg = QDialog(self.sidebar)
        dlg.setWindowTitle(title)
        dlg.setFixedSize(400, 180)
        dlg.setStyleSheet("QDialog{background-color:#1E1F24;border:1px solid #2E3038;border-radius:12px;}")
        dlg.setFont(QFont("Microsoft YaHei", 10))
        lyt = QVBoxLayout(dlg)
        lyt.setContentsMargins(28, 24, 28, 20)
        lbl = QLabel(label)
        lbl.setStyleSheet("color:#CCC;background:transparent;border:none;")
        lyt.addWidget(lbl)
        from PySide6.QtWidgets import QLineEdit
        inp = QLineEdit(default)
        inp.setFont(QFont("Microsoft YaHei", 11))
        inp.setStyleSheet("QLineEdit{background:#2A2D35;color:#EEE;border:1px solid #3A3D45;border-radius:6px;padding:8px;}")
        lyt.addWidget(inp)
        lyt.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet("QPushButton{color:#999;background:transparent;border:1px solid #444;border-radius:6px;padding:6px 16px;} QPushButton:hover{color:#DDD;}")
        cancel.clicked.connect(dlg.reject)
        ok = QPushButton("OK")
        ok.setStyleSheet("QPushButton{background-color:#3A7BC8;color:white;border:none;border-radius:6px;padding:6px 20px;} QPushButton:hover{background-color:#4A8BD8;}")
        ok.clicked.connect(dlg.accept)
        ok.setDefault(True)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lyt.addLayout(btn_row)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return (inp.text(), True)
        return ("", False)

    def _dark_confirm(self, title: str, message: str, yes_label: str = "Yes", no_label: str = "No") -> bool:
        """Dark-styled confirmation dialog. Returns True if yes."""
        dlg = QDialog(self.sidebar)
        dlg.setWindowTitle(title)
        dlg.setFixedSize(400, 160)
        dlg.setStyleSheet("QDialog{background-color:#1E1F24;border:1px solid #2E3038;border-radius:12px;}")
        dlg.setFont(QFont("Microsoft YaHei", 10))
        lyt = QVBoxLayout(dlg)
        lyt.setContentsMargins(28, 24, 28, 20)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#CCC;background:transparent;border:none;")
        lyt.addWidget(lbl)
        lyt.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        no = QPushButton(no_label)
        no.setStyleSheet("QPushButton{color:#999;background:transparent;border:1px solid #444;border-radius:6px;padding:6px 16px;} QPushButton:hover{color:#DDD;}")
        no.clicked.connect(dlg.reject)
        yes = QPushButton(yes_label)
        yes.setStyleSheet("QPushButton{background-color:#3A7BC8;color:white;border:none;border-radius:6px;padding:6px 20px;} QPushButton:hover{background-color:#4A8BD8;}")
        yes.clicked.connect(dlg.accept)
        yes.setDefault(True)
        btn_row.addWidget(no)
        btn_row.addWidget(yes)
        lyt.addLayout(btn_row)
        return dlg.exec() == QDialog.DialogCode.Accepted

    def _pick_windows(self, all_windows: list[dict], window_infos=None) -> list[dict] | None:
        """Show a dialog letting the user pick which windows to save.

        Args:
            all_windows: dicts from windows_to_dicts (no title)
            window_infos: original WindowInfo objects (with title, for display)

        Returns the selected subset, or None if cancelled.
        """
        if not all_windows:
            self._dark_confirm("Empty Snapshot", "No visible windows detected. Open some apps first.", "OK", "")
            return None

        # Build display labels: use titles from window_infos if available
        titles = []
        if window_infos and len(window_infos) == len(all_windows):
            for wi in window_infos:
                t = wi.title.strip()
                titles.append(t if t else f"({wi.app_name})")
        else:
            titles = [w.get("app_name", "Unknown") for w in all_windows]

        dlg = QDialog(self.sidebar)
        dlg.setWindowTitle("Select Windows to Save")
        dlg.setMinimumSize(560, 400)
        dlg.setStyleSheet("QDialog{background-color:#1E1F24;border:1px solid #2E3038;border-radius:12px;}")
        dlg.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        header = QLabel(f"Found {len(all_windows)} windows. Check the ones to include.")
        header.setStyleSheet("color:#CCC;background:transparent;border:none;")
        layout.addWidget(header)

        # Scrollable checkbox list
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        container = QWidget()
        checks_layout = QVBoxLayout(container)
        checks_layout.setSpacing(2)

        checkboxes: list[QCheckBox] = []
        for i, w in enumerate(all_windows):
            app_name = w.get("app_name", "Unknown")
            title = titles[i]
            rect_w = w.get("rect_width", 0)
            rect_h = w.get("rect_height", 0)
            size_hint = f"{rect_w}×{rect_h}" if rect_w and rect_h else ""

            # Display: app icon-ish name + window title + size
            label = f"{app_name}"
            if title and title != app_name:
                label += f"  —  {title}"
            if size_hint:
                label += f"  [{size_hint}]"

            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setFont(QFont("Microsoft YaHei", 9))
            exe = w.get("app_exe", "")
            if exe:
                cb.setToolTip(exe)
            cb.setStyleSheet("QCheckBox{color:#DDD;background:transparent;spacing:8px;}")
            checks_layout.addWidget(cb)
            checkboxes.append(cb)

        checks_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Select/All None buttons
        btn_row = QHBoxLayout()
        all_btn = QPushButton("Select All")
        none_btn = QPushButton("Select None")
        for btn in (all_btn, none_btn):
            btn.setFont(QFont("Microsoft YaHei", 9))
            btn.setStyleSheet("QPushButton{color:#AAA;background:rgba(255,255,255,0.05);border:1px solid #444;border-radius:5px;padding:4px 12px;} QPushButton:hover{color:#DDD;background:rgba(255,255,255,0.1);}")
        all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in checkboxes])
        none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # OK / Cancel — replace QDialogButtonBox with styled buttons
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("QPushButton{color:#999;background:transparent;border:1px solid #444;border-radius:6px;padding:6px 20px;} QPushButton:hover{color:#DDD;}")
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = QPushButton("Save")
        ok_btn.setStyleSheet("QPushButton{background-color:#3A7BC8;color:white;border:none;border-radius:6px;padding:6px 24px;} QPushButton:hover{background-color:#4A8BD8;}")
        ok_btn.clicked.connect(dlg.accept)
        ok_btn.setDefault(True)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        # Return selected windows
        selected = [all_windows[i] for i, cb in enumerate(checkboxes) if cb.isChecked()]
        return selected

    def _save_workspace(self):
        """Show save form immediately with loading, then capture async."""
        self.sidebar.enter_save_mode_loading()
        QApplication.processEvents()
        QTimer.singleShot(20, self._do_capture_and_fill)

    def _on_save_form_done(self, name: str, windows: list[dict]):
        try:
            if not name.strip() or not windows:
                return
            name = name.strip()
            self.sidebar.show_saving()
            QApplication.processEvents()
            # DB save
            existing = None
            for c in self.db.list_contexts():
                if c["name"].lower() == name.lower():
                    existing = c; break
            import random
            colors = ["#4A90D9", "#E74C3C", "#27AE60", "#F39C12", "#9B59B6", "#1ABC9C"]
            tabs = self.snapshot_engine.capture_tabs()
            if existing:
                ctx_id = existing["id"]; color = existing["color"]
                self.db.save_snapshot(ctx_id, windows, tabs=tabs if tabs else None)
                self.db.update_context(ctx_id, name=name)
            else:
                color = random.choice(colors)
                ctx_id = self.db.create_context(name=name, description=f"Saved {len(windows)} windows", color=color)
                self.db.save_snapshot(ctx_id, windows, tabs=tabs if tabs else None)
            self.sidebar.hide_saving()
            self._activate_workspace(ctx_id, name, color)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.sidebar.hide_saving()
            if self.tray:
                self.tray.showMessage("Error", str(e), QSystemTrayIcon.MessageIcon.Critical, 5000)

    def _do_capture_and_fill(self):
        """Capture windows and fill the save form."""
        snapshot = self.snapshot_engine.capture()
        all_windows = self.snapshot_engine.windows_to_dicts(snapshot.windows)
        titles = [wi.title.strip() if wi.title.strip() else f"({wi.app_name})" for wi in snapshot.windows]
        self.sidebar.enter_save_mode("My Workspace", all_windows, titles)

    def _update_workspace(self):
        """Re-save current windows to the active workspace."""
        if self._current_context_id is None:
            self._save_workspace()
            return

        ctx_id = self._current_context_id
        snapshot = self.snapshot_engine.capture()
        all_windows = self.snapshot_engine.windows_to_dicts(snapshot.windows)

        windows = self._pick_windows(all_windows, snapshot.windows)
        if windows is None:
            return
        if not windows:
            self._dark_confirm("No Windows Selected", "At least one window must be selected.", "OK", "")
            return

        self.db.save_snapshot(ctx_id, windows, tabs=self.snapshot_engine.capture_tabs())
        self._refresh_sidebar_workspaces()

        if self.tray:
            self.tray.showMessage(
                "Workspace Updated",
                f"'{self._current_context_name}' updated with {len(windows)} windows",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )

    def _delete_workspace(self, context_id: int):
        """Delete a workspace (with confirmation)."""
        ctx = self.db.get_context(context_id)
        if ctx is None:
            return

        if not self._dark_confirm("Delete Workspace",
                f"Delete workspace '{ctx['name']}'?\n"
                f"This will not close any open apps.",
                "Delete", "Cancel"):
            return

        self.db.delete_context(context_id)

        if self._current_context_id == context_id:
            self._current_context_id = None
            self._current_context_name = ""
            self._current_context_color = "#999"
            self.sidebar.set_current_none()
            if self.tray:
                self.tray.setToolTip("ContextFlow — Idle")
                self.tray.setIcon(self._make_tray_icon(TRAY_COLOR))

        self._refresh_sidebar_workspaces()
        self._rebuild_tray_menu()

    def _rename_workspace(self, context_id: int, _unused: str = ""):
        ctx = self.db.get_context(context_id)
        if ctx is None:
            return
        name, ok = self._dark_input("Rename Workspace", "New name:", ctx["name"])
        if not ok or not name.strip():
            return
        self.db.update_context(context_id, name=name.strip())
        if self._current_context_id == context_id:
            self._current_context_name = name.strip()
            self.sidebar.set_current_workspace(name.strip(), ctx["color"])
        self._refresh_sidebar_workspaces()
        self._rebuild_tray_menu()

    def _accept_suggestion(self, suggestion: dict):
        name = suggestion.get("suggested_name", "New Workspace")
        name, ok = self._dark_input("Name This Workspace", "Workspace name:", name)
        if not ok or not name.strip():
            return
        self.db.create_context(name=name.strip(), description="Auto-discovered: " + ", ".join(suggestion.get("apps", [])[:4]), color=suggestion.get("color", "#F5A623"), is_auto=True, confidence=suggestion.get("confidence", 1.0))
        self._refresh_sidebar_workspaces()
        self._rebuild_tray_menu()
        self._dismiss_suggestion(suggestion)

    def _dismiss_suggestion(self, suggestion: dict):
        self._pending_suggestions = [s for s in getattr(self, '_pending_suggestions', []) if s.get("suggested_name") != suggestion.get("suggested_name")]
        self.sidebar.set_suggestions(self._pending_suggestions)

    def _restore_by_name(self):
        """Restore workspace by name (for CLI/desktop shortcuts)."""
        for ctx in self.db.list_contexts():
            if ctx["name"].lower() == self._restore_on_start.lower():
                self._switch_workspace(ctx["id"])
                return

    def _create_desktop_shortcut(self, context_id: int):
        """Create a desktop shortcut that restores a specific workspace."""
        ctx = self.db.get_context(context_id)
        if ctx is None:
            return

        name = ctx["name"].replace("/", "-").replace("\\", "-")
        desktop = Path.home() / "Desktop"
        shortcut_path = desktop / f"{name}.lnk"

        # Detect if running as packaged exe or dev
        is_packaged = getattr(sys, 'frozen', False)
        if is_packaged:
            target = sys.executable
            args = f'--restore "{ctx["name"]}"'
            icon = sys.executable
        else:
            dist_exe = Path(__file__).resolve().parent.parent / "dist" / "ContextFlow.exe"
            if dist_exe.exists():
                target = str(dist_exe)
                args = f'--restore "{ctx["name"]}"'
                icon = str(dist_exe)
            else:
                target = str(Path(sys.executable).with_name("pythonw.exe"))
                args = f'"{Path(__file__).resolve().parent.parent / "run.py"}" --restore "{ctx["name"]}"'
                icon = target

        from win32com.client import Dispatch
        shell = Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(shortcut_path))
        sc.TargetPath = target
        sc.Arguments = args
        sc.WorkingDirectory = str(Path(__file__).resolve().parent.parent)
        sc.Description = f"Restore workspace: {ctx['name']}"
        sc.IconLocation = f"{icon},0"
        sc.Save()

        QMessageBox.information(self.sidebar, "Shortcut Created",
            f"Desktop shortcut created: {name}.lnk\n"
            f"Double-click to restore '{ctx['name']}' instantly.")

    def _show_first_run_wizard(self):
        dlg = QDialog(self.sidebar)
        dlg.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        dlg.setFixedSize(500, 480)
        dlg.setFont(QFont("Microsoft YaHei", 10))

        card = QFrame(dlg)
        card.setGeometry(0, 0, 500, 480)
        card.setStyleSheet("QFrame{background-color:rgba(30,31,36,0.98);border:1px solid rgba(255,255,255,0.08);border-radius:16px;}")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(0)

        # Icon
        icon = QLabel("\U0001F9E9")
        icon.setFont(QFont("Segoe UI Emoji", 32))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background:transparent;border:none;")
        layout.addWidget(icon)
        layout.addSpacing(12)

        # Title
        title = QLabel("ContextFlow")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color:#F0F0F4;background:transparent;border:none;")
        layout.addWidget(title)
        layout.addSpacing(4)
        subtitle = QLabel("Your workspace, one click away")
        subtitle.setFont(QFont("Microsoft YaHei", 10))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color:#888;background:transparent;border:none;")
        layout.addWidget(subtitle)
        layout.addSpacing(22)

        # Steps — each a row with icon + text
        for num, head, desc in [
            ("\u2794", "Hover right edge of screen", "A colored line appears \u2014 that's your launcher"),
            ("\u2794", "Save your open windows", "Name the workspace, pick which apps to include"),
            ("\u2794", "Click to restore anytime", "All your apps and windows come back instantly"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(12)
            n = QLabel(num)
            n.setFont(QFont("Segoe UI Emoji", 14))
            n.setFixedWidth(24)
            n.setStyleSheet("color:#4A90D9;background:transparent;border:none;")
            row.addWidget(n)
            col = QVBoxLayout()
            col.setSpacing(1)
            h = QLabel(head)
            h.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.DemiBold))
            h.setStyleSheet("color:#D0D0D8;background:transparent;border:none;")
            h.setWordWrap(True)
            col.addWidget(h)
            d = QLabel(desc)
            d.setFont(QFont("Microsoft YaHei", 8))
            d.setStyleSheet("color:#777;background:transparent;border:none;")
            d.setWordWrap(True)
            col.addWidget(d)
            row.addLayout(col, 1)
            layout.addLayout(row)
            layout.addSpacing(14)

        layout.addStretch()

        # Bottom hint
        hint = QLabel("Tip: Start Chrome with --remote-debugging-port=9222 to capture browser tabs")
        hint.setFont(QFont("Microsoft YaHei", 8))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555;background:transparent;border:none;")
        layout.addWidget(hint)
        layout.addSpacing(14)

        btn = QPushButton("Get Started")
        btn.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(42)
        btn.setStyleSheet("QPushButton{background-color:#3A7BC8;color:white;border:none;border-radius:10px;} QPushButton:hover{background-color:#4A8BD8;}")
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn)

        dlg.exec()
        self.db.set_setting("first_run_done", "1")

    def _activate_workspace(self, ctx_id: int, name: str, color: str):
        """Update UI state to reflect an active workspace."""
        self._current_context_id = ctx_id
        self._current_context_name = name
        self._current_context_color = color
        self.sidebar.set_current_workspace(name, color)
        self._handle.set_accent(QColor(color))
        self._refresh_sidebar_workspaces()
        self._rebuild_tray_menu()

        if self.tray:
            self.tray.setToolTip(f"ContextFlow — {name}")
            self.tray.setIcon(self._make_tray_icon(QColor(color)))

    def _switch_workspace(self, context_id: int):
        """Restore a saved workspace."""
        ctx = self.db.get_context(context_id)
        if ctx is None:
            return

        snap = self.db.get_latest_snapshot(context_id)
        if snap is None:
            QMessageBox.warning(
                self.sidebar, "No Snapshot",
                f"No snapshot found for '{ctx['name']}'. Save it again."
            )
            return

        # Capture current state first (for comparison during restore)
        current_snapshot = self.snapshot_engine.capture()

        # Restore
        result = self.snapshot_engine.restore(
            windows=snap["windows"],
            tabs=snap.get("tabs"),
            current_windows=current_snapshot.windows,
        )

        self._activate_workspace(context_id, ctx["name"], ctx["color"])
        self._handle.reset()
        self.sidebar.slide_out()
        QApplication.processEvents()

        # Show warnings if any
        if result.warnings:
            QMessageBox.information(
                self.sidebar, "Workspace Restored",
                f"Restored '{ctx['name']}'\n"
                + f"Launched: {len(result.launched)} app(s)\n"
                + f"Repositioned: {len(result.repositioned)} window(s)\n"
                + (f"Note: {'; '.join(result.warnings)}" if result.warnings else "")
            )

    # ── timers ──────────────────────────────────────────────────────

    def _start_timers(self):
        # Poll foreground window every 1s (for co-occurrence data)
        self._poll_timer.timeout.connect(self._poll_loop)
        self._poll_timer.start(1000)

        # Discovery snapshot every 30s
        self._discovery_timer.timeout.connect(self._discovery_loop)
        self._discovery_timer.start(30000)

        # Cluster analysis every 5 minutes
        self._discovery_check_timer.timeout.connect(self._discovery_check)
        self._discovery_check_timer.start(300000)
        # Run first check after 60s delay
        QTimer.singleShot(60000, self._discovery_check)

    def _poll_loop(self):
        self.app_watcher.poll()

    def _discovery_loop(self):
        """Periodic snapshot for auto-discovery data collection."""
        snapshot = self.snapshot_engine.capture()
        app_names = list({w.app_name for w in snapshot.windows if w.app_name != "Unknown"})
        if len(app_names) >= 2:
            self.db.update_co_occurrence(app_names)

    def _discovery_check(self):
        """Run cluster analysis and feed suggestions to sidebar."""
        if self.discovery.needs_data():
            return

        suggestions = self.discovery.analyze()
        if not suggestions:
            return

        # Filter out already-saved workspace names
        existing_names = {c["name"].lower() for c in self.db.list_contexts()}
        new_suggestions = []
        for s in suggestions:
            if s.suggested_name.lower() not in existing_names:
                new_suggestions.append({
                    "suggested_name": s.suggested_name,
                    "apps": s.apps,
                    "confidence": s.confidence,
                    "color": s.color,
                })

        if not new_suggestions:
            return

        # Merge with any previously pending suggestions (deduplicate)
        existing_pending = {p["suggested_name"] for p in getattr(self, '_pending_suggestions', [])}
        for s in new_suggestions:
            if s["suggested_name"] not in existing_pending:
                self._pending_suggestions = getattr(self, '_pending_suggestions', []) + [s]

        self.sidebar.set_suggestions(self._pending_suggestions)

    # ── lifecycle ───────────────────────────────────────────────────

    def _quit(self):
        self._poll_timer.stop()
        self._discovery_timer.stop()
        self._discovery_check_timer.stop()
        self.sidebar.close()
        self.db.close()
        self.app.quit()

    def run(self):
        return self.app.exec()


def main(restore_name: str | None = None):
    app = ContextFlowApp(restore_name=restore_name)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
