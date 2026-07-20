from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TASK_LIFECYCLE = (
    "CREATED",
    "PLANNED",
    "RUNNING",
    "PARTIAL_SUCCESS",
    "WAITING",
    "FAILED",
    "COMPLETED",
    "COMMITTED",
)


@dataclass(frozen=True)
class ExecutionSummary:
    task_id: str
    status: str
    success: bool = False
    artifacts: tuple[dict[str, Any], ...] = ()
    success_criteria: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalizerVerdict:
    task_id: str
    verified: bool
    score: float
    decision: str
    next_status: str
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verified": self.verified,
            "score": self.score,
            "decision": self.decision,
            "next_status": self.next_status,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata or {}),
        }


class ExecutionFinalizer:
    """VERIFY -> SCORE -> COMMIT skeleton for staged runtime migration."""

    def __init__(self, *, commit_threshold: float = 0.8):
        self.commit_threshold = commit_threshold

    def finalize(self, summary: ExecutionSummary) -> FinalizerVerdict:
        status = normalize_task_status(summary.status)
        criteria_checks = dict(summary.metadata.get("success_checks") or {})
        missing = tuple(criteria for criteria in summary.success_criteria if criteria_checks.get(criteria) is not True)
        verified = summary.success and status == "COMPLETED" and not missing
        explicit_score = _float_or_none(summary.metadata.get("score"))
        score = explicit_score if explicit_score is not None else self._default_score(status, verified)

        if verified and score >= self.commit_threshold:
            decision = "commit"
            next_status = "COMMITTED"
        elif status == "WAITING":
            decision = "wait"
            next_status = "WAITING"
        elif status == "PARTIAL_SUCCESS":
            decision = "review"
            next_status = "PARTIAL_SUCCESS"
        else:
            decision = "retry"
            next_status = "FAILED" if status == "FAILED" else status

        reasons: list[str] = []
        if missing:
            reasons.append("missing_success_criteria")
        if not summary.success:
            reasons.append("execution_not_successful")
        if status != "COMPLETED" and decision not in {"wait", "review"}:
            reasons.append("task_not_completed")
        if score < self.commit_threshold and decision == "retry":
            reasons.append("score_below_commit_threshold")

        return FinalizerVerdict(
            task_id=summary.task_id,
            verified=verified,
            score=score,
            decision=decision,
            next_status=next_status,
            reasons=tuple(dict.fromkeys(reasons)),
            metadata={"input_status": status, "artifact_count": len(summary.artifacts)},
        )

    @staticmethod
    def _default_score(status: str, verified: bool) -> float:
        if verified:
            return 1.0
        if status == "PARTIAL_SUCCESS":
            return 0.5
        return 0.0


def normalize_task_status(status: str) -> str:
    normalized = str(status or "").strip().upper()
    return normalized if normalized in TASK_LIFECYCLE else "FAILED"


def _float_or_none(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
