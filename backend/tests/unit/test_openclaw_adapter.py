import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.devices.openclaw import HttpOpenClawClient, InMemoryOpenClawClient, create_openclaw_arm
from backend.executors import ExecutionRequest
from backend.services.openclaw import create_openclaw_executor


class FakeOpenClawClient:
    def __init__(self):
        self.calls = []

    def home(self):
        self.calls.append(("home", {}))
        return "ok"

    def move_to(self, **kwargs):
        self.calls.append(("move_to", kwargs))
        return kwargs

    def set_gripper(self, opened: bool):
        self.calls.append(("set_gripper", {"opened": opened}))
        return opened


class LegacyGripperClient:
    def __init__(self):
        self.calls = []

    def home(self):
        self.calls.append(("home", {}))

    def move_to(self, **kwargs):
        self.calls.append(("move_to", kwargs))

    def open_gripper(self):
        self.calls.append(("open_gripper", {}))

    def close_gripper(self):
        self.calls.append(("close_gripper", {}))


class OpenClawAdapterTests(unittest.TestCase):
    def test_adapter_calls_client_methods(self):
        client = FakeOpenClawClient()
        arm = create_openclaw_arm(client=client)

        arm.home()
        arm.move_to(x=1, y=2, z=3, speed=0.5)
        arm.set_gripper(opened=True)

        self.assertEqual(
            client.calls,
            [
                ("home", {}),
                ("move_to", {"x": 1, "y": 2, "z": 3, "speed": 0.5}),
                ("set_gripper", {"opened": True}),
            ],
        )

    def test_adapter_supports_legacy_open_close_gripper_clients(self):
        client = LegacyGripperClient()
        arm = create_openclaw_arm(client=client)

        arm.set_gripper(opened=True)
        arm.set_gripper(opened=False)

        self.assertEqual(client.calls, [("open_gripper", {}), ("close_gripper", {})])

    def test_in_memory_openclaw_client_tracks_state(self):
        client = InMemoryOpenClawClient()
        arm = create_openclaw_arm(client=client)

        arm.move_to(x=4, y=5, z=6)
        arm.set_gripper(opened=False)
        status = arm.get_status()

        self.assertEqual(status["position"], {"x": 4.0, "y": 5.0, "z": 6.0})
        self.assertFalse(status["gripper_opened"])
        self.assertEqual(status["last_command"], "close_gripper")

    def test_in_memory_openclaw_client_can_restore_state_from_local_json(self):
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "openclaw" / "state.json"

            first_client = InMemoryOpenClawClient(state_path=state_path)
            first_arm = create_openclaw_arm(client=first_client)
            first_arm.move_to(x=7, y=8, z=9)
            first_arm.set_gripper(opened=False)

            second_client = InMemoryOpenClawClient(state_path=state_path)
            status = create_openclaw_arm(client=second_client).get_status()

            self.assertTrue(state_path.exists())
            self.assertEqual(status["position"], {"x": 7.0, "y": 8.0, "z": 9.0})
            self.assertFalse(status["gripper_opened"])
            self.assertEqual(status["last_command"], "close_gripper")
            self.assertEqual(status["transport"], "local_json")

    def test_service_can_build_persistent_openclaw_executor(self):
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "openclaw" / "state.json"

            first_executor = create_openclaw_executor(state_path=state_path)
            move_result = first_executor.execute(
                ExecutionRequest(target="openclaw", operation="move_to", params={"x": 2, "y": 3, "z": 4})
            )

            second_executor = create_openclaw_executor(state_path=state_path)
            status_result = second_executor.execute(ExecutionRequest(target="openclaw", operation="status"))

            self.assertTrue(move_result.success)
            self.assertTrue(state_path.exists())
            self.assertTrue(status_result.success)
            self.assertEqual(status_result.data["position"], {"x": 2.0, "y": 3.0, "z": 4.0})
            self.assertEqual(status_result.data["transport"], "local_json")

    def test_http_openclaw_client_calls_controller_api(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"state":"idle","position":{"x":1,"y":2,"z":3},"gripper_opened":true}'

        with patch("backend.devices.openclaw.request.urlopen", return_value=FakeResponse()) as urlopen:
            client = HttpOpenClawClient("http://127.0.0.1:9000", token="secret", timeout=1.5)
            result = client.move_to(x=1, y=2, z=3, speed=0.4)

        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://127.0.0.1:9000/move_to")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers["Authorization"], "Bearer secret")
        self.assertEqual(result["transport"], "http")
        self.assertEqual(result["position"], {"x": 1, "y": 2, "z": 3})

    def test_service_uses_http_transport_from_environment(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"state":"idle","position":{"x":0,"y":0,"z":0},"gripper_opened":true}'

        env = {"SPIRITKIN_OPENCLAW_HTTP_BASE_URL": "http://127.0.0.1:9000", "SPIRITKIN_OPENCLAW_HTTP_TOKEN": "secret"}
        with patch.dict("os.environ", env, clear=True), patch("backend.devices.openclaw.request.urlopen", return_value=FakeResponse()):
            executor = create_openclaw_executor()
            result = executor.execute(ExecutionRequest(target="openclaw", operation="status"))

        self.assertTrue(result.success)
        self.assertEqual(result.data["transport"], "http")


if __name__ == "__main__":
    unittest.main()
