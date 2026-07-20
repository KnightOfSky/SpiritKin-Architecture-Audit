from __future__ import annotations

import unittest

from backend.evaluation.failure_db import FailureSampleDB
from backend.evaluation.self_improvement import SelfImprovementLoop
from backend.evaluation.trajectory import TrajectoryAnalyzer, TrajectoryReport, TrajectoryStep
from backend.orchestrator.agent_performance import AgentPerformanceTracker


class TrajectoryTests(unittest.TestCase):
    def test_detect_bottlenecks(self):
        analyzer = TrajectoryAnalyzer()
        report = TrajectoryReport(
            user_input="test",
            steps=[
                TrajectoryStep(stage="planner", detail="routed to executor", success=True),
                TrajectoryStep(stage="executor", detail="execute failed", success=False, error_code="executor_failed"),
                TrajectoryStep(stage="executor", detail="execute failed again", success=False, error_code="executor_failed"),
            ],
            overall_success=False,
            bottleneck_stage="executor",
        )
        analyzer.record_trajectory(report)
        bottlenecks = analyzer.detect_bottlenecks()
        self.assertGreaterEqual(len(bottlenecks), 1)
        self.assertEqual(bottlenecks[0]["stage"], "executor")
        self.assertEqual(bottlenecks[0]["failure_count"], 2)

    def test_generate_eval_cases_from_failures(self):
        analyzer = TrajectoryAnalyzer()
        analyzer.record_trajectory(TrajectoryReport(
            user_input="open firefox",
            steps=[TrajectoryStep(stage="executor", detail="failed", success=False, error_code="executor_failed")],
            overall_success=False,
            bottleneck_stage="executor",
        ))
        cases = analyzer.generate_eval_cases()
        self.assertGreaterEqual(len(cases), 1)
        self.assertEqual(cases[0]["user_input"], "open firefox")

    def test_generate_eval_cases_skips_successful(self):
        analyzer = TrajectoryAnalyzer()
        analyzer.record_trajectory(TrajectoryReport(
            user_input="ok", steps=[TrajectoryStep(stage="planner", detail="ok", success=True)], overall_success=True,
        ))
        cases = analyzer.generate_eval_cases()
        self.assertEqual(len(cases), 0)

    def test_stats_include_bottlenecks(self):
        analyzer = TrajectoryAnalyzer()
        analyzer.record_trajectory(TrajectoryReport(
            user_input="x", steps=[TrajectoryStep(stage="tool", detail="bad", success=False)], overall_success=False,
        ))
        s = analyzer.stats()
        self.assertEqual(s["total_trajectories"], 1)
        self.assertEqual(s["failure_count"], 1)
        self.assertGreater(len(s["bottlenecks"]), 0)

    def test_trajectory_step_fields(self):
        step = TrajectoryStep(stage="planner", detail="test", success=True, latency_ms=42.0, error_code="")
        self.assertTrue(step.success)
        self.assertEqual(step.latency_ms, 42.0)

    def test_multiple_trajectories_bottleneck_aggregation(self):
        analyzer = TrajectoryAnalyzer()
        for _ in range(3):
            analyzer.record_trajectory(TrajectoryReport(
                user_input="x",
                steps=[TrajectoryStep(stage="tool", detail="fail", success=False, error_code="tool_failed")],
                overall_success=False,
            ))
        bottlenecks = analyzer.detect_bottlenecks()
        self.assertEqual(bottlenecks[0]["failure_count"], 3)

    def test_self_improvement_loop_generates_actions(self):
        tracker = AgentPerformanceTracker()
        for _ in range(3):
            tracker.record(agent_name="bad_agent", domain="tools", success=False, error_code="executor_failed")
        analyzer = TrajectoryAnalyzer()
        for _ in range(2):
            analyzer.record_trajectory(TrajectoryReport(
                user_input="open app",
                steps=[TrajectoryStep(stage="executor", detail="failed", success=False, error_code="executor_failed")],
                overall_success=False,
                bottleneck_stage="executor",
            ))
        failure_db = FailureSampleDB()
        failure_db.record(target="local_pc", operation="open", error_code="executor_failed")
        failure_db.record(target="local_pc", operation="open", error_code="executor_failed")

        report = SelfImprovementLoop(performance_tracker=tracker, trajectory_analyzer=analyzer, failure_db=failure_db).build_report()

        self.assertGreaterEqual(len(report.actions), 3)
        self.assertGreaterEqual(len(report.eval_cases), 1)
        self.assertIn("actions", report.snapshot())
