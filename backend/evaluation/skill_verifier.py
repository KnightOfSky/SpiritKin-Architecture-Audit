from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend.skills.base import SkillRegistry
from backend.tools.registry import ToolRegistry


@dataclass(frozen=True)
class SkillVerificationResult:
    skill_name: str
    passed: bool
    expected_result: str = ""
    actual_result: str = ""
    step_results: tuple[Any, ...] = ()
    errors: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    verification_timestamp: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "passed": self.passed,
            "expected_result": self.expected_result,
            "actual_result": self.actual_result,
            "step_results": list(self.step_results),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
            "verification_timestamp": self.verification_timestamp,
        }


@dataclass(frozen=True)
class SkillVerificationPolicy:
    min_replayable_rate: float = 1.0
    max_expected_failures: int = 0
    require_audit_correlation: bool = False


def verify_skill_candidate(skill_spec, tool_registry: ToolRegistry, *, dry_run: bool = True) -> SkillVerificationResult:
    errors: list[str] = []
    step_results: list[Any] = []

    for step in skill_spec.steps:
        tool = tool_registry.get(step.tool_name)
        if tool is None:
            errors.append(f"工具未注册: {step.tool_name}")
            continue
        step_results.append({"tool_name": step.tool_name, "arguments": step.arguments, "registered": True})

    tool_names = {s.tool_name for s in skill_spec.steps}
    allowlist = set(skill_spec.tool_allowlist)
    if allowlist and not allowlist.issubset(tool_names.union(allowlist)):
        pass

    passed = len(errors) == 0
    return SkillVerificationResult(
        skill_name=skill_spec.name,
        passed=passed,
        expected_result=f"Skill {skill_spec.name} 步骤全部可解析",
        actual_result="通过" if passed else f"失败: {'; '.join(errors)}",
        step_results=tuple(step_results),
        errors=tuple(errors),
    )


def verify_skill_candidate_readiness(
    skill_spec,
    tool_registry: ToolRegistry,
    *,
    replay_report=None,
    policy: SkillVerificationPolicy | None = None,
) -> SkillVerificationResult:
    policy = policy or SkillVerificationPolicy()
    base = verify_skill_candidate(skill_spec, tool_registry)
    errors = list(base.errors)
    metadata: dict[str, Any] = {"tool_check_passed": base.passed}

    if replay_report is not None:
        tool_names = {step.tool_name for step in skill_spec.steps}
        related = [record for record in getattr(replay_report, "records", []) if getattr(record, "tool_name", "") in tool_names]
        replayable = sum(1 for record in related if getattr(record, "replayable", False))
        expected_failures = sum(1 for record in related if not getattr(record, "expected_success", False))
        audit_correlations = sum(1 for record in related if getattr(record, "correlated_audit_id", ""))
        replayable_rate = replayable / len(related) if related else 0.0
        metadata.update(
            {
                "related_replay_records": len(related),
                "replayable_count": replayable,
                "replayable_rate": replayable_rate,
                "expected_failure_count": expected_failures,
                "audit_correlation_count": audit_correlations,
                "policy": {
                    "min_replayable_rate": policy.min_replayable_rate,
                    "max_expected_failures": policy.max_expected_failures,
                    "require_audit_correlation": policy.require_audit_correlation,
                },
            }
        )
        if not related:
            errors.append("没有匹配该 Skill 的 replay 记录")
        if replayable_rate < policy.min_replayable_rate:
            errors.append(f"replayable_rate 低于阈值: {replayable_rate:.2f} < {policy.min_replayable_rate:.2f}")
        if expected_failures > policy.max_expected_failures:
            errors.append(f"历史失败数超过阈值: {expected_failures} > {policy.max_expected_failures}")
        if policy.require_audit_correlation and audit_correlations == 0:
            errors.append("缺少审计日志关联")

    passed = not errors
    return SkillVerificationResult(
        skill_name=skill_spec.name,
        passed=passed,
        expected_result="候选 Skill 通过工具注册和 replay 阈值验证",
        actual_result="通过" if passed else f"失败: {'; '.join(errors)}",
        step_results=base.step_results,
        errors=tuple(errors),
        metadata=metadata,
    )


def verify_all_candidates(skill_registry: SkillRegistry, tool_registry: ToolRegistry) -> list[SkillVerificationResult]:
    results: list[SkillVerificationResult] = []
    for skill in skill_registry.list_candidates():
        results.append(verify_skill_candidate(skill, tool_registry))
    return results


def verify_all_candidate_readiness(
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
    *,
    replay_report=None,
    policy: SkillVerificationPolicy | None = None,
) -> list[SkillVerificationResult]:
    return [
        verify_skill_candidate_readiness(skill, tool_registry, replay_report=replay_report, policy=policy)
        for skill in skill_registry.list_candidates()
    ]
