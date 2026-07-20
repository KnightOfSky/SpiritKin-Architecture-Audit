import gzip
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.executors.base import ExecutionRequest, ExecutionResult
from backend.memory import InMemoryWorkflowMemory, JsonlWorkflowMemory, SQLiteWorkflowMemory, build_workflow_memory
from backend.orchestrator.agent_cluster import AgentCluster
from backend.skills import InMemorySkillSpecStore, PromotionRuleSet
from backend.skills.workflow import workflow_skill_name


class FakeDeviceBackend:
    name = "fake"

    def list_installed_apps(self, limit=80):
        return [{"name": "火豹浏览器", "can_launch": True}]


class WorkflowMemoryTests(unittest.TestCase):
    def test_memory_records_execution_request_and_result(self):
        memory = InMemoryWorkflowMemory(limit=3)

        record = memory.record_execution(
            user_input="扫描本机软件",
            request=ExecutionRequest("local_pc", "list_installed_apps"),
            result=ExecutionResult(True, "已扫描", data=[]),
        )

        self.assertEqual(record.workflow_id, "wf-000001")
        self.assertEqual(memory.recent()[0]["operation"], "list_installed_apps")

    def test_memory_queries_and_stats_by_operation_target_and_device(self):
        memory = InMemoryWorkflowMemory(limit=10)
        memory.record_execution(
            user_input="打开官网",
            request=ExecutionRequest("local_pc", "open_url", {"url": "example.com"}),
            result=ExecutionResult(True, "已打开", metadata={"device": "office-pc"}),
        )
        memory.record_execution(
            user_input="打开失败",
            request=ExecutionRequest("local_pc", "open_url", {"url": "bad"}),
            result=ExecutionResult(False, "失败", error_code="bad_url", metadata={"device": "office-pc"}),
        )
        memory.record_execution(
            user_input="再打开官网",
            request=ExecutionRequest("local_pc", "open_url", {"url": "example.com"}),
            result=ExecutionResult(True, "已打开", metadata={"device": "office-pc"}),
        )

        matched = memory.query(operation="open_url", target="local_pc", device="office-pc", success=True)
        stats = memory.stats()
        candidates = memory.skill_candidates(min_successes=2)

        self.assertEqual(len(matched), 2)
        self.assertEqual(matched[0]["user_input"], "打开官网")
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["success"], 2)
        self.assertEqual(stats["by_device"]["office-pc"], 3)
        self.assertEqual(candidates[0]["operation"], "open_url")
        self.assertEqual(candidates[0]["success_count"], 2)

    def test_agent_cluster_attaches_workflow_record_to_execution_reply(self):
        cluster = AgentCluster(llm_client=lambda _: "ok <emotion:neutral>", device_backend=FakeDeviceBackend())

        reply = cluster.process("扫描本机软件")

        self.assertEqual(reply.metadata["workflow_record"]["operation"], "list_installed_apps")
        self.assertEqual(cluster.workflow_memory_snapshot[0]["user_input"], "扫描本机软件")

    def test_agent_cluster_exposes_workflow_skill_candidates_as_skills(self):
        memory = InMemoryWorkflowMemory(limit=10)
        for _ in range(2):
            memory.record_execution(
                user_input="打开官网",
                request=ExecutionRequest("local_pc", "browser_open_url", {"url": "https://example.com"}),
                result=ExecutionResult(True, "已打开"),
            )
        cluster = AgentCluster(llm_client=lambda _: "ok <emotion:neutral>", workflow_memory=memory)

        skill_names = {skill.name for skill in cluster.available_skills}

        self.assertIn(workflow_skill_name("local_pc", "browser_open_url"), skill_names)

    def test_agent_cluster_reviews_and_persists_workflow_skill_candidates(self):
        memory = InMemoryWorkflowMemory(limit=10)
        for _ in range(3):
            memory.record_execution(
                user_input="打开官网",
                request=ExecutionRequest("local_pc", "browser_open_url", {"url": "https://example.com"}),
                result=ExecutionResult(True, "已打开"),
            )
        store = InMemorySkillSpecStore()
        cluster = AgentCluster(llm_client=lambda _: "ok <emotion:neutral>", workflow_memory=memory, skill_store=store)
        skill_name = workflow_skill_name("local_pc", "browser_open_url")

        outcomes = cluster.review_skill_candidates(
            PromotionRuleSet(min_success_count=2, min_total_count=2, max_failure_rate=0.2, require_human_review=False),
            reviewer="unit-test",
        )

        self.assertEqual(outcomes[0].decision, "promote")
        self.assertEqual(cluster._skill_registry.get(skill_name).metadata["status"], "active")
        self.assertEqual(store.load(skill_name).metadata["promoted_by"], "unit-test")

    def test_jsonl_memory_persists_and_reload_recent_records(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "workflow.jsonl"
            memory = JsonlWorkflowMemory(path, limit=5)
            memory.record_execution(
                user_input="扫描本机软件",
                request=ExecutionRequest("local_pc", "list_installed_apps"),
                result=ExecutionResult(True, "已扫描", data=[]),
            )

            reloaded = JsonlWorkflowMemory(path, limit=5)
            second = reloaded.record_execution(
                user_input="读取剪贴板",
                request=ExecutionRequest("local_pc", "clipboard_read"),
                result=ExecutionResult(True, "已读取", data={}),
            )

        self.assertEqual(reloaded.recent()[0]["operation"], "list_installed_apps")
        self.assertEqual(second.workflow_id, "wf-000002")

    def test_sqlite_memory_persists_queries_stats_and_archive(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "workflow.sqlite3"
            archive_path = Path(tmpdir) / "workflow_archive.jsonl.gz"
            memory = SQLiteWorkflowMemory(path, limit=5)
            memory.record_execution(
                user_input="打开官网",
                request=ExecutionRequest("local_pc", "open_url", {"url": "example.com"}),
                result=ExecutionResult(True, "已打开", metadata={"device": "office-pc"}),
            )
            memory.record_execution(
                user_input="机械臂状态",
                request=ExecutionRequest("openclaw", "status"),
                result=ExecutionResult(False, "失败", error_code="offline", metadata={"device": "arm-1"}),
            )

            reloaded = SQLiteWorkflowMemory(path, limit=5)
            third = reloaded.record_execution(
                user_input="读取剪贴板",
                request=ExecutionRequest("local_pc", "clipboard_read"),
                result=ExecutionResult(True, "已读取", metadata={"device": "office-pc"}),
            )
            archived = reloaded.archive_before(time.time() + 1, archive_path=archive_path)

            self.assertEqual(third.workflow_id, "wf-000003")
            self.assertEqual(reloaded.query(device="office-pc", include_archived=True)[0]["operation"], "open_url")
            self.assertEqual(reloaded.stats(include_archived=True)["by_target"]["local_pc"], 2)
            self.assertEqual(archived, 3)
            self.assertEqual(reloaded.recent(), [])
            with gzip.open(archive_path, "rt", encoding="utf-8") as fp:
                archived_lines = [json.loads(line) for line in fp if line.strip()]
            self.assertEqual(len(archived_lines), 3)

    def test_build_workflow_memory_selects_backend_from_path_suffix(self):
        with TemporaryDirectory() as tmpdir:
            sqlite_memory = build_workflow_memory(Path(tmpdir) / "workflow.sqlite3")
            jsonl_memory = build_workflow_memory(Path(tmpdir) / "workflow.jsonl")

        self.assertIsInstance(sqlite_memory, SQLiteWorkflowMemory)
        self.assertIsInstance(jsonl_memory, JsonlWorkflowMemory)


if __name__ == "__main__":
    unittest.main()