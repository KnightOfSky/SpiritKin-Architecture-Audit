import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.evaluation import build_replay_report
from backend.executors.base import ExecutionRequest, ExecutionResult
from backend.memory import JsonlWorkflowMemory
from backend.tools import ToolSpec


class ReplayHarnessTests(unittest.TestCase):
    def test_build_replay_report_reconstructs_execution_requests(self):
        records = [
            {
                "workflow_id": "wf-000001",
                "user_input": "打开官网",
                "target": "local_pc",
                "operation": "browser_open_url",
                "params": {"url": "https://example.com"},
                "success": True,
            }
        ]
        tools = [ToolSpec(name="browser.open_url", description="打开网页", target="local_pc", operation="browser_open_url", risk_level="medium")]

        report = build_replay_report(records, tools=tools, require_known_tool=True)

        self.assertEqual(report.total, 1)
        self.assertEqual(report.replayable_count, 1)
        self.assertEqual(report.records[0].request.params["url"], "https://example.com")
        self.assertEqual(report.records[0].tool_name, "browser.open_url")

    def test_build_replay_report_flags_missing_known_tool_and_high_risk(self):
        records = [
            {"workflow_id": "wf-1", "target": "feishu", "operation": "send_message", "params": {}, "success": True},
            {"workflow_id": "wf-2", "target": "unknown", "operation": "missing", "params": {}, "success": False},
        ]
        tools = [ToolSpec(name="feishu.message.send", description="发消息", target="feishu", operation="send_message", risk_level="high")]

        report = build_replay_report(records, tools=tools, require_known_tool=True)

        self.assertEqual(report.high_risk_count, 1)
        self.assertEqual(report.replayable_count, 1)
        self.assertIn("tool_not_registered", report.records[1].issues)

    def test_replay_workflow_memory_script_outputs_json_report(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "workflow.jsonl"
            memory = JsonlWorkflowMemory(path)
            memory.record_execution(
                user_input="打开官网",
                request=ExecutionRequest("local_pc", "browser_open_url", {"url": "https://example.com"}),
                result=ExecutionResult(True, "已打开"),
            )

            result = subprocess.run(
                [sys.executable, "scripts/replay_workflow_memory.py", "--path", str(path), "--require-known-tool"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["replayable_count"], 1)

    def test_replay_workflow_memory_script_can_write_report_file(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "workflow.jsonl"
            report_path = Path(tmpdir) / "reports" / "replay.json"
            memory = JsonlWorkflowMemory(path)
            memory.record_execution(
                user_input="打开官网",
                request=ExecutionRequest("local_pc", "browser_open_url", {"url": "https://example.com"}),
                result=ExecutionResult(True, "已打开"),
            )

            result = subprocess.run(
                [sys.executable, "scripts/replay_workflow_memory.py", "--path", str(path), "--output-report-to", str(report_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )

            self.assertEqual(result.returncode, 0)
            self.assertTrue(report_path.exists())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["total"], 1)


if __name__ == "__main__":
    unittest.main()