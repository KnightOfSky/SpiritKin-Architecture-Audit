from __future__ import annotations

import hashlib
import json
import time
from typing import Any

SCHEMA_VERSION = "spiritkin.model_jury.v1"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    decoder = json.JSONDecoder()
    candidates = [raw]
    if "```" in raw:
        parts = raw.split("```")
        candidates.extend(part[4:].strip() if part.lstrip().startswith("json") else part.strip() for part in parts)
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def build_model_jury_prompt(benchmark: dict[str, Any]) -> str:
    public_report = {
        key: benchmark.get(key)
        for key in (
            "benchmark_id",
            "target",
            "version",
            "baseline_version",
            "dataset",
            "before",
            "after",
            "delta",
            "promotion_gate",
        )
    }
    return (
        "Review this model replacement benchmark as an independent Model Jury member. "
        "Use only the supplied measured report. Return exactly one JSON object with keys: "
        "benchmark_id, verdict (approve|reject), confidence (0..1), rationale, risks (array). "
        "Reject if the measurements are incomparable, insufficient, regressed, unsafe, or do not prove replacement value.\n"
        f"Benchmark report:\n{json.dumps(public_report, ensure_ascii=False, sort_keys=True)}"
    )


def build_model_jury_report(
    benchmark: dict[str, Any],
    committee_review: dict[str, Any],
    *,
    requested_by: str,
) -> dict[str, Any]:
    benchmark_id = str(benchmark.get("benchmark_id") or "").strip()
    if not benchmark_id or str(benchmark.get("target_type") or "") != "model":
        raise ValueError("a model Benchmark report is required")
    raw_reviews = committee_review.get("reviews") if isinstance(committee_review.get("reviews"), list) else []
    verdicts: list[dict[str, Any]] = []
    reviewers: set[str] = set()
    for raw in raw_reviews[:8]:
        if not isinstance(raw, dict) or raw.get("ok") is not True:
            continue
        provider = str(raw.get("provider") or "").strip().lower()
        model = str(raw.get("model") or "").strip()
        reviewer_id = f"{provider}:{model}".strip(":")
        if not provider or not model or reviewer_id in reviewers:
            continue
        parsed = _extract_json_object(str(raw.get("response_text") or ""))
        if not parsed or str(parsed.get("benchmark_id") or "").strip() != benchmark_id:
            continue
        verdict = str(parsed.get("verdict") or "").strip().lower()
        if verdict not in {"approve", "reject"}:
            continue
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            continue
        rationale = str(parsed.get("rationale") or "").strip()
        if not 0 <= confidence <= 1 or len(rationale) < 2:
            continue
        risks = [str(item).strip()[:160] for item in parsed.get("risks") or [] if str(item).strip()][:12]
        reviewers.add(reviewer_id)
        verdicts.append(
            {
                "reviewer_id": reviewer_id,
                "provider": provider,
                "model": model,
                "verdict": verdict,
                "confidence": round(confidence, 4),
                "rationale": rationale[:1000],
                "risks": risks,
                "structured": True,
            }
        )
    approvals = sum(item["verdict"] == "approve" for item in verdicts)
    rejections = sum(item["verdict"] == "reject" for item in verdicts)
    status = "approved" if len(verdicts) >= 2 and approvals >= 2 and approvals > rejections else (
        "rejected" if rejections >= approvals and verdicts else "insufficient_evidence"
    )
    created_at = time.time()
    identity = json.dumps(
        {"benchmark_id": benchmark_id, "verdicts": verdicts, "created_at_ns": time.time_ns()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "jury_report_id": f"model-jury-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}",
        "benchmark_id": benchmark_id,
        "candidate_id": str(benchmark.get("candidate_id") or ""),
        "workspace_id": str(benchmark.get("workspace_id") or ""),
        "status": status,
        "approved": status == "approved",
        "structured_review_count": len(verdicts),
        "approval_count": approvals,
        "rejection_count": rejections,
        "verdicts": verdicts,
        "requested_by": str(requested_by or "")[:200],
        "created_at": created_at,
        "policy": {
            "minimum_distinct_structured_reviews": 2,
            "minimum_approvals": 2,
            "client_verdicts_trusted": False,
            "candidate_stage_advanced": False,
            "activation_enabled": False,
        },
    }
