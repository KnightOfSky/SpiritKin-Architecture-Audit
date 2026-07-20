from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.orchestrator.workflow_graph import (
    NODE_BLOCKED,
    NODE_FAILED,
    NODE_PENDING,
    NODE_RUNNING,
    NODE_SKIPPED,
    NODE_SUCCEEDED,
    NODE_WAITING,
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_PENDING,
    RUN_RUNNING,
    RUN_SUCCEEDED,
    RUN_WAITING,
    WorkflowRunner,
    build_agent_interaction_envelope,
    build_composed_workflow_definition,
    build_ecommerce_auto_listing_definition,
    get_builtin_workflow_definition,
    is_android_workflow_node_type,
    is_open_workflow_node_type,
    start_workflow_run,
    utc_now,
    workflow_definition_with_port_references,
    workflow_node_catalog,
)
from backend.orchestrator.workflow_store import JsonWorkflowStore, workflow_definition_from_dict
from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec

if TYPE_CHECKING:
    from backend.orchestrator.worker_pool import WorkerPool
    from backend.skills import SkillRunner


def _bool_arg(arguments: dict[str, Any], name: str, default: bool = False) -> bool:
    value = arguments.get(name, default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _dict_arg(arguments: dict[str, Any], name: str) -> dict[str, Any]:
    value = arguments.get(name)
    return value if isinstance(value, dict) else {}


def _list_arg(arguments: dict[str, Any], name: str) -> list[Any]:
    value = arguments.get(name)
    return list(value) if isinstance(value, list) else []


def _string_arg(arguments: dict[str, Any], name: str, default: str = "") -> str:
    return str(arguments.get(name) or default).strip()


def _node_executor(node) -> str:
    arguments = getattr(node, "arguments", {}) if isinstance(getattr(node, "arguments", {}), dict) else {}
    metadata = getattr(node, "metadata", {}) if isinstance(getattr(node, "metadata", {}), dict) else {}
    return str(arguments.get("executor") or metadata.get("executor") or "").strip().lower()


def _is_android_step_node(node) -> bool:
    node_type = getattr(node, "node_type", "")
    return is_android_workflow_node_type(node_type) or (is_open_workflow_node_type(node_type) and _node_executor(node) == "android_step")


def _is_agent_task_like(node) -> bool:
    return getattr(node, "node_type", "") == "agent_task" or (is_open_workflow_node_type(getattr(node, "node_type", "")) and _node_executor(node) == "agent_task")


def _is_signalable_node(node) -> bool:
    node_type = getattr(node, "node_type", "")
    return node_type in {"waiter", "external_callback", "subgraph"} or _is_android_step_node(node) or is_open_workflow_node_type(node_type)


def _active_auto_advance_statuses() -> set[str]:
    statuses = {RUN_PENDING, RUN_RUNNING, RUN_WAITING}
    if _workflow_agent_dispatch_enabled():
        statuses.add(RUN_BLOCKED)
    return statuses


def _workflow_agent_dispatch_enabled() -> bool:
    import os

    return str(os.getenv("SPIRITKIN_WORKFLOW_AGENT_DISPATCH", "0")).strip().lower() in {"1", "true", "yes", "y", "on"}


def _dependency_satisfied(run, node_id: str) -> bool:
    state = run.nodes.get(node_id)
    return bool(state and state.status in {NODE_SUCCEEDED, NODE_SKIPPED})


def _parse_workflow_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _workflow_age_seconds(value: str, *, now: datetime | None = None) -> float | None:
    parsed = _parse_workflow_timestamp(value)
    if parsed is None:
        return None
    current = now or datetime.now(UTC)
    return max(0.0, (current - parsed).total_seconds())


def _timeout_running_agent_tasks(definition, run, *, timeout_seconds: float) -> tuple[Any, list[dict[str, Any]]]:
    if timeout_seconds <= 0:
        return run, []
    nodes = dict(run.nodes)
    events = list(run.events)
    timed_out: list[dict[str, Any]] = []
    for node in definition.nodes:
        if not _is_agent_task_like(node):
            continue
        current = nodes.get(node.node_id)
        if current is None or current.status != NODE_RUNNING:
            continue
        age = _workflow_age_seconds(current.started_at)
        if age is None or age < timeout_seconds:
            continue
        timed_out_node = replace(
            current,
            status=NODE_FAILED,
            finished_at=utc_now(),
            error="agent_task_timeout",
            outputs={
                **dict(current.outputs or {}),
                "timeout": {
                    "timeout_seconds": timeout_seconds,
                    "age_seconds": round(age, 3),
                    "agent_id": current.assigned_agent or node.assigned_agent,
                },
            },
        )
        nodes[node.node_id] = timed_out_node
        record = {
            "node_id": node.node_id,
            "agent_id": timed_out_node.assigned_agent or node.assigned_agent,
            "age_seconds": round(age, 3),
            "timeout_seconds": timeout_seconds,
        }
        timed_out.append(record)
        events.append({"at": utc_now(), "type": "agent_task_timed_out", "payload": record})
    if not timed_out:
        return run, []
    return replace(run, status=RUN_FAILED, nodes=nodes, events=events, updated_at=utc_now()), timed_out


def _is_auto_advance_node(node) -> bool:
    return not _is_agent_task_like(node)


def _auto_advance_one_node(
    store: JsonWorkflowStore,
    arguments: dict[str, Any],
    definition,
    run,
    runner: WorkflowRunner,
    node,
) -> tuple[Any, dict[str, Any]]:
    before_status = run.nodes.get(node.node_id).status if run.nodes.get(node.node_id) else ""
    if _is_android_step_node(node):
        android_result = _run_android_step_node(store, arguments, definition, run, node, worker_pool=getattr(runner, "_worker_pool", None))
        if android_result is not None:
            refreshed = store.load_run(run.run_id) or run
            return refreshed, {"node_id": node.node_id, "node_type": node.node_type, "before": before_status, "after": refreshed.nodes.get(node.node_id).status if refreshed.nodes.get(node.node_id) else "", "message": android_result.message}
    if node.node_type == "subgraph":
        subgraph_result = _run_subgraph_node(store, definition, run, node)
        if subgraph_result is not None:
            refreshed = store.load_run(run.run_id) or run
            return refreshed, {"node_id": node.node_id, "node_type": node.node_type, "before": before_status, "after": refreshed.nodes.get(node.node_id).status if refreshed.nodes.get(node.node_id) else "", "message": subgraph_result.message}
    if node.node_type == "foreach":
        foreach_result = _run_foreach_node(store, definition, run, node, runner)
        if foreach_result is not None:
            refreshed = store.load_run(run.run_id) or run
            return refreshed, {"node_id": node.node_id, "node_type": node.node_type, "before": before_status, "after": refreshed.nodes.get(node.node_id).status if refreshed.nodes.get(node.node_id) else "", "message": foreach_result.message}
    updated = runner.run_node(definition, run, node.node_id, dry_run=False)
    store.save_run(updated)
    return updated, {"node_id": node.node_id, "node_type": node.node_type, "before": before_status, "after": updated.nodes.get(node.node_id).status if updated.nodes.get(node.node_id) else "", "message": f"advanced:{node.node_id}"}


def _auto_advance_runs(store: JsonWorkflowStore, arguments: dict[str, Any], runner: WorkflowRunner) -> dict[str, Any]:
    workflow_name = str(arguments.get("workflow_name") or "").strip()
    run_id_filter = str(arguments.get("run_id") or "").strip()
    max_runs = max(1, int(arguments.get("max_runs") or 20))
    max_steps_per_run = max(1, int(arguments.get("max_steps_per_run") or arguments.get("max_steps") or 10))
    timeout_raw = arguments.get("agent_task_timeout_seconds")
    if timeout_raw is None:
        import os

        timeout_raw = os.getenv("SPIRITKIN_WORKFLOW_AGENT_TASK_TIMEOUT_SECONDS", "600")
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = 600.0

    runs = [store.load_run(run_id_filter)] if run_id_filter else store.list_runs(workflow_name=workflow_name)
    runs = [run for run in runs if run is not None and run.status in _active_auto_advance_statuses()][:max_runs]
    reports: list[dict[str, Any]] = []
    total_steps = 0
    total_timed_out = 0
    for run in runs:
        definition = store.load_definition(run.workflow_name) or get_builtin_workflow_definition(run.workflow_name)
        if definition is None:
            reports.append({"run_id": run.run_id, "workflow_name": run.workflow_name, "advanced_steps": 0, "timed_out": [], "reason": "workflow_definition_not_found"})
            continue
        store.save_definition(definition, actor="workflow_auto_advance", reason="materialize definition for auto advance", record_history=False)
        run, timed_out = _timeout_running_agent_tasks(definition, run, timeout_seconds=timeout_seconds)
        if timed_out:
            store.save_run(run)
            total_timed_out += len(timed_out)
            reports.append({"run_id": run.run_id, "workflow_name": run.workflow_name, "advanced_steps": 0, "timed_out": timed_out, "status": run.status})
            continue

        steps: list[dict[str, Any]] = []
        for _ in range(max_steps_per_run):
            before_skip_events = len(run.events)
            run = runner.apply_branch_skips(definition, run)
            if len(run.events) != before_skip_events:
                store.save_run(run)
            run, agent_reconciled = _reconcile_dispatched_agent_tasks(store, definition, run, runner)
            if agent_reconciled:
                store.save_run(run)
            run, subgraph_reconciled = _reconcile_subgraph_nodes(store, definition, run, runner)
            if subgraph_reconciled:
                store.save_run(run)
            run, foreach_reconciled = _reconcile_foreach_nodes(store, definition, run, runner)
            if foreach_reconciled:
                store.save_run(run)
            run, reconciled = _reconcile_android_step_nodes(arguments, definition, run, runner)
            if reconciled:
                store.save_run(run)
            if run.status not in _active_auto_advance_statuses():
                break
            runnable = [node for node in runner.runnable_nodes(definition, run) if _is_auto_advance_node(node)]
            if not runnable:
                break
            run, step = _auto_advance_one_node(store, arguments, definition, run, runner, runnable[0])
            steps.append(step)
            total_steps += 1
            if run.status not in _active_auto_advance_statuses():
                break
        dispatched_agent_tasks = []
        if _workflow_agent_dispatch_enabled() and run.status in _active_auto_advance_statuses():
            run, dispatched_agent_tasks = _dispatch_runnable_agent_tasks(store, definition, run, runner)
            if dispatched_agent_tasks:
                store.save_run(run)
        waiting_agent_tasks = [
            node.node_id
            for node in runner.runnable_nodes(definition, run)
            if _is_agent_task_like(node)
        ]
        reports.append(
            {
                "run_id": run.run_id,
                "workflow_name": run.workflow_name,
                "advanced_steps": len(steps),
                "steps": steps,
                "waiting_agent_tasks": waiting_agent_tasks,
                "dispatched_agent_tasks": dispatched_agent_tasks,
                "timed_out": [],
                "status": run.status,
            }
        )
    return {
        "run_count": len(reports),
        "advanced_steps": total_steps,
        "timed_out_count": total_timed_out,
        "reports": reports,
        "agent_task_timeout_seconds": timeout_seconds,
    }


def _workflow_store(arguments: dict[str, Any]) -> JsonWorkflowStore:
    return JsonWorkflowStore(arguments.get("workflow_state_dir") or None, project_root=arguments.get("project_root") or None)


def _android_companion_store(arguments: dict[str, Any]):
    from backend.mobile.android_companion_store import AndroidCompanionStore

    return AndroidCompanionStore(arguments.get("android_companion_state") or arguments.get("android_state_path") or None)


def _default_skill_runner(project_root: str | None, tool_registry) -> SkillRunner:
    from backend.skills import JsonlSkillSpecStore, SkillRegistry, SkillRunner

    registry = SkillRegistry()
    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    registry.load_from_store(JsonlSkillSpecStore(root / "state" / "skills.jsonl"))
    return SkillRunner(registry, tool_registry)


def _persist_node_running(store: JsonWorkflowStore, run, node, *, outputs: dict[str, Any] | None = None):
    current = run.nodes.get(node.node_id)
    if current is None or current.status != NODE_PENDING:
        return run
    started = replace(
        current,
        status=NODE_RUNNING,
        attempts=current.attempts + 1,
        started_at=current.started_at or utc_now(),
        finished_at="",
        outputs={**dict(current.outputs or {}), **dict(outputs or {})},
        error="",
    )
    nodes = dict(run.nodes)
    nodes[node.node_id] = started
    events = [*run.events, {"at": utc_now(), "type": "node_started", "payload": {"node_id": node.node_id, "dry_run": False}}]
    updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
    store.save_run(updated)
    return updated


def _run_subgraph_node(store: JsonWorkflowStore, definition, run, node) -> ToolResult | None:
    current = run.nodes.get(node.node_id)
    if current is None or current.status != NODE_PENDING:
        return None
    if not all(_dependency_satisfied(run, dep) for dep in node.depends_on):
        return None
    arguments = WorkflowRunner._resolve_arguments(node.arguments, run.inputs, run.nodes)
    workflow_name = str(arguments.get("workflow_name") or node.metadata.get("workflow_name") or "").strip()
    if not workflow_name:
        return ToolResult(False, f"子工作流节点缺少 workflow_name: {node.node_id}", error_code="missing_subgraph_workflow")
    child_definition = store.load_definition(workflow_name) or get_builtin_workflow_definition(workflow_name)
    if child_definition is None:
        return ToolResult(False, f"未找到子工作流定义: {workflow_name}", error_code="workflow_definition_not_found")
    store.save_definition(child_definition, actor="subgraph", reason=f"materialize child workflow for {run.run_id}:{node.node_id}", record_history=False)
    child_inputs = arguments.get("inputs") if isinstance(arguments.get("inputs"), dict) else {}
    parent_workspace = str(run.inputs.get("workspace_id") or "").strip()
    if parent_workspace:
        child_inputs = {**child_inputs, "workspace_id": parent_workspace}
    run = _persist_node_running(store, run, node, outputs={"workflow_name": workflow_name, "status": "subgraph_starting"})
    current = run.nodes[node.node_id]
    child_run = start_workflow_run(child_definition, child_inputs)
    store.save_run(child_run)
    updated_node = replace(
        current,
        status=NODE_WAITING,
        attempts=current.attempts,
        started_at=current.started_at or utc_now(),
        finished_at="",
        outputs={"workflow_name": workflow_name, "child_run_id": child_run.run_id, "status": "subgraph_running"},
        error="",
    )
    nodes = dict(run.nodes)
    nodes[node.node_id] = updated_node
    events = list(run.events)
    events.append({"at": utc_now(), "type": "subgraph_requested", "payload": {"node_id": node.node_id, "workflow_name": workflow_name, "child_run_id": child_run.run_id}})
    updated = replace(run, status=RUN_WAITING, nodes=nodes, events=events, updated_at=utc_now())
    store.save_run(updated)
    return ToolResult(True, f"子工作流已启动: {workflow_name} / {child_run.run_id}", data={"run": updated.snapshot(), "definition": definition.snapshot(), "child_run": child_run.snapshot(), "child_definition": child_definition.snapshot()})


def _foreach_workflow_name(node, arguments: dict[str, Any]) -> str:
    return str(
        arguments.get("workflow_name")
        or arguments.get("subgraph")
        or arguments.get("child_workflow")
        or getattr(node, "metadata", {}).get("workflow_name")
        or ""
    ).strip()


def _foreach_items(arguments: dict[str, Any]) -> list[Any]:
    items = arguments.get("items")
    if items is None:
        items = arguments.get("array")
    if items is None:
        items = arguments.get("values")
    return list(items) if isinstance(items, list) else []


def _foreach_child_inputs(arguments: dict[str, Any], item: Any, index: int) -> dict[str, Any]:
    base = dict(arguments.get("inputs") or {}) if isinstance(arguments.get("inputs"), dict) else {}
    item_key = str(arguments.get("item_key") or "item").strip() or "item"
    index_key = str(arguments.get("index_key") or "index").strip() or "index"
    base[item_key] = item
    base[index_key] = index
    base["foreach"] = {
        **(dict(base.get("foreach") or {}) if isinstance(base.get("foreach"), dict) else {}),
        "item": item,
        "index": index,
    }
    return base


def _child_outputs_for_run(child_run) -> dict[str, Any]:
    return {
        node_id: dict(node_state.outputs or {})
        for node_id, node_state in child_run.nodes.items()
        if node_state.status in {NODE_SUCCEEDED, NODE_SKIPPED}
    }


def _run_foreach_node(store: JsonWorkflowStore, definition, run, node, runner: WorkflowRunner) -> ToolResult | None:
    current = run.nodes.get(node.node_id)
    if current is None or current.status != NODE_PENDING:
        return None
    if not all(_dependency_satisfied(run, dep) for dep in node.depends_on):
        return None
    arguments = WorkflowRunner._resolve_arguments(node.arguments, run.inputs, run.nodes)
    workflow_name = _foreach_workflow_name(node, arguments)
    if not workflow_name:
        return None
    child_definition = store.load_definition(workflow_name) or get_builtin_workflow_definition(workflow_name)
    if child_definition is None:
        return ToolResult(False, f"未找到 foreach 子工作流定义: {workflow_name}", error_code="workflow_definition_not_found")
    store.save_definition(child_definition, actor="foreach", reason=f"materialize foreach child workflow for {run.run_id}:{node.node_id}", record_history=False)
    items = _foreach_items(arguments)
    max_iterations = WorkflowRunner._foreach_max_iterations(node, arguments)
    selected_items = items[:max_iterations]
    if not selected_items:
        updated_node = replace(
            current,
            status=NODE_SUCCEEDED,
            attempts=current.attempts + 1,
            started_at=current.started_at or utc_now(),
            finished_at=utc_now(),
            outputs={
                "workflow_name": workflow_name,
                "items": [],
                "results": [],
                "count": 0,
                "truncated": False,
                "max_iterations": max_iterations,
            },
            error="",
        )
        nodes = dict(run.nodes)
        nodes[node.node_id] = updated_node
        events = [*run.events, {"at": utc_now(), "type": "node_started", "payload": {"node_id": node.node_id, "dry_run": False}}, {"at": utc_now(), "type": "foreach_completed", "payload": {"node_id": node.node_id, "count": 0}}]
        updated = runner._refresh_run_status(definition, replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now()))
        store.save_run(updated)
        return ToolResult(True, f"foreach 没有可迭代项: {node.node_id}", data={"run": updated.snapshot(), "definition": definition.snapshot()})
    child_inputs = _foreach_child_inputs(arguments, selected_items[0], 0)
    parent_workspace = str(run.inputs.get("workspace_id") or "").strip()
    if parent_workspace:
        child_inputs["workspace_id"] = parent_workspace
    run = _persist_node_running(store, run, node, outputs={"workflow_name": workflow_name, "status": "foreach_starting"})
    current = run.nodes[node.node_id]
    child_run = start_workflow_run(child_definition, child_inputs)
    store.save_run(child_run)
    updated_node = replace(
        current,
        status=NODE_WAITING,
        attempts=current.attempts,
        started_at=current.started_at or utc_now(),
        finished_at="",
        outputs={
            "workflow_name": workflow_name,
            "items": selected_items,
            "total": len(selected_items),
            "current_index": 0,
            "current_child_run_id": child_run.run_id,
            "results": [],
            "truncated": len(items) > len(selected_items),
            "max_iterations": max_iterations,
            "status": "foreach_running",
        },
        error="",
    )
    nodes = dict(run.nodes)
    nodes[node.node_id] = updated_node
    events = list(run.events)
    events.append({"at": utc_now(), "type": "foreach_iteration_started", "payload": {"node_id": node.node_id, "workflow_name": workflow_name, "child_run_id": child_run.run_id, "index": 0}})
    updated = replace(run, status=RUN_WAITING, nodes=nodes, events=events, updated_at=utc_now())
    store.save_run(updated)
    return ToolResult(True, f"foreach 子工作流已启动: {workflow_name} / {child_run.run_id}", data={"run": updated.snapshot(), "definition": definition.snapshot(), "child_run": child_run.snapshot(), "child_definition": child_definition.snapshot()})


def _reconcile_foreach_nodes(store: JsonWorkflowStore, definition, run, runner: WorkflowRunner) -> tuple[Any, bool]:
    nodes = dict(run.nodes)
    events = list(run.events)
    changed = False
    for node in definition.nodes:
        if node.node_type != "foreach":
            continue
        current = nodes.get(node.node_id)
        if current is None or current.status != NODE_WAITING:
            continue
        outputs = dict(current.outputs or {})
        workflow_name = str(outputs.get("workflow_name") or "").strip()
        child_run_id = str(outputs.get("current_child_run_id") or "").strip()
        if not workflow_name or not child_run_id:
            continue
        child_run = store.load_run(child_run_id)
        if child_run is None or child_run.status not in {RUN_SUCCEEDED, RUN_FAILED, RUN_BLOCKED}:
            continue
        items = list(outputs.get("items") or []) if isinstance(outputs.get("items"), list) else []
        results = list(outputs.get("results") or []) if isinstance(outputs.get("results"), list) else []
        current_index = int(outputs.get("current_index") or 0)
        results.append(
            {
                "index": current_index,
                "item": items[current_index] if 0 <= current_index < len(items) else None,
                "child_run_id": child_run.run_id,
                "child_status": child_run.status,
                "child_outputs": _child_outputs_for_run(child_run),
            }
        )
        if child_run.status != RUN_SUCCEEDED:
            nodes[node.node_id] = replace(
                current,
                status=NODE_FAILED,
                finished_at=utc_now(),
                outputs={
                    **outputs,
                    "results": results,
                    "status": "foreach_failed",
                    "failed_index": current_index,
                    "failed_child_run_id": child_run.run_id,
                    "error_detail": {"message": f"foreach child workflow {child_run.status}", "child_run_id": child_run.run_id, "index": current_index, "recorded_at": utc_now()},
                },
                error=f"foreach_child_{child_run.status}",
            )
            events.append({"at": utc_now(), "type": "foreach_failed", "payload": {"node_id": node.node_id, "child_run_id": child_run.run_id, "child_status": child_run.status, "index": current_index}})
            changed = True
            continue
        next_index = current_index + 1
        if next_index >= len(items):
            nodes[node.node_id] = replace(
                current,
                status=NODE_SUCCEEDED,
                finished_at=utc_now(),
                outputs={
                    **outputs,
                    "results": results,
                    "count": len(results),
                    "status": "foreach_completed",
                    "current_child_run_id": "",
                },
                error="",
            )
            events.append({"at": utc_now(), "type": "foreach_completed", "payload": {"node_id": node.node_id, "count": len(results)}})
            changed = True
            continue
        child_definition = store.load_definition(workflow_name) or get_builtin_workflow_definition(workflow_name)
        if child_definition is None:
            nodes[node.node_id] = replace(
                current,
                status=NODE_FAILED,
                finished_at=utc_now(),
                outputs={**outputs, "results": results, "status": "foreach_failed", "error_detail": {"message": f"missing child workflow {workflow_name}", "recorded_at": utc_now()}},
                error="workflow_definition_not_found",
            )
            events.append({"at": utc_now(), "type": "foreach_failed", "payload": {"node_id": node.node_id, "workflow_name": workflow_name, "index": next_index}})
            changed = True
            continue
        arguments = WorkflowRunner._resolve_arguments(node.arguments, run.inputs, run.nodes)
        child_inputs = _foreach_child_inputs(arguments, items[next_index], next_index)
        parent_workspace = str(run.inputs.get("workspace_id") or "").strip()
        if parent_workspace:
            child_inputs["workspace_id"] = parent_workspace
        nodes[node.node_id] = replace(
            current,
            status=NODE_RUNNING,
            outputs={
                **outputs,
                "results": results,
                "current_index": next_index,
                "current_child_run_id": "",
                "status": "foreach_iteration_starting",
            },
            error="",
        )
        starting = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
        store.save_run(starting)
        child_run = start_workflow_run(child_definition, child_inputs)
        store.save_run(child_run)
        nodes[node.node_id] = replace(
            current,
            status=NODE_WAITING,
            outputs={
                **outputs,
                "results": results,
                "current_index": next_index,
                "current_child_run_id": child_run.run_id,
                "status": "foreach_running",
            },
            error="",
        )
        events.append({"at": utc_now(), "type": "foreach_iteration_started", "payload": {"node_id": node.node_id, "workflow_name": workflow_name, "child_run_id": child_run.run_id, "index": next_index}})
        changed = True
    if not changed:
        return run, False
    updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
    return runner._refresh_run_status(definition, updated), True


def _workflow_agent_thread_id(run, node_id: str) -> str:
    return f"workflow:{run.run_id}:{node_id}"


def _agent_dispatch_message(definition, run, node, state) -> str:
    return (
        f"Workflow agent task is ready.\n"
        f"workflow={run.workflow_name}\n"
        f"run_id={run.run_id}\n"
        f"node_id={node.node_id}\n"
        f"node_label={node.label or node.node_id}\n"
        f"arguments={getattr(node, 'arguments', {})}\n"
        "Reply in this thread with the result; the workflow daemon will record the reply as outputs.reply."
    )


def _post_agent_dispatch_message(store: JsonWorkflowStore, definition, run, node, state, agent_id: str) -> dict[str, Any]:
    from backend.app.collaboration import handle_collaboration_action

    thread_id = _workflow_agent_thread_id(run, node.node_id)
    result = handle_collaboration_action(
        {
            "action": "post_message",
            "from_agent": "workflow_auto_advance",
            "to_agents": [agent_id],
            "thread_id": thread_id,
            "task_id": thread_id,
            "role": "task",
            "content": _agent_dispatch_message(definition, run, node, state),
        },
        root=store.project_root,
    )
    message = result.get("message") if isinstance(result.get("message"), dict) else {}
    return {
        "thread_id": thread_id,
        "message_id": str(message.get("message_id") or ""),
        "agent_id": agent_id,
        "posted_at": utc_now(),
    }


def _dispatch_runnable_agent_tasks(store: JsonWorkflowStore, definition, run, runner: WorkflowRunner) -> tuple[Any, list[dict[str, Any]]]:
    if not _workflow_agent_dispatch_enabled():
        return run, []
    nodes = dict(run.nodes)
    events = list(run.events)
    dispatched: list[dict[str, Any]] = []
    runnable_ids = {node.node_id for node in runner.runnable_nodes(definition, run) if _is_agent_task_like(node)}
    for node in definition.nodes:
        if not _is_agent_task_like(node):
            continue
        current = nodes.get(node.node_id)
        if current is None:
            continue
        if current.status not in {NODE_PENDING, NODE_BLOCKED}:
            continue
        if current.status == NODE_PENDING and node.node_id not in runnable_ids:
            continue
        if current.status == NODE_BLOCKED and not all(_dependency_satisfied(run, dep) for dep in node.depends_on):
            continue
        agent_id = (current.assigned_agent or node.assigned_agent or "").strip()
        if not agent_id:
            continue
        existing_dispatch = current.outputs.get("agent_dispatch") if isinstance(current.outputs, dict) else {}
        if isinstance(existing_dispatch, dict) and existing_dispatch.get("message_id"):
            continue
        try:
            dispatch = _post_agent_dispatch_message(store, definition, run, node, current, agent_id)
        except Exception as exc:
            dispatch = {"thread_id": _workflow_agent_thread_id(run, node.node_id), "agent_id": agent_id, "error": f"{type(exc).__name__}: {exc}", "posted_at": utc_now()}
        updated = replace(
            current,
            status=NODE_RUNNING if dispatch.get("message_id") else NODE_BLOCKED,
            attempts=current.attempts + (1 if current.status != NODE_RUNNING and dispatch.get("message_id") else 0),
            started_at=current.started_at or utc_now(),
            outputs={**dict(current.outputs or {}), "agent_dispatch": dispatch},
            error="" if dispatch.get("message_id") else str(dispatch.get("error") or "agent_task_dispatch_failed"),
            assigned_agent=agent_id,
        )
        nodes[node.node_id] = updated
        event_type = "agent_task_dispatched" if dispatch.get("message_id") else "agent_task_dispatch_failed"
        events.append({"at": utc_now(), "type": event_type, "payload": {"node_id": node.node_id, **dispatch}})
        dispatched.append({"node_id": node.node_id, **dispatch})
    if not dispatched:
        return run, []
    updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
    return runner._refresh_run_status(definition, updated), dispatched


def _reconcile_dispatched_agent_tasks(store: JsonWorkflowStore, definition, run, runner: WorkflowRunner) -> tuple[Any, bool]:
    if not _workflow_agent_dispatch_enabled():
        return run, False
    from backend.app.collaboration import handle_collaboration_action

    nodes = dict(run.nodes)
    events = list(run.events)
    changed = False
    for node in definition.nodes:
        if not _is_agent_task_like(node):
            continue
        current = nodes.get(node.node_id)
        if current is None or current.status != NODE_RUNNING:
            continue
        dispatch = current.outputs.get("agent_dispatch") if isinstance(current.outputs, dict) else {}
        if not isinstance(dispatch, dict) or not dispatch.get("thread_id"):
            continue
        agent_id = str(current.assigned_agent or node.assigned_agent or "").strip()
        if not agent_id:
            continue
        try:
            result = handle_collaboration_action({"action": "list_messages", "thread_id": dispatch["thread_id"]}, root=store.project_root)
        except Exception:
            continue
        messages = result.get("messages") if isinstance(result.get("messages"), list) else []
        reply = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, dict)
                and str(message.get("from_agent") or "") == agent_id
                and str(message.get("content") or "").strip()
            ),
            None,
        )
        if not isinstance(reply, dict):
            continue
        outputs = {
            **dict(current.outputs or {}),
            "reply": str(reply.get("content") or ""),
            "reply_message_id": str(reply.get("message_id") or ""),
            "completed_by": agent_id,
            "completed_at": utc_now(),
        }
        nodes[node.node_id] = replace(current, status=NODE_SUCCEEDED, finished_at=utc_now(), outputs=outputs, error="")
        events.append(
            {
                "at": utc_now(),
                "type": "agent_task_completed",
                "payload": {"node_id": node.node_id, "agent_id": agent_id, "message_id": str(reply.get("message_id") or ""), "source": "collaboration_reply"},
            }
        )
        changed = True
    if not changed:
        return run, False
    updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
    return runner._refresh_run_status(definition, updated), True


def _reconcile_subgraph_nodes(store: JsonWorkflowStore, definition, run, runner: WorkflowRunner) -> tuple[Any, bool]:
    nodes = dict(run.nodes)
    events = list(run.events)
    changed = False
    for node in definition.nodes:
        if node.node_type != "subgraph":
            continue
        current = nodes.get(node.node_id)
        if current is None or current.status != NODE_WAITING:
            continue
        child_run_id = str((current.outputs if isinstance(current.outputs, dict) else {}).get("child_run_id") or "").strip()
        if not child_run_id:
            continue
        child_run = store.load_run(child_run_id)
        if child_run is None or child_run.status not in {RUN_SUCCEEDED, RUN_FAILED, RUN_BLOCKED}:
            continue
        success = child_run.status == RUN_SUCCEEDED
        outputs = {
            **dict(current.outputs or {}),
            "child_run_id": child_run.run_id,
            "child_workflow_name": child_run.workflow_name,
            "child_status": child_run.status,
            "child_outputs": {
                node_id: dict(node_state.outputs or {})
                for node_id, node_state in child_run.nodes.items()
                if node_state.status in {NODE_SUCCEEDED, NODE_SKIPPED}
            },
            "signal_payload": {
                "child_run_id": child_run.run_id,
                "child_status": child_run.status,
                "child_workflow_name": child_run.workflow_name,
            },
            "signaled_by": "workflow_auto_advance",
            "signaled_at": utc_now(),
        }
        if success:
            nodes[node.node_id] = replace(current, status=NODE_SUCCEEDED, finished_at=utc_now(), outputs=outputs, error="")
            events.append({"at": utc_now(), "type": "subgraph_completed", "payload": {"node_id": node.node_id, "child_run_id": child_run.run_id, "child_status": child_run.status}})
        else:
            nodes[node.node_id] = replace(current, status=NODE_FAILED, finished_at=utc_now(), outputs={**outputs, "error_detail": {"message": f"child workflow {child_run.status}", "child_run_id": child_run.run_id, "recorded_at": utc_now()}}, error=f"subgraph_{child_run.status}")
            events.append({"at": utc_now(), "type": "subgraph_failed", "payload": {"node_id": node.node_id, "child_run_id": child_run.run_id, "child_status": child_run.status}})
        changed = True
    if not changed:
        return run, False
    updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
    return runner._refresh_run_status(definition, updated), True


def _android_step_arguments(node, run) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    raw_arguments = getattr(node, "arguments", {}) if isinstance(getattr(node, "arguments", {}), dict) else {}
    metadata = getattr(node, "metadata", {}) if isinstance(getattr(node, "metadata", {}), dict) else {}
    arguments = WorkflowRunner._resolve_arguments(raw_arguments, run.inputs, run.nodes)
    operation = str(arguments.get("operation") or arguments.get("android_operation") or metadata.get("operation") or "").strip()
    device_id = str(arguments.get("device_id") or metadata.get("device_id") or "android_device").strip() or "android_device"
    params = arguments.get("params") if isinstance(arguments.get("params"), dict) else {}
    if not params:
        reserved = {"executor", "operation", "android_operation", "device_id", "params", "wait_for_result"}
        params = {key: value for key, value in arguments.items() if key not in reserved}
    params = dict(params)
    params.setdefault("actor", str(run.inputs.get("actor") or "workflow_graph"))
    return device_id, operation, params, arguments


def _android_worker_requirement(node, operation: str, resolved_arguments: dict[str, Any]) -> dict[str, Any]:
    raw_needs = resolved_arguments.get("needs")
    if raw_needs is None:
        raw_needs = getattr(node, "metadata", {}).get("needs") if isinstance(getattr(node, "metadata", {}), dict) else None
    needs: list[str] = []
    if isinstance(raw_needs, str):
        needs.append(raw_needs)
    elif isinstance(raw_needs, (list, tuple)):
        needs.extend(str(item) for item in raw_needs if str(item).strip())
    for value in ("android", operation):
        if value and value not in needs:
            needs.append(value)
    if operation.startswith("pdd.") and "pdd" not in needs:
        needs.append("pdd")
    if operation.startswith("android.screenshot") and "android.screenshot" not in needs:
        needs.append("android.screenshot")
    if operation.startswith("android.ui") and "android.ui" not in needs:
        needs.append("android.ui")
    return {
        "needs": list(dict.fromkeys(needs)),
        "worker_type": "device_worker",
        "worker_subtype": "android_device_worker",
        "target": "android_device",
        "operation": operation,
    }


def _schedule_worker(worker_pool, worker_requirement: dict[str, Any]) -> dict[str, Any] | None:
    if not worker_requirement or not worker_requirement.get("needs"):
        return None
    if worker_pool is None or not hasattr(worker_pool, "schedule"):
        return {
            "status": "not_configured",
            "enforced": False,
            "reason": "worker_pool_not_configured",
            "requirement": dict(worker_requirement),
            "selected": None,
            "candidates": [],
            "rejected": [],
        }
    try:
        snapshot = worker_pool.schedule(worker_requirement).snapshot()
        snapshot["enforced"] = True
        return snapshot
    except Exception as exc:
        return {
            "status": "missing",
            "enforced": True,
            "reason": f"worker_schedule_error:{exc}",
            "requirement": dict(worker_requirement),
            "selected": None,
            "candidates": [],
            "rejected": [],
        }


def _is_default_android_device_id(device_id: str) -> bool:
    return str(device_id or "").strip() in {"", "android_device", "default"}


def _select_android_device_id(device_id: str, worker_schedule: dict[str, Any] | None, companion_snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    requested = str(device_id or "").strip() or "android_device"
    devices = [dict(item) for item in companion_snapshot.get("devices") or [] if isinstance(item, dict)]
    online_devices = [item for item in devices if bool(item.get("online"))]
    if not _is_default_android_device_id(requested):
        return requested, {
            "requested_device_id": requested,
            "selected_device_id": requested,
            "source": "explicit",
            "online_device_count": len(online_devices),
            "candidate_count": len(devices),
        }

    selected = ""
    selected_worker = (worker_schedule or {}).get("selected") if isinstance((worker_schedule or {}).get("selected"), dict) else {}
    metadata = selected_worker.get("metadata") if isinstance(selected_worker.get("metadata"), dict) else {}
    default_from_worker = str(metadata.get("default_device_id") or "").strip()
    if default_from_worker:
        selected = default_from_worker
    if not selected and online_devices:
        selected = str(online_devices[0].get("device_id") or "").strip()
    if not selected and devices:
        selected = str(devices[0].get("device_id") or "").strip()
    if not selected:
        selected = requested
    return selected, {
        "requested_device_id": requested,
        "selected_device_id": selected,
        "source": "worker_default" if default_from_worker and selected == default_from_worker else "online_device" if online_devices and selected == str(online_devices[0].get("device_id") or "").strip() else "known_device" if devices and selected == str(devices[0].get("device_id") or "").strip() else "default",
        "online_device_count": len(online_devices),
        "candidate_count": len(devices),
        "selected_worker_id": str(selected_worker.get("worker_id") or ""),
    }


def _replace_run_node(run, node_run, *, status: str, event_type: str, payload: dict[str, Any]) -> Any:
    nodes = dict(run.nodes)
    nodes[node_run.node_id] = node_run
    events = list(run.events)
    events.append({"at": utc_now(), "type": event_type, "payload": payload})
    return replace(run, status=status, nodes=nodes, events=events, updated_at=utc_now())


def _android_lifecycle_acceptance(command_status: dict[str, Any], *, accepted: bool) -> dict[str, Any]:
    lifecycle = {
        "schema_version": "spiritkin.android_command_lifecycle_acceptance.v1",
        "accepted": bool(accepted),
        "device_id": str(command_status.get("device_id") or ""),
        "operation": str(command_status.get("operation") or ""),
        "command_id": str(command_status.get("command_id") or ""),
        "status": str(command_status.get("status") or ""),
        "queued_at": command_status.get("queued_at") or command_status.get("created_at") or "",
        "delivered_at": command_status.get("delivered_at") or "",
        "completed_at": command_status.get("completed_at") or command_status.get("finished_at") or "",
        "reported_at": command_status.get("reported_at") or "",
    }
    lifecycle["evidence_complete"] = bool(
        lifecycle["command_id"]
        and lifecycle["operation"]
        and lifecycle["queued_at"]
        and lifecycle["delivered_at"]
        and lifecycle["completed_at"]
        and lifecycle["status"] == "completed"
    )
    return lifecycle


def _run_android_step_node(store: JsonWorkflowStore, arguments: dict[str, Any], definition, run, node, *, worker_pool=None) -> ToolResult | None:
    current = run.nodes.get(node.node_id)
    if current is None or current.status != NODE_PENDING:
        return None
    if not all(_dependency_satisfied(run, dep) for dep in node.depends_on):
        return None
    device_id, operation, params, resolved_arguments = _android_step_arguments(node, run)
    started_at = current.started_at or utc_now()
    worker_requirement = _android_worker_requirement(node, operation, resolved_arguments)
    worker_schedule = _schedule_worker(worker_pool, worker_requirement)
    if not operation:
        companion_snapshot = _android_companion_store(arguments).snapshot()
        resolved_device_id, device_selection = _select_android_device_id(device_id, worker_schedule, companion_snapshot)
        blocked = replace(
            current,
            status=NODE_BLOCKED,
            attempts=current.attempts + 1,
            started_at=started_at,
            finished_at=utc_now(),
            outputs={"node_type": node.node_type, "device_id": resolved_device_id, "arguments": resolved_arguments, "status": "android_step_rejected", "worker_requirement": worker_requirement, "worker_schedule": worker_schedule or {}, "device_selection": device_selection},
            error="missing_android_operation",
        )
        updated = _replace_run_node(run, blocked, status=RUN_BLOCKED, event_type="node_blocked", payload={"node_id": node.node_id, "error": blocked.error})
        store.save_run(updated)
        return ToolResult(True, f"工作流运行状态: {updated.status}", data={"run": updated.snapshot(), "definition": definition.snapshot()})
    if worker_schedule is not None and worker_schedule.get("enforced") and worker_schedule.get("status") != "selected":
        companion_snapshot = _android_companion_store(arguments).snapshot()
        resolved_device_id, device_selection = _select_android_device_id(device_id, worker_schedule, companion_snapshot)
        blocked = replace(
            current,
            status=NODE_BLOCKED,
            attempts=current.attempts + 1,
            started_at=started_at,
            finished_at=utc_now(),
            outputs={
                "node_type": node.node_type,
                "device_id": resolved_device_id,
                "operation": operation,
                "params": params,
                "status": "android_step_rejected",
                "worker_requirement": worker_requirement,
                "worker_schedule": worker_schedule,
                "device_selection": device_selection,
            },
            error=str(worker_schedule.get("reason") or "worker_not_found"),
        )
        updated = _replace_run_node(run, blocked, status=RUN_BLOCKED, event_type="worker_schedule_blocked", payload={"node_id": node.node_id, "operation": operation, "worker_schedule": worker_schedule})
        store.save_run(updated)
        return ToolResult(True, f"工作流运行状态: {updated.status}", data={"run": updated.snapshot(), "definition": definition.snapshot()})

    companion = _android_companion_store(arguments)
    companion_snapshot = companion.snapshot()
    device_id, device_selection = _select_android_device_id(device_id, worker_schedule, companion_snapshot)
    run = _persist_node_running(
        store,
        run,
        node,
        outputs={
            "node_type": node.node_type,
            "device_id": device_id,
            "operation": operation,
            "params": params,
            "status": "android_step_enqueueing",
            "worker_requirement": worker_requirement,
            "worker_schedule": worker_schedule or {},
            "device_selection": device_selection,
        },
    )
    current = run.nodes[node.node_id]
    queued = companion.enqueue_command(device_id, operation, params)
    if not queued.get("queued"):
        message = str(queued.get("message") or queued.get("error_code") or "android_command_not_queued")
        blocked = replace(
            current,
            status=NODE_BLOCKED,
            attempts=current.attempts,
            started_at=started_at,
            finished_at=utc_now(),
            outputs={
                "node_type": node.node_type,
                "device_id": device_id,
                "operation": operation,
                "params": params,
                "enqueue_result": queued,
                "status": "android_step_rejected",
                "worker_requirement": worker_requirement,
                "worker_schedule": worker_schedule or {},
                "device_selection": device_selection,
            },
            error=message,
        )
        updated = _replace_run_node(run, blocked, status=RUN_BLOCKED, event_type="node_blocked", payload={"node_id": node.node_id, "error": message, "operation": operation})
        store.save_run(updated)
        return ToolResult(True, f"工作流运行状态: {updated.status}", data={"run": updated.snapshot(), "definition": definition.snapshot(), "android_command": queued})

    command = dict(queued.get("command") or {})
    command_id = str(command.get("command_id") or "").strip()
    waiting = replace(
        current,
        status=NODE_WAITING,
        attempts=current.attempts,
        started_at=started_at,
        finished_at="",
        outputs={
            "node_type": node.node_type,
            "device_id": str(queued.get("device_id") or device_id),
            "operation": operation,
            "params": params,
            "command_id": command_id,
            "command": command,
            "status": "android_step_queued",
            "worker_requirement": worker_requirement,
            "worker_schedule": worker_schedule or {},
            "device_selection": device_selection,
        },
        error="",
    )
    updated = _replace_run_node(
        run,
        waiting,
        status=RUN_WAITING,
        event_type="android_step_queued",
        payload={"node_id": node.node_id, "device_id": waiting.outputs["device_id"], "operation": operation, "command_id": command_id},
    )
    store.save_run(updated)
    return ToolResult(True, f"Android 工作流节点已投递: {operation}", data={"run": updated.snapshot(), "definition": definition.snapshot(), "android_command": queued})


def _reconcile_android_step_nodes(arguments: dict[str, Any], definition, run, runner: WorkflowRunner):
    companion = _android_companion_store(arguments)
    updated = run
    changed = False
    for node in definition.nodes:
        if not _is_android_step_node(node):
            continue
        current = updated.nodes.get(node.node_id)
        if current is None or current.status != NODE_WAITING:
            continue
        command_id = str(current.outputs.get("command_id") or "").strip()
        if not command_id:
            command = current.outputs.get("command") if isinstance(current.outputs.get("command"), dict) else {}
            command_id = str(command.get("command_id") or "").strip()
        if not command_id:
            continue
        device_id = str(current.outputs.get("device_id") or "").strip()
        command_status = companion.command_status(command_id, device_id)
        if not command_status:
            continue
        status = str(command_status.get("status") or "").strip().lower()
        if status == "completed":
            completed = replace(
                current,
                status=NODE_SUCCEEDED,
                finished_at=utc_now(),
                outputs={
                    **current.outputs,
                    "status": "android_step_completed",
                    "command_result": command_status,
                    "lifecycle_acceptance": _android_lifecycle_acceptance(command_status, accepted=True),
                },
                error="",
            )
            updated = _replace_run_node(
                updated,
                completed,
                status=RUN_RUNNING,
                event_type="android_step_completed",
                payload={"node_id": node.node_id, "device_id": device_id, "operation": command_status.get("operation"), "command_id": command_id},
            )
            changed = True
        elif status in {"failed", "canceled"}:
            message = str(command_status.get("message") or command_status.get("error_code") or f"android_command_{status}")
            blocked = replace(
                current,
                status=NODE_BLOCKED,
                finished_at=utc_now(),
                outputs={
                    **current.outputs,
                    "status": f"android_step_{status}",
                    "command_result": command_status,
                    "lifecycle_acceptance": _android_lifecycle_acceptance(command_status, accepted=False),
                },
                error=message,
            )
            updated = _replace_run_node(
                updated,
                blocked,
                status=RUN_BLOCKED,
                event_type="node_blocked",
                payload={"node_id": node.node_id, "device_id": device_id, "operation": command_status.get("operation"), "command_id": command_id, "error": message},
            )
            changed = True
    return (runner._refresh_run_status(definition, updated) if changed else run, changed)


class WorkflowGraphTool(BaseTool):
    def __init__(
        self,
        spec: ToolSpec,
        *,
        tool_registry,
        worker_pool: WorkerPool | None = None,
        workflow_store_factory=None,
    ):
        self.spec = spec
        self._tool_registry = tool_registry
        self._worker_pool = worker_pool
        self._workflow_store_factory = workflow_store_factory

    def set_worker_pool(self, worker_pool: WorkerPool | None) -> None:
        self._worker_pool = worker_pool

    def _runner(self, *, skill_runner=None, store: JsonWorkflowStore | None = None) -> WorkflowRunner:
        return WorkflowRunner(
            tool_registry=self._tool_registry,
            skill_runner=skill_runner,
            worker_pool=self._worker_pool,
            state_sink=store.save_run if store is not None else None,
        )

    def _store(self, arguments: dict[str, Any]) -> JsonWorkflowStore:
        if self._workflow_store_factory is not None:
            return self._workflow_store_factory(arguments)
        return _workflow_store(arguments)

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return ToolResult(False, f"不支持的工具: {call.name}", error_code="tool_not_supported")
        arguments = dict(call.arguments or {})
        if (
            self.spec.operation in {"run_next", "run_node", "auto_advance_runs"}
            and self._workflow_store_factory is None
        ):
            return ToolResult(
                False,
                "工作流节点只能由持有有效 fencing lease 的 Runtime Host 推进。",
                error_code="runtime_host_execution_required",
            )
        store = self._store(arguments)
        try:
            if self.spec.operation == "save_ecommerce_definition":
                definition = build_ecommerce_auto_listing_definition()
                store.save_definition(definition, actor=str(arguments.get("actor") or "desktop"), reason="save ecommerce built-in definition")
                return ToolResult(True, f"已保存工作流定义: {definition.name}", data={"definition": definition.snapshot(), "state_dir": str(store.state_dir)})
            if self.spec.operation == "save_builtin_definition":
                workflow_name = str(arguments.get("workflow_name") or "ecommerce.auto_listing.v1")
                definition = get_builtin_workflow_definition(workflow_name)
                if definition is None:
                    return ToolResult(False, f"未找到内置工作流定义: {workflow_name}", error_code="workflow_definition_not_found")
                store.save_definition(definition, actor=str(arguments.get("actor") or "desktop"), reason="save built-in definition")
                return ToolResult(True, f"已保存工作流定义: {definition.name}", data={"definition": definition.snapshot(), "state_dir": str(store.state_dir)})
            if self.spec.operation == "upsert_definition":
                raw_definition = arguments.get("definition")
                if not isinstance(raw_definition, dict):
                    return ToolResult(False, "缺少 definition 对象", error_code="missing_params", metadata={"missing_param": "definition"})
                definition = workflow_definition_with_port_references(workflow_definition_from_dict(raw_definition))
                if not definition.name:
                    return ToolResult(False, "工作流定义缺少 name", error_code="missing_workflow_name")
                issues = definition.validate()
                if issues:
                    return ToolResult(False, f"工作流定义无效: {', '.join(issues)}", error_code="invalid_workflow_definition", metadata={"issues": issues})
                store.save_definition(definition, actor=str(arguments.get("actor") or "desktop"), reason=str(arguments.get("reason") or "upsert workflow definition"))
                return ToolResult(True, f"已保存自定义工作流定义: {definition.name}", data={"definition": definition.snapshot(), "state_dir": str(store.state_dir)})
            if self.spec.operation == "compose_definition":
                components = _list_arg(arguments, "components")
                if not components:
                    return ToolResult(False, "缺少 components 列表", error_code="missing_params", metadata={"missing_param": "components"})
                definition = build_composed_workflow_definition(
                    name=_string_arg(arguments, "workflow_name", "custom.workflow.composed.v1"),
                    components=components,
                    mode=_string_arg(arguments, "mode", "serial"),
                    version=_string_arg(arguments, "version", "0.1.0"),
                    display_name=_string_arg(arguments, "display_name", "组合工作流"),
                    description=_string_arg(arguments, "description", "由多个子工作流自由组合而成的主控工作流。"),
                    workspace_id=_string_arg(arguments, "workspace_id"),
                )
                if not definition.nodes:
                    return ToolResult(False, "components 没有可用的 workflow_name", error_code="invalid_workflow_definition")
                missing = [
                    str(node.arguments.get("workflow_name") or "")
                    for node in definition.nodes
                    if not (store.load_definition(str(node.arguments.get("workflow_name") or "")) or get_builtin_workflow_definition(str(node.arguments.get("workflow_name") or "")))
                ]
                if missing:
                    return ToolResult(False, f"组合工作流引用了未知子工作流: {', '.join(missing)}", error_code="workflow_definition_not_found", metadata={"missing_workflows": missing})
                issues = definition.validate()
                if issues:
                    return ToolResult(False, f"组合工作流定义无效: {', '.join(issues)}", error_code="invalid_workflow_definition", metadata={"issues": issues})
                store.save_definition(definition, actor=str(arguments.get("actor") or "desktop"), reason=str(arguments.get("reason") or "compose workflow definition"))
                return ToolResult(True, f"已保存组合工作流定义: {definition.name}", data={"definition": definition.snapshot(), "state_dir": str(store.state_dir)})
            if self.spec.operation == "delete_definition":
                workflow_name = str(arguments.get("workflow_name") or "").strip()
                if not workflow_name:
                    return ToolResult(False, "缺少 workflow_name 参数", error_code="missing_params", metadata={"missing_param": "workflow_name"})
                deleted = store.delete_definition(workflow_name, actor=str(arguments.get("actor") or "desktop"))
                if not deleted:
                    return ToolResult(False, f"未找到已保存工作流定义: {workflow_name}", error_code="workflow_definition_not_found")
                return ToolResult(True, f"已删除工作流定义: {workflow_name}", data={"workflow_name": workflow_name, "state_dir": str(store.state_dir)})
            if self.spec.operation == "rollback_definition":
                workflow_name = str(arguments.get("workflow_name") or "").strip()
                version_id = str(arguments.get("version_id") or "").strip()
                if not workflow_name or not version_id:
                    return ToolResult(False, "缺少 workflow_name 或 version_id 参数", error_code="missing_params")
                definition = store.rollback_definition(workflow_name, version_id, actor=str(arguments.get("actor") or "desktop"))
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义版本: {workflow_name} / {version_id}", error_code="workflow_definition_version_not_found")
                return ToolResult(True, f"已回滚工作流定义: {workflow_name}", data={"definition": definition.snapshot(), "version_id": version_id, "state_dir": str(store.state_dir)})
            if self.spec.operation == "list_definitions":
                definitions = [definition.snapshot() for definition in store.list_definitions()]
                return ToolResult(True, f"共有 {len(definitions)} 个工作流定义", data={"definitions": definitions, "count": len(definitions), "state_dir": str(store.state_dir)})
            if self.spec.operation == "start_run":
                workflow_name = str(arguments.get("workflow_name") or "ecommerce.auto_listing.v1")
                definition = store.load_definition(workflow_name) or get_builtin_workflow_definition(workflow_name)
                if definition is not None:
                    store.save_definition(definition, actor=str(arguments.get("actor") or "desktop"), reason="materialize workflow definition for run", record_history=False)
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义: {workflow_name}", error_code="workflow_definition_not_found")
                run = start_workflow_run(definition, _dict_arg(arguments, "inputs"), run_id=str(arguments.get("run_id") or ""))
                store.save_run(run)
                return ToolResult(True, f"已启动工作流运行: {run.run_id}", data={"run": run.snapshot(), "definition": definition.snapshot()})
            if self.spec.operation == "list_runs":
                workflow_name = str(arguments.get("workflow_name") or "")
                runs = [run.snapshot() for run in store.list_runs(workflow_name=workflow_name)]
                return ToolResult(True, f"共有 {len(runs)} 个工作流运行", data={"runs": runs, "count": len(runs), "state_dir": str(store.state_dir)})
            if self.spec.operation in {"list_node_catalog", "schema"}:
                specs = self._tool_registry.list_specs() if hasattr(self._tool_registry, "list_specs") else []
                catalog = workflow_node_catalog(specs)
                return ToolResult(True, "工作流节点目录已生成", data={"node_catalog": catalog, "schema": catalog, "state_dir": str(store.state_dir)})
            if self.spec.operation == "retry_node":
                run_id = str(arguments.get("run_id") or "").strip()
                node_id = str(arguments.get("node_id") or "").strip()
                if not run_id or not node_id:
                    return ToolResult(False, "缺少 run_id 或 node_id 参数", error_code="missing_params")
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                current = run.nodes.get(node_id)
                if current is None:
                    return ToolResult(False, f"未找到工作流节点: {node_id}", error_code="workflow_node_not_found")
                if current.status != NODE_FAILED:
                    return ToolResult(False, f"节点当前状态不允许 retry_node: {current.status}", error_code="workflow_node_not_retryable")
                nodes = dict(run.nodes)
                nodes[node_id] = replace(
                    current,
                    status=NODE_PENDING,
                    finished_at="",
                    error="",
                    outputs={**dict(current.outputs or {}), "retry": {"manual": True, "requested_at": utc_now(), "attempts_so_far": current.attempts}},
                )
                events = [*run.events, {"at": utc_now(), "type": "node_retry_requested", "payload": {"node_id": node_id, "actor": str(arguments.get("actor") or "desktop")}}]
                updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
                store.save_run(updated)
                return ToolResult(True, f"节点已重新排队: {node_id}", data={"run": updated.snapshot()})
            if self.spec.operation == "reset_run":
                run_id = str(arguments.get("run_id") or "").strip()
                if not run_id:
                    return ToolResult(False, "缺少 run_id 参数", error_code="missing_params")
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                nodes = {
                    node_id: (
                        node
                        if node.status == NODE_SUCCEEDED
                        else replace(node, status=NODE_PENDING, started_at="", finished_at="", error="", outputs={})
                    )
                    for node_id, node in run.nodes.items()
                }
                events = [*run.events, {"at": utc_now(), "type": "run_reset", "payload": {"actor": str(arguments.get("actor") or "desktop")}}]
                updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
                store.save_run(updated)
                return ToolResult(True, f"工作流运行已重置: {run_id}", data={"run": updated.snapshot()})
            if self.spec.operation in {"run_next", "run_node"}:
                run_id = str(arguments.get("run_id") or "").strip()
                if not run_id:
                    return ToolResult(False, "缺少 run_id 参数", error_code="missing_params", metadata={"missing_param": "run_id"})
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                definition = store.load_definition(run.workflow_name)
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义: {run.workflow_name}", error_code="workflow_definition_not_found")
                skill_runner = _default_skill_runner(str(arguments.get("project_root") or ""), self._tool_registry)
                runner = self._runner(skill_runner=skill_runner, store=store)
                dry_run = _bool_arg(arguments, "dry_run")
                if not dry_run:
                    before_skip_events = len(run.events)
                    run = runner.apply_branch_skips(definition, run)
                    if len(run.events) != before_skip_events:
                        store.save_run(run)
                    run, subgraph_reconciled = _reconcile_subgraph_nodes(store, definition, run, runner)
                    if subgraph_reconciled:
                        store.save_run(run)
                    run, foreach_reconciled = _reconcile_foreach_nodes(store, definition, run, runner)
                    if foreach_reconciled:
                        store.save_run(run)
                    run, _ = _reconcile_android_step_nodes(arguments, definition, run, runner)
                if self.spec.operation == "run_next":
                    runnable = runner.runnable_nodes(definition, run)
                    if not dry_run and runnable and _is_android_step_node(runnable[0]):
                        android_result = _run_android_step_node(store, arguments, definition, run, runnable[0], worker_pool=self._worker_pool)
                        if android_result is not None:
                            return android_result
                    if not dry_run and runnable and runnable[0].node_type == "subgraph":
                        subgraph_result = _run_subgraph_node(store, definition, run, runnable[0])
                        if subgraph_result is not None:
                            return subgraph_result
                    if not dry_run and runnable and runnable[0].node_type == "foreach":
                        foreach_result = _run_foreach_node(store, definition, run, runnable[0], runner)
                        if foreach_result is not None:
                            return foreach_result
                    updated = runner.run_next(definition, run, dry_run=dry_run)
                else:
                    node_id = str(arguments.get("node_id") or "").strip()
                    if not node_id:
                        return ToolResult(False, "缺少 node_id 参数", error_code="missing_params", metadata={"missing_param": "node_id"})
                    target_node = next((node for node in definition.nodes if node.node_id == node_id), None)
                    if not dry_run and target_node is not None and _is_android_step_node(target_node):
                        android_result = _run_android_step_node(store, arguments, definition, run, target_node, worker_pool=self._worker_pool)
                        if android_result is not None:
                            return android_result
                    if not dry_run and target_node is not None and target_node.node_type == "subgraph":
                        subgraph_result = _run_subgraph_node(store, definition, run, target_node)
                        if subgraph_result is not None:
                            return subgraph_result
                    if not dry_run and target_node is not None and target_node.node_type == "foreach":
                        foreach_result = _run_foreach_node(store, definition, run, target_node, runner)
                        if foreach_result is not None:
                            return foreach_result
                    updated = runner.run_node(definition, run, node_id, dry_run=dry_run)
                store.save_run(updated)
                return ToolResult(True, f"工作流运行状态: {updated.status}", data={"run": updated.snapshot(), "definition": definition.snapshot()})
            if self.spec.operation == "auto_advance_runs":
                skill_runner = _default_skill_runner(str(arguments.get("project_root") or ""), self._tool_registry)
                summary = _auto_advance_runs(store, arguments, self._runner(skill_runner=skill_runner, store=store))
                return ToolResult(
                    True,
                    f"自动推进 {summary['run_count']} 个工作流运行，推进节点 {summary['advanced_steps']} 个，超时 {summary['timed_out_count']} 个。",
                    data={"auto_advance": summary, "state_dir": str(store.state_dir)},
                )
            if self.spec.operation == "approve_review":
                run_id = str(arguments.get("run_id") or "").strip()
                node_id = str(arguments.get("node_id") or "").strip()
                if not run_id or not node_id:
                    return ToolResult(False, "缺少 run_id 或 node_id 参数", error_code="missing_params")
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                definition = store.load_definition(run.workflow_name)
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义: {run.workflow_name}", error_code="workflow_definition_not_found")
                runner = self._runner()
                review_payload = arguments.get("review_payload") if isinstance(arguments.get("review_payload"), dict) else arguments
                updated = runner.approve_review_node(definition, run, node_id, reviewer=str(arguments.get("reviewer") or "human"), review_payload=review_payload)
                store.save_run(updated)
                return ToolResult(True, f"审核节点已处理: {node_id}", data={"run": updated.snapshot(), "definition": definition.snapshot()})
            if self.spec.operation == "claim_agent_task":
                run_id = str(arguments.get("run_id") or "").strip()
                node_id = str(arguments.get("node_id") or "").strip()
                agent_id = str(arguments.get("agent_id") or "").strip()
                if not run_id or not node_id or not agent_id:
                    return ToolResult(False, "缺少 run_id、node_id 或 agent_id 参数", error_code="missing_params")
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                definition = store.load_definition(run.workflow_name)
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义: {run.workflow_name}", error_code="workflow_definition_not_found")
                definition_node = next((node for node in definition.nodes if node.node_id == node_id), None)
                if definition_node is None:
                    return ToolResult(False, f"未找到工作流节点定义: {node_id}", error_code="workflow_node_not_found")
                if not _is_agent_task_like(definition_node):
                    return ToolResult(False, f"节点不是 Agent 任务: {node_id}", error_code="workflow_node_not_agent_task")
                current = run.nodes.get(node_id)
                if current is None:
                    return ToolResult(False, f"未找到工作流节点: {node_id}", error_code="workflow_node_not_found")
                if current.status == NODE_RUNNING and current.assigned_agent == agent_id:
                    return ToolResult(True, f"Agent 节点已由 {agent_id} 认领: {node_id}", data={"run": run.snapshot()})
                if current.status not in {NODE_PENDING, NODE_BLOCKED}:
                    return ToolResult(False, f"节点当前状态不允许认领: {current.status}", error_code="workflow_node_not_claimable")
                if not all(_dependency_satisfied(run, dep) for dep in definition_node.depends_on):
                    return ToolResult(False, "节点依赖尚未完成", error_code="dependencies_not_satisfied")
                claimed = replace(
                    current,
                    status=NODE_RUNNING,
                    attempts=current.attempts + 1,
                    started_at=current.started_at or utc_now(),
                    error="",
                    assigned_agent=agent_id,
                )
                nodes = dict(run.nodes)
                nodes[node_id] = claimed
                events = list(run.events)
                envelope = build_agent_interaction_envelope(run, node_id, agent_id=agent_id, payload={"action": "claim_agent_task"})
                events.append({"at": utc_now(), "type": "agent_task_claimed", "payload": {"node_id": node_id, "agent_id": agent_id, "interaction_envelope": envelope}})
                updated = replace(run, status=RUN_RUNNING, nodes=nodes, events=events, updated_at=utc_now())
                store.save_run(updated)
                return ToolResult(True, f"Agent 节点已认领: {node_id}", data={"run": updated.snapshot()})
            if self.spec.operation == "assign_agent":
                run_id = str(arguments.get("run_id") or "").strip()
                node_id = str(arguments.get("node_id") or "").strip()
                agent_id = str(arguments.get("agent_id") or "").strip()
                if not run_id or not node_id or not agent_id:
                    return ToolResult(False, "缺少 run_id、node_id 或 agent_id 参数", error_code="missing_params")
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                definition = store.load_definition(run.workflow_name)
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义: {run.workflow_name}", error_code="workflow_definition_not_found")
                definition_node = next((node for node in definition.nodes if node.node_id == node_id), None)
                if definition_node is None:
                    return ToolResult(False, f"未找到工作流节点定义: {node_id}", error_code="workflow_node_not_found")
                current = run.nodes.get(node_id)
                if current is None:
                    return ToolResult(False, f"未找到工作流节点: {node_id}", error_code="workflow_node_not_found")
                updated_node = replace(current, assigned_agent=agent_id, error="" if current.error == "agent_task_claim_required" else current.error)
                nodes = dict(run.nodes)
                nodes[node_id] = updated_node
                events = list(run.events)
                envelope = build_agent_interaction_envelope(run, node_id, agent_id=agent_id, payload={"action": "assign_agent", "previous_agent": current.assigned_agent or definition_node.assigned_agent})
                events.append(
                    {
                        "at": utc_now(),
                        "type": "node_agent_assigned",
                        "payload": {
                            "node_id": node_id,
                            "agent_id": agent_id,
                            "node_type": definition_node.node_type,
                            "previous_agent": current.assigned_agent or definition_node.assigned_agent,
                            "interaction_envelope": envelope,
                        },
                    }
                )
                updated = replace(run, nodes=nodes, events=events, updated_at=utc_now())
                store.save_run(updated)
                return ToolResult(True, f"节点处理 Agent 已设置: {node_id} -> {agent_id}", data={"run": updated.snapshot(), "definition": definition.snapshot()})
            if self.spec.operation == "complete_agent_task":
                run_id = str(arguments.get("run_id") or "").strip()
                node_id = str(arguments.get("node_id") or "").strip()
                if not run_id or not node_id:
                    return ToolResult(False, "缺少 run_id 或 node_id 参数", error_code="missing_params")
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                definition = store.load_definition(run.workflow_name)
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义: {run.workflow_name}", error_code="workflow_definition_not_found")
                definition_node = next((node for node in definition.nodes if node.node_id == node_id), None)
                if definition_node is None:
                    return ToolResult(False, f"未找到工作流节点定义: {node_id}", error_code="workflow_node_not_found")
                if not _is_agent_task_like(definition_node):
                    return ToolResult(False, f"节点不是 Agent 任务: {node_id}", error_code="workflow_node_not_agent_task")
                current = run.nodes.get(node_id)
                if current is None:
                    return ToolResult(False, f"未找到工作流节点: {node_id}", error_code="workflow_node_not_found")
                if current.status != NODE_RUNNING:
                    if current.status not in {NODE_PENDING, NODE_BLOCKED}:
                        return ToolResult(False, f"节点当前状态不允许完成: {current.status}", error_code="workflow_node_not_completable")
                    if not all(_dependency_satisfied(run, dep) for dep in definition_node.depends_on):
                        return ToolResult(False, "节点依赖尚未完成", error_code="dependencies_not_satisfied")
                status = NODE_SUCCEEDED if _bool_arg(arguments, "success", True) else NODE_BLOCKED
                outputs = _dict_arg(arguments, "outputs")
                envelope = build_agent_interaction_envelope(
                    run,
                    node_id,
                    agent_id=str(arguments.get("agent_id") or current.assigned_agent),
                    artifact_refs=_list_arg(arguments, "artifact_refs"),
                    knowledge_refs=_list_arg(arguments, "knowledge_refs"),
                    audit_event_id=str(arguments.get("audit_event_id") or ""),
                    payload={"action": "complete_agent_task", "success": status == NODE_SUCCEEDED, "output_keys": sorted(outputs.keys())},
                )
                updated_node = replace(
                    current,
                    status=status,
                    attempts=current.attempts or 1,
                    started_at=current.started_at or utc_now(),
                    finished_at=utc_now(),
                    outputs={**outputs, "interaction_envelope": envelope},
                    error="" if status == NODE_SUCCEEDED else str(arguments.get("error") or "agent_task_blocked"),
                    assigned_agent=str(arguments.get("agent_id") or current.assigned_agent),
                )
                nodes = dict(run.nodes)
                nodes[node_id] = updated_node
                events = list(run.events)
                events.append(
                    {
                        "at": utc_now(),
                        "type": "agent_task_completed" if status == NODE_SUCCEEDED else "agent_task_blocked",
                        "payload": {
                            "node_id": node_id,
                            "agent_id": updated_node.assigned_agent,
                            "artifact_refs": envelope["artifact_refs"],
                            "knowledge_refs": envelope["knowledge_refs"],
                            "audit_event_id": envelope["audit_event_id"],
                            "interaction_envelope": envelope,
                        },
                    }
                )
                updated = replace(run, status=RUN_RUNNING if status == NODE_SUCCEEDED else RUN_BLOCKED, nodes=nodes, events=events, updated_at=utc_now())
                updated = self._runner()._refresh_run_status(definition, updated)
                store.save_run(updated)
                return ToolResult(True, f"Agent 节点已更新: {node_id}", data={"run": updated.snapshot()})
            if self.spec.operation == "signal_node":
                run_id = str(arguments.get("run_id") or "").strip()
                node_id = str(arguments.get("node_id") or "").strip()
                if not run_id or not node_id:
                    return ToolResult(False, "缺少 run_id 或 node_id 参数", error_code="missing_params")
                run = store.load_run(run_id)
                if run is None:
                    return ToolResult(False, f"未找到工作流运行: {run_id}", error_code="workflow_run_not_found")
                definition = store.load_definition(run.workflow_name)
                if definition is None:
                    return ToolResult(False, f"未找到工作流定义: {run.workflow_name}", error_code="workflow_definition_not_found")
                definition_node = next((node for node in definition.nodes if node.node_id == node_id), None)
                if definition_node is None:
                    return ToolResult(False, f"未找到工作流节点定义: {node_id}", error_code="workflow_node_not_found")
                if not _is_signalable_node(definition_node):
                    return ToolResult(False, f"节点不支持外部信号: {node_id}", error_code="workflow_node_not_signalable")
                current = run.nodes.get(node_id)
                if current is None:
                    return ToolResult(False, f"未找到工作流节点: {node_id}", error_code="workflow_node_not_found")
                if current.status not in {NODE_WAITING, NODE_PENDING, NODE_BLOCKED}:
                    return ToolResult(False, f"节点当前状态不允许信号完成: {current.status}", error_code="workflow_node_not_signalable")
                success = _bool_arg(arguments, "success", True)
                signal_payload = _dict_arg(arguments, "signal_payload")
                actor = str(arguments.get("actor") or arguments.get("agent_id") or "external")
                envelope = build_agent_interaction_envelope(
                    run,
                    node_id,
                    agent_id=actor,
                    artifact_refs=_list_arg(arguments, "artifact_refs"),
                    knowledge_refs=_list_arg(arguments, "knowledge_refs"),
                    audit_event_id=str(arguments.get("audit_event_id") or ""),
                    payload={"action": "signal_node", "success": success, "signal_keys": sorted(signal_payload.keys())},
                )
                outputs = {
                    **current.outputs,
                    "signal_payload": signal_payload,
                    "signaled_by": actor,
                    "signaled_at": utc_now(),
                    "interaction_envelope": envelope,
                }
                status = NODE_SUCCEEDED if success else NODE_BLOCKED
                updated_node = replace(
                    current,
                    status=status,
                    attempts=current.attempts or 1,
                    started_at=current.started_at or utc_now(),
                    finished_at=utc_now(),
                    outputs=outputs,
                    error="" if success else str(arguments.get("error") or "external_signal_failed"),
                    assigned_agent=current.assigned_agent or actor,
                )
                nodes = dict(run.nodes)
                nodes[node_id] = updated_node
                event_type = {
                    "waiter": "waiter_released",
                    "external_callback": "external_callback_received",
                    "subgraph": "subgraph_completed",
                }.get(definition_node.node_type, "node_signaled")
                if not success:
                    event_type = "node_blocked"
                events = list(run.events)
                events.append(
                    {
                        "at": utc_now(),
                        "type": event_type,
                        "payload": {
                            "node_id": node_id,
                            "agent_id": actor,
                            "artifact_refs": envelope["artifact_refs"],
                            "knowledge_refs": envelope["knowledge_refs"],
                            "audit_event_id": envelope["audit_event_id"],
                            "interaction_envelope": envelope,
                            "signal_payload": signal_payload,
                        },
                    }
                )
                updated = replace(run, status=RUN_RUNNING if success else RUN_BLOCKED, nodes=nodes, events=events, updated_at=utc_now())
                updated = self._runner()._refresh_run_status(definition, updated)
                store.save_run(updated)
                return ToolResult(True, f"节点信号已处理: {node_id}", data={"run": updated.snapshot(), "definition": definition.snapshot()})
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}", error_code="tool_exception", metadata={"exception_type": type(exc).__name__})
        return ToolResult(False, f"未实现的工作流操作: {self.spec.operation}", error_code="operation_not_supported")


def get_workflow_graph_tools(
    *,
    tool_registry,
    worker_pool: WorkerPool | None = None,
    workflow_store_factory=None,
) -> list[BaseTool]:
    tools: list[BaseTool] = [
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.save_ecommerce_definition",
                description="保存电商自动化上架 Blueprint 工作流定义。",
                target="workflow_graph",
                operation="save_ecommerce_definition",
                risk_level="low",
                schema={"project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.save_builtin_definition",
                description="按工作流名称保存一个内置 Blueprint 工作流定义。",
                target="workflow_graph",
                operation="save_builtin_definition",
                risk_level="low",
                schema={"workflow_name": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.list_definitions",
                description="列出已保存的 Blueprint 工作流定义。",
                target="workflow_graph",
                operation="list_definitions",
                risk_level="low",
                read_only=True,
                schema={"project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.upsert_definition",
                description="新增或更新一个可运行的 Blueprint 工作流定义。",
                target="workflow_graph",
                operation="upsert_definition",
                risk_level="low",
                schema={"definition": "object", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.compose_definition",
                description="把多个已有工作流自由组合为一个 subgraph 编排工作流定义。",
                target="workflow_graph",
                operation="compose_definition",
                risk_level="low",
                schema={"workflow_name": "string", "display_name": "string", "mode": "string", "components": "array", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.delete_definition",
                description="删除一个已保存的 Blueprint 工作流定义。",
                target="workflow_graph",
                operation="delete_definition",
                risk_level="medium",
                schema={"workflow_name": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.rollback_definition",
                description="回滚一个已保存的 Blueprint 工作流定义到历史版本。",
                target="workflow_graph",
                operation="rollback_definition",
                risk_level="medium",
                schema={"workflow_name": "string", "version_id": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.start_run",
                description="启动一个工作流运行实例并初始化节点状态。",
                target="workflow_graph",
                operation="start_run",
                risk_level="low",
                schema={"workflow_name": "string", "run_id": "string", "inputs": "object", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.list_runs",
                description="列出工作流运行实例。",
                target="workflow_graph",
                operation="list_runs",
                risk_level="low",
                read_only=True,
                schema={"workflow_name": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.list_node_catalog",
                description="列出可插入 Blueprint 的节点类型、工具、Skill 与端口规则。",
                target="workflow_graph",
                operation="list_node_catalog",
                risk_level="low",
                read_only=True,
                schema={"project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.schema",
                description="返回工作流节点目录、端口类型和兼容矩阵。",
                target="workflow_graph",
                operation="schema",
                risk_level="low",
                read_only=True,
                schema={"project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.retry_node",
                description="把失败节点重新置为 pending 以便重试。",
                target="workflow_graph",
                operation="retry_node",
                risk_level="medium",
                schema={"run_id": "string", "node_id": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.reset_run",
                description="把运行中所有未成功节点重置为 pending。",
                target="workflow_graph",
                operation="reset_run",
                risk_level="medium",
                schema={"run_id": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.run_next",
                description="执行或 dry-run 下一个满足依赖的工作流节点。",
                target="workflow_graph",
                operation="run_next",
                risk_level="medium",
                schema={"run_id": "string", "dry_run": "boolean", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.run_node",
                description="执行或 dry-run 指定工作流节点。",
                target="workflow_graph",
                operation="run_node",
                risk_level="medium",
                schema={"run_id": "string", "node_id": "string", "dry_run": "boolean", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.auto_advance_runs",
                description="扫描运行中的工作流，自动推进可执行节点并处理超时的 agent_task。",
                target="workflow_graph",
                operation="auto_advance_runs",
                risk_level="medium",
                schema={"workflow_name": "string", "run_id": "string", "max_runs": "number", "max_steps_per_run": "number", "agent_task_timeout_seconds": "number", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.approve_review",
                description="审核通过一个等待中的 review gate 节点。",
                target="workflow_graph",
                operation="approve_review",
                risk_level="medium",
                schema={"run_id": "string", "node_id": "string", "reviewer": "string", "jury_report_id": "string", "jury_report": "object", "jury_required": "boolean", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.claim_agent_task",
                description="由 Agent 或调度器认领一个可运行的 agent_task 节点。",
                target="workflow_graph",
                operation="claim_agent_task",
                risk_level="medium",
                schema={"run_id": "string", "node_id": "string", "agent_id": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.assign_agent",
                description="为工作流运行中的任意节点设置当前处理 Agent。",
                target="workflow_graph",
                operation="assign_agent",
                risk_level="low",
                schema={"run_id": "string", "node_id": "string", "agent_id": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.complete_agent_task",
                description="由 Agent 或调度器提交 agent_task 节点结果。",
                target="workflow_graph",
                operation="complete_agent_task",
                risk_level="medium",
                schema={"run_id": "string", "node_id": "string", "agent_id": "string", "success": "boolean", "outputs": "object", "artifact_refs": "array", "knowledge_refs": "array", "audit_event_id": "string", "error": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
        WorkflowGraphTool(
            ToolSpec(
                name="workflow.graph.signal_node",
                description="用外部信号完成 waiter、external_callback 或 subgraph 节点。",
                target="workflow_graph",
                operation="signal_node",
                risk_level="medium",
                schema={"run_id": "string", "node_id": "string", "success": "boolean", "signal_payload": "object", "artifact_refs": "array", "knowledge_refs": "array", "audit_event_id": "string", "project_root": "string", "workflow_state_dir": "string"},
            ),
            tool_registry=tool_registry,
            worker_pool=worker_pool,
        ),
    ]
    if workflow_store_factory is not None:
        for tool in tools:
            if isinstance(tool, WorkflowGraphTool):
                tool._workflow_store_factory = workflow_store_factory
    return tools
