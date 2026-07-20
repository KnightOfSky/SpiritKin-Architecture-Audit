from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_openclaw_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name="arm.status",
                description="读取 OpenClaw 当前状态快照。",
                target="openclaw",
                operation="status",
                risk_level="low",
                read_only=True,
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="arm.home",
                description="让 OpenClaw 机械臂回零。",
                target="openclaw",
                operation="home",
                risk_level="high",
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="arm.move_to",
                description="让机械臂移动到指定三维坐标。",
                target="openclaw",
                operation="move_to",
                risk_level="high",
                schema={"x": "float", "y": "float", "z": "float"},
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="gripper.open",
                description="打开夹爪。",
                target="openclaw",
                operation="open_gripper",
                risk_level="medium",
            )
        ),
        ExecutionTool(
            ToolSpec(
                name="gripper.close",
                description="关闭夹爪。",
                target="openclaw",
                operation="close_gripper",
                risk_level="high",
            )
        ),
    ]