from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_CAPABILITIES = (
    ("conversations", "会话与模型", "同步桌面会话并向 Runtime 提问", True, False),
    ("workflows", "工作流", "查看、组合、启动和审批工作流", True, False),
    ("skills", "Skill 池", "查看桌面 Skill、来源和治理状态", True, False),
    ("devices", "设备控制", "管理 Android Bridge、Worker 和设备动作", True, False),
    ("shortcuts", "iOS 自动化", "快捷指令、App Intents 和 URL Scheme", True, False),
    ("artifacts", "素材与文件", "上传照片、文件和工作流素材", True, False),
    ("resources", "Resource 资源", "管理仓库、设备、Worker 和电商项目资源", True, False),
    ("monitoring", "实时监控与自愈", "监控 workspace、Remote Worker、工作流和运行时故障", True, False),
    ("growth_governance", "Growth 治理", "提交成长证据、审核候选并登记到 Registry；不会自动激活", True, False),
    ("avatar", "3D Avatar", "显示和自定义主控端 Avatar", True, False),
    ("voice", "Fairy 语音", "播放本地 CosyVoice 助手语音", True, False),
    ("music", "音乐播放器", "管理桌面端共享播放队列与音频素材", True, False),
    ("channels", "消息通道", "查看微信 iLink 等 Runtime 集中通道状态", True, False),
    ("safety", "安全控制", "紧急停止、恢复和审批边界", True, True),
)


def _state_path(path: str | os.PathLike[str] | None = None) -> Path:
    configured = path or os.getenv("SPIRITKIN_IOS_CAPABILITIES_PATH") or "state/mobile/ios_capabilities.json"
    return Path(configured).expanduser().resolve()


def _read_payload(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = _state_path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _custom_definitions(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = payload.get("definitions")
    if not isinstance(raw, dict):
        return {}
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}


def _defaults(payload: dict[str, Any] | None = None) -> dict[str, bool]:
    values = {capability_id: enabled for capability_id, _, _, enabled, _ in _CAPABILITIES}
    for capability_id, definition in _custom_definitions(payload or {}).items():
        values.setdefault(capability_id, bool(definition.get("enabled", True)))
    return values


def _load(path: str | os.PathLike[str] | None = None) -> dict[str, bool]:
    payload = _read_payload(path)
    values = _defaults(payload)
    stored = payload.get("capabilities") if isinstance(payload, dict) else None
    if isinstance(stored, dict):
        for capability_id in values:
            if capability_id in stored:
                values[capability_id] = bool(stored[capability_id])
    values["safety"] = True
    return values


def ios_capabilities_snapshot(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    with _LOCK:
        payload = _read_payload(path)
        values = _load(path)
    items: list[dict[str, Any]] = [
        {
            "capability_id": capability_id,
            "label": str(_custom_definitions(payload).get(capability_id, {}).get("label") or label),
            "detail": str(_custom_definitions(payload).get(capability_id, {}).get("detail") or detail),
            "enabled": values[capability_id],
            "locked": locked,
            "built_in": True,
            "editable": not locked,
            "deletable": False,
        }
        for capability_id, label, detail, _, locked in _CAPABILITIES
    ]
    built_in_ids = {item[0] for item in _CAPABILITIES}
    for capability_id, definition in _custom_definitions(payload).items():
        if capability_id in built_in_ids:
            continue
        items.append(
            {
                "capability_id": capability_id,
                "label": str(definition.get("label") or capability_id),
                "detail": str(definition.get("detail") or ""),
                "enabled": bool(values.get(capability_id, True)),
                "locked": False,
                "built_in": False,
                "editable": True,
                "deletable": True,
            }
        )
    return {
        "schema_version": "spiritkin.ios.capabilities.v1",
        "updated_at": time.time(),
        "enabled_count": sum(1 for item in items if item["enabled"]),
        "capability_count": len(items),
        "capabilities": items,
    }


def update_ios_capabilities(
    payload: dict[str, Any], path: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    action = str(payload.get("action") or "toggle").strip().lower()
    definition_source = payload.get("capability") if isinstance(payload.get("capability"), dict) else payload
    capability_id = str(definition_source.get("capability_id") or definition_source.get("id") or payload.get("capability_id") or "").strip()
    if action in {"create", "upsert", "update", "delete", "remove", "archive"} and not capability_id:
        raise ValueError("capability_id is required")
    if capability_id and not capability_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("capability_id may contain only letters, numbers, _ and -")
    built_in_ids = {item[0] for item in _CAPABILITIES}
    with _LOCK:
        state = _read_payload(path)
        definitions = _custom_definitions(state)
        values = _load(path)
        if action in {"delete", "remove"}:
            if capability_id in built_in_ids:
                raise ValueError("built-in capabilities cannot be deleted")
            definitions.pop(capability_id, None)
            values.pop(capability_id, None)
            updates = {}
        elif action in {"create", "upsert", "update"}:
            if capability_id in built_in_ids and action == "create":
                raise ValueError("built-in capability already exists")
            if capability_id in built_in_ids:
                existing = dict(definitions.get(capability_id) or {})
                if "label" in definition_source:
                    existing["label"] = str(definition_source.get("label") or "")
                if "detail" in definition_source:
                    existing["detail"] = str(definition_source.get("detail") or "")
                if existing:
                    definitions[capability_id] = existing
                updates = {capability_id: bool(definition_source.get("enabled", values.get(capability_id, True)))}
            else:
                existing = dict(definitions.get(capability_id) or {})
                existing.update({
                    "label": str(definition_source.get("label") or existing.get("label") or capability_id),
                    "detail": str(definition_source.get("detail") or existing.get("detail") or ""),
                    "enabled": bool(definition_source.get("enabled", existing.get("enabled", True))),
                })
                definitions[capability_id] = existing
                updates = {capability_id: bool(existing["enabled"])}
        elif action in {"archive", "disable"}:
            updates = {capability_id: False}
        else:
            updates = payload.get("capabilities")
            if not isinstance(updates, dict):
                if not capability_id:
                    raise ValueError("capabilities or capability_id is required")
                updates = {capability_id: bool(payload.get("enabled"))}
        known = _defaults({"definitions": definitions})
        unknown = sorted(str(item) for item in updates if str(item) not in known)
        if unknown:
            raise ValueError(f"unknown iOS capability: {', '.join(unknown)}")
        for item_id, enabled in updates.items():
            values[str(item_id)] = bool(enabled)
        values["safety"] = True
        target = _state_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema_version": "spiritkin.ios.capabilities.v1",
                    "updated_at": time.time(),
                    "capabilities": values,
                    "definitions": definitions,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return {"ok": True, **ios_capabilities_snapshot(path), "action": action, "capability_id": capability_id}


def _legacy_update_ios_capabilities(
    payload: dict[str, Any], path: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    """Kept for older serialized callers during rolling upgrades."""
    updates = payload.get("capabilities")
    if not isinstance(updates, dict):
        capability_id = str(payload.get("capability_id") or "").strip()
        if not capability_id:
            raise ValueError("capabilities or capability_id is required")
        updates = {capability_id: bool(payload.get("enabled"))}
    known = _defaults(_read_payload(path))
    unknown = sorted(str(item) for item in updates if str(item) not in known)
    if unknown:
        raise ValueError(f"unknown iOS capability: {', '.join(unknown)}")
    with _LOCK:
        values = _load(path)
        for capability_id, enabled in updates.items():
            values[str(capability_id)] = bool(enabled)
        values["safety"] = True
        target = _state_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema_version": "spiritkin.ios.capabilities.v1",
                    "updated_at": time.time(),
                    "capabilities": values,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return ios_capabilities_snapshot(path)


def ios_capability_enabled(capability_id: str, path: str | os.PathLike[str] | None = None) -> bool:
    with _LOCK:
        return bool(_load(path).get(capability_id, False))


def require_ios_capability(capability_id: str) -> None:
    if not ios_capability_enabled(capability_id):
        raise PermissionError(f"iOS capability disabled: {capability_id}")
