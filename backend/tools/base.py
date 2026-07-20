from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from backend.executors.base import ExecutionRequest


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    target: str
    operation: str
    risk_level: str = "low"
    read_only: bool = False
    schema: dict[str, Any] = field(default_factory=dict)
    authz_risk: str = ""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    success: bool
    message: str = ""
    data: Any = None
    execution_request: ExecutionRequest | None = None
    error_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    spec: ToolSpec

    def supports(self, call: ToolCall) -> bool:
        return call.name == self.spec.name

    @abstractmethod
    def invoke(self, call: ToolCall) -> ToolResult:
        raise NotImplementedError


class ExecutionTool(BaseTool):
    def __init__(self, spec: ToolSpec):
        self.spec = spec

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return ToolResult(
                success=False,
                message=f"不支持的工具: {call.name}",
                error_code="tool_not_supported",
                metadata={"tool_name": call.name},
            )

        return ToolResult(
            success=True,
            message=f"已生成执行请求: {self.spec.name}",
            execution_request=_execution_request_for_tool(self.spec, dict(call.arguments or {})),
        )


def _execution_request_for_tool(spec: ToolSpec, arguments: dict[str, Any]) -> ExecutionRequest:
    params = dict(arguments or {})
    binding = params.get("worker_binding") if isinstance(params.get("worker_binding"), dict) else {}
    target = spec.target
    if binding and _is_browser_tool(spec):
        binding_type = str(binding.get("binding_type") or "").strip()
        execution_target = str(binding.get("execution_target") or "").strip()
        if binding_type == "remote_browser":
            remote_node_id = str(binding.get("remote_node_id") or "").strip()
            if not remote_node_id and execution_target.startswith("remote:"):
                remote_node_id = execution_target.split(":", 1)[1].strip()
            if execution_target.startswith("remote:"):
                target = execution_target
            elif remote_node_id:
                target = f"remote:{remote_node_id}"
            elif execution_target:
                target = execution_target
            if remote_node_id:
                params.setdefault("node_id", remote_node_id)
            params.setdefault("remote_target", "browser")
        elif binding_type == "browser":
            target = execution_target or "browser"
    return ExecutionRequest(target=target, operation=spec.operation, params=params)


def _is_browser_tool(spec: ToolSpec) -> bool:
    return spec.name.startswith("browser.") or spec.target == "browser" or spec.operation.startswith("browser_")
