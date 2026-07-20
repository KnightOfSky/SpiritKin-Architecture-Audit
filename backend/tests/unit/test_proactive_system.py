from __future__ import annotations

import time
from datetime import UTC, datetime

from backend.agents.base import AgentReply
from backend.app.runtime import SpiritKinRuntime
from backend.runtime.events.persistence import EventPersistence
from backend.proactive import (
    ProactiveHistory,
    ProactivePolicy,
    ProactiveService,
    ProactiveSignal,
    evaluate,
)


def _now(hour: int = 12) -> float:
    return datetime(2026, 7, 17, hour, tzinfo=UTC).timestamp()


def _signal(*, signal_id: str = "signal-1", value: float = 0.75, now: float | None = None) -> ProactiveSignal:
    current = _now() if now is None else now
    return ProactiveSignal(
        signal_id=signal_id,
        kind="task_context",
        summary="完成主动交互策略",
        source="unit_test",
        value_score=value,
        created_at=current,
        expires_at=current + 3600,
    )


def _policy(**overrides) -> ProactivePolicy:
    values = {
        "quiet_hours_start": 0,
        "quiet_hours_end": 0,
        "cooldown_seconds": 600,
        "daily_limit": 3,
        "timezone": UTC,
    }
    values.update(overrides)
    return ProactivePolicy(**values)


def test_proactive_boundary_blocks() -> None:
    relationship = {
        "stage": "familiar",
        "care_strategy": {"proactive_level": "off"},
        "boundaries": [{"kind": "proactive", "active": True}],
    }

    decision = evaluate(
        _signal(),
        relationship,
        {"idle_seconds": 600},
        _policy(),
        now=_now(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "relationship_boundary"


def test_quiet_hours_suppress() -> None:
    decision = evaluate(
        _signal(now=_now(23)),
        {},
        {"idle_seconds": 600},
        _policy(quiet_hours_start=22, quiet_hours_end=8),
        now=_now(23),
    )

    assert decision.allowed is False
    assert decision.reason_code == "quiet_hours"


def test_daily_limit() -> None:
    history = ProactiveHistory(suggested_at=(_now(9), _now(10), _now(11)))

    decision = evaluate(
        _signal(),
        {},
        {"idle_seconds": 600},
        _policy(daily_limit=3),
        history,
        now=_now(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "daily_limit"


def test_high_value_signal_allowed() -> None:
    decision = evaluate(
        _signal(value=0.95),
        {"stage": "new"},
        {"idle_seconds": 0},
        _policy(),
        now=_now(),
    )

    assert decision.allowed is True
    assert decision.reason_code == "allowed_high_value"
    assert decision.requires_confirmation is True


def test_suggestion_has_no_executor() -> None:
    service = ProactiveService(_policy())

    decision, suggestion, event = service.evaluate_signal(
        _signal(),
        relationship={"stage": "acquainted"},
        presence={"idle_seconds": 600},
        now=_now(),
    )

    assert decision.allowed is True
    assert suggestion is not None
    assert suggestion.intent == "offer_action"
    assert suggestion.requires_confirmation is True
    assert event["type"] == "proactive.suggested"
    assert not hasattr(service, "executor")
    assert not hasattr(service, "tool_registry")


def test_feedback_adjusts_cooldown() -> None:
    service = ProactiveService(_policy(cooldown_seconds=60))
    service.evaluate_signal(_signal(), presence={"idle_seconds": 600}, now=_now())

    feedback_event = service.record_feedback("signal-1", "dismissed", now=_now() + 1)
    decision, _, _ = service.evaluate_signal(
        _signal(signal_id="signal-2", now=_now() + 2),
        presence={"idle_seconds": 600},
        now=_now() + 2,
    )

    assert feedback_event["type"] == "proactive.feedback"
    assert feedback_event["payload"]["cooldown_until"] >= _now() + 181
    assert decision.allowed is False
    assert decision.reason_code == "cooldown_active"


def test_suppressed_event_keeps_trace_fields() -> None:
    service = ProactiveService(_policy())

    _, suggestion, event = service.evaluate_signal(
        _signal(value=0.2),
        relationship={"stage": "new"},
        presence={"idle_seconds": 600},
        now=_now(),
    )

    assert suggestion is None
    assert event["type"] == "proactive.suppressed"
    assert event["payload"]["signal_id"] == "signal-1"
    assert event["payload"]["reason_code"] == "low_value"
    assert event["payload"]["relationship_stage"] == "new"
    assert event["payload"]["requires_confirmation"] is True


def test_repeated_context_signals_only_surface_once_during_cooldown() -> None:
    service = ProactiveService(_policy(cooldown_seconds=600, daily_limit=20))
    events = []

    for index in range(20):
        _, _, event = service.evaluate_signal(
            _signal(signal_id=f"signal-{index}", value=0.65, now=_now() + index),
            presence={"idle_seconds": 600},
            now=_now() + index,
        )
        events.append(event)

    assert sum(event["type"] == "proactive.suggested" for event in events) == 1
    assert sum(event["type"] == "proactive.suppressed" for event in events) == 19


class _CapturingAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def process(self, user_input, visual_context="", channel="", input_metadata=None):
        self.calls.append(
            {
                "user_input": user_input,
                "visual_context": visual_context,
                "channel": channel,
                "input_metadata": dict(input_metadata or {}),
            }
        )
        return AgentReply(text="需要确认", requires_confirmation=True)


def test_runtime_acceptance_reenters_normal_interaction_path() -> None:
    agent = _CapturingAgent()
    runtime = SpiritKinRuntime(agent=agent, emit_runtime_events=False)
    runtime.event_persistence = EventPersistence()
    runtime.proactive = ProactiveService(_policy(cooldown_seconds=0))
    runtime.presence.last_activity_at = time.time() - 600
    signal = _signal()

    event = runtime.handle_proactive_signal(signal, now=_now())
    reply = runtime.accept_proactive_suggestion(signal.signal_id, channel="desktop")

    assert event["type"] == "proactive.suggested"
    assert reply is not None and reply.requires_confirmation is True
    assert agent.calls[-1]["input_metadata"]["proactive_acceptance"] is True
    assert agent.calls[-1]["input_metadata"]["proactive_signal_id"] == signal.signal_id
    assert runtime.event_persistence.stats()["by_type"]["proactive.suggested"] == 1
    assert runtime.event_persistence.stats()["by_type"]["proactive.feedback"] == 1
    runtime.close()
