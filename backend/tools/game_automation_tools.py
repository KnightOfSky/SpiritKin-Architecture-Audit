from __future__ import annotations

from typing import Any

from backend.game_automation.manager import GameAutomationManager, get_game_automation_manager
from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec


class GameAutomationRunTool(BaseTool):
    spec = ToolSpec(
        "game.automation.run",
        "在显式白名单的本地或测试浏览器游戏中运行结构化动作计划",
        "game_automation",
        "game_automation_run",
        risk_level="high",
        schema={
            "adapter_id": {"type": "string"},
            "url": {"type": "string", "format": "uri"},
            "session_id": {"type": "string"},
            "steps": {"type": "array"},
            "headless": {"type": "boolean"},
        },
    )

    def __init__(self, manager: GameAutomationManager | None = None):
        self.manager = manager

    def invoke(self, call: ToolCall) -> ToolResult:
        if not bool((call.arguments or {}).get("authz_confirmed")):
            return ToolResult(False, "游戏自动化需要本次显式确认。", error_code="game_confirmation_required")
        arguments = dict(call.arguments or {})
        try:
            result = (self.manager or get_game_automation_manager()).run_plan(
                adapter_id=str(arguments.get("adapter_id") or ""),
                url=str(arguments.get("url") or ""),
                steps=_steps(arguments.get("steps")),
                session_id=str(arguments.get("session_id") or ""),
                headless=bool(arguments.get("headless", False)),
            )
        except PermissionError as exc:
            return ToolResult(False, str(exc), error_code="game_adapter_not_allowlisted")
        except (TypeError, ValueError, RuntimeError) as exc:
            return ToolResult(False, str(exc), error_code="game_automation_invalid")
        success = result.get("status") == "completed"
        return ToolResult(
            success,
            "游戏自动化计划已完成。" if success else "游戏自动化已安全停止。",
            data=result,
            error_code="" if success else str(result.get("stop_reason") or "game_automation_stopped"),
        )


class GameAutomationStopTool(BaseTool):
    spec = ToolSpec(
        "game.automation.stop",
        "立即停止指定游戏自动化会话",
        "game_automation",
        "game_automation_stop",
        read_only=True,
        schema={"session_id": {"type": "string"}},
    )

    def __init__(self, manager: GameAutomationManager | None = None):
        self.manager = manager

    def invoke(self, call: ToolCall) -> ToolResult:
        session_id = str((call.arguments or {}).get("session_id") or "").strip()
        stopped = (self.manager or get_game_automation_manager()).stop(session_id)
        return ToolResult(
            stopped,
            "已发送全局停止。" if stopped else "未找到正在运行的游戏自动化会话。",
            data={"session_id": session_id, "stopped": stopped},
            error_code="" if stopped else "game_session_not_running",
        )


def get_game_automation_tools(manager: GameAutomationManager | None = None) -> list[BaseTool]:
    return [GameAutomationRunTool(manager), GameAutomationStopTool(manager)]


def _steps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("game automation steps must be an array")
    return [dict(item) if isinstance(item, dict) else item for item in value]
