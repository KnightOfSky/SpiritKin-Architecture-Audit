from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.skills.base import SkillRegistry, SkillSpec
from backend.skills.persistence import InMemorySkillSpecStore, JsonlSkillSpecStore, build_skill_store
from backend.skills.promotion import (
    CandidateReview,
    PromotionRuleSet,
    apply_candidate_review,
    bump_version,
    evaluate_candidate,
    review_skill_candidates,
)
from backend.skills.workflow import build_promotion_metric_for_candidate, build_workflow_skill_specs
from backend.tools.base import ToolSpec


class SkillPromotionTests(unittest.TestCase):
    def test_bump_version_patch(self):
        self.assertEqual(bump_version("0.1.0-candidate", "patch"), "0.1.1")
        self.assertEqual(bump_version("1.2.3", "patch"), "1.2.4")

    def test_bump_version_minor_strips_candidate(self):
        self.assertEqual(bump_version("0.1.0-candidate", "minor"), "0.2.0")
        self.assertEqual(bump_version("1.0.0", "minor"), "1.1.0")

    def test_bump_version_major(self):
        self.assertEqual(bump_version("0.1.0", "major"), "1.0.0")
        self.assertEqual(bump_version("2.3.4", "major"), "3.0.0")

    def test_evaluate_candidate_promotes_high_success(self):
        skill = SkillSpec(
            name="test.x",
            description="test",
            trigger_intents=("test",),
            metadata={"source": "workflow_memory", "status": "candidate", "success_count": 8, "total_count": 10, "success_rate": 0.8, "last_seen": time.time()},
        )
        rules = PromotionRuleSet(min_success_count=3, min_total_count=5, max_failure_rate=0.30, require_human_review=False)
        review = evaluate_candidate(skill, rules)
        self.assertEqual(review.decision, "promote")

    def test_evaluate_candidate_rejects_high_failure(self):
        skill = SkillSpec(
            name="bad.x",
            description="bad",
            metadata={"source": "workflow_memory", "status": "candidate", "success_count": 3, "total_count": 10, "success_rate": 0.3, "last_seen": time.time()},
        )
        rules = PromotionRuleSet(min_success_count=3, min_total_count=5, max_failure_rate=0.30)
        review = evaluate_candidate(skill, rules)
        self.assertEqual(review.decision, "demote")

    def test_evaluate_candidate_rejects_non_candidate(self):
        skill = SkillSpec(
            name="active.x",
            description="active",
            metadata={"status": "active", "success_count": 10, "total_count": 10, "success_rate": 1.0},
        )
        review = evaluate_candidate(skill, PromotionRuleSet())
        self.assertEqual(review.decision, "reject")

    def test_evaluate_candidate_requires_human_review(self):
        skill = SkillSpec(
            name="needs.review",
            description="needs review",
            metadata={"source": "workflow_memory", "status": "candidate", "success_count": 8, "total_count": 10, "success_rate": 0.8, "last_seen": time.time()},
        )
        rules = PromotionRuleSet(min_success_count=3, min_total_count=5, max_failure_rate=0.30, require_human_review=True)
        review = evaluate_candidate(skill, rules)
        self.assertEqual(review.decision, "pending")
        self.assertIn("人工确认", review.reason)

    def test_evaluate_candidate_fails_on_insufficient_total(self):
        skill = SkillSpec(
            name="few.x",
            description="few",
            metadata={"source": "workflow_memory", "status": "candidate", "success_count": 2, "total_count": 3, "success_rate": 0.67},
        )
        rules = PromotionRuleSet(min_success_count=2, min_total_count=5)
        review = evaluate_candidate(skill, rules)
        self.assertEqual(review.decision, "pending")

    def test_jsonl_skill_store_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skills.jsonl"
            store = JsonlSkillSpecStore(path)
            spec = SkillSpec(
                name="test.skill",
                description="test",
                trigger_intents=("test",),
                version="0.1.0-candidate",
                metadata={"status": "candidate", "success_count": 5},
            )
            store.save(spec)
            loaded = store.load("test.skill")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.name, "test.skill")
            self.assertEqual(loaded.version, "0.1.0-candidate")
            self.assertEqual(loaded.metadata["status"], "candidate")

    def test_jsonl_skill_store_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skills.jsonl"
            store1 = JsonlSkillSpecStore(path)
            store1.save(SkillSpec(name="a", description="a", metadata={"status": "candidate"}))
            store1.save(SkillSpec(name="b", description="b", metadata={"status": "active"}))
            store2 = JsonlSkillSpecStore(path)
            names = {s.name for s in store2.list_all()}
            self.assertIn("a", names)
            self.assertIn("b", names)

    def test_build_skill_store_factory(self):
        self.assertIsInstance(build_skill_store(None), InMemorySkillSpecStore)

    def test_skill_registry_list_candidates(self):
        registry = SkillRegistry()
        registry.register(SkillSpec(name="c1", description="c", metadata={"status": "candidate"}))
        registry.register(SkillSpec(name="a1", description="a", metadata={"status": "active"}))
        registry.register(SkillSpec(name="c2", description="c2", metadata={"status": "candidate"}))
        candidates = registry.list_candidates()
        self.assertEqual(len(candidates), 2)
        self.assertIn("c1", {s.name for s in candidates})

    def test_skill_registry_lists_active_skills(self):
        registry = SkillRegistry()
        registry.register(SkillSpec(name="c1", description="c", metadata={"status": "candidate"}))
        registry.register(SkillSpec(name="a1", description="a", metadata={"status": "active"}))

        self.assertEqual([s.name for s in registry.list_active()], ["a1"])

    def test_skill_registry_replace_updates_existing(self):
        registry = SkillRegistry()
        registry.register(SkillSpec(name="x", description="old", version="0.1.0"))
        registry.replace(SkillSpec(name="x", description="new", version="0.2.0"))
        self.assertEqual(registry.get("x").version, "0.2.0")

    def test_skill_registry_unregister(self):
        registry = SkillRegistry()
        registry.register(SkillSpec(name="x", description="x"))
        self.assertTrue(registry.unregister("x"))
        self.assertIsNone(registry.get("x"))
        self.assertFalse(registry.unregister("nonexistent"))

    def test_skill_registry_load_from_store(self):
        store = InMemorySkillSpecStore()
        store.save(SkillSpec(name="s1", description="s1", metadata={"status": "active"}))
        store.save(SkillSpec(name="s2", description="s2", metadata={"status": "candidate"}))
        registry = SkillRegistry()
        registry.load_from_store(store)
        self.assertEqual(len(registry.list_specs()), 2)

    def test_build_workflow_skill_specs_skips_existing(self):
        tool = ToolSpec(name="browser.open_url", target="local_pc", operation="browser_open_url", description="open", risk_level="medium", schema={"url": "string"})
        candidate = {"target": "local_pc", "operation": "browser_open_url", "success_count": 5, "total_count": 6, "success_rate": 0.83, "example_params": {"url": "https://test.com"}}
        name = "workflow.local_pc.browser_open_url"
        specs = build_workflow_skill_specs([candidate], [tool], existing_skill_names={name})
        self.assertEqual(len(specs), 0)

    def test_build_promotion_metric_computes_recent_stats(self):
        candidate = {"target": "local_pc", "operation": "browser_open_url", "success_count": 3, "total_count": 4}
        records = [
            {"target": "local_pc", "operation": "browser_open_url", "success": True},
            {"target": "local_pc", "operation": "browser_open_url", "success": False},
            {"target": "local_pc", "operation": "screen_capture", "success": True},
        ]
        metrics = build_promotion_metric_for_candidate(candidate, records)
        self.assertEqual(metrics["recent_total"], 2)
        self.assertEqual(metrics["recent_successes"], 1)
        self.assertEqual(metrics["recent_success_rate"], 0.5)

    def test_apply_candidate_review_promotes_to_active_with_version_and_history(self):
        skill = SkillSpec(name="workflow.local_pc.browser_open_url", description="open", version="0.1.0-candidate", metadata={"status": "candidate"})
        review = CandidateReview(candidate_name=skill.name, reviewer="tester", decision="promote", reason="stable")

        promoted = apply_candidate_review(skill, review)

        self.assertEqual(promoted.metadata["status"], "active")
        self.assertEqual(promoted.version, "0.2.0")
        self.assertEqual(promoted.metadata["promoted_by"], "tester")
        self.assertEqual(promoted.metadata["review_history"][0]["decision"], "promote")

    def test_review_skill_candidates_updates_registry_and_store(self):
        registry = SkillRegistry()
        registry.register(
            SkillSpec(
                name="workflow.local_pc.browser_open_url",
                description="open",
                version="0.1.0-candidate",
                metadata={"status": "candidate", "success_count": 8, "total_count": 10, "success_rate": 0.8, "last_seen": time.time()},
            )
        )
        store = InMemorySkillSpecStore()

        outcomes = review_skill_candidates(
            registry,
            PromotionRuleSet(min_success_count=3, min_total_count=5, max_failure_rate=0.3, require_human_review=False),
            store=store,
            reviewer="rules@test",
        )

        self.assertEqual(outcomes[0].decision, "promote")
        self.assertEqual(registry.get("workflow.local_pc.browser_open_url").metadata["status"], "active")
        self.assertEqual(store.load("workflow.local_pc.browser_open_url").metadata["promoted_by"], "rules@test")
