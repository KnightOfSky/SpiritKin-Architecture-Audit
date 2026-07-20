from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.orchestrator.runtime_host import RuntimeHostRegistry
from backend.world import ObservationRuntime, ObservationStore, WorldStateStore


def build_world_services(*, project_root: str | Path | None = None) -> tuple[RuntimeHostRegistry, ObservationRuntime]:
    root = Path(project_root or Path.cwd()).resolve()
    registry = RuntimeHostRegistry(root / "state" / "runtime" / "hosts.json")
    runtime = ObservationRuntime(
        observation_store=ObservationStore(root / "state" / "world" / "observations.jsonl"),
        world_store=WorldStateStore(root / "state" / "world" / "world_state.json"),
    )
    return registry, runtime


def build_ios_world_snapshot(*, workspace_id: str, project_root: str | Path | None = None) -> dict[str, Any]:
    _, runtime = build_world_services(project_root=project_root)
    return {
        "schema_version": "spiritkin.ios.world.v1",
        "workspace_id": workspace_id,
        "world": runtime.world.snapshot(workspace_id=workspace_id),
        "observations": runtime.observations.snapshot(workspace_id=workspace_id, limit=20),
        "provider_contract": {
            "raw_sensor_data_accepted": False,
            "accepted_provider": "structured_observation_only",
            "world_state_long_term": True,
        },
    }


def ingest_ios_observation(
    payload: dict[str, Any],
    *,
    workspace_id: str,
    actor_id: str,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    registry, runtime = build_world_services(project_root=project_root)
    actor_id = str(actor_id or "ios-controller").strip()[:100] or "ios-controller"
    host_id = f"ios:{actor_id}"
    provider_id = f"arkit:{actor_id}"
    host = next(
        (item for item in registry.snapshot(workspace_id=workspace_id)["hosts"] if item.get("host_id") == host_id),
        None,
    )
    if host is None:
        registry.register_host(
            host_id=host_id,
            workspace_id=workspace_id,
            host_type="ios",
            label="iOS Observation Host",
            capabilities=["runtime.control", "observation.publish", "arkit.world_tracking"],
            can_execute_workflows=False,
            can_observe=True,
            priority=-10,
            requested_by=actor_id,
        )
    else:
        registry.heartbeat(
            host_id,
            capabilities=["runtime.control", "observation.publish", "arkit.world_tracking"],
            requested_by=actor_id,
        )
    observation_payload = (
        payload.get("observation")
        if isinstance(payload.get("observation"), dict)
        else {key: value for key, value in payload.items() if key not in {"action", "confirmed"}}
    )
    result = runtime.ingest(
        dict(observation_payload),
        workspace_id=workspace_id,
        host_id=host_id,
        provider_id=provider_id,
    )
    return {
        **result,
        "world": runtime.world.snapshot(workspace_id=workspace_id),
    }


def handle_ios_world_action(
    payload: dict[str, Any],
    *,
    workspace_id: str,
    actor_id: str,
    management: bool = False,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh", "list"}:
        result: dict[str, Any] = {}
    elif action in {"ingest", "publish_observation"}:
        result = ingest_ios_observation(
            payload,
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_root=project_root,
        )
    elif action == "cleanup_observations":
        if not management:
            raise PermissionError("management token required to clean observations")
        _, runtime = build_world_services(project_root=project_root)
        result = runtime.observations.cleanup()
    else:
        raise ValueError(f"unsupported world action: {action}")
    return {
        "ok": True,
        "action": action,
        "result": result,
        "world_state": build_ios_world_snapshot(workspace_id=workspace_id, project_root=project_root),
    }
