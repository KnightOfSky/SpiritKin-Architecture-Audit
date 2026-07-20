from __future__ import annotations

import hashlib
import json
import math
import os
import time
from typing import Any

from backend.state_store import locked_state_path, resolve_state_path

SCHEMA_VERSION = "spiritkin.benchmark_runtime.v1"
DEFAULT_BENCHMARK_LOG = "state/evaluation/benchmarks.jsonl"
TARGET_TYPES = {
    "capability",
    "model",
    "agent",
    "workflow",
    "skill",
    "worker",
    "vision",
    "runtime",
    "end_to_end",
    "tool",
    "code",
    "training",
    "prompt",
}
MAX_TEXT = 160
MIN_SUCCESS_RATE = 0.80
MIN_QUALITY_SCORE = 70.0


def _now() -> float:
    return time.time()


def _bounded_text(value: Any, field: str, *, minimum: int = 1) -> str:
    text = str(value or "").strip()
    if len(text) < minimum or len(text) > MAX_TEXT or "\x00" in text:
        raise ValueError(f"{field} must contain {minimum}-{MAX_TEXT} safe characters")
    return text


def _number(value: Any, field: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    if number < minimum or (maximum is not None and number > maximum):
        ceiling = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(f"{field} must be >= {minimum}{ceiling}")
    return round(number, 4)


def _count(value: Any, field: str) -> int:
    number = _number(value, field, minimum=0)
    if not number.is_integer():
        raise ValueError(f"{field} must be an integer")
    return int(number)


def _metrics(value: Any, field: str) -> dict[str, float | int]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} metrics are required")
    success_rate = _number(value.get("success_rate"), f"{field}.success_rate", maximum=1.0)
    quality_score = _number(value.get("quality_score"), f"{field}.quality_score", maximum=100.0)
    # Overall score is server-derived so clients cannot weaken the comparison policy.
    overall_score = round((success_rate * 60.0) + (quality_score * 0.4), 4)
    return {
        "success_rate": success_rate,
        "latency_ms": _number(value.get("latency_ms"), f"{field}.latency_ms"),
        "cost": _number(value.get("cost"), f"{field}.cost"),
        "retry_count": _count(value.get("retry_count"), f"{field}.retry_count"),
        "review_count": _count(value.get("review_count"), f"{field}.review_count"),
        "quality_score": quality_score,
        "overall_score": overall_score,
    }


def _delta(after: dict[str, float | int], before: dict[str, float | int]) -> dict[str, float | int]:
    return {
        key: round(float(after[key]) - float(before[key]), 4)
        for key in after
    }


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _jury(value: Any, *, required: bool) -> dict[str, Any]:
    if not required:
        if value not in (None, [], {}):
            raise ValueError("model_jury is supported only for model benchmarks")
        return {"required": False, "status": "not_required", "verdict_count": 0, "verdicts": []}
    if value not in (None, [], {}):
        raise PermissionError("client-supplied model jury verdicts are not trusted; a governed Model Jury report is required")
    return {"required": True, "status": "waiting_jury", "verdict_count": 0, "verdicts": []}


class BenchmarkRuntime:
    """Append-only measured benchmark comparisons and promotion decisions."""

    def __init__(self, log_path: str | os.PathLike[str] | None = None) -> None:
        self.log_path = resolve_state_path(
            "SPIRITKIN_BENCHMARK_LOG",
            DEFAULT_BENCHMARK_LOG,
            log_path,
        )

    def _rows(self) -> list[dict[str, Any]]:
        with locked_state_path(self.log_path):
            if not self.log_path.exists():
                return []
            rows: list[dict[str, Any]] = []
            try:
                for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict) and value.get("schema_version") == SCHEMA_VERSION:
                        rows.append(value)
            except OSError:
                return []
            return rows

    def _append(self, report: dict[str, Any]) -> None:
        with locked_state_path(self.log_path):
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")

    def record_comparison(
        self,
        payload: dict[str, Any],
        *,
        candidate_id: str = "",
        workspace_id: str = "",
        recorded_by: str,
    ) -> dict[str, Any]:
        target = _bounded_text(payload.get("target") or candidate_id, "target", minimum=2)
        target_type = str(payload.get("target_type") or "").strip().lower()
        if target_type not in TARGET_TYPES:
            raise ValueError(f"unsupported benchmark target_type: {target_type or 'missing'}")
        version = _bounded_text(payload.get("version"), "version")
        baseline_version = _bounded_text(payload.get("baseline_version"), "baseline_version")
        dataset = _bounded_text(payload.get("dataset"), "dataset", minimum=2)
        measurement_source = _bounded_text(payload.get("measurement_source"), "measurement_source", minimum=2)
        recorded_by = _bounded_text(recorded_by, "recorded_by", minimum=2)
        before = _metrics(payload.get("before"), "before")
        after = _metrics(payload.get("after"), "after")
        jury = _jury(payload.get("model_jury"), required=target_type == "model")

        reasons: list[str] = []
        if after["success_rate"] < MIN_SUCCESS_RATE:
            reasons.append("success_rate_below_minimum")
        if after["quality_score"] < MIN_QUALITY_SCORE:
            reasons.append("quality_score_below_minimum")
        if after["success_rate"] < before["success_rate"]:
            reasons.append("success_rate_regressed")
        if after["quality_score"] < before["quality_score"]:
            reasons.append("quality_score_regressed")
        if after["overall_score"] <= before["overall_score"]:
            reasons.append("overall_score_not_improved")
        if target_type == "model" and jury["status"] != "approved":
            reasons.append("model_jury_not_approved")
        gate_status = "passed" if not reasons else (
            "waiting_jury" if reasons == ["model_jury_not_approved"] and jury["status"] == "waiting_jury" else "failed"
        )
        created_at = _now()
        identity = {
            "target": target,
            "target_type": target_type,
            "version": version,
            "dataset": dataset,
            "candidate_id": candidate_id,
            "workspace_id": workspace_id,
            "created_at_ns": time.time_ns(),
        }
        report = {
            "schema_version": SCHEMA_VERSION,
            "benchmark_id": f"benchmark-{_digest(identity)}",
            "target": target,
            "target_type": target_type,
            "version": version,
            "baseline_version": baseline_version,
            "dataset": dataset,
            "measurement_source": measurement_source,
            "candidate_id": candidate_id,
            "workspace_id": workspace_id,
            "before": before,
            "after": after,
            "delta": _delta(after, before),
            **after,
            "promotion_gate": {
                "status": gate_status,
                "passed": gate_status == "passed",
                "reasons": reasons,
                "minimum_success_rate": MIN_SUCCESS_RATE,
                "minimum_quality_score": MIN_QUALITY_SCORE,
                "requires_strict_overall_improvement": True,
            },
            "model_jury": jury,
            "recorded_by": recorded_by,
            "created_at": created_at,
            "policy": {
                "measured_evidence_only": True,
                "candidate_stage_advanced": False,
                "activation_enabled": False,
            },
        }
        self._append(report)
        return report

    def attach_model_jury(self, benchmark: dict[str, Any], jury_report: dict[str, Any]) -> dict[str, Any]:
        if str(benchmark.get("target_type") or "") != "model":
            raise ValueError("Model Jury can be attached only to model benchmarks")
        benchmark_id = str(benchmark.get("benchmark_id") or "")
        if not benchmark_id or str(jury_report.get("benchmark_id") or "") != benchmark_id:
            raise PermissionError("Model Jury report belongs to another Benchmark")
        reasons = [
            str(item)
            for item in (benchmark.get("promotion_gate") or {}).get("reasons") or []
            if str(item) != "model_jury_not_approved"
        ]
        if jury_report.get("approved") is not True:
            reasons.append("model_jury_not_approved")
        status = "passed" if not reasons else (
            "waiting_jury"
            if reasons == ["model_jury_not_approved"] and str(jury_report.get("status") or "") == "insufficient_evidence"
            else "failed"
        )
        updated = {
            **benchmark,
            "model_jury": {
                "required": True,
                "status": str(jury_report.get("status") or "insufficient_evidence"),
                "jury_report_id": str(jury_report.get("jury_report_id") or ""),
                "verdict_count": int(jury_report.get("structured_review_count") or 0),
                "verdicts": list(jury_report.get("verdicts") or []),
            },
            "promotion_gate": {
                **dict(benchmark.get("promotion_gate") or {}),
                "status": status,
                "passed": status == "passed",
                "reasons": reasons,
            },
            "updated_at": _now(),
        }
        self._append(updated)
        return updated

    @staticmethod
    def summary(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "benchmark_id": str(report.get("benchmark_id") or ""),
            "target": str(report.get("target") or ""),
            "target_type": str(report.get("target_type") or ""),
            "version": str(report.get("version") or ""),
            "baseline_version": str(report.get("baseline_version") or ""),
            "dataset": str(report.get("dataset") or ""),
            "success_rate": float(report.get("success_rate") or 0),
            "latency_ms": float(report.get("latency_ms") or 0),
            "cost": float(report.get("cost") or 0),
            "retry_count": int(report.get("retry_count") or 0),
            "review_count": int(report.get("review_count") or 0),
            "quality_score": float(report.get("quality_score") or 0),
            "overall_score": float(report.get("overall_score") or 0),
            "overall_delta": float((report.get("delta") or {}).get("overall_score") or 0),
            "promotion_status": str((report.get("promotion_gate") or {}).get("status") or "failed"),
            "promotion_passed": bool((report.get("promotion_gate") or {}).get("passed")),
            "jury_status": str((report.get("model_jury") or {}).get("status") or "not_required"),
            "created_at": float(report.get("created_at") or 0),
            "activation_enabled": False,
        }

    def latest_for_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for row in reversed(self._rows()):
            if str(row.get("candidate_id") or "") == candidate_id:
                return row
        return None

    def snapshot(
        self,
        *,
        candidate_ids: list[str] | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        allowed = None if candidate_ids is None else set(candidate_ids)
        filtered = [
            row
            for row in self._rows()
            if (allowed is None or str(row.get("candidate_id") or "") in allowed)
            and (not workspace_id or str(row.get("workspace_id") or "") == workspace_id)
        ]
        latest_by_id: dict[str, dict[str, Any]] = {}
        for row in filtered:
            benchmark_id = str(row.get("benchmark_id") or "")
            if benchmark_id:
                latest_by_id[benchmark_id] = row
        rows = list(latest_by_id.values())
        passed = sum(bool((row.get("promotion_gate") or {}).get("passed")) for row in rows)
        return {
            "schema_version": SCHEMA_VERSION,
            "count": len(rows),
            "passed_count": passed,
            "blocked_count": len(rows) - passed,
            "recent": [self.summary(row) for row in rows[-20:]],
            "everything_is_measured": True,
        }
