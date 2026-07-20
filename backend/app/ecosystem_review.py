from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from backend.app.agent_management import load_agent_management_state
from backend.app.knowledge_base_management import (
    build_knowledge_base_snapshot,
    index_knowledge_base,
    resolve_knowledge_base_path,
)
from backend.app.learning_workflow import (
    append_learning_record,
    discover_model_providers,
    load_assist_models,
    load_learning_records,
    request_multi_model_review,
    resolve_training_dataset,
)
from backend.app.module_governance import build_module_governance_snapshot
from backend.app.operations_center import build_service_snapshots, handle_service_action, list_project_logs
from backend.app.skills_console import build_desktop_skills_snapshot, handle_desktop_skills_action
from backend.prompts.review import ECOSYSTEM_REVIEW_PROMPT
from backend.state_store import resolve_state_path

SCHEMA_VERSION = "spiritkin.ecosystem_review.v1"
DEFAULT_ECOSYSTEM_REVIEW_STATE = "state/desktop_console/ecosystem_review.json"
LOW_RISK_ACTION_TYPES = {"knowledge.ensure_directory", "knowledge.index", "knowledge.write_note", "learning.record"}
MEDIUM_RISK_ACTION_TYPES = {"service.restart", "skills.review_candidates", "skill.save_candidate"}
MANUAL_ACTION_TYPES = {
    "manual.configure_assist_model",
    "manual.configure_review_gate",
    "manual.configure_skill_assist",
    "manual.disable_external_write",
    "manual.module_governance",
    "manual.review_model_suggestion",
}
VALID_PROPOSAL_STATUSES = {"pending", "approved", "rejected", "applied", "failed"}


@dataclass(frozen=True)
class EcosystemDimensionScore:
    dimension_id: str
    label: str
    score: int
    status: str
    weight: float = 1.0
    signals: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "label": self.label,
            "score": int(max(0, min(100, self.score))),
            "status": self.status,
            "weight": self.weight,
            "signals": dict(self.signals),
        }


@dataclass(frozen=True)
class EcosystemIssue:
    issue_id: str
    category: str
    severity: str
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    proposal_id: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "evidence": dict(self.evidence),
            "proposal_id": self.proposal_id,
        }


@dataclass(frozen=True)
class EcosystemProposal:
    proposal_id: str
    source: str
    category: str
    title: str
    detail: str
    risk_level: str = "low"
    status: str = "pending"
    actions: tuple[dict[str, Any], ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    reviewer: str = ""
    reviewed_at: float = 0.0
    review_note: str = ""
    apply_result: dict[str, Any] = field(default_factory=dict)

    @property
    def auto_apply_allowed(self) -> bool:
        if self.risk_level != "low":
            return False
        return bool(self.actions) and all(str(action.get("type") or "") in LOW_RISK_ACTION_TYPES for action in self.actions)

    def snapshot(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source": self.source,
            "category": self.category,
            "title": self.title,
            "detail": self.detail,
            "risk_level": self.risk_level,
            "status": self.status,
            "actions": [dict(action) for action in self.actions],
            "evidence": dict(self.evidence),
            "created_at": self.created_at,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "review_note": self.review_note,
            "apply_result": dict(self.apply_result),
            "auto_apply_allowed": self.auto_apply_allowed,
            "approval_required": self.risk_level != "low" or not self.auto_apply_allowed,
        }


def resolve_ecosystem_review_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_ECOSYSTEM_REVIEW_STATE", DEFAULT_ECOSYSTEM_REVIEW_STATE, path)


def build_ecosystem_review_snapshot(*, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    saved = _load_state()
    scan = _scan_ecosystem(root)
    proposals = _merge_saved_proposals(scan["proposals"], saved)
    model_reviews = [dict(item) for item in saved.get("model_reviews") or [] if isinstance(item, dict)][-10:]
    actions = [dict(item) for item in saved.get("applied_actions") or [] if isinstance(item, dict)][-30:]
    dimensions = [dimension.snapshot() for dimension in scan["dimensions"]]
    total_score = _total_score(scan["dimensions"])
    status_counts: dict[str, int] = {}
    for proposal in proposals:
        status_counts[proposal.status] = status_counts.get(proposal.status, 0) + 1
    proposal_triage = _build_proposal_triage(proposals)
    triage_by_id = {str(item.get("proposal_id") or ""): item for item in proposal_triage.get("items", []) if isinstance(item, dict)}
    proposal_snapshots: list[dict[str, Any]] = []
    for proposal in proposals:
        snapshot = proposal.snapshot()
        snapshot["triage"] = dict(triage_by_id.get(proposal.proposal_id) or {})
        proposal_snapshots.append(snapshot)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "state_path": str(resolve_ecosystem_review_state_path()),
        "project_root": str(root),
        "score": {
            "total": total_score,
            "status": _score_status(total_score),
            "dimensions": dimensions,
        },
        "issues": [issue.snapshot() for issue in scan["issues"]],
        "proposals": proposal_snapshots,
        "proposal_triage": proposal_triage,
        "proposal_status_counts": status_counts,
        "pending_count": status_counts.get("pending", 0),
        "approved_count": status_counts.get("approved", 0),
        "applied_count": status_counts.get("applied", 0),
        "model_reviews": model_reviews,
        "applied_actions": actions,
        "systems": scan["systems"],
        "capabilities": {
            "review_queue": True,
            "multi_model_review": True,
            "safe_apply_low_risk_by_default": True,
            "proposal_triage": True,
            "supported_action_types": sorted(LOW_RISK_ACTION_TYPES | MEDIUM_RISK_ACTION_TYPES | MANUAL_ACTION_TYPES),
            "high_risk_auto_apply": False,
        },
    }


def refresh_ecosystem_review_state(*, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    snapshot = build_ecosystem_review_snapshot(project_root=project_root)
    state = _load_state()
    _save_state(
        {
            **state,
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "last_snapshot": {
                "generated_at": snapshot["generated_at"],
                "score": snapshot["score"],
                "issue_count": len(snapshot["issues"]),
                "pending_count": snapshot["pending_count"],
            },
            "proposals": snapshot["proposals"],
        }
    )
    return build_ecosystem_review_snapshot(project_root=project_root)


def handle_ecosystem_review_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "scan").strip().lower()
    if action in {"snapshot", "scan", "refresh"}:
        return {"ok": True, "ecosystem_review": refresh_ecosystem_review_state()}
    if action == "triage":
        snapshot = build_ecosystem_review_snapshot()
        return {"ok": True, "proposal_triage": snapshot.get("proposal_triage", {}), "ecosystem_review": snapshot}
    if action == "multi_model_review":
        return _handle_multi_model_review(payload)
    if action in {"approve", "reject"}:
        proposal_id = str(payload.get("proposal_id") or "").strip()
        if not proposal_id:
            raise ValueError("proposal_id is required")
        status = "approved" if action == "approve" else "rejected"
        proposal = update_proposal_status(
            proposal_id,
            status,
            reviewer=str(payload.get("reviewer") or "human"),
            review_note=str(payload.get("review_note") or payload.get("reason") or ""),
        )
        return {"ok": True, "proposal": proposal.snapshot(), "ecosystem_review": build_ecosystem_review_snapshot()}
    if action == "apply_approved":
        proposal_ids = [str(item).strip() for item in payload.get("proposal_ids") or [] if str(item).strip()]
        allow_risk_levels = {str(item).strip().lower() for item in payload.get("allow_risk_levels") or ["low"] if str(item).strip()}
        if bool(payload.get("allow_medium")):
            allow_risk_levels.add("medium")
        result = apply_approved_proposals(proposal_ids=proposal_ids or None, allow_risk_levels=allow_risk_levels)
        return {"ok": bool(result.get("ok", True)), "apply": result, "ecosystem_review": build_ecosystem_review_snapshot()}
    if action == "reset":
        _save_state({"schema_version": SCHEMA_VERSION, "updated_at": time.time(), "proposals": [], "model_reviews": [], "applied_actions": []})
        return {"ok": True, "ecosystem_review": build_ecosystem_review_snapshot()}
    raise ValueError(f"unsupported ecosystem review action: {action}")


def update_proposal_status(proposal_id: str, status: str, *, reviewer: str = "human", review_note: str = "") -> EcosystemProposal:
    normalized = status.strip().lower()
    if normalized not in {"approved", "rejected", "pending"}:
        raise ValueError("status must be approved, rejected, or pending")
    state = _ensure_state_has_current_proposals()
    proposals = [_proposal_from_dict(item) for item in state.get("proposals") or [] if isinstance(item, dict)]
    updated: list[EcosystemProposal] = []
    selected: EcosystemProposal | None = None
    for proposal in proposals:
        if proposal.proposal_id == proposal_id:
            selected = replace(
                proposal,
                status=normalized,
                reviewer=reviewer,
                reviewed_at=time.time(),
                review_note=review_note,
                apply_result={},
            )
            updated.append(selected)
        else:
            updated.append(proposal)
    if selected is None:
        raise ValueError(f"unknown proposal_id: {proposal_id}")
    _save_state({**state, "updated_at": time.time(), "proposals": [proposal.snapshot() for proposal in updated]})
    return selected


def apply_approved_proposals(
    *,
    proposal_ids: list[str] | None = None,
    allow_risk_levels: set[str] | None = None,
) -> dict[str, Any]:
    state = _ensure_state_has_current_proposals()
    allowed = {item.lower() for item in (allow_risk_levels or {"low"})}
    allowed.discard("high")
    wanted = {item for item in (proposal_ids or []) if item}
    proposals = [_proposal_from_dict(item) for item in state.get("proposals") or [] if isinstance(item, dict)]
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    updated: list[EcosystemProposal] = []
    for proposal in proposals:
        if proposal.status != "approved" or (wanted and proposal.proposal_id not in wanted):
            updated.append(proposal)
            continue
        if proposal.risk_level not in allowed:
            skipped.append({"proposal_id": proposal.proposal_id, "reason": f"risk_not_allowed:{proposal.risk_level}"})
            updated.append(proposal)
            continue
        allowed_action_types = set(LOW_RISK_ACTION_TYPES)
        if "medium" in allowed:
            allowed_action_types.update(MEDIUM_RISK_ACTION_TYPES)
        try:
            result = _apply_proposal_actions(proposal, allowed_action_types=allowed_action_types)
        except Exception as exc:
            failure = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "applied_at": time.time()}
            applied.append({"proposal_id": proposal.proposal_id, **failure})
            updated.append(replace(proposal, status="failed", apply_result=failure))
            continue
        if int(result.get("executed_count") or 0) == 0:
            skipped.append({"proposal_id": proposal.proposal_id, "reason": "no_executable_actions", "apply_result": result})
            updated.append(replace(proposal, apply_result=result))
            continue
        applied.append({"proposal_id": proposal.proposal_id, **result})
        updated.append(replace(proposal, status="applied", apply_result=result))
    action_log = [dict(item) for item in state.get("applied_actions") or [] if isinstance(item, dict)]
    action_log.extend(applied)
    _save_state(
        {
            **state,
            "updated_at": time.time(),
            "proposals": [proposal.snapshot() for proposal in updated],
            "applied_actions": action_log[-100:],
        }
    )
    return {"ok": True, "applied": applied, "skipped": skipped, "applied_count": len(applied), "skipped_count": len(skipped)}


def _scan_ecosystem(root: Path) -> dict[str, Any]:
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []
    dimensions: list[EcosystemDimensionScore] = []
    systems: dict[str, Any] = {}

    agent_scan = _scan_agents()
    dimensions.append(agent_scan["dimension"])
    issues.extend(agent_scan["issues"])
    proposals.extend(agent_scan["proposals"])
    systems["agents"] = agent_scan["summary"]

    skills_scan = _scan_skills()
    dimensions.append(skills_scan["dimension"])
    issues.extend(skills_scan["issues"])
    proposals.extend(skills_scan["proposals"])
    systems["skills"] = skills_scan["summary"]

    knowledge_scan = _scan_knowledge()
    dimensions.append(knowledge_scan["dimension"])
    issues.extend(knowledge_scan["issues"])
    proposals.extend(knowledge_scan["proposals"])
    systems["knowledge"] = knowledge_scan["summary"]

    operations_scan = _scan_operations(root)
    dimensions.append(operations_scan["dimension"])
    issues.extend(operations_scan["issues"])
    proposals.extend(operations_scan["proposals"])
    systems["operations"] = operations_scan["summary"]

    learning_scan = _scan_learning()
    dimensions.append(learning_scan["dimension"])
    issues.extend(learning_scan["issues"])
    proposals.extend(learning_scan["proposals"])
    systems["learning"] = learning_scan["summary"]

    safety_scan = _scan_safety()
    dimensions.append(safety_scan["dimension"])
    issues.extend(safety_scan["issues"])
    proposals.extend(safety_scan["proposals"])
    systems["safety"] = safety_scan["summary"]

    governance_scan = _scan_governance(root)
    dimensions.append(governance_scan["dimension"])
    issues.extend(governance_scan["issues"])
    proposals.extend(governance_scan["proposals"])
    systems["module_governance"] = governance_scan["summary"]

    deduped_proposals = _dedupe_proposals(proposals)
    proposal_ids = {proposal.proposal_id for proposal in deduped_proposals}
    deduped_issues = [issue for issue in _dedupe_issues(issues) if not issue.proposal_id or issue.proposal_id in proposal_ids]
    return {"dimensions": dimensions, "issues": deduped_issues, "proposals": deduped_proposals, "systems": systems}


def _scan_agents() -> dict[str, Any]:
    state = load_agent_management_state()
    agents = list(state.agents)
    enabled_agents = [agent for agent in agents if agent.enabled]
    active_profile = next((profile for profile in state.route_profiles if profile.profile_id == state.active_route_profile_id), None)
    active_members = [member for member in (active_profile.members if active_profile else ()) if member.enabled]
    reviewers = [member for member in active_members if "review" in member.role or "review" in " ".join(member.capabilities)]
    enabled_external_reviewers = [assistant for assistant in state.external_assistants if assistant.enabled and assistant.review_only]
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []

    score = 100
    if not enabled_agents:
        score -= 55
        issues.append(_issue("agents", "critical", "没有启用的 Agent", "Agent 管理中没有启用任何可路由 Agent。", {"agent_count": len(agents)}))
    if active_profile is None:
        score -= 30
        issues.append(_issue("agents", "high", "缺少 active route profile", "模型组合没有 active route profile，规划器无法明确执行/评审职责。", {"active_route_profile_id": state.active_route_profile_id}))
    if not reviewers and not enabled_external_reviewers:
        score -= 18
        proposal = _proposal(
            "review_gate",
            "启用外部评审门",
            "当前 active route profile 或外部助手里没有启用的 review-only 评审者；高风险任务应先进入评审队列。",
            "medium",
            [{"type": "manual.configure_review_gate", "target": "agent_management"}],
            {"active_route_profile_id": state.active_route_profile_id},
        )
        issues.append(_issue("agents", "medium", "评审门未启用", "缺少可见的 review-only 评审成员，自动改动缺少二次检查。", {"reviewer_count": 0}, proposal.proposal_id))
        proposals.append(proposal)
    if not state.skill_assist.enabled:
        score -= 10
        proposal = _proposal(
            "skill_assist",
            "开启 Skill 辅助审核策略",
            "Skill 失败和候选升级应进入 human/cloud review，而不是只依赖运行时临时判断。",
            "medium",
            [{"type": "manual.configure_skill_assist", "target": "agent_management.skill_assist"}],
            state.skill_assist.snapshot(),
        )
        issues.append(_issue("agents", "medium", "Skill 辅助策略未启用", "Skill 自我进化闭环缺少统一审核门。", {}, proposal.proposal_id))
        proposals.append(proposal)

    summary = {
        "agent_count": len(agents),
        "enabled_agent_count": len(enabled_agents),
        "active_route_profile_id": state.active_route_profile_id,
        "active_member_count": len(active_members),
        "reviewer_count": len(reviewers),
        "external_review_only_count": len(enabled_external_reviewers),
        "skill_assist": state.skill_assist.snapshot(),
    }
    return {"dimension": _dimension("agents", "Agent 编排", score, summary, weight=1.15), "issues": issues, "proposals": proposals, "summary": summary}


def _scan_skills() -> dict[str, Any]:
    try:
        snapshot = build_desktop_skills_snapshot()
    except Exception as exc:
        issue = _issue("skills", "high", "Skill 存储读取失败", "无法读取 Skill store，候选升级和运行验证都不可见。", {"error": f"{type(exc).__name__}: {exc}"})
        return {"dimension": _dimension("skills", "Skill 进化", 35, {"error": issue.evidence}, weight=0.95), "issues": [issue], "proposals": [], "summary": {"error": issue.evidence}}

    count = int(snapshot.get("count") or 0)
    counts = dict(snapshot.get("status_counts") or {})
    candidates = int(counts.get("candidate") or 0)
    active = int(counts.get("active") or 0)
    score = 78 if count else 48
    if active:
        score += min(12, active * 3)
    if candidates:
        score -= min(24, candidates * 4)
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []
    if count == 0:
        issues.append(_issue("skills", "low", "暂无持久化 Skill", "系统可以运行原子工具，但尚未沉淀可复用 Skill。", {"store_path": snapshot.get("store_path")}))
    if candidates:
        proposal = _proposal(
            "skill_candidate_review",
            "审核候选 Skill",
            f"检测到 {candidates} 个候选 Skill。先运行规则评审并保留人工确认，再决定是否提升为 active。",
            "medium",
            [{"type": "skills.review_candidates", "reviewer": "ecosystem_review"}],
            {"candidate_count": candidates, "candidate_reviews": snapshot.get("candidate_reviews") or []},
        )
        issues.append(_issue("skills", "medium", "候选 Skill 等待审核", "候选 Skill 不应长期停留在 candidate 状态。", {"candidate_count": candidates}, proposal.proposal_id))
        proposals.append(proposal)
    summary = {"count": count, "status_counts": counts, "store_path": snapshot.get("store_path"), "candidate_reviews": snapshot.get("candidate_reviews") or []}
    return {"dimension": _dimension("skills", "Skill 进化", score, summary, weight=0.95), "issues": issues, "proposals": proposals, "summary": summary}


def _scan_knowledge() -> dict[str, Any]:
    try:
        snapshot = build_knowledge_base_snapshot()
    except Exception as exc:
        issue = _issue("knowledge", "high", "知识库状态读取失败", "无法读取 Agent 知识库配置或路径。", {"error": f"{type(exc).__name__}: {exc}"})
        return {"dimension": _dimension("knowledge", "知识库", 35, {"error": issue.evidence}, weight=0.9), "issues": [issue], "proposals": [], "summary": {"error": issue.evidence}}

    records = [dict(item) for item in snapshot.get("knowledge_bases") or [] if isinstance(item, dict)]
    enabled = [item for item in records if item.get("enabled", True)]
    missing = [item for item in enabled if not item.get("exists")]
    unindexed = [
        item
        for item in enabled
        if item.get("exists") and int(item.get("file_count") or 0) > 0 and not (item.get("last_index") or {}).get("updated_at")
    ]
    stale = [
        item
        for item in enabled
        if item.get("exists")
        and (item.get("last_index") or {}).get("updated_at")
        and int(item.get("file_count") or 0) > int((item.get("last_index") or {}).get("document_count") or 0)
    ]
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []
    for kb in missing[:8]:
        proposal = _proposal(
            "knowledge_ensure_directory",
            f"创建知识库目录: {kb.get('label') or kb.get('knowledge_base_id')}",
            "Agent 知识库已启用但目录不存在，先创建目录，后续才能接收导入和增量索引。",
            "low",
            [{"type": "knowledge.ensure_directory", "knowledge_base_id": kb.get("knowledge_base_id"), "path": kb.get("path")}],
            {"knowledge_base": kb},
        )
        issues.append(_issue("knowledge", "low", "知识库目录不存在", str(kb.get("path") or ""), {"knowledge_base_id": kb.get("knowledge_base_id")}, proposal.proposal_id))
        proposals.append(proposal)
    for kb in (unindexed + stale)[:10]:
        proposal = _proposal(
            "knowledge_index",
            f"重建知识库索引: {kb.get('label') or kb.get('knowledge_base_id')}",
            "知识库存在可索引文本，但缺少索引或索引落后。批准后只会重建该 KB 的本地索引文件。",
            "low",
            [{"type": "knowledge.index", "knowledge_base_id": kb.get("knowledge_base_id"), "path": kb.get("path")}],
            {"knowledge_base": kb},
        )
        issues.append(_issue("knowledge", "medium", "知识库索引缺失或落后", str(kb.get("path") or ""), {"file_count": kb.get("file_count")}, proposal.proposal_id))
        proposals.append(proposal)

    indexed = len([item for item in enabled if (item.get("last_index") or {}).get("updated_at")])
    score = 55
    if enabled:
        score = 55 + int((indexed / max(1, len(enabled))) * 35)
    score -= min(25, len(missing) * 4 + len(unindexed) * 5 + len(stale) * 3)
    summary = {
        "count": len(records),
        "enabled_count": len(enabled),
        "missing_count": len(missing),
        "indexed_count": indexed,
        "unindexed_count": len(unindexed),
        "stale_count": len(stale),
        "supported_extensions": snapshot.get("supported_extensions") or [],
    }
    return {"dimension": _dimension("knowledge", "知识库", score, summary, weight=0.9), "issues": issues, "proposals": proposals, "summary": summary}


def _scan_operations(root: Path) -> dict[str, Any]:
    services = build_service_snapshots()
    logs = [log.snapshot() for log in list_project_logs(project_root=root)]
    stopped = [item for item in services if item.get("enabled", True) and item.get("autostart", True) and item.get("status") != "running"]
    error_logs = [item for item in logs if int(item.get("error_count") or 0) > 0]
    warning_logs = [item for item in logs if int(item.get("warning_count") or 0) > 0]
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []

    for service in stopped[:8]:
        proposal = _proposal(
            "service_restart",
            f"重启服务: {service.get('label') or service.get('service_id')}",
            "服务被标记为 enabled/autostart，但当前未运行。批准后可通过 Operations Center 执行 restart。",
            "medium",
            [{"type": "service.restart", "service_id": service.get("service_id")}],
            {"service": service},
        )
        issues.append(_issue("operations", "medium", "关键服务未运行", str(service.get("label") or service.get("service_id")), {"service_id": service.get("service_id")}, proposal.proposal_id))
        proposals.append(proposal)

    for log in error_logs[:8]:
        service_id = _service_id_from_log(log)
        actions: list[dict[str, Any]] = []
        if service_id:
            actions.append({"type": "service.restart", "service_id": service_id})
        proposal = _proposal(
            "log_repair",
            f"处理错误日志: {log.get('log_id')}",
            "日志尾部出现 error/exception/traceback。批准低风险动作可先把错误摘要写入知识库；如允许 medium，可再重启关联服务。",
            "low",
            [
                {
                    "type": "knowledge.write_note",
                    "path": _knowledge_note_path_for_log(log),
                    "title": f"Error log review: {log.get('log_id')}",
                    "content": _knowledge_note_content_for_log(log),
                    "tags": ["operations", "error-log", "ecosystem-review"],
                },
                {
                    "type": "learning.record",
                    "record": {
                        "source": "ecosystem_review",
                        "problem": f"Error log {log.get('log_id')} has {log.get('error_count')} errors.",
                        "correction": "Investigate the captured log tail, add a regression check, and restart only after review if the service is safe to recycle.",
                        "tags": ["operations", "log_repair"],
                        "metadata": {"log_id": log.get("log_id"), "path": log.get("path")},
                    },
                },
                *actions,
            ],
            {"log": {key: log.get(key) for key in ("log_id", "path", "error_count", "warning_count", "updated_at")}},
        )
        severity = "high" if int(log.get("error_count") or 0) >= 3 else "medium"
        issues.append(_issue("operations", severity, "日志包含错误", str(log.get("log_id") or ""), {"error_count": log.get("error_count")}, proposal.proposal_id))
        proposals.append(proposal)

    score = 100
    score -= min(35, len(stopped) * 8)
    score -= min(35, len(error_logs) * 7)
    score -= min(10, len(warning_logs) * 2)
    summary = {
        "service_count": len(services),
        "running_service_count": sum(1 for item in services if item.get("status") == "running"),
        "stopped_autostart_count": len(stopped),
        "log_count": len(logs),
        "error_log_count": len(error_logs),
        "warning_log_count": len(warning_logs),
        "top_error_logs": [{key: item.get(key) for key in ("log_id", "path", "error_count", "warning_count")} for item in error_logs[:5]],
    }
    return {"dimension": _dimension("operations", "运行与日志", score, summary, weight=1.05), "issues": issues, "proposals": proposals, "summary": summary}


def _scan_learning() -> dict[str, Any]:
    records = load_learning_records(limit=200)
    providers = discover_model_providers()
    assist_models = load_assist_models()
    configured_providers = [provider for provider in providers if provider.configured]
    configured_assist = [model for model in assist_models if model.configured]
    dataset_path = resolve_training_dataset()
    dataset_count = _jsonl_line_count(dataset_path)
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []
    if not records:
        issues.append(_issue("learning", "low", "学习样本不足", "还没有足够人工纠错、失败轨迹或模型评审样本。", {"record_count": 0}))
    if not configured_assist:
        proposal = _proposal(
            "model_review_provider",
            "配置多模型评审助手",
            "生态评审可以生成结构化 proposal，但多模型外部评审需要至少一个已配置 assist model。",
            "medium",
            [{"type": "manual.configure_assist_model", "target": "desktop.learning.assist_models"}],
            {"configured_assist_model_count": 0},
        )
        issues.append(_issue("learning", "medium", "未配置多模型评审助手", "无法调用外部模型进行交叉评审。", {}, proposal.proposal_id))
        proposals.append(proposal)
    skill_records = [record for record in records if record.skill_name and record.correction.strip()]
    for record in skill_records[-5:]:
        proposal = _proposal(
            "skill_patch",
            f"生成 Skill 修补候选: {record.skill_name}",
            "基于学习记录生成 Skill 修补 proposal。默认不直接覆盖现有 Skill，只保存为 candidate 供审核。",
            "medium",
            [
                {
                    "type": "skill.save_candidate",
                    "skill": {
                        "name": f"{record.skill_name}.patch_candidate",
                        "description": f"Patch candidate generated from learning record {record.record_id}",
                        "status": "candidate",
                        "success_criteria": [record.correction],
                        "eval_cases": [record.problem],
                        "metadata": {
                            "source": "ecosystem_review",
                            "learning_record_id": record.record_id,
                            "base_skill_name": record.skill_name,
                        },
                    },
                }
            ],
            record.snapshot(),
        )
        proposals.append(proposal)

    score = 45 + min(30, len(records) * 2) + min(15, dataset_count) + (10 if configured_assist else 0)
    summary = {
        "record_count": len(records),
        "dataset_path": str(dataset_path),
        "dataset_count": dataset_count,
        "configured_provider_count": len(configured_providers),
        "assist_model_count": len(assist_models),
        "configured_assist_model_count": len(configured_assist),
        "skill_learning_record_count": len(skill_records),
    }
    return {"dimension": _dimension("learning", "学习闭环", score, summary, weight=0.85), "issues": issues, "proposals": proposals, "summary": summary}


def _scan_safety() -> dict[str, Any]:
    state = load_agent_management_state()
    write_enabled = [assistant for assistant in state.external_assistants if assistant.enabled and assistant.allow_write]
    not_review_only = [assistant for assistant in state.external_assistants if assistant.enabled and not assistant.review_only]
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []
    if write_enabled:
        proposal = _proposal(
            "external_write_guard",
            "关闭外部助手默认写权限",
            "外部 CLI/API 助手应默认 review-only；写文件需要单独批准的工作流。",
            "high",
            [{"type": "manual.disable_external_write", "assistant_ids": [assistant.assistant_id for assistant in write_enabled]}],
            {"assistants": [assistant.snapshot() for assistant in write_enabled]},
        )
        issues.append(_issue("safety", "high", "外部助手启用了写权限", "自动写入边界过宽，容易绕过项目审核门。", {"assistant_count": len(write_enabled)}, proposal.proposal_id))
        proposals.append(proposal)
    if not_review_only:
        issues.append(_issue("safety", "medium", "外部助手不是 review-only", "外部助手应先作为评审者，不应默认接管执行。", {"assistant_ids": [assistant.assistant_id for assistant in not_review_only]}))
    score = 100 - min(45, len(write_enabled) * 25) - min(25, len(not_review_only) * 10)
    summary = {
        "external_assistant_count": len(state.external_assistants),
        "external_write_enabled_count": len(write_enabled),
        "external_not_review_only_count": len(not_review_only),
        "high_risk_auto_apply": False,
    }
    return {"dimension": _dimension("safety", "安全与审核门", score, summary, weight=1.2), "issues": issues, "proposals": proposals, "summary": summary}


def _scan_governance(root: Path) -> dict[str, Any]:
    snapshot = build_module_governance_snapshot(root)
    portfolio = dict(snapshot.get("portfolio") or {})
    modules = [dict(item) for item in snapshot.get("modules") or [] if isinstance(item, dict)]
    backlog = [dict(item) for item in snapshot.get("improvement_backlog") or [] if isinstance(item, dict)]
    issues: list[EcosystemIssue] = []
    proposals: list[EcosystemProposal] = []

    for risk in portfolio.get("top_risks") or []:
        if not isinstance(risk, dict):
            continue
        severity = "high" if str(risk.get("risk_level") or "") == "high" else "medium"
        module_id = str(risk.get("module_id") or "")
        proposal = _proposal(
            "module_governance",
            f"治理模块缺口: {risk.get('label') or module_id}",
            "企业级治理扫描发现该模块存在成熟度或控制项缺口。该 proposal 只进入人工治理队列，不会自动修改代码。",
            severity,
            [
                {
                    "type": "manual.module_governance",
                    "module_id": module_id,
                    "risk_level": risk.get("risk_level"),
                    "gaps": risk.get("gaps") or [],
                }
            ],
            {"module_risk": risk},
        )
        issues.append(
            _issue(
                "module_governance",
                severity,
                f"模块治理风险: {risk.get('label') or module_id}",
                f"{module_id} maturity={risk.get('maturity_score')} gaps={', '.join(str(item) for item in risk.get('gaps') or [])}",
                {"module_id": module_id, "risk_level": risk.get("risk_level"), "maturity_score": risk.get("maturity_score")},
                proposal.proposal_id,
            )
        )
        proposals.append(proposal)

    for action in backlog[:12]:
        module_id = str(action.get("module_id") or "")
        risk = _risk(str(action.get("risk_level") or "medium"))
        proposals.append(
            _proposal(
                "module_governance_action",
                str(action.get("title") or f"模块治理改进: {module_id}"),
                str(action.get("detail") or "补齐企业级治理控制项。"),
                risk,
                [
                    {
                        "type": "manual.module_governance",
                        "module_id": module_id,
                        "owner_role": action.get("owner_role"),
                        "control": action.get("control"),
                        "verification_commands": action.get("verification_commands") or [],
                    }
                ],
                {"governance_action": action},
            )
        )

    score = int(portfolio.get("score") or 0)
    summary = {
        "portfolio": portfolio,
        "modules": modules,
        "improvement_backlog": backlog,
        "operating_model": snapshot.get("operating_model") or {},
    }
    return {"dimension": _dimension("module_governance", "模块治理", score, portfolio, weight=1.1), "issues": issues, "proposals": proposals, "summary": summary}


def _handle_multi_model_review(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = refresh_ecosystem_review_state()
    problem = _ecosystem_review_prompt(snapshot)
    review = request_multi_model_review(
        problem,
        skill_name="ecosystem_review",
        context=json.dumps({"score": snapshot["score"], "issues": snapshot["issues"][:12]}, ensure_ascii=False),
        model_ids=[str(item) for item in payload.get("model_ids") or []] if isinstance(payload.get("model_ids"), list) else None,
    )
    review_snapshot = review.snapshot()
    proposals = [_proposal_from_dict(item) for item in snapshot.get("proposals") or [] if isinstance(item, dict)]
    proposals.extend(_model_review_to_proposals(review_snapshot))
    state = _load_state()
    model_reviews = [dict(item) for item in state.get("model_reviews") or [] if isinstance(item, dict)]
    model_reviews.append(review_snapshot)
    merged = _merge_saved_proposals(_dedupe_proposals(proposals), {"proposals": state.get("proposals") or []})
    _save_state(
        {
            **state,
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "model_reviews": model_reviews[-20:],
            "proposals": [proposal.snapshot() for proposal in merged],
        }
    )
    return {
        "ok": review.ok or review.status == "not_configured",
        "multi_model_review": review_snapshot,
        "ecosystem_review": build_ecosystem_review_snapshot(),
    }


def _model_review_to_proposals(review_snapshot: dict[str, Any]) -> list[EcosystemProposal]:
    proposals: list[EcosystemProposal] = []
    for review in review_snapshot.get("reviews") or []:
        if not isinstance(review, dict) or not review.get("ok"):
            continue
        text = str(review.get("response_text") or "").strip()
        if not text:
            continue
        extracted = _extract_json_proposals(text)
        if extracted:
            for item in extracted[:8]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "外部模型改进建议").strip()
                detail = str(item.get("detail") or item.get("description") or text[:1000]).strip()
                risk = _risk(str(item.get("risk_level") or item.get("risk") or "medium"))
                actions = tuple(_normalize_model_actions(item.get("actions") or []))
                proposals.append(
                    _proposal(
                        "model_review",
                        title,
                        detail,
                        risk,
                        list(actions) or [{"type": "manual.review_model_suggestion"}],
                        {"model_review": {key: review.get(key) for key in ("provider", "model", "status", "endpoint")}, "raw": item},
                        source="external_model",
                    )
                )
            continue
        proposals.append(
            _proposal(
                "model_review",
                f"外部模型建议: {review.get('model') or review.get('provider')}",
                text[:4000],
                "medium",
                [{"type": "manual.review_model_suggestion"}],
                {"model_review": {key: review.get(key) for key in ("provider", "model", "status", "endpoint")}},
                source="external_model",
            )
        )
    return proposals


def _normalize_model_actions(raw_actions: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_actions, list):
        return []
    actions: list[dict[str, Any]] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("type") or "").strip()
        if action_type in LOW_RISK_ACTION_TYPES | MEDIUM_RISK_ACTION_TYPES:
            actions.append(dict(item))
    return actions


def _extract_json_proposals(text: str) -> list[Any]:
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    start = text.find("[")
    end = text.rfind("]")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            proposals = parsed.get("proposals")
            return proposals if isinstance(proposals, list) else [parsed]
        if isinstance(parsed, list):
            return parsed
    return []


def _apply_proposal_actions(proposal: EcosystemProposal, *, allowed_action_types: set[str]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    executed_count = 0
    for action in proposal.actions:
        action_type = str(action.get("type") or "")
        if action_type not in LOW_RISK_ACTION_TYPES | MEDIUM_RISK_ACTION_TYPES:
            results.append({"type": action_type, "ok": True, "skipped": True, "reason": "manual_or_unsupported_action"})
            continue
        if action_type not in allowed_action_types:
            results.append({"type": action_type, "ok": True, "skipped": True, "reason": "action_risk_not_allowed"})
            continue
        if action_type == "knowledge.ensure_directory":
            path = resolve_knowledge_base_path(str(action.get("path") or ""))
            path.mkdir(parents=True, exist_ok=True)
            executed_count += 1
            results.append({"type": action_type, "ok": True, "path": str(path)})
        elif action_type == "knowledge.index":
            report = index_knowledge_base(str(action.get("knowledge_base_id") or "kb_custom"), str(action.get("path") or ""))
            executed_count += 1
            results.append({"type": action_type, "ok": True, "index": report.snapshot()})
        elif action_type == "knowledge.write_note":
            path = _resolve_knowledge_note_path(str(action.get("path") or ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            content = str(action.get("content") or "").strip()
            if not content:
                content = f"# {action.get('title') or 'Ecosystem Review Note'}\n\nGenerated at {time.strftime('%Y-%m-%d %H:%M:%S')}."
            path.write_text(content.rstrip() + "\n", encoding="utf-8")
            executed_count += 1
            results.append({"type": action_type, "ok": True, "path": str(path)})
        elif action_type == "learning.record":
            record_payload = action.get("record") if isinstance(action.get("record"), dict) else dict(action)
            record = append_learning_record(record_payload)
            executed_count += 1
            results.append({"type": action_type, "ok": True, "record": record.snapshot()})
        elif action_type == "service.restart":
            service_id = str(action.get("service_id") or "").strip()
            result = handle_service_action({"action": "restart", "service_id": service_id})
            executed_count += 1
            results.append({"type": action_type, "ok": bool(result.get("ok", False)), "service_action": result})
        elif action_type == "skills.review_candidates":
            result = handle_desktop_skills_action({"action": "review_candidates", "reviewer": str(action.get("reviewer") or "ecosystem_review")})
            executed_count += 1
            results.append({"type": action_type, "ok": bool(result.get("ok", True)), "skills_action": result})
        elif action_type == "skill.save_candidate":
            skill = action.get("skill") if isinstance(action.get("skill"), dict) else {}
            if not skill:
                raise ValueError("skill.save_candidate requires skill payload")
            result = handle_desktop_skills_action({"action": "save", "skill": {**skill, "status": str(skill.get("status") or "candidate")}})
            executed_count += 1
            results.append({"type": action_type, "ok": bool(result.get("ok", True)), "skills_action": result})
    return {
        "ok": all(bool(item.get("ok", False)) for item in results),
        "applied_at": time.time(),
        "executed_count": executed_count,
        "skipped_count": sum(1 for item in results if item.get("skipped")),
        "results": results,
    }


def _ensure_state_has_current_proposals() -> dict[str, Any]:
    refresh_ecosystem_review_state()
    return _load_state()


def _load_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_ecosystem_review_state_path(path)
    if not target.exists():
        return {"schema_version": SCHEMA_VERSION, "proposals": [], "model_reviews": [], "applied_actions": []}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "proposals": [], "model_reviews": [], "applied_actions": []}
    return payload if isinstance(payload, dict) else {"schema_version": SCHEMA_VERSION, "proposals": [], "model_reviews": [], "applied_actions": []}


def _save_state(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> None:
    target = resolve_ecosystem_review_state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _merge_saved_proposals(generated: list[EcosystemProposal], saved_state: dict[str, Any]) -> list[EcosystemProposal]:
    saved = [_proposal_from_dict(item) for item in saved_state.get("proposals") or [] if isinstance(item, dict)]
    saved_by_id = {proposal.proposal_id: proposal for proposal in saved}
    merged: list[EcosystemProposal] = []
    for proposal in generated:
        previous = saved_by_id.get(proposal.proposal_id)
        if previous is not None:
            proposal = replace(
                proposal,
                status=previous.status,
                created_at=previous.created_at,
                reviewer=previous.reviewer,
                reviewed_at=previous.reviewed_at,
                review_note=previous.review_note,
                apply_result=previous.apply_result,
            )
        merged.append(proposal)
    current_ids = {proposal.proposal_id for proposal in merged}
    for proposal in saved:
        if proposal.proposal_id not in current_ids and proposal.status in {"approved", "applied", "failed"}:
            merged.append(replace(proposal, evidence={**proposal.evidence, "stale": True}))
    return sorted(_dedupe_proposals(merged), key=lambda item: (_status_order(item.status), _risk_order(item.risk_level), item.category, item.title))


def _proposal_from_dict(data: dict[str, Any]) -> EcosystemProposal:
    return EcosystemProposal(
        proposal_id=str(data.get("proposal_id") or data.get("id") or _stable_id("proposal", json.dumps(data, sort_keys=True, default=str))),
        source=str(data.get("source") or "scan"),
        category=str(data.get("category") or "general"),
        title=str(data.get("title") or "Untitled proposal"),
        detail=str(data.get("detail") or ""),
        risk_level=_risk(str(data.get("risk_level") or "low")),
        status=_proposal_status(str(data.get("status") or "pending")),
        actions=tuple(dict(item) for item in data.get("actions") or [] if isinstance(item, dict)),
        evidence=dict(data.get("evidence") or {}),
        created_at=float(data.get("created_at") or time.time()),
        reviewer=str(data.get("reviewer") or ""),
        reviewed_at=float(data.get("reviewed_at") or 0.0),
        review_note=str(data.get("review_note") or ""),
        apply_result=dict(data.get("apply_result") or {}),
    )


def _issue(category: str, severity: str, title: str, detail: str, evidence: dict[str, Any] | None = None, proposal_id: str = "") -> EcosystemIssue:
    return EcosystemIssue(_stable_id("issue", category, title, detail), category, severity, title, detail, evidence or {}, proposal_id)


def _proposal(
    category: str,
    title: str,
    detail: str,
    risk_level: str,
    actions: list[dict[str, Any]],
    evidence: dict[str, Any] | None = None,
    *,
    source: str = "scan",
) -> EcosystemProposal:
    identity = json.dumps(
        {
            "category": category,
            "title": title,
            "actions": [_stable_action_identity(action) for action in actions],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return EcosystemProposal(_stable_id("proposal", identity), source, category, title, detail, _risk(risk_level), "pending", tuple(actions), evidence or {})


def _stable_action_identity(action: dict[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("type") or "")
    identity: dict[str, Any] = {"type": action_type}
    for key in (
        "knowledge_base_id",
        "path",
        "service_id",
        "target",
        "module_id",
        "control",
        "reviewer",
        "assistant_ids",
    ):
        if key in action:
            identity[key] = action.get(key)
    record = action.get("record") if isinstance(action.get("record"), dict) else {}
    if record:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if metadata.get("log_id") or metadata.get("path"):
            identity["record"] = {
                "source": record.get("source"),
                "log_id": metadata.get("log_id"),
                "path": metadata.get("path"),
            }
        else:
            identity["record"] = {
                "source": record.get("source"),
                "problem": record.get("problem"),
                "skill_name": record.get("skill_name"),
            }
    skill = action.get("skill") if isinstance(action.get("skill"), dict) else {}
    if skill:
        identity["skill_name"] = skill.get("name") or skill.get("skill_name")
    return identity


def _dimension(dimension_id: str, label: str, score: int, signals: dict[str, Any], *, weight: float = 1.0) -> EcosystemDimensionScore:
    bounded = int(max(0, min(100, score)))
    return EcosystemDimensionScore(dimension_id, label, bounded, _score_status(bounded), weight, signals)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\n".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:12]}"


def _risk(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in {"low", "medium", "high"} else "medium"


def _proposal_status(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in VALID_PROPOSAL_STATUSES else "pending"


def _build_proposal_triage(proposals: list[EcosystemProposal]) -> dict[str, Any]:
    items = [_triage_proposal(proposal) for proposal in proposals]
    by_bucket: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for item in items:
        bucket = str(item.get("bucket") or "unknown")
        status = str(item.get("status") or "pending")
        risk = str(item.get("risk_level") or "medium")
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        by_risk[risk] = by_risk.get(risk, 0) + 1
    ordered = sorted(items, key=lambda item: (_triage_bucket_order(str(item.get("bucket") or "")), _risk_order(str(item.get("risk_level") or "")), str(item.get("category") or ""), str(item.get("title") or "")))
    return {
        "schema_version": "spiritkin.ecosystem_proposal_triage.v1",
        "generated_at": time.time(),
        "total": len(items),
        "counts": {
            "by_bucket": by_bucket,
            "by_status": by_status,
            "by_risk": by_risk,
            "pending_actionable": sum(1 for item in items if item.get("bucket") in {"apply_after_review", "convert_to_task", "review_first"}),
            "noise_or_done": sum(1 for item in items if item.get("bucket") in {"stale_noise", "done_or_rejected"}),
        },
        "items": ordered,
    }


def _triage_proposal(proposal: EcosystemProposal) -> dict[str, Any]:
    status = proposal.status
    risk = proposal.risk_level
    action_types = [str(action.get("type") or "") for action in proposal.actions]
    stale = bool(proposal.evidence.get("stale"))
    has_manual = any(action_type in MANUAL_ACTION_TYPES or action_type.startswith("manual.") for action_type in action_types)
    has_executable = any(action_type in LOW_RISK_ACTION_TYPES or action_type in MEDIUM_RISK_ACTION_TYPES for action_type in action_types)
    if status in {"applied", "rejected"}:
        bucket = "done_or_rejected"
        recommendation = "archive"
        reason = f"proposal is already {status}"
    elif stale:
        bucket = "stale_noise"
        recommendation = "archive_or_reopen"
        reason = "proposal no longer appears in the current scan"
    elif status == "failed":
        bucket = "review_first"
        recommendation = "inspect_failure"
        reason = "previous apply attempt failed"
    elif risk == "high" or has_manual:
        bucket = "convert_to_task"
        recommendation = "create_tracked_task"
        reason = "manual or high-risk work needs an explicit owner and review path"
    elif proposal.auto_apply_allowed:
        bucket = "apply_after_review"
        recommendation = "approve_then_apply"
        reason = "all actions are low-risk and executable after approval"
    elif has_executable:
        bucket = "review_first"
        recommendation = "approve_with_risk_scope"
        reason = "proposal has executable actions but not all are low-risk auto-apply actions"
    else:
        bucket = "convert_to_task"
        recommendation = "create_tracked_task"
        reason = "proposal has no executable low-risk action"
    return {
        "proposal_id": proposal.proposal_id,
        "status": status,
        "category": proposal.category,
        "risk_level": risk,
        "title": proposal.title,
        "bucket": bucket,
        "recommendation": recommendation,
        "reason": reason,
        "action_types": action_types,
        "auto_apply_allowed": proposal.auto_apply_allowed,
        "stale": stale,
        "requires_owner": bucket in {"convert_to_task", "review_first"},
    }


def _triage_bucket_order(bucket: str) -> int:
    return {
        "convert_to_task": 0,
        "review_first": 1,
        "apply_after_review": 2,
        "stale_noise": 3,
        "done_or_rejected": 4,
    }.get(bucket, 9)


def _score_status(score: int) -> str:
    if score >= 85:
        return "healthy"
    if score >= 70:
        return "watch"
    if score >= 50:
        return "needs_attention"
    return "critical"


def _total_score(dimensions: list[EcosystemDimensionScore]) -> int:
    if not dimensions:
        return 0
    weighted = sum(dimension.score * dimension.weight for dimension in dimensions)
    weights = sum(dimension.weight for dimension in dimensions)
    return int(round(weighted / max(0.01, weights)))


def _status_order(status: str) -> int:
    return {"approved": 0, "pending": 1, "failed": 2, "rejected": 3, "applied": 4}.get(status, 9)


def _risk_order(risk_level: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(risk_level, 9)


def _dedupe_proposals(proposals: list[EcosystemProposal]) -> list[EcosystemProposal]:
    by_id: dict[str, EcosystemProposal] = {}
    for proposal in proposals:
        by_id.setdefault(proposal.proposal_id, proposal)
    return list(by_id.values())


def _dedupe_issues(issues: list[EcosystemIssue]) -> list[EcosystemIssue]:
    by_id: dict[str, EcosystemIssue] = {}
    for issue in issues:
        by_id.setdefault(issue.issue_id, issue)
    return list(by_id.values())


def _jsonl_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except OSError:
        return 0


def _service_id_from_log(log: dict[str, Any]) -> str:
    log_id = str(log.get("log_id") or log.get("path") or "").lower()
    for service_id in ("command_gateway", "event_bridge", "frontend", "voice_session", "remote_worker"):
        if service_id in log_id:
            return service_id
    return ""


def _knowledge_note_path_for_log(log: dict[str, Any]) -> str:
    digest = hashlib.sha1(str(log.get("log_id") or log.get("path") or time.time()).encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"state/knowledge_bases/domains/general/operations/error-log-{digest}.md"


def _knowledge_note_content_for_log(log: dict[str, Any]) -> str:
    tail = [str(line) for line in log.get("tail") or []][-30:]
    header = [
        f"# Error Log Review: {log.get('log_id')}",
        "",
        f"- Path: `{log.get('path')}`",
        f"- Error count: {log.get('error_count')}",
        f"- Warning count: {log.get('warning_count')}",
        f"- Updated at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(log.get('updated_at') or time.time())))}",
        "",
        "## Tail",
        "",
        "```text",
    ]
    return "\n".join([*header, *tail, "```", "", "## Review Notes", "", "- Root cause: pending review.", "- Repair: pending approved action."])


def _resolve_knowledge_note_path(raw_path: str) -> Path:
    root = (Path.cwd() / "state" / "knowledge_bases").resolve()
    target = Path(raw_path)
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    else:
        target = target.resolve()
    if target.suffix.lower() not in {".md", ".txt"}:
        target = target.with_suffix(".md")
    if not (target == root or root in target.parents):
        raise ValueError("knowledge note path must stay under state/knowledge_bases")
    return target


def _ecosystem_review_prompt(snapshot: dict[str, Any]) -> str:
    return ECOSYSTEM_REVIEW_PROMPT.substitute(
        score=json.dumps(snapshot.get("score"), ensure_ascii=False),
        issues=json.dumps(snapshot.get("issues", [])[:12], ensure_ascii=False),
        proposals=json.dumps(snapshot.get("proposals", [])[:12], ensure_ascii=False),
    )
