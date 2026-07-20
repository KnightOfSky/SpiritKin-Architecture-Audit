from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.agent_management import load_agent_management_state
from backend.app.settings import resolve_skill_store_path
from backend.skills.base import SkillSpec
from backend.skills.persistence import build_skill_store

SKILL_ROUTER_SCHEMA_VERSION = "spiritkin.skill_router.v1"


@dataclass(frozen=True)
class SkillContextPack:
    context_id: str
    request: str
    agent_id: str = ""
    workspace_path: str = ""
    project_id: str = ""
    session_id: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": SKILL_ROUTER_SCHEMA_VERSION,
            "context_id": self.context_id,
            "request": self.request,
            "agent_id": self.agent_id,
            "workspace_path": self.workspace_path,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "inputs": dict(self.inputs),
            "artifacts": [dict(item) for item in self.artifacts],
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SkillRouteCandidate:
    skill_name: str
    label: str
    status: str
    owner_agent_id: str = ""
    owner_domain: str = ""
    risk_level: str = "low"
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    trigger_intents: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    required_worker_needs: tuple[str, ...] = ()
    workflow_node: dict[str, Any] = field(default_factory=dict)
    skill: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "label": self.label,
            "status": self.status,
            "owner_agent_id": self.owner_agent_id,
            "owner_domain": self.owner_domain,
            "risk_level": self.risk_level,
            "score": round(float(self.score), 4),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "trigger_intents": list(self.trigger_intents),
            "required_capabilities": list(self.required_capabilities),
            "required_worker_needs": list(self.required_worker_needs),
            "workflow_node": dict(self.workflow_node),
            "skill": dict(self.skill),
        }


@dataclass(frozen=True)
class SkillRouteDecision:
    allowed: bool
    status: str
    request: str
    context: SkillContextPack
    selected: SkillRouteCandidate | None = None
    candidates: tuple[SkillRouteCandidate, ...] = ()
    issues: tuple[dict[str, Any], ...] = ()
    orchestration: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": SKILL_ROUTER_SCHEMA_VERSION,
            "allowed": self.allowed,
            "status": self.status,
            "request": self.request,
            "context": self.context.snapshot(),
            "selected": self.selected.snapshot() if self.selected else {},
            "candidates": [candidate.snapshot() for candidate in self.candidates],
            "issues": [dict(item) for item in self.issues],
            "orchestration": dict(self.orchestration),
        }


def build_skill_context_pack(payload: dict[str, Any]) -> SkillContextPack:
    request = str(payload.get("request") or payload.get("text") or payload.get("problem") or "").strip()
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    inputs = dict(payload.get("inputs") or {}) if isinstance(payload.get("inputs"), dict) else {}
    artifacts = tuple(dict(item) for item in payload.get("artifacts") or [] if isinstance(item, dict))
    workspace_path = str(payload.get("workspace_path") or metadata.get("workspace_path") or Path.cwd()).strip()
    context_seed = "|".join(
        [
            request,
            str(payload.get("agent_id") or metadata.get("agent_id") or ""),
            str(payload.get("project_id") or metadata.get("project_id") or ""),
            str(payload.get("session_id") or metadata.get("session_id") or ""),
            str(time.time_ns()),
        ]
    )
    context_id = str(payload.get("context_id") or f"skillctx-{hashlib.sha256(context_seed.encode('utf-8')).hexdigest()[:12]}")
    return SkillContextPack(
        context_id=context_id,
        request=request,
        agent_id=str(payload.get("agent_id") or metadata.get("agent_id") or "").strip(),
        workspace_path=workspace_path,
        project_id=str(payload.get("project_id") or metadata.get("project_id") or "").strip(),
        session_id=str(payload.get("session_id") or metadata.get("session_id") or "").strip(),
        inputs=inputs,
        artifacts=artifacts,
        metadata=metadata,
    )


def build_skill_router_snapshot(*, limit: int = 200) -> dict[str, Any]:
    skills = _load_skill_specs()
    agents = _agent_snapshots_by_id()
    rows = [_skill_router_record(skill, agents) for skill in skills[: max(1, int(limit))]]
    active = [item for item in rows if item["status"] == "active"]
    return {
        "schema_version": SKILL_ROUTER_SCHEMA_VERSION,
        "skill_store_path": str(Path(resolve_skill_store_path()).resolve()),
        "skill_count": len(rows),
        "active_skill_count": len(active),
        "candidate_skill_count": sum(1 for item in rows if item["status"] == "candidate"),
        "routable_skill_count": sum(1 for item in rows if item["routable"]),
        "skills": rows,
        "agents": list(agents.values()),
        "routing_policy": {
            "active_required_by_default": True,
            "candidate_allowed_with_explicit_flag": True,
            "workflow_orchestration": "compile_selected_skill_to_skill_call_node",
            "execution": "router_only_no_direct_execution",
        },
    }


def route_skill(payload: dict[str, Any]) -> SkillRouteDecision:
    context = build_skill_context_pack(payload)
    allow_candidates = _payload_bool(payload.get("allow_candidates"), False)
    include_blocked = _payload_bool(payload.get("include_blocked"), True)
    top_k = _payload_int(payload, "top_k", 5)
    skills = _load_skill_specs()
    agents = _agent_snapshots_by_id()
    candidates: list[SkillRouteCandidate] = []
    for skill in skills:
        candidate = _score_skill(skill, context, agents, allow_candidates=allow_candidates)
        if candidate.score > 0 or include_blocked:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item.score, _status_rank(item.status), -_risk_rank(item.risk_level), item.skill_name), reverse=True)
    visible = tuple(candidates[: max(1, top_k)])
    selected = next((item for item in visible if item.score > 0 and item.status == "active"), None)
    if selected is None and allow_candidates:
        selected = next((item for item in visible if item.score > 0 and item.status in {"candidate", "draft"}), None)
    issues: list[dict[str, Any]] = []
    if not context.request:
        issues.append({"code": "missing_request", "message": "request is required for skill routing"})
    if not skills:
        issues.append({"code": "no_skills_registered", "message": "skill registry is empty"})
    if selected is None and not issues:
        issues.append({"code": "no_routable_skill", "message": "no active skill matched the request"})
    orchestration = build_skill_orchestration(selected, context).get("orchestration", {}) if selected else {}
    return SkillRouteDecision(
        allowed=selected is not None and not any(item["code"] == "missing_request" for item in issues),
        status="routed" if selected else ("blocked" if issues else "no_match"),
        request=context.request,
        context=context,
        selected=selected,
        candidates=visible,
        issues=tuple(issues),
        orchestration=orchestration,
    )


def build_skill_orchestration(candidate: SkillRouteCandidate | dict[str, Any] | None, context: SkillContextPack | dict[str, Any]) -> dict[str, Any]:
    if isinstance(context, SkillContextPack):
        context_snapshot = context.snapshot()
    else:
        context_snapshot = dict(context or {})
    if isinstance(candidate, SkillRouteCandidate):
        candidate_snapshot = candidate.snapshot()
    else:
        candidate_snapshot = dict(candidate or {})
    skill_name = str(candidate_snapshot.get("skill_name") or "").strip()
    if not skill_name:
        return {"ok": False, "error": "missing selected skill"}
    workflow_name = _safe_workflow_name(skill_name)
    node = {
        "node_id": "skill_router_selected",
        "node_type": "skill_call",
        "label": candidate_snapshot.get("label") or skill_name,
        "skill_name": skill_name,
        "arguments": {
            "request": context_snapshot.get("request") or "",
            "context_id": context_snapshot.get("context_id") or "",
            **dict(context_snapshot.get("inputs") or {}),
        },
        "assigned_agent": candidate_snapshot.get("owner_agent_id") or context_snapshot.get("agent_id") or "",
        "metadata": {
            "source": "skill_router",
            "context_id": context_snapshot.get("context_id") or "",
            "risk_level": candidate_snapshot.get("risk_level") or "",
            "required_capabilities": list(candidate_snapshot.get("required_capabilities") or []),
            "required_worker_needs": list(candidate_snapshot.get("required_worker_needs") or []),
        },
    }
    return {
        "ok": True,
        "orchestration": {
            "schema_version": SKILL_ROUTER_SCHEMA_VERSION,
            "mode": "workflow_skill_call",
            "workflow_definition": {
                "workflow_name": workflow_name,
                "description": f"Skill Router orchestration for {skill_name}",
                "version": "0.1.0",
                "nodes": [node],
                "metadata": {
                    "source": "skill_router",
                    "context_id": context_snapshot.get("context_id") or "",
                    "selected_skill": skill_name,
                },
            },
            "start_payload": {
                "workflow_name": workflow_name,
                "inputs": node["arguments"],
            },
        },
    }


def _load_skill_specs() -> list[SkillSpec]:
    store = build_skill_store(resolve_skill_store_path())
    return sorted(store.list_all(), key=lambda item: item.name)


def _agent_snapshots_by_id() -> dict[str, dict[str, Any]]:
    try:
        agents = load_agent_management_state().agents
    except Exception:
        return {}
    return {agent.agent_id: agent.snapshot() for agent in agents if agent.agent_id}


def _skill_router_record(skill: SkillSpec, agents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metadata = dict(skill.metadata or {})
    owner = str(metadata.get("owner_agent_id") or "")
    status = str(metadata.get("status") or "draft")
    return {
        "skill_name": skill.name,
        "label": skill.description or skill.name,
        "status": status,
        "routable": status == "active",
        "owner_agent_id": owner,
        "owner_domain": str(metadata.get("owner_domain") or agents.get(owner, {}).get("domain") or ""),
        "risk_level": skill.risk_level,
        "trigger_intents": list(skill.trigger_intents),
        "required_capabilities": list(skill.required_capabilities),
        "required_worker_needs": list(skill.required_worker_needs),
        "side_effects": list(skill.side_effects),
        "cost_hint": skill.cost_hint,
        "latency_hint_ms": skill.latency_hint_ms,
        "success_rate": skill.success_rate if skill.success_rate is not None else metadata.get("success_rate"),
    }


def _score_skill(skill: SkillSpec, context: SkillContextPack, agents: dict[str, dict[str, Any]], *, allow_candidates: bool) -> SkillRouteCandidate:
    metadata = dict(skill.metadata or {})
    status = str(metadata.get("status") or "draft")
    owner = str(metadata.get("owner_agent_id") or "")
    text = _normalize_text(" ".join([context.request, " ".join(str(value) for value in context.inputs.values())]))
    skill_text = _normalize_text(" ".join([skill.name, skill.description, " ".join(skill.trigger_intents), str(metadata.get("owner_domain") or "")]))
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0.0
    if status != "active":
        if allow_candidates and status in {"candidate", "draft"}:
            warnings.append(f"skill_status_{status}")
        else:
            warnings.append(f"blocked_status_{status}")
            return _candidate(skill, metadata, agents, score=0.0, reasons=(), warnings=tuple(warnings))
    for intent in skill.trigger_intents:
        normalized = _normalize_text(intent)
        if normalized and normalized in text:
            score += 4.0
            reasons.append(f"intent:{intent}")
    name_tokens = _tokens(skill.name)
    request_tokens = set(_tokens(text))
    overlap = [token for token in name_tokens if token in request_tokens]
    if overlap:
        score += min(2.0, len(overlap) * 0.5)
        reasons.append("name_overlap")
    description_overlap = request_tokens.intersection(_tokens(skill.description))
    if description_overlap:
        score += min(2.0, len(description_overlap) * 0.2)
        reasons.append("description_overlap")
    if context.agent_id and owner and context.agent_id == owner:
        score += 1.0
        reasons.append("owner_agent_match")
    owner_domain = str(metadata.get("owner_domain") or agents.get(owner, {}).get("domain") or "")
    if owner_domain and owner_domain.lower() in text:
        score += 0.8
        reasons.append("domain_match")
    if skill.required_capabilities and any(_normalize_text(item) in text for item in skill.required_capabilities):
        score += 0.8
        reasons.append("capability_match")
    if _risk_rank(skill.risk_level) >= 3:
        warnings.append("high_risk_skill_requires_review")
        score -= 0.5
    if not reasons and skill_text and request_tokens.intersection(_tokens(skill_text)):
        score += 0.3
        reasons.append("weak_keyword_overlap")
    return _candidate(skill, metadata, agents, score=max(0.0, score), reasons=tuple(reasons), warnings=tuple(warnings))


def _candidate(
    skill: SkillSpec,
    metadata: dict[str, Any],
    agents: dict[str, dict[str, Any]],
    *,
    score: float,
    reasons: tuple[str, ...],
    warnings: tuple[str, ...],
) -> SkillRouteCandidate:
    owner = str(metadata.get("owner_agent_id") or "")
    owner_domain = str(metadata.get("owner_domain") or agents.get(owner, {}).get("domain") or "")
    node = {
        "node_type": "skill_call",
        "skill_name": skill.name,
        "assigned_agent": owner,
        "arguments": {"request": "{{request}}", "context_id": "{{context_id}}"},
        "metadata": {
            "source": "skill_router",
            "owner_domain": owner_domain,
            "required_capabilities": list(skill.required_capabilities),
            "required_worker_needs": list(skill.required_worker_needs),
        },
    }
    return SkillRouteCandidate(
        skill_name=skill.name,
        label=skill.description or skill.name,
        status=str(metadata.get("status") or "draft"),
        owner_agent_id=owner,
        owner_domain=owner_domain,
        risk_level=skill.risk_level,
        score=score,
        reasons=reasons,
        warnings=warnings,
        trigger_intents=tuple(skill.trigger_intents),
        required_capabilities=tuple(skill.required_capabilities),
        required_worker_needs=tuple(skill.required_worker_needs),
        workflow_node=node,
        skill=_skill_router_record(skill, agents),
    )


def _safe_workflow_name(skill_name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", ".", skill_name).strip(".") or "skill"
    return f"skill_router.{safe}.v1"


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value: Any) -> list[str]:
    return [item for item in re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", str(value or "").lower()) if item]


def _payload_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "允许"}


def _payload_int(payload: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _status_rank(status: str) -> int:
    return {"active": 3, "candidate": 2, "draft": 1}.get(str(status or "").lower(), 0)


def _risk_rank(risk: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(str(risk or "").lower(), 0)
