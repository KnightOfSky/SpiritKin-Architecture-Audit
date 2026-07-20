"""Structured failure log + repair advice extracted from AgentCluster (I+J).

Owns the bounded recent-failure ring and the most recent repair advice, records
failure trajectories, and builds rule-based repair plans. Holds shared
references to the repair advisor and trajectory analyzer (created and also used
by AgentCluster). Logic preserved verbatim.
"""

from __future__ import annotations

from backend.evaluation.trajectory import TrajectoryReport, TrajectoryStep
from backend.executors.base import ExecutionRequest
from backend.orchestrator.repair import (
    FailureRecord,
)
from backend.orchestrator.repair import (
    build_repair_plan as build_repair_plan_from_failure,
)


class FailureLog:
    def __init__(self, *, repair_advisor, trajectory_analyzer, limit: int) -> None:
        self._repair_advisor = repair_advisor
        self._trajectory_analyzer = trajectory_analyzer
        self._limit = max(1, int(limit))
        self._recent: list[FailureRecord] = []
        self._last_repair_advice = None

    @property
    def recent_failures(self) -> list[FailureRecord]:
        return self._recent

    @property
    def last_repair_advice(self):
        return self._last_repair_advice

    def clear(self) -> None:
        self._recent.clear()
        self._last_repair_advice = None

    def record(
        self,
        *,
        stage: str,
        actor: str,
        message: str,
        user_input: str = "",
        error_code: str = "",
        tool_name: str = "",
        execution_request: ExecutionRequest | None = None,
        arguments=None,
        metadata=None,
    ) -> FailureRecord:
        failure = FailureRecord(
            stage=stage,
            actor=actor,
            message=message,
            error_code=error_code,
            user_input=user_input,
            tool_name=tool_name,
            execution_target=execution_request.target if execution_request is not None else "",
            execution_operation=execution_request.operation if execution_request is not None else "",
            arguments=dict(arguments or {}),
            metadata=dict(metadata or {}),
        )
        self._recent.append(failure)
        if len(self._recent) > self._limit:
            self._recent = self._recent[-self._limit :]

        try:
            self._last_repair_advice = self._repair_advisor.analyze(failure)
        except Exception:
            self._last_repair_advice = None
        self._record_trajectory(failure)
        return failure

    def _record_trajectory(self, failure: FailureRecord) -> None:
        if self._trajectory_analyzer is None:
            return
        step = TrajectoryStep(
            stage=failure.stage,
            detail=failure.message,
            success=False,
            error_code=failure.error_code,
            metadata={"actor": failure.actor, "tool_name": failure.tool_name},
        )
        self._trajectory_analyzer.record_trajectory(
            TrajectoryReport(user_input=failure.user_input, steps=[step], overall_success=False, bottleneck_stage=failure.stage)
        )

    def build_repair_plan(self, failure: FailureRecord | None = None):
        target_failure = failure or (self._recent[-1] if self._recent else None)
        if target_failure is None:
            return None

        advice = self._last_repair_advice if self._recent and target_failure is self._recent[-1] else None
        if advice is None:
            try:
                advice = self._repair_advisor.analyze(target_failure)
            except Exception:
                advice = None
        return build_repair_plan_from_failure(target_failure, advice)
