from __future__ import annotations

import unittest

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.orchestrator.capability_inventory import build_capability_inventory
from backend.tools.base import ToolSpec


class FakeExecutor(BaseExecutor):
    name = "fake"

    def supports(self, request: ExecutionRequest) -> bool:
        return True

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(True)


class FakeDevice:
    def list_installed_apps(self, limit=80):
        return [{"name": "Edge", "can_launch": True}]

    def list_hardware_devices(self, limit=80):
        return [{"FriendlyName": "USB Camera", "Status": "OK"}]

    def list_cli_tools(self, limit=80):
        return [
            {"name": "ffmpeg", "category": "media", "available": True, "path": "/usr/bin/ffmpeg"},
            {"name": "yt-dlp", "category": "download", "available": False, "path": ""},
        ]


class LegacyDevice:
    """Device backend without list_cli_tools (older adapter)."""

    def list_installed_apps(self, limit=80):
        return [{"name": "Edge", "can_launch": True}]

    def list_hardware_devices(self, limit=80):
        return []


class CapabilityInventoryTests(unittest.TestCase):
    def test_inventory_summarizes_tools_executors_and_devices(self):
        inventory = build_capability_inventory(
            tools=[ToolSpec(name="app.launch", description="open", target="local_pc", operation="launch_app", risk_level="medium")],
            executors=[FakeExecutor()],
            device_backend=FakeDevice(),
        ).snapshot()

        self.assertEqual(len(inventory["tools"]), 1)
        self.assertEqual(len(inventory["executors"]), 1)
        self.assertEqual(inventory["software"][0]["name"], "Edge")
        self.assertIn("工具 1 个", inventory["summary"])

    def test_inventory_includes_cli_tools_and_counts_available(self):
        inventory = build_capability_inventory(
            tools=[],
            executors=[],
            device_backend=FakeDevice(),
        ).snapshot()

        cli_names = {item["name"] for item in inventory["cli_tools"]}
        self.assertEqual(cli_names, {"ffmpeg", "yt-dlp"})
        self.assertIn("命令行工具 1/2 个可用", inventory["summary"])

    def test_inventory_tolerates_device_without_cli_probe(self):
        inventory = build_capability_inventory(
            tools=[],
            executors=[],
            device_backend=LegacyDevice(),
        ).snapshot()

        self.assertEqual(inventory["cli_tools"], [])
        self.assertIn("命令行工具 0/0 个可用", inventory["summary"])


if __name__ == "__main__":
    unittest.main()
