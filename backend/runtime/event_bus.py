from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any
from uuid import uuid4

from backend.runtime.events import EventPersistence

EVENT_BUS_SCHEMA_VERSION = "spiritkin.event_bus.v1"


@dataclass(frozen=True)
class RuntimeBusEvent:
    topic: str
    payload: dict[str, Any]
    source: str
    event_id: str = field(default_factory=lambda: f"bus-{uuid4().hex}")
    workspace_id: str = ""
    correlation_id: str = ""
    occurred_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": EVENT_BUS_SCHEMA_VERSION,
            "event_id": self.event_id,
            "topic": self.topic,
            "payload": dict(self.payload),
            "source": self.source,
            "workspace_id": self.workspace_id,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at,
        }


class RuntimeEventBus:
    def __init__(self, *, persistence: EventPersistence | None = None):
        self._persistence = persistence
        self._subscribers: dict[str, tuple[str, Callable[[RuntimeBusEvent], None]]] = {}
        self._published = 0
        self._delivery_failures = 0
        self._recent_delivery_errors: list[dict[str, str]] = []

    def subscribe(self, topic_pattern: str, handler: Callable[[RuntimeBusEvent], None]) -> str:
        pattern = str(topic_pattern or "*").strip() or "*"
        subscription_id = f"subscription-{uuid4().hex}"
        self._subscribers[subscription_id] = (pattern, handler)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        return self._subscribers.pop(str(subscription_id), None) is not None

    def publish(self, event: RuntimeBusEvent) -> RuntimeBusEvent:
        if not str(event.topic or "").strip():
            raise ValueError("runtime event topic is required")
        if not str(event.source or "").strip():
            raise ValueError("runtime event source is required")
        self._published += 1
        if self._persistence is not None:
            self._persistence.record(event.topic, event.snapshot())
        for pattern, handler in tuple(self._subscribers.values()):
            if not fnmatchcase(event.topic, pattern):
                continue
            try:
                handler(event)
            except Exception as exc:
                self._delivery_failures += 1
                self._recent_delivery_errors.append(
                    {
                        "topic": event.topic,
                        "handler": getattr(handler, "__name__", handler.__class__.__name__),
                        "error_type": type(exc).__name__,
                    }
                )
                self._recent_delivery_errors = self._recent_delivery_errors[-20:]
        return event

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": EVENT_BUS_SCHEMA_VERSION,
            "subscriber_count": len(self._subscribers),
            "published_count": self._published,
            "delivery_failure_count": self._delivery_failures,
            "recent_delivery_errors": list(self._recent_delivery_errors),
            "subscriptions": [pattern for pattern, _handler in self._subscribers.values()],
        }
