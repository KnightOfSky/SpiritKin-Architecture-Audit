from __future__ import annotations

import inspect
import unittest

from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.agent_cluster_wiring import AgentClusterWiring


class AgentClusterWiringTests(unittest.TestCase):
    def test_public_constructor_uses_grouped_wiring(self):
        parameters = inspect.signature(AgentCluster.__init__).parameters
        explicit = [
            name
            for name, parameter in parameters.items()
            if name != "self" and parameter.kind is not inspect.Parameter.VAR_KEYWORD
        ]

        self.assertEqual(explicit, ["llm_client", "memory_limit", "device_name", "deps", "wiring"])

    def test_legacy_named_dependencies_remain_compatible(self):
        marker = object()
        cluster = AgentCluster(
            llm_client=lambda prompt, **kwargs: "ok <emotion:neutral>",
            device_backend=marker,
            auto_load_project_docs=False,
        )

        self.assertIs(cluster.device_backend, marker)

    def test_wiring_rejects_unknown_legacy_dependency(self):
        with self.assertRaisesRegex(TypeError, "unexpected keyword"):
            AgentClusterWiring().with_legacy_overrides({"not_a_dependency": True})


if __name__ == "__main__":
    unittest.main()
