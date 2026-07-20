from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredEvent:
    event_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }


class EventPersistence:
    def __init__(self, limit: int = 5000):
        self._events: list[StoredEvent] = []
        self._limit = max(100, limit)
        self._counter = 0
        self._current_session_id = f"session-{int(time.time())}"

    def _next_id(self) -> str:
        self._counter += 1
        return f"evt-{self._counter:08d}"

    def record(self, event_type: str, payload: dict[str, Any] | None = None) -> StoredEvent:
        event = StoredEvent(
            event_id=self._next_id(),
            event_type=event_type,
            payload=dict(payload or {}),
            session_id=self._current_session_id,
        )
        self._events.append(event)
        if len(self._events) > self._limit:
            self._events = self._events[-self._limit:]
        return event

    def replay_session(self, session_id: str) -> list[dict[str, Any]]:
        return [e.snapshot() for e in self._events if e.session_id == session_id]

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [e.snapshot() for e in self._events[-limit:]]

    def new_session(self) -> str:
        self._current_session_id = f"session-{int(time.time())}"
        return self._current_session_id

    def stats(self) -> dict[str, Any]:
        types: dict[str, int] = {}
        for e in self._events:
            types[e.event_type] = types.get(e.event_type, 0) + 1
        return {"total": len(self._events), "current_session": self._current_session_id, "by_type": types}


class JsonlEventPersistence(EventPersistence):
    def __init__(self, path: str | Path, limit: int = 5000):
        super().__init__(limit=limit)
        self._path = Path(path).resolve()
        self._write_error_logged = False
        self._load_existing()

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = StoredEvent(
                        event_id=str(data.get("event_id") or ""),
                        event_type=str(data.get("event_type") or ""),
                        payload=dict(data.get("payload") or {}),
                        session_id=str(data.get("session_id") or ""),
                        timestamp=float(data.get("timestamp") or time.time()),
                    )
                    if event.event_id:
                        self._events.append(event)
                except (json.JSONDecodeError, TypeError):
                    continue
        except (OSError, PermissionError) as exc:
            print(f"event persistence load failed ({self._path}): {exc}", file=sys.stderr, flush=True)

    def record(self, event_type: str, payload: dict[str, Any] | None = None) -> StoredEvent:
        event = super().record(event_type, payload)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.snapshot(), ensure_ascii=False) + "\n")
            self._write_error_logged = False
        except OSError as exc:
            if not self._write_error_logged:
                self._write_error_logged = True
                print(f"event persistence write failed ({self._path}): {exc}; events stay in memory only", file=sys.stderr, flush=True)
        return event


def build_event_persistence(path: str | Path | None = None) -> EventPersistence:
    if not path:
        return EventPersistence()
    return JsonlEventPersistence(path)
