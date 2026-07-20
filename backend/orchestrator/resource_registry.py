from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RESOURCE_REGISTRY_SCHEMA_VERSION = "spiritkin.resource_registry.v1"
DEFAULT_RESOURCE_REGISTRY_PATH = "state/resources/resource_registry.json"


def normalize_resource_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", (value or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "resource:unknown"


def _merge_tuple(*items: Iterable[str]) -> tuple[str, ...]:
    values: list[str] = []
    for group in items:
        for item in group:
            text = str(item or "").strip()
            if text and text not in values:
                values.append(text)
    return tuple(values)


@dataclass(frozen=True)
class ResourceRecord:
    resource_id: str
    label: str
    resource_type: str = "generic"
    platform: str = ""
    owner_agent: str = ""
    credential_ref: str = ""
    state_ref: str = ""
    policies: dict[str, Any] = field(default_factory=dict)
    supported_capabilities: tuple[str, ...] = ()
    forbidden_capabilities: tuple[str, ...] = ()
    worker_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    health_status: str = "unknown"
    last_observed_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def merge(self, other: ResourceRecord) -> ResourceRecord:
        if normalize_resource_id(self.resource_id) != normalize_resource_id(other.resource_id):
            raise ValueError("cannot merge different resources")
        return ResourceRecord(
            resource_id=normalize_resource_id(other.resource_id or self.resource_id),
            label=other.label or self.label,
            resource_type=other.resource_type or self.resource_type,
            platform=other.platform or self.platform,
            owner_agent=other.owner_agent or self.owner_agent,
            credential_ref=other.credential_ref or self.credential_ref,
            state_ref=other.state_ref or self.state_ref,
            policies={**dict(self.policies or {}), **dict(other.policies or {})},
            supported_capabilities=_merge_tuple(self.supported_capabilities, other.supported_capabilities),
            forbidden_capabilities=_merge_tuple(self.forbidden_capabilities, other.forbidden_capabilities),
            worker_refs=_merge_tuple(self.worker_refs, other.worker_refs),
            tags=_merge_tuple(self.tags, other.tags),
            health_status=other.health_status or self.health_status,
            last_observed_at=float(other.last_observed_at or self.last_observed_at or 0.0),
            metadata={**dict(self.metadata or {}), **dict(other.metadata or {})},
        )

    def supports_capability(self, capability_id: str) -> bool:
        capability = str(capability_id or "").strip()
        return bool(capability and capability in self.supported_capabilities and capability not in self.forbidden_capabilities)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": RESOURCE_REGISTRY_SCHEMA_VERSION,
            "resource_id": normalize_resource_id(self.resource_id),
            "label": self.label,
            "resource_type": self.resource_type,
            "platform": self.platform,
            "owner_agent": self.owner_agent,
            "credential_ref": self.credential_ref,
            "state_ref": self.state_ref,
            "policies": dict(self.policies or {}),
            "supported_capabilities": list(self.supported_capabilities),
            "forbidden_capabilities": list(self.forbidden_capabilities),
            "worker_refs": list(self.worker_refs),
            "tags": list(self.tags),
            "health_status": self.health_status,
            "last_observed_at": self.last_observed_at,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class ResourceRegistrySnapshot:
    resources: tuple[ResourceRecord, ...]
    gaps: tuple[dict[str, Any], ...] = ()

    def snapshot(self) -> dict[str, Any]:
        type_counts: dict[str, int] = {}
        owner_counts: dict[str, int] = {}
        for resource in self.resources:
            type_counts[resource.resource_type] = type_counts.get(resource.resource_type, 0) + 1
            if resource.owner_agent:
                owner_counts[resource.owner_agent] = owner_counts.get(resource.owner_agent, 0) + 1
        return {
            "schema_version": RESOURCE_REGISTRY_SCHEMA_VERSION,
            "total": len(self.resources),
            "resources": [resource.snapshot() for resource in self.resources],
            "type_counts": type_counts,
            "owner_counts": owner_counts,
            "gaps": [dict(gap) for gap in self.gaps],
        }


class ResourceRegistry:
    """Registry for durable assets that Agents manage across runs."""

    def __init__(self, resources: Iterable[ResourceRecord] | None = None):
        self._resources: dict[str, ResourceRecord] = {}
        for resource in resources or ():
            self.register(resource)

    def register(self, resource: ResourceRecord) -> ResourceRecord:
        resource_id = normalize_resource_id(resource.resource_id)
        normalized = ResourceRecord(
            resource_id=resource_id,
            label=resource.label,
            resource_type=resource.resource_type,
            platform=resource.platform,
            owner_agent=resource.owner_agent,
            credential_ref=resource.credential_ref,
            state_ref=resource.state_ref,
            policies=dict(resource.policies or {}),
            supported_capabilities=tuple(resource.supported_capabilities),
            forbidden_capabilities=tuple(resource.forbidden_capabilities),
            worker_refs=tuple(resource.worker_refs),
            tags=tuple(resource.tags),
            health_status=resource.health_status,
            last_observed_at=float(resource.last_observed_at or time.time()),
            metadata=dict(resource.metadata or {}),
        )
        existing = self._resources.get(resource_id)
        stored = existing.merge(normalized) if existing is not None else normalized
        self._resources[resource_id] = stored
        return stored

    def get(self, resource_id: str) -> ResourceRecord | None:
        return self._resources.get(normalize_resource_id(resource_id))

    def list_records(
        self,
        *,
        owner_agent: str = "",
        resource_type: str = "",
        capability_id: str = "",
        include_unhealthy: bool = True,
    ) -> list[ResourceRecord]:
        records = list(self._resources.values())
        if owner_agent:
            records = [record for record in records if record.owner_agent == owner_agent]
        if resource_type:
            records = [record for record in records if record.resource_type == resource_type]
        if capability_id:
            records = [record for record in records if record.supports_capability(capability_id)]
        if not include_unhealthy:
            records = [record for record in records if record.health_status in {"ready", "degraded"}]
        return sorted(records, key=lambda item: item.resource_id)

    def snapshot(self, **filters: Any) -> dict[str, Any]:
        return ResourceRegistrySnapshot(resources=tuple(self.list_records(**filters)), gaps=self.gaps()).snapshot()

    def gaps(self) -> tuple[dict[str, Any], ...]:
        gaps: list[dict[str, Any]] = []
        for resource in self.list_records():
            if not resource.owner_agent:
                gaps.append({"gap_id": "resource_owner_missing", "resource_id": resource.resource_id, "priority": "medium"})
            if not resource.supported_capabilities:
                gaps.append({"gap_id": "resource_capabilities_missing", "resource_id": resource.resource_id, "priority": "medium"})
            if resource.credential_ref and resource.credential_ref.startswith(("plain:", "env:")):
                gaps.append({"gap_id": "resource_credential_ref_weak", "resource_id": resource.resource_id, "priority": "high"})
        return tuple(gaps)


class JsonResourceRegistryStore:
    """JSON persistence for durable Resource records.

    The store owns user/configured resources only. Runtime-derived resources can
    be merged into an in-memory registry without being written unless callers
    explicitly save them.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = resolve_resource_registry_path(path)

    def load(self) -> ResourceRegistry:
        return load_resource_registry(self.path)

    def save(self, registry: ResourceRegistry) -> dict[str, Any]:
        return save_resource_registry(registry, self.path)


def resolve_resource_registry_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_RESOURCE_REGISTRY_PATH") or DEFAULT_RESOURCE_REGISTRY_PATH
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def load_resource_registry(path: str | os.PathLike[str] | None = None) -> ResourceRegistry:
    target = resolve_resource_registry_path(path)
    if not target.exists():
        return ResourceRegistry()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ResourceRegistry()
    if not isinstance(payload, dict):
        return ResourceRegistry()
    raw_resources = payload.get("resources")
    if not isinstance(raw_resources, list):
        return ResourceRegistry()
    registry = ResourceRegistry()
    for item in raw_resources:
        if not isinstance(item, dict):
            continue
        try:
            registry.register(resource_from_snapshot(item))
        except Exception:
            continue
    return registry


def save_resource_registry(registry: ResourceRegistry, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_resource_registry_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot = registry.snapshot()
    payload = {
        "schema_version": RESOURCE_REGISTRY_SCHEMA_VERSION,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "resources": snapshot["resources"],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**payload, "path": str(target)}


def resource_from_snapshot(payload: dict[str, Any]) -> ResourceRecord:
    return ResourceRecord(
        resource_id=str(payload.get("resource_id") or payload.get("id") or ""),
        label=str(payload.get("label") or payload.get("resource_id") or "Resource"),
        resource_type=str(payload.get("resource_type") or payload.get("type") or "generic"),
        platform=str(payload.get("platform") or ""),
        owner_agent=str(payload.get("owner_agent") or payload.get("owner") or ""),
        credential_ref=str(payload.get("credential_ref") or ""),
        state_ref=str(payload.get("state_ref") or ""),
        policies=dict(payload.get("policies") or {}) if isinstance(payload.get("policies"), dict) else {},
        supported_capabilities=_string_tuple(payload.get("supported_capabilities")),
        forbidden_capabilities=_string_tuple(payload.get("forbidden_capabilities")),
        worker_refs=_string_tuple(payload.get("worker_refs")),
        tags=_string_tuple(payload.get("tags")),
        health_status=str(payload.get("health_status") or "unknown"),
        last_observed_at=_coerce_float(payload.get("last_observed_at"), fallback=0.0),
        metadata=dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
    )


def resource_from_worker_descriptor(worker: Any) -> ResourceRecord:
    snapshot = worker.snapshot() if hasattr(worker, "snapshot") else dict(worker or {})
    worker_id = str(snapshot.get("worker_id") or "worker:unknown")
    capabilities = _merge_tuple(
        tuple(str(item) for item in snapshot.get("capabilities") or ()),
        tuple(str(item) for item in snapshot.get("capability_namespaces") or ()),
        tuple(str(item) for item in snapshot.get("targets") or ()),
        tuple(str(item) for item in snapshot.get("operations") or ()),
    )
    return ResourceRecord(
        resource_id=f"worker:{worker_id}",
        label=str(snapshot.get("label") or worker_id),
        resource_type="worker",
        platform=str(snapshot.get("worker_type") or snapshot.get("kind") or ""),
        owner_agent=str((snapshot.get("metadata") or {}).get("owner_agent") or ""),
        supported_capabilities=capabilities,
        worker_refs=(worker_id,),
        health_status=str(snapshot.get("health_status") or "unknown"),
        metadata={"source": "worker_descriptor", "worker": snapshot},
    )


def local_device_resource_record(*, device_name: str, device_ready: bool) -> ResourceRecord:
    return ResourceRecord(
        resource_id="device:local_pc",
        label=str(device_name or "local_pc"),
        resource_type="device",
        platform="desktop",
        owner_agent="main_text",
        supported_capabilities=(
            "desktop",
            "software.list",
            "hardware.list",
            "screen.capture",
            "browser_open_url",
        ),
        worker_refs=("executor:local_pc",),
        health_status="ready" if device_ready else "unknown",
        metadata={"source": "agent_cluster", "device_name": device_name},
    )


def repository_resource_record(*, path: str) -> ResourceRecord:
    return ResourceRecord(
        resource_id=f"repo:{path}",
        label=os.path.basename(path) or path,
        resource_type="repository",
        platform="git",
        owner_agent="programming",
        supported_capabilities=("git.status", "git.diff", "code.generate", "code.review"),
        worker_refs=("executor:git_worker",),
        health_status="ready",
        metadata={"source": "agent_cluster", "path": path},
    )


def commerce_project_resource_record(project: dict[str, Any]) -> ResourceRecord | None:
    project_id = str(project.get("project_id") or "").strip()
    if not project_id:
        return None
    return ResourceRecord(
        resource_id=f"commerce_project:{project_id}",
        label=str(project.get("goal") or project_id),
        resource_type="commerce_project",
        platform=str(project.get("project_type") or "commerce"),
        owner_agent="ecommerce",
        state_ref=f"ecommerce_project:{project_id}",
        supported_capabilities=(
            "commerce.product.publish",
            "commerce.price.update",
            "commerce.project.review",
        ),
        tags=("ecommerce", str(project.get("current_phase") or "")),
        health_status="ready" if str(project.get("status") or "") not in {"blocked", "failed"} else "degraded",
        metadata={"source": "agent_cluster", "project": project},
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _coerce_float(value: Any, *, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def build_resource_registry_store(path: str | os.PathLike[str] | None = None) -> JsonResourceRegistryStore | None:
    """Create the JSON store only when a path is configured explicitly or via env."""
    if path is None and not os.getenv("SPIRITKIN_RESOURCE_REGISTRY_PATH", "").strip():
        return None
    try:
        return JsonResourceRegistryStore(path)
    except Exception:
        return None


def register_runtime_resources(
    registry: ResourceRegistry,
    *,
    workers: Iterable[Any] = (),
    device_name: str = "",
    device_ready: bool = False,
    projects: Iterable[Any] = (),
) -> None:
    """Register live runtime resources (workers, local device, repo, commerce projects)."""
    records: list[ResourceRecord] = []
    for worker in workers:
        try:
            records.append(resource_from_worker_descriptor(worker))
        except Exception:
            continue
    records.append(local_device_resource_record(device_name=device_name, device_ready=device_ready))
    records.append(repository_resource_record(path=os.getcwd()))
    for project in projects:
        if not isinstance(project, dict):
            continue
        record = commerce_project_resource_record(project)
        if record is not None:
            records.append(record)
    for record in records:
        try:
            registry.register(record)
        except Exception:
            continue
