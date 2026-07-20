from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from backend.proactive.signals import ProactiveSignal


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ProactivePolicy:
    enabled: bool = True
    quiet_hours_start: int = 22
    quiet_hours_end: int = 8
    cooldown_seconds: float = 10 * 60
    daily_limit: int = 3
    minimum_value_score: float = 0.6
    active_session_suppression_seconds: float = 45.0
    active_session_value_override: float = 0.85
    timezone: tzinfo | None = None

    @classmethod
    def from_env(cls) -> ProactivePolicy:
        return cls(
            enabled=_env_bool("SPIRITKIN_PROACTIVE_ENABLED", True),
            quiet_hours_start=max(0, min(23, _env_int("SPIRITKIN_PROACTIVE_QUIET_START", 22))),
            quiet_hours_end=max(0, min(23, _env_int("SPIRITKIN_PROACTIVE_QUIET_END", 8))),
            cooldown_seconds=max(0.0, _env_float("SPIRITKIN_PROACTIVE_COOLDOWN_SECONDS", 10 * 60)),
            daily_limit=max(0, _env_int("SPIRITKIN_PROACTIVE_DAILY_LIMIT", 3)),
            minimum_value_score=max(0.0, min(1.0, _env_float("SPIRITKIN_PROACTIVE_MIN_VALUE", 0.6))),
            active_session_suppression_seconds=max(
                0.0,
                _env_float("SPIRITKIN_PROACTIVE_ACTIVE_SESSION_SECONDS", 45.0),
            ),
        )


@dataclass(frozen=True)
class ProactiveHistory:
    suggested_at: tuple[float, ...] = ()
    cooldown_until: float = 0.0
    feedback_cooldown_until: float = 0.0
    dismissed_signal_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ProactiveDecision:
    allowed: bool
    reason_code: str
    cooldown_until: float
    requires_confirmation: bool = True


def _local_datetime(timestamp: float, timezone: tzinfo | None) -> datetime:
    if timezone is not None:
        return datetime.fromtimestamp(timestamp, tz=timezone)
    return datetime.fromtimestamp(timestamp).astimezone()


def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _relationship_blocks(relationship: dict[str, Any]) -> bool:
    care = relationship.get("care_strategy") if isinstance(relationship.get("care_strategy"), dict) else {}
    if str(care.get("proactive_level") or "").lower() == "off":
        return True
    boundaries = relationship.get("boundaries") if isinstance(relationship.get("boundaries"), list) else []
    return any(
        isinstance(item, dict)
        and bool(item.get("active", True))
        and str(item.get("kind") or "").lower() == "proactive"
        for item in boundaries
    )


def _daily_suggestion_count(history: ProactiveHistory, *, now: float, timezone: tzinfo | None) -> int:
    today = _local_datetime(now, timezone).date()
    return sum(1 for timestamp in history.suggested_at if _local_datetime(timestamp, timezone).date() == today)


def evaluate(
    signal: ProactiveSignal,
    relationship: dict[str, Any],
    presence: dict[str, Any],
    policy: ProactivePolicy,
    history: ProactiveHistory | None = None,
    *,
    now: float | None = None,
) -> ProactiveDecision:
    history = history or ProactiveHistory()
    current = signal.created_at if now is None else float(now)
    current_cooldown = max(history.cooldown_until, history.feedback_cooldown_until)
    if not policy.enabled:
        return ProactiveDecision(False, "policy_disabled", current_cooldown)
    if signal.expires_at and current >= signal.expires_at:
        return ProactiveDecision(False, "signal_expired", current_cooldown)
    if signal.signal_id in history.dismissed_signal_ids:
        return ProactiveDecision(False, "feedback_dismissed", current_cooldown)
    if not signal.essential and _relationship_blocks(relationship):
        return ProactiveDecision(False, "relationship_boundary", current_cooldown)

    local_now = _local_datetime(current, policy.timezone)
    if not signal.essential and _in_quiet_hours(local_now.hour, policy.quiet_hours_start, policy.quiet_hours_end):
        return ProactiveDecision(False, "quiet_hours", current_cooldown)
    if not signal.essential and current < current_cooldown:
        return ProactiveDecision(False, "cooldown_active", current_cooldown)
    if not signal.essential and _daily_suggestion_count(history, now=current, timezone=policy.timezone) >= policy.daily_limit:
        return ProactiveDecision(False, "daily_limit", current_cooldown)

    idle_seconds = max(0.0, float(presence.get("idle_seconds") or 0.0))
    if (
        not signal.essential
        and idle_seconds < policy.active_session_suppression_seconds
        and signal.value_score < policy.active_session_value_override
    ):
        return ProactiveDecision(False, "session_active", current_cooldown)
    if not signal.essential and signal.value_score < policy.minimum_value_score:
        return ProactiveDecision(False, "low_value", current_cooldown)

    reason = "allowed_essential" if signal.essential else "allowed_high_value" if signal.value_score >= 0.85 else "allowed_contextual"
    return ProactiveDecision(True, reason, current + policy.cooldown_seconds)
