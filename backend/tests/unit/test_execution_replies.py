from __future__ import annotations

import unittest

from backend.orchestrator.execution_replies import (
    build_confirmation_mismatch_reply,
    build_duplicate_confirmation_reply,
    build_execution_failure_reply,
    build_execution_success_reply,
    build_executor_missing_reply,
    build_no_pending_confirmation_reply,
    build_policy_denied_reply,
    policy_decision_snapshot,
    worker_display_name,
)


class FakeRequest:
    target = "local_pc"
    operation = "open_app"


class FakePolicyDecision:
    allowed = False
    require_confirmation = True
    reason = "危险操作"
    matched_rule_id = "rule-1"


class FakeWorker:
    label = "python_worker"

    def snapshot(self):
        return {"worker_id": "w-1"}


class FakeAuditEvent:
    def snapshot(self):
        return {"event": "audit"}


class FakeWorkerExecution:
    def __init__(self, worker=None):
        self.worker = worker
        self.audit_event = FakeAuditEvent()


class FakeResult:
    def __init__(self, message="done", error_code=""):
        self.message = message
        self.error_code = error_code
        self.data = {"out": 1}
        self.metadata = {"meta": True}


class FakeWorkflowRecord:
    def snapshot(self):
        return {"record": "wf"}


class FakePending:
    request = FakeRequest()


class SnapshotHelpersTests(unittest.TestCase):
    def test_policy_decision_snapshot(self):
        self.assertEqual(
            policy_decision_snapshot(FakePolicyDecision()),
            {"allowed": False, "require_confirmation": True, "reason": "危险操作", "matched_rule_id": "rule-1"},
        )

    def test_worker_display_name_prefers_worker_label(self):
        self.assertEqual(worker_display_name(FakeWorkerExecution(FakeWorker()), "fallback"), "python_worker")
        self.assertEqual(worker_display_name(FakeWorkerExecution(None), "fallback"), "fallback")


class ExecutionReplyTests(unittest.TestCase):
    def test_executor_missing_reply(self):
        reply = build_executor_missing_reply(FakeRequest())
        self.assertIn("local_pc.open_app", reply.text)
        self.assertEqual(reply.agent_name, "executor_missing")

    def test_policy_denied_reply_includes_trajectory_only_when_present(self):
        reply = build_policy_denied_reply(FakeRequest(), FakePolicyDecision(), {"step": 1})
        self.assertEqual(reply.metadata["response_kind"], "policy_denied")
        self.assertEqual(reply.metadata["policy_decision"]["matched_rule_id"], "rule-1")
        self.assertEqual(reply.metadata["execution"]["error_code"], "policy_denied")
        self.assertEqual(reply.metadata["trajectory_record"], {"step": 1})
        bare = build_policy_denied_reply(FakeRequest(), FakePolicyDecision(), None)
        self.assertNotIn("trajectory_record", bare.metadata)

    def test_execution_failure_reply(self):
        reply = build_execution_failure_reply(
            FakeRequest(),
            FakeResult(message="炸了", error_code="boom"),
            worker_execution=FakeWorkerExecution(FakeWorker()),
            executor_name="local_pc",
            workflow_record=FakeWorkflowRecord(),
            retry_trace=[{"attempt": 1}],
        )
        self.assertEqual(reply.text, "执行失败：炸了")
        self.assertEqual(reply.agent_name, "executor_python_worker")
        execution = reply.metadata["execution"]
        self.assertFalse(execution["success"])
        self.assertEqual(execution["error_code"], "boom")
        self.assertEqual(execution["worker"], {"worker_id": "w-1"})
        self.assertEqual(execution["worker_audit"], {"event": "audit"})
        self.assertEqual(reply.metadata["workflow_record"], {"record": "wf"})
        self.assertEqual(reply.metadata["retry_trace"], [{"attempt": 1}])

    def test_execution_success_reply_omits_empty_optionals(self):
        reply = build_execution_success_reply(
            FakeRequest(),
            FakeResult(message="成功"),
            worker_execution=FakeWorkerExecution(None),
            executor_name="local_pc",
            workflow_record=FakeWorkflowRecord(),
            trajectory_record=None,
            inventory_update=None,
            retry_trace=[],
        )
        self.assertEqual(reply.text, "成功")
        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertTrue(reply.metadata["execution"]["success"])
        self.assertIsNone(reply.metadata["execution"]["worker"])
        for key in ("trajectory_record", "inventory_update", "retry_trace"):
            self.assertNotIn(key, reply.metadata)


class ConfirmationReplyTests(unittest.TestCase):
    def test_no_pending_confirmation_reply(self):
        reply = build_no_pending_confirmation_reply()
        self.assertEqual(reply.metadata["response_kind"], "message")
        self.assertEqual(reply.agent_name, "execution_guard")

    def test_confirmation_mismatch_reply(self):
        reply = build_confirmation_mismatch_reply(FakePending(), received_target="feishu", received_operation="send")
        self.assertEqual(reply.metadata["response_kind"], "confirmation_mismatch")
        self.assertEqual(reply.metadata["pending_target"], "local_pc")
        self.assertEqual(reply.metadata["received_target"], "feishu")
        self.assertEqual(reply.metadata["received_operation"], "send")

    def test_duplicate_confirmation_reply(self):
        reply = build_duplicate_confirmation_reply(FakePending())
        self.assertEqual(reply.metadata["response_kind"], "confirmation_failed")
        self.assertEqual(reply.metadata["execution"]["error_code"], "duplicate_confirmation_request")
        self.assertFalse(reply.metadata["execution"]["success"])


if __name__ == "__main__":
    unittest.main()
