from __future__ import annotations

import unittest

from backend.memory.long_term import LongTermMemoryStore
from backend.memory.relationship import RelationshipStore
from backend.orchestrator.cluster_router import ClusterRouter
from backend.orchestrator.turn_context import TurnContextPreparer

AGENTS = [
    {"agent_id": "programming", "label": "编程 Agent"},
    {"agent_id": "main_text", "label": "Spirit"},
]


class ClusterRouterAndContextTests(unittest.TestCase):
    def test_router_is_pure_for_equal_inputs(self):
        router = ClusterRouter(AGENTS)
        metadata = {"plan_mode": False, "request_id": "req-1"}

        first = router.route("@编程 请检查代码", metadata)
        second = router.route("@编程 请检查代码", metadata)

        self.assertEqual(first, second)
        self.assertEqual(metadata, {"plan_mode": False, "request_id": "req-1"})
        self.assertEqual(first.effective_input, "请检查代码")
        self.assertEqual(first.metadata["forced_agent_id"], "programming")

    def test_router_keeps_status_mention_out_of_forced_route(self):
        decision = ClusterRouter(AGENTS).route("@编程 当前进度")

        self.assertEqual(decision.route_kind, "agent_status")
        self.assertNotIn("forced_agent_id", decision.metadata)

    def test_context_preparer_injects_relationship_memory_and_perception(self):
        relationship = RelationshipStore(path="")
        memory = LongTermMemoryStore()
        memory.add("preference", "用户偏好简短回答", importance=0.9)
        preparer = TurnContextPreparer(
            router=ClusterRouter(AGENTS),
            current_time=lambda: {"iso": "2026-07-17T12:00:00+08:00"},
            relationship_store=relationship,
            long_term_memory=memory,
        )
        turn = preparer.begin("不要再叫我老板")

        enriched = preparer.enrich(
            turn,
            channel="text",
            visual_context="原视觉",
            inventory_context="已安装 git",
            capability_inventory={"tools": 3},
            resource_registry={"resources": 2},
            perception_enricher=lambda **kwargs: (kwargs["visual_context"] + " + 屏幕", kwargs["metadata"]),
        )

        self.assertEqual(enriched.metadata["relationship_update"]["signal"], "boundary")
        self.assertEqual(enriched.metadata["relationship"]["active_boundary_count"], 1)
        self.assertEqual(enriched.metadata["long_term_memory_status"]["status"], "activated")
        self.assertEqual(enriched.metadata["inventory_context"], "已安装 git")
        self.assertEqual(enriched.visual_context, "原视觉 + 屏幕")


if __name__ == "__main__":
    unittest.main()
