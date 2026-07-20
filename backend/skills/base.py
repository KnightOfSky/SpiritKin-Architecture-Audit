from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.orchestrator.runtime_metadata import RuntimeMetadata, normalize_runtime_metadata
from backend.runtime import RuntimeContract, lifecycle_snapshot, object_state_snapshot
from backend.security.safety_control import evaluate_execution_safety
from backend.tools.base import ToolCall, ToolResult
from backend.tools.registry import ToolRegistry


@dataclass(frozen=True)
class SkillStepSpec:
    """A single auditable tool step inside a reusable Skill."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    optional: bool = False


@dataclass(frozen=True)
class SkillSpec:
    """Minimal Skill contract above atomic tools and below the planner."""

    name: str
    description: str
    trigger_intents: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    preconditions: tuple[str, ...] = ()
    steps: tuple[SkillStepSpec, ...] = ()
    tool_allowlist: tuple[str, ...] = ()
    risk_level: str = "low"
    confirmation_policy: str = "risk_based"
    rollback_strategy: str = "manual_review"
    success_criteria: tuple[str, ...] = ()
    memory_policy: str = "record_summary"
    eval_cases: tuple[str, ...] = ()
    version: str = "0.1.0"
    usage_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    cost_hint: str = ""
    latency_hint_ms: int | None = None
    success_rate: float | None = None
    required_capabilities: tuple[str, ...] = ()
    required_worker_needs: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    artifact_contract: dict[str, Any] = field(default_factory=dict)

    def runtime_metadata(self) -> RuntimeMetadata:
        return normalize_runtime_metadata(
            self.metadata,
            object_type="skill",
            object_id=self.name,
            defaults={
                "domain": self.metadata.get("domain") or self.metadata.get("owner_domain") or "skill",
                "owner": self.metadata.get("owner") or self.metadata.get("owner_agent_id") or "skill_registry",
                "version": self.version,
                "status": self.metadata.get("status") or "draft",
                "risk_level": self.risk_level,
                "permission_scope": self.metadata.get("permission_scope") or self.confirmation_policy,
                "cost_hint": self.cost_hint,
                "latency_hint_ms": self.latency_hint_ms,
                "success_rate": self.success_rate,
                "benchmark_refs": self.metadata.get("benchmark_refs") or self.eval_cases,
                "dependency_refs": (*self.required_capabilities, *self.required_worker_needs),
            },
        )

    def runtime_contract(self) -> RuntimeContract:
        resources = self.metadata.get("resources") or self.metadata.get("resource_refs") or ()
        return RuntimeContract(
            object_type="skill",
            object_id=self.name,
            input_schema=dict(self.input_schema),
            output_schema=dict(self.output_schema),
            resources=tuple(str(item) for item in resources),
            permission=str(self.metadata.get("permission_scope") or self.confirmation_policy),
            schema_ref=str(self.metadata.get("schema_ref") or ""),
            version=self.version,
        )

    def governance_snapshot(self) -> dict[str, Any]:
        metadata = self.runtime_metadata()
        return {
            "runtime_metadata": metadata.snapshot(),
            "lifecycle": lifecycle_snapshot(object_type="skill", object_id=self.name, status=metadata.status),
            "state_machine": object_state_snapshot(object_type="skill", object_id=self.name, state=metadata.status),
            "contract": self.runtime_contract().snapshot(),
        }


@dataclass
class SkillRunResult:
    success: bool
    message: str
    skill_name: str
    step_results: list[ToolResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillRegistry:
    def __init__(self, skills: list[SkillSpec] | None = None):
        self._skills: dict[str, SkillSpec] = {}
        for skill in skills or []:
            self.register(skill)

    def register(self, skill: SkillSpec) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillSpec | None:
        return self._skills.get(name)

    def unregister(self, name: str) -> bool:
        if name in self._skills:
            del self._skills[name]
            return True
        return False

    def replace(self, spec: SkillSpec) -> None:
        self._skills[spec.name] = spec

    def list_specs(self) -> list[SkillSpec]:
        return list(self._skills.values())

    def list_candidates(self) -> list[SkillSpec]:
        return [s for s in self._skills.values() if s.metadata.get("status") == "candidate"]

    def list_by_status(self, status: str) -> list[SkillSpec]:
        return [s for s in self._skills.values() if s.metadata.get("status") == status]

    def list_active(self) -> list[SkillSpec]:
        return self.list_by_status("active")

    def find_by_intent(self, intent: str) -> list[SkillSpec]:
        normalized = intent.strip().lower()
        return [skill for skill in self._skills.values() if normalized in {item.lower() for item in skill.trigger_intents}]

    def load_from_store(self, store) -> None:
        for spec in store.list_all():
            self.register(spec)


class SkillRunner:
    def __init__(self, registry: SkillRegistry, tool_registry: ToolRegistry):
        self._registry = registry
        self._tool_registry = tool_registry

    def run(self, skill_name: str, inputs: dict[str, Any] | None = None, *, dry_run: bool = False) -> SkillRunResult:
        skill = self._registry.get(skill_name)
        if skill is None:
            return self._finish(
                SkillRunResult(False, f"未注册 Skill: {skill_name}", skill_name, metadata={"error_code": "skill_not_registered"}),
                inputs=inputs,
                dry_run=dry_run,
            )

        inputs = dict(inputs or {})
        contract_validation = skill.runtime_contract().validate_input(inputs)
        if not contract_validation.valid:
            return self._finish(
                SkillRunResult(
                    False,
                    f"Skill 输入不符合契约: {', '.join(contract_validation.issues)}",
                    skill.name,
                    metadata={
                        "error_code": "skill_input_contract_violation",
                        "contract_validation": contract_validation.snapshot(),
                    },
                ),
                inputs=inputs,
                dry_run=dry_run,
            )
        safety = evaluate_execution_safety(
            target="skill",
            operation=skill.name,
            actor=str(inputs.get("actor") or ""),
            read_only=False,
            dry_run=dry_run,
        )
        if not safety.allowed:
            return self._finish(
                SkillRunResult(
                    False,
                    safety.message,
                    skill.name,
                    metadata={"error_code": safety.error_code, "safety": safety.snapshot()},
                ),
                inputs=inputs,
                dry_run=dry_run,
            )
        planned_steps: list[dict[str, Any]] = []
        step_results: list[ToolResult] = []
        allowlist = set(skill.tool_allowlist)

        for index, step in enumerate(skill.steps):
            if allowlist and step.tool_name not in allowlist:
                return self._finish(
                    SkillRunResult(False, f"Skill 步骤未在白名单内: {step.tool_name}", skill.name, step_results, {"error_code": "tool_not_allowed", "step_index": index}),
                    inputs=inputs,
                    dry_run=dry_run,
                )
            arguments = self._resolve_arguments(step.arguments, inputs)
            planned_steps.append({"tool_name": step.tool_name, "arguments": arguments, "description": step.description})
            if dry_run:
                continue
            result = self._tool_registry.invoke(ToolCall(step.tool_name, arguments))
            step_results.append(result)
            if not result.success and not step.optional:
                return self._finish(
                    SkillRunResult(False, result.message or f"步骤失败: {step.tool_name}", skill.name, step_results, {"failed_step_index": index}),
                    inputs=inputs,
                    dry_run=dry_run,
                )

        if dry_run:
            return self._finish(
                SkillRunResult(True, f"Skill dry-run 计划完成: {skill.name}", skill.name, metadata={"planned_steps": planned_steps}),
                inputs=inputs,
                dry_run=dry_run,
            )
        return self._finish(
            SkillRunResult(True, f"Skill 执行完成: {skill.name}", skill.name, step_results, {"step_count": len(skill.steps)}),
            inputs=inputs,
            dry_run=dry_run,
        )

    def _finish(self, result: SkillRunResult, *, inputs: dict[str, Any] | None = None, dry_run: bool = False) -> SkillRunResult:
        metadata = dict(result.metadata or {})
        metadata.setdefault("dry_run", bool(dry_run))
        try:
            from backend.orchestrator.runtime_trajectory_log import (
                append_runtime_trajectory,
                trajectory_from_skill_run,
                trajectory_logging_enabled,
            )
        except Exception as exc:
            metadata["trajectory_log_error"] = str(exc)
            result.metadata = metadata
            return result
        if trajectory_logging_enabled():
            try:
                metadata["trajectory_record"] = append_runtime_trajectory(
                    trajectory_from_skill_run(
                        skill_name=result.skill_name,
                        success=result.success,
                        message=result.message,
                        inputs=inputs,
                        step_results=result.step_results,
                        metadata=metadata,
                    )
                )
            except Exception as exc:
                metadata["trajectory_log_error"] = str(exc)
        result.metadata = metadata
        return result

    @staticmethod
    def _resolve_arguments(arguments: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, value in arguments.items():
            if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
                resolved[key] = inputs.get(value[2:-2].strip())
            else:
                resolved[key] = value
        return resolved
