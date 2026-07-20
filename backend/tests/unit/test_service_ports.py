import os
import tempfile
import unittest
from unittest.mock import patch

from backend.app.command_gateway import build_desktop_service_ports_update_response
from backend.app.service_ports import (
    build_default_service_env,
    build_service_port_snapshot,
    handle_service_port_action,
    load_service_port_config,
    resolve_service_port,
)


class ServicePortsTests(unittest.TestCase):
    def test_service_ports_resolve_defaults_and_env_overrides(self):
        with patch.dict(os.environ, {}, clear=True):
            snapshot = build_service_port_snapshot(include_conflicts=False)

        self.assertEqual(resolve_service_port("frontend", 0), 8787)
        self.assertEqual(snapshot["ports"]["frontend"], 8787)
        self.assertEqual(snapshot["ports"]["event_bridge"], 8765)
        self.assertEqual(snapshot["ports"]["command_gateway"], 8788)

        with patch.dict(os.environ, {"SPIRITKIN_FRONTEND_PORT": "18887", "SPIRITKIN_COMMAND_PORT": "18888"}):
            snapshot = build_service_port_snapshot(include_conflicts=False)

        self.assertEqual(snapshot["ports"]["frontend"], 18887)
        self.assertEqual(snapshot["ports"]["command_gateway"], 18888)
        self.assertEqual(snapshot["env_overrides"]["frontend"], "18887")

    def test_duplicate_ports_are_reported(self):
        with patch.dict(os.environ, {"SPIRITKIN_FRONTEND_PORT": "18888", "SPIRITKIN_COMMAND_PORT": "18888"}):
            snapshot = build_service_port_snapshot(include_conflicts=False)

        self.assertIn(18888, snapshot["duplicate_ports"])
        self.assertEqual(set(snapshot["duplicate_ports"][18888]), {"frontend", "command_gateway"})

    def test_default_service_env_uses_resolved_ports(self):
        with patch.dict(os.environ, {"SPIRITKIN_EVENTS_PORT": "17665", "SPIRITKIN_COMMAND_PORT": "18788"}):
            env = build_default_service_env(host="0.0.0.0", token="unit-token")

        self.assertEqual(env["SPIRITKIN_EVENTS_HOST"], "127.0.0.1")
        self.assertEqual(env["SPIRITKIN_EVENTS_WS_URL"], "ws://127.0.0.1:17665")
        self.assertEqual(env["SPIRITKIN_COMMAND_PORT"], "18788")
        self.assertEqual(env["SPIRITKIN_MOBILE_TOKEN"], "unit-token")

    def test_config_override_is_persisted_and_used_when_no_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "ports.json")
            with patch.dict(os.environ, {"SPIRITKIN_SERVICE_PORT_CONFIG_PATH": config_path}, clear=True):
                result = handle_service_port_action({"action": "save_port", "service_id": "frontend", "port": 19887})

                self.assertTrue(result["ok"])
                self.assertTrue(result["restart_guidance"]["restart_required"])
                self.assertIn("frontend", result["restart_guidance"]["managed_service_ids"])
                self.assertEqual(resolve_service_port("frontend", 0), 19887)
                snapshot = build_service_port_snapshot(include_conflicts=False)
                service = next(item for item in snapshot["services"] if item["service_id"] == "frontend")
                self.assertEqual(service["source"], "config")
                self.assertTrue(service["editable"])
                self.assertEqual(load_service_port_config()["overrides"]["frontend"], 19887)

                reset = handle_service_port_action({"action": "reset_port", "service_id": "frontend"})

                self.assertTrue(reset["ok"])
                self.assertTrue(reset["restart_guidance"]["restart_required"])
                self.assertIn("frontend", reset["restart_guidance"]["service_ids"])
                self.assertEqual(resolve_service_port("frontend", 0), 8787)

    def test_env_override_wins_over_config_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "ports.json")
            with patch.dict(os.environ, {"SPIRITKIN_SERVICE_PORT_CONFIG_PATH": config_path, "SPIRITKIN_FRONTEND_PORT": "18887"}, clear=True):
                result = handle_service_port_action({"action": "save_port", "service_id": "frontend", "port": 19887})
                snapshot = build_service_port_snapshot(include_conflicts=False)
                service = next(item for item in snapshot["services"] if item["service_id"] == "frontend")

                self.assertFalse(result["restart_guidance"]["restart_required"])
                self.assertEqual(result["restart_guidance"]["blocked_by_env"][0]["service_id"], "frontend")
                self.assertEqual(service["port"], 18887)
                self.assertEqual(service["source"], "env")
                self.assertFalse(service["editable"])
                self.assertEqual(service["config_value"], 19887)

    def test_repair_duplicate_ports_assigns_config_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "ports.json")
            env = {
                "SPIRITKIN_SERVICE_PORT_CONFIG_PATH": config_path,
                "SPIRITKIN_FRONTEND_PORT": "18888",
                "SPIRITKIN_COMMAND_PORT": "18888",
            }
            with patch.dict(os.environ, env, clear=True):
                result = handle_service_port_action({"action": "repair_duplicates"})

                self.assertFalse(result["ok"])
                self.assertEqual(result["changed"], {})
                self.assertEqual(result["blocked"][0]["port"], 18888)

            with patch.dict(os.environ, {"SPIRITKIN_SERVICE_PORT_CONFIG_PATH": config_path}, clear=True):
                handle_service_port_action({"action": "save_port", "service_id": "frontend", "port": 18888})
                handle_service_port_action({"action": "save_port", "service_id": "command_gateway", "port": 18888})
                result = handle_service_port_action({"action": "repair_duplicates"})

                self.assertTrue(result["ok"])
                self.assertIn("command_gateway", result["changed"])
                self.assertTrue(result["restart_guidance"]["restart_required"])
                self.assertIn("command_gateway", result["restart_guidance"]["managed_service_ids"])
                self.assertTrue(result["restart_guidance"]["migration_notes"])
                snapshot = build_service_port_snapshot(include_conflicts=False)
                self.assertEqual(snapshot["duplicate_ports"], {})

    def test_command_gateway_service_ports_update_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "ports.json")
            with patch.dict(os.environ, {"SPIRITKIN_SERVICE_PORT_CONFIG_PATH": config_path}, clear=True):
                status, payload = build_desktop_service_ports_update_response({"action": "save_port", "service_id": "event_bridge", "port": 17666})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service_ports"]["ports"]["event_bridge"], 17666)

    def test_service_port_profiles_capture_apply_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "ports.json")
            with patch.dict(os.environ, {"SPIRITKIN_SERVICE_PORT_CONFIG_PATH": config_path}, clear=True):
                handle_service_port_action({"action": "save_port", "service_id": "frontend", "port": 19887})
                saved = handle_service_port_action(
                    {
                        "action": "save_profile",
                        "profile_id": "Project A",
                        "label": "Project A ports",
                        "project_id": "project_a",
                        "workspace_path": "D:/work/project-a",
                    }
                )

                self.assertTrue(saved["ok"])
                self.assertEqual(saved["profile_id"], "project_a")
                self.assertEqual(saved["profile"]["overrides"]["frontend"], 19887)
                self.assertIn("project_a", saved["service_ports"]["config"]["profiles"])

                handle_service_port_action({"action": "save_port", "service_id": "frontend", "port": 19999})
                applied = handle_service_port_action({"action": "apply_profile", "profile_id": "project_a"})

                self.assertTrue(applied["ok"])
                self.assertEqual(resolve_service_port("frontend", 0), 19887)
                self.assertTrue(applied["restart_guidance"]["restart_required"])
                self.assertIn("frontend", applied["restart_guidance"]["service_ids"])

                deleted = handle_service_port_action({"action": "delete_profile", "profile_id": "project_a"})

                self.assertTrue(deleted["ok"])
                self.assertNotIn("project_a", load_service_port_config()["profiles"])


if __name__ == "__main__":
    unittest.main()
