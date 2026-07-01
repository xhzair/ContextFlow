"""SQLite storage for workspaces, snapshots, co-occurrence data.

All data local. No cloud. No raw window titles stored.
"""

import sqlite3
import time
import json
from pathlib import Path


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS contexts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    color       TEXT DEFAULT '#4A90D9',
    icon        TEXT DEFAULT 'folder',
    created_at  REAL NOT NULL,
    updated_at  REAL,
    is_auto     INTEGER DEFAULT 0,
    confidence  REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id  INTEGER NOT NULL,
    ts          REAL NOT NULL,
    FOREIGN KEY (context_id) REFERENCES contexts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS snapshot_windows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    app_exe     TEXT NOT NULL,
    app_name    TEXT,
    app_class   TEXT,
    rect_left   INTEGER,
    rect_top    INTEGER,
    rect_width  INTEGER,
    rect_height INTEGER,
    is_minimized INTEGER DEFAULT 0,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS snapshot_tabs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    browser     TEXT NOT NULL,
    tab_title   TEXT,
    tab_url     TEXT NOT NULL,
    is_pinned   INTEGER DEFAULT 0,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS snapshot_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    editor      TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_co_occurrence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_a       TEXT NOT NULL,
    app_b       TEXT NOT NULL,
    co_count    INTEGER DEFAULT 1,
    a_alone     INTEGER DEFAULT 0,
    b_alone     INTEGER DEFAULT 0,
    last_seen   REAL,
    UNIQUE(app_a, app_b)
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_context ON snapshots(context_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_windows_snap ON snapshot_windows(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_tabs_snap ON snapshot_tabs(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_files_snap ON snapshot_files(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_co_occurrence_pair ON app_co_occurrence(app_a, app_b);
"""


class ContextFlowDB:
    """SQLite database for ContextFlow."""

    def __init__(self, db_path: str | Path = "contextflow.db"):
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(DB_SCHEMA)

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    # ── context CRUD ─────────────────────────────────────────────────

    def create_context(self, name: str, description: str = "",
                       color: str = "#4A90D9", is_auto: bool = False,
                       confidence: float = 1.0) -> int:
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO contexts (name, description, color, created_at, updated_at, is_auto, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, description, color, now, now, 1 if is_auto else 0, confidence)
        )
        self.conn.commit()
        return cur.lastrowid

    def update_context(self, context_id: int, **kwargs):
        valid = {"name", "description", "color", "icon", "updated_at", "confidence"}
        updates = {k: v for k, v in kwargs.items() if k in valid}
        if not updates:
            return
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [context_id]
        self.conn.execute(f"UPDATE contexts SET {set_clause} WHERE id=?", values)
        self.conn.commit()

    def delete_context(self, context_id: int):
        self.conn.execute("DELETE FROM contexts WHERE id=?", (context_id,))
        self.conn.commit()

    def get_context(self, context_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, name, description, color, icon, created_at, updated_at, is_auto, confidence "
            "FROM contexts WHERE id=?", (context_id,)
        ).fetchone()
        return self._context_row(row)

    def list_contexts(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, description, color, icon, created_at, updated_at, is_auto, confidence "
            "FROM contexts ORDER BY updated_at DESC"
        ).fetchall()
        return [self._context_row(r) for r in rows]

    @staticmethod
    def _context_row(row) -> dict | None:
        if row is None:
            return None
        return {
            "id": row[0], "name": row[1], "description": row[2],
            "color": row[3], "icon": row[4], "created_at": row[5],
            "updated_at": row[6], "is_auto": bool(row[7]),
            "confidence": row[8],
        }

    # ── snapshot CRUD ────────────────────────────────────────────────

    def save_snapshot(self, context_id: int, windows: list[dict],
                      tabs: list[dict] | None = None,
                      files: list[dict] | None = None) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO snapshots (context_id, ts) VALUES (?, ?)",
            (context_id, now)
        )
        snap_id = cur.lastrowid

        for w in windows:
            self.conn.execute(
                """INSERT INTO snapshot_windows
                (snapshot_id, app_exe, app_name, app_class, rect_left, rect_top, rect_width, rect_height, is_minimized)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snap_id, w.get("app_exe", ""), w.get("app_name", ""), w.get("app_class", ""),
                 w.get("rect_left", 0), w.get("rect_top", 0),
                 w.get("rect_width", 0), w.get("rect_height", 0),
                 1 if w.get("is_minimized") else 0)
            )

        if tabs:
            for t in tabs:
                self.conn.execute(
                    "INSERT INTO snapshot_tabs (snapshot_id, browser, tab_title, tab_url, is_pinned) VALUES (?, ?, ?, ?, ?)",
                    (snap_id, t["browser"], t.get("tab_title", ""), t["tab_url"], 1 if t.get("is_pinned") else 0)
                )

        if files:
            for f in files:
                self.conn.execute(
                    "INSERT INTO snapshot_files (snapshot_id, editor, file_path) VALUES (?, ?, ?)",
                    (snap_id, f["editor"], f["file_path"])
                )

        self.conn.execute(
            "UPDATE contexts SET updated_at=? WHERE id=?",
            (now, context_id)
        )
        self.conn.commit()
        return snap_id

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        snap = self.conn.execute(
            "SELECT id, context_id, ts FROM snapshots WHERE id=?", (snapshot_id,)
        ).fetchone()
        if not snap:
            return None

        windows = self.conn.execute(
            "SELECT app_exe, app_name, app_class, rect_left, rect_top, rect_width, rect_height, is_minimized "
            "FROM snapshot_windows WHERE snapshot_id=?", (snapshot_id,)
        ).fetchall()

        tabs = self.conn.execute(
            "SELECT browser, tab_title, tab_url, is_pinned FROM snapshot_tabs WHERE snapshot_id=?", (snapshot_id,)
        ).fetchall()

        files = self.conn.execute(
            "SELECT editor, file_path FROM snapshot_files WHERE snapshot_id=?", (snapshot_id,)
        ).fetchall()

        return {
            "id": snap[0], "context_id": snap[1], "ts": snap[2],
            "windows": [
                {"app_exe": w[0], "app_name": w[1], "app_class": w[2],
                 "rect_left": w[3], "rect_top": w[4],
                 "rect_width": w[5], "rect_height": w[6],
                 "is_minimized": bool(w[7])}
                for w in windows
            ],
            "tabs": [
                {"browser": t[0], "tab_title": t[1], "tab_url": t[2], "is_pinned": bool(t[3])}
                for t in tabs
            ],
            "files": [
                {"editor": f[0], "file_path": f[1]} for f in files
            ],
        }

    def get_latest_snapshot(self, context_id: int) -> dict | None:
        snap = self.conn.execute(
            "SELECT id FROM snapshots WHERE context_id=? ORDER BY ts DESC LIMIT 1",
            (context_id,)
        ).fetchone()
        if not snap:
            return None
        return self.get_snapshot(snap[0])

    # ── co-occurrence ────────────────────────────────────────────────

    def update_co_occurrence(self, app_set: list[str]):
        """Update the co-occurrence matrix with a new observation.
        app_set: list of app names currently open together.
        """
        now = time.time()
        for i, a in enumerate(app_set):
            for b in app_set[i+1:]:
                a, b = sorted([a, b])
                existing = self.conn.execute(
                    "SELECT id, co_count FROM app_co_occurrence WHERE app_a=? AND app_b=?",
                    (a, b)
                ).fetchone()
                if existing:
                    self.conn.execute(
                        "UPDATE app_co_occurrence SET co_count=co_count+1, last_seen=? WHERE id=?",
                        (now, existing[0])
                    )
                else:
                    self.conn.execute(
                        "INSERT INTO app_co_occurrence (app_a, app_b, co_count, last_seen) VALUES (?, ?, 1, ?)",
                        (a, b, now)
                    )
        self.conn.commit()

    def get_co_occurrence_matrix(self) -> list[dict]:
        """Return all pairs with co_count >= 2, for clustering."""
        rows = self.conn.execute(
            "SELECT app_a, app_b, co_count FROM app_co_occurrence WHERE co_count >= 2 ORDER BY co_count DESC"
        ).fetchall()
        return [{"app_a": r[0], "app_b": r[1], "co_count": r[2]} for r in rows]

    # ── settings ─────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        self.conn.commit()
