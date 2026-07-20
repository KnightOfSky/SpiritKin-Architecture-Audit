from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.agents.base import AgentReply
from backend.app.runtime import InteractionInput, SpiritKinRuntime
from backend.expression.semantic_reaction import SemanticReactionMatcher, enrich_reply_avatar_reaction
from backend.knowledge.base import BaseEmbeddingProvider


class FixedEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self) -> None:
        self.profile_count = 0
        self.target_index = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.profile_count = len(texts)
        return [self._basis(index) for index in range(len(texts))]

    def embed_query(self, text: str) -> list[float]:
        return self._basis(self.target_index)

    def _basis(self, index: int) -> list[float]:
        vector = [0.0] * max(1, self.profile_count)
        vector[min(index, len(vector) - 1)] = 1.0
        return vector


class SemanticReactionTests(unittest.TestCase):
    def test_explicit_reply_contract_wins_without_embedding_call(self):
        calls = {"count": 0}

        def provider_factory():
            calls["count"] += 1
            raise AssertionError("explicit reactions must not initialize embeddings")

        matcher = SemanticReactionMatcher(provider_factory=provider_factory)
        reply = enrich_reply_avatar_reaction(
            AgentReply(text="你好，我在这里。", emotion="happy", action="wave_hand"),
            matcher=matcher,
        )

        self.assertEqual(calls["count"], 0)
        self.assertEqual(reply.emotion, "happy")
        self.assertEqual(reply.action, "wave_hand")
        self.assertEqual(reply.metadata["avatar_reaction"]["match_type"], "explicit")
        self.assertFalse(reply.metadata["avatar_reaction"]["degraded"])

    def test_shared_embedding_provider_selects_semantic_profile(self):
        provider = FixedEmbeddingProvider()
        matcher = SemanticReactionMatcher(provider_factory=lambda: provider)
        provider.target_index = next(index for index, item in enumerate(matcher._profiles) if item["id"] == "happy_greeting")

        reaction = matcher.match("见到你让我感觉很亲切")

        self.assertEqual(reaction.emotion, "happy")
        self.assertEqual(reaction.action, "wave_hand")
        self.assertEqual(reaction.match_type, "semantic")
        self.assertEqual(reaction.provider, "FixedEmbeddingProvider")
        self.assertFalse(reaction.degraded)

    def test_embedding_failure_degrades_to_versioned_keyword_library(self):
        matcher = SemanticReactionMatcher(provider_factory=lambda: (_ for _ in ()).throw(RuntimeError("embedding offline")))

        reaction = matcher.match("抱歉，这次执行失败了。")

        self.assertEqual(reaction.emotion, "error")
        self.assertEqual(reaction.action, "shake")
        self.assertEqual(reaction.match_type, "keyword_fallback")
        self.assertTrue(reaction.degraded)
        self.assertEqual(reaction.reason, "embedding_unavailable")
        self.assertTrue(reaction.source_hash)

    def test_no_match_remains_neutral_without_inventing_motion(self):
        matcher = SemanticReactionMatcher(provider_factory=lambda: (_ for _ in ()).throw(RuntimeError("not configured")))

        reaction = matcher.match("这里是本次运行记录。")

        self.assertEqual((reaction.emotion, reaction.action), ("neutral", "idle"))
        self.assertEqual(reaction.match_type, "fallback")

    def test_runtime_enriches_reply_and_both_avatar_protocol_envelopes(self):
        matcher = SemanticReactionMatcher(provider_factory=lambda: (_ for _ in ()).throw(RuntimeError("not configured")))

        class FakeAgent:
            def process(self, text, visual_context="", channel="text", input_metadata=None):
                return AgentReply(text="你好，很高兴见到你。", emotion="neutral", action="idle")

        runtime = SpiritKinRuntime(agent=FakeAgent())
        with patch(
            "backend.app.runtime.enrich_reply_avatar_reaction",
            side_effect=lambda reply: enrich_reply_avatar_reaction(reply, matcher=matcher),
        ):
            reply = runtime.handle_input(
                InteractionInput(
                    text="你好",
                    channel="desktop",
                    metadata={"session_id": "semantic-session", "request_id": "semantic-request"},
                )
            )

        self.assertIsNotNone(reply)
        self.assertEqual((reply.emotion, reply.action), ("happy", "wave_hand"))
        events = SpiritKinRuntime.build_response_events(reply)
        assistant = events[0]["payload"]
        interaction = next(item for item in events if item["type"] == "model.interaction")["payload"]
        self.assertEqual(assistant["avatar_reaction"]["profile_id"], "happy_greeting")
        self.assertEqual(assistant["data"]["avatar_reaction"]["source_hash"], assistant["avatar_reaction"]["source_hash"])
        self.assertEqual(interaction["metadata"]["avatar_reaction"]["action"], "wave_hand")
        self.assertEqual(interaction["session_id"], "semantic-session")


if __name__ == "__main__":
    unittest.main()
