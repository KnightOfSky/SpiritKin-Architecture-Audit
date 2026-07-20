from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.mcp_management import build_mcp_management_snapshot
from backend.app.operations_center import list_service_actions
from backend.app.project_runtime import list_project_runtime_audit_events
from backend.app.skills_console import list_skill_run_audit_events
from backend.code_jury import list_code_jury_audit_events
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.orchestrator.workflow_store import JsonWorkflowStore
from backend.security.safety_control import build_safety_snapshot

ACTION_LOG_SCHEMA_VERSION = "spiritkin.action_log.v1"
DEFAULT_ACTION_LOG_LIMIT = 80


def build_action_log_snapshot(*, limit: int = DEFAULT_ACTION_LOG_LIMIT, project_root: str | Path | None = None) -> dict[str, Any]:
    normalized_limit = max(1, min(500, int(limit or DEFAULT_ACTION_LOG_LIMIT)))
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    def collect(source: str, loader: Callable[[], list[dict[str, Any]]]) -> None:
        try:
            loaded = loader()
        except Exception as exc:  # Defensive: one broken log source should not hide the rest.
            errors.append({"source": source, "error": type(exc).__name__, "detail": str(exc)})
            sources.append({"source": source, "ok": False, "count": 0})
            return
        events.extend(loaded)
        sources.append({"source": source, "ok": True, "count": len(loaded)})

    collect("service", lambda: [_service_event(item) for item in list_service_actions(limit=normalized_limit)])
    collect("android", lambda: _android_events(AndroidCompanionStore().snapshot(), limit=normalized_limit))
    collect("safety", lambda: _safety_events(build_safety_snapshot(), limit=normalized_limit))
    collect("mcp", lambda: _mcp_events(build_mcp_management_snapshot(), limit=normalized_limit))
    collect("workflow", lambda: _workflow_events(project_root=project_root, limit=normalized_limit))
    collect("skill", lambda: _skill_events(limit=normalized_limit))
    collect("project_runtime", lambda: _project_runtime_events(limit=normalized_limit))
    collect("code_jury", lambda: _code_jury_events(limit=normalized_limit))

    events.sort(key=lambda item: float(item.get("timestamp") or 0.0), reverse=True)
    limited_events = events[:normalized_limit]
    source_counts: dict[str, int] = {}
    for event in limited_events:
        source = str(event.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    return {
        "schema_version": ACTION_LOG_SCHEMA_VERSION,
        "generated_at": time.time(),
        "limit": normalized_limit,
        "event_count": len(limited_events),
        "available_event_count": len(events),
        "source_counts": source_counts,
        "sources": sources,
        "events": limited_events,
        "errors": errors,
    }


def _service_event(item: dict[str, Any]) -> dict[str, Any]:
    timestamp = _timestamp(item.get("created_at") or item.get("at"))
    ok = item.get("ok")
    return _event(
        source="service",
        source_label="Service",
        action=item.get("action"),
        status=str(item.get("status") or ("ok" if ok is True else "failed" if ok is False else "recorded")),
        target=item.get("service_id") or item.get("label"),
        message=item.get("message") or item.get("label") or item.get("service_id"),
        actor=item.get("actor") or "desktop",
        timestamp=timestamp,
        metadata={
            "service_id": item.get("service_id"),
            "label": item.get("label"),
            "port": item.get("port"),
            "pid": item.get("pid"),
            "ok": item.get("ok"),
        },
    )


def _android_events(snapshot: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in _dict_items(snapshot.get("recent_commands"))[-limit:]:
        timestamp = _timestamp(item.get("updated_at") or item.get("queued_at") or item.get("created_at"))
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        permission = item.get("permission") if isinstance(item.get("permission"), dict) else {}
        operation = str(item.get("operation") or "android_command")
        events.append(
            _event(
                source="android_command",
                source_label="Android Command",
                action=operation,
                status=item.get("status") or "recorded",
                target=item.get("device_id"),
                message=f"{operation} / {item.get('status') or 'recorded'}",
                actor=params.get("actor") or "desktop",
                timestamp=timestamp,
                metadata={
                    "device_id": item.get("device_id"),
                    "command_id": item.get("command_id"),
                    "permission_tier": permission.get("tier"),
                    "read_only": permission.get("read_only"),
                    "param_keys": sorted(str(key) for key in params.keys()),
                },
            )
        )
    for item in _dict_items(snapshot.get("history"))[-limit:]:
        timestamp = _timestamp(item.get("at"))
        metadata = {key: value for key, value in item.items() if key not in {"at", "event", "device_id"}}
        events.append(
            _event(
                source="android_history",
                source_label="Android History",
                action=item.get("event") or "android_event",
                status=item.get("status") or "recorded",
                target=item.get("device_id"),
                message=item.get("operation") or item.get("command_id") or item.get("event"),
                actor=item.get("actor") or "android_companion",
                timestamp=timestamp,
                metadata=metadata,
            )
        )
    return events


def _safety_events(snapshot: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in _dict_items(snapshot.get("history"))[-limit:]:
        timestamp = _timestamp(item.get("at"))
        mode = str(item.get("mode") or snapshot.get("mode") or "normal")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        events.append(
            _event(
                source="safety",
                source_label="Safety",
                action=item.get("action") or "safety_event",
                status=mode,
                target="safety",
                message=item.get("reason") or item.get("action") or mode,
                actor=item.get("actor") or snapshot.get("actor") or "desktop",
                timestamp=timestamp,
                metadata={**metadata, "mode": mode, "active": snapshot.get("active")},
            )
        )
    return events


def _mcp_events(snapshot: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in _dict_items(snapshot.get("audit_log"))[-limit:]:
        timestamp = _timestamp(item.get("at"))
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        success = item.get("success")
        events.append(
            _event(
                source="mcp",
                source_label="MCP",
                action=item.get("action") or "mcp_audit",
                status="ok" if success is True else "failed" if success is False else "recorded",
                target=item.get("server_id") or "mcp",
                message=item.get("message") or item.get("action"),
                actor=item.get("actor") or "desktop",
                timestamp=timestamp,
                metadata=metadata,
            )
        )
    return events


def _workflow_events(*, project_root: str | Path | None, limit: int) -> list[dict[str, Any]]:
    store = JsonWorkflowStore(project_root=project_root or Path.cwd())
    events: list[dict[str, Any]] = []
    for item in store.list_audit_events(limit=limit):
        timestamp = _timestamp(item.get("at"))
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        events.append(
            _event(
                source="workflow",
                source_label="Workflow",
                action=item.get("action") or "workflow_audit",
                status=payload.get("status") or "recorded",
                target=item.get("workflow_name") or payload.get("run_id") or "workflow",
                message=item.get("message") or item.get("action"),
                actor=item.get("actor") or "system",
                timestamp=timestamp,
                metadata=payload,
            )
        )
    return events


def _skill_events(*, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in list_skill_run_audit_events(limit=limit):
        timestamp = _timestamp(item.get("at"))
        metadata = {
            "owner_agent_id": item.get("owner_agent_id"),
            "risk_level": item.get("risk_level"),
            "dry_run": item.get("dry_run"),
            "error_code": item.get("error_code"),
            "duration_ms": item.get("duration_ms"),
            "step_count": item.get("step_count"),
            "budget": item.get("budget"),
        }
        events.append(
            _event(
                source="skill",
                source_label="Skill",
                action="dry_run" if bool(item.get("dry_run")) else "run",
                status="ok" if item.get("success") is True else "failed" if item.get("success") is False else "recorded",
                target=item.get("skill_name"),
                message=item.get("message") or item.get("skill_name") or "skill run",
                actor=item.get("actor") or "desktop",
                timestamp=timestamp,
                metadata=metadata,
            )
        )
    return events


def _project_runtime_events(*, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in list_project_runtime_audit_events(limit=limit):
        timestamp = _timestamp(item.get("at"))
        policy = item.get("policy") if isinstance(item.get("policy"), dict) else {}
        events.append(
            _event(
                source="project_runtime",
                source_label="Project Runtime",
                action=item.get("action") or "project_runtime",
                status=item.get("status") or "recorded",
                target=item.get("project_title") or item.get("project_id") or item.get("workspace_path"),
                message=item.get("message") or item.get("command") or "project runtime event",
                actor=item.get("actor") or "desktop",
                timestamp=timestamp,
                metadata={
                    "project_id": item.get("project_id"),
                    "workspace_path": item.get("workspace_path"),
                    "command": item.get("command"),
                    "risk_level": item.get("risk_level"),
                    "review_required": item.get("review_required"),
                    "blocker_count": len(policy.get("blockers") or []) if isinstance(policy.get("blockers"), list) else 0,
                    "warning_count": len(policy.get("warnings") or []) if isinstance(policy.get("warnings"), list) else 0,
                },
            )
        )
    return events


def _code_jury_events(*, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in list_code_jury_audit_events(limit=limit):
        timestamp = _timestamp(item.get("at"))
        patch_plan = item.get("patch_plan") if isinstance(item.get("patch_plan"), dict) else {}
        promotion_gate = item.get("promotion_gate") if isinstance(item.get("promotion_gate"), dict) else {}
        events.append(
            _event(
                source="code_jury",
                source_label="Code Jury",
                action=item.get("action") or "jury_review",
                status=item.get("decision") or "recorded",
                target=item.get("package_id") or item.get("report_id") or "code_jury",
                message=f"{item.get('review_type') or 'code'} jury: {item.get('decision') or 'recorded'}",
                actor=item.get("actor") or "desktop",
                timestamp=timestamp,
                metadata={
                    "report_id": item.get("report_id"),
                    "plan_id": item.get("plan_id"),
                    "overall_score": item.get("overall_score"),
                    "structured_review_count": item.get("structured_review_count"),
                    "patch_status": patch_plan.get("status"),
                    "promotion_eligible": promotion_gate.get("eligible"),
                    "auto_apply_allowed": False,
                },
            )
        )
    return events


def _event(
    *,
    source: str,
    source_label: str,
    action: Any,
    status: Any,
    target: Any,
    message: Any,
    actor: Any,
    timestamp: float,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_action = _text(action, "event")
    normalized_target = _text(target, "")
    return {
        "id": f"{source}:{normalized_action}:{normalized_target}:{timestamp:.6f}",
        "source": source,
        "source_label": source_label,
        "action": normalized_action,
        "status": _text(status, "recorded"),
        "target": normalized_target,
        "message": _text(message, normalized_action),
        "actor": _text(actor, "system"),
        "at": _format_timestamp(timestamp),
        "timestamp": timestamp,
        "metadata": dict(metadata or {}),
    }


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _text(value: Any, fallback: str = "") -> str:
    text = str(value if value is not None else "").strip()
    return text[:500] if text else fallback


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, parsed.timestamp())


def _format_timestamp(timestamp: float) -> str:
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
