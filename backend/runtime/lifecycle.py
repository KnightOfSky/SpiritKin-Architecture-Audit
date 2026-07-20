from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

LIFECYCLE_SCHEMA_VERSION = "spiritkin.lifecycle.v1"
LIFECYCLE_STATES = (
    "draft",
    "candidate",
    "review",
    "approved",
    "stable",
    "deprecated",
    "archived",
)

_ALIASES = {
    "active": "stable",
    "enabled": "stable",
    "production": "stable",
    "pending_review": "review",
    "waiting_review": "review",
    "disabled": "deprecated",
    "unknown": "draft",
    "": "draft",
}

_TRANSITIONS = {
    "draft": frozenset({"candidate", "archived"}),
    "candidate": frozenset({"draft", "review", "archived"}),
    "review": frozenset({"candidate", "approved", "archived"}),
    "approved": frozenset({"review", "stable", "deprecated", "archived"}),
    "stable": frozenset({"review", "deprecated"}),
    "deprecated": frozenset({"review", "stable", "archived"}),
    "archived": frozenset(),
}


class InvalidLifecycleTransition(ValueError):
    pass


def normalize_lifecycle_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    status = _ALIASES.get(status, status)
    return status if status in LIFECYCLE_STATES else "draft"


def can_transition_lifecycle(current: Any, target: Any) -> bool:
    source = normalize_lifecycle_status(current)
    destination = normalize_lifecycle_status(target)
    return source == destination or destination in _TRANSITIONS[source]


@dataclass(frozen=True)
class LifecycleTransition:
    object_type: str
    object_id: str
    from_status: str
    to_status: str
    actor: str
    reason: str = ""
    revision: int = 1
    transitioned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": LIFECYCLE_SCHEMA_VERSION,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "actor": self.actor,
            "reason": self.reason,
            "revision": self.revision,
            "transitioned_at": self.transitioned_at,
        }


def transition_lifecycle(
    *,
    object_type: str,
    object_id: str,
    current: Any,
    target: Any,
    actor: str,
    reason: str = "",
    revision: int = 1,
) -> LifecycleTransition:
    source = normalize_lifecycle_status(current)
    destination = normalize_lifecycle_status(target)
    if not can_transition_lifecycle(source, destination):
        raise InvalidLifecycleTransition(f"{object_type}:{object_id} cannot transition from {source} to {destination}")
    return LifecycleTransition(
        object_type=str(object_type or "runtime_object"),
        object_id=str(object_id or "unknown"),
        from_status=source,
        to_status=destination,
        actor=str(actor or "system"),
        reason=str(reason or ""),
        revision=max(1, int(revision)),
    )


def lifecycle_snapshot(*, object_type: str, object_id: str, status: Any) -> dict[str, Any]:
    return {
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        "object_type": str(object_type),
        "object_id": str(object_id),
        "status": normalize_lifecycle_status(status),
        "allowed_transitions": sorted(_TRANSITIONS[normalize_lifecycle_status(status)]),
    }
