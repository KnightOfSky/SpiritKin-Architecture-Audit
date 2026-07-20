from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.agent_management import build_agent_management_desktop_snapshot
from backend.app.skills_console import build_desktop_skills_snapshot
from backend.app.workflow_run_management import handle_workflow_run_management_action
from backend.orchestrator.workflow_graph import (
    NODE_BLOCKED,
    NODE_FAILED,
    NODE_PENDING,
    NODE_RUNNING,
    NODE_SKIPPED,
    NODE_SUCCEEDED,
    NODE_WAITING,
    NODE_WAITING_REVIEW,
    WorkflowDefinition,
    WorkflowNodeDefinition,
    WorkflowRun,
    build_ecommerce_auto_listing_definition,
    builtin_workflow_definitions,
    get_builtin_workflow_definition,
    workflow_node_catalog,
)
from backend.orchestrator.workflow_store import JsonWorkflowStore
from backend.tools import ToolCall, build_default_tool_registry

SCHEMA_VERSION = "spiritkin.workflow_management.v1"
DEFAULT_WORKFLOW_NAME = "ecommerce.auto_listing.v1"
OWNER_ACTIONS = {"delete_definition", "rollback_definition"}
EDITOR_ACTIONS = {
    "save_ecommerce_definition",
    "save_builtin_definition",
    "schema",
    "list_node_catalog",
    "upsert_definition",
    "start_run",
    "run_next",
    "run_node",
    "auto_advance_runs",
    "retry_node",
    "reset_run",
    "assign_agent",
    "claim_agent_task",
    "complete_agent_task",
    "signal_node",
    "compose_definition",
}
APPROVER_ACTIONS = {"approve_review"}
RUN_MANAGEMENT_ACTIONS = {"archive_run", "archive_workflow_run", "delete_run", "delete_workflow_run", "cleanup_runs", "cleanup_workflow_runs"}


def _project_root(payload: dict[str, Any] | None = None) -> str:
    raw = str((payload or {}).get("project_root") or "").strip()
    return str((Path(raw) if raw else Path.cwd()).resolve())


def _store(project_root: str) -> JsonWorkflowStore:
    from backend.app.workflow_task_finalizer_port import DefaultCollaborationTaskFinalizerPort

    return JsonWorkflowStore(
        project_root=project_root,
        collaboration_task_port=DefaultCollaborationTaskFinalizerPort(),
    )


def _definition_for_run(store: JsonWorkflowStore, run: WorkflowRun) -> WorkflowDefinition | None:
    return store.load_definition(run.workflow_name) or get_builtin_workflow_definition(run.workflow_name)


def _runnable_node_ids(definition: WorkflowDefinition | None, run: WorkflowRun) -> list[str]:
    if definition is None:
        return []
    runnable: list[str] = []
    for node in definition.nodes:
        state = run.nodes.get(node.node_id)
        if state is None or state.status != "pending":
            continue
        if all(run.nodes.get(dep) and run.nodes[dep].status in {NODE_SUCCEEDED, NODE_SKIPPED} for dep in node.depends_on):
            runnable.append(node.node_id)
    return runnable


def _run_snapshot(store: JsonWorkflowStore, run: WorkflowRun, *, agent_skill_map: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    definition = _definition_for_run(store, run)
    snapshot = run.snapshot()
    snapshot["runnable_node_ids"] = _runnable_node_ids(definition, run)
    snapshot["definition"] = definition.snapshot() if definition else None
    snapshot["selected_node_details"] = _node_details(definition, run, agent_skill_map=agent_skill_map or {})
    snapshot["node_status_counts"] = dict(sorted(Counter(node.status for node in run.nodes.values()).items()))
    snapshot["progress"] = _run_progress(run)
    snapshot["trace_replay"] = _trace_replay_snapshot(definition, run)
    runtime_context = store.list_runtime_context_patches(run_id=run.run_id, limit=1)
    finalizer_verdicts = store.list_finalizer_verdicts(run_id=run.run_id, limit=1)
    snapshot["runtime_contract"] = {
        "context_record": runtime_context[-1] if runtime_context else None,
        "finalizer_verdict": finalizer_verdicts[-1] if finalizer_verdicts else None,
    }
    return snapshot


def _run_progress(run: WorkflowRun) -> dict[str, Any]:
    total = len(run.nodes)
    counts = Counter(node.status for node in run.nodes.values())
    done = counts.get(NODE_SUCCEEDED, 0) + counts.get(NODE_SKIPPED, 0)
    active = counts.get(NODE_RUNNING, 0) + counts.get(NODE_WAITING, 0) + counts.get(NODE_WAITING_REVIEW, 0)
    failed = counts.get(NODE_FAILED, 0) + counts.get(NODE_BLOCKED, 0)
    return {
        "total": total,
        "done": done,
        "active": active,
        "failed": failed,
        "pending": counts.get(NODE_PENDING, 0),
        "percent": round((done / total) * 100, 1) if total else 0,
    }


def _node_progress(node_id: str, definition: WorkflowDefinition, run: WorkflowRun) -> dict[str, Any]:
    ordered = [node.node_id for node in definition.nodes]
    index = ordered.index(node_id) if node_id in ordered else -1
    state = run.nodes.get(node_id)
    status = state.status if state else NODE_PENDING
    if status in {NODE_SUCCEEDED, NODE_SKIPPED}:
        percent = 100
    elif status in {NODE_RUNNING, NODE_WAITING, NODE_WAITING_REVIEW}:
        percent = 50
    elif status in {NODE_FAILED, NODE_BLOCKED}:
        percent = 100
    else:
        percent = 0
    return {
        "status": status,
        "index": index + 1 if index >= 0 else 0,
        "total": len(ordered),
        "percent": percent,
    }


def _initial_replay_node_states(definition: WorkflowDefinition | None, run: WorkflowRun) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    definition_nodes = list(definition.nodes) if definition else []
    for node in definition_nodes:
        final_state = run.nodes.get(node.node_id)
        states[node.node_id] = {
            "node_id": node.node_id,
            "label": node.label or node.node_id,
            "node_type": node.node_type,
            "status": NODE_PENDING,
            "assigned_agent": (final_state.assigned_agent if final_state else "") or node.assigned_agent,
            "attempts": 0,
            "started_at": "",
            "finished_at": "",
            "error": "",
        }
    for node_id, final_state in run.nodes.items():
        if node_id in states:
            continue
        states[node_id] = {
            "node_id": node_id,
            "label": node_id,
            "node_type": "",
            "status": NODE_PENDING,
            "assigned_agent": final_state.assigned_agent,
            "attempts": 0,
            "started_at": "",
            "finished_at": "",
            "error": "",
        }
    return states


def _compact_replay_node_states(states: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        node_id: {
            "status": state.get("status") or NODE_PENDING,
            "assigned_agent": state.get("assigned_agent") or "",
            "attempts": int(state.get("attempts") or 0),
            "started_at": state.get("started_at") or "",
            "finished_at": state.get("finished_at") or "",
            "error": state.get("error") or "",
        }
        for node_id, state in states.items()
    }


def _replay_event_node_status(event_type: str) -> str:
    if event_type in {"node_started", "agent_task_claimed", "agent_task_dispatched", "foreach_iteration_started"}:
        return NODE_RUNNING
    if event_type in {"node_succeeded", "agent_task_completed", "node_review_approved", "branch_selected", "waiter_released", "external_callback_received", "subgraph_completed", "foreach_completed", "node_signaled"}:
        return NODE_SUCCEEDED
    if event_type == "node_skipped":
        return NODE_SKIPPED
    if event_type in {"node_waiting_review", "node_review_blocked"}:
        return NODE_WAITING_REVIEW
    if event_type in {"waiter_pending", "external_callback_pending", "subgraph_requested"}:
        return NODE_WAITING
    if event_type in {"node_blocked", "agent_task_blocked"}:
        return NODE_BLOCKED
    if event_type == "node_failed":
        return NODE_FAILED
    if event_type in {"foreach_failed", "agent_task_dispatch_failed"}:
        return NODE_FAILED
    return ""


def _replay_run_status(states: dict[str, dict[str, Any]], fallback: str) -> str:
    statuses = [str(state.get("status") or NODE_PENDING) for state in states.values()]
    if statuses and all(status in {NODE_SUCCEEDED, NODE_SKIPPED} for status in statuses):
        return "succeeded"
    if any(status == NODE_FAILED for status in statuses):
        return "failed"
    if any(status == NODE_WAITING_REVIEW for status in statuses):
        return "waiting_review"
    if any(status == NODE_WAITING for status in statuses):
        return "waiting"
    if any(status == NODE_BLOCKED for status in statuses):
        return "blocked"
    if any(status == NODE_RUNNING for status in statuses):
        return "running"
    return fallback


def _replay_event_summary(event_type: str, node_state: dict[str, Any] | None, payload: dict[str, Any]) -> str:
    label = str((node_state or {}).get("label") or payload.get("node_id") or "").strip()
    agent = str(payload.get("agent_id") or (node_state or {}).get("assigned_agent") or "").strip()
    if event_type == "run_started":
        return f"Run started for {payload.get('workflow') or 'workflow'}"
    if event_type == "node_started":
        return f"Node started: {label}"
    if event_type == "node_succeeded":
        return f"Node succeeded: {label}"
    if event_type == "node_failed":
        return f"Node failed: {label}"
    if event_type == "node_skipped":
        return f"Node skipped: {label}"
    if event_type == "node_blocked":
        return f"Node blocked: {label}"
    if event_type == "node_waiting_review":
        return f"Node waiting for review: {label}"
    if event_type == "node_review_approved":
        return f"Review approved: {label}"
    if event_type == "node_review_blocked":
        return f"Review blocked: {label}"
    if event_type == "branch_selected":
        return f"Branch selected: {label} -> {payload.get('selected_route') or '--'}"
    if event_type == "waiter_pending":
        return f"Waiter pending: {label}"
    if event_type == "waiter_released":
        return f"Waiter released: {label}"
    if event_type == "subgraph_requested":
        return f"Subgraph requested: {label}"
    if event_type == "subgraph_completed":
        return f"Subgraph completed: {label}"
    if event_type == "foreach_iteration_started":
        return f"Foreach iteration started: {label} #{payload.get('index')}"
    if event_type == "foreach_completed":
        return f"Foreach completed: {label}"
    if event_type == "foreach_failed":
        return f"Foreach failed: {label}"
    if event_type == "external_callback_pending":
        return f"External callback pending: {label}"
    if event_type == "external_callback_received":
        return f"External callback received: {label}"
    if event_type == "node_signaled":
        return f"Node signaled: {label}"
    if event_type == "node_agent_assigned":
        return f"Agent assigned to {label}: {agent}"
    if event_type == "agent_task_claimed":
        return f"Agent task claimed: {label} by {agent}"
    if event_type == "agent_task_dispatched":
        return f"Agent task dispatched: {label} to {agent}"
    if event_type == "agent_task_dispatch_failed":
        return f"Agent task dispatch failed: {label}"
    if event_type == "agent_task_completed":
        return f"Agent task completed: {label} by {agent}"
    if event_type == "agent_task_blocked":
        return f"Agent task blocked: {label} by {agent}"
    return event_type.replace("_", " ")


def _trace_replay_snapshot(definition: WorkflowDefinition | None, run: WorkflowRun) -> dict[str, Any]:
    states = _initial_replay_node_states(definition, run)
    timeline: list[dict[str, Any]] = []
    run_status = "pending"
    for index, raw_event in enumerate(run.events, start=1):
        if not isinstance(raw_event, dict):
            continue
        payload = raw_event.get("payload") if isinstance(raw_event.get("payload"), dict) else {}
        event_type = str(raw_event.get("type") or "event")
        at = str(raw_event.get("at") or run.created_at or "")
        node_id = str(payload.get("node_id") or "")
        node_state = states.get(node_id) if node_id else None
        next_status = _replay_event_node_status(event_type)
        if node_state is not None:
            if next_status:
                node_state["status"] = next_status
            if event_type in {"node_started", "agent_task_claimed"}:
                node_state["attempts"] = int(node_state.get("attempts") or 0) + 1
                node_state["started_at"] = node_state.get("started_at") or at
                node_state["finished_at"] = ""
                node_state["error"] = ""
            if event_type in {"node_succeeded", "node_failed", "node_blocked", "node_waiting_review", "node_review_approved", "node_review_blocked", "agent_task_completed", "agent_task_blocked", "branch_selected", "waiter_released", "external_callback_received", "subgraph_completed", "foreach_completed", "foreach_failed", "node_signaled"}:
                node_state["finished_at"] = at
            if payload.get("agent_id"):
                node_state["assigned_agent"] = str(payload.get("agent_id") or "")
            if payload.get("error"):
                node_state["error"] = str(payload.get("error") or "")
        if event_type == "run_started":
            run_status = "pending"
        else:
            run_status = _replay_run_status(states, run_status)
        timeline.append(
            {
                "step_index": len(timeline) + 1,
                "event_index": index - 1,
                "at": at,
                "type": event_type,
                "node_id": node_id,
                "node_label": str((node_state or {}).get("label") or ""),
                "node_type": str((node_state or {}).get("node_type") or ""),
                "agent_id": str(payload.get("agent_id") or (node_state or {}).get("assigned_agent") or ""),
                "summary": _replay_event_summary(event_type, node_state, payload),
                "payload": dict(payload),
                "state_after": {
                    "run_status": run_status,
                    "node_status": str((node_state or {}).get("status") or ""),
                    "node_states": _compact_replay_node_states(states),
                },
            }
        )
    return {
        "run_id": run.run_id,
        "workflow_name": run.workflow_name,
        "workflow_version": run.workflow_version,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "event_count": len([event for event in run.events if isinstance(event, dict)]),
        "step_count": len(timeline),
        "can_replay": bool(timeline),
        "timeline": timeline,
        "final_state": {
            "run_status": run.status,
            "node_states": {node_id: node.snapshot() for node_id, node in run.nodes.items()},
        },
    }


def _agent_task_queue(agent_id: str, definition: WorkflowDefinition, run: WorkflowRun) -> list[dict[str, Any]]:
    if not agent_id:
        return []
    queue: list[dict[str, Any]] = []
    runnable = set(_runnable_node_ids(definition, run))
    for index, node in enumerate(definition.nodes, start=1):
        state = run.nodes.get(node.node_id)
        effective_agent = (state.assigned_agent if state else "") or node.assigned_agent
        if effective_agent != agent_id:
            continue
        status = state.status if state else NODE_PENDING
        queue.append(
            {
                "node_id": node.node_id,
                "label": node.label or node.node_id,
                "node_type": node.node_type,
                "status": "runnable" if node.node_id in runnable and status == NODE_PENDING else status,
                "queue_index": index,
                "attempts": state.attempts if state else 0,
            }
        )
    return queue


NODE_TASK_QUEUE_KEYS = (
    "node_task_queue",
    "task_queue",
    "product_queue",
    "items",
    "products",
    "tasks",
    "queue",
)


def _node_queue_label(item: dict[str, Any]) -> str:
    for key in ("label", "title", "name", "task_id", "product_id", "sku_id", "id", "link", "url"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_node_queue_item(item: Any, *, source: str, key: str, index: int) -> dict[str, Any]:
    if isinstance(item, dict):
        normalized = dict(item)
        normalized.setdefault("label", _node_queue_label(normalized) or f"item_{index}")
        normalized.setdefault("status", str(normalized.get("state") or "pending"))
    else:
        normalized = {"value": item, "label": str(item), "status": "pending"}
    normalized["queue_source"] = source
    normalized["queue_key"] = key
    normalized["queue_index"] = index
    return normalized


def _node_queue_summary(queue: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [dict(item) for item in queue if isinstance(item, dict)]
    total = len(normalized)
    status_counts = Counter(str(item.get("status") or "pending") for item in normalized)
    active_statuses = {"running", "active", "in_progress", "processing", "claimed", "current"}
    done_statuses = {"done", "complete", "completed", "ready", "succeeded", "success"}
    current = next((item for item in normalized if str(item.get("status") or "").lower() in active_statuses), None)
    if current is None:
        current = next((item for item in normalized if str(item.get("status") or "").lower() not in done_statuses), None)
    if current is None and normalized:
        current = normalized[0]
    current_index = int(current.get("queue_index") or 0) if current else 0
    next_items = [
        item
        for item in normalized
        if current is None
        or int(item.get("queue_index") or 0) > current_index
        or item is not current and int(item.get("queue_index") or 0) == current_index
    ][:8]
    return {
        "total": total,
        "current_index": current_index,
        "remaining": max(0, total - current_index) if current_index else total,
        "status_counts": dict(sorted(status_counts.items())),
        "current_item": current,
        "next_items": next_items,
        "has_queue": total > 0,
    }


def _node_runtime_interface_contract(node: WorkflowNodeDefinition) -> dict[str, Any]:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    policy = metadata.get("connection_policy") if isinstance(metadata.get("connection_policy"), dict) else {}
    explicit = metadata.get("interface_contract") if isinstance(metadata.get("interface_contract"), dict) else {}
    ports = metadata.get("ports") if isinstance(metadata.get("ports"), list) else []
    inputs = list(explicit.get("inputs") or []) if isinstance(explicit.get("inputs"), list) else []
    outputs = list(explicit.get("outputs") or []) if isinstance(explicit.get("outputs"), list) else []
    accepts = str(policy.get("input_accepts") or "")
    emits = str(policy.get("output_emits") or "")
    for port in ports:
        if not isinstance(port, dict):
            continue
        direction = str(port.get("direction") or "").lower()
        name = str(port.get("name") or port.get("kind") or direction or "port")
        kind = str(port.get("kind") or "")
        contract_item = {
            "name": name,
            "kind": kind,
            "required": bool(port.get("required", direction == "input")),
            "description": str(port.get("description") or ""),
        }
        if direction == "input":
            if not inputs:
                inputs.append(contract_item)
        elif direction == "output":
            if not outputs:
                outputs.append(contract_item)
    return {
        "input_accepts": accepts,
        "output_emits": emits,
        "inputs": inputs,
        "outputs": outputs,
        "summary": str(explicit.get("summary") or metadata.get("responsibility") or ""),
    }


def _append_node_queue(queue: list[dict[str, Any]], value: Any, *, source: str, key: str) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        queue.append(_normalize_node_queue_item(item, source=source, key=key, index=len(queue) + 1))


def _append_node_scoped_queue(queue: list[dict[str, Any]], mapping: dict[str, Any], node_id: str, *, source: str, key: str) -> None:
    scoped = mapping.get(node_id)
    if isinstance(scoped, list):
        _append_node_queue(queue, scoped, source=source, key=f"{key}.{node_id}")
    elif isinstance(scoped, dict):
        _append_node_task_queues_from_container(queue, scoped, node_id, source=f"{source}.{key}.{node_id}")


def _append_node_task_queues_from_container(queue: list[dict[str, Any]], container: Any, node_id: str, *, source: str) -> None:
    if not isinstance(container, dict):
        return
    for key in NODE_TASK_QUEUE_KEYS:
        value = container.get(key)
        if isinstance(value, list):
            _append_node_queue(queue, value, source=source, key=key)
        elif isinstance(value, dict):
            _append_node_scoped_queue(queue, value, node_id, source=source, key=key)
    for scoped_key in ("node_queues", "node_task_queues", "task_queues_by_node", "queues_by_node"):
        value = container.get(scoped_key)
        if isinstance(value, dict):
            _append_node_scoped_queue(queue, value, node_id, source=source, key=scoped_key)
    direct_scope = container.get(node_id)
    if isinstance(direct_scope, list):
        _append_node_queue(queue, direct_scope, source=source, key=node_id)
    elif isinstance(direct_scope, dict):
        _append_node_task_queues_from_container(queue, direct_scope, node_id, source=f"{source}.{node_id}")


def _node_task_queue(node_id: str, run: WorkflowRun, state: Any) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    _append_node_task_queues_from_container(queue, run.inputs, node_id, source="run.inputs")
    if state is not None:
        _append_node_task_queues_from_container(queue, state.outputs, node_id, source="node.outputs")
    return queue


def _repair_suggestions(node, state, deps: list[dict[str, Any]], available_skills: list[dict[str, Any]]) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
    error = state.error if state else ""
    status = state.status if state else NODE_PENDING
    if any(dep.get("status") not in {NODE_SUCCEEDED, NODE_SKIPPED, "succeeded", "skipped"} for dep in deps):
        suggestions.append(
            {
                "kind": "dependency",
                "title": "等待或修复上游依赖",
                "detail": "先运行失败/未完成的上游节点，或断开不需要的输入依赖。",
                "action": "run_or_disconnect_dependency",
            }
        )
    if node.node_type == "tool_call" and not node.tool_name:
        suggestions.append({"kind": "definition", "title": "补 tool_name", "detail": "Tool 节点缺少 tool_name，填写后保存定义。", "action": "edit_node"})
    if node.node_type == "skill_call" and not node.skill_name:
        suggestions.append({"kind": "definition", "title": "补 skill_name", "detail": "Skill 节点缺少 skill_name，填写后保存定义。", "action": "edit_node"})
    if node.node_type == "skill_call" and node.skill_name and not any(item.get("name") == node.skill_name for item in available_skills):
        suggestions.append({"kind": "skill", "title": "检查 Skill 归属或状态", "detail": f"当前 Agent 的可用 Skill 中未找到 {node.skill_name}。", "action": "open_skills"})
    if error == "agent_task_claim_required":
        suggestions.append({"kind": "agent_task", "title": "认领并完成人工/Agent 任务", "detail": "Agent 任务需要被认领，完成后下游节点才会继续。", "action": "claim_or_complete"})
    if status == NODE_WAITING and node.node_type in {"waiter", "external_callback", "subgraph"}:
        suggestions.append({"kind": "signal", "title": "等待外部信号", "detail": "收到等待条件、子流程结果或外部回调后，用 signal_node 完成该节点。", "action": "signal_node"})
    if "dependencies_not_satisfied" in error:
        suggestions.append({"kind": "dependency", "title": "依赖未满足", "detail": "运行或修复上游节点后重新执行该节点。", "action": "run_dependencies"})
    if status in {NODE_FAILED, NODE_BLOCKED} and not suggestions:
        suggestions.append({"kind": "retry", "title": "重试或复制节点隔离问题", "detail": "查看错误和事件；如果是参数问题，修正 arguments 后保存定义并重新运行。", "action": "inspect_and_retry"})
    return suggestions[:6]


def _node_details(definition: WorkflowDefinition | None, run: WorkflowRun, *, agent_skill_map: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    if definition is None:
        return {}
    agent_skill_map = agent_skill_map or {}
    details: dict[str, Any] = {}
    events_by_node: dict[str, list[dict[str, Any]]] = {}
    envelopes_by_node: dict[str, list[dict[str, Any]]] = {}
    for event in run.events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        node_id = str(payload.get("node_id") or "")
        if not node_id:
            continue
        events_by_node.setdefault(node_id, []).append(event)
        envelope = payload.get("interaction_envelope")
        if isinstance(envelope, dict):
            envelopes_by_node.setdefault(node_id, []).append(dict(envelope))
    for node in definition.nodes:
        state = run.nodes.get(node.node_id)
        deps = []
        for dep in node.depends_on:
            dep_state = run.nodes.get(dep)
            deps.append({"node_id": dep, "status": dep_state.status if dep_state else "missing"})
        effective_agent = (state.assigned_agent if state else "") or node.assigned_agent
        available_skills = agent_skill_map.get(effective_agent, [])
        node_task_queue = _node_task_queue(node.node_id, run, state)[:50]
        details[node.node_id] = {
            "definition": node.snapshot(),
            "state": state.snapshot() if state else None,
            "dependencies": deps,
            "events": events_by_node.get(node.node_id, [])[-20:],
            "interaction_envelopes": envelopes_by_node.get(node.node_id, [])[-20:],
            "effective_agent": effective_agent,
            "available_skills": available_skills[:12],
            "node_task_queue": node_task_queue,
            "node_queue_summary": _node_queue_summary(node_task_queue),
            "agent_task_queue": _agent_task_queue(effective_agent, definition, run)[:20],
            "interface_contract": _node_runtime_interface_contract(node),
            "progress": _node_progress(node.node_id, definition, run),
            "repair_suggestions": _repair_suggestions(node, state, deps, available_skills),
        }
    return details


def _overview(definitions: list[WorkflowDefinition], runs: list[WorkflowRun]) -> dict[str, Any]:
    run_counts = Counter(run.status for run in runs)
    active_count = sum(run_counts.get(status, 0) for status in ("pending", "running", "waiting", "waiting_review", "blocked"))
    return {
        "definition_count": len(definitions),
        "run_count": len(runs),
        "active_run_count": active_count,
        "status_counts": dict(sorted(run_counts.items())),
        "default_workflow_name": DEFAULT_WORKFLOW_NAME,
    }


def _selected_workflow_name(payload: dict[str, Any] | None = None) -> str:
    raw = str((payload or {}).get("workflow_name") or "").strip()
    return raw or DEFAULT_WORKFLOW_NAME


def _definitions_by_name(definitions: list[WorkflowDefinition]) -> dict[str, WorkflowDefinition]:
    return {definition.name: definition for definition in definitions}


def build_workflow_management_snapshot(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project_root = _project_root(payload)
    store = _store(project_root)
    saved_definitions = store.list_definitions()
    runs = store.list_runs()
    builtin_definitions = list(builtin_workflow_definitions())
    definition_map = _definitions_by_name(saved_definitions)
    definition_map.update(_definitions_by_name(builtin_definitions))
    definition_map.update(_definitions_by_name(saved_definitions))
    definitions = list(definition_map.values())
    selected_workflow_name = _selected_workflow_name(payload)
    selected_definition = definition_map.get(selected_workflow_name) or definition_map.get(DEFAULT_WORKFLOW_NAME) or build_ecommerce_auto_listing_definition()
    overview = _overview(saved_definitions, runs)
    overview["builtin_definition_count"] = len(builtin_definitions)
    overview["available_definition_count"] = len(definitions)
    agents_snapshot = _available_agents_snapshot()
    agent_skill_map = _agent_skill_map()
    node_catalog = _workflow_node_catalog_snapshot()
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": project_root,
        "state_dir": str(store.state_dir),
        "node_catalog": node_catalog,
        "overview": overview,
        "selected_workflow_name": selected_definition.name,
        "default_definition": selected_definition.snapshot(),
        "builtin_definitions": [definition.snapshot() for definition in builtin_definitions],
        "definitions": [definition.snapshot() for definition in definitions],
        "saved_definition_names": [definition.name for definition in saved_definitions],
        "available_agents": agents_snapshot.get("available_agents", []),
        "agent_profiles_by_id": agents_snapshot.get("agent_profiles_by_id", {}),
        "definition_versions": store.list_definition_versions(selected_definition.name, limit=20),
        "audit_events": store.list_audit_events(workflow_name=selected_definition.name, limit=30),
        "permission_policy": _permission_policy_snapshot(selected_definition),
        "governance_policy": _governance_policy_snapshot(selected_definition),
        "agent_skill_map": agent_skill_map,
        "runs": [_run_snapshot(store, run, agent_skill_map=agent_skill_map) for run in runs[:50]],
    }


def _permission_policy_snapshot(definition: WorkflowDefinition) -> dict[str, Any]:
    permissions = definition.metadata.get("permissions") if isinstance(definition.metadata, dict) else {}
    permissions = permissions if isinstance(permissions, dict) else {}
    owners = permissions.get("owners") if isinstance(permissions.get("owners"), list) else []
    editors = permissions.get("editors") if isinstance(permissions.get("editors"), list) else []
    approvers = permissions.get("approvers") if isinstance(permissions.get("approvers"), list) else []
    return {
        "mode": str(permissions.get("mode") or "desktop_owner_edit"),
        "owners": [str(item) for item in owners],
        "editors": [str(item) for item in editors],
        "approvers": [str(item) for item in approvers],
        "audit_required": bool(permissions.get("audit_required", True)),
        "rollback_requires_approval": bool(permissions.get("rollback_requires_approval", False)),
    }


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _governance_policy_snapshot(definition: WorkflowDefinition) -> dict[str, Any]:
    governance = definition.metadata.get("governance") if isinstance(definition.metadata, dict) else {}
    governance = governance if isinstance(governance, dict) else {}
    contracts = governance.get("interface_contracts") if isinstance(governance.get("interface_contracts"), dict) else {}
    return {
        "mode": str(governance.get("mode") or "advisory"),
        "forbidden_actions": _as_string_list(governance.get("forbidden_actions")),
        "forbidden_node_types": _as_string_list(governance.get("forbidden_node_types")),
        "required_arguments": governance.get("required_arguments") if isinstance(governance.get("required_arguments"), dict) else {},
        "interface_contracts": dict(contracts),
    }


def _workflow_actor(payload: dict[str, Any]) -> str:
    for key in ("actor", "reviewer", "agent_id", "user_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return "desktop"


def _permission_principals(actor: str) -> set[str]:
    principals = {actor, "*"}
    if actor in {"desktop", "wpf_desktop", "desktop_console"}:
        principals.update({"desktop", "wpf_desktop", "desktop_console"})
    return {item for item in principals if item}


def _policy_allows(policy: dict[str, Any], actor: str, required: str) -> bool:
    mode = str(policy.get("mode") or "").strip().lower()
    if mode in {"open", "public", "unrestricted"}:
        return True
    owners = {str(item) for item in policy.get("owners") or [] if str(item).strip()}
    editors = {str(item) for item in policy.get("editors") or [] if str(item).strip()}
    approvers = {str(item) for item in policy.get("approvers") or [] if str(item).strip()}
    if not owners and not editors and not approvers:
        return True
    principals = _permission_principals(actor)
    if owners & principals:
        return True
    if required == "editor":
        return bool(editors & principals)
    if required == "approver":
        return bool(approvers & principals)
    return False


def _required_workflow_permission(action: str, policy: dict[str, Any]) -> str:
    if action in APPROVER_ACTIONS:
        return "approver"
    if action == "rollback_definition" and bool(policy.get("rollback_requires_approval")):
        return "approver"
    if action in OWNER_ACTIONS:
        return "owner"
    return "editor"


def _workflow_name_for_permission_check(payload: dict[str, Any], action: str, store: JsonWorkflowStore) -> str:
    if action == "upsert_definition" and isinstance(payload.get("definition"), dict):
        return str(payload["definition"].get("name") or payload.get("workflow_name") or DEFAULT_WORKFLOW_NAME)
    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        run = store.load_run(run_id)
        if run is not None:
            return run.workflow_name
    return _selected_workflow_name(payload)


def _definition_for_permission_check(payload: dict[str, Any], action: str, store: JsonWorkflowStore) -> WorkflowDefinition | None:
    workflow_name = _workflow_name_for_permission_check(payload, action, store)
    existing = store.load_definition(workflow_name) or get_builtin_workflow_definition(workflow_name)
    if existing is not None:
        return existing
    if action == "upsert_definition" and isinstance(payload.get("definition"), dict):
        from backend.orchestrator.workflow_store import workflow_definition_from_dict

        return workflow_definition_from_dict(payload["definition"])
    return None


def _deny_workflow_action(store: JsonWorkflowStore, *, workflow_name: str, action: str, actor: str, required: str, policy: dict[str, Any]) -> dict[str, Any]:
    message = f"Workflow action denied: actor {actor} lacks {required} permission for {workflow_name}"
    store.record_audit(
        "permission_denied",
        workflow_name=workflow_name,
        actor=actor,
        message=message,
        payload={"action": action, "required": required, "permission_policy": policy},
    )
    return {
        "success": False,
        "message": message,
        "data": {},
        "error_code": "workflow_permission_denied",
        "metadata": {"workflow_name": workflow_name, "actor": actor, "required": required},
    }


def _authorize_workflow_action(payload: dict[str, Any], action: str) -> dict[str, Any] | None:
    project_root = _project_root(payload)
    store = _store(project_root)
    definition = _definition_for_permission_check(payload, action, store)
    if definition is None:
        return None
    policy = _permission_policy_snapshot(definition)
    actor = _workflow_actor(payload)
    required = _required_workflow_permission(action, policy)
    if _policy_allows(policy, actor, required):
        return None
    return _deny_workflow_action(store, workflow_name=definition.name, action=action, actor=actor, required=required, policy=policy)


def _definition_for_contract_check(payload: dict[str, Any], action: str, store: JsonWorkflowStore) -> WorkflowDefinition | None:
    if action == "upsert_definition" and isinstance(payload.get("definition"), dict):
        from backend.orchestrator.workflow_store import workflow_definition_from_dict

        return workflow_definition_from_dict(payload["definition"])
    return _definition_for_permission_check(payload, action, store)


def _node_has_contract_value(node: Any, key: str) -> bool:
    if key in getattr(node, "arguments", {}):
        value = node.arguments.get(key)
    else:
        value = getattr(node, "metadata", {}).get(key)
    return value not in (None, "", [], {})


def _node_interface_contract(policy: dict[str, Any], node_id: str) -> dict[str, Any]:
    contracts = policy.get("interface_contracts") if isinstance(policy.get("interface_contracts"), dict) else {}
    node_contracts = contracts.get("nodes") if isinstance(contracts.get("nodes"), dict) else contracts
    contract = node_contracts.get(node_id) if isinstance(node_contracts, dict) else {}
    return contract if isinstance(contract, dict) else {}


def _workflow_contract_issues(definition: WorkflowDefinition, *, action: str, payload: dict[str, Any]) -> list[str]:
    policy = _governance_policy_snapshot(definition)
    if policy["mode"] in {"off", "disabled", "open"} and not policy["forbidden_actions"] and not policy["forbidden_node_types"] and not policy["required_arguments"] and not policy["interface_contracts"]:
        return []
    issues: list[str] = []
    forbidden_actions = set(policy["forbidden_actions"])
    if "*" in forbidden_actions or action in forbidden_actions:
        issues.append(f"forbidden_action:{action}")
    forbidden_node_types = set(policy["forbidden_node_types"])
    required_by_type = policy.get("required_arguments") if isinstance(policy.get("required_arguments"), dict) else {}
    for node in definition.nodes:
        if node.node_type in forbidden_node_types:
            issues.append(f"forbidden_node_type:{node.node_id}:{node.node_type}")
        required_arguments = _as_string_list(required_by_type.get(node.node_type) if isinstance(required_by_type, dict) else [])
        required_arguments.extend(_as_string_list(_node_interface_contract(policy, node.node_id).get("required_arguments")))
        for key in dict.fromkeys(required_arguments):
            if not _node_has_contract_value(node, key):
                issues.append(f"missing_required_argument:{node.node_id}:{key}")
    node_id = str(payload.get("node_id") or "").strip()
    if node_id and action in {"complete_agent_task", "signal_node"}:
        contract = _node_interface_contract(policy, node_id)
        required_outputs = _as_string_list(contract.get("required_outputs"))
        output_payload = payload.get("signal_payload") if action == "signal_node" else payload.get("outputs")
        outputs = output_payload if isinstance(output_payload, dict) else {}
        for key in required_outputs:
            if key not in outputs:
                issues.append(f"missing_required_output:{node_id}:{key}")
        if bool(contract.get("requires_artifact_refs")) and not _as_string_list(payload.get("artifact_refs")):
            issues.append(f"missing_artifact_refs:{node_id}")
        if bool(contract.get("requires_knowledge_refs")) and not _as_string_list(payload.get("knowledge_refs")):
            issues.append(f"missing_knowledge_refs:{node_id}")
    return issues


def _deny_workflow_contract(store: JsonWorkflowStore, *, definition: WorkflowDefinition, action: str, actor: str, issues: list[str]) -> dict[str, Any]:
    message = f"Workflow contract violation: {', '.join(issues)}"
    store.record_audit(
        "contract_violation",
        workflow_name=definition.name,
        actor=actor,
        message=message,
        payload={"action": action, "issues": issues, "governance_policy": _governance_policy_snapshot(definition)},
    )
    return {
        "success": False,
        "message": message,
        "data": {},
        "error_code": "workflow_contract_violation",
        "metadata": {"workflow_name": definition.name, "actor": actor, "issues": issues},
    }


def _enforce_workflow_contract(payload: dict[str, Any], action: str) -> dict[str, Any] | None:
    if action in {"delete_definition", "rollback_definition"}:
        return None
    project_root = _project_root(payload)
    store = _store(project_root)
    definition = _definition_for_contract_check(payload, action, store)
    if definition is None:
        return None
    issues = _workflow_contract_issues(definition, action=action, payload=payload)
    if not issues:
        return None
    return _deny_workflow_contract(store, definition=definition, action=action, actor=_workflow_actor(payload), issues=issues)


def _available_agents_snapshot() -> dict[str, Any]:
    try:
        snapshot = build_agent_management_desktop_snapshot()
    except Exception:
        return {"available_agents": [], "agent_profiles_by_id": {}}
    agents = [dict(item) for item in snapshot.get("agents") or [] if isinstance(item, dict)]
    enabled = [agent for agent in agents if bool(agent.get("enabled", True))]
    return {
        "available_agents": [
            {
                "agent_id": str(agent.get("agent_id") or ""),
                "label": str(agent.get("label") or agent.get("agent_id") or ""),
                "domain": str(agent.get("domain") or ""),
                "role": str(agent.get("role") or ""),
                "provider": str(agent.get("provider") or ""),
                "model": str(agent.get("model") or ""),
                "enabled": bool(agent.get("enabled", True)),
            }
            for agent in sorted(enabled, key=lambda item: (-int(item.get("priority") or 0), str(item.get("agent_id") or "")))
            if str(agent.get("agent_id") or "")
        ],
        "agent_profiles_by_id": snapshot.get("agent_profiles_by_id") or {agent.get("agent_id"): agent for agent in agents if agent.get("agent_id")},
    }


def _agent_skill_map() -> dict[str, list[dict[str, Any]]]:
    try:
        snapshot = build_desktop_skills_snapshot()
    except Exception:
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for skill in snapshot.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        owner = str(skill.get("owner_agent_id") or "")
        status = str(skill.get("status") or "draft")
        if not owner or status not in {"active", "candidate"}:
            continue
        result.setdefault(owner, []).append(
            {
                "name": str(skill.get("name") or ""),
                "status": status,
                "risk_level": str(skill.get("risk_level") or ""),
                "trigger_intents": list(skill.get("trigger_intents") or [])[:6],
                "tool_allowlist": list(skill.get("tool_allowlist") or [])[:6],
                "promotion_status": str(skill.get("promotion_status") or ""),
            }
        )
    for owner, skills in result.items():
        result[owner] = sorted(skills, key=lambda item: (item.get("status") != "active", item.get("name") or ""))
    return result


def _workflow_node_catalog_snapshot() -> dict[str, Any]:
    try:
        registry = build_default_tool_registry()
        specs = registry.list_specs() if hasattr(registry, "list_specs") else []
        return workflow_node_catalog(specs)
    except Exception as exc:
        return {
            "schema_version": "spiritkin.workflow_node_catalog.v1",
            "node_types": [],
            "compatibility_matrix": {},
            "port_kinds": ["*", "execution", "artifact", "knowledge", "signal", "review", "automation", "control"],
            "catalog": [],
            "counts": {"builtins": 0, "tools": 0, "skills": 0},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _invoke_workflow_tool(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_root = _project_root(payload)
    registry = build_default_tool_registry()
    arguments = dict(payload)
    arguments["project_root"] = project_root
    result = registry.invoke(ToolCall(tool_name, arguments))
    return {
        "success": result.success,
        "message": result.message,
        "data": result.data,
        "error_code": result.error_code,
        "metadata": result.metadata,
    }


def _trace_replay_action_result(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = _project_root(payload)
    store = _store(project_root)
    run_id = str(payload.get("run_id") or "").strip()
    run = store.load_run(run_id) if run_id else None
    if run is None and not run_id:
        runs = store.list_runs(workflow_name=_selected_workflow_name(payload))
        run = runs[0] if runs else None
    if run is None:
        return {
            "success": False,
            "message": "Workflow run not found for trace replay",
            "data": {},
            "error_code": "workflow_run_not_found",
            "metadata": {"run_id": run_id},
        }
    definition = _definition_for_run(store, run)
    replay = _trace_replay_snapshot(definition, run)
    return {
        "success": True,
        "message": f"Trace replay ready: {run.run_id}",
        "data": {"trace_replay": replay, "run": run.snapshot(), "definition": definition.snapshot() if definition else None},
        "error_code": "",
        "metadata": {"run_id": run.run_id, "workflow_name": run.workflow_name},
    }


def handle_workflow_management_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    tool_by_action = {
        "save_ecommerce_definition": "workflow.graph.save_ecommerce_definition",
        "save_builtin_definition": "workflow.graph.save_builtin_definition",
        "schema": "workflow.graph.schema",
        "list_node_catalog": "workflow.graph.list_node_catalog",
        "start_run": "workflow.graph.start_run",
        "run_next": "workflow.graph.run_next",
        "run_node": "workflow.graph.run_node",
        "auto_advance_runs": "workflow.graph.auto_advance_runs",
        "retry_node": "workflow.graph.retry_node",
        "reset_run": "workflow.graph.reset_run",
        "approve_review": "workflow.graph.approve_review",
        "assign_agent": "workflow.graph.assign_agent",
        "claim_agent_task": "workflow.graph.claim_agent_task",
        "complete_agent_task": "workflow.graph.complete_agent_task",
        "signal_node": "workflow.graph.signal_node",
        "upsert_definition": "workflow.graph.upsert_definition",
        "compose_definition": "workflow.graph.compose_definition",
        "delete_definition": "workflow.graph.delete_definition",
        "rollback_definition": "workflow.graph.rollback_definition",
    }
    if action in {"snapshot", "refresh"}:
        return {"ok": True, "workflows": build_workflow_management_snapshot(payload)}
    if action in {"trace_replay", "replay_run"}:
        action_result = _trace_replay_action_result(payload)
        snapshot_payload = dict(payload)
        workflow_name = str(action_result.get("metadata", {}).get("workflow_name") or "")
        if workflow_name:
            snapshot_payload["workflow_name"] = workflow_name
        return {
            "ok": bool(action_result.get("success")),
            "action": action,
            "action_result": action_result,
            "trace_replay": action_result.get("data", {}).get("trace_replay", {}),
            "workflows": build_workflow_management_snapshot(snapshot_payload),
        }
    if action in RUN_MANAGEMENT_ACTIONS:
        store = _store(_project_root(payload))
        workflow_name = _selected_workflow_name(payload)
        action_result = handle_workflow_run_management_action(store, payload, action, actor=_workflow_actor(payload), workflow_name=workflow_name)
        snapshot_payload = dict(payload)
        result_workflow_name = str(action_result.get("metadata", {}).get("workflow_name") or workflow_name)
        if result_workflow_name:
            snapshot_payload["workflow_name"] = result_workflow_name
        return {
            "ok": bool(action_result.get("success")),
            "action": action,
            "action_result": action_result,
            "workflows": build_workflow_management_snapshot(snapshot_payload),
        }
    if action not in tool_by_action:
        raise ValueError(f"unsupported workflow action: {action}")

    action_result = _authorize_workflow_action(payload, action) or _enforce_workflow_contract(payload, action) or _invoke_workflow_tool(tool_by_action[action], payload)
    return {
        "ok": bool(action_result.get("success")),
        "action": action,
        "action_result": action_result,
        "workflows": build_workflow_management_snapshot(payload),
    }
