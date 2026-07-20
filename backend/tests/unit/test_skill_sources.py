import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.skills_console import build_desktop_skills_snapshot, handle_desktop_skills_action


class SkillSourceTests(unittest.TestCase):
    def test_skill_include_path_precedence_reports_shadowed_candidates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_repo = root / "source-skills"
            preferred = source_repo / "preferred"
            fallback = source_repo / "fallback"
            preferred.mkdir(parents=True)
            fallback.mkdir(parents=True)
            for directory, description in ((preferred, "preferred"), (fallback, "fallback")):
                (directory / "skill.json").write_text(
                    json.dumps(
                        {
                            "name": "git.conflict.skill",
                            "description": description,
                            "steps": [],
                        }
                    ),
                    encoding="utf-8",
                )
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                handle_desktop_skills_action(
                    {
                        "action": "register_source",
                        "source_id": "conflict-source",
                        "url": str(source_repo),
                        "include_paths": ["preferred", "fallback"],
                    }
                )
                synced = handle_desktop_skills_action({"action": "sync_source", "source_id": "conflict-source"})

        self.assertEqual(synced["scan"]["candidate_count"], 1)
        self.assertEqual(synced["scan"]["candidates"][0]["description"], "preferred")
        self.assertEqual(synced["scan"]["source"]["conflict_count"], 1)
        self.assertEqual(synced["scan"]["conflicts"][0]["resolution"], "first_include_path_then_lexical_path_wins")

    def test_local_source_syncs_to_quarantine_and_imports_candidate_skills(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_repo = root / "source-skills"
            source_repo.mkdir()
            (source_repo / "skills.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "git.demo.skill",
                                "description": "Imported demo skill",
                                "trigger_intents": ["demo"],
                                "steps": [{"tool_name": "demo.status", "arguments": {}, "description": "check"}],
                                "tool_allowlist": ["demo.status"],
                                "risk_level": "low",
                            }
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_SKILL_RUN_AUDIT_LOG": str(root / "state" / "skill-runs.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
                "SPIRITKIN_SAFETY_STATE_PATH": str(root / "state" / "safety.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                registered = handle_desktop_skills_action(
                    {
                        "action": "register_source",
                        "source_id": "demo-source",
                        "url": str(source_repo),
                        "target_scope": "project",
                        "trust_level": "untrusted",
                    }
                )
                self.assertTrue(registered["ok"])
                self.assertEqual(registered["source"]["source_id"], "demo-source")

                synced = handle_desktop_skills_action({"action": "sync_source", "source_id": "demo-source"})

                self.assertTrue(synced["ok"])
                self.assertEqual(synced["scan"]["candidate_count"], 1)
                quarantine_path = Path(synced["sync"]["quarantine_path"])
                self.assertTrue((quarantine_path / "skills.json").exists())

                imported = handle_desktop_skills_action({"action": "import_source_candidates", "source_id": "demo-source"})

                self.assertTrue(imported["ok"])
                self.assertEqual(imported["imported_count"], 1)
                skill = imported["imported_skills"][0]
                self.assertEqual(skill["name"], "git.demo.skill")
                self.assertEqual(skill["status"], "candidate")
                self.assertEqual(skill["source_type"], "git")
                self.assertEqual(skill["metadata"]["source_id"], "demo-source")
                self.assertTrue(skill["metadata"]["source_sha256"])
                self.assertIn("git.demo.skill", imported["skill_source_lock"]["skills"])

                snapshot = build_desktop_skills_snapshot()
                self.assertEqual(snapshot["status_counts"]["candidate"], 1)
                self.assertEqual(snapshot["skill_sources"]["count"], 1)

    def test_source_import_keeps_dangerous_skill_as_candidate_with_warning(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_repo = root / "source-skills"
            source_repo.mkdir()
            (source_repo / "danger.skill.json").write_text(
                json.dumps(
                    {
                        "name": "git.danger.skill",
                        "description": "Danger candidate",
                        "steps": [{"tool_name": "shell.run", "arguments": {"command": "rm -rf /tmp/demo"}}],
                        "tool_allowlist": ["shell.run"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                handle_desktop_skills_action({"action": "register_source", "source_id": "danger-source", "url": str(source_repo)})
                synced = handle_desktop_skills_action({"action": "sync_source", "source_id": "danger-source"})
                imported = handle_desktop_skills_action({"action": "import_source_candidates", "source_id": "danger-source"})

                self.assertIn("danger.skill.json:destructive_shell", synced["scan"]["warnings"])
                skill = imported["imported_skills"][0]
                self.assertEqual(skill["status"], "candidate")
                self.assertEqual(skill["risk_level"], "medium")
                self.assertTrue(skill["metadata"]["source_review"]["requires_core_review"])
                self.assertIn("destructive_shell", skill["metadata"]["source_review"]["warnings"])

    def test_desktop_skill_action_can_dry_run_imported_candidate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_repo = root / "source-skills"
            source_repo.mkdir()
            (source_repo / "skills.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "git.dryrun.skill",
                                "description": "Dry run imported skill",
                                "steps": [{"tool_name": "desktop.status", "arguments": {"query": "{{query}}"}}],
                                "tool_allowlist": ["desktop.status"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_SKILL_RUN_AUDIT_LOG": str(root / "state" / "skill-runs.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
                "SPIRITKIN_SAFETY_STATE_PATH": str(root / "state" / "safety.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                handle_desktop_skills_action({"action": "register_source", "source_id": "dry-source", "url": str(source_repo)})
                handle_desktop_skills_action({"action": "sync_source", "source_id": "dry-source"})
                handle_desktop_skills_action({"action": "import_source_candidates", "source_id": "dry-source"})

                result = handle_desktop_skills_action(
                    {
                        "action": "dry_run",
                        "name": "git.dryrun.skill",
                        "inputs": {"query": "status"},
                        "reviewer": "unit_test",
                    }
                )

                self.assertTrue(result["ok"])
                self.assertEqual(result["skill_run"]["skill_name"], "git.dryrun.skill")
                self.assertEqual(result["skill_run"]["metadata"]["planned_steps"][0]["arguments"]["query"], "status")
                self.assertEqual(result["skill"]["debug_summary"]["dry_run_count"], 1)
                self.assertEqual(result["skill"]["debug_summary"]["replay_success_count"], 1)
                self.assertEqual(result["skill"]["debug_summary"]["total_count"], 0)
                self.assertTrue(result["skill"]["metadata"]["debug_run_history"][0]["dry_run"])
                self.assertTrue(Path(env["SPIRITKIN_SKILL_RUN_AUDIT_LOG"]).exists())
                self.assertTrue(result["audit_event"]["dry_run"])

    def test_desktop_skill_run_budget_blocks_oversized_skill_and_audits(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_SKILL_RUN_AUDIT_LOG": str(root / "state" / "skill-runs.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
                "SPIRITKIN_SKILL_MAX_STEPS": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                handle_desktop_skills_action(
                    {
                        "action": "save",
                        "name": "budget.blocked.skill",
                        "description": "too many steps",
                        "steps": [
                            {"tool_name": "desktop.status", "arguments": {}},
                            {"tool_name": "desktop.status", "arguments": {}},
                        ],
                        "tool_allowlist": ["desktop.status"],
                    }
                )
                result = handle_desktop_skills_action({"action": "dry_run", "name": "budget.blocked.skill", "reviewer": "unit_test"})
                audit_lines = Path(env["SPIRITKIN_SKILL_RUN_AUDIT_LOG"]).read_text(encoding="utf-8").splitlines()

        self.assertFalse(result["ok"])
        self.assertFalse(result["budget"]["allowed"])
        self.assertEqual(result["skill_run"]["metadata"]["error_code"], "skill_budget_steps_exceeded")
        self.assertEqual(len(audit_lines), 1)

    def test_expected_file_hash_mismatch_blocks_source_candidates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_repo = root / "source-skills"
            source_repo.mkdir()
            skill_file = source_repo / "skills.json"
            skill_text = json.dumps(
                {
                    "skills": [
                        {
                            "name": "git.hash.skill",
                            "description": "Hash checked skill",
                            "steps": [{"tool_name": "desktop.status", "arguments": {}}],
                            "tool_allowlist": ["desktop.status"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
            skill_file.write_text(skill_text, encoding="utf-8")
            correct_hash = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
                "SPIRITKIN_SAFETY_STATE_PATH": str(root / "state" / "safety.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                handle_desktop_skills_action(
                    {
                        "action": "register_source",
                        "source_id": "hash-source",
                        "url": str(source_repo),
                        "expected_file_hashes": {"skills.json": "0" * 64},
                    }
                )
                mismatch = handle_desktop_skills_action({"action": "sync_source", "source_id": "hash-source"})
                self.assertFalse(mismatch["scan"]["ok"])
                self.assertEqual(mismatch["scan"]["candidate_count"], 0)
                self.assertIn("skills.json:expected_hash_mismatch", mismatch["scan"]["warnings"])

                handle_desktop_skills_action(
                    {
                        "action": "register_source",
                        "source_id": "hash-source",
                        "url": str(source_repo),
                        "expected_file_hashes": {"skills.json": correct_hash},
                    }
                )
                verified = handle_desktop_skills_action({"action": "sync_source", "source_id": "hash-source"})
                imported = handle_desktop_skills_action({"action": "import_source_candidates", "source_id": "hash-source"})
                self.assertTrue(verified["scan"]["ok"])
                self.assertEqual(imported["imported_count"], 1)
                self.assertEqual(imported["skills"]["skill_sources"]["sources"][0]["integrity_status"], "hash_verified")

    def test_manifest_schema_errors_skip_external_candidate_import(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_repo = root / "source-skills"
            source_repo.mkdir()
            (source_repo / "invalid.skill.json").write_text(
                json.dumps(
                    {
                        "name": "git.invalid.skill",
                        "description": "Invalid manifest",
                        "steps": [{"arguments": {"query": "missing tool"}}],
                        "tool_allowlist": ["desktop.status"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
                "SPIRITKIN_SAFETY_STATE_PATH": str(root / "state" / "safety.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                handle_desktop_skills_action({"action": "register_source", "source_id": "invalid-source", "url": str(source_repo)})
                synced = handle_desktop_skills_action({"action": "sync_source", "source_id": "invalid-source"})
                imported = handle_desktop_skills_action({"action": "import_source_candidates", "source_id": "invalid-source"})

                manifest = synced["scan"]["candidates"][0]["metadata"]["source_review"]["manifest"]
                self.assertFalse(manifest["valid"])
                self.assertIn("step_0_missing_tool_name", manifest["errors"])
                self.assertEqual(imported["imported_count"], 0)
                self.assertEqual(imported["skipped"][0]["reason"], "manifest_invalid")

    def test_source_policy_blocks_autonomous_discovery_until_enabled(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
                "SPIRITKIN_SAFETY_STATE_PATH": str(root / "state" / "safety.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                blocked = handle_desktop_skills_action({"action": "discover_github", "query": "skill", "autonomous": True})
                self.assertFalse(blocked["ok"])
                self.assertEqual(blocked["error"], "autonomous_discovery_disabled")

                policy = handle_desktop_skills_action(
                    {
                        "action": "save_source_policy",
                        "policy": {"allow_autonomous_discovery": True, "max_discovery_results": 3},
                    }
                )
                self.assertTrue(policy["ok"])
                self.assertTrue(policy["policy"]["allow_autonomous_discovery"])
                self.assertEqual(policy["policy"]["max_discovery_results"], 3)

    def test_openclaw_cli_sync_stages_candidates_in_quarantine(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "SPIRITKIN_SKILL_SOURCE_STATE": str(root / "state" / "sources.json"),
                "SPIRITKIN_SKILL_QUARANTINE_DIR": str(root / "state" / "quarantine"),
                "SPIRITKIN_SKILL_LOCK_PATH": str(root / "state" / "skills.lock.json"),
                "SPIRITKIN_SKILL_SOURCE_POLICY_PATH": str(root / "state" / "source_policy.json"),
                "SPIRITKIN_SKILL_STORE_PATH": str(root / "state" / "skills.jsonl"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": str(root / "state" / "agent_management.json"),
                "SPIRITKIN_SAFETY_STATE_PATH": str(root / "state" / "safety.json"),
            }
            stdout = json.dumps(
                {
                    "skills": [
                        {
                            "id": "openclaw.demo",
                            "description": "OpenClaw demo skill",
                            "steps": [{"tool_name": "desktop.status", "arguments": {}}],
                            "tools": ["desktop.status"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
            with patch.dict(os.environ, env, clear=False), patch("backend.app.skill_sources.shutil.which", return_value="C:/tools/openclaw.exe"), patch(
                "backend.app.skill_sources.subprocess.run",
                return_value=subprocess.CompletedProcess(["openclaw"], 0, stdout=stdout, stderr=""),
            ):
                synced = handle_desktop_skills_action({"action": "sync_openclaw", "source_id": "openclaw-unit"})
                imported = handle_desktop_skills_action({"action": "import_source_candidates", "source_id": "openclaw-unit"})

                self.assertTrue(synced["ok"])
                self.assertEqual(synced["scan"]["candidate_count"], 1)
                self.assertTrue((Path(synced["sync"]["quarantine_path"]) / "openclaw_skills.json").exists())
                self.assertEqual(imported["imported_count"], 1)
                skill = imported["imported_skills"][0]
                self.assertEqual(skill["name"], "openclaw.demo")
                self.assertEqual(skill["source_type"], "openclaw_cli")
                self.assertEqual(skill["metadata"]["source_review"]["manifest"]["valid"], True)


if __name__ == "__main__":
    unittest.main()
