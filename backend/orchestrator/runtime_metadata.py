from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.runtime.lifecycle import lifecycle_snapshot

RUNTIME_METADATA_SCHEMA_VERSION = "spiritkin.runtime_metadata.v1"

_KNOWN_STATUSES = {"draft", "candidate", "review", "approved", "stable", "active", "deprecated", "archived", "unknown"}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),) if str(value).strip() else ()


def _int_or_none(value: Any) -> int | None:
    try:
        if value in ("", None):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RuntimeMetadata:
    """Normalized scheduling and governance metadata shared by runtime objects."""

    object_type: str
    object_id: str
    schema_version: str = RUNTIME_METADATA_SCHEMA_VERSION
    domain: str = "general"
    owner: str = ""
    version: str = ""
    status: str = "unknown"
    tags: tuple[str, ...] = ()
    source: str = ""
    risk_level: str = "low"
    permission_scope: str = ""
    cost_hint: str = ""
    latency_hint_ms: int | None = None
    success_rate: float | None = None
    maturity: str = ""
    policy_refs: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    audit_refs: tuple[str, ...] = ()
    benchmark_refs: tuple[str, ...] = ()
    dependency_refs: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "id": self.object_id,
            "domain": self.domain,
            "owner": self.owner,
            "version": self.version,
            "status": self.status,
            "tags": list(self.tags),
            "source": self.source,
            "risk_level": self.risk_level,
            "risk": self.risk_level,
            "permission_scope": self.permission_scope,
            "permission": self.permission_scope,
            "cost_hint": self.cost_hint,
            "latency_hint_ms": self.latency_hint_ms,
            "success_rate": self.success_rate,
            "maturity": self.maturity,
            "policy_refs": list(self.policy_refs),
            "context_refs": list(self.context_refs),
            "artifact_refs": list(self.artifact_refs),
            "audit_refs": list(self.audit_refs),
            "benchmark_refs": list(self.benchmark_refs),
            "dependency_refs": list(self.dependency_refs),
            "benchmark": list(self.benchmark_refs),
            "dependency": list(self.dependency_refs),
            "lifecycle": lifecycle_snapshot(
                object_type=self.object_type,
                object_id=self.object_id,
                status=self.status,
            ),
        }
        payload.update(dict(self.extra or {}))
        return payload


def normalize_runtime_metadata(
    raw: dict[str, Any] | None,
    *,
    object_type: str,
    object_id: str,
    defaults: dict[str, Any] | None = None,
) -> RuntimeMetadata:
    data = {**dict(defaults or {}), **dict(raw or {})}
    status = str(data.get("status") or "unknown").strip().lower()
    if status not in _KNOWN_STATUSES:
        status = "unknown"

    known = {
        "schema_version",
        "object_type",
        "object_id",
        "domain",
        "owner",
        "version",
        "status",
        "tags",
        "source",
        "risk_level",
        "permission_scope",
        "cost_hint",
        "latency_hint_ms",
        "success_rate",
        "maturity",
        "policy_refs",
        "context_refs",
        "artifact_refs",
        "audit_refs",
        "benchmark_refs",
        "dependency_refs",
    }
    return RuntimeMetadata(
        object_type=str(data.get("object_type") or object_type),
        object_id=str(data.get("object_id") or object_id),
        schema_version=str(data.get("schema_version") or RUNTIME_METADATA_SCHEMA_VERSION),
        domain=str(data.get("domain") or "general"),
        owner=str(data.get("owner") or ""),
        version=str(data.get("version") or ""),
        status=status,
        tags=_string_tuple(data.get("tags")),
        source=str(data.get("source") or ""),
        risk_level=str(data.get("risk_level") or "low"),
        permission_scope=str(data.get("permission_scope") or ""),
        cost_hint=str(data.get("cost_hint") or ""),
        latency_hint_ms=_int_or_none(data.get("latency_hint_ms")),
        success_rate=_float_or_none(data.get("success_rate")),
        maturity=str(data.get("maturity") or ""),
        policy_refs=_string_tuple(data.get("policy_refs")),
        context_refs=_string_tuple(data.get("context_refs")),
        artifact_refs=_string_tuple(data.get("artifact_refs")),
        audit_refs=_string_tuple(data.get("audit_refs")),
        benchmark_refs=_string_tuple(data.get("benchmark_refs") or data.get("benchmark")),
        dependency_refs=_string_tuple(data.get("dependency_refs") or data.get("dependency")),
        extra={key: value for key, value in data.items() if key not in known},
    )
