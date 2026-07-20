from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from typing import Any
from uuid import uuid4

from backend.orchestrator.context_store import ContextPatch, append_context_patch

DOMAIN_STAGE_TEMPLATES = {
    "ecommerce": ("intake", "diagnose", "optimize", "deliver"),
    "video_animation": ("intake", "script", "storyboard", "asset_plan", "synthesis"),
    "programming": ("intake", "analyze", "implement", "validate"),
    "game_development": ("intake", "design", "systems", "validate"),
    "search": ("intake", "retrieve", "summarize"),
    "execution": ("intake", "confirm", "execute"),
    "general": ("intake", "respond"),
    "utility": ("intake", "respond"),
    "vision": ("intake", "perceive", "respond"),
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _finalizer_reasons(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if value is None:
        return []
    try:
        return [str(item) for item in value if str(item)]
    except TypeError:
        return [str(value)] if str(value) else []


def _record_finalizer_context_patch(task: ScheduledTask, finalizer: dict[str, object]) -> ContextPatch:
    patch = ContextPatch(
        context_id=f"task:{task.task_id}",
        patch_type="set",
        actor=str(finalizer.get("source") or "scheduler_task"),
        path="/scheduler/tasks/finalizer",
        value={
            "task_id": task.task_id,
            "project_id": task.project_id,
            "status": task.status,
            "domain": task.domain,
            "route": task.route,
            "resource_profile": task.resource_profile,
            "current_stage": task.current_stage,
            "result_summary": task.result_summary,
            "last_error": task.last_error,
            "stage_statuses": {stage.name: stage.status for stage in task.stages},
            "finalizer": dict(finalizer),
        },
        metadata={
            "source": "scheduler_task_finalizer",
            "views": ["task"],
            "task_id": task.task_id,
            "project_id": task.project_id,
            "scheduler_status": task.status,
            "domain": task.domain,
            "route": task.route,
        },
    )
    return append_context_patch(patch)


@dataclass
class TaskStage:
    name: str
    status: str = "pending"
    detail: str = ""

    def snapshot(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class ScheduledTask:
    task_id: str
    request: str
    visual_context: str
    route: str
    domain: str
    priority_score: int
    resource_profile: str
    stages: list[TaskStage]
    status: str = "queued"
    current_stage: str | None = None
    attempts: int = 0
    last_error: str = ""
    result_summary: str = ""
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    sequence: int = 0
    project_id: str = ""
    finalizer: dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "request": self.request,
            "status": self.status,
            "route": self.route,
            "domain": self.domain,
            "priority_score": self.priority_score,
            "resource_profile": self.resource_profile,
            "current_stage": self.current_stage,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "result_summary": self.result_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_id": self.project_id,
            "finalizer": dict(self.finalizer),
            "stages": [stage.snapshot() for stage in self.stages],
        }


@dataclass
class TaskQueue:
    _tasks: dict[str, ScheduledTask] = field(default_factory=dict, init=False)
    _counter: count = field(default_factory=count, init=False)

    @staticmethod
    def _stage_names_for_domain(domain: str) -> tuple[str, ...]:
        return DOMAIN_STAGE_TEMPLATES.get(domain, DOMAIN_STAGE_TEMPLATES["general"])

    @staticmethod
    def _touch(task: ScheduledTask) -> ScheduledTask:
        task.updated_at = _utcnow()
        return task

    def enqueue(self, *, request: str, visual_context: str, plan, project_id: str = "") -> ScheduledTask:
        task = ScheduledTask(
            task_id=f"task_{uuid4().hex[:10]}",
            request=request,
            visual_context=visual_context,
            route=plan.route,
            domain=plan.domain,
            priority_score=int(plan.priority_score),
            resource_profile=plan.resource_profile,
            stages=[TaskStage(name=name) for name in self._stage_names_for_domain(plan.domain)],
            sequence=next(self._counter),
            project_id=project_id,
        )
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> ScheduledTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self, include_finished: bool = True) -> list[dict[str, object]]:
        tasks = list(self._tasks.values())
        if not include_finished:
            tasks = [task for task in tasks if task.status not in {"complete", "failed"}]
        ordered = sorted(tasks, key=lambda task: ({"running": 0, "blocked": 1, "queued": 2}.get(task.status, 3), -task.priority_score, task.sequence))
        return [task.snapshot() for task in ordered]

    def dequeue_next(self) -> ScheduledTask | None:
        queued = [task for task in self._tasks.values() if task.status == "queued"]
        if not queued:
            return None
        return min(queued, key=lambda task: (-task.priority_score, task.sequence))

    def start(self, task_id: str, detail: str = "") -> ScheduledTask:
        task = self._tasks[task_id]
        task.status = "running"
        task.attempts += 1
        stage = next((item for item in task.stages if item.status == "pending"), None)
        if stage is not None:
            stage.status = "running"
            stage.detail = detail
            task.current_stage = stage.name
        return self._touch(task)

    def advance(self, task_id: str, detail: str = "") -> ScheduledTask:
        task = self._tasks[task_id]
        running = next((item for item in task.stages if item.status == "running"), None)
        if running is not None:
            running.status = "complete"
            if detail:
                running.detail = detail
        next_stage = next((item for item in task.stages if item.status == "pending"), None)
        if next_stage is not None:
            next_stage.status = "running"
            next_stage.detail = detail
            task.current_stage = next_stage.name
        return self._touch(task)

    def block(self, task_id: str, reason: str) -> ScheduledTask:
        task = self._tasks[task_id]
        task.status = "blocked"
        task.last_error = reason
        running = next((item for item in task.stages if item.status == "running"), None)
        if running is not None and reason:
            running.detail = reason
            task.current_stage = running.name
        return self._touch(task)

    def complete(self, task_id: str, result_summary: str = "") -> ScheduledTask:
        task = self._tasks[task_id]
        task.status = "complete"
        task.result_summary = result_summary
        for stage in task.stages:
            if stage.status in {"pending", "running"}:
                stage.status = "complete"
        if task.stages:
            task.current_stage = task.stages[-1].name
        return self._touch(task)

    def fail(self, task_id: str, reason: str) -> ScheduledTask:
        task = self._tasks[task_id]
        task.status = "failed"
        task.last_error = reason
        stage = next((item for item in task.stages if item.status == "running"), None)
        if stage is None:
            stage = next((item for item in task.stages if item.status == "pending"), None)
        if stage is not None:
            stage.status = "failed"
            stage.detail = reason
            task.current_stage = stage.name
        return self._touch(task)

    def apply_finalizer_verdict(self, task_id: str, verdict, source: str = "scheduler_task") -> ScheduledTask:
        task = self._tasks[task_id]
        snapshot = verdict.snapshot() if hasattr(verdict, "snapshot") else dict(verdict or {})
        task.finalizer = {
            "decision": str(snapshot.get("decision") or ""),
            "next_status": str(snapshot.get("next_status") or ""),
            "score": float(snapshot.get("score") or 0.0),
            "verified": bool(snapshot.get("verified")),
            "reasons": _finalizer_reasons(snapshot.get("reasons")),
            "updated_at": _utcnow(),
            "source": source,
        }
        try:
            context_patch = _record_finalizer_context_patch(task, task.finalizer)
            task.finalizer["context_id"] = context_patch.context_id
            task.finalizer["context_patch_id"] = context_patch.patch_id
            task.finalizer["context_path"] = context_patch.path
        except Exception as exc:  # pragma: no cover - finalizer should not fail on an audit-sidecar write.
            task.finalizer["context_patch_error"] = f"{type(exc).__name__}: {exc}"
        return self._touch(task)
