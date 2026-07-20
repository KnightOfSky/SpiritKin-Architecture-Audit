from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.agents.base import BaseAgent
from backend.orchestrator.agent_adapters import AgentAdapter, build_agent_adapter
from backend.orchestrator.cluster_router import ClusterRouter


@dataclass(frozen=True)
class AgentRoster:
    """Normalized managed-agent configuration and runtime routing records."""

    managed_agents: dict[str, object]
    profiles_by_id: dict[str, dict[str, object]]
    agents: list[BaseAgent]
    adapters_by_id: dict[str, AgentAdapter]
    router: ClusterRouter

    @classmethod
    def build(
        cls,
        agents: list[BaseAgent],
        managed_agents: dict[str, object] | None = None,
    ) -> AgentRoster:
        managed = dict(managed_agents or {})
        profiles = _profiles_by_id(managed)
        configured_agents = _apply_managed_config(agents, managed)
        adapters = {
            agent_id: build_agent_adapter(agent_id, profiles.get(agent_id, {}))
            for agent in configured_agents
            if (agent_id := str(getattr(agent, "name", "") or "").strip())
        }
        return cls(
            managed_agents=managed,
            profiles_by_id=profiles,
            agents=configured_agents,
            adapters_by_id=adapters,
            router=ClusterRouter(_mention_records(configured_agents, profiles)),
        )


def _profiles_by_id(managed_agents: dict[str, object]) -> dict[str, dict[str, object]]:
    profiles = managed_agents.get("agent_profiles_by_id")
    if isinstance(profiles, dict):
        return {
            str(agent_id): dict(profile)
            for agent_id, profile in profiles.items()
            if str(agent_id).strip() and isinstance(profile, dict)
        }
    records = managed_agents.get("agents")
    if not isinstance(records, list):
        return {}
    return {
        str(record.get("agent_id") or record.get("id") or ""): dict(record)
        for record in records
        if isinstance(record, dict) and str(record.get("agent_id") or record.get("id") or "").strip()
    }


def _apply_managed_config(agents: list[BaseAgent], managed_agents: dict[str, object]) -> list[BaseAgent]:
    records = managed_agents.get("agents")
    if not isinstance(records, list) or not records:
        return list(agents)
    by_id = {
        agent_id: record
        for record in records
        if isinstance(record, dict)
        and (agent_id := str(record.get("agent_id") or record.get("id") or "").strip())
    }

    configured: list[BaseAgent] = []
    for agent in agents:
        agent_id = str(getattr(agent, "name", "") or "").strip()
        record = by_id.get(agent_id)
        if record is not None and not bool(record.get("enabled", True)):
            continue
        if record is not None:
            try:
                agent.routing_priority = int(record.get("priority") or getattr(agent, "routing_priority", 0))
            except (TypeError, ValueError):
                pass
            domain = str(record.get("domain") or "").strip()
            if domain:
                agent.domain = domain
        configured.append(agent)
    return sorted(configured, key=lambda item: int(getattr(item, "routing_priority", 0)), reverse=True)


def _mention_records(
    agents: list[BaseAgent],
    profiles_by_id: dict[str, dict[str, object]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    known_agent_ids: set[str] = set()
    for agent in agents:
        agent_id = str(getattr(agent, "name", "") or "").strip()
        if not agent_id:
            continue
        profile = dict(profiles_by_id.get(agent_id, {}))
        profile.setdefault("agent_id", agent_id)
        profile.setdefault("label", agent_id)
        profile.setdefault("domain", str(getattr(agent, "domain", "") or ""))
        records.append(profile)
        known_agent_ids.add(agent_id)
    for agent_id, profile in profiles_by_id.items():
        if agent_id in known_agent_ids:
            continue
        record = dict(profile)
        record.setdefault("agent_id", agent_id)
        records.append(record)
    return records
