"""Build script: packages ContextFlow into a single .exe via PyInstaller.

Usage:
    python build.py              # Build executable
    python build.py --install    # Build + install native host + register
    python build.py --autostart  # Build + add to Windows startup
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
MAIN_SCRIPT = PROJECT_ROOT / "run.py"
APP_NAME = "ContextFlow"


def clean():
    """Clean previous build artifacts."""
    for d in [DIST_DIR, BUILD_DIR]:
        if d.exists():
            shutil.rmtree(d)
    for spec in PROJECT_ROOT.glob("*.spec"):
        spec.unlink()
    print("[OK] Cleaned build artifacts")


def build():
    """Build executable with PyInstaller."""
    clean()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onefile",
        "--noconsole",
        "--windowed",
        "--add-data", f"{PROJECT_ROOT / 'contextflow'};contextflow",
        str(MAIN_SCRIPT),
    ]

    print(f"Building {APP_NAME}...")
    subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))

    exe = DIST_DIR / f"{APP_NAME}.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        print(f"[OK] Built: {exe} ({size_mb:.1f} MB)")
    else:
        print("[FAIL] Build failed")
        return None
    return exe


def install_autostart(exe_path: Path):
    """Add to Windows startup via registry."""
    import winreg
    key = winreg.HKEY_CURRENT_USER
    subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(key, subkey, 0, winreg.KEY_SET_VALUE) as reg:
            winreg.SetValueEx(reg, APP_NAME, 0, winreg.REG_SZ, str(exe_path))
        print(f"[OK] Added to startup: {exe_path}")
    except Exception as e:
        print(f"[FAIL] Failed to add autostart: {e}")


def install_native_host():
    """Install the native messaging host for browser extension."""
    script = PROJECT_ROOT / "contextflow" / "extension" / "native_host.py"
    subprocess.run([sys.executable, str(script), "install", "chrome"], check=True)
    subprocess.run([sys.executable, str(script), "install", "edge"], check=True)
    print("\nNative host installed. Now load the extension:")
    print("  1. Open chrome://extensions/")
    print("  2. Enable 'Developer mode'")
    print("  3. Click 'Load unpacked'")
    print(f"  4. Select: {PROJECT_ROOT / 'contextflow' / 'extension'}")


if __name__ == "__main__":
    exe = build()
    if exe is None:
        sys.exit(1)

    if "--install" in sys.argv:
        install_native_host()

    if "--autostart" in sys.argv:
        install_autostart(exe)
        print("ContextFlow will start automatically on next login.")

    print(f"\nDone! Run: {exe}")
