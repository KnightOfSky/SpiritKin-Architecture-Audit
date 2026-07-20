from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from backend.agents.ecommerce_agent import EcommerceAgent
from backend.agents.game_development_agent import GameDevelopmentAgent
from backend.agents.programming_agent import ProgrammingAgent
from backend.agents.video_animation_agent import VideoAnimationAgent
from backend.agents.vision_agent import VisionAgent
from backend.devices.registry import get_device_backend
from backend.evaluation.trajectory import TrajectoryAnalyzer
from backend.memory import InMemoryWorkflowMemory
from backend.orchestrator.agent_knowledge_resolver import AgentKnowledgeResolver
from backend.orchestrator.agent_performance import AgentPerformanceTracker
from backend.orchestrator.agent_roster import AgentRoster
from backend.orchestrator.agent_runtime_context import AgentRuntimeContextBuilder, AgentRuntimeContextServices
from backend.orchestrator.app_ports import NullAgentClusterAppPort
from backend.orchestrator.capability_manager import CapabilityManager
from backend.orchestrator.cluster_inspection import ClusterInspectionFacade, ClusterInspectionServices
from backend.orchestrator.cluster_turn_orchestrator import ClusterTurnOrchestrator, ClusterTurnServices
from backend.orchestrator.cluster_wiring import build_default_executors
from backend.orchestrator.confirmation_phase import ConfirmationPhase, ConfirmationPhaseServices
from backend.orchestrator.context_assets import ContextAssetServices, ContextAssetStore
from backend.orchestrator.decision_cache import DecisionCache
from backend.orchestrator.ecommerce_projects import EcommerceProjectRegistry
from backend.orchestrator.execution_guard import ExecutionGuard
from backend.orchestrator.execution_phase import ExecutionPhase, ExecutionPhaseServices
from backend.orchestrator.execution_validation import ExecutionValidationPhase, ExecutionValidationServices
from backend.orchestrator.failure_log import FailureLog
from backend.orchestrator.failure_trajectory_phase import FailureTrajectoryPhase
from backend.orchestrator.hybrid_planner import HybridPlannerPipeline
from backend.orchestrator.intent_phase import IntentPhase, IntentPhaseServices
from backend.orchestrator.intent_resolver import IntentResolver
from backend.orchestrator.model_call_coordinator import ModelCallCoordinator
from backend.orchestrator.pending_execution_store import PendingExecutionStore
from backend.orchestrator.plan_dispatcher import PlanDispatcher, PlanDispatchServices
from backend.orchestrator.planner import Planner
from backend.orchestrator.queued_task_phase import QueuedTaskPhase, QueuedTaskServices
from backend.orchestrator.repair import RuleBasedRepairAdvisor
from backend.orchestrator.request_coordinator import RequestCoordinator
from backend.orchestrator.resource_budget import ResourceBudgetGate
from backend.orchestrator.resource_registry import ResourceRegistry, build_resource_registry_store
from backend.orchestrator.response_phase import SoulResponsePhase
from backend.orchestrator.session_manager import SessionManager
from backend.orchestrator.skill_phase import SkillPhase, SkillPhaseServices
from backend.orchestrator.task_queue import TaskQueue
from backend.orchestrator.tool_phase import ToolPhase, ToolPhaseServices
from backend.orchestrator.turn_context import TurnContextPreparer
from backend.orchestrator.worker_pool import WorkerPool
from backend.skills import SkillRegistry
from backend.tools import ToolCall, build_default_tool_registry


def initialize_agent_cluster(
    cluster: Any,
    *,
    wiring: Any,
    llm_client: Any,
    memory_limit: int,
    device_name: str,
    current_time_context: Callable[[], dict[str, str]],
) -> None:
    """Assemble AgentCluster internals while the public class stays a facade."""
    if llm_client is None:
        from backend.services.conversation_engine import get_llm_response

        llm_client = get_llm_response

    cluster.device_name = device_name
    cluster.device_backend = wiring.device_backend or get_device_backend(device_name)
    cluster._llm_client = llm_client
    cluster._app_port = wiring.app_port or NullAgentClusterAppPort()
    cluster._node_registry = wiring.node_registry
    cluster._agent_knowledge_resolver = AgentKnowledgeResolver()
    llm_for_agent = cluster._call_llm_for_agent
    configured_agents = wiring.agents or [
        VisionAgent(cluster.device_backend),
        VideoAnimationAgent(llm_for_agent),
        GameDevelopmentAgent(llm_for_agent),
        ProgrammingAgent(llm_for_agent),
        EcommerceAgent(llm_for_agent),
    ]
    roster = AgentRoster.build(configured_agents, wiring.managed_agents)
    cluster._managed_agents = roster.managed_agents
    cluster._agent_profiles_by_id = roster.profiles_by_id
    cluster._agents = roster.agents
    cluster._cluster_router = roster.router
    cluster._agent_adapters_by_id = roster.adapters_by_id
    knowledge_retriever = cluster._resolve_knowledge_retriever(
        knowledge_retriever=wiring.knowledge_retriever,
        auto_load_project_docs=wiring.auto_load_project_docs,
        knowledge_backend=wiring.knowledge_backend,
    )
    cluster._executors = wiring.executors or build_default_executors(
        cluster.device_backend,
        device_name,
        node_registry=wiring.node_registry,
        feishu_client=wiring.feishu_client,
        openclaw_client=wiring.openclaw_client,
        openclaw_client_factory=wiring.openclaw_client_factory,
        openclaw_state_path=wiring.openclaw_state_path,
        knowledge_retriever=knowledge_retriever,
    )
    cluster._tool_registry = wiring.tool_registry or build_default_tool_registry(knowledge_retriever=knowledge_retriever)
    cluster._skill_registry = wiring.skill_registry or SkillRegistry()
    if wiring.skill_store is not None and hasattr(wiring.skill_store, "list_all"):
        cluster._skill_registry.load_from_store(wiring.skill_store)
    cluster._capability_manager = CapabilityManager(
        tool_registry=cluster._tool_registry,
        skill_registry=cluster._skill_registry,
        node_registry=cluster._node_registry,
        agents=cluster._agents,
        executors=cluster._executors,
    )
    cluster._capability_registry = wiring.capability_registry or cluster._capability_manager.build_registry()
    cluster._worker_pool = wiring.worker_pool or WorkerPool(
        cluster._executors,
        capability_registry=cluster._capability_registry,
    )
    cluster._worker_pool.set_capability_registry(cluster._capability_registry)
    cluster._refresh_external_worker_pool_descriptors()
    cluster._attach_worker_pool_to_tools()
    cluster._resource_registry_store = build_resource_registry_store(wiring.resource_registry_path)
    cluster._resource_registry = wiring.resource_registry or (
        cluster._resource_registry_store.load()
        if cluster._resource_registry_store is not None
        else ResourceRegistry()
    )
    cluster._brain_router = wiring.brain_router or cluster._build_brain_router()
    cluster._model_calls = ModelCallCoordinator(
        cluster._llm_client,
        cluster._brain_router,
        input_metadata=lambda: cluster._active_input_metadata,
    )
    cluster._response_phase = SoulResponsePhase(cluster._call_llm_for_agent)
    cluster._planner = wiring.planner or Planner()
    cluster._hybrid_planner = wiring.hybrid_planner or HybridPlannerPipeline(
        base_planner=cluster._planner,
        capability_registry=cluster._capability_registry,
    )
    cluster._session_manager = wiring.session_manager or SessionManager(memory_limit=memory_limit)
    cluster._resource_budget = wiring.resource_budget or ResourceBudgetGate()
    cluster._task_queue = wiring.task_queue or TaskQueue()
    cluster._ecommerce_projects = wiring.ecommerce_projects or EcommerceProjectRegistry()
    cluster._refresh_resource_registry()
    cluster._repair_advisor = wiring.repair_advisor or RuleBasedRepairAdvisor()
    cluster._intent_resolver = wiring.intent_resolver or IntentResolver(cluster._llm_client)
    cluster._decision_cache = wiring.decision_cache or DecisionCache()
    cluster._voice_intent_mode = (
        wiring.voice_intent_mode or os.getenv("SPIRITKIN_VOICE_INTENT_MODE", "first")
    ).strip().lower()
    cluster._intent_phase = IntentPhase(
        IntentPhaseServices(
            decision_cache=cluster._decision_cache,
            intent_resolver=cluster._intent_resolver,
            available_tools=lambda: cluster.available_tools,
            execute=cluster._handle_execution,
            correct_app_name=cluster._correct_app_name_via_inventory,
            voice_intent_mode=cluster._voice_intent_mode,
        )
    )
    cluster._skill_store = wiring.skill_store
    cluster._policy_engine = wiring.policy_engine
    cluster._execution_guard = ExecutionGuard(policy_engine=wiring.policy_engine)
    cluster._personality_store = wiring.personality_store
    cluster._long_term_memory = wiring.long_term_memory
    cluster._relationship_store = wiring.relationship_store
    cluster._turn_context = TurnContextPreparer(
        router=cluster._cluster_router,
        current_time=current_time_context,
        relationship_store=cluster._relationship_store,
        long_term_memory=cluster._long_term_memory,
    )
    cluster._performance_tracker = wiring.performance_tracker or AgentPerformanceTracker()
    cluster._trajectory_analyzer = wiring.trajectory_analyzer or TrajectoryAnalyzer()
    cluster._failure_log_limit = max(1, int(wiring.failure_log_limit))
    cluster._failure_log = FailureLog(
        repair_advisor=cluster._repair_advisor,
        trajectory_analyzer=cluster._trajectory_analyzer,
        limit=cluster._failure_log_limit,
    )
    cluster._failure_trajectory = FailureTrajectoryPhase(cluster._failure_log)
    cluster._request_coordinator = RequestCoordinator()
    cluster._context_assets = ContextAssetStore(
        ContextAssetServices(
            evaluate_policy=cluster._evaluate_policy,
            requires_confirmation=cluster._requires_confirmation,
            worker_pool=cluster._worker_pool,
        )
    )
    try:
        cluster._context_assets.initialize_local_inventory(get_device_backend(device_name))
    except Exception:
        pass
    cluster._recent_inventory = cluster._context_assets.inventory
    cluster._workflow_memory = wiring.workflow_memory or InMemoryWorkflowMemory()
    cluster._agent_runtime_context = AgentRuntimeContextBuilder(
        AgentRuntimeContextServices(
            profiles_by_id=cluster._agent_profiles_by_id,
            managed_agents=cluster._managed_agents,
            adapters_by_id=cluster._agent_adapters_by_id,
            knowledge_resolver=cluster._agent_knowledge_resolver,
            capability_registry=cluster._capability_registry,
            skill_registry=cluster._skill_registry,
            worker_pool=cluster._worker_pool,
            route_brain_for_agent=cluster._route_brain_for_agent,
            recent_failure_count=lambda: len(cluster._failure_log.recent_failures),
            queued_task_count=lambda: len(cluster._task_queue.list_tasks(include_finished=False)),
            describe_plan=cluster._hybrid_planner.describe_plan,
        )
    )
    cluster._skill_phase = SkillPhase(
        SkillPhaseServices(
            skill_registry=cluster._skill_registry,
            tool_registry=cluster._tool_registry,
            workflow_memory=cluster._workflow_memory,
            app_port=cluster._app_port,
            record_failure=cluster._record_failure,
        )
    )
    cluster._tool_phase = ToolPhase(
        ToolPhaseServices(
            tool_registry=cluster._tool_registry,
            record_failure=cluster._record_failure,
            execute=cluster._handle_execution,
            respond_general=cluster._handle_general,
        )
    )
    cluster._pending_execution_store = PendingExecutionStore(wiring.pending_execution_path)
    cluster._active_input_metadata = {}
    cluster._execution_phase = ExecutionPhase(
        ExecutionPhaseServices(
            find_executor=cluster._find_executor,
            record_failure=cluster._record_failure,
            evaluate_policy=cluster._evaluate_policy,
            append_failure_trajectory=cluster._append_runtime_failure_trajectory,
            requires_confirmation=cluster._requires_confirmation,
            has_full_access=cluster._active_input_has_full_access,
            available_tools=lambda: cluster.available_tools,
            execution_guard=cluster._execution_guard,
            save_pending=cluster._save_pending_execution,
            build_confirmation_reply=cluster._build_confirmation_reply,
            worker_pool=cluster._worker_pool,
            active_input_metadata=lambda: cluster._active_input_metadata,
            workflow_memory=cluster._workflow_memory,
            llm_call=cluster._call_llm_for_agent,
            remember_inventory=cluster._remember_inventory_snapshot,
            append_execution_trajectory=cluster._append_runtime_execution_trajectory,
            invoke_tool=lambda name, arguments: cluster._tool_registry.invoke(ToolCall(name, arguments)),
        )
    )
    cluster._confirmation_phase = ConfirmationPhase(
        ConfirmationPhaseServices(
            execution_guard=cluster._execution_guard,
            load_pending=cluster._load_pending_execution,
            clear_pending=cluster._clear_pending_execution,
            execute=cluster._handle_execution,
            active_metadata=lambda: cluster._active_input_metadata,
        )
    )
    cluster._execution_validation = ExecutionValidationPhase(
        ExecutionValidationServices(
            tool_registry=cluster._tool_registry,
            execute=cluster._handle_execution,
        )
    )
    cluster._plan_dispatcher = PlanDispatcher(
        PlanDispatchServices(
            with_agent_runtime_context=cluster._with_agent_runtime_context,
            handle_builtin=cluster._handle_builtin,
            handle_development_plan=cluster._handle_development_plan,
            handle_tool=cluster._handle_tool,
            handle_execution=cluster._handle_execution_with_validation,
            handle_skill=cluster._handle_skill,
            handle_agent=cluster._run_planned_agent,
            handle_intent_fallback=cluster._handle_intent_fallback,
            handle_general=cluster._handle_general,
        )
    )
    cluster._queued_task_phase = QueuedTaskPhase(
        QueuedTaskServices(
            task_queue=cluster._task_queue,
            resource_budget=cluster._resource_budget,
            ecommerce_projects=cluster._ecommerce_projects,
            session_manager=cluster._session_manager,
            device_name=cluster.device_name,
            build_inventory_context=cluster._build_inventory_context,
            resolve_project=cluster._resolve_ecommerce_project,
            build_busy_reply=cluster._build_resource_busy_reply,
            plan_hybrid=cluster._plan_hybrid,
            dispatch_plan=cluster._dispatch_plan,
            apply_finalizer=cluster._apply_scheduler_task_finalizer,
            attach_plan_metadata=cluster._attach_plan_metadata,
            finalize_reply=cluster._finalize_reply,
        )
    )
    cluster._turn_orchestrator = ClusterTurnOrchestrator(
        ClusterTurnServices(
            turn_context=cluster._turn_context,
            set_active_metadata=cluster._set_active_input_metadata,
            register_request=cluster._register_latest_request,
            is_stale=cluster._is_stale_request,
            build_stale_reply=cluster._build_stale_request_reply,
            build_cancelled_reply=cluster._build_cancelled_request_reply,
            load_pending=cluster._load_pending_execution,
            session_manager=cluster._session_manager,
            confirmation_phase=cluster._confirmation_phase,
            execution_guard=cluster._execution_guard,
            finalize_reply=cluster._finalize_reply,
            handle_agent_status=cluster._handle_agent_mention_status,
            build_inventory_context=cluster._build_inventory_context,
            capability_inventory=lambda: cluster.capability_inventory_snapshot,
            resource_registry=lambda: cluster.resource_registry_snapshot,
            perception_enricher=cluster._enrich_perception_context,
            device_name=cluster.device_name,
            handle_plan_mode=cluster._handle_plan_mode,
            handle_goal_mode=cluster._handle_pursue_goal,
            tool_registry=cluster._tool_registry,
            hybrid_planner=cluster._hybrid_planner,
            plan_hybrid=cluster._plan_hybrid,
            intent_phase=cluster._intent_phase,
            resource_budget=cluster._resource_budget,
            task_queue=cluster._task_queue,
            resolve_project=cluster._resolve_ecommerce_project,
            ecommerce_projects=cluster._ecommerce_projects,
            build_busy_reply=cluster._build_resource_busy_reply,
            dispatch_plan=cluster._dispatch_plan,
            attach_plan_metadata=cluster._attach_plan_metadata,
        )
    )
    cluster._inspection = ClusterInspectionFacade(
        ClusterInspectionServices(
            session_manager=cluster._session_manager,
            tool_registry=cluster._tool_registry,
            skill_registry=cluster._skill_registry,
            skill_store=cluster._skill_store,
            failure_log=cluster._failure_log,
            inventory=cluster._recent_inventory,
            executors=cluster._executors,
            device_backend=cluster.device_backend,
            node_registry=cluster._node_registry,
            get_capability_registry=lambda: cluster._capability_registry,
            refresh_capability_registry=cluster._refresh_capability_registry,
            worker_pool=cluster._worker_pool,
            resource_registry=cluster._resource_registry,
            refresh_resource_registry=cluster._refresh_resource_registry,
            brain_router=cluster._brain_router,
            workflow_memory=cluster._workflow_memory,
            performance_tracker=cluster._performance_tracker,
            trajectory_analyzer=cluster._trajectory_analyzer,
            app_port=cluster._app_port,
            agent_profiles_by_id=cluster._agent_profiles_by_id,
            agent_adapters_by_id=cluster._agent_adapters_by_id,
            runtime_policy=cluster._build_agent_runtime_policy,
            resource_budget=cluster._resource_budget,
            task_queue=cluster._task_queue,
            ecommerce_projects=cluster._ecommerce_projects,
            process_next_queued_task=cluster.process_next_queued_task,
        )
    )
