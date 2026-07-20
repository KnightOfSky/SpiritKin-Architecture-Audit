"""Capability-registry refresh orchestration extracted from AgentCluster (C).

Owns the three-way relinking of capability registry + worker pool + hybrid
planner. The graph inputs (tools/skills/agents/executors) and the node registry
are held as references; the worker pool and base planner are passed into
``refresh`` because AgentCluster constructs them after the initial registry.
Logic preserved verbatim from the former ``_refresh_capability_registry`` /
``_build_capability_registry`` / ``_attach_worker_pool_to_tools`` /
``_refresh_external_worker_pool_descriptors`` methods.
"""

from __future__ import annotations

from backend.orchestrator.capability_builders import (
    build_capability_registry_with_companion,
    gather_external_worker_descriptors,
)
from backend.orchestrator.capability_graph import CapabilityRegistry
from backend.orchestrator.hybrid_planner import HybridPlannerPipeline


class CapabilityManager:
    def __init__(self, *, tool_registry, skill_registry, node_registry, agents, executors) -> None:
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry
        self._node_registry = node_registry
        self._agents = agents
        self._executors = executors

    def build_registry(self) -> CapabilityRegistry:
        return build_capability_registry_with_companion(
            tools=self._tool_registry.list_specs(),
            skills=self._skill_registry.list_specs(),
            agents=self._agents,
            executors=self._executors,
        )

    def external_worker_descriptors(self) -> list:
        return gather_external_worker_descriptors(self._node_registry)

    def attach_worker_pool_to_tools(self, worker_pool) -> None:
        for tool in self._tool_registry.list_tools():
            setter = getattr(tool, "set_worker_pool", None)
            if callable(setter):
                setter(worker_pool)

    def refresh(self, *, worker_pool, planner) -> tuple[CapabilityRegistry, HybridPlannerPipeline]:
        registry = self.build_registry()
        worker_pool.set_capability_registry(registry)
        worker_pool.set_external_workers(self.external_worker_descriptors())
        self.attach_worker_pool_to_tools(worker_pool)
        hybrid_planner = HybridPlannerPipeline(base_planner=planner, capability_registry=registry)
        return registry, hybrid_planner
