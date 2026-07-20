from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.orchestrator.context_mirror import (
    CONTEXT_WRITE_INTENT_SCHEMA_VERSION,
    build_context_write_intent_preview,
)

DEFAULT_CONTEXT_WRITE_INTENT_PATH = "state/context_write_intents.jsonl"


@dataclass(frozen=True)
class ContextWriteIntentRecord:
    intent_id: str
    context_id: str
    target_path: str
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "desktop"
    requires_review: bool = True
    status: str = "submitted"
    reason: str = ""
    preview: dict[str, Any] = field(default_factory=dict)
    reviewer: str = ""
    review_note: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_WRITE_INTENT_SCHEMA_VERSION,
            "intent_id": self.intent_id,
            "context_id": self.context_id,
            "target_path": self.target_path,
            "operation": self.operation,
            "payload": dict(self.payload),
            "actor": self.actor,
            "requires_review": self.requires_review,
            "status": self.status,
            "reason": self.reason,
            "preview": dict(self.preview),
            "reviewer": self.reviewer,
            "review_note": self.review_note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def resolve_context_write_intent_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_CONTEXT_WRITE_INTENTS", DEFAULT_CONTEXT_WRITE_INTENT_PATH)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def submit_context_write_intent(
    payload: dict[str, Any],
    *,
    path: str | os.PathLike[str] | None = None,
) -> ContextWriteIntentRecord:
    preview = build_context_write_intent_preview(payload)
    if preview.get("status") != "preview":
        status = "rejected"
        reason = str(preview.get("reason") or preview.get("write_blocked_reason") or "invalid_write_intent")
    else:
        status = "submitted"
        reason = "awaiting_review" if preview.get("requires_review") else "ready_for_reviewed_commit"
    record = _record_from_snapshot(
        {
            **preview,
            "intent_id": f"ctxwi-{uuid4().hex[:12]}",
            "status": status,
            "reason": reason,
            "preview": preview,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    )
    _append_context_write_intent_record(record, path=path)
    return record


def approve_context_write_intent(
    intent_id: str,
    *,
    reviewer: str = "human",
    review_note: str = "",
    path: str | os.PathLike[str] | None = None,
) -> ContextWriteIntentRecord | None:
    return _transition_context_write_intent(
        intent_id,
        status="approved",
        reason="approved_but_not_applied",
        reviewer=reviewer,
        review_note=review_note,
        path=path,
    )


def reject_context_write_intent(
    intent_id: str,
    *,
    reviewer: str = "human",
    review_note: str = "",
    path: str | os.PathLike[str] | None = None,
) -> ContextWriteIntentRecord | None:
    return _transition_context_write_intent(
        intent_id,
        status="rejected",
        reason="rejected_by_reviewer",
        reviewer=reviewer,
        review_note=review_note,
        path=path,
    )


def mark_context_write_intent_applied(
    intent_id: str,
    *,
    actor: str = "context_write_applier",
    review_note: str = "",
    path: str | os.PathLike[str] | None = None,
) -> ContextWriteIntentRecord | None:
    return _transition_context_write_intent(
        intent_id,
        status="applied",
        reason="applied_by_context_write_applier",
        reviewer=actor,
        review_note=review_note,
        path=path,
    )


def list_context_write_intents(
    *,
    path: str | os.PathLike[str] | None = None,
    include_terminal: bool = True,
    limit: int = 100,
) -> list[ContextWriteIntentRecord]:
    latest: dict[str, ContextWriteIntentRecord] = {}
    for record in _read_context_write_intent_records(path=path, limit=max(int(limit) * 4, 100)):
        latest[record.intent_id] = record
    records = list(latest.values())
    if not include_terminal:
        records = [record for record in records if record.status not in {"approved", "rejected", "applied"}]
    return sorted(records, key=lambda item: item.updated_at, reverse=True)[: max(1, int(limit))]


def context_write_intent_snapshot(
    *,
    path: str | os.PathLike[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    intents = list_context_write_intents(path=path, limit=limit)
    counts: dict[str, int] = {}
    for intent in intents:
        counts[intent.status] = counts.get(intent.status, 0) + 1
    return {
        "schema_version": CONTEXT_WRITE_INTENT_SCHEMA_VERSION,
        "path": str(resolve_context_write_intent_path(path)),
        "total": len(intents),
        "status_counts": dict(sorted(counts.items())),
        "intents": [intent.snapshot() for intent in intents],
    }


def _transition_context_write_intent(
    intent_id: str,
    *,
    status: str,
    reason: str,
    reviewer: str,
    review_note: str,
    path: str | os.PathLike[str] | None,
) -> ContextWriteIntentRecord | None:
    current = next((record for record in list_context_write_intents(path=path, limit=500) if record.intent_id == intent_id), None)
    if current is None:
        return None
    if current.status in {"rejected", "applied"}:
        return current
    record = _record_from_snapshot(
        {
            **current.snapshot(),
            "status": status,
            "reason": reason,
            "reviewer": reviewer,
            "review_note": review_note,
            "updated_at": time.time(),
        }
    )
    _append_context_write_intent_record(record, path=path)
    return record


def _append_context_write_intent_record(record: ContextWriteIntentRecord, *, path: str | os.PathLike[str] | None = None) -> None:
    target = resolve_context_write_intent_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.snapshot(), ensure_ascii=False) + "\n")


def _read_context_write_intent_records(
    *,
    path: str | os.PathLike[str] | None = None,
    limit: int = 500,
) -> list[ContextWriteIntentRecord]:
    target = resolve_context_write_intent_path(path)
    if not target.exists():
        return []
    records: list[ContextWriteIntentRecord] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(_record_from_snapshot(data))
    return records[-max(1, int(limit)) :]


def _record_from_snapshot(data: dict[str, Any]) -> ContextWriteIntentRecord:
    return ContextWriteIntentRecord(
        intent_id=str(data.get("intent_id") or f"ctxwi-{uuid4().hex[:12]}"),
        context_id=str(data.get("context_id") or "project:current"),
        target_path=str(data.get("target_path") or ""),
        operation=str(data.get("operation") or "set"),
        payload=dict(data.get("payload") or {}),
        actor=str(data.get("actor") or "desktop"),
        requires_review=bool(data.get("requires_review", True)),
        status=str(data.get("status") or "submitted"),
        reason=str(data.get("reason") or ""),
        preview=dict(data.get("preview") or {}),
        reviewer=str(data.get("reviewer") or ""),
        review_note=str(data.get("review_note") or ""),
        created_at=float(data.get("created_at") or time.time()),
        updated_at=float(data.get("updated_at") or data.get("created_at") or time.time()),
    )
