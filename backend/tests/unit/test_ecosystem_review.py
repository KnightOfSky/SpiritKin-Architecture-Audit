from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.command_gateway import (
    build_desktop_ecosystem_review_response,
    build_desktop_ecosystem_review_update_response,
)
from backend.app.ecosystem_review import (
    apply_approved_proposals,
    build_ecosystem_review_snapshot,
    refresh_ecosystem_review_state,
    update_proposal_status,
)
from backend.app.module_governance import ModuleGovernanceSpec, _assess_module, build_module_governance_snapshot


class EcosystemReviewTests(unittest.TestCase):
    def test_scans_score_and_generates_low_risk_knowledge_proposals(self):
        with isolated_ecosystem_workspace() as root:
            snapshot = build_ecosystem_review_snapshot(project_root=root)

            self.assertEqual(snapshot["schema_version"], "spiritkin.ecosystem_review.v1")
            self.assertIn("score", snapshot)
            self.assertGreaterEqual(snapshot["score"]["total"], 0)
            self.assertIn("knowledge", snapshot["systems"])
            self.assertTrue(
                any(
                    action.get("type") == "knowledge.ensure_directory"
                    for proposal in snapshot["proposals"]
                    for action in proposal.get("actions", [])
                )
            )

    def test_module_governance_snapshot_tracks_enterprise_module_inventory(self):
        with isolated_ecosystem_workspace() as root:
            backend_root = root / "backend"
            backend_root.mkdir()
            (backend_root / "main.py").write_text("def main():\n    return None\n", encoding="utf-8")

            snapshot = build_module_governance_snapshot(project_root=root)
            modules = {item["module_id"]: item for item in snapshot["modules"]}

            self.assertEqual(snapshot["schema_version"], "spiritkin.module_governance.v1")
            self.assertGreaterEqual(snapshot["portfolio"]["module_count"], 25)
            self.assertIn("backend.entrypoint", modules)
            self.assertIn("backend.app", modules)
            self.assertEqual(modules["backend.entrypoint"]["path"], "backend/main.py")
            self.assertTrue(modules["backend.entrypoint"]["exists"])
            self.assertEqual(modules["backend.entrypoint"]["file_count"], 1)
            self.assertIn("owner_role", modules["backend.app"])
            self.assertIn("operating_model", snapshot)
            self.assertIn("improvement_backlog", snapshot)

    def test_large_module_surface_gap_is_satisfied_by_module_readme(self):
        with isolated_ecosystem_workspace() as root:
            module = root / "large_module"
            module.mkdir()
            for index in range(61):
                (module / f"part_{index}.py").write_text("VALUE = 1\n", encoding="utf-8")
            (module / "README.md").write_text("# Large Module\n\nSubdomain owners are documented here.\n", encoding="utf-8")
            spec = ModuleGovernanceSpec(
                "large.module",
                "Large Module",
                "large_module",
                "unit",
                "Unit Owner",
                "high",
                "unit",
                "Synthetic large module.",
                ("docs", "tests"),
                ("python -m py_compile large_module/part_0.py",),
                ("large_module",),
            )

            record = _assess_module(root, spec, docs_index={}, tests=[])

            self.assertNotIn("large_module_surface", {gap["gap_id"] for gap in record.gaps})

    def test_ecosystem_review_exposes_module_governance_dimension_and_manual_queue(self):
        with isolated_ecosystem_workspace() as root:
            snapshot = build_ecosystem_review_snapshot(project_root=root)
            dimension_ids = {item["dimension_id"] for item in snapshot["score"]["dimensions"]}
            governance = snapshot["systems"]["module_governance"]
            manual_governance_proposals = [
                proposal
                for proposal in snapshot["proposals"]
                if any(action.get("type") == "manual.module_governance" for action in proposal.get("actions", []))
            ]

            self.assertIn("module_governance", dimension_ids)
            self.assertIn("manual.module_governance", snapshot["capabilities"]["supported_action_types"])
            self.assertIn("portfolio", governance)
            self.assertIn("modules", governance)
            self.assertTrue(any(item["module_id"] == "backend.entrypoint" for item in governance["modules"]))
            self.assertTrue(any(item["category"] == "module_governance" for item in snapshot["issues"]))
            self.assertTrue(manual_governance_proposals)
            self.assertTrue(all(not item["auto_apply_allowed"] for item in manual_governance_proposals))

    def test_approve_and_apply_low_risk_proposal_creates_knowledge_directory(self):
        with isolated_ecosystem_workspace() as root:
            snapshot = refresh_ecosystem_review_state(project_root=root)
            proposal = next(
                item
                for item in snapshot["proposals"]
                if any(action.get("type") == "knowledge.ensure_directory" for action in item.get("actions", []))
            )

            approved = update_proposal_status(proposal["proposal_id"], "approved", reviewer="unit-test")
            result = apply_approved_proposals(proposal_ids=[proposal["proposal_id"]])
            refreshed = build_ecosystem_review_snapshot(project_root=root)
            updated = next(item for item in refreshed["proposals"] if item["proposal_id"] == proposal["proposal_id"])

            self.assertEqual(approved.status, "approved")
            self.assertEqual(result["applied_count"], 1)
            self.assertEqual(updated["status"], "applied")
            action_path = Path(updated["apply_result"]["results"][0]["path"])
            self.assertTrue(action_path.exists())
            knowledge_root = root / "state" / "knowledge_bases"
            self.assertTrue(any(parent.samefile(knowledge_root) for parent in (action_path, *action_path.parents) if parent.exists()))

    def test_apply_low_risk_skips_medium_actions_inside_log_repair(self):
        with isolated_ecosystem_workspace() as root:
            log_dir = root / "state" / "logs"
            log_dir.mkdir(parents=True)
            (log_dir / "ops_command_gateway.err.log").write_text("Traceback: failed\nERROR boom\n", encoding="utf-8")

            snapshot = refresh_ecosystem_review_state(project_root=root)
            proposal = next(
                item
                for item in snapshot["proposals"]
                if any(action.get("type") == "service.restart" for action in item.get("actions", []))
                and any(action.get("type") == "knowledge.write_note" for action in item.get("actions", []))
            )

            update_proposal_status(proposal["proposal_id"], "approved", reviewer="unit-test")
            result = apply_approved_proposals(proposal_ids=[proposal["proposal_id"]])
            refreshed = build_ecosystem_review_snapshot(project_root=root)
            updated = next(item for item in refreshed["proposals"] if item["proposal_id"] == proposal["proposal_id"])
            action_results = updated["apply_result"]["results"]

            self.assertEqual(result["applied_count"], 1)
            self.assertEqual(updated["status"], "applied")
            self.assertTrue(any(item["type"] == "knowledge.write_note" and not item.get("skipped") for item in action_results))
            self.assertTrue(any(item["type"] == "learning.record" and not item.get("skipped") for item in action_results))
            self.assertTrue(any(item["type"] == "service.restart" and item.get("skipped") for item in action_results))

    def test_update_proposal_status_refreshes_current_scan_before_lookup(self):
        with isolated_ecosystem_workspace() as root:
            (root / "state" / "desktop_console").mkdir(parents=True)
            (root / "state" / "desktop_console" / "ecosystem_review.json").write_text(
                json.dumps({"schema_version": "spiritkin.ecosystem_review.v1", "proposals": [{"proposal_id": "old", "status": "pending"}]}),
                encoding="utf-8",
            )
            log_dir = root / "state" / "logs"
            log_dir.mkdir(parents=True)
            (log_dir / "bridge.err.log").write_text("ERROR bridge failed\n", encoding="utf-8")
            snapshot = build_ecosystem_review_snapshot(project_root=root)
            proposal = next(
                item
                for item in snapshot["proposals"]
                if item["category"] == "log_repair" and item["status"] == "pending"
            )

            approved = update_proposal_status(proposal["proposal_id"], "approved", reviewer="unit-test")

            self.assertEqual(approved.status, "approved")
            self.assertEqual(approved.proposal_id, proposal["proposal_id"])

    def test_log_repair_proposal_id_survives_log_tail_change(self):
        with isolated_ecosystem_workspace() as root:
            log_dir = root / "state" / "logs"
            log_dir.mkdir(parents=True)
            log_path = log_dir / "bridge.err.log"
            log_path.write_text("ERROR bridge failed\n", encoding="utf-8")
            snapshot = build_ecosystem_review_snapshot(project_root=root)
            proposal = next(
                item
                for item in snapshot["proposals"]
                if item["category"] == "log_repair" and item["status"] == "pending"
            )

            log_path.write_text("ERROR bridge failed\nTraceback changed tail\n", encoding="utf-8")
            approved = update_proposal_status(proposal["proposal_id"], "approved", reviewer="unit-test")

            self.assertEqual(approved.status, "approved")
            self.assertEqual(approved.proposal_id, proposal["proposal_id"])

    def test_stale_pending_proposals_are_not_carried_forward(self):
        with isolated_ecosystem_workspace() as root:
            state_path = root / "state" / "desktop_console" / "ecosystem_review.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "spiritkin.ecosystem_review.v1",
                        "proposals": [
                            {"proposal_id": "stale_pending", "status": "pending", "title": "Old", "category": "old"},
                            {"proposal_id": "stale_applied", "status": "applied", "title": "Done", "category": "old"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            snapshot = refresh_ecosystem_review_state(project_root=root)

            ids = {item["proposal_id"] for item in snapshot["proposals"]}
            self.assertNotIn("stale_pending", ids)
            self.assertIn("stale_applied", ids)

    def test_manual_only_model_review_proposal_stays_approved_when_apply_has_no_executable_actions(self):
        with isolated_ecosystem_workspace() as root:
            state_path = root / "state" / "desktop_console" / "ecosystem_review.json"
            state_path.parent.mkdir(parents=True)
            proposal = {
                "proposal_id": "proposal_manual",
                "source": "external_model",
                "category": "model_review",
                "title": "人工检查建议",
                "detail": "需要人工检查。",
                "risk_level": "low",
                "status": "approved",
                "actions": [{"type": "manual.review_model_suggestion"}],
                "evidence": {},
            }
            state_path.write_text(json.dumps({"schema_version": "spiritkin.ecosystem_review.v1", "proposals": [proposal]}), encoding="utf-8")

            result = apply_approved_proposals(proposal_ids=["proposal_manual"])
            refreshed = build_ecosystem_review_snapshot(project_root=root)
            updated = next(item for item in refreshed["proposals"] if item["proposal_id"] == "proposal_manual")

            self.assertEqual(result["applied_count"], 0)
            self.assertEqual(result["skipped_count"], 1)
            self.assertEqual(updated["status"], "approved")
            self.assertEqual(updated["apply_result"]["executed_count"], 0)

    def test_snapshot_triages_proposals_without_mutating_review_state(self):
        with isolated_ecosystem_workspace() as root:
            state_path = root / "state" / "desktop_console" / "ecosystem_review.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "spiritkin.ecosystem_review.v1",
                        "proposals": [
                            {
                                "proposal_id": "proposal_manual",
                                "source": "unit",
                                "category": "module_governance",
                                "title": "Manual module review",
                                "detail": "Needs owner.",
                                "risk_level": "high",
                                "status": "approved",
                                "actions": [{"type": "manual.module_governance"}],
                            },
                            {
                                "proposal_id": "proposal_done",
                                "source": "unit",
                                "category": "knowledge",
                                "title": "Done",
                                "detail": "Already applied.",
                                "risk_level": "low",
                                "status": "applied",
                                "actions": [{"type": "knowledge.ensure_directory", "path": "state/knowledge_bases/unit"}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            snapshot = build_ecosystem_review_snapshot(project_root=root)
            triage = snapshot["proposal_triage"]
            by_id = {item["proposal_id"]: item for item in triage["items"]}
            manual_current = next(
                item
                for item in triage["items"]
                if "manual.module_governance" in item["action_types"] and item["proposal_id"] != "proposal_manual"
            )

            self.assertTrue(snapshot["capabilities"]["proposal_triage"])
            self.assertEqual(manual_current["bucket"], "convert_to_task")
            self.assertEqual(by_id["proposal_manual"]["bucket"], "stale_noise")
            self.assertEqual(by_id["proposal_done"]["bucket"], "done_or_rejected")
            self.assertEqual(snapshot["proposals"][0]["status"], "approved")
            self.assertIn("triage", snapshot["proposals"][0])

    def test_gateway_exposes_read_only_proposal_triage_action(self):
        with isolated_ecosystem_workspace() as root:
            status, payload = build_desktop_ecosystem_review_update_response({"action": "triage"})

            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertIn("proposal_triage", payload)
            self.assertTrue(Path(payload["ecosystem_review"]["project_root"]).samefile(root))

    def test_gateway_exposes_ecosystem_review_snapshot_and_actions(self):
        with isolated_ecosystem_workspace() as root:
            get_status, get_payload = build_desktop_ecosystem_review_response()
            scan_status, scan_payload = build_desktop_ecosystem_review_update_response({"action": "scan"})

            self.assertEqual(get_status, 200)
            self.assertTrue(get_payload["ok"])
            self.assertTrue(Path(get_payload["ecosystem_review"]["project_root"]).samefile(root))
            self.assertEqual(scan_status, 200)
            self.assertTrue(scan_payload["ok"])
            self.assertIn("proposals", scan_payload["ecosystem_review"])


class isolated_ecosystem_workspace:
    def __enter__(self) -> Path:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.previous_cwd = os.getcwd()
        self.previous_env = {key: os.environ.get(key) for key in ENV_KEYS}
        os.chdir(self.root)
        os.environ["SPIRITKIN_ECOSYSTEM_REVIEW_STATE"] = str(self.root / "state" / "desktop_console" / "ecosystem_review.json")
        os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(self.root / "state" / "desktop_console" / "agent_management.json")
        os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(self.root / "state" / "skills.jsonl")
        os.environ["SPIRITKIN_LEARNING_LOG"] = str(self.root / "state" / "learning" / "learning_records.jsonl")
        os.environ["SPIRITKIN_LEARNING_DATASET"] = str(self.root / "state" / "learning" / "self_training_dataset.jsonl")
        os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = str(self.root / "state" / "desktop_console" / "assist_models.json")
        os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = str(self.root / "state" / "desktop_console" / "model_provider.json")
        return self.root

    def __exit__(self, exc_type, exc, tb) -> None:
        os.chdir(self.previous_cwd)
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()


ENV_KEYS = (
    "SPIRITKIN_ECOSYSTEM_REVIEW_STATE",
    "SPIRITKIN_AGENT_MANAGEMENT_PATH",
    "SPIRITKIN_SKILL_STORE_PATH",
    "SPIRITKIN_LEARNING_LOG",
    "SPIRITKIN_LEARNING_DATASET",
    "SPIRITKIN_ASSIST_MODEL_STATE",
    "SPIRITKIN_MODEL_PROVIDER_STATE",
)


if __name__ == "__main__":
    unittest.main()
