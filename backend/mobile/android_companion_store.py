from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from backend.mobile.android_permissions import (
    build_android_device_permission_posture,
    build_android_permission_policy,
    enforce_android_command_permission,
)
from backend.security.safety_control import evaluate_execution_safety

DEFAULT_COMPANION_STATE_PATH = "state/mobile/android-companion.json"
MAX_HISTORY = 120
MAX_COMMANDS_PER_DEVICE = 200
MAX_COMMAND_RECORDS = 300


class AndroidCompanionStore:
    def __init__(self, path: str | Path | None = None):
        self.path = _state_path(path)

    def snapshot(self) -> dict[str, Any]:
        data = self._load()
        devices = []
        for device_id, state in sorted(data.get("devices", {}).items()):
            item = dict(state if isinstance(state, dict) else {})
            item["device_id"] = device_id
            item["pending_command_count"] = len(data.get("commands", {}).get(device_id, []))
            item["inflight_command_count"] = _inflight_command_count(data, device_id)
            item["installed_app_count"] = len(data.get("apps", {}).get(device_id, []))
            item["online"] = _is_online(item.get("last_heartbeat"))
            if not isinstance(item.get("permission_posture"), dict):
                item["permission_posture"] = build_android_device_permission_posture(item, installed_apps=data.get("apps", {}).get(device_id, []))
            last_command = _latest_command_for_device(data, device_id)
            if last_command:
                item["last_command"] = last_command
            devices.append(item)
        posture_summary = _permission_posture_summary(devices)
        worker_summary = _android_worker_summary(devices, data, posture_summary)
        return {
            "state_path": str(self.path),
            "devices": devices,
            "device_count": len(devices),
            "pending_command_count": sum(len(items) for items in data.get("commands", {}).values()),
            "command_status_counts": _command_status_counts(data),
            "worker": worker_summary,
            "permission_policy": build_android_permission_policy(),
            "permission_posture": posture_summary,
            "commands": data.get("commands", {}),
            "recent_commands": _recent_commands(data),
            "apps": data.get("apps", {}),
            "history": data.get("history", [])[-MAX_HISTORY:],
        }

    def update_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        device_id = _device_id(payload)
        state = dict(payload.get("device_state") or payload)
        state["device_id"] = device_id
        state["last_heartbeat"] = float(state.get("last_heartbeat") or time.time())
        workspace_id = str(payload.get("workspace_id") or state.get("workspace_id") or "").strip()
        if workspace_id:
            state["workspace_id"] = workspace_id
        capabilities = payload.get("capabilities") or state.get("capabilities")
        if isinstance(capabilities, list):
            state["capabilities"] = [str(item) for item in capabilities if str(item).strip()]
        command_catalog = payload.get("command_catalog") or state.get("command_catalog")
        if isinstance(command_catalog, list):
            state["command_catalog"] = [dict(item) for item in command_catalog if isinstance(item, dict)]
        data = self._load()
        data.setdefault("devices", {})[device_id] = state
        apps = payload.get("installed_apps") or state.get("installed_apps")
        if isinstance(apps, list):
            data.setdefault("apps", {})[device_id] = [dict(app) if isinstance(app, dict) else {"name": str(app)} for app in apps]
        state["permission_posture"] = build_android_device_permission_posture(
            state,
            installed_apps=data.get("apps", {}).get(device_id, []),
        )
        results = payload.get("command_results") or state.get("command_results")
        result_count = self._record_command_results(data, device_id, results)
        _append_history(data, "heartbeat", device_id, {"battery_pct": state.get("battery_pct"), "current_app": state.get("current_app")})
        if result_count:
            _append_history(data, "command_results_reported", device_id, {"count": result_count})
        self._save(data)
        return self.device_status(device_id)

    def device_status(self, device_id: str = "android_device") -> dict[str, Any]:
        data = self._load()
        normalized = _normalize_device_id(device_id)
        state = dict(data.get("devices", {}).get(normalized) or {"device_id": normalized})
        state.setdefault("device_id", normalized)
        state["online"] = _is_online(state.get("last_heartbeat")) if normalized in data.get("devices", {}) else False
        state["pending_command_count"] = len(data.get("commands", {}).get(normalized, []))
        state["inflight_command_count"] = _inflight_command_count(data, normalized)
        state["installed_app_count"] = len(data.get("apps", {}).get(normalized, []))
        if not isinstance(state.get("permission_posture"), dict):
            state["permission_posture"] = build_android_device_permission_posture(state, installed_apps=data.get("apps", {}).get(normalized, []))
        last_command = _latest_command_for_device(data, normalized)
        if last_command:
            state["last_command"] = last_command
        return state

    def command_status(self, command_id: str, device_id: str = "") -> dict[str, Any] | None:
        resolved_command_id = str(command_id or "").strip()
        if not resolved_command_id:
            return None
        normalized_device_id = _normalize_device_id(device_id) if device_id else ""
        data = self._load()
        for command in reversed(data.get("command_log", [])):
            if not isinstance(command, dict):
                continue
            if str(command.get("command_id") or "") != resolved_command_id:
                continue
            if normalized_device_id and _normalize_device_id(str(command.get("device_id") or "")) != normalized_device_id:
                continue
            return dict(command)
        return None

    def list_installed_apps(self, device_id: str = "android_device", *, limit: int = 50) -> dict[str, Any]:
        data = self._load()
        normalized = _normalize_device_id(device_id)
        apps = data.get("apps", {}).get(normalized, [])
        return {"device_id": normalized, "apps": apps[: max(1, int(limit))], "count": len(apps)}

    def enqueue_command(self, device_id: str, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = _normalize_device_id(device_id)
        payload = dict(params or {})
        permission_decision = enforce_android_command_permission(operation, payload)
        if not permission_decision.get("allowed"):
            return {
                "device_id": normalized,
                "queued": False,
                "error_code": str(permission_decision.get("error_code") or "android_permission_denied"),
                "message": str(permission_decision.get("message") or "Android command permission denied."),
                "permission": permission_decision.get("permission"),
                "permission_policy": permission_decision.get("policy"),
            }
        safety = evaluate_execution_safety(
            target="android_bridge",
            operation=operation,
            actor=str(payload.get("actor") or "android_bridge"),
            read_only=bool((permission_decision.get("permission") or {}).get("read_only")),
            dry_run=bool(payload.get("dry_run")),
        )
        if not safety.allowed:
            return {
                "device_id": normalized,
                "queued": False,
                "error_code": safety.error_code,
                "message": safety.message,
                "safety": safety.snapshot(),
            }
        command = {
            "command_id": str(uuid.uuid4()),
            "operation": operation,
            "params": payload,
            "permission": permission_decision.get("permission"),
            "created_at": time.time(),
            "status": "queued",
        }
        data = self._load()
        queue = data.setdefault("commands", {}).setdefault(normalized, [])
        queue.append(command)
        data["commands"][normalized] = queue[-MAX_COMMANDS_PER_DEVICE:]
        _upsert_command_record(data, {**command, "device_id": normalized, "queued_at": command["created_at"], "updated_at": command["created_at"]})
        _append_history(data, "command_queued", normalized, {"operation": operation, "command_id": command["command_id"]})
        self._save(data)
        return {"device_id": normalized, "queued": True, "command": command}

    def drain_commands(self, device_id: str) -> list[dict[str, Any]]:
        normalized = _normalize_device_id(device_id)
        data = self._load()
        commands = list(data.setdefault("commands", {}).pop(normalized, []))
        if commands:
            now = time.time()
            for command in commands:
                command["status"] = "delivered"
                command["delivered_at"] = now
                command["updated_at"] = now
                _upsert_command_record(data, {**command, "device_id": normalized})
            _append_history(data, "commands_drained", normalized, {"count": len(commands)})
            self._save(data)
        return commands

    def clear_commands(self, device_id: str = "") -> dict[str, Any]:
        data = self._load()
        if device_id:
            normalized = _normalize_device_id(device_id)
            removed_commands = list(data.setdefault("commands", {}).pop(normalized, []))
            removed = len(removed_commands)
            self._mark_commands_canceled(data, normalized, removed_commands)
            _append_history(data, "commands_cleared", normalized, {"count": removed})
        else:
            removed = sum(len(items) for items in data.get("commands", {}).values())
            for normalized, commands in list(data.get("commands", {}).items()):
                self._mark_commands_canceled(data, normalized, list(commands))
            data["commands"] = {}
            _append_history(data, "commands_cleared", "all", {"count": removed})
        self._save(data)
        return {"ok": True, "status": "cleared", "removed": removed}

    def cleanup_history(self, *, keep_recent_commands: int = MAX_COMMAND_RECORDS, keep_recent_history: int = MAX_HISTORY) -> dict[str, Any]:
        data = self._load()
        commands = [dict(item) for item in data.get("command_log", []) if isinstance(item, dict)]
        history = [dict(item) for item in data.get("history", []) if isinstance(item, dict)]
        command_keep = max(0, int(keep_recent_commands))
        history_keep = max(0, int(keep_recent_history))
        kept_commands = commands[-command_keep:] if command_keep else []
        kept_history = history[-history_keep:] if history_keep else []
        data["command_log"] = kept_commands
        data["history"] = kept_history
        self._save(data)
        return {
            "ok": True,
            "status": "cleaned",
            "removed_command_records": max(0, len(commands) - len(kept_commands)),
            "removed_history_events": max(0, len(history) - len(kept_history)),
            "remaining_command_records": len(kept_commands),
            "remaining_history_events": len(kept_history),
        }

    def migrate(self) -> dict[str, Any]:
        data = self._load()
        self._save(data)
        return {
            "ok": True,
            "status": "migrated",
            "schema_version": str(data.get("schema_version") or "spiritkin.android_companion_store.v1"),
            "state_path": str(self.path),
        }

    def _record_command_results(self, data: dict[str, Any], device_id: str, results: Any) -> int:
        if not isinstance(results, list):
            return 0
        count = 0
        now = time.time()
        for raw in results:
            if not isinstance(raw, dict):
                continue
            command_id = str(raw.get("command_id") or "").strip()
            operation = str(raw.get("operation") or "").strip()
            if not command_id and not operation:
                continue
            status = _normalize_command_result_status(raw)
            completed_at = _float_or_default(raw.get("completed_at") or raw.get("finished_at") or raw.get("at"), now)
            record = {
                "device_id": device_id,
                "command_id": command_id or str(uuid.uuid4()),
                "operation": operation,
                "status": status,
                "success": status == "completed",
                "message": str(raw.get("message") or "").strip(),
                "error_code": str(raw.get("error_code") or "").strip(),
                "result": raw.get("data", raw.get("result")),
                "completed_at": completed_at,
                "reported_at": now,
                "updated_at": now,
            }
            _attach_android_command_trajectory(record)
            _upsert_command_record(data, record)
            count += 1
        return count

    def _mark_commands_canceled(self, data: dict[str, Any], device_id: str, commands: list[dict[str, Any]]) -> None:
        now = time.time()
        for command in commands:
            _upsert_command_record(data, {**command, "device_id": device_id, "status": "canceled", "canceled_at": now, "updated_at": now})

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", "spiritkin.android_companion_store.v1")
        data.setdefault("devices", {})
        data.setdefault("apps", {})
        data.setdefault("commands", {})
        data.setdefault("command_log", [])
        data.setdefault("history", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _state_path(path: str | Path | None = None) -> Path:
    value = path or os.getenv("SPIRITKIN_ANDROID_COMPANION_STATE", DEFAULT_COMPANION_STATE_PATH)
    resolved = Path(value)
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved
    return resolved


def _device_id(payload: dict[str, Any]) -> str:
    return _normalize_device_id(str(payload.get("device_id") or payload.get("serial") or "android_device"))


def _normalize_device_id(device_id: str) -> str:
    return str(device_id or "android_device").strip() or "android_device"


def _append_history(data: dict[str, Any], event: str, device_id: str, detail: dict[str, Any]) -> None:
    item = {"at": time.time(), "event": event, "device_id": device_id, **detail}
    data.setdefault("history", []).append(item)
    data["history"] = data["history"][-MAX_HISTORY:]


def _is_online(last_heartbeat: Any) -> bool:
    try:
        return time.time() - float(last_heartbeat) < 120
    except (TypeError, ValueError):
        return False


def _upsert_command_record(data: dict[str, Any], record: dict[str, Any]) -> None:
    records = data.setdefault("command_log", [])
    command_id = str(record.get("command_id") or "").strip()
    device_id = _normalize_device_id(str(record.get("device_id") or "android_device"))
    record["device_id"] = device_id
    if command_id:
        for existing in reversed(records):
            if not isinstance(existing, dict):
                continue
            if str(existing.get("command_id") or "") == command_id and _normalize_device_id(str(existing.get("device_id") or "")) == device_id:
                existing.update({key: value for key, value in record.items() if value is not None})
                data["command_log"] = [item for item in records if isinstance(item, dict)][-MAX_COMMAND_RECORDS:]
                return
    records.append(record)
    data["command_log"] = [item for item in records if isinstance(item, dict)][-MAX_COMMAND_RECORDS:]


def _attach_android_command_trajectory(record: dict[str, Any]) -> None:
    status = str(record.get("status") or "").strip().lower()
    if status not in {"completed", "failed", "canceled"}:
        return
    try:
        from backend.orchestrator.runtime_trajectory_log import (
            append_runtime_trajectory,
            trajectory_from_android_command_result,
            trajectory_logging_enabled,
        )
    except Exception as exc:
        record["trajectory_log_error"] = str(exc)
        return
    if not trajectory_logging_enabled():
        return
    try:
        trajectory = append_runtime_trajectory(trajectory_from_android_command_result(record))
        metadata = trajectory.get("metadata") if isinstance(trajectory.get("metadata"), dict) else {}
        record["trajectory_record"] = {
            "trajectory_id": trajectory.get("trajectory_id", ""),
            "source": metadata.get("source", "android.command_result"),
            "overall_success": bool(trajectory.get("overall_success", False)),
            "bottleneck_stage": trajectory.get("bottleneck_stage", ""),
        }
    except Exception as exc:
        record["trajectory_log_error"] = str(exc)


def _recent_commands(data: dict[str, Any]) -> list[dict[str, Any]]:
    records = [dict(item) for item in data.get("command_log", []) if isinstance(item, dict)]
    records.sort(key=lambda item: _float_or_default(item.get("updated_at") or item.get("created_at"), 0.0))
    return records[-MAX_HISTORY:]


def _command_status_counts(data: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for command in data.get("command_log", []):
        if not isinstance(command, dict):
            continue
        status = str(command.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _latest_command_for_device(data: dict[str, Any], device_id: str) -> dict[str, Any] | None:
    normalized = _normalize_device_id(device_id)
    commands = [
        dict(command)
        for command in data.get("command_log", [])
        if isinstance(command, dict) and _normalize_device_id(str(command.get("device_id") or "")) == normalized
    ]
    if not commands:
        return None
    commands.sort(key=lambda item: _float_or_default(item.get("updated_at") or item.get("created_at"), 0.0))
    return commands[-1]


def _inflight_command_count(data: dict[str, Any], device_id: str) -> int:
    normalized = _normalize_device_id(device_id)
    inflight = {"delivered", "running"}
    return sum(
        1
        for command in data.get("command_log", [])
        if isinstance(command, dict)
        and _normalize_device_id(str(command.get("device_id") or "")) == normalized
        and str(command.get("status") or "") in inflight
    )


def _permission_posture_summary(devices: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    gaps: list[dict[str, Any]] = []
    for device in devices:
        posture = device.get("permission_posture")
        if not isinstance(posture, dict):
            continue
        status = str(posture.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        for raw_gap in posture.get("gaps") or []:
            if not isinstance(raw_gap, dict):
                continue
            gaps.append({**raw_gap, "device_id": device.get("device_id")})
    return {
        "schema_version": "spiritkin.android_permission_posture_summary.v1",
        "device_count": len(devices),
        "status_counts": counts,
        "gap_count": len(gaps),
        "gaps": gaps[:20],
    }


def _android_worker_summary(devices: list[dict[str, Any]], data: dict[str, Any], posture_summary: dict[str, Any]) -> dict[str, Any]:
    command_counts = _command_status_counts(data)
    pending_count = sum(len(items) for items in data.get("commands", {}).values() if isinstance(items, list))
    inflight_count = sum(_inflight_command_count(data, str(device.get("device_id") or "")) for device in devices)
    online_count = sum(1 for device in devices if bool(device.get("online")))
    gap_count = _int_or_default(posture_summary.get("gap_count"), 0)
    capabilities = _android_worker_capabilities(devices)
    workspace_ids = sorted({str(device.get("workspace_id") or "").strip() for device in devices if str(device.get("workspace_id") or "").strip()})
    automation_ready = any(
        bool(device.get("pdd_accessibility_connected")) or bool(device.get("pdd_accessibility_granted"))
        for device in devices
    )
    screen_ready = any(bool(device.get("screen_capture_authorized")) for device in devices)
    if not devices:
        status = "needs_pairing"
    elif online_count <= 0:
        status = "offline"
    elif gap_count > 0:
        status = "needs_attention"
    elif _int_or_default(command_counts.get("failed"), 0) > _int_or_default(command_counts.get("completed"), 0):
        status = "degraded"
    else:
        status = "ready"
    return {
        "schema_version": "spiritkin.android_worker.v1",
        "worker_id": "android_control_worker",
        "label": "Android Control Worker",
        "role": "controlled_execution_worker",
        "status": status,
        "device_count": len(devices),
        "online_device_count": online_count,
        "pending_command_count": pending_count,
        "inflight_command_count": inflight_count,
        "command_status_counts": command_counts,
        "permission_gap_count": gap_count,
        "capability_count": len(capabilities),
        "capabilities": capabilities[:40],
        "workspace_ids": workspace_ids,
        "queue": {
            "pending": pending_count,
            "inflight": inflight_count,
            "status_counts": command_counts,
        },
        "permissions": {
            "status_counts": posture_summary.get("status_counts") or {},
            "gap_count": gap_count,
            "gaps": posture_summary.get("gaps") or [],
        },
        "lifecycle": {
            "can_receive_commands": online_count > 0,
            "can_run_automation": automation_ready,
            "can_capture_screen": screen_ready,
            "workspace_bound": bool(workspace_ids),
        },
    }


def _android_worker_capabilities(devices: list[dict[str, Any]]) -> list[str]:
    capabilities: set[str] = set()
    for device in devices:
        for item in device.get("capabilities") or []:
            value = str(item or "").strip()
            if value:
                capabilities.add(value)
        for raw in device.get("command_catalog") or []:
            if not isinstance(raw, dict):
                continue
            operation = str(raw.get("operation") or "").strip()
            if operation:
                capabilities.add(operation)
            for capability in raw.get("required_capabilities") or []:
                value = str(capability or "").strip()
                if value:
                    capabilities.add(value)
    return sorted(capabilities)


def _normalize_command_result_status(raw: dict[str, Any]) -> str:
    status = str(raw.get("status") or "").strip().lower()
    if status in {"completed", "failed", "running", "delivered", "queued", "canceled"}:
        return status
    if status in {"ok", "success", "succeeded"}:
        return "completed"
    if status in {"error", "failure"}:
        return "failed"
    if "success" in raw:
        return "completed" if bool(raw.get("success")) else "failed"
    return "completed"


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_default(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
