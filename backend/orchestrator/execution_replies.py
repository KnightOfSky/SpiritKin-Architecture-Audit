"""Pure AgentReply builders extracted from agent_cluster's execution pipeline.

The cluster runs the stateful loop (worker pool, retry, trajectory) and passes
results in; nothing here reads cluster internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.agents.base import AgentReply

if TYPE_CHECKING:
    from backend.executors.base import ExecutionRequest
    from backend.orchestrator.execution_guard import PendingExecution


def policy_decision_snapshot(policy_decision: Any) -> dict[str, object]:
    return {
        "allowed": policy_decision.allowed,
        "require_confirmation": policy_decision.require_confirmation,
        "reason": policy_decision.reason,
        "matched_rule_id": policy_decision.matched_rule_id,
    }


def worker_display_name(worker_execution: Any, fallback: str) -> str:
    worker = getattr(worker_execution, "worker", None)
    return worker.label if worker is not None else fallback


def build_executor_missing_reply(request: ExecutionRequest) -> AgentReply:
    return AgentReply(
        text=f"当前没有可用执行器来处理 {request.target}.{request.operation}。",
        emotion="confused",
        action="tilt_head",
        agent_name="executor_missing",
    )


def build_policy_denied_reply(
    request: ExecutionRequest,
    policy_decision: Any,
    trajectory_record: dict[str, object] | None = None,
) -> AgentReply:
    return AgentReply(
        text=f"安全策略已拦截该操作：{policy_decision.reason}",
        emotion="confused",
        action="tilt_head",
        agent_name="policy_guard",
        metadata={
            "response_kind": "policy_denied",
            "policy_decision": policy_decision_snapshot(policy_decision),
            "execution": {"target": request.target, "operation": request.operation, "success": False, "error_code": "policy_denied"},
            **({"trajectory_record": trajectory_record} if trajectory_record else {}),
        },
    )


def build_execution_failure_reply(
    request: ExecutionRequest,
    result: Any,
    *,
    worker_execution: Any,
    executor_name: str,
    workflow_record: Any,
    retry_trace: list[dict[str, object]] | None = None,
) -> AgentReply:
    worker = getattr(worker_execution, "worker", None)
    return AgentReply(
        text=f"执行失败：{result.message}",
        emotion="confused",
        action="tilt_head",
        agent_name=f"executor_{worker_display_name(worker_execution, executor_name)}",
        metadata={
            "response_kind": "execution_result",
            "execution": {
                "target": request.target,
                "operation": request.operation,
                "success": False,
                "error": result.message,
                "error_code": result.error_code,
                "data": result.data,
                "metadata": result.metadata,
                "worker": worker.snapshot() if worker is not None else None,
                "worker_audit": worker_execution.audit_event.snapshot(),
            },
            "workflow_record": workflow_record.snapshot(),
            **({"retry_trace": retry_trace} if retry_trace else {}),
        },
    )


def build_execution_success_reply(
    request: ExecutionRequest,
    result: Any,
    *,
    worker_execution: Any,
    executor_name: str,
    workflow_record: Any,
    trajectory_record: dict[str, object] | None = None,
    inventory_update: dict[str, object] | None = None,
    retry_trace: list[dict[str, object]] | None = None,
) -> AgentReply:
    worker = getattr(worker_execution, "worker", None)
    return AgentReply(
        text=result.message,
        emotion="happy",
        action="execute_task",
        agent_name=f"executor_{worker_display_name(worker_execution, executor_name)}",
        metadata={
            "response_kind": "execution_result",
            "execution": {
                "target": request.target,
                "operation": request.operation,
                "success": True,
                "data": result.data,
                "metadata": result.metadata,
                "worker": worker.snapshot() if worker is not None else None,
                "worker_audit": worker_execution.audit_event.snapshot(),
            },
            "workflow_record": workflow_record.snapshot(),
            **({"trajectory_record": trajectory_record} if trajectory_record else {}),
            **({"inventory_update": inventory_update} if inventory_update else {}),
            **({"retry_trace": retry_trace} if retry_trace else {}),
        },
    )


def build_no_pending_confirmation_reply() -> AgentReply:
    return AgentReply(
        text="当前没有等待确认的操作。",
        emotion="neutral",
        action="idle",
        agent_name="execution_guard",
        metadata={"response_kind": "message"},
    )


def build_confirmation_mismatch_reply(
    pending: PendingExecution,
    *,
    received_target: str,
    received_operation: str,
) -> AgentReply:
    return AgentReply(
        text="当前确认弹框和待执行操作不一致，已拒绝这次确认。请刷新待确认状态后再操作。",
        emotion="confused",
        action="await_confirmation",
        agent_name="execution_guard",
        metadata={
            "response_kind": "confirmation_mismatch",
            "pending_target": pending.request.target,
            "pending_operation": pending.request.operation,
            "received_target": received_target,
            "received_operation": received_operation,
        },
    )


def build_duplicate_confirmation_reply(pending: PendingExecution) -> AgentReply:
    return AgentReply(
        text="确认已经收到，但执行链仍返回了二次确认请求。已停止执行以避免重复确认。",
        emotion="confused",
        action="shake",
        agent_name="execution_guard",
        metadata={
            "response_kind": "confirmation_failed",
            "execution": {
                "target": pending.request.target,
                "operation": pending.request.operation,
                "success": False,
                "error_code": "duplicate_confirmation_request",
            },
        },
    )
