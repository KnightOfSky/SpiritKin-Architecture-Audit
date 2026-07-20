from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.collaboration import (
    CollaborationDecision,
    CollaborationMessage,
    CollaborationReview,
    post_collaboration_message,
    record_collaboration_decision,
    record_collaboration_review,
)
from backend.app.context_control import ContextPolicy, save_context_policy
from backend.app.project_overview import ProjectOverviewChange, load_project_overview, propose_project_overview_change
from backend.orchestrator.context_store import ContextPatch, append_context_patch
from backend.orchestrator.context_write_intents import (
    ContextWriteIntentRecord,
    list_context_write_intents,
    mark_context_write_intent_applied,
)

CONTEXT_WRITE_APPLIER_SCHEMA_VERSION = "spiritkin.context_write_applier.v1"
APPLIABLE_CONTEXT_PATHS = {
    "/context/policy",
    "/project/overview/proposal",
    "/collaboration/message",
    "/collaboration/decision",
    "/collaboration/review",
}
APPLIABLE_CONTEXT_OPERATIONS_BY_PATH = {
    "/context/policy": {"set", "merge", "update", "patch"},
    "/project/overview/proposal": {"set", "merge", "update", "patch", "append"},
    "/collaboration/message": {"append"},
    "/collaboration/decision": {"append"},
    "/collaboration/review": {"append"},
}


@dataclass(frozen=True)
class ContextWriteApplyResult:
    ok: bool
    intent_id: str
    status: str
    target_path: str
    operation: str
    message: str = ""
    error: str = ""
    applied: bool = False
    applied_payload: dict[str, Any] = field(default_factory=dict)
    policy: ContextPolicy | None = None
    project_overview_change: ProjectOverviewChange | None = None
    collaboration_message: CollaborationMessage | None = None
    collaboration_decision: CollaborationDecision | None = None
    collaboration_review: CollaborationReview | None = None
    context_patch: ContextPatch | None = None
    intent: ContextWriteIntentRecord | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_WRITE_APPLIER_SCHEMA_VERSION,
            "ok": self.ok,
            "intent_id": self.intent_id,
            "status": self.status,
            "target_path": self.target_path,
            "operation": self.operation,
            "message": self.message,
            "error": self.error,
            "applied": self.applied,
            "applied_payload": dict(self.applied_payload),
            "policy": self.policy.snapshot() if self.policy is not None else {},
            "project_overview_change": self.project_overview_change.snapshot() if self.project_overview_change is not None else {},
            "collaboration_message": self.collaboration_message.snapshot() if self.collaboration_message is not None else {},
            "collaboration_decision": self.collaboration_decision.snapshot() if self.collaboration_decision is not None else {},
            "collaboration_review": self.collaboration_review.snapshot() if self.collaboration_review is not None else {},
            "context_patch": self.context_patch.snapshot() if self.context_patch is not None else {},
            "intent": self.intent.snapshot() if self.intent is not None else {},
        }


def apply_context_write_intent(
    intent_id: str,
    *,
    actor: str = "context_write_applier",
) -> ContextWriteApplyResult:
    intent = next((record for record in list_context_write_intents(limit=500) if record.intent_id == intent_id), None)
    if intent is None:
        return ContextWriteApplyResult(False, intent_id, "missing", "", "", error="context_write_intent_not_found")
    if intent.status != "approved":
        return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error="context_write_intent_not_approved", intent=intent)
    if intent.target_path not in APPLIABLE_CONTEXT_PATHS:
        return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error="target_path_not_applicable", intent=intent)
    allowed_operations = APPLIABLE_CONTEXT_OPERATIONS_BY_PATH.get(intent.target_path, set())
    if intent.operation not in allowed_operations:
        return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error="operation_not_applicable", intent=intent)

    if intent.target_path == "/context/policy":
        return _apply_context_policy_intent(intent, actor=actor)
    if intent.target_path == "/project/overview/proposal":
        return _apply_project_overview_proposal_intent(intent, actor=actor)
    if intent.target_path == "/collaboration/message":
        return _apply_collaboration_message_intent(intent, actor=actor)
    if intent.target_path == "/collaboration/decision":
        return _apply_collaboration_decision_intent(intent, actor=actor)
    if intent.target_path == "/collaboration/review":
        return _apply_collaboration_review_intent(intent, actor=actor)
    return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error="target_path_not_applicable", intent=intent)


def _apply_context_policy_intent(intent: ContextWriteIntentRecord, *, actor: str) -> ContextWriteApplyResult:
    applied_payload = _normalize_context_policy_payload(intent.payload)
    policy = save_context_policy(applied_payload)
    applied_intent = mark_context_write_intent_applied(
        intent.intent_id,
        actor=actor,
        review_note=f"Applied to {intent.target_path}",
    )
    return ContextWriteApplyResult(
        True,
        intent.intent_id,
        "applied",
        intent.target_path,
        intent.operation,
        message="context_policy_updated",
        applied=True,
        applied_payload=applied_payload,
        policy=policy,
        context_patch=_record_context_write_apply_patch(intent, actor=actor, applied_payload=applied_payload, result_type="context_policy"),
        intent=applied_intent or intent,
    )


def _apply_project_overview_proposal_intent(intent: ContextWriteIntentRecord, *, actor: str) -> ContextWriteApplyResult:
    payload = dict(intent.payload or {})
    markdown = str(payload.get("markdown") or "").strip()
    append_markdown = str(payload.get("append_markdown") or "").strip()
    path = payload.get("path")
    if not markdown and append_markdown:
        current = load_project_overview(path).markdown.rstrip()
        markdown = f"{current}\n\n{append_markdown}".strip()
    if not markdown:
        return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error="missing_project_overview_markdown", intent=intent)
    change = propose_project_overview_change(
        markdown,
        author=str(payload.get("author") or actor or intent.actor),
        note=str(payload.get("note") or f"Context write intent {intent.intent_id}"),
        path=path,
    )
    applied_intent = mark_context_write_intent_applied(
        intent.intent_id,
        actor=actor,
        review_note=f"Created project overview proposal {change.change_id}",
    )
    return ContextWriteApplyResult(
        True,
        intent.intent_id,
        "applied",
        intent.target_path,
        intent.operation,
        message="project_overview_proposal_created",
        applied=True,
        applied_payload={"change_id": change.change_id, "base_path": change.base_path},
        project_overview_change=change,
        context_patch=_record_context_write_apply_patch(
            intent,
            actor=actor,
            applied_payload={"change_id": change.change_id, "base_path": change.base_path},
            result_type="project_overview_proposal",
        ),
        intent=applied_intent or intent,
    )


def _apply_collaboration_message_intent(intent: ContextWriteIntentRecord, *, actor: str) -> ContextWriteApplyResult:
    payload = dict(intent.payload or {})
    message_payload = {
        **payload,
        "from_agent": payload.get("from_agent") or payload.get("from_model") or payload.get("actor") or intent.actor or actor,
        "to_agents": payload.get("to_agents") or payload.get("to_agent") or payload.get("to_model") or "all",
        "role": payload.get("role") or "event",
        "content": payload.get("content") or payload.get("message") or "",
    }
    try:
        message = post_collaboration_message(message_payload)
    except ValueError as exc:
        return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error=str(exc), intent=intent)
    applied_intent = mark_context_write_intent_applied(
        intent.intent_id,
        actor=actor,
        review_note=f"Posted collaboration message {message.message_id}",
    )
    return ContextWriteApplyResult(
        True,
        intent.intent_id,
        "applied",
        intent.target_path,
        intent.operation,
        message="collaboration_message_posted",
        applied=True,
        applied_payload={"message_id": message.message_id, "thread_id": message.thread_id or message.task_id},
        collaboration_message=message,
        context_patch=_record_context_write_apply_patch(
            intent,
            actor=actor,
            applied_payload={"message_id": message.message_id, "thread_id": message.thread_id or message.task_id},
            result_type="collaboration_message",
        ),
        intent=applied_intent or intent,
    )


def _apply_collaboration_decision_intent(intent: ContextWriteIntentRecord, *, actor: str) -> ContextWriteApplyResult:
    payload = dict(intent.payload or {})
    decision_payload = {
        **payload,
        "actor": payload.get("actor") or intent.actor or actor,
    }
    try:
        decision = record_collaboration_decision(decision_payload)
    except Exception as exc:
        return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error=str(exc), intent=intent)
    applied_intent = mark_context_write_intent_applied(
        intent.intent_id,
        actor=actor,
        review_note=f"Recorded collaboration decision {decision.decision_id}",
    )
    return ContextWriteApplyResult(
        True,
        intent.intent_id,
        "applied",
        intent.target_path,
        intent.operation,
        message="collaboration_decision_recorded",
        applied=True,
        applied_payload={"decision_id": decision.decision_id, "task_id": decision.task_id},
        collaboration_decision=decision,
        context_patch=_record_context_write_apply_patch(
            intent,
            actor=actor,
            applied_payload={"decision_id": decision.decision_id, "task_id": decision.task_id},
            result_type="collaboration_decision",
        ),
        intent=applied_intent or intent,
    )


def _apply_collaboration_review_intent(intent: ContextWriteIntentRecord, *, actor: str) -> ContextWriteApplyResult:
    payload = dict(intent.payload or {})
    review_payload = {
        **payload,
        "reviewer": payload.get("reviewer") or payload.get("actor") or intent.actor or actor,
    }
    try:
        review = record_collaboration_review(review_payload)
    except Exception as exc:
        return ContextWriteApplyResult(False, intent.intent_id, intent.status, intent.target_path, intent.operation, error=str(exc), intent=intent)
    applied_intent = mark_context_write_intent_applied(
        intent.intent_id,
        actor=actor,
        review_note=f"Recorded collaboration review {review.review_id}",
    )
    return ContextWriteApplyResult(
        True,
        intent.intent_id,
        "applied",
        intent.target_path,
        intent.operation,
        message="collaboration_review_recorded",
        applied=True,
        applied_payload={"review_id": review.review_id, "task_id": review.task_id},
        collaboration_review=review,
        context_patch=_record_context_write_apply_patch(
            intent,
            actor=actor,
            applied_payload={"review_id": review.review_id, "task_id": review.task_id},
            result_type="collaboration_review",
        ),
        intent=applied_intent or intent,
    )


def _normalize_context_policy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "mode",
        "max_recent_messages",
        "summarize_after_messages",
        "include_project_docs",
        "include_recent_events",
        "include_learning_records",
        "pinned_context",
    }
    return {key: value for key, value in dict(payload or {}).items() if key in allowed}


def _record_context_write_apply_patch(
    intent: ContextWriteIntentRecord,
    *,
    actor: str,
    applied_payload: dict[str, Any],
    result_type: str,
) -> ContextPatch:
    patch = ContextPatch(
        context_id=intent.context_id or "project:current",
        patch_type="context_write_applied",
        actor=actor or "context_write_applier",
        path="/context/write_intents/applied",
        value={
            "intent_id": intent.intent_id,
            "target_path": intent.target_path,
            "operation": intent.operation,
            "result_type": result_type,
            "applied_payload": dict(applied_payload or {}),
        },
        metadata={
            "source": "context_write_applier",
            "target_path": intent.target_path,
            "operation": intent.operation,
            "views": ["task"],
        },
    )
    append_context_patch(patch)
    return patch
