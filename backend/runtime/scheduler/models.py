from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TRIGGER_TYPES = frozenset({"date", "interval", "cron"})
INTENT_TYPES = frozenset({"reminder", "action"})


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve_timezone(name: str) -> ZoneInfo:
    normalized = str(name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {normalized}") from exc


def parse_datetime(value: str, timezone_name: str) -> datetime:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("datetime value is required")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid datetime: {normalized}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=resolve_timezone(timezone_name))
    return parsed


@dataclass(frozen=True)
class ScheduledIntent:
    text: str
    trigger_type: str
    intent_type: str = "reminder"
    timezone: str = "UTC"
    run_at: str = ""
    interval_seconds: float = 0.0
    cron: str = ""
    start_at: str = ""
    end_at: str = ""
    action_prompt: str = ""
    priority: int = 70
    intent_id: str = field(default_factory=lambda: f"intent-{uuid4().hex}")
    status: str = "active"
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        trigger_type = str(self.trigger_type or "").strip().lower()
        intent_type = str(self.intent_type or "").strip().lower()
        text = " ".join(str(self.text or "").split())[:300]
        if trigger_type not in TRIGGER_TYPES:
            raise ValueError(f"unsupported trigger_type: {self.trigger_type}")
        if intent_type not in INTENT_TYPES:
            raise ValueError(f"unsupported intent_type: {self.intent_type}")
        if not text:
            raise ValueError("scheduled intent requires text")
        resolve_timezone(self.timezone)
        if trigger_type == "date":
            parse_datetime(self.run_at, self.timezone)
        elif trigger_type == "interval" and float(self.interval_seconds) <= 0:
            raise ValueError("interval_seconds must be positive")
        elif trigger_type == "cron" and len(str(self.cron or "").split()) != 5:
            raise ValueError("cron must contain five fields")
        if self.start_at:
            parse_datetime(self.start_at, self.timezone)
        if self.end_at:
            parse_datetime(self.end_at, self.timezone)
        object.__setattr__(self, "trigger_type", trigger_type)
        object.__setattr__(self, "intent_type", intent_type)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "action_prompt", " ".join(str(self.action_prompt or "").split())[:500])
        object.__setattr__(self, "priority", max(0, min(100, int(self.priority))))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    def with_updates(self, **updates: Any) -> ScheduledIntent:
        updates["updated_at"] = _utcnow()
        return replace(self, **updates)

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> ScheduledIntent:
        fields = {
            "text",
            "trigger_type",
            "intent_type",
            "timezone",
            "run_at",
            "interval_seconds",
            "cron",
            "start_at",
            "end_at",
            "action_prompt",
            "priority",
            "intent_id",
            "status",
            "created_at",
            "updated_at",
            "metadata",
        }
        return cls(**{key: value for key, value in payload.items() if key in fields})
