from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Condition, Lock

DEFAULT_RESOURCE_LIMITS = {
    "interactive": 1,
    "cpu_io": 2,
    "gpu_heavy": 1,
}


@dataclass(frozen=True)
class ResourceReservation:
    profile: str


@dataclass
class ResourceBudgetGate:
    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RESOURCE_LIMITS))

    def __post_init__(self) -> None:
        self._lock = Lock()
        self._condition = Condition(self._lock)
        normalized = {key: max(0, int(value)) for key, value in dict(self.limits).items()}
        self.limits = {**DEFAULT_RESOURCE_LIMITS, **normalized}
        self._active = {key: 0 for key in self.limits}

    def try_acquire(self, profile: str) -> ResourceReservation | None:
        profile_key = profile if profile in self.limits else "interactive"
        with self._lock:
            if self._active[profile_key] >= self.limits[profile_key]:
                return None
            self._active[profile_key] += 1
            return ResourceReservation(profile=profile_key)

    def wait_acquire(self, profile: str, timeout: float = 30.0) -> ResourceReservation | None:
        profile_key = profile if profile in self.limits else "interactive"
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._active[profile_key] >= self.limits[profile_key]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            self._active[profile_key] += 1
            return ResourceReservation(profile=profile_key)

    def release(self, reservation: ResourceReservation | None) -> None:
        if reservation is None:
            return
        with self._lock:
            current = self._active.get(reservation.profile, 0)
            self._active[reservation.profile] = max(0, current - 1)
            self._condition.notify_all()

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                profile: {"active": self._active[profile], "limit": self.limits[profile]}
                for profile in self.limits
            }
