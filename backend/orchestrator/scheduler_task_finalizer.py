from __future__ import annotations

from typing import Any

from backend.orchestrator.execution_finalizer import ExecutionFinalizer, ExecutionSummary, FinalizerVerdict
from backend.orchestrator.task_queue import ScheduledTask

SCHEDULER_STATUS_TO_EXECUTION_STATUS = {
    "complete": "COMPLETED",
    "failed": "FAILED",
    "blocked": "FAILED",
    "queued": "WAITING",
    "running": "WAITING",
}


def scheduler_task_execution_summary(task: ScheduledTask) -> ExecutionSummary:
    status = SCHEDULER_STATUS_TO_EXECUTION_STATUS.get(str(task.status or "").strip().lower(), "FAILED")
    success = status == "COMPLETED"
    artifacts: list[dict[str, Any]] = []
    if task.result_summary:
        artifacts.append({"type": "result_summary", "content": task.result_summary})
    return ExecutionSummary(
        task_id=task.task_id,
        status=status,
        success=success,
        artifacts=tuple(artifacts),
        metadata={
            "source": "scheduler_task",
            "scheduler_status": task.status,
            "domain": task.domain,
            "route": task.route,
            "resource_profile": task.resource_profile,
            "project_id": task.project_id,
            "current_stage": task.current_stage,
            "last_error": task.last_error,
            "stage_statuses": {stage.name: stage.status for stage in task.stages},
        },
    )


def finalize_scheduler_task(
    task: ScheduledTask,
    *,
    finalizer: ExecutionFinalizer | None = None,
) -> FinalizerVerdict:
    return (finalizer or ExecutionFinalizer()).finalize(scheduler_task_execution_summary(task))
