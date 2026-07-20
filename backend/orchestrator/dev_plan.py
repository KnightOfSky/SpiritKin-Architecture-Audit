from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DevelopmentPlan:
    target: str
    goal: str
    risk_level: str = "medium"
    interfaces: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    test_plan: list[str] = field(default_factory=list)
    rollback_strategy: str = "手动回滚"
    estimated_effort: str = "M"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        lines = [
            f"# 开发计划: {self.target}",
            f"**目标**: {self.goal}",
            f"**风险等级**: {self.risk_level}",
            f"**预估工作量**: {self.estimated_effort}",
            "",
            "## 接口边界",
        ]
        for iface in self.interfaces:
            lines.append(f"- {iface}")
        lines.append("")
        lines.append("## 实施步骤")
        for i, step in enumerate(self.steps, 1):
            lines.append(f"{i}. **{step.get('title', '')}** — {step.get('detail', '')}")
        lines.append("")
        lines.append("## 测试计划")
        for test in self.test_plan:
            lines.append(f"- {test}")
        lines.append("")
        lines.append("## 回滚策略")
        lines.append(self.rollback_strategy)
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "goal": self.goal,
            "risk_level": self.risk_level,
            "interfaces": self.interfaces,
            "steps": self.steps,
            "test_plan": self.test_plan,
            "rollback_strategy": self.rollback_strategy,
            "estimated_effort": self.estimated_effort,
            "metadata": self.metadata,
        }


def build_development_plan(
    target: str,
    goal: str,
    *,
    risk_level: str = "medium",
    interfaces: list[str] | None = None,
    steps: list[dict[str, Any]] | None = None,
    test_plan: list[str] | None = None,
    rollback_strategy: str = "手动回滚",
    estimated_effort: str = "M",
) -> DevelopmentPlan:
    return DevelopmentPlan(
        target=target,
        goal=goal,
        risk_level=risk_level,
        interfaces=interfaces or [],
        steps=steps or [],
        test_plan=test_plan or [],
        rollback_strategy=rollback_strategy,
        estimated_effort=estimated_effort,
    )
