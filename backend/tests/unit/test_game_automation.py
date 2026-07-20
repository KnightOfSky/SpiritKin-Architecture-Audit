from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.game_automation.manager import GameAutomationManager
from backend.game_automation.manifest import (
    GameAdapterManifest,
    load_game_adapter_manifests,
    parse_game_adapter_manifest,
)
from backend.game_automation.session import (
    GameAutomationAudit,
    GameAutomationSession,
    GameAutomationStatus,
    GameAutomationStep,
)
from backend.security.safety_control import SafetyDecision
from backend.security.tool_authz import ToolAuthzRegistry
from backend.tools.game_automation_tools import get_game_automation_tools
from backend.tools.registry import ToolRegistry


class FakeGameDriver:
    def __init__(self, *, focused=True, scene="collect_run", title="SpiritKin Automation Demo"):
        self.focused = focused
        self.scene = scene
        self.game_title = title
        self.uncertain = False
        self.actions = []
        self.stops = []
        self.closed = False

    def title(self):
        return self.game_title

    def is_focused(self):
        return self.focused

    def observe(self):
        return {"scene": self.scene, "uncertain": self.uncertain}

    def perform(self, action, params):
        self.actions.append((action, params))
        return {"ok": True, "action": action}

    def emergency_stop(self, reason):
        self.stops.append(reason)

    def capture_keyframe(self, label):
        return {"path": f"{label}.png", "sha256": "abc"}

    def close(self):
        self.closed = True


def adapter(max_rate=5.0):
    return GameAdapterManifest(
        adapter_id="game.local_collect_demo",
        label="Demo",
        allowed_origins=("http://127.0.0.1:*",),
        allowed_paths=("/game_automation_demo.html",),
        title_pattern="^SpiritKin Automation Demo$",
        expected_scenes=("collect_run",),
        allowed_actions=("move_right", "collect"),
        max_actions_per_second=max_rate,
    )


class GameAutomationTests(unittest.TestCase):
    def _session(self, tmp, driver=None, *, max_rate=5.0, safety=None, clock=time.monotonic):
        return GameAutomationSession(
            adapter(max_rate),
            driver or FakeGameDriver(),
            audit=GameAutomationAudit(Path(tmp) / "audit.jsonl"),
            session_id="game_session_unit",
            authorized=lambda _action, _params: True,
            safety=safety or (lambda **_: SafetyDecision(True)),
            clock=clock,
        )

    def test_shipped_manifest_only_accepts_local_demo_target(self):
        manifests = load_game_adapter_manifests()
        demo = manifests["game.local_collect_demo"]

        self.assertTrue(demo.accepts_url("http://127.0.0.1:8123/game_automation_demo.html"))
        self.assertFalse(demo.accepts_url("https://example.com/game_automation_demo.html"))
        self.assertTrue(demo.accepts_title("SpiritKin Automation Demo"))
        self.assertFalse(demo.accepts_title("Third Party Online Game"))

    def test_manifest_rejects_forbidden_raw_input_action(self):
        payload = json.loads((Path("config/game_automation/adapters/local_collect_demo.json")).read_text(encoding="utf-8"))
        payload["allowed_actions"] = ["raw_key"]

        with self.assertRaisesRegex(ValueError, "forbidden"):
            parse_game_adapter_manifest(payload)

    def test_default_empty_allowlist_prevents_driver_launch(self):
        with TemporaryDirectory() as tmp:
            launched = []
            manager = GameAutomationManager(
                allowlist=frozenset(),
                driver_factory=lambda *_args, **_kwargs: launched.append(True),
                audit_root=Path(tmp) / "audit",
            )

            with self.assertRaisesRegex(PermissionError, "not allowlisted"):
                manager.run_plan(
                    adapter_id="game.local_collect_demo",
                    url="http://127.0.0.1:8123/game_automation_demo.html",
                    steps=[{"action": "move_right"}],
                    session_id="game_session_blocked",
                    headless=True,
                )

        self.assertEqual(launched, [])

    def test_focus_loss_stops_before_input(self):
        with TemporaryDirectory() as tmp:
            driver = FakeGameDriver(focused=False)
            session = self._session(tmp, driver)
            session.start("http://127.0.0.1:8123/game_automation_demo.html")

            accepted = session.execute(GameAutomationStep("move_right"))

        self.assertFalse(accepted)
        self.assertEqual(session.stop_reason, "focus_lost")
        self.assertEqual(driver.actions, [])

    def test_kill_switch_stops_before_input(self):
        with TemporaryDirectory() as tmp:
            driver = FakeGameDriver()
            session = self._session(
                tmp,
                driver,
                safety=lambda **_: SafetyDecision(False, error_code="execution_hard_stopped", message="全局停止"),
            )
            session.start("http://127.0.0.1:8123/game_automation_demo.html")

            accepted = session.execute(GameAutomationStep("move_right"))

        self.assertFalse(accepted)
        self.assertEqual(session.stop_reason, "execution_hard_stopped")
        self.assertEqual(driver.actions, [])

    def test_unknown_scene_pauses_before_input(self):
        with TemporaryDirectory() as tmp:
            driver = FakeGameDriver(scene="dialog_unknown")
            session = self._session(tmp, driver)
            session.start("http://127.0.0.1:8123/game_automation_demo.html")

            accepted = session.execute(GameAutomationStep("move_right"))

        self.assertFalse(accepted)
        self.assertEqual(session.status, GameAutomationStatus.PAUSED)
        self.assertEqual(session.stop_reason, "unknown_scene")
        self.assertEqual(driver.actions, [])

    def test_rate_limit_pauses_session(self):
        with TemporaryDirectory() as tmp:
            now = [10.0]
            driver = FakeGameDriver()
            session = self._session(tmp, driver, max_rate=1, clock=lambda: now[0])
            session.start("http://127.0.0.1:8123/game_automation_demo.html")

            first = session.execute(GameAutomationStep("move_right", delay_after_ms=0))
            second = session.execute(GameAutomationStep("collect", delay_after_ms=0))

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(session.stop_reason, "rate_limit")
        self.assertEqual(len(driver.actions), 1)

    def test_dangerous_raw_coordinates_are_rejected(self):
        with TemporaryDirectory() as tmp:
            driver = FakeGameDriver()
            session = self._session(tmp, driver)
            session.start("http://127.0.0.1:8123/game_automation_demo.html")

            accepted = session.execute(GameAutomationStep("move_right", {"x": 500, "y": 200}))

        self.assertFalse(accepted)
        self.assertEqual(session.stop_reason, "dangerous_input_rejected")
        self.assertEqual(driver.actions, [])

    def test_audit_replay_contains_keyframe_action_and_stop_reason(self):
        with TemporaryDirectory() as tmp:
            driver = FakeGameDriver()
            session = self._session(tmp, driver)
            session.start("http://127.0.0.1:8123/game_automation_demo.html")
            session.execute(GameAutomationStep("move_right", delay_after_ms=0))
            session.request_stop("user_stop")

            events = session.audit.replay()

        self.assertIn("action.completed", [event["type"] for event in events])
        stopped = next(event for event in events if event["type"] == "session.stopped")
        self.assertEqual(stopped["reason"], "user_stop")
        action = next(event for event in events if event["type"] == "action.completed")
        self.assertEqual(action["keyframe"]["sha256"], "abc")

    def test_external_stop_interrupts_delay_under_200ms(self):
        with TemporaryDirectory() as tmp:
            session = self._session(tmp)
            session.start("http://127.0.0.1:8123/game_automation_demo.html")
            result = []
            thread = threading.Thread(
                target=lambda: result.append(session.execute(GameAutomationStep("move_right", delay_after_ms=5000))),
            )
            thread.start()
            time.sleep(0.03)
            started = time.perf_counter()
            session.request_stop("global_hotkey")
            thread.join(timeout=1)
            elapsed_ms = (time.perf_counter() - started) * 1000

        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed_ms, 200)
        self.assertEqual(result, [False])

    def test_allowlisted_manager_runs_structured_plan(self):
        with TemporaryDirectory() as tmp:
            driver = FakeGameDriver()
            manager = GameAutomationManager(
                allowlist=frozenset({"game.local_collect_demo"}),
                driver_factory=lambda *_args, **_kwargs: driver,
                audit_root=Path(tmp) / "audit",
            )

            result = manager.run_plan(
                adapter_id="game.local_collect_demo",
                url="http://127.0.0.1:8123/game_automation_demo.html",
                steps=[{"action": "move_right", "delay_after_ms": 0}, {"action": "collect", "delay_after_ms": 0}],
                session_id="game_session_allowed",
                headless=True,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["action_count"], 2)
        self.assertTrue(driver.closed)

    def test_game_run_tool_is_high_risk_and_stop_is_immediate_safe_control(self):
        with TemporaryDirectory() as tmp:
            authz = ToolAuthzRegistry(Path(tmp) / "authz.json")
            registry = ToolRegistry(authz_registry=authz)
            registry.register_many(get_game_automation_tools(GameAutomationManager(allowlist=frozenset())))
            entries = {entry["tool_id"]: entry for entry in authz.snapshot()["entries"]}

        self.assertEqual(entries["game.automation.run"]["risk"], "shell")
        self.assertEqual(entries["game.automation.run"]["confirmation_policy"], "always")
        self.assertEqual(entries["game.automation.stop"]["risk"], "safe")
        self.assertEqual(entries["game.automation.stop"]["confirmation_policy"], "never")


if __name__ == "__main__":
    unittest.main()
