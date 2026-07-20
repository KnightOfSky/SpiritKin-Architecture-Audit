from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentContext, AgentReply
from backend.executors.base import ExecutionRequest
from backend.orchestrator.agent_container import evaluate_agent_container_scope
from backend.orchestrator.response_phase import ACTION_MAP
from backend.prompts.review import SKILL_ASSIST_FALLBACK_PROMPT
from backend.skills.base import SkillRunner
from backend.tools.base import ToolResult


@dataclass(frozen=True)
class SkillPhaseServices:
    skill_registry: Any
    tool_registry: Any
    workflow_memory: Any
    app_port: Any
    record_failure: Callable[..., Any]


class SkillPhase:
    def __init__(self, services: SkillPhaseServices):
        self._services = services

    def run(self, skill_spec: Any, context: AgentContext) -> AgentReply:
        scope_reply = agent_scope_reply(context, skill_name=skill_spec.name)
        if scope_reply is not None:
            return scope_reply
        policy = self._load_assist_policy()
        if policy.get("enabled") and policy.get("require_before_run"):
            prompt = self._build_assist_prompt(skill_spec.name, "执行前审核", context.user_input, "")
            return AgentReply(
                text=f"Skill {skill_spec.name} 已进入协助审核，确认后再执行。",
                emotion="waiting",
                action="await_confirmation",
                agent_name="skill_assist",
                requires_confirmation=True,
                metadata={
                    "response_kind": "skill_assist_required",
                    "skill_assist": {
                        "stage": "before_run",
                        "skill_name": skill_spec.name,
                        "mode": policy.get("mode"),
                        "prompt": prompt,
                        "selected_assistant_id": policy.get("selected_assistant_id"),
                    },
                },
            )

        result = SkillRunner(self._services.skill_registry, self._services.tool_registry).run(
            skill_spec.name,
            {"user_input": context.user_input, "visual_context": context.visual_context},
            dry_run=False,
        )
        self._services.workflow_memory.record_execution(
            user_input=context.user_input,
            request=ExecutionRequest(target="skill", operation=skill_spec.name, params={}),
            result=ToolResult(
                success=result.success,
                message=result.message,
                metadata={"skill_name": skill_spec.name, **dict(result.metadata or {})},
            ),
        )
        metadata: dict[str, object] = {
            "skill_run": {
                "skill_name": skill_spec.name,
                "success": result.success,
                "steps_completed": len(result.step_results),
                "metadata": dict(result.metadata or {}),
            }
        }
        if not result.success:
            self._services.record_failure(
                stage="skill",
                actor=skill_spec.name,
                message=result.message,
                user_input=context.user_input,
                error_code=str(result.metadata.get("error_code") or "skill_failed"),
                metadata={"skill_name": skill_spec.name, **dict(result.metadata or {})},
            )
            assist = self._handle_failure_assist(skill_spec.name, context, result.message, policy)
            if assist:
                metadata["skill_assist"] = assist
                metadata["response_kind"] = "skill_failed_with_assist"
        return AgentReply(
            text=result.message,
            emotion="happy" if result.success else "confused",
            action=ACTION_MAP.get("happy" if result.success else "confused", "idle"),
            agent_name=f"skill_{skill_spec.name}",
            metadata=metadata,
        )

    def _load_assist_policy(self) -> dict[str, object]:
        try:
            policy = self._services.app_port.skill_assist_policy()
            return dict(policy or {}) if isinstance(policy, dict) else {}
        except Exception:
            return {}

    def _build_assist_prompt(self, skill_name: str, problem: str, user_input: str, context: str) -> str:
        try:
            prompt = self._services.app_port.build_skill_assist_prompt(
                skill_name=skill_name,
                problem=problem,
                user_input=user_input,
                context=context,
            )
            if prompt:
                return str(prompt)
        except Exception:
            pass
        return SKILL_ASSIST_FALLBACK_PROMPT.substitute(
            skill_name=skill_name,
            problem=problem,
            user_input=user_input,
            context=context,
        ).strip()

    def _handle_failure_assist(
        self,
        skill_name: str,
        context: AgentContext,
        problem: str,
        policy: dict[str, object],
    ) -> dict[str, object]:
        if not policy.get("enabled") or not policy.get("require_on_failure", True):
            return {}
        review_prompt = self._build_assist_prompt(skill_name, problem, context.user_input, context.visual_context)
        assist: dict[str, object] = {
            "stage": "failure",
            "skill_name": skill_name,
            "mode": policy.get("mode"),
            "prompt": review_prompt,
            "selected_assistant_id": policy.get("selected_assistant_id"),
        }
        try:
            assist.update(
                self._services.app_port.record_skill_failure_assist(
                    skill_name=skill_name,
                    problem=problem,
                    user_input=context.user_input,
                    policy=dict(policy),
                    review_prompt=review_prompt,
                )
            )
        except Exception as exc:
            assist["learning_error"] = f"{type(exc).__name__}: {exc}"
        return assist


def agent_scope_reply(context: AgentContext, *, tool_name: str = "", skill_name: str = "") -> AgentReply | None:
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    runtime = metadata.get("agent_runtime") if isinstance(metadata.get("agent_runtime"), dict) else {}
    container = runtime.get("capability_container") if isinstance(runtime.get("capability_container"), dict) else {}
    if not container:
        return None
    decision = evaluate_agent_container_scope(container, tool_name=tool_name, skill_name=skill_name)
    if decision.allowed:
        return None
    target = tool_name or skill_name
    return AgentReply(
        text=f"当前 Agent 容器未授权执行: {target}",
        emotion="confused",
        action="tilt_head",
        agent_name="agent_scope_gate",
        metadata={"response_kind": "agent_scope_denied", "agent_scope": decision.snapshot()},
    )
