from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from backend.app.runtime import EVENT_SCHEMA_VERSION
from backend.state_store import resolve_state_path

DEFAULT_DESKTOP_STATE_PATH = "state/desktop_console/state.json"
DESKTOP_STATE_SCHEMA_VERSION = "spiritkin.desktop_console.v1"


def resolve_desktop_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_DESKTOP_STATE_PATH", DEFAULT_DESKTOP_STATE_PATH, path)


def _now() -> float:
    return time.time()


def _default_session(now: float) -> dict[str, Any]:
    return {
        "id": "session_default",
        "title": "主会话",
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }


def default_desktop_state() -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": DESKTOP_STATE_SCHEMA_VERSION,
        "runtime_schema_version": EVENT_SCHEMA_VERSION,
        "revision": 0,
        "active_session_id": "session_default",
        "sessions": [_default_session(now)],
        "projects": [],
        "tasks": [],
        "quick_commands": [
            {"id": "quick_scan_local_software", "title": "扫描本机软件", "command": "扫描本机软件", "updated_at": now},
            {"id": "quick_open_browser", "title": "打开浏览器", "command": "打开浏览器", "updated_at": now},
            {"id": "quick_confirm_execution", "title": "确认执行", "command": "确认执行", "updated_at": now},
            {"id": "quick_cancel_execution", "title": "取消执行", "command": "取消执行", "updated_at": now},
        ],
        "events": [],
        "pending": None,
        "lastExecution": None,
        "lastRoute": None,
        "settings": {},
        "updated_at": now,
        "updated_by": "system",
    }


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ensure_optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _normalize_pending(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    target = str(value.get("target") or value.get("pending_target") or "").strip()
    operation = str(value.get("operation") or value.get("pending_operation") or "").strip()
    if not target or not operation:
        return None
    normalized = dict(value)
    normalized["target"] = target
    normalized["operation"] = operation
    normalized["pending_target"] = target
    normalized["pending_operation"] = operation
    normalized["risk_level"] = str(value.get("risk_level") or value.get("riskLevel") or "medium").strip() or "medium"
    normalized["created_at"] = float(value.get("created_at") or value.get("time") or _now())
    return normalized


def _is_successful_execution(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ok", "success", "succeeded"}
    return False


def _pending_matches_successful_execution(
    pending: dict[str, Any] | None,
    last_execution: dict[str, Any] | None,
) -> bool:
    if not pending or not last_execution or not _is_successful_execution(last_execution.get("success")):
        return False
    pending_target = str(pending.get("target") or pending.get("pending_target") or "").strip()
    pending_operation = str(pending.get("operation") or pending.get("pending_operation") or "").strip()
    executed_target = str(last_execution.get("target") or "").strip()
    executed_operation = str(last_execution.get("operation") or "").strip()
    return bool(pending_target and pending_operation and pending_target == executed_target and pending_operation == executed_operation)


def _normalize_message(item: Any, *, fallback_session_id: str = "") -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or item.get("content") or "").strip()
    steps = item.get("steps")
    has_steps = isinstance(steps, list) and len(steps) > 0
    # 工作链消息（kind=work）只有 steps 没有正文，不能因 text 为空被丢弃，
    # 否则桌面端保存状态后回传快照会把推理链整条洗掉。
    if not text and not has_steps:
        return None
    now = _now()
    role = str(item.get("role") or "assistant").strip() or "assistant"
    created_at = float(item.get("created_at") or now)
    message_id = str(item.get("id") or "").strip()
    if not message_id:
        message_id = f"msg_{uuid.uuid5(uuid.NAMESPACE_URL, f'{fallback_session_id}:{role}:{created_at:.3f}:{text}').hex[:16]}"
    return {
        **item,
        "id": message_id,
        "role": role,
        "text": text,
        "created_at": created_at,
        "updated_at": float(item.get("updated_at") or created_at),
    }


def _normalize_session(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    now = _now()
    session_id = str(item.get("id") or "").strip()
    if not session_id:
        return None
    title = str(item.get("title") or "未命名会话").strip() or "未命名会话"
    messages = [
        message
        for message in (_normalize_message(raw, fallback_session_id=session_id) for raw in _ensure_list(item.get("messages")))
        if message is not None
    ]
    messages.sort(key=lambda message: float(message.get("created_at") or 0))
    return {
        **item,
        "id": session_id,
        "title": title[:80],
        "status": str(item.get("status") or "active"),
        "project_id": str(item.get("project_id") or "").strip() or None,
        "previous_project_id": str(item.get("previous_project_id") or "").strip() or None,
        "created_at": float(item.get("created_at") or now),
        "updated_at": float(item.get("updated_at") or now),
        "messages": messages[-300:],
    }


def _normalize_named_item(item: Any, *, prefix: str, default_title: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    now = _now()
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        item_id = f"{prefix}_{int(now * 1000)}"
    title = str(item.get("title") or item.get("name") or default_title).strip() or default_title
    normalized = {
        **item,
        "id": item_id,
        "title": title[:120],
        "status": str(item.get("status") or "active"),
        "created_at": float(item.get("created_at") or now),
        "updated_at": float(item.get("updated_at") or now),
    }
    if prefix == "project":
        normalized["workspace_path"] = str(item.get("workspace_path") or item.get("workspacePath") or "").strip() or None
        normalized["env_file_path"] = str(item.get("env_file_path") or item.get("envFilePath") or "").strip() or None
        normalized["dependency_file_path"] = str(item.get("dependency_file_path") or item.get("dependencyFilePath") or "").strip() or None
        normalized["package_manager"] = str(item.get("package_manager") or item.get("packageManager") or "auto").strip() or "auto"
        normalized["start_command"] = str(item.get("start_command") or item.get("startCommand") or "").strip() or None
    return normalized


def _normalize_quick_command(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    now = _now()
    command_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or item.get("name") or "").strip()
    command = str(item.get("command") or item.get("text") or "").strip()
    if not command:
        return None
    if not command_id:
        command_id = f"quick_command_{int(now * 1000)}"
    if not title:
        title = command[:40]
    return {
        **item,
        "id": command_id,
        "title": title[:80],
        "command": command,
        "updated_at": float(item.get("updated_at") or now),
    }


def _normalize_event(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    event_type = str(item.get("type") or "").strip()
    if not event_type:
        return None
    return {
        **item,
        "type": event_type,
        "time": str(item.get("time") or item.get("created_at") or ""),
    }


def normalize_desktop_state(raw: Any) -> dict[str, Any]:
    data = _ensure_dict(raw)
    now = _now()
    default = default_desktop_state()
    previous_schema_version = str(data.get("schema_version") or "").strip() or "unknown"
    migration_history = [
        dict(item)
        for item in _ensure_list(data.get("migration_history"))
        if isinstance(item, dict)
    ][-20:]
    sessions = [
        session
        for session in (_normalize_session(item) for item in _ensure_list(data.get("sessions")))
        if session is not None
    ]
    if not sessions:
        sessions = default["sessions"]
    session_ids = {str(session["id"]) for session in sessions}
    active_session_id = str(data.get("active_session_id") or sessions[0]["id"])
    if active_session_id not in session_ids:
        active_session_id = str(sessions[0]["id"])

    projects = [
        item
        for item in (
            _normalize_named_item(raw_item, prefix="project", default_title="未命名项目")
            for raw_item in _ensure_list(data.get("projects"))
        )
        if item is not None
    ]
    tasks = [
        item
        for item in (
            _normalize_named_item(raw_item, prefix="task", default_title="未命名任务")
            for raw_item in _ensure_list(data.get("tasks"))
        )
        if item is not None
    ]
    quick_commands = [
        item
        for item in (_normalize_quick_command(raw_item) for raw_item in _ensure_list(data.get("quick_commands")))
        if item is not None
    ]
    if not quick_commands:
        quick_commands = default["quick_commands"]
    events = [
        event
        for event in (_normalize_event(raw_item) for raw_item in _ensure_list(data.get("events")))
        if event is not None
    ]
    pending = _normalize_pending(data.get("pending"))
    last_execution = _ensure_optional_dict(data.get("lastExecution") or data.get("last_execution"))
    if _pending_matches_successful_execution(pending, last_execution):
        pending = None
    if previous_schema_version != DESKTOP_STATE_SCHEMA_VERSION:
        migration_history.append(
            {
                "from_schema_version": previous_schema_version,
                "to_schema_version": DESKTOP_STATE_SCHEMA_VERSION,
                "migrated_at": now,
                "reason": "normalize_desktop_state",
            }
        )
    return {
        "schema_version": DESKTOP_STATE_SCHEMA_VERSION,
        "runtime_schema_version": EVENT_SCHEMA_VERSION,
        "revision": int(data.get("revision") or 0),
        "active_session_id": active_session_id,
        "sessions": sessions[-500:],
        "projects": projects[-80:],
        "tasks": tasks[-200:],
        "quick_commands": quick_commands[-80:],
        "events": events[-200:],
        "pending": pending,
        "lastExecution": last_execution,
        "lastRoute": _ensure_optional_dict(data.get("lastRoute") or data.get("last_route")),
        "settings": _ensure_dict(data.get("settings")),
        "migration_history": migration_history[-20:],
        "updated_at": float(data.get("updated_at") or now),
        "updated_by": str(data.get("updated_by") or "unknown"),
    }


def load_desktop_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_desktop_state_path(path)
    if not target.exists():
        return default_desktop_state()
    try:
        return normalize_desktop_state(json.loads(target.read_text(encoding="utf-8")))
    except Exception:
        return default_desktop_state()


def save_desktop_state(state: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_desktop_state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_desktop_state(state)
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return normalized


def migrate_desktop_state(path: str | os.PathLike[str] | None = None, *, actor: str = "state_maintenance") -> dict[str, Any]:
    target = resolve_desktop_state_path(path)
    existed = target.exists()
    raw: Any = {}
    previous_schema_version = "missing"
    if existed:
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            previous_schema_version = str(_ensure_dict(raw).get("schema_version") or "unknown")
        except Exception:
            raw = {}
            previous_schema_version = "unreadable"
    normalized = normalize_desktop_state(raw)
    normalized["updated_at"] = _now()
    normalized["updated_by"] = actor
    saved = save_desktop_state(normalized, path)
    return {
        "ok": True,
        "status": "migrated",
        "path": str(target),
        "existed": existed,
        "from_schema_version": previous_schema_version,
        "to_schema_version": DESKTOP_STATE_SCHEMA_VERSION,
        "session_count": len(saved.get("sessions") or []),
        "project_count": len(saved.get("projects") or []),
        "task_count": len(saved.get("tasks") or []),
        "migration_history_count": len(saved.get("migration_history") or []),
    }


def _merge_by_id(
    current_items: list[dict[str, Any]],
    incoming_items: list[dict[str, Any]],
    *,
    deleted_child_ids: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {str(item.get("id")): dict(item) for item in current_items if item.get("id")}
    for item in incoming_items:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        existing = merged.get(item_id, {})
        if item.get("messages") is not None:
            existing_messages = existing.get("messages", [])
            incoming_messages = item.get("messages", [])
            deleted_message_ids = (deleted_child_ids or {}).get(item_id, set())
            messages = _merge_by_id(
                _without_ids(_ensure_list(existing_messages), deleted_message_ids),
                _without_ids(_ensure_list(incoming_messages), deleted_message_ids),
            )
            existing = {**existing, **item, "messages": messages[-300:]}
        else:
            existing = {**existing, **item}
        merged[item_id] = existing
    ordered = sorted(merged.values(), key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0))
    return ordered


def _deleted_ids(payload: dict[str, Any], *names: str) -> set[str]:
    ids: set[str] = set()
    for name in names:
        value = payload.get(name)
        if not isinstance(value, list):
            continue
        ids.update(str(item) for item in value if str(item or "").strip())
    return ids


def _deleted_child_ids(payload: dict[str, Any], *names: str) -> dict[str, set[str]]:
    by_parent: dict[str, set[str]] = {}
    for name in names:
        value = payload.get(name)
        if isinstance(value, dict):
            for parent_id, ids in value.items():
                parent_key = str(parent_id or "").strip()
                if not parent_key or not isinstance(ids, list):
                    continue
                by_parent.setdefault(parent_key, set()).update(str(item) for item in ids if str(item or "").strip())
        elif isinstance(value, list):
            # Accept [{session_id, message_ids}] for clients that prefer explicit records.
            for record in value:
                if not isinstance(record, dict):
                    continue
                parent_key = str(record.get("session_id") or record.get("sessionId") or "").strip()
                ids = record.get("message_ids") or record.get("messageIds")
                if not parent_key or not isinstance(ids, list):
                    continue
                by_parent.setdefault(parent_key, set()).update(str(item) for item in ids if str(item or "").strip())
    return by_parent


def _without_ids(items: list[Any], deleted_ids: set[str]) -> list[dict[str, Any]]:
    if not deleted_ids:
        return [dict(item) for item in _ensure_list(items) if isinstance(item, dict)]
    return [
        dict(item)
        for item in _ensure_list(items)
        if isinstance(item, dict) and str(item.get("id") or "") not in deleted_ids
    ]


def _merge_events(current_events: list[Any], incoming_events: list[Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in current_events + incoming_events:
        normalized = _normalize_event(event)
        if normalized is None:
            continue
        key = (str(normalized.get("type") or ""), str(normalized.get("time") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged[-200:]


def update_desktop_state(
    payload: dict[str, Any],
    *,
    path: str | os.PathLike[str] | None = None,
    client_id: str = "desktop",
) -> dict[str, Any]:
    current = load_desktop_state(path)
    raw_incoming = payload.get("state") if isinstance(payload.get("state"), dict) else payload
    incoming = normalize_desktop_state(raw_incoming)
    deleted_session_ids = _deleted_ids(payload, "deleted_session_ids", "deletedSessionIds")
    deleted_project_ids = _deleted_ids(payload, "deleted_project_ids", "deletedProjectIds")
    deleted_task_ids = _deleted_ids(payload, "deleted_task_ids", "deletedTaskIds")
    deleted_message_ids = _deleted_child_ids(payload, "deleted_message_ids", "deletedMessageIds")
    now = _now()
    merged = {
        **current,
        "active_session_id": incoming.get("active_session_id") or current.get("active_session_id"),
        "sessions": _merge_by_id(
            _without_ids(_ensure_list(current.get("sessions")), deleted_session_ids),
            _without_ids(_ensure_list(incoming.get("sessions")), deleted_session_ids),
            deleted_child_ids=deleted_message_ids,
        ),
        "projects": _merge_by_id(
            _without_ids(_ensure_list(current.get("projects")), deleted_project_ids),
            _without_ids(_ensure_list(incoming.get("projects")), deleted_project_ids),
        ),
        "tasks": _merge_by_id(
            _without_ids(_ensure_list(current.get("tasks")), deleted_task_ids),
            _without_ids(_ensure_list(incoming.get("tasks")), deleted_task_ids),
        ),
        "events": _merge_events(_ensure_list(current.get("events")), _ensure_list(incoming.get("events"))),
        "pending": incoming.get("pending") if "pending" in raw_incoming else current.get("pending"),
        "lastExecution": incoming.get("lastExecution") if ("lastExecution" in raw_incoming or "last_execution" in raw_incoming) else current.get("lastExecution"),
        "lastRoute": incoming.get("lastRoute") if ("lastRoute" in raw_incoming or "last_route" in raw_incoming) else current.get("lastRoute"),
        "settings": {**_ensure_dict(current.get("settings")), **_ensure_dict(incoming.get("settings"))},
        "revision": max(int(current.get("revision") or 0), int(incoming.get("revision") or 0)) + 1,
        "updated_at": now,
        "updated_by": str(payload.get("client_id") or client_id or "desktop"),
    }
    return save_desktop_state(merged, path)
