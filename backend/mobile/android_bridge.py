from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend.executors.base import ExecutionRequest, ExecutionResult
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.mobile.android_permissions import enforce_android_command_permission
from backend.security.safety_control import evaluate_execution_safety


@dataclass(frozen=True)
class AndroidDeviceSpec:
    device_id: str
    model: str = ""
    android_version: str = ""
    capabilities: tuple[str, ...] = ()
    notification_token: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AndroidDeviceState:
    device_id: str
    battery_pct: float = 0.0
    charging: bool = False
    wifi_connected: bool = False
    screen_on: bool = False
    last_heartbeat: float = field(default_factory=time.time)
    notifications_enabled: bool = False
    current_app: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AndroidCommandTranslator:

    @staticmethod
    def device_state_to_heartbeat(state: AndroidDeviceState) -> dict[str, Any]:
        return {
            "device_id": state.device_id,
            "battery_pct": state.battery_pct,
            "charging": state.charging,
            "wifi_connected": state.wifi_connected,
            "screen_on": state.screen_on,
            "last_heartbeat": state.last_heartbeat,
            "notifications_enabled": state.notifications_enabled,
            "current_app": state.current_app,
            "metadata": state.metadata,
        }


class AndroidCompanionRegistry:
    def __init__(self, store: AndroidCompanionStore | None = None):
        self._states: dict[str, dict[str, Any]] = {}
        self._apps: dict[str, list[dict[str, Any]]] = {}
        self._commands: dict[str, list[dict[str, Any]]] = {}
        self._store = store

    def update_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._store is not None:
            return self._store.update_heartbeat(payload)
        device_id = str(payload.get("device_id") or "android_device")
        state = dict(payload.get("device_state") or payload)
        state["device_id"] = device_id
        state["last_heartbeat"] = float(state.get("last_heartbeat") or time.time())
        self._states[device_id] = state
        apps = payload.get("installed_apps") or state.get("installed_apps")
        if isinstance(apps, list):
            self._apps[device_id] = [dict(app) if isinstance(app, dict) else {"name": str(app)} for app in apps]
        return self.device_status(device_id)

    def device_status(self, device_id: str = "android_device") -> dict[str, Any]:
        if self._store is not None:
            return self._store.device_status(device_id)
        state = dict(self._states.get(device_id) or {"device_id": device_id})
        state.setdefault("online", bool(device_id in self._states))
        state["installed_app_count"] = len(self._apps.get(device_id, []))
        state["pending_command_count"] = len(self._commands.get(device_id, []))
        return state

    def list_installed_apps(self, device_id: str = "android_device", *, limit: int = 50) -> dict[str, Any]:
        if self._store is not None:
            return self._store.list_installed_apps(device_id, limit=limit)
        apps = self._apps.get(device_id, [])[: max(1, int(limit))]
        return {"device_id": device_id, "apps": apps, "count": len(apps)}

    def enqueue_command(self, device_id: str, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._store is not None:
            return self._store.enqueue_command(device_id, operation, params)
        permission_decision = enforce_android_command_permission(operation, dict(params or {}))
        if not permission_decision.get("allowed"):
            return {
                "device_id": device_id or "android_device",
                "queued": False,
                "error_code": str(permission_decision.get("error_code") or "android_permission_denied"),
                "message": str(permission_decision.get("message") or "Android command permission denied."),
                "permission": permission_decision.get("permission"),
                "permission_policy": permission_decision.get("policy"),
            }
        safety = evaluate_execution_safety(
            target="android_bridge",
            operation=operation,
            actor=str((params or {}).get("actor") or "android_bridge"),
            read_only=bool((permission_decision.get("permission") or {}).get("read_only")),
            dry_run=bool((params or {}).get("dry_run")),
        )
        if not safety.allowed:
            return {
                "device_id": device_id or "android_device",
                "queued": False,
                "error_code": safety.error_code,
                "message": safety.message,
                "safety": safety.snapshot(),
            }
        command = {"operation": operation, "params": dict(params or {}), "permission": permission_decision.get("permission"), "created_at": time.time()}
        self._commands.setdefault(device_id or "android_device", []).append(command)
        return {"device_id": device_id or "android_device", "queued": True, "command": command}

    def drain_commands(self, device_id: str) -> list[dict[str, Any]]:
        if self._store is not None:
            return self._store.drain_commands(device_id)
        return self._commands.pop(device_id or "android_device", [])


def build_android_execution_payload(request: ExecutionRequest) -> dict[str, Any]:
    return {
        "target": request.target,
        "operation": request.operation,
        "params": request.params,
    }


def build_android_reply_payload(result: ExecutionResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "message": result.message,
        "data": result.data,
        "error_code": result.error_code,
        "metadata": result.metadata,
    }
