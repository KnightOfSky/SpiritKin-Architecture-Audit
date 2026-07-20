from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryActivationPolicy:
    user_hit_base: float = 20.0
    user_hit_growth: float = 0.5
    maintenance_base: float = 8.0
    maintenance_decay: float = 0.3
    idle_weight: float = 1.5
    maintenance_weight: float = 0.3
    active_threshold: float = 30.0
    maximum: float = 100.0

    def on_user_hit(self, activation: float, hit_count: int) -> float:
        reward = self.user_hit_base * (1.0 + self.user_hit_growth * math.log1p(max(0, hit_count)))
        return self.clamp(activation + reward)

    def on_maintenance(self, activation: float, maintenance_count: int) -> float:
        reward = self.maintenance_base * math.exp(-self.maintenance_decay * max(0, maintenance_count))
        return self.clamp(activation + reward)

    def decay(
        self,
        activation: float,
        *,
        days_idle: float,
        days_since_maintenance: float,
        intrinsic_value: float,
    ) -> float:
        intrinsic = max(0.05, float(intrinsic_value))
        penalty = (
            self.idle_weight * max(0.0, days_idle) ** 2
            + self.maintenance_weight * max(0.0, days_since_maintenance) ** 2
        ) / math.sqrt(intrinsic)
        return self.clamp(activation - penalty)

    def state(self, activation: float) -> str:
        value = self.clamp(activation)
        if value <= 0.0:
            return "archived"
        if value < self.active_threshold:
            return "dormant"
        return "active"

    def clamp(self, activation: float) -> float:
        return max(0.0, min(self.maximum, float(activation)))


DEFAULT_ACTIVATION_POLICY = MemoryActivationPolicy()
