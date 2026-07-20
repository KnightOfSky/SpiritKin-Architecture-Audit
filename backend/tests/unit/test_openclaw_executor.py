import unittest

from backend.devices import InMemoryOpenClawClient
from backend.executors import ExecutionRequest, OpenClawExecutor


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

    def get_status(self):
        self.calls.append(("get_status", {}))
        return {"state": "idle"}


class OpenClawExecutorTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeOpenClawClient()
        self.executor = OpenClawExecutor(client=self.client)

    def test_executor_dispatches_supported_operations(self):
        result = self.executor.execute(ExecutionRequest(target="openclaw", operation="move_to", params={"x": 1, "y": 2, "z": 3}))

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"x": 1, "y": 2, "z": 3})
        self.assertEqual(self.client.calls[-1], ("move_to", {"x": 1, "y": 2, "z": 3}))

    def test_executor_reports_missing_params(self):
        result = self.executor.execute(ExecutionRequest(target="openclaw", operation="move_to", params={"x": 1, "y": 2}))

        self.assertFalse(result.success)
        self.assertIn("缺少参数", result.message)
        self.assertEqual(result.error_code, "missing_params")
        self.assertEqual(result.metadata["missing_param"], "z")

    def test_executor_can_read_status(self):
        result = self.executor.execute(ExecutionRequest(target="arm", operation="status"))

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"state": "idle"})

    def test_executor_can_drive_in_memory_openclaw_client(self):
        executor = OpenClawExecutor(client=InMemoryOpenClawClient())

        move_result = executor.execute(ExecutionRequest(target="openclaw", operation="move_to", params={"x": 1, "y": 2, "z": 3}))
        status_result = executor.execute(ExecutionRequest(target="openclaw", operation="status"))

        self.assertTrue(move_result.success)
        self.assertEqual(move_result.message, "OpenClaw 已移动到 (1.0, 2.0, 3.0)。")
        self.assertTrue(status_result.success)
        self.assertEqual(status_result.data["position"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertIn("OpenClaw 当前状态", status_result.message)


if __name__ == "__main__":
    unittest.main()