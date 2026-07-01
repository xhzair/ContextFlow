"""Native Messaging host for ContextFlow browser extension.

This script is launched by Chrome/Edge as a subprocess.
It reads extension messages from stdin and writes tab data to a local JSON file.
The desktop app reads this file to get browser tabs.

Installation: Run `python native_host.py install` to register with the browser.
"""

import sys
import json
import os
import struct
import time
from pathlib import Path


# Native Messaging protocol:
# - Messages are JSON-encoded with a 4-byte little-endian length prefix
# - Read from stdin, write to stdout
# - stderr goes nowhere useful (browsers don't show it)

DATA_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
DATA_DIR = DATA_DIR / "ContextFlow"
TABS_FILE = "browser_tabs.json"
HOST_NAME = "com.contextflow.bridge"


def read_message() -> dict | None:
    """Read one native-messaging message from stdin."""
    try:
        raw = sys.stdin.buffer.read(4)
        if not raw or len(raw) < 4:
            return None
        msg_len = struct.unpack("<I", raw)[0]
        if msg_len > 1024 * 1024:  # 1 MB max
            return None
        data = sys.stdin.buffer.read(msg_len)
        return json.loads(data.decode("utf-8"))
    except (EOFError, struct.error, json.JSONDecodeError):
        return None


def write_message(msg: dict):
    """Write a native-messaging message to stdout."""
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def save_tabs(tabs_data: dict):
    """Save tab data to local JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / TABS_FILE

    # Keep only the latest tabs from each browser
    # (If multiple browsers use the same host, distinguish by extension ID)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(tabs_data, f, ensure_ascii=False, indent=2)


def install_manifest(browser: str = "chrome"):
    """Install the native messaging host manifest for the given browser.

    Chrome: Registry key under HKCU
    Edge: Registry key under HKCU
    """
    import winreg

    # Determine the native host script path (this file)
    native_host_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    # Chrome manifest path
    chrome_key = r"SOFTWARE\Google\Chrome\NativeMessagingHosts\com.contextflow.bridge"
    edge_key = r"SOFTWARE\Microsoft\Edge\NativeMessagingHosts\com.contextflow.bridge"

    keys = {
        "chrome": chrome_key,
        "edge": edge_key,
    }

    key_path = keys.get(browser)
    if not key_path:
        print(f"Unknown browser: {browser}")
        return

    # Determine the extension ID
    # For now, use a placeholder — user needs to replace after loading the extension
    extension_id = os.environ.get("CONTEXTFLOW_EXT_ID", "TO_BE_REPLACED")

    manifest = {
        "name": HOST_NAME,
        "description": "ContextFlow browser tab bridge",
        "path": native_host_path,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }

    # Write manifest to a location where the browser can find it
    manifest_dir = DATA_DIR / "native_host"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "native_host_manifest.json"

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Register in Windows registry
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        winreg.CloseKey(key)
        print(f"✓ Native messaging host registered for {browser}")
        print(f"  Manifest: {manifest_path}")
    except Exception as e:
        print(f"✗ Failed to register for {browser}: {e}")


def main():
    # Check if this is an install command
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        browser = sys.argv[2] if len(sys.argv) > 2 else "chrome"
        install_manifest(browser)
        return

    # Normal operation: listen for extension messages
    while True:
        msg = read_message()
        if msg is None:
            break

        msg_type = msg.get("type", "")

        if msg_type == "tabs_update":
            save_tabs(msg)
            write_message({"status": "ok", "tabs_count": len(msg.get("tabs", []))})
        else:
            write_message({"status": "error", "message": f"Unknown message type: {msg_type}"})


if __name__ == "__main__":
    main()
