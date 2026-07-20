from __future__ import annotations

from dataclasses import dataclass

from backend.agents.base import AgentReply
from backend.executors.base import ExecutionRequest
from backend.tools.base import ToolSpec


@dataclass(frozen=True)
class PendingExecution:
    request: ExecutionRequest
    risk_level: str
    confirmation_message: str
    spoken_confirmation_message: str
    original_user_input: str
    continuation_request: ExecutionRequest | None = None


@dataclass(frozen=True)
class ConfirmationDecision:
    status: str

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"


class ExecutionGuard:
    """Centralizes execution risk, policy checks and confirmation prompts."""

    def __init__(self, *, policy_engine=None):
        self.policy_engine = policy_engine

    @staticmethod
    def resolve_risk_level(request: ExecutionRequest, available_tools: list[ToolSpec]) -> str:
        candidate_keys = _tool_match_keys_for_request(request)
        for spec in available_tools:
            if (spec.target, spec.operation) in candidate_keys:
                return spec.risk_level
        return "medium"

    def evaluate_policy(self, request: ExecutionRequest, available_tools: list[ToolSpec]):
        if self.policy_engine is None or not hasattr(self.policy_engine, "evaluate"):
            return None
        return self.policy_engine.evaluate(
            target=request.target,
            operation=request.operation,
            risk_level=self.resolve_risk_level(request, available_tools),
            actor=str(request.params.get("actor") or "agent_cluster"),
            channel=str(request.params.get("channel") or request.params.get("input_channel") or "desktop"),
        )

    def requires_confirmation(
        self,
        request: ExecutionRequest,
        available_tools: list[ToolSpec],
        *,
        skip_confirmation: bool = False,
    ) -> bool:
        if skip_confirmation:
            return False
        decision = self.evaluate_policy(request, available_tools)
        if decision is not None and getattr(decision, "require_confirmation", False):
            return True
        return self.resolve_risk_level(request, available_tools).lower() == "high"

    def build_pending_execution(
        self,
        request: ExecutionRequest,
        available_tools: list[ToolSpec],
        *,
        original_user_input: str = "",
        continuation_request: ExecutionRequest | None = None,
    ) -> PendingExecution:
        return PendingExecution(
            request=request,
            risk_level=self.resolve_risk_level(request, available_tools),
            confirmation_message=(
                f"这个操作会控制 {request.target} 执行 {request.operation}。"
                "为安全起见，请先回复“确认执行”或“取消执行”。"
            ),
            spoken_confirmation_message="这个动作需要你确认。确认就说确认执行，取消就说取消执行。",
            original_user_input=original_user_input,
            continuation_request=continuation_request,
        )

    @staticmethod
    def build_confirmation_reply(pending: PendingExecution) -> AgentReply:
        return AgentReply(
            text=pending.confirmation_message,
            emotion="confused",
            action="await_confirmation",
            agent_name="execution_guard",
            spoken_text=pending.spoken_confirmation_message,
            requires_confirmation=True,
            metadata={
                "response_kind": "confirmation_request",
                "pending_target": pending.request.target,
                "pending_operation": pending.request.operation,
                "risk_level": pending.risk_level,
                "has_continuation": pending.continuation_request is not None,
            },
        )

    @staticmethod
    def normalize_confirmation_text(user_input: str) -> str:
        return user_input.strip().lower().replace(" ", "").rstrip(".,;!?。，；！？、")

    def decide_confirmation(self, user_input: str) -> ConfirmationDecision:
        normalized = self.normalize_confirmation_text(user_input)
        confirm_tokens = {
            "确认",
            "確認",
            "确认执行",
            "確認執行",
            "继续",
            "繼續",
            "继续执行",
            "繼續執行",
            "yes",
            "y",
            "ok",
            "go",
            "好的",
            "好",
            "可以",
            "得",
            "得嘅",
            "冇问题",
            "冇問題",
            "无问题",
            "無問題",
            "行",
            "执行",
            "執行",
            "可以执行",
            "可以執行",
        }
        cancel_tokens = {
            "取消",
            "取消执行",
            "取消執行",
            "停止",
            "停",
            "唔好",
            "不要",
            "不用了",
            "唔使",
            "唔需要",
            "no",
            "n",
            "cancel",
            "stop",
            "算了",
        }
        if normalized in cancel_tokens:
            return ConfirmationDecision("cancelled")
        if normalized in confirm_tokens:
            return ConfirmationDecision("confirmed")
        return ConfirmationDecision("unknown")

    @staticmethod
    def build_cancelled_reply(pending: PendingExecution) -> AgentReply:
        return AgentReply(
            text=f"已取消 {pending.request.target}.{pending.request.operation}。",
            emotion="neutral",
            action="cancel_execution",
            agent_name="execution_guard",
            spoken_text="好的，已经取消这个动作。",
            metadata={
                "response_kind": "confirmation_cancelled",
                "cancelled_target": pending.request.target,
                "cancelled_operation": pending.request.operation,
            },
        )


def _tool_match_keys_for_request(request: ExecutionRequest) -> tuple[tuple[str, str], ...]:
    target = str(request.target or "").strip()
    operation = str(request.operation or "").strip()
    params = dict(request.params or {})
    keys = [(target, operation)]
    remote_target = str(params.get("remote_target") or "").strip()
    if remote_target:
        keys.append((remote_target, operation))
    if operation.startswith("browser_") and (target == "browser" or target.startswith("remote:") or remote_target == "browser"):
        keys.append(("local_pc", operation))
    return tuple(dict.fromkeys(keys))
