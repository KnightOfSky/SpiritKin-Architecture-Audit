from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

STATE_MACHINE_SCHEMA_VERSION = "spiritkin.state_machine.v1"

_GRAPHS: dict[str, dict[str, frozenset[str]]] = {
    "workflow": {
        "pending": frozenset({"running", "blocked", "cancelled", "archived"}),
        "running": frozenset({"waiting", "waiting_review", "blocked", "failed", "succeeded", "cancelled"}),
        "waiting": frozenset({"running", "blocked", "failed", "cancelled"}),
        "waiting_review": frozenset({"running", "blocked", "failed", "cancelled"}),
        "blocked": frozenset({"pending", "running", "failed", "cancelled"}),
        "failed": frozenset({"pending", "running", "cancelled", "archived"}),
        "succeeded": frozenset({"archived"}),
        "cancelled": frozenset({"archived"}),
        "archived": frozenset(),
    },
    "skill": {
        "draft": frozenset({"candidate", "archived"}),
        "candidate": frozenset({"review", "draft", "archived"}),
        "review": frozenset({"active", "candidate", "archived"}),
        "active": frozenset({"degraded", "deprecated"}),
        "degraded": frozenset({"active", "deprecated"}),
        "deprecated": frozenset({"active", "archived"}),
        "archived": frozenset(),
    },
    "worker": {
        "planned": frozenset({"ready", "unavailable", "revoked"}),
        "ready": frozenset({"busy", "degraded", "unavailable", "draining", "revoked"}),
        "busy": frozenset({"ready", "degraded", "unavailable", "draining", "revoked"}),
        "degraded": frozenset({"ready", "unavailable", "draining", "revoked"}),
        "draining": frozenset({"ready", "unavailable", "revoked"}),
        "unavailable": frozenset({"ready", "revoked"}),
        "revoked": frozenset(),
    },
    "agent": {
        "draft": frozenset({"candidate", "archived"}),
        "candidate": frozenset({"review", "draft", "archived"}),
        "review": frozenset({"active", "candidate", "archived"}),
        "active": frozenset({"disabled", "degraded", "deprecated"}),
        "degraded": frozenset({"active", "disabled", "deprecated"}),
        "disabled": frozenset({"active", "deprecated", "archived"}),
        "deprecated": frozenset({"active", "archived"}),
        "archived": frozenset(),
    },
    "model": {
        "unconfigured": frozenset({"candidate", "unavailable", "archived"}),
        "candidate": frozenset({"review", "unavailable", "archived"}),
        "review": frozenset({"active", "candidate", "archived"}),
        "active": frozenset({"degraded", "unavailable", "deprecated"}),
        "degraded": frozenset({"active", "unavailable", "deprecated"}),
        "unavailable": frozenset({"candidate", "active", "deprecated", "archived"}),
        "deprecated": frozenset({"candidate", "archived"}),
        "archived": frozenset(),
    },
}

_DEFAULT_STATES = {"workflow": "pending", "skill": "draft", "worker": "planned", "agent": "draft", "model": "unconfigured"}


class InvalidObjectStateTransition(ValueError):
    pass


def normalize_object_state(object_type: str, state: Any) -> str:
    kind = str(object_type or "").strip().lower()
    if kind not in _GRAPHS:
        raise ValueError(f"unsupported state machine object type: {kind}")
    value = str(state or "").strip().lower()
    aliases = {
        "success": "succeeded",
        "completed": "succeeded",
        "online": "ready",
        "healthy": "ready",
        "unknown": _DEFAULT_STATES[kind],
        "enabled": "active",
        "stable": "active",
    }
    value = aliases.get(value, value)
    return value if value in _GRAPHS[kind] else _DEFAULT_STATES[kind]


def can_transition_object_state(object_type: str, current: Any, target: Any) -> bool:
    kind = str(object_type or "").strip().lower()
    source = normalize_object_state(kind, current)
    destination = normalize_object_state(kind, target)
    return source == destination or destination in _GRAPHS[kind][source]


@dataclass(frozen=True)
class ObjectStateTransition:
    object_type: str
    object_id: str
    from_state: str
    to_state: str
    actor: str
    reason: str = ""
    revision: int = 1
    transitioned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_MACHINE_SCHEMA_VERSION,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "actor": self.actor,
            "reason": self.reason,
            "revision": self.revision,
            "transitioned_at": self.transitioned_at,
        }


def transition_object_state(
    *,
    object_type: str,
    object_id: str,
    current: Any,
    target: Any,
    actor: str,
    reason: str = "",
    revision: int = 1,
) -> ObjectStateTransition:
    kind = str(object_type or "").strip().lower()
    source = normalize_object_state(kind, current)
    destination = normalize_object_state(kind, target)
    if not can_transition_object_state(kind, source, destination):
        raise InvalidObjectStateTransition(f"{kind}:{object_id} cannot transition from {source} to {destination}")
    return ObjectStateTransition(kind, str(object_id), source, destination, str(actor or "system"), str(reason or ""), max(1, int(revision)))


def object_state_snapshot(*, object_type: str, object_id: str, state: Any) -> dict[str, Any]:
    kind = str(object_type or "").strip().lower()
    current = normalize_object_state(kind, state)
    return {
        "schema_version": STATE_MACHINE_SCHEMA_VERSION,
        "object_type": kind,
        "object_id": str(object_id),
        "state": current,
        "allowed_transitions": sorted(_GRAPHS[kind][current]),
    }
