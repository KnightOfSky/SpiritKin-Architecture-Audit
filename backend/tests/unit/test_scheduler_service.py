from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from backend.app.command_gateway import (
    build_scheduler_intents_response,
    build_scheduler_intents_update_response,
)
from backend.app.runtime import SpiritKinRuntime
from backend.proactive import OpeningBubbleService, ProactivePolicy, ProactiveService
from backend.runtime.events.persistence import EventPersistence
from backend.runtime.scheduler import ScheduledIntent, SchedulerService


def _future_date_intent(intent_id: str = "intent-future") -> ScheduledIntent:
    return ScheduledIntent(
        intent_id=intent_id,
        text="未来提醒",
        trigger_type="date",
        timezone="Asia/Shanghai",
        run_at="2099-01-01T09:00:00+08:00",
        action_prompt="请显示未来提醒",
    )


def test_timezone_and_dst_are_owned_by_apscheduler(tmp_path) -> None:
    service = SchedulerService(tmp_path / "scheduler.sqlite3")
    intent = ScheduledIntent(
        text="DST 提醒",
        trigger_type="cron",
        timezone="America/New_York",
        cron="30 1 * * *",
    )

    trigger = service._build_trigger(intent)
    next_fire = trigger.get_next_fire_time(None, datetime(2026, 10, 31, 23, tzinfo=UTC))

    assert next_fire is not None
    assert getattr(next_fire.tzinfo, "key", "") == "America/New_York"
    assert next_fire.astimezone(ZoneInfo("America/New_York")).hour == 1
    assert intent.snapshot()["timezone"] == "America/New_York"
    service.shutdown()


def test_sqlite_jobstore_recovers_after_restart(tmp_path) -> None:
    path = tmp_path / "scheduler.sqlite3"
    first = SchedulerService(path)
    first.add(_future_date_intent())
    first.shutdown()

    second = SchedulerService(path)
    second.start(paused=True)
    restored = second.get("intent-future")

    assert restored is not None
    assert restored["text"] == "未来提醒"
    assert restored["next_run_time"].startswith("2099-01-01T09:00:00+08:00")
    second.shutdown()


def test_job_defaults_prevent_misfire_storms_and_concurrency(tmp_path) -> None:
    service = SchedulerService(
        tmp_path / "scheduler.sqlite3",
        misfire_grace_time=45,
        coalesce=True,
        max_instances=1,
    )
    snapshot = service.add(_future_date_intent())

    assert snapshot["job"] == {"coalesce": True, "max_instances": 1, "misfire_grace_time": 45}
    service.shutdown()


def test_delivery_is_idempotent_per_trigger_bucket(tmp_path) -> None:
    events = []
    service = SchedulerService(tmp_path / "scheduler.sqlite3", event_sink=events.append, clock=lambda: 1000.0)
    intent = ScheduledIntent(
        intent_id="intent-interval",
        text="周期提醒",
        trigger_type="interval",
        interval_seconds=60,
        start_at="1970-01-01T00:00:00+00:00",
    )
    service._save_intent(intent)

    first = service._deliver(intent)
    duplicate = service._deliver(intent)

    assert first is not None and first["type"] == "scheduler.intent_due"
    assert duplicate is None
    assert len(events) == 1
    service.shutdown()


def test_due_date_fires_once_and_is_marked_complete(tmp_path) -> None:
    events = []
    statuses_seen_by_sink = []
    run_at = datetime.fromtimestamp(time.time() + 0.5, tz=UTC).isoformat()

    def record_event(event) -> None:
        statuses_seen_by_sink.append(service.get("intent-soon")["status"])
        events.append(event)

    service = SchedulerService(tmp_path / "scheduler.sqlite3", event_sink=record_event)
    service.add(ScheduledIntent(intent_id="intent-soon", text="两秒内提醒", trigger_type="date", run_at=run_at))

    deadline = time.time() + 3
    while time.time() < deadline and not events:
        time.sleep(0.05)

    assert [event["type"] for event in events] == ["scheduler.intent_due"]
    assert statuses_seen_by_sink == ["complete"]
    assert service.get("intent-soon")["status"] == "complete"
    service.shutdown()


def test_kill_switch_suppresses_delivery(tmp_path) -> None:
    events = []
    service = SchedulerService(
        tmp_path / "scheduler.sqlite3",
        event_sink=events.append,
        safety_gate=lambda intent: SimpleNamespace(allowed=False),
        clock=lambda: 1000.0,
    )
    intent = _future_date_intent("intent-blocked")
    service._save_intent(intent)

    event = service._deliver(intent, force=True)

    assert event is not None
    assert event["type"] == "scheduler.intent_suppressed"
    assert event["payload"]["reason_code"] == "safety_stop"
    service.shutdown()


def test_dangerous_intent_never_owns_an_executor_and_requires_confirmation(tmp_path) -> None:
    events = []
    service = SchedulerService(tmp_path / "scheduler.sqlite3", event_sink=events.append, clock=lambda: 1000.0)
    intent = ScheduledIntent(
        intent_id="intent-action",
        text="清理临时文件",
        intent_type="action",
        trigger_type="interval",
        interval_seconds=3600,
        action_prompt="请先展示清理计划和风险，再等待确认",
    )
    service._save_intent(intent)

    event = service._deliver(intent, force=True)

    assert event is not None
    assert event["payload"]["requires_confirmation"] is True
    assert event["payload"]["action_prompt"].startswith("请先展示")
    assert not hasattr(service, "executor")
    assert not hasattr(service, "tool_registry")
    service.shutdown()


class _RelationshipMemory:
    def snapshot(self):
        return {
            "relationship": {
                "stage": "familiar",
                "care_strategy": {"proactive_level": "off"},
                "boundaries": [{"kind": "proactive", "active": True}],
            }
        }


def test_relationship_boundary_suppresses_notification_but_keeps_intent() -> None:
    runtime = SpiritKinRuntime(agent=object(), emit_runtime_events=False)
    runtime.event_persistence = EventPersistence()
    runtime.memory_orchestrator = _RelationshipMemory()
    runtime.proactive = ProactiveService(
        ProactivePolicy(quiet_hours_start=0, quiet_hours_end=0, cooldown_seconds=0, daily_limit=20)
    )
    runtime.opening_bubbles = OpeningBubbleService()
    due_event = {
        "type": "scheduler.intent_due",
        "payload": {
            "intent_id": "intent-reminder",
            "delivery_id": "delivery-1",
            "intent_type": "reminder",
            "text": "喝水提醒",
            "action_prompt": "显示喝水提醒",
            "priority": 80,
            "timestamp": time.time(),
        },
    }

    runtime._handle_scheduler_event(due_event)
    stats = runtime.event_persistence.stats()["by_type"]

    assert stats["scheduler.intent_due"] == 1
    assert stats["proactive.suppressed"] == 1
    assert "opening_bubble.present" not in stats
    runtime.close()


def test_crud_and_gateway_use_one_contract(tmp_path) -> None:
    runtime = SpiritKinRuntime(agent=object(), emit_runtime_events=False)
    runtime.scheduler = SchedulerService(tmp_path / "scheduler.sqlite3")
    create_status, created = build_scheduler_intents_update_response(
        runtime,
        {"action": "create", "intent": _future_date_intent("intent-api").snapshot()},
    )
    list_status, listed = build_scheduler_intents_response(runtime)
    pause_status, paused = build_scheduler_intents_update_response(
        runtime,
        {"action": "pause", "intent_id": "intent-api"},
    )
    update_status, updated = build_scheduler_intents_update_response(
        runtime,
        {"action": "update", "intent_id": "intent-api", "updates": {"text": "修改后的提醒"}},
    )
    cancel_status, cancelled = build_scheduler_intents_update_response(
        runtime,
        {"action": "cancel", "intent_id": "intent-api"},
    )

    assert {create_status, list_status, pause_status, update_status, cancel_status} == {200}
    assert created["result"]["intent_id"] == "intent-api"
    assert listed["scheduler"]["count"] == 1
    assert paused["result"]["status"] == "paused"
    assert updated["result"]["text"] == "修改后的提醒"
    assert cancelled["result"]["status"] == "cancelled"
    runtime.close()
