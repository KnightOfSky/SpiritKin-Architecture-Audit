from __future__ import annotations

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.services.feishu import FeishuClient


class FeishuExecutor(BaseExecutor):
    name = "feishu"

    def __init__(self, client: FeishuClient | None = None):
        self._client = client or FeishuClient()

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target.lower() in {"feishu", "lark"}

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(False, f"不支持的目标: {request.target}", error_code="unsupported_target")

        operation = request.operation.lower().strip()
        params = dict(request.params or {})
        try:
            if operation != "send_message":
                return ExecutionResult(False, f"不支持的飞书操作: {request.operation}", error_code="unsupported_operation")
            result = self._client.send_text_message(str(params["recipient"]), str(params["text"]))
        except KeyError as exc:
            return ExecutionResult(False, f"缺少参数: {exc.args[0]}", error_code="missing_params", metadata={"missing_param": exc.args[0]})
        except Exception as exc:
            return ExecutionResult(False, str(exc), error_code="feishu_exception")

        mode = "dry-run，未真实发送" if result.dry_run else "已真实发送"
        return ExecutionResult(
            True,
            f"飞书消息{mode}：发给 {result.recipient}，内容是：{result.text}",
            data={
                "recipient": result.recipient,
                "receive_id": result.receive_id,
                "receive_id_type": result.receive_id_type,
                "text": result.text,
                "message_id": result.message_id,
                "dry_run": result.dry_run,
            },
            metadata={"integration": "feishu", "dry_run": result.dry_run},
        )