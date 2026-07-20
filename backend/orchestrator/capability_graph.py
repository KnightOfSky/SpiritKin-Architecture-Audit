from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from backend.executors.base import ExecutionRequest
from backend.security.tool_authz import infer_tool_risk
from backend.skills.base import SkillSpec
from backend.tools.base import ToolSpec

CAPABILITY_SCHEMA_VERSION = "spiritkin.capability_graph.v1"


def normalize_capability_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", (value or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "unknown"


def capability_id_for_target_operation(target: str, operation: str) -> str:
    return normalize_capability_id(f"{target}_{operation}")


def _execution_request_capability_ids(request: ExecutionRequest) -> tuple[str, ...]:
    target = str(request.target or "").strip()
    operation = str(request.operation or "").strip()
    params = dict(request.params or {})
    candidates = [capability_id_for_target_operation(target, operation)]
    remote_target = str(params.get("remote_target") or "").strip()
    if remote_target:
        candidates.append(capability_id_for_target_operation(remote_target, operation))
    if operation.startswith("browser_") and (target == "browser" or target.startswith("remote:") or remote_target == "browser"):
        candidates.append(capability_id_for_target_operation("local_pc", operation))
    return tuple(dict.fromkeys(candidates))


def _binding_matches_execution_request(binding: CapabilityBinding, request: ExecutionRequest, *, candidate_ids: tuple[str, ...] | None = None) -> bool:
    if binding.target == request.target and binding.operation == request.operation:
        return True
    if not binding.target or not binding.operation:
        return False
    return capability_id_for_target_operation(binding.target, binding.operation) in (candidate_ids or _execution_request_capability_ids(request))


@dataclass(frozen=True)
class CapabilityBinding:
    binding_type: str
    binding_id: str
    target: str = ""
    operation: str = ""
    risk_level: str = "low"
    read_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "binding_type": self.binding_type,
            "binding_id": self.binding_id,
            "target": self.target,
            "operation": self.operation,
            "risk_level": self.risk_level,
            "read_only": self.read_only,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class CapabilityRecord:
    capability_id: str
    label: str
    description: str = ""
    domain: str = "general"
    owner_agents: tuple[str, ...] = ()
    worker_requirements: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    knowledge_refs: tuple[str, ...] = ()
    workflow_refs: tuple[str, ...] = ()
    skill_refs: tuple[str, ...] = ()
    tool_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    bindings: tuple[CapabilityBinding, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def merge(self, other: CapabilityRecord) -> CapabilityRecord:
        if self.capability_id != other.capability_id:
            raise ValueError("cannot merge different capabilities")
        bindings_by_key = {
            (binding.binding_type, binding.binding_id, binding.target, binding.operation): binding
            for binding in (*self.bindings, *other.bindings)
        }
        return CapabilityRecord(
            capability_id=self.capability_id,
            label=self.label or other.label,
            description=self.description or other.description,
            domain=self.domain if self.domain != "general" else other.domain,
            owner_agents=_merge_tuple(self.owner_agents, other.owner_agents),
            worker_requirements=_merge_tuple(self.worker_requirements, other.worker_requirements),
            policy_refs=_merge_tuple(self.policy_refs, other.policy_refs),
            knowledge_refs=_merge_tuple(self.knowledge_refs, other.knowledge_refs),
            workflow_refs=_merge_tuple(self.workflow_refs, other.workflow_refs),
            skill_refs=_merge_tuple(self.skill_refs, other.skill_refs),
            tool_refs=_merge_tuple(self.tool_refs, other.tool_refs),
            tags=_merge_tuple(self.tags, other.tags),
            bindings=tuple(bindings_by_key.values()),
            metadata={**dict(other.metadata or {}), **dict(self.metadata or {})},
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "description": self.description,
            "domain": self.domain,
            "owner_agents": list(self.owner_agents),
            "worker_requirements": list(self.worker_requirements),
            "policy_refs": list(self.policy_refs),
            "knowledge_refs": list(self.knowledge_refs),
            "workflow_refs": list(self.workflow_refs),
            "skill_refs": list(self.skill_refs),
            "tool_refs": list(self.tool_refs),
            "tags": list(self.tags),
            "bindings": [binding.snapshot() for binding in self.bindings],
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class CapabilityGraphSnapshot:
    capabilities: tuple[CapabilityRecord, ...]
    edges: tuple[dict[str, Any], ...] = ()
    gaps: tuple[dict[str, Any], ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "total": len(self.capabilities),
            "capabilities": [capability.snapshot() for capability in self.capabilities],
            "edges": [dict(edge) for edge in self.edges],
            "gaps": [dict(gap) for gap in self.gaps],
        }


@dataclass(frozen=True)
class WorkerAvailabilityEvidence:
    requirement: str
    status: str = "unmatched"
    schedulable: bool = False
    planned: bool = False
    matched_worker_ids: tuple[str, ...] = ()
    matched_capability_ids: tuple[str, ...] = ()
    health_statuses: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "status": self.status,
            "schedulable": self.schedulable,
            "planned": self.planned,
            "matched_worker_ids": list(self.matched_worker_ids),
            "matched_capability_ids": list(self.matched_capability_ids),
            "health_statuses": list(self.health_statuses),
            "reasons": list(self.reasons),
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True)
class CapabilityCandidate:
    record: CapabilityRecord
    score: int
    reasons: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    schedulable: bool = True
    worker_evidence: tuple[WorkerAvailabilityEvidence, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "capability": self.record.snapshot(),
            "score": self.score,
            "reasons": list(self.reasons),
            "gaps": list(self.gaps),
            "schedulable": self.schedulable,
            "worker_evidence": [item.snapshot() for item in self.worker_evidence],
        }


@dataclass(frozen=True)
class CapabilityRecommendation:
    query: str
    domain: str = ""
    required_capabilities: tuple[str, ...] = ()
    required_workers: tuple[str, ...] = ()
    include_planned: bool = False
    candidates: tuple[CapabilityCandidate, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "query": self.query,
            "domain": self.domain,
            "required_capabilities": list(self.required_capabilities),
            "required_workers": list(self.required_workers),
            "include_planned": self.include_planned,
            "candidates": [candidate.snapshot() for candidate in self.candidates],
            "candidate_count": len(self.candidates),
            "top_capability_id": self.candidates[0].record.capability_id if self.candidates else "",
        }


class CapabilityRegistry:
    """Business capability graph above tools, Skills, workflows, Agents, and workers."""

    def __init__(self, records: Iterable[CapabilityRecord] | None = None):
        self._records: dict[str, CapabilityRecord] = {}
        self._worker_availability: dict[str, list[CapabilityRecord]] = {}
        for record in records or ():
            self.register(record)

    def register(self, record: CapabilityRecord) -> None:
        if not record.capability_id:
            raise ValueError("capability_id is required")
        existing = self._records.get(record.capability_id)
        self._records[record.capability_id] = existing.merge(record) if existing is not None else record
        self._rebuild_worker_availability()

    def get(self, capability_id: str) -> CapabilityRecord | None:
        return self._records.get(normalize_capability_id(capability_id))

    def list_records(self) -> list[CapabilityRecord]:
        return sorted(self._records.values(), key=lambda item: item.capability_id)

    def recommend(
        self,
        query: str,
        *,
        domain: str = "",
        required_capabilities: Iterable[str] | None = None,
        required_workers: Iterable[str] | None = None,
        include_planned: bool = False,
        limit: int = 5,
    ) -> CapabilityRecommendation:
        required_capability_ids = tuple(normalize_capability_id(item) for item in required_capabilities or () if str(item).strip())
        required_worker_ids = _string_tuple(tuple(required_workers or ()))
        candidates: list[CapabilityCandidate] = []
        for record in self.list_records():
            candidate = _score_capability_candidate(
                record,
                query=query,
                domain=domain,
                required_capabilities=required_capability_ids,
                required_workers=required_worker_ids,
                worker_availability=self._worker_availability,
            )
            if candidate.score <= 0:
                continue
            if not include_planned and not candidate.schedulable:
                continue
            candidates.append(candidate)
        candidates.sort(key=lambda item: (-item.score, item.record.capability_id))
        return CapabilityRecommendation(
            query=query,
            domain=domain,
            required_capabilities=required_capability_ids,
            required_workers=required_worker_ids,
            include_planned=include_planned,
            candidates=tuple(candidates[: max(1, int(limit))]),
        )

    def resolve_execution_request(self, request: ExecutionRequest) -> CapabilityRecord | None:
        candidate_ids = _execution_request_capability_ids(request)
        for capability_id in candidate_ids:
            found = self._records.get(capability_id)
            if found is not None:
                return found
        for record in self._records.values():
            for binding in record.bindings:
                if _binding_matches_execution_request(binding, request, candidate_ids=candidate_ids):
                    return record
        return None

    def snapshot(self) -> dict[str, Any]:
        records = self.list_records()
        return CapabilityGraphSnapshot(
            capabilities=tuple(records),
            edges=tuple(_build_edges(records)),
            gaps=tuple(_build_gaps(records)),
        ).snapshot()

    def _rebuild_worker_availability(self) -> None:
        availability: dict[str, list[CapabilityRecord]] = {}
        for record in self._records.values():
            if str(record.metadata.get("source") or "") not in {"worker_descriptor", "executor"}:
                continue
            worker_keys = _worker_availability_keys(record)
            for key in worker_keys:
                availability.setdefault(key, []).append(record)
        self._worker_availability = availability


def build_capability_registry(
    *,
    tools: Iterable[ToolSpec] | None = None,
    skills: Iterable[SkillSpec] | None = None,
    agents: Iterable[Any] | None = None,
    executors: Iterable[Any] | None = None,
    workers: Iterable[Any] | None = None,
) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for tool in tools or ():
        registry.register(capability_from_tool(tool))
    for skill in skills or ():
        registry.register(capability_from_skill(skill))
    for agent in agents or ():
        for record in capabilities_from_agent(agent):
            registry.register(record)
    for executor in executors or ():
        for record in capabilities_from_executor(executor):
            registry.register(record)
    for worker in workers or ():
        for record in capabilities_from_worker_descriptor(worker):
            registry.register(record)
    return registry


def capability_from_tool(tool: ToolSpec) -> CapabilityRecord:
    capability_id = capability_id_for_target_operation(tool.target, tool.operation)
    authz_risk = infer_tool_risk(tool)
    binding = CapabilityBinding(
        binding_type="tool",
        binding_id=tool.name,
        target=tool.target,
        operation=tool.operation,
        risk_level=authz_risk,
        read_only=tool.read_only,
        metadata={"schema": dict(tool.schema or {}), "legacy_risk_level": tool.risk_level},
    )
    return CapabilityRecord(
        capability_id=capability_id,
        label=tool.description or tool.name,
        description=tool.description,
        domain=_infer_domain(tool.target, tool.operation),
        worker_requirements=(tool.target,),
        policy_refs=(f"risk:{authz_risk}",),
        tool_refs=(tool.name,),
        tags=("tool", tool.target, tool.operation),
        bindings=(binding,),
    )


def capability_from_skill(skill: SkillSpec) -> CapabilityRecord:
    capability_id = normalize_capability_id(skill.metadata.get("capability_id") or skill.name)
    tool_refs = tuple(step.tool_name for step in skill.steps if step.tool_name)
    required_worker_needs = _string_tuple(skill.required_worker_needs or skill.metadata.get("required_worker_needs"))
    required_capabilities = _string_tuple(skill.required_capabilities or skill.metadata.get("required_capabilities"))
    binding = CapabilityBinding(
        binding_type="skill",
        binding_id=skill.name,
        risk_level=skill.risk_level,
        metadata={
            "trigger_intents": list(skill.trigger_intents),
            "version": skill.version,
            "confirmation_policy": skill.confirmation_policy,
            "input_schema": dict(skill.input_schema or {}),
            "output_schema": dict(skill.output_schema or {}),
            "cost_hint": skill.cost_hint,
            "latency_hint_ms": skill.latency_hint_ms,
            "success_rate": skill.success_rate,
            "side_effects": list(skill.side_effects),
            "artifact_contract": dict(skill.artifact_contract or {}),
        },
    )
    return CapabilityRecord(
        capability_id=capability_id,
        label=skill.description or skill.name,
        description=skill.description,
        domain=str(skill.metadata.get("domain") or "skill"),
        worker_requirements=required_worker_needs,
        policy_refs=(f"risk:{skill.risk_level}", f"confirmation:{skill.confirmation_policy}"),
        skill_refs=(skill.name,),
        tool_refs=tool_refs,
        tags=("skill", str(skill.metadata.get("status") or "unclassified"), *required_capabilities),
        bindings=(binding,),
        metadata={
            "success_criteria": list(skill.success_criteria),
            "eval_cases": list(skill.eval_cases),
            "cost_hint": skill.cost_hint,
            "latency_hint_ms": skill.latency_hint_ms,
            "success_rate": skill.success_rate,
            "artifact_contract": dict(skill.artifact_contract or {}),
        },
    )


def capabilities_from_agent(agent: Any) -> list[CapabilityRecord]:
    agent_id = str(getattr(agent, "name", "") or "").strip()
    if not agent_id:
        return []
    domain = str(getattr(agent, "domain", "") or agent_id or "agent")
    raw_capabilities = getattr(agent, "capabilities", ()) or getattr(agent, "supported_capabilities", ()) or ()
    records: list[CapabilityRecord] = []
    for raw in raw_capabilities:
        capability_id = normalize_capability_id(str(raw))
        if capability_id:
            records.append(
                CapabilityRecord(
                    capability_id=capability_id,
                    label=capability_id.replace("_", " "),
                    domain=domain,
                    owner_agents=(agent_id,),
                    tags=("agent", domain),
                    bindings=(CapabilityBinding(binding_type="agent", binding_id=agent_id, metadata={"domain": domain}),),
                )
            )
    if not records:
        records.append(
            CapabilityRecord(
                capability_id=normalize_capability_id(domain),
                label=domain.replace("_", " "),
                domain=domain,
                owner_agents=(agent_id,),
                tags=("agent", domain),
                bindings=(CapabilityBinding(binding_type="agent", binding_id=agent_id, metadata={"domain": domain}),),
            )
        )
    return records


def capabilities_from_executor(executor: Any) -> list[CapabilityRecord]:
    executor_name = str(getattr(executor, "name", "") or executor.__class__.__name__).strip()
    targets = tuple(str(item).strip() for item in getattr(executor, "supported_targets", ()) or () if str(item).strip())
    operations = tuple(str(item).strip() for item in getattr(executor, "supported_operations", ()) or () if str(item).strip())
    if not targets and not operations:
        return []
    records: list[CapabilityRecord] = []
    for target in targets or ("executor",):
        if operations:
            for operation in operations:
                records.append(_capability_from_executor_binding(executor_name, target, operation))
        else:
            capability_id = normalize_capability_id(target)
            records.append(
                CapabilityRecord(
                    capability_id=capability_id,
                    label=target,
                    domain=_infer_domain(target, ""),
                    worker_requirements=(target,),
                    tags=("executor", target),
                    bindings=(CapabilityBinding(binding_type="executor", binding_id=executor_name, target=target),),
                )
            )
    return records


def capabilities_from_worker_descriptor(worker: Any) -> list[CapabilityRecord]:
    snapshot = worker.snapshot() if hasattr(worker, "snapshot") else dict(worker or {})
    worker_id = str(snapshot.get("worker_id") or "").strip()
    if not worker_id:
        return []
    capabilities = _string_tuple(snapshot.get("capabilities"))
    namespaces = _string_tuple(snapshot.get("capability_namespaces"))
    targets = _string_tuple(snapshot.get("targets"))
    operations = _string_tuple(snapshot.get("operations"))
    worker_type = str(snapshot.get("worker_type") or "execution_worker")
    worker_subtype = str(snapshot.get("worker_subtype") or "")
    maturity = str((snapshot.get("metadata") or {}).get("maturity") or snapshot.get("health_status") or "")
    schedulable = bool((snapshot.get("metadata") or {}).get("schedulable", snapshot.get("health_status") == "ready"))
    records: list[CapabilityRecord] = []
    raw_capabilities = capabilities or namespaces or targets
    for raw in raw_capabilities:
        capability_id = normalize_capability_id(raw)
        records.append(
            CapabilityRecord(
                capability_id=capability_id,
                label=str(snapshot.get("label") or raw),
                description=f"Worker descriptor capability `{raw}` exposed by {worker_id}.",
                domain=_infer_domain(" ".join((*targets, *namespaces)), " ".join(operations)),
                worker_requirements=(worker_subtype or worker_id,),
                tags=("worker", worker_type, worker_subtype, maturity),
                bindings=(
                    CapabilityBinding(
                        binding_type="worker_descriptor",
                        binding_id=worker_id,
                        target=targets[0] if targets else "",
                        operation=operations[0] if operations else "",
                        risk_level="medium" if schedulable else "low",
                        read_only=not schedulable,
                        metadata={
                            "worker_type": worker_type,
                            "worker_subtype": worker_subtype,
                            "capability_namespaces": list(namespaces),
                            "permission_scope": str(snapshot.get("permission_scope") or ""),
                            "maturity": maturity,
                            "schedulable": schedulable,
                            "health_status": str(snapshot.get("health_status") or ""),
                        },
                    ),
                ),
                metadata={
                    "source": "worker_descriptor",
                    "worker_id": worker_id,
                    "worker_type": worker_type,
                    "worker_subtype": worker_subtype,
                    "permission_scope": str(snapshot.get("permission_scope") or ""),
                    "maturity": maturity,
                    "schedulable": schedulable,
                    "planned": maturity == "planned" or not schedulable,
                },
            )
        )
    return records


def _capability_from_executor_binding(executor_name: str, target: str, operation: str) -> CapabilityRecord:
    capability_id = capability_id_for_target_operation(target, operation)
    return CapabilityRecord(
        capability_id=capability_id,
        label=f"{target}.{operation}",
        domain=_infer_domain(target, operation),
        worker_requirements=(target,),
        tags=("executor", target, operation, executor_name),
        bindings=(
            CapabilityBinding(
                binding_type="executor",
                binding_id=executor_name,
                target=target,
                operation=operation,
                metadata={"worker_id": f"executor:{executor_name}", "maturity": "ready", "schedulable": True},
            ),
        ),
        metadata={
            "source": "executor",
            "worker_id": f"executor:{executor_name}",
            "worker_type": "execution_worker",
            "worker_subtype": executor_name,
            "maturity": "ready",
            "schedulable": True,
            "planned": False,
        },
    )


def _build_edges(records: list[CapabilityRecord]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for record in records:
        for owner in record.owner_agents:
            edges.append({"from": f"agent:{owner}", "to": f"capability:{record.capability_id}", "type": "owns"})
        for worker in record.worker_requirements:
            edges.append({"from": f"capability:{record.capability_id}", "to": f"worker:{worker}", "type": "requires_worker"})
        for tool in record.tool_refs:
            edges.append({"from": f"capability:{record.capability_id}", "to": f"tool:{tool}", "type": "uses_tool"})
        for skill in record.skill_refs:
            edges.append({"from": f"capability:{record.capability_id}", "to": f"skill:{skill}", "type": "uses_skill"})
        for workflow in record.workflow_refs:
            edges.append({"from": f"capability:{record.capability_id}", "to": f"workflow:{workflow}", "type": "uses_workflow"})
    return edges


def _build_gaps(records: list[CapabilityRecord]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for record in records:
        if not record.bindings:
            gaps.append({"capability_id": record.capability_id, "gap": "missing_runtime_binding"})
        if not record.worker_requirements and any(binding.target for binding in record.bindings):
            gaps.append({"capability_id": record.capability_id, "gap": "missing_worker_requirement"})
        if not record.owner_agents and not record.skill_refs and not record.tool_refs:
            gaps.append({"capability_id": record.capability_id, "gap": "missing_owner_or_runtime_asset"})
    return gaps


def _score_capability_candidate(
    record: CapabilityRecord,
    *,
    query: str,
    domain: str = "",
    required_capabilities: tuple[str, ...] = (),
    required_workers: tuple[str, ...] = (),
    worker_availability: dict[str, list[CapabilityRecord]] | None = None,
) -> CapabilityCandidate:
    score = 0
    reasons: list[str] = []
    gaps: list[str] = []
    haystack = " ".join(
        (
            record.capability_id,
            record.label,
            record.description,
            record.domain,
            " ".join(record.tags),
            " ".join(record.owner_agents),
            " ".join(record.workflow_refs),
            " ".join(record.skill_refs),
            " ".join(record.tool_refs),
            " ".join(record.worker_requirements),
        )
    ).lower()
    query_tokens = tuple(token for token in re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", str(query or "").lower()) if token)
    for token in query_tokens:
        normalized = normalize_capability_id(token)
        if normalized and normalized == record.capability_id:
            score += 50
            reasons.append(f"exact_capability:{record.capability_id}")
        elif token and token in haystack:
            score += 8
            reasons.append(f"query_match:{token}")
    if domain and normalize_capability_id(domain) == normalize_capability_id(record.domain):
        score += 20
        reasons.append(f"domain_match:{record.domain}")
    for capability_id in required_capabilities:
        if capability_id == record.capability_id or capability_id in {normalize_capability_id(tag) for tag in record.tags}:
            score += 35
            reasons.append(f"required_capability:{capability_id}")
        else:
            gaps.append(f"missing_required_capability:{capability_id}")
    normalized_workers = {normalize_capability_id(item) for item in record.worker_requirements}
    for worker in required_workers:
        normalized_worker = normalize_capability_id(worker)
        if normalized_worker in normalized_workers:
            score += 15
            reasons.append(f"required_worker:{normalized_worker}")
        else:
            gaps.append(f"missing_required_worker:{normalized_worker}")
    worker_evidence = _worker_availability_evidence(record, worker_availability or {})
    for evidence in worker_evidence:
        if evidence.status == "ready":
            reasons.append(f"worker_ready:{normalize_capability_id(evidence.requirement)}")
        elif evidence.status == "planned":
            gaps.append(f"worker_planned:{normalize_capability_id(evidence.requirement)}")
        elif evidence.status == "missing":
            gaps.append(f"worker_missing:{normalize_capability_id(evidence.requirement)}")
    schedulable = bool(record.metadata.get("schedulable", True)) and not bool(record.metadata.get("planned", False))
    if schedulable:
        score += 3
        reasons.append("schedulable")
    else:
        gaps.append("not_schedulable")
    if not reasons:
        return CapabilityCandidate(record=record, score=0, gaps=tuple(gaps), schedulable=schedulable, worker_evidence=worker_evidence)
    return CapabilityCandidate(
        record=record,
        score=max(0, score - len(gaps) * 5),
        reasons=tuple(dict.fromkeys(reasons)),
        gaps=tuple(dict.fromkeys(gaps)),
        schedulable=schedulable,
        worker_evidence=worker_evidence,
    )


def _worker_availability_keys(record: CapabilityRecord) -> tuple[str, ...]:
    keys: list[str] = []
    metadata = dict(record.metadata or {})
    for value in (
        record.capability_id,
        metadata.get("worker_id", ""),
        metadata.get("worker_type", ""),
        metadata.get("worker_subtype", ""),
    ):
        normalized = normalize_capability_id(str(value or ""))
        if normalized and normalized not in keys:
            keys.append(normalized)
    for worker in record.worker_requirements:
        normalized = normalize_capability_id(worker)
        if normalized and normalized not in keys:
            keys.append(normalized)
    for tag in record.tags:
        normalized = normalize_capability_id(tag)
        if normalized and normalized not in keys:
            keys.append(normalized)
    for binding in record.bindings:
        binding_metadata = dict(binding.metadata or {})
        for value in (
            binding.binding_id,
            binding.target,
            binding.operation,
            binding_metadata.get("worker_type", ""),
            binding_metadata.get("worker_subtype", ""),
        ):
            normalized = normalize_capability_id(str(value or ""))
            if normalized and normalized not in keys:
                keys.append(normalized)
        for namespace in binding_metadata.get("capability_namespaces") or ():
            normalized = normalize_capability_id(str(namespace or ""))
            if normalized and normalized not in keys:
                keys.append(normalized)
    return tuple(keys)


def _worker_availability_evidence(
    record: CapabilityRecord,
    availability: dict[str, list[CapabilityRecord]],
) -> tuple[WorkerAvailabilityEvidence, ...]:
    evidence: list[WorkerAvailabilityEvidence] = []
    for requirement in record.worker_requirements:
        normalized_requirement = normalize_capability_id(requirement)
        matched = list(availability.get(normalized_requirement, ()))
        matched_worker_ids = tuple(
            dict.fromkeys(
                str(item.metadata.get("worker_id") or "")
                for item in matched
                if str(item.metadata.get("worker_id") or "").strip()
            )
        )
        matched_capability_ids = tuple(dict.fromkeys(item.capability_id for item in matched if item.capability_id))
        health_statuses = tuple(
            dict.fromkeys(
                str(item.bindings[0].metadata.get("health_status") or item.metadata.get("maturity") or "")
                for item in matched
                if item.bindings
            )
        )
        ready = [
            item
            for item in matched
            if bool(item.metadata.get("schedulable", True)) and not bool(item.metadata.get("planned", False))
        ]
        planned = [
            item
            for item in matched
            if bool(item.metadata.get("planned", False)) or str(item.metadata.get("maturity") or "") == "planned"
        ]
        if ready:
            evidence.append(
                WorkerAvailabilityEvidence(
                    requirement=requirement,
                    status="ready",
                    schedulable=True,
                    planned=False,
                    matched_worker_ids=matched_worker_ids,
                    matched_capability_ids=matched_capability_ids,
                    health_statuses=health_statuses,
                    reasons=("ready_worker_descriptor",),
                )
            )
        elif planned:
            evidence.append(
                WorkerAvailabilityEvidence(
                    requirement=requirement,
                    status="planned",
                    schedulable=False,
                    planned=True,
                    matched_worker_ids=matched_worker_ids,
                    matched_capability_ids=matched_capability_ids,
                    health_statuses=health_statuses,
                    reasons=("planned_worker_descriptor",),
                    gaps=("worker_not_executable",),
                )
            )
        else:
            evidence.append(
                WorkerAvailabilityEvidence(
                    requirement=requirement,
                    status="missing",
                    schedulable=False,
                    planned=False,
                    gaps=("missing_worker_descriptor",),
                )
            )
    return tuple(evidence)


def _infer_domain(target: str, operation: str) -> str:
    source = f"{target}.{operation}".lower()
    if "android" in source or "ios" in source or "mobile" in source:
        return "mobile"
    if "feishu" in source or "message" in source:
        return "collaboration"
    if "openclaw" in source or "gripper" in source:
        return "robotics"
    if "browser" in source or "file" in source or "window" in source or "clipboard" in source:
        return "desktop"
    if "ecommerce" in source or "product" in source or "order" in source:
        return "ecommerce"
    if "kb" in source or "knowledge" in source:
        return "knowledge"
    return target or "general"


def _merge_tuple(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for item in (*first, *second):
        value = str(item).strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    stripped = str(value).strip()
    return (stripped,) if stripped else ()
