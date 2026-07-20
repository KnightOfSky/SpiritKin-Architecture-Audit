from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.workflow_management import build_workflow_management_snapshot, handle_workflow_management_action
from backend.orchestrator.workflow_store import JsonWorkflowStore

HOST_EXECUTION_ACTIONS = {"run_next", "run_node", "auto_advance_runs"}
RUN_SCOPED_ACTIONS = {
    "approve_review",
    "assign_agent",
    "claim_agent_task",
    "complete_agent_task",
    "signal_node",
    "retry_node",
    "reset_run",
    "archive_run",
    "archive_workflow_run",
    "delete_run",
    "delete_workflow_run",
}
DEFINITION_MUTATION_ACTIONS = {
    "compose_definition",
    "upsert_definition",
    "delete_definition",
    "rollback_definition",
}
BUILTIN_MATERIALIZATION_ACTIONS = {"save_builtin_definition", "save_ecommerce_definition"}


def _definition_workspace(item: dict[str, Any]) -> str:
    return str(dict(item.get("metadata") or {}).get("workspace_id") or "").strip()


def _run_workspace(item: dict[str, Any]) -> str:
    return str(dict(item.get("inputs") or {}).get("workspace_id") or "").strip()


def _project_root() -> str:
    # A paired phone controls the configured runtime root. It may not select an
    # arbitrary filesystem path through a request payload.
    return str(Path.cwd().resolve())


def build_ios_workflow_snapshot(*, workspace_id: str, workflow_name: str = "") -> dict[str, Any]:
    workspace = str(workspace_id or "").strip()
    if not workspace:
        raise PermissionError("iOS workflow access requires a workspace")
    request: dict[str, Any] = {"project_root": _project_root()}
    if workflow_name:
        request["workflow_name"] = workflow_name
    raw = build_workflow_management_snapshot(request)
    definitions = [
        dict(item)
        for item in raw.get("definitions") or []
        if isinstance(item, dict) and _definition_workspace(item) in {"", workspace}
    ]
    builtin_definitions = [
        dict(item)
        for item in raw.get("builtin_definitions") or []
        if isinstance(item, dict)
    ]
    runs = [
        dict(item)
        for item in raw.get("runs") or []
        if isinstance(item, dict) and _run_workspace(item) == workspace
    ]
    saved_names = {
        str(item.get("name") or "")
        for item in definitions
        if _definition_workspace(item) == workspace
    }
    run_counts = Counter(str(item.get("status") or "") for item in runs)
    active_count = sum(run_counts.get(status, 0) for status in ("pending", "running", "waiting", "waiting_review", "blocked"))
    overview = dict(raw.get("overview") or {})
    overview.update(
        {
            "definition_count": len(saved_names),
            "available_definition_count": len(definitions),
            "run_count": len(runs),
            "active_run_count": active_count,
            "status_counts": dict(sorted(run_counts.items())),
        }
    )
    visible_names = {str(item.get("name") or "") for item in definitions}
    selected_name = str(raw.get("selected_workflow_name") or "")
    if selected_name not in visible_names:
        selected_name = str(definitions[0].get("name") or "") if definitions else ""
    selected_definition = next(
        (item for item in definitions if str(item.get("name") or "") == selected_name),
        definitions[0] if definitions else {},
    )
    return {
        **raw,
        "workspace_id": workspace,
        "overview": overview,
        "selected_workflow_name": selected_name,
        "default_definition": selected_definition,
        "builtin_definitions": builtin_definitions,
        "definitions": definitions,
        "saved_definition_names": sorted(saved_names),
        "definition_versions": [],
        "audit_events": [],
        "runs": runs,
        "execution_owner": "runtime_host",
        "project_root": "",
        "state_dir": "",
    }


def _load_run_for_workspace(store: JsonWorkflowStore, run_id: str, workspace_id: str) -> None:
    run = store.load_run(run_id)
    if run is None:
        raise KeyError(f"workflow run not found: {run_id}")
    if str(dict(run.inputs or {}).get("workspace_id") or "").strip() != workspace_id:
        raise PermissionError("iOS terminal cannot access another workspace workflow run")


def _load_definition_workspace(store: JsonWorkflowStore, workflow_name: str) -> str | None:
    definition = store.load_definition(workflow_name)
    if definition is None:
        return None
    return str(dict(definition.metadata or {}).get("workspace_id") or "").strip()


def handle_ios_workflow_action(
    payload: dict[str, Any],
    *,
    workspace_id: str,
    actor: str = "ios_terminal",
) -> dict[str, Any]:
    workspace = str(workspace_id or "").strip()
    if not workspace:
        raise PermissionError("iOS workflow access requires a workspace")
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action == "workflow_snapshot":
        action = "snapshot"
    if action in HOST_EXECUTION_ACTIONS:
        raise PermissionError("workflow node execution is owned by the active Runtime Host")
    if action in BUILTIN_MATERIALIZATION_ACTIONS:
        raise PermissionError("iOS terminal cannot materialize a global workflow definition")

    next_payload = dict(payload)
    next_payload["action"] = action
    next_payload["workspace_id"] = workspace
    next_payload["project_root"] = _project_root()
    next_payload["actor"] = str(actor or "ios_terminal")
    store = JsonWorkflowStore(project_root=next_payload["project_root"])

    run_id = str(next_payload.get("run_id") or "").strip()
    if action in RUN_SCOPED_ACTIONS:
        if not run_id:
            raise ValueError("workflow action requires run_id")
        _load_run_for_workspace(store, run_id, workspace)

    raw_definition = next_payload.get("definition") if isinstance(next_payload.get("definition"), dict) else {}
    workflow_name = str(
        next_payload.get("workflow_name")
        or next_payload.get("name")
        or raw_definition.get("name")
        or ""
    ).strip()
    if action in DEFINITION_MUTATION_ACTIONS and workflow_name:
        existing_workspace = _load_definition_workspace(store, workflow_name)
        if existing_workspace is not None and existing_workspace != workspace:
            raise PermissionError("iOS terminal cannot modify a global or another-workspace workflow")

    if action == "upsert_definition":
        definition = dict(raw_definition)
        if not definition:
            raise ValueError("upsert_definition requires definition")
        metadata = dict(definition.get("metadata") or {})
        metadata["workspace_id"] = workspace
        definition["metadata"] = metadata
        next_payload["definition"] = definition
    elif action == "compose_definition":
        next_payload["workspace_id"] = workspace
    elif action == "start_run":
        if workflow_name:
            existing_workspace = _load_definition_workspace(store, workflow_name)
            if existing_workspace not in {None, "", workspace}:
                raise PermissionError("iOS terminal cannot start another workspace workflow")
        inputs = dict(next_payload.get("inputs") or {}) if isinstance(next_payload.get("inputs"), dict) else {}
        inputs["workspace_id"] = workspace
        next_payload["inputs"] = inputs

    result = handle_workflow_management_action(next_payload)
    result["workflows"] = build_ios_workflow_snapshot(workspace_id=workspace, workflow_name=workflow_name)
    return result
