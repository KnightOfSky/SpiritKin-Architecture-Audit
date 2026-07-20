"""Pure reply/context metadata helpers extracted from agent_cluster.

Each function copies the metadata dict before mutating so callers can pass
shared AgentReply/AgentContext objects safely.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agents.base import AgentContext, AgentReply
    from backend.orchestrator.ecommerce_projects import EcommerceProject
    from backend.orchestrator.intent_resolver import IntentResolution
    from backend.orchestrator.task_queue import ScheduledTask


def attach_task_metadata(reply: AgentReply, task: ScheduledTask | None) -> AgentReply:
    if task is None:
        return reply
    metadata = dict(reply.metadata)
    metadata["task"] = task.snapshot()
    reply.metadata = metadata
    return reply


def attach_project_metadata(reply: AgentReply, project: EcommerceProject | None) -> AgentReply:
    if project is None:
        return reply
    metadata = dict(reply.metadata)
    metadata["project"] = project.snapshot()
    reply.metadata = metadata
    return reply


def build_context_metadata(project: EcommerceProject | None = None) -> dict[str, object]:
    return {"project": project.snapshot()} if project is not None else {}


def attach_intent_resolution_metadata(reply: AgentReply, resolution: IntentResolution, source: str = "llm_fallback") -> AgentReply:
    metadata = dict(reply.metadata)
    metadata["intent_resolution"] = {
        "status": resolution.status,
        "reason": resolution.reason,
        "confidence": resolution.confidence,
        "source": source,
    }
    if resolution.corrected_text:
        metadata["intent_resolution"]["corrected_text"] = resolution.corrected_text
    reply.metadata = metadata
    return reply


def attach_context_runtime_metadata(reply: AgentReply, context: AgentContext) -> AgentReply:
    context_metadata = context.metadata if isinstance(context.metadata, dict) else {}
    metadata = dict(reply.metadata)
    if "agent_runtime" in context_metadata:
        metadata.setdefault("agent_runtime", context_metadata["agent_runtime"])
    if "hybrid_planner" in context_metadata:
        metadata.setdefault("hybrid_planner", context_metadata["hybrid_planner"])
    if "agent_mention" in context_metadata:
        metadata.setdefault("agent_mention", context_metadata["agent_mention"])
    if "agent_knowledge_hits" in context_metadata:
        metadata["agent_knowledge_hits"] = list(context_metadata.get("agent_knowledge_hits") or [])
    if "perception_context" in context_metadata:
        metadata["perception_context"] = dict(context_metadata.get("perception_context") or {})
    if "code_workspace_context" in context_metadata:
        metadata["code_workspace_context"] = dict(context_metadata.get("code_workspace_context") or {})
    if "resource_registry" in context_metadata:
        metadata["resource_registry"] = dict(context_metadata.get("resource_registry") or {})
    reply.metadata = metadata
    return reply


def has_attachment_context(metadata: dict | None) -> bool:
    metadata = metadata or {}
    return bool(metadata.get("attachment_documents") or metadata.get("attachment_count"))


def inject_knowledge_hits(context: AgentContext, hits) -> AgentContext:
    metadata = dict(context.metadata)
    metadata["knowledge_hits"] = list(hits or [])
    return replace(context, metadata=metadata)


def inject_web_search_hits(context: AgentContext, hits) -> AgentContext:
    metadata = dict(context.metadata)
    metadata["web_search_hits"] = list(hits or [])
    return replace(context, metadata=metadata)
