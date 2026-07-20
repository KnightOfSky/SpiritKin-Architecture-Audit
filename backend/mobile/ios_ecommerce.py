from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any

from backend.mobile.ios_monitoring import build_ios_monitor_snapshot
from backend.mobile.ios_pools import build_ios_pools_snapshot
from backend.mobile.ios_resources import build_ios_resources_snapshot
from backend.orchestrator.ecommerce_task_queue import add_history, load_queue, new_task, save_queue

_LOCK = threading.RLock()


def build_ios_ecommerce_snapshot(store: Any, *, workspace_id: str) -> dict[str, Any]:
    queue = load_queue()
    tasks = [
        dict(item)
        for item in queue.get("tasks") or []
        if isinstance(item, dict) and str(item.get("workspace_id") or "") == workspace_id
    ]
    tasks.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    control = store.snapshot(workspace_id or None)
    pools = build_ios_pools_snapshot(workspace_id=workspace_id)
    ecommerce_workflows = [item for item in pools.get("workflows", {}).get("items") or [] if str(item.get("domain") or "") == "ecommerce"]
    resources = build_ios_resources_snapshot(workspace_id=workspace_id)
    resource_items = resources.get("resource_registry", {}).get("resources") or []
    ecommerce_types = {"commerce_project", "commerce_store", "commerce_product"}
    ecommerce_resources = [
        item
        for item in resource_items
        if isinstance(item, dict)
        and (str(item.get("resource_type") or "") in ecommerce_types or "ecommerce" in (item.get("tags") or []))
    ]
    stores = [item for item in ecommerce_resources if str(item.get("resource_type") or "") == "commerce_store"]
    products = [item for item in ecommerce_resources if str(item.get("resource_type") or "") == "commerce_product"]
    status_counts = Counter(str(item.get("status") or "pending") for item in tasks)
    monitor = build_ios_monitor_snapshot(store, workspace_id=workspace_id)
    return {
        "schema_version": "spiritkin.ios.ecommerce.v1",
        "generated_at": time.time(),
        "workspace_id": workspace_id,
        "queue": {"count": len(tasks), "status_counts": dict(sorted(status_counts.items())), "items": tasks[:50]},
        "workflows": {"count": len(ecommerce_workflows), "items": ecommerce_workflows},
        "resources": {"count": len(ecommerce_resources), "items": ecommerce_resources},
        "stores": {"count": len(stores), "items": stores},
        "products": {"count": len(products), "items": products},
        "artifacts": control.get("artifacts") or {},
        "remote_workers": control.get("remote_workers") or {},
        "workspace_devices": control.get("workspace_devices") or {},
        "monitor": monitor,
    }


def handle_ios_ecommerce_action(store: Any, payload: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
    action = str(payload.get("action") or "refresh").strip().lower()
    if action in {"refresh", "snapshot", "list"}:
        return {"ok": True, "ecommerce": build_ios_ecommerce_snapshot(store, workspace_id=workspace_id)}
    task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
    with _LOCK:
        queue = load_queue()
        tasks = queue.get("tasks") if isinstance(queue.get("tasks"), list) else []
        existing = next((item for item in tasks if isinstance(item, dict) and str(item.get("id") or "") == task_id), None)
        if existing is not None and str(existing.get("workspace_id") or "") != workspace_id:
            raise PermissionError("iOS terminal cannot modify another-workspace ecommerce task")
        if action in {"create", "create_task"}:
            if not task_id:
                task_id = f"ios_task_{int(time.time() * 1000)}"
            if existing is not None:
                raise ValueError(f"task already exists: {task_id}")
            task = new_task(
                task_id,
                str(payload.get("task_type") or "ecommerce_operation"),
                str(payload.get("status") or "pending"),
                "ios-controller",
                dict(payload.get("inputs") or {}) if isinstance(payload.get("inputs"), dict) else {},
            )
            task["workspace_id"] = workspace_id
            task["title"] = str(payload.get("title") or task_id)
            tasks.append(task)
        elif action in {"update", "update_task", "archive", "archive_task"}:
            if existing is None:
                raise KeyError(f"unknown ecommerce task: {task_id}")
            for key in ("title", "status", "priority", "notes"):
                if key in payload:
                    existing[key] = payload[key]
            if action in {"archive", "archive_task"}:
                existing["status"] = "archived"
            add_history(existing, "ios_task_updated", {"action": action})
        elif action in {"delete", "delete_task"}:
            if existing is None:
                raise KeyError(f"unknown ecommerce task: {task_id}")
            tasks.remove(existing)
        else:
            raise ValueError(f"unsupported ecommerce action: {action}")
        queue["tasks"] = tasks
        save_queue(queue)
    return {"ok": True, "action": action, "task_id": task_id, "ecommerce": build_ios_ecommerce_snapshot(store, workspace_id=workspace_id)}
