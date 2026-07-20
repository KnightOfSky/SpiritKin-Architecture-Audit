from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentReply
from backend.executors.base import ExecutionRequest
from backend.orchestrator.execution_replies import (
    build_execution_failure_reply,
    build_execution_success_reply,
    build_executor_missing_reply,
    build_policy_denied_reply,
    policy_decision_snapshot,
)
from backend.orchestrator.execution_retry import (
    attach_failure_context,
    build_retry_prompt,
    error_is_retryable,
    parse_retry_response,
    plan_next_request,
    repair_plan_is_supported,
    retry_attempt_budget,
    retry_backoff_seconds,
)
from backend.orchestrator.failure_classifier import classify_failure
from backend.orchestrator.self_heal_log import append_self_heal_event


@dataclass(frozen=True)
class ExecutionPhaseServices:
    find_executor: Callable[[ExecutionRequest], Any | None]
    record_failure: Callable[..., None]
    evaluate_policy: Callable[[ExecutionRequest], Any | None]
    append_failure_trajectory: Callable[..., dict[str, object] | None]
    requires_confirmation: Callable[..., bool]
    has_full_access: Callable[[], bool]
    available_tools: Callable[[], list[Any]]
    execution_guard: Any
    save_pending: Callable[[Any], None]
    build_confirmation_reply: Callable[[Any], AgentReply]
    worker_pool: Any
    active_input_metadata: Callable[[], dict[str, object]]
    workflow_memory: Any
    llm_call: Callable[..., str]
    remember_inventory: Callable[..., dict[str, object] | None]
    append_execution_trajectory: Callable[..., dict[str, object] | None]
    invoke_tool: Callable[[str, dict[str, Any]], Any]


class ExecutionPhase:
    """Own governed execution, retry, and result assembly."""

    def __init__(self, services: ExecutionPhaseServices) -> None:
        self._services = services

    def execute(
        self,
        request: ExecutionRequest,
        *,
        user_input: str = "",
        skip_confirmation: bool = False,
    ) -> AgentReply:
        services = self._services
        executor = services.find_executor(request)
        if executor is None:
            services.record_failure(
                stage="executor",
                actor="executor_router",
                message=f"当前没有可用执行器来处理 {request.target}.{request.operation}。",
                user_input=user_input,
                error_code="executor_not_found",
                execution_request=request,
                metadata={"reason": "missing_executor"},
            )
            return build_executor_missing_reply(request)

        policy_decision = services.evaluate_policy(request)
        if policy_decision is not None and not getattr(policy_decision, "allowed", True):
            trajectory_record = services.append_failure_trajectory(
                stage="policy",
                actor="policy_guard",
                message=f"安全策略已拦截该操作：{policy_decision.reason}",
                user_input=user_input,
                error_code="policy_denied",
                execution_request=request,
                metadata={"policy_decision": policy_decision_snapshot(policy_decision)},
            )
            return build_policy_denied_reply(request, policy_decision, trajectory_record)

        if services.requires_confirmation(
            request,
            skip_confirmation=skip_confirmation or services.has_full_access(),
        ):
            pending = services.execution_guard.build_pending_execution(
                request=request,
                available_tools=services.available_tools(),
                original_user_input=user_input,
            )
            services.save_pending(pending)
            return services.build_confirmation_reply(pending)

        actor = str(services.active_input_metadata().get("actor") or "")
        max_attempts = retry_attempt_budget()
        retry_trace: list[dict[str, object]] = []
        repair_executions: list[dict[str, Any]] = []
        attempt = 0
        while True:
            worker_execution = services.worker_pool.execute(request, actor=actor, metadata={"user_input": user_input})
            result = worker_execution.result
            if not result.success:
                attach_failure_context(result, request)
            workflow_record = services.workflow_memory.record_execution(
                user_input=user_input,
                request=request,
                result=result,
            )
            if result.success:
                break
            services.record_failure(
                stage="executor",
                actor=worker_execution.worker.label if worker_execution.worker is not None else executor.name,
                message=result.message,
                user_input=user_input,
                error_code=result.error_code or "executor_failed",
                execution_request=request,
                metadata={**dict(result.metadata or {}), "worker_audit": worker_execution.audit_event.snapshot()},
            )
            if attempt >= max_attempts or not error_is_retryable(result):
                break
            attempt += 1
            failure = classify_failure(result)
            if failure.kind == "transient":
                backoff = retry_backoff_seconds(attempt)
                retry_trace.append(
                    {
                        "attempt": attempt,
                        "status": "retry",
                        "kind": failure.kind,
                        "reason": failure.reason,
                        "backoff_seconds": backoff,
                    }
                )
                append_self_heal_event(
                    {
                        "action": "retry_scheduled",
                        "attempt": attempt,
                        "target": request.target,
                        "operation": request.operation,
                        "failure": failure.snapshot(),
                        "backoff_seconds": backoff,
                    }
                )
                if backoff:
                    time.sleep(backoff)
                continue
            retry_prompt = build_retry_prompt(
                request=request,
                result=result,
                attempt=attempt,
                max_attempts=max_attempts,
                user_input=user_input,
            )
            try:
                raw_retry = services.llm_call(retry_prompt, agent_name="execution_retry")
            except Exception as exc:
                retry_trace.append(
                    {"attempt": attempt, "status": "llm_error", "detail": f"{type(exc).__name__}: {exc}"}
                )
                break
            retry_plan = parse_retry_response(str(raw_retry or ""))
            if retry_plan is None:
                retry_trace.append({"attempt": attempt, "status": "unparseable"})
                break
            if not retry_plan.should_retry:
                retry_trace.append({"attempt": attempt, "status": "abort", "reason": retry_plan.reason})
                break
            next_request = plan_next_request(request=request, plan=retry_plan)
            if retry_plan.has_repair:
                if not repair_plan_is_supported(retry_plan, result):
                    retry_trace.append(
                        {
                            "attempt": attempt,
                            "status": "repair_rejected",
                            "tool_name": retry_plan.repair_tool_name,
                            "reason": "unsupported_repair_or_missing_failure_evidence",
                        }
                    )
                    break
                tool_result = services.invoke_tool(
                    retry_plan.repair_tool_name,
                    dict(retry_plan.repair_arguments or {}),
                )
                repair_request = getattr(tool_result, "execution_request", None)
                if not getattr(tool_result, "success", False) or repair_request is None:
                    retry_trace.append(
                        {
                            "attempt": attempt,
                            "status": "repair_rejected",
                            "tool_name": retry_plan.repair_tool_name,
                            "reason": str(getattr(tool_result, "error_code", "") or "repair_tool_rejected"),
                        }
                    )
                    break
                continuation_request = next_request or request
                repair_policy = services.evaluate_policy(repair_request)
                if repair_policy is not None and not getattr(repair_policy, "allowed", True):
                    retry_trace.append(
                        {
                            "attempt": attempt,
                            "status": "repair_policy_denied",
                            "tool_name": retry_plan.repair_tool_name,
                            "reason": str(getattr(repair_policy, "reason", "") or "policy_denied"),
                        }
                    )
                    break
                full_access = services.has_full_access()
                if services.requires_confirmation(repair_request, skip_confirmation=full_access):
                    pending = services.execution_guard.build_pending_execution(
                        request=repair_request,
                        available_tools=services.available_tools(),
                        original_user_input=user_input,
                        continuation_request=continuation_request,
                    )
                    services.save_pending(pending)
                    retry_trace.append(
                        {
                            "attempt": attempt,
                            "status": "repair_confirmation_required",
                            "tool_name": retry_plan.repair_tool_name,
                        }
                    )
                    confirmation = services.build_confirmation_reply(pending)
                    confirmation.metadata = {
                        **dict(confirmation.metadata or {}),
                        "repair_for": {
                            "target": continuation_request.target,
                            "operation": continuation_request.operation,
                        },
                        "retry_trace": retry_trace,
                    }
                    return confirmation
                repair_reply = self.execute(
                    repair_request,
                    user_input=user_input,
                    skip_confirmation=full_access,
                )
                repair_execution = (
                    repair_reply.metadata.get("execution")
                    if isinstance(repair_reply.metadata, dict)
                    else None
                )
                if not isinstance(repair_execution, dict) or repair_execution.get("success") is not True:
                    retry_trace.append(
                        {
                            "attempt": attempt,
                            "status": "repair_failed",
                            "tool_name": retry_plan.repair_tool_name,
                        }
                    )
                    break
                retry_trace.append(
                    {
                        "attempt": attempt,
                        "status": "repair_succeeded",
                        "tool_name": retry_plan.repair_tool_name,
                    }
                )
                append_self_heal_event(
                    {
                        "action": "repair_succeeded",
                        "attempt": attempt,
                        "target": continuation_request.target,
                        "operation": continuation_request.operation,
                        "repair_tool": retry_plan.repair_tool_name,
                    }
                )
                repair_executions.append(dict(repair_execution))
                request = continuation_request
                continue
            if next_request is None:
                retry_trace.append({"attempt": attempt, "status": "no_param_change", "reason": retry_plan.reason})
                break
            retry_trace.append(
                {
                    "attempt": attempt,
                    "status": "retry",
                    "reason": retry_plan.reason,
                    "params": dict(next_request.params),
                }
            )
            append_self_heal_event(
                {
                    "action": "request_repaired",
                    "attempt": attempt,
                    "target": request.target,
                    "operation": request.operation,
                    "failure": failure.snapshot(),
                    "reason": retry_plan.reason,
                    "param_keys": sorted(str(key) for key in next_request.params),
                }
            )
            request = next_request

        if not result.success:
            return build_execution_failure_reply(
                request,
                result,
                worker_execution=worker_execution,
                executor_name=executor.name,
                workflow_record=workflow_record,
                retry_trace=retry_trace,
            )

        inventory_update = services.remember_inventory(request, result.data, metadata=result.metadata)
        trajectory_record = services.append_execution_trajectory(
            user_input=user_input,
            request=request,
            result=result,
            worker_execution=worker_execution,
            actor=worker_execution.worker.label if worker_execution.worker is not None else executor.name,
        )
        reply = build_execution_success_reply(
            request,
            result,
            worker_execution=worker_execution,
            executor_name=executor.name,
            workflow_record=workflow_record,
            trajectory_record=trajectory_record,
            inventory_update=inventory_update,
            retry_trace=retry_trace,
        )
        if repair_executions:
            reply.metadata = {
                **dict(reply.metadata or {}),
                "repair_execution": repair_executions[-1],
                "repair_executions": repair_executions,
            }
        return reply
