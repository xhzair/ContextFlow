# ContextFlow

**Save your workspace. Restore it instantly. No cloud, no login, no nonsense.**

A Windows desktop tool that remembers which apps and windows you have open for each project. Hover the right edge of your screen, save your current setup, and switch between workspaces with one click.

## Features

- **🖱️ Hover to reveal** — Move your mouse to the right screen edge to open the workspace panel
- **💾 Save workspaces** — Name your workspace and pick which windows to include. Browser tab capture via CDP
- **⚡ One-click restore** — Click any saved workspace to launch apps and restore window positions
- **🔍 Auto-discovery** — Watches your app usage patterns and suggests workspace groupings (local AI naming via Agnes AI)
- **🔒 100% local** — All data in SQLite. No telemetry, no cloud, no account required
- **🎨 Activity-aware** — Workspace cards fade based on how recently you used them
- **📌 Desktop shortcuts** — Right-click any workspace to create a desktop shortcut for instant restore

## Installation

### Option A — Download (no Python required)

Download `ContextFlow.exe` from [Releases](https://github.com/yourname/contextflow/releases) and run it. That's it.

### Option B — Run from source

```bash
git clone https://github.com/yourname/contextflow.git
cd contextflow
pip install -r requirements.txt
python run.py
```

**Requirements:** Python 3.11+, Windows 10/11

### Option C — Build from source

```bash
pip install -r requirements.txt pyinstaller
python build.py --autostart   # Build .exe + add to startup
```

## Usage

| Action | How |
|--------|-----|
| Open panel | Hover mouse to right screen edge |
| Save workspace | Click "+ Save Current Windows", name it, pick windows |
| Restore workspace | Click any workspace card |
| Update workspace | Right-click card → "Update with current windows" |
| Delete workspace | Right-click card → "Delete" |
| Desktop shortcut | Right-click card → "Create Desktop Shortcut" |
| Hide/show panel | Click the ✕ in the panel header, or use tray icon |

### Browser tabs (optional)

Start Chrome/Edge with `--remote-debugging-port=9222` to enable browser tab capture and restore.

## Tech Stack

- **UI:** PySide6 (Qt6)
- **Window management:** pywin32 + psutil
- **Clustering:** scipy (Jaccard distance + hierarchical)
- **Storage:** SQLite (WAL mode)
- **AI naming:** Agnes AI API (optional fallback)
- **Packaging:** PyInstaller (single .exe)

## Why not cloud?

Your open windows, file paths, and browser tabs are sensitive information. ContextFlow never sends any data anywhere. Everything stays in a local SQLite database.

## License

MIT
