from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.runtime.scheduler.models import ScheduledIntent, parse_datetime, resolve_timezone

_SERVICE_REGISTRY: dict[str, SchedulerService] = {}
_REGISTRY_LOCK = threading.RLock()


def _dispatch_persisted_intent(service_key: str, intent_snapshot: dict[str, Any]) -> None:
    with _REGISTRY_LOCK:
        service = _SERVICE_REGISTRY.get(service_key)
    if service is not None:
        service._deliver(ScheduledIntent.from_snapshot(intent_snapshot))


class SchedulerService:
    def __init__(
        self,
        path: str | Path = "state/scheduler/jobs.sqlite3",
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        safety_gate: Callable[[ScheduledIntent], Any] | None = None,
        misfire_grace_time: int = 60,
        coalesce: bool = True,
        max_instances: int = 1,
        clock: Callable[[], float] = time.time,
        autostart: bool = False,
    ):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.event_sink = event_sink or (lambda event: None)
        self.safety_gate = safety_gate or (lambda intent: True)
        self.misfire_grace_time = max(1, int(misfire_grace_time))
        self.coalesce = bool(coalesce)
        self.max_instances = max(1, int(max_instances))
        self.clock = clock
        self.service_key = str(self.path).lower()
        self._lock = threading.RLock()
        self._started = False
        self._init_store()
        url = f"sqlite:///{self.path.as_posix()}"
        self._scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=url)},
            job_defaults={
                "coalesce": self.coalesce,
                "max_instances": self.max_instances,
                "misfire_grace_time": self.misfire_grace_time,
            },
            timezone=UTC,
        )
        with _REGISTRY_LOCK:
            _SERVICE_REGISTRY[self.service_key] = self
        if autostart:
            self.start()

    def start(self, *, paused: bool = False) -> None:
        with self._lock:
            if self._started:
                return
            self._scheduler.start(paused=paused)
            self._started = True

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            if self._started:
                self._scheduler.shutdown(wait=wait)
                self._started = False
        with _REGISTRY_LOCK:
            if _SERVICE_REGISTRY.get(self.service_key) is self:
                _SERVICE_REGISTRY.pop(self.service_key, None)

    def add(self, intent: ScheduledIntent) -> dict[str, Any]:
        self._ensure_started()
        active = intent.with_updates(status="active")
        self._scheduler.add_job(
            _dispatch_persisted_intent,
            trigger=self._build_trigger(active),
            id=active.intent_id,
            kwargs={"service_key": self.service_key, "intent_snapshot": active.snapshot()},
            replace_existing=True,
            coalesce=self.coalesce,
            max_instances=self.max_instances,
            misfire_grace_time=self.misfire_grace_time,
        )
        self._save_intent(active)
        return self.get(active.intent_id) or active.snapshot()

    def get(self, intent_id: str) -> dict[str, Any] | None:
        intent = self._load_intent(intent_id)
        if intent is None:
            return None
        return self._with_job_state(intent)

    def list(self, *, include_finished: bool = True) -> list[dict[str, Any]]:
        intents = self._load_intents()
        if not include_finished:
            intents = [intent for intent in intents if intent.status not in {"complete", "cancelled"}]
        return [self._with_job_state(intent) for intent in intents]

    def pause(self, intent_id: str) -> dict[str, Any]:
        self._ensure_started()
        self._scheduler.pause_job(intent_id)
        return self._set_status(intent_id, "paused")

    def resume(self, intent_id: str) -> dict[str, Any]:
        self._ensure_started()
        self._scheduler.resume_job(intent_id)
        return self._set_status(intent_id, "active")

    def cancel(self, intent_id: str) -> dict[str, Any]:
        self._ensure_started()
        job = self._scheduler.get_job(intent_id)
        if job is not None:
            self._scheduler.remove_job(intent_id)
        return self._set_status(intent_id, "cancelled")

    def update(self, intent_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        current = self._load_intent(intent_id)
        if current is None:
            raise KeyError(intent_id)
        forbidden = {"intent_id", "created_at"}
        updated = current.with_updates(**{key: value for key, value in updates.items() if key not in forbidden})
        return self.add(updated)

    def run_now(self, intent_id: str) -> dict[str, object]:
        intent = self._load_intent(intent_id)
        if intent is None:
            raise KeyError(intent_id)
        event = self._deliver(intent, force=True)
        if event is None:
            return {"type": "scheduler.intent_suppressed", "payload": {"intent_id": intent_id, "reason_code": "duplicate"}}
        return event

    def _ensure_started(self) -> None:
        if not self._started:
            self.start()

    def _build_trigger(self, intent: ScheduledIntent):
        timezone = resolve_timezone(intent.timezone)
        if intent.trigger_type == "date":
            return DateTrigger(run_date=parse_datetime(intent.run_at, intent.timezone), timezone=timezone)
        start_date = parse_datetime(intent.start_at, intent.timezone) if intent.start_at else None
        end_date = parse_datetime(intent.end_at, intent.timezone) if intent.end_at else None
        if intent.trigger_type == "interval":
            return IntervalTrigger(
                seconds=float(intent.interval_seconds),
                start_date=start_date,
                end_date=end_date,
                timezone=timezone,
            )
        if start_date is None and end_date is None:
            return CronTrigger.from_crontab(intent.cron, timezone=timezone)
        minute, hour, day, month, day_of_week = intent.cron.split()
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=timezone,
            start_date=start_date,
            end_date=end_date,
        )

    def _deliver(self, intent: ScheduledIntent, *, force: bool = False) -> dict[str, object] | None:
        current = float(self.clock())
        delivery_key = self._delivery_key(intent, current, force=force)
        if not self._claim_delivery(delivery_key, intent.intent_id, current):
            return None
        try:
            decision = self.safety_gate(intent)
            allowed = bool(getattr(decision, "allowed", decision))
            if not allowed:
                event = self._event(intent, delivery_key, current, "scheduler.intent_suppressed", "safety_stop")
            else:
                event = self._event(intent, delivery_key, current, "scheduler.intent_due", "scheduled_time_reached")
            if intent.trigger_type == "date":
                self._set_status(intent.intent_id, "complete", missing_ok=True)
            self.event_sink(event)
            return event
        except Exception:
            self._release_delivery(delivery_key)
            raise

    def _delivery_key(self, intent: ScheduledIntent, now: float, *, force: bool) -> str:
        if force:
            return f"{intent.intent_id}:manual:{uuid4().hex}"
        if intent.trigger_type == "date":
            bucket = intent.run_at
        elif intent.trigger_type == "interval":
            start = parse_datetime(intent.start_at, intent.timezone).timestamp() if intent.start_at else 0.0
            bucket = str(int(max(0.0, now - start) // float(intent.interval_seconds)))
        else:
            bucket = datetime.fromtimestamp(now, tz=ZoneInfo(intent.timezone)).strftime("%Y-%m-%dT%H:%M")
        return f"{intent.intent_id}:{intent.trigger_type}:{bucket}"

    @staticmethod
    def _event(
        intent: ScheduledIntent,
        delivery_key: str,
        timestamp: float,
        event_type: str,
        reason_code: str,
    ) -> dict[str, object]:
        return {
            "type": event_type,
            "schema_version": "v1",
            "payload": {
                "intent_id": intent.intent_id,
                "delivery_id": delivery_key,
                "intent_type": intent.intent_type,
                "text": intent.text,
                "action_prompt": intent.action_prompt,
                "priority": intent.priority,
                "timezone": intent.timezone,
                "trigger_type": intent.trigger_type,
                "reason_code": reason_code,
                "requires_confirmation": True,
                "timestamp": timestamp,
                "intent": intent.snapshot(),
            },
        }

    def _with_job_state(self, intent: ScheduledIntent) -> dict[str, Any]:
        snapshot = intent.snapshot()
        job = self._scheduler.get_job(intent.intent_id) if self._started else None
        snapshot["next_run_time"] = job.next_run_time.isoformat() if job is not None and job.next_run_time else ""
        snapshot["job"] = {
            "coalesce": bool(getattr(job, "coalesce", self.coalesce)),
            "max_instances": int(getattr(job, "max_instances", self.max_instances)),
            "misfire_grace_time": int(getattr(job, "misfire_grace_time", self.misfire_grace_time)),
        }
        return snapshot

    def _init_store(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_intents (
                    intent_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_intent_deliveries (
                    delivery_key TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    delivered_at REAL NOT NULL
                )
                """
            )

    def _save_intent(self, intent: ScheduledIntent) -> None:
        payload = json.dumps(intent.snapshot(), ensure_ascii=False, sort_keys=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO scheduled_intents(intent_id, payload, status, updated_at) VALUES (?, ?, ?, ?)",
                (intent.intent_id, payload, intent.status, float(self.clock())),
            )

    def _load_intent(self, intent_id: str) -> ScheduledIntent | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT payload FROM scheduled_intents WHERE intent_id = ?", (intent_id,)).fetchone()
        return ScheduledIntent.from_snapshot(json.loads(row[0])) if row else None

    def _load_intents(self) -> list[ScheduledIntent]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute("SELECT payload FROM scheduled_intents ORDER BY updated_at DESC").fetchall()
        return [ScheduledIntent.from_snapshot(json.loads(row[0])) for row in rows]

    def _set_status(self, intent_id: str, status: str, *, missing_ok: bool = False) -> dict[str, Any]:
        intent = self._load_intent(intent_id)
        if intent is None:
            if missing_ok:
                return {"intent_id": intent_id, "status": status}
            raise KeyError(intent_id)
        updated = intent.with_updates(status=status)
        self._save_intent(updated)
        return self._with_job_state(updated)

    def _claim_delivery(self, delivery_key: str, intent_id: str, delivered_at: float) -> bool:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO scheduled_intent_deliveries(delivery_key, intent_id, delivered_at) VALUES (?, ?, ?)",
                (delivery_key, intent_id, delivered_at),
            )
            return cursor.rowcount == 1

    def _release_delivery(self, delivery_key: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM scheduled_intent_deliveries WHERE delivery_key = ?", (delivery_key,))
