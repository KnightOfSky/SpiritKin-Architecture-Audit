from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend.executors.base import ExecutionRequest
from backend.executors.remote_protocol import RemoteNodeClient, RemoteNodeHeartbeat
from backend.orchestrator.worker_pool import WorkerDescriptor


@dataclass
class RemoteNode:
    node_id: str
    client: RemoteNodeClient
    targets: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    status: str = "unknown"
    last_seen_at: float | None = None
    auth_token_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class NodeRegistry:
    def __init__(self, nodes: list[RemoteNode] | None = None, *, event_limit: int = 50):
        self._nodes: dict[str, RemoteNode] = {}
        self._events: list[dict[str, Any]] = []
        self._event_limit = max(10, int(event_limit))
        for node in nodes or []:
            self.register(node)

    def register(self, node: RemoteNode) -> None:
        self._nodes[node.node_id] = node

    def get(self, node_id: str) -> RemoteNode | None:
        return self._nodes.get(node_id)

    def list_nodes(self) -> list[RemoteNode]:
        return list(self._nodes.values())

    def register_heartbeat(self, heartbeat: RemoteNodeHeartbeat, client: RemoteNodeClient | None = None) -> RemoteNode:
        node = self._nodes.get(heartbeat.node_id)
        if node is None:
            if client is None:
                raise ValueError(f"未注册节点且未提供 client: {heartbeat.node_id}")
            node = RemoteNode(node_id=heartbeat.node_id, client=client)
            self._nodes[heartbeat.node_id] = node
        node.targets = set(heartbeat.targets or node.targets)
        node.aliases = set(heartbeat.aliases or node.aliases)
        node.capabilities = set(heartbeat.capabilities or node.capabilities)
        node.status = "online"
        node.last_seen_at = float(heartbeat.timestamp or time.time())
        node.auth_token_id = str(heartbeat.auth_token_id or node.auth_token_id)
        node.metadata = {**dict(node.metadata or {}), **dict(heartbeat.metadata or {})}
        node.metadata["consecutive_heartbeat_failures"] = 0
        node.metadata["last_heartbeat_error"] = ""
        node.metadata["last_heartbeat_ok_at"] = node.last_seen_at
        self._record_event(node.node_id, "heartbeat_ok", "online", "heartbeat refreshed", timestamp=node.last_seen_at)
        return node

    def refresh_from_client(self, node_id: str, *, aliases: set[str] | None = None, metadata: dict[str, Any] | None = None) -> RemoteNode:
        node = self.get(node_id)
        if node is None:
            raise ValueError(f"未找到节点: {node_id}")
        heartbeat = node.client.heartbeat(node_id=node.node_id, aliases=aliases or node.aliases, metadata=metadata)
        return self.register_heartbeat(heartbeat, client=node.client)

    def mark_stale(self, ttl_seconds: float = 30.0, *, now: float | None = None) -> list[str]:
        current = float(now or time.time())
        stale: list[str] = []
        for node in self._nodes.values():
            if node.status == "offline":
                continue
            if node.last_seen_at is None:
                continue
            if current - node.last_seen_at > max(1.0, float(ttl_seconds)):
                was_stale = node.status == "stale"
                node.status = "stale"
                if not was_stale:
                    self._record_event(node.node_id, "heartbeat_stale", "stale", "heartbeat ttl expired", timestamp=current)
                stale.append(node.node_id)
        return stale

    def list_online_nodes(self, ttl_seconds: float = 30.0, *, now: float | None = None) -> list[RemoteNode]:
        self.mark_stale(ttl_seconds=ttl_seconds, now=now)
        return [node for node in self._nodes.values() if node.status == "online"]

    def refresh_all_from_clients(self, *, ttl_seconds: float = 30.0, now: float | None = None) -> dict[str, object]:
        refreshed: list[str] = []
        failed: dict[str, str] = {}
        for node in list(self._nodes.values()):
            try:
                self.refresh_from_client(node.node_id)
                refreshed.append(node.node_id)
            except Exception as exc:
                node.status = "offline"
                failures = int(dict(node.metadata or {}).get("consecutive_heartbeat_failures") or 0) + 1
                node.metadata = {
                    **dict(node.metadata or {}),
                    "last_heartbeat_error": str(exc),
                    "last_heartbeat_error_at": float(now or time.time()),
                    "consecutive_heartbeat_failures": failures,
                }
                self._record_event(node.node_id, "heartbeat_failed", "offline", str(exc), timestamp=float(now or time.time()))
                failed[node.node_id] = str(exc)
        stale = self.mark_stale(ttl_seconds=ttl_seconds, now=now)
        return {"refreshed": refreshed, "failed": failed, "stale": stale, "total": len(self._nodes)}

    def snapshot(self, *, ttl_seconds: float = 30.0, now: float | None = None) -> dict[str, object]:
        self.mark_stale(ttl_seconds=ttl_seconds, now=now)
        nodes = []
        status_counts: dict[str, int] = {}
        for node in self._nodes.values():
            status = node.status or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            nodes.append(
                {
                    "node_id": node.node_id,
                    "status": status,
                    "targets": sorted(node.targets),
                    "aliases": sorted(node.aliases),
                    "capabilities": sorted(node.capabilities),
                    "last_seen_at": node.last_seen_at,
                    "auth_token_id": node.auth_token_id,
                    "consecutive_heartbeat_failures": int(dict(node.metadata or {}).get("consecutive_heartbeat_failures") or 0),
                    "last_heartbeat_error": str(dict(node.metadata or {}).get("last_heartbeat_error") or ""),
                    "metadata": dict(node.metadata or {}),
                }
            )
        return {"total": len(nodes), "status_counts": status_counts, "nodes": nodes, "recent_events": list(self._events[-12:])}

    def worker_descriptors(self, *, ttl_seconds: float = 30.0, now: float | None = None) -> list[WorkerDescriptor]:
        self.mark_stale(ttl_seconds=ttl_seconds, now=now)
        descriptors: list[WorkerDescriptor] = []
        for node in self._nodes.values():
            descriptors.append(self._worker_descriptor_for_node(node))
        return descriptors

    def resolve_payload_target(self, request: ExecutionRequest, node: RemoteNode) -> str:
        remote_target = str(request.params.get("remote_target", "")).strip()
        if remote_target:
            return remote_target

        if request.target in node.targets:
            return request.target

        if request.target.startswith("remote:") or request.target == node.node_id or request.target in node.aliases:
            if len(node.targets) == 1:
                return next(iter(node.targets))

        if request.target == "remote" and len(node.targets) == 1:
            return next(iter(node.targets))

        return request.target

    def find_for_request(self, request: ExecutionRequest) -> RemoteNode | None:
        node_id = str(request.params.get("node_id", "")).strip()
        if node_id:
            node = self.get(node_id)
            return node if self._node_is_routable(node) else None

        if request.target.startswith("remote:"):
            node = self.get(request.target.split(":", 1)[1])
            return node if self._node_is_routable(node) else None

        if request.target in self._nodes:
            node = self.get(request.target)
            return node if self._node_is_routable(node) else None

        for node in self._nodes.values():
            if not self._node_is_routable(node):
                continue
            if request.target in node.targets or request.target in node.aliases:
                return node

        if request.target == "remote" and len(self._nodes) == 1:
            node = next(iter(self._nodes.values()))
            return node if self._node_is_routable(node) else None

        return None

    @staticmethod
    def _node_is_routable(node: RemoteNode | None) -> bool:
        if node is None:
            return False
        return (node.status or "unknown") not in {"stale", "offline"}

    @staticmethod
    def _worker_descriptor_for_node(node: RemoteNode) -> WorkerDescriptor:
        metadata = dict(node.metadata or {})
        status = node.status or "unknown"
        health_status = "ready" if status == "online" else ("unavailable" if status in {"offline", "stale"} else status)
        capabilities = tuple(sorted(str(item) for item in node.capabilities if str(item).strip()))
        targets = tuple(sorted(str(item) for item in node.targets if str(item).strip()))
        return WorkerDescriptor(
            worker_id=f"remote:{node.node_id}",
            label=str(metadata.get("label") or node.node_id),
            kind="remote_runtime",
            worker_type="generic_remote_worker",
            worker_subtype="remote_runtime_worker",
            capabilities=capabilities,
            capability_namespaces=_remote_capability_namespaces(targets, capabilities),
            targets=targets,
            operations=tuple(_remote_operations(capabilities)),
            legacy_names=("Remote Worker",),
            workspace=str(metadata.get("workspace") or metadata.get("workspace_id") or ""),
            permission_scope="remote",
            health_status=health_status,
            health_detail=status,
            queue_depth=int(metadata.get("queue_depth") or metadata.get("pending") or 0),
            metadata={
                **metadata,
                "node_id": node.node_id,
                "aliases": sorted(node.aliases),
                "auth_token_id": node.auth_token_id,
                "last_seen_at": node.last_seen_at,
            },
        )

    def _record_event(self, node_id: str, kind: str, status: str, message: str, *, timestamp: float | None = None) -> None:
        self._events.append(
            {
                "timestamp": float(timestamp or time.time()),
                "node_id": node_id,
                "kind": kind,
                "status": status,
                "message": message,
            }
        )
        if len(self._events) > self._event_limit:
            self._events = self._events[-self._event_limit :]


def _remote_capability_namespaces(targets: tuple[str, ...], capabilities: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for raw in (*targets, *capabilities):
        text = str(raw or "").strip().lower()
        if not text:
            continue
        candidates = [text]
        if "." in text:
            candidates.append(text.split(".", 1)[0])
        if "_" in text:
            candidates.append(text.split("_", 1)[0])
        for candidate in candidates:
            mapped = {
                "desktop": "desktop",
                "local": "desktop",
                "browser": "browser",
                "playwright": "browser",
                "python": "python",
                "node": "node",
                "ffmpeg": "ffmpeg",
                "git": "git",
                "openclaw": "openclaw",
                "android": "android",
                "adb": "adb",
                "pdd": "pdd",
                "rag": "rag",
                "kb": "rag",
                "knowledge": "rag",
                "embedding": "embedding",
                "search": "search",
                "ocr": "ocr",
            }.get(candidate, candidate)
            if mapped and mapped not in values:
                values.append(mapped)
        for keyword in ("browser", "python", "ffmpeg", "git", "openclaw", "android", "adb", "pdd", "rag", "embedding", "search", "ocr"):
            if keyword in text and keyword not in values:
                values.append("browser" if keyword == "playwright" else keyword)
    return tuple(values)


def _remote_operations(capabilities: tuple[str, ...]) -> list[str]:
    operations: list[str] = []
    for capability in capabilities:
        text = str(capability or "").strip()
        if "." in text and text not in operations:
            operations.append(text)
    return operations
