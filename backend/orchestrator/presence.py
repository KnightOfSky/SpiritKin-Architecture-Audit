from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PresenceManager:
    checkin_interval_seconds: float = 600.0
    idle_threshold_seconds: float = 120.0
    last_activity_at: float = field(default_factory=time.time)
    idle_actions_enabled: bool = True
    _timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _on_checkin: callable | None = field(default=None, init=False, repr=False)
    _contexts: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _observations: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def on_activity(self) -> None:
        self.last_activity_at = time.time()

    def idle_seconds(self) -> float:
        return time.time() - self.last_activity_at

    def is_idle(self) -> bool:
        return self.idle_seconds() >= self.idle_threshold_seconds

    def get_idle_action(self) -> str | None:
        if not self.idle_actions_enabled:
            return None
        idle = self.idle_seconds()
        if idle < 300:
            return None
        if idle < 900:
            return "我在这里，有什么需要帮忙的吗？"
        if idle < 3600:
            mins = int(idle / 60)
            return f"已经安静了 {mins} 分钟，随时可以唤醒我。"
        return "已经过去一段时间了，我还在。需要我做些什么吗？"

    def record_context(self, kind: str, summary: str, *, confidence: float = 1.0, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        observation = {
            "kind": kind or "context",
            "summary": summary[:300],
            "confidence": max(0.0, min(1.0, float(confidence))),
            "metadata": dict(metadata or {}),
            "timestamp": time.time(),
        }
        self._contexts[observation["kind"]] = observation
        self._observations.append(observation)
        if len(self._observations) > 50:
            self._observations = self._observations[-50:]
        return dict(observation)

    def proactive_suggestion(self) -> str | None:
        idle_action = self.get_idle_action()
        if idle_action:
            return idle_action
        task = self._contexts.get("task") or self._contexts.get("project")
        if task and self.idle_seconds() >= 60:
            return f"我看到你还在处理：{task['summary']}。需要我帮你拆下一步吗？"
        app = self._contexts.get("app")
        if app and self.idle_seconds() >= 120:
            return f"你已经在 {app['summary']} 停留了一会儿，要不要我帮你总结当前状态？"
        return None

    def set_checkin_callback(self, callback: callable) -> None:
        self._on_checkin = callback

    def start_checkin_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.checkin_interval_seconds, self._on_tick)
        self._timer.daemon = True
        self._timer.start()

    def _on_tick(self) -> None:
        if self._on_checkin is not None:
            self._on_checkin()
        self.start_checkin_timer()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "idle_seconds": self.idle_seconds(),
            "is_idle": self.is_idle(),
            "idle_action": self.get_idle_action(),
            "proactive_suggestion": self.proactive_suggestion(),
            "active_contexts": {key: dict(value) for key, value in self._contexts.items()},
            "recent_observations": [dict(item) for item in self._observations[-10:]],
            "checkin_interval_seconds": self.checkin_interval_seconds,
        }
