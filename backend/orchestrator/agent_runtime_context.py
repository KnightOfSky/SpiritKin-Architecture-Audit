from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from backend.agents.base import AgentContext
from backend.orchestrator.agent_container import (
    AgentCapabilityContainer,
    build_agent_capability_container,
    build_agent_runtime_policy,
    capability_records_for_agent,
    skills_for_agent_container,
)
from backend.orchestrator.code_workspace_context import build_code_workspace_context, code_context_enabled


@dataclass(frozen=True)
class AgentRuntimeContextServices:
    profiles_by_id: dict[str, dict[str, object]]
    managed_agents: dict[str, object]
    adapters_by_id: dict[str, Any]
    knowledge_resolver: Any
    capability_registry: Any
    skill_registry: Any
    worker_pool: Any
    route_brain_for_agent: Callable[..., Any]
    recent_failure_count: Callable[[], int]
    queued_task_count: Callable[[], int]
    describe_plan: Callable[[AgentContext, Any], Any]


class AgentRuntimeContextBuilder:
    def __init__(self, services: AgentRuntimeContextServices):
        self._services = services

    def runtime_policy(self, agent_id: str) -> dict[str, object]:
        return build_agent_runtime_policy(
            agent_id,
            profiles_by_id=self._services.profiles_by_id,
            managed_agents=self._services.managed_agents,
        )

    def capability_container(
        self,
        agent_id: str,
        *,
        plan: Any = None,
        context: AgentContext | None = None,
    ) -> AgentCapabilityContainer:
        agent_id = str(agent_id or "main_text").strip() or "main_text"
        profile = dict(self._services.profiles_by_id.get(agent_id, {}))
        if not profile and agent_id == "main_text":
            route = self._services.managed_agents.get("agent_profiles_by_id", {})
            if isinstance(route, dict):
                profile = dict(route.get(agent_id, {}) or {})
        adapter = self._services.adapters_by_id.get(agent_id)
        adapter_policy = getattr(getattr(adapter, "policy", None), "snapshot", lambda: {})()
        capability_ids = list(profile.get("capabilities") or [])
        brain_decision = self._services.route_brain_for_agent(
            agent_id,
            context.user_input if context is not None else "",
            route=str(getattr(plan, "route", "") or ""),
            domain=str(getattr(plan, "domain", "") or profile.get("domain") or ""),
            required_capabilities=capability_ids,
        )
        policy = self.runtime_policy(agent_id)
        return build_agent_capability_container(
            agent_id=agent_id,
            profile={**policy, **profile},
            adapter_policy=adapter_policy if isinstance(adapter_policy, dict) else {},
            capability_records=capability_records_for_agent(
                self._services.capability_registry.list_records(),
                agent_id,
                capability_ids,
            ),
            skills=skills_for_agent_container(
                self._services.skill_registry.list_specs(),
                agent_id,
                capability_ids,
            ),
            knowledge_base=policy.get("knowledge_base") if isinstance(policy.get("knowledge_base"), dict) else {},
            brain_decision=brain_decision,
            state={
                "recent_failure_count": self._services.recent_failure_count(),
                "queued_task_count": self._services.queued_task_count(),
                "route": str(getattr(plan, "route", "") or ""),
            },
        )

    def architecture_metadata(self, plan: Any, agent_id: str = "") -> dict[str, object]:
        agent_id = str(agent_id or getattr(getattr(plan, "agent", None), "name", "") or "").strip()
        route = str(getattr(plan, "route", "") or "")
        worker_kind = "executor" if route == "clarify_openclaw" else route if route in {"executor", "tool", "skill"} else "agent"
        policy = self.runtime_policy(agent_id)
        container = self.capability_container(agent_id or "main_text", plan=plan)
        return {
            "framework": self._services.managed_agents.get("framework", "spiritkin_unified_agent_cluster"),
            "control_plane": {
                "runtime": "SpiritKinRuntime",
                "orchestrator": "AgentCluster",
                "llm_role": "optional_route_and_plan_only",
                "execution_gate": "ToolRegistry + ExecutionGuard + WorkerPool",
            },
            "layers": [
                {"level": 1, "name": "chief_dispatcher", "component": "AgentCluster/Planner", "agent_id": "main_text"},
                {
                    "level": 2,
                    "name": "specialist_agent",
                    "component": agent_id or route or "general",
                    "agent_id": agent_id,
                    "domain": getattr(plan, "domain", "general"),
                    "framework": policy.get("framework", "native"),
                    "adapter": policy.get("adapter", "spiritkin_native"),
                },
                {"level": 3, "name": "worker_agent_or_executor", "component": worker_kind},
            ],
            "openclaw_layer": "worker_executor",
            "policy": policy,
            "capability_container": container.snapshot(),
            "brain_router": container.brain_decision.snapshot() if container.brain_decision is not None else {},
        }

    def with_runtime_context(self, context: AgentContext, plan: Any) -> AgentContext:
        agent_id = str(getattr(getattr(plan, "agent", None), "name", "") or "").strip()
        metadata = dict(context.metadata)
        architecture = self.architecture_metadata(plan, agent_id)
        if agent_id:
            container = self.capability_container(agent_id, plan=plan, context=context)
            architecture["capability_container"] = container.snapshot()
            architecture["brain_router"] = container.brain_decision.snapshot() if container.brain_decision is not None else {}
        metadata["agent_runtime"] = architecture
        metadata.setdefault("hybrid_planner", self._services.describe_plan(context, plan).snapshot())
        enriched = replace(context, metadata=metadata)
        policy = architecture.get("policy")
        if isinstance(policy, dict):
            enriched = self._services.knowledge_resolver.with_hits(enriched, policy)
        if agent_id == "programming":
            enriched = self.with_code_workspace_context(enriched)
        return enriched

    def with_code_workspace_context(self, context: AgentContext) -> AgentContext:
        metadata = dict(context.metadata or {})
        if not code_context_enabled(metadata):
            return context
        try:
            record = build_code_workspace_context(
                worker_pool=self._services.worker_pool,
                repo_path=str(metadata.get("code_workspace_repo_path") or metadata.get("repo_path") or ""),
                include_diff=bool(metadata.get("include_code_diff") or metadata.get("code_workspace_include_diff")),
            )
        except Exception as exc:
            record = {
                "requested": True,
                "success": False,
                "error_code": "code_workspace_context_exception",
                "message": str(exc),
                "read_only": True,
            }
        metadata["code_workspace_context"] = record
        return replace(context, metadata=metadata)
