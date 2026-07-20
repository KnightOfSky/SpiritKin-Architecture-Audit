from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentContext, AgentReply
from backend.orchestrator.reply_metadata import inject_knowledge_hits, inject_web_search_hits
from backend.orchestrator.skill_phase import agent_scope_reply


@dataclass(frozen=True)
class ToolPhaseServices:
    tool_registry: Any
    record_failure: Callable[..., Any]
    execute: Callable[..., AgentReply]
    respond_general: Callable[[AgentContext], AgentReply]


class ToolPhase:
    def __init__(self, services: ToolPhaseServices):
        self._services = services

    def run(self, context: AgentContext, tool_call: Any) -> AgentReply:
        scope_reply = agent_scope_reply(context, tool_name=tool_call.name)
        if scope_reply is not None:
            return scope_reply
        result = self._services.tool_registry.invoke(tool_call)
        if not result.success:
            self._services.record_failure(
                stage="tool",
                actor=tool_call.name,
                message=result.message,
                user_input=context.user_input,
                error_code=result.error_code or "tool_failed",
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
                metadata=result.metadata,
            )
            return AgentReply(
                text=f"工具调用失败：{result.message}",
                emotion="confused",
                action="tilt_head",
                agent_name="tool_error",
            )
        if result.execution_request is not None:
            return self._services.execute(result.execution_request, user_input=context.user_input)
        if tool_call.name == "kb.search":
            return self._knowledge_reply(context, result.data)
        if tool_call.name == "web.search":
            return self._web_reply(context, tool_call, result.data)
        return AgentReply(
            text=result.message,
            emotion="happy",
            action="idle",
            agent_name="tool_generic",
        )

    def _knowledge_reply(self, context: AgentContext, data: Any) -> AgentReply:
        if not data:
            return AgentReply(
                text="知识库里暂时没有找到相关资料。",
                emotion="confused",
                action="tap_chin",
                agent_name="tool_kb_search",
            )
        reply = self._services.respond_general(inject_knowledge_hits(context, data))
        reply.agent_name = "tool_kb_search"
        return reply

    def _web_reply(self, context: AgentContext, tool_call: Any, data: Any) -> AgentReply:
        query = tool_call.arguments.get("query", "")
        if not data:
            return AgentReply(
                text="联网搜索暂时没有找到可用结果。",
                emotion="confused",
                action="tap_chin",
                agent_name="tool_web_search",
                metadata={"web_search": {"query": query, "results": []}},
            )
        reply = self._services.respond_general(inject_web_search_hits(context, data))
        reply.agent_name = "tool_web_search"
        reply.metadata = {
            **dict(reply.metadata or {}),
            "web_search": {"query": query, "results": data},
        }
        return reply
