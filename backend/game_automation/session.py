from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from backend.game_automation.manifest import GameAdapterManifest
from backend.security.safety_control import SafetyDecision, evaluate_execution_safety


class GameAutomationStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(frozen=True)
class GameAutomationStep:
    action: str
    params: dict[str, Any] | None = None
    delay_after_ms: int = 100


class GameAutomationDriver(Protocol):
    def title(self) -> str: ...
    def is_focused(self) -> bool: ...
    def observe(self) -> dict[str, Any]: ...
    def perform(self, action: str, params: dict[str, Any]) -> dict[str, Any]: ...
    def emergency_stop(self, reason: str) -> None: ...
    def capture_keyframe(self, label: str) -> dict[str, Any]: ...
    def close(self) -> None: ...


class GameAutomationAudit:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def record(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {
            "schema_version": "spiritkin.game_automation_audit.v1",
            "event_id": f"game_audit_{uuid.uuid4().hex[:16]}",
            "type": event_type,
            "timestamp": time.time(),
            **payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
        return event

    def replay(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class GameAutomationSession:
    FORBIDDEN_PARAM_KEYS = {"key", "keycode", "scan_code", "x", "y", "selector", "script", "memory", "packet"}

    def __init__(
        self,
        adapter: GameAdapterManifest,
        driver: GameAutomationDriver,
        *,
        audit: GameAutomationAudit,
        session_id: str | None = None,
        authorized: Callable[[str, dict[str, Any]], bool] | None = None,
        safety: Callable[..., SafetyDecision] = evaluate_execution_safety,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.adapter = adapter
        self.driver = driver
        self.audit = audit
        self.session_id = session_id or f"game_session_{uuid.uuid4().hex[:16]}"
        self.authorized = authorized or (lambda _action, _params: False)
        self.safety = safety
        self.clock = clock
        self.status = GameAutomationStatus.IDLE
        self.stop_reason = ""
        self._stop_event = threading.Event()
        self._action_times: deque[float] = deque()
        self._lock = threading.RLock()

    def start(self, url: str) -> bool:
        with self._lock:
            if not self.adapter.accepts_url(url) or not self.adapter.accepts_title(self.driver.title()):
                self._stop("adapter_target_mismatch", GameAutomationStatus.ERROR)
                return False
            self.status = GameAutomationStatus.RUNNING
            self.audit.record(
                "session.started",
                session_id=self.session_id,
                adapter_id=self.adapter.adapter_id,
                url=url,
                title=self.driver.title(),
            )
            return True

    def execute(self, step: GameAutomationStep) -> bool:
        with self._lock:
            if self.status != GameAutomationStatus.RUNNING or self._stop_event.is_set():
                return False
            action = str(step.action or "").strip()
            params = dict(step.params or {})
            if action not in self.adapter.allowed_actions:
                self._stop("action_not_allowlisted")
                return False
            if self.FORBIDDEN_PARAM_KEYS.intersection(key.lower() for key in params):
                self._stop("dangerous_input_rejected")
                return False
            if not self.authorized(action, params):
                self._stop("authorization_denied")
                return False
            safety = self.safety(
                target="browser_game",
                operation=f"game_{action}",
                actor="game_automation",
                read_only=False,
                dry_run=False,
            )
            if not safety.allowed:
                self._stop(safety.error_code or "kill_switch")
                return False
            if self.adapter.requires_focus and not self.driver.is_focused():
                self._stop("focus_lost")
                return False
            observation = self.driver.observe()
            scene = str(observation.get("scene") or "").strip()
            if scene not in self.adapter.expected_scenes or observation.get("uncertain") is True:
                self._stop("unknown_scene", GameAutomationStatus.PAUSED, observation=observation)
                return False
            if not self._consume_rate_slot():
                self._stop("rate_limit", GameAutomationStatus.PAUSED)
                return False
            keyframe = self.driver.capture_keyframe(f"before_{action}")
            try:
                result = self.driver.perform(action, params)
            except Exception as exc:
                self._stop("driver_error", GameAutomationStatus.ERROR, error=str(exc))
                return False
            self.audit.record(
                "action.completed",
                session_id=self.session_id,
                adapter_id=self.adapter.adapter_id,
                action=action,
                params=params,
                observation=observation,
                keyframe=keyframe,
                result=result,
            )
        return not self._stop_event.wait(max(0, int(step.delay_after_ms)) / 1000)

    def run(self, url: str, steps: Iterable[GameAutomationStep]) -> GameAutomationStatus:
        if not self.start(url):
            return self.status
        for step in steps:
            if not self.execute(step):
                return self.status
        with self._lock:
            if self.status == GameAutomationStatus.RUNNING:
                self.status = GameAutomationStatus.COMPLETED
                self.audit.record("session.completed", session_id=self.session_id, adapter_id=self.adapter.adapter_id)
        return self.status

    def request_stop(self, reason: str = "user_stop") -> None:
        started = self.clock()
        with self._lock:
            self._stop(reason)
            latency_ms = max(0.0, (self.clock() - started) * 1000)
            self.audit.record("stop.acknowledged", session_id=self.session_id, reason=reason, latency_ms=latency_ms)

    def close(self) -> None:
        try:
            self.driver.close()
        finally:
            if self.status not in {GameAutomationStatus.COMPLETED, GameAutomationStatus.STOPPED, GameAutomationStatus.ERROR}:
                self._stop("session_closed")

    def _consume_rate_slot(self) -> bool:
        now = self.clock()
        while self._action_times and now - self._action_times[0] >= 1.0:
            self._action_times.popleft()
        if len(self._action_times) >= max(1, int(self.adapter.max_actions_per_second)):
            return False
        self._action_times.append(now)
        return True

    def _stop(
        self,
        reason: str,
        status: GameAutomationStatus = GameAutomationStatus.STOPPED,
        **metadata: Any,
    ) -> None:
        if self._stop_event.is_set() and self.status in {GameAutomationStatus.STOPPED, GameAutomationStatus.ERROR}:
            return
        self._stop_event.set()
        self.stop_reason = reason
        self.status = status
        try:
            self.driver.emergency_stop(reason)
        except Exception:
            pass
        self.audit.record(
            "session.stopped",
            session_id=self.session_id,
            adapter_id=self.adapter.adapter_id,
            reason=reason,
            status=status.value,
            **metadata,
        )
