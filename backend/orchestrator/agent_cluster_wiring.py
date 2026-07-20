from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any


@dataclass(frozen=True)
class AgentClusterWiring:
    """Grouped optional dependencies and policies for AgentCluster assembly."""

    device_backend: Any = None
    agents: Any = None
    executors: Any = None
    node_registry: Any = None
    feishu_client: Any = None
    openclaw_client: Any = None
    openclaw_client_factory: Any = None
    openclaw_state_path: str | None = None
    knowledge_retriever: Any = None
    auto_load_project_docs: bool = True
    knowledge_backend: str = "keyword"
    tool_registry: Any = None
    skill_registry: Any = None
    planner: Any = None
    session_manager: Any = None
    resource_budget: Any = None
    task_queue: Any = None
    ecommerce_projects: Any = None
    repair_advisor: Any = None
    intent_resolver: Any = None
    workflow_memory: Any = None
    decision_cache: Any = None
    voice_intent_mode: str | None = None
    failure_log_limit: int = 20
    skill_store: Any = None
    policy_engine: Any = None
    personality_store: Any = None
    long_term_memory: Any = None
    relationship_store: Any = None
    performance_tracker: Any = None
    trajectory_analyzer: Any = None
    managed_agents: dict[str, object] | None = None
    capability_registry: Any = None
    resource_registry: Any = None
    resource_registry_path: Any = None
    worker_pool: Any = None
    brain_router: Any = None
    app_port: Any = None
    hybrid_planner: Any = None
    pending_execution_path: Any = None

    @classmethod
    def from_deps(cls, deps: Any) -> AgentClusterWiring:
        return cls(
            device_backend=deps.execution.device_backend,
            agents=deps.execution.agents,
            executors=deps.execution.executors,
            node_registry=deps.execution.node_registry,
            feishu_client=deps.execution.feishu_client,
            openclaw_client=deps.execution.openclaw_client,
            openclaw_client_factory=deps.execution.openclaw_client_factory,
            openclaw_state_path=deps.execution.openclaw_state_path,
            knowledge_retriever=deps.memory.knowledge_retriever,
            auto_load_project_docs=deps.memory.auto_load_project_docs,
            knowledge_backend=deps.memory.knowledge_backend,
            tool_registry=deps.execution.tool_registry,
            skill_registry=deps.execution.skill_registry,
            planner=deps.execution.planner,
            session_manager=deps.memory.session_manager,
            resource_budget=deps.execution.resource_budget,
            task_queue=deps.execution.task_queue,
            ecommerce_projects=deps.execution.ecommerce_projects,
            repair_advisor=deps.safety.repair_advisor,
            intent_resolver=deps.llm.intent_resolver,
            workflow_memory=deps.memory.workflow_memory,
            decision_cache=deps.llm.decision_cache,
            voice_intent_mode=deps.llm.voice_intent_mode,
            failure_log_limit=deps.safety.failure_log_limit,
            skill_store=deps.memory.skill_store,
            policy_engine=deps.safety.policy_engine,
            personality_store=deps.memory.personality_store,
            long_term_memory=deps.memory.long_term_memory,
            relationship_store=deps.memory.relationship_store,
            performance_tracker=deps.events.performance_tracker,
            trajectory_analyzer=deps.events.trajectory_analyzer,
            managed_agents=deps.llm.managed_agents,
            capability_registry=deps.execution.capability_registry,
            resource_registry=deps.execution.resource_registry,
            resource_registry_path=deps.execution.resource_registry_path,
            worker_pool=deps.execution.worker_pool,
            brain_router=deps.llm.brain_router,
            app_port=deps.events.app_port,
            hybrid_planner=deps.llm.hybrid_planner,
            pending_execution_path=deps.execution.pending_execution_path,
        )

    def with_legacy_overrides(self, overrides: dict[str, Any]) -> AgentClusterWiring:
        valid = {item.name for item in fields(self)}
        unknown = sorted(set(overrides) - valid)
        if unknown:
            joined = ", ".join(unknown)
            raise TypeError(f"AgentCluster got unexpected keyword argument(s): {joined}")
        return replace(self, **overrides)
