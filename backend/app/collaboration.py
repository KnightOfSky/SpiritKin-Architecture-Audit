"""Multi-model collaboration workspace (tasks, messages, file claims, reviews).

TODO(debt-#4): ~2000-line god module; carve opportunistically by feature —
see docs/ai_collaboration_context.md, 2026-07-03 review item #4.
"""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from backend.app.collaboration_participants import (
    build_collaboration_participant_registry,
    collaboration_participants_mentioned_in_text,
    resolve_collaboration_participant,
)
from backend.app.collaboration_turn_guard import (
    pause_turns,
    record_human_activity,
    record_turn_and_check,
    refill_turns,
    reset_turns,
    set_thread_turn_cap,
    turn_guard_snapshot,
)
from backend.app.collaboration_worker_status import build_collaboration_worker_config_status
from backend.executors.base import ExecutionRequest
from backend.executors.local_pc_executor import LocalPCExecutor
from backend.orchestrator.agent_protocol import AgentEnvelope, AgentRoutePolicy, JsonlAgentRouteBus
from backend.orchestrator.agent_protocol import _read_jsonl as _cached_read_jsonl
from backend.orchestrator.worker_pool import WorkerPool
from backend.state_store import resolve_state_path

SCHEMA_VERSION = "spiritkin.collaboration.v1"
DEFAULT_COLLABORATION_ROOT = "state/collaboration"
DEFAULT_PROJECT_OVERVIEW_PATH = "docs/project_management_overview.md"
DEFAULT_ROUTE_BUS_WORKER_AGENTS = ("codex", "claude_code", "cloud_model")
_COLLABORATION_MESSAGE_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class CollaborationTask:
    task_id: str
    title: str
    owner: str
    status: str = "planned"
    scope: tuple[str, ...] = ()
    allowed_files: tuple[str, ...] = ()
    blocked_files: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    note: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "owner": self.owner,
            "status": self.status,
            "scope": list(self.scope),
            "allowed_files": list(self.allowed_files),
            "blocked_files": list(self.blocked_files),
            "verification_commands": list(self.verification_commands),
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CollaborationDecision:
    decision_id: str
    title: str
    decision: str
    rationale: str
    actor: str
    task_id: str = ""
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "title": self.title,
            "decision": self.decision,
            "rationale": self.rationale,
            "actor": self.actor,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CollaborationReview:
    review_id: str
    task_id: str
    reviewer: str
    verdict: str
    summary: str
    evidence: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "task_id": self.task_id,
            "reviewer": self.reviewer,
            "verdict": self.verdict,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CollaborationMessage:
    message_id: str
    task_id: str
    from_model: str
    to_model: str
    role: str
    content: str
    thread_id: str = ""
    from_agent: str = ""
    to_agents: tuple[str, ...] = ()
    context_pack_path: str = ""
    parent_message_id: str = ""
    status: str = "open"
    read_by: tuple[str, ...] = ()
    route_verdict: dict[str, Any] = field(default_factory=dict)
    route_audit_event: dict[str, Any] = field(default_factory=dict)
    route_bus_event: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        from_agent = self.from_agent or self.from_model
        to_agents = self.to_agents or _as_agent_tuple(self.to_model)
        to_model = self.to_model or ",".join(to_agents) or "all"
        envelope_metadata = dict(self.metadata or {})
        envelope_metadata.update(
            {
                "schema_version": SCHEMA_VERSION,
                "thread_id": self.thread_id or self.task_id,
                "to_agents": list(to_agents),
                "parent_message_id": self.parent_message_id,
                "status": self.status,
            }
        )
        envelope = AgentEnvelope(
            message_id=self.message_id,
            sender=from_agent,
            recipient=to_model,
            message_type=_agent_message_type(self.role),
            content=self.content,
            context_id=self.thread_id or self.task_id,
            task_id=self.task_id,
            artifacts=({"path": self.context_pack_path, "kind": "context_pack"},) if self.context_pack_path else (),
            metadata=envelope_metadata,
            created_at=self.created_at,
        )
        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "thread_id": self.thread_id or self.task_id,
            "from_agent": from_agent,
            "to_agents": list(to_agents),
            "agent_envelope": envelope.snapshot(),
            # Compatibility fields for older desktop/CLI consumers. New code should use Agent fields above.
            "from_model": self.from_model or from_agent,
            "to_model": to_model,
            "role": self.role,
            "content": self.content,
            "context_pack_path": self.context_pack_path,
            "parent_message_id": self.parent_message_id,
            "status": self.status,
            "read_by": list(self.read_by),
            "route_verdict": dict(self.route_verdict or {}),
            "route_audit_event": dict(self.route_audit_event or {}),
            "route_bus_event": dict(self.route_bus_event or {}),
            "metadata": dict(self.metadata or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CollaborationThreadState:
    thread_id: str
    status: str = "active"
    title: str = ""
    archived_at: float = 0.0
    deleted_at: float = 0.0
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "status": self.status,
            "title": self.title,
            "archived_at": self.archived_at,
            "deleted_at": self.deleted_at,
            "updated_at": self.updated_at,
        }


def resolve_collaboration_root(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_COLLABORATION_ROOT", DEFAULT_COLLABORATION_ROOT, path)


def _jsonl_path(root: str | os.PathLike[str] | None, name: str) -> Path:
    return resolve_collaboration_root(root) / name


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    # 协作 jsonl 全部只追加，读走 agent_protocol 的增量缓存（快照轮询热路径，
    # messages.jsonl 曾 2MB，每次全量重读会叠加拖慢网关）。返回行视为只读快照。
    return _cached_read_jsonl(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return dict(default or {})
    return data if isinstance(data, dict) else dict(default or {})


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _normalize_agent_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = "".join(ch for ch in raw.lower() if ch.isalnum())
    aliases = {
        "me": "human_desktop",
        "user": "human_desktop",
        "human": "human_desktop",
        "humandesktop": "human_desktop",
        "我": "human_desktop",
        "codex": "codex",
        "codexcli": "codex",
        "claude": "claude_code",
        "claudecode": "claude_code",
        "claudecli": "claude_code",
        "cc": "claude_code",
        "reviewer": "external_reviewer",
        "externalreviewer": "external_reviewer",
        "all": "all",
        "全部": "all",
        "所有": "all",
    }
    if key in aliases:
        return aliases[key]
    resolved = resolve_collaboration_participant(raw)
    if resolved:
        return resolved
    return raw.strip().lower().replace("-", "_").replace(" ", "_")


def _agent_message_type(role: str) -> str:
    normalized = str(role or "").strip().lower()
    aliases = {
        "note": "event",
        "message": "event",
        "review_request": "review_request",
        "request_review": "review_request",
        "review": "review",
        "decision": "decision",
        "question": "question",
        "answer": "answer",
        "handoff": "handoff",
        "plan": "plan",
    }
    return aliases.get(normalized, "event")


def _as_agent_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items = [item for item in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    agents: list[str] = []
    for item in raw_items:
        normalized = _normalize_agent_id(item)
        if normalized and normalized not in agents:
            agents.append(normalized)
    return tuple(agents)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def load_collaboration_tasks(root: str | os.PathLike[str] | None = None) -> list[CollaborationTask]:
    tasks: dict[str, CollaborationTask] = {}
    for data in _read_jsonl(_jsonl_path(root, "tasks.jsonl")):
        task = _task_from_dict(data)
        tasks[task.task_id] = task
    return sorted(tasks.values(), key=lambda item: (item.created_at, item.task_id))


def load_collaboration_decisions(root: str | os.PathLike[str] | None = None) -> list[CollaborationDecision]:
    return [_decision_from_dict(item) for item in _read_jsonl(_jsonl_path(root, "decisions.jsonl"))]


def load_collaboration_reviews(root: str | os.PathLike[str] | None = None) -> list[CollaborationReview]:
    return [_review_from_dict(item) for item in _read_jsonl(_jsonl_path(root, "reviews.jsonl"))]


def load_collaboration_messages(root: str | os.PathLike[str] | None = None) -> list[CollaborationMessage]:
    messages: dict[str, CollaborationMessage] = {}
    for data in _read_jsonl(_jsonl_path(root, "messages.jsonl")):
        message = _message_from_dict(data)
        messages[message.message_id] = message
    return sorted(messages.values(), key=lambda item: (item.created_at, item.message_id))


def load_collaboration_thread_states(root: str | os.PathLike[str] | None = None) -> dict[str, CollaborationThreadState]:
    raw = _read_json(_jsonl_path(root, "threads.json"), {"threads": {}})
    result: dict[str, CollaborationThreadState] = {}
    raw_threads = raw.get("threads") if isinstance(raw, dict) else {}
    if isinstance(raw_threads, dict):
        iterable = raw_threads.values()
    elif isinstance(raw_threads, list):
        iterable = raw_threads
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        thread = _thread_state_from_dict(item)
        if thread.thread_id:
            result[thread.thread_id] = thread
    return result


def set_collaboration_thread_status(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationThreadState:
    thread_id = str(payload.get("thread_id") or payload.get("task_id") or "").strip()
    if not thread_id:
        raise ValueError("missing thread_id")
    requested = str(payload.get("status") or payload.get("thread_status") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    if action in {"archive_thread", "archive_collaboration_thread"}:
        requested = "archived"
    elif action in {"restore_thread", "restore_collaboration_thread", "unarchive_thread"}:
        requested = "active"
    elif action in {"delete_thread", "delete_collaboration_thread"}:
        requested = "deleted"
    if requested not in {"active", "archived", "deleted"}:
        raise ValueError("unsupported thread status")

    now = time.time()
    threads = load_collaboration_thread_states(root)
    current = threads.get(thread_id, CollaborationThreadState(thread_id=thread_id))
    updated = CollaborationThreadState(
        thread_id=thread_id,
        status=requested,
        title=str(payload.get("title") or current.title or "").strip(),
        archived_at=now if requested == "archived" else (0.0 if requested == "active" else current.archived_at),
        deleted_at=now if requested == "deleted" else (0.0 if requested == "active" else current.deleted_at),
        updated_at=now,
    )
    threads[thread_id] = updated
    _write_json(
        _jsonl_path(root, "threads.json"),
        {"schema_version": SCHEMA_VERSION, "threads": {key: value.snapshot() for key, value in sorted(threads.items())}},
    )
    return updated


def load_file_claims(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return _read_json(_jsonl_path(root, "file_claims.json"), {"claims": []})


def create_collaboration_task(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationTask:
    now = time.time()
    task = CollaborationTask(
        task_id=str(payload.get("task_id") or _new_id("collab-task")),
        title=str(payload.get("title") or payload.get("request") or "Untitled collaboration task").strip(),
        owner=str(payload.get("owner") or payload.get("assignee") or "unassigned").strip(),
        status=str(payload.get("status") or "planned").strip().lower(),
        scope=_as_tuple(payload.get("scope")),
        allowed_files=_as_tuple(payload.get("allowed_files")),
        blocked_files=_as_tuple(payload.get("blocked_files")),
        verification_commands=_as_tuple(payload.get("verification_commands")),
        note=str(payload.get("note") or ""),
        created_at=float(payload.get("created_at") or now),
        updated_at=now,
    )
    _append_jsonl(_jsonl_path(root, "tasks.jsonl"), task.snapshot())
    return task


def update_collaboration_task(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationTask:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("missing task_id")
    current = next((task for task in load_collaboration_tasks(root) if task.task_id == task_id), None)
    if current is None:
        raise ValueError(f"task not found: {task_id}")
    merged = {
        **current.snapshot(),
        **{key: value for key, value in payload.items() if value is not None},
        "updated_at": time.time(),
    }
    task = _task_from_dict(merged)
    _append_jsonl(_jsonl_path(root, "tasks.jsonl"), task.snapshot())
    return task


def record_collaboration_decision(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationDecision:
    decision = CollaborationDecision(
        decision_id=str(payload.get("decision_id") or _new_id("decision")),
        task_id=str(payload.get("task_id") or ""),
        title=str(payload.get("title") or "Untitled decision"),
        decision=str(payload.get("decision") or ""),
        rationale=str(payload.get("rationale") or payload.get("note") or ""),
        actor=str(payload.get("actor") or payload.get("author") or "unknown"),
        created_at=float(payload.get("created_at") or time.time()),
    )
    _append_jsonl(_jsonl_path(root, "decisions.jsonl"), decision.snapshot())
    return decision


def record_collaboration_review(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationReview:
    review = CollaborationReview(
        review_id=str(payload.get("review_id") or _new_id("review")),
        task_id=str(payload.get("task_id") or ""),
        reviewer=str(payload.get("reviewer") or "unknown"),
        verdict=str(payload.get("verdict") or "comment").strip().lower(),
        summary=str(payload.get("summary") or payload.get("note") or ""),
        evidence=_as_tuple(payload.get("evidence")),
        created_at=float(payload.get("created_at") or time.time()),
    )
    _append_jsonl(_jsonl_path(root, "reviews.jsonl"), review.snapshot())
    return review


def _existing_collaboration_message(
    message_id: str,
    root: str | os.PathLike[str] | None = None,
) -> CollaborationMessage | None:
    target_id = str(message_id or "").strip()
    if not target_id:
        return None
    for item in reversed(_read_jsonl(_jsonl_path(root, "messages.jsonl"))):
        if str(item.get("message_id") or "").strip() == target_id:
            return _message_from_dict(item)
    return None


def post_collaboration_message(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationMessage:
    # The gateway is threaded. Keep idempotency lookup, route accounting, route-bus
    # mirroring, and persistence in one critical section so two retries with the
    # same message_id cannot both append or consume collaboration turn budget.
    with _COLLABORATION_MESSAGE_WRITE_LOCK:
        return _post_collaboration_message_locked(payload, root)


def _post_collaboration_message_locked(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationMessage:
    now = time.time()
    from_agent = _normalize_agent_id(payload.get("from_agent") or payload.get("from_participant") or payload.get("from_model") or payload.get("actor") or payload.get("owner"))
    content = str(payload.get("content") or payload.get("message") or "").strip()
    to_agents = _as_agent_tuple(payload.get("to_agents") or payload.get("to_agent") or payload.get("to_participants") or payload.get("to_model") or payload.get("target_model") or "all")
    if _is_human_collaboration_agent(from_agent):
        mentioned_participants = collaboration_participants_mentioned_in_text(content)
        if mentioned_participants:
            to_agents = mentioned_participants
    from_model = str(payload.get("from_model") or from_agent).strip()
    to_model = str(payload.get("to_model") or ",".join(to_agents) or "all").strip()
    if to_agents:
        to_model = ",".join(to_agents)
    message_id = str(payload.get("message_id") or _new_id("message"))
    if not from_agent:
        raise ValueError("missing from_agent")
    if not content:
        raise ValueError("missing content")
    existing = _existing_collaboration_message(message_id, root)
    if existing is not None:
        requested_thread = str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip()
        requested_parent = str(payload.get("parent_message_id") or "").strip()
        requested_role = str(payload.get("role") or "note").strip().lower()
        if (
            existing.from_agent == from_agent
            and existing.thread_id == requested_thread
            and existing.parent_message_id == requested_parent
            and existing.role == requested_role
            and existing.content == content
        ):
            return existing
        raise ValueError(f"message_id_conflict:{message_id}")
    route_verdict, route_audit = _evaluate_message_route(
        sender=from_agent,
        recipients=to_agents or _as_agent_tuple(to_model) or ("all",),
        message_type=_agent_message_type(str(payload.get("role") or "note")),
        content=content,
        context_id=str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip(),
        task_id=str(payload.get("task_id") or "").strip(),
        permission_scope=str(payload.get("permission_scope") or ""),
        requires_review=bool(payload.get("requires_review", False)),
        context_pack_path=str(payload.get("context_pack_path") or payload.get("context_pack") or "").strip(),
        message_id=message_id,
    )
    if not route_verdict.get("allowed", False):
        raise ValueError(str(route_verdict.get("reason") or "agent_route_blocked"))
    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip()
    role = str(payload.get("role") or "note").strip().lower()
    turn_verdict: dict[str, Any] = {}
    if _is_human_collaboration_agent(from_agent):
        record_human_activity(thread_id, actor=from_agent, root=root)
    if _is_automatic_model_reply(from_agent, to_agents or ("all",), role):
        if not collaboration_auto_reply_enabled():
            # 双工关掉 ≠ 模型不回人（2026-07-07 实测事故：fan-out 让模型给人类的
            # 直接回答也总带着其他模型收件人，旧逻辑整条 400 拒收，用户发起辩论后
            # 两个模型的回复全被吞、界面只见思考卡"卡住"）。
            # 语义修正：剔除模型收件人、保留人类收件人继续投递（不计轮次——
            # 与 _is_automatic_model_reply 文档一致，回人不消耗预算）；
            # 只有纯模型→模型（无人类收件人）才维持拒收。
            human_recipients = tuple(agent for agent in (to_agents or ()) if _is_human_collaboration_agent(agent))
            if not human_recipients:
                raise ValueError(
                    "auto_reply_disabled: automatic model-to-model replies are off by default; "
                    "set SPIRITKIN_COLLABORATION_AUTO_REPLY=1 to enable them (the turn cap still applies)."
                )
            to_agents = human_recipients
            to_model = ",".join(to_agents)
        else:
            turn_verdict = record_turn_and_check(thread_id, agent=from_agent, root=root)
            if not turn_verdict.get("allowed", False):
                reason = str(turn_verdict.get("reason") or "turn_cap_reached")
                raise ValueError(
                    f"{reason}: automatic model-to-model reply paused for thread "
                    f"'{turn_verdict.get('thread_id')}' (used {turn_verdict.get('turns_used')}/{turn_verdict.get('cap')}); "
                    f"a human must refill before more automatic turns are produced."
                )
    message = CollaborationMessage(
        message_id=message_id,
        task_id=str(payload.get("task_id") or "").strip(),
        from_model=from_model,
        to_model=to_model or "all",
        role=str(payload.get("role") or "note").strip().lower(),
        content=content,
        thread_id=str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip(),
        from_agent=from_agent,
        to_agents=to_agents or ("all",),
        context_pack_path=str(payload.get("context_pack_path") or payload.get("context_pack") or "").strip(),
        parent_message_id=str(payload.get("parent_message_id") or "").strip(),
        status=str(payload.get("status") or "open").strip().lower(),
        read_by=_as_tuple(payload.get("read_by")),
        route_verdict=route_verdict,
        route_audit_event=route_audit,
        metadata=dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
        created_at=float(payload.get("created_at") or now),
        updated_at=now,
    )
    message = replace(message, route_bus_event=mirror_collaboration_message_to_agent_route_bus(message))
    _append_jsonl(_jsonl_path(root, "messages.jsonl"), message.snapshot())
    record_collaboration_message_lifecycle_events(message)
    push_collaboration_message_to_event_bridge(message)
    return message


def mirror_collaboration_message_to_agent_route_bus(message: CollaborationMessage) -> dict[str, Any]:
    snapshot = message.snapshot()
    envelope = _agent_envelope_from_snapshot(snapshot.get("agent_envelope"))
    try:
        result = JsonlAgentRouteBus().try_send(envelope)
    except Exception as exc:  # pragma: no cover - collaboration message persistence should survive mirror failures.
        return {
            "mirrored": False,
            "error": f"{type(exc).__name__}: {exc}",
            "message_id": message.message_id,
        }
    return {
        "mirrored": result.verdict.allowed,
        "message_id": message.message_id,
        "agent_message_id": result.envelope.message_id,
        "route_allowed": result.verdict.allowed,
        "route_reason": result.verdict.reason,
        "audit_event_id": result.audit_event.get("event_id", ""),
    }


def _collaboration_push_disabled() -> bool:
    return str(os.getenv("SPIRITKIN_DISABLE_COLLABORATION_PUSH") or "").strip().lower() in {"1", "true", "yes", "on"}


def collaboration_auto_reply_enabled(root: str | os.PathLike[str] | None = None) -> bool:
    """Operator switch for automatic model→model replies.

    Resolution order:
    1. ``SPIRITKIN_COLLABORATION_AUTO_REPLY`` env var (explicit override, wins).
    2. ``auto_reply.json`` under the collaboration root (desktop toggle).
    3. Default ON — duplex model↔model chat is enabled by default; the turn cap
       still bounds any reply chain so it cannot loop forever.

    The turn cap always applies once a model→model ``answer`` is attempted, so
    even ON there is a hard budget ceiling.
    """
    env = str(os.getenv("SPIRITKIN_COLLABORATION_AUTO_REPLY") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    state = _read_collaboration_auto_reply_state(root)
    if state is not None:
        return state
    return True


def _collaboration_auto_reply_path(root: str | os.PathLike[str] | None = None) -> Path:
    return resolve_collaboration_root(root) / "auto_reply.json"


def _read_collaboration_auto_reply_state(root: str | os.PathLike[str] | None = None) -> bool | None:
    path = _collaboration_auto_reply_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, dict) and "enabled" in data:
        return bool(data.get("enabled"))
    return None


def set_collaboration_auto_reply(enabled: bool, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = _collaboration_auto_reply_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": bool(enabled)}, ensure_ascii=False), encoding="utf-8")
    return get_collaboration_auto_reply_state(root)


def get_collaboration_auto_reply_state(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    env = str(os.getenv("SPIRITKIN_COLLABORATION_AUTO_REPLY") or "").strip().lower()
    if env in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        source = "env"
    elif _read_collaboration_auto_reply_state(root) is not None:
        source = "file"
    else:
        source = "default"
    return {"enabled": collaboration_auto_reply_enabled(root), "source": source}


def is_human_collaboration_agent(value: Any) -> bool:
    return _is_human_collaboration_agent(value)


def _is_automatic_model_reply(from_agent: str, to_agents: tuple[str, ...], role: str) -> bool:
    """True only for a model→model automatic ``answer``.

    These are exactly the turns that can loop forever and burn API cost, so
    they are the only messages that consume the turn-limit budget. A human
    author, or a model replying only to a human, never consumes a turn — those
    conversations are naturally paced by the human.
    """
    if str(role or "").strip().lower() != "answer":
        return False
    if _is_human_collaboration_agent(from_agent):
        return False
    model_recipients = [
        agent
        for agent in (to_agents or ())
        if agent and agent != "all" and not _is_human_collaboration_agent(agent)
    ]
    return bool(model_recipients)


PRESENTATION_USER = "user"
PRESENTATION_OUTWARD = "outward"
PRESENTATION_INTERNAL = "internal"


def _classify_collaboration_presentation(from_agent: str, to_agents: tuple[str, ...], role: str) -> str:
    """Classify how the avatar should present a collaboration message.

    The avatar is a single personality, not a chorus of models. This tells the
    frontend which surface a message belongs on:

    - ``user``: the human authored it → echo it as the user's own turn.
    - ``outward``: a model is speaking to the human → voice it as the single
      personality (no model name, this is "the assistant talking").
    - ``internal``: model↔model deliberation with no human recipient → render
      only as background "thinking", never as a separate speaking character.
    """
    if _is_human_collaboration_agent(from_agent):
        return PRESENTATION_USER
    human_recipients = [
        agent
        for agent in (to_agents or ())
        if agent and agent != "all" and _is_human_collaboration_agent(agent)
    ]
    if human_recipients:
        return PRESENTATION_OUTWARD
    if not to_agents or "all" in (to_agents or ()):
        # Broadcast with no explicit model-only routing is treated as outward:
        # the human is always an implicit audience of an "all" answer.
        return PRESENTATION_OUTWARD
    return PRESENTATION_INTERNAL


def refill_collaboration_turns(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Human top-up ("人工续杯") for a paused model-to-model conversation."""
    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip()
    additional = payload.get("additional") or payload.get("turns") or payload.get("amount") or 0
    actor = _normalize_agent_id(payload.get("actor") or payload.get("owner") or payload.get("reader")) or "human_desktop"
    return refill_turns(thread_id, additional=additional, actor=actor, root=root)


def set_collaboration_thread_turn_cap(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Apply the turn cap to the current thread immediately."""
    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip()
    cap = payload.get("cap", payload.get("turn_cap", payload.get("limit", 0)))
    actor = _normalize_agent_id(payload.get("actor") or payload.get("owner") or payload.get("reader")) or "human_desktop"
    return set_thread_turn_cap(thread_id, cap=cap, actor=actor, root=root)


def pause_collaboration_turns(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Soft-stop automatic model-to-model replies for a thread."""
    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip()
    actor = _normalize_agent_id(payload.get("actor") or payload.get("owner") or payload.get("reader")) or "human_desktop"
    return pause_turns(thread_id, actor=actor, root=root)


def reset_collaboration_turns(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Zero the consumed-turn counter for a thread (start a fresh conversation)."""
    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip()
    return reset_turns(thread_id, root=root)


def collaboration_turn_guard_status(payload: dict[str, Any] | None = None, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Operator-visible turn-budget snapshot: one thread, or all when unset."""
    payload = payload or {}
    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or payload.get("topic_id") or payload.get("task_id") or "").strip()
    return turn_guard_snapshot(thread_id, root=root)


def push_collaboration_message_to_event_bridge(message: CollaborationMessage) -> dict[str, Any]:
    """Fan the collaboration message out to the realtime event bridge (ws 8765).

    This replaces polling for message delivery: any subscribed client (web
    console, WPF desktop, or a push-triggered worker) receives the message the
    moment it is persisted, instead of waiting for the next 3-15s poll tick.
    Failures are swallowed so message persistence never depends on the bridge
    being up.
    """
    if _collaboration_push_disabled():
        return {"pushed": False, "reason": "collaboration_push_disabled"}
    try:
        from backend.app.runtime import dispatch_runtime_event, resolve_event_sink_url

        snapshot = message.snapshot()
        snapshot["presentation"] = _classify_collaboration_presentation(
            message.from_agent, message.to_agents, message.role
        )
        event = {
            "type": "collaboration.message",
            "schema_version": SCHEMA_VERSION,
            "payload": snapshot,
        }
        dispatched = dispatch_runtime_event(resolve_event_sink_url(), event)
        return {"pushed": bool(dispatched), "message_id": message.message_id}
    except Exception as exc:  # pragma: no cover - push is best-effort, never fatal.
        return {"pushed": False, "error": f"{type(exc).__name__}: {exc}", "message_id": message.message_id}


def record_collaboration_message_lifecycle_events(message: CollaborationMessage) -> None:
    if _is_human_collaboration_agent(message.from_agent):
        recipients = message.to_agents or ("all",)
        for recipient in recipients:
            agent = _normalize_agent_id(recipient)
            if not agent or agent == "all" or _is_human_collaboration_agent(agent):
                continue
            _record_collaboration_lifecycle_event(
                agent=agent,
                message=message,
                status="queued",
                lifecycle="queued",
                output=f"Queued collaboration message for {agent}.",
                extra={"recipient": agent},
            )
            _record_collaboration_lifecycle_event(
                agent=agent,
                message=message,
                status="stream",
                lifecycle="routed",
                output=f"Routed collaboration message to {agent}.",
                extra={"recipient": agent},
            )
        return

    if message.role == "answer":
        _record_collaboration_lifecycle_event(
            agent=message.from_agent,
            message=message,
            status="stream",
            lifecycle="reply_posted",
            output=f"Posted reply from {message.from_agent}.",
            extra={"reply_chars": len(message.content), "parent_message_id": message.parent_message_id},
        )
        _record_collaboration_lifecycle_event(
            agent=message.from_agent,
            message=message,
            status="completed",
            lifecycle="terminal",
            output=f"Collaboration reply from {message.from_agent} completed.",
            extra={"reply_chars": len(message.content), "parent_message_id": message.parent_message_id, "is_terminal": True},
        )


def _is_human_collaboration_agent(value: Any) -> bool:
    return _normalize_agent_id(value) in {"human_desktop", "human", "user", "me", "wpf_desktop"}


def _record_collaboration_lifecycle_event(
    *,
    agent: str,
    message: CollaborationMessage,
    status: str,
    lifecycle: str,
    output: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        "lifecycle": lifecycle,
        "stream": "lifecycle",
        "output": output,
        "thread_id": message.thread_id,
        "task_id": message.task_id,
        "role": message.role,
        "from_agent": message.from_agent,
        "to_agents": list(message.to_agents or ()),
        "parent_message_id": message.parent_message_id,
    }
    if extra:
        metadata.update(extra)
    return JsonlAgentRouteBus().record_worker_event(
        agent=agent,
        status=status,
        message_id=message.message_id,
        context_id=message.thread_id,
        task_id=message.task_id,
        transport="route_bus",
        metadata=metadata,
    )


def request_model_review_message(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationMessage:
    review_payload = {
        **payload,
        "role": "review_request",
        "from_agent": payload.get("from_agent") or payload.get("from_model") or payload.get("actor") or payload.get("owner") or "codex",
        "to_agents": payload.get("to_agents") or payload.get("to_agent") or payload.get("to_model") or payload.get("reviewer") or payload.get("agent") or "external_reviewer",
        "content": payload.get("content") or payload.get("summary") or "Please review the attached task/context pack.",
    }
    return post_collaboration_message(review_payload, root)


def mark_collaboration_message_read(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> CollaborationMessage:
    message_id = str(payload.get("message_id") or "").strip()
    reader = _normalize_agent_id(payload.get("reader") or payload.get("agent") or payload.get("model") or payload.get("actor"))
    if not message_id:
        raise ValueError("missing message_id")
    if not reader:
        raise ValueError("missing reader")
    current = next((message for message in load_collaboration_messages(root) if message.message_id == message_id), None)
    if current is None:
        raise ValueError(f"message not found: {message_id}")
    read_by = tuple(dict.fromkeys((*current.read_by, reader)))
    recipients = set(current.to_agents or _as_agent_tuple(current.to_model))
    fully_read = bool(recipients) and "all" not in recipients and recipients.issubset(set(read_by))
    updated = _message_from_dict(
        {
            **current.snapshot(),
            "status": "read" if fully_read else current.status,
            "read_by": list(read_by),
            "updated_at": time.time(),
        }
    )
    _append_jsonl(_jsonl_path(root, "messages.jsonl"), updated.snapshot())
    return updated


def list_collaboration_messages(payload: dict[str, Any] | None = None, root: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    payload = payload or {}
    task_id = str(payload.get("task_id") or "").strip()
    thread_id = str(payload.get("thread_id") or payload.get("conversation_id") or "").strip()
    to_agents = _as_agent_tuple(payload.get("to_agents") or payload.get("to_agent") or payload.get("to_model") or payload.get("model"))
    from_agent = _normalize_agent_id(payload.get("from_agent") or payload.get("from_model"))
    include_read = bool(payload.get("include_read", True))
    include_archived = bool(payload.get("include_archived", False))
    limit = max(1, min(200, int(payload.get("limit") or 80)))
    thread_states = load_collaboration_thread_states(root)
    hidden_threads = {
        item.thread_id
        for item in thread_states.values()
        if item.status == "deleted" or (item.status == "archived" and not include_archived)
    }
    messages = [
        message
        for message in load_collaboration_messages(root)
        if (message.thread_id or message.task_id) not in hidden_threads
    ]
    if task_id:
        messages = [message for message in messages if message.task_id == task_id]
    if thread_id:
        messages = [message for message in messages if (message.thread_id or message.task_id) == thread_id]
    if to_agents:
        to_agent_set = set(to_agents)
        messages = [
            message
            for message in messages
            if "all" in set(message.to_agents or _as_agent_tuple(message.to_model))
            or bool(to_agent_set.intersection(message.to_agents or _as_agent_tuple(message.to_model)))
        ]
    if from_agent:
        messages = [message for message in messages if (message.from_agent or message.from_model) == from_agent]
    if not include_read:
        if to_agents:
            readers = set(to_agents)
            messages = [message for message in messages if not readers.intersection(set(message.read_by))]
        else:
            messages = [message for message in messages if message.status != "read"]
    return [message.snapshot() for message in messages[-limit:]]


def list_collaboration_participants(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    registry = build_collaboration_participant_registry()
    kind = str(payload.get("kind") or "").strip().lower()
    include_unavailable = bool(payload.get("include_unavailable", True))
    participants = [dict(item) for item in registry.get("participants") or [] if isinstance(item, dict)]
    if kind:
        participants = [item for item in participants if str(item.get("kind") or "").strip().lower() == kind]
    if not include_unavailable:
        participants = [item for item in participants if str(item.get("status") or "").strip().lower() in {"ready", "online"}]
    return {
        **registry,
        "participants": participants,
        "filters": {
            "kind": kind,
            "include_unavailable": include_unavailable,
        },
    }


def list_agent_route_bus_messages(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    limit = max(1, min(200, int(payload.get("limit") or 100)))
    audit_limit = max(1, min(200, int(payload.get("audit_limit") or limit)))
    recipient = _normalize_agent_id(payload.get("recipient") or payload.get("to_agent") or payload.get("agent") or "")
    consumer = _normalize_agent_id(payload.get("consumer") or payload.get("reader") or payload.get("agent") or recipient)
    context_id = str(payload.get("context_id") or payload.get("thread_id") or payload.get("conversation_id") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    include_audit = bool(payload.get("include_audit", True))
    include_acked = bool(payload.get("include_acked", True))
    bus = JsonlAgentRouteBus()
    messages = bus.list_messages(
        recipient=recipient,
        context_id=context_id,
        task_id=task_id,
        consumer=consumer,
        include_acked=include_acked,
        limit=limit,
    )
    audit_events = bus.audit_events(limit=audit_limit) if include_audit else []
    ack_events = bus.ack_events(consumer=consumer, limit=audit_limit)
    return {
        "schema_version": "spiritkin.agent_route_bus.query.v1",
        "filters": {
            "recipient": recipient,
            "consumer": consumer,
            "context_id": context_id,
            "task_id": task_id,
            "limit": limit,
            "audit_limit": audit_limit,
            "include_audit": include_audit,
            "include_acked": include_acked,
        },
        "storage": {
            "root": str(bus.root),
            "messages": str(bus.messages_path),
            "route_audit": str(bus.audit_path),
            "message_acks": str(bus.ack_path),
        },
        "message_count": len(messages),
        "audit_count": len(audit_events),
        "ack_count": len(ack_events),
        "messages": [message.snapshot() for message in messages],
        "audit_events": audit_events,
        "ack_events": ack_events,
    }


def ack_agent_route_bus_message(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    bus = JsonlAgentRouteBus()
    ack = bus.ack_message(
        message_id=str(payload.get("message_id") or "").strip(),
        consumer=_normalize_agent_id(payload.get("consumer") or payload.get("reader") or payload.get("agent") or payload.get("to_agent")),
        note=str(payload.get("note") or ""),
    )
    return {
        "schema_version": "spiritkin.agent_route_bus.ack.v1",
        "acked": True,
        "ack": ack,
        "storage": {
            "root": str(bus.root),
            "message_acks": str(bus.ack_path),
        },
    }


def record_agent_route_bus_worker_event(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    bus = JsonlAgentRouteBus()
    event = bus.record_worker_event(
        agent=_normalize_agent_id(payload.get("agent") or payload.get("to_agent") or payload.get("consumer")),
        status=str(payload.get("status") or "").strip().lower(),
        message_id=str(payload.get("message_id") or "").strip(),
        context_id=str(payload.get("context_id") or payload.get("thread_id") or payload.get("conversation_id") or "").strip(),
        task_id=str(payload.get("task_id") or "").strip(),
        transport=str(payload.get("transport") or "route_bus").strip(),
        dry_run=bool(payload.get("dry_run", False)),
        error=str(payload.get("error") or "").strip(),
        metadata=dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
    )
    return {
        "schema_version": "spiritkin.agent_route_bus.worker_event.v1",
        "recorded": True,
        "event": event,
        "storage": {
            "root": str(bus.root),
            "worker_events": str(bus.worker_events_path),
        },
    }


def request_agent_tool_call(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    agent = _normalize_agent_id(payload.get("agent") or payload.get("from_agent") or payload.get("participant_id") or payload.get("requester"))
    target = str(payload.get("target") or payload.get("tool_target") or "").strip()
    operation = str(payload.get("operation") or payload.get("tool_operation") or "").strip()
    params = dict(payload.get("params") or payload.get("arguments") or {}) if isinstance(payload.get("params") or payload.get("arguments") or {}, dict) else {}
    params = _normalize_agent_tool_call_params(target, operation, params)
    if not agent:
        raise ValueError("missing agent")
    if not target:
        raise ValueError("missing target")
    if not operation:
        raise ValueError("missing operation")
    requires_review = _tool_call_requires_review(target, operation, payload)
    status = "permission_required" if requires_review else "approved"
    bus = JsonlAgentRouteBus()
    message_id = str(payload.get("message_id") or "").strip()
    context_id = str(payload.get("context_id") or payload.get("thread_id") or payload.get("conversation_id") or "").strip()
    existing = _matching_message_tool_call(
        bus,
        message_id=message_id,
        context_id=context_id,
        target=target,
        operation=operation,
        params=params,
    )
    if existing is not None:
        return {
            "schema_version": "spiritkin.agent_route_bus.tool_call.v1",
            "requested": False,
            "deduplicated": True,
            "tool_call": existing,
            "worker_event": {},
            "requires_review": bool(existing.get("requires_review", True)),
        }
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    metadata.update(
        {
            "thread_id": str(payload.get("thread_id") or payload.get("conversation_id") or "").strip(),
            "requested_by": agent,
            "source": "collaboration_tool_call",
        }
    )
    call = bus.record_tool_call(
        agent=agent,
        target=target,
        operation=operation,
        params=params,
        message_id=message_id,
        context_id=context_id,
        task_id=str(payload.get("task_id") or "").strip(),
        reason=str(payload.get("reason") or payload.get("summary") or "").strip(),
        status=status,
        requires_review=requires_review,
        metadata=metadata,
    )
    event = bus.record_worker_event(
        agent=agent,
        status="blocked" if requires_review else "running",
        message_id=message_id,
        context_id=call.get("context_id", ""),
        task_id=call.get("task_id", ""),
        transport="route_bus",
        metadata={
            "tool_call_id": call["tool_call_id"],
            "target": target,
            "operation": operation,
            "params": params,
            "lifecycle": "permission_required" if requires_review else "tool_requested",
            "requires_review": requires_review,
            "stream": "lifecycle",
            "output": f"Tool request {target}.{operation} is {'waiting for approval' if requires_review else 'queued for execution'}.",
        },
    )
    return {
        "schema_version": "spiritkin.agent_route_bus.tool_call.v1",
        "requested": True,
        "tool_call": call,
        "worker_event": event,
        "requires_review": requires_review,
    }


def _normalize_agent_tool_call_params(target: str, operation: str, params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params or {})
    if str(target or "").strip().lower() != "local_pc" or str(operation or "").strip().lower() != "launch_app":
        return normalized
    if str(normalized.get("app_name") or "").strip():
        return normalized
    candidate = normalized.get("app") or normalized.get("command") or normalized.get("application") or normalized.get("name")
    if isinstance(candidate, (list, tuple)):
        candidate = candidate[0] if candidate else ""
    if str(candidate or "").strip():
        normalized["app_name"] = str(candidate).strip()
    return normalized


def decide_agent_tool_call(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    tool_call_id = str(payload.get("tool_call_id") or payload.get("id") or "").strip()
    decision = str(payload.get("decision") or payload.get("status") or "").strip().lower()
    if decision in {"approve", "approved", "confirm", "confirmed", "allow", "allowed", "确认", "确认执行"}:
        status = "approved"
    elif decision in {"deny", "denied", "reject", "rejected", "cancel", "cancelled", "canceled", "取消", "取消执行"}:
        status = "denied"
    else:
        raise ValueError("unsupported tool_call decision")
    bus = JsonlAgentRouteBus()
    updated = bus.update_tool_call(
        tool_call_id=tool_call_id,
        status=status,
        actor=str(payload.get("actor") or payload.get("reviewer") or "human_desktop"),
        note=str(payload.get("note") or payload.get("reason") or ""),
    )
    event = bus.record_worker_event(
        agent=str(updated.get("agent") or payload.get("agent") or "agent"),
        status="running" if status == "approved" else "blocked",
        message_id=str(updated.get("message_id") or ""),
        context_id=str(updated.get("context_id") or ""),
        task_id=str(updated.get("task_id") or ""),
        transport="route_bus",
        metadata={
            "tool_call_id": tool_call_id,
            "target": updated.get("target", ""),
            "operation": updated.get("operation", ""),
            "params": dict(updated.get("params") or {}) if isinstance(updated.get("params"), dict) else {},
            "lifecycle": status,
            "stream": "lifecycle",
            "output": f"Tool call {tool_call_id} {status}.",
        },
    )
    return {
        "schema_version": "spiritkin.agent_route_bus.tool_call.v1",
        "decided": True,
        "tool_call": updated,
        "worker_event": event,
    }


def execute_agent_tool_call(payload: dict[str, Any] | None = None, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    payload = payload or {}
    bus = JsonlAgentRouteBus()
    tool_call_id = str(payload.get("tool_call_id") or payload.get("id") or "").strip()
    call = _latest_tool_call(tool_call_id)
    if call is None:
        if payload.get("target") and payload.get("operation"):
            requested = request_agent_tool_call({**payload, "requires_review": payload.get("requires_review", False)})
            call = requested["tool_call"]
            tool_call_id = str(call.get("tool_call_id") or "")
        else:
            raise ValueError(f"tool_call not found: {tool_call_id}")

    requires_review = bool(call.get("requires_review", True))
    current_status = str(call.get("status") or "").strip().lower()
    retry_requested = bool(payload.get("retry") or payload.get("force_retry"))
    if current_status == "running" or (
        current_status in {"completed", "failed", "blocked", "denied", "cancelled", "canceled"}
        and not retry_requested
    ):
        return {
            "schema_version": "spiritkin.agent_route_bus.tool_result.v1",
            "executed": False,
            "deduplicated": True,
            "status": current_status,
            "tool_call": call,
            "tool_result": _latest_tool_result(tool_call_id) or {},
            "worker_event": {},
        }
    if requires_review and current_status not in {"approved", "running"}:
        event = bus.record_worker_event(
            agent=str(call.get("agent") or "agent"),
            status="blocked",
            message_id=str(call.get("message_id") or ""),
            context_id=str(call.get("context_id") or ""),
            task_id=str(call.get("task_id") or ""),
            transport="route_bus",
            metadata={
                "tool_call_id": tool_call_id,
                "target": call.get("target", ""),
                "operation": call.get("operation", ""),
                "lifecycle": "permission_required",
                "stream": "lifecycle",
                "output": f"Tool call {tool_call_id} requires approval before execution.",
            },
        )
        return {
            "schema_version": "spiritkin.agent_route_bus.tool_result.v1",
            "executed": False,
            "status": "permission_required",
            "tool_call": call,
            "worker_event": event,
        }

    target = str(call.get("target") or "").strip()
    if _tool_call_target_requires_registered_worker(target):
        reason = f"No registered worker is available for {target}.{call.get('operation') or ''}."
        blocked = bus.update_tool_call(
            tool_call_id=tool_call_id,
            status="blocked",
            actor=str(payload.get("actor") or "worker_pool"),
            metadata={"blocked_reason": "worker_not_registered", "execution_status": "blocked"},
        )
        result = bus.record_tool_result(
            tool_call_id=tool_call_id,
            agent=str(blocked.get("agent") or "agent"),
            status="blocked",
            result={
                "result": {
                    "success": False,
                    "message": reason,
                    "data": None,
                    "error_code": "worker_not_registered",
                    "metadata": {"target": target, "operation": str(blocked.get("operation") or "")},
                },
                "worker": None,
                "audit_event": {
                    "status": "blocked",
                    "target": target,
                    "operation": str(blocked.get("operation") or ""),
                    "message": reason,
                    "error_code": "worker_not_registered",
                },
            },
            error=reason,
            metadata={"target": target, "operation": str(blocked.get("operation") or ""), "blocked_reason": "worker_not_registered"},
        )
        updated = bus.update_tool_call(
            tool_call_id=tool_call_id,
            status="blocked",
            actor=str(payload.get("actor") or "worker_pool"),
            metadata={"tool_result_id": result["tool_result_id"], "execution_status": "blocked"},
        )
        event = bus.record_worker_event(
            agent=str(updated.get("agent") or "agent"),
            status="blocked",
            message_id=str(updated.get("message_id") or ""),
            context_id=str(updated.get("context_id") or ""),
            task_id=str(updated.get("task_id") or ""),
            transport="route_bus",
            error=reason,
            metadata={
                "tool_call_id": tool_call_id,
                "tool_result_id": result["tool_result_id"],
                "target": target,
                "operation": str(updated.get("operation") or ""),
                "lifecycle": "tool_blocked",
                "stream": "lifecycle",
                "output": reason,
                "success": False,
                "blocked_reason": "worker_not_registered",
            },
        )
        reply = _post_tool_result_message(updated, result, reason, root)
        return {
            "schema_version": "spiritkin.agent_route_bus.tool_result.v1",
            "executed": False,
            "status": "blocked",
            "tool_call": updated,
            "tool_result": result,
            "worker_event": event,
            "reply_message": reply.snapshot() if reply is not None else {},
        }

    running = bus.update_tool_call(tool_call_id=tool_call_id, status="running", actor=str(payload.get("actor") or "human_desktop"))
    bus.record_worker_event(
        agent=str(running.get("agent") or "agent"),
        status="running",
        message_id=str(running.get("message_id") or ""),
        context_id=str(running.get("context_id") or ""),
        task_id=str(running.get("task_id") or ""),
        transport="route_bus",
        metadata={
            "tool_call_id": tool_call_id,
            "target": running.get("target", ""),
            "operation": running.get("operation", ""),
            "lifecycle": "tool_running",
            "stream": "lifecycle",
            "output": f"Executing {running.get('target')}.{running.get('operation')}.",
        },
    )
    request = ExecutionRequest(str(running.get("target") or ""), str(running.get("operation") or ""), dict(running.get("params") or {}))
    execution = WorkerPool([LocalPCExecutor()]).execute(
        request,
        actor=str(running.get("agent") or payload.get("actor") or "collaboration_agent"),
        dry_run=bool(payload.get("dry_run", False)),
        metadata={"tool_call_id": tool_call_id, "source": "collaboration"},
    )
    result_payload = execution.snapshot()
    status = "completed" if execution.result.success else "failed"
    result = bus.record_tool_result(
        tool_call_id=tool_call_id,
        agent=str(running.get("agent") or "agent"),
        status=status,
        result=result_payload,
        error="" if execution.result.success else execution.result.message,
        metadata={"target": request.target, "operation": request.operation},
    )
    updated = bus.update_tool_call(
        tool_call_id=tool_call_id,
        status=status,
        actor=str(payload.get("actor") or "worker_pool"),
        metadata={"tool_result_id": result["tool_result_id"], "execution_status": status},
    )
    event = bus.record_worker_event(
        agent=str(running.get("agent") or "agent"),
        status="completed" if execution.result.success else "failed",
        message_id=str(running.get("message_id") or ""),
        context_id=str(running.get("context_id") or ""),
        task_id=str(running.get("task_id") or ""),
        transport="route_bus",
        error="" if execution.result.success else execution.result.message,
        metadata={
            "tool_call_id": tool_call_id,
            "tool_result_id": result["tool_result_id"],
            "target": request.target,
            "operation": request.operation,
            "lifecycle": "tool_completed" if execution.result.success else "tool_failed",
            "stream": "lifecycle",
            "output": execution.result.message,
            "success": execution.result.success,
        },
    )
    reply = _post_tool_result_message(updated, result, execution.result.message, root)
    return {
        "schema_version": "spiritkin.agent_route_bus.tool_result.v1",
        "executed": True,
        "status": status,
        "tool_call": updated,
        "tool_result": result,
        "worker_event": event,
        "reply_message": reply.snapshot() if reply is not None else {},
        "execution": result_payload,
    }


def _tool_call_target_requires_registered_worker(target: str) -> bool:
    normalized = str(target or "").strip().lower().replace("-", "_")
    return normalized in {"remote", "remote_pc", "remote_worker", "android", "android_device", "mobile", "mobile_device", "ios", "ios_terminal"}


def list_agent_tool_calls(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    bus = JsonlAgentRouteBus()
    limit = max(1, min(500, int(payload.get("limit") or 100)))
    calls = _dedupe_latest_tool_calls(
        bus.tool_calls(
            agent=_normalize_agent_id(payload.get("agent") or payload.get("from_agent") or ""),
            status=str(payload.get("status") or "").strip().lower(),
            context_id=str(payload.get("context_id") or payload.get("thread_id") or payload.get("conversation_id") or "").strip(),
            task_id=str(payload.get("task_id") or "").strip(),
            limit=10000,
        )
    )[-limit:]
    results: list[dict[str, Any]] = []
    if bool(payload.get("include_results", True)):
        result_by_call = {str(call.get("tool_call_id") or "") for call in calls}
        results = [item for item in bus.tool_results(limit=10000) if str(item.get("tool_call_id") or "") in result_by_call][-limit:]
    return {
        "schema_version": "spiritkin.agent_route_bus.tool_call.v1",
        "tool_call_count": len(calls),
        "tool_result_count": len(results),
        "tool_calls": calls,
        "tool_results": results,
        "storage": {
            "root": str(bus.root),
            "tool_calls": str(bus.tool_calls_path),
            "tool_results": str(bus.tool_results_path),
        },
    }


def _latest_tool_call(tool_call_id: str) -> dict[str, Any] | None:
    target_id = str(tool_call_id or "").strip()
    if not target_id:
        return None
    for item in reversed(JsonlAgentRouteBus().tool_calls(limit=10000)):
        if str(item.get("tool_call_id") or "") == target_id:
            return dict(item)
    return None


def _latest_tool_result(tool_call_id: str) -> dict[str, Any] | None:
    target_id = str(tool_call_id or "").strip()
    if not target_id:
        return None
    for item in reversed(JsonlAgentRouteBus().tool_results(limit=10000)):
        if str(item.get("tool_call_id") or "") == target_id:
            return dict(item)
    return None


def _matching_message_tool_call(
    bus: JsonlAgentRouteBus,
    *,
    message_id: str,
    context_id: str,
    target: str,
    operation: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the existing logical call shared by collaborators for one human message."""

    if not message_id:
        return None
    normalized_target = str(target or "").strip().lower().replace("-", "_")
    normalized_operation = str(operation or "").strip().lower()
    canonical_params = json.dumps(params or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    calls = _dedupe_latest_tool_calls(bus.tool_calls(context_id=context_id, limit=10000))
    for call in reversed(calls):
        if str(call.get("message_id") or "").strip() != message_id:
            continue
        call_target = str(call.get("target") or "").strip().lower().replace("-", "_")
        call_operation = str(call.get("operation") or "").strip().lower()
        call_params = json.dumps(dict(call.get("params") or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if call_target == normalized_target and call_operation == normalized_operation and call_params == canonical_params:
            return call
    return None


def _dedupe_latest_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for call in calls:
        call_id = str(call.get("tool_call_id") or "")
        if call_id:
            latest[call_id] = dict(call)
    return sorted(latest.values(), key=lambda item: (float(item.get("updated_at") or item.get("created_at") or 0.0), str(item.get("tool_call_id") or "")))


def _tool_call_requires_review(target: str, operation: str, payload: dict[str, Any]) -> bool:
    if "requires_review" in payload:
        return bool(payload.get("requires_review"))
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    permission_mode = str(payload.get("permission_mode") or metadata.get("permission_mode") or "").strip().lower()
    granted_value = payload.get("full_access_granted", metadata.get("full_access_granted", False))
    full_access_granted = granted_value is True or str(granted_value).strip().lower() in {"1", "true", "yes", "on"}
    if permission_mode == "full_access" and full_access_granted and str(payload.get("client_id") or metadata.get("client_id") or "").strip() in {"127.0.0.1", "::1", "localhost", "desktop", "wpf_desktop"}:
        return False
    normalized_target = str(target or "").strip().lower().replace("-", "_")
    normalized_operation = str(operation or "").strip().lower()
    read_only_operations = {
        "browser_tab_list",
        "clipboard_read",
        "file_read",
        "file_search",
        "list_hardware_devices",
        "list_installed_apps",
        "screen_capture",
        "screen_extract_text",
        "screen_understand",
        "window_list",
    }
    if normalized_target in {"remote", "remote_pc", "android", "android_device", "mobile", "local_pc", "desktop", "browser", "screen", "window", "file", "clipboard"}:
        return normalized_operation not in read_only_operations
    return True


def _post_tool_result_message(
    tool_call: dict[str, Any],
    tool_result: dict[str, Any],
    summary: str,
    root: str | os.PathLike[str] | None,
) -> CollaborationMessage | None:
    agent = _normalize_agent_id(tool_call.get("agent") or "agent")
    status = str(tool_result.get("status") or "unknown")
    summary_text = str(summary or "").strip()
    if not summary_text:
        summary_text = f"Tool call {tool_call.get('target')}.{tool_call.get('operation')} finished with status {status}."
    execution_snapshot = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
    execution_result = execution_snapshot.get("result") if isinstance(execution_snapshot.get("result"), dict) else {}
    result_data = execution_result.get("data")
    result_text = ""
    if result_data not in (None, "", [], {}):
        try:
            result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            result_text = str(result_data)
        if len(result_text) > 6000:
            result_text = result_text[:6000] + "\n... (tool result truncated)"
    content_lines = [
        f"Tool result: {tool_call.get('target')}.{tool_call.get('operation')}",
        f"Tool call id: {tool_call.get('tool_call_id')}",
        f"Status: {status}",
        f"Summary: {summary_text}",
    ]
    if result_text:
        content_lines.extend(["Result data:", result_text])
    content = "\n".join(content_lines)
    try:
        return post_collaboration_message(
            {
                "task_id": str(tool_call.get("task_id") or ""),
                "thread_id": str(tool_call.get("context_id") or tool_call.get("task_id") or ""),
                "from_agent": f"executor_{_normalize_agent_id(tool_call.get('target') or 'worker')}",
                "to_agents": [agent, "human_desktop"],
                "role": "event",
                "content": content,
                "parent_message_id": str(tool_call.get("message_id") or ""),
                "status": "open",
            },
            root,
        )
    except Exception:
        return None


def _default_route_bus_worker_agents() -> tuple[str, ...]:
    registry = build_collaboration_participant_registry()
    agents = [
        str(item.get("participant_id") or "")
        for item in registry.get("participants") or []
        if isinstance(item, dict)
        and bool(item.get("can_chat"))
        and str(item.get("status") or "").strip().lower() in {"ready", "online"}
        and str(item.get("kind") or "").strip().lower() in {"external_cli", "model_api"}
    ]
    return tuple(dict.fromkeys([*DEFAULT_ROUTE_BUS_WORKER_AGENTS, *[agent for agent in agents if agent]]))


def _allowed_collaboration_recipients() -> tuple[str, ...]:
    registry = build_collaboration_participant_registry()
    participant_ids = [
        str(item.get("participant_id") or "")
        for item in registry.get("participants") or []
        if isinstance(item, dict) and str(item.get("participant_id") or "")
    ]
    return tuple(dict.fromkeys(["human_desktop", "external_reviewer", "all", *participant_ids]))


def build_agent_route_bus_worker_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    requested_agents = _as_agent_tuple(payload.get("agents") or payload.get("agent") or payload.get("to_agent"))
    agents = requested_agents or _default_route_bus_worker_agents()
    context_id = str(payload.get("context_id") or payload.get("thread_id") or payload.get("conversation_id") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    limit = max(1, min(500, int(payload.get("limit") or 200)))
    bus = JsonlAgentRouteBus()
    config_status = build_collaboration_worker_config_status({"agents": agents})
    config_by_agent = {
        str(item.get("agent") or ""): item
        for item in config_status.get("agents") or []
        if isinstance(item, dict)
    }
    agent_statuses: list[dict[str, Any]] = []
    total_pending = 0
    total_ack_count = 0
    worker_events = bus.worker_events(context_id=context_id, task_id=task_id, limit=20)
    latest_event_by_agent: dict[str, dict[str, Any]] = {}
    for event in worker_events:
        agent_id = _normalize_agent_id(event.get("agent"))
        if agent_id:
            latest_event_by_agent[agent_id] = dict(event)
    for agent in agents:
        pending_messages = bus.list_messages(
            recipient=agent,
            context_id=context_id,
            task_id=task_id,
            consumer=agent,
            include_acked=False,
            limit=limit,
        )
        ack_events = bus.ack_events(consumer=agent, limit=limit)
        total_pending += len(pending_messages)
        total_ack_count += len(ack_events)
        external_worker = config_by_agent.get(agent, {})
        agent_statuses.append(
            {
                "agent": agent,
                "worker_mode": "dry_run_only",
                "real_worker_status": "ready" if external_worker.get("can_start_real_worker") else "not_enabled",
                "available_actions": ["list", "ack", "dry_run_once", "external_cli_worker"],
                "pending_count": len(pending_messages),
                "ack_count": len(ack_events),
                "latest_pending_message": pending_messages[-1].snapshot() if pending_messages else {},
                "latest_worker_event": latest_event_by_agent.get(agent, {}),
                "external_worker": external_worker,
            }
        )
    ready_real_workers = [item for item in agent_statuses if item.get("real_worker_status") == "ready"]
    return {
        "schema_version": "spiritkin.agent_route_bus.worker_status.v1",
        "generated_at": time.time(),
        "mode": "dry_run_only",
        "real_worker_status": "ready" if ready_real_workers else "not_enabled",
        "dry_run_available": True,
        "external_cli_worker_available": bool(ready_real_workers),
        "supported_actions": [
            "list_agent_route_bus_messages",
            "ack_agent_route_bus_message",
            "run_participant_once",
            "run_agent_route_bus_worker_once",
            "agent_route_bus_worker_status",
        ],
        "filters": {
            "agents": list(agents),
            "context_id": context_id,
            "task_id": task_id,
            "limit": limit,
        },
        "pending_count": total_pending,
        "ack_count": total_ack_count,
        "worker_event_count": len(worker_events),
        "recent_worker_events": worker_events,
        "external_worker_config": config_status,
        "agents": agent_statuses,
        "storage": {
            "root": str(bus.root),
            "messages": str(bus.messages_path),
            "route_audit": str(bus.audit_path),
            "message_acks": str(bus.ack_path),
            "worker_events": str(bus.worker_events_path),
        },
    }


def run_agent_route_bus_worker_once(payload: dict[str, Any] | None = None, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    payload = payload or {}
    agent = _normalize_agent_id(payload.get("agent") or payload.get("to_agent") or payload.get("consumer"))
    if not agent:
        raise ValueError("missing agent")
    dry_run = bool(payload.get("dry_run", True))
    if not dry_run:
        event = JsonlAgentRouteBus().record_worker_event(
            agent=agent,
            status="real_worker_not_enabled",
            context_id=str(payload.get("context_id") or payload.get("thread_id") or payload.get("conversation_id") or "").strip(),
            task_id=str(payload.get("task_id") or "").strip(),
            transport="route_bus",
            dry_run=False,
            error="Route bus worker action is dry-run only until a governed external assistant executor is wired.",
        )
        return {
            "schema_version": "spiritkin.agent_route_bus.worker.v1",
            "ok": False,
            "status": "real_worker_not_enabled",
            "agent": agent,
            "worker_event": event,
            "message": "Route bus worker action is dry-run only until a governed external assistant executor is wired.",
        }
    context_id = str(payload.get("context_id") or payload.get("thread_id") or payload.get("conversation_id") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    bus = JsonlAgentRouteBus()
    messages = bus.list_messages(
        recipient=agent,
        context_id=context_id,
        task_id=task_id,
        consumer=agent,
        include_acked=False,
        limit=1,
    )
    if not messages:
        event = bus.record_worker_event(
            agent=agent,
            status="idle",
            context_id=context_id,
            task_id=task_id,
            transport="route_bus",
            dry_run=True,
        )
        return {
            "schema_version": "spiritkin.agent_route_bus.worker.v1",
            "ok": True,
            "status": "idle",
            "agent": agent,
            "processed": False,
            "worker_event": event,
            "message": "No pending route bus message for this Agent.",
        }
    message = messages[-1]
    should_ack = bool(payload.get("ack", True))
    should_post_answer = bool(payload.get("post_answer", False))
    ack = bus.ack_message(message_id=message.message_id, consumer=agent, note="dry_run_worker_consumed") if should_ack else {}
    answer: dict[str, Any] = {}
    if should_post_answer:
        answer = post_collaboration_message(
            {
                "thread_id": message.context_id,
                "task_id": message.task_id,
                "from_agent": agent,
                "to_agent": message.sender,
                "role": "answer",
                "content": f"[dry-run:{agent}] Received route bus message {message.message_id}.",
                "parent_message_id": message.message_id,
            },
            root=root,
        ).snapshot()
    event = bus.record_worker_event(
        agent=agent,
        status="processed",
        message_id=message.message_id,
        context_id=message.context_id,
        task_id=message.task_id,
        transport="route_bus",
        dry_run=True,
        metadata={"acked": bool(ack), "posted_answer": bool(answer)},
    )
    return {
        "schema_version": "spiritkin.agent_route_bus.worker.v1",
        "ok": True,
        "status": "processed",
        "agent": agent,
        "processed": True,
        "dry_run": True,
        "message": message.snapshot(),
        "ack": ack,
        "answer": answer,
        "worker_event": event,
    }


def claim_collaboration_files(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    owner = str(payload.get("owner") or payload.get("model") or "").strip()
    patterns = _as_tuple(payload.get("patterns") or payload.get("files"))
    if not owner:
        raise ValueError("missing owner")
    if not patterns:
        raise ValueError("missing patterns")
    claims_state = load_file_claims(root)
    claims = [dict(item) for item in claims_state.get("claims") or [] if isinstance(item, dict)]
    now = time.time()
    claim = {
        "claim_id": str(payload.get("claim_id") or _new_id("claim")),
        "owner": owner,
        "task_id": str(payload.get("task_id") or ""),
        "patterns": list(patterns),
        "status": str(payload.get("status") or "active").strip().lower(),
        "note": str(payload.get("note") or ""),
        "created_at": now,
        "updated_at": now,
    }
    claims.append(claim)
    state = {"schema_version": SCHEMA_VERSION, "updated_at": now, "claims": claims}
    _write_json(_jsonl_path(root, "file_claims.json"), state)
    return claim


def build_context_pack(payload: dict[str, Any] | None = None, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    payload = payload or {}
    collab_root = resolve_collaboration_root(root)
    task_id = str(payload.get("task_id") or "").strip()
    task = next((item for item in load_collaboration_tasks(root) if item.task_id == task_id), None) if task_id else None
    pack_id = str(payload.get("pack_id") or _new_id("context-pack"))
    include_files = _as_tuple(payload.get("include_files"))
    max_chars = max(500, int(payload.get("max_chars_per_file") or 2400))
    overview_path = Path(str(payload.get("project_overview_path") or os.getenv("SPIRITKIN_PROJECT_OVERVIEW_PATH", DEFAULT_PROJECT_OVERVIEW_PATH)))
    if not overview_path.is_absolute():
        overview_path = Path.cwd() / overview_path
    overview_text = _read_file_preview(overview_path, max_chars=max(max_chars, 6000))
    file_previews = [
        {"path": str(path), "text_preview": _read_file_preview(Path(path), max_chars=max_chars)}
        for path in include_files
    ]
    pack = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": pack_id,
        "generated_at": time.time(),
        "task": task.snapshot() if task else None,
        "project_overview": {
            "path": str(overview_path),
            "text_preview": overview_text,
        },
        "active_tasks": [item.snapshot() for item in active_collaboration_tasks(root)],
        "file_claims": active_file_claims(root),
        "recent_decisions": [item.snapshot() for item in load_collaboration_decisions(root)[-12:]],
        "recent_reviews": [item.snapshot() for item in load_collaboration_reviews(root)[-12:]],
        "recent_messages": [item.snapshot() for item in load_collaboration_messages(root)[-20:]],
        "file_previews": file_previews,
        "instructions": [
            "Read project_overview first.",
            "Respect file_claims and task blocked_files.",
            "Default to proposal/review-only unless explicitly assigned write access.",
            "Return verification commands and affected files with any proposed change.",
        ],
    }
    pack_path = collab_root / "context_packs" / f"{pack_id}.json"
    _write_json(pack_path, pack)
    return {"pack_path": str(pack_path), **pack}


def build_collaboration_snapshot(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    tasks = load_collaboration_tasks(root)
    decisions = load_collaboration_decisions(root)
    reviews = load_collaboration_reviews(root)
    thread_states = load_collaboration_thread_states(root)
    deleted_threads = {
        thread_id
        for thread_id, state in thread_states.items()
        if state.status == "deleted"
    }
    messages = [
        message
        for message in load_collaboration_messages(root)
        if (message.thread_id or message.task_id) not in deleted_threads
    ]
    claims = active_file_claims(root)
    active_tasks = active_collaboration_tasks(root)
    unread_messages = [message for message in messages if message.status != "read"]
    route_bus = JsonlAgentRouteBus().snapshot(message_limit=30, audit_limit=30)
    route_bus_worker = build_agent_route_bus_worker_status()
    turn_guard = turn_guard_snapshot("", root=root)
    participants = build_collaboration_participant_registry()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "root": str(resolve_collaboration_root(root)),
        "participants": participants,
        "overview": {
            "task_count": len(tasks),
            "active_task_count": len(active_tasks),
            "decision_count": len(decisions),
            "review_count": len(reviews),
            "message_count": len(messages),
            "unread_message_count": len(unread_messages),
            "active_file_claim_count": len(claims),
            "recommended_surface": "separate_collaboration_page",
            "project_overview_role": "source_of_truth_and_summary",
            "collaboration_page_role": "tasks_file_claims_context_packs_reviews",
        },
        "thread_states": {key: value.snapshot() for key, value in sorted(thread_states.items())},
        "active_tasks": [task.snapshot() for task in active_tasks],
        "tasks": [task.snapshot() for task in tasks[-80:]],
        "file_claims": claims,
        "recent_decisions": [decision.snapshot() for decision in decisions[-30:]],
        "recent_reviews": [review.snapshot() for review in reviews[-30:]],
        "recent_messages": [message.snapshot() for message in messages[-80:]],
        "agent_route_bus": _collaboration_route_bus_snapshot(route_bus),
        "agent_route_bus_worker": route_bus_worker,
        "turn_guard": turn_guard,
        "source_files": {
            "tasks": str(_jsonl_path(root, "tasks.jsonl")),
            "decisions": str(_jsonl_path(root, "decisions.jsonl")),
            "reviews": str(_jsonl_path(root, "reviews.jsonl")),
            "messages": str(_jsonl_path(root, "messages.jsonl")),
            "threads": str(_jsonl_path(root, "threads.json")),
            "file_claims": str(_jsonl_path(root, "file_claims.json")),
            "context_packs": str(resolve_collaboration_root(root) / "context_packs"),
        },
    }


def _collaboration_route_bus_snapshot(route_bus: dict[str, Any]) -> dict[str, Any]:
    storage = route_bus.get("storage") if isinstance(route_bus.get("storage"), dict) else {}
    messages = [item for item in route_bus.get("messages") or [] if isinstance(item, dict)]
    audit_events = [item for item in route_bus.get("audit_events") or [] if isinstance(item, dict)]
    ack_events = [item for item in route_bus.get("ack_events") or [] if isinstance(item, dict)]
    worker_events = [item for item in route_bus.get("worker_events") or [] if isinstance(item, dict)]
    tool_calls = _dedupe_latest_tool_calls([item for item in route_bus.get("tool_calls") or [] if isinstance(item, dict)])
    tool_results = [item for item in route_bus.get("tool_results") or [] if isinstance(item, dict)]
    return {
        "schema_version": route_bus.get("schema_version", "spiritkin.agent_protocol.v1"),
        "storage": dict(storage),
        "total": int(route_bus.get("total") or len(audit_events)),
        "routed": int(route_bus.get("routed") or len(messages)),
        "blocked": int(route_bus.get("blocked") or sum(1 for event in audit_events if not event.get("allowed"))),
        "ack_count": int(route_bus.get("ack_count") or len(ack_events)),
        "worker_event_count": int(route_bus.get("worker_event_count") or len(worker_events)),
        "tool_call_count": int(route_bus.get("tool_call_count") or len(tool_calls)),
        "tool_result_count": int(route_bus.get("tool_result_count") or len(tool_results)),
        "recent_messages": messages[-10:],
        "recent_audit_events": audit_events[-10:],
        "recent_ack_events": ack_events[-10:],
        "recent_worker_events": worker_events[-10:],
        "recent_tool_calls": tool_calls[-10:],
        "recent_tool_results": tool_results[-10:],
    }


def handle_collaboration_action(payload: dict[str, Any], root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    result: dict[str, Any] = {"ok": True}
    if action in {"snapshot", "refresh"}:
        return {"ok": True, "collaboration": build_collaboration_snapshot(root)}
    if action in {"list_participants", "participants", "participant_registry"}:
        result["participants"] = list_collaboration_participants(payload)
    elif action in {"create_task", "add_task"}:
        result["task"] = create_collaboration_task(payload, root).snapshot()
    elif action in {"update_task", "set_task_status"}:
        result["task"] = update_collaboration_task(payload, root).snapshot()
    elif action in {"record_decision", "add_decision"}:
        result["decision"] = record_collaboration_decision(payload, root).snapshot()
    elif action in {"record_review", "add_review"}:
        result["review"] = record_collaboration_review(payload, root).snapshot()
    elif action in {"post_message", "send_message", "add_message"}:
        result["message"] = post_collaboration_message(payload, root).snapshot()
    elif action in {"request_model_review", "request_review_message"}:
        result["message"] = request_model_review_message(payload, root).snapshot()
    elif action in {"mark_message_read", "read_message"}:
        result["message"] = mark_collaboration_message_read(payload, root).snapshot()
    elif action in {"archive_thread", "restore_thread", "unarchive_thread", "delete_thread", "set_thread_status"}:
        result["thread"] = set_collaboration_thread_status(payload, root).snapshot()
    elif action in {"list_messages", "messages"}:
        result["messages"] = list_collaboration_messages(payload, root)
    elif action in {"list_agent_route_bus_messages", "agent_route_bus_messages", "route_bus_messages"}:
        result["agent_route_bus_messages"] = list_agent_route_bus_messages(payload)
    elif action in {"ack_agent_route_bus_message", "ack_route_bus_message", "ack_agent_message"}:
        result["agent_route_bus_ack"] = ack_agent_route_bus_message(payload)
    elif action in {"record_agent_route_bus_worker_event", "record_route_bus_worker_event"}:
        result["agent_route_bus_worker_event"] = record_agent_route_bus_worker_event(payload)
    elif action in {"request_tool_call", "request_agent_tool_call"}:
        result["agent_route_bus_tool_call"] = request_agent_tool_call(payload)
        if isinstance(result["agent_route_bus_tool_call"].get("worker_event"), dict):
            result["agent_route_bus_worker_event"] = {"event": result["agent_route_bus_tool_call"]["worker_event"]}
    elif action in {"decide_tool_call", "decide_agent_tool_call"}:
        result["agent_route_bus_tool_call"] = decide_agent_tool_call(payload)
        if isinstance(result["agent_route_bus_tool_call"].get("worker_event"), dict):
            result["agent_route_bus_worker_event"] = {"event": result["agent_route_bus_tool_call"]["worker_event"]}
        decided_call = result["agent_route_bus_tool_call"].get("tool_call")
        if (
            isinstance(decided_call, dict)
            and str(decided_call.get("status") or "").strip().lower() == "approved"
            and bool(payload.get("execute_on_approve", True))
        ):
            execution_payload = {
                "tool_call_id": str(decided_call.get("tool_call_id") or ""),
                "actor": str(payload.get("actor") or payload.get("reviewer") or "human_desktop"),
                "dry_run": bool(payload.get("dry_run", False)),
            }
            result["agent_route_bus_tool_result"] = execute_agent_tool_call(execution_payload, root)
            if isinstance(result["agent_route_bus_tool_result"].get("worker_event"), dict):
                result["agent_route_bus_worker_event"] = {
                    "event": result["agent_route_bus_tool_result"]["worker_event"]
                }
    elif action in {"execute_tool_call", "execute_agent_tool_call"}:
        result["agent_route_bus_tool_result"] = execute_agent_tool_call(payload, root)
        if isinstance(result["agent_route_bus_tool_result"].get("worker_event"), dict):
            result["agent_route_bus_worker_event"] = {"event": result["agent_route_bus_tool_result"]["worker_event"]}
    elif action in {"list_tool_calls", "agent_tool_calls", "list_agent_tool_calls"}:
        result["agent_route_bus_tool_calls"] = list_agent_tool_calls(payload)
    elif action in {"agent_route_bus_worker_status", "route_bus_worker_status", "dry_run_route_bus_worker_status"}:
        result["agent_route_bus_worker_status"] = build_agent_route_bus_worker_status(payload)
    elif action in {"run_participant_once", "run_collaboration_participant_once", "run_agent_route_bus_worker_once", "route_bus_worker_once", "dry_run_route_bus_worker"}:
        result["agent_route_bus_worker"] = run_agent_route_bus_worker_once(payload, root)
        result["participant_run"] = result["agent_route_bus_worker"]
    elif action in {"claim_files", "claim_file"}:
        result["file_claim"] = claim_collaboration_files(payload, root)
    elif action in {"build_context_pack", "context_pack"}:
        result["context_pack"] = build_context_pack(payload, root)
    elif action in {"refill_turns", "refill_collaboration_turns", "turn_refill"}:
        result["turn_guard"] = refill_collaboration_turns(payload, root)
    elif action in {"set_thread_turn_cap", "set_collaboration_thread_turn_cap", "turn_cap_set"}:
        result["turn_guard"] = set_collaboration_thread_turn_cap(payload, root)
    elif action in {"pause_turns", "pause_collaboration_turns", "turn_pause"}:
        result["turn_guard"] = pause_collaboration_turns(payload, root)
    elif action in {"reset_turns", "reset_collaboration_turns", "turn_reset"}:
        result["turn_guard"] = reset_collaboration_turns(payload, root)
    elif action in {"turn_guard_status", "collaboration_turn_guard_status", "turn_status"}:
        result["turn_guard"] = collaboration_turn_guard_status(payload, root)
    elif action in {"collaboration_auto_reply", "auto_reply", "get_auto_reply", "set_auto_reply"}:
        op = str(payload.get("op") or ("set" if "enabled" in payload else "get")).strip().lower()
        if op == "set":
            result["auto_reply"] = set_collaboration_auto_reply(bool(payload.get("enabled")), root)
        else:
            result["auto_reply"] = get_collaboration_auto_reply_state(root)
    else:
        raise ValueError(f"unsupported collaboration action: {action}")
    if _collaboration_action_needs_snapshot(action, payload):
        result["collaboration"] = build_collaboration_snapshot(root)
    return result


def _collaboration_action_needs_snapshot(action: str, payload: dict[str, Any]) -> bool:
    if payload.get("include_collaboration") is True:
        return True
    if payload.get("include_collaboration") is False:
        return False
    lightweight_actions = {
        "list_participants",
        "participants",
        "participant_registry",
        "list_messages",
        "messages",
        "list_agent_route_bus_messages",
        "agent_route_bus_messages",
        "route_bus_messages",
        "ack_agent_route_bus_message",
        "ack_route_bus_message",
        "ack_agent_message",
        "record_agent_route_bus_worker_event",
        "record_route_bus_worker_event",
        "request_tool_call",
        "request_agent_tool_call",
        "decide_tool_call",
        "decide_agent_tool_call",
        "execute_tool_call",
        "execute_agent_tool_call",
        "list_tool_calls",
        "agent_tool_calls",
        "list_agent_tool_calls",
        "agent_route_bus_worker_status",
        "route_bus_worker_status",
        "dry_run_route_bus_worker_status",
        "run_participant_once",
        "run_collaboration_participant_once",
        "run_agent_route_bus_worker_once",
        "route_bus_worker_once",
        "dry_run_route_bus_worker",
        "set_thread_turn_cap",
        "set_collaboration_thread_turn_cap",
        "turn_cap_set",
        "pause_turns",
        "pause_collaboration_turns",
        "turn_pause",
        "turn_guard_status",
        "collaboration_turn_guard_status",
        "turn_status",
        "collaboration_auto_reply",
        "auto_reply",
        "get_auto_reply",
        "set_auto_reply",
    }
    return action not in lightweight_actions


def active_collaboration_tasks(root: str | os.PathLike[str] | None = None) -> list[CollaborationTask]:
    inactive = {"done", "complete", "closed", "cancelled", "canceled", "rejected"}
    return [task for task in load_collaboration_tasks(root) if task.status not in inactive]


def active_file_claims(root: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    state = load_file_claims(root)
    return [dict(item) for item in state.get("claims") or [] if isinstance(item, dict) and str(item.get("status") or "active") == "active"]


def file_claim_conflicts(path: str, root: str | os.PathLike[str] | None = None, *, actor: str = "") -> list[dict[str, Any]]:
    normalized = path.replace("\\", "/")
    conflicts: list[dict[str, Any]] = []
    for claim in active_file_claims(root):
        if actor and str(claim.get("owner") or "") == actor:
            continue
        for pattern in claim.get("patterns") or []:
            if fnmatch.fnmatch(normalized, str(pattern).replace("\\", "/")):
                conflicts.append(claim)
                break
    return conflicts


def _read_file_preview(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:max_chars]


def _task_from_dict(data: dict[str, Any]) -> CollaborationTask:
    return CollaborationTask(
        task_id=str(data.get("task_id") or _new_id("collab-task")),
        title=str(data.get("title") or "Untitled collaboration task"),
        owner=str(data.get("owner") or "unassigned"),
        status=str(data.get("status") or "planned").strip().lower(),
        scope=_as_tuple(data.get("scope")),
        allowed_files=_as_tuple(data.get("allowed_files")),
        blocked_files=_as_tuple(data.get("blocked_files")),
        verification_commands=_as_tuple(data.get("verification_commands")),
        note=str(data.get("note") or ""),
        created_at=float(data.get("created_at") or time.time()),
        updated_at=float(data.get("updated_at") or data.get("created_at") or time.time()),
    )


def _decision_from_dict(data: dict[str, Any]) -> CollaborationDecision:
    return CollaborationDecision(
        decision_id=str(data.get("decision_id") or _new_id("decision")),
        task_id=str(data.get("task_id") or ""),
        title=str(data.get("title") or "Untitled decision"),
        decision=str(data.get("decision") or ""),
        rationale=str(data.get("rationale") or ""),
        actor=str(data.get("actor") or "unknown"),
        created_at=float(data.get("created_at") or time.time()),
    )


def _review_from_dict(data: dict[str, Any]) -> CollaborationReview:
    return CollaborationReview(
        review_id=str(data.get("review_id") or _new_id("review")),
        task_id=str(data.get("task_id") or ""),
        reviewer=str(data.get("reviewer") or "unknown"),
        verdict=str(data.get("verdict") or "comment").strip().lower(),
        summary=str(data.get("summary") or ""),
        evidence=_as_tuple(data.get("evidence")),
        created_at=float(data.get("created_at") or time.time()),
    )


def _message_from_dict(data: dict[str, Any]) -> CollaborationMessage:
    from_agent = _normalize_agent_id(data.get("from_agent") or data.get("from_model") or "unknown")
    to_agents = _as_agent_tuple(data.get("to_agents") or data.get("to_model") or "all")
    envelope = data.get("agent_envelope") if isinstance(data.get("agent_envelope"), dict) else {}
    envelope_metadata = envelope.get("metadata") if isinstance(envelope.get("metadata"), dict) else {}
    metadata = dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), dict) else dict(envelope_metadata)
    return CollaborationMessage(
        message_id=str(data.get("message_id") or _new_id("message")),
        task_id=str(data.get("task_id") or ""),
        from_model=str(data.get("from_model") or from_agent or "unknown"),
        to_model=str(data.get("to_model") or ",".join(to_agents) or "all"),
        role=str(data.get("role") or "note").strip().lower(),
        content=str(data.get("content") or ""),
        thread_id=str(data.get("thread_id") or data.get("conversation_id") or data.get("task_id") or ""),
        from_agent=from_agent,
        to_agents=to_agents or ("all",),
        context_pack_path=str(data.get("context_pack_path") or ""),
        parent_message_id=str(data.get("parent_message_id") or ""),
        status=str(data.get("status") or "open").strip().lower(),
        read_by=_as_tuple(data.get("read_by")),
        route_verdict=dict(data.get("route_verdict") or {}) if isinstance(data.get("route_verdict"), dict) else {},
        route_audit_event=dict(data.get("route_audit_event") or {}) if isinstance(data.get("route_audit_event"), dict) else {},
        route_bus_event=dict(data.get("route_bus_event") or {}) if isinstance(data.get("route_bus_event"), dict) else {},
        metadata=metadata,
        created_at=float(data.get("created_at") or time.time()),
        updated_at=float(data.get("updated_at") or data.get("created_at") or time.time()),
    )


def _agent_envelope_from_snapshot(snapshot: Any) -> AgentEnvelope:
    data = snapshot if isinstance(snapshot, dict) else {}
    return AgentEnvelope(
        message_id=str(data.get("message_id") or _new_id("agentmsg")),
        sender=str(data.get("sender") or "unknown"),
        recipient=str(data.get("recipient") or "all"),
        message_type=str(data.get("message_type") or "event"),
        content=data.get("content", ""),
        context_id=str(data.get("context_id") or ""),
        task_id=str(data.get("task_id") or ""),
        expected_output_schema=dict(data.get("expected_output_schema") or {}) if isinstance(data.get("expected_output_schema"), dict) else {},
        permission_scope=str(data.get("permission_scope") or ""),
        deadline_at=float(data["deadline_at"]) if data.get("deadline_at") not in ("", None) else None,
        requires_review=bool(data.get("requires_review")),
        artifacts=tuple(dict(item) for item in data.get("artifacts") or () if isinstance(item, dict)),
        metadata=dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), dict) else {},
        created_at=float(data.get("created_at") or time.time()),
    )


def _evaluate_message_route(
    *,
    sender: str,
    recipients: tuple[str, ...],
    message_type: str,
    content: str,
    context_id: str,
    task_id: str,
    permission_scope: str,
    requires_review: bool,
    context_pack_path: str,
    message_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    allowed_recipients = _allowed_collaboration_recipients()
    verdicts: list[dict[str, Any]] = []
    audit_events: list[dict[str, Any]] = []
    for recipient in recipients or ("all",):
        envelope = AgentEnvelope(
            message_id=message_id,
            sender=sender,
            recipient=recipient,
            message_type=message_type,
            content=content,
            context_id=context_id or task_id,
            task_id=task_id,
            permission_scope=permission_scope,
            requires_review=requires_review,
            artifacts=({"path": context_pack_path, "kind": "context_pack"},) if context_pack_path else (),
        )
        router_result = AgentRoutePolicy(allowed_recipients=allowed_recipients).evaluate(envelope)
        verdicts.append(router_result.snapshot())
        audit_events.append(
            {
                "schema_version": "spiritkin.agent_router_audit.v1",
                "action": "collaboration_message_route",
                "allowed": router_result.allowed,
                "reason": router_result.reason,
                "issues": list(router_result.issues),
                "message_id": envelope.message_id,
                "sender": envelope.sender,
                "recipient": envelope.recipient,
                "message_type": envelope.message_type,
                "context_id": envelope.context_id,
                "task_id": envelope.task_id,
                "permission_scope": envelope.permission_scope,
                "requires_review": envelope.requires_review,
                "created_at": router_result.created_at,
            }
        )
    blocked = [verdict for verdict in verdicts if not verdict.get("allowed", False)]
    return {
        "schema_version": "spiritkin.agent_protocol.v1",
        "allowed": not blocked,
        "reason": "allowed" if not blocked else str(blocked[0].get("reason") or "agent_route_blocked"),
        "issues": [issue for verdict in verdicts for issue in verdict.get("issues", [])],
        "recipients": list(recipients or ("all",)),
        "recipient_verdicts": verdicts,
        "permission_scope": permission_scope.strip().lower().replace("-", "_").replace(" ", "_"),
        "requires_review": requires_review,
        "created_at": time.time(),
    }, {
        "schema_version": "spiritkin.agent_router_audit.v1",
        "action": "collaboration_message_route",
        "allowed": not blocked,
        "reason": "allowed" if not blocked else str(blocked[0].get("reason") or "agent_route_blocked"),
        "issues": [issue for verdict in verdicts for issue in verdict.get("issues", [])],
        "message_id": message_id,
        "sender": sender,
        "recipients": list(recipients or ("all",)),
        "message_type": message_type,
        "context_id": context_id or task_id,
        "task_id": task_id,
        "permission_scope": permission_scope.strip().lower().replace("-", "_").replace(" ", "_"),
        "requires_review": requires_review,
        "recipient_events": audit_events,
        "created_at": time.time(),
    }



def _thread_state_from_dict(data: dict[str, Any]) -> CollaborationThreadState:
    status = str(data.get("status") or "active").strip().lower()
    if status not in {"active", "archived", "deleted"}:
        status = "active"
    return CollaborationThreadState(
        thread_id=str(data.get("thread_id") or data.get("id") or "").strip(),
        status=status,
        title=str(data.get("title") or ""),
        archived_at=float(data.get("archived_at") or 0.0),
        deleted_at=float(data.get("deleted_at") or 0.0),
        updated_at=float(data.get("updated_at") or time.time()),
    )
