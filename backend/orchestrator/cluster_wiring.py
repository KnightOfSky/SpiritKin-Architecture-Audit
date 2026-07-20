"""Default executor wiring extracted from agent_cluster.

Pure factory: the cluster passes its own state (device backend, clients,
registries) explicitly; nothing here reads cluster internals.
"""

from __future__ import annotations

import os

from backend.executors import (
    BrowserWorkerExecutor,
    FeishuExecutor,
    FFmpegWorkerExecutor,
    GitWorkerExecutor,
    LocalPCExecutor,
    NodeRegistry,
    PythonWorkerExecutor,
    RemoteExecutor,
    ServiceRAGWorkerExecutor,
)
from backend.executors.base import BaseExecutor
from backend.services.openclaw import create_openclaw_executor


def build_default_executors(
    device_backend,
    device_name: str,
    *,
    node_registry: NodeRegistry | None = None,
    feishu_client=None,
    openclaw_client=None,
    openclaw_client_factory=None,
    openclaw_state_path: str | None = None,
    knowledge_retriever=None,
) -> list[BaseExecutor]:
    executors: list[BaseExecutor] = [
        LocalPCExecutor(device_backend, device_name=device_name),
        FeishuExecutor(client=feishu_client),
        PythonWorkerExecutor(),
        GitWorkerExecutor(),
        FFmpegWorkerExecutor(),
        ServiceRAGWorkerExecutor(retriever=knowledge_retriever),
    ]
    browser_worker = BrowserWorkerExecutor.from_environment()
    if browser_worker is not None:
        executors.append(browser_worker)

    if (
        openclaw_client is not None
        or openclaw_client_factory is not None
        or openclaw_state_path is not None
        or os.getenv("SPIRITKIN_OPENCLAW_HTTP_BASE_URL", "").strip()
    ):
        try:
            executors.append(
                create_openclaw_executor(
                    client=openclaw_client,
                    client_factory=openclaw_client_factory,
                    state_path=openclaw_state_path,
                )
            )
        except Exception:
            pass

    if node_registry is not None and node_registry.list_nodes():
        executors.append(RemoteExecutor(node_registry))

    return executors
