from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_feishu_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(
            ToolSpec(
                name="feishu.message.send",
                description="发送飞书文本消息。高风险动作，需要用户确认后执行。",
                target="feishu",
                operation="send_message",
                risk_level="high",
                schema={"recipient": "string", "text": "string"},
            )
        )
    ]