from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.orchestrator.brain_router import BrainRouterDecision
from backend.orchestrator.capability_graph import CapabilityRecord

AGENT_CONTAINER_SCHEMA_VERSION = "spiritkin.agent_capability_container.v1"


@dataclass(frozen=True)
class AgentScopeDecision:
    allowed: bool
    scope_type: str
    requested_id: str
    reason: str
    allowed_ids: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "scope_type": self.scope_type,
            "requested_id": self.requested_id,
            "reason": self.reason,
            "allowed_ids": list(self.allowed_ids),
        }


@dataclass(frozen=True)
class AgentCapabilityContainer:
    agent_id: str
    label: str = ""
    role: str = "specialist"
    domain: str = "general"
    framework: str = "native"
    adapter: str = "spiritkin_native"
    capabilities: tuple[str, ...] = ()
    capability_records: tuple[CapabilityRecord, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_skills: tuple[str, ...] = ()
    workflow_templates: tuple[str, ...] = ()
    knowledge_base: dict[str, Any] = field(default_factory=dict)
    memory_policy: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    brain_policy: dict[str, Any] = field(default_factory=dict)
    brain_decision: BrainRouterDecision | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_CONTAINER_SCHEMA_VERSION,
            "agent_id": self.agent_id,
            "label": self.label,
            "role": self.role,
            "domain": self.domain,
            "framework": self.framework,
            "adapter": self.adapter,
            "capabilities": list(self.capabilities),
            "capability_records": [record.snapshot() for record in self.capability_records],
            "allowed_tools": list(self.allowed_tools),
            "allowed_skills": list(self.allowed_skills),
            "workflow_templates": list(self.workflow_templates),
            "knowledge_base": dict(self.knowledge_base or {}),
            "memory_policy": dict(self.memory_policy or {}),
            "state": dict(self.state or {}),
            "brain_policy": dict(self.brain_policy or {}),
            "brain_decision": self.brain_decision.snapshot() if self.brain_decision is not None else {},
            "metadata": dict(self.metadata or {}),
        }

    def scope_decision(self, *, tool_name: str = "", skill_name: str = "") -> AgentScopeDecision:
        return evaluate_agent_container_scope(self, tool_name=tool_name, skill_name=skill_name)


def build_agent_capability_container(
    *,
    agent_id: str,
    profile: dict[str, Any] | None = None,
    adapter_policy: dict[str, Any] | None = None,
    capability_records: list[CapabilityRecord] | tuple[CapabilityRecord, ...] | None = None,
    skills: list[Any] | tuple[Any, ...] | None = None,
    knowledge_base: dict[str, Any] | None = None,
    brain_decision: BrainRouterDecision | None = None,
    state: dict[str, Any] | None = None,
) -> AgentCapabilityContainer:
    profile = dict(profile or {})
    adapter_policy = dict(adapter_policy or {})
    records = tuple(capability_records or ())
    explicit_capabilities = tuple(str(item) for item in profile.get("capabilities") or adapter_policy.get("capabilities") or () if str(item).strip())
    capability_ids = tuple(record.capability_id for record in records)
    allowed_tools = _merge_strings(
        adapter_policy.get("allowed_tools"),
        profile.get("allowed_tools"),
        *(record.tool_refs for record in records),
    )
    adapter_metadata = adapter_policy.get("metadata") if isinstance(adapter_policy.get("metadata"), dict) else {}
    label = str(profile.get("label") or adapter_metadata.get("label") or agent_id)
    role = str(profile.get("role") or adapter_metadata.get("role") or "specialist")
    domain = str(profile.get("domain") or adapter_metadata.get("domain") or "general")
    skill_names = tuple(str(getattr(skill, "name", "") or "") for skill in skills or () if str(getattr(skill, "name", "") or "").strip())
    allowed_skills = _merge_strings(profile.get("allowed_skills"), profile.get("skill_refs"), skill_names, *(record.skill_refs for record in records))
    workflow_templates = _merge_strings(profile.get("workflow_templates"), *(record.workflow_refs for record in records))
    return AgentCapabilityContainer(
        agent_id=agent_id,
        label=label,
        role=role,
        domain=domain,
        framework=str(profile.get("framework") or adapter_policy.get("framework") or "native"),
        adapter=str(profile.get("adapter") or adapter_policy.get("adapter") or "spiritkin_native"),
        capabilities=_merge_strings(explicit_capabilities, capability_ids),
        capability_records=records,
        allowed_tools=allowed_tools,
        allowed_skills=allowed_skills,
        workflow_templates=workflow_templates,
        knowledge_base=dict(knowledge_base or {}),
        memory_policy={
            "short_term": "session_manager",
            "workflow_memory": "enabled",
            "long_term": "optional",
        },
        state=dict(state or {}),
        brain_policy={
            "brain_profile": brain_decision.brain_profile if brain_decision is not None else str(profile.get("brain_profile") or ""),
            "provider": brain_decision.provider if brain_decision is not None else str(profile.get("provider") or ""),
            "model": brain_decision.model if brain_decision is not None else str(profile.get("model") or ""),
            "route": brain_decision.route if brain_decision is not None else "unresolved",
        },
        brain_decision=brain_decision,
        metadata={
            "container_source": "runtime_resolution",
            "scope_enforcement": {
                "mode": "allowlist",
                "tools_restricted": bool(allowed_tools),
                "skills_restricted": bool(allowed_skills),
            },
        },
    )


def evaluate_agent_container_scope(
    container: AgentCapabilityContainer | dict[str, Any],
    *,
    tool_name: str = "",
    skill_name: str = "",
) -> AgentScopeDecision:
    if isinstance(container, AgentCapabilityContainer):
        allowed_tools = container.allowed_tools
        allowed_skills = container.allowed_skills
    else:
        allowed_tools = tuple(str(item) for item in container.get("allowed_tools") or () if str(item).strip())
        allowed_skills = tuple(str(item) for item in container.get("allowed_skills") or () if str(item).strip())
    requested_tool = str(tool_name or "").strip()
    requested_skill = str(skill_name or "").strip()
    if requested_tool:
        return _scope_decision("tool", requested_tool, allowed_tools)
    if requested_skill:
        return _scope_decision("skill", requested_skill, allowed_skills)
    return AgentScopeDecision(True, "none", "", "no_scope_requested", ())


def build_agent_runtime_policy(
    agent_id: str,
    *,
    profiles_by_id: dict[str, dict[str, Any]],
    managed_agents: dict[str, Any],
) -> dict[str, object]:
    agent_id = str(agent_id or "").strip()
    profile = dict(profiles_by_id.get(agent_id, {}))
    allowlists = managed_agents.get("assistant_allowlist_by_agent")
    allowed_ids = []
    if isinstance(allowlists, dict):
        raw_allowed = allowlists.get(agent_id, [])
        if isinstance(raw_allowed, list):
            allowed_ids = [str(item) for item in raw_allowed if str(item).strip()]

    enabled_assistants = managed_agents.get("enabled_external_assistants_by_id")
    assistant_records = []
    if isinstance(enabled_assistants, dict):
        for assistant_id in allowed_ids:
            record = enabled_assistants.get(assistant_id)
            if isinstance(record, dict):
                assistant_records.append(record)

    knowledge_map = managed_agents.get("knowledge_base_by_agent")
    knowledge_base = {}
    if isinstance(knowledge_map, dict):
        record = knowledge_map.get(agent_id)
        if isinstance(record, dict):
            knowledge_base = record

    return {
        "agent_id": agent_id,
        "label": profile.get("label", agent_id),
        "role": profile.get("role", "specialist" if agent_id else "general"),
        "domain": profile.get("domain", "general"),
        "provider": profile.get("provider", ""),
        "model": profile.get("model", ""),
        "model_id": profile.get("model_id", ""),
        "framework": profile.get("framework", "native"),
        "adapter": profile.get("adapter", "spiritkin_native"),
        "capabilities": list(profile.get("capabilities") or []),
        "allowed_assistant_ids": allowed_ids,
        "allowed_assistants": assistant_records,
        "knowledge_base": knowledge_base,
    }


def capability_records_for_agent(records, agent_id: str, capability_ids: list[str] | tuple[str, ...] | None = None):
    wanted = {str(item).strip() for item in capability_ids or () if str(item).strip()}
    selected = []
    for record in records:
        if wanted and record.capability_id in wanted:
            selected.append(record)
            continue
        if agent_id and agent_id in record.owner_agents:
            selected.append(record)
    return selected


def skills_for_agent_container(specs, agent_id: str, capability_ids: list[str] | tuple[str, ...] | None = None):
    wanted = {str(item).strip() for item in capability_ids or () if str(item).strip()}
    skills = []
    for skill in specs:
        metadata = dict(getattr(skill, "metadata", {}) or {})
        if metadata.get("owner_agent_id") == agent_id or metadata.get("agent_id") == agent_id:
            skills.append(skill)
            continue
        capability_id = str(metadata.get("capability_id") or "").strip()
        if capability_id and capability_id in wanted:
            skills.append(skill)
    return skills


def _merge_strings(*groups) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            candidates = [group]
        else:
            candidates = list(group)
        for item in candidates:
            value = str(item).strip()
            if value and value not in values:
                values.append(value)
    return tuple(values)


def _scope_decision(scope_type: str, requested_id: str, allowed_ids: tuple[str, ...]) -> AgentScopeDecision:
    if not allowed_ids:
        return AgentScopeDecision(True, scope_type, requested_id, "allowlist_empty", allowed_ids)
    if _matches_scope(requested_id, allowed_ids):
        return AgentScopeDecision(True, scope_type, requested_id, "allowlist_match", allowed_ids)
    return AgentScopeDecision(False, scope_type, requested_id, "not_in_agent_container_allowlist", allowed_ids)


def _matches_scope(requested_id: str, allowed_ids: tuple[str, ...]) -> bool:
    for allowed in allowed_ids:
        if requested_id == allowed:
            return True
        if allowed.endswith(".*") and requested_id.startswith(allowed[:-1]):
            return True
    return False
