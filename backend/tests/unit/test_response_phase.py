from __future__ import annotations

import unittest

from backend.agents.base import AgentContext
from backend.orchestrator.response_phase import SoulResponsePhase


class SoulResponsePhaseTests(unittest.TestCase):
    def test_soul_prompt_uses_activated_context_but_not_tool_definitions(self):
        prompts = []
        phase = SoulResponsePhase(
            lambda prompt, **kwargs: prompts.append((prompt, kwargs))
            or "会保持简短。<emotion:happy><action:nod>"
        )
        context = AgentContext(
            user_input="继续简短回答",
            metadata={
                "tool_definitions": ["SECRET_TOOL_SCHEMA_SENTINEL"],
                "knowledge_hits": ["项目知识"],
                "long_term_memory_hits": [
                    {"category": "preference", "content": "用户偏好简短回答"}
                ],
                "relationship": {
                    "stage": "acquainted",
                    "care_strategy": {"mode": "focused_support", "proactive_level": "low"},
                    "boundaries": [{"kind": "address", "subject": "叫我老板", "active": True}],
                },
                "recent_history": [{"role": "user", "content": "上一轮"}],
            },
        )

        reply = phase.respond(
            context,
            inventory={
                "software": [{"name": "Edge"}],
                "cli_tools": [{"name": "git", "available": True}],
            },
        )

        self.assertEqual(reply.agent_name, "general")
        self.assertEqual(reply.action, "nod")
        self.assertEqual(prompts[0][1]["agent_name"], "main_text")
        self.assertIn("用户偏好简短回答", prompts[0][0])
        self.assertIn("项目知识", prompts[0][0])
        self.assertIn("叫我老板", prompts[0][0])
        self.assertNotIn("SECRET_TOOL_SCHEMA_SENTINEL", prompts[0][0])

    def test_soul_phase_has_no_tool_or_executor_dependency(self):
        phase = SoulResponsePhase(lambda prompt, **kwargs: "ok <emotion:neutral>")

        self.assertEqual(set(vars(phase)), {"_llm_call"})


if __name__ == "__main__":
    unittest.main()
