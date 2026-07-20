from __future__ import annotations

import unittest

from backend.evaluation.self_improvement import SelfImprovementLoop
from backend.evaluation.trajectory import TrajectoryAnalyzer, TrajectoryReport, TrajectoryStep
from backend.orchestrator.agent_performance import AgentPerformanceTracker


class SelfTrainingLoopTests(unittest.TestCase):
    def test_report_includes_self_training_package_from_failed_trajectory(self):
        analyzer = TrajectoryAnalyzer()
        analyzer.record_trajectory(
            TrajectoryReport(
                user_input="打开不存在的软件",
                steps=[
                    TrajectoryStep(
                        stage="executor",
                        detail="missing executor",
                        success=False,
                        error_code="executor_not_found",
                    )
                ],
                overall_success=False,
                bottleneck_stage="executor",
            )
        )

        report = SelfImprovementLoop(trajectory_analyzer=analyzer).build_report()
        snapshot = report.snapshot()

        self.assertIsNotNone(snapshot["training_package"])
        examples = snapshot["training_package"]["examples"]
        self.assertEqual(examples[0]["source"], "trajectory")
        self.assertEqual(examples[0]["task_type"], "regression_eval")
        self.assertIn("打开不存在的软件", examples[0]["input_text"])

    def test_self_training_package_contains_routing_feedback_for_weak_agent(self):
        tracker = AgentPerformanceTracker()
        for _ in range(3):
            tracker.record(agent_name="unstable_agent", domain="execution", success=False, error_code="tool_not_registered")

        package = SelfImprovementLoop(performance_tracker=tracker).build_self_training_package()
        examples = package.snapshot()["examples"]

        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["source"], "performance")
        self.assertEqual(examples[0]["task_type"], "routing_feedback")
        self.assertIn("unstable_agent", examples[0]["input_text"])


if __name__ == "__main__":
    unittest.main()
