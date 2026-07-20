from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.memory.long_term import JsonlLongTermMemoryStore, LongTermMemoryStore, build_long_term_memory
from backend.memory.personality import PersonalityState, PersonalityStore, build_personality_store


class LongTermMemoryTests(unittest.TestCase):
    def test_add_and_recall_by_keyword(self):
        store = LongTermMemoryStore()
        store.add("conversation", "用户询问了 Python 性能优化", importance=0.8)
        store.add("conversation", "讨论了机器学习部署方案", importance=0.5)
        results = store.recall("Python", top_k=5)
        self.assertEqual(len(results), 1)
        self.assertIn("Python", results[0].content)

    def test_recall_by_category(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户偏好简短回复", importance=0.9)
        store.add("conversation", "普通对话内容", importance=0.3)
        results = store.recall(category="preference")
        self.assertEqual(len(results), 1)

    def test_recall_matches_chinese_semantics_without_spaces(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户喜欢简短直接的回答", importance=0.9)
        store.add("conversation", "昨天讨论了桌面窗口布局", importance=0.8)

        results = store.recall("回答尽量简短", top_k=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].category, "preference")
        self.assertGreater(results[0].activation, 0.5)
        self.assertEqual(results[0].memory_state, "active")
        self.assertEqual(results[0].access_count, 1)

    def test_recall_respects_min_importance(self):
        store = LongTermMemoryStore()
        store.add("conversation", "普通内容", importance=0.2)
        store.add("conversation", "重要内容", importance=0.9)
        results = store.recall(min_importance=0.5)
        self.assertEqual(len(results), 1)
        self.assertIn("重要内容", results[0].content)

    def test_decay_importance_reduces_stale_entries(self):
        store = LongTermMemoryStore()
        store.add("conversation", "stale content", importance=0.8)
        eid = list(store._entries.keys())[0]
        store._entries[eid] = store._entries[eid].__class__(
            entry_id=eid, category="conversation", content="stale content",
            importance=0.8, timestamp=time.time() - 86400 * 60, last_recalled=time.time() - 86400 * 60,
        )
        decayed = store.decay_importance(days_threshold=30)
        self.assertGreaterEqual(decayed, 1)

    def test_consolidate_merges_similar(self):
        store = LongTermMemoryStore()
        store.add("conversation", "今天天气很好适合出门散步", importance=0.5)
        store.add("conversation", "今天天气很好适合出去走走", importance=0.5)
        before = len(list(store._entries.values()))
        store.consolidate(similarity_threshold=0.4)
        after = len(list(store._entries.values()))
        self.assertLessEqual(after, before + 1)

    def test_jsonl_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ltm.jsonl"
            store1 = JsonlLongTermMemoryStore(path)
            store1.add("conversation", "测试持久化", importance=0.7)
            store2 = JsonlLongTermMemoryStore(path)
            results = store2.recall("持久化")
            self.assertEqual(len(results), 1)

    def test_personality_state_mood_transition(self):
        state = PersonalityState(mood="neutral")
        state.mood_transition("user_praise")
        self.assertEqual(state.mood, "happy")
        state.mood_transition("execution_failure")
        self.assertIn(state.mood, ("neutral", "curious", "tired"))

    def test_personality_store_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "personality.json"
            store1 = PersonalityStore(path)
            store1.state.interaction_count = 42
            store1.state.mood = "excited"
            store1.save()
            store2 = PersonalityStore(path)
            self.assertEqual(store2.state.interaction_count, 42)
            self.assertEqual(store2.state.mood, "excited")

    def test_personality_context_string(self):
        state = PersonalityState(mood="curious", interaction_count=10)
        state.set_preference("nickname", "小明")
        ctx = state.personality_context()
        self.assertIn("curious", ctx)
        self.assertIn("小明", ctx)
        self.assertIn("陪伴模式", ctx)

    def test_personality_lpm_state_reports_mode_and_energy(self):
        state = PersonalityState(mood="excited", interaction_count=20, successful_actions=8, failed_actions=2)
        lpm = state.lpm_state()

        self.assertEqual(lpm["mode"], "active_companion")
        self.assertGreater(lpm["energy"], 0.5)
        self.assertEqual(lpm["reliability"], 0.8)

    def test_personality_companion_mode_enters_repair_support_after_failures(self):
        state = PersonalityState(mood="neutral", interaction_count=5, successful_actions=1, failed_actions=3)
        self.assertEqual(state.companion_mode(), "repair_support")

    def test_build_factories(self):
        self.assertIsInstance(build_long_term_memory(None), LongTermMemoryStore)
        self.assertIsInstance(build_personality_store(None), PersonalityStore)
