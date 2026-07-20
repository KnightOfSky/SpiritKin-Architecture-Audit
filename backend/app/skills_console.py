from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from backend.app.agent_management import load_agent_management_state
from backend.app.review_gate import evaluate_review_gate, payload_bool
from backend.app.settings import resolve_skill_store_path
from backend.app.skill_sources import (
    build_candidate_payloads_from_source,
    build_skill_sources_snapshot,
    get_skill_source,
    handle_skill_source_action,
    update_skill_source_lock,
)
from backend.code_jury import evaluate_jury_gate
from backend.skills.base import SkillRegistry, SkillRunner, SkillRunResult, SkillSpec, SkillStepSpec
from backend.skills.persistence import build_skill_store
from backend.skills.promotion import (
    CandidateReview,
    PromotionRuleSet,
    apply_candidate_review,
    evaluate_candidate,
    review_skill_candidates,
)
from backend.tools import build_default_tool_registry

DEFAULT_SKILL_EXPORT_DIR = "state/skill_exports"
DEFAULT_SKILL_OWNER_ID = "skill_runner"
DEFAULT_SKILL_SOURCE_TYPE = "human"
DEFAULT_SKILL_PROMOTION_STATUS = "draft"
DEFAULT_SKILL_REVIEW_GATE = "core_review"
DEFAULT_SKILL_RUN_AUDIT_LOG = "state/skills/skill_runs.jsonl"
DEFAULT_SKILL_MAX_STEPS = 20
DEFAULT_SKILL_MAX_DAILY_RUNS = 200
DEFAULT_SKILL_MAX_DAILY_NON_DRY_RUNS = 50


def _store():
    return build_skill_store(resolve_skill_store_path())


def _agent_profiles_by_id() -> dict[str, dict[str, Any]]:
    try:
        state = load_agent_management_state()
    except Exception:
        return {}
    return {agent.agent_id: agent.snapshot() for agent in state.agents if agent.agent_id}


def _default_workspace_for_agent(agent_id: str) -> str:
    safe = "".join(ch for ch in agent_id if ch.isalnum() or ch in {"_", "-"}) or DEFAULT_SKILL_OWNER_ID
    return f"state/agents/{safe}/workspace"


def _skill_workspace_allowed(agent_id: str, workspace_path: str) -> bool:
    raw = workspace_path.strip()
    if not raw:
        return False
    root = (Path.cwd() / _default_workspace_for_agent(agent_id)).resolve()
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        target = target.resolve()
    except OSError:
        return False
    return target == root or root in target.parents


def _infer_owner_agent_id(payload: dict[str, Any], metadata: dict[str, Any], agents: dict[str, dict[str, Any]]) -> str:
    explicit = str(payload.get("owner_agent_id") or metadata.get("owner_agent_id") or "").strip()
    if explicit:
        return explicit
    searchable = " ".join(
        str(value or "").lower()
        for value in (
            payload.get("name") or payload.get("skill_name"),
            payload.get("description"),
            " ".join(str(item) for item in payload.get("trigger_intents") or [] if not isinstance(payload.get("trigger_intents"), str)),
        )
    )
    domain_hints = (
        ("programming", ("code", "代码", "编程", "debug", "test", "bug", "python")),
        ("ecommerce", ("ecommerce", "电商", "商品", "店铺", "sku", "投放", "运营")),
        ("video_animation", ("video", "视频", "动画", "分镜", "字幕", "剪辑")),
        ("vision_model", ("vision", "image", "图像", "图片", "视觉", "截图", "ocr")),
        ("game_development", ("game", "游戏", "unity", "玩法")),
    )
    for agent_id, hints in domain_hints:
        if agent_id in agents and any(hint in searchable for hint in hints):
            return agent_id
    return DEFAULT_SKILL_OWNER_ID if DEFAULT_SKILL_OWNER_ID in agents else (next(iter(agents), DEFAULT_SKILL_OWNER_ID))


def _normalize_skill_ownership_metadata(payload: dict[str, Any], existing: SkillSpec | None, metadata: dict[str, Any]) -> dict[str, Any]:
    agents = _agent_profiles_by_id()
    owner_id = _infer_owner_agent_id(payload, metadata, agents)
    if agents and owner_id not in agents:
        raise ValueError(f"unknown owner_agent_id: {owner_id}")

    existing_owner = str((existing.metadata if existing else {}).get("owner_agent_id") or "").strip()
    if existing_owner and owner_id != existing_owner and not payload_bool(payload.get("allow_owner_reassign"), False):
        raise ValueError(f"skill owner mismatch: {existing_owner} -> {owner_id} requires allow_owner_reassign")

    profile = agents.get(owner_id, {})
    owner_domain = str(payload.get("owner_domain") or metadata.get("owner_domain") or profile.get("domain") or "skill").strip() or "skill"
    workspace_path = str(payload.get("workspace_path") or metadata.get("workspace_path") or _default_workspace_for_agent(owner_id)).strip()
    if not _skill_workspace_allowed(owner_id, workspace_path):
        raise ValueError(f"workspace_path must stay under {_default_workspace_for_agent(owner_id)}")

    metadata.update(
        {
            "owner_agent_id": owner_id,
            "owner_domain": owner_domain,
            "workspace_path": workspace_path,
            "source_type": str(payload.get("source_type") or metadata.get("source_type") or DEFAULT_SKILL_SOURCE_TYPE).strip() or DEFAULT_SKILL_SOURCE_TYPE,
            "promotion_status": str(payload.get("promotion_status") or metadata.get("promotion_status") or metadata.get("status") or DEFAULT_SKILL_PROMOTION_STATUS).strip() or DEFAULT_SKILL_PROMOTION_STATUS,
            "review_gate": str(payload.get("review_gate") or metadata.get("review_gate") or DEFAULT_SKILL_REVIEW_GATE).strip() or DEFAULT_SKILL_REVIEW_GATE,
            "managed_scope": "agent",
        }
    )
    return metadata


def _spec_to_snapshot(spec: SkillSpec) -> dict[str, Any]:
    metadata = dict(spec.metadata)
    return {
        "name": spec.name,
        "description": spec.description,
        "trigger_intents": list(spec.trigger_intents),
        "input_schema": spec.input_schema,
        "preconditions": list(spec.preconditions),
        "steps": [
            {
                "tool_name": step.tool_name,
                "arguments": step.arguments,
                "description": step.description,
                "optional": step.optional,
            }
            for step in spec.steps
        ],
        "tool_allowlist": list(spec.tool_allowlist),
        "risk_level": spec.risk_level,
        "confirmation_policy": spec.confirmation_policy,
        "rollback_strategy": spec.rollback_strategy,
        "success_criteria": list(spec.success_criteria),
        "memory_policy": spec.memory_policy,
        "eval_cases": list(spec.eval_cases),
        "version": spec.version,
        "usage_count": spec.usage_count,
        "output_schema": dict(spec.output_schema),
        "cost_hint": spec.cost_hint,
        "latency_hint_ms": spec.latency_hint_ms,
        "success_rate": spec.success_rate,
        "required_capabilities": list(spec.required_capabilities),
        "required_worker_needs": list(spec.required_worker_needs),
        "side_effects": list(spec.side_effects),
        "artifact_contract": dict(spec.artifact_contract),
        "metadata": metadata,
        "status": str(metadata.get("status") or "draft"),
        "owner_agent_id": str(metadata.get("owner_agent_id") or ""),
        "owner_domain": str(metadata.get("owner_domain") or ""),
        "workspace_path": str(metadata.get("workspace_path") or ""),
        "source_type": str(metadata.get("source_type") or ""),
        "promotion_status": str(metadata.get("promotion_status") or ""),
        "review_gate": str(metadata.get("review_gate") or ""),
        "debug_summary": _skill_debug_summary(metadata),
        **spec.governance_snapshot(),
    }


def _skill_jury_required(skill: SkillSpec, payload: dict[str, Any]) -> bool:
    metadata = dict(skill.metadata)
    explicit = payload.get("jury_required")
    if explicit is not None:
        return payload_bool(explicit, False)
    gate = payload.get("jury_gate") if isinstance(payload.get("jury_gate"), dict) else {}
    if "required" in gate:
        return payload_bool(gate.get("required"), False)
    for key in ("jury_required", "requires_jury", "code_jury_required"):
        if key in metadata:
            return payload_bool(metadata.get(key), False)
    review_gate = str(metadata.get("review_gate") or "").lower()
    if "jury" in review_gate or "code_review" in review_gate:
        return True
    if str(skill.risk_level or metadata.get("risk_level") or "").lower() in {"high", "critical"}:
        return True
    source_review = metadata.get("source_review") if isinstance(metadata.get("source_review"), dict) else {}
    if str(source_review.get("risk_level") or "").lower() in {"high", "critical"}:
        return True
    return False


def _skill_jury_review_type(skill: SkillSpec) -> str:
    metadata = dict(skill.metadata)
    if str(metadata.get("ui_binding_status") or "").lower() in {"required", "bound"}:
        return "ui"
    owner = str(metadata.get("owner_agent_id") or metadata.get("owner_domain") or "").lower()
    text = " ".join([skill.name, skill.description, owner, " ".join(skill.trigger_intents)]).lower()
    if any(token in text for token in ("ui", "video", "screenshot", "视觉", "截图", "界面")):
        return "ui"
    return "code"


def _skill_debug_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    def as_int(key: str) -> int:
        try:
            return int(metadata.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    def as_float(key: str) -> float:
        try:
            return float(metadata.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "total_count": as_int("total_count"),
        "success_count": as_int("success_count"),
        "failure_count": as_int("failure_count"),
        "success_rate": as_float("success_rate"),
        "dry_run_count": as_int("dry_run_count"),
        "replay_total_count": as_int("replay_total_count"),
        "replay_success_count": as_int("replay_success_count"),
        "replay_success_rate": as_float("replay_success_rate"),
        "last_run_at": metadata.get("last_run_at"),
        "last_run_success": metadata.get("last_run_success"),
        "last_run_dry_run": metadata.get("last_run_dry_run"),
        "last_run_message": str(metadata.get("last_run_message") or ""),
    }


def _spec_from_payload(payload: dict[str, Any], existing: SkillSpec | None = None) -> SkillSpec:
    def str_list(name: str) -> tuple[str, ...]:
        value = payload.get(name)
        if isinstance(value, str):
            return tuple(line.strip() for line in value.replace(",", "\n").splitlines() if line.strip())
        return tuple(str(item).strip() for item in (value or []) if str(item).strip())

    def dict_value(name: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        value = payload.get(name)
        if isinstance(value, str):
            try:
                value = json.loads(value or "{}")
            except json.JSONDecodeError:
                value = {}
        return dict(value or fallback or {})

    def optional_int(name: str, fallback: int | None = None) -> int | None:
        value = payload.get(name)
        if value is None:
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def optional_float(name: str, fallback: float | None = None) -> float | None:
        value = payload.get(name)
        if value is None:
            return fallback
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def parse_steps(value: Any) -> tuple[SkillStepSpec, ...]:
        if isinstance(value, str):
            try:
                value = json.loads(value or "[]")
            except json.JSONDecodeError:
                value = []
        steps: list[SkillStepSpec] = []
        for item in value or []:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            if not tool_name:
                continue
            steps.append(
                SkillStepSpec(
                    tool_name=tool_name,
                    arguments=dict(item.get("arguments") or {}),
                    description=str(item.get("description") or ""),
                    optional=bool(item.get("optional", False)),
                )
            )
        return tuple(steps)

    fallback_name = existing.name if existing is not None else ""
    name = str(payload.get("name") or payload.get("skill_name") or fallback_name).strip()
    if not name:
        raise ValueError("skill name is required")
    metadata = dict(existing.metadata if existing else {})
    incoming_meta = payload.get("metadata")
    if isinstance(incoming_meta, dict):
        metadata.update(incoming_meta)
    status = str(payload.get("status") or metadata.get("status") or "draft")
    metadata["status"] = status
    metadata = _normalize_skill_ownership_metadata(payload, existing, metadata)
    return SkillSpec(
        name=name,
        description=str(payload.get("description") if payload.get("description") is not None else (existing.description if existing else "")),
        trigger_intents=str_list("trigger_intents") or (existing.trigger_intents if existing else ()),
        input_schema=dict(payload.get("input_schema") or (existing.input_schema if existing else {})),
        preconditions=str_list("preconditions") or (existing.preconditions if existing else ()),
        steps=parse_steps(payload.get("steps")) or (existing.steps if existing else ()),
        tool_allowlist=str_list("tool_allowlist") or (existing.tool_allowlist if existing else ()),
        risk_level=str(payload.get("risk_level") or (existing.risk_level if existing else "low")),
        confirmation_policy=str(payload.get("confirmation_policy") or (existing.confirmation_policy if existing else "risk_based")),
        rollback_strategy=str(payload.get("rollback_strategy") or (existing.rollback_strategy if existing else "manual_review")),
        success_criteria=str_list("success_criteria") or (existing.success_criteria if existing else ()),
        memory_policy=str(payload.get("memory_policy") or (existing.memory_policy if existing else "record_summary")),
        eval_cases=str_list("eval_cases") or (existing.eval_cases if existing else ()),
        version=str(payload.get("version") or (existing.version if existing else "0.1.0")),
        usage_count=int(payload.get("usage_count") if payload.get("usage_count") is not None else (existing.usage_count if existing else 0)),
        metadata=metadata,
        output_schema=dict_value("output_schema", existing.output_schema if existing else {}),
        cost_hint=str(payload.get("cost_hint") if payload.get("cost_hint") is not None else (existing.cost_hint if existing else "")),
        latency_hint_ms=optional_int("latency_hint_ms", existing.latency_hint_ms if existing else None),
        success_rate=optional_float("success_rate", existing.success_rate if existing else None),
        required_capabilities=str_list("required_capabilities") or (existing.required_capabilities if existing else ()),
        required_worker_needs=str_list("required_worker_needs") or (existing.required_worker_needs if existing else ()),
        side_effects=str_list("side_effects") or (existing.side_effects if existing else ()),
        artifact_contract=dict_value("artifact_contract", existing.artifact_contract if existing else {}),
    )


def build_desktop_skills_snapshot() -> dict[str, Any]:
    store = _store()
    skills = sorted(store.list_all(), key=lambda item: (str(item.metadata.get("status") or "draft"), item.name))
    counts: dict[str, int] = {}
    owner_counts: dict[str, int] = {}
    missing_owner = 0
    for skill in skills:
        status = str(skill.metadata.get("status") or "draft")
        counts[status] = counts.get(status, 0) + 1
        owner = str(skill.metadata.get("owner_agent_id") or "")
        if owner:
            owner_counts[owner] = owner_counts.get(owner, 0) + 1
        else:
            missing_owner += 1
    candidates = [skill for skill in skills if skill.metadata.get("status") == "candidate"]
    reviews = [evaluate_candidate(skill, PromotionRuleSet()).__dict__ for skill in candidates]
    return {
        "store_path": str(Path(resolve_skill_store_path()).resolve()),
        "count": len(skills),
        "status_counts": counts,
        "owner_counts": owner_counts,
        "missing_owner_count": missing_owner,
        "skills": [_spec_to_snapshot(skill) for skill in skills],
        "skill_sources": build_skill_sources_snapshot(),
        "candidate_reviews": reviews,
        "execution_governance": build_skill_execution_governance_snapshot(),
        "recent_skill_runs": list_skill_run_audit_events(limit=20),
        "updated_at": time.time(),
    }


def _skill_run_snapshot(result: SkillRunResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "message": result.message,
        "skill_name": result.skill_name,
        "metadata": dict(result.metadata or {}),
        "step_results": [
            {
                "success": step.success,
                "message": step.message,
                "data": step.data,
                "error_code": step.error_code,
                "metadata": dict(step.metadata or {}),
            }
            for step in result.step_results
        ],
    }


def _metadata_int(metadata: dict[str, Any], key: str) -> int:
    try:
        return int(metadata.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _record_skill_run_metrics(
    skill: SkillSpec,
    result: SkillRunResult,
    *,
    dry_run: bool,
    started_at: float,
    actor: str,
) -> SkillSpec:
    metadata = dict(skill.metadata)
    now = time.time()
    planned_steps = result.metadata.get("planned_steps") if isinstance(result.metadata, dict) else None
    step_count = len(result.step_results) if result.step_results else (len(planned_steps) if isinstance(planned_steps, list) else len(skill.steps))
    entry = {
        "at": now,
        "actor": actor,
        "dry_run": bool(dry_run),
        "success": bool(result.success),
        "message": result.message[:500],
        "duration_ms": round((now - started_at) * 1000, 2),
        "step_count": step_count,
        "error_code": str(result.metadata.get("error_code") or "") if isinstance(result.metadata, dict) else "",
    }
    history = list(metadata.get("debug_run_history") or [])
    history.append(entry)
    metadata["debug_run_history"] = history[-30:]
    metadata["last_run"] = entry
    metadata["last_run_at"] = entry["at"]
    metadata["last_seen"] = entry["at"]
    metadata["last_run_success"] = entry["success"]
    metadata["last_run_dry_run"] = entry["dry_run"]
    metadata["last_run_message"] = entry["message"]
    if dry_run:
        replay_total = _metadata_int(metadata, "replay_total_count") + 1
        replay_success = _metadata_int(metadata, "replay_success_count") + (1 if result.success else 0)
        metadata["dry_run_count"] = _metadata_int(metadata, "dry_run_count") + 1
        metadata["replay_total_count"] = replay_total
        metadata["replay_success_count"] = replay_success
        metadata["replay_success_rate"] = replay_success / replay_total if replay_total else 0.0
    else:
        total = _metadata_int(metadata, "total_count") + 1
        success = _metadata_int(metadata, "success_count") + (1 if result.success else 0)
        metadata["total_count"] = total
        metadata["success_count"] = success
        metadata["failure_count"] = max(0, total - success)
        metadata["success_rate"] = success / total if total else 0.0
    return replace(skill, usage_count=skill.usage_count + (0 if dry_run else 1), metadata=metadata)


def build_skill_execution_governance_snapshot() -> dict[str, Any]:
    recent = list_skill_run_audit_events(limit=200)
    now = time.time()
    recent_24h = [event for event in recent if now - _float_or_default(event.get("at"), 0.0) <= 86400]
    return {
        "schema_version": "spiritkin.skill_execution_governance.v1",
        "audit_log_path": str(_skill_run_audit_log_path()),
        "budget": _skill_budget_policy_snapshot(),
        "recent_run_count": len(recent),
        "run_count_24h": len(recent_24h),
        "non_dry_run_count_24h": sum(1 for event in recent_24h if not bool(event.get("dry_run"))),
    }


def list_skill_run_audit_events(*, limit: int = 80) -> list[dict[str, Any]]:
    path = _skill_run_audit_log_path()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, int(limit)) * 2 :]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    events.sort(key=lambda item: _float_or_default(item.get("at"), 0.0), reverse=True)
    return events[: max(1, int(limit))]


def _skill_execution_budget(skill: SkillSpec, *, dry_run: bool, actor: str) -> dict[str, Any]:
    policy = _skill_budget_policy_snapshot()
    now = time.time()
    recent_24h = [event for event in list_skill_run_audit_events(limit=max(policy["max_daily_runs"] + 100, 300)) if now - _float_or_default(event.get("at"), 0.0) <= 86400]
    step_count = len(skill.steps)
    run_count_24h = len(recent_24h)
    non_dry_run_count_24h = sum(1 for event in recent_24h if not bool(event.get("dry_run")))
    reasons: list[dict[str, Any]] = []
    if step_count > policy["max_steps"]:
        reasons.append(
            {
                "error_code": "skill_budget_steps_exceeded",
                "message": f"Skill step count {step_count} exceeds budget {policy['max_steps']}",
            }
        )
    if run_count_24h >= policy["max_daily_runs"]:
        reasons.append(
            {
                "error_code": "skill_budget_daily_runs_exceeded",
                "message": f"Skill daily run budget exhausted: {run_count_24h}/{policy['max_daily_runs']}",
            }
        )
    if not dry_run and non_dry_run_count_24h >= policy["max_daily_non_dry_runs"]:
        reasons.append(
            {
                "error_code": "skill_budget_daily_execute_exceeded",
                "message": f"Skill daily execute budget exhausted: {non_dry_run_count_24h}/{policy['max_daily_non_dry_runs']}",
            }
        )
    return {
        "schema_version": "spiritkin.skill_execution_budget.v1",
        "allowed": not reasons,
        "actor": actor,
        "dry_run": bool(dry_run),
        "skill_name": skill.name,
        "step_count": step_count,
        "run_count_24h": run_count_24h,
        "non_dry_run_count_24h": non_dry_run_count_24h,
        "remaining_daily_runs": max(0, policy["max_daily_runs"] - run_count_24h),
        "remaining_daily_non_dry_runs": max(0, policy["max_daily_non_dry_runs"] - non_dry_run_count_24h),
        "policy": policy,
        "reasons": reasons,
    }


def _skill_budget_policy_snapshot() -> dict[str, Any]:
    return {
        "max_steps": _env_int("SPIRITKIN_SKILL_MAX_STEPS", DEFAULT_SKILL_MAX_STEPS),
        "max_daily_runs": _env_int("SPIRITKIN_SKILL_MAX_DAILY_RUNS", DEFAULT_SKILL_MAX_DAILY_RUNS),
        "max_daily_non_dry_runs": _env_int("SPIRITKIN_SKILL_MAX_DAILY_NON_DRY_RUNS", DEFAULT_SKILL_MAX_DAILY_NON_DRY_RUNS),
        "env": {
            "max_steps": "SPIRITKIN_SKILL_MAX_STEPS",
            "max_daily_runs": "SPIRITKIN_SKILL_MAX_DAILY_RUNS",
            "max_daily_non_dry_runs": "SPIRITKIN_SKILL_MAX_DAILY_NON_DRY_RUNS",
            "audit_log_path": "SPIRITKIN_SKILL_RUN_AUDIT_LOG",
        },
    }


def _append_skill_run_audit(
    *,
    skill: SkillSpec,
    result: SkillRunResult,
    dry_run: bool,
    started_at: float,
    actor: str,
    budget: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    now = time.time()
    entry = {
        "schema_version": "spiritkin.skill_run_audit.v1",
        "at": now,
        "actor": actor,
        "skill_name": skill.name,
        "owner_agent_id": str(skill.metadata.get("owner_agent_id") or ""),
        "status": str(skill.metadata.get("status") or "draft"),
        "risk_level": skill.risk_level,
        "dry_run": bool(dry_run),
        "success": bool(result.success),
        "message": result.message[:500],
        "error_code": str(result.metadata.get("error_code") or "") if isinstance(result.metadata, dict) else "",
        "duration_ms": round((now - started_at) * 1000, 2),
        "step_count": len(result.step_results) if result.step_results else len(skill.steps),
        "input_keys": sorted(str(key) for key in inputs.keys() if key != "actor"),
        "budget": budget,
    }
    path = _skill_run_audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def _run_skill_from_payload(payload: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    name = str(payload.get("name") or payload.get("skill_name") or "").strip()
    if not name:
        raise ValueError("skill name is required")
    store = _store()
    skill = store.load(name)
    if skill is None:
        raise ValueError(f"unknown skill: {name}")
    inputs = payload.get("inputs")
    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"inputs must be valid JSON: {exc}") from exc
    if not isinstance(inputs, dict):
        inputs = {}
    actor = str(payload.get("reviewer") or payload.get("actor") or inputs.get("actor") or "desktop")
    inputs.setdefault("actor", actor)
    started_at = time.time()
    budget = _skill_execution_budget(skill, dry_run=dry_run, actor=actor)
    if budget.get("allowed"):
        registry = SkillRegistry([skill])
        runner = SkillRunner(registry, build_default_tool_registry())
        result = runner.run(name, inputs, dry_run=dry_run)
    else:
        first_reason = next(iter(budget.get("reasons") or []), {})
        result = SkillRunResult(
            False,
            str(first_reason.get("message") or "Skill execution budget blocked."),
            skill.name,
            metadata={"error_code": str(first_reason.get("error_code") or "skill_budget_blocked"), "budget": budget},
        )
    updated = _record_skill_run_metrics(
        skill,
        result,
        dry_run=dry_run,
        started_at=started_at,
        actor=actor,
    )
    store.save(updated)
    audit_event = _append_skill_run_audit(skill=skill, result=result, dry_run=dry_run, started_at=started_at, actor=actor, budget=budget, inputs=inputs)
    return {
        "ok": result.success,
        "skill_run": _skill_run_snapshot(result),
        "budget": budget,
        "audit_event": audit_event,
        "skill": _spec_to_snapshot(updated),
        "skills": build_desktop_skills_snapshot(),
    }


def resolve_skill_run_audit_log_path() -> Path:
    value = os.getenv("SPIRITKIN_SKILL_RUN_AUDIT_LOG", DEFAULT_SKILL_RUN_AUDIT_LOG)
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _skill_run_audit_log_path() -> Path:
    return resolve_skill_run_audit_log_path()


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def handle_desktop_skills_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "save").strip().lower()
    store = _store()
    if action in {
        "source_snapshot",
        "list_sources",
        "skill_sources",
        "register_source",
        "add_source",
        "source_register",
        "delete_source",
        "remove_source",
        "source_delete",
        "sync_source",
        "source_sync",
        "scan_source",
        "source_scan",
        "sync_declarative_config",
        "load_declarative_config",
        "discover_github",
        "search_github_sources",
        "save_source_policy",
        "source_policy",
        "update_source_policy",
        "sync_openclaw",
        "sync_openclaw_source",
        "openclaw_sync",
        "discover_openclaw",
    }:
        result = handle_skill_source_action(payload)
        if "skills" not in result:
            result["skills"] = build_desktop_skills_snapshot()
        return result
    if action in {"import_source_candidates", "source_import_candidates"}:
        candidates = build_candidate_payloads_from_source(payload)
        imported: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        allow_updates = payload_bool(payload.get("allow_updates"), False)
        for candidate in candidates:
            name = str(candidate.get("name") or candidate.get("skill_name") or "").strip()
            if not name:
                skipped.append({"reason": "missing_name", "candidate": candidate})
                continue
            source_review = candidate.get("metadata", {}).get("source_review") if isinstance(candidate.get("metadata"), dict) else {}
            manifest = source_review.get("manifest") if isinstance(source_review, dict) else {}
            manifest_errors = manifest.get("errors") if isinstance(manifest, dict) else []
            if manifest_errors:
                skipped.append({"name": name, "reason": "manifest_invalid", "errors": list(manifest_errors), "candidate": candidate})
                continue
            existing = store.load(name)
            if existing is not None and not allow_updates:
                skipped.append({"name": name, "reason": "already_exists"})
                continue
            candidate["status"] = "candidate"
            candidate["promotion_status"] = "candidate"
            spec = _spec_from_payload(candidate, existing)
            store.save(spec)
            imported.append(_spec_to_snapshot(spec))
        source = get_skill_source(str(payload.get("source_id") or "")) or {}
        lock = update_skill_source_lock(source=source, imported_skills=imported) if imported else build_skill_sources_snapshot().get("lock", {})
        return {
            "ok": True,
            "imported_count": len(imported),
            "skipped_count": len(skipped),
            "imported_skills": imported,
            "skipped": skipped,
            "skill_source_lock": lock,
            "skills": build_desktop_skills_snapshot(),
        }
    if action in {"dry_run", "run_dry", "dry_run_skill", "run_skill_dry"}:
        return _run_skill_from_payload(payload, dry_run=True)
    if action in {"run", "run_skill", "execute", "execute_skill"}:
        return _run_skill_from_payload(payload, dry_run=payload_bool(payload.get("dry_run"), False))
    if action == "save":
        existing = store.load(str(payload.get("name") or payload.get("skill_name") or ""))
        spec = _spec_from_payload(payload.get("skill") if isinstance(payload.get("skill"), dict) else payload, existing)
        store.save(spec)
        return {"ok": True, "skill": _spec_to_snapshot(spec), "skills": build_desktop_skills_snapshot()}
    if action == "enforce_ownership":
        updated: list[dict[str, Any]] = []
        for skill in store.list_all():
            spec = _spec_from_payload(
                {
                    "name": skill.name,
                    "metadata": dict(skill.metadata),
                    "status": str(skill.metadata.get("status") or "draft"),
                    "allow_owner_reassign": False,
                },
                skill,
            )
            if spec != skill:
                store.save(spec)
                updated.append(_spec_to_snapshot(spec))
        return {"ok": True, "updated_count": len(updated), "updated_skills": updated, "skills": build_desktop_skills_snapshot()}
    if action == "delete":
        name = str(payload.get("name") or payload.get("skill_name") or "").strip()
        if not name:
            raise ValueError("skill name is required")
        removed = store.delete(name)
        return {"ok": removed, "deleted": name, "skills": build_desktop_skills_snapshot()}
    if action == "review_candidates":
        registry = SkillRegistry()
        registry.load_from_store(store)
        outcomes = review_skill_candidates(registry, PromotionRuleSet(require_human_review=True), store=store, reviewer=str(payload.get("reviewer") or "desktop"))
        return {
            "ok": True,
            "outcomes": [
                {
                    "candidate_name": item.candidate_name,
                    "decision": item.decision,
                    "changed": item.changed,
                    "reason": item.review.reason,
                    "skill": _spec_to_snapshot(item.skill),
                }
                for item in outcomes
            ],
            "skills": build_desktop_skills_snapshot(),
        }
    if action == "bind_ui":
        name = str(payload.get("name") or payload.get("skill_name") or "").strip()
        skill = store.load(name)
        if skill is None:
            raise ValueError(f"unknown skill: {name}")
        bindings = payload.get("ui_bindings")
        if isinstance(bindings, str):
            try:
                bindings = json.loads(bindings or "[]")
            except json.JSONDecodeError:
                bindings = []
        if not isinstance(bindings, list) or not bindings:
            raise ValueError("ui_bindings must be a non-empty list")
        normalized_bindings = []
        for index, item in enumerate(bindings, start=1):
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("action") or item.get("type") or "").strip()
            selector = str(item.get("selector") or item.get("target") or item.get("element") or "").strip()
            coordinate = item.get("coordinate") if isinstance(item.get("coordinate"), dict) else {}
            if not selector and not coordinate:
                continue
            normalized_bindings.append(
                {
                    "index": int(item.get("index") or index),
                    "action": action_type or "ui_action",
                    "selector": selector,
                    "coordinate": coordinate,
                    "notes": str(item.get("notes") or ""),
                }
            )
        if not normalized_bindings:
            raise ValueError("ui_bindings must include selectors or coordinates")
        metadata = dict(skill.metadata)
        metadata["ui_binding_status"] = "bound"
        metadata["ui_bindings"] = normalized_bindings
        metadata["ui_bound_at"] = time.time()
        metadata["ui_bound_by"] = str(payload.get("reviewer") or payload.get("bound_by") or "desktop")
        updated = SkillSpec(
            name=skill.name,
            description=skill.description,
            trigger_intents=skill.trigger_intents,
            input_schema=skill.input_schema,
            preconditions=skill.preconditions,
            steps=skill.steps,
            tool_allowlist=skill.tool_allowlist,
            risk_level=skill.risk_level,
            confirmation_policy=skill.confirmation_policy,
            rollback_strategy=skill.rollback_strategy,
            success_criteria=skill.success_criteria,
            memory_policy=skill.memory_policy,
            eval_cases=skill.eval_cases,
            version=skill.version,
            usage_count=skill.usage_count,
            metadata=metadata,
        )
        store.save(updated)
        return {"ok": True, "skill": _spec_to_snapshot(updated), "skills": build_desktop_skills_snapshot()}
    if action in {"promote", "reject", "archive", "demote"}:
        name = str(payload.get("name") or payload.get("skill_name") or "").strip()
        skill = store.load(name)
        if skill is None:
            raise ValueError(f"unknown skill: {name}")
        decision = "promote" if action == "promote" else action
        gate = None
        jury_gate = None
        if decision == "promote":
            if str(skill.metadata.get("ui_binding_status") or "").lower() == "required":
                return {
                    "ok": False,
                    "error": "ui_binding_required",
                    "detail": "Video-to-Skill candidates must bind UI selectors or coordinates before promotion.",
                    "skills": build_desktop_skills_snapshot(),
                }
            gate = evaluate_review_gate(payload, "skill.promote", subject=name)
            if not gate.allowed:
                return {"ok": False, "error": "review_required", "review_gate": gate.snapshot(), "skills": build_desktop_skills_snapshot()}
            jury_required = _skill_jury_required(skill, payload)
            jury_gate = evaluate_jury_gate(
                payload,
                "skill.promote.jury",
                subject=name,
                default_required=jury_required,
                default_review_type=_skill_jury_review_type(skill),
            )
            if not jury_gate.allowed:
                return {
                    "ok": False,
                    "error": "jury_review_required",
                    "jury_gate": jury_gate.snapshot(),
                    "review_gate": gate.snapshot(),
                    "skills": build_desktop_skills_snapshot(),
                }
        review = CandidateReview(candidate_name=name, reviewer=str(payload.get("reviewer") or "desktop"), decision=decision, reason=str(payload.get("reason") or f"desktop {decision}"))
        updated = apply_candidate_review(skill, review)
        if gate is not None or jury_gate is not None:
            metadata = dict(updated.metadata)
            if gate is not None:
                metadata["core_review_gate"] = gate.snapshot()
            if jury_gate is not None:
                metadata["jury_gate"] = jury_gate.snapshot()
                metadata["jury_report_id"] = jury_gate.report_id
            updated = SkillSpec(
                name=updated.name,
                description=updated.description,
                trigger_intents=updated.trigger_intents,
                input_schema=updated.input_schema,
                preconditions=updated.preconditions,
                steps=updated.steps,
                tool_allowlist=updated.tool_allowlist,
                risk_level=updated.risk_level,
                confirmation_policy=updated.confirmation_policy,
                rollback_strategy=updated.rollback_strategy,
                success_criteria=updated.success_criteria,
                memory_policy=updated.memory_policy,
                eval_cases=updated.eval_cases,
                version=updated.version,
                usage_count=updated.usage_count,
                metadata=metadata,
            )
        store.save(updated)
        return {"ok": True, "skill": _spec_to_snapshot(updated), "skills": build_desktop_skills_snapshot()}
    if action == "export":
        names = [str(item).strip() for item in payload.get("skill_names") or [] if str(item).strip()]
        if not names and payload.get("name"):
            names = [str(payload.get("name"))]
        specs = [store.load(name) for name in names]
        snapshots = [_spec_to_snapshot(spec) for spec in specs if spec is not None]
        export_dir = Path(os.getenv("SPIRITKIN_SKILL_EXPORT_DIR", DEFAULT_SKILL_EXPORT_DIR))
        if not export_dir.is_absolute():
            export_dir = Path.cwd() / export_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        export_id = str(payload.get("export_id") or f"skills-{int(time.time())}")
        safe_id = "".join(ch for ch in export_id if ch.isalnum() or ch in {"-", "_"}) or f"skills-{int(time.time())}"
        path = export_dir / f"{safe_id}.json"
        package = {"export_id": safe_id, "created_at": time.time(), "skills": snapshots}
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "package_path": str(path), "package": package, "skills": build_desktop_skills_snapshot()}
    raise ValueError(f"unsupported skills action: {action}")
