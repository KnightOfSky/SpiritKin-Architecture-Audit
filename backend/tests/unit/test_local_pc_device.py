import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.devices.local_pc import LocalPCDevice


class LocalPCDeviceTests(unittest.TestCase):
    def test_launch_app_browser_alias_uses_default_browser(self):
        device = LocalPCDevice()

        with patch.dict("os.environ", {}, clear=True), patch("backend.devices.local_pc.webbrowser.open", return_value=True) as open_browser:
            result = device.launch_app("browser")

        open_browser.assert_called_once_with("https://www.bing.com/", new=2, autoraise=True)
        self.assertEqual(result["resolved_app"], "default_browser")
        self.assertEqual(result["display_name"], "默认浏览器")
        self.assertEqual(result["url"], "https://www.bing.com/")
        self.assertIsNone(result["pid"])

    def test_launch_app_browser_alias_uses_configured_start_url(self):
        device = LocalPCDevice()

        with patch.dict("os.environ", {"SPIRITKIN_DEFAULT_BROWSER_URL": "https://example.test/"}, clear=False), patch("backend.devices.local_pc.webbrowser.open", return_value=True) as open_browser:
            result = device.launch_app("默认浏览器")

        open_browser.assert_called_once_with("https://example.test/", new=2, autoraise=True)
        self.assertEqual(result["url"], "https://example.test/")

    def test_launch_app_reports_default_browser_failure(self):
        device = LocalPCDevice()

        with patch("backend.devices.local_pc.webbrowser.open", return_value=False):
            with self.assertRaises(RuntimeError):
                device.launch_app("默认浏览器")

    def test_launch_app_falls_back_to_shell_command(self):
        device = LocalPCDevice()
        process = Mock(pid=12345)

        with patch.object(device, "find_installed_app", return_value=None), patch("backend.devices.local_pc.subprocess.Popen", return_value=process) as popen:
            result = device.launch_app("notepad")

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args, (["notepad"],))
        self.assertEqual(popen.call_args.kwargs["shell"], False)
        if "creationflags" in popen.call_args.kwargs:
            self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result["pid"], 12345)
        self.assertEqual(result["resolved_app"], "notepad")
        self.assertEqual(result["launch_method"], "shell_command")

    def test_launch_app_cmd_uses_system_command_without_installed_app_match(self):
        device = LocalPCDevice()
        process = Mock(pid=12345)

        with patch.object(device, "find_installed_app", return_value={"name": "Visual Studio Code", "exe_path": r"C:\\Code\\Code.exe"}) as find_app, patch("backend.devices.local_pc.subprocess.Popen", return_value=process) as popen:
            result = device.launch_app("cmd")

        find_app.assert_not_called()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args, (["cmd.exe"],))
        self.assertEqual(popen.call_args.kwargs["shell"], False)
        self.assertNotIn("creationflags", popen.call_args.kwargs)
        self.assertEqual(result["resolved_app"], "cmd.exe")
        self.assertEqual(result["display_name"], "命令提示符")
        self.assertEqual(result["launch_method"], "system_command")

    def test_launch_app_maps_edge_alias_to_msedge_command(self):
        device = LocalPCDevice()
        process = Mock(pid=12345)

        with patch.object(device, "find_installed_app", return_value=None), patch("backend.devices.local_pc.subprocess.Popen", return_value=process) as popen:
            result = device.launch_app("msedge")

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args, (["msedge"],))
        self.assertEqual(popen.call_args.kwargs["shell"], False)
        if "creationflags" in popen.call_args.kwargs:
            self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result["resolved_app"], "msedge")
        self.assertEqual(result["display_name"], "Edge 浏览器")

    def test_launch_app_uses_installed_app_exe_when_scan_matches(self):
        device = LocalPCDevice()
        process = Mock(pid=12345)
        installed_app = {"name": "Mozilla Firefox", "exe_path": r"C:\\Program Files\\Mozilla Firefox\\firefox.exe"}

        with patch.object(device, "find_installed_app", return_value=installed_app), patch("backend.devices.local_pc.os.path.isfile", return_value=True), patch("backend.devices.local_pc.subprocess.Popen", return_value=process) as popen:
            result = device.launch_app("Firefox 浏览器")

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args, ([r"C:\\Program Files\\Mozilla Firefox\\firefox.exe"],))
        self.assertEqual(popen.call_args.kwargs["shell"], False)
        if "creationflags" in popen.call_args.kwargs:
            self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result["display_name"], "Mozilla Firefox")
        self.assertEqual(result["launch_method"], "installed_app_exe")

    def test_find_installed_app_matches_browser_aliases(self):
        device = LocalPCDevice()
        device._installed_apps_cache = [
            {"name": "Google Chrome", "publisher": "Google LLC", "exe_path": r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"},
            {"name": "Mozilla Firefox", "publisher": "Mozilla", "exe_path": r"C:\\Program Files\\Mozilla Firefox\\firefox.exe"},
        ]

        self.assertEqual(device.find_installed_app("谷歌浏览器")["name"], "Google Chrome")
        self.assertEqual(device.find_installed_app("fire fox 浏览器")["name"], "Mozilla Firefox")

    def test_find_installed_app_tolerates_huobao_asr_confusion(self):
        device = LocalPCDevice()
        device._installed_apps_cache = [
            {"name": "火豹浏览器", "publisher": "Huobao", "exe_path": r"C:\\Program Files\\Huobao\\huobao.exe"},
        ]

        match = device.find_installed_app("火爆浏览器")

        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "火豹浏览器")

    def test_close_app_uses_installed_app_and_alias_terms(self):
        device = LocalPCDevice()
        installed_app = {"name": "火豹浏览器", "exe_path": r"C:\\Program Files\\Huobao\\huobao.exe"}

        with patch("backend.devices.local_pc.platform.system", return_value="Windows"), patch.object(device, "find_installed_app", return_value=installed_app), patch.object(LocalPCDevice, "_close_windows_app", return_value=[{"pid": 123, "name": "huobao", "closed": True}]) as close_app:
            result = device.close_app("火爆浏览器")

        terms = close_app.call_args.args[0]
        self.assertIn("火豹", terms)
        self.assertIn("huobao", terms)
        self.assertEqual(result["closed_count"], 1)
        self.assertEqual(result["display_name"], "火豹浏览器")

    def test_find_installed_app_dynamically_matches_scanned_software_without_alias(self):
        device = LocalPCDevice()
        device._installed_apps_cache = [
            {"name": "Adobe Premiere Pro 2025", "publisher": "Adobe Inc.", "exe_path": r"C:\\Program Files\\Adobe\\Premiere Pro\\Premiere.exe"},
            {"name": "DaVinci Resolve", "publisher": "Blackmagic Design", "exe_path": r"C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\Resolve.exe"},
        ]

        match = device.find_installed_app("Premiere Pro")

        self.assertEqual(match["name"], "Adobe Premiere Pro 2025")
        self.assertGreaterEqual(match["match_score"], 0.62)

    def test_list_installed_apps_merges_registry_and_start_menu_shortcuts(self):
        device = LocalPCDevice()
        registry_app = {"name": "Example App", "version": "1.0", "publisher": "Example", "exe_path": "", "can_launch": False}
        shortcut_app = {"name": "Example App", "shortcut_path": r"C:\\Menu\\Example App.lnk", "exe_path": r"C:\\Example\\example.exe", "source": "start_menu", "can_launch": True}

        with patch("backend.devices.local_pc.platform.system", return_value="Windows"), patch.object(LocalPCDevice, "_list_windows_installed_apps", return_value=[registry_app]), patch.object(LocalPCDevice, "_list_windows_start_menu_apps", return_value=[shortcut_app]):
            result = device.list_installed_apps(limit=10, refresh=True)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Example App")
        self.assertEqual(result[0]["exe_path"], r"C:\\Example\\example.exe")
        self.assertIn("registry", result[0]["sources"])
        self.assertIn("start_menu", result[0]["sources"])

    def test_extract_executable_path_from_display_icon(self):
        path = LocalPCDevice._extract_executable_path(r'"C:\\Program Files\\App\\app.exe",0')

        self.assertEqual(path, r"C:\\Program Files\\App\\app.exe")

    def test_list_hardware_devices_uses_pnp_device_inventory(self):
        device = LocalPCDevice()
        completed = Mock(returncode=0, stdout='[{"Class":"Camera","FriendlyName":"Integrated Camera","Status":"OK","InstanceId":"USB\\\\1"}]', stderr="")

        with patch("backend.devices.local_pc.platform.system", return_value="Windows"), patch("backend.devices.local_pc.subprocess.run", return_value=completed) as run:
            result = device.list_hardware_devices(limit=1)

        self.assertEqual(result[0]["FriendlyName"], "Integrated Camera")
        self.assertEqual(result[0]["capabilities"], ["capture_video"])
        self.assertIn("Get-PnpDevice", run.call_args.args[0][-1])

    def test_open_url_adds_https_scheme_and_uses_default_browser(self):
        device = LocalPCDevice()

        with patch("backend.devices.local_pc.webbrowser.open", return_value=True) as open_browser:
            result = device.open_url("example.com/docs")

        open_browser.assert_called_once_with("https://example.com/docs", new=2, autoraise=True)
        self.assertEqual(result["url"], "https://example.com/docs")

    def test_search_web_opens_engine_search_url(self):
        device = LocalPCDevice()

        with patch.object(device, "open_url", return_value={"url": "https://www.bing.com/search?q=SpiritKinAI", "opened": True}) as open_url:
            result = device.search_web("SpiritKinAI", engine="bing")

        self.assertIn("SpiritKinAI", open_url.call_args.args[0])
        self.assertEqual(result["query"], "SpiritKinAI")

    def test_clipboard_read_uses_windows_get_clipboard(self):
        device = LocalPCDevice()
        completed = Mock(returncode=0, stdout="hello\r\n", stderr="")

        with patch("backend.devices.local_pc.platform.system", return_value="Windows"), patch("backend.devices.local_pc.subprocess.run", return_value=completed) as run:
            result = device.read_clipboard()

        self.assertEqual(result["text"], "hello")
        self.assertIn("Get-Clipboard", run.call_args.args[0][-1])

    def test_clipboard_write_uses_windows_set_clipboard(self):
        device = LocalPCDevice()
        completed = Mock(returncode=0, stdout="", stderr="")

        with patch("backend.devices.local_pc.platform.system", return_value="Windows"), patch("backend.devices.local_pc.subprocess.run", return_value=completed) as run:
            result = device.write_clipboard("hello")

        self.assertEqual(result["length"], 5)
        self.assertEqual(run.call_args.kwargs["input"], "hello")
        self.assertIn("Set-Clipboard", run.call_args.args[0][-1])

    def test_write_file_text_allows_state_data_runs_only(self):
        device = LocalPCDevice()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("backend.devices.local_pc.os.getcwd", return_value=str(root)):
                result = device.write_file_text("state/local_pc/write.txt", "hello")
                target = root / "state" / "local_pc" / "write.txt"

                self.assertEqual(Path(result["path"]).resolve(), target.resolve())
                self.assertEqual(target.read_text(encoding="utf-8"), "hello")

                with self.assertRaisesRegex(RuntimeError, "state/.+data/.+runs/"):
                    device.write_file_text("outside.txt", "blocked")
                with self.assertRaisesRegex(RuntimeError, "state/.+data/.+runs/"):
                    device.write_file_text(str(root.parent / "escape.txt"), "blocked")

    def test_list_windows_uses_visible_process_titles(self):
        device = LocalPCDevice()
        completed = Mock(returncode=0, stdout='[{"Id":1,"ProcessName":"Code","MainWindowTitle":"SpiritKinAI - VSCode"}]', stderr="")

        with patch("backend.devices.local_pc.platform.system", return_value="Windows"), patch("backend.devices.local_pc.subprocess.run", return_value=completed):
            result = device.list_windows(limit=5)

        self.assertEqual(result[0]["title"], "SpiritKinAI - VSCode")

    def test_capture_screen_saves_screenshot(self):
        device = LocalPCDevice()
        screenshot = Mock(width=100, height=80)
        gui = Mock()
        gui.screenshot.return_value = screenshot

        with patch.object(device, "_pyautogui", return_value=gui):
            result = device.capture_screen(output_path="tmp/screen.png")

        screenshot.save.assert_called_once()
        self.assertTrue(result["path"].endswith("tmp\\screen.png") or result["path"].endswith("tmp/screen.png"))

    def test_search_files_defaults_to_given_root(self):
        device = LocalPCDevice()
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "notes_handoff.md").write_text("hello", encoding="utf-8")
            result = device.search_files("handoff", root=tmpdir, limit=5)

        self.assertEqual(result["matches"][0]["name"], "notes_handoff.md")

    def test_read_file_text_returns_content_and_truncation_flag(self):
        device = LocalPCDevice()
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "example.txt"
            path.write_text("abcdef", encoding="utf-8")
            result = device.read_file_text(str(path), max_chars=3)

        self.assertEqual(result["content"], "abc")
        self.assertTrue(result["truncated"])

    def test_open_file_uses_os_startfile_on_windows(self):
        device = LocalPCDevice()
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "example.txt"
            path.write_text("abc", encoding="utf-8")
            with patch("backend.devices.local_pc.platform.system", return_value="Windows"), patch("backend.devices.local_pc.os.startfile", create=True) as startfile:
                result = device.open_file(str(path))

        startfile.assert_called_once()
        self.assertTrue(result["opened"])


if __name__ == "__main__":
    unittest.main()
