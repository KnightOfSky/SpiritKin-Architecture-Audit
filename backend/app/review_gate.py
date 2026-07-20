from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.state_store import resolve_state_path

DEFAULT_REVIEW_GATE_LOG = "state/evolution/review_gate.jsonl"


@dataclass(frozen=True)
class ReviewGateDecision:
    allowed: bool
    gate_id: str
    reason: str
    reviewer: str = ""
    approved_at: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "gate_id": self.gate_id,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "approved_at": self.approved_at,
            "evidence": dict(self.evidence),
        }


REVIEW_REQUIRED_OPERATIONS = {
    "training.cloud_package",
    "evolution.cloud_training_package",
    "skill.promote",
    "remote.export",
    "remote.push",
    "remote.execute",
}


def payload_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return bool(value)


def evaluate_review_gate(payload: dict[str, Any], operation: str, *, subject: str = "", default_required: bool = True) -> ReviewGateDecision:
    gate = payload.get("review_gate") if isinstance(payload.get("review_gate"), dict) else {}
    approved = any(
        payload_bool(value, False)
        for value in (
            payload.get("core_review_approved"),
            payload.get("review_approved"),
            gate.get("core_review_approved"),
            gate.get("approved"),
        )
    )
    reviewer = str(payload.get("reviewer") or gate.get("reviewer") or "").strip()
    reason = str(payload.get("review_reason") or gate.get("reason") or "").strip()
    required = payload_bool(payload.get("core_review_required"), default_required or operation in REVIEW_REQUIRED_OPERATIONS)
    if not required:
        return _record_decision(ReviewGateDecision(True, operation, "review not required", reviewer=reviewer, approved_at=time.time(), evidence={"subject": subject, "required": False}))
    if approved and reviewer:
        return _record_decision(ReviewGateDecision(True, operation, reason or "core review approved", reviewer=reviewer, approved_at=time.time(), evidence={"subject": subject, "required": True}))
    return _record_decision(
        ReviewGateDecision(
            False,
            operation,
            "core review approval required",
            reviewer=reviewer,
            evidence={"subject": subject, "required": True, "missing": "core_review_approved and reviewer"},
        )
    )


def require_review_gate(payload: dict[str, Any], operation: str, *, subject: str = "", default_required: bool = True) -> ReviewGateDecision:
    decision = evaluate_review_gate(payload, operation, subject=subject, default_required=default_required)
    if not decision.allowed:
        raise PermissionError(decision.reason)
    return decision


def resolve_review_gate_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_REVIEW_GATE_LOG", DEFAULT_REVIEW_GATE_LOG, path)


def _record_decision(decision: ReviewGateDecision) -> ReviewGateDecision:
    path = resolve_review_gate_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"recorded_at": time.time(), **decision.snapshot()}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return decision
