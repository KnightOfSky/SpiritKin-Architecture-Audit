import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import control_plane_worker as worker


class ControlPlaneWorkerTests(unittest.TestCase):
    def test_heartbeat_payload_declares_identity_and_capabilities(self):
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["ecommerce.auto_listing"],
        )

        payload = worker.heartbeat_payload(config)

        self.assertEqual(payload["worker_id"], "worker-1")
        self.assertEqual(payload["workspace_id"], "tenant-a")
        self.assertEqual(payload["capabilities"], ["ecommerce.auto_listing"])
        self.assertFalse(payload["state"]["allow_production"])

    def test_heartbeat_payload_includes_bound_worker_token(self):
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["ecommerce.auto_listing"],
            token="worker-token",
        )

        payload = worker.heartbeat_payload(config)

        self.assertEqual(payload["token"], "worker-token")

    def test_pair_worker_binds_with_pairing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                pairing_token="pair-token",
                state_dir=str(Path(tmp) / "worker-state"),
            )

            with patch.object(worker, "post_json") as post_json, patch("builtins.print"):
                post_json.return_value = {"ok": True, "binding": {"worker_id": "worker-1", "workspace_id": "tenant-a"}}
                token = worker.pair_worker(config)

            self.assertEqual(token, "pair-token")
            post_json.assert_called_once()
            args, kwargs = post_json.call_args
            self.assertEqual(args[1], "/worker/pair")
            self.assertEqual(args[2]["pairing_token"], "pair-token")
            self.assertEqual(args[2]["worker_id"], "worker-1")
            self.assertEqual(kwargs["token"], "")

    def test_pair_worker_persists_bound_token_runtime_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                pairing_token="pair-token",
                state_dir=str(Path(tmp) / "worker-state"),
            )

            with patch.object(worker, "post_json") as post_json, patch("builtins.print"):
                post_json.return_value = {
                    "ok": True,
                    "binding": {"worker_id": "worker-bound", "workspace_id": "tenant-a", "device_role": "remote_worker"},
                }
                token = worker.pair_worker(config)

            state = worker.load_runtime_state(config)
            self.assertEqual(token, "pair-token")
            self.assertEqual(state["token"], "pair-token")
            self.assertEqual(state["worker_id"], "worker-bound")
            self.assertEqual(state["workspace_id"], "tenant-a")
            self.assertTrue(worker.worker_runtime_state_file(config).exists())

    def test_config_from_sources_loads_file_and_runtime_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "worker-state"
            config_file = Path(tmp) / "worker.json"
            config_file.write_text(
                json.dumps(
                    {
                        "server_url": "http://control.test",
                        "worker_id": "worker-1",
                        "workspace_id": "tenant-a",
                        "capabilities": ["local.cli"],
                        "state_dir": str(state_dir),
                    }
                ),
                encoding="utf-8",
            )
            base = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["local.cli"],
                state_dir=str(state_dir),
            )
            worker.save_runtime_state(base, {"token": "bound-token", "worker_id": "worker-bound", "workspace_id": "tenant-a"})
            args = worker.parse_args(["--config", str(config_file)])

            config = worker.config_from_sources(args)

            self.assertEqual(config.token, "bound-token")
            self.assertEqual(config.worker_id, "worker-bound")
            self.assertEqual(config.workspace_id, "tenant-a")
            self.assertEqual(config.capabilities, ["local.cli"])

    def test_config_from_sources_loads_account_and_local_proxy_with_cli_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "worker.json"
            config_file.write_text(
                json.dumps(
                    {
                        "server_url": "http://control.test",
                        "worker_id": "worker-1",
                        "workspace_id": "tenant-a",
                        "account_id": "acct-file",
                        "local_proxy": {"http_proxy": "http://file-proxy:8080"},
                    }
                ),
                encoding="utf-8",
            )

            file_config = worker.config_from_sources(worker.parse_args(["--config", str(config_file)]))
            override_config = worker.config_from_sources(
                worker.parse_args(["--config", str(config_file), "--account-id", "acct-cli", "--proxy-url", "http://cli-proxy:8080"])
            )

            self.assertEqual(file_config.account_id, "acct-file")
            self.assertEqual(file_config.proxy_url, "http://file-proxy:8080")
            self.assertEqual(override_config.account_id, "acct-cli")
            self.assertEqual(override_config.proxy_url, "http://cli-proxy:8080")

    def test_default_outbox_uses_worker_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                state_dir=str(Path(tmp) / "worker-state"),
            )

            self.assertEqual(worker.worker_outbox_dir(config), Path(tmp).resolve() / "worker-state" / "outbox")

    def test_config_to_json_includes_update_settings(self):
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["ecommerce.auto_listing"],
            update_manifest_url="http://control.test/worker/package/manifest",
            auto_update=True,
            update_install_dir="C:/SpiritKinWorker",
        )

        data = worker.config_to_json(config)

        self.assertEqual(data["update_manifest_url"], "http://control.test/worker/package/manifest")
        self.assertTrue(data["auto_update"])
        self.assertEqual(data["update_install_dir"], "C:/SpiritKinWorker")

    def test_config_to_json_includes_account_and_local_proxy_but_heartbeat_does_not_leak_proxy_url(self):
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["local.cli"],
            account_id="acct-a",
            proxy_url="http://127.0.0.1:7890",
        )

        data = worker.config_to_json(config)
        heartbeat = worker.heartbeat_payload(config)

        self.assertEqual(data["account_id"], "acct-a")
        self.assertEqual(data["local_proxy"]["http_proxy"], "http://127.0.0.1:7890")
        self.assertEqual(data["local_proxy"]["https_proxy"], "http://127.0.0.1:7890")
        self.assertEqual(heartbeat["state"]["account_id"], "acct-a")
        self.assertTrue(heartbeat["state"]["proxy_configured"])
        self.assertNotIn("127.0.0.1:7890", json.dumps(heartbeat, ensure_ascii=False))

    def test_worker_release_manifest_lists_files_and_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "scripts" / "control_plane_worker.py"
            doc = root / "docs" / "worker.md"
            script.parent.mkdir(parents=True)
            doc.parent.mkdir(parents=True)
            script.write_text("print('worker')\n", encoding="utf-8")
            doc.write_text("worker docs\n", encoding="utf-8")

            manifest = worker.worker_release_manifest(
                version="1.2.3",
                base_dir=root,
                files=["scripts/control_plane_worker.py", "docs/worker.md"],
                signing_secret="secret",
            )

            self.assertEqual(manifest["package"], "spiritkin-control-plane-worker")
            self.assertEqual(manifest["version"], "1.2.3")
            self.assertEqual(len(manifest["files"]), 2)
            self.assertRegex(manifest["files"][0]["sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(manifest["integrity"]["file_count"], 2)
            self.assertEqual(manifest["signature"]["algorithm"], "hmac-sha256")
            self.assertRegex(manifest["signature"]["value"], r"^[a-f0-9]{64}$")

    def test_cli_writes_worker_release_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "worker-release.json"

            with patch.dict(worker.os.environ, {"SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET": "secret"}), patch(
                "builtins.print"
            ):
                result = worker.main(["--release-manifest", str(manifest_path)])

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(result, 0)
            self.assertEqual(manifest["manifest_version"], worker.MANIFEST_VERSION)
            self.assertEqual(manifest["version"], worker.WORKER_VERSION)
            self.assertIn("signature", manifest)

    def test_build_worker_package_zip_contains_manifest_and_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_path = Path(tmp) / "worker.zip"

            built = worker.build_worker_package(str(package_path), signing_secret="secret")

            self.assertEqual(built, package_path)
            with zipfile.ZipFile(package_path) as archive:
                names = set(archive.namelist())
                self.assertIn("scripts/control_plane_worker.py", names)
                self.assertIn("worker.example.json", names)
                self.assertIn("run-worker.cmd", names)
                self.assertIn("setup-worker.ps1", names)
                self.assertIn("update-worker.ps1", names)
                self.assertIn("install-worker-gui.ps1", names)
                self.assertIn("install-worker-scheduled-task.ps1", names)
                self.assertIn("worker-release-manifest.json", names)
                manifest = json.loads(archive.read("worker-release-manifest.json").decode("utf-8"))
                listed = {item["path"]: item for item in manifest["files"]}
                script_bytes = archive.read("scripts/control_plane_worker.py")
                example = json.loads(archive.read("worker.example.json").decode("utf-8"))
                setup_script = archive.read("setup-worker.ps1").decode("utf-8")
                update_script = archive.read("update-worker.ps1").decode("utf-8")
                gui_script = archive.read("install-worker-gui.ps1").decode("utf-8")
                run_cmd = archive.read("run-worker.cmd").decode("utf-8")
                scheduled_task_script = archive.read("install-worker-scheduled-task.ps1").decode("utf-8")

            self.assertEqual(manifest["package_format"], "zip")
            self.assertEqual(manifest["signature"]["algorithm"], "hmac-sha256")
            self.assertEqual(listed["scripts/control_plane_worker.py"]["sha256"], worker.hashlib.sha256(script_bytes).hexdigest())
            self.assertIn("setup-worker.ps1", listed)
            self.assertIn("update-worker.ps1", listed)
            self.assertIn("install-worker-gui.ps1", listed)
            self.assertIn("state/workers/worker-1", example["state_dir"])
            self.assertIn("account_id", example)
            self.assertIn("local_proxy", example)
            self.assertIn("/worker/package/manifest", example["update_manifest_url"])
            self.assertIn("--write-config", setup_script)
            self.assertIn("--pairing-token", setup_script)
            self.assertIn("--account-id", setup_script)
            self.assertIn("--proxy-url", setup_script)
            self.assertIn("--update-manifest-url", setup_script)
            self.assertIn("--update-install-dir", setup_script)
            self.assertIn("--auto-update", setup_script)
            self.assertIn("Get-FileHash -Algorithm SHA256", update_script)
            self.assertIn("Expand-Archive", update_script)
            self.assertIn("System.Windows.Forms", gui_script)
            self.assertIn("setup-worker.ps1", gui_script)
            self.assertIn("Account ID", gui_script)
            self.assertIn("Local proxy URL", gui_script)
            self.assertIn("Local-only credentials", gui_script)
            self.assertIn("-AccountId", gui_script)
            self.assertIn("-ProxyUrl", gui_script)
            self.assertIn("Register Scheduled Task", gui_script)
            self.assertIn("spiritkin-control-plane-worker.exe", run_cmd)
            self.assertIn("spiritkin-control-plane-worker.exe", setup_script)
            self.assertIn("spiritkin-control-plane-worker.exe", scheduled_task_script)

    def test_build_worker_package_can_include_pyinstaller_onefile_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_path = Path(tmp) / "worker.zip"
            executable = Path(tmp) / "spiritkin-control-plane-worker.exe"
            executable.write_bytes(b"MZ-fake-worker-binary")

            worker.build_worker_package(
                str(package_path),
                signing_secret="secret",
                worker_executable=executable,
            )

            with zipfile.ZipFile(package_path) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("worker-release-manifest.json").decode("utf-8"))
                binary = archive.read("spiritkin-control-plane-worker.exe")

        self.assertIn("spiritkin-control-plane-worker.exe", names)
        self.assertEqual(binary, b"MZ-fake-worker-binary")
        self.assertTrue(manifest["entrypoint"].startswith("spiritkin-control-plane-worker.exe"))
        listed = {item["path"]: item for item in manifest["files"]}
        self.assertEqual(
            listed["spiritkin-control-plane-worker.exe"]["sha256"],
            worker.hashlib.sha256(binary).hexdigest(),
        )

    def test_cli_builds_worker_package_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_path = Path(tmp) / "worker.zip"

            with patch.dict(worker.os.environ, {"SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET": "secret"}), patch(
                "builtins.print"
            ):
                result = worker.main(["--package-zip", str(package_path)])

            self.assertEqual(result, 0)
            self.assertTrue(package_path.exists())

    def test_check_update_skips_current_version(self):
        config = worker.WorkerConfig(
            server_url="",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["ecommerce.auto_listing"],
            update_manifest_url="http://control.test/worker/package/manifest",
        )

        with patch.object(worker, "get_json") as get_json:
            get_json.return_value = {
                "worker_package": {
                    "package": "spiritkin-control-plane-worker",
                    "version": worker.WORKER_VERSION,
                    "download_url": "http://control.test/worker/package",
                }
            }
            result = worker.check_and_apply_update(config)

        self.assertTrue(result["checked"])
        self.assertFalse(result["updated"])

    def test_check_update_downloads_verifies_and_extracts_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_dir = root / "install"
            package = root / "worker.zip"
            source = root / "source"
            source.mkdir()
            (source / "worker.example.json").write_text('{"ok": true}\n', encoding="utf-8")
            with zipfile.ZipFile(package, "w") as archive:
                archive.write(source / "worker.example.json", "worker.example.json")
            digest = worker.sha256_file(package)
            config = worker.WorkerConfig(
                server_url="",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                state_dir=str(root / "state"),
                update_manifest_url="http://control.test/worker/package/manifest",
            )

            with patch.object(worker, "get_json") as get_json, patch.object(worker, "download_file") as download_file:
                get_json.return_value = {
                    "worker_package": {
                        "package": "spiritkin-control-plane-worker",
                        "version": "9999.1",
                        "download_url": "http://control.test/worker/package",
                        "download_file": "worker.zip",
                        "sha256": digest,
                    }
                }
                download_file.side_effect = lambda url, target, timeout=60.0: target.write_bytes(package.read_bytes()) or target
                result = worker.check_and_apply_update(config, install_dir=install_dir)

            self.assertTrue(result["updated"])
            self.assertEqual((install_dir / "worker.example.json").read_text(encoding="utf-8"), '{"ok": true}\n')
            state = worker.load_runtime_state(config)
            self.assertEqual(state["last_update_from_version"], worker.WORKER_VERSION)
            self.assertEqual(state["last_update_to_version"], "9999.1")

    def test_extract_worker_package_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "worker.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../outside.txt", "bad")

            with self.assertRaises(ValueError):
                worker.extract_worker_package(package, Path(tmp) / "install")

    def test_run_loop_auto_update_exits_after_applying_update(self):
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["ecommerce.auto_listing"],
            update_manifest_url="http://control.test/worker/package/manifest",
            auto_update=True,
        )

        with patch.object(worker, "check_and_apply_update") as check_update, patch.object(worker, "run_once") as run_once, patch(
            "builtins.print"
        ):
            check_update.return_value = {"checked": True, "updated": True, "latest_version": "9999.1"}
            worker.run_loop(config)

        run_once.assert_not_called()

    def test_cli_check_update_runs_without_server(self):
        with patch.object(worker, "check_and_apply_update") as check_update, patch("builtins.print"):
            check_update.return_value = {"checked": True, "updated": False}
            result = worker.main(
                [
                    "--update-manifest-url",
                    "http://control.test/worker/package/manifest",
                    "--check-update",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(check_update.call_args.args[0].update_manifest_url, "http://control.test/worker/package/manifest")

    def test_run_loop_exits_after_consecutive_error_budget(self):
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["ecommerce.auto_listing"],
            max_consecutive_errors=2,
            error_backoff_seconds=0.5,
        )

        with patch.object(worker, "run_once", side_effect=RuntimeError("network down")) as run_once, patch.object(
            worker.time, "sleep"
        ) as sleep, patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                worker.run_loop(config)

        self.assertEqual(run_once.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_spool_and_flush_outbox_posts_then_deletes_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                token="worker-token",
                outbox_dir=str(Path(tmp) / "outbox"),
            )
            payload = {"task_id": "task-1", "worker_id": "worker-1", "status": "completed", "result": {"ok": True}}
            path = worker.spool_result(worker.worker_outbox_dir(config), payload)

            with patch.object(worker, "post_json") as post_json:
                post_json.return_value = {"ok": True, "task": {"task_id": "task-1"}}
                flushed = worker.flush_outbox(config)

            self.assertFalse(path.exists())
            self.assertEqual(flushed[0]["ok"], True)
            post_json.assert_called_once()
            args, kwargs = post_json.call_args
            self.assertEqual(args[1], "/worker/result")
            self.assertEqual(args[2]["task_id"], "task-1")
            self.assertEqual(kwargs["token"], "worker-token")

    def test_worker_result_contract_redacts_sensitive_fields_before_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            payload = {
                "task_id": "task-1",
                "worker_id": "worker-1",
                "status": "completed",
                "result": {
                    "ok": True,
                    "productData": {"title": "item", "cookies": "secret-cookie"},
                    "artifact_ids": ["art-1"],
                    "profile_path": "C:/Users/me/AppData/Chrome",
                    "access_token": "cloud-token",
                    "unknown_debug": {"password": "pw"},
                    "usage": {"runtime_seconds": 1},
                },
            }

            path = worker.spool_result(outbox_dir, payload)
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(saved["result"]["productData"], {"title": "item"})
            self.assertEqual(saved["result"]["artifact_ids"], ["art-1"])
            self.assertEqual(saved["result"]["usage"]["runtime_seconds"], 1)
            self.assertNotIn("profile_path", saved["result"])
            self.assertNotIn("access_token", saved["result"])
            self.assertNotIn("unknown_debug", saved["result"])
            self.assertIn("productData.cookies", saved["result"]["redacted_sensitive_keys"])
            self.assertIn("profile_path", saved["result"]["redacted_sensitive_keys"])
            self.assertIn("access_token", saved["result"]["redacted_sensitive_keys"])

    def test_flush_outbox_keeps_file_on_post_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                outbox_dir=str(Path(tmp) / "outbox"),
            )
            path = worker.spool_result(worker.worker_outbox_dir(config), {"task_id": "task-1"})

            with patch.object(worker, "post_json", side_effect=RuntimeError("network down")):
                flushed = worker.flush_outbox(config)

            self.assertTrue(path.exists())
            self.assertFalse(flushed[0]["ok"])
            self.assertIn("network down", flushed[0]["message"])

    def test_run_once_spools_result_before_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                outbox_dir=str(Path(tmp) / "outbox"),
            )
            calls = []

            def fake_post(server_url, path, payload, *, token="", timeout=15.0):
                calls.append((path, payload))
                if path == "/worker/heartbeat":
                    return {
                        "ok": True,
                        "worker_id": "worker-1",
                        "tasks": [
                            {
                                "task_id": "task-1",
                                "worker_id": "worker-1",
                                "operation": "workflow.execute.auto_listing",
                                "inputs": {},
                                "governance": {"promote_mode": "dry_run"},
                            }
                        ],
                    }
                if path == "/desktop/workflows":
                    return {
                        "ok": True,
                        "action_result": {
                            "data": {
                                "run": {
                                    "run_id": "wfr_auto_listing_dry",
                                    "workflow_name": "ecommerce.auto_listing.v1",
                                }
                            }
                        },
                    }
                return {"ok": True}

            with patch.object(worker, "post_json", side_effect=fake_post):
                result = worker.run_once(config)

            self.assertEqual(result["results"][0]["task_id"], "task-1")
            self.assertEqual(worker.pending_outbox_files(worker.worker_outbox_dir(config)), [])
            self.assertEqual([item[0] for item in calls], ["/worker/heartbeat", "/desktop/workflows", "/worker/result"])
            self.assertEqual(calls[-1][1]["result"]["workflow_run_id"], "wfr_auto_listing_dry")

    def test_run_once_production_auto_listing_starts_graph_run_before_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = worker.WorkerConfig(
                server_url="http://control.test",
                worker_id="worker-1",
                workspace_id="tenant-a",
                capabilities=["ecommerce.auto_listing"],
                token="worker-token",
                allow_production=True,
                outbox_dir=str(Path(tmp) / "outbox"),
            )
            calls = []

            def fake_post(server_url, path, payload, *, token="", timeout=15.0):
                calls.append((path, payload, token))
                if path == "/worker/heartbeat":
                    return {
                        "ok": True,
                        "worker_id": "worker-1",
                        "tasks": [
                            {
                                "task_id": "task-1",
                                "worker_id": "worker-1",
                                "workspace_id": "tenant-a",
                                "operation": "workflow.execute.auto_listing",
                                "inputs": {"artifact_ids": ["art_1"]},
                                "governance": {"promote_mode": "production", "dry_run": False},
                            }
                        ],
                    }
                if path == "/desktop/workflows":
                    return {
                        "ok": True,
                        "action_result": {
                            "data": {
                                "run": {
                                    "run_id": "wfr_auto_listing",
                                    "workflow_name": "ecommerce.auto_listing.v1",
                                }
                            }
                        }
                    }
                return {"ok": True}

            with patch.object(worker, "post_json", side_effect=fake_post):
                worker.run_once(config)

            paths = [item[0] for item in calls]
            self.assertEqual(paths, ["/worker/heartbeat", "/desktop/workflows", "/worker/result"])
            result_payload = calls[-1][1]["result"]
            self.assertEqual(result_payload["side_effects"], ["workflow_graph_run"])
            self.assertEqual(result_payload["workflow_run_id"], "wfr_auto_listing")
            self.assertEqual(calls[1][1]["workflow_name"], "ecommerce.auto_listing.v1")
            self.assertEqual(calls[1][1]["inputs"]["artifact_ids"], ["art_1"])
            self.assertEqual(calls[1][2], "worker-token")

    def test_default_capabilities_include_adapter_entrypoints(self):
        self.assertIn("local.cli", worker.DEFAULT_CAPABILITIES)
        self.assertIn("langgraph.run", worker.DEFAULT_CAPABILITIES)
        self.assertIn("crewai.run", worker.DEFAULT_CAPABILITIES)

    def test_run_local_command_uses_local_proxy_environment(self):
        task = {
            "task_id": "task-1",
            "inputs": {},
            "runtime_profile": {
                "workspace_root": str(Path.cwd()),
                "allowed_local_commands": ["python"],
            },
            "budget": {"max_runtime_seconds": 5},
        }
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["local.cli"],
            proxy_url="http://127.0.0.1:7890",
        )

        with patch.object(worker.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ok"
            run.return_value.stderr = ""
            result = worker.run_local_command(task, ["python", "-V"], config=config)

        self.assertTrue(result["ok"])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(env["SPIRITKIN_WORKSPACE_ROOT"], str(Path.cwd()))

    def test_auto_listing_dry_run_plans_android_steps_without_publish_side_effects(self):
        task = {
            "task_id": "task-1",
            "operation": "workflow.execute.auto_listing",
            "inputs": {"artifact_ids": ["art_1"]},
            "governance": {"promote_mode": "dry_run", "dry_run": True},
        }

        result = worker.execute_task(task)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["result"]["dry_run"])
        self.assertEqual(result["result"]["side_effects"], [])
        self.assertEqual(result["result"]["usage"]["android_commands"], 2)

    def test_auto_listing_production_starts_graph_run_when_enabled(self):
        task = {
            "task_id": "task-1",
            "worker_id": "worker-1",
            "workspace_id": "tenant-a",
            "operation": "workflow.execute.auto_listing",
            "inputs": {"artifact_ids": ["art_1"], "target_device_id": "android-1"},
            "governance": {"promote_mode": "production", "dry_run": False},
        }
        config = worker.WorkerConfig(
            server_url="http://control.test",
            worker_id="worker-1",
            workspace_id="tenant-a",
            capabilities=["ecommerce.auto_listing"],
            token="worker-token",
        )

        def fake_post(server_url, path, payload, *, token="", timeout=15.0):
            self.assertEqual(path, "/desktop/workflows")
            return {
                "ok": True,
                "action_result": {
                    "data": {
                        "run": {
                            "run_id": "wfr_auto_listing",
                            "workflow_name": "ecommerce.auto_listing.v1",
                        }
                    }
                }
            }

        with patch.object(worker, "post_json", side_effect=fake_post) as post_json:
            result = worker.execute_task(task, config=config, allow_production=True)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["side_effects"], ["workflow_graph_run"])
        self.assertEqual(result["result"]["workflow_run_id"], "wfr_auto_listing")
        self.assertEqual(post_json.call_count, 1)
        first_payload = post_json.call_args_list[0].args[2]
        self.assertEqual(first_payload["workflow_name"], "ecommerce.auto_listing.v1")
        self.assertEqual(first_payload["inputs"]["workspace_id"], "tenant-a")
        self.assertEqual(first_payload["inputs"]["target_device_id"], "android-1")
        self.assertEqual(first_payload["inputs"]["artifact_ids"], ["art_1"])
        self.assertEqual(post_json.call_args_list[0].kwargs["token"], "worker-token")

    def test_auto_listing_production_is_disabled_by_default(self):
        task = {
            "task_id": "task-1",
            "operation": "workflow.execute.auto_listing",
            "inputs": {"artifact_ids": ["art_1"]},
            "governance": {"promote_mode": "production", "dry_run": False},
        }

        result = worker.execute_task(task)

        self.assertEqual(result["status"], "failed")
        self.assertIn("production execution disabled", result["result"]["error"])

    def test_unknown_operation_fails_closed(self):
        result = worker.execute_task({"task_id": "task-1", "operation": "unknown.operation"})

        self.assertEqual(result["status"], "failed")
        self.assertIn("unsupported operation", result["result"]["error"])

    def test_cli_task_requires_explicit_enablement(self):
        task = {"task_id": "task-1", "operation": "local.cli.run", "inputs": {"command": [sys.executable, "-c", "print('ok')"]}}

        result = worker.execute_task(task)

        self.assertEqual(result["status"], "failed")
        self.assertIn("CLI execution disabled", result["result"]["error"])

    def test_cli_task_runs_allowed_command_under_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp) / "workspace"
            task = {
                "task_id": "task-1",
                "operation": "local.cli.run",
                "inputs": {"command": [sys.executable, "-c", "print('ok')"]},
                "runtime_profile": {
                    "workspace_root": str(workspace_root),
                    "allowed_local_commands": [Path(sys.executable).name],
                },
                "budget": {"max_runtime_seconds": 10},
            }

            result = worker.execute_task(task, allow_cli=True)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["returncode"], 0)
            self.assertIn("ok", result["result"]["stdout"])

    def test_runtime_profile_rejects_paths_outside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp) / "workspace"

            with self.assertRaises(ValueError):
                worker.resolve_under_workspace(workspace_root.resolve(), str(Path(tmp).resolve()))

    def test_langgraph_adapter_builds_module_command(self):
        task = {"task_id": "task-1", "operation": "langgraph.run", "inputs": {"module": "my_graph", "args": ["--once"]}}

        adapted = worker.with_adapter_command(task, default_module="langgraph")

        self.assertEqual(adapted["inputs"]["command"][:3], [sys.executable, "-m", "my_graph"])
        self.assertEqual(adapted["inputs"]["command"][-1], "--once")


if __name__ == "__main__":
    unittest.main()
