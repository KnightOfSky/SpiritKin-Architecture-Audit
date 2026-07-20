from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.orchestrator.capability_graph import (
    CapabilityBinding,
    CapabilityRecord,
    capability_id_for_target_operation,
)
from backend.orchestrator.worker_pool import WorkerDescriptor

if TYPE_CHECKING:
    from backend.mobile.android_companion_store import AndroidCompanionStore


ANDROID_WORKER_ID = "android_control_worker"
ANDROID_WORKER_TARGET = "android_device"


def load_android_worker_inventory(store: AndroidCompanionStore | None = None) -> dict[str, Any]:
    from backend.mobile.android_companion_store import AndroidCompanionStore

    companion = (store or AndroidCompanionStore()).snapshot()
    worker = dict(companion.get("worker") or {})
    return {
        "companion": companion,
        "worker": worker,
        "descriptor": android_worker_descriptor(worker, companion=companion).snapshot(),
        "capabilities": [record.snapshot() for record in android_worker_capability_records(worker, companion=companion)],
    }


def android_worker_descriptor(worker: dict[str, Any], *, companion: dict[str, Any] | None = None) -> WorkerDescriptor:
    companion = dict(companion or {})
    queue = dict(worker.get("queue") or {})
    operations = tuple(_android_worker_operations(worker, companion=companion))
    status = str(worker.get("status") or "needs_pairing")
    permission_scope = "android_controlled"
    if status in {"needs_pairing", "endpoint_offline", "offline"}:
        health_status = "unavailable"
    elif status in {"needs_attention", "degraded"}:
        health_status = "degraded"
    else:
        health_status = "ready"
    workspace_ids = [str(item) for item in worker.get("workspace_ids") or [] if str(item).strip()]
    devices = [_android_worker_device_metadata(device) for device in companion.get("devices") or [] if isinstance(device, dict)]
    online_devices = [device for device in devices if bool(device.get("online"))]
    return WorkerDescriptor(
        worker_id=str(worker.get("worker_id") or ANDROID_WORKER_ID),
        label=str(worker.get("label") or "Android Control Worker"),
        kind="android_control_worker",
        worker_type="device_worker",
        worker_subtype="android_device_worker",
        capabilities=operations,
        capability_namespaces=("android", "adb", "pdd", "artifact", "image"),
        targets=(ANDROID_WORKER_TARGET,),
        operations=operations,
        legacy_names=("Android Bridge", "ADB"),
        workspace=",".join(workspace_ids),
        permission_scope=permission_scope,
        health_status=health_status,
        health_detail=status,
        queue_depth=int(queue.get("pending") or worker.get("pending_command_count") or companion.get("pending_command_count") or 0),
        metadata={
            "schema_version": str(worker.get("schema_version") or "spiritkin.android_worker.v1"),
            "role": str(worker.get("role") or "controlled_execution_worker"),
            "device_count": int(worker.get("device_count") or companion.get("device_count") or 0),
            "online_device_count": int(worker.get("online_device_count") or 0),
            "inflight_command_count": int(worker.get("inflight_command_count") or 0),
            "permission_gap_count": int(worker.get("permission_gap_count") or 0),
            "queue": queue,
            "lifecycle": dict(worker.get("lifecycle") or {}),
            "architecture": dict(worker.get("architecture") or {}),
            "devices": devices[:20],
            "online_devices": online_devices[:20],
            "default_device_id": str((online_devices[0] if online_devices else devices[0] if devices else {}).get("device_id") or ""),
        },
    )


def android_worker_capability_records(worker: dict[str, Any], *, companion: dict[str, Any] | None = None) -> list[CapabilityRecord]:
    from backend.mobile.android_permissions import classify_android_operation

    companion = dict(companion or {})
    records: list[CapabilityRecord] = []
    for operation in _android_worker_operations(worker, companion=companion):
        permission = classify_android_operation(operation).snapshot()
        capability_id = capability_id_for_target_operation(ANDROID_WORKER_TARGET, operation)
        records.append(
            CapabilityRecord(
                capability_id=capability_id,
                label=f"Android {operation}",
                description=f"Queue Android controlled-worker operation `{operation}` through the mobile management command path.",
                domain="mobile",
                owner_agents=("ecommerce", "main_text"),
                worker_requirements=(ANDROID_WORKER_ID,),
                policy_refs=(f"risk:{permission.get('risk_level')}", f"android_permission_tier:{permission.get('tier')}"),
                tags=("android", "worker_pool", str(permission.get("tier") or "")),
                bindings=(
                    CapabilityBinding(
                        binding_type="android_worker",
                        binding_id=ANDROID_WORKER_ID,
                        target=ANDROID_WORKER_TARGET,
                        operation=operation,
                        risk_level=str(permission.get("risk_level") or "high"),
                        read_only=bool(permission.get("read_only")),
                        metadata={"permission": permission, "queued_execution": True},
                    ),
                ),
                metadata={
                    "execution_boundary": "queued_android_command",
                    "requires_mobile_management": True,
                    "source": "AndroidCompanionStore.worker",
                },
            )
        )
    return records


def _android_worker_operations(worker: dict[str, Any], *, companion: dict[str, Any]) -> list[str]:
    operations: list[str] = []
    for value in worker.get("capabilities") or []:
        text = str(value or "").strip()
        if "." in text and text not in operations:
            operations.append(text)
    for device in companion.get("devices") or []:
        if not isinstance(device, dict):
            continue
        for raw in device.get("command_catalog") or []:
            if not isinstance(raw, dict):
                continue
            operation = str(raw.get("operation") or "").strip()
            if operation and operation not in operations:
                operations.append(operation)
    if operations:
        return sorted(operations)
    return [
        "app.launch",
        "url.open",
        "clipboard.write",
        "android.open_bridge",
        "android.ui_snapshot",
        "android.screenshot.request_permission",
        "android.screenshot.capture",
        "artifact.download",
        "image.share_to_app",
        "pdd.launch",
        "pdd.share_image",
        "pdd.create_listing",
    ]


def _android_worker_device_metadata(device: dict[str, Any]) -> dict[str, Any]:
    posture = device.get("permission_posture") if isinstance(device.get("permission_posture"), dict) else {}
    operations = [
        str(item.get("operation") or "").strip()
        for item in posture.get("operations") or []
        if isinstance(item, dict) and bool(item.get("available")) and str(item.get("operation") or "").strip()
    ]
    return {
        "device_id": str(device.get("device_id") or "").strip(),
        "workspace_id": str(device.get("workspace_id") or "").strip(),
        "online": bool(device.get("online")),
        "current_app": str(device.get("current_app") or device.get("foreground_package") or "").strip(),
        "pending_command_count": int(device.get("pending_command_count") or 0),
        "inflight_command_count": int(device.get("inflight_command_count") or 0),
        "permission_posture_status": str(posture.get("status") or ""),
        "available_operations": operations[:40],
    }
