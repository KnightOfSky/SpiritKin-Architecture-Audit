"""OpenClaw 统一入口：设备适配在 devices，执行控制在 executors。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.action.arm_operations import close_gripper, move_arm_home, move_arm_to, open_gripper
from backend.devices.openclaw import (
    HttpOpenClawClient,
    InMemoryOpenClawClient,
    JsonOpenClawStateStore,
    OpenClawArm,
    create_openclaw_arm,
    create_openclaw_client_from_env,
)
from backend.executors.node_registry import RemoteNode
from backend.executors.openclaw_executor import OpenClawExecutor
from backend.executors.remote_protocol import ExecutorRemoteNodeClient


def create_openclaw_executor(
    *,
    arm: OpenClawArm | None = None,
    client=None,
    client_factory=None,
    state_store: JsonOpenClawStateStore | None = None,
    state_path: str | Path | None = None,
) -> OpenClawExecutor:
    """创建软件优先的 OpenClaw 执行器。"""

    if arm is not None:
        return OpenClawExecutor(arm=arm)

    if client is None and client_factory is None:
        if state_store is None and state_path is None:
            client = create_openclaw_client_from_env()
        if client is None:
            client = InMemoryOpenClawClient(state_store=state_store, state_path=state_path)

    return OpenClawExecutor(client=client, client_factory=client_factory)


def create_openclaw_remote_node(
    node_id: str,
    *,
    aliases: set[str] | None = None,
    metadata: dict[str, Any] | None = None,
    targets: set[str] | None = None,
    arm: OpenClawArm | None = None,
    client=None,
    client_factory=None,
    state_store: JsonOpenClawStateStore | None = None,
    state_path: str | Path | None = None,
) -> RemoteNode:
    """把 OpenClaw 软件执行器包装成一个可被 RemoteExecutor 调度的节点。"""

    executor = create_openclaw_executor(
        arm=arm,
        client=client,
        client_factory=client_factory,
        state_store=state_store,
        state_path=state_path,
    )
    return RemoteNode(
        node_id=node_id,
        client=ExecutorRemoteNodeClient([executor]),
        aliases=set(aliases or ()),
        targets=set(targets or {"openclaw"}),
        metadata=dict(metadata or {}),
    )

__all__ = [
    "InMemoryOpenClawClient",
    "HttpOpenClawClient",
    "JsonOpenClawStateStore",
    "OpenClawArm",
    "OpenClawExecutor",
    "RemoteNode",
    "close_gripper",
    "create_openclaw_executor",
    "create_openclaw_arm",
    "create_openclaw_remote_node",
    "move_arm_home",
    "move_arm_to",
    "open_gripper",
]
