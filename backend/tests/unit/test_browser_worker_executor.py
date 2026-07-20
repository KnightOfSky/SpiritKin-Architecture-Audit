from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.executors import BrowserWorkerExecutor, ExecutionRequest
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.capability_graph import build_capability_registry
from backend.orchestrator.worker_pool import WorkerPool, WorkerRequirement
from backend.tools import ToolCall, build_default_tool_registry


class BrowserWorkerExecutorTests(unittest.TestCase):
    def test_unconfigured_browser_worker_fails_closed(self):
        with TemporaryDirectory() as tmp:
            executor = BrowserWorkerExecutor(workspace_root=tmp, browser_command=[])

            result = executor.execute(
                ExecutionRequest(target="browser", operation="browser.health_check", params={})
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "browser_worker_process_not_configured")
        self.assertEqual(result.metadata["worker_maturity"], "not_configured")

    def test_process_backed_browser_worker_health_check(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = _write_browser_worker_stub(workspace)
            executor = BrowserWorkerExecutor(
                workspace_root=workspace,
                browser_command=[sys.executable, str(script)],
            )

            result = executor.execute(
                ExecutionRequest(target="browser", operation="browser.health_check", params={})
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["operation"], "browser.health_check")
        self.assertEqual(result.metadata["worker_maturity"], "process_backed")
        self.assertEqual(result.metadata["process_protocol"], "json_stdin_stdout")

    def test_process_backed_browser_worker_open_url(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = _write_browser_worker_stub(workspace)
            executor = BrowserWorkerExecutor(
                workspace_root=workspace,
                browser_command=[sys.executable, str(script)],
            )

            result = executor.execute(
                ExecutionRequest(
                    target="browser",
                    operation="browser_open_url",
                    params={"url": "https://example.com"},
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["url"], "https://example.com")
        self.assertEqual(result.data["target"], "browser")

    def test_browser_worker_tool_request_runs_through_worker_pool(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = _write_browser_worker_stub(workspace)
            registry = build_default_tool_registry()
            tool_result = registry.invoke(
                ToolCall(name="browser.worker_open_url", arguments={"url": "https://example.com"})
            )
            pool = WorkerPool(
                [
                    BrowserWorkerExecutor(
                        workspace_root=workspace,
                        browser_command=[sys.executable, str(script)],
                    )
                ]
            )

            execution = pool.execute(tool_result.execution_request, actor="unit-test")

        self.assertTrue(tool_result.success)
        self.assertTrue(execution.result.success)
        self.assertEqual(execution.worker.worker_id, "executor:browser_worker")
        self.assertEqual(execution.result.data["url"], "https://example.com")

    def test_worker_pool_schedules_process_backed_browser_worker(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = _write_browser_worker_stub(workspace)
            pool = WorkerPool(
                [
                    BrowserWorkerExecutor(
                        workspace_root=workspace,
                        browser_command=[sys.executable, str(script)],
                    )
                ]
            )

            decision = pool.schedule(
                WorkerRequirement(
                    needs=("browser",),
                    target="browser",
                    operation="browser_open_url",
                )
            )

        self.assertEqual(decision.status, "selected")
        self.assertEqual(decision.selected.worker_id, "executor:browser_worker")
        self.assertEqual(decision.selected.worker_type, "browser_worker")
        self.assertEqual(decision.selected.worker_subtype, "local_browser_worker")

    def test_capability_graph_reports_browser_worker_as_ready(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = _write_browser_worker_stub(workspace)
            executor = BrowserWorkerExecutor(
                workspace_root=workspace,
                browser_command=[sys.executable, str(script)],
            )
            graph = build_capability_registry(executors=[executor])

            recommendation = graph.recommend(
                "browser open url",
                required_capabilities=("browser_browser_open_url",),
                required_workers=("browser",),
                include_planned=True,
            ).snapshot()

        top = recommendation["candidates"][0]
        evidence = top["worker_evidence"][0]
        self.assertEqual(top["capability"]["metadata"]["source"], "executor")
        self.assertEqual(evidence["status"], "ready")
        self.assertTrue(evidence["schedulable"])
        self.assertIn("executor:browser_worker", evidence["matched_worker_ids"])

    def test_agent_cluster_default_snapshot_exposes_browser_worker_when_configured(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = _write_browser_worker_stub(workspace)
            command = json.dumps([sys.executable, str(script)])

            with patch.dict("os.environ", {"SPIRITKIN_BROWSER_WORKER_COMMAND": command}, clear=False):
                cluster = AgentCluster(llm_client=lambda prompt: "ok", auto_load_project_docs=False)

            workers = {item["worker_id"]: item for item in cluster.worker_pool_snapshot["workers"]}

        self.assertIn("executor:browser_worker", workers)
        self.assertEqual(workers["executor:browser_worker"]["health_status"], "ready")
        self.assertEqual(workers["executor:browser_worker"]["worker_type"], "browser_worker")
        self.assertIn("browser", workers["executor:browser_worker"]["targets"])

    def test_agent_cluster_default_snapshot_omits_browser_worker_without_process_command(self):
        with patch.dict("os.environ", {"SPIRITKIN_BROWSER_WORKER_COMMAND": ""}, clear=False):
            cluster = AgentCluster(llm_client=lambda prompt: "ok", auto_load_project_docs=False)

        workers = {item["worker_id"]: item for item in cluster.worker_pool_snapshot["workers"]}

        self.assertNotIn("executor:browser_worker", workers)


def _write_browser_worker_stub(workspace: Path) -> Path:
    script = workspace / "browser_worker_stub.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "params = payload.get('params') or {}\n"
        "operation = payload.get('operation')\n"
        "data = {'target': payload.get('target'), 'operation': operation, 'workspace_root': payload.get('workspace_root')}\n"
        "if 'url' in params:\n"
        "    data['url'] = params['url']\n"
        "if 'query' in params:\n"
        "    data['query'] = params['query']\n"
        "print(json.dumps({'success': True, 'message': 'browser stub ok', 'data': data}, sort_keys=True))\n",
        encoding="utf-8",
    )
    return script


if __name__ == "__main__":
    unittest.main()
