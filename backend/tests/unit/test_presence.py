from __future__ import annotations

import time
import unittest

from backend.runtime.events.persistence import EventPersistence, JsonlEventPersistence, build_event_persistence
from backend.orchestrator.presence import PresenceManager


class PresenceTests(unittest.TestCase):
    def test_presence_manager_detects_idle(self):
        pm = PresenceManager(idle_threshold_seconds=1.0)
        pm.on_activity()
        self.assertFalse(pm.is_idle())
        pm.last_activity_at = time.time() - 2.0
        self.assertTrue(pm.is_idle())

    def test_presence_manager_returns_idle_action(self):
        pm = PresenceManager(idle_threshold_seconds=0.0)
        pm.last_activity_at = time.time() - 310
        action = pm.get_idle_action()
        self.assertIsNotNone(action)
        self.assertIsInstance(action, str)

    def test_presence_manager_snapshot(self):
        pm = PresenceManager()
        snap = pm.snapshot()
        self.assertIn("idle_seconds", snap)
        self.assertIn("is_idle", snap)

    def test_presence_manager_records_context_and_suggests_help(self):
        pm = PresenceManager()
        pm.record_context("task", "完成 P2 持续感知", confidence=0.9)
        pm.last_activity_at = time.time() - 90
        snap = pm.snapshot()

        self.assertIn("task", snap["active_contexts"])
        self.assertIn("拆下一步", snap["proactive_suggestion"])
        self.assertEqual(snap["recent_observations"][-1]["confidence"], 0.9)

    def test_event_persistence_records_and_replays(self):
        ep = EventPersistence()
        ep.record("user_input", {"text": "hello"})
        ep.record("assistant.message", {"text": "hi"})
        recent = ep.recent(limit=10)
        self.assertEqual(len(recent), 2)

    def test_event_persistence_replay_session(self):
        ep = EventPersistence()
        sid = ep._current_session_id
        ep.record("test", {"a": 1})
        events = ep.replay_session(sid)
        self.assertEqual(len(events), 1)

    def test_event_persistence_stats(self):
        ep = EventPersistence()
        ep.record("user_input", {})
        ep.record("user_input", {})
        s = ep.stats()
        self.assertEqual(s["by_type"]["user_input"], 2)

    def test_jsonl_event_persistence_round_trip(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            ep1 = JsonlEventPersistence(path)
            ep1.record("test", {"x": 1})
            ep2 = JsonlEventPersistence(path)
            self.assertGreater(ep2.stats()["total"], 0)

    def test_build_event_persistence_factory(self):
        self.assertIsInstance(build_event_persistence(None), EventPersistence)
