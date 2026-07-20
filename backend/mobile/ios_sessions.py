"""Authenticated iOS access to the shared desktop conversation state."""

from __future__ import annotations

from typing import Any

from backend.app.desktop_state import load_desktop_state, update_desktop_state


def _ios_visible_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    role = str(message.get("role") or "assistant").strip().lower()
    if role == "system":
        # Desktop keeps internal reasoning/audit events for debugging. They are
        # intentionally not rendered as assistant speech on the iOS surface.
        text = str(message.get("subtitle") or "系统状态已同步。").strip()
        role = "assistant"
    else:
        text = str(message.get("text") or message.get("content") or "").strip()
    if not text:
        return None
    if len(text) > 800 or any(marker in text for marker in ("思考 ·", "thinking process", "调用 ·")):
        text = "桌面运行记录已同步，详细过程请在电商 Terminal 查看。"
    return {
        "id": str(message.get("id") or ""),
        "role": role,
        "text": text,
        "created_at": float(message.get("created_at") or 0),
        "updated_at": float(message.get("updated_at") or message.get("created_at") or 0),
    }


def _ios_visible_sessions(state: dict[str, Any]) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for raw_session in state.get("sessions") or []:
        if not isinstance(raw_session, dict):
            continue
        session = dict(raw_session)
        session["messages"] = [
            visible
            for visible in (_ios_visible_message(item) for item in raw_session.get("messages") or [])
            if visible is not None
        ]
        sessions.append(session)
    return sessions


def _session_workspace(session: dict[str, Any]) -> str:
    return str(session.get("workspace_id") or session.get("workspaceId") or "").strip()


def ios_sessions_snapshot(*, workspace_id: str = "", include_unscoped: bool = False) -> dict[str, Any]:
    state = load_desktop_state()
    sessions = _ios_visible_sessions(state)
    if workspace_id and not include_unscoped:
        sessions = [item for item in sessions if _session_workspace(item) == workspace_id]
    return {
        "ok": True,
        "revision": int(state.get("revision") or 0),
        "active_session_id": str(state.get("active_session_id") or ""),
        "sessions": sessions,
    }


def update_ios_sessions(payload: dict[str, Any], *, workspace_id: str = "", include_unscoped: bool = False) -> dict[str, Any]:
    incoming = payload.get("state") if isinstance(payload.get("state"), dict) else payload
    if not isinstance(incoming, dict):
        raise ValueError("session state must be an object")
    visible_ids = {
        str(item.get("id") or "")
        for item in _ios_visible_sessions(load_desktop_state())
        if isinstance(item, dict) and (include_unscoped or not workspace_id or _session_workspace(item) == workspace_id)
    }
    incoming_sessions = incoming.get("sessions") if isinstance(incoming.get("sessions"), list) else []
    if workspace_id and not include_unscoped:
        incoming_sessions = [
            {**item, "workspace_id": workspace_id}
            for item in incoming_sessions
            if isinstance(item, dict) and (str(item.get("id") or "") in visible_ids or not str(item.get("id") or ""))
        ]
    deleted = [str(item) for item in (payload.get("deleted_session_ids") or payload.get("deletedSessionIds") or [])]
    if workspace_id and not include_unscoped:
        deleted = [item for item in deleted if item in visible_ids]
    session_payload = {
        "state": {
            "active_session_id": incoming.get("active_session_id"),
            "sessions": incoming_sessions,
        },
        "deleted_session_ids": deleted,
        "deleted_message_ids": payload.get("deleted_message_ids") or payload.get("deletedMessageIds") or {},
        "client_id": payload.get("client_id") or "ios_controller",
    }
    state = update_desktop_state(session_payload, client_id="ios_controller")
    return {
        "ok": True,
        "revision": int(state.get("revision") or 0),
        "active_session_id": str(state.get("active_session_id") or ""),
        "sessions": _ios_visible_sessions(state),
    }
