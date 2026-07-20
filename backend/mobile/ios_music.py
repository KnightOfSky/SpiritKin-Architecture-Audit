from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.tools.music_tools import SUPPORTED_MUSIC_EXTENSIONS, MusicCommandQueue, resolve_music_status_path

_SIMPLE_ACTIONS = {"pause", "resume", "stop", "next", "previous", "clear"}


def build_ios_music_snapshot(*, status_path: str | Path | None = None) -> dict[str, Any]:
    path = resolve_music_status_path(status_path)
    payload: dict[str, Any] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            payload = raw
    except (OSError, json.JSONDecodeError):
        payload = {}

    queue = [_public_track(item) for item in payload.get("queue") or [] if isinstance(item, dict)]
    updated_at = str(payload.get("updated_at") or "")
    age_seconds = _age_seconds(updated_at)
    return {
        "schema_version": "spiritkin.ios.music.v1",
        "controller_online": age_seconds is not None and age_seconds <= 5.0,
        "updated_at": updated_at,
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "status": str(payload.get("status") or ("empty" if not queue else "stopped")),
        "queue": queue,
        "queue_count": len(queue),
        "current_index": int(payload.get("current_index") or 0) if queue else -1,
        "current_track": _public_track(payload.get("current_track")) if isinstance(payload.get("current_track"), dict) else {},
        "position_seconds": float(payload.get("position_seconds") or 0),
        "duration_seconds": float(payload.get("duration_seconds") or 0),
        "volume": max(0.0, min(1.0, float(payload.get("volume") or 0.8))),
        "loop_mode": str(payload.get("loop_mode") or "off"),
        "error": str(payload.get("error") or ""),
    }


def handle_ios_music_action(
    store: Any,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    command_path: str | Path | None = None,
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh", "list"}:
        return {"ok": True, "music": build_ios_music_snapshot(status_path=status_path)}

    queue = MusicCommandQueue(command_path)
    arguments: dict[str, Any] = {}
    command_action = action
    if action in _SIMPLE_ACTIONS:
        pass
    elif action == "select":
        arguments = {"index": _bounded_int(payload.get("index"), minimum=0, maximum=9999)}
    elif action == "remove":
        arguments = {"index": _bounded_int(payload.get("index"), minimum=0, maximum=9999)}
    elif action == "seek":
        arguments = {"seconds": max(0.0, min(24 * 3600.0, float(payload.get("seconds") or 0)))}
    elif action == "volume":
        arguments = {"volume": max(0.0, min(1.0, float(payload.get("volume") or 0)))}
    elif action == "loop":
        mode = str(payload.get("mode") or "off").strip().lower()
        if mode not in {"off", "all", "one"}:
            raise ValueError("loop mode must be off, all, or one")
        arguments = {"mode": mode}
    elif action in {"play_artifact", "queue_artifact"}:
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("artifact_id is required")
        file_index = _bounded_int(payload.get("file_index"), minimum=0, maximum=9999)
        artifact_file = store.artifact_file(artifact_id, file_index=file_index, workspace_id=workspace_id)
        path = Path(artifact_file["path"]).resolve()
        mime_type = str(artifact_file.get("mime_type") or "").lower()
        if not mime_type.startswith("audio/") and path.suffix.lower() not in SUPPORTED_MUSIC_EXTENSIONS:
            raise ValueError("artifact is not a supported audio file")
        command_action = "play" if action == "play_artifact" else "queue"
        arguments = {
            "paths": [str(path)],
            "autoplay": action == "play_artifact" or bool(payload.get("autoplay")),
            "replace": action == "play_artifact",
        }
    else:
        raise ValueError(f"unsupported music action: {action}")

    command = queue.enqueue(command_action, arguments)
    return {
        "ok": True,
        "action": action,
        "command": command,
        "music": build_ios_music_snapshot(status_path=status_path),
    }


def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("index must be an integer") from exc
    return max(minimum, min(maximum, number))


def _age_seconds(value: str) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, time.time() - parsed.timestamp())


def _public_track(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title") or "Untitled"),
        "is_remote": bool(item.get("is_remote")),
    }
