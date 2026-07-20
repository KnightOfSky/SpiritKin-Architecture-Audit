from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.capability.growth.runtime import GrowthRuntime, handle_growth_action


class GrowthRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.runtime = GrowthRuntime(
            event_path=root / "events.jsonl",
            registry_path=root / "registry.jsonl",
            artifact_root=root / "artifacts",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_gap_analysis_is_candidate_only_and_idempotent(self) -> None:
        first = self.runtime.analyze_gap(
            {
                "request": "自动混剪视频",
                "required_capabilities": ["video.cut", "video.beat_sync"],
                "available_capabilities": ["video.cut"],
                "domain": "video_animation",
            }
        )
        self.assertEqual(first["gap"]["status"], "gap_found")
        self.assertEqual([item["requirements"] for item in first["candidates"]], [["video.beat_sync"]])
        self.assertFalse(first["candidates"][0]["activation"]["enabled"])
        second = self.runtime.analyze_gap(
            {
                "request": "自动混剪视频",
                "required_capabilities": ["video.cut", "video.beat_sync"],
                "available_capabilities": ["video.cut"],
                "domain": "video_animation",
            }
        )
        self.assertEqual(second["growth"]["candidate_count"], 1)
        self.assertEqual(first["candidates"][0]["risk"]["requires_human_review"], True)

    def test_candidate_escalation_builds_governed_lineage_and_freezes_parent(self) -> None:
        root = self.runtime.analyze_gap(
            {
                "request": "自动混剪视频",
                "required_capabilities": ["video.beat_sync"],
                "available_capabilities": [],
                "workspace_id": "tenant-a",
            }
        )["candidates"][0]
        skill_result = self.runtime.escalate_candidate(
            {
                "candidate_id": root["candidate_id"],
                "workspace_id": "tenant-a",
                "target_kind": "skill",
                "reason": "没有可复用的 Skill",
                "evidence": {"skill_registry_matches": 0},
                "requested_by": "unit-test",
            }
        )
        skill = skill_result["child_candidate"]
        self.assertEqual(skill_result["candidate"]["status"], "escalated")
        self.assertEqual(skill["kind"], "skill")
        self.assertEqual(skill["lineage"]["parent_candidate_id"], root["candidate_id"])
        self.assertEqual(skill["lineage"]["root_candidate_id"], root["candidate_id"])
        self.assertEqual(skill["lineage"]["depth"], 1)
        self.assertFalse(skill["activation"]["enabled"])
        with self.assertRaises(PermissionError):
            self.runtime.advance_stage(
                {
                    "candidate_id": root["candidate_id"],
                    "workspace_id": "tenant-a",
                    "stage": "research",
                    "evidence": {"status": "should-not-run"},
                }
            )

        tool_result = self.runtime.escalate_candidate(
            {
                "candidate_id": skill["candidate_id"],
                "workspace_id": "tenant-a",
                "target_kind": "tool",
                "reason": "Skill 需要外部节拍分析工具",
                "evidence": {"tool_registry_matches": 0},
                "requested_by": "unit-test",
            }
        )
        tool = tool_result["child_candidate"]
        self.assertEqual(tool["lineage"]["root_candidate_id"], root["candidate_id"])
        self.assertEqual(tool["lineage"]["depth"], 2)
        self.assertEqual(tool["lineage"]["transition"], "skill->tool")

    def test_growth_escalation_can_end_in_explicit_human_requirement(self) -> None:
        model = self.runtime.propose_model(
            {"model_id": "private-domain-model", "workspace_id": "tenant-a"}
        )["candidate"]
        result = self.runtime.escalate_candidate(
            {
                "candidate_id": model["candidate_id"],
                "workspace_id": "tenant-a",
                "target_kind": "human",
                "reason": "需要领域数据授权与人工评估",
                "evidence": {"license_review": "missing"},
                "requested_by": "unit-test",
            }
        )
        self.assertIsNone(result["child_candidate"])
        self.assertEqual(result["candidate"]["status"], "needs_human")
        self.assertTrue(result["candidate"]["resolution"]["requires_human"])
        self.assertEqual(result["growth"]["human_required_count"], 1)
        self.assertFalse(result["candidate"]["activation"]["enabled"])

    def test_growth_escalation_rejects_reverse_route_and_requires_public_confirmation(self) -> None:
        skill = self.runtime.propose_skill(
            {"missing_capability": "video.beat_sync", "workspace_id": "tenant-a"}
        )["candidate"]
        with self.assertRaises(ValueError):
            self.runtime.escalate_candidate(
                {
                    "candidate_id": skill["candidate_id"],
                    "workspace_id": "tenant-a",
                    "target_kind": "workflow",
                    "reason": "不允许逆向升级",
                    "evidence": {"test": "reverse"},
                    "requested_by": "unit-test",
                }
            )
        with self.assertRaises(PermissionError):
            handle_growth_action(
                {
                    "action": "escalate_candidate",
                    "candidate_id": "missing",
                    "target_kind": "human",
                    "reason": "missing",
                    "evidence": {"test": "confirmation"},
                    "requested_by": "unit-test",
                }
            )

    def test_workflow_review_and_registry_require_reviewer(self) -> None:
        proposed = self.runtime.mine_workflow(
            {
                "title": "电商商品发布",
                "steps": [{"capability_id": "image.ocr"}, {"capability_id": "commerce.product.publish"}],
                "occurrence_count": 100,
            }
        )
        candidate_id = proposed["candidate"]["candidate_id"]
        with self.assertRaises(ValueError):
            self.runtime.review_candidate({"candidate_id": candidate_id, "decision": "approve"})
        with self.assertRaises(PermissionError):
            self.runtime.register_candidate({"candidate_id": candidate_id})
        for stage in ("design", "dry_run", "benchmark"):
            self.runtime.advance_stage({"candidate_id": candidate_id, "stage": stage, "evidence": {"status": "ok"}})
        measured = self.runtime.record_candidate_benchmark(
            {
                "candidate_id": candidate_id,
                "recorded_by": "unit-test",
                "version": "2",
                "baseline_version": "1",
                "dataset": "workflow-fixture-v1",
                "before": {"success_rate": 0.8, "latency_ms": 150, "cost": 1, "retry_count": 2, "review_count": 2, "quality_score": 75},
                "after": {"success_rate": 0.9, "latency_ms": 120, "cost": 0.8, "retry_count": 1, "review_count": 1, "quality_score": 88},
            }
        )
        self.assertTrue(measured["benchmark_report"]["promotion_gate"]["passed"])
        self.runtime.advance_stage({"candidate_id": candidate_id, "stage": "review", "evidence": {"benchmark_id": measured["benchmark_report"]["benchmark_id"]}})
        approved = self.runtime.review_candidate(
            {
                "candidate_id": candidate_id,
                "decision": "approve",
                "reviewer": "human",
                "reason": "验证通过，可进入注册队列",
                "evidence": {"test_run": "growth-test-1"},
            }
        )
        self.assertEqual(approved["candidate"]["status"], "approved")
        with self.assertRaises(ValueError):
            self.runtime.register_candidate({"candidate_id": candidate_id})
        registered = self.runtime.register_candidate(
            {
                "candidate_id": candidate_id,
                "registered_by": "human",
                "registry_evidence": {"release_gate": "growth-test-1"},
            }
        )
        self.assertEqual(registered["candidate"]["status"], "registered")
        self.assertEqual(registered["registry"]["count"], 1)
        self.assertFalse(registered["candidate"]["activation"]["enabled"])
        self.assertEqual(registered["candidate"]["registry"]["evidence"]["release_gate"], "growth-test-1")

    def test_review_cannot_skip_ordered_stages(self) -> None:
        proposed = self.runtime.propose_tool({"missing_capability": "video.beat_sync"})
        with self.assertRaises(PermissionError):
            self.runtime.review_candidate(
                {
                    "candidate_id": proposed["candidate"]["candidate_id"],
                    "decision": "approve",
                    "reviewer": "human",
                    "reason": "直接批准不允许",
                    "evidence": {"test_run": "missing"},
                }
            )

    def test_growth_actions_are_workspace_scoped(self) -> None:
        proposed = self.runtime.propose_tool({"missing_capability": "workspace.a", "workspace_id": "tenant-a"})
        with self.assertRaises(PermissionError):
            self.runtime.advance_stage(
                {
                    "candidate_id": proposed["candidate"]["candidate_id"],
                    "stage": "research",
                    "evidence": {"source": "x"},
                    "workspace_id": "tenant-b",
                }
            )

    def test_public_growth_action_cannot_override_state_paths(self) -> None:
        root = Path(self.temp_dir.name)
        outside = root / "outside-events.jsonl"
        with patch.dict(
            "os.environ",
            {
                "SPIRITKIN_GROWTH_EVENT_LOG": str(root / "events.jsonl"),
                "SPIRITKIN_GROWTH_REGISTRY_LOG": str(root / "registry.jsonl"),
            },
            clear=False,
        ):
            result = handle_growth_action(
                {
                    "action": "propose_tool",
                    "missing_capability": "safe.tool",
                    "event_path": str(outside),
                    "registry_path": str(outside),
                }
            )
        self.assertTrue(result["ok"])
        self.assertFalse(outside.exists())

    def test_public_growth_review_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(PermissionError):
            handle_growth_action(
                {
                    "action": "review_candidate",
                    "candidate_id": "missing",
                    "decision": "approve",
                    "reviewer": "human",
                    "reason": "should not pass",
                    "evidence": {"test": "missing"},
                }
            )

    def test_builder_artifact_prepares_inventory_without_execution(self) -> None:
        root = Path(self.temp_dir.name)
        mcp_adapter = Mock()
        mcp_adapter.list_mappings.return_value = []
        with patch.dict(
            "os.environ",
            {
                "SPIRITKIN_TOOL_AUTHZ_PATH": str(root / "tool-authz.json"),
                "SPIRITKIN_MCP_REGISTRY_PATH": str(root / "mcp.json"),
                "SPIRITKIN_MODEL_CATALOG_PATH": str(root / "models.json"),
                "SPIRITKIN_MCP_DYNAMIC_TOOL_REGISTRATION": "1",
            },
            clear=False,
        ), patch("backend.tools.registry.build_mcp_adapter_from_config", return_value=mcp_adapter):
            proposed = self.runtime.propose_tool(
                {
                    "missing_capability": "python.run_script",
                    "workspace_id": "tenant-a",
                    "research_targets": ["python.run_script"],
                }
            )
            prepared = self.runtime.prepare_builder_artifact(
                {
                    "candidate_id": proposed["candidate"]["candidate_id"],
                    "workspace_id": "tenant-a",
                    "research_sources": [
                        {
                            "type": "local_registry",
                            "label": "Python Worker",
                            "url": "https://docs.python.org/3/",
                            "license": "PSF",
                        }
                    ],
                }
            )
        mcp_adapter.discover_tool_mappings.assert_not_called()
        artifact = prepared["builder_artifact"]
        self.assertEqual(artifact["status"], "prepared")
        self.assertFalse(artifact["research"]["network_accessed"])
        self.assertFalse(artifact["sandbox_plan"]["external_code_execution_enabled"])
        self.assertEqual(artifact["sandbox_plan"]["install_mode"], "proposal_only")
        self.assertFalse(artifact["registry_plan"]["activation_enabled"])
        self.assertIn("python.run_script", {item["tool_id"] for item in artifact["inventory"]["tool_matches"]})
        self.assertTrue(Path(artifact["path"]).is_file())
        self.assertEqual(prepared["candidate"]["current_stage"], "gap_analysis")
        self.assertFalse(prepared["candidate"]["activation"]["enabled"])
        self.assertEqual(prepared["growth"]["builder_artifacts"]["count"], 1)
        self.assertNotIn("inventory", prepared["growth"]["builder_artifacts"]["recent"][0])
        self.assertNotIn("path", prepared["growth"]["builder_artifacts"]["recent"][0])
        self.assertNotIn("root", prepared["growth"]["builder_artifacts"])
        self.assertNotIn("path", prepared["candidate"]["evidence"]["builder_artifact"])

    def test_builder_artifact_rejects_sensitive_research_sources(self) -> None:
        proposed = self.runtime.propose_tool({"missing_capability": "safe.tool", "workspace_id": "tenant-a"})
        with self.assertRaises(ValueError):
            self.runtime.prepare_builder_artifact(
                {
                    "candidate_id": proposed["candidate"]["candidate_id"],
                    "workspace_id": "tenant-a",
                    "research_sources": [{"url": "https://example.test/tool", "api_token": "secret"}],
                }
            )

    def test_remote_research_updates_candidate_without_advancing_or_activating(self) -> None:
        proposed = self.runtime.propose_tool(
            {"missing_capability": "video.beat_sync", "workspace_id": "tenant-a"}
        )
        candidate_id = proposed["candidate"]["candidate_id"]
        report_path = Path(self.temp_dir.name) / "artifacts" / candidate_id / "research-test.json"
        self.runtime.remote_researcher.research = Mock(
            return_value={
                "report_id": "research-test",
                "status": "completed",
                "provider": "github_repository_search",
                "query": "video beat sync",
                "result_count": 1,
                "total_count": 1,
                "incomplete_results": False,
                "repositories": [
                    {
                        "source_type": "github_repository_metadata",
                        "full_name": "example/beat-sync",
                        "url": "https://github.com/example/beat-sync",
                        "description": "Beat synchronization",
                        "license_spdx": "MIT",
                        "needs_license_review": False,
                        "eligible_for_sandbox_review": True,
                    }
                ],
                "rate_limit": {"remaining": 9},
                "created_at": 1780000000.0,
                "path": str(report_path),
            }
        )

        result = self.runtime.research_candidate(
            {
                "candidate_id": candidate_id,
                "workspace_id": "tenant-a",
                "researched_by": "unit-test",
            }
        )

        candidate = result["candidate"]
        self.assertEqual(candidate["current_stage"], "gap_analysis")
        self.assertEqual(candidate["evidence"]["remote_research"]["result_count"], 1)
        self.assertFalse(candidate["evidence"]["remote_research"]["downloaded"])
        self.assertFalse(candidate["activation"]["enabled"])
        self.assertNotIn("path", candidate["evidence"]["remote_research"])

        prepared = self.runtime.prepare_builder_artifact(
            {"candidate_id": candidate_id, "workspace_id": "tenant-a"}
        )
        artifact = prepared["builder_artifact"]
        self.assertTrue(artifact["research"]["network_accessed"])
        self.assertEqual(artifact["research"]["remote_report_id"], "research-test")
        self.assertEqual(
            artifact["research"]["declared_sources"][0]["url"],
            "https://github.com/example/beat-sync",
        )
        self.assertFalse(artifact["sandbox_plan"]["external_code_execution_enabled"])
        self.assertFalse(artifact["registry_plan"]["activation_enabled"])

    def test_public_remote_research_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(PermissionError):
            handle_growth_action(
                {
                    "action": "research_candidate",
                    "candidate_id": "missing",
                    "researched_by": "unit-test",
                }
            )

    def test_builder_verification_writes_managed_report_without_execution(self) -> None:
        root = Path(self.temp_dir.name)
        with patch.dict(
            "os.environ",
            {
                "SPIRITKIN_TOOL_AUTHZ_PATH": str(root / "tool-authz.json"),
                "SPIRITKIN_MCP_REGISTRY_PATH": str(root / "mcp.json"),
                "SPIRITKIN_MODEL_CATALOG_PATH": str(root / "models.json"),
            },
            clear=False,
        ):
            proposed = self.runtime.propose_tool(
                {"missing_capability": "python.run_script", "workspace_id": "tenant-a"}
            )
            candidate_id = proposed["candidate"]["candidate_id"]
            self.runtime.prepare_builder_artifact(
                {"candidate_id": candidate_id, "workspace_id": "tenant-a"}
            )
            self.runtime.advance_stage(
                {"candidate_id": candidate_id, "workspace_id": "tenant-a", "stage": "research", "evidence": {"source": "local_registry"}}
            )
            self.runtime.advance_stage(
                {"candidate_id": candidate_id, "workspace_id": "tenant-a", "stage": "sandbox", "evidence": {"mode": "isolated_candidate"}}
            )
            verified = self.runtime.verify_builder_artifact(
                {"candidate_id": candidate_id, "workspace_id": "tenant-a", "verified_by": "unit-test"}
            )
        report = verified["verification_report"]
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["mode"], "static_sandbox_preflight")
        self.assertFalse(report["policy"]["network_accessed"])
        self.assertFalse(report["policy"]["external_code_executed"])
        self.assertFalse(report["policy"]["candidate_stage_advanced"])
        self.assertFalse(report["policy"]["activation_enabled"])
        self.assertTrue(Path(report["path"]).resolve().is_relative_to((root / "sandboxes").resolve()))
        self.assertEqual(verified["candidate"]["current_stage"], "sandbox")
        self.assertFalse(verified["candidate"]["activation"]["enabled"])
        self.assertEqual(verified["growth"]["builder_artifacts"]["recent"][0]["verification_status"], "passed")
        self.assertNotIn("path", verified["candidate"]["evidence"]["builder_verification"])
        self.assertNotIn("event_path", verified["growth"])
        self.assertNotIn("registry_path", verified["growth"])

    def test_public_builder_verification_requires_confirmation(self) -> None:
        with self.assertRaises(PermissionError):
            handle_growth_action({"action": "verify_builder_artifact", "candidate_id": "missing"})

    def test_public_builder_action_cannot_override_artifact_root(self) -> None:
        root = Path(self.temp_dir.name)
        managed_root = root / "managed-artifacts"
        outside = root / "outside-artifacts"
        with patch.dict(
            "os.environ",
            {
                "SPIRITKIN_GROWTH_EVENT_LOG": str(root / "public-events.jsonl"),
                "SPIRITKIN_GROWTH_REGISTRY_LOG": str(root / "public-registry.jsonl"),
                "SPIRITKIN_GROWTH_ARTIFACT_ROOT": str(managed_root),
                "SPIRITKIN_TOOL_AUTHZ_PATH": str(root / "public-tool-authz.json"),
                "SPIRITKIN_MCP_REGISTRY_PATH": str(root / "public-mcp.json"),
                "SPIRITKIN_MODEL_CATALOG_PATH": str(root / "public-models.json"),
            },
            clear=False,
        ):
            proposed = handle_growth_action(
                {
                    "action": "propose_tool",
                    "missing_capability": "python.run_script",
                    "workspace_id": "tenant-a",
                }
            )
            prepared = handle_growth_action(
                {
                    "action": "prepare_builder_artifact",
                    "candidate_id": proposed["candidate"]["candidate_id"],
                    "workspace_id": "tenant-a",
                    "artifact_root": str(outside),
                }
            )
        managed_path = managed_root.resolve()
        managed_artifacts = list(managed_path.rglob("builder-*.json"))
        self.assertEqual(len(managed_artifacts), 1)
        self.assertNotIn("path", prepared["builder_artifact"])
        self.assertNotIn("root", prepared["growth"]["builder_artifacts"])
        self.assertFalse(outside.exists())

    def test_candidate_stage_advancement_requires_ordered_evidence(self) -> None:
        proposed = self.runtime.propose_tool({"missing_capability": "video.beat_sync"})
        candidate_id = proposed["candidate"]["candidate_id"]
        advanced = self.runtime.advance_stage({"candidate_id": candidate_id, "stage": "research", "evidence": {"sources": ["ffmpeg"]}})
        self.assertEqual(advanced["candidate"]["current_stage"], "research")
        with self.assertRaises(ValueError):
            self.runtime.advance_stage({"candidate_id": candidate_id, "stage": "benchmark", "evidence": {"score": 0.8}})
        for stage in ("sandbox", "dry_run", "benchmark"):
            self.runtime.advance_stage({"candidate_id": candidate_id, "stage": stage, "evidence": {"status": "ok"}})
        with self.assertRaises(PermissionError):
            self.runtime.advance_stage({"candidate_id": candidate_id, "stage": "review", "evidence": {"status": "ok"}})
        with self.assertRaises(ValueError):
            self.runtime.advance_stage({"candidate_id": candidate_id, "stage": "registry", "evidence": {"approved": True}})

    def test_tool_growth_never_marks_installable(self) -> None:
        result = self.runtime.propose_tool({"missing_capability": "video.beat_sync", "research_targets": ["ffmpeg"]})
        self.assertEqual(result["candidate"]["kind"], "tool")
        self.assertFalse(result["candidate"]["evidence"]["install_allowed"])
        self.assertFalse(result["candidate"]["activation"]["enabled"])

    def test_repeated_failure_observation_creates_skill_candidate(self) -> None:
        payload = {
            "stage": "workflow_node",
            "tool_name": "video.beat_sync",
            "error_code": "tool_unavailable",
            "message": "beat sync tool unavailable",
            "workspace_id": "tenant-a",
        }
        with patch.dict("os.environ", {"SPIRITKIN_GROWTH_FAILURE_THRESHOLD": "2"}, clear=False):
            first = self.runtime.observe_failure(payload)
            second = self.runtime.observe_failure(payload)
        self.assertIsNone(first["candidate"])
        self.assertEqual(second["candidate"]["kind"], "skill")
        self.assertFalse(second["candidate"]["activation"]["enabled"])

    def test_repeated_workflow_trajectory_creates_workflow_candidate(self) -> None:
        with patch.dict("os.environ", {"SPIRITKIN_GROWTH_WORKFLOW_THRESHOLD": "2"}, clear=False):
            first = self.runtime.observe_trajectory(
                {
                    "trajectory_id": "traj-1",
                    "overall_success": True,
                    "domain": "ecommerce",
                    "metadata": {"workflow_name": "listing", "run_id": "run-1", "node_id": "ocr"},
                }
            )
            second = self.runtime.observe_trajectory(
                {
                    "trajectory_id": "traj-2",
                    "overall_success": True,
                    "domain": "ecommerce",
                    "metadata": {"workflow_name": "listing", "run_id": "run-2", "node_id": "publish"},
                }
            )
        self.assertFalse(first["observed"] is False)
        self.assertEqual(second["candidate"]["kind"], "workflow")
        self.assertEqual(second["candidate"]["evidence"]["occurrence_count"], 2)

    def test_code_and_model_growth_are_candidate_only(self) -> None:
        code = self.runtime.propose_code({"missing_capability": "video.beat_sync"})
        model = self.runtime.propose_model({"model_id": "local-reasoner"})
        self.assertEqual(code["candidate"]["kind"], "code")
        self.assertEqual(model["candidate"]["kind"], "model")
        self.assertFalse(code["candidate"]["activation"]["enabled"])
        self.assertFalse(model["candidate"]["activation"]["enabled"])

    def test_model_jury_is_server_requested_and_unlocks_review_only_after_two_structured_approvals(self) -> None:
        candidate = self.runtime.propose_model(
            {"model_id": "candidate-reasoner", "workspace_id": "tenant-a"}
        )["candidate"]
        candidate_id = candidate["candidate_id"]
        self.runtime.advance_stage(
            {"candidate_id": candidate_id, "workspace_id": "tenant-a", "stage": "research", "evidence": {"source": "catalog"}}
        )
        self.runtime.advance_stage(
            {"candidate_id": candidate_id, "workspace_id": "tenant-a", "stage": "benchmark", "evidence": {"dataset": "planning-v1"}}
        )
        measured = self.runtime.record_candidate_benchmark(
            {
                "candidate_id": candidate_id,
                "workspace_id": "tenant-a",
                "recorded_by": "unit-test",
                "version": "2",
                "baseline_version": "1",
                "dataset": "planning-v1",
                "measurement_source": "model-benchmark-run-1",
                "before": {"success_rate": 0.8, "latency_ms": 500, "cost": 1, "retry_count": 2, "review_count": 2, "quality_score": 75},
                "after": {"success_rate": 0.9, "latency_ms": 450, "cost": 0.9, "retry_count": 1, "review_count": 1, "quality_score": 88},
            }
        )
        benchmark_id = measured["benchmark_report"]["benchmark_id"]
        self.assertEqual(measured["benchmark_report"]["promotion_gate"]["status"], "waiting_jury")

        committee = Mock()
        committee.snapshot.return_value = {
            "reviews": [
                {"ok": True, "provider": "openai", "model": "gpt", "response_text": json.dumps({"benchmark_id": benchmark_id, "verdict": "approve", "confidence": 0.9, "rationale": "Measured gain.", "risks": []})},
                {"ok": True, "provider": "anthropic", "model": "claude", "response_text": json.dumps({"benchmark_id": benchmark_id, "verdict": "approve", "confidence": 0.9, "rationale": "No regression.", "risks": []})},
            ]
        }
        with patch("backend.app.learning_workflow.request_multi_model_review", return_value=committee) as request_review:
            juried = self.runtime.run_model_jury(
                {"candidate_id": candidate_id, "workspace_id": "tenant-a", "requested_by": "unit-test"}
            )

        self.assertTrue(juried["benchmark_report"]["promotion_gate"]["passed"])
        self.assertEqual(juried["model_jury"]["structured_review_count"], 2)
        request_review.assert_called_once()
        advanced = self.runtime.advance_stage(
            {"candidate_id": candidate_id, "workspace_id": "tenant-a", "stage": "review", "evidence": {"benchmark_id": benchmark_id}}
        )
        self.assertEqual(advanced["candidate"]["current_stage"], "review")

    def test_runtime_trajectory_log_feeds_growth_observer(self) -> None:
        from backend.orchestrator.runtime_trajectory_log import append_runtime_trajectory

        root = Path(self.temp_dir.name)
        with patch.dict(
            "os.environ",
            {
                "SPIRITKIN_GROWTH_EVENT_LOG": str(root / "growth-events.jsonl"),
                "SPIRITKIN_GROWTH_FAILURE_THRESHOLD": "2",
                "SPIRITKIN_GROWTH_AUTO_OBSERVE_TRAJECTORIES": "1",
                "SPIRITKIN_GROWTH_OBSERVER_SYNC": "1",
            },
            clear=False,
        ):
            trajectory = {
                "overall_success": False,
                "bottleneck_stage": "executor",
                "execution_result": "model unavailable",
                "steps": [{"stage": "executor", "success": False, "error_code": "model_unavailable", "detail": "model unavailable", "metadata": {"operation": "chat"}}],
            }
            append_runtime_trajectory(trajectory, path=root / "trajectory-1.jsonl")
            append_runtime_trajectory(trajectory, path=root / "trajectory-2.jsonl")
            snapshot = self.runtime.__class__(event_path=root / "growth-events.jsonl", registry_path=root / "registry.jsonl").snapshot()
        self.assertEqual(snapshot["candidate_count"], 1)
        self.assertEqual(snapshot["candidates"][0]["kind"], "skill")

    def test_snapshot_filters_candidates_by_workspace(self) -> None:
        self.runtime.propose_tool({"missing_capability": "workspace.a", "workspace_id": "tenant-a"})
        self.runtime.propose_tool({"missing_capability": "workspace.b", "workspace_id": "tenant-b"})
        snapshot = self.runtime.snapshot(workspace_id="tenant-a", include_unscoped=False)
        self.assertEqual(snapshot["candidate_count"], 1)
        self.assertEqual(snapshot["candidates"][0]["workspace_id"], "tenant-a")


if __name__ == "__main__":
    unittest.main()
