from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentReply
from backend.orchestrator.planner import ExecutionPlan
from backend.orchestrator.reply_metadata import (
    attach_project_metadata,
    attach_task_metadata,
    build_context_metadata,
)


@dataclass(frozen=True)
class QueuedTaskServices:
    task_queue: Any
    resource_budget: Any
    ecommerce_projects: Any
    session_manager: Any
    device_name: str
    build_inventory_context: Callable[[], str]
    resolve_project: Callable[..., Any]
    build_busy_reply: Callable[..., AgentReply]
    plan_hybrid: Callable[[Any], Any]
    dispatch_plan: Callable[[Any, ExecutionPlan], AgentReply]
    apply_finalizer: Callable[[Any], Any]
    attach_plan_metadata: Callable[[AgentReply, ExecutionPlan], AgentReply]
    finalize_reply: Callable[[AgentReply], AgentReply]


class QueuedTaskPhase:
    def __init__(self, services: QueuedTaskServices):
        self._services = services

    def process_next(self) -> AgentReply | None:
        task = self._services.task_queue.dequeue_next()
        if task is None:
            return None
        fallback_plan = ExecutionPlan(
            route=task.route,
            reason="队列任务等待资源释放",
            domain=task.domain,
            priority_score=task.priority_score,
            resource_profile=task.resource_profile,
        )
        reservation = self._services.resource_budget.try_acquire(task.resource_profile)
        if reservation is None:
            project = self._services.ecommerce_projects.get(task.project_id) if task.project_id else None
            return self._services.finalize_reply(
                attach_project_metadata(self._services.build_busy_reply(fallback_plan, task), project)
            )

        self._services.task_queue.start(task.task_id, detail="资源已分配，开始执行")
        project = (
            self._services.ecommerce_projects.get(task.project_id)
            if task.project_id
            else self._services.resolve_project(fallback_plan, task.request, task_id=task.task_id)
        )
        if project is not None:
            task.project_id = project.project_id
            project = self._services.ecommerce_projects.note_task(
                project.project_id,
                task_id=task.task_id,
                status="running",
                detail="队列任务开始执行",
            ) or project
        inventory_context = self._services.build_inventory_context()
        context = self._services.session_manager.build_context(
            user_input=task.request,
            visual_context=task.visual_context,
            device_name=self._services.device_name,
            metadata={
                **build_context_metadata(project),
                **({"inventory_context": inventory_context} if inventory_context else {}),
            },
        )
        hybrid_result = self._services.plan_hybrid(context)
        plan = hybrid_result.execution_plan
        self._services.task_queue.advance(task.task_id, detail="进入领域执行阶段")
        try:
            reply = self._services.dispatch_plan(context, plan)
        except Exception as exc:
            return self._failed(task, project, reservation, exc)

        self._services.resource_budget.release(reservation)
        if reply.requires_confirmation:
            return self._blocked(task, project, plan, reply)
        return self._completed(task, project, plan, reply)

    def _failed(self, task: Any, project: Any, reservation: Any, exc: Exception) -> AgentReply:
        failed_task = self._services.apply_finalizer(self._services.task_queue.fail(task.task_id, str(exc)))
        if project is not None:
            project = self._services.ecommerce_projects.note_task(
                project.project_id,
                task_id=task.task_id,
                status="failed",
                detail=str(exc),
            ) or project
        self._services.resource_budget.release(reservation)
        reply = attach_task_metadata(
            AgentReply(
                text=f"队列任务执行失败：{exc}",
                emotion="confused",
                action="tilt_head",
                agent_name="scheduler",
                metadata={"response_kind": "task_failed"},
            ),
            failed_task,
        )
        return self._services.finalize_reply(attach_project_metadata(reply, project))

    def _blocked(self, task: Any, project: Any, plan: ExecutionPlan, reply: AgentReply) -> AgentReply:
        blocked_task = self._services.apply_finalizer(self._services.task_queue.block(task.task_id, "等待用户确认"))
        if project is not None:
            project = self._services.ecommerce_projects.note_task(
                project.project_id,
                task_id=task.task_id,
                status="blocked",
                detail="等待用户确认",
            ) or project
        queued_reply = attach_task_metadata(self._services.attach_plan_metadata(reply, plan), blocked_task)
        return self._services.finalize_reply(attach_project_metadata(queued_reply, project))

    def _completed(self, task: Any, project: Any, plan: ExecutionPlan, reply: AgentReply) -> AgentReply:
        completed_task = self._services.apply_finalizer(
            self._services.task_queue.complete(task.task_id, result_summary=reply.text[:120])
        )
        if project is not None:
            project = self._services.ecommerce_projects.note_task(
                project.project_id,
                task_id=task.task_id,
                status="active",
                summary=reply.text[:120],
                detail="阶段产出已返回",
            ) or project
        queued_reply = attach_task_metadata(self._services.attach_plan_metadata(reply, plan), completed_task)
        return self._services.finalize_reply(attach_project_metadata(queued_reply, project))
