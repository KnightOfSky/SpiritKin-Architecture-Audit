from __future__ import annotations

from typing import Any

from backend.orchestrator.context_store import ContextPatch
from backend.orchestrator.execution_finalizer import ExecutionSummary
from backend.orchestrator.workflow_graph import (
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_SUCCEEDED,
    RUN_WAITING,
    RUN_WAITING_REVIEW,
    WorkflowDefinition,
    WorkflowRun,
)

WORKFLOW_RUNTIME_CONTRACT_VERSION = "spiritkin.workflow_runtime_contracts.v1"


def workflow_run_context_patches(
    definition: WorkflowDefinition,
    run: WorkflowRun,
    *,
    context_id: str = "",
    actor: str = "workflow_runner",
) -> list[ContextPatch]:
    resolved_context_id = context_id or f"workflow:{run.run_id}"
    metadata = {
        "schema_version": WORKFLOW_RUNTIME_CONTRACT_VERSION,
        "workflow_name": run.workflow_name,
        "workflow_version": run.workflow_version,
        "views": ["task"],
    }
    patches = [
        ContextPatch(
            context_id=resolved_context_id,
            patch_type="set",
            actor=actor,
            path="/workflow/run",
            value=run.snapshot(),
            metadata=metadata,
        ),
        ContextPatch(
            context_id=resolved_context_id,
            patch_type="set",
            actor=actor,
            path="/workflow/definition",
            value={
                "name": definition.name,
                "version": definition.version,
                "description": definition.description,
                "metadata": dict(definition.metadata or {}),
                "node_count": len(definition.nodes),
            },
            metadata=metadata,
        ),
    ]
    for node_id, node_run in run.nodes.items():
        patches.append(
            ContextPatch(
                context_id=resolved_context_id,
                patch_type="set",
                actor=actor,
                path=f"/workflow/nodes/{node_id}",
                value=node_run.snapshot(),
                metadata={**metadata, "views": ["task", "worker"]},
            )
        )
    return patches


def workflow_run_execution_summary(definition: WorkflowDefinition, run: WorkflowRun, *, task_id: str = "") -> ExecutionSummary:
    success_criteria = _success_criteria(definition)
    success_checks = _success_checks(success_criteria, run)
    return ExecutionSummary(
        task_id=task_id or run.run_id,
        status=_finalizer_status(run.status),
        success=run.status == RUN_SUCCEEDED,
        artifacts=tuple(dict(item) for item in run.artifacts if isinstance(item, dict)),
        success_criteria=tuple(success_criteria),
        metadata={
            "schema_version": WORKFLOW_RUNTIME_CONTRACT_VERSION,
            "workflow_name": run.workflow_name,
            "workflow_version": run.workflow_version,
            "run_id": run.run_id,
            "success_checks": success_checks,
            "node_status_counts": _node_status_counts(run),
        },
    )


def workflow_run_contract_snapshot(definition: WorkflowDefinition, run: WorkflowRun, *, context_id: str = "") -> dict[str, Any]:
    summary = workflow_run_execution_summary(definition, run)
    patches = workflow_run_context_patches(definition, run, context_id=context_id)
    return {
        "schema_version": WORKFLOW_RUNTIME_CONTRACT_VERSION,
        "context_id": context_id or f"workflow:{run.run_id}",
        "execution_summary": {
            "task_id": summary.task_id,
            "status": summary.status,
            "success": summary.success,
            "success_criteria": list(summary.success_criteria),
            "metadata": dict(summary.metadata or {}),
        },
        "context_patches": [patch.snapshot() for patch in patches],
    }


def _success_criteria(definition: WorkflowDefinition) -> list[str]:
    raw = definition.metadata.get("success_criteria") if isinstance(definition.metadata, dict) else None
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _success_checks(success_criteria: list[str], run: WorkflowRun) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for event in run.events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_checks = payload.get("success_checks") if isinstance(payload.get("success_checks"), dict) else {}
        for key, value in event_checks.items():
            checks[str(key)] = bool(value)
    return checks


def _finalizer_status(run_status: str) -> str:
    if run_status == RUN_SUCCEEDED:
        return "COMPLETED"
    if run_status == RUN_FAILED:
        return "FAILED"
    if run_status == RUN_BLOCKED:
        return "PARTIAL_SUCCESS"
    if run_status in {RUN_WAITING, RUN_WAITING_REVIEW}:
        return "WAITING"
    return "RUNNING"


def _node_status_counts(run: WorkflowRun) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in run.nodes.values():
        counts[node.status] = counts.get(node.status, 0) + 1
    return counts
