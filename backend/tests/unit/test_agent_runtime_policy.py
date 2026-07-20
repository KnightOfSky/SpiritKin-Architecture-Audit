from __future__ import annotations

import unittest

from backend.orchestrator.agent_container import (
    build_agent_runtime_policy,
    capability_records_for_agent,
    skills_for_agent_container,
)


class FakeCapabilityRecord:
    def __init__(self, capability_id, owner_agents=()):
        self.capability_id = capability_id
        self.owner_agents = tuple(owner_agents)


class FakeSkill:
    def __init__(self, name, metadata):
        self.name = name
        self.metadata = metadata


class BuildAgentRuntimePolicyTests(unittest.TestCase):
    def test_assembles_policy_from_profile_and_managed_agents(self):
        policy = build_agent_runtime_policy(
            " ecommerce ",
            profiles_by_id={"ecommerce": {"label": "电商", "domain": "ecommerce", "capabilities": ["publish"]}},
            managed_agents={
                "assistant_allowlist_by_agent": {"ecommerce": ["helper-1", "", 2]},
                "enabled_external_assistants_by_id": {"helper-1": {"assistant_id": "helper-1"}, "2": "bad"},
                "knowledge_base_by_agent": {"ecommerce": {"knowledge_base_id": "kb-1"}},
            },
        )
        self.assertEqual(policy["agent_id"], "ecommerce")
        self.assertEqual(policy["label"], "电商")
        self.assertEqual(policy["role"], "specialist")
        self.assertEqual(policy["allowed_assistant_ids"], ["helper-1", "2"])
        self.assertEqual(policy["allowed_assistants"], [{"assistant_id": "helper-1"}])
        self.assertEqual(policy["knowledge_base"], {"knowledge_base_id": "kb-1"})

    def test_empty_agent_defaults_to_general_role(self):
        policy = build_agent_runtime_policy("", profiles_by_id={}, managed_agents={})
        self.assertEqual(policy["role"], "general")
        self.assertEqual(policy["framework"], "native")
        self.assertEqual(policy["adapter"], "spiritkin_native")
        self.assertEqual(policy["knowledge_base"], {})


class CapabilityRecordsForAgentTests(unittest.TestCase):
    def test_matches_wanted_ids_or_owner(self):
        records = [
            FakeCapabilityRecord("cap-a", owner_agents=("other",)),
            FakeCapabilityRecord("cap-b", owner_agents=("me",)),
            FakeCapabilityRecord("cap-c", owner_agents=()),
        ]
        selected = capability_records_for_agent(records, "me", ["cap-a"])
        self.assertEqual([record.capability_id for record in selected], ["cap-a", "cap-b"])

    def test_without_wanted_ids_only_owner_matches(self):
        records = [FakeCapabilityRecord("cap-a", owner_agents=("other",))]
        self.assertEqual(capability_records_for_agent(records, "me"), [])


class SkillsForAgentContainerTests(unittest.TestCase):
    def test_matches_owner_or_capability(self):
        specs = [
            FakeSkill("mine", {"owner_agent_id": "me"}),
            FakeSkill("legacy", {"agent_id": "me"}),
            FakeSkill("by-cap", {"capability_id": "cap-a"}),
            FakeSkill("other", {"owner_agent_id": "other", "capability_id": "cap-x"}),
        ]
        skills = skills_for_agent_container(specs, "me", ["cap-a"])
        self.assertEqual([skill.name for skill in skills], ["mine", "legacy", "by-cap"])


if __name__ == "__main__":
    unittest.main()
