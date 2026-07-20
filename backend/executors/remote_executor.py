from __future__ import annotations

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.executors.node_registry import NodeRegistry
from backend.executors.remote_protocol import RemoteExecutionPayload


class RemoteExecutor(BaseExecutor):
    name = "remote"

    def __init__(self, node_registry: NodeRegistry):
        self._node_registry = node_registry

    def supports(self, request: ExecutionRequest) -> bool:
        return self._node_registry.find_for_request(request) is not None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        node = self._node_registry.find_for_request(request)
        if node is None:
            return ExecutionResult(
                success=False,
                message=f"未找到可用远端节点: {request.target}",
                error_code="remote_node_not_found",
                metadata={"target": request.target},
            )

        payload = RemoteExecutionPayload(
            node_id=node.node_id,
            target=self._node_registry.resolve_payload_target(request, node),
            operation=request.operation,
            params=dict(request.params or {}),
        )

        try:
            response = node.client.execute(payload)
        except Exception as exc:
            return ExecutionResult(success=False, message=f"远端执行异常: {exc}", error_code="remote_execution_exception")

        message = response.message or f"远端执行完成: {node.node_id}.{request.operation}"
        return ExecutionResult(
            success=response.success,
            message=message,
            data=response.data,
            error_code="" if response.success else (response.error_code or "remote_execution_failed"),
            metadata={"node_id": node.node_id, "remote_target": payload.target, **dict(response.metadata or {})},
        )