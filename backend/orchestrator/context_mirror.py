from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.orchestrator.context_store import CONTEXT_STORE_SCHEMA_VERSION, AppendOnlyContextStore, ContextPatch
from backend.orchestrator.ecommerce_task_queue import load_queue

CONTEXT_MIRROR_SCHEMA_VERSION = "spiritkin.context_mirror.v1"
CONTEXT_WRITE_INTENT_SCHEMA_VERSION = "spiritkin.context_write_intent.v1"
CONTEXT_WRITE_OPERATIONS = {"set", "append", "merge", "update", "patch", "delete"}


@dataclass(frozen=True)
class ContextMirrorSnapshot:
    context_id: str
    source_count: int
    patches: tuple[ContextPatch, ...]
    generated_at: float = field(default_factory=time.time)

    def snapshot(self, *, view: str = "full") -> dict[str, Any]:
        store = AppendOnlyContextStore(list(self.patches))
        return {
            "schema_version": CONTEXT_MIRROR_SCHEMA_VERSION,
            "context_store_schema_version": CONTEXT_STORE_SCHEMA_VERSION,
            "context_id": self.context_id,
            "view": view,
            "source_count": self.source_count,
            "generated_at": self.generated_at,
            "context": store.snapshot(context_id=self.context_id, view=view),
        }


@dataclass(frozen=True)
class ContextWriteIntent:
    context_id: str
    target_path: str
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "desktop"
    requires_review: bool = True
    dry_run: bool = True
    status: str = "preview"
    reason: str = ""
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_WRITE_INTENT_SCHEMA_VERSION,
            "context_id": self.context_id,
            "target_path": self.target_path,
            "operation": self.operation,
            "payload": dict(self.payload),
            "actor": self.actor,
            "requires_review": self.requires_review,
            "dry_run": self.dry_run,
            "status": self.status,
            "reason": self.reason,
            "created_at": self.created_at,
        }


def build_context_write_intent_preview(
    payload: dict[str, Any] | None = None,
    *,
    context_id: str = "project:current",
    actor: str = "desktop",
) -> dict[str, Any]:
    data = dict(payload or {})
    if not data:
        return {
            "schema_version": CONTEXT_WRITE_INTENT_SCHEMA_VERSION,
            "context_id": context_id,
            "status": "idle",
            "dry_run": True,
            "requires_review": True,
            "supported_operations": sorted(CONTEXT_WRITE_OPERATIONS),
            "message": "No write intent requested.",
        }
    target_path = _normalize_context_target_path(data.get("target_path") or data.get("path"))
    operation = str(data.get("operation") or "set").strip().lower()
    requires_review = bool(data.get("requires_review", True))
    status = "preview"
    reason = ""
    if not target_path:
        status = "rejected"
        reason = "target_path is required."
    elif operation not in CONTEXT_WRITE_OPERATIONS:
        status = "rejected"
        reason = f"Unsupported operation: {operation}"
    raw_payload = data.get("payload") if "payload" in data else data.get("value")
    if isinstance(raw_payload, dict):
        intent_payload = dict(raw_payload)
    elif raw_payload in ("", None):
        intent_payload = {}
    else:
        intent_payload = {"value": raw_payload}
    intent = ContextWriteIntent(
        context_id=str(data.get("context_id") or context_id),
        target_path=target_path,
        operation=operation,
        payload=intent_payload,
        actor=str(data.get("actor") or actor),
        requires_review=requires_review,
        status=status,
        reason=reason,
    )
    snapshot = intent.snapshot()
    snapshot["would_write"] = False
    snapshot["validated"] = status == "preview"
    snapshot["write_blocked_reason"] = "dry_run_only" if status == "preview" else reason
    return snapshot


def build_project_context_mirror(
    *,
    desktop_state: dict[str, Any] | None = None,
    collaboration_snapshot: dict[str, Any] | None = None,
    ecommerce_queue: dict[str, Any] | None = None,
    context_id: str = "project:current",
    actor: str = "context_mirror",
) -> ContextMirrorSnapshot:
    store = AppendOnlyContextStore()
    source_count = 0
    if isinstance(desktop_state, dict):
        source_count += 1
        _mirror_desktop_state(store, context_id=context_id, actor=actor, desktop_state=desktop_state)
    if isinstance(collaboration_snapshot, dict):
        source_count += 1
        _mirror_collaboration(store, context_id=context_id, actor=actor, collaboration_snapshot=collaboration_snapshot)
    if isinstance(ecommerce_queue, dict):
        source_count += 1
        _mirror_ecommerce_queue(store, context_id=context_id, actor=actor, ecommerce_queue=ecommerce_queue)
    return ContextMirrorSnapshot(context_id=context_id, source_count=source_count, patches=tuple(store.list_patches(context_id=context_id)))


def build_project_context_mirror_from_files(
    *,
    project_root: str | Path | None = None,
    desktop_state_path: str | Path | None = None,
    collaboration_root: str | Path | None = None,
    ecommerce_state_dir: str | Path | None = None,
    context_id: str = "project:current",
    desktop_state_loader: Callable[[str | Path | None], dict[str, Any]] | None = None,
    collaboration_snapshot_loader: Callable[[str | Path | None], dict[str, Any]] | None = None,
) -> ContextMirrorSnapshot:
    if desktop_state_loader is None or collaboration_snapshot_loader is None:
        raise RuntimeError("context mirror file loaders must be injected by the app layer")
    root = Path(project_root or Path.cwd()).resolve()
    return build_project_context_mirror(
        desktop_state=desktop_state_loader(desktop_state_path),
        collaboration_snapshot=collaboration_snapshot_loader(collaboration_root),
        ecommerce_queue=load_queue(ecommerce_state_dir, project_root=root),
        context_id=context_id,
    )


def _mirror_desktop_state(
    store: AppendOnlyContextStore,
    *,
    context_id: str,
    actor: str,
    desktop_state: dict[str, Any],
) -> None:
    sessions = _dict_items(desktop_state.get("sessions"))
    projects = _dict_items(desktop_state.get("projects"))
    tasks = _dict_items(desktop_state.get("tasks"))
    active_session = _active_session(desktop_state, sessions)
    active_project = _project_for_session(active_session, projects)
    store.append_patch(
        context_id=context_id,
        patch_type="mirror",
        actor=actor,
        path="/desktop/summary",
        value={
            "revision": desktop_state.get("revision", 0),
            "active_session_id": desktop_state.get("active_session_id", ""),
            "session_count": len(sessions),
            "project_count": len(projects),
            "task_count": len(tasks),
            "updated_at": desktop_state.get("updated_at", 0),
        },
        metadata={"source": "desktop_state", "views": ["task"]},
    )
    if active_session:
        messages = _dict_items(active_session.get("messages"))
        store.append_patch(
            context_id=context_id,
            patch_type="mirror",
            actor=actor,
            path="/desktop/active_session",
            value={
                "id": active_session.get("id", ""),
                "title": active_session.get("title", ""),
                "status": active_session.get("status", ""),
                "project_id": active_session.get("project_id") or "",
                "message_count": len(messages),
                "recent_messages": _message_previews(messages[-8:]),
                "updated_at": active_session.get("updated_at", 0),
            },
            metadata={"source": "desktop_state", "views": ["task"]},
        )
    if active_project:
        store.append_patch(
            context_id=context_id,
            patch_type="mirror",
            actor=actor,
            path="/project/active",
            value=_project_summary(active_project, sessions),
            metadata={"source": "desktop_state", "views": ["task"]},
        )
    store.append_patch(
        context_id=context_id,
        patch_type="mirror",
        actor=actor,
        path="/project/list",
        value=[_project_summary(project, sessions) for project in projects[-20:]],
        metadata={"source": "desktop_state", "views": ["task"]},
    )


def _mirror_collaboration(
    store: AppendOnlyContextStore,
    *,
    context_id: str,
    actor: str,
    collaboration_snapshot: dict[str, Any],
) -> None:
    overview = collaboration_snapshot.get("overview") if isinstance(collaboration_snapshot.get("overview"), dict) else {}
    tasks = _dict_items(collaboration_snapshot.get("active_tasks"))
    messages = _dict_items(collaboration_snapshot.get("recent_messages"))
    decisions = _dict_items(collaboration_snapshot.get("recent_decisions"))
    reviews = _dict_items(collaboration_snapshot.get("recent_reviews"))
    route_bus = collaboration_snapshot.get("agent_route_bus") if isinstance(collaboration_snapshot.get("agent_route_bus"), dict) else {}
    route_bus_worker = (
        collaboration_snapshot.get("agent_route_bus_worker")
        if isinstance(collaboration_snapshot.get("agent_route_bus_worker"), dict)
        else {}
    )
    store.append_patch(
        context_id=context_id,
        patch_type="mirror",
        actor=actor,
        path="/collaboration/summary",
        value={
            "task_count": overview.get("task_count", len(tasks)),
            "active_task_count": overview.get("active_task_count", len(tasks)),
            "message_count": overview.get("message_count", len(messages)),
            "unread_message_count": overview.get("unread_message_count", 0),
            "decision_count": overview.get("decision_count", len(decisions)),
            "review_count": overview.get("review_count", len(reviews)),
        },
        metadata={"source": "collaboration", "views": ["task"]},
    )
    if route_bus:
        store.append_patch(
            context_id=context_id,
            patch_type="mirror",
            actor=actor,
            path="/agent_route_bus/summary",
            value={
                "total": route_bus.get("total", 0),
                "routed": route_bus.get("routed", 0),
                "blocked": route_bus.get("blocked", 0),
                "ack_count": route_bus.get("ack_count", 0),
                "worker_event_count": route_bus.get("worker_event_count", 0),
                "recent_message_count": len(_dict_items(route_bus.get("recent_messages"))),
                "recent_audit_count": len(_dict_items(route_bus.get("recent_audit_events"))),
                "recent_ack_count": len(_dict_items(route_bus.get("recent_ack_events"))),
                "recent_worker_event_count": len(_dict_items(route_bus.get("recent_worker_events"))),
                "storage": route_bus.get("storage", {}),
            },
            metadata={"source": "agent_route_bus", "views": ["task"]},
        )
    if route_bus_worker:
        worker_agents = _dict_items(route_bus_worker.get("agents"))
        store.append_patch(
            context_id=context_id,
            patch_type="mirror",
            actor=actor,
            path="/agent_route_bus/worker_status",
            value={
                "mode": route_bus_worker.get("mode", "dry_run_only"),
                "real_worker_status": route_bus_worker.get("real_worker_status", "not_enabled"),
                "dry_run_available": bool(route_bus_worker.get("dry_run_available", True)),
                "external_cli_worker_available": bool(route_bus_worker.get("external_cli_worker_available", False)),
                "pending_count": route_bus_worker.get("pending_count", 0),
                "ack_count": route_bus_worker.get("ack_count", 0),
                "worker_event_count": route_bus_worker.get("worker_event_count", 0),
                "recent_worker_events": _dict_items(route_bus_worker.get("recent_worker_events"))[-5:],
                "agents": [
                    {
                        "agent": item.get("agent", ""),
                        "worker_mode": item.get("worker_mode", "dry_run_only"),
                        "real_worker_status": item.get("real_worker_status", "not_enabled"),
                        "pending_count": item.get("pending_count", 0),
                        "ack_count": item.get("ack_count", 0),
                        "latest_worker_event": item.get("latest_worker_event", {}),
                        "external_assistant_status": (
                            ((item.get("external_worker") or {}).get("external_assistant") or {}).get("status", "")
                            if isinstance(item.get("external_worker"), dict)
                            else ""
                        ),
                    }
                    for item in worker_agents
                ],
                "storage": route_bus_worker.get("storage", {}),
            },
            metadata={"source": "agent_route_bus_worker", "views": ["task"]},
        )
    store.append_patch(
        context_id=context_id,
        patch_type="mirror",
        actor=actor,
        path="/collaboration/tasks",
        value=[
            {
                "task_id": task.get("task_id", ""),
                "title": task.get("title", ""),
                "owner": task.get("owner", ""),
                "status": task.get("status", ""),
                "updated_at": task.get("updated_at", 0),
            }
            for task in tasks[-30:]
        ],
        metadata={"source": "collaboration", "views": ["task"]},
    )
    store.append_patch(
        context_id=context_id,
        patch_type="mirror",
        actor=actor,
        path="/collaboration/recent_messages",
        value=[_collaboration_message_preview(message) for message in messages[-20:]],
        metadata={"source": "collaboration", "views": ["task"]},
    )


def _mirror_ecommerce_queue(
    store: AppendOnlyContextStore,
    *,
    context_id: str,
    actor: str,
    ecommerce_queue: dict[str, Any],
) -> None:
    tasks = _dict_items(ecommerce_queue.get("tasks"))
    status_counts: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    store.append_patch(
        context_id=context_id,
        patch_type="mirror",
        actor=actor,
        path="/ecommerce/queue_summary",
        value={
            "task_count": len(tasks),
            "status_counts": dict(sorted(status_counts.items())),
            "updated_at": ecommerce_queue.get("updated_at", ""),
        },
        metadata={"source": "ecommerce_queue", "views": ["task"]},
    )
    store.append_patch(
        context_id=context_id,
        patch_type="mirror",
        actor=actor,
        path="/ecommerce/recent_tasks",
        value=[
            {
                "id": task.get("id", ""),
                "type": task.get("type", ""),
                "status": task.get("status", ""),
                "source": task.get("source", ""),
                "updated_at": task.get("updated_at", ""),
                "workflow_run_id": task.get("workflow_run_id", ""),
            }
            for task in tasks[-30:]
        ],
        metadata={"source": "ecommerce_queue", "views": ["task"]},
    )


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _normalize_context_target_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("/"):
        text = f"/{text}"
    parts = [part for part in text.split("/") if part]
    return "/" + "/".join(parts)


def _active_session(desktop_state: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    active_id = str(desktop_state.get("active_session_id") or "")
    return next((session for session in sessions if str(session.get("id") or "") == active_id), sessions[0] if sessions else {})


def _project_for_session(active_session: dict[str, Any], projects: list[dict[str, Any]]) -> dict[str, Any]:
    project_id = str(active_session.get("project_id") or "")
    if not project_id:
        return {}
    return next((project for project in projects if str(project.get("id") or "") == project_id), {})


def _project_summary(project: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    project_id = str(project.get("id") or "")
    project_sessions = [session for session in sessions if str(session.get("project_id") or "") == project_id]
    return {
        "id": project_id,
        "title": project.get("title", ""),
        "status": project.get("status", ""),
        "workspace_path": project.get("workspace_path") or "",
        "session_count": len(project_sessions),
        "updated_at": project.get("updated_at", 0),
    }


def _message_previews(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": message.get("id", ""),
            "role": message.get("role", ""),
            "text_preview": str(message.get("text") or "")[:240],
            "created_at": message.get("created_at", 0),
        }
        for message in messages
    ]


def _collaboration_message_preview(message: dict[str, Any]) -> dict[str, Any]:
    envelope = message.get("agent_envelope") if isinstance(message.get("agent_envelope"), dict) else {}
    return {
        "message_id": message.get("message_id", ""),
        "thread_id": message.get("thread_id", ""),
        "task_id": message.get("task_id", ""),
        "sender": envelope.get("sender") or message.get("from_agent") or message.get("from_model") or "",
        "recipient": envelope.get("recipient") or message.get("to_model") or "",
        "message_type": envelope.get("message_type") or message.get("role") or "",
        "content_preview": str(envelope.get("content") or message.get("content") or "")[:360],
        "status": message.get("status", ""),
        "created_at": message.get("created_at", 0),
    }
