from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.orchestrator.runtime_host import RuntimeCheckpointStore, RuntimeHostRegistry
from backend.orchestrator.workflow_store import JsonWorkflowStore


def build_runtime_host_services(*, project_root: str | Path | None = None) -> tuple[RuntimeHostRegistry, RuntimeCheckpointStore]:
    root = Path(project_root or Path.cwd()).resolve()
    registry = RuntimeHostRegistry(root / "state" / "runtime" / "hosts.json")
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=root / "state" / "runtime" / "checkpoints.json",
        workflow_store=JsonWorkflowStore(project_root=root),
    )
    return registry, checkpoints


def build_ios_runtime_host_snapshot(*, workspace_id: str, project_root: str | Path | None = None) -> dict[str, Any]:
    registry, checkpoints = build_runtime_host_services(project_root=project_root)
    return {
        "schema_version": "spiritkin.ios.runtime_host.v1",
        "workspace_id": workspace_id,
        "registry": registry.snapshot(workspace_id=workspace_id),
        "checkpoints": checkpoints.snapshot(workspace_id=workspace_id),
        "controller_capabilities": {
            "register_ios_adapter": True,
            "request_host_election": True,
            "request_migration": True,
            "claim_execution_lease": False,
            "resume_workflow_directly": False,
        },
    }


def handle_ios_runtime_host_action(
    payload: dict[str, Any],
    *,
    workspace_id: str,
    actor_id: str,
    management: bool = False,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    registry, checkpoints = build_runtime_host_services(project_root=project_root)
    action = str(payload.get("action") or "snapshot").strip().lower()
    actor_id = str(actor_id or "ios-controller").strip()[:100] or "ios-controller"
    ios_host_id = f"ios:{actor_id}"
    result: dict[str, Any] = {}
    if action in {"snapshot", "refresh", "list"}:
        pass
    elif action in {"register", "register_ios_host", "heartbeat"}:
        existing = next(
            (item for item in registry.snapshot(workspace_id=workspace_id)["hosts"] if item.get("host_id") == ios_host_id),
            None,
        )
        if existing is None:
            result = {
                "host": registry.register_host(
                    host_id=ios_host_id,
                    workspace_id=workspace_id,
                    host_type="ios",
                    label=str(payload.get("label") or "iOS Controller"),
                    capabilities=payload.get("capabilities") or ["runtime.control", "observation.publish"],
                    can_execute_workflows=False,
                    can_observe=True,
                    priority=-10,
                    requested_by=actor_id,
                )
            }
        else:
            result = registry.heartbeat(ios_host_id, capabilities=payload.get("capabilities"), requested_by=actor_id)
    elif action in {"elect", "request_election"}:
        if not bool(payload.get("confirmed")):
            raise PermissionError("runtime host election requires explicit confirmation")
        result = {"lease": registry.elect(workspace_id, requested_by=actor_id)}
    elif action in {"request_migration", "prepare_migration"}:
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
                requested_by=actor_id,
                confirmed=bool(payload.get("confirmed")),
            )
        }
    elif action == "set_host_status":
        if not management:
            raise PermissionError("management token required to change another runtime host")
        result = {
            "host": registry.set_host_status(
                str(payload.get("host_id") or ""),
                str(payload.get("status") or "draining"),
                requested_by=actor_id,
            )
        }
    elif action in {"create_checkpoint", "claim_migration", "resume_checkpoint"}:
        if not management:
            raise PermissionError("execution-host operation requires management or host authority")
        if action == "create_checkpoint":
            result = {
                "checkpoint": checkpoints.create_checkpoint(
                    str(payload.get("run_id") or ""),
                    workspace_id=workspace_id,
                    host_id=str(payload.get("host_id") or ""),
                    fencing_token=str(payload.get("fencing_token") or ""),
                    epoch=int(payload.get("epoch") or 0),
                    reason=str(payload.get("reason") or "manual"),
                    requested_by=actor_id,
                )
            }
        elif action == "claim_migration":
            result = registry.claim_migration(
                str(payload.get("migration_id") or ""),
                target_host_id=str(payload.get("target_host_id") or ""),
                requested_by=actor_id,
                confirmed=bool(payload.get("confirmed")),
            )
        else:
            result = checkpoints.resume_checkpoint(
                str(payload.get("checkpoint_id") or ""),
                target_host_id=str(payload.get("target_host_id") or ""),
                fencing_token=str(payload.get("fencing_token") or ""),
                epoch=int(payload.get("epoch") or 0),
                requested_by=actor_id,
                confirmed=bool(payload.get("confirmed")),
            )
    else:
        raise ValueError(f"unsupported runtime host action: {action}")
    return {
        "ok": True,
        "action": action,
        "result": result,
        "runtime_host": build_ios_runtime_host_snapshot(workspace_id=workspace_id, project_root=project_root),
    }
