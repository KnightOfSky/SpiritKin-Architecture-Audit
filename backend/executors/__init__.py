from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult

_LAZY_EXPORTS = {
    "BrowserWorkerExecutor": ("backend.executors.browser_worker_executor", "BrowserWorkerExecutor"),
    "FFmpegWorkerExecutor": ("backend.executors.ffmpeg_worker_executor", "FFmpegWorkerExecutor"),
    "FeishuExecutor": ("backend.executors.feishu_executor", "FeishuExecutor"),
    "GitWorkerExecutor": ("backend.executors.git_worker_executor", "GitWorkerExecutor"),
    "LocalPCExecutor": ("backend.executors.local_pc_executor", "LocalPCExecutor"),
    "NodeRegistry": ("backend.executors.node_registry", "NodeRegistry"),
    "RemoteNode": ("backend.executors.node_registry", "RemoteNode"),
    "OpenClawExecutor": ("backend.executors.openclaw_executor", "OpenClawExecutor"),
    "PythonWorkerExecutor": ("backend.executors.python_worker_executor", "PythonWorkerExecutor"),
    "RemoteExecutor": ("backend.executors.remote_executor", "RemoteExecutor"),
    "ExecutorRemoteNodeClient": ("backend.executors.remote_protocol", "ExecutorRemoteNodeClient"),
    "HttpRemoteNodeClient": ("backend.executors.remote_protocol", "HttpRemoteNodeClient"),
    "RemoteExecutionPayload": ("backend.executors.remote_protocol", "RemoteExecutionPayload"),
    "RemoteExecutionResponse": ("backend.executors.remote_protocol", "RemoteExecutionResponse"),
    "RemoteNodeHeartbeat": ("backend.executors.remote_protocol", "RemoteNodeHeartbeat"),
    "ServiceRAGWorkerExecutor": ("backend.executors.service_rag_worker_executor", "ServiceRAGWorkerExecutor"),
}


def __getattr__(name):
    lazy_export = _LAZY_EXPORTS.get(name)
    if lazy_export is None:
        raise AttributeError(name)
    module_name, attr_name = lazy_export
    from importlib import import_module

    return getattr(import_module(module_name), attr_name)

__all__ = [
    "BaseExecutor",
    "BrowserWorkerExecutor",
    "ExecutionRequest",
    "ExecutionResult",
    "FFmpegWorkerExecutor",
    "FeishuExecutor",
    "GitWorkerExecutor",
    "LocalPCExecutor",
    "OpenClawExecutor",
    "PythonWorkerExecutor",
    "ServiceRAGWorkerExecutor",
    "NodeRegistry",
    "RemoteNode",
    "RemoteExecutor",
    "ExecutorRemoteNodeClient",
    "HttpRemoteNodeClient",
    "RemoteExecutionPayload",
    "RemoteExecutionResponse",
    "RemoteNodeHeartbeat",
]
