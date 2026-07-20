from __future__ import annotations

from typing import Any

from backend.orchestrator.workflow_store import JsonWorkflowStore


def handle_workflow_run_management_action(store: JsonWorkflowStore, payload: dict[str, Any], action: str, *, actor: str, workflow_name: str) -> dict[str, Any]:
    if action in {"archive_run", "archive_workflow_run"}:
        return _archive_run(store, payload, actor=actor)
    if action in {"delete_run", "delete_workflow_run"}:
        return _delete_run(store, payload, actor=actor)
    if action in {"cleanup_runs", "cleanup_workflow_runs"}:
        keep_recent = _int_payload(payload, "keep_recent", 30)
        result = store.cleanup_runs(
            workflow_name=workflow_name,
            keep_recent=keep_recent,
            include_archived=bool(payload.get("include_archived", True)),
            actor=actor,
            reason=str(payload.get("reason") or "").strip(),
        )
        return {
            "success": True,
            "message": f"Cleaned {result.get('removed', 0)} workflow run(s).",
            "data": result,
            "error_code": "",
            "metadata": {"workflow_name": workflow_name, "keep_recent": keep_recent},
        }
    return {
        "success": False,
        "message": f"Unsupported workflow run management action: {action}",
        "data": {},
        "error_code": "unsupported_workflow_run_action",
        "metadata": {"action": action},
    }


def _archive_run(store: JsonWorkflowStore, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return _missing_run_id()
    run = store.archive_run(run_id, actor=actor, reason=str(payload.get("reason") or "").strip())
    if run is None:
        return _run_not_found(run_id)
    return {
        "success": True,
        "message": f"Archived workflow run {run_id}.",
        "data": {"run": run.snapshot()},
        "error_code": "",
        "metadata": {"run_id": run_id, "workflow_name": run.workflow_name},
    }


def _delete_run(store: JsonWorkflowStore, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return _missing_run_id()
    run = store.load_run(run_id)
    if run is None:
        return _run_not_found(run_id)
    if run.status in {"pending", "running", "waiting", "waiting_review"} and not bool(payload.get("force")):
        return {
            "success": False,
            "message": f"Workflow run {run_id} is active; archive it or pass force=true before deleting.",
            "data": {"run": run.snapshot()},
            "error_code": "workflow_run_active",
            "metadata": {"run_id": run_id, "workflow_name": run.workflow_name, "status": run.status},
        }
    result = store.delete_run(run_id, actor=actor, reason=str(payload.get("reason") or "").strip())
    return {
        "success": bool(result.get("deleted")),
        "message": f"Deleted workflow run {run_id}.",
        "data": result,
        "error_code": "",
        "metadata": {"run_id": run_id, "workflow_name": run.workflow_name},
    }


def _missing_run_id() -> dict[str, Any]:
    return {"success": False, "message": "run_id is required.", "data": {}, "error_code": "missing_run_id", "metadata": {}}


def _run_not_found(run_id: str) -> dict[str, Any]:
    return {"success": False, "message": f"Workflow run not found: {run_id}", "data": {}, "error_code": "workflow_run_not_found", "metadata": {"run_id": run_id}}


def _int_payload(payload: dict[str, Any], key: str, fallback: int) -> int:
    raw = payload.get(key)
    if raw in (None, ""):
        return max(0, fallback)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = fallback
    return max(0, value)
