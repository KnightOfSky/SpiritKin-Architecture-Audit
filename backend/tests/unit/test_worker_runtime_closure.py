from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.executors import (
    BrowserWorkerExecutor,
    ExecutionRequest,
    FFmpegWorkerExecutor,
    GitWorkerExecutor,
    ServiceRAGWorkerExecutor,
)
from backend.knowledge import InMemoryKnowledgeStore, SimpleKnowledgeRetriever, ingest_text_document
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.capability_graph import build_capability_registry
from backend.orchestrator.worker_pool import WorkerPool, WorkerRequirement
from backend.tools import ToolCall, build_default_tool_registry


class WorkerRuntimeClosureTests(unittest.TestCase):
    def test_git_worker_runs_status_inside_workspace_repo(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
            executor = GitWorkerExecutor(workspace_root=workspace)

            result = executor.execute(
                ExecutionRequest(target="git", operation="git.status", params={"repo_path": "repo"})
            )

            self.assertTrue(result.success)
            self.assertIn("hello.txt", result.data["stdout"])
            self.assertEqual(result.data["command"][-3:], ["status", "--porcelain=v1", "-b"])
            self.assertEqual(result.metadata["worker_maturity"], "real")

    def test_git_worker_rejects_repo_outside_workspace(self):
        with TemporaryDirectory() as workspace_tmp, TemporaryDirectory() as outside_tmp:
            executor = GitWorkerExecutor(workspace_root=workspace_tmp)

            result = executor.execute(
                ExecutionRequest(target="git", operation="git.status", params={"repo_path": outside_tmp})
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "git_worker_path_outside_workspace")

    def test_git_tool_request_runs_through_worker_pool(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            registry = build_default_tool_registry()
            tool_result = registry.invoke(ToolCall(name="git.status", arguments={"repo_path": "repo"}))
            pool = WorkerPool([GitWorkerExecutor(workspace_root=workspace)])

            execution = pool.execute(tool_result.execution_request, actor="unit-test")

            self.assertTrue(tool_result.success)
            self.assertTrue(execution.result.success)
            self.assertEqual(execution.worker.worker_id, "executor:git_worker")

    def test_ffmpeg_worker_rejects_input_outside_workspace(self):
        with TemporaryDirectory() as workspace_tmp, TemporaryDirectory() as outside_tmp:
            outside_file = Path(outside_tmp) / "movie.mp4"
            outside_file.write_bytes(b"not really media")
            executor = FFmpegWorkerExecutor(workspace_root=workspace_tmp)

            result = executor.execute(
                ExecutionRequest(target="ffmpeg", operation="ffmpeg.probe", params={"input_path": str(outside_file)})
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "ffmpeg_worker_path_outside_workspace")

    def test_ffmpeg_worker_fails_closed_when_binary_missing(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            media = workspace / "sample.mp4"
            media.write_bytes(b"not really media")
            executor = FFmpegWorkerExecutor(workspace_root=workspace, ffprobe_executable="definitely-missing-ffprobe")

            result = executor.execute(
                ExecutionRequest(target="ffmpeg", operation="ffmpeg.probe", params={"input_path": "sample.mp4"})
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "ffmpeg_worker_not_available")
            self.assertEqual(result.metadata["failure_context"]["kind"], "fixable")
            self.assertIn("Install FFmpeg", result.metadata["failure_context"]["install_suggestion"])

    def test_rag_worker_retrieves_from_configured_retriever(self):
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "Ops SOP", "Refund requests must check the order before reply")
        retriever = SimpleKnowledgeRetriever(store)
        executor = ServiceRAGWorkerExecutor(retriever=retriever)

        result = executor.execute(
            ExecutionRequest(target="knowledge", operation="rag.search", params={"query": "refund order", "top_k": 1})
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["hits"][0]["source_title"], "Ops SOP")
        self.assertTrue(result.metadata["read_only"])

    def test_rag_tool_request_runs_through_worker_pool(self):
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "Dev Notes", "WorkerPool dispatches execution requests")
        retriever = SimpleKnowledgeRetriever(store)
        registry = build_default_tool_registry(knowledge_retriever=retriever)
        tool_result = registry.invoke(ToolCall(name="rag.search", arguments={"query": "WorkerPool", "top_k": 1}))
        pool = WorkerPool([ServiceRAGWorkerExecutor(retriever=retriever)])

        execution = pool.execute(tool_result.execution_request, actor="unit-test")

        self.assertTrue(tool_result.success)
        self.assertTrue(execution.result.success)
        self.assertEqual(execution.worker.worker_id, "executor:service_rag_worker")
        self.assertEqual(execution.result.data["hits"][0]["source_title"], "Dev Notes")

    def test_rag_embedding_create_fails_closed_without_provider(self):
        executor = ServiceRAGWorkerExecutor()
        with patch.dict("os.environ", {"SPIRITKIN_EMBEDDING_PROVIDER": "hashing"}, clear=True):
            result = executor.execute(
                ExecutionRequest(target="knowledge", operation="embedding.create", params={"text": "hello"})
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "service_rag_worker_embedding_not_configured")

    def test_agent_cluster_snapshot_exposes_remaining_real_workers(self):
        cluster = AgentCluster(llm_client=lambda prompt: "ok", auto_load_project_docs=False)

        workers = {item["worker_id"]: item for item in cluster.worker_pool_snapshot["workers"]}

        self.assertIn("executor:git_worker", workers)
        self.assertIn("executor:ffmpeg_worker", workers)
        self.assertIn("executor:service_rag_worker", workers)
        self.assertIn("git", workers["executor:git_worker"]["targets"])
        self.assertIn("ffmpeg", workers["executor:ffmpeg_worker"]["targets"])
        self.assertIn("knowledge", workers["executor:service_rag_worker"]["targets"])

    def test_browser_worker_process_is_explicitly_configured_before_ready(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            script = workspace / "browser_worker_stub.py"
            script.write_text(
                "import json, sys\n"
                "payload = json.loads(sys.stdin.read() or '{}')\n"
                "print(json.dumps({'success': True, 'message': 'ok', 'data': {'operation': payload.get('operation')}}))\n",
                encoding="utf-8",
            )
            pool = WorkerPool(
                [
                    BrowserWorkerExecutor(
                        workspace_root=workspace,
                        browser_command=[sys.executable, str(script)],
                    )
                ]
            )

            execution = pool.execute(
                ExecutionRequest(target="browser", operation="browser.health_check", params={}),
                actor="unit-test",
            )

            self.assertTrue(execution.result.success)
            self.assertEqual(execution.worker.worker_id, "executor:browser_worker")
            self.assertEqual(execution.worker.worker_type, "browser_worker")
            self.assertEqual(execution.result.metadata["worker_maturity"], "process_backed")

    def test_capability_graph_reports_remaining_workers_as_ready(self):
        with TemporaryDirectory() as tmp:
            executors = [
                BrowserWorkerExecutor(
                    workspace_root=tmp,
                    browser_command=[
                        sys.executable,
                        "-c",
                        "import json, sys; json.loads(sys.stdin.read() or '{}'); print(json.dumps({'success': True, 'data': {}}))",
                    ],
                ),
                GitWorkerExecutor(workspace_root=tmp),
                FFmpegWorkerExecutor(workspace_root=tmp),
                ServiceRAGWorkerExecutor(),
            ]
            graph = build_capability_registry(executors=executors)

            for capability_id, required_worker in (
                ("browser_browser_health_check", "browser"),
                ("git_git_status", "git"),
                ("ffmpeg_ffmpeg_probe", "ffmpeg"),
                ("knowledge_rag_search", "knowledge"),
            ):
                recommendation = graph.recommend(
                    capability_id,
                    required_capabilities=(capability_id,),
                    required_workers=(required_worker,),
                ).snapshot()
                self.assertEqual(recommendation["candidates"][0]["worker_evidence"][0]["status"], "ready")

    def test_worker_pool_schedules_remaining_workers(self):
        with TemporaryDirectory() as tmp:
            pool = WorkerPool(
                [
                    BrowserWorkerExecutor(
                        workspace_root=tmp,
                        browser_command=[
                            sys.executable,
                            "-c",
                            "import json, sys; json.loads(sys.stdin.read() or '{}'); print(json.dumps({'success': True, 'data': {}}))",
                        ],
                    ),
                    GitWorkerExecutor(workspace_root=tmp),
                    FFmpegWorkerExecutor(workspace_root=tmp),
                    ServiceRAGWorkerExecutor(),
                ]
            )

            self.assertEqual(pool.schedule(WorkerRequirement(needs=("browser",))).selected.worker_id, "executor:browser_worker")
            self.assertEqual(pool.schedule(WorkerRequirement(needs=("git",))).selected.worker_id, "executor:git_worker")
            self.assertEqual(pool.schedule(WorkerRequirement(needs=("ffmpeg",))).selected.worker_id, "executor:ffmpeg_worker")
            self.assertEqual(pool.schedule(WorkerRequirement(needs=("rag",))).selected.worker_id, "executor:service_rag_worker")


if __name__ == "__main__":
    unittest.main()
