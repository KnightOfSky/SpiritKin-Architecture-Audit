from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.executors.base import ExecutionRequest, ExecutionResult
from backend.orchestrator.execution_retry import (
    DEFAULT_RETRY_ATTEMPTS,
    MAX_RETRY_ATTEMPTS_CEILING,
    attach_failure_context,
    build_retry_prompt,
    error_is_retryable,
    extract_stderr,
    parse_retry_response,
    plan_next_request,
    repair_plan_is_supported,
    retry_attempt_budget,
    retry_backoff_seconds,
)
from backend.orchestrator.failure_classifier import classify_failure


class RetryBudgetTests(unittest.TestCase):
    def test_default_when_unset(self):
        import os

        os.environ.pop("SPIRITKIN_EXECUTION_RETRY_ATTEMPTS", None)
        self.assertEqual(retry_attempt_budget(), DEFAULT_RETRY_ATTEMPTS)

    def test_clamped_to_ceiling(self):
        import os

        os.environ["SPIRITKIN_EXECUTION_RETRY_ATTEMPTS"] = "99"
        try:
            self.assertEqual(retry_attempt_budget(), MAX_RETRY_ATTEMPTS_CEILING)
        finally:
            os.environ.pop("SPIRITKIN_EXECUTION_RETRY_ATTEMPTS", None)

    def test_zero_disables(self):
        import os

        os.environ["SPIRITKIN_EXECUTION_RETRY_ATTEMPTS"] = "0"
        try:
            self.assertEqual(retry_attempt_budget(), 0)
        finally:
            os.environ.pop("SPIRITKIN_EXECUTION_RETRY_ATTEMPTS", None)

    def test_garbage_falls_back_to_default(self):
        import os

        os.environ["SPIRITKIN_EXECUTION_RETRY_ATTEMPTS"] = "abc"
        try:
            self.assertEqual(retry_attempt_budget(), DEFAULT_RETRY_ATTEMPTS)
        finally:
            os.environ.pop("SPIRITKIN_EXECUTION_RETRY_ATTEMPTS", None)


class RetryableTests(unittest.TestCase):
    def test_success_is_not_retryable(self):
        self.assertFalse(error_is_retryable(ExecutionResult(True, "ok")))

    def test_generic_failure_is_retryable(self):
        self.assertTrue(error_is_retryable(ExecutionResult(False, "boom", error_code="ffmpeg_failed")))

    def test_environment_codes_are_not_retryable(self):
        for code in ("worker_not_found", "executor_not_found", "policy_denied", "safety_denied"):
            self.assertFalse(error_is_retryable(ExecutionResult(False, "x", error_code=code)), code)

    def test_failure_classifier_distinguishes_transient_fixable_and_fatal(self):
        self.assertEqual(classify_failure(ExecutionResult(False, "connection reset", error_code="network_error")).kind, "transient")
        self.assertEqual(classify_failure(ExecutionResult(False, "No such file", error_code="path_error")).kind, "fixable")
        self.assertEqual(classify_failure(ExecutionResult(False, "permission denied", error_code="forbidden")).kind, "fatal")

    def test_retry_backoff_is_exponential_and_capped(self):
        with patch.dict("os.environ", {"SPIRITKIN_EXECUTION_RETRY_BACKOFF_SECONDS": "0.5"}, clear=False):
            self.assertEqual(retry_backoff_seconds(1), 0.5)
            self.assertEqual(retry_backoff_seconds(2), 1.0)
            self.assertEqual(retry_backoff_seconds(99), 10.0)


class ExtractStderrTests(unittest.TestCase):
    def test_prefers_stderr_from_data_dict(self):
        result = ExecutionResult(False, "failed", data={"stderr": "No such file", "stdout": "x"})
        self.assertIn("No such file", extract_stderr(result))

    def test_failure_context_is_attached_before_retry_and_reply(self):
        request = ExecutionRequest(target="git", operation="git.status", params={"repo_path": "missing"})
        result = ExecutionResult(
            False,
            "Git executable is not available",
            data={"stderr": "git: command not found", "returncode": 127},
            error_code="git_worker_not_available",
            metadata={"failure_context": {"install_suggestion": "Install Git"}},
        )

        context = attach_failure_context(result, request)

        self.assertEqual(context["kind"], "fixable")
        self.assertEqual(context["exit_code"], 127)
        self.assertIn("command not found", context["stderr_tail"])
        self.assertEqual(context["install_suggestion"], "Install Git")
        self.assertEqual(result.data["failure_context"], context)

    def test_reads_from_metadata(self):
        result = ExecutionResult(False, "failed", metadata={"error": "bad flag"})
        self.assertIn("bad flag", extract_stderr(result))

    def test_truncates_long_output(self):
        result = ExecutionResult(False, "failed", data={"stderr": "x" * 5000})
        self.assertLessEqual(len(extract_stderr(result)), 2100)


class ParseRetryResponseTests(unittest.TestCase):
    def test_parses_plain_json(self):
        plan = parse_retry_response('{"action": "retry", "params": {"path": "/tmp/a"}, "reason": "改路径"}')
        self.assertIsNotNone(plan)
        self.assertTrue(plan.should_retry)
        self.assertEqual(plan.params, {"path": "/tmp/a"})

    def test_parses_fenced_json(self):
        plan = parse_retry_response('```json\n{"action": "abort", "params": {}, "reason": "缺环境"}\n```')
        self.assertIsNotNone(plan)
        self.assertFalse(plan.should_retry)

    def test_missing_action_with_params_defaults_retry(self):
        plan = parse_retry_response('{"params": {"x": 1}}')
        self.assertTrue(plan.should_retry)

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_retry_response("对不起我无法解析"))


class PlanNextRequestTests(unittest.TestCase):
    def setUp(self):
        self.request = ExecutionRequest("local_pc", "run", {"path": "/old"})

    def test_abort_plan_yields_none(self):
        plan = parse_retry_response('{"action": "abort", "params": {}}')
        self.assertIsNone(plan_next_request(request=self.request, plan=plan))

    def test_unchanged_params_yields_none(self):
        plan = parse_retry_response('{"action": "retry", "params": {"path": "/old"}}')
        self.assertIsNone(plan_next_request(request=self.request, plan=plan))

    def test_changed_params_yields_new_request(self):
        plan = parse_retry_response('{"action": "retry", "params": {"path": "/new"}}')
        next_request = plan_next_request(request=self.request, plan=plan)
        self.assertIsNotNone(next_request)
        self.assertEqual(next_request.params, {"path": "/new"})
        self.assertEqual(next_request.target, "local_pc")
        self.assertEqual(next_request.operation, "run")

    def test_python_repair_requires_missing_module_evidence(self):
        plan = parse_retry_response(
            '{"action":"retry","params":{"path":"/old"},'
            '"repair_tool":{"name":"python.install_package","arguments":{"package":"requests==2.32.3"}}}'
        )

        supported = repair_plan_is_supported(
            plan,
            ExecutionResult(False, "script failed", data={"stderr": "ModuleNotFoundError: No module named 'requests'"}),
        )
        rejected = repair_plan_is_supported(
            plan,
            ExecutionResult(False, "syntax error", data={"stderr": "SyntaxError: invalid syntax"}),
        )

        self.assertTrue(supported)
        self.assertFalse(rejected)


class BuildRetryPromptTests(unittest.TestCase):
    def test_prompt_includes_stderr_and_params(self):
        request = ExecutionRequest("ffmpeg", "transcode", {"input": "a.mp4"})
        result = ExecutionResult(False, "failed", data={"stderr": "codec not found"}, error_code="ffmpeg_failed")
        prompt = build_retry_prompt(request=request, result=result, attempt=1, max_attempts=1, user_input="转码")
        self.assertIn("codec not found", prompt)
        self.assertIn("ffmpeg", prompt)
        self.assertIn("transcode", prompt)


if __name__ == "__main__":
    unittest.main()
