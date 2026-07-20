import tempfile
import unittest
from pathlib import Path

from scripts.android_adb_capture import AdbCapture, capture_to_artifact, parse_foreground
from scripts.control_plane_store import ControlPlaneStore


class AndroidAdbCaptureTests(unittest.TestCase):
    def test_parse_foreground_from_current_focus(self):
        text = """
        mCurrentFocus=Window{a1b2 u0 com.xunmeng.pinduoduo/.ui.activity.HomeActivity}
        """

        package_name, activity, focus = parse_foreground(text)

        self.assertEqual(package_name, "com.xunmeng.pinduoduo")
        self.assertEqual(activity, ".ui.activity.HomeActivity")
        self.assertIn("com.xunmeng.pinduoduo", focus)

    def test_capture_to_artifact_stores_screenshot_xml_and_foreground(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ControlPlaneStore(Path(tmp) / "control")
            capture = AdbCapture(
                device_id="device-1",
                foreground_package="com.xunmeng.pinduoduo",
                foreground_activity=".HomeActivity",
                window_focus="com.xunmeng.pinduoduo/.HomeActivity",
                screenshot_png=b"\x89PNG\r\n\x1a\npng-bytes",
                ui_xml="<hierarchy><node text=\"PDD\" /></hierarchy>",
            )

            artifact = capture_to_artifact(capture, store=store, workspace_id="tenant-a")

            self.assertEqual(artifact["workspace_id"], "tenant-a")
            self.assertEqual(artifact["purpose"], "android_adb_diagnostic")
            self.assertEqual(len(artifact["files"]), 3)
            files = {item["name"]: item for item in artifact["files"]}
            screenshot = store.state_dir / files["adb-screenshot.png"]["relative_path"]
            ui_xml = store.state_dir / files["adb-ui.xml"]["relative_path"]
            foreground = store.state_dir / files["adb-foreground.json"]["relative_path"]
            self.assertEqual(screenshot.read_bytes(), b"\x89PNG\r\n\x1a\npng-bytes")
            self.assertIn("<hierarchy>", ui_xml.read_text(encoding="utf-8"))
            self.assertIn("com.xunmeng.pinduoduo", foreground.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
