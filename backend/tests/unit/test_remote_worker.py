import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.agent_management import export_remote_submodule
from backend.executors import ExecutionRequest, ExecutionResult, HttpRemoteNodeClient, RemoteExecutionPayload
from backend.executors.base import BaseExecutor
from backend.remote.worker import REMOTE_AUTH_HEADER, RemoteWorker, RemoteWorkerHandler
from scripts.smoke_remote_worker import run_remote_worker_smoke


class FakeWorkerExecutor(BaseExecutor):
    name = "desktop"

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in {"desktop", "local_pc"}

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.operation == "status":
            return ExecutionResult(success=True, message="worker ok", data={"state": "idle"}, metadata={"executor": self.name})
        return ExecutionResult(success=False, message="unsupported", error_code="unsupported_operation")


class RemoteWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = RemoteWorker(
            node_id="office-pc",
            auth_token="secret-token",
            aliases={"公司电脑"},
            metadata={"transport": "http"},
            executors=[FakeWorkerExecutor()],
        )
        RemoteWorkerHandler.worker = self.worker
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RemoteWorkerHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_http_remote_node_client_reads_worker_heartbeat(self):
        client = HttpRemoteNodeClient(self.base_url, auth_token="secret-token")

        heartbeat = client.heartbeat("office-pc")

        self.assertEqual(heartbeat.node_id, "office-pc")
        self.assertIn("desktop", heartbeat.targets)
        self.assertIn("desktop", heartbeat.capabilities)
        self.assertEqual(heartbeat.metadata["transport"], "http")

    def test_http_remote_node_client_executes_worker_request(self):
        client = HttpRemoteNodeClient(self.base_url, auth_token="secret-token")

        response = client.execute(RemoteExecutionPayload(node_id="office-pc", target="desktop", operation="status"))

        self.assertTrue(response.success)
        self.assertEqual(response.message, "worker ok")
        self.assertEqual(response.data["state"], "idle")

    def test_worker_execute_payload_persists_runtime_trajectory(self):
        with TemporaryDirectory() as tmp:
            trajectory_path = Path(tmp) / "trajectories.jsonl"
            with patch.dict("os.environ", {"SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path)}, clear=False):
                result = self.worker.execute_payload({"node_id": "office-pc", "target": "desktop", "operation": "status", "actor": "unit"})
            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]

        self.assertTrue(result["success"])
        self.assertEqual(result["trajectory_record"]["source"], "remote.worker_result")
        self.assertEqual(records[0]["metadata"]["source"], "remote.worker_result")
        self.assertEqual(records[0]["agent_id"], "unit")
        self.assertEqual(records[0]["domain"], "desktop")
        self.assertTrue(records[0]["overall_success"])

    def test_worker_requires_token_for_execute(self):
        client = HttpRemoteNodeClient(self.base_url)

        with self.assertRaises(RuntimeError) as ctx:
            client.execute(RemoteExecutionPayload(node_id="office-pc", target="desktop", operation="status"))

        self.assertIn("unauthorized", str(ctx.exception))
        self.assertEqual(REMOTE_AUTH_HEADER, "X-SpiritKin-Remote-Token")

    def test_worker_without_token_is_not_open_by_default(self):
        self.worker.auth_token = ""
        client = HttpRemoteNodeClient(self.base_url)

        with self.assertRaises(RuntimeError) as ctx:
            client.execute(RemoteExecutionPayload(node_id="office-pc", target="desktop", operation="status"))

        self.assertIn("unauthorized", str(ctx.exception))

    def test_smoke_remote_worker_checks_heartbeat_and_execute(self):
        report = run_remote_worker_smoke(
            base_url=self.base_url,
            node_id="office-pc",
            auth_token="secret-token",
            target="desktop",
            operation="status",
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["node_id"], "office-pc")
        self.assertEqual(report["heartbeat"]["node_id"], "office-pc")
        self.assertEqual(report["execution"]["message"], "worker ok")

    def test_worker_imports_and_registers_remote_package(self):
        with TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"SPIRITKIN_REMOTE_PACKAGE_DIR": str(Path(tmp) / "packages"), "SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY": "secret-token"}, clear=False):
                self.worker.package_dir = self.worker._resolve_package_dir()
                package = export_remote_submodule(
                    {
                        "export_id": "pkg-a",
                        "skill_names": ["workflow.local.scan"],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    },
                    output_dir=Path(tmp) / "exports",
                )["package"]
                result = self.worker.import_package({"package": package})
                self.assertTrue(result["ok"])
                self.assertEqual(result["package_id"], "pkg-a")
                self.assertTrue(Path(result["package_path"]).exists())
                self.assertEqual(result["package"]["status"], "staged")
                self.assertTrue(result["package"]["signature_verification"]["verified"])
                self.assertEqual(result["package"]["activation"]["status"], "staged")

    def test_worker_execute_package_defaults_to_import_without_running_commands(self):
        with TemporaryDirectory() as tmp:
            trajectory_path = Path(tmp) / "trajectories.jsonl"
            with patch.dict(
                "os.environ",
                {
                    "SPIRITKIN_REMOTE_PACKAGE_DIR": str(Path(tmp) / "packages"),
                    "SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY": "secret-token",
                    "SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path),
                },
                clear=False,
            ):
                self.worker.package_dir = self.worker._resolve_package_dir()
                package = export_remote_submodule(
                    {
                        "export_id": "pkg-b",
                        "verification_commands": ["python --version"],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    },
                    output_dir=Path(tmp) / "exports",
                )["package"]
                result = self.worker.execute_package(
                    {
                        "package": package,
                    }
                )
                records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "staged_verification_pending")
        self.assertTrue(result["verification"][0]["skipped"])
        self.assertTrue(result["signature_verification"]["verified"])
        self.assertEqual(result["activation"]["status"], "staged")
        self.assertEqual(result["trajectory_record"]["source"], "remote.worker_result")
        self.assertEqual(records[0]["metadata"]["source"], "remote.worker_result")
        self.assertEqual(records[0]["metadata"]["action"], "execute_package")
        self.assertFalse(records[0]["overall_success"])

    def test_worker_rejects_tampered_signed_remote_package(self):
        with TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"SPIRITKIN_REMOTE_PACKAGE_DIR": str(Path(tmp) / "packages"), "SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY": "secret-token"}, clear=False):
                self.worker.package_dir = self.worker._resolve_package_dir()
                package = export_remote_submodule(
                    {
                        "export_id": "pkg-tampered",
                        "skill_names": ["workflow.local.scan"],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    },
                    output_dir=Path(tmp) / "exports",
                )["package"]
                package = json.loads(json.dumps(package))
                package["notes"] = "tampered after signing"

                with self.assertRaises(ValueError) as ctx:
                    self.worker.import_package({"package": package})

        self.assertIn("signature mismatch", str(ctx.exception))

    def test_worker_rolls_back_to_previous_active_package(self):
        with TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"SPIRITKIN_REMOTE_PACKAGE_DIR": str(Path(tmp) / "packages"), "SPIRITKIN_REMOTE_PACKAGE_SIGNING_KEY": "secret-token"}, clear=False):
                self.worker.package_dir = self.worker._resolve_package_dir()
                exports = Path(tmp) / "exports"
                first_package = export_remote_submodule(
                    {
                        "export_id": "pkg-v1",
                        "verification_commands": [],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    },
                    output_dir=exports,
                )["package"]
                second_package = export_remote_submodule(
                    {
                        "export_id": "pkg-v2",
                        "verification_commands": [],
                        "core_review_approved": True,
                        "reviewer": "unit-test",
                    },
                    output_dir=exports,
                )["package"]
                first = self.worker.execute_package({"package": first_package, "run_verification": False})
                second = self.worker.execute_package({"package": second_package, "run_verification": False})

                rollback = self.worker.rollback_package({"package_id": "pkg-v2"})
                active_pointer = json.loads((Path(self.worker.package_dir) / "active-package.json").read_text(encoding="utf-8"))
                first_record = json.loads(Path(first["package_path"]).read_text(encoding="utf-8"))
                second_record = json.loads(Path(second["package_path"]).read_text(encoding="utf-8"))

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(rollback["ok"])
        self.assertEqual(rollback["from_package_id"], "pkg-v2")
        self.assertEqual(rollback["to_package_id"], "pkg-v1")
        self.assertTrue(rollback["signature_verification"]["verified"])
        self.assertEqual(active_pointer["package_id"], "pkg-v1")
        self.assertEqual(active_pointer["previous_active_package_id"], "pkg-v2")
        self.assertEqual(first_record["status"], "active")
        self.assertEqual(second_record["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
