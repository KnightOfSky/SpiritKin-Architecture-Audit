from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImprovementAction:
    action_id: str
    category: str
    priority: str
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "category": self.category,
            "priority": self.priority,
            "title": self.title,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class TrainingExample:
    example_id: str
    source: str
    task_type: str
    input_text: str
    expected_behavior: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "source": self.source,
            "task_type": self.task_type,
            "input_text": self.input_text,
            "expected_behavior": self.expected_behavior,
            "weight": self.weight,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SelfTrainingPackage:
    package_id: str
    generated_at: float
    purpose: str
    examples: list[TrainingExample] = field(default_factory=list)
    evaluator_notes: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "generated_at": self.generated_at,
            "purpose": self.purpose,
            "examples": [example.snapshot() for example in self.examples],
            "evaluator_notes": list(self.evaluator_notes),
            "safety_notes": list(self.safety_notes),
        }


@dataclass(frozen=True)
class SelfImprovementReport:
    generated_at: float
    performance: dict[str, Any]
    trajectory: dict[str, Any]
    failure_samples: dict[str, Any]
    eval_cases: list[dict[str, Any]] = field(default_factory=list)
    actions: list[ImprovementAction] = field(default_factory=list)
    training_package: SelfTrainingPackage | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "performance": dict(self.performance),
            "trajectory": dict(self.trajectory),
            "failure_samples": dict(self.failure_samples),
            "eval_cases": list(self.eval_cases),
            "actions": [action.snapshot() for action in self.actions],
            "training_package": self.training_package.snapshot() if self.training_package is not None else None,
        }


class SelfImprovementLoop:
    def __init__(self, *, performance_tracker=None, trajectory_analyzer=None, failure_db=None):
        self.performance_tracker = performance_tracker
        self.trajectory_analyzer = trajectory_analyzer
        self.failure_db = failure_db

    def build_report(self, *, eval_case_limit: int = 10) -> SelfImprovementReport:
        performance = self.performance_tracker.stats() if self.performance_tracker is not None else {}
        trajectory = self.trajectory_analyzer.stats() if self.trajectory_analyzer is not None else {}
        failure_samples = self.failure_db.stats() if self.failure_db is not None else {}
        eval_cases = self.trajectory_analyzer.generate_eval_cases(limit=eval_case_limit) if self.trajectory_analyzer is not None else []
        actions = self._build_actions(performance, trajectory, failure_samples)
        training_package = self.build_self_training_package(
            performance=performance,
            trajectory=trajectory,
            failure_samples=failure_samples,
            eval_cases=eval_cases,
        )
        return SelfImprovementReport(time.time(), performance, trajectory, failure_samples, eval_cases, actions, training_package)

    def build_self_training_package(
        self,
        *,
        performance: dict[str, Any] | None = None,
        trajectory: dict[str, Any] | None = None,
        failure_samples: dict[str, Any] | None = None,
        eval_cases: list[dict[str, Any]] | None = None,
    ) -> SelfTrainingPackage:
        generated_at = time.time()
        performance = performance if performance is not None else (self.performance_tracker.stats() if self.performance_tracker is not None else {})
        trajectory = trajectory if trajectory is not None else (self.trajectory_analyzer.stats() if self.trajectory_analyzer is not None else {})
        failure_samples = failure_samples if failure_samples is not None else (self.failure_db.stats() if self.failure_db is not None else {})
        eval_cases = list(eval_cases if eval_cases is not None else (self.trajectory_analyzer.generate_eval_cases(limit=10) if self.trajectory_analyzer is not None else []))

        examples: list[TrainingExample] = []
        for index, case in enumerate(eval_cases, start=1):
            user_input = str(case.get("user_input") or "").strip()
            if not user_input:
                continue
            failed_steps = case.get("failed_steps") or []
            expected = "应识别失败阶段，优先给出可验证修复或澄清问题。"
            if failed_steps:
                first_failed = failed_steps[0]
                expected = (
                    f"应避免在 {first_failed.get('stage', 'unknown')} 阶段重复失败；"
                    f"遇到 {first_failed.get('error_code', 'unknown')} 时返回可执行修复建议。"
                )
            examples.append(TrainingExample(
                example_id=f"trajectory-{index}",
                source="trajectory",
                task_type="regression_eval",
                input_text=user_input,
                expected_behavior=expected,
                weight=1.0,
                metadata=dict(case),
            ))

        for item in performance.get("ranking", []) or []:
            if item.get("total", 0) >= 3 and item.get("success_rate", 1.0) < 0.7:
                agent_name = str(item.get("agent_name") or "unknown")
                examples.append(TrainingExample(
                    example_id=f"performance-{agent_name}",
                    source="performance",
                    task_type="routing_feedback",
                    input_text=f"Agent {agent_name} 近期成功率偏低。",
                    expected_behavior="规划器应降低不稳定 agent 的默认路由权重，或在缺少工具/权限时先澄清。",
                    weight=0.8,
                    metadata=dict(item),
                ))

        for error_code, count in (failure_samples.get("by_error_code", {}) or {}).items():
            if count >= 2:
                examples.append(TrainingExample(
                    example_id=f"failure-{error_code}",
                    source="failure_db",
                    task_type="failure_pattern",
                    input_text=f"系统反复出现错误类型：{error_code}",
                    expected_behavior="应把重复错误转成专门 eval，并在执行前检查对应 ToolSpec、权限或执行器可用性。",
                    weight=0.7,
                    metadata={"error_code": error_code, "count": count},
                ))

        notes = [
            "该训练包用于 AI 生成评测、路由改进建议、prompt 调整和技能候选，不直接自动覆盖生产代码。",
            "优先把失败轨迹转为可回放 eval，再由人审核是否进入工具、planner 或 agent 改动。",
        ]
        safety = [
            "高风险执行、权限策略、外部写操作必须保留人工确认或策略门。",
            "训练样本只应保存必要上下文，避免泄露用户隐私、密钥和完整文件内容。",
        ]
        return SelfTrainingPackage(
            package_id=f"self-training-{int(generated_at)}",
            generated_at=generated_at,
            purpose="AI-for-AI feedback loop: convert runtime failures into eval and improvement signals",
            examples=examples,
            evaluator_notes=notes,
            safety_notes=safety,
        )

    def _build_actions(self, performance: dict[str, Any], trajectory: dict[str, Any], failure_samples: dict[str, Any]) -> list[ImprovementAction]:
        actions: list[ImprovementAction] = []
        for item in performance.get("ranking", []) or []:
            if item.get("total", 0) >= 3 and item.get("success_rate", 1.0) < 0.6:
                actions.append(ImprovementAction(
                    action_id=f"agent-{item.get('agent_name', 'unknown')}-review",
                    category="agent_routing",
                    priority="high",
                    title=f"审核低成功率 Agent: {item.get('agent_name', 'unknown')}",
                    detail="该 Agent 近期成功率偏低，建议降低路由权重、补充工具注册或增加澄清问题。",
                    evidence=dict(item),
                ))
        for bottleneck in trajectory.get("bottlenecks", []) or []:
            if bottleneck.get("failure_count", 0) >= 2:
                actions.append(ImprovementAction(
                    action_id=f"bottleneck-{bottleneck.get('stage', 'unknown')}",
                    category="trajectory_eval",
                    priority="medium",
                    title=f"生成 {bottleneck.get('stage', 'unknown')} 阶段回归用例",
                    detail="该阶段反复失败，建议把失败轨迹转为 eval case，并在修复后持续回放。",
                    evidence=dict(bottleneck),
                ))
        for error_code, count in (failure_samples.get("by_error_code", {}) or {}).items():
            if count >= 2:
                actions.append(ImprovementAction(
                    action_id=f"failure-{error_code}",
                    category="failure_sample",
                    priority="medium",
                    title=f"聚合处理失败类型: {error_code}",
                    detail="失败样本库中该错误反复出现，建议建立专项修复任务或 ToolSpec 检查。",
                    evidence={"error_code": error_code, "count": count},
                ))
        return actions
