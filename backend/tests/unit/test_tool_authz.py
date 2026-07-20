import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.command_gateway import (
    build_desktop_tool_authorization_response,
    build_desktop_tool_authorization_update_response,
)
from backend.security.safety_control import SafetyDecision
from backend.security.tool_authz import ToolAuthzRegistry
from backend.tools import ExecutionTool, ToolCall, ToolRegistry, ToolSpec, build_default_tool_registry


class ToolAuthorizationTests(unittest.TestCase):
    def test_risk_policies_require_expected_confirmation_scope(self):
        with TemporaryDirectory() as tmp:
            registry = ToolAuthzRegistry(Path(tmp) / "tool_authz.json")
            network = ToolSpec("remote.fetch", "fetch", "remote", "fetch", risk_level="medium")
            shell = ToolSpec("shell.run", "run", "shell", "run", risk_level="high")
            writable = ToolSpec("file.write", "write", "local", "file_write", risk_level="high")
            safe = ToolSpec("status.read", "status", "local", "status", read_only=True)
            for spec in (network, shell, writable, safe):
                registry.ensure_tool(spec)

            self.assertEqual(registry.evaluate("status.read").reason, "tool_authorized")
            self.assertEqual(registry.evaluate("remote.fetch").reason, "tool_confirmation_required")
            self.assertEqual(registry.evaluate("file.write").confirmation_policy, "once")
            self.assertTrue(registry.evaluate("remote.fetch", {"session_confirmed_tools": ["remote.fetch"]}).allowed)
            self.assertFalse(registry.evaluate("shell.run", {"session_confirmed_tools": ["shell.run"]}).allowed)
            self.assertTrue(registry.evaluate("shell.run", {"authz_confirmed": True}).allowed)

    def test_operator_disable_is_enforced_before_execution_safety(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tool_authz.json"
            authz = ToolAuthzRegistry(path)
            tool = ExecutionTool(ToolSpec("demo.write", "write", "demo", "write", risk_level="medium"))
            tools = ToolRegistry([tool], authz_registry=authz)
            authz.update("demo.write", enabled=False)

            with patch("backend.tools.registry.evaluate_execution_safety") as safety:
                result = tools.invoke(ToolCall("demo.write", {"value": 1}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "tool_disabled_by_operator")
        safety.assert_not_called()

    def test_authorized_tool_still_runs_existing_safety_check(self):
        with TemporaryDirectory() as tmp:
            authz = ToolAuthzRegistry(Path(tmp) / "tool_authz.json")
            tool = ExecutionTool(ToolSpec("demo.status", "status", "demo", "status", read_only=True))
            tools = ToolRegistry([tool], authz_registry=authz)

            with patch("backend.tools.registry.evaluate_execution_safety", return_value=SafetyDecision(True)) as safety:
                result = tools.invoke(ToolCall("demo.status", {}))

        self.assertTrue(result.success)
        safety.assert_called_once()
        self.assertTrue(result.metadata["tool_authz"]["allowed"])

    def test_existing_default_tools_are_imported_without_confirmation_regression(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_TOOL_AUTHZ_PATH": str(Path(tmp) / "tool_authz.json")}, clear=False):
                registry = build_default_tool_registry()
                snapshot_status, payload = build_desktop_tool_authorization_response()

        self.assertGreater(len(registry.list_specs()), 20)
        self.assertEqual(snapshot_status, 200)
        self.assertGreater(payload["tool_authorization"]["entry_count"], 20)
        legacy_entries = [entry for entry in payload["tool_authorization"]["entries"] if entry["source"] == "legacy_import"]
        self.assertTrue(legacy_entries)
        self.assertTrue(all(entry["confirmation_policy"] == "never" for entry in legacy_entries))
        music_url = next(entry for entry in payload["tool_authorization"]["entries"] if entry["tool_id"] == "music.play_url")
        self.assertEqual(music_url["risk"], "network")
        self.assertEqual(music_url["confirmation_policy"], "once")

    def test_management_endpoint_updates_enabled_state(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_TOOL_AUTHZ_PATH": str(Path(tmp) / "tool_authz.json")}, clear=False):
                build_default_tool_registry()
                status, payload = build_desktop_tool_authorization_update_response(
                    {"action": "disable_tool", "tool_id": "browser.open_url"}
                )
                refresh_status, refresh = build_desktop_tool_authorization_response()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["entry"]["tool_id"], "browser.open_url")
        self.assertFalse(payload["entry"]["enabled"])
        self.assertEqual(refresh_status, 200)
        entry = next(item for item in refresh["tool_authorization"]["entries"] if item["tool_id"] == "browser.open_url")
        self.assertFalse(entry["enabled"])

    def test_manifest_canonical_risk_reaches_authz_and_snapshot_reports_conflicts(self):
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            for root, target in ((first, "preferred"), (second, "shadowed")):
                manifest_dir = root / "fetch"
                manifest_dir.mkdir(parents=True)
                (manifest_dir / "manifest.json").write_text(
                    '{"id":"example.fetch","risk":"network","entry":{"target":"'
                    + target
                    + '","operation":"fetch"}}',
                    encoding="utf-8",
                )
            env = {
                "SPIRITKIN_TOOL_MANIFEST_ROOTS": os.pathsep.join((str(first), str(second))),
                "SPIRITKIN_TOOL_AUTHZ_PATH": str(Path(tmp) / "tool_authz.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                registry = build_default_tool_registry()
                status, payload = build_desktop_tool_authorization_response()

        entry = next(item for item in payload["tool_authorization"]["entries"] if item["tool_id"] == "example.fetch")
        self.assertEqual(registry.get("example.fetch").spec.authz_risk, "network")
        self.assertEqual(entry["risk"], "network")
        self.assertEqual(status, 200)
        discovery = payload["tool_authorization"]["manifest_discovery"]
        self.assertEqual(discovery["conflict_count"], 1)
        self.assertEqual(discovery["conflicts"][0]["resolution"], "first_manifest_root_wins")


if __name__ == "__main__":
    unittest.main()
