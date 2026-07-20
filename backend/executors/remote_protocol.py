from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib import error
from urllib import request as urllib_request

from backend.executors.base import BaseExecutor, ExecutionRequest


@dataclass
class RemoteExecutionPayload:
    node_id: str
    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    target: str = "remote"


@dataclass
class RemoteExecutionResponse:
    success: bool
    message: str = ""
    data: Any = None
    error_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteNodeHeartbeat:
    node_id: str
    targets: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    auth_token_id: str = ""
    timestamp: float = field(default_factory=time.time)


def remote_execution_payload_to_dict(payload: RemoteExecutionPayload) -> dict[str, Any]:
    return {
        "node_id": payload.node_id,
        "operation": payload.operation,
        "params": dict(payload.params or {}),
        "target": payload.target,
    }


def remote_execution_response_to_dict(response: RemoteExecutionResponse) -> dict[str, Any]:
    return {
        "success": response.success,
        "message": response.message,
        "data": response.data,
        "error_code": response.error_code,
        "metadata": dict(response.metadata or {}),
    }


def remote_execution_response_from_dict(payload: dict[str, Any]) -> RemoteExecutionResponse:
    return RemoteExecutionResponse(
        success=bool(payload.get("success")),
        message=str(payload.get("message") or ""),
        data=payload.get("data"),
        error_code=str(payload.get("error_code") or ""),
        metadata=dict(payload.get("metadata") or {}),
    )


def remote_node_heartbeat_to_dict(heartbeat: RemoteNodeHeartbeat) -> dict[str, Any]:
    return {
        "node_id": heartbeat.node_id,
        "targets": sorted(heartbeat.targets),
        "aliases": sorted(heartbeat.aliases),
        "capabilities": sorted(heartbeat.capabilities),
        "metadata": dict(heartbeat.metadata or {}),
        "auth_token_id": heartbeat.auth_token_id,
        "timestamp": heartbeat.timestamp,
    }


def remote_node_heartbeat_from_dict(payload: dict[str, Any]) -> RemoteNodeHeartbeat:
    return RemoteNodeHeartbeat(
        node_id=str(payload.get("node_id") or ""),
        targets=set(payload.get("targets") or []),
        aliases=set(payload.get("aliases") or []),
        capabilities=set(payload.get("capabilities") or []),
        metadata=dict(payload.get("metadata") or {}),
        auth_token_id=str(payload.get("auth_token_id") or ""),
        timestamp=float(payload.get("timestamp") or time.time()),
    )


class HttpRemoteNodeClient:
    """标准库 HTTP 版远端节点客户端，对接 backend.remote.worker。"""

    AUTH_HEADER = "X-SpiritKin-Remote-Token"

    def __init__(self, base_url: str, *, auth_token: str = "", timeout_seconds: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token.strip()
        self.timeout_seconds = max(0.5, float(timeout_seconds))

    def _request_json(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(f"{self.base_url}{path}", data=data, method=method.upper())
        req.add_header("Content-Type", "application/json; charset=utf-8")
        if self.auth_token:
            req.add_header(self.AUTH_HEADER, self.auth_token)
        try:
            with urllib_request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read().decode("utf-8") or "{}"
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc.fp is not None else "{}"
            try:
                payload = json.loads(body or "{}")
            except Exception:
                payload = {"ok": False, "error": body or str(exc)}
            raise RuntimeError(str(payload.get("error") or exc)) from exc
        return json.loads(body or "{}")

    def execute(self, payload: RemoteExecutionPayload) -> RemoteExecutionResponse:
        data = self._request_json("/execute", method="POST", payload=remote_execution_payload_to_dict(payload))
        response_payload = data.get("response") if isinstance(data, dict) else {}
        return remote_execution_response_from_dict(dict(response_payload or {}))

    def heartbeat(self, node_id: str, *, aliases: set[str] | None = None, metadata: dict[str, Any] | None = None) -> RemoteNodeHeartbeat:
        data = self._request_json("/heartbeat")
        heartbeat_payload = data.get("heartbeat") if isinstance(data, dict) else {}
        heartbeat = remote_node_heartbeat_from_dict(dict(heartbeat_payload or {}))
        if aliases:
            heartbeat.aliases.update(set(aliases))
        if metadata:
            heartbeat.metadata.update(dict(metadata))
        if node_id and heartbeat.node_id and heartbeat.node_id != node_id:
            raise RuntimeError(f"remote node id mismatch: expected {node_id}, got {heartbeat.node_id}")
        return heartbeat


class ExecutorRemoteNodeClient:
    """把本地 executor 列表包装成远端节点客户端，便于软件态联调。"""

    def __init__(self, executors: list[BaseExecutor]):
        self._executors = list(executors or [])

    def _find_executor(self, request: ExecutionRequest) -> BaseExecutor | None:
        return next((executor for executor in self._executors if executor.supports(request)), None)

    def execute(self, payload: RemoteExecutionPayload) -> RemoteExecutionResponse:
        request = ExecutionRequest(
            target=payload.target,
            operation=payload.operation,
            params=dict(payload.params or {}),
        )
        executor = self._find_executor(request)
        if executor is None:
            return RemoteExecutionResponse(
                success=False,
                message=f"远端节点未找到可用执行器: {payload.target}.{payload.operation}",
                error_code="remote_executor_not_found",
                metadata={"target": payload.target, "operation": payload.operation},
            )

        result = executor.execute(request)
        metadata = dict(result.metadata or {})
        metadata.setdefault("executor", executor.name)
        return RemoteExecutionResponse(
            success=result.success,
            message=result.message,
            data=result.data,
            error_code=result.error_code,
            metadata=metadata,
        )

    def heartbeat(self, node_id: str, *, aliases: set[str] | None = None, metadata: dict[str, Any] | None = None) -> RemoteNodeHeartbeat:
        targets: set[str] = set()
        capabilities: set[str] = set()
        for executor in self._executors:
            name = getattr(executor, "name", "")
            if name:
                capabilities.add(name)
            supported = getattr(executor, "supported_targets", None)
            if supported:
                targets.update(set(supported))
            elif name:
                targets.add(name)
        return RemoteNodeHeartbeat(
            node_id=node_id,
            targets=targets,
            aliases=set(aliases or set()),
            capabilities=capabilities,
            metadata=dict(metadata or {}),
            auth_token_id="local_executor_client",
        )


class RemoteNodeClient(Protocol):
    def execute(self, payload: RemoteExecutionPayload) -> RemoteExecutionResponse:
        raise NotImplementedError

    def heartbeat(self, node_id: str, *, aliases: set[str] | None = None, metadata: dict[str, Any] | None = None) -> RemoteNodeHeartbeat:
        raise NotImplementedError