from __future__ import annotations

import hashlib
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib import request

from backend.app.mobile_management import handle_mobile_management_action
from backend.app.operations_center import default_managed_services
from backend.devices.android_device import AndroidDeviceBackend
from backend.executors.android_executor import AndroidExecutor
from backend.executors.base import ExecutionRequest
from backend.mobile.android_apk_promotion import approve_apk_release, build_apk_promotion_gate
from backend.mobile.android_bridge import (
    AndroidCommandTranslator,
    AndroidCompanionRegistry,
    AndroidDeviceSpec,
    AndroidDeviceState,
    build_android_execution_payload,
)
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.mobile.android_endpoint import (
    ANDROID_AUTH_HEADER,
    AndroidDeviceEndpoint,
    _android_authorized,
    _apk_manifest,
)
from backend.mobile.android_push import AndroidPushNotification, AndroidPushQueue
from backend.mobile.ios_endpoint import _clear_ios_control_snapshot_cache, _ios_control_action, _ios_control_snapshot
from backend.mobile.link_receiver import extract_pdd_link, record_mobile_pdd_link
from backend.security.safety_control import set_safety_stop
from backend.tools.android_tools import get_android_tools


class AndroidBridgeTests(unittest.TestCase):
    def test_android_endpoint_without_token_is_not_open_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_android_authorized({}, ""))
        with patch.dict(os.environ, {"SPIRITKIN_ALLOW_LOCALHOST_WITHOUT_TOKEN": "1"}, clear=False):
            # Bypass only applies to requests that are actually local.
            self.assertTrue(_android_authorized({"Host": "127.0.0.1:8790"}, ""))
            self.assertTrue(_android_authorized({}, "", client_ip="127.0.0.1"))
            self.assertFalse(_android_authorized({}, "", client_ip="203.0.113.5"))
            self.assertFalse(_android_authorized({"Host": "127.0.0.1:8790"}, "", client_ip="203.0.113.5"))

    def test_android_endpoint_accepts_configured_token(self):
        self.assertTrue(_android_authorized({ANDROID_AUTH_HEADER: "secret"}, "secret"))
        self.assertFalse(_android_authorized({ANDROID_AUTH_HEADER: "wrong"}, "secret"))

    def test_android_command_translator_builds_heartbeat(self):
        state = AndroidDeviceState(device_id="test-phone", battery_pct=85.0, charging=True, wifi_connected=True, screen_on=True)
        heartbeat = AndroidCommandTranslator.device_state_to_heartbeat(state)
        self.assertEqual(heartbeat["device_id"], "test-phone")
        self.assertEqual(heartbeat["battery_pct"], 85.0)
        self.assertTrue(heartbeat["charging"])

    def test_build_android_execution_payload(self):
        request = ExecutionRequest(target="android_device", operation="launch_app", params={"app_name": "TestApp"})
        payload = build_android_execution_payload(request)
        self.assertEqual(payload["target"], "android_device")
        self.assertEqual(payload["operation"], "launch_app")

    def test_push_queue_enqueues_and_drains_per_device(self):
        queue = AndroidPushQueue()
        queue.push(AndroidPushNotification(title="Hello", body="World", target_device_id="phone1"))
        queue.push(AndroidPushNotification(title="Hi", body="There", target_device_id="phone1"))
        self.assertEqual(queue.pending_count("phone1"), 2)
        drained = queue.drain("phone1")
        self.assertEqual(len(drained), 2)
        self.assertEqual(drained[0]["title"], "Hello")
        self.assertEqual(queue.pending_count("phone1"), 0)

    def test_android_executor_supports_android_target(self):
        executor = AndroidExecutor()
        self.assertTrue(executor.supports(ExecutionRequest(target="android_device", operation="device_status")))
        self.assertTrue(executor.supports(ExecutionRequest(target="android", operation="device_status")))

    def test_android_executor_device_status(self):
        executor = AndroidExecutor()
        result = executor.execute(ExecutionRequest(target="android_device", operation="device_status"))
        self.assertTrue(result.success)
        self.assertIn("device_id", result.data)

    def test_android_executor_launch_app(self):
        executor = AndroidExecutor()
        result = executor.execute(ExecutionRequest(target="android_device", operation="launch_app", params={"app_name": "Test"}))
        self.assertTrue(result.success)
        self.assertIn("Test", result.message)

    def test_android_executor_missing_app_name(self):
        executor = AndroidExecutor()
        result = executor.execute(ExecutionRequest(target="android_device", operation="launch_app", params={}))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "missing_app_name")

    def test_get_android_tools_produces_six_tools(self):
        tools = get_android_tools()
        self.assertGreaterEqual(len(tools), 6)
        names = {t.spec.name for t in tools}
        self.assertIn("android.device.info", names)
        self.assertIn("android.notification.push", names)
        self.assertIn("android.app.launch", names)

    def test_android_device_spec_is_immutable(self):
        spec = AndroidDeviceSpec(device_id="p1", model="Pixel", android_version="14")
        self.assertEqual(spec.model, "Pixel")

    def test_android_device_backend_returns_device_id(self):
        backend = AndroidDeviceBackend(device_id="my-phone")
        status = backend.device_status()
        self.assertEqual(status["device_id"], "my-phone")

    def test_android_companion_registry_tracks_state_apps_and_commands(self):
        registry = AndroidCompanionRegistry()
        registry.update_heartbeat({"device_id": "phone1", "battery_pct": 80, "installed_apps": [{"name": "Feishu", "package": "com.feishu"}]})
        queued = registry.enqueue_command("phone1", "app.launch", {"app_name": "Feishu"})

        self.assertTrue(queued["queued"])
        self.assertEqual(queued["command"]["permission"]["tier"], "open_app")
        self.assertEqual(registry.device_status("phone1")["installed_app_count"], 1)
        self.assertEqual(registry.list_installed_apps("phone1")["apps"][0]["name"], "Feishu")
        self.assertEqual(registry.drain_commands("phone1")[0]["operation"], "app.launch")

    def test_android_companion_store_persists_heartbeat_and_commands(self):
        with TemporaryDirectory() as tmp:
            store = AndroidCompanionStore(Path(tmp) / "android-companion.json")
            status = store.update_heartbeat({"device_id": "phone1", "battery_pct": 81, "installed_apps": ["Feishu"]})
            queued = store.enqueue_command("phone1", "app.launch", {"app_name": "Feishu"})

            reloaded = AndroidCompanionStore(Path(tmp) / "android-companion.json")
            snapshot = reloaded.snapshot()
            drained = reloaded.drain_commands("phone1")

        self.assertTrue(status["online"])
        self.assertTrue(queued["queued"])
        self.assertEqual(snapshot["device_count"], 1)
        self.assertEqual(snapshot["pending_command_count"], 1)
        self.assertEqual(drained[0]["operation"], "app.launch")
        self.assertEqual(drained[0]["permission"]["tier"], "open_app")

    def test_android_companion_store_enforces_permission_tiers(self):
        with TemporaryDirectory() as tmp:
            store = AndroidCompanionStore(Path(tmp) / "android-companion.json")
            read_only = store.enqueue_command("phone1", "device.status", {})
            screenshot = store.enqueue_command("phone1", "screenshot.capture", {"purpose": "unit"})
            high_risk = store.enqueue_command("phone1", "adb.shell.rm", {"command": "rm -rf /sdcard"})
            snapshot = store.snapshot()

        self.assertTrue(read_only["queued"])
        self.assertEqual(read_only["command"]["permission"]["tier"], "read_only")
        self.assertTrue(screenshot["queued"])
        self.assertEqual(screenshot["command"]["permission"]["tier"], "screenshot")
        self.assertFalse(high_risk["queued"])
        self.assertEqual(high_risk["error_code"], "android_permission_tier_blocked")
        self.assertIn("permission_policy", snapshot)
        self.assertIn("high_risk", {item["tier"] for item in snapshot["permission_policy"]["tiers"]})

    def test_android_companion_store_builds_device_permission_posture_from_heartbeat(self):
        with TemporaryDirectory() as tmp:
            store = AndroidCompanionStore(Path(tmp) / "android-companion.json")
            store.update_heartbeat(
                {
                    "device_id": "phone1",
                    "device_state": {
                        "battery_pct": 81,
                        "pdd_accessibility_granted": False,
                        "pdd_accessibility_connected": False,
                        "screen_capture_authorized": False,
                    },
                    "installed_apps": [{"name": "PDD", "package": "com.xunmeng.pinduoduo"}],
                    "capabilities": ["heartbeat", "pdd.launch", "android.ui_snapshot", "android.screenshot.capture"],
                    "command_catalog": [
                        {"operation": "pdd.launch", "required_capabilities": ["pdd.launch"], "required_packages": ["com.xunmeng.pinduoduo"]},
                        {"operation": "android.ui_snapshot", "required_capabilities": ["android.ui_snapshot"], "requires_accessibility": True},
                        {"operation": "android.screenshot.capture", "required_capabilities": ["android.screenshot.capture"]},
                        {"operation": "pdd.create_listing", "required_capabilities": ["pdd.create_listing"], "requires_accessibility": True, "required_packages": ["com.xunmeng.pinduoduo"]},
                    ],
                }
            )
            snapshot = store.snapshot()
            posture = snapshot["devices"][0]["permission_posture"]
            worker = snapshot["worker"]
            gap_ids = {gap["id"] for gap in posture["gaps"]}

        self.assertEqual(posture["status"], "partial")
        self.assertEqual(posture["operation_count"], 4)
        self.assertEqual(posture["available_operation_count"], 1)
        self.assertIn("android_accessibility_required", gap_ids)
        self.assertIn("android_screenshot_permission_missing", gap_ids)
        self.assertIn("android_permission_tier_blocked", gap_ids)
        self.assertGreaterEqual(snapshot["permission_posture"]["gap_count"], 3)
        self.assertEqual(worker["schema_version"], "spiritkin.android_worker.v1")
        self.assertEqual(worker["worker_id"], "android_control_worker")
        self.assertEqual(worker["role"], "controlled_execution_worker")
        self.assertEqual(worker["online_device_count"], 1)
        self.assertEqual(worker["status"], "needs_attention")
        self.assertIn("android.screenshot.capture", worker["capabilities"])
        self.assertEqual(worker["permissions"]["gap_count"], snapshot["permission_posture"]["gap_count"])
        self.assertFalse(worker["lifecycle"]["can_capture_screen"])

    def test_android_companion_store_records_command_delivery_and_results(self):
        with TemporaryDirectory() as tmp:
            store = AndroidCompanionStore(Path(tmp) / "android-companion.json")
            queued = store.enqueue_command("phone1", "app.launch", {"app_name": "Feishu"})
            command_id = queued["command"]["command_id"]
            drained = store.drain_commands("phone1")
            delivered = store.snapshot()
            status_after_delivery = store.device_status("phone1")
            store.update_heartbeat(
                {
                    "device_id": "phone1",
                    "command_results": [
                        {
                            "command_id": command_id,
                            "operation": "app.launch",
                            "status": "completed",
                            "message": "启动应用: Feishu",
                        }
                    ],
                }
            )
            command_status = store.command_status(command_id, "phone1")
            completed = store.snapshot()

        self.assertEqual(drained[0]["status"], "delivered")
        self.assertEqual(status_after_delivery["inflight_command_count"], 1)
        self.assertEqual(delivered["command_status_counts"]["delivered"], 1)
        self.assertEqual(completed["command_status_counts"]["completed"], 1)
        self.assertEqual(command_status["status"], "completed")
        self.assertEqual(command_status["command_id"], command_id)
        self.assertEqual(completed["recent_commands"][-1]["message"], "启动应用: Feishu")

    def test_android_command_result_persists_runtime_trajectory(self):
        with TemporaryDirectory() as tmp:
            trajectory_path = Path(tmp) / "trajectories.jsonl"
            store = AndroidCompanionStore(Path(tmp) / "android-companion.json")
            queued = store.enqueue_command("phone1", "app.launch", {"app_name": "Feishu"})
            command_id = queued["command"]["command_id"]
            with patch.dict(os.environ, {"SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path)}, clear=False):
                store.update_heartbeat(
                    {
                        "device_id": "phone1",
                        "command_results": [
                            {
                                "command_id": command_id,
                                "operation": "app.launch",
                                "status": "failed",
                                "message": "启动失败",
                                "error_code": "app_not_found",
                            }
                        ],
                    }
                )
            command_status = store.command_status(command_id, "phone1")
            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(command_status["trajectory_record"]["source"], "android.command_result")
        self.assertEqual(command_status["trajectory_record"]["bottleneck_stage"], "mobile_worker")
        self.assertEqual(records[0]["metadata"]["source"], "android.command_result")
        self.assertEqual(records[0]["agent_id"], "android_worker")
        self.assertEqual(records[0]["domain"], "mobile")
        self.assertFalse(records[0]["overall_success"])

    def test_android_device_backend_uses_companion_registry_for_apps_and_launch(self):
        registry = AndroidCompanionRegistry()
        backend = AndroidDeviceBackend(device_id="phone1", companion_registry=registry)
        backend.update_state({"battery_pct": 90, "installed_apps": ["Bilibili"]})

        self.assertEqual(backend.list_installed_apps()["apps"][0]["name"], "Bilibili")
        self.assertTrue(backend.launch_app("Bilibili")["queued"])
        self.assertEqual(registry.drain_commands("phone1")[0]["params"]["app_name"], "Bilibili")

    def test_safety_stop_blocks_android_commands_but_allows_status(self):
        with TemporaryDirectory() as tmp:
            env = {"SPIRITKIN_SAFETY_STATE_PATH": os.path.join(tmp, "safety.json")}
            with patch.dict(os.environ, env, clear=False):
                set_safety_stop(mode="soft_stop", reason="unit test", actor="test")
                executor = AndroidExecutor()

                status = executor.execute(ExecutionRequest(target="android_device", operation="device_status"))
                launch = executor.execute(ExecutionRequest(target="android_device", operation="launch_app", params={"app_name": "Feishu"}))
                queued = AndroidCompanionRegistry().enqueue_command("phone1", "app.launch", {"app_name": "Feishu"})
                read_only = AndroidCompanionRegistry().enqueue_command("phone1", "device.status", {})

                self.assertTrue(status.success)
                self.assertFalse(launch.success)
                self.assertEqual(launch.error_code, "safety_stop_active")
                self.assertFalse(queued["queued"])
                self.assertEqual(queued["error_code"], "safety_stop_active")
                self.assertTrue(read_only["queued"])

    def test_mobile_pdd_link_receiver_records_legacy_files_and_queue_task(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = record_mobile_pdd_link(
                {
                    "link": "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283",
                    "source": "android-bridge",
                    "device_id": "phone1",
                },
                project_root=root,
                client="100.118.62.77",
            )

            self.assertEqual(result["link_type"], "pdd_web_link")
            self.assertTrue((root / "state" / "mobile-links" / "links.jsonl").exists())
            self.assertEqual((root / "state" / "mobile-links" / "latest-link.txt").read_text(encoding="utf-8").strip(), result["link"])
            self.assertEqual(result["ingest"]["task_count"], 1)
            self.assertEqual(len(result["ingest"]["created"]), 1)

    def test_mobile_pdd_link_receiver_prefers_web_link_for_extension(self):
        text = "#小程序://拼多多/UhecnYM1HJR3d5i https://mobile.yangkeduo.com/goods.html?goods_id=680378531283"

        self.assertEqual(
            extract_pdd_link(text),
            "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283",
        )

    def test_android_endpoint_accepts_pdd_link_path(self):
        self.assertIn("/android/link", AndroidDeviceEndpoint.do_POST.__code__.co_consts)

    def test_android_endpoint_exposes_apk_pairing_and_artifact_routes(self):
        constants = _flatten_constants(AndroidDeviceEndpoint.do_GET.__code__.co_consts)

        self.assertIn("/android/apk", constants)
        self.assertIn("/pairing", constants)
        self.assertIn("/android/artifact/", constants)

    def test_android_apk_manifest_reports_integrity_compatibility_and_missing_apk(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AndroidManifest.xml").write_text(
                """
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.spiritkin.mobilelinkbridge"
    android:versionCode="42"
    android:versionName="42.0">
    <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35" />
</manifest>
""".strip(),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SPIRITKIN_ANDROID_BRIDGE_ROOT": str(root)}, clear=False):
                payload = _apk_manifest("http://127.0.0.1:8791")

        self.assertEqual(payload["manifest_version"], 2)
        self.assertEqual(payload["package_name"], "com.spiritkin.mobilelinkbridge")
        self.assertEqual(payload["version_code"], 42)
        self.assertEqual(payload["compatibility"]["min_sdk"], 23)
        self.assertEqual(payload["integrity"]["algorithm"], "sha256")
        self.assertEqual(payload["rollback"]["previous_versions"], [])
        self.assertEqual(payload["build"]["status"], "missing_apk")

    def test_android_apk_manifest_uses_release_manifest_when_built(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            apk = out / "mobile-link-bridge.apk"
            apk.write_bytes(b"apk")
            (root / "AndroidManifest.xml").write_text(
                """
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.spiritkin.mobilelinkbridge"
    android:versionCode="42"
    android:versionName="42.0" />
""".strip(),
                encoding="utf-8",
            )
            (out / "release-manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "package_name": "com.spiritkin.mobilelinkbridge",
                        "version_code": 43,
                        "version_name": "43.0",
                        "compatibility": {"min_sdk": 26, "target_sdk": 35, "max_sdk": 0},
                        "integrity": {"algorithm": "sha256", "sha256": "manifest-hash", "size_bytes": 3},
                        "rollback": {"supported": True, "previous_versions": [{"version_code": 42}]},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SPIRITKIN_ANDROID_BRIDGE_ROOT": str(root)}, clear=False):
                payload = _apk_manifest("http://127.0.0.1:8791")

        self.assertEqual(payload["version_code"], 43)
        self.assertEqual(payload["version_name"], "43.0")
        self.assertEqual(payload["sha256"], "dd37c2d7274f7ea982cb83390c36918fee9ce8889073c44b68cdc00bdb8c3e04")
        self.assertEqual(payload["integrity"]["sha256"], payload["sha256"])
        self.assertEqual(payload["compatibility"]["min_sdk"], 26)
        self.assertTrue(payload["rollback"]["supported"])
        self.assertEqual(payload["build"]["status"], "ready")
        self.assertTrue(payload["build"]["release_manifest_present"])

    def test_android_apk_manifest_reads_bom_release_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            apk = out / "mobile-link-bridge.apk"
            apk.write_bytes(b"apk")
            (root / "AndroidManifest.xml").write_text(
                """
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.spiritkin.mobilelinkbridge"
    android:versionCode="41"
    android:versionName="41.0" />
""".strip(),
                encoding="utf-8",
            )
            (out / "release-manifest.json").write_text(
                "\ufeff" + json.dumps({"version_code": 45, "version_name": "45.0", "package_name": "com.spiritkin.mobilelinkbridge"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SPIRITKIN_ANDROID_BRIDGE_ROOT": str(root)}, clear=False):
                payload = _apk_manifest("http://127.0.0.1:8791")

        self.assertEqual(payload["version_code"], 45)
        self.assertEqual(payload["version_name"], "45.0")
        self.assertTrue(payload["build"]["release_manifest_present"])

    def test_android_apk_promotion_gate_requires_human_approval_before_serving(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = root / "mobile-link-bridge.apk"
            apk.write_bytes(b"signed-apk")
            approval_path = root / "apk-approval.json"
            sha256 = hashlib.sha256(b"signed-apk").hexdigest()
            release_manifest = {
                "package_name": "com.spiritkin.mobilelinkbridge",
                "version_code": 44,
                "version_name": "44.0",
                "integrity": {"algorithm": "sha256", "sha256": sha256, "size_bytes": apk.stat().st_size},
            }

            pending = build_apk_promotion_gate(apk_path=apk, release_manifest=release_manifest, approval_path=approval_path)
            approval = approve_apk_release(
                apk_path=apk,
                release_manifest=release_manifest,
                reviewer="unit-test",
                reason="release smoke passed",
                approval_path=approval_path,
            )
            approved = build_apk_promotion_gate(apk_path=apk, release_manifest=release_manifest, approval_path=approval_path)

        self.assertEqual(pending["status"], "needs_approval")
        self.assertFalse(pending["serving_allowed"])
        self.assertEqual(pending["required_actions"], ["approve_android_apk_release"])
        self.assertTrue(approval["ok"])
        self.assertEqual(approval["promotion_gate"]["status"], "approved")
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(approved["serving_allowed"])
        self.assertEqual(approved["approval"]["reviewer"], "unit-test")

    def test_android_apk_manifest_exposes_promotion_gate_for_matching_release(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            apk = out / "mobile-link-bridge.apk"
            apk.write_bytes(b"release-apk")
            sha256 = hashlib.sha256(b"release-apk").hexdigest()
            (root / "AndroidManifest.xml").write_text(
                """
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.spiritkin.mobilelinkbridge"
    android:versionCode="44"
    android:versionName="44.0" />
""".strip(),
                encoding="utf-8",
            )
            (out / "release-manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "package_name": "com.spiritkin.mobilelinkbridge",
                        "version_code": 44,
                        "version_name": "44.0",
                        "integrity": {"algorithm": "sha256", "sha256": sha256, "size_bytes": apk.stat().st_size},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SPIRITKIN_ANDROID_BRIDGE_ROOT": str(root), "SPIRITKIN_ANDROID_APK_PROMOTION_PATH": str(root / "approval.json")}, clear=False):
                payload = _apk_manifest("http://127.0.0.1:8791")

        self.assertEqual(payload["promotion_gate"]["status"], "needs_approval")
        self.assertFalse(payload["promotion_gate"]["serving_allowed"])
        self.assertEqual(payload["promotion_gate"]["validation"]["actual_sha256"], payload["sha256"])

    def test_android_apk_download_requires_promotion_approval(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            apk = out / "mobile-link-bridge.apk"
            apk.write_bytes(b"release-apk")
            sha256 = hashlib.sha256(b"release-apk").hexdigest()
            (root / "AndroidManifest.xml").write_text(
                """
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.spiritkin.mobilelinkbridge"
    android:versionCode="46"
    android:versionName="46.0" />
""".strip(),
                encoding="utf-8",
            )
            release_manifest = {
                "manifest_version": 2,
                "package_name": "com.spiritkin.mobilelinkbridge",
                "version_code": 46,
                "version_name": "46.0",
                "integrity": {"algorithm": "sha256", "sha256": sha256, "size_bytes": apk.stat().st_size},
            }
            (out / "release-manifest.json").write_text(json.dumps(release_manifest), encoding="utf-8")
            approval_path = root / "approval.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), AndroidDeviceEndpoint)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with patch.dict(os.environ, {"SPIRITKIN_ANDROID_BRIDGE_ROOT": str(root), "SPIRITKIN_ANDROID_APK_PROMOTION_PATH": str(approval_path)}, clear=False):
                    try:
                        request.urlopen(f"{base}/android/apk", timeout=5)
                    except Exception as exc:
                        blocked_code = getattr(exc, "code", 0)
                    approve_apk_release(apk_path=apk, release_manifest=release_manifest, reviewer="unit-test", approval_path=approval_path)
                    with request.urlopen(f"{base}/android/apk", timeout=5) as response:
                        status_code = response.status
                        body = response.read()
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(blocked_code, 403)
        self.assertEqual(status_code, 200)
        self.assertEqual(body, b"release-apk")

    def test_mobile_management_can_approve_android_apk_release(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            apk = out / "mobile-link-bridge.apk"
            apk.write_bytes(b"approved-apk")
            sha256 = hashlib.sha256(b"approved-apk").hexdigest()
            (out / "release-manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "package_name": "com.spiritkin.mobilelinkbridge",
                        "version_code": 45,
                        "version_name": "45.0",
                        "sha256": sha256,
                        "size_bytes": apk.stat().st_size,
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "SPIRITKIN_ANDROID_BRIDGE_ROOT": str(root),
                "SPIRITKIN_ANDROID_APK_PROMOTION_PATH": str(root / "approval.json"),
                "SPIRITKIN_ANDROID_COMPANION_STATE": str(root / "android-companion.json"),
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "backend.app.mobile_management._resolve_adb_path", return_value=None
            ), patch("backend.app.mobile_management._pc_tailscale_ip", return_value=""), patch(
                "backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}
            ):
                payload = handle_mobile_management_action({"action": "approve_android_apk_release", "reviewer": "unit-test"})

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "approved")
        worker = payload["mobile_management"]["android"]["worker"]
        self.assertEqual(worker["promotion_gate"]["status"], "approved")
        self.assertTrue(worker["update"]["serving_allowed"])

    def test_mobile_artifact_store_accepts_android_base64_alias_and_download_lookup(self):
        from backend.mobile.artifact_store import MobileArtifactStore

        with TemporaryDirectory() as tmp:
            store = MobileArtifactStore(Path(tmp))
            result = store.ingest(
                {
                    "purpose": "android_shared_image",
                    "files": [{"name": "phone.png", "mime_type": "image/png", "base64": "cG5n"}],
                },
                source="android_bridge",
                device_id="phone1",
            )
            artifact_id = result["artifacts"][0]["artifact_id"]
            artifact_file = store.artifact_file(artifact_id)

        self.assertTrue(result["ok"])
        self.assertEqual(artifact_file["filename"], "phone.png")
        self.assertEqual(artifact_file["mime_type"], "image/png")

    def test_android_and_ios_endpoints_are_managed_optional_services(self):
        services = {service.service_id: service for service in default_managed_services()}

        self.assertIn("android_endpoint", services)
        self.assertIn("ios_endpoint", services)
        self.assertFalse(services["android_endpoint"].autostart)
        self.assertEqual(services["android_endpoint"].health_path, "/android/health")

    def test_ios_control_snapshot_exposes_primary_terminal_state(self):
        _clear_ios_control_snapshot_cache()
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json")}, clear=False), patch(
            "backend.app.mobile_management._resolve_adb_path", return_value=None
        ), patch("backend.app.mobile_management._pc_tailscale_ip", return_value=""), patch(
            "backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}
        ), patch("backend.app.command_gateway.build_desktop_module_management_response") as module_snapshot:
            snapshot = _ios_control_snapshot(force_refresh=True)

        module_snapshot.assert_not_called()
        self.assertTrue(snapshot["ok"])
        self.assertIn("services", snapshot)
        self.assertEqual(snapshot["services"]["source"], "ios_light")
        self.assertIn("module_management", snapshot)
        self.assertEqual(snapshot["module_management"]["source"], "ios_compact")
        self.assertIn("mobile_management", snapshot)
        self.assertIn("workflows", snapshot)
        self.assertGreaterEqual(snapshot["workflows"]["overview"]["available_definition_count"], 3)
        self.assertEqual(snapshot["snapshot_meta"]["cache"], "miss")

    def test_ios_control_snapshot_uses_short_ttl_cache(self):
        _clear_ios_control_snapshot_cache()
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json")}, clear=False), patch(
            "backend.app.mobile_management._resolve_adb_path", return_value=None
        ), patch("backend.app.mobile_management._pc_tailscale_ip", return_value=""), patch(
            "backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}
        ):
            first = _ios_control_snapshot(force_refresh=True)
            second = _ios_control_snapshot()

        self.assertEqual(first["snapshot_meta"]["cache"], "miss")
        self.assertEqual(second["snapshot_meta"]["cache"], "hit")
        self.assertIn("age_seconds", second["snapshot_meta"])

    def test_ios_pwa_includes_binding_panel(self):
        from backend.mobile.ios_endpoint import _ios_terminal_html

        html = _ios_terminal_html()

        self.assertIn("配对与工作区", html)
        self.assertIn('id="binding"', html)
        self.assertIn("bindingHtml", html)

    def test_ios_control_action_can_queue_android_command(self):
        _clear_ios_control_snapshot_cache()
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"SPIRITKIN_ANDROID_COMPANION_STATE": os.path.join(tmp, "android-companion.json")}, clear=False), patch(
            "backend.app.mobile_management._resolve_adb_path", return_value=None
        ), patch("backend.app.mobile_management._pc_tailscale_ip", return_value=""), patch(
            "backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}
        ):
            status, payload = _ios_control_action(
                {"action": "enqueue_android_command", "device_id": "phone1", "operation": "app.launch", "params": {"app_name": "Feishu"}}
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "queued")
        self.assertIn("ios_control", payload)
        self.assertEqual(payload["ios_control"]["snapshot_meta"]["cache"], "merged")

    def test_ios_control_action_can_compose_and_start_workflow(self):
        _clear_ios_control_snapshot_cache()
        with TemporaryDirectory() as tmp, patch(
            "backend.app.mobile_management._resolve_adb_path", return_value=None
        ), patch("backend.app.mobile_management._pc_tailscale_ip", return_value=""), patch(
            "backend.app.mobile_management._http_health", return_value={"ok": False, "status": 0}
        ):
            status, payload = _ios_control_action(
                {
                    "action": "compose_definition",
                    "project_root": tmp,
                    "workflow_name": "custom.ios.combo.v1",
                    "display_name": "iOS Combo",
                    "mode": "serial",
                    "components": ["ecommerce.auto_listing.v1", "content.video_generation.v1"],
                }
            )
            start_status, start_payload = _ios_control_action(
                {
                    "action": "start_run",
                    "project_root": tmp,
                    "workflow_name": "custom.ios.combo.v1",
                    "inputs": {"composition_note": "unit"},
                }
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action_result"]["data"]["definition"]["metadata"]["composition"]["component_count"], 2)
        self.assertEqual(start_status, 200)
        self.assertTrue(start_payload["ok"])
        self.assertEqual(start_payload["action_result"]["data"]["run"]["workflow_name"], "custom.ios.combo.v1")


def _flatten_constants(values):
    out = []
    for value in values:
        if isinstance(value, (tuple, list, set, frozenset)):
            out.extend(_flatten_constants(value))
        else:
            out.append(value)
    return out
