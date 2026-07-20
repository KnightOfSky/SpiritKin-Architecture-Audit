from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from backend.orchestrator.execution_finalizer import FinalizerVerdict
from backend.orchestrator.workflow_graph import WorkflowRun

WORKFLOW_TASK_FINALIZER_VERSION = "spiritkin.workflow_task_finalizer.v1"


class CollaborationTaskFinalizerPort(Protocol):
    def load_task(self, task_id: str, root: Path | None) -> Any | None: ...

    def update_task(self, payload: dict[str, Any], root: Path | None) -> Any: ...


@dataclass(frozen=True)
class TaskFinalizerSyncResult:
    ok: bool
    status: str
    task_id: str = ""
    run_id: str = ""
    source: str = ""
    previous_status: str = ""
    next_status: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": WORKFLOW_TASK_FINALIZER_VERSION,
            "ok": self.ok,
            "status": self.status,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "source": self.source,
            "previous_status": self.previous_status,
            "next_status": self.next_status,
            "message": self.message,
            "metadata": dict(self.metadata or {}),
        }


def sync_workflow_verdict_to_task(
    run: WorkflowRun,
    verdict: FinalizerVerdict,
    *,
    project_root: str | Path | None = None,
    collaboration_root: str | Path | None = None,
    collaboration_port: CollaborationTaskFinalizerPort | None = None,
) -> TaskFinalizerSyncResult:
    ecommerce_binding = workflow_ecommerce_task_binding(run)
    if ecommerce_binding.get("task_id"):
        return sync_workflow_verdict_to_ecommerce_task(
            run,
            verdict,
            project_root=project_root,
            binding=ecommerce_binding,
        )

    binding = workflow_task_binding(run)
    task_id = binding.get("task_id", "")
    if not task_id:
        return TaskFinalizerSyncResult(
            ok=True,
            status="skipped",
            run_id=run.run_id,
            source=binding.get("source", ""),
            message="workflow run has no task binding",
            metadata={"verdict": verdict.snapshot()},
        )

    if collaboration_port is None:
        return TaskFinalizerSyncResult(
            ok=False,
            status="integration_unavailable",
            task_id=task_id,
            run_id=run.run_id,
            source=binding.get("source", ""),
            message="collaboration task finalizer port is not configured",
            metadata={"verdict": verdict.snapshot()},
        )
    try:
        root = _collaboration_root(project_root=project_root, collaboration_root=collaboration_root)
        current = collaboration_port.load_task(task_id, root)
        if current is None:
            return TaskFinalizerSyncResult(
                ok=False,
                status="task_not_found",
                task_id=task_id,
                run_id=run.run_id,
                source=binding.get("source", ""),
                message=f"collaboration task not found: {task_id}",
                metadata={"verdict": verdict.snapshot(), "collaboration_root": str(root) if root else ""},
            )

        next_status = collaboration_status_for_verdict(verdict)
        note = _task_note(current.note, run=run, verdict=verdict)
        updated = collaboration_port.update_task(
            {
                "task_id": task_id,
                "status": next_status,
                "note": note,
                "workflow_run_id": run.run_id,
                "workflow_name": run.workflow_name,
                "finalizer_decision": verdict.decision,
                "finalizer_next_status": verdict.next_status,
            },
            root,
        )
        return TaskFinalizerSyncResult(
            ok=True,
            status="updated",
            task_id=task_id,
            run_id=run.run_id,
            source=binding.get("source", ""),
            previous_status=current.status,
            next_status=updated.status,
            message=f"collaboration task {task_id} updated from workflow verdict",
            metadata={"verdict": verdict.snapshot(), "collaboration_root": str(root) if root else ""},
        )
    except Exception as exc:
        return TaskFinalizerSyncResult(
            ok=False,
            status="error",
            task_id=task_id,
            run_id=run.run_id,
            source=binding.get("source", ""),
            message=f"{type(exc).__name__}: {exc}",
            metadata={"verdict": verdict.snapshot()},
        )


def sync_workflow_verdict_to_ecommerce_task(
    run: WorkflowRun,
    verdict: FinalizerVerdict,
    *,
    project_root: str | Path | None = None,
    binding: dict[str, str] | None = None,
) -> TaskFinalizerSyncResult:
    resolved_binding = dict(binding or workflow_ecommerce_task_binding(run))
    task_id = resolved_binding.get("task_id", "")
    state_dir = resolved_binding.get("state_dir", "")
    if not task_id:
        return TaskFinalizerSyncResult(
            ok=True,
            status="skipped",
            run_id=run.run_id,
            source=resolved_binding.get("source", ""),
            message="workflow run has no ecommerce task binding",
            metadata={"verdict": verdict.snapshot()},
        )

    try:
        from backend.orchestrator.ecommerce_task_queue import (
            add_history,
            append_event,
            load_queue,
            save_queue,
            task_by_id,
        )

        queue = load_queue(state_dir or None, project_root=project_root)
        task = task_by_id(queue, task_id)
        if task is None:
            return TaskFinalizerSyncResult(
                ok=False,
                status="task_not_found",
                task_id=task_id,
                run_id=run.run_id,
                source=resolved_binding.get("source", ""),
                message=f"ecommerce task not found: {task_id}",
                metadata={"verdict": verdict.snapshot(), "state_dir": state_dir},
            )

        previous_status = str(task.get("status") or "")
        next_status = ecommerce_status_for_verdict(verdict)
        verdict_snapshot = verdict.snapshot()
        task["status"] = next_status
        task["workflow_run_id"] = run.run_id
        task["workflow_name"] = run.workflow_name
        task["finalizer_decision"] = verdict.decision
        task["finalizer_next_status"] = verdict.next_status
        task.setdefault("checks", {})["workflow_finalizer"] = verdict_snapshot
        add_history(
            task,
            "workflow_finalizer_synced",
            {
                "workflow_name": run.workflow_name,
                "run_id": run.run_id,
                "decision": verdict.decision,
                "next_status": verdict.next_status,
                "task_status": next_status,
            },
        )
        save_queue(queue, state_dir or None, project_root=project_root)
        append_event(
            {
                "type": "workflow_finalizer_task_sync",
                "task_id": task_id,
                "payload": {
                    "queue": "ecommerce",
                    "workflow_name": run.workflow_name,
                    "run_id": run.run_id,
                    "previous_status": previous_status,
                    "next_status": next_status,
                    "verdict": verdict_snapshot,
                },
            },
            state_dir or None,
            project_root=project_root,
        )
        return TaskFinalizerSyncResult(
            ok=True,
            status="updated",
            task_id=task_id,
            run_id=run.run_id,
            source=resolved_binding.get("source", ""),
            previous_status=previous_status,
            next_status=next_status,
            message=f"ecommerce task {task_id} updated from workflow verdict",
            metadata={"verdict": verdict_snapshot, "queue": "ecommerce", "state_dir": state_dir},
        )
    except Exception as exc:
        return TaskFinalizerSyncResult(
            ok=False,
            status="error",
            task_id=task_id,
            run_id=run.run_id,
            source=resolved_binding.get("source", ""),
            message=f"{type(exc).__name__}: {exc}",
            metadata={"verdict": verdict.snapshot(), "queue": "ecommerce", "state_dir": state_dir},
        )


def workflow_task_binding(run: WorkflowRun) -> dict[str, str]:
    inputs = dict(run.inputs or {})
    for key in ("task_id", "collaboration_task_id", "ledger_task_id"):
        value = str(inputs.get(key) or "").strip()
        if value:
            return {"task_id": value, "source": f"run.inputs.{key}"}
    metadata = inputs.get("metadata") if isinstance(inputs.get("metadata"), dict) else {}
    for key in ("task_id", "collaboration_task_id", "ledger_task_id"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return {"task_id": value, "source": f"run.inputs.metadata.{key}"}
    return {"task_id": "", "source": ""}


def workflow_ecommerce_task_binding(run: WorkflowRun) -> dict[str, str]:
    inputs = dict(run.inputs or {})
    metadata = inputs.get("metadata") if isinstance(inputs.get("metadata"), dict) else {}
    task_keys = ("ecommerce_task_id", "commerce_task_id")
    state_keys = ("ecommerce_state_dir", "ecommerce_task_state_dir", "ecommerce_task_queue_state_dir")

    task_id = ""
    source = ""
    for key in task_keys:
        value = str(inputs.get(key) or "").strip()
        if value:
            task_id = value
            source = f"run.inputs.{key}"
            break
    if not task_id:
        for key in task_keys:
            value = str(metadata.get(key) or "").strip()
            if value:
                task_id = value
                source = f"run.inputs.metadata.{key}"
                break

    state_dir = ""
    for key in state_keys:
        value = str(inputs.get(key) or "").strip()
        if value:
            state_dir = value
            break
    if not state_dir:
        for key in state_keys:
            value = str(metadata.get(key) or "").strip()
            if value:
                state_dir = value
                break

    return {"task_id": task_id, "source": source, "state_dir": state_dir}


def collaboration_status_for_verdict(verdict: FinalizerVerdict) -> str:
    if verdict.decision == "commit" and verdict.next_status == "COMMITTED":
        return "complete"
    if verdict.decision == "review":
        return "review"
    if verdict.decision == "wait":
        return "waiting"
    if verdict.decision == "retry":
        return "blocked"
    return "active"


def ecommerce_status_for_verdict(verdict: FinalizerVerdict) -> str:
    if verdict.decision == "commit" and verdict.next_status == "COMMITTED":
        return "workflow_complete"
    if verdict.decision == "review":
        return "workflow_review"
    if verdict.decision == "wait":
        return "workflow_waiting"
    if verdict.decision == "retry":
        return "workflow_blocked"
    return "workflow_waiting"


def _collaboration_root(*, project_root: str | Path | None, collaboration_root: str | Path | None) -> Path | None:
    if collaboration_root:
        return Path(collaboration_root)
    if project_root:
        return Path(project_root) / "state" / "collaboration"
    return None


def _task_note(existing_note: str, *, run: WorkflowRun, verdict: FinalizerVerdict) -> str:
    line = f"Workflow {run.workflow_name}/{run.run_id}: finalizer {verdict.decision} -> {verdict.next_status}"
    existing = str(existing_note or "").strip()
    if line in existing:
        return existing
    return f"{existing}\n{line}".strip() if existing else line
