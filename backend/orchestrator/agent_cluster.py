"""Agent cluster runtime: routing, execution, confirmation, and reply assembly.

The facade has been carved from 2753 lines to roughly 550 lines. Routing,
intent, tool/skill execution, retries, confirmation, response assembly,
managed-agent roster, and model calls now live in focused collaborators.
The app/orchestrator dependency is inverted through ``AgentClusterAppPort``:
standalone orchestration uses a null port, while ``SpiritKinRuntime`` injects
the app-owned snapshot and learning integrations.
"""

from __future__ import annotations

from dataclasses import asdict

from backend.agents.base import (
    AgentContext,
    AgentReply,
    BaseAgent,
)
from backend.executors.base import ExecutionRequest
from backend.knowledge import build_project_docs_embedding_retriever, build_project_docs_retriever
from backend.orchestrator.agent_cluster_bootstrap import initialize_agent_cluster
from backend.orchestrator.agent_cluster_wiring import AgentClusterWiring
from backend.orchestrator.agent_mentions import AgentMention
from backend.orchestrator.brain_router import BrainRouter, BrainRouterDecision
from backend.orchestrator.builtins import calc as _builtin_calc
from backend.orchestrator.builtins import get_time as _builtin_get_time
from backend.orchestrator.builtins import handle_builtin as _handle_builtin_route
from backend.orchestrator.cluster_deps import ClusterDeps
from backend.orchestrator.context_assets import ContextAssetStore
from backend.orchestrator.ecommerce_projects import EcommerceProject
from backend.orchestrator.execution_guard import PendingExecution
from backend.orchestrator.hybrid_planner import HybridPlannerResult
from backend.orchestrator.planner import ExecutionPlan
from backend.orchestrator.repair import (
    FailureRecord,
)
from backend.orchestrator.repair import (
    build_development_plan as build_development_plan_from_request,
)
from backend.orchestrator.reply_metadata import (
    attach_task_metadata,
)
from backend.orchestrator.request_coordinator import RequestCoordinator
from backend.orchestrator.resource_registry import (
    register_runtime_resources,
)
from backend.orchestrator.scheduler_task_finalizer import finalize_scheduler_task
from backend.orchestrator.task_queue import ScheduledTask
from backend.orchestrator.text_utils import current_time_context
from backend.skills import PromotionRuleSet


def _current_time_context() -> dict[str, str]:
    return current_time_context()


class AgentCluster:
    """第一版智能体集群协调器。"""

    def __init__(
        self,
        llm_client=None,
        memory_limit: int = 20,
        device_name: str = "local_pc",
        *,
        deps: ClusterDeps | None = None,
        wiring: AgentClusterWiring | None = None,
        **legacy_wiring,
    ):
        if deps is not None and (wiring is not None or legacy_wiring):
            raise TypeError("AgentCluster deps cannot be combined with wiring or legacy keyword dependencies")
        wiring = (wiring or AgentClusterWiring()).with_legacy_overrides(legacy_wiring)
        deps = deps or ClusterDeps.from_wiring(llm_client, wiring)
        llm_client = deps.llm.client
        wiring = AgentClusterWiring.from_deps(deps)
        initialize_agent_cluster(
            self,
            wiring=wiring,
            llm_client=llm_client,
            memory_limit=memory_limit,
            device_name=device_name,
            current_time_context=_current_time_context,
        )
        return
    def _call_llm_for_agent(self, prompt: str, *args, **kwargs):
        return self._model_calls.call(prompt, *args, **kwargs)

    def _parse_agent_mention(self, user_input: str) -> AgentMention | None:
        return self._cluster_router.route(user_input).agent_mention

    @property
    def memory(self):
        return self._inspection.memory

    @property
    def available_tools(self):
        return self._inspection.available_tools

    @property
    def available_skills(self):
        return self._inspection.available_skills

    @property
    def recent_failures(self):
        return self._inspection.recent_failures

    @property
    def recent_inventory(self):
        return self._inspection.recent_inventory

    @property
    def capability_inventory_snapshot(self):
        return self._inspection.capability_inventory_snapshot

    @property
    def worker_environment_snapshot(self):
        return self._inspection.worker_environment_snapshot

    @property
    def capability_graph_snapshot(self):
        return self._inspection.capability_graph_snapshot

    @property
    def worker_pool_snapshot(self):
        return self._inspection.worker_pool_snapshot

    @property
    def resource_registry_snapshot(self):
        return self._inspection.resource_registry_snapshot

    @property
    def brain_router_snapshot(self):
        return self._inspection.brain_router_snapshot

    @property
    def workflow_memory_snapshot(self):
        return self._inspection.workflow_memory_snapshot

    @property
    def workflow_memory_stats(self):
        return self._inspection.workflow_memory_stats

    @property
    def workflow_skill_candidates(self):
        return self._inspection.workflow_skill_candidates

    def _sync_workflow_skill_candidates(self) -> None:
        self._inspection.sync_workflow_skill_candidates()

    def review_skill_candidates(self, rules: PromotionRuleSet | None = None, *, reviewer: str = "rules_engine"):
        return self._inspection.review_skill_candidates(rules, reviewer=reviewer)

    @property
    def last_repair_advice(self):
        return self._inspection.last_repair_advice

    def build_self_improvement_report(self):
        return self._inspection.build_self_improvement_report()

    @property
    def pending_execution(self):
        return self._pending_execution_store.load()

    @property
    def _pending_execution(self) -> PendingExecution | None:
        return self._pending_execution_store.pending

    @_pending_execution.setter
    def _pending_execution(self, value: PendingExecution | None) -> None:
        self._pending_execution_store.pending = value

    def _save_pending_execution(self, pending: PendingExecution) -> None:
        self._pending_execution_store.save(pending)

    def _load_pending_execution(self) -> PendingExecution | None:
        return self._pending_execution_store.load()

    def _clear_pending_execution(self) -> None:
        self._pending_execution_store.clear()

    @property
    def resource_budget_snapshot(self):
        return self._inspection.resource_budget_snapshot

    @property
    def task_queue_snapshot(self):
        return self._inspection.task_queue_snapshot

    def _build_agent_status_snapshot(self, mention: AgentMention) -> dict[str, object]:
        return self._inspection.build_agent_status_snapshot(mention)

    def _handle_agent_mention_status(self, mention: AgentMention) -> AgentReply:
        return self._inspection.handle_agent_status(mention)

    @property
    def ecommerce_projects_snapshot(self):
        return self._inspection.ecommerce_projects_snapshot

    def get_task(self, task_id: str):
        return self._inspection.get_task(task_id)

    def list_active_tasks(self):
        return self._inspection.list_active_tasks()

    def get_ecommerce_project(self, project_id: str):
        return self._inspection.get_ecommerce_project(project_id)

    def list_active_ecommerce_projects(self, project_type: str | None = None, status: str | None = None):
        return self._inspection.list_active_ecommerce_projects(project_type, status)

    def drain_queued_tasks(self, max_tasks: int = 1) -> list[AgentReply]:
        return self._inspection.drain_queued_tasks(max_tasks)

    def clear_recent_failures(self) -> None:
        self._inspection.clear_recent_failures()

    def build_repair_plan(self, failure: FailureRecord | None = None):
        return self._inspection.build_repair_plan(failure)

    def build_development_plan(self, request: str):
        return build_development_plan_from_request(
            request,
            available_tool_names=[tool.name for tool in self.available_tools],
        )

    def _handle_development_plan(self, request: str) -> AgentReply:
        plan = self.build_development_plan(request)
        targets = "、".join(plan.target_integrations) or "外部能力"

        text_parts = [
            f"已为 {targets} 整理 development plan。",
            f"摘要：{plan.summary}",
        ]
        if plan.suggested_actions:
            text_parts.append("建议动作：\n- " + "\n- ".join(plan.suggested_actions[:5]))
        if plan.candidate_files:
            text_parts.append("候选文件：\n- " + "\n- ".join(plan.candidate_files[:8]))
        if plan.suggested_test_commands:
            text_parts.append("建议验证：\n- " + "\n- ".join(plan.suggested_test_commands[:3]))
        text_parts.append("默认策略：先人工审核，再决定是否进入下一步实现。")

        return AgentReply(
            text="\n".join(text_parts),
            emotion="thinking",
            action="plan_development",
            agent_name="development_planner",
            spoken_text=f"我已经生成 {targets} 的接入开发计划，优先 API 接入，重要写操作继续走确认和人工审核。",
            metadata={
                "response_kind": "development_plan",
                "development_plan": asdict(plan),
            },
        )

    @staticmethod
    def _resolve_knowledge_retriever(
        knowledge_retriever=None,
        auto_load_project_docs: bool = True,
        knowledge_backend: str = "keyword",
    ):
        if knowledge_retriever is not None:
            return knowledge_retriever
        if not auto_load_project_docs:
            return None
        try:
            if knowledge_backend == "embedding":
                return build_project_docs_embedding_retriever()
            return build_project_docs_retriever()
        except Exception:
            return None

    @staticmethod
    def _get_time() -> str:
        return _builtin_get_time()

    @staticmethod
    def _calc(expr: str) -> str:
        return _builtin_calc(expr)

    def _handle_builtin(self, builtin_name: str, user_input: str):
        return _handle_builtin_route(builtin_name, user_input)

    def _handle_general(self, context: AgentContext) -> AgentReply:
        return self._response_phase.respond(context, inventory=self._recent_inventory)

    def _handle_plan_mode(self, context: AgentContext) -> AgentReply:
        return self._response_phase.plan(context)

    def _handle_pursue_goal(self, context: AgentContext) -> AgentReply:
        return self._response_phase.pursue_goal(context)

    def _correct_app_name_via_inventory(self, app_name: str) -> str:
        """Use device backend fuzzy matching against installed apps."""
        if not app_name or len(app_name) < 2:
            return app_name
        try:
            be = self.device_backend
            if be and hasattr(be, "find_installed_app"):
                match = be.find_installed_app(app_name)
                if match:
                    name = str(match.get("name") or match.get("display_name") or "")
                    if name and name.lower() != app_name.lower():
                        return name
        except Exception:
            pass
        return app_name

    def _handle_intent_fallback(self, context: AgentContext, source: str = "llm_fallback") -> AgentReply | None:
        return self._intent_phase.resolve(context, source)

    def _should_try_intent_before_planner(self, channel: str, user_input: str, metadata: dict | None = None, plan: ExecutionPlan | None = None) -> bool:
        return self._intent_phase.should_run_before_planner(channel, user_input, metadata, plan)

    def _build_brain_router(self) -> BrainRouter:
        try:
            model_catalog = self._app_port.model_catalog_snapshot()
        except Exception:
            model_catalog = {}
        return BrainRouter(agent_profiles=self._agent_profiles_by_id, model_catalog=model_catalog)

    def _refresh_capability_registry(self) -> None:
        self._capability_registry, self._hybrid_planner = self._capability_manager.refresh(
            worker_pool=self._worker_pool,
            planner=self._planner,
        )

    def _attach_worker_pool_to_tools(self) -> None:
        self._capability_manager.attach_worker_pool_to_tools(self._worker_pool)

    def _refresh_external_worker_pool_descriptors(self) -> None:
        self._worker_pool.set_external_workers(self._capability_manager.external_worker_descriptors())

    def _refresh_resource_registry(self) -> None:
        ecommerce_registry = getattr(self, "_ecommerce_projects", None)
        projects = []
        if ecommerce_registry is not None:
            try:
                projects = ecommerce_registry.list_projects(active_only=False)
            except Exception:
                projects = []
        register_runtime_resources(
            self._resource_registry,
            workers=self._worker_pool.snapshot().get("workers", []),
            device_name=self.device_name,
            device_ready=self.device_backend is not None,
            projects=projects,
        )

    def _route_brain_for_agent(
        self,
        agent_id: str,
        task_text: str = "",
        *,
        route: str = "",
        domain: str = "",
        risk_level: str = "",
        required_capabilities: list[str] | tuple[str, ...] | None = None,
    ) -> BrainRouterDecision:
        return self._model_calls.route(
            agent_id,
            task_text,
            route=route,
            domain=domain,
            risk_level=risk_level,
            required_capabilities=required_capabilities,
        )

    def _plan_hybrid(self, context: AgentContext) -> HybridPlannerResult:
        return self._hybrid_planner.plan(context, self._agents, self.available_tools)

    def _find_executor(self, request: ExecutionRequest):
        executor, _ = self._worker_pool.find_worker(request)
        return executor

    def _resolve_request_risk_level(self, request: ExecutionRequest) -> str:
        return self._execution_guard.resolve_risk_level(request, self.available_tools)

    def _build_confirmation_reply(self, pending: PendingExecution) -> AgentReply:
        return self._execution_guard.build_confirmation_reply(pending)

    def _requires_confirmation(self, request: ExecutionRequest, skip_confirmation: bool = False) -> bool:
        return self._execution_guard.requires_confirmation(
            request,
            self.available_tools,
            skip_confirmation=skip_confirmation,
        )

    def _active_input_has_full_access(self) -> bool:
        metadata = getattr(self, "_active_input_metadata", {}) or {}
        permission_mode = str(metadata.get("permission_mode") or "").strip().lower()
        granted_value = metadata.get("full_access_granted", False)
        granted = granted_value is True or str(granted_value).strip().lower() in {"1", "true", "yes", "on"}
        return permission_mode == "full_access" and granted

    def _evaluate_policy(self, request: ExecutionRequest):
        return self._execution_guard.evaluate_policy(request, self.available_tools)

    def _handle_confirmation_response(self, user_input: str) -> AgentReply:
        return self._confirmation_phase.handle(user_input)

    def _record_failure(
        self,
        *,
        stage: str,
        actor: str,
        message: str,
        user_input: str = "",
        error_code: str = "",
        tool_name: str = "",
        execution_request: ExecutionRequest | None = None,
        arguments=None,
        metadata=None,
    ) -> FailureRecord:
        return self._failure_trajectory.record(
            stage=stage,
            actor=actor,
            message=message,
            user_input=user_input,
            error_code=error_code,
            tool_name=tool_name,
            execution_request=execution_request,
            arguments=arguments,
            metadata=metadata,
        )

    def _append_runtime_failure_trajectory(self, **kwargs) -> dict[str, object] | None:
        return self._failure_trajectory.append_failure(**kwargs)

    def _append_runtime_execution_trajectory(
        self,
        *,
        user_input: str,
        request: ExecutionRequest,
        result,
        worker_execution,
        actor: str,
    ) -> dict[str, object] | None:
        return self._failure_trajectory.append_execution(
            user_input=user_input,
            request=request,
            result=result,
            worker_execution=worker_execution,
            actor=actor,
        )

    def _handle_execution(self, request: ExecutionRequest, user_input: str = "", skip_confirmation: bool = False) -> AgentReply:
        return self._execution_phase.execute(
            request,
            user_input=user_input,
            skip_confirmation=skip_confirmation,
        )

    def _enrich_perception_context(
        self,
        *,
        user_input: str,
        visual_context: str,
        metadata: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        return self._context_assets.enrich_perception(
            user_input=user_input,
            visual_context=visual_context,
            metadata=metadata,
        )

    def _remember_inventory_snapshot(self, request: ExecutionRequest, data, metadata: dict | None = None) -> dict[str, object] | None:
        return self._context_assets.remember_inventory(request, data, metadata)

    @staticmethod
    def _inventory_scope(request: ExecutionRequest, metadata: dict | None = None) -> tuple[str, dict[str, str]]:
        return ContextAssetStore.inventory_scope(request, metadata)

    def _remember_scoped_inventory(self, kind: str, items: list[dict], *, request: ExecutionRequest, metadata: dict | None = None) -> dict[str, object]:
        return self._context_assets.remember_scoped(kind, items, request=request, metadata=metadata)

    def _build_inventory_context(self) -> str:
        return self._context_assets.build_inventory_context()

    def _resolve_agent_knowledge_retriever(self, knowledge_base: dict[str, object]):
        return self._agent_knowledge_resolver.resolve_retriever(knowledge_base)

    def _with_agent_knowledge_hits(self, context: AgentContext, policy: dict[str, object]) -> AgentContext:
        return self._agent_knowledge_resolver.with_hits(context, policy)

    def _build_agent_runtime_policy(self, agent_id: str) -> dict[str, object]:
        return self._agent_runtime_context.runtime_policy(agent_id)

    def _build_agent_capability_container(
        self,
        agent_id: str,
        *,
        plan=None,
        context: AgentContext | None = None,
    ):
        return self._agent_runtime_context.capability_container(agent_id, plan=plan, context=context)

    def _build_agent_architecture_metadata(self, plan, agent_id: str = "") -> dict[str, object]:
        return self._agent_runtime_context.architecture_metadata(plan, agent_id)

    def _with_agent_runtime_context(self, context: AgentContext, plan) -> AgentContext:
        return self._agent_runtime_context.with_runtime_context(context, plan)

    def _with_code_workspace_context(self, context: AgentContext) -> AgentContext:
        return self._agent_runtime_context.with_code_workspace_context(context)

    def _handle_tool(self, context: AgentContext, tool_call) -> AgentReply:
        return self._tool_phase.run(context, tool_call)

    def _attach_plan_metadata(self, reply: AgentReply, plan) -> AgentReply:
        metadata = dict(reply.metadata)
        metadata.setdefault("scheduler", {})
        agent_id = str(getattr(getattr(plan, "agent", None), "name", "") or reply.agent_name or "").strip()
        existing_runtime = metadata.get("agent_runtime") if isinstance(metadata.get("agent_runtime"), dict) else None
        metadata["scheduler"] = {
            **dict(metadata.get("scheduler") or {}),
            "route": plan.route,
            "domain": plan.domain,
            "priority_score": plan.priority_score,
            "resource_profile": plan.resource_profile,
            "reason": plan.reason,
            "agent_id": agent_id,
            "agent_runtime": existing_runtime or self._build_agent_architecture_metadata(plan, agent_id),
        }
        if isinstance(metadata.get("hybrid_planner"), dict):
            metadata["scheduler"]["hybrid_planner"] = metadata["hybrid_planner"]
        reply.metadata = metadata
        return reply

    def _apply_scheduler_task_finalizer(self, task: ScheduledTask) -> ScheduledTask:
        verdict = finalize_scheduler_task(task)
        return self._task_queue.apply_finalizer_verdict(task.task_id, verdict, source="scheduler_task")

    def _register_latest_request(self, metadata: dict[str, object]) -> None:
        self._request_coordinator.register(metadata)

    def _set_active_input_metadata(self, metadata: dict[str, object]) -> None:
        self._active_input_metadata = metadata

    def _is_stale_request(self, metadata: dict[str, object]) -> bool:
        return self._request_coordinator.is_stale(metadata)

    def _build_stale_request_reply(self, metadata: dict[str, object]) -> AgentReply:
        return AgentReply(
            text="",
            emotion="neutral",
            action="idle",
            agent_name="request_coordinator",
            spoken_text="",
            metadata={
                "response_kind": "stale_request",
                "request_id": RequestCoordinator.request_id(metadata),
                "request_scope": RequestCoordinator.request_scope(metadata),
            },
        )

    def _build_cancelled_request_reply(self, metadata: dict[str, object]) -> AgentReply:
        return AgentReply(
            text="",
            emotion="neutral",
            action="idle",
            agent_name="request_coordinator",
            spoken_text="",
            metadata={
                "response_kind": "request_cancelled",
                "request_id": RequestCoordinator.request_id(metadata),
                "cancelled_request_id": str(metadata.get("cancelled_request_id") or ""),
                "request_scope": RequestCoordinator.request_scope(metadata),
            },
        )

    def _resolve_ecommerce_project(self, plan: ExecutionPlan, request: str, task_id: str = "") -> EcommerceProject | None:
        if plan.domain != "ecommerce":
            return None
        return self._ecommerce_projects.ensure_project(request, task_id=task_id)

    def _build_resource_busy_reply(self, plan, task: ScheduledTask | None = None) -> AgentReply:
        resource_labels = {
            "gpu_heavy": "本地重推理",
            "cpu_io": "后台工具",
            "interactive": "实时交互",
        }
        resource_label = resource_labels.get(plan.resource_profile, "当前")
        reply = AgentReply(
            text=f"当前 {resource_label} 通道正忙，建议先排队处理这个 {plan.domain} 任务，避免本地 3060 同时抢资源。",
            emotion="thinking",
            action="queue_task",
            agent_name="scheduler",
            spoken_text="当前本地重任务通道正忙，我建议把这个任务排队，避免卡死。",
            metadata={
                "response_kind": "scheduler_busy",
                "scheduler": {
                    "route": plan.route,
                    "domain": plan.domain,
                    "priority_score": plan.priority_score,
                    "resource_profile": plan.resource_profile,
                    "reason": plan.reason,
                    "snapshot": self.resource_budget_snapshot,
                    "queued_tasks": self._task_queue.list_tasks(include_finished=False),
                },
            },
        )
        return attach_task_metadata(reply, task)

    def _handle_execution_with_validation(self, request, context: AgentContext):
        return self._execution_validation.run(request, context)

    def _handle_skill(self, skill_spec, context: AgentContext):
        return self._skill_phase.run(skill_spec, context)

    def _dispatch_plan(self, context: AgentContext, plan: ExecutionPlan) -> AgentReply:
        return self._plan_dispatcher.dispatch(context, plan)

    def _run_planned_agent(self, agent: BaseAgent, context: AgentContext) -> AgentReply:
        agent_id = str(getattr(agent, "name", "") or "").strip()
        adapter = self._agent_adapters_by_id.get(agent_id)
        if adapter is not None:
            return adapter.run(agent, context)
        return agent.handle(context)

    def process_next_queued_task(self) -> AgentReply | None:
        return self._queued_task_phase.process_next()

    def process(self, user_input: str, visual_context: str = "", channel: str = "text", input_metadata: dict | None = None) -> AgentReply:
        return self._turn_orchestrator.process(user_input, visual_context, channel, input_metadata)

    def _finalize_reply(self, reply: AgentReply) -> AgentReply:
        if reply.spoken_text is None:
            reply.spoken_text = reply.text
        self._record_reply_performance(reply)
        self._session_manager.record_agent_turn(reply)
        return reply

    def _record_reply_performance(self, reply: AgentReply) -> None:
        if self._performance_tracker is None:
            return
        metadata = reply.metadata if isinstance(reply.metadata, dict) else {}
        execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else None
        scheduler = metadata.get("scheduler") if isinstance(metadata.get("scheduler"), dict) else {}
        success = True if execution is None else bool(execution.get("success", True))
        error_code = "" if execution is None else str(execution.get("error_code") or "")
        if metadata.get("response_kind") in {"policy_denied", "task_failed"}:
            success = False
            error_code = error_code or str(metadata.get("response_kind"))
        last_user = ""
        for item in reversed(self._session_manager.transcript):
            if item.get("role") == "user":
                last_user = str(item.get("content") or "")
                break
        self._performance_tracker.record(
            agent_name=reply.agent_name or "unknown",
            domain=str(scheduler.get("domain") or "general"),
            success=success,
            user_input_snippet=last_user,
            error_code=error_code,
        )
