from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.orchestrator.capability_graph import CapabilityRegistry
from backend.orchestrator.runtime_metadata import RuntimeMetadata, normalize_runtime_metadata
from backend.runtime import ProviderContract, lifecycle_snapshot, object_state_snapshot
from backend.security.safety_control import evaluate_execution_safety

WORKER_POOL_SCHEMA_VERSION = "spiritkin.worker_pool.v1"
WORKER_TAXONOMY_SCHEMA_VERSION = "spiritkin.worker_taxonomy.v1"


LEGACY_WORKER_POSITIONING: tuple[dict[str, str], ...] = (
    {
        "old_name": "Android Bridge",
        "new_positioning": "Android Device Worker",
        "worker_type": "device_worker",
        "capability_boundary": "android.adb/android.ui/pdd queued commands",
    },
    {
        "old_name": "OpenClaw",
        "new_positioning": "Desktop Device Worker",
        "worker_type": "device_worker",
        "capability_boundary": "openclaw arm/gripper operations",
    },
    {
        "old_name": "Remote Worker",
        "new_positioning": "Generic Remote Worker",
        "worker_type": "generic_remote_worker",
        "capability_boundary": "remote runtime advertised capabilities",
    },
    {
        "old_name": "Browser Automation",
        "new_positioning": "Browser Worker",
        "worker_type": "browser_worker",
        "capability_boundary": "browser/playwright operations",
    },
    {
        "old_name": "ADB",
        "new_positioning": "Android Worker Capability",
        "worker_type": "device_worker",
        "capability_boundary": "adb.* capability namespace",
    },
    {
        "old_name": "Playwright",
        "new_positioning": "Browser Worker Capability",
        "worker_type": "browser_worker",
        "capability_boundary": "playwright/browser capability namespace",
    },
    {
        "old_name": "FFmpeg",
        "new_positioning": "Media Worker",
        "worker_type": "execution_worker",
        "capability_boundary": "ffmpeg/media capability namespace",
    },
    {
        "old_name": "Python Runtime",
        "new_positioning": "Python Worker",
        "worker_type": "execution_worker",
        "capability_boundary": "python execution capability namespace",
    },
)


PLANNED_WORKER_SEED_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "worker_id": "planned:python_worker",
        "label": "Python Worker",
        "worker_type": "execution_worker",
        "worker_subtype": "python_worker",
        "capabilities": ("python.execute", "python.script", "python.runtime"),
        "capability_namespaces": ("python",),
        "targets": ("python", "local_runtime"),
        "operations": ("python.run", "python.execute"),
        "legacy_names": ("Python Runtime",),
        "permission_scope": "workspace",
    },
    {
        "worker_id": "planned:ffmpeg_worker",
        "label": "FFmpeg Worker",
        "worker_type": "execution_worker",
        "worker_subtype": "ffmpeg_worker",
        "capabilities": ("ffmpeg.transcode", "ffmpeg.probe", "media.render"),
        "capability_namespaces": ("ffmpeg", "media"),
        "targets": ("ffmpeg", "media"),
        "operations": ("ffmpeg.transcode", "ffmpeg.probe"),
        "legacy_names": ("FFmpeg",),
        "permission_scope": "workspace",
    },
    {
        "worker_id": "planned:git_worker",
        "label": "Git Worker",
        "worker_type": "execution_worker",
        "worker_subtype": "git_worker",
        "capabilities": ("git.status", "git.diff", "git.commit"),
        "capability_namespaces": ("git", "vcs"),
        "targets": ("git", "repository"),
        "operations": ("git.status", "git.diff", "git.commit"),
        "legacy_names": ("Git",),
        "permission_scope": "workspace",
    },
    {
        "worker_id": "planned:service_rag_worker",
        "label": "Service RAG Worker",
        "worker_type": "service_worker",
        "worker_subtype": "service_rag_worker",
        "capabilities": ("rag.search", "embedding.create", "knowledge.retrieve"),
        "capability_namespaces": ("rag", "embedding", "knowledge"),
        "targets": ("knowledge", "vector_store"),
        "operations": ("rag.search", "knowledge.retrieve", "embedding.create"),
        "legacy_names": ("RAG Worker", "Embedding Service"),
        "permission_scope": "local_service",
    },
)


@dataclass(frozen=True)
class WorkerDescriptor:
    worker_id: str
    label: str
    kind: str = "executor"
    worker_type: str = "execution_worker"
    worker_subtype: str = ""
    capabilities: tuple[str, ...] = ()
    capability_namespaces: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    legacy_names: tuple[str, ...] = ()
    workspace: str = ""
    permission_scope: str = "local"
    health_status: str = "unknown"
    health_detail: str = ""
    queue_depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def runtime_metadata(self) -> RuntimeMetadata:
        return normalize_runtime_metadata(
            self.metadata,
            object_type="worker",
            object_id=self.worker_id,
            defaults={
                "domain": self.metadata.get("domain") or "worker",
                "owner": self.metadata.get("owner") or "worker_pool",
                "version": self.metadata.get("version") or "1.0.0",
                "status": "active" if self.health_status in {"ready", "busy"} else self.metadata.get("status") or "candidate",
                "risk_level": self.metadata.get("risk_level") or "medium",
                "permission_scope": self.permission_scope,
                "tags": (self.worker_type, self.worker_subtype, *self.capability_namespaces),
                "benchmark_refs": self.metadata.get("benchmark_refs") or (),
                "dependency_refs": self.metadata.get("dependency_refs") or self.capabilities,
            },
        )

    def provider_contract(self) -> ProviderContract:
        return ProviderContract(
            provider_id=self.worker_id,
            provider_type="worker",
            capabilities=self.capabilities,
            status=self.health_status,
            locality=str(self.metadata.get("locality") or ("workspace" if self.workspace else "local")),
            permission=self.permission_scope,
            metadata={"worker_type": self.worker_type, "worker_subtype": self.worker_subtype},
        )

    def snapshot(self) -> dict[str, Any]:
        metadata = self.runtime_metadata()
        return {
            "worker_id": self.worker_id,
            "label": self.label,
            "kind": self.kind,
            "worker_type": self.worker_type,
            "worker_subtype": self.worker_subtype,
            "capabilities": list(self.capabilities),
            "capability_namespaces": list(self.capability_namespaces),
            "targets": list(self.targets),
            "operations": list(self.operations),
            "legacy_names": list(self.legacy_names),
            "workspace": self.workspace,
            "permission_scope": self.permission_scope,
            "health_status": self.health_status,
            "health_detail": self.health_detail,
            "queue_depth": self.queue_depth,
            "metadata": dict(self.metadata or {}),
            "runtime_metadata": metadata.snapshot(),
            "lifecycle": lifecycle_snapshot(object_type="worker", object_id=self.worker_id, status=metadata.status),
            "state_machine": object_state_snapshot(object_type="worker", object_id=self.worker_id, state=self.health_status),
            "provider_contract": self.provider_contract().snapshot(),
        }


def planned_worker_seed_descriptors() -> tuple[WorkerDescriptor, ...]:
    return tuple(
        WorkerDescriptor(
            worker_id=str(seed["worker_id"]),
            label=str(seed["label"]),
            kind="planned",
            worker_type=str(seed["worker_type"]),
            worker_subtype=str(seed["worker_subtype"]),
            capabilities=tuple(seed["capabilities"]),
            capability_namespaces=tuple(seed["capability_namespaces"]),
            targets=tuple(seed["targets"]),
            operations=tuple(seed["operations"]),
            legacy_names=tuple(seed.get("legacy_names") or ()),
            permission_scope=str(seed["permission_scope"]),
            health_status="planned",
            health_detail="Descriptor seed only; no executable worker is registered.",
            metadata={"maturity": "planned", "schedulable": False, "seed": True},
        )
        for seed in PLANNED_WORKER_SEED_DEFINITIONS
    )


@dataclass(frozen=True)
class WorkerRequirement:
    needs: tuple[str, ...] = ()
    worker_type: str = ""
    worker_subtype: str = ""
    target: str = ""
    operation: str = ""
    workspace: str = ""
    permission_scope: str = ""
    prefer_remote: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "needs": list(self.needs),
            "worker_type": self.worker_type,
            "worker_subtype": self.worker_subtype,
            "target": self.target,
            "operation": self.operation,
            "workspace": self.workspace,
            "permission_scope": self.permission_scope,
            "prefer_remote": self.prefer_remote,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class WorkerCandidate:
    worker: WorkerDescriptor
    score: int
    matched_needs: tuple[str, ...] = ()
    missing_needs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "worker": self.worker.snapshot(),
            "score": self.score,
            "matched_needs": list(self.matched_needs),
            "missing_needs": list(self.missing_needs),
            "reasons": list(self.reasons),
            "penalties": list(self.penalties),
        }


@dataclass(frozen=True)
class WorkerScheduleDecision:
    requirement: WorkerRequirement
    selected: WorkerDescriptor | None
    candidates: tuple[WorkerCandidate, ...] = ()
    rejected: tuple[WorkerCandidate, ...] = ()
    status: str = "selected"
    reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement.snapshot(),
            "selected": self.selected.snapshot() if self.selected is not None else None,
            "candidates": [candidate.snapshot() for candidate in self.candidates],
            "rejected": [candidate.snapshot() for candidate in self.rejected],
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WorkerAuditEvent:
    event_id: str
    worker_id: str
    target: str
    operation: str
    status: str
    started_at: float
    finished_at: float
    message: str = ""
    error_code: str = ""
    capability_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "worker_id": self.worker_id,
            "target": self.target,
            "operation": self.operation,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round((self.finished_at - self.started_at) * 1000, 2),
            "message": self.message,
            "error_code": self.error_code,
            "capability_id": self.capability_id,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class WorkerExecutionResult:
    result: ExecutionResult
    worker: WorkerDescriptor | None
    audit_event: WorkerAuditEvent

    def snapshot(self) -> dict[str, Any]:
        return {
            "result": {
                "success": self.result.success,
                "message": self.result.message,
                "data": self.result.data,
                "error_code": self.result.error_code,
                "metadata": dict(self.result.metadata or {}),
            },
            "worker": self.worker.snapshot() if self.worker is not None else None,
            "audit_event": self.audit_event.snapshot(),
        }


class WorkerPool:
    """Managed execution layer for deterministic workers and legacy executors."""

    def __init__(
        self,
        executors: Iterable[BaseExecutor] | None = None,
        *,
        capability_registry: CapabilityRegistry | None = None,
        external_workers: Iterable[WorkerDescriptor | dict[str, Any]] | None = None,
        audit_limit: int = 100,
    ):
        self._executors = list(executors or [])
        self._capability_registry = capability_registry
        self._external_workers = [self._normalize_external_worker(item) for item in external_workers or ()]
        self._audit_limit = max(1, int(audit_limit))
        self._audit: list[WorkerAuditEvent] = []

    @property
    def executors(self) -> list[BaseExecutor]:
        return list(self._executors)

    def set_capability_registry(self, registry: CapabilityRegistry | None) -> None:
        self._capability_registry = registry

    def set_external_workers(self, workers: Iterable[WorkerDescriptor | dict[str, Any]] | None) -> None:
        self._external_workers = [self._normalize_external_worker(item) for item in workers or ()]

    def find_worker(self, request: ExecutionRequest) -> tuple[BaseExecutor | None, WorkerDescriptor | None]:
        first_unhealthy: WorkerDescriptor | None = None
        for executor in self._executors:
            try:
                if executor.supports(request):
                    return executor, self._descriptor_for_executor(executor, request=request, health_status="ready")
            except Exception as exc:
                if first_unhealthy is None:
                    first_unhealthy = self._descriptor_for_executor(executor, request=request, health_status="unhealthy", health_detail=str(exc))
                continue
        return None, first_unhealthy

    def supports(self, request: ExecutionRequest) -> bool:
        executor, _ = self.find_worker(request)
        return executor is not None

    def schedule(self, requirement: WorkerRequirement | dict[str, Any] | str, *, limit: int = 5) -> WorkerScheduleDecision:
        normalized = normalize_worker_requirement(requirement)
        workers = self._all_descriptors()
        candidates: list[WorkerCandidate] = []
        rejected: list[WorkerCandidate] = []
        for worker in workers:
            candidate = _score_worker_candidate(worker, normalized)
            if candidate.missing_needs or worker.health_status in {"unavailable", "offline", "stale"}:
                rejected.append(candidate)
            else:
                candidates.append(candidate)
        candidates.sort(key=lambda item: (-item.score, item.worker.queue_depth, item.worker.worker_id))
        rejected.sort(key=lambda item: (-item.score, item.worker.queue_depth, item.worker.worker_id))
        selected = candidates[0].worker if candidates else None
        return WorkerScheduleDecision(
            requirement=normalized,
            selected=selected,
            candidates=tuple(candidates[: max(1, int(limit))]),
            rejected=tuple(rejected[: max(1, int(limit))]),
            status="selected" if selected is not None else "missing",
            reason=_schedule_reason(selected, candidates, rejected, normalized),
        )

    def execute(self, request: ExecutionRequest, *, actor: str = "", dry_run: bool = False, metadata: dict[str, Any] | None = None) -> WorkerExecutionResult:
        started_at = time.time()
        executor, worker = self.find_worker(request)
        capability = self._capability_registry.resolve_execution_request(request) if self._capability_registry is not None else None
        capability_id = capability.capability_id if capability is not None else ""
        if executor is None:
            result = ExecutionResult(
                success=False,
                message=f"当前没有可用 Worker 处理 {request.target}.{request.operation}。",
                error_code="worker_not_found",
                metadata={"target": request.target, "operation": request.operation},
            )
            event = self._record_audit(
                worker_id=worker.worker_id if worker is not None else "worker_missing",
                request=request,
                status="missing",
                started_at=started_at,
                result=result,
                capability_id=capability_id,
                metadata=metadata,
            )
            return WorkerExecutionResult(result=result, worker=worker, audit_event=event)

        safety = evaluate_execution_safety(
            target=f"worker_pool:{request.target}",
            operation=request.operation,
            actor=actor,
            read_only=self._request_is_read_only(request),
            dry_run=dry_run,
        )
        if not safety.allowed:
            result = ExecutionResult(
                success=False,
                message=safety.message,
                error_code=safety.error_code,
                metadata={"safety": safety.snapshot(), "worker_id": worker.worker_id},
            )
            event = self._record_audit(
                worker_id=worker.worker_id,
                request=request,
                status="blocked",
                started_at=started_at,
                result=result,
                capability_id=capability_id,
                metadata=metadata,
            )
            return WorkerExecutionResult(result=result, worker=worker, audit_event=event)

        if dry_run:
            result = ExecutionResult(
                success=True,
                message=f"Worker dry-run 计划完成: {worker.worker_id}",
                data={"target": request.target, "operation": request.operation, "params": dict(request.params or {})},
                metadata={"worker_id": worker.worker_id, "dry_run": True},
            )
        else:
            try:
                result = executor.execute(request)
            except Exception as exc:
                result = ExecutionResult(
                    success=False,
                    message=str(exc),
                    error_code="worker_exception",
                    metadata={"worker_id": worker.worker_id, "executor": getattr(executor, "name", executor.__class__.__name__)},
                )
        status = "succeeded" if result.success else "failed"
        event = self._record_audit(
            worker_id=worker.worker_id,
            request=request,
            status=status,
            started_at=started_at,
            result=result,
            capability_id=capability_id,
            metadata=metadata,
        )
        result.metadata = {**dict(result.metadata or {}), "worker_id": worker.worker_id, "worker_audit_id": event.event_id}
        if capability_id:
            result.metadata["capability_id"] = capability_id
        return WorkerExecutionResult(result=result, worker=worker, audit_event=event)

    def snapshot(self) -> dict[str, Any]:
        descriptor_objects = self._all_descriptors()
        descriptors = [descriptor.snapshot() for descriptor in descriptor_objects if descriptor.kind == "executor"]
        external_descriptors = [descriptor.snapshot() for descriptor in descriptor_objects if descriptor.kind != "executor"]
        planned_descriptors = [descriptor.snapshot() for descriptor in planned_worker_seed_descriptors()]
        taxonomy = _build_worker_taxonomy(descriptors + external_descriptors)
        taxonomy.update(_build_planned_worker_taxonomy(planned_descriptors))
        return {
            "schema_version": WORKER_POOL_SCHEMA_VERSION,
            "total": len(descriptors) + len(external_descriptors),
            "executor_total": len(descriptors),
            "external_total": len(external_descriptors),
            "planned_total": len(planned_descriptors),
            "workers": descriptors + external_descriptors,
            "planned_workers": planned_descriptors,
            "taxonomy": taxonomy,
            "audit": [event.snapshot() for event in self._audit[-self._audit_limit :]],
        }

    def _all_descriptors(self) -> list[WorkerDescriptor]:
        return [self._descriptor_for_executor(executor) for executor in self._executors] + list(self._external_workers)

    @staticmethod
    def _normalize_external_worker(item: WorkerDescriptor | dict[str, Any]) -> WorkerDescriptor:
        if isinstance(item, WorkerDescriptor):
            return item
        payload = dict(item or {})
        capabilities = _tuple_from_values(payload.get("capabilities") or ())
        targets = _tuple_from_values(payload.get("targets") or ())
        operations = _tuple_from_values(payload.get("operations") or ())
        profile = _infer_worker_profile(
            name=str(payload.get("worker_id") or payload.get("label") or "external_worker"),
            kind=str(payload.get("kind") or "external"),
            targets=targets,
            operations=operations,
            capabilities=capabilities,
            metadata=dict(payload.get("metadata") or {}),
        )
        return WorkerDescriptor(
            worker_id=str(payload.get("worker_id") or "external_worker"),
            label=str(payload.get("label") or payload.get("worker_id") or "External Worker"),
            kind=str(payload.get("kind") or "external"),
            worker_type=str(payload.get("worker_type") or profile["worker_type"]),
            worker_subtype=str(payload.get("worker_subtype") or profile["worker_subtype"]),
            capabilities=capabilities,
            capability_namespaces=_tuple_from_values(payload.get("capability_namespaces") or profile["capability_namespaces"]),
            targets=targets,
            operations=operations,
            legacy_names=_tuple_from_values(payload.get("legacy_names") or profile["legacy_names"]),
            workspace=str(payload.get("workspace") or ""),
            permission_scope=str(payload.get("permission_scope") or "external"),
            health_status=str(payload.get("health_status") or "unknown"),
            health_detail=str(payload.get("health_detail") or ""),
            queue_depth=int(payload.get("queue_depth") or 0),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _request_is_read_only(self, request: ExecutionRequest) -> bool:
        if self._capability_registry is None:
            return False
        capability = self._capability_registry.resolve_execution_request(request)
        if capability is None:
            return False
        matching = [
            binding
            for binding in capability.bindings
            if binding.target == request.target and binding.operation == request.operation
        ]
        return bool(matching) and all(binding.read_only for binding in matching)

    def _descriptor_for_executor(
        self,
        executor: BaseExecutor,
        *,
        request: ExecutionRequest | None = None,
        health_status: str = "ready",
        health_detail: str = "",
    ) -> WorkerDescriptor:
        name = str(getattr(executor, "name", "") or executor.__class__.__name__).strip()
        targets = tuple(str(item).strip() for item in getattr(executor, "supported_targets", ()) or () if str(item).strip())
        operations = tuple(str(item).strip() for item in getattr(executor, "supported_operations", ()) or () if str(item).strip())
        capabilities: list[str] = []
        if self._capability_registry is not None:
            if request is not None:
                capability = self._capability_registry.resolve_execution_request(request)
                if capability is not None:
                    capabilities.append(capability.capability_id)
            else:
                for record in self._capability_registry.list_records():
                    executor_bindings = {binding.binding_id for binding in record.bindings if binding.binding_type == "executor"}
                    if name in executor_bindings:
                        capabilities.append(record.capability_id)
                        continue
                    for binding in record.bindings:
                        if not binding.target or not binding.operation:
                            continue
                        try:
                            if executor.supports(ExecutionRequest(binding.target, binding.operation, {})):
                                capabilities.append(record.capability_id)
                                break
                        except Exception:
                            continue
        profile = _infer_worker_profile(
            name=name,
            kind="executor",
            targets=targets,
            operations=operations,
            capabilities=capabilities,
            metadata={"class": executor.__class__.__name__},
        )
        return WorkerDescriptor(
            worker_id=f"executor:{name}",
            label=name,
            kind="executor",
            worker_type=profile["worker_type"],
            worker_subtype=profile["worker_subtype"],
            capabilities=tuple(dict.fromkeys(capabilities)),
            capability_namespaces=tuple(profile["capability_namespaces"]),
            targets=targets,
            operations=operations,
            legacy_names=tuple(profile["legacy_names"]),
            permission_scope="local" if name != "remote" else "remote",
            health_status=health_status,
            health_detail=health_detail,
            metadata={"class": executor.__class__.__name__, "new_positioning": profile["new_positioning"]},
        )

    def _record_audit(
        self,
        *,
        worker_id: str,
        request: ExecutionRequest,
        status: str,
        started_at: float,
        result: ExecutionResult,
        capability_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WorkerAuditEvent:
        event = WorkerAuditEvent(
            event_id=f"worker-{int(started_at * 1000)}-{len(self._audit) + 1}",
            worker_id=worker_id,
            target=request.target,
            operation=request.operation,
            status=status,
            started_at=started_at,
            finished_at=time.time(),
            message=result.message,
            error_code=result.error_code,
            capability_id=capability_id,
            metadata=dict(metadata or {}),
        )
        self._audit.append(event)
        if len(self._audit) > self._audit_limit:
            self._audit = self._audit[-self._audit_limit :]
        return event


def normalize_worker_requirement(requirement: WorkerRequirement | dict[str, Any] | str) -> WorkerRequirement:
    if isinstance(requirement, WorkerRequirement):
        return requirement
    if isinstance(requirement, str):
        return WorkerRequirement(needs=(requirement,))
    payload = dict(requirement or {})
    raw_needs = payload.get("needs")
    if raw_needs is None:
        raw_needs = payload.get("need") or payload.get("capabilities") or payload.get("capability_namespaces") or ()
    if isinstance(raw_needs, str):
        needs = (raw_needs,)
    else:
        needs = _tuple_from_values(raw_needs or ())
    target = str(payload.get("target") or "").strip()
    operation = str(payload.get("operation") or "").strip()
    inferred_needs = _tuple_from_values((*needs, *_needs_from_target_operation(target, operation)))
    return WorkerRequirement(
        needs=inferred_needs,
        worker_type=str(payload.get("worker_type") or "").strip(),
        worker_subtype=str(payload.get("worker_subtype") or "").strip(),
        target=target,
        operation=operation,
        workspace=str(payload.get("workspace") or payload.get("workspace_id") or "").strip(),
        permission_scope=str(payload.get("permission_scope") or "").strip(),
        prefer_remote=bool(payload.get("prefer_remote")),
        metadata=dict(payload.get("metadata") or {}),
    )


def _tuple_from_values(values: Iterable[Any]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values or ():
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _needs_from_target_operation(target: str, operation: str) -> tuple[str, ...]:
    values: list[str] = []
    for value in (target, operation, f"{target}.{operation}" if target and operation else ""):
        text = str(value or "").strip()
        if text:
            values.append(text)
    return tuple(values)


def _score_worker_candidate(worker: WorkerDescriptor, requirement: WorkerRequirement) -> WorkerCandidate:
    reasons: list[str] = []
    penalties: list[str] = []
    score = 0
    worker_tokens = _worker_match_tokens(worker)
    matched: list[str] = []
    missing: list[str] = []

    for need in requirement.needs:
        normalized_need = _normalize_need_token(need)
        if not normalized_need:
            continue
        if _need_matches_worker(normalized_need, worker_tokens):
            matched.append(need)
            score += 35
            reasons.append(f"matched_need:{need}")
        else:
            missing.append(need)
            penalties.append(f"missing_need:{need}")
            score -= 100

    if requirement.worker_type:
        if worker.worker_type == requirement.worker_type:
            score += 25
            reasons.append(f"worker_type:{worker.worker_type}")
        else:
            score -= 60
            penalties.append(f"worker_type_mismatch:{worker.worker_type}")

    if requirement.worker_subtype:
        if worker.worker_subtype == requirement.worker_subtype:
            score += 20
            reasons.append(f"worker_subtype:{worker.worker_subtype}")
        else:
            score -= 40
            penalties.append(f"worker_subtype_mismatch:{worker.worker_subtype}")

    if requirement.target:
        target = _normalize_need_token(requirement.target)
        if target in worker_tokens:
            score += 18
            reasons.append(f"target:{requirement.target}")
        elif requirement.target.startswith("remote:") and worker.worker_id == requirement.target.split(":", 1)[1]:
            score += 18
            reasons.append(f"target_node:{requirement.target}")

    if requirement.operation:
        operation = _normalize_need_token(requirement.operation)
        if operation in worker_tokens:
            score += 18
            reasons.append(f"operation:{requirement.operation}")

    if requirement.workspace and worker.workspace:
        worker_workspaces = {item.strip() for item in worker.workspace.split(",") if item.strip()}
        if requirement.workspace in worker_workspaces:
            score += 15
            reasons.append(f"workspace:{requirement.workspace}")
        else:
            score -= 20
            penalties.append(f"workspace_mismatch:{worker.workspace}")

    if requirement.permission_scope:
        if worker.permission_scope == requirement.permission_scope:
            score += 10
            reasons.append(f"permission_scope:{worker.permission_scope}")
        else:
            score -= 12
            penalties.append(f"permission_scope_mismatch:{worker.permission_scope}")

    health_scores = {
        "ready": 30,
        "online": 25,
        "unknown": 0,
        "degraded": -15,
        "unhealthy": -30,
        "unavailable": -80,
        "offline": -100,
        "stale": -100,
    }
    health_score = health_scores.get(worker.health_status or "unknown", 0)
    score += health_score
    if health_score > 0:
        reasons.append(f"health:{worker.health_status}")
    elif health_score < 0:
        penalties.append(f"health:{worker.health_status}")

    if requirement.prefer_remote:
        if worker.worker_type == "generic_remote_worker" or worker.permission_scope == "remote":
            score += 12
            reasons.append("prefer_remote")
        else:
            score -= 6
            penalties.append("prefer_remote_not_matched")

    if worker.queue_depth:
        score -= min(25, max(0, worker.queue_depth))
        penalties.append(f"queue_depth:{worker.queue_depth}")

    if not requirement.needs and not requirement.worker_type and not requirement.target:
        score += 1
        reasons.append("no_specific_requirement")

    return WorkerCandidate(
        worker=worker,
        score=score,
        matched_needs=tuple(matched),
        missing_needs=tuple(missing),
        reasons=tuple(dict.fromkeys(reasons)),
        penalties=tuple(dict.fromkeys(penalties)),
    )


def _worker_match_tokens(worker: WorkerDescriptor) -> set[str]:
    tokens: set[str] = set()
    for value in (
        worker.worker_id,
        worker.label,
        worker.kind,
        worker.worker_type,
        worker.worker_subtype,
        worker.permission_scope,
        *worker.capabilities,
        *worker.capability_namespaces,
        *worker.targets,
        *worker.operations,
        *worker.legacy_names,
    ):
        normalized = _normalize_need_token(value)
        if normalized:
            tokens.add(normalized)
        if "." in normalized:
            tokens.add(normalized.split(".", 1)[0])
        if "_" in normalized:
            tokens.add(normalized.split("_", 1)[0])
    return tokens


def _normalize_need_token(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace(" ", "_").replace("-", "_")
    aliases = {
        "android_device_worker": "android",
        "android_worker": "android",
        "android_bridge": "android",
        "android_device": "android",
        "adb": "adb",
        "pdd": "pdd",
        "browser_worker": "browser",
        "browser_automation": "browser",
        "playwright": "browser",
        "remote_worker": "remote",
        "generic_remote_worker": "remote",
        "remote_runtime_worker": "remote",
        "desktop_device_worker": "desktop",
        "local_pc": "desktop",
        "openclaw": "openclaw",
        "python_runtime": "python",
        "media_worker": "media",
        "rag_worker": "rag",
        "vector_worker": "vector",
        "embedding_worker": "embedding",
    }
    return aliases.get(text, text)


def _need_matches_worker(need: str, worker_tokens: set[str]) -> bool:
    if need in worker_tokens:
        return True
    if "." in need:
        raw_parts = [part for part in need.split(".") if part]
        parts = [_normalize_need_token(part) for part in raw_parts]
        parts = [part for part in parts if part]
        if all(part in worker_tokens for part in parts):
            return True
        for index in range(1, len(raw_parts)):
            left = _normalize_need_token(".".join(raw_parts[:index]))
            right = _normalize_need_token(".".join(raw_parts[index:]))
            if left in worker_tokens and right in worker_tokens:
                return True
    return False


def _schedule_reason(
    selected: WorkerDescriptor | None,
    candidates: list[WorkerCandidate],
    rejected: list[WorkerCandidate],
    requirement: WorkerRequirement,
) -> str:
    if selected is not None:
        return f"selected {selected.worker_id} for needs {', '.join(requirement.needs) or 'any'}"
    if rejected:
        missing = ", ".join(rejected[0].missing_needs) or rejected[0].worker.health_status
        return f"no routable worker matched requirement; closest={rejected[0].worker.worker_id}; missing={missing}"
    return "no workers registered"


def _infer_worker_profile(
    *,
    name: str,
    kind: str,
    targets: Iterable[str] = (),
    operations: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    targets_tuple = _tuple_from_values(targets)
    operations_tuple = _tuple_from_values(operations)
    capabilities_tuple = _tuple_from_values(capabilities)
    text = " ".join(
        [
            name,
            kind,
            str(metadata.get("class") or ""),
            str(metadata.get("role") or ""),
            *targets_tuple,
            *operations_tuple,
            *capabilities_tuple,
        ]
    ).lower()
    namespaces = _infer_capability_namespaces(targets_tuple, operations_tuple, capabilities_tuple)
    legacy_names: list[str] = []

    if "android" in text or "adb" in text or "pdd" in text:
        return {
            "worker_type": "device_worker",
            "worker_subtype": "android_device_worker",
            "capability_namespaces": tuple(dict.fromkeys((*namespaces, "android", "adb"))),
            "legacy_names": ("Android Bridge", "ADB"),
            "new_positioning": "Android Device Worker",
        }

    if "openclaw" in text or "gripper" in text or "arm" in targets_tuple:
        return {
            "worker_type": "device_worker",
            "worker_subtype": "desktop_device_worker",
            "capability_namespaces": tuple(dict.fromkeys((*namespaces, "openclaw"))),
            "legacy_names": ("OpenClaw",),
            "new_positioning": "Desktop Device Worker",
        }

    if kind == "remote" or name == "remote" or "remote" in text:
        return {
            "worker_type": "generic_remote_worker",
            "worker_subtype": "remote_runtime_worker",
            "capability_namespaces": namespaces,
            "legacy_names": ("Remote Worker",),
            "new_positioning": "Generic Remote Worker",
        }

    if {"local_pc", "desktop", "pointer", "keyboard", "screen", "window", "clipboard"} & (set(targets_tuple) | set(namespaces)):
        return {
            "worker_type": "device_worker",
            "worker_subtype": "desktop_device_worker",
            "capability_namespaces": namespaces,
            "legacy_names": (),
            "new_positioning": "Desktop Device Worker",
        }

    if "browser" in namespaces:
        return {
            "worker_type": "browser_worker",
            "worker_subtype": "local_browser_worker",
            "capability_namespaces": namespaces,
            "legacy_names": ("Browser Automation", "Playwright"),
            "new_positioning": "Browser Worker",
        }

    if "feishu" in text or "search" in namespaces or "rag" in namespaces or "embedding" in namespaces:
        return {
            "worker_type": "service_worker",
            "worker_subtype": "integration_service_worker",
            "capability_namespaces": namespaces,
            "legacy_names": (),
            "new_positioning": "Service Worker",
        }

    if {"python", "node", "ffmpeg", "git", "ocr", "media"} & set(namespaces):
        legacy_names = [value for value in ("Python Runtime", "FFmpeg") if value.lower().split()[0] in text]
        return {
            "worker_type": "execution_worker",
            "worker_subtype": "runtime_execution_worker",
            "capability_namespaces": namespaces,
            "legacy_names": tuple(legacy_names),
            "new_positioning": "Execution Worker",
        }

    return {
        "worker_type": "execution_worker",
        "worker_subtype": "deterministic_executor_worker",
        "capability_namespaces": namespaces,
        "legacy_names": (),
        "new_positioning": "Execution Worker",
    }


def _infer_capability_namespaces(
    targets: Iterable[str],
    operations: Iterable[str],
    capabilities: Iterable[str],
) -> tuple[str, ...]:
    namespaces: list[str] = []
    keyword_aliases = {
        "browser": "browser",
        "playwright": "browser",
        "android": "android",
        "adb": "adb",
        "pdd": "pdd",
        "openclaw": "openclaw",
        "python": "python",
        "node": "node",
        "ffmpeg": "ffmpeg",
        "git": "git",
        "ocr": "ocr",
        "rag": "rag",
        "embedding": "embedding",
        "search": "search",
        "feishu": "feishu",
    }
    for raw in (*_tuple_from_values(targets), *_tuple_from_values(operations), *_tuple_from_values(capabilities)):
        value = str(raw or "").strip().lower()
        if not value:
            continue
        if "." in value:
            namespace = value.split(".", 1)[0]
        elif "_" in value:
            namespace = value.split("_", 1)[0]
        else:
            namespace = value
        aliases = {
            "local": "local_pc",
            "desktop": "desktop",
            "screen": "screen",
            "window": "window",
            "clipboard": "clipboard",
            "browser": "browser",
            "android": "android",
            "adb": "adb",
            "pdd": "pdd",
            "openclaw": "openclaw",
            "arm": "openclaw",
            "remote": "remote",
            "python": "python",
            "node": "node",
            "ffmpeg": "ffmpeg",
            "git": "git",
            "ocr": "ocr",
            "kb": "rag",
            "knowledge": "rag",
            "embedding": "embedding",
            "search": "search",
            "feishu": "feishu",
            "file": "file",
            "software": "software",
        }
        namespace = aliases.get(namespace, namespace)
        if namespace and namespace not in namespaces:
            namespaces.append(namespace)
        for keyword, mapped in keyword_aliases.items():
            if keyword in value and mapped not in namespaces:
                namespaces.append(mapped)
    return tuple(namespaces)


def _build_worker_taxonomy(workers: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts: dict[str, int] = {}
    subtype_counts: dict[str, int] = {}
    namespaces: dict[str, list[str]] = {}
    workers_by_type: dict[str, list[str]] = {}
    for worker in workers:
        worker_id = str(worker.get("worker_id") or "").strip()
        worker_type = str(worker.get("worker_type") or "execution_worker").strip()
        worker_subtype = str(worker.get("worker_subtype") or "").strip()
        type_counts[worker_type] = type_counts.get(worker_type, 0) + 1
        if worker_subtype:
            subtype_counts[worker_subtype] = subtype_counts.get(worker_subtype, 0) + 1
        workers_by_type.setdefault(worker_type, []).append(worker_id)
        for namespace in worker.get("capability_namespaces") or []:
            value = str(namespace or "").strip()
            if not value:
                continue
            namespaces.setdefault(value, [])
            if worker_id and worker_id not in namespaces[value]:
                namespaces[value].append(worker_id)
    return {
        "schema_version": WORKER_TAXONOMY_SCHEMA_VERSION,
        "type_counts": type_counts,
        "subtype_counts": subtype_counts,
        "workers_by_type": workers_by_type,
        "capability_namespaces": namespaces,
        "legacy_positioning": [dict(item) for item in LEGACY_WORKER_POSITIONING],
    }


def _build_planned_worker_taxonomy(workers: list[dict[str, Any]]) -> dict[str, Any]:
    subtype_counts: dict[str, int] = {}
    namespaces: dict[str, list[str]] = {}
    workers_by_type: dict[str, list[str]] = {}
    for worker in workers:
        worker_id = str(worker.get("worker_id") or "").strip()
        worker_type = str(worker.get("worker_type") or "execution_worker").strip()
        worker_subtype = str(worker.get("worker_subtype") or "").strip()
        if worker_subtype:
            subtype_counts[worker_subtype] = subtype_counts.get(worker_subtype, 0) + 1
        workers_by_type.setdefault(worker_type, []).append(worker_id)
        for namespace in worker.get("capability_namespaces") or []:
            value = str(namespace or "").strip()
            if not value:
                continue
            namespaces.setdefault(value, [])
            if worker_id and worker_id not in namespaces[value]:
                namespaces[value].append(worker_id)
    return {
        "planned_subtype_counts": subtype_counts,
        "planned_workers_by_type": workers_by_type,
        "planned_capability_namespaces": namespaces,
    }
