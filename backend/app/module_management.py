from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from backend.app.action_log import build_action_log_snapshot
from backend.app.agent_management import build_agent_management_desktop_snapshot
from backend.app.collaboration import build_collaboration_snapshot
from backend.app.ecosystem_review import build_ecosystem_review_snapshot, resolve_ecosystem_review_state_path
from backend.app.evolution_management import build_evolution_management_snapshot
from backend.app.knowledge_base_management import build_knowledge_base_snapshot
from backend.app.learning_workflow import build_learning_workflow_report
from backend.app.local_model_policy import build_local_model_policy_snapshot
from backend.app.mcp_management import build_mcp_management_snapshot
from backend.app.mobile_management import build_mobile_management_snapshot
from backend.app.model_catalog import load_model_catalog
from backend.app.project_runtime import build_project_runtime_snapshot
from backend.app.replaceable_brain import build_brain_replacement_snapshot
from backend.app.resource_management import build_resource_management_snapshot
from backend.app.search_management import build_search_management_snapshot
from backend.app.service_ports import build_service_port_snapshot
from backend.app.skill_router import build_skill_router_snapshot
from backend.app.skills_console import build_desktop_skills_snapshot
from backend.app.state_maintenance import build_state_maintenance_snapshot
from backend.app.workflow_management import build_workflow_management_snapshot
from backend.code_jury import build_code_jury_snapshot

SCHEMA_VERSION = "spiritkin.module_management.v2"
SNAPSHOT_CACHE_TTL_SECONDS = 8.0

_SNAPSHOT_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_SNAPSHOT_CACHE_LOCK = threading.Lock()


def clear_module_management_cache() -> None:
    with _SNAPSHOT_CACHE_LOCK:
        _SNAPSHOT_CACHE.clear()


def build_module_management_snapshot(
    *,
    ecosystem_snapshot: dict[str, Any] | None = None,
    fast: bool = False,
    use_cache: bool = False,
) -> dict[str, Any]:
    cache_key = _snapshot_cache_key(fast) if ecosystem_snapshot is None and use_cache else None
    if cache_key is not None:
        cached = _cached_snapshot(cache_key)
        if cached is not None:
            return cached

    skills = _safe_snapshot("skills", build_desktop_skills_snapshot)
    skill_router = _safe_snapshot("skill_router", build_skill_router_snapshot)
    agents = _safe_snapshot("agent_management", build_agent_management_desktop_snapshot)
    knowledge = _safe_snapshot("knowledge_base", build_knowledge_base_snapshot)
    model_catalog = _safe_snapshot("model_catalog", load_model_catalog)
    learning = _safe_snapshot("learning", lambda: build_learning_workflow_report(include_improvement=False).snapshot())
    search = _safe_snapshot("search_management", build_search_management_snapshot)
    service_ports = _safe_snapshot("service_ports", build_service_port_snapshot)
    project_runtime = _safe_snapshot("project_runtime", build_project_runtime_snapshot)
    resource_management = _safe_snapshot("resource_management", build_resource_management_snapshot)
    state_maintenance = _safe_snapshot("state_maintenance", build_state_maintenance_snapshot)
    action_log = _safe_snapshot("action_log", lambda: build_action_log_snapshot(limit=80))
    mcp = _safe_snapshot("mcp_management", build_mcp_management_snapshot)
    mobile = _safe_snapshot("mobile_management", _fast_mobile_management_snapshot if fast else build_mobile_management_snapshot)
    evolution = _safe_snapshot("evolution", build_evolution_management_snapshot)
    code_jury = _safe_snapshot("code_jury", build_code_jury_snapshot)
    collaboration = _safe_snapshot("collaboration", build_collaboration_snapshot)
    workflows = _safe_snapshot("workflows", build_workflow_management_snapshot)
    ecosystem = ecosystem_snapshot if ecosystem_snapshot is not None else _safe_snapshot(
        "ecosystem_review",
        _fast_ecosystem_review_snapshot if fast else build_ecosystem_review_snapshot,
    )

    modules = [
        _evolution_module(evolution),
        _workflow_module(workflows),
        _skills_module(skills),
        _skill_router_module(skill_router),
        _agents_module(agents),
        _knowledge_module(knowledge),
        _search_module(search),
        _service_ports_module(service_ports),
        _project_runtime_module(project_runtime),
        _resource_registry_module(resource_management),
        _state_maintenance_module(state_maintenance),
        _action_log_module(action_log),
        _mobile_module(mobile),
        _mcp_module(mcp),
        _models_module(model_catalog, learning),
        _collaboration_module(collaboration),
        _code_jury_module(code_jury),
        _governance_module(ecosystem),
    ]
    modules = [_with_enterprise_fields(module) for module in modules]
    module_by_id = {str(module.get("module_id") or ""): module for module in modules}
    action_items = _module_actions(modules)
    action_items.extend(_ecosystem_actions(ecosystem))
    action_items = [_enrich_action_item(action, module_by_id.get(str(action.get("module_id") or ""))) for action in action_items]
    action_items = sorted(action_items, key=lambda item: (_priority_order(item.get("priority")), str(item.get("module_id")), str(item.get("title"))))[:18]
    portfolio = _portfolio_snapshot(modules, action_items)
    status = _portfolio_status(modules)
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "status": status,
        "overview": {
            "status": status,
            "module_count": len(modules),
            "ready_count": sum(1 for item in modules if item.get("status") == "ready"),
            "attention_count": sum(1 for item in modules if item.get("status") == "needs_attention"),
            "blocked_count": sum(1 for item in modules if item.get("status") == "blocked"),
            "action_count": len(action_items),
            "high_action_count": sum(1 for item in action_items if item.get("priority") == "high"),
            "health_score": portfolio["health_score"],
            "readiness_percent": portfolio["readiness_percent"],
        },
        "portfolio": portfolio,
        "modules": modules,
        "action_items": action_items,
        "source_endpoints": {
            "skills": "/desktop/skills",
            "skill_router": "/desktop/skill-router",
            "agents": "/desktop/agent-management",
            "knowledge_base": "/desktop/knowledge-base",
            "search_management": "/desktop/search-management",
            "service_ports": "/desktop/service-ports",
            "project_runtime": "/desktop/project-runtime",
            "resource_registry": "/desktop/resource-registry",
            "state_maintenance": "/desktop/state-maintenance",
            "action_log": "/desktop/action-log",
            "mobile_management": "/desktop/mobile-management",
            "mcp_management": "/desktop/mcp-management",
            "model_catalog": "/desktop/model-catalog",
            "learning": "/desktop/learning",
            "collaboration": "/desktop/collaboration",
            "code_jury": "/desktop/code-jury",
            "evolution": "/desktop/evolution",
            "workflows": "/desktop/workflows",
            "ecosystem_review": "/desktop/ecosystem-review",
        },
    }
    if cache_key is not None:
        _store_cached_snapshot(cache_key, snapshot)
    return snapshot


def _snapshot_cache_key(fast: bool) -> tuple[str, str]:
    env_bits = [
        os.getcwd(),
        os.getenv("SPIRITKIN_SKILL_STORE_PATH", ""),
        os.getenv("SPIRITKIN_AGENT_MANAGEMENT_PATH", ""),
        os.getenv("SPIRITKIN_MODEL_CATALOG_PATH", ""),
        os.getenv("SPIRITKIN_ECOSYSTEM_REVIEW_STATE", ""),
    ]
    return ("fast" if fast else "full", "|".join(env_bits))


def _cached_snapshot(cache_key: tuple[str, str]) -> dict[str, Any] | None:
    now = time.time()
    with _SNAPSHOT_CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(cache_key)
        if cached is None:
            return None
        generated_at, snapshot = cached
        if now - generated_at > SNAPSHOT_CACHE_TTL_SECONDS:
            _SNAPSHOT_CACHE.pop(cache_key, None)
            return None
        return copy.deepcopy(snapshot)


def _store_cached_snapshot(cache_key: tuple[str, str], snapshot: dict[str, Any]) -> None:
    with _SNAPSHOT_CACHE_LOCK:
        _SNAPSHOT_CACHE[cache_key] = (time.time(), copy.deepcopy(snapshot))


def _fast_mobile_management_snapshot() -> dict[str, Any]:
    return build_mobile_management_snapshot(disable_device_probes=True)


def _fast_ecosystem_review_snapshot() -> dict[str, Any]:
    path = resolve_ecosystem_review_state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    proposals = [dict(item) for item in state.get("proposals") or [] if isinstance(item, dict)]
    pending_count = sum(1 for item in proposals if str(item.get("status") or "pending") == "pending")
    last_snapshot = dict(state.get("last_snapshot") or {})
    score = dict(last_snapshot.get("score") or {})
    model_reviews = [dict(item) for item in state.get("model_reviews") or [] if isinstance(item, dict)][-10:]
    actions = [dict(item) for item in state.get("applied_actions") or [] if isinstance(item, dict)][-30:]
    return {
        "schema_version": "spiritkin.ecosystem_review.v1",
        "generated_at": float(last_snapshot.get("generated_at") or state.get("updated_at") or time.time()),
        "state_path": str(path),
        "project_root": str(Path.cwd().resolve()),
        "score": score or {"total": 0, "status": "unknown", "dimensions": []},
        "issues": [],
        "proposals": proposals,
        "proposal_status_counts": _proposal_status_counts(proposals),
        "pending_count": pending_count,
        "approved_count": sum(1 for item in proposals if str(item.get("status") or "") == "approved"),
        "applied_count": sum(1 for item in proposals if str(item.get("status") or "") == "applied"),
        "model_reviews": model_reviews,
        "applied_actions": actions,
        "systems": {},
        "capabilities": {"fast_snapshot": True, "full_scan_endpoint": "/desktop/ecosystem-review"},
    }


def _proposal_status_counts(proposals: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for proposal in proposals:
        status = str(proposal.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _with_enterprise_fields(module: dict[str, Any]) -> dict[str, Any]:
    module_id = str(module.get("module_id") or "")
    profile = _enterprise_profile(module_id)
    actions = [dict(item) for item in module.get("actions") or [] if isinstance(item, dict)]
    high_actions = sum(1 for item in actions if item.get("priority") == "high")
    medium_actions = sum(1 for item in actions if item.get("priority") == "medium")
    status = str(module.get("status") or "needs_attention")
    health_score = _module_health_score(status, high_actions, medium_actions, int(profile["criticality_weight"]))
    risk_level = _risk_level(status, high_actions, medium_actions, int(profile["criticality_weight"]))
    governance_state = "controlled" if status == "ready" and high_actions == 0 else ("blocked" if status == "blocked" or high_actions else "review_required")
    enriched = dict(module)
    enriched.update(
        {
            "business_capability": profile["business_capability"],
            "management_group": profile["management_group"],
            "owner_role": profile["owner_role"],
            "criticality": profile["criticality"],
            "criticality_weight": profile["criticality_weight"],
            "maturity": profile["maturity"],
            "maturity_level": profile["maturity_level"],
            "sla": profile["sla"],
            "risk_level": risk_level,
            "risk_summary": _risk_summary(status, risk_level, actions),
            "health_score": health_score,
            "governance_state": governance_state,
            "action_count": len(actions),
            "high_action_count": high_actions,
            "medium_action_count": medium_actions,
            "runbook": profile["runbook"],
        }
    )
    return enriched


def _enterprise_profile(module_id: str) -> dict[str, Any]:
    profiles = {
        "evolution": {
            "business_capability": "Self-improvement and learning flywheel",
            "management_group": "Intelligence Ops",
            "owner_role": "AI Platform Owner",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "incubating",
            "maturity_level": 2,
            "sla": "weekly review",
            "runbook": "/desktop/evolution",
        },
        "workflows": {
            "business_capability": "Multi-agent process orchestration",
            "management_group": "Automation Control",
            "owner_role": "Workflow Operator",
            "criticality": "critical",
            "criticality_weight": 4,
            "maturity": "managed",
            "maturity_level": 3,
            "sla": "same-day triage",
            "runbook": "/desktop/workflows",
        },
        "skills": {
            "business_capability": "Reusable governed capabilities",
            "management_group": "Capability Catalog",
            "owner_role": "Skill Curator",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "managed",
            "maturity_level": 3,
            "sla": "weekly review",
            "runbook": "/desktop/skills",
        },
        "skill_router": {
            "business_capability": "Skill selection, context packing, and workflow orchestration",
            "management_group": "Capability Catalog",
            "owner_role": "Skill Routing Owner",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "same-day routing regression review",
            "runbook": "/desktop/skill-router",
        },
        "agents": {
            "business_capability": "Agent workforce and route profiles",
            "management_group": "Agent Operations",
            "owner_role": "Agent Ops Lead",
            "criticality": "critical",
            "criticality_weight": 4,
            "maturity": "managed",
            "maturity_level": 3,
            "sla": "same-day triage",
            "runbook": "/desktop/agent-management",
        },
        "knowledge_base": {
            "business_capability": "Knowledge grounding and agent memory",
            "management_group": "Knowledge Ops",
            "owner_role": "Knowledge Steward",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "weekly index check",
            "runbook": "/desktop/knowledge-base",
        },
        "search_management": {
            "business_capability": "Search, retrieval, embedding, and reranking",
            "management_group": "Knowledge Ops",
            "owner_role": "RAG Owner",
            "criticality": "medium",
            "criticality_weight": 2,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "weekly provider check",
            "runbook": "/desktop/search-management",
        },
        "mcp_management": {
            "business_capability": "External MCP tool provider governance",
            "management_group": "Tooling Ops",
            "owner_role": "Tooling Owner",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "incubating",
            "maturity_level": 2,
            "sla": "same-day unsafe connector review",
            "runbook": "/desktop/mcp-management",
        },
        "mobile_management": {
            "business_capability": "Android/iOS control bridge and device intake",
            "management_group": "Device Operations",
            "owner_role": "Mobile Ops Owner",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "same-day device bridge triage",
            "runbook": "/desktop/mobile-management",
        },
        "service_ports": {
            "business_capability": "Local service routing and repair",
            "management_group": "Platform Operations",
            "owner_role": "Desktop Runtime Owner",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "same-day port conflict triage",
            "runbook": "/desktop/services",
        },
        "project_runtime": {
            "business_capability": "Project workspace execution boundary",
            "management_group": "Project Operations",
            "owner_role": "Project Runtime Owner",
            "criticality": "critical",
            "criticality_weight": 4,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "same-day blocked launch triage",
            "runbook": "/desktop/project-overview",
        },
        "resource_registry": {
            "business_capability": "Long-lived digital asset registry",
            "management_group": "Resource Operations",
            "owner_role": "Resource Steward",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "same-day resource onboarding review",
            "runbook": "/desktop/resource-registry",
        },
        "state_maintenance": {
            "business_capability": "State retention and cleanup",
            "management_group": "Platform Operations",
            "owner_role": "State Steward",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "weekly retention review",
            "runbook": "/desktop/mobile-management",
        },
        "action_log": {
            "business_capability": "Unified operator audit trail",
            "management_group": "Governance",
            "owner_role": "Audit Owner",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "same-day audit source failure review",
            "runbook": "/desktop/logs",
        },
        "models": {
            "business_capability": "Model catalog and reviewer configuration",
            "management_group": "Model Ops",
            "owner_role": "Model Steward",
            "criticality": "high",
            "criticality_weight": 3,
            "maturity": "emerging",
            "maturity_level": 2,
            "sla": "weekly provider check",
            "runbook": "/desktop/model-catalog",
        },
        "code_jury": {
            "business_capability": "Structured code/UI review and controlled patch synthesis",
            "management_group": "Governance",
            "owner_role": "Review Gate Owner",
            "criticality": "critical",
            "criticality_weight": 4,
            "maturity": "incubating",
            "maturity_level": 2,
            "sla": "same-day high-risk code review",
            "runbook": "/desktop/code-jury",
        },
        "collaboration": {
            "business_capability": "Cross-model task ledger, context packs, and file ownership",
            "management_group": "Governance",
            "owner_role": "Collaboration Coordinator",
            "criticality": "critical",
            "criticality_weight": 4,
            "maturity": "incubating",
            "maturity_level": 2,
            "sla": "same-day collaboration conflict triage",
            "runbook": "/desktop/collaboration",
        },
        "module_governance": {
            "business_capability": "Risk, proposal, audit, and maturity control",
            "management_group": "Governance",
            "owner_role": "Governance Reviewer",
            "criticality": "critical",
            "criticality_weight": 4,
            "maturity": "managed",
            "maturity_level": 3,
            "sla": "same-day critical risk review",
            "runbook": "/desktop/ecosystem-review",
        },
    }
    return dict(
        profiles.get(
            module_id,
            {
                "business_capability": "Managed platform capability",
                "management_group": "Platform",
                "owner_role": "Module Owner",
                "criticality": "medium",
                "criticality_weight": 2,
                "maturity": "emerging",
                "maturity_level": 2,
                "sla": "weekly review",
                "runbook": "",
            },
        )
    )


def _module_health_score(status: str, high_actions: int, medium_actions: int, criticality_weight: int) -> int:
    score = 100
    if status == "blocked":
        score -= 38
    elif status == "needs_attention":
        score -= 18
    score -= high_actions * (12 + criticality_weight * 2)
    score -= medium_actions * 5
    return max(0, min(100, score))


def _risk_level(status: str, high_actions: int, medium_actions: int, criticality_weight: int) -> str:
    if status == "blocked" or high_actions > 0:
        return "high" if criticality_weight >= 3 else "medium"
    if status == "needs_attention" or medium_actions >= 2:
        return "medium"
    return "low"


def _risk_summary(status: str, risk_level: str, actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "无待处理风险；保持例行巡检。"
    high = sum(1 for item in actions if item.get("priority") == "high")
    medium = sum(1 for item in actions if item.get("priority") == "medium")
    return f"{risk_level} risk · {status} · high {high} · medium {medium} · total {len(actions)}"


def _portfolio_snapshot(modules: list[dict[str, Any]], action_items: list[dict[str, Any]]) -> dict[str, Any]:
    module_count = len(modules)
    ready = sum(1 for item in modules if item.get("status") == "ready")
    health_score = round(sum(int(item.get("health_score") or 0) for item in modules) / max(module_count, 1))
    risk_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    critical_high_risk = 0
    for module in modules:
        risk = str(module.get("risk_level") or "medium")
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        if module.get("criticality") == "critical" and risk == "high":
            critical_high_risk += 1
    return {
        "health_score": health_score,
        "readiness_percent": round((ready / max(module_count, 1)) * 100),
        "risk_counts": risk_counts,
        "critical_high_risk_count": critical_high_risk,
        "high_action_count": sum(1 for item in action_items if item.get("priority") == "high"),
        "medium_action_count": sum(1 for item in action_items if item.get("priority") == "medium"),
        "low_action_count": sum(1 for item in action_items if item.get("priority") == "low"),
        "operator_posture": "blocked" if critical_high_risk else ("attention" if any(item.get("priority") == "high" for item in action_items) else "controlled"),
    }


def _enrich_action_item(action: dict[str, Any], module: dict[str, Any] | None) -> dict[str, Any]:
    enriched = dict(action)
    module = module or {}
    priority = str(enriched.get("priority") or "medium")
    enriched["priority"] = priority if priority in {"high", "medium", "low"} else "medium"
    enriched["module_label"] = str(module.get("label") or enriched.get("module_id") or "模块")
    enriched["owner_role"] = str(module.get("owner_role") or "Module Owner")
    enriched["management_group"] = str(module.get("management_group") or "Platform")
    enriched["risk_level"] = str(module.get("risk_level") or _priority_to_risk(enriched["priority"]))
    enriched["criticality"] = str(module.get("criticality") or "medium")
    enriched["governance_state"] = str(module.get("governance_state") or "review_required")
    enriched["sla"] = str(module.get("sla") or "weekly review")
    enriched["operator_hint"] = _operator_hint(enriched)
    return enriched


def _priority_to_risk(priority: str) -> str:
    return {"high": "high", "medium": "medium", "low": "low"}.get(priority, "medium")


def _operator_hint(action: dict[str, Any]) -> str:
    priority = str(action.get("priority") or "medium")
    owner = str(action.get("owner_role") or "Module Owner")
    sla = str(action.get("sla") or "weekly review")
    if priority == "high":
        return f"{owner} 需要优先处理；SLA: {sla}。"
    if priority == "medium":
        return f"{owner} 纳入本轮治理队列；SLA: {sla}。"
    return f"{owner} 例行跟进；SLA: {sla}。"


def _safe_snapshot(name: str, loader: Any) -> dict[str, Any]:
    try:
        snapshot = loader()
    except Exception as exc:  # pragma: no cover - defensive boundary for desktop UI
        return {"error": f"{type(exc).__name__}: {exc}", "name": name}
    return snapshot if isinstance(snapshot, dict) else {"value": snapshot}


def _skills_module(skills: dict[str, Any]) -> dict[str, Any]:
    counts = dict(skills.get("status_counts") or {})
    total = int(skills.get("count") or 0)
    candidates = int(counts.get("candidate") or 0)
    active = int(counts.get("active") or 0)
    errors = _error_actions("skills", skills)
    actions = list(errors)
    if total == 0:
        actions.append(_action("skills", "medium", "创建首批 Skill", "当前 Skill registry 为空，先把高频桌面操作沉淀成可审核 Skill。", "/desktop/skills"))
    if candidates:
        actions.append(_action("skills", "medium", "审核候选 Skill", f"{candidates} 个候选 Skill 等待人工审核、测试或归档。", "/desktop/skills"))
    return {
        "module_id": "skills",
        "label": "Skills",
        "status": _status(errors=errors, needs_attention=bool(candidates or total == 0)),
        "endpoint": "/desktop/skills",
        "desktop_page": "skills",
        "description": "Skill 注册表、候选审核、升级和远端导出。",
        "summary": f"总数 {total} · active {active} · candidate {candidates}",
        "metrics": [
            {"label": "总数", "value": total},
            {"label": "Active", "value": active},
            {"label": "Candidate", "value": candidates},
        ],
        "actions": actions,
    }


def _skill_router_module(skill_router: dict[str, Any]) -> dict[str, Any]:
    total = int(skill_router.get("skill_count") or 0)
    active = int(skill_router.get("active_skill_count") or 0)
    routable = int(skill_router.get("routable_skill_count") or 0)
    candidates = int(skill_router.get("candidate_skill_count") or 0)
    errors = _error_actions("skill_router", skill_router)
    actions = list(errors)
    if total == 0:
        actions.append(_action("skill_router", "medium", "等待 Skill Registry", "当前没有可路由 Skill；先在 Skills 中创建并审核首批能力。", "/desktop/skills"))
    elif routable == 0:
        actions.append(_action("skill_router", "medium", "激活可路由 Skill", "Skill Router 默认只选择 active Skill；请审核并提升至少一个 Skill。", "/desktop/skills"))
    if candidates:
        actions.append(_action("skill_router", "low", "评估候选 Skill 路由", f"{candidates} 个候选 Skill 可在显式 allow_candidates 时参与路由。", "/desktop/skill-router"))
    return {
        "module_id": "skill_router",
        "label": "Skill Router",
        "status": _status(errors=errors, needs_attention=bool(total == 0 or routable == 0)),
        "endpoint": "/desktop/skill-router",
        "desktop_page": "skills:router",
        "description": "从请求、上下文、Agent 和 Skill 元数据选择可执行 Skill，并编译为 Workflow skill_call 草案。",
        "summary": f"Skill {total} · active {active} · routable {routable} · candidate {candidates}",
        "metrics": [
            {"label": "Skill", "value": total},
            {"label": "Active", "value": active},
            {"label": "Routable", "value": routable},
            {"label": "Candidate", "value": candidates},
        ],
        "actions": actions,
    }


def _evolution_module(evolution: dict[str, Any]) -> dict[str, Any]:
    summary = dict(evolution.get("self_improvement_summary") or {})
    counts = dict(summary.get("counts") or {})
    trajectory = dict(evolution.get("trajectory") or {})
    distribution = dict(evolution.get("agent_skill_distribution") or {})
    artifacts = dict(evolution.get("learning_artifacts") or {})
    templates = dict(evolution.get("domain_skill_templates") or {})
    errors = _error_actions("evolution", evolution)
    actions = list(errors)
    for item in evolution.get("action_items") or []:
        if not isinstance(item, dict):
            continue
        actions.append(
            _action(
                "evolution",
                str(item.get("priority") or "medium"),
                str(item.get("title") or "进化待处理事项"),
                str(item.get("detail") or ""),
                "/desktop/evolution",
            )
        )
    status = str(evolution.get("status") or "")
    if errors:
        status = "blocked"
    if status not in {"ready", "needs_attention", "blocked"}:
        status = "needs_attention" if actions else "ready"
    return {
        "module_id": "evolution",
        "label": "进化闭环",
        "status": status,
        "endpoint": "/desktop/evolution",
        "desktop_page": "evolution",
        "description": "任务轨迹、裁判评分、eval 生成、训练包导出和核心审核门。",
        "summary": f"轨迹 {int(trajectory.get('total') or 0)} · Artifact {int(artifacts.get('artifact_count') or 0)} · 模板 {int(templates.get('existing_count') or 0)}/{int(templates.get('count') or 0)} · 未归属 Skill {int(distribution.get('missing_owner_count') or 0)}",
        "metrics": [
            {"label": "轨迹", "value": int(trajectory.get("total") or 0)},
            {"label": "Eval", "value": int(counts.get("eval_cases") or 0)},
            {"label": "自训样本", "value": int(counts.get("self_training_examples") or 0)},
            {"label": "Artifact", "value": int(artifacts.get("artifact_count") or 0)},
            {"label": "领域模板", "value": f"{int(templates.get('existing_count') or 0)}/{int(templates.get('count') or 0)}"},
            {"label": "未归属 Skill", "value": int(distribution.get("missing_owner_count") or 0)},
        ],
        "actions": actions[:8],
    }


def _workflow_module(workflows: dict[str, Any]) -> dict[str, Any]:
    overview = dict(workflows.get("overview") or {})
    status_counts = dict(overview.get("status_counts") or {})
    definitions = int(overview.get("definition_count") or 0)
    runs = int(overview.get("run_count") or 0)
    active = int(overview.get("active_run_count") or 0)
    errors = _error_actions("workflows", workflows)
    actions = list(errors)
    if definitions == 0:
        actions.append(_action("workflows", "medium", "保存默认电商工作流", "先保存 ecommerce.auto_listing.v1，供桌面端和 Agent 调度器统一读取。", "/desktop/workflows"))
    if active:
        actions.append(_action("workflows", "low", "处理运行中的工作流", f"{active} 个工作流运行等待节点执行、审核或 Agent 提交结果。", "/desktop/workflows"))
    return {
        "module_id": "workflows",
        "label": "工作流",
        "status": _status(errors=errors, needs_attention=bool(definitions == 0 or active)),
        "endpoint": "/desktop/workflows",
        "desktop_page": "workflows",
        "description": "Blueprint 式流程定义、运行状态、节点审核和 Agent 认领入口。",
        "summary": f"定义 {definitions} · 运行 {runs} · active {active}",
        "metrics": [
            {"label": "定义", "value": definitions},
            {"label": "运行", "value": runs},
            {"label": "Active", "value": active},
            {"label": "Succeeded", "value": int(status_counts.get("succeeded") or 0)},
        ],
        "actions": actions,
    }


def _agents_module(agents: dict[str, Any]) -> dict[str, Any]:
    summary = dict(agents.get("distribution_summary") or {})
    counts = dict(summary.get("counts") or {})
    gaps = [dict(item) for item in summary.get("gaps") or [] if isinstance(item, dict)]
    errors = _error_actions("agents", agents)
    actions = list(errors)
    for gap in gaps[:5]:
        actions.append(
            _action(
                "agents",
                _gap_priority(gap),
                str(gap.get("title") or "Agent 配置缺口"),
                str(gap.get("detail") or ""),
                "/desktop/agent-management",
            )
        )
    raw_status = str(summary.get("status") or "")
    status = "blocked" if raw_status == "blocked" or any(item.get("priority") == "high" for item in gaps) else ("needs_attention" if gaps else "ready")
    if errors:
        status = "blocked"
    return {
        "module_id": "agents",
        "label": "Agent 集群",
        "status": status,
        "endpoint": "/desktop/agent-management",
        "desktop_page": "agents",
        "description": "主 Agent、专家 Agent、外部助手、路由组合和远端节点。",
        "summary": f"启用 Agent {int(counts.get('agents_enabled') or 0)} · 路由 {int(counts.get('route_profiles_total') or 0)} · 外部助手 {int(counts.get('external_assistants_enabled') or 0)}",
        "metrics": [
            {"label": "启用 Agent", "value": int(counts.get("agents_enabled") or 0)},
            {"label": "Route profiles", "value": int(counts.get("route_profiles_total") or 0)},
            {"label": "外部助手", "value": int(counts.get("external_assistants_enabled") or 0)},
        ],
        "actions": actions,
    }


def _knowledge_module(knowledge: dict[str, Any]) -> dict[str, Any]:
    records = [dict(item) for item in knowledge.get("knowledge_bases") or [] if isinstance(item, dict)]
    enabled = [item for item in records if bool(item.get("enabled", True))]
    missing = [item for item in enabled if not bool(item.get("exists"))]
    unindexed = [item for item in enabled if not dict(item.get("last_index") or {}).get("updated_at")]
    job_history = dict(knowledge.get("job_history") or {})
    failed_jobs = [dict(item) for item in job_history.get("jobs") or [] if isinstance(item, dict) and str(item.get("status") or "") == "failed"]
    file_count = sum(int(item.get("file_count") or 0) for item in enabled)
    errors = _error_actions("knowledge_base", knowledge)
    actions = list(errors)
    for item in missing[:4]:
        actions.append(_action("knowledge_base", "medium", f"创建知识库目录: {item.get('label') or item.get('knowledge_base_id')}", str(item.get("resolved_path") or item.get("path") or ""), "/desktop/knowledge-base"))
    for item in unindexed[:4]:
        actions.append(_action("knowledge_base", "medium", f"索引知识库: {item.get('label') or item.get('knowledge_base_id')}", str(item.get("path") or ""), "/desktop/knowledge-base"))
    for job in failed_jobs[:3]:
        title = f"检查知识任务失败: {job.get('target_id') or job.get('job_type') or 'knowledge'}"
        detail = str(job.get("error") or job.get("summary") or "最近知识索引/同步任务失败。")
        actions.append(_action("knowledge_base", "medium", title, detail, "/desktop/search-management"))
    return {
        "module_id": "knowledge_base",
        "label": "知识库",
        "status": _status(errors=errors, needs_attention=bool(missing or unindexed or failed_jobs)),
        "endpoint": "/desktop/knowledge-base",
        "desktop_page": "agents:knowledge",
        "description": "按 Agent 或领域配置知识库目录、导入文件和索引状态。",
        "summary": f"启用 {len(enabled)} · 文件 {file_count} · 未索引 {len(unindexed)} · 失败任务 {len(failed_jobs)}",
        "metrics": [
            {"label": "启用库", "value": len(enabled)},
            {"label": "文本文件", "value": file_count},
            {"label": "未索引", "value": len(unindexed)},
            {"label": "失败任务", "value": len(failed_jobs)},
        ],
        "actions": actions,
    }


def _search_module(search: dict[str, Any]) -> dict[str, Any]:
    web = dict(search.get("web_search") or {})
    retrieval = dict(search.get("knowledge_retrieval") or {})
    gaps = [dict(item) for item in search.get("missing_capabilities") or [] if isinstance(item, dict)]
    knowledge_jobs = dict(search.get("knowledge_jobs") or {})
    failed_jobs = [dict(item) for item in knowledge_jobs.get("jobs") or [] if isinstance(item, dict) and str(item.get("status") or "") == "failed"]
    errors = _error_actions("search_management", search)
    actions = list(errors)
    for gap in gaps[:6]:
        actions.append(_action("search_management", str(gap.get("priority") or "medium"), str(gap.get("title") or "Search/RAG 缺口"), str(gap.get("detail") or ""), "/desktop/search-management"))
    for job in failed_jobs[:3]:
        title = f"排查索引/同步失败: {job.get('target_id') or job.get('job_type') or 'knowledge'}"
        detail = str(job.get("error") or job.get("summary") or "最近知识索引/同步任务失败。")
        actions.append(_action("search_management", "medium", title, detail, "/desktop/search-management"))
    return {
        "module_id": "search_management",
        "label": "搜索 / RAG",
        "status": _status(errors=errors, needs_attention=bool(gaps or failed_jobs)),
        "endpoint": "/desktop/search-management",
        "desktop_page": "search",
        "description": "Web search provider、知识库检索、embedding、reranker 和主流模型能力对比。",
        "summary": f"Web {web.get('provider') or '--'} · KB {retrieval.get('backend') or '--'} · Embedding {retrieval.get('embedding_provider') or '--'} · Reranker {retrieval.get('reranker') or '--'} · 失败任务 {len(failed_jobs)}",
        "metrics": [
            {"label": "Web Provider", "value": web.get("provider") or "--"},
            {"label": "KB Backend", "value": retrieval.get("backend") or "--"},
            {"label": "Embedding", "value": retrieval.get("embedding_provider") or "--"},
            {"label": "Reranker", "value": retrieval.get("reranker") or "--"},
            {"label": "缺口", "value": len(gaps)},
            {"label": "失败任务", "value": len(failed_jobs)},
        ],
        "actions": actions,
    }


def _service_ports_module(service_ports: dict[str, Any]) -> dict[str, Any]:
    services = [dict(item) for item in service_ports.get("services") or [] if isinstance(item, dict)]
    duplicate_ports = dict(service_ports.get("duplicate_ports") or {})
    env_overrides = dict(service_ports.get("env_overrides") or {})
    required = [item for item in services if bool(item.get("required", True))]
    listening_required = [item for item in required if bool(item.get("listening"))]
    optional_listening = [item for item in services if not bool(item.get("required", True)) and bool(item.get("listening"))]
    errors = _error_actions("service_ports", service_ports)
    actions = list(errors)
    for port, service_ids in duplicate_ports.items():
        actions.append(
            _action(
                "service_ports",
                "high",
                f"修复重复端口 {port}",
                "多个服务声明了同一个端口：" + ", ".join(str(item) for item in service_ids),
                "/desktop/services",
            )
        )
    if env_overrides:
        actions.append(_action("service_ports", "low", "确认端口环境变量覆盖", f"当前有 {len(env_overrides)} 个端口通过环境变量覆盖。", "/desktop/services"))
    return {
        "module_id": "service_ports",
        "label": "服务端口",
        "status": _status(errors=errors, needs_attention=bool(duplicate_ports)),
        "endpoint": "/desktop/service-ports",
        "desktop_page": "operations:services",
        "description": "本地服务端口、环境变量覆盖、重复端口检测和服务路由快照。",
        "summary": f"服务 {len(services)} · 必需 {len(required)} · 正在监听 {len(listening_required)}/{len(required)} · 可选监听 {len(optional_listening)} · 覆盖 {len(env_overrides)}",
        "metrics": [
            {"label": "声明服务", "value": len(services)},
            {"label": "必需服务", "value": len(required)},
            {"label": "必需监听", "value": f"{len(listening_required)}/{len(required)}"},
            {"label": "环境覆盖", "value": len(env_overrides)},
            {"label": "重复端口", "value": len(duplicate_ports)},
        ],
        "actions": actions,
    }


def _project_runtime_module(project_runtime: dict[str, Any]) -> dict[str, Any]:
    profiles = [dict(item) for item in project_runtime.get("profiles") or [] if isinstance(item, dict)]
    blocked = int(project_runtime.get("blocked_count") or 0)
    review_required = int(project_runtime.get("review_required_count") or 0)
    events = [dict(item) for item in project_runtime.get("recent_events") or [] if isinstance(item, dict)]
    errors = _error_actions("project_runtime", project_runtime)
    actions = list(errors)
    for profile in profiles:
        policy = dict(profile.get("execution_policy") or {})
        project_title = str(profile.get("title") or profile.get("project_id") or "项目")
        blockers = [dict(item) for item in policy.get("blockers") or [] if isinstance(item, dict)]
        warnings = [dict(item) for item in policy.get("warnings") or [] if isinstance(item, dict)]
        if blockers:
            actions.append(
                _action(
                    "project_runtime",
                    "high",
                    f"修复项目启动阻断: {project_title}",
                    str(blockers[0].get("detail") or blockers[0].get("message") or "项目启动命令被运行边界阻止。"),
                    "/desktop/project-runtime",
                )
            )
        elif warnings and bool(policy.get("review_required")):
            actions.append(
                _action(
                    "project_runtime",
                    "medium",
                    f"审核项目启动命令: {project_title}",
                    str(warnings[0].get("detail") or warnings[0].get("message") or "项目启动命令需要人工确认。"),
                    "/desktop/project-runtime",
                )
            )
    if not profiles:
        actions.append(_action("project_runtime", "low", "登记项目运行配置", "当前没有项目运行配置；需要时可在项目页补充工作目录和启动命令。", "/desktop/project-runtime"))
    return {
        "module_id": "project_runtime",
        "label": "项目运行",
        "status": _status(errors=errors, needs_attention=bool(blocked or review_required)),
        "endpoint": "/desktop/project-runtime",
        "desktop_page": "project_runtime",
        "description": "项目工作目录、启动命令、环境文件和运行边界审核。",
        "summary": f"项目 {len(profiles)} · 阻断 {blocked} · 待审核 {review_required} · 最近记录 {len(events)}",
        "metrics": [
            {"label": "项目", "value": len(profiles)},
            {"label": "阻断", "value": blocked},
            {"label": "待审核", "value": review_required},
            {"label": "最近记录", "value": len(events)},
        ],
        "actions": actions[:8],
    }


def _resource_registry_module(resource_management: dict[str, Any]) -> dict[str, Any]:
    registry = dict(resource_management.get("resource_registry") or {})
    total = int(registry.get("total") or resource_management.get("resource_count") or 0)
    gaps = [dict(item) for item in registry.get("gaps") or [] if isinstance(item, dict)]
    gap_count = int(resource_management.get("gap_count") or len(gaps))
    type_counts = dict(registry.get("type_counts") or {})
    owner_counts = dict(registry.get("owner_counts") or {})
    errors = _error_actions("resource_registry", resource_management)
    actions = list(errors)
    if total == 0:
        actions.append(_action("resource_registry", "medium", "登记首批长期资源", "先登记店铺、账号、浏览器 Profile、知识库或仓库资源，供 Agent 围绕 Resource 规划。", "/desktop/resource-registry"))
    if gap_count:
        high_gaps = sum(1 for item in gaps if item.get("priority") == "high")
        priority = "high" if high_gaps else "medium"
        actions.append(_action("resource_registry", priority, "补齐 Resource Contract", f"{gap_count} 个资源缺少 owner/capability 或存在弱 credential_ref。", "/desktop/resource-registry"))
    return {
        "module_id": "resource_registry",
        "label": "Resource Registry",
        "status": _status(errors=errors, needs_attention=bool(total == 0 or gap_count)),
        "endpoint": "/desktop/resource-registry",
        "desktop_page": "resource-registry",
        "description": "长期数字资产登记：店铺、账号、浏览器 Profile、设备、知识库、仓库和媒体库。",
        "summary": f"资源 {total} · 类型 {len(type_counts)} · owner {len(owner_counts)} · gap {gap_count}",
        "metrics": [
            {"label": "资源", "value": total},
            {"label": "类型", "value": len(type_counts)},
            {"label": "Owner", "value": len(owner_counts)},
            {"label": "Gap", "value": gap_count},
        ],
        "actions": actions[:8],
    }


def _state_maintenance_module(state_maintenance: dict[str, Any]) -> dict[str, Any]:
    summary = dict(state_maintenance.get("summary") or {})
    components = [dict(item) for item in state_maintenance.get("components") or [] if isinstance(item, dict)]
    attention = int(summary.get("attention_count") or 0)
    total_count = int(summary.get("total_count") or 0)
    total_size = int(summary.get("total_size_bytes") or 0)
    errors = _error_actions("state_maintenance", state_maintenance)
    actions = list(errors)
    for component in components:
        if not bool(component.get("needs_attention")):
            continue
        label = str(component.get("label") or component.get("component_id") or "状态数据")
        actions.append(
            _action(
                "state_maintenance",
                "medium",
                f"清理或迁移状态数据: {label}",
                str(component.get("attention_reason") or component.get("path") or "该状态组件需要维护。"),
                "/desktop/state-maintenance",
            )
        )
    return {
        "module_id": "state_maintenance",
        "label": "状态维护",
        "status": _status(errors=errors, needs_attention=bool(attention)),
        "endpoint": "/desktop/state-maintenance",
        "desktop_page": "state_maintenance",
        "description": "桌面状态、工作流记录、移动端素材、手机命令记录和运行日志清理。",
        "summary": f"组件 {len(components)} · 待处理 {attention} · 记录 {total_count} · 大小 {total_size} bytes",
        "metrics": [
            {"label": "组件", "value": len(components)},
            {"label": "待处理", "value": attention},
            {"label": "记录", "value": total_count},
            {"label": "总大小", "value": total_size},
        ],
        "actions": actions[:8],
    }


def _action_log_module(action_log: dict[str, Any]) -> dict[str, Any]:
    sources = [dict(item) for item in action_log.get("sources") or [] if isinstance(item, dict)]
    events = [dict(item) for item in action_log.get("events") or [] if isinstance(item, dict)]
    errors = _error_actions("action_log", action_log)
    source_errors = [dict(item) for item in action_log.get("errors") or [] if isinstance(item, dict)]
    actions = list(errors)
    for item in source_errors[:5]:
        source = str(item.get("source") or "日志来源")
        detail = str(item.get("detail") or item.get("error") or "该日志来源读取失败。")
        actions.append(_action("action_log", "medium", f"修复操作记录来源: {source}", detail, "/desktop/action-log"))
    failed_sources = [item for item in sources if not bool(item.get("ok", True))]
    return {
        "module_id": "action_log",
        "label": "操作记录",
        "status": _status(errors=errors, needs_attention=bool(source_errors or failed_sources)),
        "endpoint": "/desktop/action-log",
        "desktop_page": "action_log",
        "description": "桌面服务、手机端、工作流、Skill、项目运行和安全记录的统一审计视图。",
        "summary": f"记录 {len(events)} · 来源 {len(sources)} · 异常来源 {len(source_errors) + len(failed_sources)}",
        "metrics": [
            {"label": "记录", "value": len(events)},
            {"label": "可用记录", "value": int(action_log.get("available_event_count") or len(events))},
            {"label": "来源", "value": len(sources)},
            {"label": "异常来源", "value": len(source_errors) + len(failed_sources)},
        ],
        "actions": actions[:8],
    }


def _mcp_module(mcp: dict[str, Any]) -> dict[str, Any]:
    servers = [dict(item) for item in mcp.get("servers") or [] if isinstance(item, dict)]
    mappings = [dict(item) for item in mcp.get("tool_mappings") or [] if isinstance(item, dict)]
    enabled = int(mcp.get("enabled_count") or 0)
    ready = int(mcp.get("ready_count") or 0)
    attention = int(mcp.get("attention_count") or 0)
    errors = _error_actions("mcp_management", mcp)
    actions = list(errors)
    if not servers:
        actions.append(_action("mcp_management", "low", "登记 MCP Server", "MCP 管理层为空；需要先登记候选 server，再审核后启用工具映射。", "/desktop/mcp-management"))
    if attention:
        actions.append(_action("mcp_management", "medium", "审核 MCP Server 风险", f"{attention} 个 MCP Server 需要检查传输、权限、工具声明或审核状态。", "/desktop/mcp-management"))
    return {
        "module_id": "mcp_management",
        "label": "MCP 管理",
        "status": _status(errors=errors, needs_attention=bool(attention)),
        "endpoint": "/desktop/mcp-management",
        "desktop_page": "mcp",
        "description": "MCP Server 注册、传输方式、权限作用域、工具映射和启停治理。",
        "summary": f"Server {len(servers)} · enabled {enabled} · ready {ready} · mappings {len(mappings)}",
        "metrics": [
            {"label": "Server", "value": len(servers)},
            {"label": "Enabled", "value": enabled},
            {"label": "Ready", "value": ready},
            {"label": "Mappings", "value": len(mappings)},
        ],
        "actions": actions,
    }


def _mobile_module(mobile: dict[str, Any]) -> dict[str, Any]:
    android = dict(mobile.get("android") or {})
    ios = dict(mobile.get("ios") or {})
    android_endpoint = dict(android.get("endpoint") or {})
    ios_endpoint = dict(ios.get("endpoint") or {})
    active_device = dict(android.get("active_device") or {})
    apk = dict(android.get("apk") or {})
    installed = dict(android.get("installed") or {})
    errors = _error_actions("mobile_management", mobile)
    actions = list(errors)

    android_health_ok = bool(dict(android_endpoint.get("health") or {}).get("ok"))
    ios_health_ok = bool(dict(ios_endpoint.get("health") or {}).get("ok"))
    apk_exists = bool(apk.get("exists"))
    package_installed = bool(installed.get("installed"))
    device_connected = bool(active_device.get("serial"))

    if not android_health_ok:
        actions.append(_action("mobile_management", "medium", "启动 Android 手机端服务", "Android 服务入口未在线；手机分享链接和后台同步不会进入主控。", "/desktop/mobile-management"))
    if not device_connected:
        actions.append(_action("mobile_management", "medium", "重连 Android 本机调试", "当前没有可用 Android 调试连接；需要启用无线调试或运行重连脚本。", "/desktop/mobile-management"))
    if not apk_exists:
        actions.append(_action("mobile_management", "medium", "构建 Android 手机端安装包", "Android 手机端安装包不存在或路径未配置。", "/desktop/mobile-management"))
    elif not package_installed:
        actions.append(_action("mobile_management", "low", "安装 Android 手机端", "安装包已存在，但未检测到手机已安装 Android 手机端。", "/desktop/mobile-management"))
    if not ios_health_ok:
        actions.append(_action("mobile_management", "low", "按需启动 iOS 主控入口", "iOS 主控入口未在线；当前为可选服务。", "/desktop/mobile-management"))

    status = _status(errors=errors, needs_attention=bool(not android_health_ok or not device_connected or not apk_exists))
    return {
        "module_id": "mobile_management",
        "label": "移动端与设备管理",
        "status": status,
        "endpoint": "/desktop/mobile-management",
        "desktop_page": "mobile",
        "description": "Android 手机端、PDD 链接接收、本机调试连接、安装包和 iOS 主控入口。",
        "summary": (
            f"Android 服务 {'在线' if android_health_ok else '离线'} · "
            f"本机调试 {'已连接' if device_connected else '未连接'} · "
            f"安装包 {'已存在' if apk_exists else '缺失'} · "
            f"iOS 主控 {'在线' if ios_health_ok else '离线/可选'}"
        ),
        "metrics": [
            {"label": "Android 端口", "value": int(android_endpoint.get("port") or 0) or "--"},
            {"label": "Android 服务", "value": "在线" if android_health_ok else "离线"},
            {"label": "本机调试", "value": active_device.get("serial") or "--"},
            {"label": "安装包", "value": "已存在" if apk_exists else "缺失"},
            {"label": "手机已安装", "value": "是" if package_installed else "否"},
            {"label": "iOS 主控", "value": "在线" if ios_health_ok else "离线"},
        ],
        "actions": actions[:8],
    }


def _models_module(model_catalog: dict[str, Any], learning: dict[str, Any]) -> dict[str, Any]:
    models = [dict(item) for item in model_catalog.get("models") or [] if isinstance(item, dict)]
    local_policy = build_local_model_policy_snapshot(model_catalog=model_catalog)
    brain_replacement = build_brain_replacement_snapshot(model_catalog=model_catalog)
    role_assignments = [dict(item) for item in local_policy.get("role_assignments") or [] if isinstance(item, dict)]
    benchmark = dict(local_policy.get("scheduler_benchmark") or {})
    adapters = dict(brain_replacement.get("adapter_registry") or {})
    failures = [dict(item) for item in model_catalog.get("failures") or [] if isinstance(item, dict)]
    assist_models = [dict(item) for item in learning.get("assist_models") or [] if isinstance(item, dict)]
    configured = [item for item in assist_models if bool(item.get("enabled", True)) and bool(item.get("configured"))]
    provider_settings = dict(learning.get("model_provider_settings") or {})
    providers = [dict(item) for item in learning.get("model_providers") or [] if isinstance(item, dict)]
    configured_providers = [item for item in providers if bool(item.get("configured"))]
    errors = _error_actions("model_catalog", model_catalog) + _error_actions("learning", learning)
    actions = list(errors)
    if not configured and not configured_providers and not bool(provider_settings.get("configured")):
        actions.append(_action("models", "medium", "配置至少一个评审/辅助模型", "模型管理里需要一个可用的云端或本地辅助模型，供 Skill 失败复盘和多模型评审使用。", "/desktop/learning"))
    if failures:
        actions.append(_action("models", "low", "检查模型目录刷新失败", f"{len(failures)} 个模型刷新失败，可稍后重试或检查网络。", "/desktop/model-catalog"))
    if benchmark.get("status") != "passed":
        actions.append(_action("models", "medium", "运行 Scheduler 本地模型基准", "35B-A3B/27B 本地角色切分需要 JSON、工具调用、Workflow 步骤和上下文漂移基准证据。", "/desktop/model-catalog"))
    if not adapters.get("adapter_count"):
        actions.append(_action("models", "medium", "注册 Brain Adapter", "模型替换必须先注册 base/LoRA adapter，并通过能力基准与评审 gate。", "/desktop/model-catalog"))
    return {
        "module_id": "models",
        "label": "模型管理",
        "status": _status(errors=errors, needs_attention=bool(actions)),
        "endpoint": "/desktop/model-catalog",
        "desktop_page": "models",
        "description": "候选基础模型目录、Provider 配置和评审/辅助模型。",
        "summary": f"候选模型 {len(models)} · 本地角色 {len(role_assignments)} · Brain Adapter {int(adapters.get('adapter_count') or 0)} · Benchmark {benchmark.get('status') or 'not_run'}",
        "metrics": [
            {"label": "候选模型", "value": len(models)},
            {"label": "辅助模型", "value": len(assist_models)},
            {"label": "可用配置", "value": len(configured) + len(configured_providers)},
            {"label": "本地角色", "value": len(role_assignments)},
            {"label": "Brain Adapter", "value": int(adapters.get("adapter_count") or 0)},
            {"label": "基准用例", "value": int(benchmark.get("case_count") or 0)},
        ],
        "actions": actions,
    }


def _code_jury_module(code_jury: dict[str, Any]) -> dict[str, Any]:
    summary = dict(code_jury.get("summary") or {})
    capabilities = dict(code_jury.get("capabilities") or {})
    decisions = dict(summary.get("decision_counts") or {})
    audit_count = int(summary.get("audit_count") or 0)
    latest = str(summary.get("latest_decision") or "")
    errors = _error_actions("code_jury", code_jury)
    actions = list(errors)
    if not capabilities.get("structured_jury_report"):
        actions.append(_action("code_jury", "high", "补齐结构化 JuryReport", "Code/UI Jury 必须输出结构化分数、问题和建议，不能只保存自由文本。", "/desktop/code-jury"))
    if capabilities.get("auto_apply_reviewer_patch") is not False:
        actions.append(_action("code_jury", "high", "关闭评审模型直接改代码", "云端 reviewer 只能判断和建议，补丁必须由受控 Patch Synthesizer 生成。", "/desktop/code-jury"))
    if audit_count == 0:
        actions.append(_action("code_jury", "medium", "运行首个结构化代码评审包", "为高风险代码、UI 截图或 PR 生成 CodeReviewPackage 并保存 JuryReport 审计。", "/desktop/code-jury"))
    status = "blocked" if errors or any(item.get("priority") == "high" for item in actions) else ("needs_attention" if actions else "ready")
    return {
        "module_id": "code_jury",
        "label": "Code/UI Jury",
        "status": status,
        "endpoint": "/desktop/code-jury",
        "desktop_page": "models",
        "description": "结构化代码/UI 评审包、裁判报告、补丁合成计划和 promotion gate 证据。",
        "summary": f"审计 {audit_count} · 最新 {latest or '--'} · 决策 {len(decisions)}",
        "metrics": [
            {"label": "审计", "value": audit_count},
            {"label": "Approved", "value": int(decisions.get("approved") or 0)},
            {"label": "Changes", "value": int(decisions.get("changes_requested") or 0)},
            {"label": "Blocked", "value": int(decisions.get("blocked") or 0)},
        ],
        "actions": actions[:8],
    }


def _collaboration_module(collaboration: dict[str, Any]) -> dict[str, Any]:
    overview = dict(collaboration.get("overview") or {})
    active_tasks = int(overview.get("active_task_count") or 0)
    claims = int(overview.get("active_file_claim_count") or 0)
    decisions = int(overview.get("decision_count") or 0)
    reviews = int(overview.get("review_count") or 0)
    errors = _error_actions("collaboration", collaboration)
    actions = list(errors)
    if active_tasks == 0:
        actions.append(_action("collaboration", "low", "创建协作任务", "为 Opus/Codex/Reviewer 分工建立任务账本，记录 owner、允许文件、禁止文件和验证命令。", "/desktop/collaboration"))
    if claims == 0:
        actions.append(_action("collaboration", "low", "登记文件归属", "为活跃 UI 重构、后端集成或评审任务登记文件 claim，降低模型间覆盖风险。", "/desktop/collaboration"))
    return {
        "module_id": "collaboration",
        "label": "协作控制",
        "status": _status(errors=errors, needs_attention=bool(active_tasks == 0 or claims == 0)),
        "endpoint": "/desktop/collaboration",
        "desktop_page": "collaboration",
        "description": "跨模型任务账本、文件占用、决策记录、评审记录和 context pack 生成。",
        "summary": f"活跃任务 {active_tasks} · 文件占用 {claims} · 决策 {decisions} · 评审 {reviews}",
        "metrics": [
            {"label": "活跃任务", "value": active_tasks},
            {"label": "文件占用", "value": claims},
            {"label": "决策", "value": decisions},
            {"label": "评审", "value": reviews},
        ],
        "actions": actions,
    }


def _governance_module(ecosystem: dict[str, Any]) -> dict[str, Any]:
    score = dict(ecosystem.get("score") or {})
    systems = dict(ecosystem.get("systems") or {})
    module_governance = dict(systems.get("module_governance") or {})
    portfolio = dict(module_governance.get("portfolio") or {})
    triage = dict(ecosystem.get("proposal_triage") or {})
    triage_counts = dict((triage.get("counts") or {}).get("by_bucket") or {})
    pending = int(ecosystem.get("pending_count") or 0)
    total_score = int(score.get("total") or 0)
    risk_count = int(portfolio.get("critical_high_risk_count") or 0)
    convert_to_task = int(triage_counts.get("convert_to_task") or 0)
    apply_after_review = int(triage_counts.get("apply_after_review") or 0)
    errors = _error_actions("module_governance", ecosystem)
    actions = list(errors)
    if pending:
        detail = f"{pending} 个 proposal 等待处理"
        if convert_to_task or apply_after_review:
            detail += f"；转任务 {convert_to_task} · 审核后可执行 {apply_after_review}"
        else:
            detail += "。"
        actions.append(_action("module_governance", "medium", "处理治理 Proposal 队列", detail, "/desktop/ecosystem-review"))
    if risk_count:
        actions.append(_action("module_governance", "high", "清理关键模块高风险项", f"{risk_count} 个 critical 模块处于 high risk。", "/desktop/ecosystem-review"))
    status = "blocked" if risk_count or errors else ("needs_attention" if pending or total_score < 70 else "ready")
    return {
        "module_id": "module_governance",
        "label": "模块治理",
        "status": status,
        "endpoint": "/desktop/ecosystem-review",
        "desktop_page": "overview",
        "description": "生态评分、模块成熟度、风险队列和 proposal 审核。",
        "summary": f"生态分 {total_score or '--'} · 待审 {pending} · 转任务 {convert_to_task} · 关键高风险 {risk_count}",
        "metrics": [
            {"label": "生态分", "value": total_score or "--"},
            {"label": "待审 Proposal", "value": pending},
            {"label": "转任务建议", "value": convert_to_task},
            {"label": "审核可执行", "value": apply_after_review},
            {"label": "关键高风险", "value": risk_count},
        ],
        "actions": actions,
    }


def _module_actions(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for module in modules:
        for action in module.get("actions") or []:
            if isinstance(action, dict):
                actions.append(dict(action))
    return actions


def _ecosystem_actions(ecosystem: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for proposal in ecosystem.get("proposals") or []:
        if not isinstance(proposal, dict) or str(proposal.get("status") or "pending") != "pending":
            continue
        category = str(proposal.get("category") or "module_governance")
        actions.append(
            _action(
                category,
                str(proposal.get("risk_level") or "medium"),
                str(proposal.get("title") or "待处理 Proposal"),
                str(proposal.get("detail") or ""),
                "/desktop/ecosystem-review",
                source="ecosystem_review",
                proposal_id=str(proposal.get("proposal_id") or ""),
            )
        )
    return actions[:8]


def _error_actions(module_id: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    error = str(snapshot.get("error") or "").strip()
    if not error:
        return []
    return [_action(module_id, "high", "模块快照读取失败", error, "")]


def _action(
    module_id: str,
    priority: str,
    title: str,
    detail: str,
    endpoint: str,
    *,
    source: str = "module_management",
    proposal_id: str = "",
) -> dict[str, Any]:
    payload = {
        "module_id": module_id,
        "priority": priority if priority in {"high", "medium", "low"} else "medium",
        "title": title,
        "detail": detail,
        "endpoint": endpoint,
        "source": source,
    }
    if proposal_id:
        payload["proposal_id"] = proposal_id
    return payload


def _status(*, errors: list[dict[str, Any]], needs_attention: bool) -> str:
    if errors:
        return "blocked"
    return "needs_attention" if needs_attention else "ready"


def _portfolio_status(modules: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in modules}
    if "blocked" in statuses:
        return "blocked"
    if "needs_attention" in statuses:
        return "needs_attention"
    return "ready"


def _gap_priority(gap: dict[str, Any]) -> str:
    priority = str(gap.get("priority") or gap.get("severity") or "medium").lower()
    return priority if priority in {"high", "medium", "low"} else "medium"


def _priority_order(value: Any) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(value), 9)
