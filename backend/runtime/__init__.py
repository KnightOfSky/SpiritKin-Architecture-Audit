"""Runtime-owned scheduling, contracts, events, continuity, and providers."""

from backend.runtime.contracts import ContractValidationResult, RuntimeContract, validate_schema
from backend.runtime.event_bus import RuntimeBusEvent, RuntimeEventBus
from backend.runtime.lifecycle import (
    InvalidLifecycleTransition,
    LifecycleTransition,
    lifecycle_snapshot,
    transition_lifecycle,
)
from backend.runtime.providers import ProviderContract, ProviderRegistry, RuntimeProvider
from backend.runtime.state_machine import (
    InvalidObjectStateTransition,
    ObjectStateTransition,
    object_state_snapshot,
    transition_object_state,
)

__all__ = [
    "ContractValidationResult",
    "InvalidLifecycleTransition",
    "InvalidObjectStateTransition",
    "LifecycleTransition",
    "ObjectStateTransition",
    "ProviderContract",
    "ProviderRegistry",
    "RuntimeBusEvent",
    "RuntimeContract",
    "RuntimeEventBus",
    "RuntimeProvider",
    "lifecycle_snapshot",
    "object_state_snapshot",
    "transition_lifecycle",
    "transition_object_state",
    "validate_schema",
]
