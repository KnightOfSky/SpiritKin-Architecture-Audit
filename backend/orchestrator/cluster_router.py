from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.orchestrator.agent_mentions import AgentMention, parse_agent_mention


@dataclass(frozen=True)
class RouteDecision:
    original_input: str
    effective_input: str
    metadata: dict[str, Any]
    agent_mention: AgentMention | None = None

    @property
    def route_kind(self) -> str:
        if self.agent_mention is not None:
            return f"agent_{self.agent_mention.intent}"
        if self.metadata.get("plan_mode") is True:
            return "plan_mode"
        if self.metadata.get("pursue_goal") is True:
            return "goal_pursuit"
        return "automatic"


class ClusterRouter:
    """Pure input routing for the AgentCluster facade."""

    def __init__(self, agent_records: list[dict[str, Any]] | dict[str, dict[str, Any]]):
        if isinstance(agent_records, dict):
            self._agent_records = {
                str(key): dict(value)
                for key, value in agent_records.items()
                if isinstance(value, dict)
            }
        else:
            self._agent_records = [dict(item) for item in agent_records if isinstance(item, dict)]

    def route(self, user_input: str, metadata: dict[str, Any] | None = None) -> RouteDecision:
        original = str(user_input or "")
        routed_metadata = dict(metadata or {})
        mention = parse_agent_mention(original, self._agent_records)
        effective = original
        if mention is not None:
            routed_metadata["agent_mention"] = mention.snapshot()
            routed_metadata["target_agent_id"] = mention.agent_id
            if mention.intent in {"route", "chat"}:
                routed_metadata["forced_agent_id"] = mention.agent_id
                effective = mention.text_without_mention or original
        return RouteDecision(
            original_input=original,
            effective_input=effective,
            metadata=routed_metadata,
            agent_mention=mention,
        )
