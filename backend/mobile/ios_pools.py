from __future__ import annotations

import time
from typing import Any

from backend.app.skills_console import build_desktop_skills_snapshot, handle_desktop_skills_action
from backend.app.workflow_management import build_workflow_management_snapshot, handle_workflow_management_action
from backend.mobile.ios_domains import enrich_workflow_definition


def _compact_skill(item: dict[str, Any], *, workspace_id: str = "", management: bool = False) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    item_workspace = str(metadata.get("workspace_id") or "")
    return {
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or metadata.get("description") or ""),
        "status": str(metadata.get("status") or item.get("status") or "draft"),
        "owner_agent_id": str(metadata.get("owner_agent_id") or ""),
        "domain": str(metadata.get("domain") or "general"),
        "enabled": bool(metadata.get("enabled", item.get("enabled", True))),
        "step_count": len(item.get("steps") or []),
        "workspace_id": item_workspace,
        "editable": bool(management or (workspace_id and item_workspace == workspace_id)),
        "deletable": bool(management or (workspace_id and item_workspace == workspace_id)),
    }


def _compact_workflow(item: dict[str, Any], *, workspace_id: str = "", management: bool = False) -> dict[str, Any]:
    enriched = enrich_workflow_definition(item)
    metadata = dict(enriched.get("metadata") or {})
    item_workspace = str(metadata.get("workspace_id") or "")
    return {
        "name": str(enriched.get("name") or ""),
        "display_name": str(metadata.get("display_name") or enriched.get("name") or ""),
        "description": str(metadata.get("description") or enriched.get("description") or ""),
        "domain": str(enriched.get("domain") or "general"),
        "version": str(enriched.get("version") or metadata.get("version") or ""),
        "node_count": len(enriched.get("nodes") or []),
        "workspace_id": item_workspace,
        "editable": bool(management or (workspace_id and item_workspace == workspace_id)),
        "deletable": bool(management or (workspace_id and item_workspace == workspace_id)),
    }


def build_ios_pools_snapshot(*, workspace_id: str = "", management: bool = False) -> dict[str, Any]:
    skills = build_desktop_skills_snapshot()
    workflows = build_workflow_management_snapshot()
    skill_items = [
        compact
        for item in skills.get("skills") or []
        if isinstance(item, dict)
        for compact in [_compact_skill(item, workspace_id=workspace_id, management=management)]
        if management or not compact["workspace_id"] or compact["workspace_id"] == workspace_id
    ]
    workflow_items = [
        compact
        for item in workflows.get("definitions") or []
        if isinstance(item, dict)
        for compact in [_compact_workflow(item, workspace_id=workspace_id, management=management)]
        if management or not compact["workspace_id"] or compact["workspace_id"] == workspace_id
    ]
    return {
        "schema_version": "spiritkin.ios.pools.v1",
        "generated_at": time.time(),
        "skills": {
            "count": len(skill_items),
            "status_counts": dict(skills.get("status_counts") or {}),
            "items": skill_items,
        },
        "workflows": {
            "count": len(workflow_items),
            "items": workflow_items,
        },
    }


def handle_ios_pool_action(payload: dict[str, Any], *, workspace_id: str, management: bool = False) -> dict[str, Any]:
    pool = str(payload.get("pool") or payload.get("kind") or "").strip().lower()
    action = str(payload.get("action") or "snapshot").strip().lower()
    if pool not in {"skills", "workflows"}:
        raise ValueError("pool must be skills or workflows")
    if action in {"snapshot", "refresh", "list"}:
        return {"ok": True, **build_ios_pools_snapshot(workspace_id=workspace_id, management=management)}

    next_payload = dict(payload)
    next_payload.pop("pool", None)
    next_payload.pop("kind", None)
    if pool == "skills":
        name = str(next_payload.get("name") or next_payload.get("skill_name") or "").strip()
        if action in {"create", "update", "upsert"}:
            next_payload["action"] = "save"
        existing = next(
            (
                item
                for item in build_desktop_skills_snapshot().get("skills") or []
                if isinstance(item, dict) and str(item.get("name") or "") == name
            ),
            None,
        )
        existing_workspace = str(dict((existing or {}).get("metadata") or {}).get("workspace_id") or "")
        if action in {"create", "update", "upsert", "save"} and existing is not None and not management and existing_workspace != workspace_id:
            raise PermissionError("iOS terminal cannot overwrite a global or another-workspace skill")
        metadata = dict(next_payload.get("metadata") or {})
        if isinstance(next_payload.get("skill"), dict):
            skill = dict(next_payload["skill"])
            metadata = dict(skill.get("metadata") or {})
            metadata["workspace_id"] = workspace_id
            skill["metadata"] = metadata
            next_payload["skill"] = skill
        elif action in {"create", "update", "upsert", "save"}:
            metadata["workspace_id"] = workspace_id
            next_payload["metadata"] = metadata
        if action in {"delete", "archive", "remove"}:
            if not management and existing_workspace != workspace_id:
                raise PermissionError("iOS terminal cannot modify a global or another-workspace skill")
        result = handle_desktop_skills_action(next_payload)
    else:
        workflow_name = str(next_payload.get("workflow_name") or next_payload.get("name") or "").strip()
        existing = next(
            (
                item
                for item in build_workflow_management_snapshot().get("definitions") or []
                if isinstance(item, dict) and str(item.get("name") or "") == workflow_name
            ),
            None,
        )
        existing_workspace = str(dict((existing or {}).get("metadata") or {}).get("workspace_id") or "")
        if action in {"create", "update", "upsert"} and existing is not None and not management and existing_workspace != workspace_id:
            raise PermissionError("iOS terminal cannot overwrite a global or another-workspace workflow")
        if action in {"create", "update", "upsert"}:
            next_payload["action"] = "upsert_definition"
        elif action in {"delete", "remove", "archive"}:
            next_payload["action"] = "delete_definition"
            next_payload["workflow_name"] = workflow_name
        definition = dict(next_payload.get("definition") or {}) if isinstance(next_payload.get("definition"), dict) else {}
        if definition:
            metadata = dict(definition.get("metadata") or {})
            metadata["workspace_id"] = workspace_id
            definition["metadata"] = metadata
            next_payload["definition"] = definition
        if action in {"delete", "remove", "archive"}:
            if not management and existing_workspace != workspace_id:
                raise PermissionError("iOS terminal cannot modify a global or another-workspace workflow")
        result = handle_workflow_management_action(next_payload)
    return {
        "ok": bool(result.get("ok", True)),
        "pool": pool,
        "action": action,
        "result": result,
        **build_ios_pools_snapshot(workspace_id=workspace_id, management=management),
    }
