"""Edge sidebar — Android style. Hover-triggered, auto-slide.
In-panel save form (no dialogs).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QApplication, QMenu, QCheckBox, QLineEdit, QStackedWidget,
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect, Signal, QTimer, QPoint
from PySide6.QtGui import QFont, QColor, QPainter, QBrush, QPen, QAction, QLinearGradient, QPainterPath


HANDLE_W = 32
HANDLE_H = 280
PANEL_W = 300


# ── handle ─────────────────────────────────────────────────────────

class _Handle(QWidget):
    panel_requested = Signal()
    hide_requested = Signal()

    def __init__(self, screen_geom: QRect):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        x = screen_geom.right() - HANDLE_W
        y = (screen_geom.height() - HANDLE_H) // 2
        self.setGeometry(QRect(x, y, HANDLE_W, HANDLE_H))
        self._accent = QColor("#4A90D9")
        self._hovered = False
        self._panel_visible = False
        self._panel_ref = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_mouse)
        self._poll_timer.setInterval(150)

    def set_accent(self, color: QColor):
        self._accent = color; self.update()

    def set_panel_visible(self, visible: bool):
        self._panel_visible = visible; self.update()

    def set_panel_ref(self, panel):
        self._panel_ref = panel

    def reset(self):
        self._poll_timer.stop()
        self._hovered = False
        self._panel_visible = False
        self.update()

    def enterEvent(self, event):
        self._hovered = True; self.update()
        if not self._panel_visible:
            self.panel_requested.emit()

    def leaveEvent(self, event):
        self._hovered = False; self.update()
        if self._panel_visible:
            self._poll_timer.start(200)

    def _poll_mouse(self):
        if self._panel_ref is None:
            self._poll_timer.stop(); return
        pos = self.cursor().pos()
        in_handle = self.geometry().contains(self.mapFromGlobal(pos))
        in_panel = self._panel_ref.geometry().contains(pos)
        if not in_handle and not in_panel:
            self._poll_timer.stop()
            self.hide_requested.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height(); cx = HANDLE_W // 2
        if self._hovered:
            glow = QColor(self._accent); glow.setAlpha(40)
            p.fillRect(0, 0, HANDLE_W, h, glow)
        alpha = 220 if self._hovered else 55
        c = QColor(self._accent); c.setAlpha(alpha)
        pen_w = 3 if self._hovered else 1.5
        p.setPen(QPen(c, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, int(h * 0.12), cx, int(h * 0.88))
        p.end()


# ── panel ──────────────────────────────────────────────────────────

class EdgeSidebar(QWidget):
    switch_requested = Signal(int)
    save_requested = Signal()                    # save form data
    save_form_submitted = Signal(str, list)      # name, selected windows
    save_form_cancelled = Signal()
    delete_requested = Signal(int)
    update_requested = Signal()
    rename_requested = Signal(int, str)
    shortcut_requested = Signal(int)
    suggestion_accepted = Signal(dict)
    suggestion_dismissed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        screen = self.screen() or QApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        self.hidden_x = geom.right()
        self.visible_x = geom.right() - PANEL_W
        self.setGeometry(QRect(self.hidden_x, geom.top(), PANEL_W, geom.height()))
        self._anim = None
        self._workspaces: list[dict] = []
        self._suggestions: list[dict] = []
        self._accent_color = QColor("#4A90D9")
        self._build_ui()

    # ── glass paint ──

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(18, 20, 26, 242)
        p.setBrush(QBrush(bg)); p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        r = self.rect()
        path.moveTo(r.right(), r.top())
        path.lineTo(r.left() + 10, r.top())
        path.quadTo(r.left(), r.top(), r.left(), r.top() + 10)
        path.lineTo(r.left(), r.bottom() - 10)
        path.quadTo(r.left(), r.bottom(), r.left() + 10, r.bottom())
        path.lineTo(r.right(), r.bottom()); path.closeSubpath()
        p.drawPath(path)
        highlight = QLinearGradient(0, 0, 4, 0)
        highlight.setColorAt(0, QColor(255, 255, 255, 25))
        highlight.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(highlight))
        p.drawRect(0, 10, 4, self.height() - 20)
        p.end()

    # ── build ──

    def _build_ui(self):
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        # Fixed header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(22, 18, 18, 12)
        title = QLabel("Workspaces")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        title.setStyleSheet("color:#E0E0E8;background:transparent;border:none;")
        hdr.addWidget(title); hdr.addStretch()
        self._update_btn = QPushButton("Update")
        self._update_btn.setFont(QFont("Microsoft YaHei", 8))
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.setStyleSheet("QPushButton{color:#888;background:rgba(255,255,255,0.05);border:1px solid #444;border-radius:5px;padding:3px 10px;}QPushButton:hover{color:#DDD;background:rgba(255,255,255,0.1);}")
        self._update_btn.clicked.connect(self.update_requested.emit)
        self._update_btn.setVisible(False)
        hdr.addWidget(self._update_btn)
        outer.addLayout(hdr)

        # Current
        curr_w = QHBoxLayout()
        curr_w.setContentsMargins(22, 0, 18, 4)
        self._current_label = QLabel()
        self._current_label.setFont(QFont("Microsoft YaHei", 9))
        self._current_label.setStyleSheet("color:#888;background:transparent;border:none;")
        self._current_label.setVisible(False)
        curr_w.addWidget(self._current_label)
        outer.addLayout(curr_w)

        # List container (always in layout, toggled visible)
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background:transparent;")
        lpl = QVBoxLayout(self._list_container)
        lpl.setContentsMargins(22, 0, 18, 18); lpl.setSpacing(8)
        self._add_list_content(lpl)
        outer.addWidget(self._list_container, 1)

        # Save container (hidden until save is triggered)
        self._save_container = QWidget()
        self._save_container.setStyleSheet("background:transparent;")
        self._save_container.setVisible(False)
        spl = QVBoxLayout(self._save_container)
        spl.setContentsMargins(22, 0, 18, 18); spl.setSpacing(8)
        self._add_save_content(spl)
        outer.addWidget(self._save_container, 1)

    def _add_list_content(self, layout):
        # Suggestions
        self._suggestions_header = QLabel()
        self._suggestions_header.setFont(QFont("Microsoft YaHei", 8, QFont.Weight.Bold))
        self._suggestions_header.setStyleSheet("color:#F5A623;background:transparent;border:none;")
        self._suggestions_header.setVisible(False)
        layout.addWidget(self._suggestions_header)
        self._suggestions_container = QWidget()
        self._suggestions_container.setStyleSheet("background:transparent;")
        self._suggestions_layout = QVBoxLayout(self._suggestions_container)
        self._suggestions_layout.setContentsMargins(0, 0, 0, 0); self._suggestions_layout.setSpacing(4)
        self._suggestions_container.setVisible(False)
        layout.addWidget(self._suggestions_container)

        # Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}QScrollBar:vertical{background:transparent;width:4px;}QScrollBar::handle:vertical{background:rgba(255,255,255,0.08);border-radius:2px;min-height:30px;}QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self._card_container = QWidget()
        self._card_container.setStyleSheet("background:transparent;")
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setContentsMargins(0, 0, 0, 0); self._card_layout.setSpacing(6)
        scroll.setWidget(self._card_container)
        layout.addWidget(scroll, 1)

        # Save button
        save_btn = QPushButton("+ Save Current Windows")
        save_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("QPushButton{background-color:#3A7BC8;color:white;border:none;border-radius:8px;padding:10px;}QPushButton:hover{background-color:#4A8BD8;}")
        save_btn.clicked.connect(self.save_requested.emit)
        layout.addWidget(save_btn)

    def _add_save_content(self, layout):
        # Name input
        nl = QLabel("Workspace name")
        nl.setStyleSheet("color:#CCC;background:transparent;border:none;")
        layout.addWidget(nl)
        self._save_name_input = QLineEdit()
        self._save_name_input.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self._save_name_input.setStyleSheet("QLineEdit{background:rgba(255,255,255,0.05);color:#EEE;border:1px solid rgba(255,255,255,0.10);border-radius:8px;padding:10px;}QLineEdit:focus{border:1px solid rgba(74,144,217,0.5);}")
        layout.addWidget(self._save_name_input)

        # Window count
        self._save_win_label = QLabel()
        self._save_win_label.setStyleSheet("color:#888;background:transparent;border:none;")
        layout.addWidget(self._save_win_label)

        # Window list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._save_check_container = QWidget()
        self._save_check_layout = QVBoxLayout(self._save_check_container)
        self._save_check_layout.setContentsMargins(0, 0, 0, 0); self._save_check_layout.setSpacing(1)
        scroll.setWidget(self._save_check_container)
        layout.addWidget(scroll, 1)

        # All/None
        sel_row = QHBoxLayout(); sel_row.addStretch()
        ab = QPushButton("All"); nb = QPushButton("None")
        for b in (ab, nb):
            b.setFont(QFont("Microsoft YaHei", 8))
            b.setStyleSheet("QPushButton{color:#888;background:transparent;border:1px solid #444;border-radius:4px;padding:2px 10px;}QPushButton:hover{color:#DDD;}")
        ab.clicked.connect(lambda: [c.setChecked(True) for c in self._save_checkboxes])
        nb.clicked.connect(lambda: [c.setChecked(False) for c in self._save_checkboxes])
        sel_row.addWidget(ab); sel_row.addWidget(nb)
        layout.addLayout(sel_row)

        # Buttons
        br = QHBoxLayout(); br.addStretch()
        cb = QPushButton("Cancel")
        cb.setStyleSheet("QPushButton{color:#999;background:transparent;border:1px solid #444;border-radius:6px;padding:6px 20px;}QPushButton:hover{color:#DDD;}")
        cb.clicked.connect(self._on_save_cancel)
        ok = QPushButton("Save")
        ok.setStyleSheet("QPushButton{background-color:#3A7BC8;color:white;border:none;border-radius:6px;padding:6px 24px;}QPushButton:hover{background-color:#4A8BD8;}")
        ok.clicked.connect(self._on_save_confirm)
        br.addWidget(cb); br.addWidget(ok)
        layout.addLayout(br)

        self._save_checkboxes: list[QCheckBox] = []
        self._save_windows_data: list[dict] = []

    # ── save mode ──

    def enter_save_mode_loading(self):
        """Switch to save page with loading placeholder."""
        self._save_name_input.setText("")
        self._save_win_label.setText("Capturing windows...")
        while self._save_check_layout.count():
            item = self._save_check_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._save_checkboxes.clear()
        self._list_container.setVisible(False)
        self._save_container.setVisible(True)
        self.update()

    def enter_save_mode(self, name: str, windows: list[dict], titles: list[str]):
        """Populate save form with captured data."""
        self._save_windows_data = windows
        self._save_name_input.setText(name)
        self._save_win_label.setText(f"Windows ({len(windows)} found)")
        while self._save_check_layout.count():
            item = self._save_check_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._save_checkboxes.clear()
        for i, w in enumerate(windows):
            app = w.get("app_name", "Unknown")
            t = titles[i] if i < len(titles) else app
            rw = w.get("rect_width", 0); rh = w.get("rect_height", 0)
            sz = f"  [{rw}x{rh}]" if rw and rh else ""
            lbl = f"{app}"
            if t and t != app: lbl += f"  —  {t}"
            lbl += sz
            cb = QCheckBox(lbl)
            cb.setChecked(True)
            cb.setFont(QFont("Microsoft YaHei", 9))
            cb.setStyleSheet("QCheckBox{color:#DDD;background:transparent;spacing:8px;}")
            self._save_check_layout.addWidget(cb)
            self._save_checkboxes.append(cb)
        self._save_check_layout.addStretch()
        self._list_container.setVisible(False)
        self._save_container.setVisible(True)
        self.update()

    def _on_save_cancel(self):
        self.exit_save_mode()
        self.save_form_cancelled.emit()

    def _on_save_confirm(self):
        selected = [self._save_windows_data[i] for i, cb in enumerate(self._save_checkboxes) if cb.isChecked()]
        name = self._save_name_input.text().strip()
        # Hide save form IMMEDIATELY, before emitting
        self.exit_save_mode()
        QApplication.processEvents()
        self.save_form_submitted.emit(name, selected)

    def show_saving(self):
        """Show saving indicator in the current workspace label area."""
        self._save_anim_state = 0
        self._current_label.setText("Saving...")
        self._current_label.setStyleSheet("color:#4A90D9;background:transparent;border:none;")
        self._current_label.setVisible(True)
        if not hasattr(self, '_save_anim_timer'):
            self._save_anim_timer = QTimer(self)
            self._save_anim_timer.timeout.connect(self._tick_saving)
        self._save_anim_timer.start(300)

    def _tick_saving(self):
        dots = [".", "..", "..."]
        self._save_anim_state = (self._save_anim_state + 1) % 3
        self._current_label.setText(f"Saving{dots[self._save_anim_state]}")

    def hide_saving(self):
        if hasattr(self, '_save_anim_timer'):
            self._save_anim_timer.stop()
        self._current_label.setVisible(False)

    def exit_save_mode(self):
        """Return to workspace list."""
        self._save_container.setVisible(False)
        self._list_container.setVisible(True)
        self.update()
        """Return to workspace list."""
        self._save_container.setVisible(False)
        self._list_container.setVisible(True)
        self.update()

    # ── slide ──

    def slide_in(self):
        self._animate_to(self.visible_x)

    def slide_out(self):
        self._animate_to(self.hidden_x)

    def _animate_to(self, target_x: int):
        if self._anim and self._anim.state() == QPropertyAnimation.State.Running:
            self._anim.stop()
        r = self.geometry()
        start = QRect(r.x(), r.y(), r.width(), r.height())
        end = QRect(target_x, r.y(), r.width(), r.height())
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(250)
        self._anim.setStartValue(start); self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    # ── list cards ──

    def set_workspaces(self, workspaces: list[dict]):
        self._workspaces = workspaces
        self._rebuild_cards()

    def set_suggestions(self, suggestions: list[dict]):
        self._suggestions = suggestions
        while self._suggestions_layout.count():
            item = self._suggestions_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        if not suggestions:
            self._suggestions_header.setVisible(False)
            self._suggestions_container.setVisible(False)
            return
        self._suggestions_header.setText(f"★  {len(suggestions)} suggestion(s)")
        self._suggestions_header.setVisible(True)
        self._suggestions_container.setVisible(True)
        for s in suggestions:
            card = self._make_suggestion_card(s)
            self._suggestions_layout.addWidget(card)

    def _rebuild_cards(self):
        while self._card_layout.count():
            item = self._card_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        if not self._workspaces:
            # Empty state
            empty = QLabel("No workspaces yet\n\nClick + Save Current Windows\nto save your first workspace")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setFont(QFont("Microsoft YaHei", 10))
            empty.setStyleSheet("color:#666;background:transparent;border:none;padding:20px;")
            empty.setWordWrap(True)
            self._card_layout.addWidget(empty)
        else:
            for ws in self._workspaces:
                self._card_layout.addWidget(self._make_card(ws))
        self._card_layout.addStretch()

    def _make_card(self, ws: dict) -> QWidget:
        card = QFrame()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setMinimumHeight(56)
        color = ws.get("color", "#4A90D9")
        name = ws.get("name", "Unnamed")
        is_auto = ws.get("is_auto", False)
        desc = ws.get("description", "")
        last_used = ws.get("updated_at", 0)
        import time; h_ago = (time.time() - last_used) / 3600 if last_used else 999
        activity = max(0.2, 1.0 - h_ago / 168)
        bg_a = int(0.10 + activity * 0.10); bd_a = int(0.08 + activity * 0.07)
        card.setStyleSheet(f"QFrame{{background-color:rgba(255,255,255,{bg_a/255:.3f});border:1px solid rgba(255,255,255,{bd_a/255:.3f});border-radius:8px;}}QFrame:hover{{background-color:rgba(255,255,255,{min(0.25,bg_a/255+0.08):.3f});}}")
        l = QHBoxLayout(card); l.setContentsMargins(12, 8, 10, 8); l.setSpacing(8)
        dot_a = int(150 + activity * 105); dot_s = int(6 + activity * 5)
        dot = QLabel(); dot.setFixedSize(dot_s, dot_s)
        qc = QColor(color)
        dot.setStyleSheet(f"background-color:rgba({qc.red()},{qc.green()},{qc.blue()},{dot_a});border-radius:{dot_s//2}px;border:none;")
        l.addWidget(dot)
        tc = QVBoxLayout(); tc.setSpacing(1)
        nl = QLabel(name); nl.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.DemiBold))
        nl.setStyleSheet("color:#DDD;background:transparent;border:none;"); nl.setWordWrap(True); nl.setMaximumWidth(180)
        tc.addWidget(nl)
        if desc:
            dl = QLabel(desc); dl.setFont(QFont("Microsoft YaHei", 8))
            dl.setStyleSheet("color:#777;background:transparent;border:none;"); dl.setWordWrap(True); dl.setMaximumWidth(180)
            tc.addWidget(dl)
        l.addLayout(tc, 1)
        if is_auto:
            badge = QLabel("Auto"); badge.setFont(QFont("Microsoft YaHei", 7, QFont.Weight.Bold))
            badge.setStyleSheet(f"color:{color};background:transparent;border:1px solid {color}66;border-radius:5px;padding:1px 5px;")
            l.addWidget(badge)
        l.addStretch(0)
        arrow = QLabel("\u25b8"); arrow.setStyleSheet("color:#555;background:transparent;border:none;"); l.addWidget(arrow)
        ws_id = ws["id"]
        card.mousePressEvent = self._make_click_handler(ws_id, card)
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(lambda pos, cid=ws_id: self._show_context_menu(pos, cid, card))
        return card

    def _make_suggestion_card(self, s: dict) -> QWidget:
        card = QFrame(); card.setMinimumHeight(64)
        name = s.get("suggested_name", "Suggestion")
        apps = " \u00b7 ".join(s.get("apps", [])[:3])
        conf = s.get("confidence", 1.0)
        card.setStyleSheet("QFrame{background-color:rgba(245,165,35,0.10);border:1px solid rgba(245,165,35,0.20);border-radius:10px;}QFrame:hover{background-color:rgba(245,165,35,0.16);}")
        l = QHBoxLayout(card); l.setContentsMargins(14, 10, 10, 10); l.setSpacing(10)
        spark = QLabel("\u2726"); spark.setFont(QFont("Segoe UI Emoji", 18))
        spark.setStyleSheet("color:#E8B84B;background:transparent;border:none;"); l.addWidget(spark)
        info = QVBoxLayout(); info.setSpacing(1)
        nl = QLabel(name); nl.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.DemiBold))
        nl.setStyleSheet("color:#E8D5A3;background:transparent;border:none;"); nl.setWordWrap(True); nl.setMaximumWidth(170)
        info.addWidget(nl)
        sl = QLabel(f"{apps}  \u00b7  {conf:.0%} confidence")
        sl.setFont(QFont("Microsoft YaHei", 8)); sl.setStyleSheet("color:#8A8060;background:transparent;border:none;")
        sl.setWordWrap(True); sl.setMaximumWidth(170); info.addWidget(sl)
        l.addLayout(info, 1); l.addStretch(0)
        accept = QPushButton("Add"); accept.setFixedWidth(42)
        accept.setFont(QFont("Microsoft YaHei", 8, QFont.Weight.Bold))
        accept.setCursor(Qt.CursorShape.PointingHandCursor)
        accept.setStyleSheet("QPushButton{color:#78B8A6;background:rgba(100,180,140,0.1);border:1px solid rgba(100,180,140,0.2);border-radius:6px;padding:4px 8px;}QPushButton:hover{background:rgba(100,180,140,0.25);}")
        accept.clicked.connect(lambda: self.suggestion_accepted.emit(s)); l.addWidget(accept)
        return card

    def _make_click_handler(self, ws_id: int, card: QFrame):
        def handler(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.switch_requested.emit(ws_id)
            elif event.button() == Qt.MouseButton.RightButton:
                self._show_context_menu(event.pos(), ws_id, card)
        return handler

    def _show_context_menu(self, pos: QPoint, ws_id: int, parent_widget: QWidget):
        menu = QMenu(parent_widget)
        menu.setStyleSheet("QMenu{background:#353840;color:#DDD;border:1px solid #555;border-radius:6px;padding:4px;}QMenu::item{padding:6px 20px;border-radius:4px;}QMenu::item:selected{background:#4A4D58;}")
        a = QAction("Switch to this workspace", menu); a.triggered.connect(lambda: self.switch_requested.emit(ws_id)); menu.addAction(a)
        menu.addSeparator()
        a = QAction("Rename", menu); a.triggered.connect(lambda: self.rename_requested.emit(ws_id, "")); menu.addAction(a)
        a = QAction("Update with current windows", menu); a.triggered.connect(self.update_requested.emit); menu.addAction(a)
        menu.addSeparator()
        a = QAction("Delete", menu); a.triggered.connect(lambda: self.delete_requested.emit(ws_id)); menu.addAction(a)
        menu.addSeparator()
        a = QAction("Create Desktop Shortcut", menu); a.triggered.connect(lambda: self.shortcut_requested.emit(ws_id)); menu.addAction(a)
        menu.exec(parent_widget.mapToGlobal(pos))

    def set_current_workspace(self, name: str, color: str = "#4A90D9"):
        self._accent_color = QColor(color)
        if name:
            self._current_label.setText(f"\u25cf  Current: {name}")
            self._current_label.setStyleSheet(f"color:{color};background:transparent;border:none;")
            self._current_label.setVisible(True); self._update_btn.setVisible(True)
        else:
            self._current_label.setVisible(False); self._update_btn.setVisible(False)

    def set_current_none(self):
        self._current_label.setVisible(False); self._update_btn.setVisible(False)


_handle_instance: _Handle | None = None

def get_handle() -> _Handle:
    global _handle_instance
    if _handle_instance is None:
        screen = QApplication.primaryScreen()
        geom = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        _handle_instance = _Handle(geom)
    return _handle_instance
