from __future__ import annotations

import re
from typing import Any

from backend.skills.base import SkillSpec, SkillStepSpec
from backend.tools.base import ToolSpec


def _safe_skill_part(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return cleaned or "unknown"


def workflow_skill_name(target: str, operation: str) -> str:
    return f"workflow.{_safe_skill_part(target)}.{_safe_skill_part(operation)}"


def build_workflow_skill_specs(
    candidates: list[dict[str, Any]],
    tools: list[ToolSpec],
    *,
    existing_skill_names: set[str] | None = None,
) -> list[SkillSpec]:
    """Convert successful workflow-memory candidates into auditable candidate Skills."""
    tool_by_route = {(tool.target, tool.operation): tool for tool in tools}
    skip = existing_skill_names or set()
    specs: list[SkillSpec] = []
    for candidate in candidates:
        target = str(candidate.get("target") or "")
        operation = str(candidate.get("operation") or "")
        tool = tool_by_route.get((target, operation))
        if tool is None:
            continue
        name = workflow_skill_name(target, operation)
        if name in skip:
            continue
        success_count = int(candidate.get("success_count") or 0)
        total_count = int(candidate.get("total_count") or success_count)
        example_params = dict(candidate.get("example_params") or {})
        specs.append(
            SkillSpec(
                name=name,
                description=f"候选 Skill：复用高频成功流程 {target}.{operation}（成功 {success_count}/{total_count}）。",
                trigger_intents=(operation, f"{target}.{operation}", name),
                input_schema=dict(tool.schema or {}),
                steps=(SkillStepSpec(tool.name, example_params, description=f"执行 {tool.name}"),),
                tool_allowlist=(tool.name,),
                risk_level=tool.risk_level,
                confirmation_policy="risk_based",
                success_criteria=("workflow_memory_success_rate",),
                memory_policy="promote_candidate_after_review",
                eval_cases=(f"复用 {target}.{operation} 流程",),
                version="0.1.0-candidate",
                usage_count=success_count,
                metadata={
                    "source": "workflow_memory",
                    "status": "candidate",
                    "target": target,
                    "operation": operation,
                    "success_count": success_count,
                    "total_count": total_count,
                    "success_rate": float(candidate.get("success_rate") or 0.0),
                    "last_seen": candidate.get("last_seen"),
                    "example_params": example_params,
                },
            )
        )
    return specs


def build_promotion_metric_for_candidate(candidate: dict[str, Any], recent_workflow_records: list[dict[str, Any]]) -> dict[str, Any]:
    target = candidate.get("target", "")
    operation = candidate.get("operation", "")
    matching = [r for r in recent_workflow_records if r.get("target") == target and r.get("operation") == operation]
    total = len(matching)
    successes = sum(1 for r in matching if r.get("success"))
    return {
        "target": target,
        "operation": operation,
        "recent_total": total,
        "recent_successes": successes,
        "recent_success_rate": (successes / total) if total > 0 else 0.0,
        "candidate_success_count": candidate.get("success_count", 0),
        "candidate_total_count": candidate.get("total_count", 0),
    }