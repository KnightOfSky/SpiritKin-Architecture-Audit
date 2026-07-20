"""ADB fallback capture for Android UI diagnostics.

This script is a desktop-side fallback for cases where the Android APK cannot
capture raw pixels without a MediaProjection permission flow. It captures:

- foreground package/window hints from dumpsys
- a PNG screenshot through `adb exec-out screencap -p`
- a UI XML dump through `uiautomator dump`

The result is stored as a normal control-plane Artifact so the existing
management, cleanup, and audit paths can consume it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.control_plane_store import DEFAULT_WORKSPACE_ID, ControlPlaneStore
except ModuleNotFoundError:
    from control_plane_store import DEFAULT_WORKSPACE_ID, ControlPlaneStore


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE_XML = "/sdcard/spiritkin-ui.xml"
MAX_CAPTURE_BYTES = 25 * 1024 * 1024


@dataclass
class AdbCapture:
    device_id: str
    foreground_package: str
    foreground_activity: str
    window_focus: str
    screenshot_png: bytes
    ui_xml: str


def find_adb(explicit: str = "") -> str:
    candidates = [
        explicit,
        os.environ.get("ADB", ""),
        str(ROOT / "tools" / "android-sdk" / "platform-tools" / "adb.exe"),
        str(ROOT / "tools" / "android-sdk" / "platform-tools" / "adb"),
        shutil.which("adb") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    raise FileNotFoundError("adb not found; set ADB or install Android platform-tools")


def adb_cmd(adb: str, serial: str = "") -> list[str]:
    command = [adb]
    if serial:
        command.extend(["-s", serial])
    return command


def run_text(command: Sequence[str], *, timeout: int = 15) -> str:
    completed = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        timeout=timeout,
    )
    return completed.stdout.decode("utf-8", errors="replace")


def run_bytes(command: Sequence[str], *, timeout: int = 20) -> bytes:
    completed = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        timeout=timeout,
    )
    if len(completed.stdout) > MAX_CAPTURE_BYTES:
        raise ValueError("ADB capture output is too large")
    return completed.stdout


def capture_adb(adb: str, *, serial: str = "", remote_xml: str = DEFAULT_REMOTE_XML) -> AdbCapture:
    base = adb_cmd(adb, serial)
    device_id = run_text([*base, "shell", "getprop", "ro.serialno"]).strip() or serial or "adb-device"
    window_text = run_text([*base, "shell", "dumpsys", "window", "windows"], timeout=20)
    foreground_package, foreground_activity, window_focus = parse_foreground(window_text)
    screenshot = run_bytes([*base, "exec-out", "screencap", "-p"], timeout=30)
    if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("ADB screencap did not return a PNG")
    run_text([*base, "shell", "uiautomator", "dump", remote_xml], timeout=30)
    ui_xml = run_text([*base, "shell", "cat", remote_xml], timeout=20)
    if "<hierarchy" not in ui_xml:
        raise ValueError("ADB UI dump did not return uiautomator XML")
    return AdbCapture(
        device_id=device_id,
        foreground_package=foreground_package,
        foreground_activity=foreground_activity,
        window_focus=window_focus,
        screenshot_png=screenshot,
        ui_xml=ui_xml,
    )


def parse_foreground(window_text: str) -> tuple[str, str, str]:
    text = window_text or ""
    focus = ""
    for pattern in (
        r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^}]+)\}",
        r"mFocusedApp=ActivityRecord\{[^ ]+ [^ ]+ ([^ ]+) [^}]+\}",
        r"mInputMethodTarget=Window\{[^ ]+ [^ ]+ ([^}]+)\}",
    ):
        match = re.search(pattern, text)
        if match:
            focus = match.group(1)
            break
    focus = focus.strip()
    package_name = ""
    activity = ""
    if "/" in focus:
        package_name, activity = focus.split("/", 1)
    elif focus:
        package_name = focus.split()[0]
    return package_name, activity, focus


def capture_to_artifact(
    capture: AdbCapture,
    *,
    store: ControlPlaneStore,
    workspace_id: str,
    source: str = "adb_fallback",
    client: str = "local-adb",
) -> dict[str, object]:
    metadata_text = json.dumps(
        {
            "device_id": capture.device_id,
            "foreground_package": capture.foreground_package,
            "foreground_activity": capture.foreground_activity,
            "window_focus": capture.window_focus,
        },
        ensure_ascii=False,
        indent=2,
    )
    return store.record_artifact(
        {
            "workspace_id": workspace_id,
            "source": source,
            "device_id": capture.device_id,
            "purpose": "android_adb_diagnostic",
            "tags": ["android", "adb", "screenshot", "ui_snapshot"],
            "files": [
                {
                    "name": "adb-screenshot.png",
                    "mime_type": "image/png",
                    "base64": base64.b64encode(capture.screenshot_png).decode("ascii"),
                },
                {
                    "name": "adb-ui.xml",
                    "mime_type": "application/xml",
                    "text": capture.ui_xml,
                },
                {
                    "name": "adb-foreground.json",
                    "mime_type": "application/json",
                    "text": metadata_text,
                },
            ],
        },
        client=client,
        default_source=source,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture Android screenshot/UI XML through ADB and store it as a control-plane Artifact.")
    parser.add_argument("--adb", default="", help="Path to adb. Defaults to ADB env, bundled SDK path, then PATH.")
    parser.add_argument("--serial", default="", help="ADB serial for multi-device setups.")
    parser.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    parser.add_argument("--state-dir", default="", help="Override control-plane state dir.")
    parser.add_argument("--remote-xml", default=DEFAULT_REMOTE_XML)
    parser.add_argument("--dry-run", action="store_true", help="Capture and print metadata without writing control-plane state.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    adb = find_adb(args.adb)
    capture = capture_adb(adb, serial=args.serial, remote_xml=args.remote_xml)
    summary = {
        "device_id": capture.device_id,
        "foreground_package": capture.foreground_package,
        "foreground_activity": capture.foreground_activity,
        "window_focus": capture.window_focus,
        "screenshot_bytes": len(capture.screenshot_png),
        "ui_xml_chars": len(capture.ui_xml),
    }
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "capture": summary}, ensure_ascii=False, indent=2))
        return 0
    store = ControlPlaneStore(args.state_dir or None)
    artifact = capture_to_artifact(capture, store=store, workspace_id=args.workspace_id)
    print(json.dumps({"ok": True, "capture": summary, "artifact": artifact}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
