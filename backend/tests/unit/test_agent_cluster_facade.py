from __future__ import annotations

import inspect
import unittest

from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.agent_cluster_wiring import AgentClusterWiring
from backend.orchestrator.cluster_deps import ClusterDeps


class AgentClusterFacadeTests(unittest.TestCase):
    def test_grouped_deps_preserve_wiring_values(self):
        marker = object()
        wiring = AgentClusterWiring(
            long_term_memory=marker,
            policy_engine=marker,
            tool_registry=marker,
            app_port=marker,
            managed_agents={"framework": "test"},
        )

        deps = ClusterDeps.from_wiring(lambda _: "ok", wiring)

        self.assertIs(deps.memory.long_term_memory, marker)
        self.assertIs(deps.safety.policy_engine, marker)
        self.assertIs(deps.execution.tool_registry, marker)
        self.assertIs(deps.events.app_port, marker)
        self.assertEqual(deps.llm.managed_agents, {"framework": "test"})

    def test_constructor_exposes_grouped_deps_without_removing_legacy_wiring(self):
        parameters = inspect.signature(AgentCluster.__init__).parameters

        self.assertIn("deps", parameters)
        self.assertIn("wiring", parameters)
        self.assertIn("legacy_wiring", parameters)

    def test_facade_keeps_expected_public_api(self):
        expected = {
            "process",
            "process_next_queued_task",
            "drain_queued_tasks",
            "review_skill_candidates",
            "build_self_improvement_report",
            "build_repair_plan",
            "build_development_plan",
            "get_task",
            "list_active_tasks",
            "get_ecommerce_project",
            "list_active_ecommerce_projects",
        }

        self.assertTrue(expected.issubset(set(dir(AgentCluster))))

    def test_grouped_deps_and_wiring_cannot_be_mixed(self):
        deps = ClusterDeps.from_wiring(lambda _: "ok", AgentClusterWiring())

        with self.assertRaisesRegex(TypeError, "cannot be combined"):
            AgentCluster(deps=deps, wiring=AgentClusterWiring())


if __name__ == "__main__":
    unittest.main()
