from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.state_store import resolve_state_path

SCHEMA_VERSION = "spiritkin.safety_control.v1"
DEFAULT_SAFETY_STATE_PATH = "state/safety/kill_switch.json"
STOP_MODES = {"soft_stop", "hard_stop"}
RECOVERY_POST_PATHS = {
    "/desktop/safety",
    "/desktop/diagnostics",
    "/desktop/services",
    "/desktop/service-ports",
    "/proactive/feedback",
    "/scheduler/intents",
}
HARD_STOP_RESUME_CONFIRMATION = "RESUME_HARD_STOP"
SAFETY_HISTORY_LIMIT = 80


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    error_code: str = ""
    message: str = ""
    safety: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "error_code": self.error_code,
            "message": self.message,
            "safety": dict(self.safety or {}),
        }


def resolve_safety_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SAFETY_STATE_PATH", DEFAULT_SAFETY_STATE_PATH, path)


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "active": False,
        "mode": "normal",
        "reason": "",
        "actor": "",
        "updated_at": "",
        "revision": 0,
        "history": [],
    }


def load_safety_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state_path = resolve_safety_state_path(path)
    if not state_path.exists():
        return _default_state()
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(raw, dict):
        return _default_state()
    state = _default_state()
    state.update(raw)
    state["schema_version"] = SCHEMA_VERSION
    state["active"] = bool(state.get("active"))
    mode = str(state.get("mode") or "normal").strip().lower()
    state["mode"] = mode if mode in STOP_MODES or mode == "normal" else ("soft_stop" if state["active"] else "normal")
    if state["active"] and state["mode"] == "normal":
        state["mode"] = "soft_stop"
    if not state["active"]:
        state["mode"] = "normal"
    history = state.get("history")
    state["history"] = [dict(item) for item in history if isinstance(item, dict)][-SAFETY_HISTORY_LIMIT:] if isinstance(history, list) else []
    return state


def save_safety_state(state: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state_path = resolve_safety_state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _default_state()
    normalized.update(state)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["revision"] = int(normalized.get("revision") or 0)
    state_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def build_safety_snapshot(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state = load_safety_state(path)
    state["state_path"] = str(resolve_safety_state_path(path))
    state["resume_confirmation_required"] = bool(state.get("active")) and state.get("mode") == "hard_stop"
    state["resume_confirmation_text"] = HARD_STOP_RESUME_CONFIRMATION if state["resume_confirmation_required"] else ""
    return state


def append_safety_event(
    action: str,
    *,
    mode: str = "",
    reason: str = "",
    actor: str = "",
    metadata: dict[str, Any] | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    state = load_safety_state(path)
    event = {
        "at": _utc_timestamp(),
        "action": action,
        "mode": mode or str(state.get("mode") or "normal"),
        "reason": reason,
        "actor": actor,
        "metadata": dict(metadata or {}),
    }
    history = [*list(state.get("history") or []), event][-SAFETY_HISTORY_LIMIT:]
    updated = {
        **state,
        "updated_at": event["at"],
        "revision": int(state.get("revision") or 0) + 1,
        "history": history,
    }
    return save_safety_state(updated, path)


def set_safety_stop(
    *,
    mode: str = "soft_stop",
    reason: str = "",
    actor: str = "",
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    state = load_safety_state(path)
    normalized_mode = str(mode or "soft_stop").strip().lower()
    if normalized_mode not in STOP_MODES:
        normalized_mode = "soft_stop"
    event = {
        "at": _utc_timestamp(),
        "action": "set_stop",
        "mode": normalized_mode,
        "reason": reason,
        "actor": actor,
    }
    history = [*list(state.get("history") or []), event][-SAFETY_HISTORY_LIMIT:]
    updated = {
        **state,
        "active": True,
        "mode": normalized_mode,
        "reason": reason,
        "actor": actor,
        "updated_at": event["at"],
        "revision": int(state.get("revision") or 0) + 1,
        "history": history,
    }
    return save_safety_state(updated, path)


def clear_safety_stop(
    *,
    reason: str = "",
    actor: str = "",
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    state = load_safety_state(path)
    event = {
        "at": _utc_timestamp(),
        "action": "clear_stop",
        "mode": "normal",
        "reason": reason,
        "actor": actor,
    }
    history = [*list(state.get("history") or []), event][-SAFETY_HISTORY_LIMIT:]
    updated = {
        **state,
        "active": False,
        "mode": "normal",
        "reason": reason,
        "actor": actor,
        "updated_at": event["at"],
        "revision": int(state.get("revision") or 0) + 1,
        "history": history,
    }
    return save_safety_state(updated, path)


def evaluate_execution_safety(
    *,
    target: str,
    operation: str,
    actor: str = "",
    read_only: bool = False,
    dry_run: bool = False,
    path: str | os.PathLike[str] | None = None,
) -> SafetyDecision:
    state = build_safety_snapshot(path)
    if not bool(state.get("active")):
        return SafetyDecision(True, safety=state)
    if read_only or dry_run:
        return SafetyDecision(True, safety=state)
    message = f"Safety stop is active; blocked {target}.{operation}"
    updated = append_safety_event(
        "blocked_execution",
        mode=str(state.get("mode") or "soft_stop"),
        reason=message,
        actor=actor,
        metadata={"target": target, "operation": operation},
        path=path,
    )
    snapshot = build_safety_snapshot(path)
    return SafetyDecision(
        False,
        error_code="safety_stop_active",
        message=message,
        safety={**snapshot, "blocked_target": target, "blocked_operation": operation, "actor": actor, "blocked_event": updated.get("history", [])[-1] if updated.get("history") else {}},
    )


def evaluate_gateway_request_safety(
    *,
    path: str,
    method: str,
    path_override: str | os.PathLike[str] | None = None,
) -> SafetyDecision:
    state = build_safety_snapshot(path_override)
    if not bool(state.get("active")):
        return SafetyDecision(True, safety=state)
    normalized_method = method.strip().upper()
    normalized_path = (path.rstrip("/") or "/").lower()
    if normalized_method == "GET":
        return SafetyDecision(True, safety=state)
    if normalized_path in RECOVERY_POST_PATHS:
        return SafetyDecision(True, safety=state)
    if state.get("mode") != "hard_stop":
        return SafetyDecision(True, safety=state)
    message = f"Hard safety stop is active; blocked {normalized_method} {normalized_path}"
    updated = append_safety_event(
        "blocked_gateway_post",
        mode="hard_stop",
        reason=message,
        actor="command_gateway",
        metadata={"method": normalized_method, "path": normalized_path},
        path=path_override,
    )
    snapshot = build_safety_snapshot(path_override)
    return SafetyDecision(
        False,
        error_code="safety_hard_stop_active",
        message=message,
        safety={**snapshot, "blocked_event": updated.get("history", [])[-1] if updated.get("history") else {}},
    )


def handle_safety_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "status").strip().lower()
    if action in {"status", "snapshot", "refresh"}:
        return {"ok": True, "safety": build_safety_snapshot()}
    if action in {"panic_stop", "soft_stop", "safe_mode", "enable", "enable_safe_mode"}:
        state = set_safety_stop(
            mode=str(payload.get("mode") or "soft_stop"),
            reason=str(payload.get("reason") or ""),
            actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
        )
        return {"ok": True, "safety": build_safety_snapshot(), "event": state.get("history", [])[-1] if state.get("history") else {}}
    if action in {"hard_stop", "panic_hard_stop"}:
        state = set_safety_stop(
            mode="hard_stop",
            reason=str(payload.get("reason") or ""),
            actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
        )
        return {"ok": True, "safety": build_safety_snapshot(), "event": state.get("history", [])[-1] if state.get("history") else {}}
    if action in {"resume", "resume_safe_mode", "clear", "disable", "disable_safe_mode"}:
        current = build_safety_snapshot()
        if current.get("mode") == "hard_stop":
            confirmation = str(payload.get("confirm") or payload.get("confirmation") or payload.get("confirmation_text") or "").strip()
            if confirmation != HARD_STOP_RESUME_CONFIRMATION:
                raise ValueError(f"hard stop resume requires confirmation: {HARD_STOP_RESUME_CONFIRMATION}")
        state = clear_safety_stop(
            reason=str(payload.get("reason") or ""),
            actor=str(payload.get("actor") or payload.get("reviewer") or "desktop"),
        )
        return {"ok": True, "safety": build_safety_snapshot(), "event": state.get("history", [])[-1] if state.get("history") else {}}
    raise ValueError(f"unsupported safety action: {action}")
