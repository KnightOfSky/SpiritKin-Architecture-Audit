from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _age_seconds(value: object) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())


def build_ios_monitor_snapshot(store: Any, *, workspace_id: str) -> dict[str, Any]:
    snapshot = store.snapshot(workspace_id or None)
    validation = store.validate_state(workspace_id=workspace_id or None)
    incidents: list[dict[str, Any]] = []

    for index, error in enumerate(validation.get("errors") or []):
        incidents.append({
            "incident_id": f"state_error_{index}",
            "severity": "critical",
            "source": "control_state",
            "title": "控制面状态校验失败",
            "detail": str(error),
            "workspace_id": workspace_id,
            "auto_repairable": False,
            "recommended_action": "人工检查状态文件和备份",
        })
    for index, warning in enumerate(validation.get("warnings") or []):
        incidents.append({
            "incident_id": f"state_warning_{index}",
            "severity": "medium",
            "source": "control_state",
            "title": "控制面状态警告",
            "detail": str(warning),
            "workspace_id": workspace_id,
            "auto_repairable": False,
            "recommended_action": "检查关联 workflow/task/resource",
        })

    overview = snapshot.get("workspace_devices") if isinstance(snapshot.get("workspace_devices"), dict) else {}
    workspaces = overview.get("items") if isinstance(overview.get("items"), list) else []
    for workspace in workspaces:
        if not isinstance(workspace, dict):
            continue
        current_workspace = str(workspace.get("workspace_id") or workspace_id)
        for kind, label in (("remote_workers", "Remote Worker"), ("android_devices", "Android Bridge"), ("ios_controllers", "iOS 主控")):
            for item in workspace.get(kind) or []:
                if not isinstance(item, dict):
                    continue
                age = _age_seconds(item.get("last_seen_at"))
                stale = age is not None and age > 300
                status = str(item.get("status") or "unknown")
                # cleanup_state intentionally records a stale binding as offline;
                # do not rediscover the same repaired binding on every poll.
                if status in {"offline", "disabled", "revoked"}:
                    continue
                if status == "online" and not stale:
                    continue
                item_id = str(item.get("device_id") or item.get("worker_id") or item.get("terminal_id") or "unknown")
                incidents.append({
                    "incident_id": f"{kind}_{item_id}",
                    "severity": "high" if kind == "remote_workers" else "medium",
                    "source": kind,
                    "title": f"{label} 离线或心跳过期",
                    "detail": f"{item_id} · {status} · last_seen={item.get('last_seen_at') or '--'}",
                    "workspace_id": current_workspace,
                    "target_id": item_id,
                    "auto_repairable": True,
                    "repair_action": "cleanup_stale_bindings",
                    "recommended_action": "标记离线并回收过期任务，等待节点重新心跳",
                })

    runs = snapshot.get("workflow_runs") if isinstance(snapshot.get("workflow_runs"), dict) else {}
    for run in runs.get("recent") or []:
        if not isinstance(run, dict) or str(run.get("status") or "") not in {"failed", "blocked", "expired"}:
            continue
        run_id = str(run.get("run_id") or "")
        incidents.append({
            "incident_id": f"workflow_{run_id}",
            "severity": "high",
            "source": "workflow",
            "title": "Workflow 运行失败",
            "detail": str(run.get("error") or run.get("status") or "failed"),
            "workspace_id": workspace_id,
            "target_id": run_id,
            "auto_repairable": False,
            "repair_action": "retry_workflow_run",
            "recommended_action": "确认副作用后手动重试",
        })

    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    incidents.sort(key=lambda item: (-severity_rank.get(str(item.get("severity")), 0), str(item.get("incident_id") or "")))
    return {
        "schema_version": "spiritkin.ios.monitor.v1",
        "generated_at": time.time(),
        "workspace_id": workspace_id,
        "status": "critical" if any(item["severity"] == "critical" for item in incidents) else "attention" if incidents else "healthy",
        "incident_count": len(incidents),
        "auto_repairable_count": sum(1 for item in incidents if item.get("auto_repairable")),
        "incidents": incidents,
        "validation": validation,
        "workspace_devices": overview,
        "remote_workers": snapshot.get("remote_workers") or {},
        "worker_tasks": snapshot.get("worker_tasks") or {},
        "workflow_runs": runs,
    }


def handle_ios_monitor_action(store: Any, payload: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
    action = str(payload.get("action") or "refresh").strip().lower()
    if action in {"refresh", "snapshot", "list"}:
        return {"ok": True, "monitor": build_ios_monitor_snapshot(store, workspace_id=workspace_id)}
    if action in {"auto_repair", "cleanup_stale_bindings"}:
        try:
            older_than_hours = float(payload.get("older_than_hours") or 0.1)
        except (TypeError, ValueError):
            older_than_hours = 0.1
        result = store.cleanup_state(
            older_than_hours=max(0.1, min(24.0, older_than_hours)),
            workspace_id=workspace_id or None,
            dry_run=False,
        )
        return {"ok": True, "action": action, "repair_result": result, "monitor": build_ios_monitor_snapshot(store, workspace_id=workspace_id)}
    if action == "retry_workflow_run":
        result = store.management_action(
            {
                "action": "retry_workflow_run",
                "workspace_id": workspace_id,
                "run_id": str(payload.get("run_id") or payload.get("target_id") or ""),
                "requested_by": "ios_monitor",
                "actor_role": "ios_terminal",
            },
            client="ios-monitor",
        )
        return {"ok": True, "action": action, "repair_result": result, "monitor": build_ios_monitor_snapshot(store, workspace_id=workspace_id)}
    raise ValueError(f"unsupported monitor action: {action}")
