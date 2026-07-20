from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from backend.agents.base import AgentContext, BaseAgent
from backend.orchestrator.capability_graph import (
    CapabilityRecommendation,
    CapabilityRegistry,
    capability_id_for_target_operation,
    normalize_capability_id,
)
from backend.orchestrator.planner import ExecutionPlan, Planner
from backend.tools.base import ToolSpec

HYBRID_PLANNER_SCHEMA_VERSION = "spiritkin.hybrid_planner.v1"


@dataclass(frozen=True)
class TaskAnalysis:
    task_type: str
    domain: str
    complexity_score: int
    risk_score: int
    context_score: int
    required_capabilities: tuple[str, ...] = ()
    should_escalate_planning: bool = False
    reasons: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "domain": self.domain,
            "complexity_score": self.complexity_score,
            "risk_score": self.risk_score,
            "context_score": self.context_score,
            "required_capabilities": list(self.required_capabilities),
            "should_escalate_planning": self.should_escalate_planning,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TaskPlan:
    route: str
    domain: str
    priority_score: int
    resource_profile: str
    local_execution_allowed: bool = True
    planner_profile: str = "local"
    budget: dict[str, Any] = field(default_factory=dict)
    steps: tuple[dict[str, Any], ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "domain": self.domain,
            "priority_score": self.priority_score,
            "resource_profile": self.resource_profile,
            "local_execution_allowed": self.local_execution_allowed,
            "planner_profile": self.planner_profile,
            "budget": dict(self.budget or {}),
            "steps": [dict(step) for step in self.steps],
        }


@dataclass(frozen=True)
class WorkflowPlan:
    mode: str
    workflow_template_ids: tuple[str, ...] = ()
    skill_candidates: tuple[str, ...] = ()
    owner_agent_id: str = ""
    notes: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "workflow_template_ids": list(self.workflow_template_ids),
            "skill_candidates": list(self.skill_candidates),
            "owner_agent_id": self.owner_agent_id,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class HybridPlannerResult:
    analysis: TaskAnalysis
    task_plan: TaskPlan
    workflow_plan: WorkflowPlan
    execution_plan: ExecutionPlan
    capability_recommendation: CapabilityRecommendation | None = None
    growth_gap: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": HYBRID_PLANNER_SCHEMA_VERSION,
            "analysis": self.analysis.snapshot(),
            "task_plan": self.task_plan.snapshot(),
            "workflow_plan": self.workflow_plan.snapshot(),
            "capability_recommendation": self.capability_recommendation.snapshot() if self.capability_recommendation is not None else {},
            "growth_gap": dict(self.growth_gap or {}),
            "execution_plan": {
                "route": self.execution_plan.route,
                "reason": self.execution_plan.reason,
                "domain": self.execution_plan.domain,
                "priority_score": self.execution_plan.priority_score,
                "resource_profile": self.execution_plan.resource_profile,
                "agent": str(getattr(self.execution_plan.agent, "name", "") or ""),
                "tool_call": self.execution_plan.tool_call.name if self.execution_plan.tool_call is not None else "",
                "execution_target": self.execution_plan.execution_request.target if self.execution_plan.execution_request is not None else "",
                "execution_operation": self.execution_plan.execution_request.operation if self.execution_plan.execution_request is not None else "",
            },
        }


class HybridPlannerPipeline:
    """TaskAnalyzer -> TaskPlanner -> WorkflowPlanner wrapper around the current Planner."""

    def __init__(self, *, base_planner: Planner | None = None, capability_registry: CapabilityRegistry | None = None):
        self._base_planner = base_planner or Planner()
        self._capability_registry = capability_registry

    def plan(self, context: AgentContext, agents: list[BaseAgent], available_tools: list[ToolSpec] | None = None) -> HybridPlannerResult:
        execution_plan = self._base_planner.plan(context, agents, available_tools)
        return self.describe_plan(context, execution_plan)

    def describe_plan(self, context: AgentContext, execution_plan: ExecutionPlan) -> HybridPlannerResult:
        analysis = self._analyze(context, execution_plan)
        task_plan = self._build_task_plan(analysis, execution_plan, context)
        workflow_plan = self._build_workflow_plan(analysis, execution_plan)
        capability_recommendation = self._build_capability_recommendation(context, analysis, execution_plan)
        growth_gap = self._build_growth_gap(context, analysis)
        return HybridPlannerResult(analysis, task_plan, workflow_plan, execution_plan, capability_recommendation, growth_gap)

    def _analyze(self, context: AgentContext, execution_plan: ExecutionPlan) -> TaskAnalysis:
        text = context.user_input or ""
        capability_ids = self._capability_hints(execution_plan)
        complexity = _complexity_score(text, execution_plan)
        risk = _risk_score(text, execution_plan)
        context_score = _context_score(context)
        reasons = []
        if capability_ids:
            reasons.append("capability_graph_match")
        if complexity >= 75:
            reasons.append("high_complexity")
        if risk >= 80:
            reasons.append("high_risk")
        if context_score >= 75:
            reasons.append("large_context")
        should_escalate = complexity >= 80 or risk >= 90 or context_score >= 85
        return TaskAnalysis(
            task_type=execution_plan.route,
            domain=execution_plan.domain,
            complexity_score=complexity,
            risk_score=risk,
            context_score=context_score,
            required_capabilities=tuple(capability_ids),
            should_escalate_planning=should_escalate,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _build_task_plan(analysis: TaskAnalysis, execution_plan: ExecutionPlan, context: AgentContext | None = None) -> TaskPlan:
        cloud_gate = _cloud_planner_gate(analysis, context)
        if analysis.should_escalate_planning:
            planner_profile = "cloud_planner_approved" if cloud_gate["approved"] else "cloud_planner_candidate"
        else:
            planner_profile = "local_planner"
        steps = (
            {"stage": "analyze", "status": "complete", "summary": f"{analysis.task_type}:{analysis.domain}"},
            {"stage": "route", "status": "complete", "summary": execution_plan.reason},
            {"stage": "execute", "status": "pending", "summary": execution_plan.route},
        )
        return TaskPlan(
            route=execution_plan.route,
            domain=execution_plan.domain,
            priority_score=execution_plan.priority_score,
            resource_profile=execution_plan.resource_profile,
            local_execution_allowed=True,
            planner_profile=planner_profile,
            budget={
                "planner_hops": 1 if not analysis.should_escalate_planning else 2,
                "latency_class": "interactive" if execution_plan.resource_profile == "interactive" else "background",
                "cloud_planning_candidate": analysis.should_escalate_planning,
                "cloud_planning_approved": bool(cloud_gate["approved"]),
                "cloud_planner_gate": cloud_gate,
            },
            steps=steps,
        )

    @staticmethod
    def _build_workflow_plan(analysis: TaskAnalysis, execution_plan: ExecutionPlan) -> WorkflowPlan:
        agent_id = str(getattr(execution_plan.agent, "name", "") or "")
        if execution_plan.route == "skill":
            return WorkflowPlan(mode="skill", owner_agent_id=agent_id, notes="Skill route already selected")
        if execution_plan.route in {"executor", "tool"}:
            return WorkflowPlan(mode="single_step", owner_agent_id=agent_id, skill_candidates=analysis.required_capabilities)
        if analysis.complexity_score >= 75:
            return WorkflowPlan(mode="workflow_candidate", owner_agent_id=agent_id, workflow_template_ids=analysis.required_capabilities, notes="Complex task should be promoted to workflow template when repeated")
        return WorkflowPlan(mode="direct", owner_agent_id=agent_id)

    def _capability_hints(self, execution_plan: ExecutionPlan) -> list[str]:
        if self._capability_registry is None:
            return []
        if execution_plan.execution_request is not None:
            record = self._capability_registry.resolve_execution_request(execution_plan.execution_request)
            if record is not None:
                return [record.capability_id]
            request = execution_plan.execution_request
            return [capability_id_for_target_operation(request.target, request.operation)]
        if execution_plan.tool_call is not None:
            matches = [
                record.capability_id
                for record in self._capability_registry.list_records()
                if execution_plan.tool_call.name in record.tool_refs
            ]
            return (matches or [normalize_capability_id(execution_plan.tool_call.name)])[:5]
        agent = execution_plan.agent
        agent_id = str(getattr(agent, "name", "") or "")
        if agent_id:
            return [
                record.capability_id
                for record in self._capability_registry.list_records()
                if agent_id in record.owner_agents
            ][:8]
        return []

    def _build_capability_recommendation(
        self,
        context: AgentContext,
        analysis: TaskAnalysis,
        execution_plan: ExecutionPlan,
    ) -> CapabilityRecommendation | None:
        if self._capability_registry is None:
            return None
        required_workers: list[str] = []
        if execution_plan.execution_request is not None:
            request = execution_plan.execution_request
            required_workers.extend(
                item
                for item in (
                    request.target,
                    str((request.params or {}).get("remote_target") or ""),
                )
                if item
            )
        recommendation = self._capability_registry.recommend(
            context.user_input or "",
            domain=analysis.domain,
            required_capabilities=analysis.required_capabilities,
            required_workers=required_workers,
            include_planned=False,
            limit=5,
        )
        return recommendation

    def _build_growth_gap(self, context: AgentContext, analysis: TaskAnalysis) -> dict[str, Any]:
        if self._capability_registry is None or not analysis.required_capabilities:
            return {}
        required = [normalize_capability_id(item) for item in analysis.required_capabilities]
        missing = [item for item in required if self._capability_registry.get(item) is None]
        if not missing:
            return {}
        from backend.capability.growth.runtime import GrowthRuntime

        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        result = GrowthRuntime().analyze_gap(
            {
                "request": context.user_input or "",
                "required_capabilities": required,
                "available_capabilities": [record.capability_id for record in self._capability_registry.list_records()],
                "domain": analysis.domain,
                "workspace_id": str(metadata.get("workspace_id") or "").strip(),
            }
        )
        return {
            "status": "candidate_created" if result.get("candidates") else "gap_found",
            "missing_capabilities": missing,
            "candidates": result.get("candidates") or [],
        }


def _complexity_score(text: str, plan: ExecutionPlan) -> int:
    score = min(35, len(text or "") // 50)
    if plan.route in {"agent", "general"}:
        score += 25
    if plan.route == "development_plan":
        score += 35
    if any(token in (text or "").lower() for token in ("架构", "重构", "多步骤", "workflow", "规划", "评审", "benchmark", "闭环")):
        score += 30
    return min(100, score)


def _risk_score(text: str, plan: ExecutionPlan) -> int:
    score = 20
    if plan.route in {"executor", "tool"}:
        score += 20
    if any(token in (text or "").lower() for token in ("删除", "覆盖", "发布", "部署", "付款", "token", "密钥", "delete", "deploy", "publish")):
        score += 45
    return min(100, score)


def _context_score(context: AgentContext) -> int:
    text_len = len(context.user_input or "") + len(context.visual_context or "") + len(str(context.metadata.get("attachment_text") or ""))
    if context.metadata.get("attachment_documents") or context.metadata.get("attachment_count"):
        text_len += 2000
    return min(100, text_len // 70)


def _cloud_planner_gate(analysis: TaskAnalysis, context: AgentContext | None = None) -> dict[str, Any]:
    requested = bool(analysis.should_escalate_planning)
    metadata = context.metadata if context is not None and isinstance(context.metadata, dict) else {}
    approved = _truthy(metadata.get("cloud_planning_approved")) or _truthy(metadata.get("allow_cloud_planning")) or _truthy(os.getenv("SPIRITKIN_CLOUD_PLANNER_APPROVED"))
    status = "not_required"
    if requested:
        status = "approved" if approved else "requires_approval"
    return {
        "schema_version": "spiritkin.cloud_planner_gate.v1",
        "requested": requested,
        "approved": bool(requested and approved),
        "status": status,
        "approval_sources": {
            "context": bool(_truthy(metadata.get("cloud_planning_approved")) or _truthy(metadata.get("allow_cloud_planning"))),
            "environment": bool(_truthy(os.getenv("SPIRITKIN_CLOUD_PLANNER_APPROVED"))),
        },
        "required_action": "" if not requested or approved else "approve_cloud_planning",
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "approved"}
