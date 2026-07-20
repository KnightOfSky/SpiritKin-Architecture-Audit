"""Pure capability-graph construction helpers extracted from AgentCluster (C).

These functions build the capability registry (capability graph + Android
companion worker records) and gather external worker descriptors. They contain
no AgentCluster state mutation — the orchestrator retains ownership of wiring
the results into the spine collaborators (capability registry, worker pool,
hybrid planner, tools). Logic preserved verbatim.
"""

from __future__ import annotations

from backend.orchestrator.android_worker_registry import (
    android_worker_capability_records,
    android_worker_descriptor,
)
from backend.orchestrator.capability_graph import CapabilityRegistry, build_capability_registry
from backend.orchestrator.worker_pool import planned_worker_seed_descriptors


def build_capability_registry_with_companion(*, tools, skills, agents, executors) -> CapabilityRegistry:
    registry = build_capability_registry(
        tools=tools,
        skills=skills,
        agents=agents,
        executors=executors,
        workers=planned_worker_seed_descriptors(),
    )
    try:
        from backend.mobile.android_companion_store import AndroidCompanionStore

        companion = AndroidCompanionStore().snapshot()
        worker = dict(companion.get("worker") or {})
        for record in android_worker_capability_records(worker, companion=companion):
            registry.register(record)
    except Exception:
        pass
    return registry


def gather_external_worker_descriptors(node_registry) -> list:
    descriptors = []
    if node_registry is not None and hasattr(node_registry, "worker_descriptors"):
        try:
            descriptors.extend(node_registry.worker_descriptors())
        except Exception:
            pass
    try:
        from backend.mobile.android_companion_store import AndroidCompanionStore

        companion = AndroidCompanionStore().snapshot()
        worker = dict(companion.get("worker") or {})
        descriptors.append(android_worker_descriptor(worker, companion=companion))
    except Exception:
        pass
    return descriptors
