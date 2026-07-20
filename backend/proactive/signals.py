from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

PROACTIVE_SIGNAL_KINDS = frozenset(
    {
        "calendar_due",
        "device_anomaly",
        "idle",
        "relationship_change",
        "task_completed",
        "task_context",
        "task_failed",
    }
)


@dataclass(frozen=True)
class ProactiveSignal:
    kind: str
    summary: str
    source: str
    value_score: float
    signal_id: str = field(default_factory=lambda: f"signal-{uuid.uuid4().hex}")
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    essential: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_kind = str(self.kind or "").strip().lower()
        if normalized_kind not in PROACTIVE_SIGNAL_KINDS:
            raise ValueError(f"unsupported proactive signal kind: {self.kind}")
        summary = " ".join(str(self.summary or "").split())[:300]
        source = " ".join(str(self.source or "").split())[:80]
        if not summary or not source:
            raise ValueError("proactive signal requires source and summary")
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "value_score", max(0.0, min(1.0, float(self.value_score))))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def snapshot(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "kind": self.kind,
            "summary": self.summary,
            "source": self.source,
            "value_score": self.value_score,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "essential": self.essential,
            "metadata": dict(self.metadata),
        }


def signal_from_presence(snapshot: dict[str, Any], *, now: float | None = None) -> ProactiveSignal | None:
    current = time.time() if now is None else float(now)
    suggestion = " ".join(str(snapshot.get("proactive_suggestion") or "").split())
    if not suggestion:
        return None

    contexts = snapshot.get("active_contexts") if isinstance(snapshot.get("active_contexts"), dict) else {}
    task = contexts.get("task") or contexts.get("project")
    if isinstance(task, dict) and str(task.get("summary") or "").strip():
        confidence = max(0.0, min(1.0, float(task.get("confidence") or 0.0)))
        return ProactiveSignal(
            kind="task_context",
            summary=str(task.get("summary")),
            source="presence",
            value_score=max(0.65, confidence * 0.8),
            created_at=current,
            expires_at=current + 20 * 60,
            metadata={"suggestion_text": suggestion, "context_kind": str(task.get("kind") or "task")},
        )

    idle_seconds = max(0.0, float(snapshot.get("idle_seconds") or 0.0))
    value_score = 0.68 if idle_seconds >= 15 * 60 else 0.6
    return ProactiveSignal(
        kind="idle",
        summary=suggestion,
        source="presence",
        value_score=value_score,
        created_at=current,
        expires_at=current + 10 * 60,
        metadata={"suggestion_text": suggestion, "idle_seconds": idle_seconds},
    )
