from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from backend.executors.base import ExecutionRequest, ExecutionResult
from backend.orchestrator.failure_classifier import classify_failure
from backend.prompts.execution import RETRY_PROMPT

RETRY_SCHEMA_VERSION = "spiritkin.execution_retry.v1"
DEFAULT_RETRY_ATTEMPTS = 1
MAX_RETRY_ATTEMPTS_CEILING = 3
_STDERR_TRUNCATE = 2000

# error_code 值域里明显"换个执行器/环境缺失"类,重试改参数无意义,直接放弃。
_NON_RETRYABLE_CODES = {
    "worker_not_found",
    "executor_not_found",
    "policy_denied",
    "worker_blocked",
    "safety_denied",
}


@dataclass(frozen=True)
class RetryPlan:
    action: str  # "retry" | "abort"
    params: dict[str, Any]
    reason: str = ""
    repair_tool_name: str = ""
    repair_arguments: dict[str, Any] | None = None

    @property
    def should_retry(self) -> bool:
        return self.action == "retry"

    @property
    def has_repair(self) -> bool:
        return bool(self.repair_tool_name)


def retry_attempt_budget() -> int:
    """允许的最大重试次数(不含首次执行)。0 表示关闭。"""
    raw = os.getenv("SPIRITKIN_EXECUTION_RETRY_ATTEMPTS", "").strip()
    if not raw:
        return DEFAULT_RETRY_ATTEMPTS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_ATTEMPTS
    return max(0, min(MAX_RETRY_ATTEMPTS_CEILING, value))


def error_is_retryable(result: ExecutionResult) -> bool:
    """失败结果是否值得让模型改参数重试。"""
    if result.success:
        return False
    code = str(result.error_code or "").strip().lower()
    if code in _NON_RETRYABLE_CODES:
        return False
    return classify_failure(result).kind != "fatal"


def retry_backoff_seconds(attempt: int) -> float:
    try:
        configured = float(os.getenv("SPIRITKIN_EXECUTION_RETRY_BACKOFF_SECONDS", "0.25"))
    except ValueError:
        configured = 0.25
    base = max(0.0, min(10.0, configured))
    return min(10.0, base * (2 ** max(0, int(attempt) - 1)))


def extract_stderr(result: ExecutionResult) -> str:
    """从 ExecutionResult.data / metadata 里捞真实报错文本(stderr 优先)。"""
    candidates: list[Any] = []
    data = result.data
    if isinstance(data, dict):
        for key in ("stderr", "error", "output", "stdout", "detail"):
            if data.get(key):
                candidates.append(data.get(key))
    elif data:
        candidates.append(data)
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    for key in ("stderr", "error", "detail"):
        if metadata.get(key):
            candidates.append(metadata.get(key))
    text = "\n".join(str(item).strip() for item in candidates if str(item).strip())
    if len(text) > _STDERR_TRUNCATE:
        text = text[:_STDERR_TRUNCATE] + "…(截断)"
    return text


def attach_failure_context(result: ExecutionResult, request: ExecutionRequest) -> dict[str, Any]:
    failure = classify_failure(result)
    existing = result.metadata.get("failure_context") if isinstance(result.metadata, dict) else None
    context = {
        **(dict(existing) if isinstance(existing, dict) else {}),
        "kind": failure.kind,
        "reason": failure.reason,
        "stderr_tail": failure.stderr_tail,
        "exit_code": failure.exit_code,
        "error_code": failure.error_code,
        "target": request.target,
        "operation": request.operation,
        "param_keys": sorted(str(key) for key in (request.params or {})),
    }
    data = dict(result.data) if isinstance(result.data, dict) else {"result": result.data}
    data["failure_context"] = context
    result.data = data
    result.metadata = {**dict(result.metadata or {}), "failure_context": context}
    return context


def build_retry_prompt(
    *,
    request: ExecutionRequest,
    result: ExecutionResult,
    attempt: int,
    max_attempts: int,
    user_input: str = "",
) -> str:
    stderr = extract_stderr(result) or "(无 stderr 输出)"
    try:
        params_json = json.dumps(request.params or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        params_json = str(request.params or {})
    return RETRY_PROMPT.substitute(
        target=request.target,
        operation=request.operation,
        params_json=params_json,
        message=result.message,
        error_code=result.error_code or "(空)",
        stderr=stderr,
        attempt=attempt,
        max_attempts=max_attempts,
        user_input=user_input or "(未提供)",
    )


def parse_retry_response(raw: str) -> RetryPlan | None:
    """解析模型返回的重试计划;无法解析返回 None。"""
    data = _extract_json_object(raw)
    if data is None:
        return None
    action = str(data.get("action") or "").strip().lower()
    if action not in {"retry", "abort"}:
        # 容错:给了 params 但没写 action，视为 retry。
        action = "retry" if isinstance(data.get("params"), dict) else "abort"
    params = data.get("params")
    params = dict(params) if isinstance(params, dict) else {}
    reason = str(data.get("reason") or "").strip()
    repair = data.get("repair_tool") if isinstance(data.get("repair_tool"), dict) else {}
    repair_tool_name = str(repair.get("name") or "").strip()
    repair_arguments = repair.get("arguments") if isinstance(repair.get("arguments"), dict) else {}
    return RetryPlan(
        action=action,
        params=params,
        reason=reason,
        repair_tool_name=repair_tool_name,
        repair_arguments=dict(repair_arguments),
    )


def plan_next_request(
    *,
    request: ExecutionRequest,
    plan: RetryPlan,
) -> ExecutionRequest | None:
    """把重试计划落成新的 ExecutionRequest;无有效改动返回 None。"""
    if not plan.should_retry:
        return None
    if not plan.params:
        return None
    if plan.params == dict(request.params or {}):
        # 参数没变，重试没意义。
        return None
    return ExecutionRequest(target=request.target, operation=request.operation, params=dict(plan.params))


def repair_plan_is_supported(plan: RetryPlan, result: ExecutionResult) -> bool:
    if plan.repair_tool_name != "python.install_package":
        return False
    package = str((plan.repair_arguments or {}).get("package") or "").strip()
    if not package:
        return False
    failure_text = f"{result.message}\n{extract_stderr(result)}".lower()
    return "modulenotfounderror" in failure_text or "no module named" in failure_text


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    cleaned = str(raw).strip()
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None
