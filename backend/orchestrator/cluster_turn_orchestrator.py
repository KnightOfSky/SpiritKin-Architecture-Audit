from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from backend.agents.base import AgentReply
from backend.orchestrator.planner import ExecutionPlan
from backend.orchestrator.prompt_context import extract_backend_web_search_query, web_search_requested
from backend.orchestrator.reply_metadata import attach_project_metadata, build_context_metadata
from backend.orchestrator.request_coordinator import RequestCoordinator
from backend.tools.base import ToolCall


@dataclass(frozen=True)
class ClusterTurnServices:
    turn_context: Any
    set_active_metadata: Callable[[dict[str, object]], None]
    register_request: Callable[[dict[str, object]], None]
    is_stale: Callable[[dict[str, object]], bool]
    build_stale_reply: Callable[[dict[str, object]], AgentReply]
    build_cancelled_reply: Callable[[dict[str, object]], AgentReply]
    load_pending: Callable[[], Any]
    session_manager: Any
    confirmation_phase: Any
    execution_guard: Any
    finalize_reply: Callable[[AgentReply], AgentReply]
    handle_agent_status: Callable[[Any], AgentReply]
    build_inventory_context: Callable[[], str]
    capability_inventory: Callable[[], dict[str, object]]
    resource_registry: Callable[[], dict[str, object]]
    perception_enricher: Callable[..., tuple[str, dict[str, object]]]
    device_name: str
    handle_plan_mode: Callable[[Any], AgentReply]
    handle_goal_mode: Callable[[Any], AgentReply]
    tool_registry: Any
    hybrid_planner: Any
    plan_hybrid: Callable[[Any], Any]
    intent_phase: Any
    resource_budget: Any
    task_queue: Any
    resolve_project: Callable[..., Any]
    ecommerce_projects: Any
    build_busy_reply: Callable[..., AgentReply]
    dispatch_plan: Callable[[Any, ExecutionPlan], AgentReply]
    attach_plan_metadata: Callable[[AgentReply, ExecutionPlan], AgentReply]


class ClusterTurnOrchestrator:
    def __init__(self, services: ClusterTurnServices):
        self._services = services

    def process(
        self,
        user_input: str,
        visual_context: str = "",
        channel: str = "text",
        input_metadata: dict | None = None,
    ) -> AgentReply:
        turn = self._services.turn_context.begin(user_input, input_metadata)
        metadata = turn.metadata
        effective_input = turn.effective_input
        self._services.set_active_metadata(metadata)
        self._services.register_request(metadata)
        if str(metadata.get("control_action") or "").strip().lower() == "cancel_generation":
            return self._services.build_cancelled_reply(metadata)
        if self._services.is_stale(metadata):
            return self._services.build_stale_reply(metadata)
        if self._services.load_pending() is not None:
            self._services.session_manager.record_user_turn(effective_input)
            return self._services.finalize_reply(self._services.confirmation_phase.handle(effective_input))
        mode_claims_input = metadata.get("plan_mode") is True or metadata.get("pursue_goal") is True
        confirmation = self._services.execution_guard.decide_confirmation(effective_input)
        if not mode_claims_input and confirmation.status != "unknown":
            self._services.session_manager.record_user_turn(effective_input)
            return self._services.finalize_reply(self._orphan_confirmation_reply())
        if turn.agent_mention is not None and turn.agent_mention.intent == "status":
            self._services.session_manager.record_user_turn(user_input)
            return self._services.finalize_reply(self._services.handle_agent_status(turn.agent_mention))

        turn = self._services.turn_context.enrich(
            turn,
            channel=channel,
            visual_context=visual_context,
            inventory_context=self._services.build_inventory_context(),
            capability_inventory=self._services.capability_inventory(),
            resource_registry=self._services.resource_registry(),
            perception_enricher=self._services.perception_enricher,
        )
        metadata = turn.metadata
        visual_context = turn.visual_context
        self._services.set_active_metadata(metadata)
        initial_context = self._services.session_manager.build_context(
            user_input=effective_input,
            visual_context=visual_context,
            device_name=self._services.device_name,
            metadata=metadata,
        )
        if metadata.get("plan_mode") is True:
            self._services.session_manager.record_user_turn(effective_input)
            return self._services.finalize_reply(self._services.handle_plan_mode(initial_context))
        if metadata.get("pursue_goal") is True and str(metadata.get("goal_text") or "").strip():
            self._services.session_manager.record_user_turn(effective_input)
            return self._services.finalize_reply(self._services.handle_goal_mode(initial_context))

        hybrid_result, plan = self._build_plan(initial_context, metadata, effective_input)
        metadata["hybrid_planner"] = hybrid_result.snapshot()
        if self._services.intent_phase.should_run_before_planner(channel, effective_input, metadata, plan):
            intent_reply = self._run_intent_first(initial_context, channel, effective_input, metadata)
            if intent_reply is not None:
                return intent_reply

        reservation = self._acquire(plan, metadata)
        if reservation is None:
            return self._queue_or_busy(plan, effective_input, visual_context)
        if self._services.is_stale(metadata):
            self._services.resource_budget.release(reservation)
            return self._services.build_stale_reply(metadata)

        project = self._services.resolve_project(plan, effective_input)
        context = self._services.session_manager.build_context(
            user_input=effective_input,
            visual_context=visual_context,
            device_name=self._services.device_name,
            metadata={**build_context_metadata(project), **metadata},
        )
        context = replace(context, metadata={**dict(context.metadata or {}), "hybrid_planner": hybrid_result.snapshot()})
        try:
            reply = self._services.dispatch_plan(context, plan)
        finally:
            self._services.resource_budget.release(reservation)
        if self._services.is_stale(metadata):
            return self._services.build_stale_reply(metadata)
        self._services.session_manager.record_user_turn(effective_input)
        if project is not None:
            project = self._services.ecommerce_projects.note_task(
                project.project_id,
                status="active",
                summary=reply.text[:120],
                detail="实时阶段建议已生成",
            ) or project
        reply = attach_project_metadata(self._services.attach_plan_metadata(reply, plan), project)
        return self._services.finalize_reply(reply)

    def _build_plan(self, context: Any, metadata: dict[str, object], user_input: str) -> tuple[Any, ExecutionPlan]:
        if self._services.tool_registry.get("web.search") is not None and web_search_requested(metadata, user_input):
            plan = ExecutionPlan(
                route="tool",
                tool_call=ToolCall(
                    name="web.search",
                    arguments={"query": extract_backend_web_search_query(user_input), "count": 5},
                ),
                reason="命中后端联网搜索工具",
                domain="search",
                priority_score=190,
                resource_profile="cpu_io",
            )
            return self._services.hybrid_planner.describe_plan(context, plan), plan
        result = self._services.plan_hybrid(context)
        return result, result.execution_plan

    def _run_intent_first(
        self,
        context: Any,
        channel: str,
        user_input: str,
        metadata: dict[str, object],
    ) -> AgentReply | None:
        normalized_channel = channel.strip().lower() or "text"
        source = "llm_voice_first" if normalized_channel == "voice" else f"llm_{normalized_channel}_first"
        reply = self._services.intent_phase.resolve(context, source=source)
        if reply is None:
            return None
        self._services.session_manager.record_user_turn(user_input)
        plan = ExecutionPlan(
            route="intent",
            reason="自然语言动作输入优先交给意图智能体纠错和理解",
            domain="execution",
            priority_score=240,
            resource_profile="cpu_io",
        )
        snapshot = self._services.hybrid_planner.describe_plan(context, plan).snapshot()
        metadata["hybrid_planner"] = snapshot
        reply.metadata = {**dict(reply.metadata or {}), "hybrid_planner": snapshot}
        return self._services.finalize_reply(self._services.attach_plan_metadata(reply, plan))

    def _acquire(self, plan: ExecutionPlan, metadata: dict[str, object]):
        reservation = self._services.resource_budget.try_acquire(plan.resource_profile)
        if reservation is None and RequestCoordinator.latest_wins_enabled(metadata):
            reservation = self._services.resource_budget.wait_acquire(plan.resource_profile, timeout=45.0)
        return reservation

    def _queue_or_busy(self, plan: ExecutionPlan, user_input: str, visual_context: str) -> AgentReply:
        self._services.session_manager.record_user_turn(user_input)
        task = self._services.task_queue.enqueue(request=user_input, visual_context=visual_context, plan=plan)
        project = self._services.resolve_project(plan, user_input, task_id=task.task_id)
        if project is not None:
            task.project_id = project.project_id
            project = self._services.ecommerce_projects.note_task(
                project.project_id,
                task_id=task.task_id,
                status="queued",
                detail="等待资源释放",
            ) or project
        reply = attach_project_metadata(self._services.build_busy_reply(plan, task), project)
        return self._services.finalize_reply(reply)

    @staticmethod
    def _orphan_confirmation_reply() -> AgentReply:
        return AgentReply(
            text="当前没有等待确认的操作。",
            emotion="neutral",
            action="idle",
            agent_name="execution_guard",
            spoken_text="当前没有等待确认的操作。",
            metadata={"response_kind": "message"},
        )
