from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.expression.model_interaction import build_performance_interaction

EVENT_SCHEMA_VERSION = "v1"


LPM_PHASE_TO_AVATAR = {
    "silence_idle": ("idle", "idle", False),
    "listening": ("listening", "listen", False),
    "attentive_wait": ("listening", "wait", False),
    "thinking": ("thinking", "think", False),
    "acting": ("thinking", "act", False),
    "speaking": ("speaking", "speak", True),
    "interrupted": ("alert", "interrupted", False),
    "waiting_confirmation": ("waiting", "confirm", False),
    "error": ("error", "error", False),
}

@dataclass(frozen=True)
class PerformanceState:
    """LPM-like performance event for Listen/Speak/Silence orchestration."""

    phase: str
    message: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_event(self) -> dict[str, object]:
        return {
            "type": "performance.state",
            "schema_version": EVENT_SCHEMA_VERSION,
            "payload": {
                "phase": self.phase,
                "message": self.message,
                "metadata": dict(self.metadata or {}),
                "timestamp": self.timestamp,
            },
        }

    def to_avatar_event(self) -> dict[str, object]:
        emotion, action, speaking = LPM_PHASE_TO_AVATAR.get(self.phase, ("neutral", self.phase, False))
        return {
            "type": "avatar.state",
            "schema_version": EVENT_SCHEMA_VERSION,
            "payload": {
                "emotion": emotion,
                "speaking": speaking,
                "action": action,
                "message": "",
                "performance_phase": self.phase,
                "metadata": dict(self.metadata or {}),
                "timestamp": self.timestamp,
            },
        }

    def to_model_interaction_event(self) -> dict[str, object]:
        return build_performance_interaction(self.phase, self.message, self.metadata)


class PerformanceController:
    """Emits the state stream consumed by UI, Live2D, or future diffusion avatars."""

    def __init__(self, emit: Callable[[dict[str, object]], object] | None = None):
        self._emit = emit
        self.history: list[PerformanceState] = []

    @property
    def phase(self) -> str:
        if not self.history:
            return "silence_idle"
        return self.history[-1].phase

    def emit(self, phase: str, message: str = "", metadata: dict[str, object] | None = None) -> PerformanceState:
        state = PerformanceState(phase=phase, message=message, metadata=dict(metadata or {}))
        self.history.append(state)
        if self._emit is not None:
            self._emit(state.to_event())
            self._emit(state.to_avatar_event())
            self._emit(state.to_model_interaction_event())
        return state
