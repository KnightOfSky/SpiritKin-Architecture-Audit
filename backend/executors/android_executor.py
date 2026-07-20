from __future__ import annotations

from backend.devices.android_device import AndroidDeviceBackend
from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.security.safety_control import evaluate_execution_safety


class AndroidExecutor(BaseExecutor):
    def __init__(self, backend: AndroidDeviceBackend | None = None):
        self._backend = backend or AndroidDeviceBackend()

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in ("android_device", "android")

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        op = request.operation
        params = dict(request.params or {})
        read_only = op in {"device_status", "device.info", "status", "list_installed_apps", "software.list_installed"}
        safety = evaluate_execution_safety(
            target="android_executor",
            operation=op,
            actor=str(params.get("actor") or ""),
            read_only=read_only,
            dry_run=bool(params.get("dry_run")),
        )
        if not safety.allowed:
            return ExecutionResult(
                success=False,
                message=safety.message,
                error_code=safety.error_code,
                metadata={"safety": safety.snapshot()},
            )

        try:
            if op in ("device_status", "device.info", "status"):
                data = self._backend.device_status()
                return ExecutionResult(success=True, message="Android 设备状态", data=data)
            elif op in ("list_installed_apps", "software.list_installed"):
                limit = int(params.get("limit", 50))
                data = self._backend.list_installed_apps(limit=limit)
                return ExecutionResult(success=True, message="已请求应用清单（需 Companion 上报）", data=data)
            elif op in ("launch_app", "app.launch"):
                app_name = str(params.get("app_name") or "")
                if not app_name:
                    return ExecutionResult(success=False, message="未指定 app_name", error_code="missing_app_name")
                data = self._backend.launch_app(app_name)
                return ExecutionResult(success=True, message=f"已入队启动 {app_name}", data=data)
            elif op in ("close_app", "app.close"):
                app_name = str(params.get("app_name") or "")
                force = bool(params.get("force"))
                data = self._backend.close_app(app_name, force=force)
                return ExecutionResult(success=True, message=f"已入队关闭 {app_name}", data=data)
            elif op == "push_notification":
                title = str(params.get("title") or "")
                body = str(params.get("body") or params.get("text") or "")
                data = self._backend.push_notification(title, body)
                return ExecutionResult(success=True, message="通知已入队", data=data)
            else:
                return ExecutionResult(success=False, message=f"Android 不支持: {op}", error_code="unsupported_operation")
        except Exception as exc:
            return ExecutionResult(success=False, message=str(exc), error_code="android_executor_error")
