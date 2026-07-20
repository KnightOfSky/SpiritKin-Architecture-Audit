from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.agents.base import AgentContext
from backend.app.runtime import SpiritKinRuntime
from backend.memory.orchestrator import MemoryOrchestrator
from backend.memory.relationship import RelationshipStore
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.prompt_context import build_relationship_context
from backend.orchestrator.response_phase import SoulResponsePhase


class RelationshipSystemTests(unittest.TestCase):
    def test_explicit_boundary_persists_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "relationship.json"
            store = RelationshipStore(path)

            update = store.observe_user_input("以后请不要再叫我宝宝")
            reloaded = RelationshipStore(path)

        self.assertEqual(update["signal"], "boundary")
        self.assertEqual(reloaded.snapshot()["active_boundary_count"], 1)
        self.assertEqual(reloaded.snapshot()["boundaries"][0]["kind"], "address")
        self.assertIn("叫我宝宝", reloaded.snapshot()["boundaries"][0]["subject"])

    def test_repeated_boundary_is_deduplicated(self):
        store = RelationshipStore(path="")
        first = store.observe_user_input("不要再主动提醒我休息")
        second = store.observe_user_input("请不要再主动提醒我休息")

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        snapshot = store.snapshot()
        self.assertEqual(snapshot["active_boundary_count"], 1)
        self.assertEqual(snapshot["boundaries"][0]["repeated_count"], 1)
        self.assertEqual(snapshot["care_strategy"]["mode"], "quiet_presence")
        self.assertEqual(snapshot["care_strategy"]["proactive_level"], "off")

    def test_explicit_release_deactivates_matching_boundary(self):
        store = RelationshipStore(path="")
        store.observe_user_input("以后不要再聊工作压力")

        update = store.observe_user_input("现在可以再聊工作压力")

        self.assertEqual(update["signal"], "boundary_released")
        self.assertEqual(store.snapshot()["active_boundary_count"], 0)

    def test_correction_changes_care_strategy_without_unbounded_scores(self):
        store = RelationshipStore(path="")
        for _ in range(200):
            store.record_interaction(success=True)
        store.observe_user_input("我已经说了，不是这个意思")
        snapshot = store.snapshot()

        self.assertLessEqual(snapshot["trust"], 1.0)
        self.assertLessEqual(snapshot["familiarity"], 1.0)
        self.assertEqual(snapshot["stage"], "trusted")
        self.assertEqual(snapshot["care_strategy"]["mode"], "repair_and_listen")

    def test_relationship_context_contains_hard_boundary_and_no_tool_schema(self):
        store = RelationshipStore(path="")
        store.observe_user_input("不要再叫我老板")
        metadata = {"relationship": store.context_snapshot(), "tool_definitions": ["SECRET_TOOL_SCHEMA"]}

        context = build_relationship_context(metadata)

        self.assertIn("必须遵守", context)
        self.assertIn("叫我老板", context)
        self.assertNotIn("SECRET_TOOL_SCHEMA", context)

    def test_soul_phase_injects_relationship_context(self):
        prompts: list[str] = []
        phase = SoulResponsePhase(lambda prompt, **_: prompts.append(prompt) or "好的。<emotion:neutral><action:nod>")
        store = RelationshipStore(path="")
        store.observe_user_input("不要再主动提醒我休息")

        phase.respond(AgentContext(user_input="继续", metadata={"relationship": store.context_snapshot()}))

        self.assertTrue(prompts)
        self.assertIn("主动提醒我休息", prompts[0])
        self.assertIn("主动程度: off", prompts[0])

    def test_agent_cluster_observes_boundary_before_llm_response(self):
        prompts: list[str] = []
        store = RelationshipStore(path="")
        cluster = AgentCluster(
            llm_client=lambda prompt, **_: prompts.append(prompt) or "明白。<emotion:neutral><action:nod>",
            relationship_store=store,
        )

        cluster.process("以后不要再叫我亲爱的")

        self.assertEqual(store.snapshot()["active_boundary_count"], 1)
        self.assertIn("叫我亲爱的", prompts[-1])
        self.assertEqual(cluster._active_input_metadata["relationship_update"]["signal"], "boundary")

    def test_memory_orchestrator_exposes_and_updates_relationship(self):
        store = RelationshipStore(path="")
        memory = MemoryOrchestrator(relationship_store=store)

        memory.record_interaction(user_input="你好", reply_text="你好", success=True)
        snapshot = memory.snapshot()

        self.assertEqual(snapshot["relationship"]["interaction_count"], 1)
        self.assertIn("关系阶段", memory.recall().recall_summary)

    def test_runtime_emits_relationship_state_event(self):
        store = RelationshipStore(path="")
        store.observe_user_input("不要再主动提醒我休息")
        runtime = SpiritKinRuntime.__new__(SpiritKinRuntime)
        runtime.presence = None
        runtime.memory_orchestrator = MemoryOrchestrator(relationship_store=store)

        events = runtime.build_lpm_state_events()

        relationship_event = next(item for item in events if item["type"] == "relationship.updated")
        self.assertEqual(relationship_event["payload"]["active_boundary_count"], 1)
        self.assertEqual(relationship_event["payload"]["care_strategy"]["proactive_level"], "off")


if __name__ == "__main__":
    unittest.main()
