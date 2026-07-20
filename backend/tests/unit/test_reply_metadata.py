from __future__ import annotations

import unittest

from backend.agents.base import AgentContext, AgentReply
from backend.orchestrator.reply_metadata import (
    attach_context_runtime_metadata,
    attach_intent_resolution_metadata,
    attach_project_metadata,
    attach_task_metadata,
    build_context_metadata,
    has_attachment_context,
    inject_knowledge_hits,
    inject_web_search_hits,
)


class FakeSnapshot:
    def snapshot(self):
        return {"id": "x-1"}


class FakeResolution:
    status = "resolved"
    reason = "matched"
    confidence = 0.9
    corrected_text = "打开浏览器"


def make_reply(**metadata) -> AgentReply:
    return AgentReply(text="ok", emotion="neutral", action="idle", agent_name="test", metadata=metadata)


class ReplyMetadataTests(unittest.TestCase):
    def test_attach_task_metadata_none_is_noop(self):
        reply = make_reply()
        self.assertIs(attach_task_metadata(reply, None), reply)
        self.assertNotIn("task", reply.metadata)
        attach_task_metadata(reply, FakeSnapshot())
        self.assertEqual(reply.metadata["task"], {"id": "x-1"})

    def test_attach_project_metadata_and_context_metadata(self):
        reply = make_reply()
        attach_project_metadata(reply, FakeSnapshot())
        self.assertEqual(reply.metadata["project"], {"id": "x-1"})
        self.assertEqual(build_context_metadata(None), {})
        self.assertEqual(build_context_metadata(FakeSnapshot()), {"project": {"id": "x-1"}})

    def test_attach_intent_resolution_metadata_includes_correction(self):
        reply = attach_intent_resolution_metadata(make_reply(), FakeResolution(), source="voice")
        resolution = reply.metadata["intent_resolution"]
        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(resolution["source"], "voice")
        self.assertEqual(resolution["corrected_text"], "打开浏览器")

    def test_attach_context_runtime_metadata_prefers_existing_reply_values(self):
        context = AgentContext(
            user_input="hi",
            metadata={
                "agent_runtime": {"from": "context"},
                "agent_knowledge_hits": ["hit"],
                "perception_context": {"screen": True},
            },
        )
        reply = attach_context_runtime_metadata(make_reply(agent_runtime={"from": "reply"}), context)
        self.assertEqual(reply.metadata["agent_runtime"], {"from": "reply"})
        self.assertEqual(reply.metadata["agent_knowledge_hits"], ["hit"])
        self.assertEqual(reply.metadata["perception_context"], {"screen": True})

    def test_has_attachment_context(self):
        self.assertFalse(has_attachment_context(None))
        self.assertFalse(has_attachment_context({}))
        self.assertTrue(has_attachment_context({"attachment_count": 2}))
        self.assertTrue(has_attachment_context({"attachment_documents": [{}]}))

    def test_inject_hits_return_new_context(self):
        context = AgentContext(user_input="hi", metadata={"keep": 1})
        with_knowledge = inject_knowledge_hits(context, ["k"])
        with_web = inject_web_search_hits(context, None)
        self.assertEqual(with_knowledge.metadata["knowledge_hits"], ["k"])
        self.assertEqual(with_web.metadata["web_search_hits"], [])
        self.assertNotIn("knowledge_hits", context.metadata)
        self.assertEqual(with_knowledge.metadata["keep"], 1)


if __name__ == "__main__":
    unittest.main()
