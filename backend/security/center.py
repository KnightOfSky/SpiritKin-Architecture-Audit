from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.security.audit import InMemoryAuditLog
from backend.security.capability import CapabilityRegistry
from backend.security.policy import PolicyDecision, PolicyEngine
from backend.security.rate_limiter import InMemoryRateLimiter


@dataclass(frozen=True)
class AuthorizationRequest:
    target: str
    operation: str
    risk_level: str = "low"
    actor: str = "runtime"
    channel: str = "desktop"
    capability_id: str = ""
    rate_limit_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    require_confirmation: bool
    reason: str
    policy_decision: PolicyDecision | None = None
    capability_checked: bool = False
    rate_limit_remaining: int | None = None


class PermissionCenter:
    """Small orchestration layer for policy, capabilities, rate limits and audit."""

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        capability_registry: CapabilityRegistry | None = None,
        rate_limiter: InMemoryRateLimiter | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ):
        self.policy_engine = policy_engine
        self.capability_registry = capability_registry
        self.rate_limiter = rate_limiter
        self.audit_log = audit_log

    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        capability_checked = False
        if request.capability_id and self.capability_registry is not None:
            capability_checked = True
            if not self.capability_registry.check(request.capability_id, request.target, request.operation):
                return self._finish(request, AuthorizationResult(False, False, "能力授权不足", capability_checked=True))

        decision = self.policy_engine.evaluate(
            target=request.target,
            operation=request.operation,
            risk_level=request.risk_level,
            actor=request.actor,
            channel=request.channel,
        ) if self.policy_engine is not None else PolicyDecision(True, request.risk_level == "high", "无策略引擎，按风险等级处理")
        if not decision.allowed:
            return self._finish(request, AuthorizationResult(False, decision.require_confirmation, decision.reason, decision, capability_checked))

        remaining = None
        key = request.rate_limit_key or f"{request.actor}:{request.channel}:{request.target}.{request.operation}"
        if self.rate_limiter is not None:
            remaining = self.rate_limiter.remaining(key)
            if not self.rate_limiter.check(key):
                return self._finish(request, AuthorizationResult(False, False, "触发速率限制", decision, capability_checked, 0), event_type="rate_limit_violation")
            self.rate_limiter.record(key)
            remaining = self.rate_limiter.remaining(key)

        return self._finish(request, AuthorizationResult(True, decision.require_confirmation, decision.reason, decision, capability_checked, remaining))

    def _finish(self, request: AuthorizationRequest, result: AuthorizationResult, *, event_type: str | None = None) -> AuthorizationResult:
        if self.audit_log is not None:
            self.audit_log.record(
                event_type or ("authorization_allowed" if result.allowed else "authorization_denied"),
                actor=request.actor,
                channel=request.channel,
                target=request.target,
                operation=request.operation,
                risk_level=request.risk_level,
                success=result.allowed,
                message=result.reason,
                policy_decision_id=result.policy_decision.matched_rule_id if result.policy_decision else "",
                capability_id=request.capability_id,
                metadata=dict(request.metadata),
            )
        return result