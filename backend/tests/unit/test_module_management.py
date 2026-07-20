import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.module_management import build_module_management_snapshot


class ModuleManagementTests(unittest.TestCase):
    def test_snapshot_exposes_v2_enterprise_portfolio_fields(self):
        ecosystem_snapshot = {
            "score": {"total": 86},
            "pending_count": 1,
            "systems": {"module_governance": {"portfolio": {"critical_high_risk_count": 1}}},
            "proposal_triage": {"counts": {"by_bucket": {"convert_to_task": 1, "apply_after_review": 0}}},
            "proposals": [
                {
                    "proposal_id": "proposal_unit",
                    "status": "pending",
                    "category": "module_governance",
                    "risk_level": "high",
                    "title": "Review critical module",
                    "detail": "Needs owner review",
                }
            ],
        }
        learning_report = SimpleNamespace(
            snapshot=lambda: {
                "assist_models": [{"enabled": True, "configured": True}],
                "model_provider_settings": {"configured": True},
                "model_providers": [],
            }
        )

        with patch("backend.app.module_management.build_desktop_skills_snapshot", return_value={"count": 1, "status_counts": {"active": 1}}), patch(
            "backend.app.module_management.build_skill_router_snapshot",
            return_value={"skill_count": 1, "active_skill_count": 1, "routable_skill_count": 1, "candidate_skill_count": 0},
        ), patch(
            "backend.app.module_management.build_agent_management_desktop_snapshot",
            return_value={
                "distribution_summary": {
                    "counts": {
                        "agents_enabled": 2,
                        "route_profiles_total": 1,
                        "external_assistants_enabled": 0,
                    },
                    "gaps": [{"priority": "high", "title": "Assign agent route", "detail": "Route missing"}],
                }
            },
        ), patch(
            "backend.app.module_management.build_knowledge_base_snapshot",
            return_value={
                "knowledge_bases": [
                    {
                        "knowledge_base_id": "kb_unit",
                        "enabled": True,
                        "exists": True,
                        "file_count": 2,
                        "last_index": {"updated_at": 1},
                    }
                ]
            },
        ), patch("backend.app.module_management.load_model_catalog", return_value={"models": [{"name": "model"}], "failures": []}), patch(
            "backend.app.module_management.build_learning_workflow_report", return_value=learning_report
        ), patch(
            "backend.app.module_management.build_search_management_snapshot",
            return_value={
                "web_search": {"provider": "unit"},
                "knowledge_retrieval": {"backend": "local", "embedding_provider": "unit", "reranker": "none"},
                "missing_capabilities": [],
            },
        ), patch(
            "backend.app.module_management.build_resource_management_snapshot",
            return_value={
                "resource_count": 1,
                "gap_count": 0,
                "resource_registry": {
                    "total": 1,
                    "type_counts": {"shop": 1},
                    "owner_counts": {"ecommerce": 1},
                    "gaps": [],
                },
            },
        ), patch(
            "backend.app.module_management.build_evolution_management_snapshot",
            return_value={
                "status": "ready",
                "self_improvement_summary": {"counts": {}},
                "trajectory": {"total": 1},
                "agent_skill_distribution": {},
                "learning_artifacts": {"artifact_count": 1},
                "domain_skill_templates": {"existing_count": 1, "count": 1},
                "action_items": [],
            },
        ), patch(
            "backend.app.module_management.build_workflow_management_snapshot",
            return_value={"overview": {"definition_count": 1, "run_count": 0, "active_run_count": 0, "status_counts": {}}},
        ), patch(
            "backend.app.module_management.build_mobile_management_snapshot",
            return_value={
                "android": {
                    "active_device": {"serial": "100.118.62.77:41055"},
                    "apk": {"exists": True},
                    "installed": {"installed": True},
                    "endpoint": {"port": 8791, "health": {"ok": True}},
                },
                "ios": {"endpoint": {"port": 8792, "health": {"ok": False}}},
            },
        ):
            snapshot = build_module_management_snapshot(ecosystem_snapshot=ecosystem_snapshot)

        self.assertEqual(snapshot["schema_version"], "spiritkin.module_management.v2")
        self.assertEqual(snapshot["overview"]["health_score"], snapshot["portfolio"]["health_score"])
        self.assertEqual(snapshot["overview"]["readiness_percent"], snapshot["portfolio"]["readiness_percent"])
        self.assertIn(snapshot["portfolio"]["operator_posture"], {"controlled", "attention", "blocked"})
        self.assertEqual(set(snapshot["portfolio"]["risk_counts"].keys()), {"high", "medium", "low"})

        modules_by_id = {module["module_id"]: module for module in snapshot["modules"]}
        self.assertEqual(modules_by_id["agents"]["owner_role"], "Agent Ops Lead")
        self.assertEqual(modules_by_id["skill_router"]["owner_role"], "Skill Routing Owner")
        self.assertEqual(modules_by_id["skill_router"]["status"], "ready")
        self.assertEqual(modules_by_id["agents"]["management_group"], "Agent Operations")
        self.assertEqual(modules_by_id["agents"]["risk_level"], "high")
        self.assertEqual(modules_by_id["agents"]["governance_state"], "blocked")
        self.assertEqual(modules_by_id["workflows"]["owner_role"], "Workflow Operator")
        self.assertEqual(modules_by_id["workflows"]["risk_level"], "low")
        self.assertEqual(modules_by_id["mobile_management"]["owner_role"], "Mobile Ops Owner")
        self.assertEqual(modules_by_id["mobile_management"]["desktop_page"], "mobile")
        self.assertIn("Android 服务 在线", modules_by_id["mobile_management"]["summary"])
        self.assertEqual(modules_by_id["resource_registry"]["endpoint"], "/desktop/resource-registry")
        self.assertEqual(modules_by_id["resource_registry"]["status"], "ready")
        self.assertIn("转任务 1", modules_by_id["module_governance"]["summary"])
        governance_metric_labels = {item["label"]: item["value"] for item in modules_by_id["module_governance"]["metrics"]}
        self.assertEqual(governance_metric_labels["转任务建议"], 1)

        for module in snapshot["modules"]:
            for field in (
                "business_capability",
                "management_group",
                "owner_role",
                "criticality",
                "maturity",
                "sla",
                "risk_level",
                "risk_summary",
                "health_score",
                "governance_state",
                "action_count",
                "runbook",
            ):
                self.assertIn(field, module)

        agent_action = next(item for item in snapshot["action_items"] if item["module_id"] == "agents")
        self.assertEqual(agent_action["owner_role"], "Agent Ops Lead")
        self.assertEqual(agent_action["management_group"], "Agent Operations")
        self.assertEqual(agent_action["risk_level"], "high")
        self.assertEqual(agent_action["governance_state"], "blocked")
        self.assertIn("Agent Ops Lead", agent_action["operator_hint"])
        self.assertIn("SLA:", agent_action["operator_hint"])

    def test_knowledge_and_search_modules_surface_failed_knowledge_jobs(self):
        failed_job = {
            "job_id": "job-1",
            "job_type": "index",
            "status": "failed",
            "target_id": "kb_bad",
            "target_path": "state/knowledge_bases/bad",
            "summary": "Synthetic failure",
            "error": "ValueError: broken index",
            "actor": "unit-test",
            "started_at": 1,
            "completed_at": 2,
            "duration_ms": 1000,
        }
        learning_report = SimpleNamespace(
            snapshot=lambda: {
                "assist_models": [{"enabled": True, "configured": True}],
                "model_provider_settings": {"configured": True},
                "model_providers": [],
            }
        )

        with patch("backend.app.module_management.build_desktop_skills_snapshot", return_value={"count": 1, "status_counts": {"active": 1}}), patch(
            "backend.app.module_management.build_skill_router_snapshot",
            return_value={"skill_count": 1, "active_skill_count": 1, "routable_skill_count": 1, "candidate_skill_count": 0},
        ), patch(
            "backend.app.module_management.build_agent_management_desktop_snapshot",
            return_value={"distribution_summary": {"counts": {"agents_enabled": 1, "route_profiles_total": 1, "external_assistants_enabled": 0}, "gaps": []}},
        ), patch(
            "backend.app.module_management.build_knowledge_base_snapshot",
            return_value={
                "knowledge_bases": [
                    {
                        "knowledge_base_id": "kb_bad",
                        "enabled": True,
                        "exists": True,
                        "file_count": 1,
                        "last_index": {"updated_at": 1},
                    }
                ],
                "job_history": {"count": 1, "failed_count": 1, "jobs": [failed_job], "last_error": failed_job["error"]},
            },
        ), patch("backend.app.module_management.load_model_catalog", return_value={"models": [{"name": "model"}], "failures": []}), patch(
            "backend.app.module_management.build_learning_workflow_report", return_value=learning_report
        ), patch(
            "backend.app.module_management.build_search_management_snapshot",
            return_value={
                "web_search": {"provider": "unit"},
                "knowledge_retrieval": {"backend": "embedding", "embedding_provider": "unit", "reranker": "none"},
                "knowledge_jobs": {"count": 1, "failed_count": 1, "jobs": [failed_job], "last_error": failed_job["error"]},
                "missing_capabilities": [],
            },
        ), patch(
            "backend.app.module_management.build_resource_management_snapshot",
            return_value={"resource_count": 0, "gap_count": 0, "resource_registry": {"total": 0, "type_counts": {}, "owner_counts": {}, "gaps": []}},
        ), patch(
            "backend.app.module_management.build_evolution_management_snapshot",
            return_value={
                "status": "ready",
                "self_improvement_summary": {"counts": {}},
                "trajectory": {"total": 1},
                "agent_skill_distribution": {},
                "learning_artifacts": {"artifact_count": 1},
                "domain_skill_templates": {"existing_count": 1, "count": 1},
                "action_items": [],
            },
        ), patch(
            "backend.app.module_management.build_workflow_management_snapshot",
            return_value={"overview": {"definition_count": 1, "run_count": 0, "active_run_count": 0, "status_counts": {}}},
        ), patch(
            "backend.app.module_management.build_mobile_management_snapshot",
            return_value={
                "android": {
                    "active_device": {"serial": "100.118.62.77:41055"},
                    "apk": {"exists": True},
                    "installed": {"installed": True},
                    "endpoint": {"port": 8791, "health": {"ok": True}},
                },
                "ios": {"endpoint": {"port": 8792, "health": {"ok": False}}},
            },
        ):
            snapshot = build_module_management_snapshot(ecosystem_snapshot={"score": {"total": 90}, "proposals": [], "systems": {}})

        modules_by_id = {module["module_id"]: module for module in snapshot["modules"]}
        self.assertEqual(modules_by_id["knowledge_base"]["status"], "needs_attention")
        self.assertEqual(modules_by_id["search_management"]["status"], "needs_attention")
        self.assertIn("失败任务 1", modules_by_id["knowledge_base"]["summary"])
        self.assertIn("失败任务 1", modules_by_id["search_management"]["summary"])
        self.assertTrue(any("kb_bad" in action["title"] for action in modules_by_id["knowledge_base"]["actions"]))
        self.assertTrue(any("kb_bad" in action["title"] for action in modules_by_id["search_management"]["actions"]))


if __name__ == "__main__":
    unittest.main()
