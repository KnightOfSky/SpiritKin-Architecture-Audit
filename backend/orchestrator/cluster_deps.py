from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.orchestrator.agent_cluster_wiring import AgentClusterWiring


@dataclass(frozen=True)
class ClusterLlmDeps:
    client: Any = None
    brain_router: Any = None
    hybrid_planner: Any = None
    intent_resolver: Any = None
    decision_cache: Any = None
    managed_agents: dict[str, object] | None = None
    voice_intent_mode: str | None = None


@dataclass(frozen=True)
class ClusterMemoryDeps:
    session_manager: Any = None
    workflow_memory: Any = None
    skill_store: Any = None
    personality_store: Any = None
    long_term_memory: Any = None
    relationship_store: Any = None
    knowledge_retriever: Any = None
    knowledge_backend: str = "keyword"
    auto_load_project_docs: bool = True


@dataclass(frozen=True)
class ClusterExecutionDeps:
    device_backend: Any = None
    agents: Any = None
    executors: Any = None
    node_registry: Any = None
    feishu_client: Any = None
    openclaw_client: Any = None
    openclaw_client_factory: Any = None
    openclaw_state_path: str | None = None
    tool_registry: Any = None
    skill_registry: Any = None
    planner: Any = None
    resource_budget: Any = None
    task_queue: Any = None
    ecommerce_projects: Any = None
    capability_registry: Any = None
    resource_registry: Any = None
    resource_registry_path: Any = None
    worker_pool: Any = None
    pending_execution_path: Any = None


@dataclass(frozen=True)
class ClusterSafetyDeps:
    policy_engine: Any = None
    repair_advisor: Any = None
    failure_log_limit: int = 20


@dataclass(frozen=True)
class ClusterEventDeps:
    performance_tracker: Any = None
    trajectory_analyzer: Any = None
    app_port: Any = None


@dataclass(frozen=True)
class ClusterDeps:
    llm: ClusterLlmDeps
    memory: ClusterMemoryDeps
    execution: ClusterExecutionDeps
    safety: ClusterSafetyDeps
    events: ClusterEventDeps

    @classmethod
    def from_wiring(cls, llm_client: Any, wiring: AgentClusterWiring) -> ClusterDeps:
        return cls(
            llm=ClusterLlmDeps(
                client=llm_client,
                brain_router=wiring.brain_router,
                hybrid_planner=wiring.hybrid_planner,
                intent_resolver=wiring.intent_resolver,
                decision_cache=wiring.decision_cache,
                managed_agents=wiring.managed_agents,
                voice_intent_mode=wiring.voice_intent_mode,
            ),
            memory=ClusterMemoryDeps(
                session_manager=wiring.session_manager,
                workflow_memory=wiring.workflow_memory,
                skill_store=wiring.skill_store,
                personality_store=wiring.personality_store,
                long_term_memory=wiring.long_term_memory,
                relationship_store=wiring.relationship_store,
                knowledge_retriever=wiring.knowledge_retriever,
                knowledge_backend=wiring.knowledge_backend,
                auto_load_project_docs=wiring.auto_load_project_docs,
            ),
            execution=ClusterExecutionDeps(
                device_backend=wiring.device_backend,
                agents=wiring.agents,
                executors=wiring.executors,
                node_registry=wiring.node_registry,
                feishu_client=wiring.feishu_client,
                openclaw_client=wiring.openclaw_client,
                openclaw_client_factory=wiring.openclaw_client_factory,
                openclaw_state_path=wiring.openclaw_state_path,
                tool_registry=wiring.tool_registry,
                skill_registry=wiring.skill_registry,
                planner=wiring.planner,
                resource_budget=wiring.resource_budget,
                task_queue=wiring.task_queue,
                ecommerce_projects=wiring.ecommerce_projects,
                capability_registry=wiring.capability_registry,
                resource_registry=wiring.resource_registry,
                resource_registry_path=wiring.resource_registry_path,
                worker_pool=wiring.worker_pool,
                pending_execution_path=wiring.pending_execution_path,
            ),
            safety=ClusterSafetyDeps(
                policy_engine=wiring.policy_engine,
                repair_advisor=wiring.repair_advisor,
                failure_log_limit=wiring.failure_log_limit,
            ),
            events=ClusterEventDeps(
                performance_tracker=wiring.performance_tracker,
                trajectory_analyzer=wiring.trajectory_analyzer,
                app_port=wiring.app_port,
            ),
        )
