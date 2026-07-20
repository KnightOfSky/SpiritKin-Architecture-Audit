from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.evaluation.correlation import correlate_replay_to_audit
from backend.evaluation.failure_db import FailureSampleDB, JsonlFailureSampleDB, build_failure_sample_db
from backend.evaluation.replay import (
    ReplayRecord,
    ReplayReport,
    build_replay_report,
    build_replay_report_with_audit_correlation,
)
from backend.evaluation.skill_verifier import (
    SkillVerificationPolicy,
    verify_all_candidate_readiness,
    verify_all_candidates,
    verify_skill_candidate,
    verify_skill_candidate_readiness,
)
from backend.executors.base import ExecutionRequest
from backend.skills.base import SkillRegistry, SkillSpec
from backend.tools.base import ToolSpec
from backend.tools.registry import ToolRegistry


class EvalPhase2Tests(unittest.TestCase):
    def test_failure_sample_db_deduplicates_by_key(self):
        db = FailureSampleDB()
        s1 = db.record(tool_name="test.tool", target="pc", operation="do", error_code="ERR")
        s2 = db.record(tool_name="test.tool", target="pc", operation="do", error_code="ERR")
        self.assertEqual(s1.sample_id, s2.sample_id)
        self.assertEqual(s2.observed_count, 2)

    def test_failure_sample_db_queries_by_error_code(self):
        db = FailureSampleDB()
        db.record(tool_name="a", target="pc", operation="x", error_code="E1")
        db.record(tool_name="b", target="pc", operation="y", error_code="E2")
        results = db.query(error_code="E1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].tool_name, "a")

    def test_jsonl_failure_sample_db_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "failures.jsonl"
            db1 = JsonlFailureSampleDB(path)
            db1.record(tool_name="t", target="pc", operation="o", error_code="E")
            db2 = JsonlFailureSampleDB(path)
            results = db2.query(error_code="E")
            self.assertEqual(len(results), 1)

    def test_build_failure_sample_db_factory(self):
        self.assertIsInstance(build_failure_sample_db(None), FailureSampleDB)

    def test_verify_skill_candidate_with_all_tools_registered(self):
        from backend.skills.base import SkillStepSpec

        spec = SkillSpec(
            name="test.skill",
            description="test",
            steps=(SkillStepSpec(tool_name="tool.a", arguments={}), SkillStepSpec(tool_name="tool.b", arguments={})),
            tool_allowlist=("tool.a", "tool.b"),
        )
        registry = ToolRegistry()
        registry.register_many([])
        from backend.tools.base import ExecutionTool
        registry.register(ExecutionTool(ToolSpec(name="tool.a", target="pc", operation="a", description="a")))
        registry.register(ExecutionTool(ToolSpec(name="tool.b", target="pc", operation="b", description="b")))

        result = verify_skill_candidate(spec, registry)
        self.assertTrue(result.passed)

    def test_verify_skill_candidate_with_missing_tool(self):
        from backend.skills.base import SkillStepSpec

        spec = SkillSpec(
            name="bad.skill",
            description="bad",
            steps=(SkillStepSpec(tool_name="missing.tool", arguments={}),),
            tool_allowlist=("missing.tool",),
        )
        registry = ToolRegistry()
        result = verify_skill_candidate(spec, registry)
        self.assertFalse(result.passed)
        self.assertGreater(len(result.errors), 0)

    def test_verify_all_candidates_only_checks_candidates(self):
        from backend.skills.base import SkillStepSpec

        registry = SkillRegistry()
        registry.register(
            SkillSpec(
                name="candidate.x",
                description="c",
                steps=(SkillStepSpec(tool_name="t1", arguments={}),),
                metadata={"status": "candidate"},
            )
        )
        registry.register(
            SkillSpec(
                name="active.y",
                description="a",
                steps=(SkillStepSpec(tool_name="t2", arguments={}),),
                metadata={"status": "active"},
            )
        )
        tool_registry = ToolRegistry()
        from backend.tools.base import ExecutionTool
        tool_registry.register(ExecutionTool(ToolSpec(name="t1", target="pc", operation="x", description="x")))
        tool_registry.register(ExecutionTool(ToolSpec(name="t2", target="pc", operation="y", description="y")))

        results = verify_all_candidates(registry, tool_registry)
        self.assertEqual(len(results), 1)

    def test_verify_skill_candidate_readiness_applies_replay_threshold(self):
        from backend.skills.base import SkillStepSpec
        from backend.tools.base import ExecutionTool

        spec = SkillSpec(
            name="candidate.open",
            description="open",
            steps=(SkillStepSpec(tool_name="browser.open_url", arguments={"url": "https://example.com"}),),
            tool_allowlist=("browser.open_url",),
            metadata={"status": "candidate"},
        )
        tool_registry = ToolRegistry()
        tool_registry.register(ExecutionTool(ToolSpec(name="browser.open_url", target="local_pc", operation="browser_open_url", description="open")))
        report = ReplayReport(
            total=1,
            replayable_count=0,
            expected_success_count=1,
            expected_failure_count=0,
            high_risk_count=0,
            records=(ReplayRecord("wf-1", "open", ExecutionRequest("local_pc", "browser_open_url", {}), True, False, tool_name="browser.open_url"),),
        )

        result = verify_skill_candidate_readiness(spec, tool_registry, replay_report=report, policy=SkillVerificationPolicy(min_replayable_rate=1.0))

        self.assertFalse(result.passed)
        self.assertIn("replayable_rate", result.errors[0])
        self.assertEqual(result.metadata["related_replay_records"], 1)

    def test_verify_all_candidate_readiness_requires_audit_when_configured(self):
        from backend.skills.base import SkillStepSpec
        from backend.tools.base import ExecutionTool

        registry = SkillRegistry()
        registry.register(SkillSpec(name="candidate.audit", description="audit", steps=(SkillStepSpec(tool_name="tool.a"),), metadata={"status": "candidate"}))
        tool_registry = ToolRegistry()
        tool_registry.register(ExecutionTool(ToolSpec(name="tool.a", target="pc", operation="a", description="a")))
        report = ReplayReport(
            total=1,
            replayable_count=1,
            expected_success_count=1,
            expected_failure_count=0,
            high_risk_count=0,
            records=(ReplayRecord("wf-1", "a", ExecutionRequest("pc", "a", {}), True, True, tool_name="tool.a"),),
        )

        results = verify_all_candidate_readiness(registry, tool_registry, replay_report=report, policy=SkillVerificationPolicy(require_audit_correlation=True))

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].passed)
        self.assertIn("审计日志关联", results[0].actual_result)

    def test_correlate_replay_to_audit_finds_exact_match(self):
        record = ReplayRecord(
            workflow_id="wf-001",
            user_input="test",
            request=ExecutionRequest(target="pc", operation="do", params={}),
            expected_success=True,
            replayable=True,
            tool_name="t",
        )
        from backend.security.audit import InMemoryAuditLog
        audit = InMemoryAuditLog()
        audit.record("execution_result", target="pc", operation="do")
        correlations = correlate_replay_to_audit(type("R", (), {"records": [record]})(), audit)
        self.assertEqual(len(correlations), 1)
        self.assertEqual(correlations[0].match_type, "exact")

    def test_build_replay_report_with_audit_correlation(self):
        from backend.security.audit import InMemoryAuditLog
        audit = InMemoryAuditLog()
        audit.record("execution_result", target="local_pc", operation="browser_open_url")
        records = [{"workflow_id": "wf-001", "user_input": "open", "target": "local_pc", "operation": "browser_open_url", "params": {"url": "x"}, "success": True}]
        tool = ToolSpec(name="browser.open_url", target="local_pc", operation="browser_open_url", description="open")
        report = build_replay_report_with_audit_correlation(records, tools=[tool], audit_log=audit)
        self.assertEqual(report.total, 1)
        correlated = [r for r in report.records if r.correlated_audit_id]
        self.assertTrue(len(correlated) > 0)

    def test_replay_record_snapshot_includes_correlated_audit_id(self):
        record = ReplayRecord(
            workflow_id="wf-001",
            user_input="test",
            request=ExecutionRequest(target="pc", operation="do", params={}),
            expected_success=True,
            replayable=True,
            correlated_audit_id="audit-001",
        )
        snap = record.snapshot()
        self.assertEqual(snap["correlated_audit_id"], "audit-001")

    def test_build_replay_report_includes_failure_sample_ids(self):
        records = [{"workflow_id": "wf-001", "user_input": "bad", "target": "pc", "operation": "fail", "params": {}, "success": False, "error_code": "ERR"}]
        db = FailureSampleDB()
        db.record(tool_name="", target="pc", operation="fail", error_code="ERR")
        tools = [ToolSpec(name="fail", target="pc", operation="fail", description="f")]
        report = build_replay_report(records, tools=tools, failure_samples=db)
        self.assertIn("ERR", [r.metadata.get("source_error_code", "") for r in report.records])
