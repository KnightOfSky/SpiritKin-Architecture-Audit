from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentContext, AgentReply
from backend.executors.base import ExecutionRequest
from backend.tools.base import ToolCall


@dataclass(frozen=True)
class ExecutionValidationServices:
    tool_registry: Any
    execute: Callable[..., AgentReply]


class ExecutionValidationPhase:
    def __init__(self, services: ExecutionValidationServices):
        self._services = services

    def run(self, request: ExecutionRequest, context: AgentContext) -> AgentReply:
        matched = self._services.tool_registry.get(f"executor.{request.target}.{request.operation}")
        if matched is None:
            for spec in self._services.tool_registry.list_specs():
                if spec.target == request.target and spec.operation == request.operation:
                    matched = self._services.tool_registry.get(spec.name)
                    break
        if matched is None:
            return self._services.execute(request, user_input=context.user_input)

        tracked_result = self._services.tool_registry.invoke(
            ToolCall(name=matched.spec.name, arguments=request.params)
        )
        if not tracked_result.success:
            return AgentReply(
                text=f"工具验证失败: {tracked_result.message}",
                emotion="confused",
                action="tilt_head",
                agent_name="tool_gate",
                metadata={"tool_error": tracked_result.error_code},
            )
        reply = self._services.execute(request, user_input=context.user_input)
        reply.metadata["tool_validated"] = True
        reply.metadata["tool_name"] = matched.spec.name
        return reply
