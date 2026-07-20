import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.start_desktop_console import (
    CosyVoiceServiceConfig,
    _avatar_url,
    _desktop_url,
    _edge_candidates,
    _service_started,
    _websocket_bridge_healthy,
    build_edge_app_command,
    build_service_commands,
    build_wpf_command,
    ensure_services,
    open_console_window,
    resolve_cosyvoice_service_config,
    resolve_launch_token,
    resolve_session_token,
    status_lines,
    stop_launch_services,
)


class DesktopConsoleLauncherTests(unittest.TestCase):
    def test_resolve_session_token_uses_existing_or_generates(self):
        self.assertEqual(resolve_session_token("abc"), "abc")
        with patch.dict("os.environ", {"SPIRITKIN_MOBILE_TOKEN": "env-token"}, clear=False):
            self.assertEqual(resolve_session_token(""), "env-token")
        with patch.dict("os.environ", {}, clear=True), patch("scripts.start_desktop_console.secrets.token_urlsafe", return_value="generated"):
            self.assertEqual(resolve_session_token(""), "generated")

    def test_restart_wpf_reuses_existing_service_token(self):
        state = {"session_token": "existing-service-token"}
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                resolve_launch_token("", restart_wpf=True, launch_state=state),
                "existing-service-token",
            )
            self.assertEqual(
                resolve_launch_token("explicit", restart_wpf=True, launch_state=state),
                "explicit",
            )

    def test_desktop_url_includes_sync_command_and_token(self):
        url = _desktop_url("0.0.0.0", 8787, events_port=8765, command_port=8788, token="abc")

        self.assertTrue(url.startswith("http://127.0.0.1:8787/desktop_console.html?"))
        self.assertIn("ws=ws%3A%2F%2F127.0.0.1%3A8765", url)
        self.assertIn("cmd=http%3A%2F%2F127.0.0.1%3A8788%2Fcommand", url)
        self.assertIn("token=abc", url)

    def test_avatar_url_targets_spirit3d_manifest_and_token(self):
        url = _avatar_url("127.0.0.1", 8787, events_port=8765, command_port=8788, token="abc")

        self.assertIn("avatar_3d.html", url)
        self.assertIn("config=models%2Fspirit3d%2Fmanifest.json", url)
        self.assertIn("cmd=http%3A%2F%2F127.0.0.1%3A8788%2Fcommand", url)
        self.assertIn("token=abc", url)

    def test_service_commands_start_frontend_bridge_and_gateway(self):
        commands = build_service_commands(host="127.0.0.1", frontend_port=8787, events_port=8765, command_port=8788)

        self.assertEqual(commands["bridge"][-2:], ["-m", "backend.app.realtime_bridge"])
        self.assertEqual(commands["command_gateway"][1:3], ["-u", "-c"])
        self.assertIn("codex_work_events", commands["command_gateway"][-1])
        self.assertIn("backend.app.command_gateway", commands["command_gateway"][-1])
        self.assertEqual(commands["voice_session"][1:4], ["-u", "-m", "backend.perception.audio.realtime_session"])
        self.assertIn("--strict-hotword", commands["voice_session"])
        self.assertIn("backend.app.static_frontend_server", commands["frontend"])
        self.assertIn("8787", commands["frontend"])
        self.assertNotIn("cosyvoice", commands)

    def test_cosyvoice_runtime_resolves_selected_loopback_service(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = {
                "python": root / "runtime" / "python.exe",
                "service": root / "backend" / "expression" / "cosyvoice_service.py",
                "model": root / "model" / "cosyvoice3.yaml",
                "source": root / "source" / "cosyvoice" / "cli" / "cosyvoice.py",
                "matcha": root / "source" / "third_party" / "Matcha-TTS",
                "profile": root / "profiles" / "spiritkin.primary.v1" / "profile.json",
            }
            for key, path in paths.items():
                if key == "matcha":
                    path.mkdir(parents=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("test", encoding="utf-8")
            env = {
                "SPIRITKIN_TTS_PROVIDER": "cosyvoice",
                "SPIRITKIN_TTS_BASE_URL": "http://127.0.0.1:50123",
                "SPIRITKIN_TTS_VOICE_PROFILE_PATH": "profiles/spiritkin.primary.v1/profile.json",
                "SPIRITKIN_COSYVOICE_PYTHON": "runtime/python.exe",
                "SPIRITKIN_COSYVOICE_MODEL_DIR": "model",
                "SPIRITKIN_COSYVOICE_SOURCE_DIR": "source",
                "SPIRITKIN_VOICE_PROFILE_ROOT": "profiles",
                "SPIRITKIN_COSYVOICE_SERVICE_SCRIPT": "backend/expression/cosyvoice_service.py",
                "SPIRITKIN_COSYVOICE_FP16": "1",
            }

            config = resolve_cosyvoice_service_config(
                environ=env,
                config_path=root / "missing.yaml",
                root_dir=root,
            )

        self.assertTrue(config.selected)
        self.assertTrue(config.available)
        self.assertEqual(config.port, 50123)
        self.assertEqual(config.command[0], str(paths["python"]))
        self.assertIn("--fp16", config.command)
        self.assertEqual(config.command[config.command.index("--host") + 1], "127.0.0.1")

    def test_cosyvoice_runtime_reports_missing_assets_and_rejects_remote_bind(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_env = {
                "SPIRITKIN_TTS_PROVIDER": "cosyvoice",
                "SPIRITKIN_TTS_VOICE_PROFILE_PATH": "profiles/spiritkin.primary.v1/profile.json",
                "SPIRITKIN_COSYVOICE_PYTHON": "runtime/python.exe",
                "SPIRITKIN_COSYVOICE_MODEL_DIR": "model",
                "SPIRITKIN_COSYVOICE_SOURCE_DIR": "source",
                "SPIRITKIN_VOICE_PROFILE_ROOT": "profiles",
                "SPIRITKIN_COSYVOICE_SERVICE_SCRIPT": "backend/expression/cosyvoice_service.py",
            }
            missing = resolve_cosyvoice_service_config(
                environ={**base_env, "SPIRITKIN_TTS_BASE_URL": "http://127.0.0.1:50000"},
                config_path=root / "missing.yaml",
                root_dir=root,
            )
            remote = resolve_cosyvoice_service_config(
                environ={**base_env, "SPIRITKIN_TTS_BASE_URL": "http://192.168.1.20:50000"},
                config_path=root / "missing.yaml",
                root_dir=root,
            )

        self.assertTrue(missing.selected)
        self.assertFalse(missing.available)
        self.assertTrue(missing.reason.startswith("missing_runtime:"))
        self.assertEqual(remote.reason, "loopback_http_required")

    def test_service_commands_place_cosyvoice_before_voice_listener(self):
        config = CosyVoiceServiceConfig(
            selected=True,
            available=True,
            port=50000,
            command=("isolated-python", "cosyvoice_service.py"),
        )

        commands = build_service_commands(
            host="127.0.0.1",
            frontend_port=8787,
            events_port=8765,
            command_port=8788,
            cosyvoice=config,
        )

        self.assertEqual(commands["cosyvoice"], ["isolated-python", "cosyvoice_service.py"])
        self.assertLess(list(commands).index("cosyvoice"), list(commands).index("voice_session"))

    def test_cosyvoice_service_started_requires_provider_health(self):
        with patch("scripts.start_desktop_console._cosyvoice_healthy", return_value=True) as probe:
            self.assertTrue(
                _service_started(
                    "cosyvoice",
                    host="0.0.0.0",
                    events_port=8765,
                    command_port=8788,
                    frontend_port=8787,
                    cosyvoice_port=50123,
                )
            )

        probe.assert_called_once_with(50123)

    def test_bridge_service_started_requires_websocket_snapshot(self):
        with patch("scripts.start_desktop_console._websocket_bridge_healthy", return_value=True) as bridge_probe:
            self.assertTrue(_service_started("bridge", host="127.0.0.1", events_port=8765, command_port=8788, frontend_port=8787, token="secret"))

        bridge_probe.assert_called_once_with("127.0.0.1", 8765, token="secret")

    def test_command_gateway_health_probe_uses_session_token(self):
        with patch("scripts.start_desktop_console._command_gateway_healthy", return_value=True) as gateway_probe:
            self.assertTrue(_service_started("command_gateway", host="127.0.0.1", events_port=8765, command_port=8788, frontend_port=8787, token="secret"))

        gateway_probe.assert_called_once_with("127.0.0.1", 8788, token="secret")

    def test_websocket_bridge_probe_rejects_plain_http_port(self):
        self.assertFalse(_websocket_bridge_healthy("127.0.0.1", 9, timeout=0.01))

    def test_ensure_services_marks_busy_bridge_port_without_websocket_snapshot(self):
        def fake_accepts(host, port):
            return port in {8765, 8787}

        with (
            patch(
                "scripts.start_desktop_console.resolve_cosyvoice_service_config",
                return_value=CosyVoiceServiceConfig(selected=False, available=False),
            ),
            patch("scripts.start_desktop_console._websocket_bridge_healthy", return_value=False),
            patch("scripts.start_desktop_console._command_gateway_healthy", return_value=True),
            patch("scripts.start_desktop_console._port_accepts_connection", side_effect=fake_accepts),
            patch("scripts.start_desktop_console._pid_for_listening_port", return_value=321),
        ):
            records = ensure_services(host="127.0.0.1", frontend_port=8787, events_port=8765, command_port=8788, startup_timeout=0.01)

        bridge = next(record for record in records if record["name"] == "bridge")
        self.assertEqual(bridge["status"], "port_busy")
        self.assertEqual(bridge["pid"], 321)

    def test_edge_candidates_include_standard_windows_paths(self):
        candidates = [str(path) for path in _edge_candidates()]

        self.assertTrue(any(path.endswith(r"Microsoft\Edge\Application\msedge.exe") for path in candidates))

    def test_edge_app_command_uses_app_mode_and_profile(self):
        fake_edge = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")

        with patch("scripts.start_desktop_console._find_edge", return_value=fake_edge):
            command = build_edge_app_command("http://127.0.0.1:8787/desktop_console.html", profile_dir=Path("state/run/test_profile"))

        self.assertEqual(command[0], str(fake_edge))
        self.assertIn("--app=http://127.0.0.1:8787/desktop_console.html", command)
        self.assertTrue(any(part.startswith("--user-data-dir=") for part in command))

    def test_open_console_window_prefers_wpf_when_available(self):
        with (
            patch("scripts.start_desktop_console.WPF_PROJECT") as project,
            patch("scripts.start_desktop_console.WPF_BUILD_EXE") as exe,
            patch("scripts.start_desktop_console._wpf_process_running", return_value=False),
            patch("scripts.start_desktop_console._wpf_sources_newer_than_build", return_value=False),
            patch("scripts.start_desktop_console.subprocess.Popen") as popen,
        ):
            project.exists.return_value = True
            exe.exists.return_value = True
            exe.__str__.return_value = r"C:\SpiritKinDesktop.exe"
            mode = open_console_window("http://127.0.0.1:8787/desktop_console.html", mode="auto")

        self.assertEqual(mode, "wpf")
        self.assertEqual(popen.call_args.args[0][0], str(exe))

    def test_open_console_window_reuses_running_wpf(self):
        with (
            patch("scripts.start_desktop_console.WPF_PROJECT") as project,
            patch("scripts.start_desktop_console._wpf_process_running", return_value=True),
            patch("scripts.start_desktop_console._wpf_sources_newer_than_build", return_value=False),
            patch("scripts.start_desktop_console.subprocess.Popen") as popen,
        ):
            project.exists.return_value = True
            mode = open_console_window("http://127.0.0.1:8787/desktop_console.html", mode="auto")

        self.assertEqual(mode, "wpf")
        popen.assert_not_called()

    def test_open_console_window_restarts_running_wpf_when_sources_are_newer(self):
        with (
            patch("scripts.start_desktop_console.WPF_PROJECT") as project,
            patch("scripts.start_desktop_console._wpf_process_running", return_value=True),
            patch("scripts.start_desktop_console._wpf_sources_newer_than_build", return_value=True),
            patch("scripts.start_desktop_console._pid_for_process_match", return_value=5172),
            patch("scripts.start_desktop_console._terminate_pid", return_value=True) as terminate,
            patch("scripts.start_desktop_console.subprocess.Popen") as popen,
        ):
            project.exists.return_value = True
            mode = open_console_window("http://127.0.0.1:8787/desktop_console.html", mode="auto")

        self.assertEqual(mode, "wpf-updated")
        terminate.assert_called_once_with(5172)
        popen.assert_called_once()

    def test_build_wpf_command_prefers_compiled_exe(self):
        with (
            patch("scripts.start_desktop_console.WPF_BUILD_EXE") as exe,
            patch("scripts.start_desktop_console._wpf_sources_newer_than_build", return_value=False),
        ):
            exe.exists.return_value = True
            exe.__str__.return_value = r"C:\SpiritKinDesktop.exe"

            command = build_wpf_command()

        self.assertEqual(command, [str(exe)])

    def test_build_wpf_command_rebuilds_when_sources_are_newer(self):
        with (
            patch("scripts.start_desktop_console.WPF_BUILD_EXE") as exe,
            patch("scripts.start_desktop_console.WPF_PROJECT", Path(r"C:\SpiritKinDesktop.csproj")),
            patch("scripts.start_desktop_console.WPF_ASSETS_FILE") as assets,
            patch("scripts.start_desktop_console._wpf_sources_newer_than_build", return_value=True),
        ):
            exe.exists.return_value = True
            assets.exists.return_value = True

            command = build_wpf_command()

        self.assertEqual(command[:2], ["dotnet", "run"])
        self.assertIn(r"C:\SpiritKinDesktop.csproj", command)
        self.assertIn("--no-restore", command)

    def test_build_wpf_command_restores_when_state_maintenance_removed_assets(self):
        with (
            patch("scripts.start_desktop_console.WPF_BUILD_EXE") as exe,
            patch("scripts.start_desktop_console.WPF_PROJECT", Path(r"C:\SpiritKinDesktop.csproj")),
            patch("scripts.start_desktop_console.WPF_ASSETS_FILE") as assets,
            patch("scripts.start_desktop_console._wpf_sources_newer_than_build", return_value=True),
        ):
            exe.exists.return_value = True
            assets.exists.return_value = False

            command = build_wpf_command()

        self.assertEqual(command[:2], ["dotnet", "run"])
        self.assertNotIn("--no-restore", command)

    def test_build_wpf_command_falls_back_to_dotnet_run(self):
        with (
            patch("scripts.start_desktop_console.WPF_BUILD_EXE") as exe,
            patch("scripts.start_desktop_console.WPF_PROJECT", Path(r"C:\SpiritKinDesktop.csproj")),
        ):
            exe.exists.return_value = False

            command = build_wpf_command()

        self.assertEqual(command[:2], ["dotnet", "run"])
        self.assertIn(r"C:\SpiritKinDesktop.csproj", command)

    def test_status_lines_report_launch_state_ports(self):
        state = {"url": "http://127.0.0.1:8787/desktop_console.html", "open_mode": "edge-app", "host": "127.0.0.1", "frontend_port": 8787}

        with (
            patch("scripts.start_desktop_console._port_accepts_connection", return_value=True),
            patch(
                "scripts.start_desktop_console.resolve_cosyvoice_service_config",
                return_value=CosyVoiceServiceConfig(selected=False, available=False),
            ),
        ):
            lines = status_lines(state)

        self.assertIn("official_desktop=wpf-native", lines[0])
        self.assertIn("compat_console=desktop_console.html", lines[1])
        self.assertTrue(any("workspace_root=" in line for line in lines))
        self.assertTrue(any("desktop_state_path=" in line for line in lines))
        self.assertTrue(any("url=http://127.0.0.1:8787/desktop_console.html" in line for line in lines))
        self.assertTrue(any("open_mode=edge-app" in line for line in lines))
        self.assertTrue(any("frontend_port=8787 listening=True" in line for line in lines))
        self.assertIn("voice_session running=", lines[-1])

    def test_status_lines_report_selected_cosyvoice_runtime(self):
        state = {"url": "http://127.0.0.1:8787/desktop_console.html", "open_mode": "wpf", "host": "127.0.0.1"}
        config = CosyVoiceServiceConfig(selected=True, available=True, port=50000, command=("python", "service.py"))

        with (
            patch("scripts.start_desktop_console.resolve_cosyvoice_service_config", return_value=config),
            patch("scripts.start_desktop_console._cosyvoice_healthy", return_value=True),
            patch("scripts.start_desktop_console._pid_for_listening_port", return_value=24704),
            patch("scripts.start_desktop_console._pid_for_process_match", return_value=None),
        ):
            lines = status_lines(state)

        self.assertIn("cosyvoice configured=True ready=True port=50000 pid=24704", lines[-1])

    def test_stop_launch_services_only_stops_started_records(self):
        state = {
            "services": [
                {"name": "bridge", "status": "started", "pid": 101},
                {"name": "frontend", "status": "reused", "pid": 0},
                {"name": "voice_session", "status": "skipped", "pid": 0},
            ]
        }
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "desktop_console.json"
            state_file.write_text("{}", encoding="utf-8")

            with patch("scripts.start_desktop_console._terminate_pid", return_value=True) as terminate:
                lines = stop_launch_services(state, state_file=state_file)

        terminate.assert_called_once_with(101)
        self.assertFalse(state_file.exists())
        self.assertIn("bridge pid=101 stopped", lines[0])
        self.assertIn("frontend pid=0 skipped status=reused", lines[1])
        self.assertIn("voice_session pid=0 skipped status=skipped", lines[2])


if __name__ == "__main__":
    unittest.main()
