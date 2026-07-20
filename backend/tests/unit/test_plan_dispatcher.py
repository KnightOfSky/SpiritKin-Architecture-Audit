from __future__ import annotations

import unittest

from backend.agents.base import AgentContext, AgentReply
from backend.executors.base import ExecutionRequest
from backend.orchestrator.plan_dispatcher import PlanDispatcher, PlanDispatchServices
from backend.orchestrator.planner import ExecutionPlan
from backend.tools.base import ToolCall


def _reply(name: str) -> AgentReply:
    return AgentReply(text=name, agent_name=name, metadata={"response_kind": name})


class PlanDispatcherTests(unittest.TestCase):
    def _dispatcher(self, calls: list[str]) -> PlanDispatcher:
        return PlanDispatcher(
            PlanDispatchServices(
                with_agent_runtime_context=lambda context, plan: context,
                handle_builtin=lambda name, text: calls.append("builtin") or _reply(name),
                handle_development_plan=lambda request: calls.append("development") or _reply(request),
                handle_tool=lambda context, call: calls.append("tool") or _reply(call.name),
                handle_execution=lambda request, context: calls.append("executor") or _reply(request.operation),
                handle_skill=lambda skill, context: calls.append("skill") or _reply(str(skill)),
                handle_agent=lambda agent, context: calls.append("agent") or _reply("agent"),
                handle_intent_fallback=lambda context: calls.append("intent") or _reply("intent"),
                handle_general=lambda context: calls.append("general") or _reply("general"),
            )
        )

    def test_dispatches_tool_without_calling_other_handlers(self):
        calls: list[str] = []
        dispatcher = self._dispatcher(calls)

        reply = dispatcher.dispatch(
            AgentContext(user_input="查资料"),
            ExecutionPlan(route="tool", reason="test", tool_call=ToolCall(name="web.search", arguments={})),
        )

        self.assertEqual(calls, ["tool"])
        self.assertEqual(reply.agent_name, "web.search")

    def test_dispatches_executor_without_general_fallback(self):
        calls: list[str] = []
        dispatcher = self._dispatcher(calls)

        reply = dispatcher.dispatch(
            AgentContext(user_input="打开记事本"),
            ExecutionPlan(
                route="executor",
                reason="test",
                execution_request=ExecutionRequest(target="local_pc", operation="launch_app", params={"app_name": "notepad"}),
            ),
        )

        self.assertEqual(calls, ["executor"])
        self.assertEqual(reply.agent_name, "launch_app")

    def test_general_action_can_use_intent_fallback(self):
        calls: list[str] = []
        dispatcher = self._dispatcher(calls)

        reply = dispatcher.dispatch(
            AgentContext(user_input="打开记事本"),
            ExecutionPlan(route="general", reason="fallback"),
        )

        self.assertEqual(calls, ["intent"])
        self.assertEqual(reply.agent_name, "intent")

    def test_attachment_general_request_stays_in_soul_phase(self):
        calls: list[str] = []
        dispatcher = self._dispatcher(calls)

        reply = dispatcher.dispatch(
            AgentContext(user_input="打开并解释", metadata={"attachment_documents": [{"path": "a.txt", "text_preview": "x"}]}),
            ExecutionPlan(route="general", reason="attachment"),
        )

        self.assertEqual(calls, ["general"])
        self.assertEqual(reply.agent_name, "general")


if __name__ == "__main__":
    unittest.main()
