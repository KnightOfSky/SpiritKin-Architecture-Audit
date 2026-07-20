from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.executors.base import ExecutionRequest
from backend.orchestrator.execution_guard import ExecutionGuard
from backend.orchestrator.pending_execution_store import PendingExecutionStore
from backend.security.policy import PolicyEngine, PolicyRule
from backend.tools.base import ToolSpec


class ExecutionGuardTests(unittest.TestCase):
    def test_resolves_risk_from_tool_spec(self):
        request = ExecutionRequest("local_pc", "file_write")
        tools = [ToolSpec(name="file.write", description="write", target="local_pc", operation="file_write", risk_level="high")]

        self.assertEqual(ExecutionGuard.resolve_risk_level(request, tools), "high")

    def test_resolves_browser_worker_binding_risk_from_tool_spec(self):
        tools = [ToolSpec(name="browser.open_url", description="open", target="local_pc", operation="browser_open_url", risk_level="medium")]

        self.assertEqual(ExecutionGuard.resolve_risk_level(ExecutionRequest("browser", "browser_open_url"), tools), "medium")
        self.assertEqual(
            ExecutionGuard.resolve_risk_level(ExecutionRequest("remote:office-pc", "browser_open_url", {"remote_target": "browser"}), tools),
            "medium",
        )

    def test_policy_can_require_confirmation(self):
        policy = PolicyEngine([
            PolicyRule(
                rule_id="confirm-write",
                description="confirm write",
                target_pattern="local_pc",
                operation_pattern="file_write",
                risk_levels=("medium",),
                require_confirmation=True,
                priority=1,
            )
        ])
        guard = ExecutionGuard(policy_engine=policy)
        request = ExecutionRequest("local_pc", "file_write")

        self.assertTrue(guard.requires_confirmation(request, []))

    def test_confirmation_decision_recognizes_confirm_cancel_and_unknown(self):
        guard = ExecutionGuard()

        self.assertTrue(guard.decide_confirmation("确认执行").confirmed)
        self.assertTrue(guard.decide_confirmation("取消执行").cancelled)
        self.assertEqual(guard.decide_confirmation("等一下").status, "unknown")

    def test_pending_repair_continuation_persists_across_store_reload(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.json"
            guard = ExecutionGuard()
            pending = guard.build_pending_execution(
                ExecutionRequest("python", "python.install_package", {"package": "requests==2.32.3"}),
                [
                    ToolSpec(
                        name="python.install_package",
                        description="install",
                        target="python",
                        operation="python.install_package",
                        risk_level="high",
                    )
                ],
                original_user_input="运行脚本",
                continuation_request=ExecutionRequest("python", "python.run", {"script_path": "demo.py"}),
            )
            PendingExecutionStore(path).save(pending)

            restored = PendingExecutionStore(path).load()

        self.assertIsNotNone(restored)
        self.assertEqual(restored.request.operation, "python.install_package")
        self.assertEqual(restored.continuation_request.operation, "python.run")
        self.assertEqual(restored.continuation_request.params, {"script_path": "demo.py"})


if __name__ == "__main__":
    unittest.main()
