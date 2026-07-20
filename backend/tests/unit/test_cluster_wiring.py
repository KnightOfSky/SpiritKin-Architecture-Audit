from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.executors import (
    FeishuExecutor,
    FFmpegWorkerExecutor,
    GitWorkerExecutor,
    LocalPCExecutor,
    PythonWorkerExecutor,
    RemoteExecutor,
    ServiceRAGWorkerExecutor,
)
from backend.orchestrator.cluster_wiring import build_default_executors


class FakeNodeRegistry:
    def __init__(self, nodes):
        self._nodes = nodes

    def list_nodes(self):
        return self._nodes


CLEAN_ENV = {
    "SPIRITKIN_OPENCLAW_HTTP_BASE_URL": "",
    "SPIRITKIN_BROWSER_WORKER_COMMAND": "",
}


class BuildDefaultExecutorsTests(unittest.TestCase):
    def test_base_executor_set_without_optional_workers(self):
        with patch.dict(os.environ, CLEAN_ENV):
            executors = build_default_executors(None, "pc-1")
        types = [type(executor) for executor in executors]
        self.assertEqual(types[0], LocalPCExecutor)
        for expected in (FeishuExecutor, PythonWorkerExecutor, GitWorkerExecutor, FFmpegWorkerExecutor, ServiceRAGWorkerExecutor):
            self.assertIn(expected, types)
        self.assertNotIn(RemoteExecutor, types)

    def test_remote_executor_added_when_nodes_registered(self):
        with patch.dict(os.environ, CLEAN_ENV):
            executors = build_default_executors(None, "pc-1", node_registry=FakeNodeRegistry([{"node_id": "n1"}]))
        self.assertIn(RemoteExecutor, [type(executor) for executor in executors])

    def test_empty_node_registry_skips_remote_executor(self):
        with patch.dict(os.environ, CLEAN_ENV):
            executors = build_default_executors(None, "pc-1", node_registry=FakeNodeRegistry([]))
        self.assertNotIn(RemoteExecutor, [type(executor) for executor in executors])


if __name__ == "__main__":
    unittest.main()
