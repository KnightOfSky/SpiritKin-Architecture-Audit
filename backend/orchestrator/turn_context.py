from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from backend.orchestrator.agent_mentions import AgentMention
from backend.orchestrator.cluster_router import ClusterRouter


@dataclass(frozen=True)
class PreparedTurn:
    original_input: str
    effective_input: str
    visual_context: str
    metadata: dict[str, Any]
    agent_mention: AgentMention | None = None


class TurnContextPreparer:
    """Prepare one turn without choosing tools, executors, or response text."""

    def __init__(
        self,
        *,
        router: ClusterRouter,
        current_time: Callable[[], dict[str, str]],
        relationship_store: Any = None,
        long_term_memory: Any = None,
    ) -> None:
        self._router = router
        self._current_time = current_time
        self._relationship_store = relationship_store
        self._long_term_memory = long_term_memory

    def begin(self, user_input: str, metadata: dict[str, Any] | None = None) -> PreparedTurn:
        initial = dict(metadata or {})
        initial.setdefault("current_time", self._current_time())
        decision = self._router.route(user_input, initial)
        return PreparedTurn(
            original_input=decision.original_input,
            effective_input=decision.effective_input,
            visual_context="",
            metadata=decision.metadata,
            agent_mention=decision.agent_mention,
        )

    def enrich(
        self,
        turn: PreparedTurn,
        *,
        channel: str,
        visual_context: str,
        inventory_context: str,
        capability_inventory: dict[str, Any],
        resource_registry: dict[str, Any],
        perception_enricher: Callable[..., tuple[str, dict[str, Any]]],
    ) -> PreparedTurn:
        metadata = dict(turn.metadata)
        metadata["input_channel"] = channel
        self._inject_relationship(metadata, turn.effective_input)
        self._inject_long_term_memory(metadata, turn.effective_input)
        if inventory_context:
            metadata["inventory_context"] = inventory_context
        metadata["capability_inventory"] = capability_inventory
        metadata["resource_registry"] = resource_registry
        enriched_visual, enriched_metadata = perception_enricher(
            user_input=turn.effective_input,
            visual_context=visual_context,
            metadata=metadata,
        )
        return replace(turn, visual_context=enriched_visual, metadata=enriched_metadata)

    def _inject_relationship(self, metadata: dict[str, Any], user_input: str) -> None:
        if self._relationship_store is None:
            return
        try:
            metadata["relationship_update"] = self._relationship_store.observe_user_input(user_input)
            metadata["relationship"] = self._relationship_store.context_snapshot()
        except Exception as exc:
            metadata["relationship"] = {"status": "degraded", "error_type": type(exc).__name__}

    def _inject_long_term_memory(self, metadata: dict[str, Any], user_input: str) -> None:
        if self._long_term_memory is None or not hasattr(self._long_term_memory, "recall"):
            return
        try:
            hits = self._long_term_memory.recall(user_input, top_k=5, min_importance=0.15)
            metadata["long_term_memory_hits"] = [
                item.snapshot() if hasattr(item, "snapshot") else dict(item)
                for item in hits
            ]
            metadata["long_term_memory_status"] = {"status": "activated", "count": len(hits)}
        except Exception as exc:
            metadata["long_term_memory_hits"] = []
            metadata["long_term_memory_status"] = {"status": "degraded", "error_type": type(exc).__name__}
