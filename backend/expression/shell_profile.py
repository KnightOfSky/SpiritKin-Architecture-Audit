from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True)
class AvatarShellProfile:
    platform: str
    shell_type: str
    avatar_url: str
    avatar_3d_url: str
    live2d_url: str
    command_url: str
    events_ws_url: str
    window: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "shell_type": self.shell_type,
            "avatar_url": self.avatar_url,
            "avatar_3d_url": self.avatar_3d_url,
            "live2d_url": self.live2d_url,
            "command_url": self.command_url,
            "events_ws_url": self.events_ws_url,
            "window": dict(self.window),
            "capabilities": dict(self.capabilities),
        }


def _base_capabilities(platform: str) -> dict[str, bool]:
    return {
        "webview": True,
        "avatar3d_web": True,
        "runtime_events": True,
        "command_api": True,
        "transparent_window": platform == "desktop",
        "always_on_top": platform == "desktop",
        "mobile_safe_area": platform in {"android", "ios", "mobile"},
        "native_live2d_sdk_future": platform in {"android", "ios", "desktop"},
    }


def build_avatar_shell_profile(
    platform: str = "desktop",
    *,
    frontend_base_url: str = "http://127.0.0.1:8787",
    events_ws_url: str = "ws://127.0.0.1:8765",
    command_url: str = "http://127.0.0.1:8788/command",
    role: str = "spirit",
) -> AvatarShellProfile:
    platform = (platform or "desktop").strip().lower()
    frontend = frontend_base_url.rstrip("/")
    ws = quote(events_ws_url, safe="")
    cmd = quote(command_url, safe="")
    mobile = "1" if platform in {"android", "ios", "mobile"} else "0"
    avatar_url = f"{frontend}/spirit_avatar.html?ws={ws}&cmd={cmd}&mobile={mobile}"
    avatar_3d_url = f"{frontend}/avatar_3d.html?ws={ws}&cmd={cmd}&mobile={mobile}&config=models/spirit3d/manifest.json"
    live2d_url = f"{frontend}/live2d.html?ws={ws}&mobile={mobile}&role={quote(role)}&config=models/manifest.json&autoload=1"
    window = {
        "width": 420 if platform == "desktop" else 390,
        "height": 720 if platform == "desktop" else 780,
        "transparent": platform == "desktop",
        "always_on_top": platform == "desktop",
        "resizable": platform != "ios",
    }
    shell_type = "desktop_webview" if platform == "desktop" else f"{platform}_webview"
    return AvatarShellProfile(platform, shell_type, avatar_url, avatar_3d_url, live2d_url, command_url, events_ws_url, window, _base_capabilities(platform))


def build_multi_end_avatar_manifest(
    *,
    frontend_base_url: str = "http://127.0.0.1:8787",
    events_ws_url: str = "ws://127.0.0.1:8765",
    command_url: str = "http://127.0.0.1:8788/command",
) -> dict[str, Any]:
    profiles = [
        build_avatar_shell_profile("desktop", frontend_base_url=frontend_base_url, events_ws_url=events_ws_url, command_url=command_url),
        build_avatar_shell_profile("android", frontend_base_url=frontend_base_url, events_ws_url=events_ws_url, command_url=command_url),
        build_avatar_shell_profile("ios", frontend_base_url=frontend_base_url, events_ws_url=events_ws_url, command_url=command_url),
    ]
    return {"schema_version": "v1", "default": "desktop", "profiles": {p.platform: p.snapshot() for p in profiles}}
