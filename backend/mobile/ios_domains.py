"""Shared iOS controller domain taxonomy for workflow navigation."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any
from pathlib import Path

IOS_DOMAIN_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "ecommerce",
        "title": "电商",
        "icon": "bag",
        "description": "商品素材、选品、发布预检、Android 上架和完整 iOS Terminal。",
    },
    {
        "id": "content",
        "title": "内容与媒体",
        "icon": "play.rectangle",
        "description": "视频、图片、语音和 AI Cover 等创作工作流。",
    },
    {
        "id": "engineering",
        "title": "开发与自动化",
        "icon": "hammer",
        "description": "代码、浏览器、脚本、测试和远程执行工作流。",
    },
    {
        "id": "system",
        "title": "系统与治理",
        "icon": "gearshape",
        "description": "运行时、模型、诊断、安全和状态维护。",
    },
    {
        "id": "general",
        "title": "其他",
        "icon": "square.grid.2x2",
        "description": "尚未归入专门领域的工作流。",
    },
)

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ecommerce": ("ecommerce", "commerce", "listing", "product", "pdd", "taobao", "jd"),
    "content": ("content", "video", "image", "audio", "voice", "cover", "music"),
    "engineering": ("code", "dev", "git", "browser", "cli", "test", "automation", "game"),
    "system": ("runtime", "health", "diagnostic", "service", "model", "safety", "maintenance"),
}

_DOMAIN_LOCK = threading.RLock()
_DOMAIN_STATE_PATH = "state/mobile/ios_domains.json"


def _domain_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_IOS_DOMAINS_PATH") or _DOMAIN_STATE_PATH
    return Path(raw).expanduser().resolve()


def _load_custom_domains(path: str | os.PathLike[str] | None = None) -> dict[str, dict[str, Any]]:
    target = _domain_state_path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = payload.get("domains") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}


def _save_custom_domains(domains: dict[str, dict[str, Any]], path: str | os.PathLike[str] | None = None) -> None:
    target = _domain_state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema_version": "spiritkin.ios.domains.v1", "updated_at": time.time(), "domains": domains}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def ios_domains_snapshot(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    with _DOMAIN_LOCK:
        custom = _load_custom_domains(path)
    items: list[dict[str, Any]] = []
    for index, item in enumerate(IOS_DOMAIN_CATALOG):
        override = custom.get(item["id"], {})
        items.append({
            **dict(item),
            **override,
            "id": item["id"],
            "built_in": True,
            "editable": True,
            "deletable": False,
            "enabled": bool(override.get("enabled", True)),
            "sort_order": int(override.get("sort_order", index)),
        })
    built_in_ids = {item["id"] for item in IOS_DOMAIN_CATALOG}
    for domain_id, item in custom.items():
        if domain_id in built_in_ids:
            continue
        items.append({
            "id": domain_id,
            "title": str(item.get("title") or domain_id),
            "icon": str(item.get("icon") or "square.grid.2x2"),
            "description": str(item.get("description") or ""),
            "built_in": False,
            "editable": True,
            "deletable": True,
            "enabled": bool(item.get("enabled", True)),
            "sort_order": int(item.get("sort_order", len(items))),
        })
    items.sort(key=lambda item: (int(item.get("sort_order") or 0), str(item.get("title") or "")))
    return {
        "schema_version": "spiritkin.ios.domains.v1",
        "updated_at": time.time(),
        "domains": items,
        "domain_count": len(items),
    }


def update_ios_domains(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    action = str(payload.get("action") or "upsert").strip().lower()
    source = payload.get("domain") if isinstance(payload.get("domain"), dict) else payload
    domain_id = str(source.get("id") or source.get("domain_id") or "").strip().lower()
    if action in {"create", "upsert", "update"} and not domain_id:
        raise ValueError("domain id is required")
    if domain_id and not domain_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("domain id may contain only letters, numbers, _ and -")
    built_in = domain_id in {item["id"] for item in IOS_DOMAIN_CATALOG}
    with _DOMAIN_LOCK:
        custom = _load_custom_domains(path)
        if action in {"delete", "remove"}:
            if built_in:
                raise ValueError("built-in domains cannot be deleted")
            custom.pop(domain_id, None)
        elif action in {"archive", "disable"}:
            existing = dict(custom.get(domain_id) or {})
            existing["enabled"] = False
            custom[domain_id] = existing
        elif action in {"create", "upsert", "update"}:
            if built_in and action == "create":
                raise ValueError("built-in domain already exists")
            existing = dict(custom.get(domain_id) or {})
            existing.update({
                "title": str(source.get("title") or existing.get("title") or domain_id),
                "icon": str(source.get("icon") or existing.get("icon") or "square.grid.2x2"),
                "description": str(source.get("description") or existing.get("description") or ""),
                "enabled": bool(source.get("enabled", existing.get("enabled", True))),
                "sort_order": int(source.get("sort_order", existing.get("sort_order", len(IOS_DOMAIN_CATALOG)))),
            })
            custom[domain_id] = existing
        else:
            raise ValueError(f"unsupported domain action: {action}")
        _save_custom_domains(custom, path)
    return {"ok": True, **ios_domains_snapshot(path), "action": action, "domain_id": domain_id}


def classify_workflow(name: str, metadata: dict[str, Any] | None = None) -> str:
    """Return the explicit domain first, then classify stable workflow ids."""

    meta = metadata if isinstance(metadata, dict) else {}
    explicit = str(meta.get("domain") or meta.get("category") or "").strip().lower()
    if explicit in {item["id"] for item in IOS_DOMAIN_CATALOG}:
        return explicit
    normalized = str(name or "").strip().lower()
    for domain_id, keywords in _DOMAIN_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return domain_id
    return "general"


def enrich_workflow_definition(item: dict[str, Any]) -> dict[str, Any]:
    """Add a stable domain id without changing the source workflow contract."""

    enriched = dict(item)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    domain_id = classify_workflow(str(item.get("name") or item.get("template_id") or ""), metadata)
    enriched["domain"] = domain_id
    enriched["domain_title"] = next(item["title"] for item in IOS_DOMAIN_CATALOG if item["id"] == domain_id)
    return enriched


def workflow_domain_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in IOS_DOMAIN_CATALOG]
