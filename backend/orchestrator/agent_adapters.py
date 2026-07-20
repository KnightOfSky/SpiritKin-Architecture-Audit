from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.agents.base import AgentContext, AgentReply, BaseAgent


@dataclass(frozen=True)
class AgentAdapterPolicy:
    """Runtime-visible policy for a specialist Agent adapter."""

    agent_id: str
    framework: str = "native"
    adapter: str = "spiritkin_native"
    provider: str = ""
    model: str = ""
    model_id: str = ""
    capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_assistant_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "framework": self.framework,
            "adapter": self.adapter,
            "provider": self.provider,
            "model": self.model,
            "model_id": self.model_id,
            "capabilities": list(self.capabilities),
            "allowed_tools": list(self.allowed_tools),
            "allowed_assistant_ids": list(self.allowed_assistant_ids),
            "metadata": dict(self.metadata),
        }


class AgentAdapter(Protocol):
    """Boundary for mixed Agent frameworks under the SpiritKin control plane."""

    policy: AgentAdapterPolicy

    def run(self, agent: BaseAgent, context: AgentContext) -> AgentReply:
        ...


class NativeAgentAdapter:
    """Default adapter: existing BaseAgent implementation, no extra framework."""

    def __init__(self, policy: AgentAdapterPolicy):
        self.policy = policy

    def run(self, agent: BaseAgent, context: AgentContext) -> AgentReply:
        reply = agent.handle(context)
        metadata = dict(reply.metadata)
        metadata.setdefault("agent_adapter", self.policy.snapshot())
        metadata.setdefault("framework", self.policy.framework)
        metadata.setdefault("adapter", self.policy.adapter)
        reply.metadata = metadata
        return reply


class LangGraphAdapter(NativeAgentAdapter):
    """Minimal graph adapter that maps an agent_task node to BaseAgent.handle."""

    def run(self, agent: BaseAgent, context: AgentContext) -> AgentReply:
        graph = self.policy.metadata.get("graph") if isinstance(self.policy.metadata.get("graph"), dict) else {}
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else [{"id": "agent_task", "type": "agent_task"}]
        supported = [dict(node) for node in nodes if isinstance(node, dict) and str(node.get("type") or "agent_task") == "agent_task"]
        if not supported:
            raise ValueError("LangGraph adapter requires at least one agent_task node")
        reply = agent.handle(context)
        metadata = dict(reply.metadata)
        metadata["agent_adapter"] = self.policy.snapshot()
        metadata["framework"] = "langgraph"
        metadata["adapter"] = "langgraph_minimal"
        metadata["graph_execution"] = {
            "status": "completed",
            "node_count": len(supported),
            "executed_node_ids": [str(node.get("id") or f"agent_task_{index + 1}") for index, node in enumerate(supported)],
        }
        reply.metadata = metadata
        return reply


class CrewAIAdapter(NativeAgentAdapter):
    def run(self, agent: BaseAgent, context: AgentContext) -> AgentReply:
        raise NotImplementedError("CrewAI adapter execution is not enabled; use the native fallback adapter")


def build_adapter_policy(agent_id: str, profile: dict[str, Any] | None = None) -> AgentAdapterPolicy:
    profile = dict(profile or {})
    allowed_tools = profile.get("allowed_tools") or profile.get("allowed_tool_ids") or ()
    allowed_assistants = profile.get("allowed_assistant_ids") or ()
    capabilities = profile.get("capabilities") or ()
    return AgentAdapterPolicy(
        agent_id=agent_id,
        framework=str(profile.get("framework") or "native"),
        adapter=str(profile.get("adapter") or "spiritkin_native"),
        provider=str(profile.get("provider") or ""),
        model=str(profile.get("model") or ""),
        model_id=str(profile.get("model_id") or ""),
        capabilities=tuple(str(item) for item in capabilities if str(item).strip()),
        allowed_tools=tuple(str(item) for item in allowed_tools if str(item).strip()),
        allowed_assistant_ids=tuple(str(item) for item in allowed_assistants if str(item).strip()),
        metadata={
            "role": str(profile.get("role") or "specialist"),
            "domain": str(profile.get("domain") or ""),
            "label": str(profile.get("label") or agent_id),
            "graph": dict(profile.get("graph") or {}) if isinstance(profile.get("graph"), dict) else {},
        },
    )


def build_agent_adapter(agent_id: str, profile: dict[str, Any] | None = None) -> AgentAdapter:
    profile = dict(profile or {})
    policy = build_adapter_policy(agent_id, profile)
    framework = str(profile.get("framework") or "native").strip().lower()
    adapter = str(profile.get("adapter") or "spiritkin_native").strip().lower()
    if framework == "langgraph" or adapter in {"langgraph", "langgraph_minimal"}:
        return LangGraphAdapter(policy)
    if framework == "crewai" or adapter == "crewai":
        return CrewAIAdapter(policy)
    return NativeAgentAdapter(policy)
