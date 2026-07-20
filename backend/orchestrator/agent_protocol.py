from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AGENT_PROTOCOL_SCHEMA_VERSION = "spiritkin.agent_protocol.v1"
AGENT_ROUTER_AUDIT_SCHEMA_VERSION = "spiritkin.agent_router_audit.v1"
AGENT_MESSAGE_TYPES = {"question", "answer", "plan", "decision", "review_request", "review", "event", "handoff"}
DEFAULT_AGENT_PERMISSION_SCOPES = ("", "read", "review", "write_intent")
DEFAULT_REVIEW_REQUIRED_PERMISSION_SCOPES = ("write_intent", "execute", "device", "worker", "admin")
DEFAULT_CONTEXT_REQUIRED_MESSAGE_TYPES = ("decision", "review_request", "review", "handoff")
DEFAULT_BLOCKED_AGENT_RECIPIENTS = ("worker", "worker_pool", "executor")
DEFAULT_AGENT_ROUTE_BUS_ROOT = "state/agent_route_bus"
AGENT_ROUTE_BUS_WORKER_EVENT_SCHEMA_VERSION = "spiritkin.agent_route_bus.worker_event.v1"
AGENT_ROUTE_BUS_TOOL_CALL_SCHEMA_VERSION = "spiritkin.agent_route_bus.tool_call.v1"
AGENT_ROUTE_BUS_TOOL_RESULT_SCHEMA_VERSION = "spiritkin.agent_route_bus.tool_result.v1"


@dataclass(frozen=True)
class AgentEnvelope:
    sender: str
    recipient: str
    message_type: str
    content: Any
    context_id: str = ""
    task_id: str = ""
    expected_output_schema: dict[str, Any] = field(default_factory=dict)
    permission_scope: str = ""
    deadline_at: float | None = None
    requires_review: bool = False
    artifacts: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: f"agentmsg-{uuid.uuid4().hex}")
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        normalized = str(self.message_type or "").strip().lower()
        if normalized not in AGENT_MESSAGE_TYPES:
            normalized = "event"
        object.__setattr__(self, "message_type", normalized)
        object.__setattr__(self, "permission_scope", normalize_permission_scope(self.permission_scope))

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_PROTOCOL_SCHEMA_VERSION,
            "message_id": self.message_id,
            "created_at": self.created_at,
            "sender": self.sender,
            "recipient": self.recipient,
            "message_type": self.message_type,
            "content": self.content,
            "context_id": self.context_id,
            "task_id": self.task_id,
            "expected_output_schema": dict(self.expected_output_schema or {}),
            "permission_scope": self.permission_scope,
            "deadline_at": self.deadline_at,
            "requires_review": self.requires_review,
            "artifacts": [dict(item) for item in self.artifacts],
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class AgentRouteVerdict:
    allowed: bool
    reason: str
    issues: tuple[str, ...] = ()
    permission_scope: str = ""
    requires_review: bool = False
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_PROTOCOL_SCHEMA_VERSION,
            "allowed": self.allowed,
            "reason": self.reason,
            "issues": list(self.issues),
            "permission_scope": self.permission_scope,
            "requires_review": self.requires_review,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AgentRoutePolicy:
    """Deterministic routing guard for cross-Agent messages."""

    allowed_senders: tuple[str, ...] = ()
    allowed_recipients: tuple[str, ...] = ()
    allowed_message_types: tuple[str, ...] = tuple(sorted(AGENT_MESSAGE_TYPES))
    allowed_permission_scopes: tuple[str, ...] = DEFAULT_AGENT_PERMISSION_SCOPES
    review_required_permission_scopes: tuple[str, ...] = DEFAULT_REVIEW_REQUIRED_PERMISSION_SCOPES
    context_required_message_types: tuple[str, ...] = DEFAULT_CONTEXT_REQUIRED_MESSAGE_TYPES
    blocked_recipients: tuple[str, ...] = DEFAULT_BLOCKED_AGENT_RECIPIENTS

    def evaluate(self, envelope: AgentEnvelope) -> AgentRouteVerdict:
        issues: list[str] = []
        sender = normalize_agent_actor_id(envelope.sender)
        recipient = normalize_agent_actor_id(envelope.recipient)
        permission_scope = normalize_permission_scope(envelope.permission_scope)
        allowed_senders = _normalized_tuple(self.allowed_senders)
        allowed_recipients = _normalized_tuple(self.allowed_recipients)
        blocked_recipients = _normalized_tuple(self.blocked_recipients)
        allowed_types = {str(item).strip().lower() for item in self.allowed_message_types if str(item).strip()}
        allowed_scopes = {normalize_permission_scope(item) for item in self.allowed_permission_scopes}
        review_required_scopes = {normalize_permission_scope(item) for item in self.review_required_permission_scopes}
        context_required_types = {str(item).strip().lower() for item in self.context_required_message_types if str(item).strip()}

        if not sender:
            issues.append("missing_sender")
        if not recipient:
            issues.append("missing_recipient")
        if sender and allowed_senders and sender not in allowed_senders:
            issues.append(f"sender_not_allowed:{sender}")
        if recipient and allowed_recipients and recipient not in allowed_recipients:
            issues.append(f"recipient_not_allowed:{recipient}")
        if recipient and recipient in blocked_recipients:
            issues.append(f"recipient_blocked:{recipient}")
        if envelope.message_type not in allowed_types:
            issues.append(f"message_type_not_allowed:{envelope.message_type}")
        if envelope.message_type in context_required_types and not (str(envelope.context_id or "").strip() or str(envelope.task_id or "").strip()):
            issues.append("missing_context")
        if permission_scope not in allowed_scopes:
            issues.append(f"permission_scope_not_allowed:{permission_scope or 'default'}")
        if permission_scope in review_required_scopes and not envelope.requires_review:
            issues.append(f"review_required_for_scope:{permission_scope}")
        if envelope.content is None:
            issues.append("missing_content")

        return AgentRouteVerdict(
            allowed=not issues,
            reason="allowed" if not issues else issues[0],
            issues=tuple(issues),
            permission_scope=permission_scope,
            requires_review=envelope.requires_review,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_PROTOCOL_SCHEMA_VERSION,
            "allowed_senders": list(self.allowed_senders),
            "allowed_recipients": list(self.allowed_recipients),
            "allowed_message_types": list(self.allowed_message_types),
            "allowed_permission_scopes": list(self.allowed_permission_scopes),
            "review_required_permission_scopes": list(self.review_required_permission_scopes),
            "context_required_message_types": list(self.context_required_message_types),
            "blocked_recipients": list(self.blocked_recipients),
        }


@dataclass(frozen=True)
class AgentRouteResult:
    envelope: AgentEnvelope
    verdict: AgentRouteVerdict
    audit_event: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_PROTOCOL_SCHEMA_VERSION,
            "envelope": self.envelope.snapshot(),
            "verdict": self.verdict.snapshot(),
            "audit_event": dict(self.audit_event),
        }


class InMemoryAgentRouter:
    """Minimal structured Agent bus used before integrating runtime transport."""

    def __init__(self, policy: AgentRoutePolicy | None = None):
        self._policy = policy or AgentRoutePolicy()
        self._messages: list[AgentEnvelope] = []
        self._audit_events: list[dict[str, Any]] = []

    def send(self, envelope: AgentEnvelope) -> AgentEnvelope:
        result = self.try_send(envelope)
        if not result.verdict.allowed:
            raise ValueError(result.verdict.reason)
        return envelope

    def try_send(self, envelope: AgentEnvelope) -> AgentRouteResult:
        verdict = self._policy.evaluate(envelope)
        audit_event = _agent_route_audit_event(envelope, verdict)
        self._audit_events.append(audit_event)
        if verdict.allowed:
            self._messages.append(envelope)
        return AgentRouteResult(envelope=envelope, verdict=verdict, audit_event=audit_event)

    def list_messages(
        self,
        *,
        recipient: str = "",
        context_id: str = "",
        task_id: str = "",
    ) -> list[AgentEnvelope]:
        return [
            message
            for message in self._messages
            if (not recipient or message.recipient == recipient)
            and (not context_id or message.context_id == context_id)
            and (not task_id or message.task_id == task_id)
        ]

    def audit_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        events = self._audit_events[-max(1, int(limit)) :]
        return [dict(event) for event in events]

    def snapshot(self, *, audit_limit: int = 50) -> dict[str, Any]:
        routed = len(self._messages)
        total = len(self._audit_events)
        return {
            "schema_version": AGENT_PROTOCOL_SCHEMA_VERSION,
            "policy": self._policy.snapshot(),
            "total": total,
            "routed": routed,
            "blocked": max(0, total - routed),
            "messages": [message.snapshot() for message in self._messages],
            "audit_events": self.audit_events(limit=audit_limit),
        }


class JsonlAgentRouteBus:
    """Durable Agent route bus seed backed by JSONL sidecars."""

    def __init__(self, root: str | os.PathLike[str] | None = None, policy: AgentRoutePolicy | None = None):
        self.root = resolve_agent_route_bus_root(root)
        self._policy = policy or AgentRoutePolicy()

    @property
    def messages_path(self) -> Path:
        return self.root / "messages.jsonl"

    @property
    def audit_path(self) -> Path:
        return self.root / "route_audit.jsonl"

    @property
    def ack_path(self) -> Path:
        return self.root / "message_acks.jsonl"

    @property
    def worker_events_path(self) -> Path:
        return self.root / "worker_events.jsonl"

    @property
    def tool_calls_path(self) -> Path:
        return self.root / "tool_calls.jsonl"

    @property
    def tool_results_path(self) -> Path:
        return self.root / "tool_results.jsonl"

    def try_send(self, envelope: AgentEnvelope) -> AgentRouteResult:
        verdict = self._policy.evaluate(envelope)
        audit_event = _agent_route_audit_event(envelope, verdict)
        _append_jsonl(self.audit_path, audit_event)
        if verdict.allowed:
            _append_jsonl(self.messages_path, envelope.snapshot())
        return AgentRouteResult(envelope=envelope, verdict=verdict, audit_event=audit_event)

    def send(self, envelope: AgentEnvelope) -> AgentEnvelope:
        result = self.try_send(envelope)
        if not result.verdict.allowed:
            raise ValueError(result.verdict.reason)
        return envelope

    def list_messages(
        self,
        *,
        recipient: str = "",
        context_id: str = "",
        task_id: str = "",
        consumer: str = "",
        include_acked: bool = True,
        limit: int = 100,
    ) -> list[AgentEnvelope]:
        recipient_id = normalize_agent_actor_id(recipient)
        consumer_id = normalize_agent_actor_id(consumer or recipient)
        acked_message_ids = set() if include_acked else self.acked_message_ids(consumer=consumer_id)
        row_limit = max(1, int(limit))
        rows = _read_jsonl(self.messages_path)
        messages = [_agent_envelope_from_snapshot(row) for row in rows]
        filtered = [
            message
            for message in messages
            if message is not None
            and (not recipient_id or _recipient_matches(message.recipient, recipient_id))
            and (not context_id or message.context_id == context_id)
            and (not task_id or message.task_id == task_id)
            and (include_acked or message.message_id not in acked_message_ids)
        ]
        return filtered[-row_limit:]

    def audit_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return _read_jsonl(self.audit_path)[-max(1, int(limit)) :]

    def ack_message(self, *, message_id: str, consumer: str, note: str = "") -> dict[str, Any]:
        consumer_id = normalize_agent_actor_id(consumer)
        if not str(message_id or "").strip():
            raise ValueError("missing message_id")
        if not consumer_id:
            raise ValueError("missing consumer")
        ack = {
            "schema_version": AGENT_PROTOCOL_SCHEMA_VERSION,
            "ack_id": f"agentack-{uuid.uuid4().hex}",
            "message_id": str(message_id).strip(),
            "consumer": consumer_id,
            "note": str(note or ""),
            "created_at": time.time(),
        }
        _append_jsonl(self.ack_path, ack)
        return ack

    def ack_events(self, *, consumer: str = "", message_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        consumer_id = normalize_agent_actor_id(consumer)
        target_message_id = str(message_id or "").strip()
        events = _read_jsonl(self.ack_path)
        filtered = [
            event
            for event in events
            if (not consumer_id or normalize_agent_actor_id(event.get("consumer")) == consumer_id)
            and (not target_message_id or str(event.get("message_id") or "") == target_message_id)
        ]
        return filtered[-max(1, int(limit)) :]

    def acked_message_ids(self, *, consumer: str = "") -> set[str]:
        return {str(event.get("message_id") or "") for event in self.ack_events(consumer=consumer, limit=10000) if event.get("message_id")}

    def record_worker_event(
        self,
        *,
        agent: str,
        status: str,
        message_id: str = "",
        context_id: str = "",
        task_id: str = "",
        transport: str = "route_bus",
        dry_run: bool = False,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        agent_id = normalize_agent_actor_id(agent)
        if not agent_id:
            raise ValueError("missing agent")
        event = {
            "schema_version": AGENT_ROUTE_BUS_WORKER_EVENT_SCHEMA_VERSION,
            "event_id": f"agentworkerevent-{uuid.uuid4().hex}",
            "created_at": time.time(),
            "agent": agent_id,
            "status": str(status or "unknown").strip().lower() or "unknown",
            "message_id": str(message_id or "").strip(),
            "context_id": str(context_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "transport": str(transport or "route_bus").strip(),
            "dry_run": bool(dry_run),
            "error": str(error or "").strip(),
            "metadata": dict(metadata or {}),
        }
        _attach_worker_event_trajectory(event)
        _append_jsonl(self.worker_events_path, event)
        _rotate_jsonl_if_oversized(self.worker_events_path)
        return event

    def worker_events(
        self,
        *,
        agent: str = "",
        status: str = "",
        context_id: str = "",
        task_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        agent_id = normalize_agent_actor_id(agent)
        target_status = str(status or "").strip().lower()
        target_context_id = str(context_id or "").strip()
        target_task_id = str(task_id or "").strip()
        events = _read_jsonl(self.worker_events_path)
        filtered = [
            event
            for event in events
            if (not agent_id or normalize_agent_actor_id(event.get("agent")) == agent_id)
            and (not target_status or str(event.get("status") or "").strip().lower() == target_status)
            and (not target_context_id or str(event.get("context_id") or "").strip() == target_context_id)
            and (not target_task_id or str(event.get("task_id") or "").strip() == target_task_id)
        ]
        return filtered[-max(1, int(limit)) :]

    def record_tool_call(
        self,
        *,
        agent: str,
        target: str,
        operation: str,
        params: dict[str, Any] | None = None,
        message_id: str = "",
        context_id: str = "",
        task_id: str = "",
        reason: str = "",
        status: str = "permission_required",
        requires_review: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        agent_id = normalize_agent_actor_id(agent)
        if not agent_id:
            raise ValueError("missing agent")
        if not str(target or "").strip():
            raise ValueError("missing target")
        if not str(operation or "").strip():
            raise ValueError("missing operation")
        call = {
            "schema_version": AGENT_ROUTE_BUS_TOOL_CALL_SCHEMA_VERSION,
            "tool_call_id": f"agenttoolcall-{uuid.uuid4().hex}",
            "created_at": time.time(),
            "updated_at": time.time(),
            "agent": agent_id,
            "target": str(target or "").strip(),
            "operation": str(operation or "").strip(),
            "params": dict(params or {}),
            "message_id": str(message_id or "").strip(),
            "context_id": str(context_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "reason": str(reason or "").strip(),
            "status": str(status or "permission_required").strip().lower(),
            "requires_review": bool(requires_review),
            "metadata": dict(metadata or {}),
        }
        _append_jsonl(self.tool_calls_path, call)
        return call

    def update_tool_call(
        self,
        *,
        tool_call_id: str,
        status: str,
        actor: str = "",
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_id = str(tool_call_id or "").strip()
        if not target_id:
            raise ValueError("missing tool_call_id")
        calls = self.tool_calls(limit=10000)
        current = next((item for item in reversed(calls) if str(item.get("tool_call_id") or "") == target_id), None)
        if current is None:
            raise ValueError(f"tool_call not found: {target_id}")
        updated = {
            **current,
            "status": str(status or current.get("status") or "unknown").strip().lower(),
            "updated_at": time.time(),
            "decision_actor": normalize_agent_actor_id(actor),
            "decision_note": str(note or "").strip(),
            "metadata": {**dict(current.get("metadata") or {}), **dict(metadata or {})},
        }
        _append_jsonl(self.tool_calls_path, updated)
        return updated

    def tool_calls(
        self,
        *,
        agent: str = "",
        status: str = "",
        context_id: str = "",
        task_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        agent_id = normalize_agent_actor_id(agent)
        target_status = str(status or "").strip().lower()
        target_context_id = str(context_id or "").strip()
        target_task_id = str(task_id or "").strip()
        rows = _read_jsonl(self.tool_calls_path)
        filtered = [
            row
            for row in rows
            if (not agent_id or normalize_agent_actor_id(row.get("agent")) == agent_id)
            and (not target_status or str(row.get("status") or "").strip().lower() == target_status)
            and (not target_context_id or str(row.get("context_id") or "").strip() == target_context_id)
            and (not target_task_id or str(row.get("task_id") or "").strip() == target_task_id)
        ]
        return filtered[-max(1, int(limit)) :]

    def record_tool_result(
        self,
        *,
        tool_call_id: str,
        agent: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_id = str(tool_call_id or "").strip()
        if not target_id:
            raise ValueError("missing tool_call_id")
        event = {
            "schema_version": AGENT_ROUTE_BUS_TOOL_RESULT_SCHEMA_VERSION,
            "tool_result_id": f"agenttoolresult-{uuid.uuid4().hex}",
            "tool_call_id": target_id,
            "created_at": time.time(),
            "agent": normalize_agent_actor_id(agent),
            "status": str(status or "unknown").strip().lower(),
            "result": dict(result or {}),
            "error": str(error or "").strip(),
            "metadata": dict(metadata or {}),
        }
        _append_jsonl(self.tool_results_path, event)
        return event

    def tool_results(self, *, tool_call_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        target_id = str(tool_call_id or "").strip()
        rows = _read_jsonl(self.tool_results_path)
        filtered = [row for row in rows if (not target_id or str(row.get("tool_call_id") or "") == target_id)]
        return filtered[-max(1, int(limit)) :]

    def snapshot(self, *, message_limit: int = 100, audit_limit: int = 100) -> dict[str, Any]:
        messages = self.list_messages(limit=message_limit)
        audit_events = self.audit_events(limit=audit_limit)
        ack_events = self.ack_events(limit=audit_limit)
        worker_events = self.worker_events(limit=audit_limit)
        tool_calls = self.tool_calls(limit=audit_limit)
        tool_results = self.tool_results(limit=audit_limit)
        return {
            "schema_version": AGENT_PROTOCOL_SCHEMA_VERSION,
            "storage": {
                "root": str(self.root),
                "messages": str(self.messages_path),
                "route_audit": str(self.audit_path),
                "message_acks": str(self.ack_path),
                "worker_events": str(self.worker_events_path),
                "tool_calls": str(self.tool_calls_path),
                "tool_results": str(self.tool_results_path),
            },
            "policy": self._policy.snapshot(),
            "total": len(audit_events),
            "routed": len(messages),
            "blocked": sum(1 for event in audit_events if not event.get("allowed")),
            "ack_count": len(ack_events),
            "worker_event_count": len(worker_events),
            "tool_call_count": len(tool_calls),
            "tool_result_count": len(tool_results),
            "messages": [message.snapshot() for message in messages],
            "audit_events": audit_events,
            "ack_events": ack_events,
            "worker_events": worker_events,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
        }


def normalize_agent_actor_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _recipient_matches(raw_recipient: Any, recipient_id: str) -> bool:
    # 协作消息镜像到路由总线时，多收件人以逗号拼接（"codex,model_deepseek"）；
    # 必须按成员匹配而不是全等，否则广播/多 @ 消息任何 worker 都收不到。
    parts = {
        normalize_agent_actor_id(part)
        for part in str(raw_recipient or "").split(",")
        if str(part).strip()
    }
    return "all" in parts or recipient_id in parts


def normalize_permission_scope(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def resolve_agent_route_bus_root(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", DEFAULT_AGENT_ROUTE_BUS_ROOT)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def _normalized_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in (normalize_agent_actor_id(value) for value in values) if item)


def _agent_route_audit_event(envelope: AgentEnvelope, verdict: AgentRouteVerdict) -> dict[str, Any]:
    return {
        "schema_version": AGENT_ROUTER_AUDIT_SCHEMA_VERSION,
        "event_id": f"agentroutelog-{uuid.uuid4().hex}",
        "created_at": verdict.created_at,
        "action": "agent_message_route",
        "allowed": verdict.allowed,
        "reason": verdict.reason,
        "issues": list(verdict.issues),
        "message_id": envelope.message_id,
        "sender": envelope.sender,
        "recipient": envelope.recipient,
        "message_type": envelope.message_type,
        "context_id": envelope.context_id,
        "task_id": envelope.task_id,
        "permission_scope": verdict.permission_scope,
        "requires_review": verdict.requires_review,
    }


def _agent_envelope_from_snapshot(snapshot: dict[str, Any]) -> AgentEnvelope | None:
    if not isinstance(snapshot, dict):
        return None
    return AgentEnvelope(
        sender=str(snapshot.get("sender") or ""),
        recipient=str(snapshot.get("recipient") or ""),
        message_type=str(snapshot.get("message_type") or "event"),
        content=snapshot.get("content"),
        context_id=str(snapshot.get("context_id") or ""),
        task_id=str(snapshot.get("task_id") or ""),
        expected_output_schema=dict(snapshot.get("expected_output_schema") or {}) if isinstance(snapshot.get("expected_output_schema"), dict) else {},
        permission_scope=str(snapshot.get("permission_scope") or ""),
        deadline_at=_float_or_none(snapshot.get("deadline_at")),
        requires_review=bool(snapshot.get("requires_review")),
        artifacts=tuple(dict(item) for item in snapshot.get("artifacts") or () if isinstance(item, dict)),
        metadata=dict(snapshot.get("metadata") or {}) if isinstance(snapshot.get("metadata"), dict) else {},
        message_id=str(snapshot.get("message_id") or f"agentmsg-{uuid.uuid4().hex}"),
        created_at=_float_or_now(snapshot.get("created_at")),
    )


def _attach_worker_event_trajectory(event: dict[str, Any]) -> None:
    if not _should_log_worker_event_trajectory(event):
        return
    try:
        from backend.orchestrator.runtime_trajectory_log import (
            append_runtime_trajectory,
            trajectory_from_collaboration_worker_event,
            trajectory_logging_enabled,
        )
    except Exception as exc:
        event["trajectory_log_error"] = str(exc)
        return
    if not trajectory_logging_enabled():
        return
    try:
        record = append_runtime_trajectory(trajectory_from_collaboration_worker_event(event))
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        event["trajectory_record"] = {
            "trajectory_id": record.get("trajectory_id", ""),
            "source": metadata.get("source", "collaboration.worker_event"),
            "overall_success": bool(record.get("overall_success", False)),
            "bottleneck_stage": record.get("bottleneck_stage", ""),
        }
    except Exception as exc:
        event["trajectory_log_error"] = str(exc)


def _should_log_worker_event_trajectory(event: dict[str, Any]) -> bool:
    status = str(event.get("status") or "").strip().lower()
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    lifecycle = str(metadata.get("lifecycle") or "").strip().lower()
    if status in {"", "started", "stream", "running", "idle"}:
        return False
    if status == "blocked" and lifecycle == "permission_required" and not str(event.get("error") or "").strip():
        return False
    return status in {"processed", "completed", "failed", "blocked", "real_worker_not_enabled"} or bool(str(event.get("error") or "").strip())


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


# 快照/轮询热路径会对同一批 jsonl 每秒重复全量读（worker_events 曾膨胀到 15MB），
# 单次快照就要 ~1.1s CPU，多个客户端并发轮询直接把网关打到 40s+ 排队。
# 这些文件只追加，所以按 (size, mtime) 缓存已解析行；文件变大时只解析新增字节。
# 注意：缓存行是共享引用，调用方必须把返回的 dict 当只读快照使用。
_JSONL_CACHE: dict[str, dict[str, Any]] = {}
_JSONL_CACHE_LOCK = threading.Lock()


def _parse_jsonl_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        stat = path.stat()
    except OSError:
        return []
    key = str(path)
    size = int(stat.st_size)
    mtime_ns = int(stat.st_mtime_ns)
    with _JSONL_CACHE_LOCK:
        cached = _JSONL_CACHE.get(key)
        if cached is not None and cached["size"] == size and cached["mtime_ns"] == mtime_ns:
            return list(cached["rows"])
        try:
            if cached is not None and 0 < cached["size"] < size:
                # 纯追加：只读新增字节（追加以整行为单位，旧 size 必落在行边界上）。
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(cached["size"])
                    delta = handle.read()
                rows = cached["rows"] + _parse_jsonl_lines(delta)
            else:
                # 首次读取，或文件被轮转/截断/重写：全量重建。
                rows = _parse_jsonl_lines(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return []
        _JSONL_CACHE[key] = {"size": size, "mtime_ns": mtime_ns, "rows": rows}
        return list(rows)


# worker_events 每个流式碎片记一条，若不设上限文件会无限膨胀并拖垮所有快照读。
_WORKER_EVENTS_ROTATE_BYTES = 4 * 1024 * 1024
_WORKER_EVENTS_KEEP_LINES = 2000
_WORKER_EVENTS_ROTATE_LOCK = threading.Lock()


def _rotate_jsonl_if_oversized(path: Path, *, max_bytes: int = _WORKER_EVENTS_ROTATE_BYTES, keep_lines: int = _WORKER_EVENTS_KEEP_LINES) -> None:
    try:
        if path.stat().st_size <= max_bytes:
            return
    except OSError:
        return
    with _WORKER_EVENTS_ROTATE_LOCK:
        try:
            if path.stat().st_size <= max_bytes:
                return
            lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
            # 行数与字节双预算：单条事件可能带大段输出（实测均值 3KB+），
            # 只按行数截尾可能仍超阈值，导致每次追加都重触发轮转重写。
            byte_budget = max(1, int(max_bytes) // 2)
            kept: list[str] = []
            kept_bytes = 0
            for line in reversed(lines[-max(1, int(keep_lines)) :]):
                line_bytes = len(line.encode("utf-8")) + 1
                if kept and kept_bytes + line_bytes > byte_budget:
                    break
                kept.append(line)
                kept_bytes += line_bytes
            kept.reverse()
            tmp_path = path.with_suffix(path.suffix + ".rotate.tmp")
            tmp_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            os.replace(tmp_path, path)
        except OSError:
            return
        # 轮转后文件变小；若随后快速追加回超过旧缓存 size，增量读会从旧偏移错切半行。
        # 直接作废缓存，下次读全量重建。
        with _JSONL_CACHE_LOCK:
            _JSONL_CACHE.pop(str(path), None)


def _float_or_none(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_now(value: Any) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else time.time()
