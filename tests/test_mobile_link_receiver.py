import hashlib
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.mobile_link_receiver as receiver
from scripts.mobile_link_receiver import (
    Handler,
    android_apk_file,
    android_apk_manifest,
    extract_pdd_link,
    is_supported_pdd_link,
    worker_package_manifest,
)


class MobileLinkReceiverTests(unittest.TestCase):
    def test_extracts_yangkeduo_web_link(self):
        text = "复制打开 https://mobile.yangkeduo.com/goods2.html?goods_id=123&refer_share_channel=copy_link"

        self.assertEqual(
            extract_pdd_link(text),
            "https://mobile.yangkeduo.com/goods2.html?goods_id=123&refer_share_channel=copy_link",
        )

    def test_extracts_pinduoduo_web_link(self):
        text = "https://mobile.pinduoduo.com/goods.html?goods_id=456"

        self.assertEqual(extract_pdd_link(text), "https://mobile.pinduoduo.com/goods.html?goods_id=456")

    def test_extracts_web_link_when_share_text_contains_unsupported_content(self):
        text = "分享给你 #小程序://拼多多/UhecnYM1HJR3d5i https://mobile.yangkeduo.com/goods.html?ps=fallback"

        self.assertEqual(extract_pdd_link(text), "https://mobile.yangkeduo.com/goods.html?ps=fallback")

    def test_rejects_unrelated_link(self):
        self.assertEqual(extract_pdd_link("https://example.com/goods.html"), "")
        self.assertFalse(is_supported_pdd_link("https://example.com/goods.html"))

    def test_supported_link_requires_clean_extracted_value(self):
        self.assertFalse(is_supported_pdd_link("打开 #小程序://拼多多/UhecnYM1HJR3d5i"))
        self.assertFalse(is_supported_pdd_link("#小程序://拼多多/UhecnYM1HJR3d5i"))

    def test_ios_terminal_contains_management_actions(self):
        from scripts.mobile_link_receiver import ios_terminal_html

        html = ios_terminal_html(
            {
                "artifacts": {"count": 0},
                "android": {"device_count": 0},
                "remote_workers": {"count": 0},
                "workflow_runs": {"count": 0},
            }
        )

        self.assertIn("/mobile/artifacts", html)
        self.assertIn("/ios/control/pairing", html)
        self.assertIn("managementToken", html)
        self.assertIn('/ios/terminal.webmanifest', html)
        self.assertIn('/ios/apple-touch-icon.png', html)
        self.assertIn('/ios/icon.svg', html)
        self.assertIn("serviceWorker", html)
        self.assertIn("/ios/service-worker.js", html)
        self.assertIn("diagnosticRowHtml", html)
        self.assertIn("runDiagnosticAction", html)
        self.assertIn("device_id: payload.device_id", html)
        self.assertIn("management-panel", html)
        self.assertIn("clearIosTerminalHistory", html)
        self.assertIn("createBrowserExtensionPairingForWorkspace", html)
        self.assertIn("生成抓取扩展配对码", html)
        self.assertIn("电商运营 Terminal", html)
        self.assertIn('option value="ecommerce.auto_listing.v1"', html)
        self.assertNotIn('option value="local.cli.run.v1"', html)
        self.assertIn("主控端管理", html)
        self.assertIn("这里只管理主控端本身和主控侧工具", html)
        self.assertLess(html.index('id="controller-management"'), html.index('id="workspace-devices"'))
        self.assertIn("当前主控端", html)
        self.assertIn("iosControllers", html)
        self.assertIn("历史/其他主控端", html)
        self.assertIn("inline-preview", html)
        self.assertIn("previewArtifact(", html)
        self.assertIn("copyApkLink", html)
        self.assertIn("copyApkLink", html)
        self.assertIn("workspacePairingManagementHtml", html)
        self.assertIn("workspaceBindingsHtml", html)
        self.assertIn("workspaceWorkflowRunsHtml", html)
        self.assertIn("workspaceDiagnosticsHtml", html)
        self.assertIn("workspaceArtifactGroupsHtml", html)
        self.assertIn("DETAIL_STATE_STORAGE_KEY", html)
        self.assertIn("rememberDetailState", html)
        self.assertIn("applyDetailState(document)", html)
        self.assertIn("data-detail-key", html)
        self.assertIn("artifactGroupRows", html)
        self.assertIn("startWorkflowForWorkspace", html)
        self.assertIn("clearWorkflowRunsForWorkspace", html)
        self.assertIn("loadActionLogForWorkspace", html)
        self.assertIn("clearBindingHistory", html)
        self.assertIn("cancelPairingToken", html)
        self.assertIn("revokeDeviceBinding", html)
        self.assertIn("cancel_workflow_run", html)
        self.assertIn("retry_workflow_run", html)
        self.assertIn("delete_workflow_run", html)
        self.assertIn("clear_workflow_runs", html)
        self.assertIn("deleteWorkflowRun", html)
        self.assertIn("clearWorkflowRuns", html)
        self.assertIn("validate_state", html)
        self.assertIn("start_workflow_run", html)
        self.assertIn("workflowFieldId", html)
        self.assertIn("local.cli.run.v1", html)
        self.assertIn("langgraph.run.v1", html)
        self.assertIn("crewai.run.v1", html)
        self.assertIn("已结束记录", html)
        self.assertIn("promote_mode", html)
        self.assertIn("runtimeVenv", html)
        self.assertIn("dependencyPolicy", html)
        self.assertIn("update_runtime_profile", html)
        self.assertIn("queue_android_command", html)
        self.assertIn("image.share_to_app", html)
        self.assertIn("android.screenshot.capture", html)
        self.assertIn("android_command_catalog", html)
        self.assertIn("createWorkerPairing", html)
        self.assertIn("createIosTerminalToken", html)
        self.assertIn("pairingExpiresAt", html)
        self.assertIn("pairingTtlMinutes", html)
        self.assertIn("pairingExpiryInputId", html)
        self.assertIn("ios_terminal", html)
        self.assertIn("spiritkin_control_token", html)
        self.assertIn("localStorage.removeItem('spiritkin_management_token')", html)
        self.assertIn("/ios/control/snapshot?terminal_id=", html)
        self.assertIn("remote_worker", html)
        self.assertIn("targetDevice", html)
        self.assertIn("commandMetadata", html)
        self.assertIn('id="workspace-devices"', html)
        self.assertIn('id="account-console"', html)
        self.assertIn("renderAccountConsole", html)
        self.assertIn("loadAccountUsage", html)
        self.assertIn("绑定自己的 Worker", html)
        self.assertIn("工作区设备管理", html)
        self.assertIn("每个工作区独立折叠管理", html)
        self.assertIn('id="avatarFrame"', html)
        self.assertIn("/avatar_3d.html", html)
        self.assertIn('title="SpiritKin 3D Avatar"', html)
        self.assertIn("Android Bridge", html)
        self.assertIn("--fx-canvas: #eef5fa", html)
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn("clearAndroidCommandsForDevice", html)
        self.assertIn("addDeviceWorkflow", html)
        self.assertIn("deleteDeviceWorkflow", html)
        self.assertIn("deletePairingToken", html)
        self.assertIn("clearPairingHistory", html)
        self.assertIn("approvePairingRequestForWorkspace", html)
        self.assertIn("rejectPairingRequestForWorkspace", html)
        self.assertIn("approve_pairing_request", html)
        self.assertIn("reject_pairing_request", html)
        self.assertIn("工作流（", html)
        self.assertIn("工作流运行记录", html)
        self.assertIn("添加到这台设备", html)
        self.assertIn('id="ios-controller-management"', html)
        self.assertIn('id="artifacts-upload"', html)
        self.assertNotIn('id="workflow"', html)
        self.assertNotIn('id="artifact-preview"', html)
        self.assertIn('id="android-command"', html)

    def test_ios_terminal_pwa_assets_are_served_by_control_plane_script(self):
        from scripts.mobile_link_receiver import Handler

        cases = [
            ("/ios/terminal.webmanifest", "application/json; charset=utf-8", '"start_url": "/ios/terminal"'),
            ("/ios/service-worker.js", "application/javascript; charset=utf-8", "CACHE_NAME"),
            ("/ios/icon.svg", "image/svg+xml; charset=utf-8", "<svg"),
            ("/ios/apple-touch-icon.png", "image/svg+xml; charset=utf-8", "<svg"),
        ]

        for path, content_type, expected in cases:
            with self.subTest(path=path):
                handler = object.__new__(Handler)
                handler.path = path
                handler.headers = {}
                handler.client_address = ("127.0.0.1", 12345)
                handler.wfile = BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.store = Mock()

                handler.do_GET()

                handler.send_response.assert_called_once_with(200)
                header_calls = [call.args for call in handler.send_header.call_args_list]
                self.assertIn(("content-type", content_type), header_calls)
                self.assertIn(expected, handler.wfile.getvalue().decode("utf-8"))

    def test_ios_shortcut_schema_is_served_by_primary_terminal(self):
        handler = object.__new__(Handler)
        handler.path = "/ios/schemas/shortcuts.json"
        handler.headers = {}
        handler.client_address = ("127.0.0.1", 12345)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["shortcuts"]), 6)
        self.assertIn("Ask Spirit", {item["name"] for item in payload["shortcuts"]})
        self.assertIn("Check Spirit Status", {item["name"] for item in payload["shortcuts"]})
        self.assertNotIn("Capture Screen", {item["name"] for item in payload["shortcuts"]})
        self.assertTrue(any(item["confirmation_required"] for item in payload["shortcuts"]))

    def test_ios_native_token_header_and_cors_are_supported(self):
        handler = object.__new__(Handler)
        handler.headers = {"X-SpiritKin-iOS-Token": "ios-secret"}
        handler.send_header = Mock()

        self.assertEqual(handler._auth_token({}), "ios-secret")
        handler._cors()

        self.assertIn(
            ("access-control-allow-headers", "content-type, authorization, x-spiritkin-ios-token, x-spiritkin-token, x-spiritkin-workspace, x-spiritkin-workspace-id"),
            [call.args for call in handler.send_header.call_args_list],
        )

    def test_ios_growth_governance_requires_explicit_confirmation(self):
        body = json.dumps(
            {
                "action": "review_candidate",
                "candidate_id": "growth-workflow-test",
                "decision": "approve",
                "reason": "test",
                "evidence": {"source": "test"},
            }
        ).encode("utf-8")
        handler = object.__new__(Handler)
        handler.path = "/ios/growth"
        handler.headers = {"content-length": str(len(body)), "Origin": "http://127.0.0.1:8792"}
        handler.client_address = ("127.0.0.1", 12345)
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = Mock()
        handler._authorized_control_workspace = Mock(return_value="tenant-a")
        handler._is_management_token = Mock(return_value=False)
        handler._control_terminal_binding = Mock(return_value={"device_id": "ios-test"})

        with patch("backend.capability.growth.runtime.handle_growth_action") as growth_action:
            handler.do_POST()

        handler.send_response.assert_called_once_with(403)
        growth_action.assert_not_called()
        response = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("confirmation", response["error"])

    def test_ios_growth_overwrites_client_workspace_and_actor(self):
        body = json.dumps(
            {
                "action": "advance_stage",
                "candidate_id": "growth-workflow-test",
                "workspace_id": "tenant-b",
                "stage": "design",
                "evidence": {"source": "test"},
            }
        ).encode("utf-8")
        handler = object.__new__(Handler)
        handler.path = "/ios/growth"
        handler.headers = {"content-length": str(len(body)), "Origin": "http://127.0.0.1:8792"}
        handler.client_address = ("127.0.0.1", 12345)
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = Mock()
        handler._authorized_control_workspace = Mock(return_value="tenant-a")
        handler._is_management_token = Mock(return_value=False)
        handler._control_terminal_binding = Mock(return_value={"device_id": "ios-test"})

        with patch("backend.capability.growth.runtime.handle_growth_action", return_value={"ok": True, "growth": {}}) as growth_action:
            handler.do_POST()

        handler.send_response.assert_called_once_with(200)
        payload = growth_action.call_args.args[0]
        self.assertEqual(payload["workspace_id"], "tenant-a")
        self.assertEqual(payload["submitted_by"], "ios-terminal:ios-test")
        self.assertFalse(payload["allow_unscoped_governance"])

    def test_ios_growth_injects_actor_for_sandbox_bundle_and_execution(self):
        for action, actor_key in (
            ("prepare_sandbox_bundle", "prepared_by"),
            ("execute_builder_sandbox", "executed_by"),
        ):
            body = json.dumps(
                {
                    "action": action,
                    "candidate_id": "growth-tool-test",
                    "workspace_id": "tenant-b",
                    "confirmed": True,
                    "prepared_by": "spoofed-client",
                    "executed_by": "spoofed-client",
                }
            ).encode("utf-8")
            handler = object.__new__(Handler)
            handler.path = "/ios/growth"
            handler.headers = {"content-length": str(len(body)), "Origin": "http://127.0.0.1:8792"}
            handler.client_address = ("127.0.0.1", 12345)
            handler.rfile = BytesIO(body)
            handler.wfile = BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler.store = Mock()
            handler._authorized_control_workspace = Mock(return_value="tenant-a")
            handler._is_management_token = Mock(return_value=False)
            handler._control_terminal_binding = Mock(return_value={"device_id": "ios-sandbox"})

            with patch(
                "backend.capability.growth.runtime.handle_growth_action", return_value={"ok": True, "growth": {}}
            ) as growth_action:
                handler.do_POST()

            handler.send_response.assert_called_once_with(200)
            payload = growth_action.call_args.args[0]
            self.assertEqual(payload["workspace_id"], "tenant-a")
            self.assertEqual(payload[actor_key], "ios-terminal:ios-sandbox")
            self.assertNotEqual(payload[actor_key], "spoofed-client")

    def test_primary_terminal_shortcut_route_returns_runtime_reply(self):
        body = json.dumps(
            {
                "shortcut_name": "Ask Spirit",
                "action": "ask_spirit",
                "input_text": "检查当前状态",
                "workspace_id": "tenant-a",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        handler = object.__new__(Handler)
        handler.path = "/ios/shortcut"
        handler.headers = {"content-length": str(len(body))}
        handler.client_address = ("127.0.0.1", 12345)
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._authorized_control_workspace = Mock(return_value="tenant-a")
        runtime = Mock()
        runtime.handle_input.return_value = types.SimpleNamespace(text="当前状态正常。", emotion="neutral")

        with (
            patch.object(Handler, "ios_runtime", runtime),
            patch("backend.app.runtime.SpiritKinRuntime.build_output_payload", return_value={"text": "当前状态正常。"}),
        ):
            handler.do_POST()

        handler.send_response.assert_called_once_with(200)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(payload["shortcut_output"]["result"], "当前状态正常。")
        request = runtime.handle_input.call_args.args[0]
        self.assertEqual(request.channel, "ios")
        self.assertEqual(request.metadata["workspace_id"], "tenant-a")

    def test_primary_terminal_shortcut_uses_direct_path_for_pure_chat(self):
        body = json.dumps(
            {
                "shortcut_name": "Ask Spirit",
                "action": "ask_spirit",
                "input_text": "你好",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        handler = object.__new__(Handler)
        handler.path = "/ios/shortcut"
        handler.headers = {"content-length": str(len(body))}
        handler.client_address = ("127.0.0.1", 12345)
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._authorized_control_workspace = Mock(return_value="local-ecommerce")

        reply = types.SimpleNamespace(
            text="你好，我在。",
            emotion="neutral",
            metadata={},
        )
        with (
            patch("backend.mobile.ios_conversation.handle_ios_direct_chat", return_value=reply),
            patch("backend.app.runtime.SpiritKinRuntime.build_output_payload", return_value={"text": reply.text}),
            patch.object(Handler, "_ios_runtime") as runtime,
        ):
            handler.do_POST()

        handler.send_response.assert_called_once_with(200)
        runtime.assert_not_called()
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(payload["shortcut_output"]["result"], "你好，我在。")

    def test_native_ios_persists_outgoing_turn_before_ask_spirit(self):
        store = Path("ios/SpiritKinTerminal/Sources/Store/TerminalStore.swift").read_text(encoding="utf-8")
        send_start = store.index("func sendConversationMessage() async")
        send_end = store.index("func refreshConversations", send_start)
        send_body = store[send_start:send_end]

        self.assertLess(send_body.index("await persistConversations()"), send_body.index("api.askSpirit"))

    def test_primary_terminal_shortcut_timeout_is_retryable(self):
        body = json.dumps(
            {
                "shortcut_name": "Ask Spirit",
                "action": "ask_spirit",
                "input_text": "你好",
                "metadata": {"full_runtime": True},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        handler = object.__new__(Handler)
        handler.path = "/ios/shortcut"
        handler.headers = {"content-length": str(len(body))}
        handler.client_address = ("127.0.0.1", 12345)
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._authorized_control_workspace = Mock(return_value="local-ecommerce")
        runtime = Mock()
        runtime.handle_input.side_effect = RuntimeError("API error: timed out")

        with patch.object(Handler, "ios_runtime", runtime):
            handler.do_POST()

        handler.send_response.assert_called_once_with(503)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(payload["error"], "model_timeout")
        self.assertTrue(payload["retryable"])

    def test_control_home_collects_primary_product_entrypoints(self):
        from scripts.mobile_link_receiver import control_home_html

        html = control_home_html(
            {
                "artifacts": {"count": 2},
                "android": {"device_count": 1},
                "remote_workers": {"count": 1},
                "workflow_runs": {"count": 3},
                "pairings": {"bound_count": 1},
            }
        )

        self.assertIn("SpiritKin Control", html)
        self.assertIn("统一入口", html)
        self.assertIn("/ios/control#workspace-devices", html)
        self.assertIn("/ios/control#artifacts-upload", html)
        self.assertIn("/ios/control#workflow", html)
        self.assertIn("/ios/control#android-command", html)
        self.assertIn("/android/apk/manifest", html)
        self.assertIn("/android/apk", html)
        self.assertIn("/worker/package/manifest", html)
        self.assertIn("/worker/package", html)

    def test_control_home_get_renders_bootstrap_when_management_token_is_missing(self):
        handler = object.__new__(Handler)
        handler.path = "/control"
        handler.headers = {}
        handler.client_address = ("127.0.0.1", 12345)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = Mock()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        payload = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("SpiritKin Control", payload)
        self.assertIn("需要 Management Token", payload)

    def test_ios_terminal_bootstrap_snapshot_requires_no_store_data(self):
        from scripts.mobile_link_receiver import ios_terminal_bootstrap_snapshot, ios_terminal_html

        snapshot = ios_terminal_bootstrap_snapshot(auth_required=True)
        html = ios_terminal_html(snapshot)

        self.assertTrue(snapshot["auth_required"])
        self.assertIn("const initialSnapshot", html)
        self.assertIn('"auth_required": true', html)

    def test_android_bridge_sources_expose_material_management_ui(self):
        main = Path("mobile-link-bridge/src/com/spiritkin/mobilelinkbridge/MainActivity.java").read_text(encoding="utf-8")
        artifact = Path("mobile-link-bridge/src/com/spiritkin/mobilelinkbridge/ArtifactManager.java").read_text(encoding="utf-8")
        links = Path("mobile-link-bridge/src/com/spiritkin/mobilelinkbridge/LinkManager.java").read_text(encoding="utf-8")
        modules = Path("mobile-link-bridge/src/com/spiritkin/mobilelinkbridge/BridgeModuleRegistry.java").read_text(encoding="utf-8")

        self.assertIn("ImageView", main)
        self.assertIn("删除第 ", main)
        self.assertNotIn("高级：复制图片组编号", main)
        self.assertIn("自动化上架工作流", main)
        self.assertIn("通用手机端能力", main)
        self.assertIn("验收与排查", main)
        self.assertIn('navigationButton("状态"', main)
        self.assertIn('navigationButton("工作流"', main)
        self.assertIn('navigationButton("连接"', main)
        self.assertIn("selectNavigationPage", main)
        self.assertIn("button.setMinHeight(dp(48))", main)
        self.assertNotIn("avatar_3d", main.lower())
        self.assertNotIn("AvatarView", main)
        self.assertIn("spacedWrap", main)
        self.assertIn("刷新云端链接", main)
        self.assertIn("删除链接", main)
        self.assertIn("fetchPreview", artifact)
        self.assertIn("/links?format=lines", links)
        self.assertIn("/links/delete", links)
        self.assertIn("商品图片", modules)
        self.assertIn("商品链接", modules)

    def test_ios_native_app_keeps_terminal_capabilities_in_adaptive_navigation(self):
        root = Path("ios/SpiritKinTerminal/Sources/Views/RootView.swift").read_text(encoding="utf-8")
        dashboard = Path("ios/SpiritKinTerminal/Sources/Views/DashboardView.swift").read_text(encoding="utf-8")
        avatar = Path("ios/SpiritKinTerminal/Sources/Views/AvatarStageView.swift").read_text(encoding="utf-8")
        conversation = Path("ios/SpiritKinTerminal/Sources/Views/ConversationPanel.swift").read_text(encoding="utf-8")
        profile = Path("ios/SpiritKinTerminal/Sources/Views/ProfileHubView.swift").read_text(encoding="utf-8")
        automation = Path("ios/SpiritKinTerminal/Sources/Views/IOSAutomationView.swift").read_text(encoding="utf-8")
        theme = Path("ios/SpiritKinTerminal/Sources/Support/Theme.swift").read_text(encoding="utf-8")
        store = Path("ios/SpiritKinTerminal/Sources/Store/TerminalStore.swift").read_text(encoding="utf-8")
        api = Path("ios/SpiritKinTerminal/Sources/Networking/TerminalAPI.swift").read_text(encoding="utf-8")
        resources = Path("ios/SpiritKinTerminal/Sources/Views/ResourceManagementView.swift").read_text(encoding="utf-8")
        monitor = Path("ios/SpiritKinTerminal/Sources/Views/RuntimeMonitorView.swift").read_text(encoding="utf-8")
        pools = Path("ios/SpiritKinTerminal/Sources/Views/CapabilityPoolsView.swift").read_text(encoding="utf-8")
        info = Path("ios/SpiritKinTerminal/Sources/Info.plist").read_text(encoding="utf-8")

        self.assertIn("NavigationSplitView", root)
        self.assertIn("TabView(selection:", root)
        self.assertIn('case .conversation: return "message"', root)
        self.assertIn('case .profile: return "person.crop.circle"', root)
        self.assertNotIn("case artifacts", root)
        self.assertNotIn("case settings", root)
        self.assertIn("DashboardView(isActive:", root)
        self.assertIn("ProfileHubView()", root)
        self.assertIn("AvatarStageView(url: store.avatarURL, isActive:", dashboard)
        self.assertIn("ConversationPanel()", dashboard)
        self.assertIn("sendConversationMessage", conversation)
        self.assertIn("ArtifactsView()", profile)
        self.assertIn("SettingsView()", profile)
        self.assertIn("IOSAutomationView()", profile)
        self.assertIn("Ask Spirit", automation)
        self.assertIn("enum SpiritKinAppearance", theme)
        self.assertIn("WKWebView", avatar)
        self.assertIn("final class Coordinator", avatar)
        self.assertIn("lastPaused", avatar)
        self.assertIn("if isActive", avatar)
        self.assertIn("private func load(_ url: URL", avatar)
        self.assertIn("accessibilityLabel(\"SpiritKin 3D Avatar 主控舞台\")", avatar)
        self.assertIn("var avatarURL: URL?", store)
        self.assertIn("var profileAvatarURL: URL?", store)
        self.assertIn("components.queryItems = [", store)
        self.assertIn('URLQueryItem(name: "embed", value: "1")', store)
        self.assertIn('endpoint("ios/native/snapshot")', api)
        self.assertIn('endpoint("ios/native/action")', api)
        self.assertIn('endpoint("ios/shortcut")', api)
        self.assertIn('endpoint("ios/sessions")', api)
        self.assertIn('endpoint("ios/control/pair")', api)
        self.assertIn('endpoint("ios/resources")', api)
        self.assertIn('endpoint("ios/monitor")', api)
        self.assertIn('endpoint("ios/growth")', api)
        self.assertIn("func growthAction", api)
        self.assertIn('status == "failed"', api)
        self.assertIn('job["error"]', api)
        self.assertIn("SpiritSubmission", api)
        self.assertIn("func spiritJobStatus", api)
        self.assertNotIn("for _ in 0..<120", api)
        self.assertIn("async let nextSnapshot", store)
        self.assertIn("refreshThrottleInterval", store)
        self.assertIn("endpointFreshUntil", store)
        self.assertIn("beginEndpointRefresh", store)
        self.assertIn("sessionsRefreshInFlight", store)
        self.assertIn("domainsRefreshInFlight", store)
        self.assertIn("continueSpiritJob", store)
        self.assertIn("conversationJobTasks", store)
        self.assertIn("bindPairingToken", store)
        self.assertIn("pairingToken", Path("ios/SpiritKinTerminal/Sources/Models/TerminalConfig.swift").read_text(encoding="utf-8"))
        self.assertIn("resourceAction", resources)
        self.assertIn("monitorAction", monitor)
        self.assertIn("confirmationDialog", monitor)
        self.assertIn("GrowthCandidateSheet", pools)
        self.assertIn('"confirmed": .bool(true)', pools)
        self.assertIn("verify_builder_artifact", pools)
        self.assertIn("运行静态沙箱预检", pools)
        self.assertIn("review_candidate", pools)
        self.assertIn("register_candidate", pools)
        self.assertIn("escalate_candidate", pools)
        self.assertIn("research_candidate", pools)
        self.assertIn("研究公开仓库元数据", pools)
        self.assertIn("escalationTargets", pools)
        self.assertIn("parent_candidate_id", pools)
        self.assertIn("ConversationSessionsView", Path("ios/SpiritKinTerminal/Sources/Views/ConversationSessionsView.swift").read_text(encoding="utf-8"))
        self.assertIn("EcommerceTerminalView", Path("ios/SpiritKinTerminal/Sources/Views/EcommerceTerminalView.swift").read_text(encoding="utf-8"))
        self.assertIn("WorkflowDomain", Path("ios/SpiritKinTerminal/Sources/Models/TerminalModels.swift").read_text(encoding="utf-8"))
        self.assertIn("sessionID", store)
        self.assertIn("activeConversationTitle", store)
        self.assertIn("<string>SpiritKin</string>", info)
        self.assertIn("<string>spiritkin-terminal</string>", info)
        self.assertIn("<string>spiritkin</string>", info)

    def test_ios_controller_prototype_groups_domains_and_manages_sessions(self):
        html = Path("frontend/ios_controller_prototype.html").read_text(encoding="utf-8")
        self.assertIn("电商运营 Terminal", html)
        self.assertIn("verify_builder_artifact", html)
        self.assertIn("沙箱预检", html)
        self.assertIn("startSpiritJobPoll", html)
        self.assertIn("sessionPersistFlight", html)
        self.assertNotIn("for (let attempt = 0; attempt < 120", html)
        self.assertIn("CONTROL_PLANE_URL", html)
        self.assertIn("window.location.hostname", html)
        self.assertIn("the authenticated 8791 receiver", html)
        self.assertIn("requestedWorkspaceID", html)
        self.assertIn("localStorage.setItem(WORKSPACE_STORAGE_KEY", html)
        self.assertIn("/ios/sessions", html)
        self.assertIn("deleted_session_ids", html)
        self.assertIn("sessionDialog", html)
        self.assertIn("板块", html)
        self.assertIn("syncFreshUntil", html)
        self.assertIn("DEFAULT_SYNC_TTL_MS = 8000", html)
        self.assertIn("function avatarPhotoURL()", html)
        self.assertIn("A photo is a profile identity", html)
        self.assertIn("growthDialog", html)
        self.assertIn("confirmed: true", html)
        self.assertIn("advance_stage", html)
        self.assertIn("register_candidate", html)
        self.assertIn("submitGrowthEscalation", html)
        self.assertIn("submitGrowthRemoteResearch", html)
        self.assertIn("research_candidate", html)
        self.assertIn('id="growthResearchKeywordsInput"', html)
        self.assertIn('const keywords = document.getElementById("growthResearchKeywordsInput").value.trim()', html)
        self.assertIn("submitGrowthSandboxBundle", html)
        self.assertIn("execute_builder_sandbox", html)
        self.assertIn('execution_ack: "run_untrusted_code_in_isolated_container"', html)
        self.assertIn("candidate_execution_enabled", html)
        self.assertIn("escalation_targets", html)
        self.assertIn("parent_candidate_id", html)
        self.assertIn("right.workspace_id === currentWorkspaceID()", html)
        self.assertIn("human_required_count", html)
        self.assertIn("const candidates = Array.isArray(growth.candidates)", html)
        self.assertIn("candidate.current_stage", html)
        self.assertIn("invalidateSync", html)
        self.assertIn('data-src="avatar_3d.html', html)
        self.assertNotIn("preview=1", html)
        avatar_html = Path("frontend/avatar_3d.html").read_text(encoding="utf-8")
        self.assertIn("body.embed .hud,body.embed .stage-note{display:none}", avatar_html)
        self.assertIn("scheduleAvatarFrameLoad", html)
        self.assertIn("loading=\"lazy\"", html)
        self.assertIn("job.status === \"failed\"", html)

    def test_artifact_download_url_is_absolute_for_android_commands(self):
        handler = object.__new__(Handler)
        handler.headers = {"host": "control.test:8791"}
        handler.server = Mock()
        handler.server.server_address = ("127.0.0.1", 8791)

        params = handler._with_artifact_download_url({"artifact_id": "art_123"})

        self.assertEqual(params["download_url"], "http://control.test:8791/android/artifact/art_123?file_index=0")
        indexed = handler._with_artifact_download_url({"artifact_id": "art_123", "file_index": 2})
        self.assertEqual(indexed["download_url"], "http://control.test:8791/android/artifact/art_123?file_index=2")

    def test_artifact_response_adds_download_url(self):
        handler = object.__new__(Handler)
        handler.headers = {"host": "control.test:8791"}
        handler.server = Mock()
        handler.server.server_address = ("127.0.0.1", 8791)

        artifact = handler._with_artifact_urls({"artifact_id": "art_123", "workspace_id": "tenant-a"})

        self.assertEqual(artifact["download_url"], "http://control.test:8791/android/artifact/art_123?file_index=0")

    def test_android_artifacts_lines_are_tab_separated(self):
        handler = object.__new__(Handler)
        result = {
            "artifacts": [
                {
                    "artifact_id": "art_123",
                    "created_at": "2026-06-23T01:02:03+00:00",
                    "purpose": "android_shared_image",
                    "files": [
                        {"file_index": 0, "name": "one.jpg", "mime_type": "image/jpeg", "size_bytes": 3},
                        {"file_index": 1, "name": "two.jpg", "mime_type": "image/jpeg", "size_bytes": 4},
                    ],
                }
            ]
        }

        lines = handler._android_artifacts_lines(result)

        self.assertIn("# artifact_id\tfile_index", lines)
        self.assertIn("art_123\t0\tone.jpg\timage/jpeg\t3\t2026-06-23T01:02:03+00:00\tandroid_shared_image", lines)
        self.assertIn("art_123\t1\ttwo.jpg\timage/jpeg\t4\t2026-06-23T01:02:03+00:00\tandroid_shared_image", lines)

    def test_android_links_lines_are_tab_separated(self):
        handler = object.__new__(Handler)
        result = {
            "links": [
                {
                    "link_id": "mlink_123",
                    "link": "https://mobile.yangkeduo.com/goods.html?goods_id=1",
                    "received_at": "2026-06-23T01:02:03+00:00",
                    "source": "android-bridge",
                }
            ]
        }

        lines = handler._android_links_lines(result)

        self.assertIn("# link_id\tlink", lines)
        self.assertIn(
            "mlink_123\thttps://mobile.yangkeduo.com/goods.html?goods_id=1\t2026-06-23T01:02:03+00:00\tandroid-bridge",
            lines,
        )

    def test_pairing_response_contains_deep_link_and_qr(self):
        handler = object.__new__(Handler)
        handler.headers = {"host": "control.test:8791"}
        handler.server = Mock()
        handler.server.server_address = ("127.0.0.1", 8791)
        qrcode_stub = types.SimpleNamespace()
        qrcode_stub.make = Mock(return_value=Mock(save=lambda out, format: out.write(b"png-bytes")))

        with patch.dict(sys.modules, {"qrcode": qrcode_stub}):
            response = handler._pairing_response(
                {
                    "token_id": "pair_1",
                    "workspace_id": "tenant-a",
                    "device_role": "android_bridge",
                    "token": "secret-token",
                    "expires_at": "2026-06-14T00:00:00+00:00",
                }
            )

        self.assertEqual(response["server_url"], "http://control.test:8791/android/link")
        self.assertIn("spiritkin://pair?", response["deep_link"])
        self.assertIn("pairing_token=secret-token", response["deep_link"])
        self.assertTrue(response["qr_png_data_url"].startswith("data:image/png;base64,"))
        qrcode_stub.make.assert_called_once_with(response["deep_link"])

    def test_worker_pairing_response_contains_worker_command(self):
        handler = object.__new__(Handler)
        handler.headers = {"host": "control.test:8791"}
        handler.server = Mock()
        handler.server.server_address = ("127.0.0.1", 8791)

        response = handler._pairing_response(
            {
                "token_id": "pair_1",
                "workspace_id": "tenant-a",
                "device_role": "remote_worker",
                "token": "secret-token",
                "expires_at": "2026-06-14T00:00:00+00:00",
            }
        )

        self.assertEqual(response["server_url"], "http://control.test:8791")
        self.assertEqual(response["device_role"], "remote_worker")
        self.assertEqual(response["package_manifest_url"], "http://control.test:8791/worker/package/manifest")
        self.assertEqual(response["package_download_url"], "http://control.test:8791/worker/package")
        self.assertIn("setup-worker.ps1", response["setup_command"])
        self.assertIn("-PairingToken secret-token", response["setup_command"])
        self.assertIn("install-worker-gui.ps1", response["gui_install_command"])
        self.assertIn("-PairingToken secret-token", response["gui_install_command"])
        self.assertIn("--pairing-token secret-token", response["pairing_command"])
        self.assertIn("--workspace-id tenant-a", response["pairing_command"])

    def test_worker_package_manifest_builds_zip_and_download_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_dir = Path(tmp) / "worker-releases"
            worker_executable = Path(tmp) / "spiritkin-control-plane-worker.exe"
            worker_executable.write_bytes(b"MZ-test-worker")
            with patch.object(receiver, "WORKER_PACKAGE_DIR", package_dir), patch.object(
                receiver, "WORKER_EXECUTABLE_PATH", worker_executable
            ), patch.dict(
                receiver.os.environ,
                {"SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET": "secret"},
            ):
                manifest = worker_package_manifest("http://control.test:8791")

            package_path = package_dir / receiver.WORKER_PACKAGE_NAME

            self.assertTrue(package_path.exists())
            self.assertEqual(manifest["download_url"], "http://control.test:8791/worker/package")
            self.assertEqual(manifest["download_file"], receiver.WORKER_PACKAGE_NAME)
            self.assertEqual(manifest["package_format"], "zip")
            self.assertRegex(str(manifest["sha256"]), r"^[a-f0-9]{64}$")
            self.assertEqual(manifest["size_bytes"], package_path.stat().st_size)
            self.assertEqual(manifest["package_integrity"]["sha256"], manifest["sha256"])
            self.assertEqual(manifest["serving_validation"]["status"], "ok")
            self.assertEqual(manifest["signature"]["algorithm"], "hmac-sha256")
            self.assertTrue(str(manifest["entrypoint"]).startswith("spiritkin-control-plane-worker.exe"))
            with zipfile.ZipFile(package_path) as archive:
                self.assertEqual(archive.read("spiritkin-control-plane-worker.exe"), b"MZ-test-worker")

    def test_worker_package_get_serves_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_dir = Path(tmp) / "worker-releases"
            handler = object.__new__(Handler)
            handler.path = "/worker/package"
            handler.headers = {}
            handler.wfile = BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch.object(receiver, "WORKER_PACKAGE_DIR", package_dir), patch.object(
                receiver, "WORKER_EXECUTABLE_PATH", Path(tmp) / "missing-worker.exe"
            ):
                handler.do_GET()

            payload = handler.wfile.getvalue()
            self.assertGreater(len(payload), 0)
            self.assertEqual(payload[:2], b"PK")
            handler.send_response.assert_called_once_with(200)
            header_calls = [call.args for call in handler.send_header.call_args_list]
            self.assertIn(("content-type", "application/zip"), header_calls)

    def test_worker_package_head_has_no_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_dir = Path(tmp) / "worker-releases"
            handler = object.__new__(Handler)
            handler.path = "/worker/package"
            handler.headers = {}
            handler.wfile = BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch.object(receiver, "WORKER_PACKAGE_DIR", package_dir), patch.object(
                receiver, "WORKER_EXECUTABLE_PATH", Path(tmp) / "missing-worker.exe"
            ):
                handler.do_HEAD()

            self.assertEqual(handler.wfile.getvalue(), b"")
            handler.send_response.assert_called_once_with(200)

    def test_pairing_html_contains_manual_fields(self):
        from scripts.mobile_link_receiver import pairing_html

        html = pairing_html(
            {
                "workspace_id": "tenant-a",
                "server_url": "http://control.test:8791/android/link",
                "pairing_token": "secret-token",
                "deep_link": "spiritkin://pair?pairing_token=secret-token",
                "expires_at": "2026-06-14T00:00:00+00:00",
                "qr_png_data_url": "data:image/png;base64,abc",
            }
        )

        self.assertIn("Android 手机端配对", html)
        self.assertIn("http://control.test:8791/android/link", html)
        self.assertIn("secret-token", html)
        self.assertIn("工作区", html)

    def test_pairing_endpoint_requires_management_token_when_configured(self):
        handler = object.__new__(Handler)
        handler.headers = {}

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            with self.assertRaises(PermissionError):
                handler._authorize_pairing_endpoint("/pairing")

        handler.headers = {"authorization": "Bearer owner-secret"}
        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            handler._authorize_pairing_endpoint("/pairing")

    def test_pairing_endpoint_is_open_for_local_dev_without_management_token(self):
        handler = object.__new__(Handler)
        handler.headers = {}

        with patch.dict(os.environ, {}, clear=True):
            handler._authorize_pairing_endpoint("/pairing")

    def test_production_mode_requires_management_token_for_pairing(self):
        handler = object.__new__(Handler)
        handler.headers = {}

        with patch.dict(os.environ, {"SPIRITKIN_PRODUCTION_MODE": "1"}, clear=True):
            with self.assertRaises(PermissionError):
                handler._authorize_pairing_endpoint("/pairing")

        handler.headers = {"authorization": "Bearer owner-secret"}
        with patch.dict(
            os.environ,
            {"SPIRITKIN_PRODUCTION_MODE": "1", "SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"},
            clear=True,
        ):
            handler._authorize_pairing_endpoint("/pairing")

    def test_ios_pairing_endpoint_returns_json_401_without_management_token(self):
        handler = object.__new__(Handler)
        handler.path = "/ios/control/pairing?workspace_id=tenant-a"
        handler.headers = {}
        handler.client_address = ("127.0.0.1", 12345)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = Mock()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            handler.do_GET()

        handler.send_response.assert_called_once_with(401)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "management or iOS terminal token required")

    def test_android_apk_manifest_contains_download_url_and_version(self):
        manifest = android_apk_manifest("http://control.test:8791")

        self.assertEqual(manifest["manifest_version"], 2)
        self.assertEqual(manifest["app_id"], "com.spiritkin.mobilelinkbridge")
        self.assertEqual(manifest["package_name"], "com.spiritkin.mobilelinkbridge")
        self.assertGreaterEqual(manifest["version_code"], 2026061503)
        self.assertRegex(str(manifest["version_name"]), r"^2026\.06\.\d{2}\.\d+$")
        self.assertEqual(manifest["download_url"], "http://control.test:8791/android/apk")
        self.assertRegex(str(manifest["sha256"]), r"^[a-f0-9]{64}$")
        self.assertGreater(manifest["size_bytes"], 0)
        self.assertEqual(manifest["compatibility"]["min_sdk"], 23)
        self.assertEqual(manifest["integrity"]["algorithm"], "sha256")
        self.assertEqual(manifest["integrity"]["sha256"], manifest["sha256"])
        self.assertTrue(manifest["integrity"]["same_package_signature_required"])
        self.assertTrue(manifest["rollback"]["supported"])
        self.assertEqual(manifest["serving_validation"]["status"], "ok")

    def test_android_apk_manifest_prefers_build_release_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            apk = out_dir / "mobile-link-bridge.apk"
            apk.write_bytes(b"test-apk")
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            release_manifest = out_dir / "release-manifest.json"
            release_manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "package_name": "com.spiritkin.mobilelinkbridge",
                        "version_code": 2026061502,
                        "version_name": "2026.06.15.2",
                        "download_file": "mobile-link-bridge.apk",
                        "download_url": "",
                        "sha256": digest,
                        "size_bytes": apk.stat().st_size,
                        "updated_at": "2026-06-15T00:00:00+00:00",
                        "rollback": {
                            "supported": True,
                            "strategy": "test",
                            "previous_versions": [
                                {
                                    "version_code": 2026061501,
                                    "sha256": "b" * 64,
                                    "archive_file": "releases/mobile-link-bridge-2026.06.15.1.apk",
                                }
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(receiver, "APK_RELEASE_MANIFEST", release_manifest), patch.object(receiver, "APK_PATH", apk):
                manifest = receiver.android_apk_manifest("http://control.test:8791")

        self.assertEqual(manifest["version_code"], 2026061502)
        self.assertEqual(manifest["download_url"], "http://control.test:8791/android/apk")
        self.assertEqual(manifest["rollback"]["previous_versions"][0]["version_code"], 2026061501)
        self.assertEqual(
            manifest["rollback"]["previous_versions"][0]["download_url"],
            "http://control.test:8791/android/apk?version_code=2026061501",
        )
        self.assertEqual(manifest["serving_validation"]["status"], "ok")

    def test_android_apk_manifest_reports_serving_validation_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            apk = out_dir / "mobile-link-bridge.apk"
            apk.write_bytes(b"actual-apk")
            release_manifest = out_dir / "release-manifest.json"
            release_manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "package_name": "com.spiritkin.mobilelinkbridge",
                        "version_code": 2026061502,
                        "version_name": "2026.06.15.2",
                        "download_file": "mobile-link-bridge.apk",
                        "sha256": "a" * 64,
                        "size_bytes": apk.stat().st_size,
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(receiver, "APK_RELEASE_MANIFEST", release_manifest), patch.object(receiver, "APK_PATH", apk):
                manifest = receiver.android_apk_manifest("http://control.test:8791")

        self.assertEqual(manifest["serving_validation"]["status"], "sha256_mismatch")
        self.assertEqual(manifest["serving_validation"]["expected_sha256"], "a" * 64)
        self.assertRegex(str(manifest["serving_validation"]["actual_sha256"]), r"^[a-f0-9]{64}$")

    def test_android_apk_file_resolves_archived_release_by_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            releases = out_dir / "releases"
            releases.mkdir(parents=True)
            latest = out_dir / "mobile-link-bridge.apk"
            archived = releases / "mobile-link-bridge-2026.06.15.1.apk"
            latest.write_bytes(b"latest")
            archived.write_bytes(b"archived")
            history = out_dir / "release-history.json"
            history.write_text(
                json.dumps(
                    {
                        "releases": [
                            {
                                "version_code": 2026061501,
                                "file_name": "mobile-link-bridge.apk",
                                "archive_file": "releases/mobile-link-bridge-2026.06.15.1.apk",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(receiver, "APK_PATH", latest), patch.object(
                receiver, "APK_RELEASE_DIR", releases
            ), patch.object(receiver, "APK_RELEASE_HISTORY", history), patch.object(
                receiver, "APK_RELEASE_MANIFEST", out_dir / "missing-release-manifest.json"
            ):
                latest_result = android_apk_file()
                archived_result = android_apk_file("2026061501")
                with self.assertRaises(KeyError):
                    android_apk_file("2026061599")

        self.assertEqual(latest_result["path"], latest)
        self.assertEqual(archived_result["path"], archived.resolve())
        self.assertEqual(archived_result["filename"], archived.name)

    def test_android_auth_can_require_pairing_token(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return None

        handler = object.__new__(Handler)
        handler.headers = {}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_REQUIRE_PAIRING_TOKEN": "1"}):
            with self.assertRaises(PermissionError):
                handler._authorize_android({})

    def test_worker_auth_can_require_pairing_token(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return None

        handler = object.__new__(Handler)
        handler.headers = {}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_REQUIRE_WORKER_TOKEN": "1"}):
            with self.assertRaises(PermissionError):
                handler._authorize_worker({})

    def test_production_mode_requires_android_and_worker_tokens_by_default(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return None

        handler = object.__new__(Handler)
        handler.headers = {}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_PRODUCTION_MODE": "1"}, clear=True):
            with self.assertRaises(PermissionError):
                handler._authorize_android({})
            with self.assertRaises(PermissionError):
                handler._authorize_worker({})

    def test_production_mode_requires_management_token_for_control_without_token(self):
        handler = object.__new__(Handler)
        handler.headers = {}
        handler.store = Mock()

        with patch.dict(os.environ, {"SPIRITKIN_PRODUCTION_MODE": "production"}, clear=True):
            with self.assertRaises(PermissionError):
                handler._authorize_management()
            with self.assertRaises(PermissionError):
                handler._authorized_control_workspace("tenant-a")
            with self.assertRaises(PermissionError):
                handler._authorized_android_command_workspace({"workspace_id": "tenant-a"})

    def test_worker_binding_overwrites_workspace_and_worker_id(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                self.required_role = required_role
                return {
                    "worker_id": "worker-bound",
                    "device_id": "worker-bound",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer worker-token"}
        handler.store = FakeStore()

        payload = handler._with_worker_binding({"workspace_id": "tenant-b", "worker_id": "spoofed"})

        self.assertEqual(payload["workspace_id"], "tenant-a")
        self.assertEqual(payload["worker_id"], "worker-bound")
        self.assertEqual(payload["token"], "worker-token")
        self.assertEqual(handler.store.required_role, "remote_worker")

    def test_ios_terminal_token_scopes_control_actions(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                self.required_role = required_role
                return {
                    "terminal_id": "ios-1",
                    "device_id": "ios-1",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer terminal-token"}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            payload = handler._authorized_control_action_payload(
                {"action": "start_workflow_run", "workspace_id": "tenant-a"}
            )
            with self.assertRaises(PermissionError):
                handler._authorized_control_action_payload({"action": "start_workflow_run", "workspace_id": "tenant-b"})

        self.assertEqual(payload["workspace_id"], "tenant-a")
        self.assertEqual(payload["requested_by"], "ios-1")
        self.assertEqual(payload["actor_role"], "ios_terminal")
        self.assertEqual(handler.store.required_role, "ios_terminal")

    def test_account_console_cannot_access_owner_only_surfaces(self):
        handler = object.__new__(Handler)
        with patch.object(handler, "_is_account_console_token", return_value=True):
            with patch.object(handler, "_authorized_control_workspace", return_value="tenant-a") as authorized:
                with self.assertRaisesRegex(PermissionError, "owner controller"):
                    handler._authorized_owner_workspace("tenant-a")
        authorized.assert_not_called()

    def test_ios_owner_heartbeat_uses_bound_terminal_identity(self):
        handler = object.__new__(Handler)
        handler.client_address = ("127.0.0.1", 50000)
        handler.store = Mock()
        handler.store.register_ios_terminal.return_value = {"terminal_id": "ios-bound", "status": "online"}
        handler._authorized_owner_workspace = Mock(return_value="tenant-a")
        handler._control_terminal_binding = Mock(
            return_value={"terminal_id": "ios-bound", "workspace_id": "tenant-a", "device_role": "ios_terminal"}
        )
        handler._send_json = Mock()
        handler.path = "/ios/heartbeat"
        body = b'{"terminal_id":"spoofed","workspace_id":"tenant-a"}'
        handler.headers = {"content-length": str(len(body))}
        handler.rfile = BytesIO(body)

        handler.do_POST()

        handler.store.register_ios_terminal.assert_called_once_with(
            "ios-bound", "127.0.0.1", workspace_id="tenant-a"
        )
        handler._send_json.assert_called_once()

    def test_account_console_token_scopes_control_actions_to_account_workspaces(self):
        class FakeStore:
            def __init__(self):
                self.required_role = ""

            def authenticate_token(self, token, *, required_role=""):
                self.required_role = required_role
                if required_role != "account_console":
                    return None
                return {
                    "console_id": "console-1",
                    "device_id": "console-1",
                    "account_id": "acct-a",
                    "device_role": "account_console",
                    "status": "active",
                }

            def snapshot(self, workspace_id=None, *, account_id=None):
                self.snapshot_workspace_id = workspace_id
                self.snapshot_account_id = account_id
                return {
                    "accounts": {
                        "items": [
                            {"account_id": "acct-a", "workspace_ids": ["tenant-a"]},
                            {"account_id": "acct-b", "workspace_ids": ["tenant-b"]},
                        ]
                    }
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer account-token"}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            payload = handler._authorized_control_action_payload(
                {"action": "get_account_usage", "workspace_id": "tenant-a"}
            )
            with self.assertRaises(PermissionError):
                handler._authorized_control_action_payload({"action": "get_account_usage", "workspace_id": "tenant-b"})
            with self.assertRaises(PermissionError):
                handler._authorized_control_action_payload({"action": "update_account_plan", "workspace_id": "tenant-a"})
            with self.assertRaises(PermissionError):
                handler._authorized_control_action_payload({"action": "workflow.graph.start_run", "workspace_id": "tenant-a"})

        self.assertEqual(payload["workspace_id"], "tenant-a")
        self.assertEqual(payload["account_id"], "acct-a")
        self.assertEqual(payload["requested_by"], "console-1")
        self.assertEqual(payload["actor_role"], "account_console")
        self.assertEqual(handler.store.required_role, "account_console")

    def test_account_console_token_can_only_create_worker_pairing_for_own_workspace(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                if required_role != "account_console":
                    return None
                return {
                    "console_id": "console-1",
                    "device_id": "console-1",
                    "account_id": "acct-a",
                    "device_role": "account_console",
                    "status": "active",
                }

            def snapshot(self, workspace_id=None, *, account_id=None):
                return {
                    "accounts": {
                        "items": [
                            {"account_id": "acct-a", "workspace_ids": ["tenant-a"]},
                            {"account_id": "acct-b", "workspace_ids": ["tenant-b"]},
                        ]
                    }
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer account-token"}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            query = handler._authorized_control_pairing_query(
                {"workspace_id": ["tenant-a"], "device_role": ["remote_worker"], "requested_by": ["spoofed"]}
            )
            with self.assertRaises(PermissionError):
                handler._authorized_control_pairing_query(
                    {"workspace_id": ["tenant-b"], "device_role": ["remote_worker"]}
                )
            with self.assertRaises(PermissionError):
                handler._authorized_control_pairing_query(
                    {"workspace_id": ["tenant-a"], "device_role": ["android_bridge"]}
                )

        self.assertEqual(query["workspace_id"], ["tenant-a"])
        self.assertEqual(query["account_id"], ["acct-a"])
        self.assertEqual(query["device_role"], ["remote_worker"])
        self.assertEqual(query["requested_by"], ["console-1"])

    def test_account_console_snapshot_filters_account_and_does_not_register_ios_terminal(self):
        class FakeStore:
            def __init__(self):
                self.snapshot_kwargs = None
                self.registered_terminal = False

            def authenticate_token(self, token, *, required_role=""):
                if required_role != "account_console":
                    return None
                return {
                    "console_id": "console-1",
                    "device_id": "console-1",
                    "account_id": "acct-a",
                    "device_role": "account_console",
                    "status": "active",
                }

            def snapshot(self, workspace_id=None, *, account_id=None):
                self.snapshot_kwargs = {"workspace_id": workspace_id, "account_id": account_id}
                if account_id:
                    return {"ok": True, "accounts": {"items": [{"account_id": account_id, "workspace_ids": ["tenant-a"]}]}}
                return {
                    "ok": True,
                    "accounts": {
                        "items": [
                            {"account_id": "acct-a", "workspace_ids": ["tenant-a"]},
                            {"account_id": "acct-b", "workspace_ids": ["tenant-b"]},
                        ]
                    },
                }

            def register_ios_terminal(self, *args, **kwargs):
                self.registered_terminal = True

        handler = object.__new__(Handler)
        handler.path = "/ios/control/snapshot?workspace_id=tenant-a&terminal_id=ios-web-test"
        handler.headers = {"authorization": "Bearer account-token"}
        handler.client_address = ("127.0.0.1", 12345)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            handler.do_GET()

        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(handler.store.snapshot_kwargs, {"workspace_id": "tenant-a", "account_id": "acct-a"})
        self.assertFalse(handler.store.registered_terminal)

    def test_management_token_marks_control_actions_as_management(self):
        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer owner-secret"}
        handler.store = Mock()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            payload = handler._authorized_control_action_payload({"action": "queue_android_command"})

        self.assertEqual(payload["actor_role"], "management")
        self.assertFalse("requested_by" in payload and payload["requested_by"])

    def test_ios_terminal_token_can_create_workspace_scoped_device_pairing(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return {
                    "terminal_id": "ios-1",
                    "device_id": "ios-1",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer terminal-token"}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            query = handler._authorized_control_pairing_query(
                {"workspace_id": ["tenant-a"], "device_role": ["android_bridge"], "requested_by": ["spoofed"]}
            )
            with self.assertRaises(PermissionError):
                handler._authorized_control_pairing_query(
                    {"workspace_id": ["tenant-b"], "device_role": ["android_bridge"]}
                )
            with self.assertRaises(PermissionError):
                handler._authorized_control_pairing_query(
                    {"workspace_id": ["tenant-a"], "device_role": ["ios_terminal"]}
                )

        self.assertEqual(query["workspace_id"], ["tenant-a"])
        self.assertEqual(query["requested_by"], ["ios-1"])

    def test_ios_control_pair_endpoint_binds_terminal_token(self):
        class FakeStore:
            def bind_device(self, payload, *, client="", required_role=""):
                self.payload = payload
                self.client = client
                self.required_role = required_role
                return {
                    "terminal_id": "ios-1",
                    "device_id": "ios-1",
                    "workspace_id": "tenant-a",
                    "device_role": "ios_terminal",
                    "token": payload["pairing_token"],
                }

        handler = object.__new__(Handler)
        body = json.dumps({"pairing_token": "terminal-token", "terminal_id": "ios-1"}).encode("utf-8")
        handler.path = "/ios/control/pair"
        handler.headers = {"content-length": str(len(body)), "host": "control.test:8791"}
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = Mock()
        handler.server.server_address = ("127.0.0.1", 8791)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = FakeStore()

        handler.do_POST()

        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["binding"]["terminal_id"], "ios-1")
        self.assertEqual(payload["binding"]["token"], "terminal-token")
        self.assertEqual(handler.store.required_role, "ios_terminal")

    def test_android_pairing_request_waits_for_controller_approval(self):
        class FakeStore:
            def create_pairing_request(self, **kwargs):
                self.request_kwargs = kwargs
                return {
                    "token_id": "preq-1",
                    "request_id": "preq-1",
                    "workspace_id": kwargs["workspace_id"],
                    "device_id": kwargs["device_id"],
                    "device_role": "android_bridge",
                    "status": "requested",
                }

        handler = object.__new__(Handler)
        body = json.dumps({"workspace_id": "tenant-a", "device_id": "vivo-test"}).encode("utf-8")
        handler.path = "/android/pairing/request"
        handler.headers = {"content-length": str(len(body)), "host": "control.test:8791"}
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = Mock()
        handler.server.server_address = ("127.0.0.1", 8791)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = FakeStore()

        handler.do_POST()

        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "requested")
        self.assertEqual(payload["request_id"], "preq-1")
        self.assertEqual(handler.store.request_kwargs["device_role"], "android_bridge")
        self.assertEqual(handler.store.request_kwargs["device_id"], "vivo-test")

    def test_android_pairing_status_returns_approved_token(self):
        class FakeStore:
            def pairing_request_status(self, request_id, *, workspace_id=None):
                self.request_id = request_id
                self.workspace_id = workspace_id
                return {
                    "token_id": "preq-1",
                    "request_id": "preq-1",
                    "token": "android-token",
                    "workspace_id": "tenant-a",
                    "device_id": "vivo-test",
                    "device_role": "android_bridge",
                    "status": "pending",
                    "expires_at": "2026-07-23T00:00:00+00:00",
                }

        handler = object.__new__(Handler)
        handler.path = "/android/pairing/status?request_id=preq-1&workspace_id=tenant-a"
        handler.headers = {"host": "control.test:8791"}
        handler.rfile = BytesIO()
        handler.wfile = BytesIO()
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = Mock()
        handler.server.server_address = ("127.0.0.1", 8791)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.store = FakeStore()

        handler.do_GET()

        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["token"], "android-token")
        self.assertEqual(payload["pairing"]["pairing_token"], "android-token")
        self.assertEqual(handler.store.request_id, "preq-1")

    def test_management_auth_requires_configured_token(self):
        handler = object.__new__(Handler)
        handler.headers = {}

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            with self.assertRaises(PermissionError):
                handler._authorize_management()

        handler.headers = {"authorization": "Bearer owner-secret"}
        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            handler._authorize_management()

        handler.headers = {"authorization": "Bearer wrong"}
        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            with self.assertRaises(PermissionError):
                handler._authorize_management()

    def test_android_command_rejects_workspace_mismatch_for_bound_token(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return {
                    "device_id": "android-1",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer android-token"}
        handler.store = FakeStore()

        with self.assertRaises(PermissionError):
            handler._authorized_android_command_workspace({"workspace_id": "tenant-b"})

        self.assertEqual(handler._authorized_android_command_workspace({"workspace_id": "tenant-a"}), "tenant-a")

    def test_android_command_allows_management_token_workspace_selection(self):
        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer owner-secret"}
        handler.store = Mock()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            workspace = handler._authorized_android_command_workspace({"workspace_id": "tenant-b"})

        self.assertEqual(workspace, "tenant-b")

    def test_android_artifact_download_uses_bound_workspace(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return {
                    "device_id": "android-1",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer android-token"}
        handler.store = FakeStore()

        workspace = handler._authorized_artifact_download_workspace("/android/artifact/art_1", {})

        self.assertEqual(workspace, "tenant-a")

    def test_android_artifact_download_rejects_workspace_mismatch_for_bound_token(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return {
                    "device_id": "android-1",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer android-token"}
        handler.store = FakeStore()

        with self.assertRaises(PermissionError):
            handler._authorized_artifact_download_workspace(
                "/android/artifact/art_1",
                {"workspace_id": ["tenant-b"]},
            )

    def test_android_artifact_upload_overwrites_bound_workspace(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return {
                    "device_id": "android-1",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer android-token"}
        handler.store = FakeStore()

        payload = handler._authorized_artifact_upload_payload(
            "/android/artifact",
            {"workspace_id": "tenant-b", "device_id": "spoofed", "files": [{"name": "a.txt", "text": "x"}]},
        )

        self.assertEqual(payload["workspace_id"], "tenant-a")
        self.assertEqual(payload["device_id"], "android-1")

    def test_mobile_artifact_access_requires_management_token_when_configured(self):
        handler = object.__new__(Handler)
        handler.headers = {}

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            with self.assertRaises(PermissionError):
                handler._authorized_artifact_download_workspace("/mobile/artifacts/art_1", {})
            with self.assertRaises(PermissionError):
                handler._authorized_artifact_upload_payload("/mobile/artifacts", {"workspace_id": "tenant-a"})

        handler.headers = {"authorization": "Bearer owner-secret"}
        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            workspace = handler._authorized_artifact_download_workspace(
                "/mobile/artifacts/art_1",
                {"workspace_id": ["tenant-a"]},
            )
            payload = handler._authorized_artifact_upload_payload("/mobile/artifacts", {"workspace_id": "tenant-b"})

        self.assertEqual(workspace, "tenant-a")
        self.assertEqual(payload["workspace_id"], "tenant-b")

    def test_mobile_artifact_access_allows_ios_terminal_token_in_workspace(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                return {
                    "terminal_id": "ios-1",
                    "device_id": "ios-1",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer terminal-token"}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            workspace = handler._authorized_artifact_download_workspace(
                "/mobile/artifacts/art_1",
                {"workspace_id": ["tenant-a"]},
            )
            payload = handler._authorized_artifact_upload_payload("/mobile/artifacts", {"workspace_id": "tenant-a"})
            with self.assertRaises(PermissionError):
                handler._authorized_artifact_download_workspace(
                    "/mobile/artifacts/art_1",
                    {"workspace_id": ["tenant-b"]},
                )
            with self.assertRaises(PermissionError):
                handler._authorized_artifact_upload_payload("/mobile/artifacts", {"workspace_id": "tenant-b"})

        self.assertEqual(workspace, "tenant-a")
        self.assertEqual(payload["workspace_id"], "tenant-a")

    def test_browser_extension_auth_requires_its_own_pairing_role(self):
        class FakeStore:
            def authenticate_token(self, token, *, required_role=""):
                self.seen = (token, required_role)
                return {
                    "device_id": "edge-main",
                    "workspace_id": "tenant-a",
                    "device_role": required_role,
                    "status": "active",
                }

        handler = object.__new__(Handler)
        handler.headers = {"authorization": "Bearer extension-token"}
        handler.store = FakeStore()

        with patch.dict(os.environ, {"SPIRITKIN_MANAGEMENT_TOKEN": "owner-secret"}):
            binding = handler._authorize_extension({})

        self.assertEqual(binding["workspace_id"], "tenant-a")
        self.assertEqual(handler.store.seen, ("extension-token", "browser_extension"))

    def test_handle_link_writes_legacy_and_control_store(self):
        class FakeStore:
            def record_mobile_link(self, link, *, source, client, workspace_id, device_id=""):
                return {
                    "link_id": "mlink_1",
                    "link": link,
                    "source": source,
                    "client": client,
                    "workspace_id": workspace_id,
                    "device_id": device_id,
                }

        handler = object.__new__(Handler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.store = FakeStore()
        handler._send_json = Mock()

        with patch("scripts.mobile_link_receiver.write_legacy_mobile_link") as write_legacy, patch(
            "scripts.mobile_link_receiver.print"
        ):
            handler._handle_link(
                {
                    "link": "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283",
                    "source": "test",
                    "device_id": "android-1",
                }
            )

        handler._send_json.assert_called_once()
        write_legacy.assert_called_once()
        payload = handler._send_json.call_args.args[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["link"]["link"], "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283")
        self.assertEqual(payload["link_id"], "mlink_1")
        self.assertEqual(payload["link"]["device_id"], "android-1")
        self.assertIn("legacy_latest", payload["stored_at"])

    def test_browser_extension_http_round_trip_creates_product_artifact(self):
        from scripts.control_plane_store import ControlPlaneStore

        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="browser_extension")
            mobile_link = store.record_mobile_link(
                "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283",
                source="android-bridge",
                workspace_id="tenant-a",
            )
            previous_store = Handler.store
            Handler.store = store
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"

            def post(path, payload, token=""):
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                request = urllib.request.Request(
                    base_url + path,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))

            try:
                bound = post(
                    "/extension/pair",
                    {"pairing_token": pairing["token"], "device_id": "edge-main"},
                )
                token = bound["binding"]["token"]
                claimed = post("/extension/links/claim", {"limit": 1}, token)
                completed = post(
                    "/extension/results",
                    {
                        "link_id": mobile_link["link_id"],
                        "success": True,
                        "product_data": {
                            "schema": "spiritkin.pdd_product_data.v1",
                            "goodsId": "680378531283",
                            "title": "test product",
                            "mainImages": ["https://img.test/1.jpg", "https://img.test/2.jpg"],
                            "detailImages": ["https://img.test/detail.jpg"],
                            "skuInfo": {"skuList": []},
                        },
                        "summary": {"goods_id": "680378531283", "listing_gate_ok": False},
                    },
                    token,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                Handler.store = previous_store

            self.assertEqual(claimed["links"][0]["link_id"], mobile_link["link_id"])
            self.assertTrue(completed["artifact_id"].startswith("art_"))
            self.assertEqual(completed["ecommerce_task"]["task"]["status"], "productdata_ready_with_gaps")
            self.assertEqual(
                completed["ecommerce_task"]["artifact"]["control_plane_artifact_id"],
                completed["artifact_id"],
            )
            saved_link = store.list_mobile_links(workspace_id="tenant-a", status="completed")["links"][0]
            self.assertEqual(saved_link["artifact_id"], completed["artifact_id"])
            artifact_file = store.artifact_file(completed["artifact_id"], workspace_id="tenant-a")
            self.assertIn("680378531283", artifact_file["path"].read_text(encoding="utf-8"))

    def test_ios_growth_http_round_trip_requires_ordered_governance(self):
        from scripts.control_plane_store import ControlPlaneStore

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_GROWTH_EVENT_LOG": str(Path(tmp) / "growth-events.jsonl"),
                "SPIRITKIN_GROWTH_REGISTRY_LOG": str(Path(tmp) / "growth-registry.jsonl"),
                "SPIRITKIN_IOS_CAPABILITIES_PATH": str(Path(tmp) / "ios-capabilities.json"),
            },
            clear=False,
        ):
            previous_store = Handler.store
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="local-ecommerce", device_role="ios_terminal")
            binding = store.bind_device(
                {"pairing_token": pairing["token"], "device_id": "ios-http-test"},
                required_role="ios_terminal",
            )
            Handler.store = store
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"

            def post(payload):
                request = urllib.request.Request(
                    base_url + "/ios/growth",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "http://127.0.0.1:8792",
                        "X-SpiritKin-Workspace": "local-ecommerce",
                        "Authorization": f"Bearer {binding['token']}",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read().decode("utf-8"))

            try:
                proposed_status, proposed = post(
                    {
                        "action": "mine_workflow",
                        "title": "HTTP governance workflow",
                        "steps": [
                            {"capability_id": "catalog.read"},
                            {"capability_id": "listing.validate"},
                        ],
                        "workspace_id": "local-ecommerce",
                    }
                )
                candidate_id = proposed["candidate"]["candidate_id"]
                skipped_status, _ = post(
                    {
                        "action": "review_candidate",
                        "candidate_id": candidate_id,
                        "decision": "approve",
                        "reviewer": "spoofed-client",
                        "reason": "skip stages",
                        "evidence": {"source": "http-test"},
                    }
                )
                cross_workspace_status, _ = post(
                    {
                        "action": "advance_stage",
                        "candidate_id": candidate_id,
                        "workspace_id": "attacker-workspace",
                        "stage": "design",
                        "evidence": {"source": "http-test", "stage": "design"},
                    }
                )
                for stage in ("design", "dry_run", "benchmark"):
                    stage_status, stage_payload = post(
                        {
                            "action": "advance_stage",
                            "candidate_id": candidate_id,
                            "workspace_id": "local-ecommerce",
                            "stage": stage,
                            "evidence": {"source": "http-test", "stage": stage},
                        }
                    )
                    self.assertEqual(stage_status, 200)
                    self.assertEqual(stage_payload["candidate"]["workspace_id"], "local-ecommerce")
                benchmark_status, benchmarked = post(
                    {
                        "action": "record_candidate_benchmark",
                        "candidate_id": candidate_id,
                        "confirmed": True,
                        "benchmark": {
                            "version": "2.0",
                            "baseline_version": "1.0",
                            "dataset": "http-governance-v1",
                            "before": {
                                "success_rate": 0.80,
                                "latency_ms": 1500,
                                "cost": 2.0,
                                "retry_count": 4,
                                "review_count": 3,
                                "quality_score": 75,
                            },
                            "after": {
                                "success_rate": 0.92,
                                "latency_ms": 1100,
                                "cost": 1.5,
                                "retry_count": 2,
                                "review_count": 1,
                                "quality_score": 88,
                            },
                        },
                    }
                )
                review_stage_status, _ = post(
                    {
                        "action": "advance_stage",
                        "candidate_id": candidate_id,
                        "stage": "review",
                        "evidence": {"source": "http-test", "stage": "review"},
                    }
                )
                review_status, reviewed = post(
                    {
                        "action": "review_candidate",
                        "candidate_id": candidate_id,
                        "decision": "approve",
                        "reviewer": "spoofed-client",
                        "reason": "沙箱与评测证据通过",
                        "confirmed": True,
                        "evidence": {"source": "http-test", "test": "all"},
                    }
                )
                missing_evidence_status, _ = post(
                    {"action": "register_candidate", "candidate_id": candidate_id, "confirmed": True}
                )
                register_status, registered = post(
                    {
                        "action": "register_candidate",
                        "candidate_id": candidate_id,
                        "confirmed": True,
                        "registry_evidence": {"source": "http-test", "release": "manual"},
                    }
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                Handler.store = previous_store

            self.assertEqual(proposed_status, 200)
            self.assertEqual(skipped_status, 403)
            self.assertEqual(cross_workspace_status, 403)
            self.assertEqual(benchmark_status, 200)
            self.assertTrue(benchmarked["benchmark_report"]["promotion_gate"]["passed"])
            self.assertEqual(review_stage_status, 200)
            self.assertEqual(review_status, 200)
            self.assertEqual(reviewed["candidate"]["review"]["reviewer"], "ios-terminal:ios-http-test")
            self.assertEqual(missing_evidence_status, 400)
            self.assertEqual(register_status, 200)
            self.assertEqual(registered["candidate"]["status"], "registered")
            self.assertFalse(registered["candidate"]["activation"]["enabled"])
            self.assertEqual(registered["candidate"]["registry"]["registered_by"], "ios-terminal:ios-http-test")

    def test_mobile_artifact_store_rejects_sensitive_payload_keys(self):
        from backend.mobile.artifact_store import MobileArtifactStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MobileArtifactStore(Path(tmp) / "mobile-artifacts")

            with self.assertRaises(ValueError) as ctx:
                store.ingest(
                    {
                        "source": "ios_terminal",
                        "files": [{"name": "safe.txt", "text": "safe"}],
                        "metadata": {"session_token": "secret"},
                    }
                )

            self.assertIn("metadata.session_token", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
