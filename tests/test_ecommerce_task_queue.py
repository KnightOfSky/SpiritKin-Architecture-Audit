import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import ecommerce_task_queue as queue


class EcommerceTaskQueueTests(unittest.TestCase):
    def test_ingest_mobile_links_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state" / "ecommerce_tasks"
            links = root / "state" / "mobile-links" / "links.jsonl"
            links.parent.mkdir(parents=True)
            event = {
                "link": "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283",
                "source": "android-bridge",
                "receivedAt": "2026-06-08T12:00:00+00:00",
                "client": "127.0.0.1",
            }
            links.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

            first = queue.ingest_mobile_links(state_dir=state_dir, links_path=links, root=root)
            second = queue.ingest_mobile_links(state_dir=state_dir, links_path=links, root=root)
            snapshot = queue.load_queue(state_dir)

            self.assertEqual(len(first["created"]), 1)
            task_id = first["created"][0]
            self.assertTrue(task_id.startswith("link_"))
            self.assertEqual(second["created"], [])
            self.assertEqual(second["updated"], [task_id])
            self.assertEqual(len(snapshot["tasks"]), 1)
            self.assertEqual(snapshot["tasks"][0]["inputs"]["link_type"], "pdd_web_link")

    def test_attach_browser_extension_productdata_updates_listing_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state" / "ecommerce_tasks"
            links = root / "state" / "mobile-links" / "links.jsonl"
            links.parent.mkdir(parents=True)
            link = "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283"
            links.write_text(json.dumps({"link": link, "source": "android-bridge"}) + "\n", encoding="utf-8")
            task_id = queue.ingest_mobile_links(state_dir=state_dir, links_path=links, root=root)["created"][0]
            product_data = root / "product.json"
            product_data.write_text(
                json.dumps({"goodsId": "680378531283", "listingGate": {"ok": True, "missing": [], "checks": {}}}),
                encoding="utf-8",
            )

            result = queue.attach_productdata_artifact(
                task_id,
                product_data_json=product_data,
                control_plane_artifact_id="art_product_1",
                state_dir=state_dir,
                root=root,
            )

            self.assertEqual(result["task"]["status"], queue.STATUS_PRODUCTDATA_READY)
            self.assertEqual(result["artifact"]["control_plane_artifact_id"], "art_product_1")
            self.assertTrue(queue.as_path(result["artifact"]["path"], root=root).exists())

    def test_ingest_mobile_links_skips_test_links_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state" / "ecommerce_tasks"
            links = root / "state" / "mobile-links" / "links.jsonl"
            links.parent.mkdir(parents=True)
            event = {
                "link": "https://mobile.yangkeduo.com/goods.html?ps=localtest",
                "source": "local-self-test",
                "receivedAt": "2026-06-08T12:00:00+00:00",
                "client": "127.0.0.1",
            }
            links.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

            result = queue.ingest_mobile_links(state_dir=state_dir, links_path=links, root=root)
            snapshot = queue.load_queue(state_dir)

            self.assertEqual(result["created"], [])
            self.assertEqual(result["ignored"], 1)
            self.assertEqual(snapshot["tasks"], [])

            included = queue.ingest_mobile_links(
                state_dir=state_dir,
                links_path=links,
                include_test_links=True,
                root=root,
            )
            self.assertEqual(len(included["created"]), 1)

    def test_enqueue_image_copies_source_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "source.jpg"
            image.write_bytes(b"image-bytes")
            state_dir = root / "state" / "ecommerce_tasks"

            result = queue.enqueue_image_task(image, state_dir=state_dir, task_id="image_test", root=root)

            self.assertTrue(result["created"])
            artifact = result["task"]["artifacts"][0]
            artifact_path = queue.as_path(artifact["path"], root=root)
            self.assertTrue(artifact_path.exists())
            self.assertEqual(artifact_path.read_bytes(), b"image-bytes")
            self.assertFalse(artifact["temporary"])
            self.assertEqual(result["task"]["status"], queue.STATUS_IMAGE_QUEUED)

    def test_cleanup_temp_artifacts_respects_dry_run_and_deletes_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state" / "ecommerce_tasks"
            image = root / "source.jpg"
            image.write_bytes(b"source")
            task = queue.enqueue_image_task(image, state_dir=state_dir, task_id="image_test", root=root)["task"]
            probe = root / "probe.json"
            screenshot = root / "ocr.png"
            probe.write_text("{}", encoding="utf-8")
            screenshot.write_bytes(b"ocr")
            queue.attach_probe_artifacts(
                "image_test",
                probe_result=probe,
                screenshots=[screenshot],
                state_dir=state_dir,
                ttl_hours=1,
                root=root,
            )
            snapshot = queue.load_queue(state_dir)
            task = snapshot["tasks"][0]
            temp_artifact = next(item for item in task["artifacts"] if item["kind"] == "ocr_screenshot")
            temp_artifact["created_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            queue.save_queue(state_dir, snapshot)
            temp_path = queue.as_path(temp_artifact["path"], root=root)

            dry_run = queue.cleanup_temporary_artifacts(state_dir=state_dir, older_than_hours=1, dry_run=True, root=root)
            self.assertEqual(dry_run["deleted"], [queue.display_path(temp_path, root=root)])
            self.assertTrue(temp_path.exists())

            result = queue.cleanup_temporary_artifacts(state_dir=state_dir, older_than_hours=1, dry_run=False, root=root)
            self.assertEqual(result["deleted"], [queue.display_path(temp_path, root=root)])
            self.assertFalse(temp_path.exists())
            refreshed = queue.load_queue(state_dir)
            refreshed_temp = next(item for item in refreshed["tasks"][0]["artifacts"] if item["kind"] == "ocr_screenshot")
            self.assertIn("deleted_at", refreshed_temp)

    def test_cleanup_rejects_temporary_artifact_outside_managed_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state" / "ecommerce_tasks"
            outside = root / "outside.png"
            outside.write_bytes(b"outside")
            task = queue.new_task("task_1", "test", "probe_captured", "test", {})
            task["artifacts"].append(
                {
                    "kind": "ocr_screenshot",
                    "path": queue.display_path(outside, root=root),
                    "temporary": True,
                    "created_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                }
            )
            queue.save_queue(state_dir, {"version": 1, "updated_at": queue.utc_now(), "tasks": [task]})

            with self.assertRaises(ValueError):
                queue.cleanup_temporary_artifacts(state_dir=state_dir, older_than_hours=1, dry_run=False, root=root)
            self.assertTrue(outside.exists())

    def test_export_skill_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state" / "ecommerce_tasks"

            result = queue.export_skill_candidates(state_dir=state_dir)
            output = Path(result["output"])
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(result["count"], 3)
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["metadata"]["runtime_dependency_policy"] == "project_local_only" for row in rows))

    def test_evolution_proposal_decision_queue_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state" / "ecommerce_tasks"

            first = queue.enqueue_evolution_proposal(
                state_dir=state_dir,
                source_type="paper",
                title="OCR recovery policy",
                summary="Use paper notes to improve screenshot retry decisions.",
                source_ref="https://example.test/paper",
                evidence=["contains retry benchmark"],
            )
            second = queue.enqueue_evolution_proposal(
                state_dir=state_dir,
                source_type="paper",
                title="OCR recovery policy",
                summary="Use paper notes to improve screenshot retry decisions.",
                source_ref="https://example.test/paper",
            )

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            proposals = queue.read_jsonl(queue.evolution_proposals_path(state_dir))
            self.assertEqual(len(proposals), 1)

            snapshot = queue.build_evolution_queue(state_dir)
            proposal_id = first["proposal"]["proposal_id"]
            self.assertEqual(snapshot["pending_count"], 1)
            self.assertEqual(snapshot["status_counts"], {"pending_review": 1})
            self.assertEqual(snapshot["proposals"][0]["proposal_id"], proposal_id)
            self.assertFalse(snapshot["proposals"][0]["metadata"]["live_code_change_allowed"])

            queue.decide_evolution_proposal(
                proposal_id,
                state_dir=state_dir,
                decision="needs_changes",
                reviewer="human-reviewer",
                rationale="Need a replay eval before rollout.",
                conditions=["attach local replay result"],
            )

            updated = queue.build_evolution_queue(state_dir)
            decisions = queue.read_jsonl(queue.evolution_decisions_path(state_dir))
            self.assertEqual(len(decisions), 1)
            self.assertEqual(updated["pending_count"], 0)
            self.assertEqual(updated["status_counts"], {"needs_changes": 1})
            self.assertEqual(updated["proposals"][0]["last_decision"]["decision"], "needs_changes")

    def test_training_package_and_video_proposals_use_expected_review_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state" / "ecommerce_tasks"

            training = queue.enqueue_evolution_proposal(
                state_dir=state_dir,
                source_type="training_package",
                title="Miniapp OCR training bundle",
                summary="Curated failed OCR traces for evaluator scoring.",
                source_ref="state/evals/ocr_bundle_v1",
                risk_level="high",
            )
            video = queue.enqueue_evolution_proposal(
                state_dir=state_dir,
                source_type="video",
                title="Operator recovery walkthrough",
                summary="Human walkthrough of retry and handoff decisions.",
                source_ref="state/training/recovery_walkthrough.mp4",
                risk_level="low",
            )

            self.assertEqual(training["proposal"]["review_gate"], "cloud_eval_review")
            self.assertEqual(training["proposal"]["required_reviews"], ["cloud_evaluator", "human"])
            self.assertEqual(video["proposal"]["review_gate"], "knowledge_review")
            self.assertEqual(video["proposal"]["required_reviews"], ["human"])

    def test_skill_candidates_queue_for_governed_evolution_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state" / "ecommerce_tasks"

            result = queue.enqueue_skill_candidates_for_evolution(state_dir=state_dir)
            repeated = queue.enqueue_skill_candidates_for_evolution(state_dir=state_dir)
            snapshot = queue.build_evolution_queue(state_dir)

            self.assertEqual(result["count"], 3)
            self.assertEqual(len(result["created"]), 3)
            self.assertEqual(repeated["created"], [])
            self.assertEqual(len(repeated["existing"]), 3)
            self.assertEqual(snapshot["source_type_counts"], {"skill_candidate": 3})
            self.assertTrue(all(item["review_gate"] == "core_review" for item in snapshot["proposals"]))
            self.assertTrue(all(item["metadata"]["activation_state"] == "inactive_candidate" for item in snapshot["proposals"]))

    def test_status_reports_evolution_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state" / "ecommerce_tasks"
            queue.enqueue_evolution_proposal(
                state_dir=state_dir,
                source_type="video",
                title="Retry walkthrough",
                summary="Capture manual recovery behavior before automation changes.",
            )

            status = queue.build_status(state_dir)

            self.assertEqual(status["evolution_proposal_count"], 1)
            self.assertEqual(status["evolution_pending_count"], 1)
            self.assertEqual(status["evolution_status_counts"], {"pending_review": 1})


if __name__ == "__main__":
    unittest.main()
