from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.channels.wechat_ilink import ILinkConfig
from backend.state_store import resolve_state_path

DEFAULT_CHANNEL_STATUS_PATH = "state/channels/wechat-ilink-status.json"


def build_ios_channels_snapshot(*, status_path: str | Path | None = None) -> dict[str, Any]:
    config = ILinkConfig.from_env()
    runtime_status = _load_status(status_path)
    validation = config.validate()
    return {
        "schema_version": "spiritkin.ios.channels.v1",
        "wechat_ilink": {
            "enabled": config.enabled,
            "configured": validation is None,
            "phase": str(runtime_status.get("phase") or ("disabled" if not config.enabled else "offline")),
            "message": str(runtime_status.get("message") or validation or "等待 Runtime 通道启动"),
            "bot_id": _masked(config.bot_id),
            "user_id": _masked(config.user_id),
            "credential_source": "credential_file" if config.credentials_path else "environment",
            "credential_path_configured": bool(config.credentials_path),
            "updated_at": str(runtime_status.get("updated_at") or ""),
            "capabilities": ["text.receive", "text.reply"],
            "media_supported": False,
            "secret_exposed": False,
        }
    }


def persist_wechat_ilink_status(payload: dict[str, Any], *, status_path: str | Path | None = None) -> None:
    import datetime

    target = _status_path(status_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    next_payload = {
        "schema_version": "spiritkin.wechat_ilink.status.v1",
        "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "phase": str(payload.get("phase") or "unknown"),
        "message": str(payload.get("message") or ""),
        "detail": _redact(dict(payload.get("detail") or {})) if isinstance(payload.get("detail"), dict) else {},
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _load_status(path: str | Path | None) -> dict[str, Any]:
    target = _status_path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _status_path(path: str | Path | None) -> Path:
    return resolve_state_path("SPIRITKIN_WECHAT_ILINK_STATUS_PATH", DEFAULT_CHANNEL_STATUS_PATH, path)


def _masked(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:3]}...{text[-3:]}"


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    blocked = ("token", "secret", "password", "credential", "cookie")
    return {key: value for key, value in payload.items() if not any(part in str(key).lower() for part in blocked)}
