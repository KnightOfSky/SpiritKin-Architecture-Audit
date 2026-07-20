from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrajectoryStep:
    stage: str
    detail: str
    success: bool = True
    error_code: str = ""
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrajectoryReport:
    user_input: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    overall_success: bool = True
    bottleneck_stage: str = ""


class TrajectoryAnalyzer:
    def __init__(self):
        self._trajectories: list[TrajectoryReport] = []
        self._limit = 200

    def record_trajectory(self, report: TrajectoryReport) -> None:
        self._trajectories.append(report)
        if len(self._trajectories) > self._limit:
            self._trajectories = self._trajectories[-self._limit:]

    def detect_bottlenecks(self) -> list[dict[str, Any]]:
        stage_failures: dict[str, list[str]] = {}
        for traj in self._trajectories:
            for step in traj.steps:
                if not step.success:
                    stage_failures.setdefault(step.stage, []).append(step.error_code or "unknown")

        bottlenecks: list[dict[str, Any]] = []
        for stage, errors in sorted(stage_failures.items(), key=lambda x: -len(x[1])):
            bottlenecks.append({
                "stage": stage,
                "failure_count": len(errors),
                "top_errors": list(dict.fromkeys(errors))[:3],
            })
        return bottlenecks

    def generate_eval_cases(self, limit: int = 10) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        for traj in self._trajectories:
            if traj.overall_success:
                continue
            case = {
                "user_input": traj.user_input,
                "bottleneck_stage": traj.bottleneck_stage,
                "step_count": len(traj.steps),
                "failed_steps": [
                    {"stage": s.stage, "error_code": s.error_code, "detail": s.detail[:100]}
                    for s in traj.steps if not s.success
                ],
            }
            cases.append(case)
            if len(cases) >= limit:
                break
        return cases

    def stats(self) -> dict[str, Any]:
        total = len(self._trajectories)
        failures = sum(1 for t in self._trajectories if not t.overall_success)
        bottlenecks = self.detect_bottlenecks()
        return {
            "total_trajectories": total,
            "failure_count": failures,
            "success_rate": (total - failures) / max(1, total),
            "bottlenecks": bottlenecks[:5],
            "eval_cases_available": len(self.generate_eval_cases(limit=50)),
        }
