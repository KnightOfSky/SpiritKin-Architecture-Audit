from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuditCorrelation:
    replay_workflow_id: str
    audit_id: str = ""
    match_type: str = "none"
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


def correlate_replay_to_audit(
    replay_report,
    audit_log,
    *,
    time_window_seconds: float = 5.0,
) -> list[AuditCorrelation]:
    correlations: list[AuditCorrelation] = []
    audit_records = audit_log.recent(limit=500) if hasattr(audit_log, "recent") else []
    if not audit_records:
        return correlations

    for replay_record in replay_report.records:
        best_audit: dict[str, Any] | None = None
        best_confidence = 0.0
        best_match_type = "none"

        replay_target = getattr(replay_record.request, "target", "") if replay_record.request else ""
        replay_op = getattr(replay_record.request, "operation", "") if replay_record.request else ""
        source_meta = replay_record.metadata.get("source_metadata", {})
        replay_ts = float(source_meta.get("timestamp", 0)) if isinstance(source_meta, dict) else 0.0

        for audit in audit_records:
            audit_target = str(audit.get("target") or "")
            audit_op = str(audit.get("operation") or "")
            audit_ts = float(audit.get("timestamp") or 0)

            if replay_target == audit_target and replay_op == audit_op:
                if audit_ts and replay_ts:
                    time_diff = abs(audit_ts - replay_ts)
                    if time_diff <= time_window_seconds:
                        confidence = 1.0 - (time_diff / max(time_window_seconds, 1))
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_match_type = "exact"
                            best_audit = audit
                    else:
                        if best_match_type != "exact":
                            best_confidence = 0.5
                            best_match_type = "partial"
                            best_audit = audit
                else:
                    best_confidence = 0.8
                    best_match_type = "exact"
                    best_audit = audit

        correlations.append(
            AuditCorrelation(
                replay_workflow_id=replay_record.workflow_id,
                audit_id=str(best_audit.get("audit_id") or "") if best_audit else "",
                match_type=best_match_type,
                confidence=best_confidence,
                metadata={
                    "replay_target": replay_target,
                    "replay_operation": replay_op,
                    "audit_target": best_audit.get("target") if best_audit else "",
                    "audit_operation": best_audit.get("operation") if best_audit else "",
                },
            )
        )

    return correlations
