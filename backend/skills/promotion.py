from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from backend.skills.base import SkillRegistry, SkillSpec
from backend.skills.persistence import SkillSpecStore


@dataclass(frozen=True)
class PromotionRuleSet:
    min_success_count: int = 3
    min_total_count: int = 5
    min_days_since_last_seen: float = 0.0
    max_failure_rate: float = 0.30
    require_human_review: bool = True


@dataclass(frozen=True)
class CandidateReview:
    candidate_name: str
    reviewer: str = "rules_engine"
    decision: str = "pending"
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    reviewed_at: float = field(default_factory=time.time)
    previous_version: str = ""


@dataclass(frozen=True)
class PromotionOutcome:
    candidate_name: str
    decision: str
    changed: bool
    review: CandidateReview
    skill: SkillSpec


def bump_version(current: str, level: str = "patch") -> str:
    base = current.split("-")[0]
    parts = base.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        major, minor, patch = 0, 1, 0

    if level == "major":
        major += 1
        minor = 0
        patch = 0
    elif level == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1

    return f"{major}.{minor}.{patch}"


def evaluate_candidate(skill: SkillSpec, rules: PromotionRuleSet) -> CandidateReview:
    meta = skill.metadata
    success_count = int(meta.get("success_count") or 0)
    total_count = int(meta.get("total_count") or 0)
    success_rate = float(meta.get("success_rate") or 0.0)
    last_seen = meta.get("last_seen")
    status = meta.get("status", "")

    metrics = {
        "success_count": success_count,
        "total_count": total_count,
        "success_rate": success_rate,
        "last_seen": last_seen,
        "status": status,
    }

    if status != "candidate":
        return CandidateReview(
            candidate_name=skill.name,
            decision="reject",
            reason=f"Skill 状态为 '{status}'，非候选状态，不能升级",
            metrics=metrics,
            previous_version=skill.version,
        )

    if total_count < rules.min_total_count:
        return CandidateReview(
            candidate_name=skill.name,
            decision="pending",
            reason=f"总执行次数不足（{total_count}/{rules.min_total_count}）",
            metrics=metrics,
            previous_version=skill.version,
        )

    if success_count < rules.min_success_count:
        return CandidateReview(
            candidate_name=skill.name,
            decision="pending",
            reason=f"成功次数不足（{success_count}/{rules.min_success_count}）",
            metrics=metrics,
            previous_version=skill.version,
        )

    failure_rate = 1.0 - success_rate
    if failure_rate > rules.max_failure_rate:
        return CandidateReview(
            candidate_name=skill.name,
            decision="demote",
            reason=f"失败率过高（{failure_rate:.1%} > {rules.max_failure_rate:.0%}）",
            metrics=metrics,
            previous_version=skill.version,
        )

    if last_seen is not None:
        days_since = (time.time() - float(last_seen)) / 86400.0
        if days_since > rules.min_days_since_last_seen and rules.min_days_since_last_seen > 0:
            return CandidateReview(
                candidate_name=skill.name,
                decision="archive",
                reason=f"最近执行距今 {days_since:.0f} 天，超过阈值",
                metrics={**metrics, "days_since_last_seen": days_since},
                previous_version=skill.version,
            )

    if rules.require_human_review:
        return CandidateReview(
            candidate_name=skill.name,
            decision="pending",
            reason="自动审核通过，待人工确认升级",
            metrics=metrics,
            previous_version=skill.version,
        )

    return CandidateReview(
        candidate_name=skill.name,
        decision="promote",
        reason="自动审核通过，满足所有升级条件",
        metrics=metrics,
        previous_version=skill.version,
    )


def _review_to_dict(review: CandidateReview) -> dict[str, Any]:
    return {
        "candidate_name": review.candidate_name,
        "reviewer": review.reviewer,
        "decision": review.decision,
        "reason": review.reason,
        "metrics": dict(review.metrics),
        "reviewed_at": review.reviewed_at,
        "previous_version": review.previous_version,
    }


def apply_candidate_review(skill: SkillSpec, review: CandidateReview, *, version_level: str = "minor") -> SkillSpec:
    metadata = dict(skill.metadata)
    history = list(metadata.get("review_history") or [])
    history.append(_review_to_dict(review))
    metadata["review_history"] = history[-20:]
    metadata["last_review"] = _review_to_dict(review)
    metadata["review_status"] = review.decision

    new_version = skill.version
    if review.decision == "promote":
        metadata.update(
            {
                "status": "active",
                "promoted_at": review.reviewed_at,
                "promoted_by": review.reviewer,
                "promotion_reason": review.reason,
                "previous_status": skill.metadata.get("status", "candidate"),
            }
        )
        new_version = bump_version(skill.version, version_level)
    elif review.decision == "demote":
        metadata.update({"status": "demoted", "demoted_at": review.reviewed_at, "demotion_reason": review.reason})
    elif review.decision == "archive":
        metadata.update({"status": "archived", "archived_at": review.reviewed_at, "archive_reason": review.reason})
    elif review.decision == "reject":
        metadata.update({"status": "rejected", "rejected_at": review.reviewed_at, "reject_reason": review.reason})
    else:
        metadata.setdefault("status", skill.metadata.get("status", "candidate"))
        metadata["pending_reason"] = review.reason

    return replace(skill, version=new_version, metadata=metadata)


def review_skill_candidates(
    registry: SkillRegistry,
    rules: PromotionRuleSet | None = None,
    *,
    store: SkillSpecStore | None = None,
    reviewer: str = "rules_engine",
    version_level: str = "minor",
) -> list[PromotionOutcome]:
    rules = rules or PromotionRuleSet()
    outcomes: list[PromotionOutcome] = []
    for skill in registry.list_candidates():
        review = evaluate_candidate(skill, rules)
        review = replace(review, reviewer=reviewer)
        updated = apply_candidate_review(skill, review, version_level=version_level)
        changed = updated != skill
        registry.replace(updated)
        if store is not None:
            store.save(updated)
        outcomes.append(PromotionOutcome(skill.name, review.decision, changed, review, updated))
    return outcomes
