from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    description: str
    target_pattern: str = "*"
    operation_pattern: str = "*"
    risk_levels: tuple[str, ...] = ("low", "medium", "high")
    actor_pattern: str = "*"
    channel_pattern: str = "*"
    allowed: bool = True
    require_confirmation: bool = False
    rate_limit_rpm: int | None = None
    priority: int = 100


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    require_confirmation: bool
    reason: str
    matched_rule_id: str = ""
    rate_limit_remaining: int | None = None


class PolicyEngine:
    def __init__(self, rules: list[PolicyRule] | None = None, *, default_deny: bool = False):
        self._rules = sorted(rules or [], key=lambda r: r.priority)
        self._default_deny = default_deny

    def evaluate(
        self,
        *,
        target: str = "",
        operation: str = "",
        risk_level: str = "low",
        actor: str = "runtime",
        channel: str = "desktop",
    ) -> PolicyDecision:
        for rule in self._rules:
            if not fnmatch.fnmatch(target, rule.target_pattern):
                continue
            if not fnmatch.fnmatch(operation, rule.operation_pattern):
                continue
            if risk_level not in rule.risk_levels:
                continue
            if not fnmatch.fnmatch(actor, rule.actor_pattern):
                continue
            if not fnmatch.fnmatch(channel, rule.channel_pattern):
                continue
            return PolicyDecision(
                allowed=rule.allowed,
                require_confirmation=rule.require_confirmation,
                reason=rule.description,
                matched_rule_id=rule.rule_id,
                rate_limit_remaining=None,
            )
        return PolicyDecision(
            allowed=not self._default_deny,
            require_confirmation=self._default_deny,
            reason="未匹配任何策略规则，使用默认策略" if self._default_deny else "未匹配任何策略规则，默认允许",
        )


class PolicyTemplateLibrary:
    READ_ONLY_TEMPLATE = "read_only"
    WRITE_WITH_CONFIRM_TEMPLATE = "write_with_confirm"
    PUBLIC_READ_TEMPLATE = "public_read"
    MOBILE_RESTRICTED_TEMPLATE = "mobile_restricted"

    @staticmethod
    def build_read_only(rule_id: str, target_pattern: str = "*", operation_pattern: str = "*", *, priority: int = 50) -> PolicyRule:
        return PolicyRule(
            rule_id=rule_id,
            description="只读操作允许，无需确认",
            target_pattern=target_pattern,
            operation_pattern=operation_pattern,
            risk_levels=("low",),
            allowed=True,
            require_confirmation=False,
            priority=priority,
        )

    @staticmethod
    def build_write_with_confirm(rule_id: str, target_pattern: str = "*", operation_pattern: str = "*", *, priority: int = 80) -> PolicyRule:
        return PolicyRule(
            rule_id=rule_id,
            description="写操作需确认",
            target_pattern=target_pattern,
            operation_pattern=operation_pattern,
            risk_levels=("medium", "high"),
            allowed=True,
            require_confirmation=True,
            priority=priority,
        )

    @staticmethod
    def build_mobile_restricted(rule_id: str, target_pattern: str = "*", operation_pattern: str = "*", *, priority: int = 90) -> PolicyRule:
        return PolicyRule(
            rule_id=rule_id,
            description="移动端高风险操作需确认且限速",
            target_pattern=target_pattern,
            operation_pattern=operation_pattern,
            risk_levels=("high",),
            channel_pattern="mobile",
            allowed=True,
            require_confirmation=True,
            rate_limit_rpm=10,
            priority=priority,
        )


def build_default_policy() -> list[PolicyRule]:
    return [
        PolicyTemplateLibrary.build_read_only("default-read", priority=50),
        PolicyTemplateLibrary.build_write_with_confirm("default-write", priority=80),
        PolicyTemplateLibrary.build_mobile_restricted("mobile-high-risk", priority=90),
        PolicyRule(
            rule_id="public-deny",
            description="公网通道默认确认所有操作",
            target_pattern="*",
            operation_pattern="*",
            channel_pattern="public",
            allowed=True,
            require_confirmation=True,
            priority=100,
        ),
    ]


def build_permissive_policy() -> list[PolicyRule]:
    return [
        PolicyRule(
            rule_id="allow-all",
            description="开发环境：允许所有操作",
            allowed=True,
            require_confirmation=False,
            priority=0,
        ),
    ]
