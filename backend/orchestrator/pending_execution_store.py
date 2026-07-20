"""Persistence + in-memory cache for a single pending high-risk execution.

Extracted from AgentCluster (cluster G). Owns the optional on-disk file and the
in-memory pending value so the orchestrator core only coordinates. Branch-by-
branch semantics match the former AgentCluster methods exactly (no behavior
change).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from backend.executors.base import ExecutionRequest
from backend.orchestrator.execution_guard import PendingExecution


class PendingExecutionStore:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(path) if path else None
        self._pending: PendingExecution | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def pending(self) -> PendingExecution | None:
        return self._pending

    @pending.setter
    def pending(self, value: PendingExecution | None) -> None:
        self._pending = value

    def serialize(self, pending: PendingExecution) -> dict[str, object]:
        payload: dict[str, object] = {
            "request": asdict(pending.request),
            "risk_level": pending.risk_level,
            "confirmation_message": pending.confirmation_message,
            "spoken_confirmation_message": pending.spoken_confirmation_message,
            "original_user_input": pending.original_user_input,
            "created_at": time.time(),
        }
        if pending.continuation_request is not None:
            payload["continuation_request"] = asdict(pending.continuation_request)
        return payload

    def deserialize(self, payload: dict[str, object]) -> PendingExecution | None:
        request_payload = payload.get("request")
        if not isinstance(request_payload, dict):
            return None
        target = str(request_payload.get("target") or "").strip()
        operation = str(request_payload.get("operation") or "").strip()
        if not target or not operation:
            return None
        params = request_payload.get("params")
        continuation_payload = payload.get("continuation_request")
        continuation = None
        if isinstance(continuation_payload, dict):
            continuation_target = str(continuation_payload.get("target") or "").strip()
            continuation_operation = str(continuation_payload.get("operation") or "").strip()
            continuation_params = continuation_payload.get("params")
            if continuation_target and continuation_operation:
                continuation = ExecutionRequest(
                    target=continuation_target,
                    operation=continuation_operation,
                    params=dict(continuation_params or {}) if isinstance(continuation_params, dict) else {},
                )
        return PendingExecution(
            request=ExecutionRequest(target=target, operation=operation, params=dict(params or {}) if isinstance(params, dict) else {}),
            risk_level=str(payload.get("risk_level") or "high"),
            confirmation_message=str(payload.get("confirmation_message") or ""),
            spoken_confirmation_message=str(payload.get("spoken_confirmation_message") or ""),
            original_user_input=str(payload.get("original_user_input") or ""),
            continuation_request=continuation,
        )

    def save(self, pending: PendingExecution) -> None:
        self._pending = pending
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self.serialize(pending), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return

    def load(self) -> PendingExecution | None:
        if self._path is None:
            return self._pending
        if not self._path.exists():
            self._pending = None
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._pending
        if not isinstance(payload, dict):
            return None
        pending = self.deserialize(payload)
        self._pending = pending
        return pending

    def clear(self) -> None:
        self._pending = None
        if self._path is None:
            return
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            return
