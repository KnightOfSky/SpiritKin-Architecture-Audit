from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.executors import ExecutionRequest, PythonWorkerExecutor
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.capability_graph import build_capability_registry
from backend.orchestrator.worker_pool import WorkerPool, WorkerRequirement
from backend.tools import ToolCall, build_default_tool_registry


class PythonWorkerExecutorTests(unittest.TestCase):
    def test_runs_workspace_python_script(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = workspace / "scripts" / "echo_args.py"
            script.parent.mkdir()
            script.write_text(
                "import json\n"
                "import sys\n"
                "print(json.dumps({'args': sys.argv[1:]}, sort_keys=True))\n",
                encoding="utf-8",
            )
            executor = PythonWorkerExecutor(workspace_root=workspace, python_executable=sys.executable)

            result = executor.execute(
                ExecutionRequest(
                    target="python",
                    operation="python.run",
                    params={"script_path": "scripts/echo_args.py", "args": ["one", "2"]},
                )
            )

            self.assertTrue(result.success)
            self.assertEqual(result.error_code, "")
            self.assertIn('"args": ["one", "2"]', result.data["stdout"])
            self.assertEqual(result.data["returncode"], 0)
            self.assertEqual(result.data["command"][-2:], ["one", "2"])
            self.assertEqual(result.metadata["worker_maturity"], "real")

    def test_rejects_paths_outside_workspace(self):
        with TemporaryDirectory() as workspace_tmp, TemporaryDirectory() as outside_tmp:
            outside_script = Path(outside_tmp) / "escape.py"
            outside_script.write_text("print('escape')\n", encoding="utf-8")
            executor = PythonWorkerExecutor(workspace_root=workspace_tmp, python_executable=sys.executable)

            result = executor.execute(
                ExecutionRequest(
                    target="python",
                    operation="python.run",
                    params={"script_path": str(outside_script)},
                )
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "python_worker_path_outside_workspace")

    def test_inline_code_is_disabled_by_default(self):
        with TemporaryDirectory() as tmp:
            executor = PythonWorkerExecutor(workspace_root=tmp, python_executable=sys.executable)

            result = executor.execute(
                ExecutionRequest(
                    target="python",
                    operation="python.execute",
                    params={"code": "print('nope')"},
                )
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "python_worker_inline_disabled")

    def test_tool_registry_request_runs_through_worker_pool(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = workspace / "worker_ok.py"
            script.write_text("print('worker-ok')\n", encoding="utf-8")
            registry = build_default_tool_registry()

            tool_result = registry.invoke(
                ToolCall(
                    name="python.run_script",
                    arguments={"script_path": "worker_ok.py", "timeout_seconds": 5},
                )
            )
            pool = WorkerPool(
                [PythonWorkerExecutor(workspace_root=workspace, python_executable=sys.executable)]
            )

            execution = pool.execute(tool_result.execution_request, actor="unit-test")

            self.assertTrue(tool_result.success)
            self.assertTrue(execution.result.success)
            self.assertEqual(execution.worker.worker_id, "executor:python_worker")
            self.assertIn("worker-ok", execution.result.data["stdout"])

    def test_worker_pool_schedules_real_python_executor(self):
        with TemporaryDirectory() as tmp:
            pool = WorkerPool([PythonWorkerExecutor(workspace_root=tmp, python_executable=sys.executable)])

            decision = pool.schedule(
                WorkerRequirement(
                    needs=("python",),
                    target="python",
                    operation="python.run",
                )
            )

            self.assertEqual(decision.status, "selected")
            self.assertEqual(decision.selected.worker_id, "executor:python_worker")
            self.assertNotIn("planned:", decision.selected.worker_id)

    def test_agent_cluster_default_snapshot_exposes_real_python_worker(self):
        cluster = AgentCluster(llm_client=lambda prompt: "ok", auto_load_project_docs=False)

        workers = {item["worker_id"]: item for item in cluster.worker_pool_snapshot["workers"]}

        self.assertIn("executor:python_worker", workers)
        self.assertEqual(workers["executor:python_worker"]["health_status"], "ready")
        self.assertIn("python", workers["executor:python_worker"]["targets"])

    def test_capability_graph_reports_python_executor_as_ready(self):
        with TemporaryDirectory() as tmp:
            executor = PythonWorkerExecutor(workspace_root=tmp, python_executable=sys.executable)
            graph = build_capability_registry(executors=[executor])

            recommendation = graph.recommend(
                "python run",
                required_capabilities=("python_python_run",),
                required_workers=("python",),
                include_planned=True,
            ).snapshot()

            top = recommendation["candidates"][0]
            evidence = top["worker_evidence"][0]
            self.assertEqual(top["capability"]["metadata"]["source"], "executor")
            self.assertEqual(evidence["status"], "ready")
            self.assertTrue(evidence["schedulable"])
            self.assertIn("executor:python_worker", evidence["matched_worker_ids"])


if __name__ == "__main__":
    unittest.main()
