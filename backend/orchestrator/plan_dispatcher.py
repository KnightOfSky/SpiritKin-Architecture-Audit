from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentContext, AgentReply
from backend.orchestrator.planner import ExecutionPlan
from backend.orchestrator.prompt_context import looks_like_action_request
from backend.orchestrator.reply_metadata import attach_context_runtime_metadata, has_attachment_context


@dataclass(frozen=True)
class PlanDispatchServices:
    with_agent_runtime_context: Callable[[AgentContext, ExecutionPlan], AgentContext]
    handle_builtin: Callable[[str, str], AgentReply]
    handle_development_plan: Callable[[str], AgentReply]
    handle_tool: Callable[[AgentContext, Any], AgentReply]
    handle_execution: Callable[[Any, AgentContext], AgentReply]
    handle_skill: Callable[[Any, AgentContext], AgentReply]
    handle_agent: Callable[[Any, AgentContext], AgentReply]
    handle_intent_fallback: Callable[[AgentContext], AgentReply | None]
    handle_general: Callable[[AgentContext], AgentReply]


class PlanDispatcher:
    """Dispatch a completed plan without owning planning or execution state."""

    def __init__(self, services: PlanDispatchServices):
        self._services = services

    def dispatch(self, context: AgentContext, plan: ExecutionPlan) -> AgentReply:
        context = self._services.with_agent_runtime_context(context, plan)
        if plan.route == "builtin" and plan.builtin_name:
            return self._attach(self._services.handle_builtin(plan.builtin_name, context.user_input), context)
        if plan.route == "clarify_openclaw":
            return self._attach(self._openclaw_clarification(), context)
        if plan.route == "development_plan" and plan.development_request is not None:
            return self._attach(self._services.handle_development_plan(plan.development_request), context)
        if plan.route == "tool" and plan.tool_call is not None:
            return self._attach(self._services.handle_tool(context, plan.tool_call), context)
        if plan.route == "executor" and plan.execution_request is not None:
            return self._attach(self._services.handle_execution(plan.execution_request, context), context)
        if plan.route == "skill" and plan.skill_spec is not None:
            return self._attach(self._services.handle_skill(plan.skill_spec, context), context)
        if plan.route == "agent" and plan.agent is not None:
            return self._attach(self._services.handle_agent(plan.agent, context), context)
        if plan.route == "general" and not has_attachment_context(context.metadata) and looks_like_action_request(context.user_input):
            intent_reply = self._services.handle_intent_fallback(context)
            if intent_reply is not None:
                return self._attach(intent_reply, context)
        return self._attach(self._services.handle_general(context), context)

    @staticmethod
    def _attach(reply: AgentReply, context: AgentContext) -> AgentReply:
        return attach_context_runtime_metadata(reply, context)

    @staticmethod
    def _openclaw_clarification() -> AgentReply:
        return AgentReply(
            text="我听到的内容像是在说 OpenClaw/机械臂，但动作没识别清楚。你可以直接说：机械臂状态、机械臂回零、打开夹爪、关闭夹爪。",
            spoken_text="我听到像是机械臂指令，但动作没识别清楚。请说机械臂状态、机械臂回零、打开夹爪或关闭夹爪。",
            emotion="confused",
            action="tilt_head",
            agent_name="openclaw_intent_clarifier",
            metadata={"response_kind": "intent_clarification", "target": "openclaw"},
        )
