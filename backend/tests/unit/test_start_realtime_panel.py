import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_frp_config import build_frp_client_config
from scripts.start_realtime_panel import (
    _apply_remote_worker_env,
    _avatar_3d_url,
    _avatar_url,
    _build_child_env,
    _build_frontend_server_command,
    _command_url,
    _default_port_status_lines,
    _detached_status_lines,
    _detect_tailscale_ip,
    _events_ws_url,
    _frontend_url,
    _frp_public_urls,
    _live2d_url,
    _mobile_avatar_3d_url,
    _mobile_avatar_url,
    _mobile_live2d_url,
    _port_accepts_connection,
    _read_detached_state,
    _service_is_listening,
    _service_log_paths,
    _service_probe_port,
    _stop_detached_services,
    _tailscale_urls,
    _websocket_bridge_healthy,
    _write_detached_state,
    build_detached_service_specs,
    build_startup_commands,
)


class StartRealtimePanelTests(unittest.TestCase):
    def test_build_startup_commands_installs_command_gateway_work_events(self):
        commands = build_startup_commands()

        self.assertEqual(commands["bridge"][-2:], ["-m", "backend.app.realtime_bridge"])
        self.assertEqual(commands["runtime"][-2:], ["-m", "backend.main"])
        self.assertEqual(commands["command_gateway"][1:3], ["-u", "-c"])
        self.assertIn("codex_work_events", commands["command_gateway"][-1])
        self.assertIn("backend.app.command_gateway", commands["command_gateway"][-1])

    def test_fast_voice_env_defaults_enable_hotword_and_llm_correction(self):
        env = _build_child_env(fast_voice=True)

        self.assertEqual(env["SPIRITKIN_HOTWORD_FAST"], "1")
        self.assertEqual(env["SPIRITKIN_HOTWORD_VAD"], "0")
        self.assertEqual(env["SPIRITKIN_VOICE_INTENT_MODE"], "first")
        self.assertEqual(env["SPIRIT_ASR_BEAM_SIZE"], "1")
        self.assertEqual(env["SPIRITKIN_WAKE_ACK_ENABLED"], "0")
        self.assertEqual(env["SPIRITKIN_VOICE_ACK_ENABLED"], "0")

    def test_frontend_server_helpers_support_lan_browser_url(self):
        command = _build_frontend_server_command("0.0.0.0", 8787)

        self.assertIn("http.server", command)
        self.assertEqual(_frontend_url("0.0.0.0", 8787), "http://127.0.0.1:8787/index.html")

    def test_detached_service_specs_allow_codex_browser_manual_open(self):
        commands = build_startup_commands()
        frontend_command = _build_frontend_server_command("127.0.0.1", 8787)

        specs = build_detached_service_specs(commands, frontend_command=frontend_command, no_runtime=True)

        self.assertEqual([name for name, _ in specs], ["bridge", "command_gateway", "frontend"])
        self.assertEqual(specs[-1][1], frontend_command)

    def test_detached_service_specs_can_skip_optional_services(self):
        specs = build_detached_service_specs(
            build_startup_commands(),
            frontend_command=["python", "-m", "http.server"],
            no_command_gateway=True,
            no_frontend_server=True,
            no_runtime=True,
        )

        self.assertEqual([name for name, _ in specs], ["bridge"])

    def test_detached_service_helpers_report_logs_and_probe_ports(self):
        out_log, err_log = _service_log_paths("frontend")

        self.assertEqual(out_log.name, "frontend.out.log")
        self.assertEqual(err_log.name, "frontend.err.log")
        self.assertEqual(_service_probe_port("bridge", events_port=8765, command_port=8788, frontend_port=8787), 8765)
        self.assertEqual(_service_probe_port("command_gateway", events_port=8765, command_port=8788, frontend_port=8787), 8788)
        self.assertEqual(_service_probe_port("frontend", events_port=8765, command_port=8788, frontend_port=8787), 8787)
        self.assertIsNone(_service_probe_port("runtime", events_port=8765, command_port=8788, frontend_port=8787))

    def test_port_accepts_connection_returns_false_when_closed(self):
        self.assertFalse(_port_accepts_connection("127.0.0.1", 9, timeout=0.01))

    def test_bridge_listening_probe_requires_websocket_snapshot(self):
        with patch("scripts.start_realtime_panel._websocket_bridge_healthy", return_value=True) as bridge_probe, patch("scripts.start_realtime_panel._port_accepts_connection") as connect_probe:
            self.assertTrue(_service_is_listening("bridge", bind_host="127.0.0.1", port=8765, token="secret"))

        bridge_probe.assert_called_once_with("127.0.0.1", 8765, token="secret")
        connect_probe.assert_not_called()

    def test_websocket_bridge_probe_rejects_plain_http_port(self):
        self.assertFalse(_websocket_bridge_healthy("127.0.0.1", 9, timeout=0.01))

    def test_detached_state_roundtrip_supports_status_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "panel.json"
            _write_detached_state(
                [{"name": "frontend", "pid": 123, "command": ["python"], "stdout": "out", "stderr": "err"}],
                bind_host="127.0.0.1",
                events_port=8765,
                command_port=8788,
                frontend_port=8787,
                frontend_url="http://127.0.0.1:8787/index.html",
                token="secret",
                state_file=state_file,
            )

            state = _read_detached_state(state_file)

        self.assertEqual(state["frontend_url"], "http://127.0.0.1:8787/index.html")
        self.assertEqual(state["mobile_token"], "secret")
        self.assertEqual(state["services"][0]["name"], "frontend")

    def test_detached_status_lines_report_process_and_port_state(self):
        state = {
            "bind_host": "127.0.0.1",
            "frontend_url": "http://127.0.0.1:8787/index.html",
            "events_port": 8765,
            "command_port": 8788,
            "frontend_port": 8787,
            "services": [{"name": "frontend", "pid": 123}],
        }

        with patch("scripts.start_realtime_panel._process_is_running", return_value=True), patch("scripts.start_realtime_panel._port_accepts_connection", return_value=True):
            lines = _detached_status_lines(state)

        self.assertIn("Frontend", lines[0])
        self.assertIn("frontend:8787 pid=123 process=running port=listening", lines[1])

    def test_default_status_lines_probe_ports_without_state_file(self):
        with patch("scripts.start_realtime_panel._port_accepts_connection", side_effect=[True, False]), patch("scripts.start_realtime_panel._service_is_listening", return_value=True), patch("scripts.start_realtime_panel._known_service_pids", return_value=[456]):
            lines = _default_port_status_lines()

        self.assertIn("默认端口探测", lines[0])
        self.assertIn("frontend:8787 port=listening", lines[1])
        self.assertIn("command_gateway:8788 port=not-listening", lines[2])
        self.assertIn("bridge:8765 port=listening", lines[3])
        self.assertIn("runtime pid=456 process=running", lines[4])

    def test_detached_status_reports_runtime_when_recorded(self):
        state = {
            "bind_host": "127.0.0.1",
            "frontend_url": "http://127.0.0.1:8787/index.html",
            "services": [{"name": "runtime", "pid": 456}],
        }

        with patch("scripts.start_realtime_panel._process_is_running", return_value=True):
            lines = _detached_status_lines(state)

        self.assertIn("runtime pid=456 process=running", lines[1])

    def test_detached_status_infers_runtime_when_state_is_missing_it(self):
        state = {
            "bind_host": "127.0.0.1",
            "frontend_url": "http://127.0.0.1:8787/index.html",
            "services": [{"name": "frontend", "pid": 123}],
        }

        with patch("scripts.start_realtime_panel._process_is_running", return_value=True), patch("scripts.start_realtime_panel._port_accepts_connection", return_value=True), patch("scripts.start_realtime_panel._known_service_pids", return_value=[]):
            lines = _detached_status_lines(state)

        self.assertIn("runtime pid=- process=stopped", lines[-1])

    def test_stop_detached_services_uses_recorded_pids_and_removes_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "panel.json"
            state_file.write_text("{}", encoding="utf-8")
            state = {"services": [{"name": "frontend", "pid": 123}]}

            with patch("scripts.start_realtime_panel._stop_process", return_value=True) as stop_process:
                lines = _stop_detached_services(state, state_file=state_file)

            stop_process.assert_called_once_with(123)
            self.assertFalse(state_file.exists())
        self.assertEqual(lines, ["[stop] frontend pid=123 stopped"])

    def test_stop_detached_services_skips_reused_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "panel.json"
            state_file.write_text("{}", encoding="utf-8")
            state = {"services": [{"name": "frontend", "pid": 123, "external": True}]}

            with patch("scripts.start_realtime_panel._stop_process") as stop_process:
                lines = _stop_detached_services(state, state_file=state_file)

            stop_process.assert_not_called()
        self.assertEqual(lines, ["[stop] frontend pid=123 skipped-reused"])

    def test_live2d_url_uses_same_frontend_server(self):
        self.assertEqual(_avatar_url("0.0.0.0", 8787), "http://127.0.0.1:8787/spirit_avatar.html")
        self.assertEqual(_avatar_3d_url("0.0.0.0", 8787), "http://127.0.0.1:8787/avatar_3d.html")
        self.assertEqual(_live2d_url("0.0.0.0", 8787), "http://127.0.0.1:8787/live2d.html")
        self.assertEqual(_live2d_url("192.168.1.9", 8787), "http://192.168.1.9:8787/live2d.html")

    def test_public_urls_support_mobile_data_or_tunnel_access(self):
        self.assertEqual(_frontend_url("0.0.0.0", 8787, "https://panel.example.com"), "https://panel.example.com/index.html")
        self.assertEqual(_avatar_url("0.0.0.0", 8787, "https://panel.example.com"), "https://panel.example.com/spirit_avatar.html")
        self.assertEqual(_avatar_3d_url("0.0.0.0", 8787, "https://panel.example.com"), "https://panel.example.com/avatar_3d.html")
        self.assertEqual(_live2d_url("0.0.0.0", 8787, "https://panel.example.com"), "https://panel.example.com/live2d.html")
        self.assertEqual(_events_ws_url("0.0.0.0", 8765, "wss://events.example.com/ws"), "wss://events.example.com/ws")
        self.assertEqual(_command_url("0.0.0.0", 8788, "https://api.example.com/command"), "https://api.example.com/command")
        self.assertIn("avatar_3d.html", _mobile_avatar_3d_url("https://panel.example.com/avatar_3d.html", "wss://events.example.com/ws", "https://api.example.com/command"))
        self.assertIn("config=models%2Fspirit3d%2Fmanifest.json", _mobile_avatar_3d_url("https://panel.example.com/avatar_3d.html", "wss://events.example.com/ws", "https://api.example.com/command"))
        self.assertIn("wss%3A%2F%2Fevents.example.com%2Fws", _mobile_live2d_url("https://panel.example.com/live2d.html", "wss://events.example.com/ws"))
        self.assertIn("cmd=https%3A%2F%2Fapi.example.com%2Fcommand", _mobile_avatar_url("https://panel.example.com/spirit_avatar.html", "wss://events.example.com/ws", "https://api.example.com/command"))
        self.assertIn("token=abc", _mobile_avatar_url("https://panel.example.com/spirit_avatar.html", "wss://events.example.com/ws", "https://api.example.com/command", "abc"))
        self.assertIn("token=abc", _mobile_avatar_3d_url("https://panel.example.com/avatar_3d.html", "wss://events.example.com/ws", "https://api.example.com/command", token="abc"))
        self.assertIn("token=abc", _mobile_live2d_url("https://panel.example.com/live2d.html", "wss://events.example.com/ws", "abc"))

    def test_frp_public_urls_derive_three_public_entrypoints(self):
        urls = _frp_public_urls("example.com", prefix="spirit", https=True)

        self.assertEqual(urls["frontend"], "https://spirit.example.com/index.html")
        self.assertIn("spirit_avatar.html", urls["avatar"])
        self.assertIn("avatar_3d.html", urls["avatar_3d"])
        self.assertIn("config=models%2Fspirit3d%2Fmanifest.json", urls["avatar_3d"])
        self.assertEqual(urls["websocket"], "wss://spirit-events.example.com")
        self.assertEqual(urls["command"], "https://spirit-command.example.com/command")
        self.assertIn("wss%3A%2F%2Fspirit-events.example.com", urls["live2d"])

    def test_generate_frp_config_maps_local_spiritkin_ports(self):
        config = build_frp_client_config(
            server_addr="frp.example.com",
            token="secret",
            domain_suffix="example.com",
            prefix="spirit",
            remote_worker_port=8790,
        )

        self.assertIn('serverAddr = "frp.example.com"', config)
        self.assertIn('token = "secret"', config)
        self.assertIn('customDomains = ["spirit.example.com"]', config)
        self.assertIn('customDomains = ["spirit-events.example.com"]', config)
        self.assertIn('customDomains = ["spirit-command.example.com"]', config)
        self.assertIn('customDomains = ["spirit-worker.example.com"]', config)

    def test_tailscale_url_helpers_support_mobile_data_access(self):
        urls = _tailscale_urls("100.64.0.8", frontend_port=8787, events_port=8765, command_port=8788)

        self.assertEqual(urls["frontend"], "http://100.64.0.8:8787/index.html")
        self.assertIn("spirit_avatar.html", urls["avatar"])
        self.assertIn("avatar_3d.html", urls["avatar_3d"])
        self.assertIn("config=models%2Fspirit3d%2Fmanifest.json", urls["avatar_3d"])
        self.assertEqual(urls["websocket"], "ws://100.64.0.8:8765")
        self.assertEqual(urls["command"], "http://100.64.0.8:8788/command")
        self.assertIn("ws%3A%2F%2F100.64.0.8%3A8765", urls["live2d"])

    def test_detect_tailscale_ip_reads_first_ipv4_line(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "100.64.0.8\n"})()

        with patch("scripts.start_realtime_panel.subprocess.run", return_value=completed):
            self.assertEqual(_detect_tailscale_ip(), "100.64.0.8")

    def test_detect_tailscale_ip_returns_empty_when_command_missing(self):
        with patch("scripts.start_realtime_panel.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(_detect_tailscale_ip(), "")

    def test_apply_remote_worker_env_sets_central_runtime_connection(self):
        env = {}

        _apply_remote_worker_env(
            env,
            url="http://100.64.0.8:8790/",
            node_id="office-pc",
            token="secret",
            aliases="公司电脑,office",
        )

        self.assertEqual(env["SPIRITKIN_REMOTE_WORKER_URL"], "http://100.64.0.8:8790")
        self.assertEqual(env["SPIRITKIN_REMOTE_WORKER_NODE_ID"], "office-pc")
        self.assertEqual(env["SPIRITKIN_REMOTE_WORKER_TOKEN"], "secret")
        self.assertEqual(env["SPIRITKIN_REMOTE_WORKER_ALIASES"], "公司电脑,office")


if __name__ == "__main__":
    unittest.main()
