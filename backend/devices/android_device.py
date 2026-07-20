from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus


class AndroidDeviceBackend:
    name = "android_device"

    def __init__(self, device_id: str = "android_device", companion_registry: Any | None = None):
        self._device_id = device_id
        self._last_state: dict[str, Any] = {}
        self._installed_apps: list[dict[str, Any]] = []
        self._registry = companion_registry

    @property
    def device_id(self) -> str:
        return self._device_id

    def update_state(self, state: dict[str, Any]) -> None:
        self._last_state = dict(state)
        apps = state.get("installed_apps")
        if isinstance(apps, list):
            self._installed_apps = [dict(app) if isinstance(app, dict) else {"name": str(app)} for app in apps]
        if self._registry is not None:
            self._registry.update_heartbeat({"device_id": self._device_id, "device_state": self._last_state, "installed_apps": self._installed_apps})

    def device_status(self) -> dict[str, Any]:
        if self._registry is not None:
            return {"permission": "current_user", **self._registry.device_status(self._device_id)}
        return {"device_id": self._device_id, "permission": "current_user", **self._last_state}

    def list_installed_apps(self, limit: int = 50) -> dict[str, Any]:
        if self._registry is not None:
            return {"permission": "current_user", **self._registry.list_installed_apps(self._device_id, limit=limit)}
        return {"device_id": self._device_id, "permission": "current_user", "apps": self._installed_apps[: max(1, int(limit))], "summary": "应用清单来自 Android Companion 上报"}

    def get_screen_size(self) -> dict[str, Any]:
        size = self._last_state.get("screen_size")
        if isinstance(size, dict):
            width = int(size.get("width") or 0)
            height = int(size.get("height") or 0)
        elif isinstance(size, (list, tuple)) and len(size) >= 2:
            width, height = int(size[0] or 0), int(size[1] or 0)
        else:
            width = int(self._last_state.get("screen_width") or 0)
            height = int(self._last_state.get("screen_height") or 0)
        return {"device_id": self._device_id, "width": width, "height": height, "source": "android_companion_heartbeat"}

    def move_to(self, x: int, y: int) -> dict[str, Any]:
        return self._unsupported("move_to", "Android touch input has no persistent pointer")

    def click(self, x: int, y: int) -> dict[str, Any]:
        return self._enqueue("accessibility.tap", {"x": int(x), "y": int(y)})

    def double_click(self, x: int, y: int) -> dict[str, Any]:
        return self._enqueue("accessibility.tap", {"x": int(x), "y": int(y), "tap_count": 2})

    def extract_text(self, region=None, lang: str = "chi_sim+eng") -> str:
        text = str(self._last_state.get("screen_text") or self._last_state.get("accessibility_text") or "").strip()
        if text:
            return text
        queued = self._enqueue("android.ui_snapshot", {"region": region, "language": lang, "purpose": "extract_text"})
        return str(queued.get("summary") or "Android UI snapshot queued for text extraction")

    def understand_screen(self, query: str, region=None) -> str:
        summary = str(self._last_state.get("screen_summary") or "").strip()
        if summary:
            return summary
        queued = self._enqueue("android.ui_snapshot", {"query": query, "region": region, "purpose": "understand_screen"})
        return str(queued.get("summary") or "Android UI snapshot queued for screen understanding")

    def capture_screen(self, output_path: str | None = None) -> dict[str, Any]:
        return self._enqueue("android.screenshot.capture", {"output_path": output_path or ""})

    def read_clipboard(self) -> dict[str, Any]:
        if "clipboard_text" not in self._last_state:
            return self._unsupported("read_clipboard", "Android Companion does not expose clipboard reads")
        return {"device_id": self._device_id, "text": str(self._last_state.get("clipboard_text") or ""), "source": "android_companion_heartbeat"}

    def write_clipboard(self, text: str) -> dict[str, Any]:
        return self._enqueue("clipboard.write", {"text": str(text)})

    def open_url(self, url: str) -> dict[str, Any]:
        normalized = str(url or "").strip()
        if normalized and "://" not in normalized:
            normalized = f"https://{normalized}"
        return self._enqueue("url.open", {"url": normalized})

    def search_web(self, query: str, engine: str = "bing") -> dict[str, Any]:
        hosts = {"google": "https://www.google.com/search?q=", "bing": "https://www.bing.com/search?q="}
        selected = str(engine or "bing").strip().lower()
        result = self.open_url(hosts.get(selected, hosts["bing"]) + quote_plus(str(query or "")))
        return {**result, "query": query, "engine": selected}

    def list_windows(self, limit: int = 40) -> list[dict[str, Any]]:
        tasks = self._last_state.get("recent_tasks") or self._last_state.get("running_apps") or []
        if not isinstance(tasks, list):
            return []
        return [dict(item) if isinstance(item, dict) else {"title": str(item)} for item in tasks[: max(1, int(limit))]]

    def activate_window(self, title: str) -> dict[str, Any]:
        return self.launch_app(title)

    def close_window(self, title: str, force: bool = False) -> dict[str, Any]:
        return self.close_app(title, force=force)

    def search_files(self, query: str, root: str | None = None, limit: int = 20) -> dict[str, Any]:
        return self._unsupported("search_files", "Android scoped storage search is not exposed by the Companion")

    def read_file_text(self, path: str, max_chars: int = 4000) -> dict[str, Any]:
        return self._unsupported("read_file_text", "Android scoped storage reads require an explicit artifact grant")

    def open_file(self, path: str) -> dict[str, Any]:
        return self._unsupported("open_file", "Use artifact.download followed by an explicit Android share/open action")

    def type_text(self, text: str) -> dict[str, Any]:
        return self._unsupported("type_text", "Generic text injection is disabled; use an approved workflow-specific Accessibility action")

    def press_key(self, key: str) -> dict[str, Any]:
        return self._unsupported("press_key", "Generic Android key injection is not exposed by the Companion")

    def hotkey(self, *keys: str) -> dict[str, Any]:
        return self._unsupported("hotkey", "Desktop hotkeys do not map to Android touch navigation")

    def launch_app(self, app_name: str) -> dict[str, Any]:
        return self._enqueue("app.launch", {"app_name": app_name})

    def close_app(self, app_name: str, force: bool = False) -> dict[str, Any]:
        return self._enqueue("app.close", {"app_name": app_name, "force": bool(force)})

    def list_hardware_devices(self, limit: int = 80) -> list[dict[str, Any]]:
        hardware = self._last_state.get("hardware_devices") or self._last_state.get("sensors") or []
        if not isinstance(hardware, list):
            return []
        return [dict(item) if isinstance(item, dict) else {"name": str(item)} for item in hardware[: max(1, int(limit))]]

    def push_notification(self, title: str, body: str) -> dict[str, Any]:
        return self._enqueue("notification.push", {"title": title, "body": body})

    def _enqueue(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._registry is not None:
            return self._registry.enqueue_command(self._device_id, operation, dict(params))
        return {
            "device_id": self._device_id,
            "operation": operation,
            "params": dict(params),
            "queued": True,
            "summary": f"已入队 Android 操作 {operation}，等待 Companion 执行",
        }

    def _unsupported(self, operation: str, reason: str) -> dict[str, Any]:
        return {"device_id": self._device_id, "operation": operation, "supported": False, "queued": False, "reason": reason}
