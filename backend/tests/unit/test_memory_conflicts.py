from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.command_gateway import build_desktop_memory_response, build_desktop_memory_update_response
from backend.app.runtime import SpiritKinRuntime
from backend.memory.conflicts import find_conflict_candidate
from backend.memory.long_term import JsonlLongTermMemoryStore, LongTermMemoryStore
from backend.memory.orchestrator import MemoryOrchestrator
from backend.security.audit import InMemoryAuditLog


def explicit_evidence(text: str) -> dict[str, object]:
    return {
        "source": "user_feedback",
        "attribution": "user_explicit",
        "evidence_quotes": [text],
    }


class MemoryConflictTests(unittest.TestCase):
    def test_shared_topic_polarity_change_creates_conservative_candidate(self):
        candidate = find_conflict_candidate(
            "用户现在不喜欢喝咖啡",
            "用户喜欢喝咖啡",
            new_metadata=explicit_evidence("我现在不喜欢喝咖啡了"),
        )

        self.assertTrue(candidate.is_candidate)
        self.assertGreaterEqual(candidate.confidence, 0.5)
        self.assertIn("咖啡", "".join(candidate.shared_topics))

    def test_unrelated_preferences_do_not_create_conflict(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户喜欢喝咖啡", metadata=explicit_evidence("我喜欢喝咖啡"))
        store.add("preference", "用户不喜欢拥挤的地铁", metadata=explicit_evidence("我不喜欢挤地铁"))

        self.assertEqual(store.list_conflicts(), [])

    def test_candidate_does_not_change_recall_until_explicit_resolution(self):
        store = LongTermMemoryStore()
        old = store.add("preference", "用户喜欢喝咖啡", importance=0.8, metadata=explicit_evidence("我喜欢喝咖啡"))
        new = store.add("preference", "用户现在不喜欢喝咖啡", importance=0.9, metadata=explicit_evidence("我现在不喜欢喝咖啡了"))

        conflicts = store.list_conflicts()
        before = store.recall("咖啡", top_k=5)

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["status"], "pending_review")
        self.assertEqual({item.entry_id for item in before}, {old.entry_id, new.entry_id})

    def test_prefer_new_archives_old_memory_and_prevents_prompt_recall(self):
        store = LongTermMemoryStore()
        old = store.add("preference", "用户喜欢喝咖啡", importance=0.8, metadata=explicit_evidence("我喜欢喝咖啡"))
        new = store.add("preference", "用户现在不喜欢喝咖啡", importance=0.9, metadata=explicit_evidence("我现在不喜欢喝咖啡了"))
        conflict_id = store.list_conflicts()[0]["conflict_id"]

        resolved = store.resolve_conflict(conflict_id, "prefer_new", reason="用户明确更正")
        recalled = store.recall("咖啡", top_k=5)

        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual([item.entry_id for item in recalled], [new.entry_id])
        self.assertEqual(store._entries[old.entry_id].memory_state, "archived")
        self.assertEqual(store._entries[old.entry_id].metadata["superseded_by"], new.entry_id)

    def test_clarification_keeps_both_memories_and_audit_marks_open_item(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户喜欢喝咖啡", metadata=explicit_evidence("我喜欢喝咖啡"))
        store.add("preference", "用户不喜欢喝咖啡", metadata=explicit_evidence("我不喜欢喝咖啡"))
        conflict_id = store.list_conflicts()[0]["conflict_id"]

        store.resolve_conflict(conflict_id, "clarification_needed", reason="可能是场景差异")
        audit = store.audit()

        self.assertEqual(store.list_conflicts()[0]["status"], "clarification_needed")
        self.assertEqual(audit["by_code"]["unresolved_conflict"], 1)
        self.assertEqual(len(store.recall("咖啡", top_k=5)), 2)

    def test_destructive_resolution_requires_reason_and_closed_conflict_is_immutable(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户喜欢喝咖啡", metadata=explicit_evidence("我喜欢喝咖啡"))
        store.add("preference", "用户不喜欢喝咖啡", metadata=explicit_evidence("我不喜欢喝咖啡"))
        conflict_id = store.list_conflicts()[0]["conflict_id"]

        with self.assertRaisesRegex(ValueError, "requires a reason"):
            store.resolve_conflict(conflict_id, "prefer_new")
        store.resolve_conflict(conflict_id, "prefer_new", reason="用户明确更正")
        with self.assertRaisesRegex(ValueError, "already closed"):
            store.resolve_conflict(conflict_id, "prefer_existing", reason="尝试反向覆盖")

    def test_jsonl_round_trip_preserves_conflict_and_resolution_links(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.jsonl"
            first = JsonlLongTermMemoryStore(path)
            old = first.add("preference", "用户喜欢喝咖啡", metadata=explicit_evidence("我喜欢喝咖啡"))
            new = first.add("preference", "用户不喜欢喝咖啡", metadata=explicit_evidence("我不喜欢喝咖啡"))
            conflict_id = first.list_conflicts()[0]["conflict_id"]
            first.resolve_conflict(conflict_id, "prefer_new", reason="用户明确更新偏好")

            second = JsonlLongTermMemoryStore(path)

        conflict = second.list_conflicts()[0]
        self.assertEqual(conflict["status"], "resolved")
        self.assertEqual(second._entries[old.entry_id].metadata["superseded_by"], new.entry_id)
        self.assertEqual(second._entries[old.entry_id].memory_state, "archived")

    def test_audit_reports_missing_evidence_and_unsupported_absolute_claim(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户永远只喜欢咖啡", importance=0.9)

        audit = store.audit()

        self.assertEqual(audit["by_code"]["missing_evidence"], 1)
        self.assertEqual(audit["by_code"]["absolute_overclaim"], 1)

    def test_memory_orchestrator_exposes_management_snapshot_and_evidence(self):
        store = LongTermMemoryStore()
        orchestrator = MemoryOrchestrator(long_term=store)

        orchestrator.record_user_feedback("我喜欢喝咖啡")
        snapshot = orchestrator.memory_management_snapshot()

        self.assertEqual(snapshot["schema_version"], "spiritkin.memory_management.v1")
        self.assertEqual(snapshot["recent_memories"][0]["metadata"]["attribution"], "user_explicit")
        self.assertEqual(snapshot["audit"]["by_severity"]["error"], 0)

    def test_management_conflict_snapshot_includes_both_memories_for_review(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户喜欢喝咖啡", metadata=explicit_evidence("我喜欢喝咖啡"))
        store.add("preference", "用户不喜欢喝咖啡", metadata=explicit_evidence("我不喜欢喝咖啡"))

        conflict = store.management_snapshot()["conflicts"][0]

        self.assertEqual(conflict["source_memory"]["content"], "用户不喜欢喝咖啡")
        self.assertEqual(conflict["target_memory"]["content"], "用户喜欢喝咖啡")
        self.assertEqual(conflict["source_memory"]["metadata"]["evidence_quotes"], ["我不喜欢喝咖啡"])

    def test_authenticated_gateway_helpers_expose_and_resolve_conflict(self):
        store = LongTermMemoryStore()
        store.add("preference", "用户喜欢喝咖啡", metadata=explicit_evidence("我喜欢喝咖啡"))
        store.add("preference", "用户不喜欢喝咖啡", metadata=explicit_evidence("我不喜欢喝咖啡"))
        conflict_id = store.list_conflicts()[0]["conflict_id"]
        runtime = SpiritKinRuntime.__new__(SpiritKinRuntime)
        runtime.memory_orchestrator = MemoryOrchestrator(long_term=store)
        runtime.audit_log = InMemoryAuditLog()
        runtime.emit_runtime_events = False
        runtime.presence = None

        get_status, get_payload = build_desktop_memory_response(runtime)
        post_status, post_payload = build_desktop_memory_update_response(
            runtime,
            {
                "action": "resolve_conflict",
                "conflict_id": conflict_id,
                "resolution": "prefer_new",
                "reason": "用户明确更新偏好",
            },
        )

        self.assertEqual(get_status, 200)
        self.assertEqual(get_payload["memory_management"]["stats"]["pending_conflict_count"], 1)
        self.assertEqual(post_status, 200)
        self.assertEqual(post_payload["conflict"]["resolution"], "prefer_new")
        self.assertEqual(post_payload["memory_management"]["stats"]["pending_conflict_count"], 0)


if __name__ == "__main__":
    unittest.main()
