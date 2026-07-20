from __future__ import annotations

import unittest

from backend.devices.android_device import AndroidDeviceBackend
from backend.devices.base import DeviceBackend
from backend.devices.local_pc import LocalPCDevice

DEVICE_METHODS = (
    "get_screen_size",
    "move_to",
    "click",
    "double_click",
    "extract_text",
    "understand_screen",
    "capture_screen",
    "read_clipboard",
    "write_clipboard",
    "open_url",
    "search_web",
    "list_windows",
    "activate_window",
    "close_window",
    "search_files",
    "read_file_text",
    "open_file",
    "type_text",
    "press_key",
    "hotkey",
    "launch_app",
    "close_app",
    "list_installed_apps",
    "list_hardware_devices",
)


class RecordingAndroidRegistry:
    def __init__(self):
        self.commands = []

    def enqueue_command(self, device_id, operation, params):
        command = {"device_id": device_id, "operation": operation, "params": dict(params), "queued": True}
        self.commands.append(command)
        return command

    def update_heartbeat(self, payload):
        self.heartbeat = dict(payload)

    def device_status(self, device_id):
        return {"device_id": device_id, "online": True}

    def list_installed_apps(self, device_id, limit=50):
        return {"device_id": device_id, "apps": [{"name": "SpiritKin"}][:limit]}


class DeviceBackendContractTests(unittest.TestCase):
    def test_local_and_android_backends_implement_the_complete_protocol(self):
        for backend in (LocalPCDevice(), AndroidDeviceBackend()):
            with self.subTest(backend=backend.name):
                self.assertIsInstance(backend, DeviceBackend)
                for method_name in DEVICE_METHODS:
                    self.assertTrue(callable(getattr(backend, method_name, None)), method_name)

    def test_android_backend_maps_supported_contract_calls_to_companion_commands(self):
        registry = RecordingAndroidRegistry()
        backend = AndroidDeviceBackend(device_id="phone-1", companion_registry=registry)
        backend.update_state({"screen_size": {"width": 1080, "height": 2400}, "screen_text": "商品详情"})

        tap = backend.click(120, 340)
        clipboard = backend.write_clipboard("hello")
        launch = backend.launch_app("PDD")

        self.assertEqual(backend.get_screen_size()["width"], 1080)
        self.assertEqual(backend.extract_text(), "商品详情")
        self.assertEqual(tap["operation"], "accessibility.tap")
        self.assertEqual(clipboard["operation"], "clipboard.write")
        self.assertEqual(launch["operation"], "app.launch")
        self.assertEqual(len(registry.commands), 3)

    def test_android_backend_reports_nonportable_contract_calls_without_raising(self):
        backend = AndroidDeviceBackend(device_id="phone-1")

        result = backend.hotkey("ctrl", "c")

        self.assertFalse(result["supported"])
        self.assertFalse(result["queued"])
        self.assertIn("Android", result["reason"])


if __name__ == "__main__":
    unittest.main()
