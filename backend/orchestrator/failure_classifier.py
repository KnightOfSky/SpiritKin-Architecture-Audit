from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.executors.base import ExecutionResult

FAILURE_KINDS = {"transient", "fixable", "fatal"}


@dataclass(frozen=True)
class FailureClassification:
    kind: str
    reason: str
    error_code: str
    stderr_tail: str
    exit_code: int | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "error_code": self.error_code,
            "stderr_tail": self.stderr_tail,
            "exit_code": self.exit_code,
        }


def _failure_text(result: ExecutionResult) -> tuple[str, str, int | None]:
    data = result.data if isinstance(result.data, dict) else {}
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    stderr = str(data.get("stderr") or metadata.get("stderr") or data.get("error") or metadata.get("error") or "")[-2048:]
    exit_code = data.get("returncode") if isinstance(data.get("returncode"), int) else metadata.get("returncode")
    exit_code = exit_code if isinstance(exit_code, int) else None
    text = " ".join((str(result.error_code or ""), str(result.message or ""), stderr)).lower()
    return text, stderr, exit_code


def classify_failure(result: ExecutionResult) -> FailureClassification:
    if result.success:
        return FailureClassification("fatal", "result_already_successful", str(result.error_code or ""), "")
    text, stderr, exit_code = _failure_text(result)
    error_code = str(result.error_code or "")
    if any(token in text for token in ("safety", "kill switch", "policy_denied", "permission denied", "unauthorized", "forbidden")):
        return FailureClassification("fatal", "security_or_policy_denial", error_code, stderr, exit_code)
    if any(token in text for token in ("timeout", "timed out", "connection reset", "connection refused", "temporarily unavailable", "rate limit", "http 429", "http 502", "http 503", "http 504")):
        return FailureClassification("transient", "temporary_transport_or_capacity_failure", error_code, stderr, exit_code)
    if any(token in text for token in ("not found", "no such file", "missing", "modulenotfounderror", "syntaxerror", "invalid path", "out_of_bounds", "out of bounds", "bad flag", "unknown option")):
        return FailureClassification("fixable", "request_or_environment_can_be_corrected", error_code, stderr, exit_code)
    return FailureClassification("fixable", "unclassified_execution_failure", error_code, stderr, exit_code)
