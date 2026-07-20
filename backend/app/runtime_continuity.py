from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from backend.mobile.ios_runtime_host import build_runtime_host_services
from backend.mobile.ios_world import build_world_services

DEFAULT_RUNTIME_WORKSPACE_ID = "local-ecommerce"


def resolve_runtime_workspace_id(value: Any = "") -> str:
    workspace_id = str(value or os.getenv("SPIRITKIN_RUNTIME_WORKSPACE_ID") or DEFAULT_RUNTIME_WORKSPACE_ID).strip()
    if not workspace_id:
        return DEFAULT_RUNTIME_WORKSPACE_ID
    return workspace_id[:120]


def build_runtime_continuity_snapshot(
    *,
    workspace_id: str = "",
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    workspace_id = resolve_runtime_workspace_id(workspace_id)
    registry, checkpoints = build_runtime_host_services(project_root=project_root)
    _, world = build_world_services(project_root=project_root)
    registry_snapshot = registry.snapshot(workspace_id=workspace_id)
    checkpoint_snapshot = checkpoints.snapshot(workspace_id=workspace_id)
    world_snapshot = world.world.snapshot(workspace_id=workspace_id)
    leases = registry_snapshot.get("leases") if isinstance(registry_snapshot.get("leases"), list) else []
    active_lease = leases[0] if leases and isinstance(leases[0], dict) else {}
    return {
        "schema_version": "spiritkin.runtime_continuity.v1",
        "workspace_id": workspace_id,
        "status": "ready" if active_lease.get("effective_status") == "active" else "attention",
        "active_lease": active_lease,
        "runtime_hosts": registry_snapshot,
        "checkpoints": checkpoint_snapshot,
        "world": world_snapshot,
        "policy": {
            "workflow_restart_on_migration": False,
            "inflight_side_effect_reconciliation_required": True,
            "execution_lease_secret_exposed": False,
            "raw_sensor_data_stored": False,
        },
    }


def handle_runtime_continuity_action(
    payload: dict[str, Any],
    *,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    workspace_id = resolve_runtime_workspace_id(payload.get("workspace_id"))
    registry, checkpoints = build_runtime_host_services(project_root=project_root)
    actor = str(payload.get("requested_by") or payload.get("actor") or "desktop-controller").strip()[:120]
    result: dict[str, Any] = {}
    if action in {"snapshot", "refresh", "list"}:
        pass
    elif action in {"elect", "request_election"}:
        if payload.get("confirmed") is not True:
            raise PermissionError("runtime host election requires explicit confirmation")
        result = {"lease": registry.elect(workspace_id, requested_by=actor)}
    elif action in {"request_migration", "prepare_migration"}:
        if payload.get("confirmed") is not True:
            raise PermissionError("runtime migration requires explicit confirmation")
        checkpoint_id = str(payload.get("checkpoint_id") or "")
        checkpoint = next(
            (item for item in checkpoints.snapshot(workspace_id=workspace_id)["checkpoints"] if item.get("checkpoint_id") == checkpoint_id),
            None,
        )
        if checkpoint is None:
            raise KeyError(f"unknown runtime checkpoint: {checkpoint_id}")
        result = {
            "migration": registry.request_migration(
                workspace_id=workspace_id,
                checkpoint_id=checkpoint_id,
                target_host_id=str(payload.get("target_host_id") or ""),
                requested_by=actor,
                confirmed=True,
            )
        }
    elif action == "set_host_status":
        if payload.get("confirmed") is not True:
            raise PermissionError("runtime host status change requires explicit confirmation")
        result = {
            "host": registry.set_host_status(
                str(payload.get("host_id") or ""),
                str(payload.get("status") or "draining"),
                requested_by=actor,
            )
        }
    elif action == "cleanup_observations":
        if payload.get("confirmed") is not True:
            raise PermissionError("observation cleanup requires explicit confirmation")
        _, world = build_world_services(project_root=project_root)
        result = world.observations.cleanup()
    else:
        raise ValueError(f"unsupported runtime continuity action: {action}")
    return {
        "ok": True,
        "action": action,
        "result": result,
        "runtime_continuity": build_runtime_continuity_snapshot(
            workspace_id=workspace_id,
            project_root=project_root,
        ),
    }
