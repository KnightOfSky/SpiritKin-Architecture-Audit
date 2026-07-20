from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.agents.base import AgentReply
from backend.orchestrator.execution_guard import ExecutionGuard, PendingExecution
from backend.orchestrator.execution_replies import (
    build_confirmation_mismatch_reply,
    build_duplicate_confirmation_reply,
    build_no_pending_confirmation_reply,
)


@dataclass(frozen=True)
class ConfirmationPhaseServices:
    execution_guard: ExecutionGuard
    load_pending: Callable[[], PendingExecution | None]
    clear_pending: Callable[[], None]
    execute: Callable[..., AgentReply]
    active_metadata: Callable[[], dict[str, object]]


class ConfirmationPhase:
    def __init__(self, services: ConfirmationPhaseServices):
        self._services = services

    def handle(self, user_input: str) -> AgentReply:
        pending = self._services.load_pending()
        if pending is None:
            return build_no_pending_confirmation_reply()
        decision = self._services.execution_guard.decide_confirmation(user_input)
        if decision.confirmed:
            return self._confirm(pending)
        if decision.cancelled:
            self._services.clear_pending()
            return self._services.execution_guard.build_cancelled_reply(pending)
        return self._services.execution_guard.build_confirmation_reply(pending)

    def _confirm(self, pending: PendingExecution) -> AgentReply:
        context = self._services.active_metadata() or {}
        if context.get("confirmation_control") is True:
            expected_target = str(context.get("pending_target") or "").strip()
            expected_operation = str(context.get("pending_operation") or "").strip()
            if (
                expected_target
                and expected_operation
                and (expected_target != pending.request.target or expected_operation != pending.request.operation)
            ):
                return build_confirmation_mismatch_reply(
                    pending,
                    received_target=expected_target,
                    received_operation=expected_operation,
                )
        self._services.clear_pending()
        reply = self._services.execute(
            pending.request,
            user_input=pending.original_user_input,
            skip_confirmation=True,
        )
        if context.get("confirmation_control") is True and reply.requires_confirmation:
            self._services.clear_pending()
            return build_duplicate_confirmation_reply(pending)
        execution = reply.metadata.get("execution") if isinstance(reply.metadata, dict) else None
        repair_succeeded = isinstance(execution, dict) and execution.get("success") is True
        if not repair_succeeded or pending.continuation_request is None:
            return reply
        continued = self._services.execute(
            pending.continuation_request,
            user_input=pending.original_user_input,
            skip_confirmation=True,
        )
        if context.get("confirmation_control") is True and continued.requires_confirmation:
            self._services.clear_pending()
            return build_duplicate_confirmation_reply(pending)
        continued.metadata = {
            **dict(continued.metadata or {}),
            "repair_execution": execution,
        }
        return continued
