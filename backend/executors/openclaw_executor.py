from __future__ import annotations

from backend.action.arm_operations import close_gripper, move_arm_home, move_arm_to, open_gripper
from backend.devices.openclaw import OpenClawArm, create_openclaw_arm
from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult


class OpenClawExecutor(BaseExecutor):
    """OpenClaw 执行器：统一承接物理机械臂或软件节点动作请求。"""

    name = "openclaw"

    def __init__(self, arm: OpenClawArm | None = None, *, client=None, client_factory=None):
        self._arm = arm or create_openclaw_arm(client=client, client_factory=client_factory)

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in {"openclaw", "arm", self.name}

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(
                success=False,
                message=f"不支持的目标: {request.target}",
                error_code="unsupported_target",
                metadata={"target": request.target},
            )

        operation = request.operation.lower().strip()
        params = dict(request.params or {})

        try:
            if operation == "home":
                data = move_arm_home(self._arm)
            elif operation == "move_to":
                data = move_arm_to(params["x"], params["y"], params["z"], self._arm)
            elif operation == "open_gripper":
                data = open_gripper(self._arm)
            elif operation == "close_gripper":
                data = close_gripper(self._arm)
            elif operation == "status":
                data = self._arm.get_status()
            else:
                return ExecutionResult(
                    success=False,
                    message=f"不支持的操作: {request.operation}",
                    error_code="unsupported_operation",
                    metadata={"operation": request.operation},
                )
        except KeyError as exc:
            return ExecutionResult(
                success=False,
                message=f"缺少参数: {exc.args[0]}",
                error_code="missing_params",
                metadata={"missing_param": exc.args[0]},
            )
        except Exception as exc:
            return ExecutionResult(success=False, message=str(exc), error_code="executor_exception")

        return ExecutionResult(
            success=True,
            message=self._build_success_message(operation, data),
            data=data,
            metadata={"target": request.target, "operation": operation},
        )

    @staticmethod
    def _build_success_message(operation: str, data) -> str:
        if operation == "home":
            return "OpenClaw 已回零。"
        if operation == "move_to" and isinstance(data, dict):
            position = data.get("position") or data
            return f"OpenClaw 已移动到 ({position.get('x')}, {position.get('y')}, {position.get('z')})。"
        if operation == "open_gripper":
            return "OpenClaw 夹爪已打开。"
        if operation == "close_gripper":
            return "OpenClaw 夹爪已关闭。"
        if operation == "status" and isinstance(data, dict):
            position = data.get("position") or {}
            gripper_text = "打开" if data.get("gripper_opened") else "关闭"
            return (
                "OpenClaw 当前状态："
                f"{data.get('state', 'unknown')}，"
                f"位置 ({position.get('x')}, {position.get('y')}, {position.get('z')})，"
                f"夹爪{gripper_text}。"
            )
        return f"执行成功: {operation}"