import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.agents.base import AgentReply
from backend.app.command_gateway import (
    build_command_response,
    build_desktop_action_log_response,
    build_desktop_agent_management_response,
    build_desktop_agent_management_update_response,
    build_desktop_code_jury_response,
    build_desktop_code_jury_update_response,
    build_desktop_collaboration_response,
    build_desktop_collaboration_update_response,
    build_desktop_context_response,
    build_desktop_context_update_response,
    build_desktop_daily_response,
    build_desktop_diagnostics_response,
    build_desktop_diagnostics_update_response,
    build_desktop_evolution_response,
    build_desktop_evolution_update_response,
    build_desktop_knowledge_base_response,
    build_desktop_knowledge_base_update_response,
    build_desktop_learning_response,
    build_desktop_learning_update_response,
    build_desktop_logs_response,
    build_desktop_mcp_management_response,
    build_desktop_mcp_management_update_response,
    build_desktop_mobile_management_response,
    build_desktop_mobile_management_update_response,
    build_desktop_module_management_response,
    build_desktop_module_management_update_response,
    build_desktop_operations_response,
    build_desktop_project_overview_response,
    build_desktop_project_overview_update_response,
    build_desktop_project_runtime_response,
    build_desktop_project_runtime_update_response,
    build_desktop_resource_registry_response,
    build_desktop_resource_registry_update_response,
    build_desktop_safety_response,
    build_desktop_safety_update_response,
    build_desktop_search_management_response,
    build_desktop_search_management_update_response,
    build_desktop_service_ports_response,
    build_desktop_services_response,
    build_desktop_services_update_response,
    build_desktop_skill_router_response,
    build_desktop_skill_router_update_response,
    build_desktop_skills_response,
    build_desktop_skills_update_response,
    build_desktop_state_maintenance_response,
    build_desktop_state_maintenance_update_response,
    build_desktop_state_response,
    build_desktop_state_update_response,
    build_desktop_sync_response,
    build_desktop_sync_update_response,
    build_desktop_workflows_response,
    build_desktop_workflows_update_response,
    build_gateway_security_context,
    build_mobile_artifacts_ingest_response,
    build_model_catalog_response,
    build_model_catalog_update_response,
    build_training_cloud_package_response,
    build_training_dataset_response,
    token_is_authorized,
)
from backend.app.operations_center import default_managed_services, handle_service_action, list_service_actions
from backend.app.runtime import SpiritKinRuntime
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.security.safety_control import HARD_STOP_RESUME_CONFIRMATION
from scripts.smoke_mobile_access import _command_health_url, run_mobile_access_smoke


class FakeAgent:
    def __init__(self):
        self.calls = []

    def process(self, user_input, visual_context="", channel="text", input_metadata=None):
        self.calls.append((user_input, visual_context, channel, input_metadata or {}))
        return AgentReply(text=f"收到：{user_input}", agent_name="fake")


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class CommandGatewayTests(unittest.TestCase):
    def test_build_command_response_routes_mobile_text_to_runtime(self):
        agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=agent, emit_runtime_events=False)

        status, payload = build_command_response(runtime, {"text": "扫描本机软件"}, client_id="phone")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reply"]["text"], "收到：扫描本机软件")
        self.assertEqual([event["type"] for event in payload["events"][:2]], ["assistant.message", "avatar.state"])
        self.assertEqual(agent.calls[0][2], "mobile")
        self.assertEqual(agent.calls[0][3]["client_type"], "mobile")

    def test_build_command_response_returns_no_content_for_cancel_control(self):
        class CancelAgent:
            def process(self, user_input, visual_context="", channel="text", input_metadata=None):
                return AgentReply(
                    text="",
                    agent_name="request_coordinator",
                    metadata={"response_kind": "request_cancelled", "request_id": "cancel-r2"},
                )

        runtime = SpiritKinRuntime(agent=CancelAgent(), emit_runtime_events=False)

        status, payload = build_command_response(
            runtime,
            {"text": "停止当前生成", "metadata": {"control_action": "cancel_generation"}},
            client_id="desktop",
        )

        self.assertEqual(status, 204)
        self.assertTrue(payload["cancelled"])
        self.assertIsNone(payload["reply"])

    def test_desktop_service_ports_response_exposes_registry_snapshot(self):
        status, payload = build_desktop_service_ports_response()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service_ports"]["schema_version"], "spiritkin.service_ports.v1")
        self.assertIn("command_gateway", payload["service_ports"]["ports"])

        services_status, services_payload = build_desktop_services_response()
        self.assertEqual(services_status, 200)
        self.assertIn("service_ports", services_payload)

    def test_desktop_resource_registry_crud_persists_long_lived_assets(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_RESOURCE_REGISTRY_PATH")
            os.environ["SPIRITKIN_RESOURCE_REGISTRY_PATH"] = str(Path(tmp) / "resources.json")
            try:
                save_status, save_payload = build_desktop_resource_registry_update_response(
                    {
                        "action": "save",
                        "resource_id": "Shop A",
                        "label": "Douyin Shop A",
                        "resource_type": "shop",
                        "platform": "douyin",
                        "owner_agent": "ecommerce",
                        "credential_ref": "vault:douyin_shop_a",
                        "supported_capabilities": ["commerce.product.publish", "commerce.price.update"],
                        "policies": {"risk": "medium"},
                    }
                )
                get_status, get_payload = build_desktop_resource_registry_response()
                weak_status, weak_payload = build_desktop_resource_registry_update_response(
                    {
                        "action": "save",
                        "resource_id": "bad",
                        "label": "Bad",
                        "credential_ref": "plain:secret",
                    }
                )
                delete_status, delete_payload = build_desktop_resource_registry_update_response({"action": "delete", "resource_id": "shop_a"})
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_RESOURCE_REGISTRY_PATH", None)
                else:
                    os.environ["SPIRITKIN_RESOURCE_REGISTRY_PATH"] = previous

        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["resource"]["resource_id"], "shop_a")
        self.assertEqual(save_payload["resource"]["credential_ref"], "vault:douyin_shop_a")
        self.assertEqual(get_status, 200)
        self.assertEqual(get_payload["resource_management"]["resource_registry"]["total"], 1)
        self.assertEqual(get_payload["resource_management"]["resource_registry"]["type_counts"]["shop"], 1)
        self.assertEqual(weak_status, 400)
        self.assertIn("credential_ref", weak_payload["detail"])
        self.assertEqual(delete_status, 200)
        self.assertTrue(delete_payload["deleted"])
        self.assertEqual(delete_payload["resource_management"]["resource_registry"]["total"], 0)

    def test_build_command_response_returns_execution_event_for_http_fallback(self):
        class ExecutionAgent(FakeAgent):
            def process(self, user_input, visual_context="", channel="text", input_metadata=None):
                self.calls.append((user_input, visual_context, channel, input_metadata or {}))
                return AgentReply(
                    text="执行完成",
                    emotion="happy",
                    action="execute_task",
                    agent_name="executor_fake",
                    metadata={
                        "response_kind": "execution_result",
                        "execution": {
                            "target": "local_pc",
                            "operation": "launch_app",
                            "success": True,
                            "data": {"app": "browser"},
                            "metadata": {},
                        },
                    },
                )

        runtime = SpiritKinRuntime(agent=ExecutionAgent(), emit_runtime_events=False)

        status, payload = build_command_response(runtime, {"text": "确认执行", "channel": "desktop"}, client_id="desktop")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reply"]["response_kind"], "execution_result")
        self.assertIn("assistant.execution_updated", [event["type"] for event in payload["events"]])

    def test_build_command_response_requires_text(self):
        runtime = SpiritKinRuntime(agent=FakeAgent(), emit_runtime_events=False)

        status, payload = build_command_response(runtime, {})

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_build_command_response_returns_json_when_runtime_fails(self):
        class FailingAgent(FakeAgent):
            def process(self, user_input, visual_context="", channel="text", input_metadata=None):
                raise RuntimeError("API error: connection refused")

        runtime = SpiritKinRuntime(agent=FailingAgent(), emit_runtime_events=False)

        status, payload = build_command_response(
            runtime,
            {"text": "普通聊天", "channel": "desktop", "metadata": {"request_id": "req-fail"}},
            client_id="desktop",
        )

        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "runtime_failed")
        self.assertEqual(payload["reply"]["response_kind"], "task_failed")
        self.assertEqual(payload["reply"]["data"]["request_id"], "req-fail")
        self.assertIn("assistant.message", [event["type"] for event in payload["events"]])
        self.assertIn("avatar.state", [event["type"] for event in payload["events"]])

    def test_build_command_response_passes_attachments_and_document_previews(self):
        agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=agent, emit_runtime_events=False)

        status, payload = build_command_response(
            runtime,
            {
                "text": "处理拖入文件",
                "attachments": [
                    {
                        "file_id": "file_1",
                        "name": "note.md",
                        "mime_type": "text/markdown",
                        "uri": "state/uploads/upl/note.md",
                        "size_bytes": 12,
                    }
                ],
                "documents": [{"path": "note.md", "text": "这是拖入文件的内容。"}],
            },
            client_id="web",
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        metadata = agent.calls[0][3]
        self.assertEqual(metadata["attachment_count"], 1)
        self.assertEqual(metadata["attachment_document_count"], 1)
        self.assertEqual(metadata["attachment_documents"][0]["text_preview"], "这是拖入文件的内容。")
        self.assertEqual(payload["reply"]["attachments"][0]["file_id"], "file_1")

    def test_mobile_artifacts_ingest_response_stores_ios_work_image(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_MOBILE_ARTIFACT_ROOT": tmp}, clear=False):
            status, payload = build_mobile_artifacts_ingest_response(
                {
                    "source": "ios_terminal",
                    "purpose": "ios_work_image",
                    "files": [{"path": "phone.png", "content_base64": "cG5n", "mime_type": "image/png"}],
                },
                client_id="iphone",
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["artifacts"][0]["source"], "ios_terminal")
        self.assertEqual(payload["mobile_artifacts"]["image_count"], 1)

    def test_build_command_response_passes_plan_and_goal_metadata(self):
        agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=agent, emit_runtime_events=False)

        status, payload = build_command_response(
            runtime,
            {
                "text": "继续",
                "channel": "desktop",
                "metadata": {
                    "plan_mode": True,
                    "pursue_goal": True,
                    "goal_text": "完善 + 号菜单",
                },
            },
            client_id="desktop",
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        metadata = agent.calls[0][3]
        self.assertTrue(metadata["plan_mode"])
        self.assertTrue(metadata["pursue_goal"])
        self.assertEqual(metadata["goal_text"], "完善 + 号菜单")

    def test_build_command_response_exposes_plan_and_goal_mode_data(self):
        class ModeAgent(FakeAgent):
            def process(self, user_input, visual_context="", channel="text", input_metadata=None):
                return AgentReply(
                    text="计划模式",
                    agent_name="plan_mode",
                    metadata={
                        "response_kind": "plan_mode",
                        "plan": {
                            "title": "完善 + 号菜单",
                            "mode": "plan_only",
                            "steps": [{"index": 1, "title": "检查 UI", "status": "pending"}],
                        },
                        "goal": {
                            "text": "完善 + 号菜单",
                            "status": "active",
                            "progress_percent": 20,
                            "next_action": "继续完善",
                        },
                    },
                )

        runtime = SpiritKinRuntime(agent=ModeAgent(), emit_runtime_events=False)
        status, payload = build_command_response(runtime, {"text": "继续", "channel": "desktop"}, client_id="desktop")

        self.assertEqual(status, 200)
        data = payload["reply"]["data"]
        self.assertEqual(data["plan"]["mode"], "plan_only")
        self.assertEqual(data["goal"]["status"], "active")
        self.assertEqual(payload["events"][0]["payload"]["data"]["goal"]["progress_percent"], 20)

    def test_build_training_dataset_response_rejects_output_outside_workspace_training_dirs(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "uploaded.jsonl"
            status, payload = build_training_dataset_response(
                {
                    "documents": [{"path": "docs/openclaw.md", "text": "OpenClaw 回零前需要确认。"}],
                    "output_path": str(output),
                }
            )

        self.assertEqual(status, 400)
        self.assertIn("output_path", payload["error"])

    def test_build_training_dataset_response_accepts_uploaded_documents(self):
        output = Path("state/tests/training_gateway_uploaded.jsonl")
        registry_path = Path("state/tests/training_gateway_datasets.jsonl")
        previous_registry = os.environ.get("SPIRITKIN_DATASET_REGISTRY_PATH")
        os.environ["SPIRITKIN_DATASET_REGISTRY_PATH"] = str(registry_path)
        try:
            status, payload = build_training_dataset_response(
                {
                    "documents": [{"path": "docs/openclaw.md", "text": "OpenClaw 回零前需要确认。"}],
                    "output_path": str(output),
                    "base_model": "Qwen/Qwen2.5-3B-Instruct",
                }
            )
        finally:
            output.unlink(missing_ok=True)
            registry_path.unlink(missing_ok=True)
            if previous_registry is None:
                os.environ.pop("SPIRITKIN_DATASET_REGISTRY_PATH", None)
            else:
                os.environ["SPIRITKIN_DATASET_REGISTRY_PATH"] = previous_registry

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dataset"]["example_count"], 1)
        self.assertTrue(payload["dataset_gate"]["allowed"])
        self.assertEqual(payload["dataset_card"]["status"], "training_ready")
        self.assertEqual(payload["dataset_registry"]["dataset_count"], 1)
        self.assertIn("backend.model.training.unsloth_lora_train", payload["training_command"])

    def test_training_dataset_response_lists_registry_and_evaluates_gate(self):
        output = Path("state/tests/training_gateway_registry_query.jsonl")
        registry_path = Path("state/tests/training_gateway_registry_query_datasets.jsonl")
        previous_registry = os.environ.get("SPIRITKIN_DATASET_REGISTRY_PATH")
        os.environ["SPIRITKIN_DATASET_REGISTRY_PATH"] = str(registry_path)
        try:
            build_status, build_payload = build_training_dataset_response(
                {
                    "documents": [{"path": "docs/openclaw.md", "text": "OpenClaw 工具调用要保留人工确认。"}],
                    "output_path": str(output),
                }
            )
            list_status, list_payload = build_training_dataset_response({"action": "list_registry"})
            gate_status, gate_payload = build_training_dataset_response({"action": "evaluate_dataset_gate", "dataset_path": str(output)})
        finally:
            output.unlink(missing_ok=True)
            registry_path.unlink(missing_ok=True)
            if previous_registry is None:
                os.environ.pop("SPIRITKIN_DATASET_REGISTRY_PATH", None)
            else:
                os.environ["SPIRITKIN_DATASET_REGISTRY_PATH"] = previous_registry

        self.assertEqual(build_status, 200)
        self.assertTrue(build_payload["dataset_gate"]["allowed"])
        self.assertEqual(list_status, 200)
        self.assertEqual(list_payload["dataset_registry"]["dataset_count"], 1)
        self.assertEqual(gate_status, 200)
        self.assertTrue(gate_payload["ok"])
        self.assertEqual(gate_payload["dataset_gate"]["status"], "verified")

    def test_build_training_cloud_package_response_exports_self_contained_package(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "train.jsonl"
            dataset.write_text('{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"ok"}]}\n', encoding="utf-8")
            previous = os.environ.get("SPIRITKIN_CLOUD_TRAINING_DIR")
            os.environ["SPIRITKIN_CLOUD_TRAINING_DIR"] = str(root / "packages")
            try:
                blocked_status, blocked_payload = build_training_cloud_package_response(
                    {
                        "dataset_path": str(dataset),
                        "base_model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                        "package_id": "unit-cloud-train-blocked",
                    }
                )
                string_false_status, string_false_payload = build_training_cloud_package_response(
                    {
                        "dataset_path": str(dataset),
                        "base_model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                        "package_id": "unit-cloud-train-string-false",
                        "core_review_approved": "false",
                        "reviewer": "unit-test",
                    }
                )
                status, payload = build_training_cloud_package_response(
                    {
                        "dataset_path": str(dataset),
                        "base_model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                        "package_id": "unit-cloud-train",
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_CLOUD_TRAINING_DIR", None)
                else:
                    os.environ["SPIRITKIN_CLOUD_TRAINING_DIR"] = previous

            self.assertEqual(blocked_status, 403)
            self.assertEqual(blocked_payload["error"], "review_required")
            self.assertEqual(string_false_status, 403)
            self.assertEqual(string_false_payload["error"], "review_required")
            self.assertEqual(status, 200)
            package = payload["cloud_training_package"]
            self.assertTrue(payload["review_gate"]["allowed"])
            self.assertTrue(payload["dataset_gate"]["allowed"])
            self.assertTrue(Path(package["manifest_path"]).exists())
            self.assertTrue(Path(package["dataset_path"]).exists())
            self.assertIn("backend.model.training.unsloth_lora_train", package["command"])

    def test_build_training_cloud_package_response_rejects_unverified_dataset(self):
        with TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "broken.jsonl"
            dataset.write_text("{not-json}\n", encoding="utf-8")

            status, payload = build_training_cloud_package_response(
                {
                    "dataset_path": str(dataset),
                    "base_model": "Qwen/Qwen2.5-3B-Instruct",
                    "core_review_approved": True,
                    "reviewer": "unit-test",
                }
            )

        self.assertEqual(status, 422)
        self.assertEqual(payload["error"], "dataset_gate_failed")
        self.assertFalse(payload["dataset_gate"]["allowed"])

    def test_model_catalog_can_load_bundled_and_refresh_with_mocked_network(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_MODEL_CATALOG_PATH")
            previous_history = os.environ.get("SPIRITKIN_SCHEDULER_BENCHMARK_HISTORY_PATH")
            os.environ["SPIRITKIN_MODEL_CATALOG_PATH"] = str(Path(tmp) / "catalog.json")
            os.environ["SPIRITKIN_SCHEDULER_BENCHMARK_HISTORY_PATH"] = str(Path(tmp) / "scheduler-benchmarks.jsonl")
            try:
                status, payload = build_model_catalog_response()
                self.assertEqual(status, 200)
                self.assertGreater(payload["model_catalog"]["models"][0]["priority"], 0)
                self.assertEqual(payload["local_model_policy"]["schema_version"], "spiritkin.local_model_policy.v1")
                self.assertEqual(payload["local_model_policy"]["scheduler_benchmark"]["status"], "not_run")
                self.assertEqual(payload["brain_replacement"]["schema_version"], "spiritkin.replaceable_brain.v1")

                with patch("backend.app.model_catalog.fetch_huggingface_model_info", return_value={"id": "Qwen/Qwen3-VL-8B-Instruct", "downloads": 12, "tags": ["vision"]}):
                    status, payload = build_model_catalog_update_response({"action": "refresh", "model_ids": ["Qwen/Qwen3-VL-8B-Instruct"]})
                benchmark_status, benchmark_payload = build_model_catalog_update_response(
                    {
                        "action": "evaluate_scheduler_benchmark",
                        "outputs_by_case_id": {
                            "json_validity_route_plan": {"route": "tool", "tool_calls": [], "workflow_steps": [], "confidence": 0.9},
                            "tool_call_accuracy_browser": {"route": "executor", "tool_calls": [{"name": "browser.open_url"}]},
                            "workflow_step_completeness_publish": {"route": "workflow", "workflow_steps": ["intake", "asset_check", "review_gate", "upload_product"]},
                            "context_drift_followup": {"route": "agent", "context_retained_ids": ["order-42", "ecom-demo"], "irrelevant_context_ids": []},
                        },
                    }
                )
                replacement_status, replacement_payload = build_model_catalog_update_response(
                    {
                        "action": "evaluate_brain_replacement",
                        "current_adapter_id": "base_qwen35b",
                        "candidate_adapter": {
                            "adapter_id": "lora_publish_v2",
                            "adapter_type": "lora",
                            "artifact_path": "outputs/publish-lora",
                            "review_state": "approved",
                        },
                        "benchmark_results": [
                            {"case_id": "publish_product_workflow_assets", "capability_id": "publish_product", "score": 96, "passed": True, "critical": True},
                            {"case_id": "run_tests_tool_boundary", "capability_id": "run_tests", "score": 92, "passed": True, "critical": True},
                        ],
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_MODEL_CATALOG_PATH", None)
                else:
                    os.environ["SPIRITKIN_MODEL_CATALOG_PATH"] = previous
                if previous_history is None:
                    os.environ.pop("SPIRITKIN_SCHEDULER_BENCHMARK_HISTORY_PATH", None)
                else:
                    os.environ["SPIRITKIN_SCHEDULER_BENCHMARK_HISTORY_PATH"] = previous_history

        self.assertEqual(status, 200)
        catalog = payload["model_catalog"]
        self.assertTrue(catalog["online"])
        self.assertEqual(catalog["models"][0]["metadata"]["downloads"], 12)
        self.assertEqual(benchmark_status, 200)
        self.assertTrue(benchmark_payload["scheduler_benchmark_result"]["passed"])
        self.assertEqual(replacement_status, 200)
        self.assertTrue(replacement_payload["brain_replacement_decision"]["allowed"])

    def test_desktop_knowledge_base_endpoint_can_import_and_index_files(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_agent = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.chdir(tmp)
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                status, payload = build_desktop_knowledge_base_response()
                import_status, import_payload = build_desktop_knowledge_base_update_response(
                    {
                        "action": "import_files",
                        "knowledge_base_id": "kb_unit",
                        "path": "state/knowledge_bases/custom/kb_unit",
                        "files": [{"path": "note.md", "text": "统一模块管理需要可索引知识库。"}],
                    }
                )
                index_exists = Path("state/knowledge_bases/custom/kb_unit/.spiritkin_kb_index.json").exists()
            finally:
                os.chdir(previous_cwd)
                if previous_agent is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_agent

        self.assertEqual(status, 200)
        self.assertTrue(payload["knowledge_base"]["knowledge_bases"])
        self.assertEqual(import_status, 200)
        self.assertEqual(import_payload["import"]["count"], 1)
        self.assertEqual(import_payload["index"]["document_count"], 1)
        self.assertTrue(index_exists)

    def test_desktop_module_management_aggregates_major_management_modules(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_env = {
                key: os.environ.get(key)
                for key in (
                    "SPIRITKIN_SKILL_STORE_PATH",
                    "SPIRITKIN_AGENT_MANAGEMENT_PATH",
                    "SPIRITKIN_MODEL_CATALOG_PATH",
                    "SPIRITKIN_MODEL_PROVIDER_STATE",
                    "SPIRITKIN_ASSIST_MODEL_STATE",
                    "SPIRITKIN_ECOSYSTEM_REVIEW_STATE",
                    "SPIRITKIN_EVOLUTION_STATE",
                    "SPIRITKIN_TRAJECTORY_LOG",
                    "SPIRITKIN_FAILURE_SAMPLE_LOG",
                    "SPIRITKIN_EVOLUTION_EVAL_CASES",
                    "SPIRITKIN_EVOLUTION_DATASET",
                )
            }
            os.chdir(tmp)
            os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(Path(tmp) / "skills.jsonl")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_MODEL_CATALOG_PATH"] = str(Path(tmp) / "model_catalog.json")
            os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = str(Path(tmp) / "model_provider.json")
            os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = str(Path(tmp) / "assist_models.json")
            os.environ["SPIRITKIN_ECOSYSTEM_REVIEW_STATE"] = str(Path(tmp) / "ecosystem_reviews.json")
            os.environ["SPIRITKIN_EVOLUTION_STATE"] = str(Path(tmp) / "evolution.json")
            os.environ["SPIRITKIN_TRAJECTORY_LOG"] = str(Path(tmp) / "trajectories.jsonl")
            os.environ["SPIRITKIN_FAILURE_SAMPLE_LOG"] = str(Path(tmp) / "failures.jsonl")
            os.environ["SPIRITKIN_EVOLUTION_EVAL_CASES"] = str(Path(tmp) / "eval_cases.jsonl")
            os.environ["SPIRITKIN_EVOLUTION_DATASET"] = str(Path(tmp) / "evolution_dataset.jsonl")
            try:
                status, payload = build_desktop_module_management_response()
                scan_status, scan_payload = build_desktop_module_management_update_response({"action": "scan"})
            finally:
                os.chdir(previous_cwd)
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(status, 200)
        management = payload["module_management"]
        self.assertEqual(management["schema_version"], "spiritkin.module_management.v2")
        module_ids = {module["module_id"] for module in management["modules"]}
        self.assertTrue({"evolution", "workflows", "skills", "skill_router", "agents", "knowledge_base", "search_management", "models", "module_governance"}.issubset(module_ids))
        modules_by_id = {module["module_id"]: module for module in management["modules"]}
        self.assertIn("portfolio", management)
        self.assertIn("risk_counts", management["portfolio"])
        self.assertEqual(management["overview"]["health_score"], management["portfolio"]["health_score"])
        self.assertEqual(modules_by_id["workflows"]["owner_role"], "Workflow Operator")
        self.assertIn(modules_by_id["workflows"]["risk_level"], {"low", "medium", "high"})
        self.assertIn("governance_state", modules_by_id["workflows"])
        self.assertIn("health_score", modules_by_id["workflows"])
        self.assertTrue(management["action_items"])
        self.assertIn("operator_hint", management["action_items"][0])
        self.assertIn("owner_role", management["action_items"][0])
        self.assertIn("risk_level", management["action_items"][0])
        self.assertEqual(management["source_endpoints"]["evolution"], "/desktop/evolution")
        self.assertEqual(management["source_endpoints"]["skill_router"], "/desktop/skill-router")
        self.assertEqual(management["source_endpoints"]["knowledge_base"], "/desktop/knowledge-base")
        self.assertEqual(management["source_endpoints"]["search_management"], "/desktop/search-management")
        self.assertEqual(management["source_endpoints"]["workflows"], "/desktop/workflows")
        self.assertGreaterEqual(management["overview"]["module_count"], 7)
        self.assertEqual(scan_status, 200)
        self.assertIn("ecosystem_review", scan_payload)
        self.assertIn("module_management", scan_payload)

    def test_desktop_workflows_endpoint_can_save_definition_and_start_run(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                status, payload = build_desktop_workflows_response()
                save_status, save_payload = build_desktop_workflows_update_response({"action": "save_ecommerce_definition"})
                save_video_status, save_video_payload = build_desktop_workflows_update_response({"action": "save_builtin_definition", "workflow_name": "content.video_generation.v1"})
                start_status, start_payload = build_desktop_workflows_update_response(
                    {
                        "action": "start_run",
                        "workflow_name": "content.video_generation.v1",
                        "inputs": {"prompt": "生成一个商品短视频", "duration_seconds": 8, "project_root": str(Path(tmp).resolve())},
                    }
                )
                custom_status, custom_payload = build_desktop_workflows_update_response(
                    {
                        "action": "upsert_definition",
                        "definition": {
                            "name": "custom.desktop.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {
                                    "node_id": "start",
                                    "node_type": "agent_task",
                                    "label": "Start",
                                    "assigned_agent": "programming",
                                    "arguments": {},
                                    "depends_on": [],
                                }
                            ],
                            "metadata": {"display_name": "Custom Desktop", "category": "custom"},
                        },
                    }
                )
                compose_status, compose_payload = build_desktop_workflows_update_response(
                    {
                        "action": "compose_definition",
                        "workflow_name": "custom.desktop.combo.v1",
                        "display_name": "Desktop Combo",
                        "components": ["ecommerce.auto_listing.v1", "content.video_generation.v1"],
                    }
                )
                delete_status, delete_payload = build_desktop_workflows_update_response({"action": "delete_definition", "workflow_name": "custom.desktop.v1"})
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(status, 200)
        self.assertEqual(payload["workflows"]["overview"]["definition_count"], 0)
        self.assertEqual(payload["workflows"]["overview"]["builtin_definition_count"], 4)
        self.assertEqual(save_status, 200)
        self.assertTrue(save_payload["ok"])
        self.assertEqual(save_payload["workflows"]["overview"]["definition_count"], 1)
        self.assertEqual(save_video_status, 200)
        self.assertTrue(save_video_payload["ok"])
        self.assertEqual(save_video_payload["workflows"]["overview"]["definition_count"], 2)
        self.assertEqual(start_status, 200)
        self.assertTrue(start_payload["ok"])
        self.assertEqual(start_payload["workflows"]["overview"]["run_count"], 1)
        self.assertEqual(start_payload["workflows"]["runs"][0]["workflow_name"], "content.video_generation.v1")
        run = start_payload["workflows"]["runs"][0]
        self.assertIn("progress", run)
        first_detail = next(iter(run["selected_node_details"].values()))
        self.assertIn("progress", first_detail)
        self.assertIn("agent_task_queue", first_detail)
        self.assertIn("available_skills", first_detail)
        self.assertIn("repair_suggestions", first_detail)
        self.assertEqual(custom_status, 200)
        self.assertTrue(custom_payload["ok"])
        self.assertIn("custom.desktop.v1", custom_payload["workflows"]["saved_definition_names"])
        self.assertEqual(compose_status, 200)
        self.assertTrue(compose_payload["ok"])
        self.assertIn("custom.desktop.combo.v1", compose_payload["workflows"]["saved_definition_names"])
        self.assertEqual(delete_status, 200)
        self.assertTrue(delete_payload["ok"])
        self.assertNotIn("custom.desktop.v1", delete_payload["workflows"]["saved_definition_names"])

    def test_desktop_search_management_reports_rag_gaps_and_runtime_config(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_env = {
                key: os.environ.get(key)
                for key in (
                    "SPIRITKIN_AGENT_MANAGEMENT_PATH",
                    "SPIRITKIN_MODEL_CATALOG_PATH",
                    "SPIRITKIN_WEB_SEARCH_PROVIDER",
                    "SPIRIT_KNOWLEDGE_BACKEND",
                    "SPIRITKIN_EMBEDDING_PROVIDER",
                    "SPIRITKIN_EMBEDDING_MODEL",
                    "SPIRITKIN_EMBEDDING_BASE_URL",
                    "SPIRITKIN_RERANKER_PROVIDER",
                    "SPIRITKIN_RERANKER_MODEL",
                    "SPIRITKIN_RERANKER_BASE_URL",
                )
            }
            os.chdir(tmp)
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_MODEL_CATALOG_PATH"] = str(Path(tmp) / "model_catalog.json")
            try:
                os.environ["SPIRITKIN_EMBEDDING_PROVIDER"] = "hashing"
                status, payload = build_desktop_search_management_response()
                save_status, save_payload = build_desktop_search_management_update_response(
                    {
                        "action": "save_runtime_config",
                        "web_search_provider": "duckduckgo",
                        "knowledge_backend": "embedding",
                        "embedding_provider": "lmstudio",
                        "embedding_model": "qwen3-embedding",
                        "embedding_base_url": "http://127.0.0.1:1234/v1",
                        "reranker": "lmstudio",
                        "reranker_model": "qwen3-reranker",
                        "reranker_base_url": "http://127.0.0.1:1234/v1",
                    }
                )
                module_status, module_payload = build_desktop_module_management_response()
            finally:
                os.chdir(previous_cwd)
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(status, 200)
        self.assertEqual(payload["search_management"]["schema_version"], "spiritkin.search_management.v1")
        self.assertIn("embedding_hashing", {item["gap_id"] for item in payload["search_management"]["missing_capabilities"]})
        self.assertIn("embedding_dev_fallback_allowed", payload["search_management"]["knowledge_retrieval"])
        self.assertIn("embedding_runtime", payload["search_management"]["knowledge_retrieval"])
        self.assertIn("embedding_evaluation", payload["search_management"]["knowledge_retrieval"])
        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["search_management"]["knowledge_retrieval"]["backend"], "embedding")
        self.assertEqual(save_payload["search_management"]["knowledge_retrieval"]["embedding_provider"], "lmstudio")
        self.assertEqual(save_payload["search_management"]["knowledge_retrieval"]["reranker"], "lmstudio")
        self.assertEqual(save_payload["search_management"]["knowledge_retrieval"]["embedding_runtime"]["service_count"], 0)
        self.assertEqual(module_status, 200)
        module_ids = {module["module_id"] for module in module_payload["module_management"]["modules"]}
        self.assertIn("search_management", module_ids)

    def test_agent_management_merges_system_wiki_namespaces(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_env = {"SPIRITKIN_AGENT_MANAGEMENT_PATH": os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")}
            os.chdir(tmp)
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                save_status, save_payload = build_desktop_agent_management_update_response(
                    {
                        "knowledge_bases": [
                            {
                                "knowledge_base_id": "kb_custom",
                                "label": "Custom KB",
                                "path": "state/knowledge_bases/custom",
                                "shared_scope": "global",
                            }
                        ]
                    }
                )
                status, payload = build_desktop_agent_management_response()
            finally:
                os.chdir(previous_cwd)
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(save_status, 200)
        self.assertTrue(save_payload["ok"])
        self.assertEqual(status, 200)
        kb_ids = {item["knowledge_base_id"] for item in payload["agent_management"]["knowledge_bases"]}
        self.assertIn("kb_custom", kb_ids)
        self.assertIn("wiki_agent_registry", kb_ids)
        self.assertIn("wiki_model_registry", kb_ids)
        self.assertIn("wiki_project_knowledge", kb_ids)

    def test_desktop_skills_can_seed_studio_workflow_candidates(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_env = {
                "SPIRITKIN_SKILL_STORE_PATH": os.environ.get("SPIRITKIN_SKILL_STORE_PATH"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH"),
            }
            os.chdir(tmp)
            os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(Path(tmp) / "skills.jsonl")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                status, payload = build_desktop_skills_update_response({"action": "seed_studio_workflow_skills"})
            finally:
                os.chdir(previous_cwd)
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["created_count"], 6)
        skills = {item["name"]: item for item in payload["skills"]["skills"]}
        self.assertIn("studio.gate_check.workflow", skills)
        self.assertEqual(skills["studio.gate_check.workflow"]["status"], "candidate")
        self.assertEqual(skills["studio.gate_check.workflow"]["source_type"], "claude_code_game_studios_reference")

    def test_skill_router_routes_active_skill_and_builds_workflow_orchestration(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_env = {
                "SPIRITKIN_SKILL_STORE_PATH": os.environ.get("SPIRITKIN_SKILL_STORE_PATH"),
                "SPIRITKIN_AGENT_MANAGEMENT_PATH": os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH"),
            }
            os.chdir(tmp)
            os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(Path(tmp) / "skills.jsonl")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                save_status, save_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.router.productdata",
                        "description": "生成商品 productData",
                        "status": "active",
                        "owner_agent_id": "ecommerce",
                        "workspace_path": "state/agents/ecommerce/workspace/skills",
                        "trigger_intents": ["生成商品数据", "productData"],
                        "required_capabilities": ["publish_product"],
                        "required_worker_needs": ["python_runtime"],
                        "side_effects": ["writes_artifact"],
                        "output_schema": {"productData": "dict"},
                        "artifact_contract": {"outputs": ["productData"]},
                        "latency_hint_ms": 1200,
                        "success_rate": 0.91,
                        "steps": [],
                    }
                )
                snapshot_status, snapshot_payload = build_desktop_skill_router_response()
                route_status, route_payload = build_desktop_skill_router_update_response(
                    {
                        "action": "route",
                        "request": "请生成商品数据 productData",
                        "agent_id": "ecommerce",
                        "inputs": {"task_id": "task-1"},
                    }
                )
                orchestration_status, orchestration_payload = build_desktop_skill_router_update_response(
                    {
                        "action": "orchestrate",
                        "request": "请生成商品数据 productData",
                        "agent_id": "ecommerce",
                        "inputs": {"task_id": "task-1"},
                    }
                )
            finally:
                os.chdir(previous_cwd)
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["skill"]["status"], "active")
        self.assertEqual(save_payload["skill"]["required_capabilities"], ["publish_product"])
        self.assertEqual(save_payload["skill"]["required_worker_needs"], ["python_runtime"])
        self.assertEqual(save_payload["skill"]["output_schema"], {"productData": "dict"})
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot_payload["skill_router"]["routable_skill_count"], 1)
        self.assertEqual(route_status, 200)
        self.assertTrue(route_payload["ok"])
        self.assertEqual(route_payload["skill_route"]["selected"]["skill_name"], "workflow.router.productdata")
        self.assertEqual(route_payload["skill_route"]["context"]["inputs"]["task_id"], "task-1")
        self.assertEqual(orchestration_status, 200)
        workflow = orchestration_payload["skill_orchestration"]["workflow_definition"]
        self.assertEqual(workflow["nodes"][0]["node_type"], "skill_call")
        self.assertEqual(workflow["nodes"][0]["skill_name"], "workflow.router.productdata")

    def test_desktop_evolution_records_exports_and_enforces_skill_ownership(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_env = {
                key: os.environ.get(key)
                for key in (
                    "SPIRITKIN_SKILL_STORE_PATH",
                    "SPIRITKIN_AGENT_MANAGEMENT_PATH",
                    "SPIRITKIN_MODEL_PROVIDER_STATE",
                    "SPIRITKIN_ASSIST_MODEL_STATE",
                    "SPIRITKIN_EVOLUTION_STATE",
                    "SPIRITKIN_TRAJECTORY_LOG",
                    "SPIRITKIN_FAILURE_SAMPLE_LOG",
                    "SPIRITKIN_EVOLUTION_DATASET",
                    "SPIRITKIN_DATASET_REGISTRY_PATH",
                    "SPIRITKIN_LEARNING_ARTIFACT_LOG",
                    "SPIRITKIN_EVOLUTION_JOB_LOG",
                    "SPIRITKIN_REVIEW_GATE_LOG",
                    "SPIRITKIN_CLOUD_TRAINING_DIR",
                    "SPIRIT_TEXT_PROVIDER",
                    "SPIRIT_TEXT_MODEL",
                    "SPIRITKIN_TEXT_BASE_URL",
                    "SPIRIT_VISION_PROVIDER",
                    "SPIRIT_VISION_MODEL",
                    "SPIRIT_VISION_BASE_URL",
                    "SPIRIT_VISION_API_KEY",
                )
            }
            os.chdir(tmp)
            os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(Path(tmp) / "skills.jsonl")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = str(Path(tmp) / "model_provider.json")
            os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = str(Path(tmp) / "assist_models.json")
            os.environ["SPIRITKIN_EVOLUTION_STATE"] = str(Path(tmp) / "evolution.json")
            os.environ["SPIRITKIN_TRAJECTORY_LOG"] = str(Path(tmp) / "trajectories.jsonl")
            os.environ["SPIRITKIN_FAILURE_SAMPLE_LOG"] = str(Path(tmp) / "failures.jsonl")
            os.environ["SPIRITKIN_EVOLUTION_EVAL_CASES"] = str(Path(tmp) / "eval_cases.jsonl")
            os.environ["SPIRITKIN_EVOLUTION_DATASET"] = str(Path(tmp) / "evolution_dataset.jsonl")
            os.environ["SPIRITKIN_DATASET_REGISTRY_PATH"] = str(Path(tmp) / "datasets.jsonl")
            os.environ["SPIRITKIN_LEARNING_ARTIFACT_LOG"] = str(Path(tmp) / "learning_artifacts.jsonl")
            os.environ["SPIRITKIN_EVOLUTION_JOB_LOG"] = str(Path(tmp) / "jobs.jsonl")
            os.environ["SPIRITKIN_REVIEW_GATE_LOG"] = str(Path(tmp) / "review_gate.jsonl")
            os.environ["SPIRITKIN_CLOUD_TRAINING_DIR"] = str(Path(tmp) / "cloud_packages")
            os.environ["SPIRIT_TEXT_PROVIDER"] = "openai_compatible"
            os.environ["SPIRIT_TEXT_MODEL"] = "qwen-local"
            os.environ["SPIRITKIN_TEXT_BASE_URL"] = "http://127.0.0.1:1234/v1"
            os.environ["SPIRIT_VISION_PROVIDER"] = "openai_compatible"
            os.environ["SPIRIT_VISION_MODEL"] = "qwen-vl-local"
            os.environ["SPIRIT_VISION_BASE_URL"] = "http://127.0.0.1:1234/v1"
            os.environ["SPIRIT_VISION_API_KEY"] = "lm-studio"
            eval_cases_env_path = os.environ["SPIRITKIN_EVOLUTION_EVAL_CASES"]
            try:
                save_status, save_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.code.test",
                        "description": "代码生成测试 Skill",
                        "status": "candidate",
                        "owner_agent_id": "programming",
                        "workspace_path": "state/agents/programming/workspace/skills",
                        "steps": [],
                    }
                )
                record_status, record_payload = build_desktop_evolution_update_response(
                    {
                        "action": "record_trajectory",
                        "user_input": "修复测试失败",
                        "agent_id": "programming",
                        "domain": "programming",
                        "overall_success": False,
                        "score": 0.2,
                        "bottleneck_stage": "validate",
                        "steps": [{"stage": "validate", "success": False, "error_code": "unit_failed", "detail": "tests failed"}],
                    }
                )
                eval_export_status, eval_export_payload = build_desktop_evolution_update_response({"action": "export_eval_cases"})
                eval_export_exists = Path(eval_export_payload["eval_cases"]["path"]).exists()
                export_status, export_payload = build_desktop_evolution_update_response({"action": "export_self_training_dataset"})
                export_exists = Path(export_payload["dataset"]["path"]).exists()
                ownership_status, ownership_payload = build_desktop_evolution_update_response({"action": "enforce_skill_ownership"})
                blocked_cloud_status, blocked_cloud_payload = build_desktop_evolution_update_response({"action": "build_cloud_training_package", "package_id": "unit-evolution-blocked"})
                cloud_status, cloud_payload = build_desktop_evolution_update_response({"action": "build_cloud_training_package", "package_id": "unit-evolution", "core_review_approved": True, "reviewer": "unit-test"})
                cloud_manifest_exists = Path(cloud_payload["cloud_training_package"]["manifest_path"]).exists()
                with patch("backend.app.evolution_management._post_openai_compatible_chat") as model_chat:
                    model_chat.side_effect = [
                        json.dumps(
                            {
                                "summary": "模型提炼后的 Paper2Agent 方法。",
                                "extracted_actions": ["extract model method", "create candidate skill", "run model eval"],
                                "skill_description": "由 LM Studio 文本模型提炼的论文 Skill。",
                                "eval_cases": ["paper method extraction eval"],
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "summary": "模型识别出视频里的导入流程。",
                                "operation_sequence": [
                                    {"action": "click", "target": "button#import", "confidence": 0.91},
                                    {"action": "input", "target": "input#title", "value": "Demo", "confidence": 0.84},
                                    {"action": "key", "value": "ENTER", "confidence": 0.8},
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    ]
                    paper_status, paper_payload = build_desktop_evolution_update_response(
                        {
                            "action": "ingest_paper",
                            "title": "Paper2Agent style method extraction",
                            "source": "paper://unit",
                            "owner_agent_id": "programming",
                            "summary": "把论文方法提炼为结构化知识、候选 Skill 和 eval。",
                        }
                    )
                    video_status, video_payload = build_desktop_evolution_update_response(
                        {
                            "action": "ingest_video",
                            "title": "UI click workflow",
                            "source": "video://unit",
                            "owner_agent_id": "video_animation",
                            "frames": ["data:image/png;base64,AAAA"],
                        }
                    )
                retry_status, retry_payload = build_desktop_evolution_update_response(
                    {
                        "action": "retry_artifact_job",
                        "job_id": paper_payload["learning_artifact"]["job_id"],
                        "artifact_type": "paper",
                        "title": "Paper2Agent style method extraction retry",
                        "source": "paper://unit-retry",
                        "owner_agent_id": "programming",
                        "summary": "重试时复用原 job_id 并重新生成候选。",
                    }
                )
                template_status, template_payload = build_desktop_evolution_update_response({"action": "seed_domain_skill_templates"})
                get_status, get_payload = build_desktop_evolution_response()
                artifact_log_exists = Path(os.environ["SPIRITKIN_LEARNING_ARTIFACT_LOG"]).exists()
                job_log_exists = Path(os.environ["SPIRITKIN_EVOLUTION_JOB_LOG"]).exists()
                paper_workspace_exists = Path(paper_payload["learning_artifact"]["workspace_resolved_path"], "skill_candidate.json").exists()
            finally:
                os.chdir(previous_cwd)
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["skill"]["owner_agent_id"], "programming")
        self.assertEqual(record_status, 200)
        self.assertEqual(record_payload["trajectory_record"]["bottleneck_stage"], "validate")
        self.assertEqual(eval_export_status, 200)
        self.assertTrue(eval_export_exists)
        self.assertGreaterEqual(eval_export_payload["eval_cases"]["count"], 1)
        self.assertEqual(eval_export_payload["eval_cases"]["cases"][0]["source"], "trajectory")
        self.assertEqual(export_status, 200)
        self.assertTrue(export_exists)
        self.assertTrue(export_payload["dataset_gate"]["allowed"])
        self.assertEqual(export_payload["dataset_card"]["status"], "training_ready")
        self.assertEqual(Path(export_payload["dataset_card"]["linked_eval_report"]).resolve(), Path(eval_cases_env_path).resolve())
        self.assertEqual(export_payload["dataset_registry"]["dataset_count"], 1)
        self.assertEqual(ownership_status, 200)
        self.assertEqual(ownership_payload["evolution"]["agent_skill_distribution"]["missing_owner_count"], 0)
        self.assertEqual(blocked_cloud_status, 200)
        self.assertFalse(blocked_cloud_payload["ok"])
        self.assertEqual(blocked_cloud_payload["error"], "review_required")
        self.assertEqual(cloud_status, 200)
        self.assertTrue(cloud_payload["dataset_gate"]["allowed"])
        self.assertTrue(cloud_manifest_exists)
        self.assertEqual(paper_status, 200)
        self.assertEqual(paper_payload["learning_artifact"]["owner_agent_id"], "programming")
        self.assertEqual(paper_payload["learning_artifact"]["model_extraction"]["status"], "ok")
        self.assertEqual(paper_payload["learning_artifact"]["summary"], "模型提炼后的 Paper2Agent 方法。")
        self.assertTrue(paper_payload["learning_artifact"]["skill_candidate"]["name"].startswith("artifact.paper.programming."))
        self.assertTrue(paper_workspace_exists)
        self.assertEqual(video_status, 200)
        self.assertEqual(video_payload["learning_artifact"]["owner_agent_id"], "video_animation")
        self.assertEqual(video_payload["learning_artifact"]["model_extraction"]["status"], "ok")
        self.assertGreaterEqual(len(video_payload["learning_artifact"]["operation_sequence"]), 3)
        self.assertEqual(video_payload["learning_artifact"]["operation_sequence"][0]["target"], "button#import")
        self.assertEqual(video_payload["learning_artifact"]["skill_candidate"]["metadata"]["ui_binding_status"], "required")
        self.assertEqual(video_payload["learning_artifact"]["skill_candidate"]["metadata"]["model_extraction_status"], "ok")
        self.assertEqual(retry_status, 200)
        self.assertEqual(retry_payload["learning_artifact"]["job_id"], paper_payload["learning_artifact"]["job_id"])
        self.assertEqual(template_status, 200)
        self.assertGreaterEqual(template_payload["domain_skill_templates"]["seeded_count"], 1)
        self.assertEqual(get_status, 200)
        self.assertEqual(get_payload["evolution"]["schema_version"], "spiritkin.evolution_management.v1")
        self.assertTrue(artifact_log_exists)
        self.assertTrue(job_log_exists)
        self.assertGreaterEqual(get_payload["evolution"]["learning_artifacts"]["artifact_count"], 3)
        self.assertGreaterEqual(get_payload["evolution"]["jobs"]["job_count"], 2)
        self.assertGreaterEqual(get_payload["evolution"]["review_gate_audit"]["record_count"], 2)
        self.assertGreaterEqual(get_payload["evolution"]["review_gate_audit"]["denied_count"], 1)
        self.assertEqual(get_payload["evolution"]["domain_skill_templates"]["missing_count"], 0)
        self.assertTrue(get_payload["evolution"]["capabilities"]["eval_case_export"])
        self.assertGreaterEqual(get_payload["evolution"]["eval_cases_export"]["count"], 1)

    def test_desktop_evolution_review_gate_parses_string_booleans(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous = os.environ.get("SPIRITKIN_EVOLUTION_STATE")
            os.chdir(tmp)
            os.environ["SPIRITKIN_EVOLUTION_STATE"] = str(Path(tmp) / "evolution.json")
            try:
                status, payload = build_desktop_evolution_update_response(
                    {
                        "action": "save_review_gate",
                        "core_review_required": "false",
                        "auto_promote_skill": "true",
                        "allow_training_schedule": "false",
                    }
                )
            finally:
                os.chdir(previous_cwd)
                if previous is None:
                    os.environ.pop("SPIRITKIN_EVOLUTION_STATE", None)
                else:
                    os.environ["SPIRITKIN_EVOLUTION_STATE"] = previous

        self.assertEqual(status, 200)
        self.assertFalse(payload["review_gate"]["core_review_required"])
        self.assertTrue(payload["review_gate"]["auto_promote_skill"])
        self.assertFalse(payload["review_gate"]["allow_training_schedule"])

    def test_desktop_state_response_has_default_session(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(Path(tmp) / "state.json")
            try:
                status, payload = build_desktop_state_response()
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["active_session_id"], "session_default")
        self.assertEqual(payload["state"]["sessions"][0]["title"], "主会话")

    def test_desktop_state_update_merges_sessions_projects_and_tasks(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(Path(tmp) / "state.json")
            try:
                status, payload = build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "active_session_id": "session_a",
                        "sessions": [
                            {
                                "id": "session_a",
                                "title": "测试会话",
                                "messages": [{"role": "user", "text": "你好"}],
                            }
                        ],
                        "projects": [{"id": "project_a", "title": "桌面端"}],
                        "tasks": [{"id": "task_a", "title": "同步测试", "status": "running"}],
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["active_session_id"], "session_a")
        self.assertEqual(payload["state"]["sessions"][-1]["messages"][0]["text"], "你好")
        self.assertEqual(payload["state"]["projects"][-1]["title"], "桌面端")
        self.assertEqual(payload["state"]["tasks"][-1]["status"], "running")
        self.assertEqual(payload["event"]["type"], "desktop.state_updated")

    def test_desktop_state_preserves_project_runtime_profile(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(Path(tmp) / "state.json")
            try:
                status, payload = build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "projects": [
                            {
                                "id": "project_runtime",
                                "title": "运行隔离项目",
                                "workspace_path": "D:/work/runtime",
                                "env_file_path": ".env.local",
                                "dependency_file_path": "requirements.txt",
                                "package_manager": "uv",
                                "start_command": "python app.py",
                            }
                        ],
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous

        project = payload["state"]["projects"][-1]
        self.assertEqual(status, 200)
        self.assertEqual(project["workspace_path"], "D:/work/runtime")
        self.assertEqual(project["env_file_path"], ".env.local")
        self.assertEqual(project["dependency_file_path"], "requirements.txt")
        self.assertEqual(project["package_manager"], "uv")
        self.assertEqual(project["start_command"], "python app.py")

    def test_desktop_project_runtime_blocks_workspace_escape_and_records_safe_start(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_DESKTOP_STATE_PATH": os.path.join(tmp, "desktop-state.json"),
                "SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG": os.path.join(tmp, "project-runtime-audit.jsonl"),
            },
            clear=False,
        ):
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            state_status, state_payload = build_desktop_state_update_response(
                {
                    "client_id": "desktop-a",
                    "projects": [
                        {
                            "id": "project_runtime",
                            "title": "运行隔离项目",
                            "workspace_path": str(workspace),
                            "package_manager": "npm",
                            "start_command": "npm run dev",
                        }
                    ],
                }
            )
            snapshot_status, snapshot_payload = build_desktop_project_runtime_response()
            blocked_status, blocked_payload = build_desktop_project_runtime_update_response(
                {
                    "action": "evaluate_start_command",
                    "project": {
                        "id": "project_runtime",
                        "title": "运行隔离项目",
                        "workspace_path": str(workspace),
                        "start_command": "cd ..; npm run dev",
                    },
                }
            )
            record_status, record_payload = build_desktop_project_runtime_update_response(
                {
                    "action": "record_start_command",
                    "status": "started",
                    "actor": "desktop_test",
                    "project": {
                        "id": "project_runtime",
                        "title": "运行隔离项目",
                        "workspace_path": str(workspace),
                        "start_command": "npm run dev",
                    },
                }
            )
            log_status, log_payload = build_desktop_action_log_response(limit=20, project_root=tmp)

        self.assertEqual(state_status, 200)
        self.assertTrue(state_payload["ok"])
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot_payload["project_runtime"]["schema_version"], "spiritkin.project_runtime.v1")
        self.assertEqual(snapshot_payload["project_runtime"]["project_count"], 1)
        self.assertEqual(blocked_status, 200)
        self.assertFalse(blocked_payload["ok"])
        self.assertEqual(blocked_payload["status"], "blocked")
        blocker_ids = {item["issue_id"] for item in blocked_payload["execution_policy"]["blockers"]}
        self.assertIn("project_command_workspace_escape", blocker_ids)
        self.assertEqual(record_status, 200)
        self.assertTrue(record_payload["ok"])
        self.assertEqual(record_payload["audit_event"]["status"], "started")
        self.assertEqual(record_payload["audit_event"]["project_id"], "project_runtime")
        self.assertEqual(log_status, 200)
        self.assertIn("project_runtime", {item["source"] for item in log_payload["action_log"]["events"]})

    def test_desktop_state_normalizes_legacy_pending_confirmation_fields(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(Path(tmp) / "state.json")
            try:
                status, payload = build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "pending": {
                            "pending_target": "local_pc",
                            "pending_operation": "browser_search",
                            "risk_level": "medium",
                        },
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        pending = payload["state"]["pending"]
        self.assertEqual(pending["target"], "local_pc")
        self.assertEqual(pending["operation"], "browser_search")
        self.assertEqual(pending["pending_target"], "local_pc")
        self.assertEqual(pending["pending_operation"], "browser_search")

    def test_desktop_state_clears_matching_pending_after_successful_execution(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(Path(tmp) / "state.json")
            try:
                build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "pending": {
                            "pending_target": "local_pc",
                            "pending_operation": "browser_search",
                            "risk_level": "medium",
                        },
                    }
                )
                status, payload = build_desktop_state_update_response(
                    {
                        "client_id": "desktop-runtime",
                        "lastExecution": {
                            "target": "local_pc",
                            "operation": "browser_search",
                            "success": True,
                        },
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        self.assertIsNone(payload["state"]["pending"])
        self.assertEqual(payload["state"]["lastExecution"]["operation"], "browser_search")

    def test_desktop_state_update_honors_deleted_ids(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(Path(tmp) / "state.json")
            try:
                build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "active_session_id": "session_keep",
                        "sessions": [
                            {"id": "session_delete", "title": "删除我"},
                            {"id": "session_keep", "title": "保留我"},
                        ],
                        "projects": [{"id": "project_delete", "title": "删除项目"}],
                        "tasks": [{"id": "task_delete", "title": "删除任务"}],
                    }
                )
                status, payload = build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "active_session_id": "session_keep",
                        "sessions": [{"id": "session_keep", "title": "保留我"}],
                        "projects": [],
                        "tasks": [],
                        "deleted_session_ids": ["session_delete"],
                        "deleted_project_ids": ["project_delete"],
                        "deleted_task_ids": ["task_delete"],
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertNotIn("session_delete", {item["id"] for item in payload["state"]["sessions"]})
        self.assertNotIn("project_delete", {item["id"] for item in payload["state"]["projects"]})
        self.assertNotIn("task_delete", {item["id"] for item in payload["state"]["tasks"]})

    def test_desktop_state_update_honors_deleted_message_ids(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(Path(tmp) / "state.json")
            try:
                build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "active_session_id": "session_a",
                        "sessions": [
                            {
                                "id": "session_a",
                                "title": "测试会话",
                                "messages": [
                                    {"id": "msg_user", "role": "user", "text": "你好", "created_at": 1},
                                    {"id": "msg_work", "role": "system", "kind": "work", "text": "Working", "created_at": 2},
                                    {"id": "msg_old_reply", "role": "assistant", "text": "旧回答", "created_at": 3},
                                ],
                            }
                        ],
                    }
                )
                status, payload = build_desktop_state_update_response(
                    {
                        "client_id": "desktop-a",
                        "active_session_id": "session_a",
                        "sessions": [
                            {
                                "id": "session_a",
                                "title": "测试会话",
                                "messages": [
                                    {"id": "msg_user", "role": "user", "text": "你是谁", "created_at": 1},
                                    {"id": "msg_new_reply", "role": "assistant", "text": "新回答", "created_at": 4},
                                ],
                            }
                        ],
                        "deleted_message_ids": {"session_a": ["msg_work", "msg_old_reply"]},
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        messages = payload["state"]["sessions"][-1]["messages"]
        self.assertEqual([message["id"] for message in messages], ["msg_user", "msg_new_reply"])
        self.assertEqual(messages[0]["text"], "你是谁")

    def test_desktop_diagnostics_response_reports_service_checks(self):
        status, payload = build_desktop_diagnostics_response()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        names = {check["name"] for check in payload["diagnostics"]["checks"]}
        self.assertIn("python", names)
        self.assertIn("desktop_state", names)
        self.assertIn("avatar_3d_manifest_model", names)

    def test_desktop_diagnostics_reports_missing_avatar_model_with_repair_command(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frontend" / "models" / "spirit3d" / "reference").mkdir(parents=True)
            (root / "frontend" / "models" / "spirit3d" / "manifest.json").write_text(
                json.dumps({"model": "models/spirit3d/reference/missing.glb"}),
                encoding="utf-8",
            )
            (root / "frontend" / "models" / "spirit3d" / "reference" / "bangboo_pmx_glb_screen.glb").write_bytes(b"glb")
            (root / "frontend" / "avatar_3d.html").write_text("avatar_3d THREE", encoding="utf-8")
            (root / "desktop" / "SpiritKinDesktop").mkdir(parents=True)
            (root / "desktop" / "SpiritKinDesktop" / "SpiritKinDesktop.csproj").write_text("<Project />", encoding="utf-8")
            (root / "backend" / "app").mkdir(parents=True)
            (root / "backend" / "app" / "command_gateway.py").write_text("", encoding="utf-8")

            with patch("backend.app.command_gateway.build_desktop_diagnostics_report") as report_fn:
                from backend.app.diagnostics import build_desktop_diagnostics_report

                report_fn.return_value = build_desktop_diagnostics_report(root=root, frontend_port=0, events_port=0, command_port=0)
                status, payload = build_desktop_diagnostics_response()

        self.assertEqual(status, 200)
        issues = payload["diagnostics"]["issues"]
        avatar_issue = next(item for item in issues if item["issue_id"] == "avatar-model-missing")
        commands = [step["command"] for step in avatar_issue["repair_steps"]]
        self.assertIn("desktop-repair:avatar_manifest", commands)

    def test_desktop_diagnostics_update_can_repair_avatar_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_dir = root / "frontend" / "models" / "spirit3d"
            reference_dir = manifest_dir / "reference"
            reference_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(
                json.dumps({"model": "models/spirit3d/reference/missing.glb"}),
                encoding="utf-8",
            )
            (reference_dir / "bangboo_pmx_glb_screen.glb").write_bytes(b"glb")
            cwd = os.getcwd()
            os.chdir(root)
            try:
                status, payload = build_desktop_diagnostics_update_response({"action": "repair", "issue_id": "avatar-model-missing"})
            finally:
                os.chdir(cwd)

            repaired = json.loads((manifest_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("bangboo_pmx_glb_screen.glb", repaired["model"])

    def test_desktop_operations_services_logs_sync_and_daily_snapshots(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state" / "logs").mkdir(parents=True)
            (root / "state" / "logs" / "service.err.log").write_text("warning\nTraceback: failed\n", encoding="utf-8")
            previous_state = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            previous_learning_log = os.environ.get("SPIRITKIN_LEARNING_LOG")
            previous_dataset = os.environ.get("SPIRITKIN_LEARNING_DATASET")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(root / "desktop_state.json")
            os.environ["SPIRITKIN_LEARNING_LOG"] = str(root / "learning.jsonl")
            os.environ["SPIRITKIN_LEARNING_DATASET"] = str(root / "learning_dataset.jsonl")
            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                build_desktop_state_update_response(
                    {
                        "tasks": [{"id": "task-a", "title": "今日任务", "status": "running"}],
                        "events": [{"type": "desktop.test", "time": "now", "client_id": "test-client"}],
                    }
                )
                ops_status, ops_payload = build_desktop_operations_response()
                services_status, services_payload = build_desktop_services_response()
                logs_status, logs_payload = build_desktop_logs_response("state/logs/service.err.log")
                sync_status, sync_payload = build_desktop_sync_response()
                sync_update_status, sync_update_payload = build_desktop_sync_update_response({"action": "clear_events"})
                daily_status, daily_payload = build_desktop_daily_response()
            finally:
                os.chdir(old_cwd)
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous_state
                if previous_learning_log is None:
                    os.environ.pop("SPIRITKIN_LEARNING_LOG", None)
                else:
                    os.environ["SPIRITKIN_LEARNING_LOG"] = previous_learning_log
                if previous_dataset is None:
                    os.environ.pop("SPIRITKIN_LEARNING_DATASET", None)
                else:
                    os.environ["SPIRITKIN_LEARNING_DATASET"] = previous_dataset

        self.assertEqual(ops_status, 200)
        self.assertIn("services", ops_payload["operations"])
        self.assertEqual(services_status, 200)
        self.assertGreaterEqual(len(services_payload["services"]["services"]), 3)
        self.assertTrue(any(item["service_id"] == "voice_session" for item in services_payload["services"]["services"]))
        voice_service = next(item for item in services_payload["services"]["services"] if item["service_id"] == "voice_session")
        self.assertFalse(voice_service["autostart"])
        self.assertIn("--strict-hotword", voice_service["command"])
        self.assertEqual(logs_status, 200)
        self.assertEqual(logs_payload["logs"]["selected"]["error_count"], 1)
        self.assertEqual(sync_status, 200)
        self.assertEqual(sync_payload["sync"]["event_count"], 1)
        self.assertEqual(sync_update_status, 200)
        self.assertEqual(sync_update_payload["sync"]["event_count"], 0)
        self.assertEqual(daily_status, 200)
        self.assertIn("task_total", daily_payload["daily"])

    def test_service_action_records_audit_and_allows_disabled_manual_start(self):
        remote = next(service for service in default_managed_services() if service.service_id == "remote_worker")
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_SERVICE_ACTION_LOG")
            os.environ["SPIRITKIN_SERVICE_ACTION_LOG"] = str(Path(tmp) / "service_actions.jsonl")
            try:
                with patch("backend.app.operations_center._port_open", return_value=True), patch("backend.app.operations_center._pid_map_for_ports", return_value={8790: 1234}):
                    status, payload = build_desktop_services_response()
                    result = handle_service_action({"action": "start", "service_id": remote.service_id})
                    actions = list_service_actions()
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_SERVICE_ACTION_LOG", None)
                else:
                    os.environ["SPIRITKIN_SERVICE_ACTION_LOG"] = previous

        self.assertEqual(status, 200)
        self.assertTrue(next(item for item in payload["services"]["services"] if item["service_id"] == "remote_worker")["enabled"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already_running")
        self.assertEqual(actions[-1]["service_id"], "remote_worker")
        self.assertEqual(actions[-1]["action"], "start")

    def test_command_gateway_restart_is_scheduled_instead_of_manual_only(self):
        action_order = []
        with TemporaryDirectory() as tmp:
            previous_log = os.environ.get("SPIRITKIN_SERVICE_ACTION_LOG")
            os.environ["SPIRITKIN_SERVICE_ACTION_LOG"] = str(Path(tmp) / "service_actions.jsonl")
            try:
                with patch("backend.app.operations_center.subprocess.Popen") as popen, patch(
                    "backend.app.operations_center._pid_map_for_ports", return_value={8788: 4321}
                ), patch(
                    "backend.app.operations_center.build_service_snapshots",
                    side_effect=lambda: action_order.append("snapshots") or [],
                ):
                    popen.side_effect = lambda *_args, **_kwargs: action_order.append("scheduled")
                    result = handle_service_action({"action": "restart", "service_id": "command_gateway"})
                    actions = list_service_actions()
            finally:
                if previous_log is None:
                    os.environ.pop("SPIRITKIN_SERVICE_ACTION_LOG", None)
                else:
                    os.environ["SPIRITKIN_SERVICE_ACTION_LOG"] = previous_log

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "restart_scheduled")
        self.assertTrue(popen.called)
        helper_call = next(
            call
            for call in popen.call_args_list
            if call.args and len(call.args[0]) >= 3 and call.args[0][1] == "-c"
        )
        self.assertIn("env", helper_call.kwargs)
        self.assertEqual(helper_call.kwargs["env"].get("SPIRITKIN_SERVICE_ACTION_LOG"), str(Path(tmp) / "service_actions.jsonl"))
        helper_script = helper_call.args[0][2]
        helper_payload = json.loads(helper_call.args[0][3])
        self.assertIn("taskkill','/PID',str(p['pid']),'/F'", helper_script)
        self.assertGreaterEqual(helper_payload["delay_seconds"], 4.0)
        self.assertLess(action_order.index("snapshots"), action_order.index("scheduled"))
        self.assertEqual(actions[-1]["status"], "restart_scheduled")

    def test_command_gateway_restart_builds_operations_before_scheduling_exit(self):
        action_order = []

        class Operations:
            def snapshot(self):
                return {"services": []}

        with patch(
            "backend.app.command_gateway.build_operations_snapshot",
            side_effect=lambda: action_order.append("operations") or Operations(),
        ), patch(
            "backend.app.command_gateway.handle_service_action",
            side_effect=lambda _payload: action_order.append("schedule")
            or {"ok": True, "status": "restart_scheduled"},
        ):
            status, payload = build_desktop_services_update_response(
                {"action": "restart", "service_id": "command_gateway"}
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(action_order, ["operations", "schedule"])

    def test_desktop_learning_update_records_feedback_and_exports_dataset(self):
        with TemporaryDirectory() as tmp:
            previous_log = os.environ.get("SPIRITKIN_LEARNING_LOG")
            previous_dataset = os.environ.get("SPIRITKIN_LEARNING_DATASET")
            previous_registry = os.environ.get("SPIRITKIN_DATASET_REGISTRY_PATH")
            os.environ["SPIRITKIN_LEARNING_LOG"] = str(Path(tmp) / "learning.jsonl")
            os.environ["SPIRITKIN_LEARNING_DATASET"] = str(Path(tmp) / "self_training_dataset.jsonl")
            os.environ["SPIRITKIN_DATASET_REGISTRY_PATH"] = str(Path(tmp) / "datasets.jsonl")
            try:
                status, payload = build_desktop_learning_update_response(
                    {
                        "action": "record",
                        "record": {
                            "source": "human",
                            "problem": "Agent 写错了参数映射",
                            "correction": "应先读取 ToolSpec，再生成参数。",
                            "skill_name": "coding.fix_tool_args",
                        },
                    }
                )
                report_status, report_payload = build_desktop_learning_response()
            finally:
                if previous_log is None:
                    os.environ.pop("SPIRITKIN_LEARNING_LOG", None)
                else:
                    os.environ["SPIRITKIN_LEARNING_LOG"] = previous_log
                if previous_dataset is None:
                    os.environ.pop("SPIRITKIN_LEARNING_DATASET", None)
                else:
                    os.environ["SPIRITKIN_LEARNING_DATASET"] = previous_dataset
                if previous_registry is None:
                    os.environ.pop("SPIRITKIN_DATASET_REGISTRY_PATH", None)
                else:
                    os.environ["SPIRITKIN_DATASET_REGISTRY_PATH"] = previous_registry

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dataset"]["count"], 1)
        self.assertTrue(payload["dataset_gate"]["allowed"])
        self.assertEqual(payload["dataset_card"]["status"], "training_ready")
        self.assertEqual(payload["dataset_registry"]["dataset_count"], 1)
        self.assertEqual(report_status, 200)
        self.assertEqual(report_payload["learning"]["dataset"]["count"], 1)
        summary = report_payload["learning"]["self_improvement_summary"]
        self.assertEqual(summary["counts"]["learning_records"], 1)
        self.assertEqual(summary["counts"]["dataset_examples"], 1)
        self.assertTrue(summary["loop"]["training_dataset_exported"])
        self.assertFalse(summary["loop"]["auto_code_apply_enabled"])
        self.assertTrue(summary["loop"]["human_review_required"])

    def test_desktop_learning_review_prompt(self):
        status, payload = build_desktop_learning_update_response(
            {"action": "review_prompt", "problem": "代码质量差", "skill_name": "programming"}
        )

        self.assertEqual(status, 200)
        self.assertIn("programming", payload["prompt"])
        self.assertIn("代码质量差", payload["prompt"])

    def test_desktop_learning_model_review_reports_missing_provider(self):
        previous = os.environ.get("ANTHROPIC_API_KEY")
        os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            status, payload = build_desktop_learning_update_response(
                {"action": "model_review", "provider": "anthropic", "problem": "Skill 生成了错误代码"}
            )
        finally:
            if previous is not None:
                os.environ["ANTHROPIC_API_KEY"] = previous

        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["review"]["status"], "not_configured")
        self.assertIn("desktop cloud provider", payload["review"]["error"])

    def test_desktop_learning_can_save_cloud_model_provider(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_MODEL_PROVIDER_STATE")
            previous_assist = os.environ.get("SPIRITKIN_ASSIST_MODEL_STATE")
            os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = str(Path(tmp) / "provider.json")
            os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = str(Path(tmp) / "assist_models.json")
            try:
                status, payload = build_desktop_learning_update_response(
                    {
                        "action": "save_provider",
                        "provider": {
                            "enabled": True,
                            "display_name": "云顿模型",
                            "endpoint": "https://example.com/v1",
                            "model": "yundun-chat",
                            "api_key": "secret",
                        },
                    }
                )
                assist_status, assist_payload = build_desktop_learning_update_response(
                    {
                        "action": "save_assist_model",
                        "model": {
                            "model_id": "deepseek",
                            "display_name": "DeepSeek",
                            "provider": "openai_compatible",
                            "endpoint": "https://api.deepseek.com/v1",
                            "model": "deepseek-chat",
                            "api_key": "secret",
                            "enabled": True,
                            "role": "reasoning_reviewer",
                            "priority": 90,
                        },
                    }
                )
                report_status, report_payload = build_desktop_learning_response()
                delete_status, delete_payload = build_desktop_learning_update_response({"action": "delete_assist_model", "model_id": "deepseek"})
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_MODEL_PROVIDER_STATE", None)
                else:
                    os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = previous
                if previous_assist is None:
                    os.environ.pop("SPIRITKIN_ASSIST_MODEL_STATE", None)
                else:
                    os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = previous_assist

        self.assertEqual(status, 200)
        self.assertTrue(payload["provider"]["enabled"])
        self.assertTrue(payload["provider"]["api_key_set"])
        self.assertEqual(assist_status, 200)
        self.assertTrue(assist_payload["assist_model"]["configured"])
        self.assertEqual(report_status, 200)
        providers = report_payload["learning"]["model_providers"]
        cloud_provider = next(provider for provider in providers if provider["display_name"] == "云顿模型")
        self.assertTrue(cloud_provider["configured"])
        self.assertTrue(any(model["model_id"] == "deepseek" for model in report_payload["learning"]["assist_models"]))
        self.assertEqual(delete_status, 200)
        self.assertEqual(delete_payload["deleted"], "deepseek")

    def test_desktop_learning_accepts_llamacpp_without_api_key(self):
        with TemporaryDirectory() as tmp:
            previous_provider = os.environ.get("SPIRITKIN_MODEL_PROVIDER_STATE")
            previous_assist = os.environ.get("SPIRITKIN_ASSIST_MODEL_STATE")
            os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = str(Path(tmp) / "provider.json")
            os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = str(Path(tmp) / "assist_models.json")
            try:
                status, payload = build_desktop_learning_update_response(
                    {
                        "action": "save_provider",
                        "provider": {
                            "enabled": True,
                            "provider": "llama-cpp",
                            "display_name": "llama.cpp",
                            "endpoint": "http://127.0.0.1:8080/v1",
                            "model": "local-gguf-model",
                        },
                    }
                )
                assist_status, assist_payload = build_desktop_learning_update_response(
                    {
                        "action": "save_assist_model",
                        "model": {
                            "model_id": "local_llamacpp",
                            "display_name": "Local llama.cpp",
                            "provider": "llama.cpp",
                            "endpoint": "http://127.0.0.1:8080/v1",
                            "model": "local-gguf-model",
                            "enabled": True,
                        },
                    }
                )
            finally:
                if previous_provider is None:
                    os.environ.pop("SPIRITKIN_MODEL_PROVIDER_STATE", None)
                else:
                    os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = previous_provider
                if previous_assist is None:
                    os.environ.pop("SPIRITKIN_ASSIST_MODEL_STATE", None)
                else:
                    os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = previous_assist

        self.assertEqual(status, 200)
        self.assertEqual(payload["provider"]["provider"], "llamacpp")
        self.assertEqual(payload["learning"]["model_provider_settings"]["provider"], "llamacpp")
        self.assertTrue(next(provider for provider in payload["learning"]["model_providers"] if provider["provider"] == "llamacpp")["configured"])
        self.assertEqual(assist_status, 200)
        self.assertEqual(assist_payload["assist_model"]["provider"], "llamacpp")
        self.assertTrue(assist_payload["assist_model"]["configured"])

    def test_desktop_learning_provider_test_reports_health_metadata(self):
        with TemporaryDirectory() as tmp:
            previous_health = os.environ.get("SPIRITKIN_MODEL_PROVIDER_HEALTH")
            os.environ["SPIRITKIN_MODEL_PROVIDER_HEALTH"] = str(Path(tmp) / "model_provider_health.jsonl")
            try:
                status, payload = build_desktop_learning_update_response(
                    {
                        "action": "test_provider",
                        "provider": {
                            "provider": "unit_provider",
                            "display_name": "Unit Provider",
                            "endpoint": "",
                            "model": "",
                        },
                    }
                )
            finally:
                if previous_health is None:
                    os.environ.pop("SPIRITKIN_MODEL_PROVIDER_HEALTH", None)
                else:
                    os.environ["SPIRITKIN_MODEL_PROVIDER_HEALTH"] = previous_health

            health_lines = (Path(tmp) / "model_provider_health.jsonl").read_text(encoding="utf-8").splitlines()

        action = payload["provider_action"]
        self.assertEqual(status, 502)
        self.assertEqual(action["health_status"], "not_configured")
        self.assertGreaterEqual(action["duration_ms"], 0)
        self.assertGreater(action["checked_at"], 0)
        self.assertEqual(action["model_count"], 0)
        self.assertEqual(json.loads(health_lines[-1])["health_status"], "not_configured")

    def test_desktop_learning_can_save_review_committee_policy(self):
        with TemporaryDirectory() as tmp:
            previous_provider = os.environ.get("SPIRITKIN_MODEL_PROVIDER_STATE")
            previous_assist = os.environ.get("SPIRITKIN_ASSIST_MODEL_STATE")
            previous_policy = os.environ.get("SPIRITKIN_REVIEW_COMMITTEE_POLICY_STATE")
            os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = str(Path(tmp) / "provider.json")
            os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = str(Path(tmp) / "assist_models.json")
            os.environ["SPIRITKIN_REVIEW_COMMITTEE_POLICY_STATE"] = str(Path(tmp) / "review_policy.json")
            try:
                save_model_status, _ = build_desktop_learning_update_response(
                    {
                        "action": "save_assist_model",
                        "model": {
                            "model_id": "deepseek",
                            "display_name": "DeepSeek",
                            "provider": "openai_compatible",
                            "endpoint": "https://api.deepseek.com/v1",
                            "model": "deepseek-chat",
                            "api_key": "unit-key",
                            "enabled": True,
                            "role": "reasoning_reviewer",
                            "priority": 90,
                        },
                    }
                )
                policy_status, policy_payload = build_desktop_learning_update_response(
                    {
                        "action": "save_review_committee_policy",
                        "policy": {
                            "policy_id": "self_evolution_gate",
                            "label": "Self Evolution Gate",
                            "enabled": True,
                            "model_ids": ["deepseek"],
                            "required_model_ids": ["deepseek"],
                            "required_roles": ["reasoning_reviewer"],
                            "min_success_count": 1,
                            "pass_threshold": 1.0,
                            "require_human_final": True,
                            "apply_to_actions": ["self_evolution", "skill_promotion"],
                        },
                    }
                )
                report_status, report_payload = build_desktop_learning_response()
                review_status, review_payload = build_desktop_learning_update_response(
                    {"action": "multi_model_review", "problem": "需要评审", "model_ids": []}
                )
            finally:
                if previous_provider is None:
                    os.environ.pop("SPIRITKIN_MODEL_PROVIDER_STATE", None)
                else:
                    os.environ["SPIRITKIN_MODEL_PROVIDER_STATE"] = previous_provider
                if previous_assist is None:
                    os.environ.pop("SPIRITKIN_ASSIST_MODEL_STATE", None)
                else:
                    os.environ["SPIRITKIN_ASSIST_MODEL_STATE"] = previous_assist
                if previous_policy is None:
                    os.environ.pop("SPIRITKIN_REVIEW_COMMITTEE_POLICY_STATE", None)
                else:
                    os.environ["SPIRITKIN_REVIEW_COMMITTEE_POLICY_STATE"] = previous_policy

        self.assertEqual(save_model_status, 200)
        self.assertEqual(policy_status, 200)
        self.assertEqual(policy_payload["review_committee_policy"]["policy_id"], "self_evolution_gate")
        self.assertEqual(report_status, 200)
        summary = report_payload["learning"]["review_committee_summary"]
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["selected_model_ids"], ["deepseek"])
        self.assertEqual(summary["configured_model_ids"], ["deepseek"])
        self.assertEqual(review_status, 502)
        review = review_payload["multi_model_review"]
        self.assertEqual(review["policy"]["policy_id"], "self_evolution_gate")
        self.assertEqual(review["decision"]["required_success_count"], 1)

    def test_desktop_context_update_persists_policy(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_CONTEXT_STATE_PATH")
            os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = str(Path(tmp) / "context.json")
            try:
                status, payload = build_desktop_context_update_response(
                    {"policy": {"mode": "compact", "max_recent_messages": 5, "pinned_context": ["项目规则"]}}
                )
                report_status, report_payload = build_desktop_context_response()
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        self.assertEqual(payload["policy"]["mode"], "compact")
        self.assertEqual(report_status, 200)
        self.assertEqual(report_payload["context"]["policy"]["max_recent_messages"], 5)
        self.assertEqual(report_payload["write_intent_preview"]["status"], "idle")

    def test_desktop_context_update_previews_write_intent_without_policy_write(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_CONTEXT_STATE_PATH")
            os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = str(Path(tmp) / "context.json")
            try:
                status, payload = build_desktop_context_update_response(
                    {
                        "write_intent": {
                            "context_id": "project:unit",
                            "target_path": "project/active/title",
                            "operation": "set",
                            "payload": {"title": "Unit"},
                            "actor": "unit-test",
                            "requires_review": True,
                        }
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        preview = payload["write_intent_preview"]
        self.assertEqual(preview["schema_version"], "spiritkin.context_write_intent.v1")
        self.assertEqual(preview["status"], "preview")
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["target_path"], "/project/active/title")
        self.assertEqual(payload["policy"]["mode"], "balanced")

    def test_desktop_context_write_intent_actions_use_append_only_ledger(self):
        with TemporaryDirectory() as tmp:
            previous_context = os.environ.get("SPIRITKIN_CONTEXT_STATE_PATH")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = str(Path(tmp) / "context.json")
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            try:
                submit_status, submit_payload = build_desktop_context_update_response(
                    {
                        "action": "submit_write_intent",
                        "write_intent": {
                            "context_id": "project:unit",
                            "target_path": "project/active/status",
                            "operation": "set",
                            "payload": {"status": "active"},
                            "actor": "unit-test",
                        },
                    }
                )
                intent_id = submit_payload["write_intent"]["intent_id"]
                approve_status, approve_payload = build_desktop_context_update_response(
                    {
                        "action": "approve_write_intent",
                        "intent_id": intent_id,
                        "reviewer": "human",
                        "review_note": "approved for later writer",
                    }
                )
                list_status, list_payload = build_desktop_context_update_response({"action": "list_write_intents"})
                report_status, report_payload = build_desktop_context_response()
            finally:
                if previous_context is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = previous_context
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents

        self.assertEqual(submit_status, 200)
        self.assertEqual(submit_payload["write_intent"]["status"], "submitted")
        self.assertEqual(approve_status, 200)
        self.assertEqual(approve_payload["write_intent"]["status"], "approved")
        self.assertEqual(approve_payload["write_intent"]["reason"], "approved_but_not_applied")
        self.assertEqual(list_status, 200)
        self.assertEqual(list_payload["write_intents"]["status_counts"]["approved"], 1)
        self.assertEqual(report_status, 200)
        self.assertIn("runtime_context", report_payload)
        self.assertEqual(report_payload["write_intents"]["status_counts"]["approved"], 1)

    def test_desktop_context_can_apply_approved_policy_write_intent(self):
        with TemporaryDirectory() as tmp:
            previous_context = os.environ.get("SPIRITKIN_CONTEXT_STATE_PATH")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            previous_store = os.environ.get("SPIRITKIN_CONTEXT_STORE_PATH")
            os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = str(Path(tmp) / "context.json")
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = str(Path(tmp) / "context_patches.jsonl")
            try:
                submit_status, submit_payload = build_desktop_context_update_response(
                    {
                        "action": "submit_write_intent",
                        "write_intent": {
                            "context_id": "project:unit",
                            "target_path": "/context/policy",
                            "operation": "merge",
                            "payload": {"mode": "compact", "max_recent_messages": 7},
                        },
                    }
                )
                intent_id = submit_payload["write_intent"]["intent_id"]
                blocked_status, blocked_payload = build_desktop_context_update_response(
                    {"action": "apply_write_intent", "intent_id": intent_id}
                )
                approve_status, _ = build_desktop_context_update_response(
                    {"action": "approve_write_intent", "intent_id": intent_id, "reviewer": "human"}
                )
                apply_status, apply_payload = build_desktop_context_update_response(
                    {"action": "apply_write_intent", "intent_id": intent_id}
                )
                report_status, report_payload = build_desktop_context_response()
            finally:
                if previous_context is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = previous_context
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents
                if previous_store is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STORE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = previous_store

        self.assertEqual(submit_status, 200)
        self.assertEqual(blocked_status, 409)
        self.assertEqual(blocked_payload["error"], "context_write_intent_not_approved")
        self.assertEqual(approve_status, 200)
        self.assertEqual(apply_status, 200)
        self.assertTrue(apply_payload["write_apply"]["applied"])
        self.assertEqual(apply_payload["write_apply"]["policy"]["mode"], "compact")
        self.assertEqual(apply_payload["write_apply"]["policy"]["max_recent_messages"], 7)
        self.assertEqual(apply_payload["write_apply"]["context_patch"]["value"]["result_type"], "context_policy")
        self.assertEqual(apply_payload["context_ledger"]["patch_count"], 1)
        self.assertEqual(apply_payload["context_ledger"]["patches"][0]["value"]["intent_id"], intent_id)
        self.assertEqual(apply_payload["write_intent"]["status"], "applied")
        self.assertEqual(report_status, 200)
        self.assertEqual(report_payload["context"]["policy"]["mode"], "compact")
        self.assertEqual(report_payload["context_ledger"]["patches"][0]["value"]["result_type"], "context_policy")
        self.assertEqual(report_payload["write_intents"]["status_counts"]["applied"], 1)

    def test_desktop_context_apply_project_overview_intent_creates_proposal(self):
        with TemporaryDirectory() as tmp:
            overview_path = Path(tmp) / "overview.md"
            review_path = Path(tmp) / "overview_reviews.jsonl"
            overview_path.write_text("# Base\n\nOriginal.", encoding="utf-8")
            previous_overview = os.environ.get("SPIRITKIN_PROJECT_OVERVIEW_PATH")
            previous_review = os.environ.get("SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = str(overview_path)
            os.environ["SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH"] = str(review_path)
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            try:
                submit_status, submit_payload = build_desktop_context_update_response(
                    {
                        "action": "submit_write_intent",
                        "write_intent": {
                            "context_id": "project:unit",
                            "target_path": "/project/overview/proposal",
                            "operation": "merge",
                            "payload": {"append_markdown": "## Proposed\n\nNew section.", "author": "unit"},
                        },
                    }
                )
                intent_id = submit_payload["write_intent"]["intent_id"]
                approve_status, _ = build_desktop_context_update_response(
                    {"action": "approve_write_intent", "intent_id": intent_id, "reviewer": "human"}
                )
                apply_status, apply_payload = build_desktop_context_update_response(
                    {"action": "apply_write_intent", "intent_id": intent_id}
                )
                overview_status, overview_payload = build_desktop_project_overview_response()
                current_text = overview_path.read_text(encoding="utf-8")
            finally:
                if previous_overview is None:
                    os.environ.pop("SPIRITKIN_PROJECT_OVERVIEW_PATH", None)
                else:
                    os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = previous_overview
                if previous_review is None:
                    os.environ.pop("SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH", None)
                else:
                    os.environ["SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH"] = previous_review
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents

        self.assertEqual(submit_status, 200)
        self.assertEqual(approve_status, 200)
        self.assertEqual(apply_status, 200)
        self.assertEqual(apply_payload["write_apply"]["message"], "project_overview_proposal_created")
        self.assertEqual(apply_payload["write_apply"]["project_overview_change"]["status"], "pending")
        self.assertEqual(current_text, "# Base\n\nOriginal.")
        self.assertEqual(overview_status, 200)
        self.assertEqual(overview_payload["project_overview"]["pending_count"], 1)

    def test_desktop_context_apply_collaboration_message_intent_posts_message(self):
        with TemporaryDirectory() as tmp:
            previous_collab = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            try:
                submit_status, submit_payload = build_desktop_context_update_response(
                    {
                        "action": "submit_write_intent",
                        "write_intent": {
                            "context_id": "thread:unit",
                            "target_path": "/collaboration/message",
                            "operation": "append",
                            "payload": {
                                "thread_id": "thread-unit",
                                "from_agent": "codex",
                                "to_agents": ["claude_code"],
                                "role": "question",
                                "content": "Review this context write path.",
                            },
                        },
                    }
                )
                intent_id = submit_payload["write_intent"]["intent_id"]
                approve_status, _ = build_desktop_context_update_response(
                    {"action": "approve_write_intent", "intent_id": intent_id, "reviewer": "human"}
                )
                apply_status, apply_payload = build_desktop_context_update_response(
                    {"action": "apply_write_intent", "intent_id": intent_id}
                )
                inbox_status, inbox_payload = build_desktop_collaboration_update_response(
                    {"action": "list_messages", "to_agent": "claude_code", "include_read": False}
                )
            finally:
                if previous_collab is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_collab
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents

        self.assertEqual(submit_status, 200)
        self.assertEqual(approve_status, 200)
        self.assertEqual(apply_status, 200)
        message = apply_payload["write_apply"]["collaboration_message"]
        self.assertEqual(message["from_agent"], "codex")
        self.assertIn("claude_code", message["to_agents"])
        self.assertEqual(message["agent_envelope"]["sender"], "codex")
        self.assertEqual(inbox_status, 200)
        self.assertEqual(len(inbox_payload["messages"]), 1)
        self.assertEqual(inbox_payload["messages"][0]["message_id"], message["message_id"])

    def test_desktop_context_apply_collaboration_decision_and_review_intents(self):
        with TemporaryDirectory() as tmp:
            previous_collab = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            try:
                decision_submit_status, decision_submit = build_desktop_context_update_response(
                    {
                        "action": "submit_write_intent",
                        "write_intent": {
                            "context_id": "thread:unit",
                            "target_path": "/collaboration/decision",
                            "operation": "append",
                            "payload": {
                                "task_id": "task-unit",
                                "title": "Use governed writes",
                                "decision": "Use ContextWriteIntent for low-risk writes.",
                                "rationale": "Keeps state mutation auditable.",
                                "actor": "codex",
                            },
                        },
                    }
                )
                review_submit_status, review_submit = build_desktop_context_update_response(
                    {
                        "action": "submit_write_intent",
                        "write_intent": {
                            "context_id": "thread:unit",
                            "target_path": "/collaboration/review",
                            "operation": "append",
                            "payload": {
                                "task_id": "task-unit",
                                "reviewer": "claude_code",
                                "verdict": "pass",
                                "summary": "Looks bounded.",
                                "evidence": ["context_write_applier.py"],
                            },
                        },
                    }
                )
                for intent_id in (decision_submit["write_intent"]["intent_id"], review_submit["write_intent"]["intent_id"]):
                    build_desktop_context_update_response({"action": "approve_write_intent", "intent_id": intent_id, "reviewer": "human"})
                decision_apply_status, decision_apply = build_desktop_context_update_response(
                    {"action": "apply_write_intent", "intent_id": decision_submit["write_intent"]["intent_id"]}
                )
                review_apply_status, review_apply = build_desktop_context_update_response(
                    {"action": "apply_write_intent", "intent_id": review_submit["write_intent"]["intent_id"]}
                )
                collab_status, collab_payload = build_desktop_collaboration_response()
            finally:
                if previous_collab is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_collab
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents

        self.assertEqual(decision_submit_status, 200)
        self.assertEqual(review_submit_status, 200)
        self.assertEqual(decision_apply_status, 200)
        self.assertEqual(review_apply_status, 200)
        self.assertEqual(decision_apply["write_apply"]["collaboration_decision"]["actor"], "codex")
        self.assertEqual(review_apply["write_apply"]["collaboration_review"]["reviewer"], "claude_code")
        self.assertEqual(collab_status, 200)
        self.assertEqual(collab_payload["collaboration"]["overview"]["decision_count"], 1)
        self.assertEqual(collab_payload["collaboration"]["overview"]["review_count"], 1)

    def test_desktop_context_response_includes_runtime_context_mirror(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_context = os.environ.get("SPIRITKIN_CONTEXT_STATE_PATH")
            previous_desktop = os.environ.get("SPIRITKIN_DESKTOP_STATE_PATH")
            previous_collab = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = str(root / "context.json")
            os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = str(root / "desktop_state.json")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(root / "collaboration")
            try:
                build_desktop_state_update_response(
                    {
                        "state": {
                            "active_session_id": "session-ctx",
                            "sessions": [{"id": "session-ctx", "title": "Ctx", "messages": [{"role": "user", "text": "hello"}]}],
                            "projects": [{"id": "project-ctx", "title": "Ctx Project", "workspace_path": str(root)}],
                        }
                    }
                )
                status, payload = build_desktop_context_response()
            finally:
                if previous_context is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = previous_context
                if previous_desktop is None:
                    os.environ.pop("SPIRITKIN_DESKTOP_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_DESKTOP_STATE_PATH"] = previous_desktop
                if previous_collab is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_collab

        self.assertEqual(status, 200)
        self.assertIn("runtime_context", payload)
        patches = payload["runtime_context"]["context"]["patches"]
        self.assertIn("/desktop/active_session", [patch["path"] for patch in patches])

    def test_desktop_project_overview_can_refresh_and_save(self):
        with TemporaryDirectory() as tmp:
            previous_overview = os.environ.get("SPIRITKIN_PROJECT_OVERVIEW_PATH")
            previous_review = os.environ.get("SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH")
            os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = str(Path(tmp) / "overview.md")
            os.environ["SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH"] = str(Path(tmp) / "reviews.jsonl")
            try:
                status, payload = build_desktop_project_overview_update_response({"action": "refresh"})
                save_status, save_payload = build_desktop_project_overview_update_response(
                    {"action": "save", "markdown": "# Custom Overview\n\nManual edit."}
                )
                change_id = save_payload["project_overview"]["changes"][-1]["change_id"]
                approve_status, approve_payload = build_desktop_project_overview_update_response(
                    {"action": "approve", "change_id": change_id, "reviewer": "unit-test"}
                )
                get_status, get_payload = build_desktop_project_overview_response()
            finally:
                if previous_overview is None:
                    os.environ.pop("SPIRITKIN_PROJECT_OVERVIEW_PATH", None)
                else:
                    os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = previous_overview
                if previous_review is None:
                    os.environ.pop("SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH", None)
                else:
                    os.environ["SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH"] = previous_review

        self.assertEqual(status, 200)
        self.assertIn("SpiritKinAI Project Management Overview", payload["project_overview"]["overview"]["markdown"])
        self.assertGreaterEqual(payload["project_overview"]["pending_count"], 1)
        self.assertEqual(save_status, 200)
        self.assertIn("Custom Overview", save_payload["project_overview"]["changes"][-1]["proposed_markdown"])
        self.assertEqual(approve_status, 200)
        self.assertEqual(approve_payload["project_overview"]["changes"][-1]["status"], "approved")
        self.assertEqual(get_status, 200)
        self.assertIn("Manual edit", get_payload["project_overview"]["overview"]["markdown"])

    def test_desktop_collaboration_records_tasks_claims_reviews_and_context_pack(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_overview = os.environ.get("SPIRITKIN_PROJECT_OVERVIEW_PATH")
            collab_root = Path(tmp) / "collaboration"
            overview_path = Path(tmp) / "overview.md"
            overview_path.write_text("# Overview\n\nShared context.", encoding="utf-8")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(collab_root)
            os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = str(overview_path)
            try:
                create_status, create_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "create_task",
                        "task_id": "task-ui-opus",
                        "title": "Opus UI refactor",
                        "owner": "claude_code",
                        "scope": ["desktop ui"],
                        "allowed_files": ["desktop/SpiritKinDesktop/Controls/*.xaml"],
                        "blocked_files": ["backend/app/*.py"],
                        "verification_commands": ["dotnet build desktop\\SpiritKinDesktop\\SpiritKinDesktop.csproj --no-restore"],
                    }
                )
                claim_status, claim_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "claim_files",
                        "owner": "claude_code",
                        "task_id": "task-ui-opus",
                        "patterns": ["desktop/SpiritKinDesktop/Controls/*.xaml"],
                        "note": "UI refactor in progress",
                    }
                )
                decision_status, decision_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "record_decision",
                        "task_id": "task-ui-opus",
                        "title": "Collaboration surface",
                        "decision": "Use a separate Collaboration page.",
                        "rationale": "Project Overview remains the source document.",
                        "actor": "codex",
                    }
                )
                review_status, review_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "record_review",
                        "task_id": "task-ui-opus",
                        "reviewer": "external_reviewer",
                        "verdict": "comment",
                        "summary": "Keep UI changes isolated from backend contracts.",
                        "evidence": ["project_management_overview.md"],
                    }
                )
                message_status, message_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "post_message",
                        "task_id": "task-ui-opus",
                        "from_model": "codex",
                        "to_agents": ["claude_code"],
                        "role": "question",
                        "content": "Can you review the desktop collaboration UI?",
                    }
                )
                review_request_status, review_request_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "request_model_review",
                        "task_id": "task-ui-opus",
                        "from_model": "codex",
                        "to_agents": ["external_reviewer"],
                        "content": "Review the collaboration message bus contract.",
                    }
                )
                read_status, read_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "mark_message_read",
                        "message_id": message_payload["message"]["message_id"],
                        "reader": "claude_code",
                    }
                )
                unread_after_read_status, unread_after_read_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "list_messages",
                        "to_agent": "claude_code",
                        "include_read": False,
                    }
                )
                pack_status, pack_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "build_context_pack",
                        "task_id": "task-ui-opus",
                        "include_files": [str(overview_path)],
                        "max_chars_per_file": 1200,
                    }
                )
                pack_exists = Path(pack_payload["context_pack"]["pack_path"]).exists()
                get_status, get_payload = build_desktop_collaboration_response()
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_overview is None:
                    os.environ.pop("SPIRITKIN_PROJECT_OVERVIEW_PATH", None)
                else:
                    os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = previous_overview

        self.assertEqual(create_status, 200)
        self.assertEqual(create_payload["task"]["task_id"], "task-ui-opus")
        self.assertEqual(claim_status, 200)
        self.assertEqual(claim_payload["file_claim"]["owner"], "claude_code")
        self.assertEqual(decision_status, 200)
        self.assertEqual(decision_payload["decision"]["actor"], "codex")
        self.assertEqual(review_status, 200)
        self.assertEqual(review_payload["review"]["reviewer"], "external_reviewer")
        self.assertEqual(message_status, 200)
        self.assertEqual(message_payload["message"]["from_model"], "codex")
        self.assertTrue(message_payload["message"]["route_verdict"]["allowed"])
        self.assertEqual(message_payload["message"]["route_audit_event"]["action"], "collaboration_message_route")
        self.assertEqual(message_payload["event"]["type"], "desktop.collaboration_updated")
        self.assertEqual(message_payload["event"]["payload"]["message_id"], message_payload["message"]["message_id"])
        self.assertEqual(review_request_status, 200)
        self.assertEqual(review_request_payload["message"]["role"], "review_request")
        self.assertEqual(read_status, 200)
        self.assertEqual(unread_after_read_status, 200)
        self.assertEqual(message_payload["message"]["from_agent"], "codex")
        self.assertIn("claude_code", message_payload["message"]["to_agents"])
        self.assertIn("claude_code", read_payload["message"]["read_by"])
        self.assertEqual(unread_after_read_payload["messages"], [])
        self.assertEqual(pack_status, 200)
        self.assertTrue(pack_exists)
        self.assertEqual(get_status, 200)
        collaboration = get_payload["collaboration"]
        self.assertEqual(collaboration["schema_version"], "spiritkin.collaboration.v1")
        self.assertEqual(collaboration["overview"]["active_task_count"], 1)
        self.assertEqual(collaboration["overview"]["active_file_claim_count"], 1)
        self.assertEqual(collaboration["overview"]["message_count"], 2)
        self.assertEqual(collaboration["overview"]["unread_message_count"], 1)
        self.assertEqual(len(collaboration["recent_messages"]), 2)
        self.assertEqual(collaboration["overview"]["recommended_surface"], "separate_collaboration_page")

    def test_desktop_collaboration_blocks_invalid_agent_routes(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            try:
                worker_status, worker_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "post_message",
                        "thread_id": "thread-route",
                        "from_agent": "codex",
                        "to_agent": "worker",
                        "role": "handoff",
                        "content": "run directly",
                    }
                )
                scope_status, scope_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "post_message",
                        "thread_id": "thread-route",
                        "from_agent": "codex",
                        "to_agent": "claude_code",
                        "role": "review_request",
                        "content": "execute this",
                        "permission_scope": "execute",
                    }
                )
                snapshot_status, snapshot = build_desktop_collaboration_response()
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root

        self.assertEqual(worker_status, 400)
        self.assertIn("recipient_not_allowed", worker_payload["detail"])
        self.assertEqual(scope_status, 400)
        self.assertIn("permission_scope_not_allowed", scope_payload["detail"])
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot["collaboration"]["overview"]["message_count"], 0)

    def test_collaboration_unread_is_scoped_by_agent(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            try:
                status, payload = build_desktop_collaboration_update_response(
                    {
                        "action": "post_message",
                        "thread_id": "thread-shared",
                        "from_agent": "human_desktop",
                        "to_agents": ["codex", "claude_code"],
                        "role": "question",
                        "content": "Please both review this.",
                    }
                )
                message_id = payload["message"]["message_id"]
                codex_read_status, _ = build_desktop_collaboration_update_response(
                    {"action": "mark_message_read", "message_id": message_id, "reader": "codex"}
                )
                codex_inbox_status, codex_inbox = build_desktop_collaboration_update_response(
                    {"action": "list_messages", "to_agent": "codex", "include_read": False}
                )
                claude_inbox_status, claude_inbox = build_desktop_collaboration_update_response(
                    {"action": "list_messages", "to_agent": "claude_code", "include_read": False}
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root

        self.assertEqual(status, 200)
        self.assertEqual(codex_read_status, 200)
        self.assertEqual(codex_inbox_status, 200)
        self.assertEqual(claude_inbox_status, 200)
        self.assertEqual(codex_inbox["messages"], [])
        self.assertEqual(len(claude_inbox["messages"]), 1)
        self.assertEqual(claude_inbox["messages"][0]["message_id"], message_id)

    def test_desktop_agent_management_saves_skill_assist_and_exports_remote_package(self):
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            previous_export = os.environ.get("SPIRITKIN_REMOTE_EXPORT_DIR")
            previous_signing_key = os.environ.get("SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_REMOTE_EXPORT_DIR"] = str(Path(tmp) / "exports")
            os.environ["SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY"] = "unit-signing-key"
            try:
                status, payload = build_desktop_agent_management_update_response(
                    {
                        "state": {
                            "skill_assist": {
                                "enabled": True,
                                "mode": "cloud_model_review",
                                "require_on_failure": True,
                                "allow_external_model": True,
                                "allow_external_cli": True,
                                "selected_assistant_id": "codex_cli",
                            },
                            "route_profiles": [
                                {
                                    "profile_id": "route-a",
                                    "label": "GPT+KIMI",
                                    "strategy": "committee_review",
                                    "members": [
                                        {"member_id": "main", "role": "primary_text", "provider": "openai_compatible", "model": "gpt"},
                                        {"member_id": "review", "role": "reviewer", "provider": "openai_compatible", "model": "kimi"},
                                    ],
                                }
                            ],
                            "active_route_profile_id": "route-a",
                            "agents": [
                                {
                                    "agent_id": "main_text",
                                    "label": "主 Agent",
                                    "domain": "general",
                                    "enabled": True,
                                    "provider": "openai_compatible",
                                    "model": "gpt",
                                    "role": "primary",
                                    "priority": 100,
                                    "capabilities": ["planning", "coding"],
                                }
                            ],
                            "external_assistants": [
                                {"assistant_id": "codex_cli", "label": "Codex CLI", "command": "codex", "enabled": True}
                            ],
                        }
                    }
                )
                export_status, export_payload = build_desktop_agent_management_update_response(
                    {
                        "action": "export_remote",
                        "export_id": "skill-export",
                        "target_id": "worker-a",
                        "skill_names": ["workflow.local.scan"],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    }
                )
                export_exists = Path(export_payload["export"]["package_path"]).exists()
                package = export_payload["export"]["package"]
                get_status, get_payload = build_desktop_agent_management_response()
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state
                if previous_export is None:
                    os.environ.pop("SPIRITKIN_REMOTE_EXPORT_DIR", None)
                else:
                    os.environ["SPIRITKIN_REMOTE_EXPORT_DIR"] = previous_export
                if previous_signing_key is None:
                    os.environ.pop("SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY", None)
                else:
                    os.environ["SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY"] = previous_signing_key

        self.assertEqual(status, 200)
        self.assertTrue(payload["agent_management"]["skill_assist"]["enabled"])
        self.assertEqual(payload["agent_management"]["route_profiles"][0]["label"], "GPT+KIMI")
        self.assertEqual(payload["agent_management"]["active_route_profile_id"], "route-a")
        self.assertEqual(payload["agent_management"]["agents"][0]["agent_id"], "main_text")
        summary = payload["agent_management"]["distribution_summary"]
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["counts"]["agents_enabled"], 1)
        self.assertEqual(summary["active_route"]["profile_id"], "route-a")
        self.assertEqual(summary["active_route"]["primary_text"]["member_id"], "main")
        self.assertEqual(export_status, 200)
        self.assertTrue(export_exists)
        self.assertEqual(package["package_schema_version"], "spiritkin.remote_package.v2")
        self.assertEqual(package["manifest"]["schema_version"], "spiritkin.remote_package.v2")
        self.assertEqual(package["manifest"]["created_by"], "unit-test")
        self.assertEqual(package["compatibility"]["worker_api_version"], "spiritkin.remote_worker.v1")
        self.assertIn("sha256_integrity", package["compatibility"]["required_worker_features"])
        self.assertGreaterEqual(len(package["rollback_plan"]["steps"]), 3)
        self.assertEqual(package["integrity"]["algorithm"], "sha256")
        self.assertEqual(len(package["integrity"]["digest"]), 64)
        self.assertEqual(package["signature"]["algorithm"], "hmac-sha256")
        self.assertTrue(package["signature"]["signed"])
        self.assertEqual(len(package["signature"]["digest"]), 64)
        self.assertEqual(get_status, 200)
        self.assertEqual(get_payload["agent_management"]["external_assistants"][0]["assistant_id"], "codex_cli")
        self.assertEqual(get_payload["agent_management"]["distribution_summary"]["counts"]["external_assistants_enabled"], 1)

    def test_desktop_agent_management_default_routes_include_local_cloud_patterns(self):
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                status, payload = build_desktop_agent_management_response()
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state

        self.assertEqual(status, 200)
        profiles = payload["agent_management"]["route_profiles"]
        profile_ids = {profile["profile_id"] for profile in profiles}
        self.assertIn("default_hybrid", profile_ids)
        self.assertIn("cloud_review_gate", profile_ids)
        self.assertIn("cloud_fallback_chain", profile_ids)
        default_profile = next(profile for profile in profiles if profile["profile_id"] == "default_hybrid")
        roles = {member["role"] for member in default_profile["members"]}
        self.assertIn("primary_text", roles)
        self.assertIn("reviewer", roles)
        summary = payload["agent_management"]["distribution_summary"]
        self.assertEqual(summary["status"], "ready")
        self.assertGreaterEqual(summary["counts"]["agents_enabled"], 7)
        self.assertEqual(summary["active_route"]["profile_id"], "default_hybrid")
        self.assertIn("planning", {item["capability"] for item in summary["capability_coverage"]})
        adapter_ids = {adapter["adapter_id"] for adapter in payload["agent_management"]["agent_adapters"]}
        self.assertIn("coordinator_router", adapter_ids)
        self.assertIn("code_agent_adapter", adapter_ids)
        self.assertIn("review_agent_adapter", adapter_ids)
        self.assertGreaterEqual(summary["counts"]["agent_adapters_total"], 8)

    def test_desktop_agent_management_preserves_agent_adapters(self):
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                status, payload = build_desktop_agent_management_update_response(
                    {
                        "state": {
                            "agents": [
                                {
                                    "agent_id": "researcher",
                                    "label": "Research Agent",
                                    "domain": "research",
                                    "enabled": True,
                                    "provider": "cloud_openai_compatible",
                                    "model": "deepseek-reviewer",
                                    "model_id": "deepseek-reviewer",
                                    "framework": "langgraph",
                                    "adapter": "research_graph_adapter",
                                    "role": "specialist",
                                    "priority": 70,
                                    "capabilities": ["paper_search", "proposal_review"],
                                }
                            ],
                            "route_profiles": [
                                {
                                    "profile_id": "research-route",
                                    "label": "Research Route",
                                    "strategy": "primary_with_specialists",
                                    "members": [
                                        {
                                            "member_id": "researcher",
                                            "role": "primary_text",
                                            "provider": "cloud_openai_compatible",
                                            "model": "deepseek-reviewer",
                                        }
                                    ],
                                }
                            ],
                            "active_route_profile_id": "research-route",
                            "agent_adapters": [
                                {
                                    "adapter_id": "research_graph_adapter",
                                    "label": "Research LangGraph",
                                    "kind": "framework",
                                    "framework": "langgraph",
                                    "module": "spiritkin.adapters.research_graph",
                                    "enabled": True,
                                    "review_only": True,
                                    "allow_write": False,
                                    "capabilities": ["paper_search", "proposal_review"],
                                    "owner_agent_ids": ["researcher"],
                                }
                            ],
                        }
                    }
                )
                get_status, get_payload = build_desktop_agent_management_response()
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state

        self.assertEqual(status, 200)
        self.assertEqual(get_status, 200)
        agents = {agent["agent_id"]: agent for agent in get_payload["agent_management"]["agents"]}
        self.assertEqual(agents["researcher"]["framework"], "langgraph")
        self.assertEqual(agents["researcher"]["adapter"], "research_graph_adapter")
        adapters = {adapter["adapter_id"]: adapter for adapter in get_payload["agent_management"]["agent_adapters"]}
        self.assertEqual(adapters["research_graph_adapter"]["health_status"], "configured")
        runtime_adapter = get_payload["agent_management"]["distribution_summary"]["agent_adapters"][0]
        self.assertIn("adapter_id", runtime_adapter)
        self.assertEqual(get_payload["agent_management"]["distribution_summary"]["status"], "ready")

    def test_desktop_agent_management_reports_missing_agent_adapter(self):
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                status, payload = build_desktop_agent_management_update_response(
                    {
                        "state": {
                            "agents": [
                                {
                                    "agent_id": "broken",
                                    "label": "Broken Agent",
                                    "domain": "general",
                                    "enabled": True,
                                    "provider": "openai_compatible",
                                    "model": "local",
                                    "adapter": "missing_adapter",
                                    "role": "primary",
                                }
                            ],
                            "route_profiles": [
                                {
                                    "profile_id": "broken-route",
                                    "label": "Broken Route",
                                    "members": [
                                        {
                                            "member_id": "broken",
                                            "role": "primary_text",
                                            "provider": "openai_compatible",
                                            "model": "local",
                                        }
                                    ],
                                }
                            ],
                            "active_route_profile_id": "broken-route",
                        }
                    }
                )
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state

        self.assertEqual(status, 200)
        summary = payload["agent_management"]["distribution_summary"]
        self.assertEqual(summary["status"], "blocked")
        self.assertIn("agent_adapter_missing:broken", {gap["id"] for gap in summary["gaps"]})

    def test_desktop_agent_management_pushes_and_executes_remote_package(self):
        received_paths = []

        class RemotePackageHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                received_paths.append(self.path)
                if self.path == "/remote-package/import":
                    body = {"ok": True, "package_id": payload["package"]["export_id"], "status": "imported"}
                elif self.path == "/remote-package/execute":
                    body = {"ok": False, "package_id": payload["package"]["export_id"], "status": "imported_verification_pending"}
                elif self.path == "/remote-package/rollback":
                    body = {"ok": True, "from_package_id": payload["package_id"], "to_package_id": "previous", "status": "rolled_back"}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), RemotePackageHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            previous_export = os.environ.get("SPIRITKIN_REMOTE_EXPORT_DIR")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_REMOTE_EXPORT_DIR"] = str(Path(tmp) / "exports")
            try:
                host, port = server.server_address
                save_status, _ = build_desktop_agent_management_update_response(
                    {
                        "state": {
                            "remote_targets": [
                                {
                                    "target_id": "worker-a",
                                    "label": "Worker A",
                                    "base_url": f"http://{host}:{port}",
                                    "enabled": True,
                                }
                            ]
                        }
                    }
                )
                export_status, export_payload = build_desktop_agent_management_update_response(
                    {
                        "action": "export_remote",
                        "export_id": "skill-export",
                        "target_id": "worker-a",
                        "skill_names": ["workflow.local.scan"],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    }
                )
                package_path = export_payload["export"]["package_path"]
                push_status, push_payload = build_desktop_agent_management_update_response(
                    {"action": "push_remote", "target_id": "worker-a", "package_path": package_path, "core_review_approved": True, "reviewer": "unit-test"}
                )
                execute_status, execute_payload = build_desktop_agent_management_update_response(
                    {"action": "execute_remote", "target_id": "worker-a", "package_path": package_path, "core_review_approved": True, "reviewer": "unit-test"}
                )
                rollback_status, rollback_payload = build_desktop_agent_management_update_response(
                    {"action": "rollback_remote", "target_id": "worker-a", "package_path": package_path, "core_review_approved": True, "reviewer": "unit-test"}
                )
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state
                if previous_export is None:
                    os.environ.pop("SPIRITKIN_REMOTE_EXPORT_DIR", None)
                else:
                    os.environ["SPIRITKIN_REMOTE_EXPORT_DIR"] = previous_export
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(save_status, 200)
        self.assertEqual(export_status, 200)
        self.assertEqual(push_status, 200)
        self.assertTrue(push_payload["push"]["ok"])
        self.assertEqual(execute_status, 200)
        self.assertFalse(execute_payload["remote_execution"]["ok"])
        self.assertEqual(rollback_status, 200)
        self.assertTrue(rollback_payload["remote_rollback"]["ok"])
        self.assertTrue(push_payload["push"]["integrity"]["verified"])
        self.assertEqual(push_payload["push"]["integrity"]["expected_digest"], push_payload["push"]["integrity"]["actual_digest"])
        self.assertEqual(received_paths, ["/remote-package/import", "/remote-package/execute", "/remote-package/rollback"])

    def test_desktop_agent_management_rejects_tampered_remote_package(self):
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            previous_export = os.environ.get("SPIRITKIN_REMOTE_EXPORT_DIR")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_REMOTE_EXPORT_DIR"] = str(Path(tmp) / "exports")
            try:
                export_status, export_payload = build_desktop_agent_management_update_response(
                    {
                        "action": "export_remote",
                        "export_id": "tamper-test",
                        "target_id": "worker-a",
                        "skill_names": ["workflow.local.scan"],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    }
                )
                package_path = Path(export_payload["export"]["package_path"])
                package = json.loads(package_path.read_text(encoding="utf-8"))
                package["skill_names"].append("workflow.injected")
                package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
                push_status, push_payload = build_desktop_agent_management_update_response(
                    {
                        "action": "push_remote",
                        "target_id": "worker-a",
                        "package_path": str(package_path),
                        "base_url": "http://127.0.0.1:1",
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    }
                )

                inline_package = dict(export_payload["export"]["package"])
                inline_package["notes"] = "tampered inline package"
                inline_status, inline_payload = build_desktop_agent_management_update_response(
                    {
                        "action": "execute_remote",
                        "target_id": "worker-a",
                        "package": inline_package,
                        "base_url": "http://127.0.0.1:1",
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    }
                )
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state
                if previous_export is None:
                    os.environ.pop("SPIRITKIN_REMOTE_EXPORT_DIR", None)
                else:
                    os.environ["SPIRITKIN_REMOTE_EXPORT_DIR"] = previous_export

        self.assertEqual(export_status, 200)
        self.assertEqual(push_status, 400)
        self.assertIn("integrity mismatch", push_payload["detail"])
        self.assertEqual(inline_status, 400)
        self.assertIn("integrity mismatch", inline_payload["detail"])

    def test_desktop_skills_can_save_review_delete_and_export(self):
        with TemporaryDirectory() as tmp:
            previous_store = os.environ.get("SPIRITKIN_SKILL_STORE_PATH")
            previous_export = os.environ.get("SPIRITKIN_SKILL_EXPORT_DIR")
            os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(Path(tmp) / "skills.jsonl")
            os.environ["SPIRITKIN_SKILL_EXPORT_DIR"] = str(Path(tmp) / "exports")
            try:
                save_status, save_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.local.test",
                        "description": "测试 Skill",
                        "status": "candidate",
                        "steps": [{"tool_name": "app.launch", "arguments": {"app_name": "browser"}}],
                        "tool_allowlist": ["app.launch"],
                        "metadata": {"success_count": 3, "total_count": 5, "success_rate": 1.0},
                    }
                )
                get_status, get_payload = build_desktop_skills_response()
                review_status, review_payload = build_desktop_skills_update_response({"action": "review_candidates", "reviewer": "desktop_test"})
                export_status, export_payload = build_desktop_skills_update_response({"action": "export", "name": "workflow.local.test", "export_id": "skills-test"})
                export_exists = Path(export_payload["package_path"]).exists()
                delete_status, delete_payload = build_desktop_skills_update_response({"action": "delete", "name": "workflow.local.test"})
            finally:
                if previous_store is None:
                    os.environ.pop("SPIRITKIN_SKILL_STORE_PATH", None)
                else:
                    os.environ["SPIRITKIN_SKILL_STORE_PATH"] = previous_store
                if previous_export is None:
                    os.environ.pop("SPIRITKIN_SKILL_EXPORT_DIR", None)
                else:
                    os.environ["SPIRITKIN_SKILL_EXPORT_DIR"] = previous_export

        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["skill"]["name"], "workflow.local.test")
        self.assertEqual(get_status, 200)
        self.assertEqual(get_payload["skills"]["count"], 1)
        self.assertEqual(review_status, 200)
        self.assertEqual(review_payload["outcomes"][0]["decision"], "pending")
        self.assertEqual(export_status, 200)
        self.assertTrue(export_exists)
        self.assertEqual(delete_status, 200)
        self.assertEqual(delete_payload["deleted"], "workflow.local.test")

    def test_desktop_skills_review_gate_and_workspace_escape(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_store = os.environ.get("SPIRITKIN_SKILL_STORE_PATH")
            previous_agent = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            previous_review_log = os.environ.get("SPIRITKIN_REVIEW_GATE_LOG")
            os.chdir(tmp)
            os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(Path(tmp) / "skills.jsonl")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_REVIEW_GATE_LOG"] = str(Path(tmp) / "review_gate.jsonl")
            try:
                escape_status, escape_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.escape",
                        "description": "bad workspace",
                        "owner_agent_id": "programming",
                        "workspace_path": "state/agents/programming/workspace/../../outside",
                    }
                )
                save_status, save_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.review.required",
                        "description": "review gate test",
                        "status": "candidate",
                        "owner_agent_id": "programming",
                        "workspace_path": "state/agents/programming/workspace/skills",
                        "metadata": {"success_count": 3, "total_count": 5, "success_rate": 1.0},
                    }
                )
                reassign_status, reassign_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.review.required",
                        "description": "owner change should stay blocked",
                        "status": "candidate",
                        "owner_agent_id": "video_animation",
                        "workspace_path": "state/agents/video_animation/workspace/skills",
                        "allow_owner_reassign": "false",
                    }
                )
                blocked_status, blocked_payload = build_desktop_skills_update_response({"action": "promote", "name": "workflow.review.required", "reviewer": "unit-test"})
                promote_status, promote_payload = build_desktop_skills_update_response(
                    {"action": "promote", "name": "workflow.review.required", "reviewer": "unit-test", "core_review_approved": True}
                )
                video_save_status, video_save_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.video.unbound",
                        "description": "video skill must bind UI first",
                        "status": "candidate",
                        "owner_agent_id": "video_animation",
                        "workspace_path": "state/agents/video_animation/workspace/skills",
                        "metadata": {"ui_binding_status": "required", "success_count": 3, "total_count": 5, "success_rate": 1.0},
                    }
                )
                video_promote_status, video_promote_payload = build_desktop_skills_update_response(
                    {"action": "promote", "name": "workflow.video.unbound", "reviewer": "unit-test", "core_review_approved": True}
                )
                bind_status, bind_payload = build_desktop_skills_update_response(
                    {
                        "action": "bind_ui",
                        "name": "workflow.video.unbound",
                        "reviewer": "unit-test",
                        "ui_bindings": [{"index": 1, "action": "click", "selector": "button#import"}],
                    }
                )
                video_promote_after_bind_status, video_promote_after_bind_payload = build_desktop_skills_update_response(
                    {"action": "promote", "name": "workflow.video.unbound", "reviewer": "unit-test", "core_review_approved": True}
                )
                review_log_exists = Path(os.environ["SPIRITKIN_REVIEW_GATE_LOG"]).exists()
                review_log_lines = Path(os.environ["SPIRITKIN_REVIEW_GATE_LOG"]).read_text(encoding="utf-8").splitlines()
            finally:
                os.chdir(previous_cwd)
                if previous_store is None:
                    os.environ.pop("SPIRITKIN_SKILL_STORE_PATH", None)
                else:
                    os.environ["SPIRITKIN_SKILL_STORE_PATH"] = previous_store
                if previous_agent is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_agent
                if previous_review_log is None:
                    os.environ.pop("SPIRITKIN_REVIEW_GATE_LOG", None)
                else:
                    os.environ["SPIRITKIN_REVIEW_GATE_LOG"] = previous_review_log

        self.assertEqual(escape_status, 400)
        self.assertIn("workspace_path", escape_payload["detail"])
        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["skill"]["owner_agent_id"], "programming")
        self.assertEqual(reassign_status, 400)
        self.assertIn("owner mismatch", reassign_payload["detail"])
        self.assertEqual(blocked_status, 200)
        self.assertFalse(blocked_payload["ok"])
        self.assertEqual(blocked_payload["error"], "review_required")
        self.assertEqual(promote_status, 200)
        self.assertEqual(promote_payload["skill"]["status"], "active")
        self.assertTrue(promote_payload["skill"]["metadata"]["core_review_gate"]["allowed"])
        self.assertEqual(video_save_status, 200)
        self.assertEqual(video_save_payload["skill"]["metadata"]["ui_binding_status"], "required")
        self.assertEqual(video_promote_status, 200)
        self.assertFalse(video_promote_payload["ok"])
        self.assertEqual(video_promote_payload["error"], "ui_binding_required")
        self.assertEqual(bind_status, 200)
        self.assertEqual(bind_payload["skill"]["metadata"]["ui_binding_status"], "bound")
        self.assertEqual(video_promote_after_bind_status, 200)
        self.assertEqual(video_promote_after_bind_payload["skill"]["status"], "active")
        self.assertTrue(video_promote_after_bind_payload["skill"]["metadata"]["core_review_gate"]["allowed"])
        self.assertTrue(review_log_exists)
        self.assertGreaterEqual(len(review_log_lines), 2)

    def test_desktop_skills_high_risk_promotion_requires_passing_jury_report(self):
        with TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_store = os.environ.get("SPIRITKIN_SKILL_STORE_PATH")
            previous_agent = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            previous_review_log = os.environ.get("SPIRITKIN_REVIEW_GATE_LOG")
            os.chdir(tmp)
            os.environ["SPIRITKIN_SKILL_STORE_PATH"] = str(Path(tmp) / "skills.jsonl")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_REVIEW_GATE_LOG"] = str(Path(tmp) / "review_gate.jsonl")
            try:
                save_status, save_payload = build_desktop_skills_update_response(
                    {
                        "action": "save",
                        "name": "workflow.highrisk.code",
                        "description": "high risk code promotion",
                        "status": "candidate",
                        "risk_level": "high",
                        "owner_agent_id": "programming",
                        "workspace_path": "state/agents/programming/workspace/skills",
                        "metadata": {"success_count": 3, "total_count": 5, "success_rate": 1.0},
                    }
                )
                blocked_status, blocked_payload = build_desktop_skills_update_response(
                    {
                        "action": "promote",
                        "name": "workflow.highrisk.code",
                        "reviewer": "unit-test",
                        "core_review_approved": True,
                    }
                )
                approved_report = {
                    "report_id": "jury_report_unit_approved",
                    "decision": "approved",
                    "overall_score": 92,
                    "summary": {"structured_review_count": 1},
                    "package": {
                        "package_id": "workflow.highrisk.code",
                        "review_type": "code",
                        "metadata": {"skill_name": "workflow.highrisk.code"},
                    },
                    "promotion_gate": {"eligible": True},
                }
                promote_status, promote_payload = build_desktop_skills_update_response(
                    {
                        "action": "promote",
                        "name": "workflow.highrisk.code",
                        "reviewer": "unit-test",
                        "core_review_approved": True,
                        "jury_report": approved_report,
                    }
                )
            finally:
                os.chdir(previous_cwd)
                if previous_store is None:
                    os.environ.pop("SPIRITKIN_SKILL_STORE_PATH", None)
                else:
                    os.environ["SPIRITKIN_SKILL_STORE_PATH"] = previous_store
                if previous_agent is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_agent
                if previous_review_log is None:
                    os.environ.pop("SPIRITKIN_REVIEW_GATE_LOG", None)
                else:
                    os.environ["SPIRITKIN_REVIEW_GATE_LOG"] = previous_review_log

        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["skill"]["risk_level"], "high")
        self.assertEqual(blocked_status, 200)
        self.assertFalse(blocked_payload["ok"])
        self.assertEqual(blocked_payload["error"], "jury_review_required")
        self.assertTrue(blocked_payload["review_gate"]["allowed"])
        self.assertFalse(blocked_payload["jury_gate"]["allowed"])
        self.assertEqual(promote_status, 200)
        self.assertEqual(promote_payload["skill"]["status"], "active")
        self.assertTrue(promote_payload["skill"]["metadata"]["jury_gate"]["allowed"])
        self.assertEqual(promote_payload["skill"]["metadata"]["jury_report_id"], "jury_report_unit_approved")

    def test_desktop_safety_endpoint_can_stop_and_resume(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_SAFETY_STATE_PATH")
            os.environ["SPIRITKIN_SAFETY_STATE_PATH"] = str(Path(tmp) / "safety.json")
            try:
                status, payload = build_desktop_safety_response()
                stop_status, stop_payload = build_desktop_safety_update_response({"action": "hard_stop", "reason": "unit test"})
                missing_status, missing_payload = build_desktop_safety_update_response({"action": "resume", "reason": "unit done"})
                resume_status, resume_payload = build_desktop_safety_update_response(
                    {"action": "resume", "reason": "unit done", "confirmation_text": HARD_STOP_RESUME_CONFIRMATION}
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_SAFETY_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_SAFETY_STATE_PATH"] = previous

        self.assertEqual(status, 200)
        self.assertFalse(payload["safety"]["active"])
        self.assertEqual(stop_status, 200)
        self.assertTrue(stop_payload["safety"]["active"])
        self.assertEqual(stop_payload["safety"]["mode"], "hard_stop")
        self.assertEqual(missing_status, 400)
        self.assertIn(HARD_STOP_RESUME_CONFIRMATION, missing_payload["detail"])
        self.assertEqual(resume_status, 200)
        self.assertFalse(resume_payload["safety"]["active"])

    def test_desktop_mcp_management_endpoint_can_save_candidate_server(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_MCP_REGISTRY_PATH")
            os.environ["SPIRITKIN_MCP_REGISTRY_PATH"] = str(Path(tmp) / "mcp.json")
            try:
                status, payload = build_desktop_mcp_management_response()
                save_status, save_payload = build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "docs",
                        "transport": "stdio",
                        "command": "npx docs-mcp",
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.docs.search", "read_only": True}],
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_MCP_REGISTRY_PATH", None)
                else:
                    os.environ["SPIRITKIN_MCP_REGISTRY_PATH"] = previous

        self.assertEqual(status, 200)
        self.assertEqual(payload["mcp_management"]["server_count"], 0)
        self.assertEqual(save_status, 200)
        self.assertEqual(save_payload["mcp_management"]["server_count"], 1)
        self.assertEqual(save_payload["server"]["review_state"], "candidate")

    def test_desktop_mobile_management_endpoint_exposes_android_and_ios_bridge_state(self):
        with patch.dict(
            os.environ,
            {"SPIRITKIN_MOBILE_TOKEN": "", "SPIRITKIN_ANDROID_TOKEN": "", "SPIRITKIN_IOS_TOKEN": ""},
            clear=False,
        ), patch("backend.app.mobile_management._resolve_adb_path", return_value=None), patch(
            "backend.app.mobile_management._pc_tailscale_ip", return_value="100.83.63.91"
        ), patch("backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}):
            status, payload = build_desktop_mobile_management_response()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mobile_management"]["schema_version"], "spiritkin.mobile_management.v1")
        self.assertEqual(payload["mobile_management"]["default_workspace_id"], "local-ecommerce")
        self.assertEqual(payload["mobile_management"]["workspaces"][0]["workspace_id"], "local-ecommerce")
        self.assertEqual(payload["mobile_management"]["android"]["endpoint"]["port"], 8791)
        self.assertIn("/android/link", payload["mobile_management"]["android"]["receiver_url"])
        self.assertIn("/pairing?workspace_id=local-ecommerce", payload["mobile_management"]["android"]["pairing_url"])
        android_worker = payload["mobile_management"]["android"]["worker"]
        self.assertEqual(android_worker["schema_version"], "spiritkin.android_worker.v1")
        self.assertEqual(android_worker["worker_id"], "android_control_worker")
        self.assertEqual(android_worker["role"], "controlled_execution_worker")
        self.assertEqual(android_worker["status"], "endpoint_offline")
        self.assertIn("queue", android_worker)
        self.assertIn("update", android_worker)
        self.assertIn("architecture", android_worker)
        self.assertEqual(android_worker["architecture"]["worker_role"], "android_control_worker")
        self.assertIn("shortcuts", payload["mobile_management"]["ios"])
        native_terminal = payload["mobile_management"]["ios"]["native_terminal"]
        self.assertEqual(native_terminal["scheme"], "spiritkin")
        self.assertIn("spiritkin://pair?", native_terminal["deep_link"])
        self.assertIn("server_url=http%3A%2F%2F100.83.63.91%3A8791", native_terminal["deep_link"])
        self.assertTrue(native_terminal["requires_pairing"])
        self.assertIn("pairing_url", native_terminal["config_json"])
        self.assertIn('"workspace_id": "local-ecommerce"', native_terminal["config_json"])
        self.assertEqual(payload["mobile_management"]["ios"]["endpoint"]["control_port"], 8791)
        self.assertEqual(payload["mobile_management"]["ios"]["endpoint"]["pwa_port"], 8792)
        security = payload["mobile_management"]["security"]
        self.assertEqual(security["network_scope"], "tailscale")
        self.assertTrue(security["https_required_for_public"])
        self.assertFalse(security["tokens"]["command_gateway"])
        self.assertFalse(security["tokens"]["android_endpoint"])
        self.assertFalse(security["tokens"]["ios_endpoint"])
        self.assertIn("ios_endpoint_token_missing", {item["warning_id"] for item in security["warnings"]})
        binding = payload["mobile_management"]["binding"]
        self.assertEqual(binding["schema_version"], "spiritkin.mobile_binding.v1")
        self.assertEqual(binding["workspace_id"], "local-ecommerce")
        self.assertEqual(binding["network"]["scope"], "tailscale")
        self.assertIn("/frontend/ios_controller_prototype.html?workspace_id=local-ecommerce", binding["ios"]["pwa_url"])
        self.assertIn("http://100.83.63.91:8791/ios/control/snapshot", binding["ios"]["control_snapshot_url"])
        self.assertIn("http://100.83.63.91:8791/ios/terminal?workspace_id=local-ecommerce", binding["ios"]["home_screen"]["install_url"])
        self.assertIn("preview_url", binding["ios"]["home_screen"])
        self.assertIn("/pairing?workspace_id=local-ecommerce", binding["android"]["pairing_page_url"])
        self.assertFalse(binding["tokens"]["android_endpoint"]["configured"])
        permission_policy = payload["mobile_management"]["android_command_permissions"]
        self.assertEqual(permission_policy["schema_version"], "spiritkin.android_command_permissions.v1")
        self.assertIn("automation", permission_policy["allowed_tiers"])

    def test_desktop_mobile_management_security_marks_public_http_high_risk(self):
        from backend.app.mobile_security import build_mobile_security_snapshot

        snapshot = build_mobile_security_snapshot(
            pc_tailscale_ip="",
            android_receiver_url="http://mobile.example.com:8791/android/link",
            android_pairing_url="http://mobile.example.com:8791/pairing?workspace_id=local-ecommerce",
            ios_base_url="http://mobile.example.com:8792",
        )

        self.assertEqual(snapshot["status"], "needs_attention")
        self.assertEqual(snapshot["network_scope"], "public_or_unknown")
        self.assertTrue(snapshot["https_required_for_public"])
        warnings = {item["warning_id"]: item["severity"] for item in snapshot["warnings"]}
        self.assertEqual(warnings["android_receiver_public_http"], "high")
        self.assertEqual(warnings["ios_terminal_public_http"], "high")

    def test_desktop_mobile_management_update_handles_refresh(self):
        with patch("backend.app.mobile_management._resolve_adb_path", return_value=None), patch(
            "backend.app.mobile_management._pc_tailscale_ip", return_value=""
        ), patch("backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}):
            status, payload = build_desktop_mobile_management_update_response({"action": "refresh"})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "refresh")
        self.assertIn("mobile_management", payload)

    def test_desktop_mobile_management_can_queue_android_command(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json")}, clear=False), patch(
            "backend.app.mobile_management._resolve_adb_path", return_value=None
        ), patch("backend.app.mobile_management._pc_tailscale_ip", return_value=""), patch(
            "backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}
        ):
            status, payload = build_desktop_mobile_management_update_response(
                {"action": "enqueue_android_command", "device_id": "phone1", "operation": "app.launch", "params": {"app_name": "Feishu"}}
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "queued")
        self.assertEqual(payload["result"]["command"]["permission"]["tier"], "open_app")
        self.assertEqual(payload["mobile_management"]["android"]["companion"]["pending_command_count"], 1)

    def test_desktop_mobile_management_blocks_high_risk_android_command(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json")}, clear=False), patch(
            "backend.app.mobile_management._resolve_adb_path", return_value=None
        ), patch("backend.app.mobile_management._pc_tailscale_ip", return_value=""), patch(
            "backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}
        ):
            status, payload = build_desktop_mobile_management_update_response(
                {"action": "enqueue_android_command", "device_id": "phone1", "operation": "adb.shell.rm", "params": {"command": "rm -rf /sdcard"}}
            )

        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["result"]["status"], "android_permission_tier_blocked")
        self.assertEqual(payload["result"]["permission"]["tier"], "high_risk")
        self.assertEqual(payload["mobile_management"]["android"]["companion"]["pending_command_count"], 0)

    def test_desktop_mobile_management_forwards_device_workflow_add_and_delete(self):
        forwarded = []

        def fake_control_action(payload):
            forwarded.append(dict(payload))
            return {"ok": True, "status": "ok", "message": "控制面动作已执行。", "control_response": {"ok": True}}

        with patch("backend.app.mobile_management._resolve_adb_path", return_value=None), patch(
            "backend.app.mobile_management._pc_tailscale_ip", return_value=""
        ), patch("backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}), patch(
            "backend.app.mobile_management._control_plane_action", side_effect=fake_control_action
        ):
            add_status, add_payload = build_desktop_mobile_management_update_response(
                {
                    "action": "add_device_workflow",
                    "workspace_id": "local-ecommerce",
                    "device_id": "phone1",
                    "workflow_id": "ecommerce.auto_listing.v1",
                    "enabled": True,
                }
            )
            delete_status, delete_payload = build_desktop_mobile_management_update_response(
                {
                    "action": "delete_device_workflow",
                    "workspace_id": "local-ecommerce",
                    "device_id": "phone1",
                    "workflow_id": "ecommerce.auto_listing.v1",
                }
            )

        self.assertEqual(add_status, 200)
        self.assertTrue(add_payload["ok"])
        self.assertEqual(delete_status, 200)
        self.assertTrue(delete_payload["ok"])
        self.assertEqual([item["action"] for item in forwarded], ["add_device_workflow", "delete_device_workflow"])
        self.assertEqual(forwarded[0]["device_id"], "phone1")
        self.assertEqual(forwarded[1]["workflow_id"], "ecommerce.auto_listing.v1")

    def test_desktop_mobile_management_forwards_pairing_request_actions(self):
        forwarded = []

        def fake_control_action(payload):
            forwarded.append(dict(payload))
            return {"ok": True, "status": "ok", "message": "控制面动作已执行。", "control_response": {"ok": True}}

        with patch("backend.app.mobile_management._resolve_adb_path", return_value=None), patch(
            "backend.app.mobile_management._pc_tailscale_ip", return_value=""
        ), patch("backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}), patch(
            "backend.app.mobile_management._control_plane_action", side_effect=fake_control_action
        ):
            approve_status, approve_payload = build_desktop_mobile_management_update_response(
                {"action": "approve_pairing_request", "workspace_id": "local-ecommerce", "request_id": "req1"}
            )
            reject_status, reject_payload = build_desktop_mobile_management_update_response(
                {"action": "reject_pairing_request", "workspace_id": "local-ecommerce", "request_id": "req2"}
            )
            cleanup_status, cleanup_payload = build_desktop_mobile_management_update_response(
                {"action": "clear_pairing_history", "workspace_id": "local-ecommerce"}
            )

        self.assertEqual(approve_status, 200)
        self.assertTrue(approve_payload["ok"])
        self.assertEqual(reject_status, 200)
        self.assertTrue(reject_payload["ok"])
        self.assertEqual(cleanup_status, 200)
        self.assertTrue(cleanup_payload["ok"])
        self.assertEqual(
            [item["action"] for item in forwarded],
            ["approve_pairing_request", "reject_pairing_request", "clear_pairing_history"],
        )
        self.assertEqual(forwarded[0]["request_id"], "req1")
        self.assertEqual(forwarded[1]["request_id"], "req2")

    def test_desktop_state_maintenance_snapshot_and_cleanup(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json"),
                "SPIRITKIN_MOBILE_ARTIFACT_ROOT": os.path.join(tmp, "mobile-artifacts"),
                "SPIRITKIN_KNOWLEDGE_JOB_HISTORY_PATH": os.path.join(tmp, "kb-jobs.json"),
                "SPIRITKIN_DESKTOP_STATE_PATH": os.path.join(tmp, "desktop-state.json"),
                "SPIRITKIN_SKILL_RUN_AUDIT_LOG": os.path.join(tmp, "skill-runs.jsonl"),
                "SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG": os.path.join(tmp, "project-runtime.jsonl"),
            },
            clear=False,
        ):
            root = Path(tmp)
            desktop_state_path = Path(os.environ["SPIRITKIN_DESKTOP_STATE_PATH"])
            desktop_state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "legacy.desktop_state",
                        "sessions": [{"id": "session_legacy", "title": "Legacy"}],
                        "projects": [{"id": "project_legacy", "title": "Legacy Project"}],
                    }
                ),
                encoding="utf-8",
            )
            android = AndroidCompanionStore(root / "android-companion.json")
            for idx in range(5):
                android.enqueue_command("phone1", "device.status", {"idx": idx})
            Path(os.environ["SPIRITKIN_SKILL_RUN_AUDIT_LOG"]).write_text(
                "".join(json.dumps({"schema_version": "spiritkin.skill_run_audit.v1", "at": idx, "skill_name": f"skill.{idx}"}) + "\n" for idx in range(5)),
                encoding="utf-8",
            )
            Path(os.environ["SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG"]).write_text(
                "".join(json.dumps({"schema_version": "spiritkin.project_runtime_audit.v1", "at": idx, "project_id": f"project.{idx}"}) + "\n" for idx in range(5)),
                encoding="utf-8",
            )

            status, snapshot_payload = build_desktop_state_maintenance_response()
            update_status, cleanup_payload = build_desktop_state_maintenance_update_response(
                {
                    "action": "migrate_state",
                    "keep_android_commands": 2,
                    "keep_android_history": 2,
                    "keep_skill_run_audit_events": 2,
                    "keep_project_runtime_events": 3,
                    "project_root": tmp,
                }
            )
            saved_desktop = json.loads(desktop_state_path.read_text(encoding="utf-8"))
            cleanup_status, cleanup_all_payload = build_desktop_state_maintenance_update_response(
                {
                    "action": "cleanup_all",
                    "keep_recent": 30,
                    "keep_android_commands": 2,
                    "keep_android_history": 2,
                    "keep_kb_jobs": 80,
                    "keep_skill_run_audit_events": 2,
                    "keep_project_runtime_events": 3,
                    "project_root": tmp,
                }
            )
            skill_lines = Path(os.environ["SPIRITKIN_SKILL_RUN_AUDIT_LOG"]).read_text(encoding="utf-8").splitlines()
            project_lines = Path(os.environ["SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG"]).read_text(encoding="utf-8").splitlines()

        self.assertEqual(status, 200)
        self.assertTrue(snapshot_payload["ok"])
        self.assertEqual(snapshot_payload["state_maintenance"]["schema_version"], "spiritkin.state_maintenance.v1")
        snapshot_components = {item["component_id"] for item in snapshot_payload["state_maintenance"]["components"]}
        self.assertIn("desktop_state", snapshot_components)
        self.assertIn("skill_run_audit", snapshot_components)
        self.assertIn("project_runtime_audit", snapshot_components)
        self.assertIn("generated_build_artifacts", snapshot_components)
        self.assertIn("reproducible_caches", snapshot_components)
        self.assertEqual(update_status, 200)
        self.assertTrue(cleanup_payload["ok"])
        self.assertEqual(cleanup_payload["result"]["desktop_state"]["from_schema_version"], "legacy.desktop_state")
        self.assertEqual(cleanup_payload["result"]["desktop_state"]["to_schema_version"], "spiritkin.desktop_console.v1")
        self.assertIn("android_command_history", {item["component_id"] for item in cleanup_payload["state_maintenance"]["components"]})
        self.assertEqual(saved_desktop["schema_version"], "spiritkin.desktop_console.v1")
        self.assertTrue(saved_desktop["migration_history"])
        self.assertEqual(cleanup_status, 200)
        self.assertTrue(cleanup_all_payload["ok"])
        self.assertEqual(len(skill_lines), 2)
        self.assertEqual(len(project_lines), 3)
        self.assertEqual(cleanup_all_payload["result"]["results"]["skill_run_audit"]["remaining"], 2)
        self.assertEqual(cleanup_all_payload["result"]["results"]["project_runtime_audit"]["remaining"], 3)

    def test_desktop_state_maintenance_removes_only_generated_verification_outputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated_bin = root / "state" / "build" / "bin-verify-tests"
            generated_obj = root / "state" / "build" / "obj-verify-tests"
            retained = root / "state" / "build" / "user-output"
            for generated in (generated_bin, generated_obj, retained):
                generated.mkdir(parents=True)
                (generated / "artifact.bin").write_bytes(b"verification")
            status, payload = build_desktop_state_maintenance_update_response(
                {"action": "cleanup_generated_build_artifacts", "project_root": tmp}
            )
            generated_bin_exists = generated_bin.exists()
            generated_obj_exists = generated_obj.exists()
            retained_exists = retained.exists()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(generated_bin_exists)
        self.assertFalse(generated_obj_exists)
        self.assertTrue(retained_exists)
        removed = payload["result"]["removed"]
        self.assertEqual(len(removed), 2)
        removed_names = {Path(item["path"]).name for item in removed}
        self.assertEqual(removed_names, {"bin-verify-tests", "obj-verify-tests"})

    def test_desktop_state_maintenance_removes_only_allowlisted_reproducible_caches(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            removable = (
                root / "tmp" / "lucide-static-package",
                root / "state" / "providers" / "miniconda3" / "pkgs",
                root / "output" / "playwright",
                root / ".playwright-cli",
                root / "desktop" / "SpiritKinDesktop" / "obj",
                root / "desktop" / "SpiritKinDesktop.Tests" / "bin",
                root / "desktop" / "SpiritKinDesktop.Tests" / "obj",
            )
            retained = (
                root / "tmp" / "reference-repos",
                root / "state" / "providers" / "cosyvoice-model",
                root / "desktop" / "SpiritKinDesktop" / "bin",
            )
            for directory in (*removable, *retained):
                directory.mkdir(parents=True)
                (directory / "artifact.bin").write_bytes(b"cache")

            status, payload = build_desktop_state_maintenance_update_response(
                {"action": "cleanup_reproducible_caches", "project_root": tmp}
            )
            removable_exists = [directory.exists() for directory in removable]
            retained_exists = [directory.exists() for directory in retained]

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(removable_exists, [False, False, False, False, False, False, False])
        self.assertEqual(retained_exists, [True, True, True])
        self.assertEqual(len(payload["result"]["removed"]), 7)

    def test_desktop_action_log_aggregates_major_audit_sources(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json"),
                "SPIRITKIN_SERVICE_ACTION_LOG": os.path.join(tmp, "service-actions.jsonl"),
                "SPIRITKIN_SAFETY_STATE_PATH": os.path.join(tmp, "safety.json"),
                "SPIRITKIN_MCP_REGISTRY_PATH": os.path.join(tmp, "mcp-registry.json"),
                "SPIRITKIN_SKILL_RUN_AUDIT_LOG": os.path.join(tmp, "skill-runs.jsonl"),
                "SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG": os.path.join(tmp, "project-runtime.jsonl"),
            },
            clear=False,
        ):
            root = Path(tmp)
            AndroidCompanionStore(root / "android-companion.json").enqueue_command(
                "phone1",
                "app.launch",
                {"app_name": "Feishu", "actor": "desktop_test"},
            )
            Path(os.environ["SPIRITKIN_SERVICE_ACTION_LOG"]).write_text(
                json.dumps(
                    {
                        "created_at": 1710000000.0,
                        "action": "restart",
                        "service_id": "command_gateway",
                        "label": "命令网关",
                        "ok": True,
                        "status": "restart_scheduled",
                        "message": "restart queued",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            Path(os.environ["SPIRITKIN_SAFETY_STATE_PATH"]).write_text(
                json.dumps(
                    {
                        "schema_version": "spiritkin.safety_control.v1",
                        "active": False,
                        "mode": "normal",
                        "history": [{"at": "2026-06-16T01:00:00Z", "action": "clear_stop", "actor": "tester", "reason": "resume"}],
                    }
                ),
                encoding="utf-8",
            )
            Path(os.environ["SPIRITKIN_MCP_REGISTRY_PATH"]).write_text(
                json.dumps(
                    {
                        "schema_version": "spiritkin.mcp_management.v2",
                        "servers": [],
                        "audit_log": [{"at": 1710000001.0, "action": "save_server", "server_id": "local-tools", "actor": "tester", "success": True}],
                    }
                ),
                encoding="utf-8",
            )
            workflow_store = root / "state" / "workflows"
            workflow_store.mkdir(parents=True)
            (workflow_store / "audit.jsonl").write_text(
                json.dumps(
                    {
                        "at": "2026-06-16T01:01:00Z",
                        "action": "run_archived",
                        "workflow_name": "demo.workflow.v1",
                        "actor": "tester",
                        "message": "archived",
                        "payload": {"run_id": "run_1", "status": "archived"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            Path(os.environ["SPIRITKIN_SKILL_RUN_AUDIT_LOG"]).write_text(
                json.dumps(
                    {
                        "schema_version": "spiritkin.skill_run_audit.v1",
                        "at": 1710000002.0,
                        "actor": "tester",
                        "skill_name": "workflow.local.test",
                        "dry_run": True,
                        "success": True,
                        "message": "dry run ok",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            Path(os.environ["SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG"]).write_text(
                json.dumps(
                    {
                        "schema_version": "spiritkin.project_runtime_audit.v1",
                        "at": 1710000003.0,
                        "action": "start_command",
                        "status": "started",
                        "actor": "tester",
                        "project_id": "project-runtime",
                        "project_title": "Project Runtime",
                        "workspace_path": str(root),
                        "command": "npm run dev",
                        "risk_level": "low",
                        "message": "start recorded",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            status, payload = build_desktop_action_log_response(limit=20, project_root=tmp)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        action_log = payload["action_log"]
        self.assertEqual(action_log["schema_version"], "spiritkin.action_log.v1")
        sources = {item["source"] for item in action_log["events"]}
        self.assertIn("android_command", sources)
        self.assertIn("android_history", sources)
        self.assertIn("service", sources)
        self.assertIn("safety", sources)
        self.assertIn("mcp", sources)
        self.assertIn("workflow", sources)
        self.assertIn("skill", sources)
        self.assertIn("project_runtime", sources)
        timestamps = [item["timestamp"] for item in action_log["events"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
        self.assertFalse(action_log["errors"])

    def test_desktop_code_jury_endpoint_records_structured_report_and_audit(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_CODE_JURY_AUDIT_LOG": os.path.join(tmp, "code-jury.jsonl"),
                "SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json"),
                "SPIRITKIN_SERVICE_ACTION_LOG": os.path.join(tmp, "service-actions.jsonl"),
                "SPIRITKIN_SAFETY_STATE_PATH": os.path.join(tmp, "safety.json"),
                "SPIRITKIN_MCP_REGISTRY_PATH": os.path.join(tmp, "mcp-registry.json"),
                "SPIRITKIN_SKILL_RUN_AUDIT_LOG": os.path.join(tmp, "skill-runs.jsonl"),
                "SPIRITKIN_PROJECT_RUNTIME_AUDIT_LOG": os.path.join(tmp, "project-runtime.jsonl"),
            },
            clear=False,
        ):
            status, initial_payload = build_desktop_code_jury_response()
            review_status, review_payload = build_desktop_code_jury_update_response(
                {
                    "action": "review",
                    "actor": "unit-test",
                    "package": {
                        "review_type": "ui",
                        "requirement": "检查桌面模型面板布局",
                        "files_changed": ["frontend/desktop_console.html"],
                        "screenshots": [{"path": "tmp/models.png", "viewport": "desktop"}],
                    },
                    "model_reviews": [
                        {
                            "provider": "unit",
                            "model": "reviewer",
                            "response_text": json.dumps(
                                {
                                    "decision": "changes_requested",
                                    "scores": {
                                        "usability": 86,
                                        "visual_hierarchy": 72,
                                        "accessibility": 80,
                                        "consistency": 84,
                                        "discoverability": 78,
                                    },
                                    "findings": [
                                        {
                                            "severity": "medium",
                                            "category": "visual_hierarchy",
                                            "title": "Model controls lack hierarchy",
                                            "detail": "Primary save and review controls compete visually.",
                                            "file_path": "frontend/desktop_console.html",
                                            "suggested_fix": "Group jury controls under the model review section.",
                                        }
                                    ],
                                }
                            ),
                        }
                    ],
                }
            )
            action_status, action_payload = build_desktop_action_log_response(limit=20, project_root=tmp)

        self.assertEqual(status, 200)
        self.assertEqual(initial_payload["code_jury"]["schema_version"], "spiritkin.code_jury.v1")
        self.assertEqual(review_status, 200)
        self.assertTrue(review_payload["ok"])
        self.assertEqual(review_payload["jury_report"]["decision"], "changes_requested")
        self.assertEqual(review_payload["jury_report"]["summary"]["structured_review_count"], 1)
        self.assertEqual(review_payload["patch_synthesis"]["status"], "proposal_ready")
        self.assertFalse(review_payload["jury_report"]["promotion_gate"]["auto_apply_allowed"])
        self.assertEqual(action_status, 200)
        self.assertIn("code_jury", {item["source"] for item in action_payload["action_log"]["events"]})

    def test_desktop_mobile_management_can_create_android_pairing_token(self):
        pairing_response = {
            "ok": True,
            "pairing": {
                "workspace_id": "tenant-a",
                "pairing_token": "secret-token",
                "deep_link": "spiritkin://pair?pairing_token=secret-token",
                "expires_at": "2026-06-14T12:00:00Z",
            },
        }
        with patch("backend.app.mobile_management._resolve_adb_path", return_value=None), patch(
            "backend.app.mobile_management._pc_tailscale_ip", return_value="100.83.63.91"
        ), patch("backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}), patch(
            "backend.app.mobile_management._http_json", return_value=pairing_response
        ) as http_json:
            status, payload = build_desktop_mobile_management_update_response(
                {"action": "create_android_pairing", "workspace_id": "tenant-a", "ttl_minutes": 15}
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "pairing_created")
        self.assertEqual(payload["result"]["pairing"]["workspace_id"], "tenant-a")
        self.assertEqual(payload["result"]["pairing"]["pairing_token"], "secret-token")
        self.assertEqual(payload["result"]["pairing"]["pairing_page_url"], "http://100.83.63.91:8791/pairing?workspace_id=tenant-a")
        requested_url = http_json.call_args.args[0]
        self.assertIn("workspace_id=tenant-a", requested_url)
        self.assertIn("ttl_minutes=15", requested_url)

    def test_token_authorization_accepts_header_or_bearer(self):
        self.assertTrue(token_is_authorized(FakeHeaders({"X-SpiritKin-Token": "abc"}), "abc"))
        self.assertTrue(token_is_authorized(FakeHeaders({"Authorization": "Bearer abc"}), "abc"))
        self.assertFalse(token_is_authorized(FakeHeaders({"X-SpiritKin-Token": "wrong"}), "abc"))

    def test_token_authorization_requires_token_even_for_localhost(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(token_is_authorized(FakeHeaders({"Host": "127.0.0.1:8788"}), "abc"))
        with patch.dict(os.environ, {"SPIRITKIN_ALLOW_LOCALHOST_WITHOUT_TOKEN": "1"}, clear=False):
            self.assertFalse(token_is_authorized(FakeHeaders({"Host": "127.0.0.1:8788"}), "abc"))
            self.assertTrue(token_is_authorized(FakeHeaders({"Host": "127.0.0.1:8788"}), ""))
            # A spoofed local Host header must not unlock the bypass for a remote peer.
            self.assertFalse(token_is_authorized(FakeHeaders({"Host": "127.0.0.1:8788"}), "", client_ip="203.0.113.5"))

    def test_gateway_security_context_marks_public_token_requirement(self):
        context = build_gateway_security_context(FakeHeaders({"Host": "mobile.example.com", "Authorization": "Bearer abc"}), expected_token="abc", client_ip="203.0.113.10")

        self.assertTrue(context["public_access"])
        self.assertTrue(context["token_required"])
        self.assertTrue(context["authenticated"])
        self.assertEqual(context["auth_method"], "bearer")

    def test_gateway_security_context_marks_localhost_as_local(self):
        with patch.dict(os.environ, {}, clear=True):
            context = build_gateway_security_context(FakeHeaders({"Host": "127.0.0.1:8788"}), expected_token="abc", client_ip="127.0.0.1")

        self.assertTrue(context["local_request"])
        self.assertTrue(context["token_required"])
        self.assertFalse(context["localhost_bypass_enabled"])

    def test_mobile_access_smoke_checks_frontend_and_command_health(self):
        class SmokeHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/index.html":
                    self.send_response(200)
                    body = b"<!DOCTYPE html><title>SpiritKin</title>"
                elif self.path == "/health":
                    self.send_response(200)
                    body = b'{"ok": true, "service": "spiritkin-command-gateway"}'
                else:
                    self.send_response(404)
                    body = b"not found"
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            report = run_mobile_access_smoke(
                frontend_url=f"http://{host}:{port}/index.html",
                command_url=f"http://{host}:{port}/command",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(report["ok"])
        self.assertEqual(report["checks"]["command_gateway"]["url"], f"http://127.0.0.1:{port}/health")

    def test_command_health_url_normalizes_command_path(self):
        self.assertEqual(_command_health_url("http://100.64.0.8:8788/command"), "http://100.64.0.8:8788/health")
        self.assertEqual(_command_health_url("http://100.64.0.8:8788"), "http://100.64.0.8:8788/health")


if __name__ == "__main__":
    unittest.main()
