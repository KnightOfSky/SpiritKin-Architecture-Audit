"""Agent status snapshot assembly extracted from agent_cluster.

Pure functions: the cluster gathers its own state (profiles, adapters,
queues) and passes it in; nothing here reads cluster internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.agents.base import AgentReply

if TYPE_CHECKING:
    from backend.orchestrator.agent_mentions import AgentMention


def extract_agent_skills(skill_specs, agent_id: str) -> list[dict[str, object]]:
    skills = []
    for spec in skill_specs:
        metadata = dict(getattr(spec, "metadata", {}) or {})
        if str(metadata.get("owner_agent_id") or "") == agent_id:
            skills.append(
                {
                    "name": spec.name,
                    "status": str(metadata.get("status") or "draft"),
                    "risk_level": spec.risk_level,
                    "promotion_status": str(metadata.get("promotion_status") or ""),
                }
            )
    return skills


def extract_agent_workflow_queue(workflow_snapshot: dict[str, Any], agent_id: str) -> list[dict[str, object]]:
    workflow_queue = []
    for run in workflow_snapshot.get("runs") or []:
        if not isinstance(run, dict):
            continue
        for node in run.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            effective_agent = str(node.get("assigned_agent") or "")
            if effective_agent == agent_id:
                workflow_queue.append(
                    {
                        "run_id": str(run.get("run_id") or ""),
                        "workflow_name": str(run.get("workflow_name") or ""),
                        "node_id": str(node.get("node_id") or ""),
                        "label": str(node.get("label") or node.get("node_id") or ""),
                        "status": str(node.get("status") or ""),
                    }
                )
    return workflow_queue


def build_agent_status_snapshot(
    mention: AgentMention,
    *,
    profile: dict[str, object],
    runtime_policy: dict[str, object],
    adapter: object | None,
    skills: list[dict[str, object]],
    workflow_queue: list[dict[str, object]],
    task_queue: list[object],
    recent_performance: dict[str, object],
) -> dict[str, object]:
    agent_id = mention.agent_id
    adapter_policy = getattr(adapter, "policy", None)
    return {
        "agent_id": agent_id,
        "label": mention.label or str(profile.get("label") or agent_id),
        "enabled": bool(profile.get("enabled", True)),
        "domain": str(profile.get("domain") or runtime_policy.get("domain") or ""),
        "role": str(profile.get("role") or runtime_policy.get("role") or ""),
        "provider": str(profile.get("provider") or ""),
        "model": str(profile.get("model") or ""),
        "framework": str(runtime_policy.get("framework") or ""),
        "adapter": str(runtime_policy.get("adapter") or ""),
        "adapter_policy": adapter_policy.snapshot() if hasattr(adapter_policy, "snapshot") else {},
        "capabilities": list(profile.get("capabilities") or runtime_policy.get("capabilities") or []),
        "skills": sorted(skills, key=lambda item: (item["status"] != "active", item["name"]))[:20],
        "workflow_queue": workflow_queue[:20],
        "task_queue": [task for task in task_queue if isinstance(task, dict) and str(task.get("domain") or "") == str(profile.get("domain") or "")][:10],
        "recent_performance": recent_performance,
    }


def build_agent_status_reply(mention: AgentMention, snapshot: dict[str, object]) -> AgentReply:
    skill_count = len(snapshot.get("skills") or [])
    queue_count = len(snapshot.get("workflow_queue") or [])
    capabilities = ", ".join(str(item) for item in list(snapshot.get("capabilities") or [])[:5]) or "--"
    text = (
        f"{snapshot['label']} 当前状态："
        f"domain={snapshot.get('domain') or '--'}，role={snapshot.get('role') or '--'}，"
        f"framework={snapshot.get('framework') or '--'} / {snapshot.get('adapter') or '--'}。"
        f"能力：{capabilities}。关联 Skill {skill_count} 个，工作流队列 {queue_count} 项。"
    )
    return AgentReply(
        text=text,
        emotion="neutral",
        action="idle",
        agent_name="agent_status",
        metadata={
            "response_kind": "agent_status",
            "agent_mention": mention.snapshot(),
            "agent_status": snapshot,
        },
    )
