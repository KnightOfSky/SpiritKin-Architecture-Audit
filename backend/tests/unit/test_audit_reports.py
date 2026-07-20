import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.audit_reports import generate_audit_report
from backend.app.command_gateway import build_desktop_audit_report_update_response


def _record(index: int, *, session_id: str = "session-a", success: bool = True, risk: str = "low") -> dict:
    return {
        "audit_id": f"audit-{index:06d}",
        "event_type": "tool_execution",
        "actor": "unit-test",
        "target": "remote:worker-1" if index == 2 else "local_pc",
        "operation": "demo",
        "risk_level": risk,
        "success": success,
        "timestamp": 1_700_000_000 + index,
        "metadata": {"session_id": session_id, "node_id": "worker-1" if index == 2 else ""},
    }


class AuditReportTests(unittest.TestCase):
    def test_incremental_report_uses_cursor_without_rereading_prior_lines(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_path = root / "audit.jsonl"
            cursor_path = root / "cursor.json"
            report_path = root / "latest.json"
            audit_path.write_text("\n".join(json.dumps(_record(index, success=index != 2, risk="high" if index == 2 else "low")) for index in (1, 2)) + "\n", encoding="utf-8")

            first = generate_audit_report(audit_log_path=audit_path, cursor_path=cursor_path, report_path=report_path)
            with audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(_record(3, session_id="session-b")) + "\n")
            second = generate_audit_report(audit_log_path=audit_path, cursor_path=cursor_path, report_path=report_path)
            third = generate_audit_report(audit_log_path=audit_path, cursor_path=cursor_path, report_path=report_path)

        self.assertEqual(first["cursor"]["read_records"], 2)
        self.assertEqual(second["cursor"]["read_records"], 1)
        self.assertEqual(third["cursor"]["read_records"], 0)
        self.assertEqual(second["total"], 3)
        self.assertEqual(second["failure_count"], 1)
        self.assertEqual(second["high_risk_count"], 1)
        self.assertEqual(second["remote_count"], 1)
        self.assertEqual(second["sessions"]["session-a"]["total"], 2)
        self.assertEqual(second["sessions"]["session-b"]["total"], 1)

    def test_audit_report_endpoint_returns_frontend_contract(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_path = root / "audit.jsonl"
            audit_path.write_text(json.dumps(_record(1)) + "\n", encoding="utf-8")
            env = {
                "SPIRITKIN_AUDIT_LOG_PATH": str(audit_path),
                "SPIRITKIN_AUDIT_REPORT_CURSOR_PATH": str(root / "cursor.json"),
                "SPIRITKIN_AUDIT_REPORT_PATH": str(root / "latest.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                status, payload = build_desktop_audit_report_update_response({"action": "generate"})

        self.assertEqual(status, 200)
        report = payload["audit_report"]
        self.assertEqual(report["schema_version"], "spiritkin.audit_report.v1")
        for field in ("total", "high_risk_count", "failure_count", "remote_count", "records", "sessions", "cursor"):
            self.assertIn(field, report)
        self.assertEqual(report["records"][0]["audit_id"], "audit-000001")


if __name__ == "__main__":
    unittest.main()
