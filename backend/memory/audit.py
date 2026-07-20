from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_ABSOLUTE_TERMS = ("只", "永远", "从不", "一定", "完全", "绝对", "以后都", "不再", "always", "never", "only")
_EVIDENCE_CATEGORIES = {"preference", "habit", "knowledge_fact", "user_feedback"}


@dataclass(frozen=True)
class MemoryAuditFinding:
    code: str
    severity: str
    message: str
    suggestion: str
    entry_id: str = ""
    conflict_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
            "entry_id": self.entry_id,
            "conflict_id": self.conflict_id,
            "details": dict(self.details),
        }


def audit_memory_state(entries: Iterable[Any], conflicts: Iterable[Any]) -> list[MemoryAuditFinding]:
    memory_entries = list(entries)
    memory_conflicts = list(conflicts)
    entry_by_id = {str(entry.entry_id): entry for entry in memory_entries}
    findings: list[MemoryAuditFinding] = []

    for entry in memory_entries:
        metadata = dict(getattr(entry, "metadata", {}) or {})
        evidence = _evidence_quotes(metadata)
        if entry.category in _EVIDENCE_CATEGORIES and not evidence:
            findings.append(
                MemoryAuditFinding(
                    code="missing_evidence",
                    severity="warning",
                    entry_id=entry.entry_id,
                    message=f"长期记忆 {entry.entry_id} 没有可回看的用户证据。",
                    suggestion="人工复核后补充 evidence_quotes；无依据的稳定偏好不要提升为核心画像。",
                )
            )
        overclaims = [term for term in _ABSOLUTE_TERMS if term in entry.content.lower() and not any(term in quote.lower() for quote in evidence)]
        if overclaims and entry.category in _EVIDENCE_CATEGORIES:
            findings.append(
                MemoryAuditFinding(
                    code="absolute_overclaim",
                    severity="warning",
                    entry_id=entry.entry_id,
                    message=f"长期记忆 {entry.entry_id} 含缺少原文依据的绝对化表达。",
                    suggestion="缩窄表述范围，或补充包含该表述的用户原话。",
                    details={"terms": overclaims},
                )
            )
        if metadata.get("resolution_status") == "superseded" and entry.memory_state != "archived":
            findings.append(
                MemoryAuditFinding(
                    code="superseded_entry_recallable",
                    severity="error",
                    entry_id=entry.entry_id,
                    message=f"已被取代的长期记忆 {entry.entry_id} 仍可被召回。",
                    suggestion="将 activation 归零并标记 archived。",
                )
            )

    for conflict in memory_conflicts:
        if conflict.status in {"pending_review", "clarification_needed"}:
            findings.append(
                MemoryAuditFinding(
                    code="unresolved_conflict",
                    severity="warning" if conflict.status == "pending_review" else "info",
                    conflict_id=conflict.conflict_id,
                    message=f"记忆冲突 {conflict.conflict_id} 尚未消解。",
                    suggestion="对照证据后选择新记忆、旧记忆、上下文并存或请求用户澄清。",
                    details={"source_entry_id": conflict.source_entry_id, "target_entry_id": conflict.target_entry_id},
                )
            )
            continue
        if conflict.status != "resolved":
            continue
        expected_entry_id = ""
        expected_link = ""
        if conflict.resolution == "prefer_new":
            expected_entry_id = conflict.target_entry_id
            expected_link = conflict.source_entry_id
        elif conflict.resolution == "prefer_existing":
            expected_entry_id = conflict.source_entry_id
            expected_link = conflict.target_entry_id
        if expected_entry_id:
            entry = entry_by_id.get(expected_entry_id)
            metadata = dict(getattr(entry, "metadata", {}) or {}) if entry is not None else {}
            if entry is None or metadata.get("superseded_by") != expected_link:
                findings.append(
                    MemoryAuditFinding(
                        code="broken_resolution_link",
                        severity="error",
                        entry_id=expected_entry_id,
                        conflict_id=conflict.conflict_id,
                        message=f"记忆冲突 {conflict.conflict_id} 的取代链不完整。",
                        suggestion="恢复 superseded_by 指向，或重新执行人工消解。",
                        details={"expected_superseded_by": expected_link},
                    )
                )
    return findings


def summarize_memory_audit(findings: Iterable[MemoryAuditFinding]) -> dict[str, Any]:
    items = list(findings)
    by_severity = {"info": 0, "warning": 0, "error": 0}
    by_code: dict[str, int] = {}
    for finding in items:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        by_code[finding.code] = by_code.get(finding.code, 0) + 1
    return {
        "schema_version": "spiritkin.memory_audit.v1",
        "total": len(items),
        "by_severity": by_severity,
        "by_code": by_code,
        "findings": [finding.snapshot() for finding in items],
    }


def _evidence_quotes(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("evidence_quotes")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item or "").strip()]
