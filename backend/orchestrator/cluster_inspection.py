from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentReply
from backend.evaluation.self_improvement import SelfImprovementLoop
from backend.orchestrator.agent_mentions import AgentMention
from backend.orchestrator.agent_status import (
    build_agent_status_reply,
    build_agent_status_snapshot,
    extract_agent_skills,
    extract_agent_workflow_queue,
)
from backend.orchestrator.capability_inventory import build_capability_inventory
from backend.orchestrator.repair import FailureRecord
from backend.orchestrator.worker_environment import build_worker_environment_snapshot
from backend.skills import PromotionRuleSet, build_workflow_skill_specs, review_skill_candidates


@dataclass(frozen=True)
class ClusterInspectionServices:
    session_manager: Any
    tool_registry: Any
    skill_registry: Any
    skill_store: Any
    failure_log: Any
    inventory: dict[str, object]
    executors: Any
    device_backend: Any
    node_registry: Any
    get_capability_registry: Callable[[], Any]
    refresh_capability_registry: Callable[[], None]
    worker_pool: Any
    resource_registry: Any
    refresh_resource_registry: Callable[[], None]
    brain_router: Any
    workflow_memory: Any
    performance_tracker: Any
    trajectory_analyzer: Any
    app_port: Any
    agent_profiles_by_id: dict[str, dict[str, object]]
    agent_adapters_by_id: dict[str, Any]
    runtime_policy: Callable[[str], dict[str, object]]
    resource_budget: Any
    task_queue: Any
    ecommerce_projects: Any
    process_next_queued_task: Callable[[], AgentReply | None]


class ClusterInspectionFacade:
    def __init__(self, services: ClusterInspectionServices):
        self._services = services

    @property
    def memory(self):
        return self._services.session_manager.transcript

    @property
    def available_tools(self):
        return self._services.tool_registry.list_specs()

    @property
    def available_skills(self):
        self.sync_workflow_skill_candidates()
        return self._services.skill_registry.list_specs()

    @property
    def recent_failures(self):
        return list(self._services.failure_log.recent_failures)

    @property
    def recent_inventory(self) -> dict[str, object]:
        snapshot: dict[str, object] = {}
        for key, value in self._services.inventory.items():
            if isinstance(value, list):
                snapshot[key] = list(value)
            elif isinstance(value, dict):
                snapshot[key] = {scope: dict(record) for scope, record in value.items()}
            else:
                snapshot[key] = value
        return snapshot

    @property
    def capability_inventory_snapshot(self) -> dict[str, object]:
        inventory = build_capability_inventory(
            tools=self.available_tools,
            executors=self._services.executors,
            device_backend=self._services.device_backend,
        )
        snapshot = inventory.snapshot()
        snapshot["capability_graph"] = self.capability_graph_snapshot
        snapshot["worker_pool"] = self.worker_pool_snapshot
        snapshot["resource_registry"] = self.resource_registry_snapshot
        snapshot["brain_router"] = self.brain_router_snapshot
        snapshot["worker_environment"] = self.worker_environment_snapshot
        return snapshot

    @property
    def worker_environment_snapshot(self):
        return build_worker_environment_snapshot(self._services.node_registry)

    @property
    def capability_graph_snapshot(self):
        self._services.refresh_capability_registry()
        return self._services.get_capability_registry().snapshot()

    @property
    def worker_pool_snapshot(self):
        return self._services.worker_pool.snapshot()

    @property
    def resource_registry_snapshot(self):
        self._services.refresh_resource_registry()
        return self._services.resource_registry.snapshot()

    @property
    def brain_router_snapshot(self):
        return self._services.brain_router.snapshot()

    @property
    def workflow_memory_snapshot(self):
        return self._services.workflow_memory.recent()

    @property
    def workflow_memory_stats(self):
        return self._services.workflow_memory.stats() if hasattr(self._services.workflow_memory, "stats") else {}

    @property
    def workflow_skill_candidates(self):
        if hasattr(self._services.workflow_memory, "skill_candidates"):
            return self._services.workflow_memory.skill_candidates()
        return []

    def sync_workflow_skill_candidates(self) -> None:
        candidates = self.workflow_skill_candidates
        if not candidates:
            return
        changed = False
        for skill in build_workflow_skill_specs(candidates, self.available_tools):
            if self._services.skill_registry.get(skill.name) is not None:
                continue
            self._services.skill_registry.register(skill)
            changed = True
            if self._services.skill_store is not None and hasattr(self._services.skill_store, "save"):
                self._services.skill_store.save(skill)
        if changed:
            self._services.refresh_capability_registry()

    def review_skill_candidates(self, rules: PromotionRuleSet | None = None, *, reviewer: str = "rules_engine"):
        self.sync_workflow_skill_candidates()
        return review_skill_candidates(
            self._services.skill_registry,
            rules,
            store=self._services.skill_store,
            reviewer=reviewer,
        )

    @property
    def last_repair_advice(self):
        return self._services.failure_log.last_repair_advice

    def build_self_improvement_report(self):
        return SelfImprovementLoop(
            performance_tracker=self._services.performance_tracker,
            trajectory_analyzer=self._services.trajectory_analyzer,
        ).build_report()

    @property
    def resource_budget_snapshot(self):
        return self._services.resource_budget.snapshot()

    @property
    def task_queue_snapshot(self):
        return self._services.task_queue.list_tasks()

    def build_agent_status_snapshot(self, mention: AgentMention) -> dict[str, object]:
        agent_id = mention.agent_id
        try:
            workflow_queue = extract_agent_workflow_queue(
                self._services.app_port.workflow_management_snapshot(),
                agent_id,
            )
        except Exception as exc:
            workflow_queue = [{"error": f"{type(exc).__name__}: {exc}"}]
        try:
            recent_performance = (
                self._services.performance_tracker.snapshot().get(agent_id, {})
                if hasattr(self._services.performance_tracker, "snapshot")
                else {}
            )
        except Exception:
            recent_performance = {}
        return build_agent_status_snapshot(
            mention,
            profile=dict(self._services.agent_profiles_by_id.get(agent_id, {})),
            runtime_policy=self._services.runtime_policy(agent_id),
            adapter=self._services.agent_adapters_by_id.get(agent_id),
            skills=extract_agent_skills(self.available_skills, agent_id),
            workflow_queue=workflow_queue,
            task_queue=self.task_queue_snapshot,
            recent_performance=recent_performance,
        )

    def handle_agent_status(self, mention: AgentMention) -> AgentReply:
        return build_agent_status_reply(mention, self.build_agent_status_snapshot(mention))

    @property
    def ecommerce_projects_snapshot(self):
        return self._services.ecommerce_projects.list_projects()

    def get_task(self, task_id: str):
        task = self._services.task_queue.get(task_id)
        return task.snapshot() if task is not None else None

    def list_active_tasks(self):
        return self._services.task_queue.list_tasks(include_finished=False)

    def get_ecommerce_project(self, project_id: str):
        return self._services.ecommerce_projects.get_snapshot(project_id)

    def list_active_ecommerce_projects(self, project_type: str | None = None, status: str | None = None):
        return self._services.ecommerce_projects.list_projects(
            active_only=True,
            project_type=project_type,
            status=status,
        )

    def drain_queued_tasks(self, max_tasks: int = 1) -> list[AgentReply]:
        replies: list[AgentReply] = []
        for _ in range(max(0, int(max_tasks))):
            reply = self._services.process_next_queued_task()
            if reply is None:
                break
            replies.append(reply)
            if reply.metadata.get("response_kind") == "scheduler_busy":
                break
        return replies

    def clear_recent_failures(self) -> None:
        self._services.failure_log.clear()

    def build_repair_plan(self, failure: FailureRecord | None = None):
        return self._services.failure_log.build_repair_plan(failure)
