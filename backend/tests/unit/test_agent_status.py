from __future__ import annotations

import unittest

from backend.orchestrator.agent_mentions import AgentMention
from backend.orchestrator.agent_status import (
    build_agent_status_reply,
    build_agent_status_snapshot,
    extract_agent_skills,
    extract_agent_workflow_queue,
)


class FakeSkillSpec:
    def __init__(self, name, metadata, risk_level="low"):
        self.name = name
        self.metadata = metadata
        self.risk_level = risk_level


class FakePolicy:
    def snapshot(self):
        return {"mode": "safe"}


class FakeAdapter:
    policy = FakePolicy()


def make_mention(agent_id="agent-1", label="", intent="status"):
    return AgentMention(raw=agent_id, agent_id=agent_id, label=label, text_without_mention="", intent=intent)


class ExtractAgentSkillsTests(unittest.TestCase):
    def test_filters_by_owner_and_defaults(self):
        specs = [
            FakeSkillSpec("mine", {"owner_agent_id": "agent-1"}),
            FakeSkillSpec("other", {"owner_agent_id": "agent-2"}),
            FakeSkillSpec("no-meta", None),
        ]
        skills = extract_agent_skills(specs, "agent-1")
        self.assertEqual(
            skills,
            [{"name": "mine", "status": "draft", "risk_level": "low", "promotion_status": ""}],
        )


class ExtractAgentWorkflowQueueTests(unittest.TestCase):
    def test_filters_nodes_by_assigned_agent(self):
        snapshot = {
            "runs": [
                {
                    "run_id": "r1",
                    "workflow_name": "wf",
                    "nodes": [
                        {"node_id": "n1", "assigned_agent": "agent-1", "status": "running"},
                        {"node_id": "n2", "assigned_agent": "agent-2"},
                        "not-a-dict",
                    ],
                },
                "not-a-dict",
            ]
        }
        queue = extract_agent_workflow_queue(snapshot, "agent-1")
        self.assertEqual(
            queue,
            [{"run_id": "r1", "workflow_name": "wf", "node_id": "n1", "label": "n1", "status": "running"}],
        )

    def test_empty_snapshot(self):
        self.assertEqual(extract_agent_workflow_queue({}, "agent-1"), [])


class BuildAgentStatusSnapshotTests(unittest.TestCase):
    def test_assembles_fields_with_fallbacks(self):
        snapshot = build_agent_status_snapshot(
            make_mention(label="小助手"),
            profile={"enabled": True, "domain": "code", "capabilities": ["a", "b"]},
            runtime_policy={"framework": "langgraph", "adapter": "default", "role": "coder"},
            adapter=FakeAdapter(),
            skills=[
                {"name": "b", "status": "draft", "risk_level": "low", "promotion_status": ""},
                {"name": "a", "status": "active", "risk_level": "low", "promotion_status": ""},
            ],
            workflow_queue=[{"run_id": "r1"}],
            task_queue=[
                {"domain": "code", "id": "t1"},
                {"domain": "video", "id": "t2"},
                "not-a-dict",
            ],
            recent_performance={"success_rate": 1.0},
        )
        self.assertEqual(snapshot["agent_id"], "agent-1")
        self.assertEqual(snapshot["label"], "小助手")
        self.assertEqual(snapshot["domain"], "code")
        self.assertEqual(snapshot["role"], "coder")
        self.assertEqual(snapshot["framework"], "langgraph")
        self.assertEqual(snapshot["adapter_policy"], {"mode": "safe"})
        # active skills sort before drafts
        self.assertEqual([item["name"] for item in snapshot["skills"]], ["a", "b"])
        self.assertEqual(snapshot["task_queue"], [{"domain": "code", "id": "t1"}])
        self.assertEqual(snapshot["recent_performance"], {"success_rate": 1.0})

    def test_adapter_without_policy_yields_empty_dict(self):
        snapshot = build_agent_status_snapshot(
            make_mention(),
            profile={},
            runtime_policy={},
            adapter=None,
            skills=[],
            workflow_queue=[],
            task_queue=[],
            recent_performance={},
        )
        self.assertEqual(snapshot["adapter_policy"], {})
        self.assertEqual(snapshot["label"], "agent-1")
        self.assertTrue(snapshot["enabled"])


class BuildAgentStatusReplyTests(unittest.TestCase):
    def test_reply_text_and_metadata(self):
        mention = make_mention(label="小助手")
        snapshot = {
            "label": "小助手",
            "domain": "code",
            "role": "coder",
            "framework": "langgraph",
            "adapter": "default",
            "capabilities": ["a", "b"],
            "skills": [{"name": "a"}],
            "workflow_queue": [{"run_id": "r1"}, {"run_id": "r2"}],
        }
        reply = build_agent_status_reply(mention, snapshot)
        self.assertIn("小助手 当前状态", reply.text)
        self.assertIn("domain=code", reply.text)
        self.assertIn("关联 Skill 1 个", reply.text)
        self.assertIn("工作流队列 2 项", reply.text)
        self.assertEqual(reply.metadata["response_kind"], "agent_status")
        self.assertEqual(reply.metadata["agent_status"], snapshot)
        self.assertEqual(reply.metadata["agent_mention"], mention.snapshot())

    def test_reply_handles_missing_fields(self):
        reply = build_agent_status_reply(make_mention(), {"label": "x"})
        self.assertIn("domain=--", reply.text)
        self.assertIn("能力：--", reply.text)


if __name__ == "__main__":
    unittest.main()
