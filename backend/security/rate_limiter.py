from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    max_requests: int = 30
    window_seconds: float = 60.0
    key_by: str = "actor"


class InMemoryRateLimiter:
    def __init__(self, config: RateLimitConfig | None = None):
        self._config = config or RateLimitConfig()
        self._windows: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self._config.window_seconds
        self._windows[key] = [ts for ts in self._windows.get(key, []) if ts > cutoff]
        return len(self._windows[key]) < self._config.max_requests

    def record(self, key: str) -> None:
        now = time.time()
        cutoff = now - self._config.window_seconds
        self._windows[key] = [ts for ts in self._windows.get(key, []) if ts > cutoff]
        self._windows[key].append(now)

    def remaining(self, key: str) -> int:
        now = time.time()
        cutoff = now - self._config.window_seconds
        self._windows[key] = [ts for ts in self._windows.get(key, []) if ts > cutoff]
        return max(0, self._config.max_requests - len(self._windows[key]))

    def reset(self, key: str) -> None:
        self._windows.pop(key, None)
