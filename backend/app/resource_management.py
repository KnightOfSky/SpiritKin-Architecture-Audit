from __future__ import annotations

import time
from typing import Any

from backend.orchestrator.resource_registry import (
    JsonResourceRegistryStore,
    ResourceRecord,
    ResourceRegistry,
    normalize_resource_id,
    resolve_resource_registry_path,
    resource_from_snapshot,
)

SCHEMA_VERSION = "spiritkin.resource_management.v1"


def build_resource_management_snapshot() -> dict[str, Any]:
    store = JsonResourceRegistryStore()
    registry = store.load()
    snapshot = registry.snapshot()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "registry_path": str(resolve_resource_registry_path()),
        "resource_registry": snapshot,
        "resource_count": int(snapshot.get("total") or 0),
        "gap_count": len(snapshot.get("gaps") or []),
        "capabilities": {
            "register_resource": True,
            "delete_resource": True,
            "credentials_are_references_only": True,
            "execute_resource_actions": False,
        },
    }


def handle_resource_management_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh", "list"}:
        return {"ok": True, "resource_management": build_resource_management_snapshot()}
    if action in {"save", "register", "upsert"}:
        resource = _resource_from_payload(payload.get("resource") if isinstance(payload.get("resource"), dict) else payload)
        _validate_resource(resource)
        store = JsonResourceRegistryStore()
        registry = store.load()
        stored = registry.register(resource)
        saved = store.save(registry)
        return {
            "ok": True,
            "resource": stored.snapshot(),
            "saved": {"path": saved["path"], "count": len(saved.get("resources") or [])},
            "resource_management": build_resource_management_snapshot(),
        }
    if action in {"delete", "remove", "archive"}:
        resource_id = str(payload.get("resource_id") or payload.get("id") or "").strip()
        if not resource_id:
            raise ValueError("resource_id is required")
        result = _delete_resource(resource_id)
        return {"ok": True, **result, "resource_management": build_resource_management_snapshot()}
    raise ValueError(f"unsupported resource management action: {action}")


def _resource_from_payload(payload: dict[str, Any]) -> ResourceRecord:
    data = dict(payload or {})
    capabilities = data.get("supported_capabilities") or data.get("capabilities")
    if capabilities is not None:
        data["supported_capabilities"] = capabilities
    return resource_from_snapshot(data)


def _validate_resource(resource: ResourceRecord) -> None:
    if not normalize_resource_id(resource.resource_id) or normalize_resource_id(resource.resource_id) == "resource:unknown":
        raise ValueError("resource_id is required")
    if not resource.label.strip():
        raise ValueError("label is required")
    credential = resource.credential_ref.strip()
    if credential.startswith(("plain:", "env:")):
        raise ValueError("credential_ref must be a vault/keychain reference, not plain: or env:")


def _delete_resource(resource_id: str) -> dict[str, Any]:
    store = JsonResourceRegistryStore()
    registry = store.load()
    normalized = normalize_resource_id(resource_id)
    existing = registry.get(normalized)
    remaining = [record for record in registry.list_records() if normalize_resource_id(record.resource_id) != normalized]
    saved = store.save(ResourceRegistry(remaining))
    return {
        "deleted": existing is not None,
        "resource_id": normalized,
        "saved": {"path": saved["path"], "count": len(saved.get("resources") or [])},
    }
