import base64
import json
import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.control_plane_store import (
    DEFAULT_ACCOUNT_ID,
    STATE_VERSION,
    ControlPlaneStore,
    built_in_workflow_templates,
)


class ControlPlaneStoreTests(unittest.TestCase):
    def test_stale_non_auth_save_preserves_new_pairing_and_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "control"
            stale_store = ControlPlaneStore(state_dir)
            auth_store = ControlPlaneStore(state_dir)
            stale_state = stale_store.load()

            pairing = auth_store.create_pairing_token(
                workspace_id="local-ecommerce",
                device_role="ios_terminal",
                requested_by="test",
            )
            binding = auth_store.bind_device(
                {"pairing_token": pairing["token"], "terminal_id": "ios-race-test"},
                required_role="ios_terminal",
            )

            stale_state["deployment"]["test_marker"] = "stale-writer"
            stale_store.save(stale_state)
            reloaded = auth_store.load()

            self.assertEqual(reloaded["pairing_tokens"][pairing["token_id"]]["status"], "bound")
            self.assertEqual(reloaded["device_bindings"][binding["token"]]["status"], "active")
            self.assertEqual(reloaded["device_bindings"][binding["token"]]["terminal_id"], "ios-race-test")

    def test_built_in_workflow_templates_share_unified_schema_keys(self):
        # Amendment #2 of the SaaS foundation plan: all templates must carry
        # input_schema / metered / category so metering needs no special cases.
        for template_id, template in built_in_workflow_templates().items():
            for key in ("input_schema", "metered", "category"):
                self.assertIn(key, template, f"{template_id} is missing unified key: {key}")
            self.assertIsInstance(template["input_schema"], dict, template_id)
            self.assertIsInstance(template["metered"], str, template_id)


    def test_load_migrates_v1_state_to_schema_v3_accounts_and_action_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "control"
            state_dir.mkdir(parents=True)
            state_file = state_dir / "control_state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "created_at": "2026-06-14T00:00:00+00:00",
                        "updated_at": "2026-06-14T00:00:00+00:00",
                        "events": [
                            {
                                "event_id": "evt_1",
                                "type": "android_command_queued",
                                "at": "2026-06-14T01:00:00+00:00",
                                "payload": {
                                    "workspace_id": "tenant-a",
                                    "command_id": "cmd_1",
                                    "requested_by": "tester",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            store = ControlPlaneStore(state_dir)
            state = store.load()

            self.assertEqual(state["version"], STATE_VERSION)
            self.assertEqual(state["schema"]["version"], STATE_VERSION)
            self.assertIn("v2_action_log_from_events", state["schema"]["migrations"])
            self.assertIn("v3_accounts_and_quotas", state["schema"]["migrations"])
            self.assertIn(DEFAULT_ACCOUNT_ID, state["accounts"])
            self.assertIn("local-ecommerce", state["accounts"][DEFAULT_ACCOUNT_ID]["workspace_ids"])
            self.assertEqual(state["workspaces"]["local-ecommerce"]["account_id"], DEFAULT_ACCOUNT_ID)
            self.assertEqual(state["action_log"][0]["action"], "android_command_queued")
            self.assertEqual(state["action_log"][0]["workspace_id"], "tenant-a")
            self.assertEqual(state["action_log"][0]["actor"], "tester")
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["version"], STATE_VERSION)

    def test_account_crud_assigns_workspace_and_reports_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            created = store.management_action(
                {
                    "action": "create_account",
                    "account_id": "acct-a",
                    "name": "Account A",
                    "plan": {"tier": "trial", "quotas": {"max_workspaces": 2, "custom_future_quota": 7}},
                    "actor_role": "management",
                }
            )
            workspace = store.management_action(
                {
                    "action": "register_workspace",
                    "workspace_id": "tenant-a",
                    "account_id": "acct-a",
                    "name": "Tenant A",
                    "actor_role": "management",
                }
            )
            account = store.management_action({"action": "get_account_usage", "account_id": "acct-a", "actor_role": "management"})["account"]

            self.assertEqual(created["account"]["account_id"], "acct-a")
            self.assertEqual(workspace["workspace"]["account_id"], "acct-a")
            self.assertEqual(account["usage_summary"]["workspace_count"], 1)
            self.assertEqual(account["plan"]["quotas"]["custom_future_quota"], 7)

    def test_account_workspace_quota_blocks_new_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.create_account(account_id="acct-a", plan={"quotas": {"max_workspaces": 1}})
            store.ensure_workspace("tenant-a", account_id="acct-a")

            with self.assertRaises(PermissionError):
                store.ensure_workspace("tenant-b", account_id="acct-a")

    def test_account_worker_quota_blocks_second_remote_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.create_account(account_id="acct-a", plan={"quotas": {"max_workers": 1}})
            store.ensure_workspace("tenant-a", account_id="acct-a")
            first = store.create_pairing_token(workspace_id="tenant-a", device_role="remote_worker")
            store.bind_device({"pairing_token": first["token"], "worker_id": "worker-a"}, required_role="remote_worker")

            with self.assertRaises(PermissionError):
                store.create_pairing_token(workspace_id="tenant-a", device_role="remote_worker")

    def test_account_scrape_quota_is_consumed_and_resets_by_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.create_account(account_id="acct-a", plan={"quotas": {"max_scrapes_per_period": 2, "scrape_period_days": 1}})
            store.ensure_workspace("tenant-a", account_id="acct-a")
            first = store.start_workflow_run(workspace_id="tenant-a", inputs={"metered_amount": 2})

            self.assertEqual(first["run"]["quota_consumption"]["used_after"], 2)
            with self.assertRaises(PermissionError):
                store.start_workflow_run(workspace_id="tenant-a")

            state = store.load()
            state["accounts"]["acct-a"]["plan"]["period_end"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            store.save(state)
            second = store.start_workflow_run(workspace_id="tenant-a")

            self.assertEqual(second["run"]["quota_consumption"]["used_before"], 0)
            self.assertEqual(second["run"]["quota_consumption"]["used_after"], 1)

    def test_disabled_account_freezes_workspace_worker_and_workflow_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.create_account(account_id="acct-a")
            store.ensure_workspace("tenant-a", account_id="acct-a")
            store.set_account_status("acct-a", "disabled")

            with self.assertRaises(PermissionError):
                store.ensure_workspace("tenant-b", account_id="acct-a")
            with self.assertRaises(PermissionError):
                store.create_pairing_token(workspace_id="tenant-a", device_role="remote_worker")
            with self.assertRaises(PermissionError):
                store.start_workflow_run(workspace_id="tenant-a")

    def test_account_actions_require_management_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")

            with self.assertRaises(PermissionError):
                store.management_action({"action": "create_account", "account_id": "acct-a", "actor_role": "account_console"})
            with self.assertRaises(PermissionError):
                store.management_action({"action": "update_account_plan", "account_id": "owner", "actor_role": "ios_terminal"})

    def test_account_console_pairing_token_authenticates_account_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.create_account(account_id="acct-a")
            store.ensure_workspace("tenant-a", account_id="acct-a")
            token = store.create_pairing_token(account_id="acct-a", device_role="account_console")
            binding = store.bind_device({"pairing_token": token["token"], "console_id": "console-a"}, required_role="account_console")
            account_binding = store.authenticate_token(token["token"], required_role="account_console")

            allowed = store.management_action(
                {
                    "action": "get_account_usage",
                    "account_id": "acct-a",
                    "workspace_id": "tenant-a",
                    "actor_role": "account_console",
                }
            )
            with self.assertRaises(PermissionError):
                store.management_action(
                    {
                        "action": "set_account_status",
                        "account_id": "acct-a",
                        "status": "disabled",
                        "actor_role": "account_console",
                    }
                )

            self.assertEqual(binding["account_id"], "acct-a")
            self.assertEqual(account_binding["account_id"], "acct-a")
            self.assertEqual(allowed["account"]["account_id"], "acct-a")

    def test_snapshot_can_be_filtered_by_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.create_account(account_id="acct-a")
            store.create_account(account_id="acct-b")
            store.ensure_workspace("tenant-a", account_id="acct-a")
            store.ensure_workspace("tenant-b", account_id="acct-b")
            store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "a.txt", "text": "a"}]})
            store.record_artifact({"workspace_id": "tenant-b", "files": [{"name": "b.txt", "text": "b"}]})
            worker_pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="remote_worker")
            store.bind_device({"pairing_token": worker_pairing["token"], "worker_id": "worker-a"}, required_role="remote_worker")
            store.create_pairing_token(workspace_id="tenant-b", device_role="remote_worker")
            store.start_workflow_run(workspace_id="tenant-a")
            store.start_workflow_run(workspace_id="tenant-b")

            snapshot = store.snapshot(account_id="acct-a")

            self.assertEqual(snapshot["account_filter"], "acct-a")
            self.assertEqual([item["account_id"] for item in snapshot["accounts"]["items"]], ["acct-a"])
            self.assertEqual([item["workspace_id"] for item in snapshot["workspaces"]], ["tenant-a"])
            self.assertTrue(all(item["workspace_id"] == "tenant-a" for item in snapshot["artifacts"]["recent"]))
            self.assertTrue(all(item["workspace_id"] == "tenant-a" for item in snapshot["workflow_runs"]["recent"]))
            self.assertEqual(snapshot["workspace_devices"]["items"][0]["workspace_id"], "tenant-a")
            self.assertEqual(set(snapshot["artifacts"]["quota"]["workspaces"].keys()), {"tenant-a"})
            self.assertNotIn("tenant-b", json.dumps(snapshot, ensure_ascii=False))

    def test_artifact_ingest_snapshot_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            artifact = store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "source": "ios_terminal",
                    "purpose": "mobile_work_image",
                    "files": [
                        {
                            "name": "product.jpg",
                            "mime_type": "image/jpeg",
                            "base64": base64.b64encode(b"image-bytes").decode("ascii"),
                        }
                    ],
                }
            )

            path = store.state_dir / artifact["files"][0]["relative_path"]
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"image-bytes")
            snapshot = store.snapshot()
            self.assertEqual(snapshot["artifacts"]["count"], 1)
            self.assertEqual(snapshot["artifacts"]["total_size_bytes"], len(b"image-bytes"))

            state = store.load()
            state["artifacts"][artifact["artifact_id"]]["created_at"] = (
                datetime.now(UTC) - timedelta(hours=200)
            ).isoformat()
            store.save(state)

            result = store.cleanup_artifacts(older_than_hours=168)
            self.assertEqual(result["deleted"], [artifact["artifact_id"]])
            self.assertFalse(path.exists())
            self.assertEqual(store.load()["artifacts"][artifact["artifact_id"]]["status"], "deleted")

    def test_artifact_file_resolves_available_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            artifact = store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "files": [
                        {
                            "name": "product.png",
                            "mime_type": "image/png",
                            "base64": base64.b64encode(b"png-bytes").decode("ascii"),
                        }
                    ],
                }
            )

            resolved = store.artifact_file(artifact["artifact_id"], workspace_id="tenant-a")

            self.assertEqual(resolved["filename"], "product.png")
            self.assertEqual(resolved["mime_type"], "image/png")
            self.assertEqual(resolved["path"].read_bytes(), b"png-bytes")

    def test_mobile_link_can_be_listed_and_deleted_by_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            own = store.record_mobile_link(
                "https://mobile.yangkeduo.com/goods.html?goods_id=1",
                source="android-bridge",
                workspace_id="tenant-a",
                device_id="device-1",
            )
            store.record_mobile_link(
                "https://mobile.yangkeduo.com/goods.html?goods_id=2",
                source="android-bridge",
                workspace_id="tenant-a",
                device_id="device-2",
            )

            listed = store.list_mobile_links(workspace_id="tenant-a", device_id="device-1", source="android-bridge")
            deleted = store.delete_mobile_link(own["link_id"], workspace_id="tenant-a", device_id="device-1")
            after = store.list_mobile_links(workspace_id="tenant-a", device_id="device-1", source="android-bridge")

            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["links"][0]["link_id"], own["link_id"])
            self.assertEqual(deleted["link"]["status"], "deleted")
            self.assertEqual(after["count"], 0)

    def test_browser_extension_claims_only_pdd_web_links_and_records_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            web = store.record_mobile_link(
                "https://mobile.yangkeduo.com/goods.html?goods_id=1",
                source="android-bridge",
                workspace_id="tenant-a",
            )
            store.record_mobile_link(
                "https://example.com/not-pdd",
                source="android-bridge",
                workspace_id="tenant-a",
            )

            claimed = store.claim_mobile_links_for_extension(
                workspace_id="tenant-a",
                extension_id="edge-main",
            )
            result = store.record_mobile_link_extraction_result(
                web["link_id"],
                workspace_id="tenant-a",
                extension_id="edge-main",
                success=True,
                artifact_id="art_product_1",
                summary={"goods_id": "1", "sku_count": 3},
            )

            self.assertEqual(claimed["count"], 1)
            self.assertEqual(claimed["links"][0]["link_id"], web["link_id"])
            self.assertEqual(claimed["links"][0]["status"], "processing")
            self.assertEqual(result["link"]["status"], "completed")
            self.assertEqual(result["link"]["artifact_id"], "art_product_1")
            self.assertEqual(store.list_mobile_links(workspace_id="tenant-a", status="available")["count"], 1)

    def test_browser_extension_cannot_complete_another_extensions_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            web = store.record_mobile_link(
                "https://mobile.yangkeduo.com/goods.html?goods_id=2",
                source="android-bridge",
                workspace_id="tenant-a",
            )
            store.claim_mobile_links_for_extension(workspace_id="tenant-a", extension_id="edge-main")

            with self.assertRaises(PermissionError):
                store.record_mobile_link_extraction_result(
                    web["link_id"],
                    workspace_id="tenant-a",
                    extension_id="edge-other",
                    success=False,
                    error="not owner",
                )

    def test_browser_extension_can_requeue_failed_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            web = store.record_mobile_link(
                "https://mobile.yangkeduo.com/goods.html?goods_id=3",
                source="android-bridge",
                workspace_id="tenant-a",
            )
            store.claim_mobile_links_for_extension(workspace_id="tenant-a", extension_id="edge-main")
            store.record_mobile_link_extraction_result(
                web["link_id"],
                workspace_id="tenant-a",
                extension_id="edge-main",
                success=False,
                error="login required",
            )

            requeued = store.requeue_mobile_link_for_extension(
                web["link_id"],
                workspace_id="tenant-a",
                requested_by="edge-main",
            )

            self.assertEqual(requeued["link"]["status"], "available")
            self.assertNotIn("claimed_by", requeued["link"])

    def test_delete_artifact_file_removes_single_file_and_keeps_remaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            artifact = store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "files": [
                        {"name": "one.png", "mime_type": "image/png", "base64": base64.b64encode(b"one").decode("ascii")},
                        {"name": "two.png", "mime_type": "image/png", "base64": base64.b64encode(b"two").decode("ascii")},
                    ],
                }
            )
            first_path = store.state_dir / artifact["files"][0]["relative_path"]
            second_path = store.state_dir / artifact["files"][1]["relative_path"]

            result = store.delete_artifact_file(artifact["artifact_id"], file_index=0, workspace_id="tenant-a")
            refreshed = store.load()["artifacts"][artifact["artifact_id"]]

            self.assertEqual(result["remaining_files"], 1)
            self.assertFalse(first_path.exists())
            self.assertTrue(second_path.exists())
            self.assertEqual(refreshed["status"], "available")
            self.assertEqual(refreshed["files"][0]["name"], "two.png")
            self.assertEqual(refreshed["size_bytes"], 3)

    def test_delete_last_artifact_file_marks_artifact_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            artifact = store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "only.txt", "text": "x"}]})

            result = store.delete_artifact_file(artifact["artifact_id"], file_index=0, workspace_id="tenant-a")
            refreshed = store.load()["artifacts"][artifact["artifact_id"]]

            self.assertEqual(result["remaining_files"], 0)
            self.assertEqual(refreshed["status"], "deleted")
            self.assertEqual(refreshed["files"], [])

    def test_list_artifact_files_filters_android_device_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            own = store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "source": "android_bridge",
                    "device_id": "device-1",
                    "files": [
                        {"name": "one.jpg", "mime_type": "image/jpeg", "base64": base64.b64encode(b"one").decode("ascii")},
                        {"name": "two.jpg", "mime_type": "image/jpeg", "base64": base64.b64encode(b"two").decode("ascii")},
                    ],
                }
            )
            store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "source": "android_bridge",
                    "device_id": "device-2",
                    "files": [{"name": "other.jpg", "text": "other"}],
                }
            )
            store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "source": "ios_terminal",
                    "device_id": "device-1",
                    "files": [{"name": "ios.jpg", "text": "ios"}],
                }
            )

            result = store.list_artifact_files(workspace_id="tenant-a", device_id="device-1", source="android_bridge")

            self.assertEqual(result["count"], 1)
            self.assertEqual(result["file_count"], 2)
            self.assertEqual(result["artifacts"][0]["artifact_id"], own["artifact_id"])
            self.assertEqual([item["file_index"] for item in result["artifacts"][0]["files"]], [0, 1])

    def test_delete_artifact_file_rejects_wrong_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            artifact = store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "source": "android_bridge",
                    "device_id": "device-1",
                    "files": [{"name": "one.png", "mime_type": "image/png", "base64": base64.b64encode(b"one").decode("ascii")}],
                }
            )

            with self.assertRaises(KeyError):
                store.delete_artifact_file(artifact["artifact_id"], file_index=0, workspace_id="tenant-a", device_id="device-2")

            self.assertEqual(store.load()["artifacts"][artifact["artifact_id"]]["status"], "available")

    def test_artifact_policy_enforces_workspace_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {
                        "artifact_policy": {
                            "max_workspace_artifacts": 1,
                            "max_workspace_bytes": 10,
                            "max_file_bytes": 10,
                        }
                    },
                    "actor_role": "management",
                }
            )

            first = store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "a.txt", "text": "12345"}]})
            with self.assertRaises(PermissionError):
                store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "b.txt", "text": "1"}]})

            snapshot = store.snapshot(workspace_id="tenant-a")
            quota = snapshot["artifacts"]["quota"]
            self.assertEqual(first["quota"]["artifact_count_after"], 1)
            self.assertEqual(quota["artifact_count"], 1)
            self.assertEqual(quota["total_size_bytes"], 5)
            self.assertEqual(quota["max_workspace_artifacts"], 1)

    def test_artifact_policy_enforces_file_and_byte_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"artifact_policy": {"max_workspace_bytes": 5, "max_file_bytes": 4}},
                    "actor_role": "management",
                }
            )

            with self.assertRaises(ValueError):
                store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "too-large.txt", "text": "12345"}]})
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"artifact_policy": {"max_workspace_bytes": 5, "max_file_bytes": 10}},
                    "actor_role": "management",
                }
            )
            store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "a.txt", "text": "123"}]})
            with self.assertRaises(PermissionError):
                store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "b.txt", "text": "123"}]})

    def test_artifact_policy_can_use_filesystem_object_store_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            backend_root = Path(tmp) / "object-store"
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {
                        "artifact_policy": {
                            "backend": "filesystem_object_store",
                            "backend_root": str(backend_root),
                        }
                    },
                    "actor_role": "management",
                }
            )

            artifact = store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "remote.txt", "text": "object"}]})
            resolved = store.artifact_file(artifact["artifact_id"], workspace_id="tenant-a")

            self.assertEqual(artifact["backend"], "filesystem_object_store")
            self.assertTrue(str(resolved["path"]).startswith(str(backend_root.resolve())))
            self.assertEqual(resolved["path"].read_text(encoding="utf-8"), "object")

    def test_artifact_policy_can_use_s3_backend(self):
        class FakeS3Handler(BaseHTTPRequestHandler):
            objects: dict[str, bytes] = {}
            calls: list[dict[str, str]] = []

            def do_PUT(self):
                length = int(self.headers.get("content-length") or "0")
                data = self.rfile.read(length)
                self.__class__.objects[self.path] = data
                self.__class__.calls.append(
                    {"method": "PUT", "path": self.path, "authorization": self.headers.get("authorization") or ""}
                )
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                self.__class__.calls.append(
                    {"method": "GET", "path": self.path, "authorization": self.headers.get("authorization") or ""}
                )
                if self.path not in self.__class__.objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = self.__class__.objects[self.path]
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_HEAD(self):
                self.__class__.calls.append(
                    {"method": "HEAD", "path": self.path, "authorization": self.headers.get("authorization") or ""}
                )
                self.send_response(200 if self.path in self.__class__.objects else 404)
                self.end_headers()

            def do_DELETE(self):
                self.__class__.calls.append(
                    {"method": "DELETE", "path": self.path, "authorization": self.headers.get("authorization") or ""}
                )
                self.__class__.objects.pop(self.path, None)
                self.send_response(204)
                self.end_headers()

            def log_message(self, fmt, *args):
                return

        FakeS3Handler.objects = {}
        FakeS3Handler.calls = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = ControlPlaneStore(Path(tmp) / "control")
                old_access = os.environ.get("SPIRITKIN_TEST_S3_ACCESS")
                old_secret = os.environ.get("SPIRITKIN_TEST_S3_SECRET")
                old_allowlist = os.environ.get("SPIRITKIN_ARTIFACT_S3_ALLOWED_CREDENTIAL_ENVS")
                os.environ["SPIRITKIN_TEST_S3_ACCESS"] = "test-access"
                os.environ["SPIRITKIN_TEST_S3_SECRET"] = "test-secret"
                os.environ["SPIRITKIN_ARTIFACT_S3_ALLOWED_CREDENTIAL_ENVS"] = "SPIRITKIN_TEST_S3_ACCESS,SPIRITKIN_TEST_S3_SECRET"
                try:
                    store.management_action(
                        {
                            "action": "update_workspace_policy",
                            "workspace_id": "tenant-a",
                            "policy": {
                                "artifact_policy": {
                                    "backend": "s3",
                                    "s3_endpoint_url": endpoint,
                                    "s3_bucket": "spiritkin-artifacts",
                                    "s3_region": "us-east-1",
                                    "s3_prefix": "prod",
                                    "s3_access_key_env": "SPIRITKIN_TEST_S3_ACCESS",
                                    "s3_secret_key_env": "SPIRITKIN_TEST_S3_SECRET",
                                }
                            },
                            "actor_role": "management",
                        }
                    )
                    artifact = store.record_artifact(
                        {"workspace_id": "tenant-a", "files": [{"name": "s3.txt", "text": "cloud"}]}
                    )
                    resolved = store.artifact_file(artifact["artifact_id"], workspace_id="tenant-a")
                    self.assertEqual(resolved["path"].read_text(encoding="utf-8"), "cloud")
                    validation = store.validate_state(workspace_id="tenant-a")
                    cleanup = store.cleanup_artifacts(older_than_hours=0, workspace_id="tenant-a")
                finally:
                    if old_access is None:
                        os.environ.pop("SPIRITKIN_TEST_S3_ACCESS", None)
                    else:
                        os.environ["SPIRITKIN_TEST_S3_ACCESS"] = old_access
                    if old_secret is None:
                        os.environ.pop("SPIRITKIN_TEST_S3_SECRET", None)
                    else:
                        os.environ["SPIRITKIN_TEST_S3_SECRET"] = old_secret
                    if old_allowlist is None:
                        os.environ.pop("SPIRITKIN_ARTIFACT_S3_ALLOWED_CREDENTIAL_ENVS", None)
                    else:
                        os.environ["SPIRITKIN_ARTIFACT_S3_ALLOWED_CREDENTIAL_ENVS"] = old_allowlist

                self.assertEqual(artifact["backend"], "s3")
                self.assertEqual(artifact["bucket"], "spiritkin-artifacts")
                self.assertEqual(artifact["files"][0]["storage_key"].split("/")[:2], ["prod", "tenant-a"])
                self.assertTrue(validation["ok"])
                self.assertEqual(cleanup["deleted"], [artifact["artifact_id"]])
                self.assertEqual(FakeS3Handler.objects, {})
                self.assertFalse(resolved["path"].exists())
                methods = [item["method"] for item in FakeS3Handler.calls]
                self.assertIn("PUT", methods)
                self.assertIn("GET", methods)
                self.assertIn("HEAD", methods)
                self.assertIn("DELETE", methods)
                self.assertTrue(any(item["authorization"].startswith("AWS4-HMAC-SHA256") for item in FakeS3Handler.calls))
        finally:
            server.shutdown()
            server.server_close()

    def test_validate_state_reports_missing_artifact_file_and_orphan_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            artifact = store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "files": [{"name": "missing.txt", "text": "hello"}],
                }
            )
            started = store.start_workflow_run(workspace_id="tenant-a")
            state = store.load()
            artifact_path = store.state_dir / artifact["files"][0]["relative_path"]
            artifact_path.unlink()
            del state["workflow_runs"][started["run"]["run_id"]]
            store.save(state)

            validation = store.management_action({"action": "validate_state", "workspace_id": "tenant-a"})["validation"]

            self.assertTrue(validation["ok"])
            self.assertEqual(validation["schema_version"], STATE_VERSION)
            self.assertGreaterEqual(len(validation["migrations"]), 2)
            self.assertEqual(validation["counts"]["missing_artifact_files"], 1)
            self.assertEqual(validation["counts"]["orphan_worker_tasks"], 1)
            self.assertIn("missing artifact files: 1", validation["warnings"])

    def test_android_heartbeat_delivers_and_records_command_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            command = store.queue_android_command(
                operation="app.launch",
                params={"app_name": "PDD"},
                device_id="device-1",
            )

            first = store.android_heartbeat({"device_id": "device-1", "device_state": {"battery_pct": 90}})
            self.assertEqual(first["commands"][0]["command_id"], command["command_id"])
            self.assertEqual(store.load()["android_commands"][command["command_id"]]["status"], "delivered")

            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "command_results": [
                        {
                            "command_id": command["command_id"],
                            "status": "completed",
                            "success": True,
                            "message": "ok",
                            "result": {
                                "artifact_id": "art_snapshot_1",
                                "download_url": "http://control.test/android/artifact/art_snapshot_1",
                                "foreground_package": "com.xunmeng.pinduoduo",
                                "snapshot_chars": 1234,
                            },
                        }
                    ],
                }
            )

            saved = store.load()["android_commands"][command["command_id"]]
            self.assertEqual(saved["status"], "completed")
            self.assertTrue(saved["success"])
            self.assertEqual(saved["result"]["artifact_id"], "art_snapshot_1")
            self.assertEqual(saved["result"]["foreground_package"], "com.xunmeng.pinduoduo")
            log = store.action_log(workspace_id="local-ecommerce", action="android_command_result")
            self.assertEqual(log["count"], 1)
            self.assertEqual(log["items"][0]["target_id"], command["command_id"])
            self.assertEqual(log["items"][0]["payload"]["artifact_id"], "art_snapshot_1")

    def test_android_heartbeat_long_poll_returns_when_command_is_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            result: dict[str, object] = {}

            def heartbeat() -> None:
                result.update(
                    store.android_heartbeat(
                        {
                            "device_id": "device-1",
                            "workspace_id": "tenant-a",
                            "wait_seconds": 5,
                            "device_state": {"battery_pct": 90},
                        }
                    )
                )

            thread = threading.Thread(target=heartbeat)
            thread.start()
            threading.Event().wait(0.3)
            command = store.queue_android_command(
                operation="app.launch",
                params={"app_name": "PDD"},
                device_id="device-1",
                workspace_id="tenant-a",
            )
            thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertEqual(result["commands"][0]["command_id"], command["command_id"])
            self.assertEqual(store.load()["android_commands"][command["command_id"]]["status"], "delivered")

    def test_android_diagnostics_flags_accessibility_and_foreground_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "installed_apps": [{"name": "PDD", "package": "com.xunmeng.pinduoduo"}],
                    "device_state": {
                        "foreground_package": "com.example.browser",
                        "pdd_accessibility_granted": False,
                        "pdd_accessibility_connected": False,
                        "automation_modules": [
                            {"id": "core.command_sync", "status": "ready"},
                            {"id": "pdd.automation", "status": "needs_accessibility"},
                            {"id": "android.ui_snapshot", "status": "needs_accessibility"},
                        ],
                    },
                }
            )

            diagnostic = store.snapshot()["android"]["diagnostics"]["items"][0]

            self.assertEqual(diagnostic["status"], "blocked")
            codes = {item["code"] for item in diagnostic["issues"]}
            self.assertIn("accessibility.not_granted", codes)
            self.assertIn("foreground.not_pdd", codes)
            self.assertIn("ui_snapshot.needs_accessibility", codes)
            self.assertIn("android.open_accessibility_settings", {item["command"] for item in diagnostic["actions"]})

    def test_android_diagnostic_actions_include_device_and_capability_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "capabilities": ["heartbeat", "pdd.launch"],
                    "device_state": {
                        "foreground_package": "com.example.browser",
                        "pdd_accessibility_granted": False,
                        "automation_modules": [{"id": "core.command_sync", "status": "ready"}],
                    },
                }
            )

            snapshot = store.snapshot()
            device = snapshot["android"]["devices"][0]
            diagnostic = snapshot["android"]["diagnostics"]["items"][0]

            self.assertEqual(device["capabilities"], ["heartbeat", "pdd.launch"])
            actions = {item["command"]: item for item in diagnostic["actions"]}
            self.assertEqual(actions["pdd.launch"]["device_id"], "device-1")
            self.assertTrue(actions["pdd.launch"]["supported"])
            self.assertFalse(actions["android.open_accessibility_settings"]["supported"])

    def test_workspace_device_overview_groups_and_filters_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            android_pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="android_bridge")
            store.create_pairing_token(workspace_id="tenant-b", device_role="android_bridge")
            store.bind_device(
                {
                    "pairing_token": android_pairing["token"],
                    "device_id": "phone-a",
                    "device_label": "Phone A",
                },
                required_role="android_bridge",
            )
            store.android_heartbeat(
                {
                    "token": android_pairing["token"],
                    "device_id": "phone-a",
                    "device_state": {"foreground_package": "com.xunmeng.pinduoduo"},
                }
            )
            store.register_ios_terminal("ios-a", workspace_id="tenant-a")
            worker_pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="remote_worker")
            store.bind_device(
                {
                    "pairing_token": worker_pairing["token"],
                    "device_id": "worker-a",
                    "device_label": "Worker A",
                },
                required_role="remote_worker",
            )
            store.worker_heartbeat(
                {
                    "token": worker_pairing["token"],
                    "worker_id": "worker-a",
                    "capabilities": ["local.cli"],
                }
            )

            snapshot = store.snapshot(workspace_id="tenant-a")
            overview = snapshot["workspace_devices"]

            self.assertEqual(overview["count"], 1)
            item = overview["items"][0]
            self.assertEqual(item["workspace_id"], "tenant-a")
            self.assertEqual(item["counts"]["android"], 1)
            self.assertEqual(item["counts"]["ios_controllers"], 1)
            self.assertEqual(item["counts"]["remote_workers"], 1)
            self.assertEqual(item["counts"]["active_bindings"], 2)
            self.assertEqual(item["counts"]["pending_pairings"], 0)
            self.assertEqual(len(item["active_bindings"]), 2)
            self.assertEqual(item["active_bindings"][0]["workspace_id"], "tenant-a")
            self.assertEqual(item["android_devices"][0]["device_id"], "phone-a")
            self.assertEqual(item["ios_controllers"][0]["device_id"], "ios-a")
            self.assertEqual(item["remote_workers"][0]["device_id"], "worker-a")
            self.assertNotIn("tenant-b", json.dumps(overview))

    def test_workspace_overview_includes_pairing_and_binding_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pending = store.create_pairing_token(workspace_id="tenant-a", device_role="android_bridge")
            request = store.create_pairing_request(workspace_id="tenant-a", device_id="phone-a")
            binding_pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="android_bridge")
            binding = store.bind_device(
                {"pairing_token": binding_pairing["token"], "device_id": "phone-a"},
                required_role="android_bridge",
            )
            store.revoke_device_binding(binding["token_id"], workspace_id="tenant-a")

            snapshot = store.snapshot(workspace_id="tenant-a")
            item = snapshot["workspace_devices"]["items"][0]

            self.assertEqual(item["pending_pairings"][0]["token_id"], pending["token_id"])
            self.assertEqual(item["pairing_requests"][0]["request_id"], request["request_id"])
            self.assertGreaterEqual(item["counts"]["pairing_history"], 1)
            self.assertEqual(item["binding_history"][0]["status"], "revoked")

    def test_android_diagnostics_suggests_snapshot_after_failed_pdd_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            command = store.queue_android_command(
                operation="pdd.create_listing",
                device_id="device-1",
                params={"artifact_id": "art_1", "title": "shirt"},
            )
            store.android_heartbeat({"device_id": "device-1"})
            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "command_results": [
                        {
                            "command_id": command["command_id"],
                            "status": "failed",
                            "success": False,
                            "message": "title field not found",
                            "result": {},
                        }
                    ],
                }
            )

            diagnostic = store.snapshot()["android"]["diagnostics"]["items"][0]

            self.assertEqual(diagnostic["status"], "warning")
            issues = {item["code"]: item for item in diagnostic["issues"]}
            self.assertIn("command.failed", issues)
            self.assertEqual(issues["command.failed"]["failure_class"], "selector_or_foreground")
            actions = {(item["kind"], item["command"]): item for item in diagnostic["actions"]}
            self.assertIn(("queue_command", "android.ui_snapshot"), actions)
            retry = actions[("retry_command", "pdd.create_listing")]
            self.assertEqual(retry["source_command_id"], command["command_id"])
            self.assertEqual(retry["params"]["artifact_id"], "art_1")
            self.assertEqual(retry["operation"], "pdd.create_listing")

    def test_targeted_android_command_is_delivered_only_to_matching_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            command = store.queue_android_command(operation="pdd.launch", device_id="device-2")

            first = store.android_heartbeat({"device_id": "device-1"})
            second = store.android_heartbeat({"device_id": "device-2"})

            self.assertEqual(first["commands"], [])
            self.assertEqual(second["commands"][0]["command_id"], command["command_id"])

    def test_action_log_filters_by_workspace_action_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            first = store.queue_android_command(
                operation="app.launch",
                params={"app_name": "PDD"},
                workspace_id="tenant-a",
                requested_by="tester",
            )
            store.queue_android_command(
                operation="clipboard.write",
                params={"text": "hello"},
                workspace_id="tenant-b",
                requested_by="tester",
            )

            log = store.action_log(workspace_id="tenant-a", action="android_command_queued", status="recorded")

            self.assertEqual(log["count"], 1)
            self.assertEqual(log["items"][0]["target_id"], first["command_id"])
            self.assertEqual(log["items"][0]["actor"], "tester")
            self.assertEqual(store.snapshot()["action_log"]["count"], 2)

    def test_workspace_policy_blocks_denied_android_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            result = store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {
                        "android_allowed_operations": ["pdd.launch"],
                        "android_denied_operations": ["clipboard.write"],
                    },
                    "requested_by": "tester",
                }
            )

            self.assertIn("pdd.launch", result["policy"]["android_allowed_operations"])
            store.queue_android_command(operation="pdd.launch", workspace_id="tenant-a")
            with self.assertRaises(PermissionError):
                store.queue_android_command(operation="clipboard.write", workspace_id="tenant-a")
            with self.assertRaises(PermissionError):
                store.queue_android_command(operation="app.launch", workspace_id="tenant-a")

    def test_workspace_policy_blocks_unlisted_control_action_for_scoped_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            result = store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"control_allowed_actions": ["snapshot", "start_workflow_run"]},
                    "actor_role": "management",
                }
            )

            started = store.management_action(
                {
                    "action": "start_workflow_run",
                    "workspace_id": "tenant-a",
                    "requested_by": "ios-1",
                    "actor_role": "ios_terminal",
                }
            )
            with self.assertRaises(PermissionError):
                store.management_action(
                    {
                        "action": "queue_android_command",
                        "workspace_id": "tenant-a",
                        "operation": "pdd.launch",
                        "requested_by": "ios-1",
                        "actor_role": "ios_terminal",
                    }
                )
            queued = store.management_action(
                {
                    "action": "queue_android_command",
                    "workspace_id": "tenant-a",
                    "operation": "pdd.launch",
                    "actor_role": "management",
                }
            )

            self.assertEqual(result["policy"]["control_allowed_actions"], ["snapshot", "start_workflow_run"])
            self.assertEqual(started["workflow"]["run"]["workspace_id"], "tenant-a")
            self.assertEqual(queued["command"]["operation"], "pdd.launch")

    def test_workspace_policy_denies_control_action_for_scoped_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            result = store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"control_denied_actions": ["queue_android_command"]},
                    "actor_role": "management",
                }
            )

            with self.assertRaises(PermissionError):
                store.management_action(
                    {
                        "action": "queue_android_command",
                        "workspace_id": "tenant-a",
                        "operation": "pdd.launch",
                        "requested_by": "ios-1",
                        "actor_role": "ios_terminal",
                    }
                )
            queued = store.management_action(
                {
                    "action": "queue_android_command",
                    "workspace_id": "tenant-a",
                    "operation": "pdd.launch",
                    "actor_role": "management",
                }
            )

            self.assertEqual(result["policy"]["control_denied_actions"], ["queue_android_command"])
            self.assertEqual(queued["command"]["operation"], "pdd.launch")

    def test_workspace_policy_blocks_unlisted_workflow_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            state = store.load()
            state["workflow_templates"]["custom.workflow.v1"] = {
                **state["workflow_templates"]["ecommerce.auto_listing.v1"],
                "template_id": "custom.workflow.v1",
            }
            store.save(state)
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"workflow_allowed_templates": ["ecommerce.auto_listing.v1"]},
                }
            )

            store.start_workflow_run(workspace_id="tenant-a", template_id="ecommerce.auto_listing.v1")
            with self.assertRaises(PermissionError):
                store.start_workflow_run(workspace_id="tenant-a", template_id="custom.workflow.v1")

    def test_builtin_worker_adapter_templates_are_available_and_assignable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            templates = {item["template_id"]: item for item in store.snapshot()["workflow_templates"]}

            self.assertIn("local.cli.run.v1", templates)
            self.assertIn("langgraph.run.v1", templates)
            self.assertIn("crewai.run.v1", templates)

            started = store.start_workflow_run(
                template_id="local.cli.run.v1",
                inputs={"command": ["python", "-c", "print('ok')"]},
            )
            heartbeat = store.worker_heartbeat({"worker_id": "worker-cli", "capabilities": ["local.cli"]})

            self.assertEqual(heartbeat["tasks"][0]["task_id"], started["worker_task"]["task_id"])
            self.assertEqual(heartbeat["tasks"][0]["operation"], "local.cli.run")

    def test_workspace_policy_can_disable_builtin_adapter_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"workflow_allowed_templates": ["ecommerce.auto_listing.v1"]},
                }
            )

            with self.assertRaises(PermissionError):
                store.start_workflow_run(workspace_id="tenant-a", template_id="langgraph.run.v1")

    def test_workspace_runtime_profile_updates_and_attaches_to_worker_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            result = store.management_action(
                {
                    "action": "update_runtime_profile",
                    "workspace_id": "tenant-a",
                    "runtime_profile": {
                        "workspace_root": "state/workspaces/tenant-a",
                        "venv_path": "state/workspaces/tenant-a/.venv",
                        "dependency_files": ["requirements.lock"],
                        "dependency_policy": "locked",
                        "allowed_local_commands": ["python", "node"],
                        "forbidden_paths": ["E:/AutoProcessAP"],
                    },
                    "requested_by": "tester",
                }
            )
            started = store.start_workflow_run(workspace_id="tenant-a")

            profile = result["runtime_profile"]
            self.assertEqual(profile["dependency_policy"], "locked")
            self.assertEqual(profile["allowed_local_commands"], ["node", "python"])
            self.assertEqual(started["worker_task"]["runtime_profile"]["venv_path"], "state/workspaces/tenant-a/.venv")
            self.assertEqual(started["run"]["runtime_profile"]["dependency_files"], ["requirements.lock"])
            log = store.action_log(workspace_id="tenant-a", action="workspace_runtime_profile_updated")
            self.assertEqual(log["count"], 1)

    def test_android_command_preflight_blocks_device_workspace_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "workspace_id": "tenant-b",
                    "capabilities": ["heartbeat", "pdd.launch"],
                    "installed_apps": [{"name": "PDD", "package": "com.xunmeng.pinduoduo"}],
                }
            )

            with self.assertRaises(PermissionError) as ctx:
                store.queue_android_command(operation="pdd.launch", device_id="device-1", workspace_id="tenant-a")

            self.assertIn("target device belongs to workspace tenant-b", str(ctx.exception))

    def test_android_command_preflight_blocks_missing_capability_and_accessibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "workspace_id": "tenant-a",
                    "capabilities": ["heartbeat", "pdd.launch"],
                    "installed_apps": [{"name": "PDD", "package": "com.xunmeng.pinduoduo"}],
                    "device_state": {
                        "pdd_accessibility_granted": False,
                        "pdd_accessibility_connected": False,
                    },
                }
            )

            with self.assertRaises(PermissionError) as ctx:
                store.queue_android_command(operation="pdd.create_listing", device_id="device-1", workspace_id="tenant-a")

            message = str(ctx.exception)
            self.assertIn("device missing capability: pdd.create_listing", message)
            self.assertIn("手机无障碍未开启", message)

    def test_android_command_preflight_records_broadcast_risk_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")

            command = store.queue_android_command(operation="pdd.create_listing", params={"title": "shirt"})
            catalog = {item["operation"]: item for item in store.snapshot()["android_command_catalog"]}

            self.assertEqual(command["preflight"]["risk"], "critical")
            self.assertEqual(command["preflight"]["status"], "warning")
            self.assertIn("broadcast command", command["preflight"]["warnings"][0])
            self.assertEqual(catalog["pdd.create_listing"]["risk"], "critical")
            self.assertEqual(catalog["android.screenshot.capture"]["risk"], "high")

    def test_android_heartbeat_records_reported_command_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "command_catalog": [
                        {
                            "operation": "pdd.create_listing",
                            "risk": "critical",
                            "required_capabilities": ["pdd.create_listing"],
                            "requires_accessibility": True,
                            "requires_artifact": False,
                            "required_packages": ["com.xunmeng.pinduoduo"],
                        }
                    ],
                }
            )

            device = store.snapshot()["android"]["devices"][0]

            self.assertEqual(device["command_catalog"][0]["operation"], "pdd.create_listing")
            self.assertTrue(device["command_catalog"][0]["requires_accessibility"])

    def test_android_screenshot_capture_requires_permission_when_targeted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.android_heartbeat(
                {
                    "device_id": "device-1",
                    "workspace_id": "tenant-a",
                    "capabilities": ["android.screenshot.capture"],
                    "device_state": {"screen_capture_authorized": False},
                }
            )

            with self.assertRaises(PermissionError) as ctx:
                store.queue_android_command(operation="android.screenshot.capture", device_id="device-1", workspace_id="tenant-a")

            self.assertIn("screen capture permission", str(ctx.exception))

    def test_pairing_token_binds_android_device_and_authenticates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")

            binding = store.bind_device(
                {
                    "pairing_token": pairing["token"],
                    "device_id": "android-1",
                    "device_state": {"model": "test"},
                }
            )

            self.assertEqual(binding["workspace_id"], "tenant-a")
            self.assertEqual(binding["device_id"], "android-1")
            self.assertEqual(binding["expires_at"], pairing["expires_at"])
            self.assertEqual(store.load()["pairing_tokens"][pairing["token_id"]]["status"], "bound")
            authenticated = store.authenticate_token(pairing["token"], required_role="android_bridge")
            self.assertIsNotNone(authenticated)
            self.assertEqual(authenticated["workspace_id"], "tenant-a")

            heartbeat = store.android_heartbeat(
                {
                    "token": pairing["token"],
                    "device_id": "ignored-device",
                    "workspace_id": "ignored-workspace",
                    "device_state": {"battery_pct": 55},
                }
            )

            self.assertEqual(heartbeat["device_id"], "android-1")
            self.assertIn("android-1", store.load()["android_devices"])

    def test_pending_pairing_token_can_be_cancelled_by_management_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")

            result = store.management_action(
                {
                    "action": "cancel_pairing_token",
                    "workspace_id": "tenant-a",
                    "token_id": pairing["token_id"],
                }
            )
            snapshot = store.snapshot(workspace_id="tenant-a")

            self.assertEqual(result["pairing"]["status"], "cancelled")
            self.assertEqual(store.load()["pairing_tokens"][pairing["token_id"]]["status"], "cancelled")
            self.assertEqual(snapshot["pairings"]["pending_count"], 0)

    def test_pairing_token_record_can_be_deleted_by_management_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")
            store.management_action(
                {
                    "action": "cancel_pairing_token",
                    "workspace_id": "tenant-a",
                    "token_id": pairing["token_id"],
                }
            )

            result = store.management_action(
                {
                    "action": "delete_pairing_token",
                    "workspace_id": "tenant-a",
                    "token_id": pairing["token_id"],
                }
            )

            self.assertTrue(result["deleted"])
            self.assertNotIn(pairing["token_id"], store.load()["pairing_tokens"])

    def test_legacy_android_policy_allows_base_device_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            state = store.load()
            state["workspaces"]["tenant-a"] = {
                "workspace_id": "tenant-a",
                "name": "Tenant A",
                "execution_policy": {
                    "android_allowed_operations": ["app.launch"],
                },
            }
            store.save(state)

            for operation in (
                "device.status",
                "list_installed_apps",
                "android.open_accessibility_settings",
                "android.open_bridge",
                "android.screenshot.request_permission",
                "android.screenshot.capture",
                "artifact.cache.status",
                "artifact.cache.cleanup",
            ):
                command = store.queue_android_command(
                    operation=operation,
                    workspace_id="tenant-a",
                    device_id="android-1",
                )
                self.assertEqual(command["operation"], operation)

            with self.assertRaises(PermissionError):
                store.queue_android_command(
                    operation="clipboard.write",
                    workspace_id="tenant-a",
                    device_id="android-1",
                )

    def test_ios_terminal_history_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.register_ios_terminal("ios-old", workspace_id="tenant-a")
            store.register_ios_terminal("ios-new", workspace_id="tenant-a")
            state = store.load()
            state["ios_terminals"]["ios-old"]["last_seen_at"] = "2026-06-01T00:00:00+00:00"
            state["ios_terminals"]["ios-new"]["last_seen_at"] = "2026-06-24T00:00:00+00:00"
            store.save(state)

            result = store.management_action(
                {
                    "action": "clear_ios_terminal_history",
                    "workspace_id": "tenant-a",
                    "keep_latest": 1,
                }
            )

            saved = store.load()["ios_terminals"]
            self.assertEqual(result["deleted_count"], 1)
            self.assertNotIn("ios-old", saved)
            self.assertIn("ios-new", saved)

    def test_android_pairing_request_requires_management_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            request = store.create_pairing_request(workspace_id="tenant-a", device_id="android-1")
            self.assertEqual(request["status"], "requested")
            self.assertEqual(request["token"], "")

            approved = store.management_action(
                {
                    "action": "approve_pairing_request",
                    "workspace_id": "tenant-a",
                    "request_id": request["request_id"],
                    "ttl_minutes": 60,
                }
            )["pairing"]

            self.assertEqual(approved["status"], "pending")
            self.assertTrue(approved["token"])
            binding = store.bind_device({"pairing_token": approved["token"], "device_id": "android-1"})
            self.assertEqual(binding["workspace_id"], "tenant-a")
            self.assertEqual(store.load()["pairing_tokens"][request["request_id"]]["status"], "bound")

    def test_android_pairing_request_can_be_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            request = store.create_pairing_request(workspace_id="tenant-a", device_id="android-1")

            result = store.management_action(
                {
                    "action": "reject_pairing_request",
                    "workspace_id": "tenant-a",
                    "request_id": request["request_id"],
                }
            )

            self.assertEqual(result["pairing"]["status"], "rejected")
            self.assertEqual(store.pairing_request_status(request["request_id"])["status"], "rejected")

    def test_android_pairing_request_recovers_same_device_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="android_bridge")
            binding = store.bind_device(
                {"pairing_token": pairing["token"], "device_id": "android-1"},
                required_role="android_bridge",
            )

            request = store.create_pairing_request(workspace_id="tenant-a", device_id="android-1")
            snapshot = store.snapshot(workspace_id="tenant-a")

            self.assertIn(binding["token_id"], request["recoverable_binding_tokens"])
            self.assertEqual(snapshot["pairings"]["bound_count"], 0)
            self.assertEqual(snapshot["workspace_devices"]["items"][0]["counts"]["active_bindings"], 0)
            self.assertEqual(snapshot["workspace_devices"]["items"][0]["binding_history"][0]["status"], "needs_rebind")
            self.assertIsNotNone(store.authenticate_token(pairing["token"], required_role="android_bridge"))

            approved = store.approve_pairing_request(request["request_id"], workspace_id="tenant-a", ttl_minutes=60)
            self.assertEqual(approved["token"], pairing["token"])
            self.assertEqual(approved["expires_at"], pairing["expires_at"])
            rebound = store.bind_device(
                {"pairing_token": approved["token"], "device_id": "android-1"},
                required_role="android_bridge",
            )
            after = store.snapshot(workspace_id="tenant-a")

            self.assertEqual(rebound["token_id"], binding["token_id"])
            self.assertEqual(after["pairings"]["bound_count"], 1)
            self.assertEqual(after["workspace_devices"]["items"][0]["counts"]["active_bindings"], 1)
            self.assertIsNotNone(store.authenticate_token(pairing["token"], required_role="android_bridge"))

    def test_android_heartbeat_recovers_needs_rebind_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="android_bridge")
            store.bind_device(
                {"pairing_token": pairing["token"], "device_id": "android-1"},
                required_role="android_bridge",
            )
            request = store.create_pairing_request(workspace_id="tenant-a", device_id="android-1")
            self.assertEqual(store.load()["device_bindings"][pairing["token"]]["status"], "needs_rebind")

            heartbeat = store.android_heartbeat(
                {
                    "token": pairing["token"],
                    "device_id": "android-1",
                    "workspace_id": "tenant-a",
                    "device_state": {"battery_pct": 80},
                }
            )

            self.assertEqual(heartbeat["device_id"], "android-1")
            self.assertEqual(store.load()["device_bindings"][pairing["token"]]["status"], "active")
            self.assertEqual(store.load()["pairing_tokens"][pairing["token_id"]]["status"], "bound")
            self.assertEqual(store.pairing_request_status(request["request_id"])["status"], "requested")

    def test_android_pairing_request_generates_new_token_when_old_binding_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="android_bridge")
            store.bind_device(
                {"pairing_token": pairing["token"], "device_id": "android-1"},
                required_role="android_bridge",
            )
            state = store.load()
            state["pairing_tokens"][pairing["token_id"]]["expires_at"] = "2026-01-01T00:00:00+00:00"
            state["device_bindings"][pairing["token"]]["expires_at"] = "2026-01-01T00:00:00+00:00"
            store.save(state)

            request = store.create_pairing_request(workspace_id="tenant-a", device_id="android-1")
            approved = store.approve_pairing_request(request["request_id"], workspace_id="tenant-a", ttl_minutes=60)

            self.assertNotEqual(approved["token"], pairing["token"])
            self.assertEqual(store.load()["pairing_tokens"][pairing["token_id"]]["status"], "expired")

    def test_pairing_history_can_be_cleared_without_pending_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pending = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")
            used = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")
            store.bind_device({"pairing_token": used["token"], "device_id": "android-1"})

            snapshot = store.snapshot(workspace_id="tenant-a")
            self.assertEqual(len(snapshot["pairings"]["recent_pending"]), 1)
            self.assertEqual(len(snapshot["pairings"]["recent_history"]), 1)

            result = store.management_action(
                {
                    "action": "clear_pairing_history",
                    "workspace_id": "tenant-a",
                }
            )
            saved = store.load()["pairing_tokens"]

            self.assertEqual(result["deleted_count"], 1)
            self.assertIn(pending["token_id"], saved)
            self.assertNotIn(used["token_id"], saved)

    def test_bound_device_can_be_revoked_by_management_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")
            store.bind_device(
                {
                    "pairing_token": pairing["token"],
                    "device_id": "android-1",
                }
            )

            result = store.management_action(
                {
                    "action": "revoke_device_binding",
                    "workspace_id": "tenant-a",
                    "token_id": pairing["token_id"],
                }
            )
            snapshot = store.snapshot(workspace_id="tenant-a")

            self.assertEqual(result["binding"]["status"], "revoked")
            self.assertEqual(store.load()["pairing_tokens"][pairing["token_id"]]["status"], "revoked")
            self.assertIsNone(store.authenticate_token(pairing["token"], required_role="android_bridge"))
            self.assertEqual(snapshot["pairings"]["bound_count"], 0)

    def test_rebinding_same_device_replaces_old_binding_and_history_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            first = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")
            second = store.create_pairing_token(workspace_id="tenant-a", server_url="http://control.test")
            store.bind_device({"pairing_token": first["token"], "device_id": "android-1"})
            store.bind_device({"pairing_token": second["token"], "device_id": "android-1"})

            snapshot = store.snapshot(workspace_id="tenant-a")
            self.assertEqual(snapshot["pairings"]["bound_count"], 1)
            self.assertEqual(len(snapshot["pairings"]["bindings"]), 1)
            self.assertEqual(len(snapshot["pairings"]["binding_history"]), 1)
            self.assertEqual(snapshot["pairings"]["binding_history"][0]["status"], "replaced")
            self.assertIsNone(store.authenticate_token(first["token"], required_role="android_bridge"))
            self.assertIsNotNone(store.authenticate_token(second["token"], required_role="android_bridge"))

            result = store.management_action(
                {
                    "action": "clear_binding_history",
                    "workspace_id": "tenant-a",
                }
            )
            cleaned = store.snapshot(workspace_id="tenant-a")

            self.assertEqual(result["deleted_binding_count"], 1)
            self.assertEqual(cleaned["pairings"]["bound_count"], 1)
            self.assertEqual(cleaned["pairings"]["binding_history"], [])

    def test_remote_worker_pairing_token_scopes_heartbeat_and_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="remote_worker")
            binding = store.bind_device(
                {
                    "pairing_token": pairing["token"],
                    "worker_id": "worker-paired",
                    "workspace_id": "spoofed-workspace",
                },
                required_role="remote_worker",
            )
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"artifact_ids": ["art_1"]})

            heartbeat = store.worker_heartbeat(
                {
                    "token": pairing["token"],
                    "worker_id": "spoofed-worker",
                    "workspace_id": "spoofed-workspace",
                    "capabilities": ["ecommerce.auto_listing"],
                }
            )

            self.assertEqual(binding["worker_id"], "worker-paired")
            self.assertEqual(heartbeat["worker_id"], "worker-paired")
            self.assertEqual(heartbeat["tasks"][0]["task_id"], started["worker_task"]["task_id"])
            saved_task = store.load()["worker_tasks"][started["worker_task"]["task_id"]]
            self.assertEqual(saved_task["worker_id"], "worker-paired")

            result = store.worker_result(
                {
                    "token": pairing["token"],
                    "worker_id": "spoofed-worker",
                    "task_id": started["worker_task"]["task_id"],
                    "status": "completed",
                    "result": {"ok": True},
                }
            )

            self.assertTrue(result["ok"])
            self.assertEqual(store.load()["workflow_runs"][started["run"]["run_id"]]["status"], "completed")

    def test_sensitive_payloads_are_rejected_from_worker_android_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"artifact_ids": ["art_1"]})
            task_id = started["worker_task"]["task_id"]
            store.worker_heartbeat({"worker_id": "worker-1", "workspace_id": "tenant-a", "capabilities": ["ecommerce.auto_listing"]})

            with self.assertRaises(ValueError) as worker_ctx:
                store.worker_result(
                    {
                        "task_id": task_id,
                        "worker_id": "worker-1",
                        "status": "completed",
                        "result": {"ok": True, "productData": {"title": "item", "cookies": "secret"}},
                    }
                )
            self.assertIn("productData.cookies", str(worker_ctx.exception))

            with self.assertRaises(ValueError) as android_ctx:
                store.android_heartbeat({"workspace_id": "tenant-a", "device_id": "phone-1", "device_state": {"session_cookie": "secret"}})
            self.assertIn("device_state.session_cookie", str(android_ctx.exception))

            with self.assertRaises(ValueError) as artifact_ctx:
                store.record_artifact(
                    {
                        "workspace_id": "tenant-a",
                        "files": [{"name": "item.txt", "text": "safe"}],
                        "metadata": {"profile_path": "C:/Users/me/AppData/Chrome"},
                    }
                )
            self.assertIn("metadata.profile_path", str(artifact_ctx.exception))

    def test_ios_terminal_pairing_token_authenticates_and_scopes_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="ios_terminal")
            binding = store.bind_device(
                {
                    "pairing_token": pairing["token"],
                    "terminal_id": "ios-1",
                    "workspace_id": "spoofed-workspace",
                },
                required_role="ios_terminal",
            )
            store.record_artifact({"workspace_id": "tenant-a", "files": [{"name": "a.txt", "text": "a"}]})
            store.record_artifact({"workspace_id": "tenant-b", "files": [{"name": "b.txt", "text": "b"}]})

            authenticated = store.authenticate_token(pairing["token"], required_role="ios_terminal")
            tenant_a = store.snapshot(workspace_id="tenant-a")
            tenant_b = store.snapshot(workspace_id="tenant-b")

            self.assertEqual(binding["terminal_id"], "ios-1")
            self.assertEqual(binding["workspace_id"], "tenant-a")
            self.assertIsNotNone(authenticated)
            self.assertEqual(authenticated["workspace_id"], "tenant-a")
            self.assertEqual(tenant_a["workspace_filter"], "tenant-a")
            self.assertEqual(tenant_a["artifacts"]["count"], 1)
            self.assertEqual(tenant_a["pairings"]["ios_terminal_bound_count"], 1)
            self.assertEqual(tenant_b["artifacts"]["count"], 1)
            self.assertEqual(tenant_b["pairings"]["ios_terminal_bound_count"], 0)

    def test_android_pairing_token_cannot_bind_as_remote_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            pairing = store.create_pairing_token(workspace_id="tenant-a", device_role="android_bridge")

            with self.assertRaises(PermissionError):
                store.bind_device(
                    {
                        "pairing_token": pairing["token"],
                        "worker_id": "worker-1",
                    },
                    required_role="remote_worker",
                )

    def test_workflow_run_assigns_to_remote_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            started = store.start_workflow_run(inputs={"artifact_ids": ["art_1"]})
            task_id = started["worker_task"]["task_id"]

            heartbeat = store.worker_heartbeat(
                {"worker_id": "worker-1", "capabilities": ["ecommerce.auto_listing", "android.bridge"]}
            )

            self.assertEqual(heartbeat["tasks"][0]["task_id"], task_id)
            saved_task = store.load()["worker_tasks"][task_id]
            self.assertEqual(saved_task["status"], "assigned")
            self.assertEqual(saved_task["worker_id"], "worker-1")

            result = store.worker_result({"task_id": task_id, "status": "completed", "result": {"ok": True}})
            self.assertTrue(result["ok"])
            self.assertEqual(store.load()["workflow_runs"][started["run"]["run_id"]]["status"], "completed")

    def test_worker_capability_policy_blocks_task_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"worker_allowed_capabilities": ["android.bridge"]},
                }
            )
            started = store.start_workflow_run(workspace_id="tenant-a")

            heartbeat = store.worker_heartbeat(
                {"worker_id": "worker-1", "workspace_id": "tenant-a", "capabilities": ["ecommerce.auto_listing"]}
            )

            self.assertEqual(heartbeat["tasks"], [])
            self.assertEqual(store.load()["worker_tasks"][started["worker_task"]["task_id"]]["status"], "queued")
            log = store.action_log(workspace_id="tenant-a", action="worker_task_claim_skipped")
            self.assertEqual(log["count"], 1)
            self.assertIn("does not authorize", log["items"][0]["payload"]["reason"])

    def test_expired_worker_task_lease_requeues_for_another_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"budget": {"max_runtime_seconds": 1, "max_retries": 1}})
            task_id = started["worker_task"]["task_id"]
            first = store.worker_heartbeat(
                {"worker_id": "worker-1", "workspace_id": "tenant-a", "capabilities": ["ecommerce.auto_listing"]}
            )
            self.assertEqual(first["tasks"][0]["task_id"], task_id)

            state = store.load()
            state["worker_tasks"][task_id]["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
            store.save(state)

            second = store.worker_heartbeat(
                {"worker_id": "worker-2", "workspace_id": "tenant-a", "capabilities": ["ecommerce.auto_listing"]}
            )
            saved = store.load()["worker_tasks"][task_id]

            self.assertEqual(second["reclaimed_tasks"], [task_id])
            self.assertEqual(second["tasks"][0]["task_id"], task_id)
            self.assertEqual(saved["status"], "assigned")
            self.assertEqual(saved["worker_id"], "worker-2")
            self.assertEqual(saved["attempt"], 2)
            self.assertEqual(saved["attempt_history"][0]["worker_id"], "worker-1")
            log = store.action_log(workspace_id="tenant-a", action="worker_task_reclaimed")
            self.assertEqual(log["count"], 1)
            self.assertEqual(log["items"][0]["payload"]["status"], "requeued")

    def test_expired_worker_task_lease_fails_after_retry_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"budget": {"max_runtime_seconds": 1, "max_retries": 0}})
            task_id = started["worker_task"]["task_id"]
            store.worker_heartbeat({"worker_id": "worker-1", "workspace_id": "tenant-a", "capabilities": ["ecommerce.auto_listing"]})
            state = store.load()
            state["worker_tasks"][task_id]["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
            store.save(state)

            cleanup = store.cleanup_state(workspace_id="tenant-a")
            saved = store.load()

            self.assertEqual(cleanup["reclaimed_worker_tasks"], [task_id])
            self.assertEqual(saved["worker_tasks"][task_id]["status"], "failed")
            self.assertEqual(saved["workflow_runs"][started["run"]["run_id"]]["status"], "failed")
            self.assertEqual(saved["worker_tasks"][task_id]["failure_reason"], "worker_task_lease_expired")

    def test_workflow_promote_gate_requires_approval_for_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.management_action(
                {
                    "action": "update_workspace_policy",
                    "workspace_id": "tenant-a",
                    "policy": {"require_promote_gate": True},
                }
            )

            with self.assertRaises(PermissionError):
                store.start_workflow_run(workspace_id="tenant-a", inputs={"promote_mode": "production"})

            approval = store.management_action(
                {
                    "action": "approve_workflow_promotion",
                    "workspace_id": "tenant-a",
                    "template_id": "ecommerce.auto_listing.v1",
                    "requested_by": "tester",
                }
            )
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"promote_mode": "production"})

            self.assertIn("ecommerce.auto_listing.v1", approval["policy"]["approved_promotions"])
            self.assertFalse(started["run"]["governance"]["dry_run"])
            self.assertTrue(started["run"]["governance"]["promotion_approved"])

    def test_worker_result_blocks_budget_excess_and_dry_run_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            over_budget = store.start_workflow_run(inputs={"budget": {"max_runtime_seconds": 5}})
            task_id = over_budget["worker_task"]["task_id"]
            store.worker_heartbeat({"worker_id": "worker-1", "capabilities": ["ecommerce.auto_listing"]})

            with self.assertRaises(PermissionError) as budget_ctx:
                store.worker_result(
                    {
                        "task_id": task_id,
                        "worker_id": "worker-1",
                        "status": "completed",
                        "result": {"usage": {"runtime_seconds": 6}},
                    }
                )
            self.assertIn("runtime_seconds 6>5", str(budget_ctx.exception))

            dry_run = store.start_workflow_run(inputs={"promote_mode": "dry_run"})
            dry_task_id = dry_run["worker_task"]["task_id"]
            store.worker_heartbeat({"worker_id": "worker-1", "capabilities": ["ecommerce.auto_listing"]})
            with self.assertRaises(PermissionError):
                store.worker_result(
                    {
                        "task_id": dry_task_id,
                        "worker_id": "worker-1",
                        "status": "completed",
                        "result": {"published": True, "usage": {"runtime_seconds": 1}},
                    }
                )

    def test_cancel_workflow_run_cancels_worker_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"artifact_ids": ["art_1"]})
            run_id = started["run"]["run_id"]
            task_id = started["worker_task"]["task_id"]

            result = store.management_action(
                {
                    "action": "cancel_workflow_run",
                    "workspace_id": "tenant-a",
                    "run_id": run_id,
                    "requested_by": "tester",
                }
            )

            self.assertEqual(result["workflow"]["run"]["status"], "cancelled")
            saved = store.load()
            self.assertEqual(saved["worker_tasks"][task_id]["status"], "cancelled")
            log = store.action_log(workspace_id="tenant-a", action="workflow_run_cancelled")
            self.assertEqual(log["count"], 1)
            self.assertEqual(log["items"][0]["target_id"], run_id)

    def test_retry_workflow_run_creates_new_run_with_same_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"artifact_ids": ["art_1"]})
            run_id = started["run"]["run_id"]

            result = store.management_action(
                {
                    "action": "retry_workflow_run",
                    "workspace_id": "tenant-a",
                    "run_id": run_id,
                    "requested_by": "tester",
                }
            )

            retry = result["workflow"]["run"]
            self.assertNotEqual(retry["run_id"], run_id)
            self.assertEqual(retry["inputs"], {"artifact_ids": ["art_1"]})
            self.assertEqual(retry["retry_of"], run_id)
            saved = store.load()
            self.assertEqual(saved["workflow_runs"][run_id]["retried_by_run_id"], retry["run_id"])
            log = store.action_log(workspace_id="tenant-a", action="workflow_run_retried")
            self.assertEqual(log["count"], 1)
            self.assertEqual(log["items"][0]["payload"]["new_run_id"], retry["run_id"])

    def test_delete_workflow_run_removes_worker_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            started = store.start_workflow_run(workspace_id="tenant-a", inputs={"artifact_ids": ["art_1"]})
            run_id = started["run"]["run_id"]
            task_id = started["worker_task"]["task_id"]

            result = store.management_action(
                {
                    "action": "delete_workflow_run",
                    "workspace_id": "tenant-a",
                    "run_id": run_id,
                    "requested_by": "tester",
                }
            )

            self.assertEqual(result["workflow"]["run_id"], run_id)
            saved = store.load()
            self.assertNotIn(run_id, saved["workflow_runs"])
            self.assertNotIn(task_id, saved["worker_tasks"])
            log = store.action_log(workspace_id="tenant-a", action="workflow_run_deleted")
            self.assertEqual(log["count"], 1)

    def test_clear_workflow_runs_keeps_active_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            active = store.start_workflow_run(workspace_id="tenant-a")
            terminal = store.start_workflow_run(workspace_id="tenant-a")
            other = store.start_workflow_run(workspace_id="tenant-b")
            store.cancel_workflow_run(terminal["run"]["run_id"], workspace_id="tenant-a")
            store.cancel_workflow_run(other["run"]["run_id"], workspace_id="tenant-b")

            result = store.management_action(
                {
                    "action": "clear_workflow_runs",
                    "workspace_id": "tenant-a",
                    "requested_by": "tester",
                }
            )

            saved = store.load()
            self.assertEqual(result["workflow"]["deleted_run_count"], 1)
            self.assertIn(active["run"]["run_id"], saved["workflow_runs"])
            self.assertNotIn(terminal["run"]["run_id"], saved["workflow_runs"])
            self.assertIn(other["run"]["run_id"], saved["workflow_runs"])

    def test_cleanup_state_expires_tokens_marks_offline_and_prunes_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            old = (datetime.now(UTC) - timedelta(hours=200)).isoformat()
            artifact = store.record_artifact(
                {
                    "workspace_id": "tenant-a",
                    "files": [{"name": "old.txt", "text": "expired"}],
                }
            )
            pairing = store.create_pairing_token(workspace_id="tenant-a", ttl_minutes=-1)
            command = store.queue_android_command(
                operation="app.launch",
                workspace_id="tenant-a",
                params={"app_name": "PDD"},
            )
            store.worker_heartbeat({"worker_id": "worker-1", "workspace_id": "tenant-a"})
            store.android_heartbeat({"device_id": "android-1", "workspace_id": "tenant-a"})
            store.register_ios_terminal("ios-1", workspace_id="tenant-a")

            state = store.load()
            state["artifacts"][artifact["artifact_id"]]["created_at"] = old
            state["android_commands"][command["command_id"]]["status"] = "completed"
            state["android_commands"][command["command_id"]]["completed_at"] = old
            state["remote_workers"]["worker-1"]["last_seen_at"] = old
            state["android_devices"]["android-1"]["last_seen_at"] = old
            state["ios_terminals"]["ios-1"]["last_seen_at"] = old
            store.save(state)

            cleanup = store.cleanup_state(older_than_hours=168, workspace_id="tenant-a")
            saved = store.load()

            self.assertIn(artifact["artifact_id"], cleanup["artifact_cleanup"]["deleted"])
            self.assertIn(pairing["token_id"], cleanup["expired_pairing_tokens"])
            self.assertIn(command["command_id"], cleanup["pruned_android_commands"])
            self.assertIn("worker-1", cleanup["offline_workers"])
            self.assertIn("android-1", cleanup["offline_android_devices"])
            self.assertIn("ios-1", cleanup["offline_ios_terminals"])
            self.assertEqual(saved["pairing_tokens"][pairing["token_id"]]["status"], "expired")
            self.assertNotIn(command["command_id"], saved["android_commands"])
            self.assertEqual(saved["remote_workers"]["worker-1"]["status"], "offline")
            self.assertEqual(saved["android_devices"]["android-1"]["status"], "offline")
            self.assertEqual(saved["ios_terminals"]["ios-1"]["status"], "offline")
            self.assertEqual(saved["artifacts"][artifact["artifact_id"]]["status"], "deleted")

    def test_device_workflow_controls_are_workspace_scoped_and_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            store.ensure_workspace("tenant-a", "Tenant A")
            store.android_heartbeat({"device_id": "android-1", "workspace_id": "tenant-a"})
            initial = store.snapshot(workspace_id="tenant-a")["workspace_devices"]["items"][0]["android_devices"][0]
            self.assertEqual(initial["workflow_controls"], [])

            added = store.management_action(
                {
                    "action": "add_device_workflow",
                    "workspace_id": "tenant-a",
                    "device_id": "android-1",
                    "workflow_id": "ecommerce.auto_listing.v1",
                }
            )
            self.assertTrue(added["control"]["enabled"])

            paused = store.management_action(
                {
                    "action": "set_device_workflow_state",
                    "workspace_id": "tenant-a",
                    "device_id": "android-1",
                    "workflow_id": "ecommerce.auto_listing.v1",
                    "enabled": False,
                    "reason": "验收暂停",
                }
            )
            snapshot = store.snapshot(workspace_id="tenant-a")
            android = snapshot["workspace_devices"]["items"][0]["android_devices"][0]
            controls = android["workflow_controls"]

            self.assertFalse(paused["control"]["enabled"])
            self.assertEqual(controls[0]["status"], "paused")
            self.assertEqual(controls[0]["reason"], "验收暂停")
            with self.assertRaises(PermissionError):
                store.start_workflow_run(
                    workspace_id="tenant-a",
                    template_id="ecommerce.auto_listing.v1",
                    inputs={"device_id": "android-1"},
                )

            enabled = store.management_action(
                {
                    "action": "set_device_workflow_state",
                    "workspace_id": "tenant-a",
                    "device_id": "android-1",
                    "workflow_id": "ecommerce.auto_listing.v1",
                    "enabled": True,
                }
            )
            run = store.start_workflow_run(
                workspace_id="tenant-a",
                template_id="ecommerce.auto_listing.v1",
                inputs={"device_id": "android-1"},
            )

            self.assertTrue(enabled["control"]["enabled"])
            self.assertEqual(run["run"]["workspace_id"], "tenant-a")

            deleted = store.management_action(
                {
                    "action": "delete_device_workflow",
                    "workspace_id": "tenant-a",
                    "device_id": "android-1",
                    "workflow_id": "ecommerce.auto_listing.v1",
                }
            )
            after_delete = store.snapshot(workspace_id="tenant-a")["workspace_devices"]["items"][0]["android_devices"][0]
            self.assertTrue(deleted["removed"])
            self.assertEqual(after_delete["workflow_controls"], [])

    def test_clear_android_commands_is_workspace_and_device_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            first = store.queue_android_command(
                operation="device.status",
                workspace_id="tenant-a",
                device_id="phone-a",
            )
            second = store.queue_android_command(
                operation="device.status",
                workspace_id="tenant-a",
                device_id="phone-b",
            )
            third = store.queue_android_command(
                operation="device.status",
                workspace_id="tenant-b",
                device_id="phone-a",
            )

            result = store.management_action(
                {
                    "action": "clear_android_commands",
                    "workspace_id": "tenant-a",
                    "device_id": "phone-a",
                }
            )
            saved = store.load()["android_commands"]

            self.assertEqual(result["removed"], 1)
            self.assertNotIn(first["command_id"], saved)
            self.assertIn(second["command_id"], saved)
            self.assertIn(third["command_id"], saved)


if __name__ == "__main__":
    unittest.main()
