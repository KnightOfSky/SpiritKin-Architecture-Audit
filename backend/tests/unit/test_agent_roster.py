from __future__ import annotations

import unittest

from backend.orchestrator.agent_roster import AgentRoster


class _Agent:
    def __init__(self, name: str, *, priority: int = 0, domain: str = ""):
        self.name = name
        self.routing_priority = priority
        self.domain = domain


class AgentRosterTests(unittest.TestCase):
    def test_applies_enabled_priority_domain_and_adapter_configuration(self):
        programming = _Agent("programming", priority=1, domain="code")
        disabled = _Agent("vision", priority=5, domain="vision")

        roster = AgentRoster.build(
            [programming, disabled],
            {
                "agents": [
                    {
                        "agent_id": "programming",
                        "enabled": True,
                        "priority": 9,
                        "domain": "software",
                        "framework": "langgraph",
                        "label": "编程 Agent",
                    },
                    {"agent_id": "vision", "enabled": False},
                ]
            },
        )

        self.assertEqual(roster.agents, [programming])
        self.assertEqual(programming.routing_priority, 9)
        self.assertEqual(programming.domain, "software")
        self.assertEqual(roster.adapters_by_id["programming"].policy.framework, "langgraph")
        self.assertEqual(roster.router.route("@编程 请检查代码").effective_input, "请检查代码")

    def test_keeps_profile_only_agents_available_for_status_mentions(self):
        roster = AgentRoster.build(
            [_Agent("programming")],
            {
                "agent_profiles_by_id": {
                    "programming": {"label": "编程 Agent"},
                    "external_reviewer": {"label": "外部审查 Agent"},
                }
            },
        )

        decision = roster.router.route("@外部审查 当前进度")

        self.assertIsNotNone(decision.agent_mention)
        self.assertEqual(decision.agent_mention.agent_id, "external_reviewer")
        self.assertEqual(decision.route_kind, "agent_status")


if __name__ == "__main__":
    unittest.main()
