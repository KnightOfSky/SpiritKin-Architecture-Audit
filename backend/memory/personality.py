from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MOODS = ("happy", "neutral", "tired", "excited", "calm", "curious")

MOOD_TRANSITIONS: dict[str, dict[str, float]] = {
    "neutral": {"happy": 0.25, "curious": 0.20, "tired": 0.10, "calm": 0.15},
    "happy": {"excited": 0.30, "calm": 0.20, "neutral": 0.15},
    "curious": {"happy": 0.25, "excited": 0.20, "neutral": 0.15},
    "excited": {"happy": 0.30, "calm": 0.25, "neutral": 0.15},
    "tired": {"calm": 0.30, "neutral": 0.25},
    "calm": {"neutral": 0.20, "curious": 0.20, "happy": 0.10},
}


@dataclass
class PersonalityState:
    mood: str = "neutral"
    traits: dict[str, float] = field(default_factory=lambda: {
        "openness": 0.7, "conscientiousness": 0.8, "extraversion": 0.5, "agreeableness": 0.8,
    })
    preferences: dict[str, Any] = field(default_factory=dict)
    interaction_count: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    last_interaction_at: float = 0.0
    session_started_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def mood_transition(self, event_type: str) -> str:
        previous = self.mood

        if event_type == "user_praise":
            self.mood = "happy"
        elif event_type == "user_correction":
            self.mood = "neutral"
            self.traits["agreeableness"] = min(1.0, self.traits.get("agreeableness", 0.8) + 0.02)
        elif event_type == "execution_success":
            candidates = MOOD_TRANSITIONS.get(self.mood, {}).get("happy", 0.2)
            if random.random() < candidates:
                self.mood = random.choice(["happy", "excited", "calm"])
        elif event_type == "execution_failure":
            self.mood = random.choice(["neutral", "curious", "tired"])
        elif event_type == "long_idle":
            self.mood = random.choice(["calm", "tired", "neutral"])
        elif event_type == "session_start":
            if time.time() - self.last_interaction_at > 86400:
                self.mood = "curious"

        return previous

    def update_trait(self, trait: str, delta: float) -> None:
        if trait in self.traits:
            self.traits[trait] = max(0.0, min(1.0, self.traits[trait] + delta))

    def set_preference(self, key: str, value: Any) -> None:
        self.preferences[key] = value

    def get_preference(self, key: str, default: Any = None) -> Any:
        return self.preferences.get(key, default)

    def personality_context(self) -> str:
        parts = [
            f"当前心情: {self.mood}",
            f"已交互 {self.interaction_count} 次",
            f"陪伴模式: {self.companion_mode()}",
        ]
        if self.preferences.get("nickname"):
            parts.append(f"用户称呼偏好: {self.preferences['nickname']}")
        if self.preferences.get("reply_style"):
            parts.append(f"回复风格偏好: {self.preferences['reply_style']}")
        return "；".join(parts)

    def companion_mode(self) -> str:
        if self.failed_actions >= 2 and self.failed_actions >= self.successful_actions:
            return "repair_support"
        if self.mood == "tired":
            return "quiet_presence"
        if self.mood in {"curious", "excited"}:
            return "active_companion"
        if self.interaction_count == 0:
            return "standby"
        return "focused_support"

    def lpm_state(self) -> dict[str, Any]:
        session_minutes = max(0.0, (time.time() - self.session_started_at) / 60.0)
        familiarity = min(1.0, self.interaction_count / 100.0)
        reliability = self.successful_actions / max(1, self.successful_actions + self.failed_actions)
        energy = 0.55 + self.traits.get("extraversion", 0.5) * 0.25
        if self.mood in {"excited", "happy"}:
            energy += 0.15
        if self.mood == "tired":
            energy -= 0.25
        return {
            "mode": self.companion_mode(),
            "mood": self.mood,
            "energy": round(max(0.0, min(1.0, energy)), 3),
            "familiarity": round(familiarity, 3),
            "reliability": round(reliability, 3),
            "session_minutes": round(session_minutes, 2),
            "interaction_count": self.interaction_count,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "mood": self.mood,
            "traits": dict(self.traits),
            "preferences": dict(self.preferences),
            "interaction_count": self.interaction_count,
            "successful_actions": self.successful_actions,
            "failed_actions": self.failed_actions,
            "last_interaction_at": self.last_interaction_at,
            "session_started_at": self.session_started_at,
            "lpm_state": self.lpm_state(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> PersonalityState:
        return cls(
            mood=str(data.get("mood") or "neutral"),
            traits=dict(data.get("traits") or {}),
            preferences=dict(data.get("preferences") or {}),
            interaction_count=int(data.get("interaction_count") or 0),
            successful_actions=int(data.get("successful_actions") or 0),
            failed_actions=int(data.get("failed_actions") or 0),
            last_interaction_at=float(data.get("last_interaction_at") or 0),
            session_started_at=float(data.get("session_started_at") or time.time()),
            metadata=dict(data.get("metadata") or {}),
        )


class PersonalityStore:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._state = PersonalityState()
        self._load()

    @property
    def state(self) -> PersonalityState:
        return self._state

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._state = PersonalityState.from_snapshot(data)
        except (json.JSONDecodeError, OSError):
            pass

    def save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def record_interaction(self, *, success: bool = True) -> str:
        self._state.interaction_count += 1
        self._state.last_interaction_at = time.time()
        if success:
            self._state.successful_actions += 1
            return self._state.mood_transition("execution_success")
        else:
            self._state.failed_actions += 1
            return self._state.mood_transition("execution_failure")

    def on_praise(self) -> str:
        return self._state.mood_transition("user_praise")

    def on_correction(self) -> str:
        return self._state.mood_transition("user_correction")

    def on_long_idle(self) -> str:
        return self._state.mood_transition("long_idle")


def build_personality_store(path: str | Path | None = None) -> PersonalityStore:
    return PersonalityStore(path)
