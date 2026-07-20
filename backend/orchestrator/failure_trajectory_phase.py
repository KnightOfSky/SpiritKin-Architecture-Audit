from __future__ import annotations

from typing import Any

from backend.executors.base import ExecutionRequest
from backend.orchestrator.failure_log import FailureLog
from backend.orchestrator.repair import FailureRecord
from backend.orchestrator.runtime_trajectory_log import (
    append_runtime_trajectory,
    trajectory_from_execution,
    trajectory_from_failure,
    trajectory_logging_enabled,
)


class FailureTrajectoryPhase:
    def __init__(self, failure_log: FailureLog):
        self._failure_log = failure_log

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
        arguments: Any = None,
        metadata: Any = None,
    ) -> FailureRecord:
        failure = self._failure_log.record(
            stage=stage,
            actor=actor,
            message=message,
            user_input=user_input,
            error_code=error_code,
            tool_name=tool_name,
            execution_request=execution_request,
            arguments=arguments,
            metadata=metadata,
        )
        self.append_failure(
            stage=stage,
            actor=actor,
            message=message,
            user_input=user_input,
            error_code=error_code,
            tool_name=tool_name,
            execution_request=execution_request,
            arguments=arguments,
            metadata=metadata,
        )
        return failure

    @staticmethod
    def append_failure(**kwargs: Any) -> dict[str, object] | None:
        if not trajectory_logging_enabled():
            return None
        try:
            return append_runtime_trajectory(trajectory_from_failure(**kwargs))
        except Exception as exc:
            return {"trajectory_log_error": str(exc)}

    @staticmethod
    def append_execution(
        *,
        user_input: str,
        request: ExecutionRequest,
        result: Any,
        worker_execution: Any,
        actor: str,
    ) -> dict[str, object] | None:
        if not trajectory_logging_enabled():
            return None
        try:
            worker = worker_execution.worker.snapshot() if worker_execution.worker is not None else {}
            return append_runtime_trajectory(
                trajectory_from_execution(
                    user_input=user_input,
                    request=request,
                    result=result,
                    actor=actor,
                    worker_id=str(worker.get("worker_id") or ""),
                    worker_audit=worker_execution.audit_event.snapshot(),
                    metadata={"worker": worker},
                )
            )
        except Exception as exc:
            return {"trajectory_log_error": str(exc)}
