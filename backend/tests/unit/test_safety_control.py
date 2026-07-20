import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.agents.base import AgentReply
from backend.app.command_gateway import (
    build_command_response,
    build_desktop_safety_response,
    build_desktop_safety_update_response,
)
from backend.app.runtime import SpiritKinRuntime
from backend.orchestrator.workflow_graph import (
    RUN_BLOCKED,
    WorkflowDefinition,
    WorkflowNodeDefinition,
    WorkflowRunner,
    start_workflow_run,
)
from backend.security.safety_control import (
    HARD_STOP_RESUME_CONFIRMATION,
    build_safety_snapshot,
    evaluate_gateway_request_safety,
)
from backend.skills import SkillRegistry, SkillRunner, SkillSpec, SkillStepSpec
from backend.tools import BaseTool, ExecutionTool, ToolCall, ToolRegistry, ToolResult, ToolSpec


class FakeAgent:
    def process(self, user_input, visual_context="", channel="text", input_metadata=None):
        return AgentReply(text=f"received: {user_input}", agent_name="fake")


class CountingTool(BaseTool):
    def __init__(self):
        self.spec = ToolSpec("demo.write", "write", "demo", "write", risk_level="medium")
        self.calls = 0

    def invoke(self, call: ToolCall) -> ToolResult:
        self.calls += 1
        return ToolResult(True, "called", data={"arguments": call.arguments})


class SafetyControlTests(unittest.TestCase):
    def test_safety_stop_blocks_command_tool_skill_and_workflow_execution(self):
        with TemporaryDirectory() as tmp:
            safety_path = str(Path(tmp) / "kill_switch.json")
            with patch.dict(os.environ, {"SPIRITKIN_SAFETY_STATE_PATH": safety_path}):
                status, payload = build_desktop_safety_response()
                self.assertEqual(status, 200)
                self.assertFalse(payload["safety"]["active"])

                stop_status, stop_payload = build_desktop_safety_update_response(
                    {"action": "panic_stop", "reason": "unit test", "actor": "tester"}
                )
                self.assertEqual(stop_status, 200)
                self.assertTrue(stop_payload["safety"]["active"])
                self.assertEqual(stop_payload["safety"]["mode"], "soft_stop")

                runtime = SpiritKinRuntime(agent=FakeAgent(), emit_runtime_events=False)
                command_status, command_payload = build_command_response(runtime, {"text": "执行一个动作"}, client_id="desktop")
                self.assertEqual(command_status, 423)
                self.assertEqual(command_payload["error"], "safety_stop_active")
                self.assertEqual(command_payload["safety"]["safety"]["history"][-1]["action"], "blocked_execution")
                self.assertEqual(command_payload["safety"]["safety"]["history"][-1]["metadata"]["target"], "command_gateway")

                tool = CountingTool()
                registry = ToolRegistry([tool])
                tool_result = registry.invoke(ToolCall("demo.write", {"value": 1}))
                self.assertFalse(tool_result.success)
                self.assertEqual(tool_result.error_code, "safety_stop_active")
                self.assertEqual(tool.calls, 0)
                self.assertEqual(tool_result.metadata["safety"]["safety"]["history"][-1]["metadata"]["target"], "demo")

                read_only = ToolRegistry(
                    [ExecutionTool(ToolSpec("demo.status", "status", "demo", "status", read_only=True))]
                )
                read_only_result = read_only.invoke(ToolCall("demo.status", {}))
                self.assertTrue(read_only_result.success)

                skill = SkillSpec(
                    name="demo.skill",
                    description="demo",
                    steps=(SkillStepSpec("demo.write", {"value": 1}),),
                    tool_allowlist=("demo.write",),
                )
                skill_runner = SkillRunner(SkillRegistry([skill]), registry)
                skill_result = skill_runner.run("demo.skill")
                self.assertFalse(skill_result.success)
                self.assertEqual(skill_result.metadata["error_code"], "safety_stop_active")

                dry_run_result = skill_runner.run("demo.skill", dry_run=True)
                self.assertTrue(dry_run_result.success)
                self.assertIn("planned_steps", dry_run_result.metadata)

                definition = WorkflowDefinition(
                    name="demo.workflow",
                    nodes=(WorkflowNodeDefinition("write", "tool_call", tool_name="demo.write"),),
                )
                run = start_workflow_run(definition)
                blocked = WorkflowRunner(tool_registry=registry).run_next(definition, run)
                self.assertEqual(blocked.status, RUN_BLOCKED)
                self.assertEqual(blocked.events[-1]["type"], "run_blocked_by_safety")

                resume_status, resume_payload = build_desktop_safety_update_response(
                    {"action": "resume", "reason": "done", "actor": "tester"}
                )
                self.assertEqual(resume_status, 200)
                self.assertFalse(resume_payload["safety"]["active"])

                allowed_result = registry.invoke(ToolCall("demo.write", {"value": 2}))
                self.assertTrue(allowed_result.success)
                self.assertEqual(tool.calls, 1)

    def test_hard_stop_blocks_non_recovery_post_routes(self):
        with TemporaryDirectory() as tmp:
            safety_path = str(Path(tmp) / "kill_switch.json")
            with patch.dict(os.environ, {"SPIRITKIN_SAFETY_STATE_PATH": safety_path}):
                build_desktop_safety_update_response({"action": "hard_stop", "reason": "unit test"})

                blocked = evaluate_gateway_request_safety(path="/desktop/skills", method="POST")
                self.assertFalse(blocked.allowed)
                self.assertEqual(blocked.error_code, "safety_hard_stop_active")
                self.assertEqual(blocked.snapshot()["safety"]["history"][-1]["action"], "blocked_gateway_post")
                self.assertEqual(blocked.snapshot()["safety"]["history"][-1]["metadata"]["path"], "/desktop/skills")

                recovery = evaluate_gateway_request_safety(path="/desktop/safety", method="POST")
                self.assertTrue(recovery.allowed)

                readonly = evaluate_gateway_request_safety(path="/desktop/skills", method="GET")
                self.assertTrue(readonly.allowed)

    def test_hard_stop_resume_requires_explicit_confirmation(self):
        with TemporaryDirectory() as tmp:
            safety_path = str(Path(tmp) / "kill_switch.json")
            with patch.dict(os.environ, {"SPIRITKIN_SAFETY_STATE_PATH": safety_path}):
                build_desktop_safety_update_response({"action": "hard_stop", "reason": "unit test", "actor": "tester"})

                missing_status, missing_payload = build_desktop_safety_update_response(
                    {"action": "resume", "reason": "missing confirmation", "actor": "tester"}
                )
                self.assertEqual(missing_status, 400)
                self.assertIn(HARD_STOP_RESUME_CONFIRMATION, missing_payload["detail"])
                self.assertTrue(build_safety_snapshot()["active"])

                resume_status, resume_payload = build_desktop_safety_update_response(
                    {
                        "action": "resume",
                        "reason": "confirmed",
                        "actor": "tester",
                        "confirmation_text": HARD_STOP_RESUME_CONFIRMATION,
                    }
                )

        self.assertEqual(resume_status, 200)
        self.assertFalse(resume_payload["safety"]["active"])


if __name__ == "__main__":
    unittest.main()
