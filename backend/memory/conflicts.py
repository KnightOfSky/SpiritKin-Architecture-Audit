from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

_CONTRADICTION_PAIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("喜欢", ("不喜欢", "讨厌", "反感", "厌恶", "不再喜欢")),
    ("爱", ("不爱", "讨厌", "恨")),
    ("想", ("不想", "不愿", "不再想")),
    ("要", ("不要", "不需要", "别要")),
    ("可以", ("不可以", "不行", "不能")),
    ("忙", ("不忙", "有空", "空闲")),
    ("like", ("dislike", "do not like", "don't like", "hate")),
    ("prefer", ("do not prefer", "don't prefer", "no longer prefer")),
    ("want", ("do not want", "don't want", "no longer want")),
)

_CORRECTION_PHRASES = (
    "不是这样",
    "你记错了",
    "记错了",
    "我现在不",
    "已经不",
    "改了",
    "更正",
    "correction",
    "you remembered wrong",
    "no longer",
)

_STOP_TERMS = {
    "用户",
    "助手",
    "一个",
    "一种",
    "这个",
    "那个",
    "自己",
    "因为",
    "所以",
    "但是",
    "现在",
    "已经",
    "喜欢",
    "讨厌",
    "反感",
    "厌恶",
    "不喜欢",
    "想要",
    "不要",
    "可以",
    "不能",
    "prefer",
    "like",
    "dislike",
    "want",
    "user",
    "assistant",
}


@dataclass(frozen=True)
class ConflictCandidate:
    is_candidate: bool
    confidence: float = 0.0
    reason: str = ""
    shared_topics: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryConflict:
    conflict_id: str
    source_entry_id: str
    target_entry_id: str
    status: str = "pending_review"
    reason: str = ""
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)
    resolution: str = ""
    resolution_reason: str = ""
    resolved_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "source_entry_id": self.source_entry_id,
            "target_entry_id": self.target_entry_id,
            "status": self.status,
            "reason": self.reason,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "resolution": self.resolution,
            "resolution_reason": self.resolution_reason,
            "resolved_at": self.resolved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> MemoryConflict:
        return cls(
            conflict_id=str(data.get("conflict_id") or ""),
            source_entry_id=str(data.get("source_entry_id") or ""),
            target_entry_id=str(data.get("target_entry_id") or ""),
            status=str(data.get("status") or "pending_review"),
            reason=str(data.get("reason") or ""),
            confidence=max(0.0, min(1.0, float(data.get("confidence") or 0.0))),
            created_at=float(data.get("created_at") or time.time()),
            resolution=str(data.get("resolution") or ""),
            resolution_reason=str(data.get("resolution_reason") or ""),
            resolved_at=float(data.get("resolved_at") or 0.0),
            metadata=dict(data.get("metadata") or {}),
        )


def find_conflict_candidate(
    new_content: str,
    existing_content: str,
    *,
    new_metadata: dict[str, Any] | None = None,
) -> ConflictCandidate:
    new_text = _normalize(new_content)
    existing_text = _normalize(existing_content)
    shared_topics = tuple(sorted(_topic_terms(new_text) & _topic_terms(existing_text)))
    if not shared_topics:
        return ConflictCandidate(False)

    contradicted_term = ""
    for positive, negatives in _CONTRADICTION_PAIRS:
        new_polarity = _polarity(new_text, positive, negatives)
        existing_polarity = _polarity(existing_text, positive, negatives)
        if new_polarity and existing_polarity and new_polarity != existing_polarity:
            contradicted_term = positive
            break
    if not contradicted_term:
        return ConflictCandidate(False)

    metadata = dict(new_metadata or {})
    confidence = 0.35 + min(0.1, len(shared_topics) * 0.02)
    if str(metadata.get("attribution") or "") == "user_explicit":
        confidence += 0.15
    evidence_text = " ".join(_evidence_quotes(metadata))
    if any(phrase in f"{new_text} {evidence_text}" for phrase in _CORRECTION_PHRASES):
        confidence += 0.2
    return ConflictCandidate(
        True,
        min(0.85, confidence),
        f"shared-topic lexical contradiction: {contradicted_term}",
        shared_topics[:12],
    )


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _polarity(text: str, positive: str, negatives: tuple[str, ...]) -> int:
    has_negative = any(negative in text for negative in negatives)
    without_negatives = text
    for negative in sorted(negatives, key=len, reverse=True):
        without_negatives = without_negatives.replace(negative, " ")
    has_positive = positive in without_negatives
    if has_negative and not has_positive:
        return -1
    if has_positive and not has_negative:
        return 1
    return 0


def _topic_terms(text: str) -> set[str]:
    stripped = text
    for positive, negatives in _CONTRADICTION_PAIRS:
        stripped = stripped.replace(positive, " ")
        for negative in negatives:
            stripped = stripped.replace(negative, " ")
    terms: set[str] = set()
    for raw in re.findall(r"[\u3400-\u9fff]{2,}|[a-z0-9_]{3,}", stripped):
        term = raw.lower()
        if term in _STOP_TERMS:
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", term):
            terms.update(term[index : index + 2] for index in range(max(0, len(term) - 1)))
        else:
            terms.add(term)
    return {term for term in terms if term and term not in _STOP_TERMS}


def _evidence_quotes(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("evidence_quotes")
    if not isinstance(raw, list):
        return []
    return [str(item).strip().lower() for item in raw if str(item or "").strip()]
