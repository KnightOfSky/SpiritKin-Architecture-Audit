from __future__ import annotations

from typing import Any

from backend.app.resource_management import build_resource_management_snapshot, handle_resource_management_action


def build_ios_resources_snapshot(*, workspace_id: str, management: bool = False) -> dict[str, Any]:
    snapshot = dict(build_resource_management_snapshot())
    registry = dict(snapshot.get("resource_registry") or {})
    items = []
    for raw in registry.get("resources") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        metadata = dict(item.get("metadata") or {})
        item_workspace = str(metadata.get("workspace_id") or "")
        if not management and item_workspace and item_workspace != workspace_id:
            continue
        item["workspace_id"] = item_workspace
        item["editable"] = bool(management or (workspace_id and item_workspace == workspace_id))
        item["deletable"] = item["editable"]
        items.append(item)
    registry["resources"] = items
    registry["total"] = len(items)
    snapshot["resource_registry"] = registry
    snapshot["resource_count"] = len(items)
    snapshot["workspace_id"] = workspace_id
    return snapshot


def handle_ios_resource_action(payload: dict[str, Any], *, workspace_id: str, management: bool = False) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh", "list"}:
        return {"ok": True, "resource_management": build_ios_resources_snapshot(workspace_id=workspace_id, management=management)}
    next_payload = dict(payload)
    if action in {"save", "register", "upsert", "create", "update"}:
        next_payload["action"] = "upsert"
        resource = dict(next_payload.get("resource") or {}) if isinstance(next_payload.get("resource"), dict) else dict(next_payload)
        resource_id = str(resource.get("resource_id") or resource.get("id") or "").strip()
        if not resource_id:
            raise ValueError("resource_id is required")
        current = build_ios_resources_snapshot(workspace_id=workspace_id, management=True)
        existing = next(
            (
                item
                for item in current.get("resource_registry", {}).get("resources") or []
                if str(item.get("resource_id") or "") == resource_id
            ),
            None,
        )
        existing_workspace = str((existing or {}).get("workspace_id") or "")
        if existing is not None and not management and existing_workspace != workspace_id:
            raise PermissionError("iOS terminal cannot overwrite a global or another-workspace resource")
        metadata = dict(resource.get("metadata") or {})
        metadata["workspace_id"] = workspace_id
        resource_type = str(resource.get("resource_type") or "generic").strip().lower()
        if resource_type in {"commerce_store", "commerce_product"}:
            tags = [str(item).strip() for item in resource.get("tags") or [] if str(item).strip()]
            for tag in ("ecommerce", "store" if resource_type == "commerce_store" else "product"):
                if tag not in tags:
                    tags.append(tag)
            resource["tags"] = tags
            resource.setdefault("owner_agent", "ecommerce")
        if resource_type == "commerce_product" and not str(metadata.get("store_resource_id") or "").strip():
            raise ValueError("commerce product requires store_resource_id")
        if resource_type == "commerce_product":
            store_id = str(metadata.get("store_resource_id") or "").strip()
            visible = build_ios_resources_snapshot(workspace_id=workspace_id, management=management)
            store_resource = next(
                (
                    item
                    for item in visible.get("resource_registry", {}).get("resources") or []
                    if str(item.get("resource_id") or "") == store_id
                    and str(item.get("resource_type") or "") == "commerce_store"
                ),
                None,
            )
            if store_resource is None:
                raise ValueError("commerce product store_resource_id must reference an accessible store")
        resource["metadata"] = metadata
        next_payload["resource"] = resource
    if action in {"delete", "remove", "archive"}:
        resource_id = str(next_payload.get("resource_id") or next_payload.get("id") or "")
        current = build_ios_resources_snapshot(workspace_id=workspace_id, management=management)
        existing = next((item for item in current.get("resource_registry", {}).get("resources") or [] if str(item.get("resource_id") or "") == resource_id), None)
        if not existing or not bool(existing.get("deletable")):
            raise PermissionError("iOS terminal cannot modify a global or another-workspace resource")
        if str(existing.get("resource_type") or "") == "commerce_store":
            dependent_products = [
                item
                for item in current.get("resource_registry", {}).get("resources") or []
                if str(item.get("resource_type") or "") == "commerce_product"
                and str((item.get("metadata") or {}).get("store_resource_id") or "") == resource_id
            ]
            if dependent_products:
                raise ValueError("commerce store cannot be deleted while products reference it")
    result = handle_resource_management_action(next_payload)
    return {
        "ok": bool(result.get("ok", True)),
        "action": action,
        "result": result,
        "resource_management": build_ios_resources_snapshot(workspace_id=workspace_id, management=management),
    }
