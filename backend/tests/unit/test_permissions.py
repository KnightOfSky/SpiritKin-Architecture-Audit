from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.orchestrator.agent_cluster import AgentCluster
from backend.security.audit import AuditRecord, InMemoryAuditLog
from backend.security.capability import (
    CapabilityRegistry,
    CapabilityToken,
    JsonlCapabilityStore,
)
from backend.security.center import AuthorizationRequest, PermissionCenter
from backend.security.policy import (
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
    build_default_policy,
    build_permissive_policy,
)
from backend.security.rate_limiter import InMemoryRateLimiter, RateLimitConfig
from backend.security.user_identity import RoleHierarchy, resolve_actor_identity


class PermissionTests(unittest.TestCase):
    def test_policy_engine_first_match_wins_by_priority(self):
        rules = [
            PolicyRule(rule_id="allow-safe", description="allow safe ops", target_pattern="safe", allowed=True, priority=10),
            PolicyRule(rule_id="deny-all", description="deny all", allowed=False, priority=50),
        ]
        engine = PolicyEngine(rules)
        decision = engine.evaluate(target="safe", operation="read")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.matched_rule_id, "allow-safe")

    def test_policy_engine_default_deny(self):
        engine = PolicyEngine([], default_deny=True)
        decision = engine.evaluate(target="anything", operation="anything")
        self.assertFalse(decision.allowed)

    def test_policy_engine_default_allow(self):
        engine = PolicyEngine([])
        decision = engine.evaluate(target="anything", operation="anything")
        self.assertTrue(decision.allowed)

    def test_policy_rule_glob_matching(self):
        rules = [PolicyRule(rule_id="remote-glob", description="remote", target_pattern="remote:*", operation_pattern="*", allowed=True, priority=10)]
        engine = PolicyEngine(rules)
        self.assertTrue(engine.evaluate(target="remote:node1", operation="do").allowed)
        self.assertTrue(engine.evaluate(target="local_pc", operation="do").allowed)

    def test_build_default_policy_produces_rules(self):
        rules = build_default_policy()
        self.assertGreater(len(rules), 0)

    def test_build_permissive_policy_allows_all(self):
        engine = PolicyEngine(build_permissive_policy())
        self.assertTrue(engine.evaluate(target="anything", operation="delete", risk_level="high").allowed)

    def test_policy_decision_struct(self):
        decision = PolicyDecision(allowed=True, require_confirmation=False, reason="test", matched_rule_id="r1")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.require_confirmation)

    def test_rate_limiter_allows_within_limit(self):
        limiter = InMemoryRateLimiter(RateLimitConfig(max_requests=3, window_seconds=60))
        for _ in range(3):
            self.assertTrue(limiter.check("user-a"))
            limiter.record("user-a")
        self.assertFalse(limiter.check("user-a"))

    def test_rate_limiter_reset(self):
        limiter = InMemoryRateLimiter(RateLimitConfig(max_requests=2, window_seconds=60))
        limiter.record("x")
        limiter.record("x")
        self.assertFalse(limiter.check("x"))
        limiter.reset("x")
        self.assertTrue(limiter.check("x"))

    def test_rate_limiter_remaining(self):
        limiter = InMemoryRateLimiter(RateLimitConfig(max_requests=5, window_seconds=60))
        self.assertEqual(limiter.remaining("x"), 5)
        limiter.record("x")
        self.assertEqual(limiter.remaining("x"), 4)

    def test_capability_registry_grants_and_checks(self):
        reg = CapabilityRegistry()
        token = CapabilityToken(token_id="t1", actor="user", capabilities=("local_pc.browser_open_url", "feishu.message.send"))
        reg.grant(token)
        self.assertTrue(reg.check("t1", "local_pc", "browser_open_url"))
        self.assertFalse(reg.check("t1", "local_pc", "file_delete"))

    def test_capability_registry_rejects_expired(self):
        reg = CapabilityRegistry()
        token = CapabilityToken(token_id="t1", actor="u", capabilities=("do.thing",), expires_at=time.time() - 1)
        reg.grant(token)
        self.assertFalse(reg.check("t1", "do", "thing"))

    def test_jsonl_capability_store_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "caps.jsonl"
            store1 = JsonlCapabilityStore(path)
            store1.grant(CapabilityToken(token_id="t1", actor="u", capabilities=("a.b",)))
            store2 = JsonlCapabilityStore(path)
            self.assertTrue(store2.check("t1", "a", "b"))

    def test_capability_wildcard(self):
        reg = CapabilityRegistry()
        reg.grant(CapabilityToken(token_id="admin", actor="a", capabilities=("*",)))
        self.assertTrue(reg.check("admin", "any", "thing"))

    def test_capability_target_operation_glob(self):
        reg = CapabilityRegistry()
        reg.grant(CapabilityToken(token_id="operator", actor="a", capabilities=("local_pc.*",)))
        self.assertTrue(reg.check("operator", "local_pc", "browser_open_url"))
        self.assertFalse(reg.check("operator", "remote", "browser_open_url"))

    def test_resolve_actor_identity_anonymous(self):
        identity = resolve_actor_identity(auth_header="", channel="mobile")
        self.assertIn("anonymous", identity.user_id)

    def test_role_hierarchy_get_capabilities(self):
        viewer = RoleHierarchy.get_capabilities("viewer")
        self.assertIn("knowledge.search", viewer)
        admin = RoleHierarchy.get_capabilities("admin")
        self.assertIn("*", admin)

    def test_audit_record_carries_policy_fields(self):
        record = AuditRecord(audit_id="a1", event_type="test", policy_decision_id="p1", capability_id="c1")
        snap = record.snapshot()
        self.assertEqual(snap["policy_decision_id"], "p1")
        self.assertEqual(snap["capability_id"], "c1")

    def test_audit_log_summary_includes_rate_limit_violations(self):
        log = InMemoryAuditLog()
        log.record("rate_limit_violation")
        log.record("rate_limit_violation")
        log.record("normal_event")
        summary = log.summary()
        self.assertEqual(summary["rate_limit_violations"], 2)

    def test_audit_log_query_and_redacted_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = InMemoryAuditLog()
            log.record("execution_result", target="local_pc", operation="file_read", success=True, metadata={"token": "secret"})
            log.record("command_received", channel="mobile", success=True)
            records = log.query(event_type="execution_result", target="local_pc")
            export_path = Path(tmp) / "audit.jsonl"
            count = log.export_jsonl(export_path, records=records)

            self.assertEqual(count, 1)
            line = json.loads(export_path.read_text(encoding="utf-8").strip())
            self.assertEqual(line["metadata"]["token"], "<redacted>")

    def test_permission_center_checks_capability_policy_rate_limit_and_audit(self):
        caps = CapabilityRegistry()
        caps.grant(CapabilityToken(token_id="mobile-token", actor="phone", capabilities=("local_pc.browser_open_url",)))
        audit = InMemoryAuditLog()
        center = PermissionCenter(
            policy_engine=PolicyEngine(build_default_policy()),
            capability_registry=caps,
            rate_limiter=InMemoryRateLimiter(RateLimitConfig(max_requests=1, window_seconds=60)),
            audit_log=audit,
        )

        first = center.authorize(AuthorizationRequest("local_pc", "browser_open_url", "medium", actor="phone", channel="mobile", capability_id="mobile-token"))
        second = center.authorize(AuthorizationRequest("local_pc", "browser_open_url", "medium", actor="phone", channel="mobile", capability_id="mobile-token"))

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(audit.summary()["rate_limit_violations"], 1)

    def test_agent_cluster_blocks_execution_when_policy_denies(self):
        class FakeExecutor(BaseExecutor):
            name = "fake"

            def supports(self, request: ExecutionRequest) -> bool:
                return request.target == "local_pc" and request.operation == "status"

            def execute(self, request: ExecutionRequest) -> ExecutionResult:
                return ExecutionResult(True, "should not run")

        policy = PolicyEngine([PolicyRule(rule_id="deny-status", description="deny status", target_pattern="local_pc", operation_pattern="status", allowed=False, priority=1)])
        cluster = AgentCluster(llm_client=lambda _: "ok", executors=[FakeExecutor()], policy_engine=policy)

        reply = cluster._handle_execution(ExecutionRequest("local_pc", "status"), user_input="status")

        self.assertEqual(reply.agent_name, "policy_guard")
        self.assertEqual(reply.metadata["response_kind"], "policy_denied")
