from __future__ import annotations

import unittest

from backend.orchestrator.agent_performance import AgentPerformanceTracker


class AgentPerformanceTests(unittest.TestCase):
    def test_tracker_records_and_ranks_agents(self):
        tracker = AgentPerformanceTracker(window_size=50)
        tracker.record(agent_name="agent_a", domain="vision", success=True, latency_ms=100)
        tracker.record(agent_name="agent_a", domain="vision", success=True, latency_ms=120)
        tracker.record(agent_name="agent_b", domain="ecommerce", success=False, latency_ms=200, error_code="executor_not_found")
        tracker.record(agent_name="agent_b", domain="ecommerce", success=False, latency_ms=180, error_code="executor_not_found")
        ranked = tracker.rank_agents()
        self.assertEqual(ranked[0]["agent_name"], "agent_a")
        self.assertEqual(ranked[0]["success_rate"], 1.0)

    def test_suggest_prompts_for_failing_agent(self):
        tracker = AgentPerformanceTracker()
        for _ in range(5):
            tracker.record(agent_name="bad_agent", domain="test", success=False, error_code="tool_not_registered")
        suggestions = tracker.suggest_prompts("bad_agent")
        self.assertGreater(len(suggestions), 0)

    def test_stats_summary(self):
        tracker = AgentPerformanceTracker()
        tracker.record(agent_name="x", domain="d", success=True)
        s = tracker.stats()
        self.assertEqual(s["total_records"], 1)
        self.assertEqual(s["agents_tracked"], 1)

    def test_empty_tracker_ranks_empty(self):
        tracker = AgentPerformanceTracker()
        self.assertEqual(tracker.rank_agents(), [])

    def test_window_prunes_old_records(self):
        tracker = AgentPerformanceTracker(window_size=5)
        for i in range(10):
            tracker.record(agent_name=f"a{i}", domain="d", success=True)
        self.assertEqual(len(tracker._records), 5)

    def test_suggest_prompts_no_failures(self):
        tracker = AgentPerformanceTracker()
        tracker.record(agent_name="good", domain="d", success=True)
        self.assertEqual(tracker.suggest_prompts("good"), [])
