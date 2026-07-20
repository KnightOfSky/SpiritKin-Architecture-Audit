"""Built-in tool handlers extracted from AgentCluster (cluster S).

Stateless helpers for the ``time`` and ``calc`` built-in routes. No dependency
on AgentCluster state; logic preserved verbatim.
"""

from __future__ import annotations

import re
from datetime import datetime

from backend.agents.base import AgentReply


def get_time() -> str:
    return f"现在是 {datetime.now().strftime('%Y年%m月%d日 %H:%M')}"


def calc(expr: str) -> str:
    allowed = "0123456789+-*/(). "
    if all(char in allowed for char in expr):
        try:
            result = eval(expr, {"__builtins__": {}}, {})
            return f"计算结果是：{result}"
        except Exception:
            pass
    return "抱歉，我无法计算这个表达式。"


def handle_builtin(builtin_name: str, user_input: str) -> AgentReply | None:
    if builtin_name == "time":
        return AgentReply(
            text=get_time(),
            emotion="neutral",
            action="glance_clock",
            agent_name="tool_time",
        )

    if builtin_name == "calc":
        expr = re.sub(r"[^0-9+\-*/.]", "", user_input)
        response = calc(expr)
        return AgentReply(
            text=response,
            emotion="confused" if "无法" in response else "happy",
            action="write_on_board",
            agent_name="tool_calc",
        )

    return None
