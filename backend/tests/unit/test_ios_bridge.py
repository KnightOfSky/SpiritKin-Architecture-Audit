from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.mobile.artifact_store import MobileArtifactStore
from backend.mobile.ios_bridge import (
    build_shortcut_url_scheme,
    generate_app_intent_schema,
    generate_shortcut_schema,
    iOSAppIntentPayload,
    iOSCommandTranslator,
    iOSShortcutPayload,
    validate_ios_action,
)
from backend.mobile.ios_domains import classify_workflow, workflow_domain_catalog
from backend.mobile.ios_ecommerce import build_ios_ecommerce_snapshot, handle_ios_ecommerce_action
from backend.mobile.ios_endpoint import (
    _ios_compact_model_governance,
    _ios_compact_module_management,
    _ios_control_action,
    _ios_terminal_html,
    _ios_terminal_manifest,
    iOSShortcutEndpoint,
)
from backend.mobile.ios_monitoring import build_ios_monitor_snapshot, handle_ios_monitor_action
from backend.mobile.ios_pools import build_ios_pools_snapshot, handle_ios_pool_action
from backend.mobile.ios_resources import build_ios_resources_snapshot, handle_ios_resource_action
from backend.mobile.ios_sessions import ios_sessions_snapshot, update_ios_sessions
from backend.mobile.ios_shortcuts_catalog import SHORTCUT_CATALOG, ShortcutDefinition
from scripts.control_plane_store import ControlPlaneStore
from scripts.mobile_link_receiver import Handler as MobileReceiverHandler


class FakeiOSHandler:
    _authorized = iOSShortcutEndpoint._authorized
    _workspace_id = iOSShortcutEndpoint._workspace_id

    def __init__(self, *, headers=None, path="/ios/command", auth_token="", client_ip=""):
        self.headers = headers or {}
        self.path = path
        self.auth_token = auth_token
        if client_ip:
            self.client_address = (client_ip, 0)


class iOSBridgeTests(unittest.TestCase):
    def test_native_ios_registers_real_app_intents_and_dynamic_shortcut_catalog(self):
        root = Path(__file__).resolve().parents[3]
        intents = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Intents" / "SpiritKinAppIntents.swift").read_text(encoding="utf-8")
        automation = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Views" / "IOSAutomationView.swift").read_text(encoding="utf-8")
        api = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Networking" / "TerminalAPI.swift").read_text(encoding="utf-8")
        app = (root / "ios" / "SpiritKinTerminal" / "Sources" / "App" / "SpiritKinTerminalApp.swift").read_text(encoding="utf-8")

        self.assertIn("struct AskSpiritAppIntent: AppIntent", intents)
        self.assertIn("struct CheckSpiritStatusAppIntent: AppIntent", intents)
        self.assertIn("struct ReadClipboardAppIntent: AppIntent", intents)
        self.assertIn("struct WriteClipboardAppIntent: AppIntent", intents)
        self.assertIn("struct SendLocalNotificationAppIntent: AppIntent", intents)
        self.assertIn("struct CheckBatteryAppIntent: AppIntent", intents)
        self.assertIn("struct SpiritKinAppShortcuts: AppShortcutsProvider", intents)
        self.assertIn("store.shortcutCatalog", automation)
        self.assertIn("func shortcutCatalog()", api)
        self.assertIn("SpiritKinAppShortcuts.updateAppShortcutParameters()", app)
        self.assertIn('AutomationRow(title: "Read Clipboard"', automation)

    def test_native_ios_can_create_and_cancel_remote_worker_pairing(self):
        root = Path(__file__).resolve().parents[3]
        api = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Networking" / "TerminalAPI.swift").read_text(encoding="utf-8")
        store = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Store" / "TerminalStore.swift").read_text(encoding="utf-8")
        view = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Views" / "AndroidBridgeView.swift").read_text(encoding="utf-8")

        self.assertIn('endpoint("ios/control/pairing")', api)
        self.assertIn("func createRemoteWorkerPairing()", store)
        self.assertIn("func cancelRemoteWorkerPairing()", store)
        self.assertIn("生成一次性配对码", view)

    def test_native_ios_runtime_host_and_arkit_publish_structured_observations(self):
        root = Path(__file__).resolve().parents[3]
        api = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Networking" / "TerminalAPI.swift").read_text(encoding="utf-8")
        store = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Store" / "TerminalStore.swift").read_text(encoding="utf-8")
        devices = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Views" / "AndroidBridgeView.swift").read_text(encoding="utf-8")
        provider = (root / "ios" / "SpiritKinTerminal" / "Sources" / "World" / "ARKitObservationProvider.swift").read_text(encoding="utf-8")
        world_view = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Views" / "WorldObservationView.swift").read_text(encoding="utf-8")
        info = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Info.plist").read_text(encoding="utf-8")

        self.assertIn('endpoint("ios/runtime-host")', api)
        self.assertIn('endpoint("ios/observations")', api)
        self.assertIn("func refreshRuntimeHosts", store)
        self.assertIn("func publishObservation", store)
        self.assertIn("RuntimeHostView()", devices)
        self.assertIn("WorldObservationView()", devices)
        self.assertIn("ARWorldTrackingConfiguration", provider)
        self.assertIn("RealityKit", provider)
        self.assertIn("sceneReconstruction = .meshWithClassification", provider)
        self.assertIn("let worldCenter = plane.transform * SIMD4<Float>", provider)
        self.assertIn("return .object([", provider)
        self.assertIn("observationInterval: TimeInterval = 2.0", provider)
        self.assertIn("不会上传 RGB 帧、深度图、点云或录像", world_view)
        self.assertNotIn("capturedImage", provider)
        self.assertNotIn("sceneDepth.depthMap", provider)
        self.assertNotIn("fencing_token", world_view)
        self.assertIn("NSLocationWhenInUseUsageDescription", info)

    def test_native_ios_growth_uses_runtime_escalation_contract(self):
        root = Path(__file__).resolve().parents[3]
        pools = (root / "ios" / "SpiritKinTerminal" / "Sources" / "Views" / "CapabilityPoolsView.swift").read_text(encoding="utf-8")

        self.assertIn('"action": .string("escalate_candidate")', pools)
        self.assertIn('"action": .string("research_candidate")', pools)
        self.assertIn("研究公开仓库元数据", pools)
        self.assertIn("@State private var researchKeywords", pools)
        self.assertIn("let keywords = researchKeywords.trimmingCharacters", pools)
        self.assertIn('"action": .string("execute_builder_sandbox")', pools)
        self.assertIn('"action": .string("prepare_sandbox_bundle")', pools)
        self.assertIn('"execution_ack": .string("run_untrusted_code_in_isolated_container")', pools)
        self.assertIn("运行隔离候选测试", pools)
        self.assertIn("JSONHelpers.parseObject(sandboxBundleJSON)", pools)
        self.assertIn('sandboxRuntime["candidate_execution_enabled"]', pools)
        self.assertIn('"action": .string("record_candidate_benchmark")', pools)
        self.assertIn("benchmarkConfirmationPresented", pools)
        self.assertIn("Promotion Gate", pools)
        self.assertIn("benchmarkReport[\"overall_score\"].doubleValue", pools)
        self.assertIn("client-supplied model jury", (root / "backend" / "evaluation" / "benchmark_runtime.py").read_text(encoding="utf-8"))
        self.assertIn('["escalation_targets"]', pools)
        self.assertIn('["parent_candidate_id"]', pools)
        self.assertIn('"confirmed": .bool(true)', pools)
        self.assertIn("leftIsCurrent && !rightIsCurrent", pools)

    def test_mobile_receiver_reads_workspace_header_with_query_precedence(self):
        handler = object.__new__(MobileReceiverHandler)
        handler.headers = {"X-SpiritKin-Workspace": "tenant-header"}

        self.assertEqual(handler._requested_workspace_id(), "tenant-header")
        self.assertEqual(
            handler._requested_workspace_id({"workspace_id": ["tenant-query"]}),
            "tenant-query",
        )

    def test_ios_domain_catalog_keeps_terminal_under_ecommerce(self):
        self.assertEqual(classify_workflow("ecommerce.auto_listing.v1"), "ecommerce")
        self.assertEqual(classify_workflow("content.video_generation.v1"), "content")
        self.assertEqual(classify_workflow("local.cli.run.v1"), "engineering")
        self.assertEqual({item["id"] for item in workflow_domain_catalog()}, {"ecommerce", "content", "engineering", "system", "general"})

    def test_ios_sessions_round_trip_uses_desktop_state(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_DESKTOP_STATE_PATH": os.path.join(tmp, "state.json")}, clear=False):
            initial = ios_sessions_snapshot()
            initial["active_session_id"] = "session_ios_test"
            initial["sessions"] = [
                {
                    "id": "session_ios_test",
                    "title": "电商预检",
                    "status": "active",
                    "created_at": 1,
                    "updated_at": 1,
                    "messages": [{"id": "message_1", "role": "user", "text": "检查商品", "created_at": 1, "updated_at": 1}],
                }
            ]
            saved = update_ios_sessions(initial)
            self.assertEqual(saved["active_session_id"], "session_ios_test")
            self.assertEqual(saved["sessions"][0]["messages"][0]["text"], "检查商品")
            self.assertEqual(ios_sessions_snapshot()["sessions"][0]["title"], "电商预检")

            initial["sessions"][0]["messages"].append({"id": "internal", "role": "system", "text": "思考 · internal model trace", "created_at": 2, "updated_at": 2})
            saved = update_ios_sessions(initial)
            self.assertEqual(saved["sessions"][0]["messages"][-1]["text"], "系统状态已同步。")

    def test_ios_endpoint_without_token_is_not_open_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(FakeiOSHandler()._authorized())
        with patch.dict(os.environ, {"SPIRITKIN_ALLOW_LOCALHOST_WITHOUT_TOKEN": "1"}, clear=False):
            self.assertFalse(FakeiOSHandler(headers={"Host": "127.0.0.1:8792"})._authorized())
            self.assertFalse(FakeiOSHandler(client_ip="127.0.0.1")._authorized())
            self.assertFalse(FakeiOSHandler(client_ip="203.0.113.5")._authorized())
            self.assertFalse(FakeiOSHandler(headers={"Host": "127.0.0.1:8792"}, client_ip="203.0.113.5")._authorized())

    def test_ios_endpoint_accepts_header_only_token(self):
        self.assertTrue(FakeiOSHandler(headers={"X-SpiritKin-iOS-Token": "secret"}, auth_token="secret")._authorized())
        self.assertFalse(FakeiOSHandler(path="/ios/command?token=secret", auth_token="secret")._authorized())
        self.assertFalse(FakeiOSHandler(headers={"X-SpiritKin-iOS-Token": "wrong"}, auth_token="secret")._authorized())

    def test_shortcut_to_execution_request_maps_query(self):
        payload = iOSShortcutPayload(shortcut_name="Ask Spirit", input_text="hello")
        request = iOSCommandTranslator.shortcut_to_execution_request(payload)
        self.assertEqual(request.target, "ios_device")
        self.assertEqual(request.operation, "shortcut_query")
        self.assertEqual(request.params["text"], "hello")

    def test_shortcut_to_execution_request_maps_allowlisted_action(self):
        payload = iOSShortcutPayload(shortcut_name="Check Battery", parameters={"action": "check_battery"})
        request = iOSCommandTranslator.shortcut_to_execution_request(payload)
        self.assertEqual(request.operation, "device.battery")

    def test_app_intent_to_execution_request_rejects_unknown_action(self):
        payload = iOSAppIntentPayload(intent_name="delete_everything", parameters={"action": "delete_everything"})
        request = iOSCommandTranslator.app_intent_to_execution_request(payload)
        self.assertEqual(request.operation, "clarify")
        self.assertEqual(request.params["reason"], "unsupported_ios_action")

    def test_reply_to_shortcut_output(self):
        output = iOSCommandTranslator.reply_to_shortcut_output({"text": "hi there", "emotion": "happy", "success": True})
        self.assertEqual(output["result"], "hi there")
        self.assertEqual(output["emotion"], "happy")

    def test_generate_shortcut_schema(self):
        schema = generate_shortcut_schema("test")
        self.assertEqual(schema["name"], "test")
        self.assertIn("input_fields", schema)

    def test_generate_app_intent_schema(self):
        schema = generate_app_intent_schema("test_intent")
        self.assertEqual(schema["name"], "test_intent")
        self.assertEqual(schema["method"], "POST")
        self.assertIn("ask_spirit", schema["allowed_actions"])

    def test_build_shortcut_url_scheme(self):
        url = build_shortcut_url_scheme("test", "https://example.com", "token123")
        self.assertIn("example.com", url)
        self.assertIn("shortcut_name=test", url)

    def test_validate_ios_action_uses_allowlist(self):
        self.assertTrue(validate_ios_action("ask_spirit")[0])
        self.assertFalse(validate_ios_action("unsafe")[0])

    def test_shortcut_catalog_has_six_shortcuts(self):
        self.assertEqual(len(SHORTCUT_CATALOG), 6)

    def test_shortcut_catalog_contains_ask_spirit(self):
        names = {s.name for s in SHORTCUT_CATALOG}
        self.assertIn("Ask Spirit", names)
        self.assertIn("Read Clipboard", names)
        self.assertIn("Check Battery", names)

    def test_shortcut_definition_fields(self):
        definition = ShortcutDefinition(name="Test", description="desc", icon="star", color="red")
        self.assertEqual(definition.icon, "star")
        self.assertFalse(definition.confirmation_required)

    def test_ios_endpoint_exposes_mobile_artifact_download_route(self):
        constants = _flatten_constants(iOSShortcutEndpoint.do_GET.__code__.co_consts)
        self.assertIn("/mobile/artifacts/", constants)

    def test_ios_endpoint_exposes_pwa_routes(self):
        constants = _flatten_constants(iOSShortcutEndpoint.do_GET.__code__.co_consts)
        self.assertIn("/ios/terminal.webmanifest", constants)
        self.assertIn("/ios/service-worker.js", constants)
        self.assertTrue(any(isinstance(item, frozenset) and "/ios/apple-touch-icon.png" in item for item in constants))

    def test_ios_endpoint_exposes_shared_domain_resource_ecommerce_monitor_contract(self):
        get_constants = _flatten_constants(iOSShortcutEndpoint.do_GET.__code__.co_consts)
        post_constants = _flatten_constants(iOSShortcutEndpoint.do_POST.__code__.co_consts)
        for route in ("/ios/domains", "/ios/resources", "/ios/ecommerce", "/ios/monitor"):
            self.assertIn(route, get_constants)
            self.assertIn(route, post_constants)

    def test_ios_monitor_repairs_stale_worker_without_retrying_failed_workflow(self):
        with TemporaryDirectory() as tmp:
            store = ControlPlaneStore(os.path.join(tmp, "control"))
            store.ensure_workspace("tenant-a", "Tenant A")
            heartbeat = store.worker_heartbeat(
                {
                    "worker_id": "stale-worker",
                    "workspace_id": "tenant-a",
                    "capabilities": ["local_pc"],
                },
                client="127.0.0.1",
            )
            state = store.load()
            worker_id = heartbeat["worker_id"]
            state["remote_workers"][worker_id]["last_seen_at"] = (
                datetime.now(UTC) - timedelta(hours=1)
            ).isoformat()
            state["workflow_runs"]["run-failed"] = {
                "run_id": "run-failed",
                "workspace_id": "tenant-a",
                "status": "failed",
                "error": "unit failure",
                "updated_at": datetime.now(UTC).isoformat(),
            }
            store.save(state)

            monitor = build_ios_monitor_snapshot(store, workspace_id="tenant-a")
            repaired = handle_ios_monitor_action(
                store,
                {"action": "auto_repair", "older_than_hours": 0.1},
                workspace_id="tenant-a",
            )
            saved = store.load()

        self.assertIn("remote_workers", {item["source"] for item in monitor["incidents"]})
        self.assertIn("workflow", {item["source"] for item in monitor["incidents"]})
        self.assertIn(worker_id, repaired["repair_result"]["offline_workers"])
        self.assertEqual(saved["remote_workers"][worker_id]["status"], "offline")
        self.assertEqual(saved["workflow_runs"]["run-failed"]["status"], "failed")

    def test_ios_endpoint_workspace_header_has_local_ecommerce_default(self):
        handler = FakeiOSHandler(headers={"X-SpiritKin-Workspace": "tenant-a"})
        with self.assertRaises(PermissionError):
            handler._workspace_id()
        self.assertEqual(FakeiOSHandler()._workspace_id(), "local-ecommerce")

    def test_ios_pool_snapshot_hides_other_workspace_entries(self):
        skills = {
            "skills": [
                {"name": "global", "metadata": {}},
                {"name": "owned", "metadata": {"workspace_id": "tenant-a"}},
                {"name": "hidden", "metadata": {"workspace_id": "tenant-b"}},
            ],
            "status_counts": {},
        }
        workflows = {
            "definitions": [
                {"name": "global.workflow", "nodes": [], "metadata": {}},
                {"name": "owned.workflow", "nodes": [], "metadata": {"workspace_id": "tenant-a"}},
                {"name": "hidden.workflow", "nodes": [], "metadata": {"workspace_id": "tenant-b"}},
            ]
        }
        with (
            patch("backend.mobile.ios_pools.build_desktop_skills_snapshot", return_value=skills),
            patch("backend.mobile.ios_pools.build_workflow_management_snapshot", return_value=workflows),
        ):
            snapshot = build_ios_pools_snapshot(workspace_id="tenant-a")

        self.assertEqual({item["name"] for item in snapshot["skills"]["items"]}, {"global", "owned"})
        self.assertEqual(
            {item["name"] for item in snapshot["workflows"]["items"]},
            {"global.workflow", "owned.workflow"},
        )
        self.assertFalse(next(item for item in snapshot["skills"]["items"] if item["name"] == "global")["editable"])
        self.assertTrue(next(item for item in snapshot["skills"]["items"] if item["name"] == "owned")["editable"])

    def test_ios_pool_cannot_overwrite_global_skill(self):
        with patch(
            "backend.mobile.ios_pools.build_desktop_skills_snapshot",
            return_value={"skills": [{"name": "global", "metadata": {}}]},
        ):
            with self.assertRaises(PermissionError):
                handle_ios_pool_action(
                    {"pool": "skills", "action": "update", "name": "global"},
                    workspace_id="tenant-a",
                )

    def test_ios_resource_snapshot_and_updates_are_workspace_scoped(self):
        management = {
            "resource_registry": {
                "resources": [
                    {"resource_id": "global", "label": "Global", "metadata": {}},
                    {"resource_id": "owned", "label": "Owned", "metadata": {"workspace_id": "tenant-a"}},
                    {"resource_id": "hidden", "label": "Hidden", "metadata": {"workspace_id": "tenant-b"}},
                ]
            }
        }
        with patch("backend.mobile.ios_resources.build_resource_management_snapshot", return_value=management):
            snapshot = build_ios_resources_snapshot(workspace_id="tenant-a")
            with self.assertRaises(PermissionError):
                handle_ios_resource_action(
                    {"action": "update", "resource": {"resource_id": "global", "label": "Claimed"}},
                    workspace_id="tenant-a",
                )

        items = snapshot["resource_registry"]["resources"]
        self.assertEqual({item["resource_id"] for item in items}, {"global", "owned"})
        self.assertFalse(next(item for item in items if item["resource_id"] == "global")["editable"])
        self.assertTrue(next(item for item in items if item["resource_id"] == "owned")["editable"])

    def test_ios_ecommerce_snapshot_and_mutations_are_workspace_scoped(self):
        queue = {
            "tasks": [
                {"id": "task-a", "workspace_id": "tenant-a", "status": "pending"},
                {"id": "task-b", "workspace_id": "tenant-b", "status": "pending"},
            ]
        }

        class Store:
            def snapshot(self, _workspace_id=None):
                return {}

            def validate_state(self, *, workspace_id=None):
                return {"ok": True, "workspace_id": workspace_id, "errors": [], "warnings": []}

        with (
            patch("backend.mobile.ios_ecommerce.load_queue", return_value=queue),
            patch("backend.mobile.ios_ecommerce.build_ios_pools_snapshot", return_value={"workflows": {"items": []}}),
            patch(
                "backend.mobile.ios_ecommerce.build_ios_resources_snapshot",
                return_value={"resource_registry": {"resources": []}},
            ),
        ):
            snapshot = build_ios_ecommerce_snapshot(Store(), workspace_id="tenant-a")
            with self.assertRaises(PermissionError):
                handle_ios_ecommerce_action(
                    Store(),
                    {"action": "update_task", "task_id": "task-b", "title": "Claimed"},
                    workspace_id="tenant-a",
                )

        self.assertEqual([item["id"] for item in snapshot["queue"]["items"]], ["task-a"])

    def test_ios_terminal_manifest_is_standalone_pwa(self):
        manifest = _ios_terminal_manifest()
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/ios/terminal")
        self.assertEqual(manifest["scope"], "/ios/")
        self.assertTrue(any(icon["src"] == "/ios/icon.svg" for icon in manifest["icons"]))

    def test_ios_terminal_exposes_apk_lifecycle_benchmark_and_android_ops(self):
        html = _ios_terminal_html()

        self.assertIn('id="approveApk"', html)
        self.assertIn('id="startLifecycle"', html)
        self.assertIn('id="runBenchmark"', html)
        self.assertIn('value="android.ui_snapshot"', html)
        self.assertIn('value="android.screenshot.request_permission"', html)
        self.assertIn('value="pdd.create_listing"', html)

    def test_ios_control_action_can_run_scheduler_benchmark(self):
        outputs = {
            "json_validity_route_plan": {"route": "tool", "tool_calls": [], "workflow_steps": [], "confidence": 0.9},
            "tool_call_accuracy_browser": {"route": "executor", "tool_calls": [{"name": "browser.open_url"}]},
            "workflow_step_completeness_publish": {"route": "workflow", "workflow_steps": ["intake", "asset_check", "review_gate", "upload_product"]},
            "context_drift_followup": {"route": "agent", "context_retained_ids": ["order-42", "ecom-demo"], "irrelevant_context_ids": []},
        }
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_SCHEDULER_BENCHMARK_HISTORY_PATH": os.path.join(tmp, "benchmarks.jsonl")}, clear=False):
            status, payload = _ios_control_action({"action": "evaluate_scheduler_benchmark", "outputs_by_case_id": outputs})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["scheduler_benchmark_result"]["passed"])
        self.assertEqual(payload["ios_control"]["model_governance"]["scheduler_benchmark"]["status"], "passed")

    def test_mobile_artifact_download_lookup_is_usable_by_ios_endpoint(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_MOBILE_ARTIFACT_ROOT": tmp}, clear=False):
            store = MobileArtifactStore()
            result = store.ingest(
                {
                    "purpose": "ios_work_image",
                    "files": [{"name": "phone.png", "mime_type": "image/png", "content_base64": "cG5n"}],
                },
                source="ios_native_terminal",
                device_id="iphone",
            )
            artifact_id = result["artifacts"][0]["artifact_id"]
            artifact_file = MobileArtifactStore().artifact_file(artifact_id)

        self.assertEqual(artifact_file["filename"], "phone.png")
        self.assertEqual(artifact_file["mime_type"], "image/png")

    def test_ios_compact_model_governance_exposes_roles_and_gate(self):
        compact = _ios_compact_model_governance(
            {
                "local_model_policy": {
                    "hardware": {"hardware_class": "single_gpu_16gb"},
                    "policy": {"default_mode": "single_local_scheduler"},
                    "role_assignments": [
                        {
                            "role_id": "local_scheduler_master",
                            "label": "Local Scheduler",
                            "model_id": "Qwen/Qwen3.6-35B-A3B-Instruct",
                            "quantization_profile": "Q4_K_M",
                            "vram_policy": "sequential",
                        }
                    ],
                    "scheduler_benchmark": {"status": "not_run", "case_count": 4},
                },
                "brain_replacement": {
                    "adapter_registry": {
                        "adapter_count": 1,
                        "adapters": [{"adapter_id": "adapter.current", "label": "Current Brain", "status": "active"}],
                    },
                    "replacement_gate": {"minimum_average_score": 88, "critical_cases_must_pass": True, "auto_replace_allowed": False},
                },
            }
        )

        self.assertEqual(compact["hardware_class"], "single_gpu_16gb")
        self.assertEqual(compact["role_count"], 1)
        self.assertEqual(compact["adapter_count"], 1)
        self.assertEqual(compact["replacement_gate"]["minimum_average_score"], 88)
        self.assertFalse(compact["replacement_gate"]["auto_replace_allowed"])

    def test_ios_compact_modules_include_model_governance(self):
        summary = _ios_compact_module_management(
            {
                "services": {"services": [{"status": "running", "running": True}]},
                "mobile_management": {"android": {"endpoint": {"health": {"ok": True}}}, "ios": {"endpoint": {"health": {"ok": True}}}},
                "workflows": {"overview": {"available_definition_count": 1}},
                "model_governance": {"status": "ready", "summary": "single_gpu_16gb · roles 2 · adapters 3"},
                "safety": {"active": False, "mode": "normal"},
            }
        )

        module_ids = {module["module_id"] for module in summary["modules"]}
        self.assertIn("model_governance", module_ids)


def _flatten_constants(value):
    output = []
    if isinstance(value, tuple):
        for item in value:
            output.extend(_flatten_constants(item))
    else:
        output.append(value)
    return output
