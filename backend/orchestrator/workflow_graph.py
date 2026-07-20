from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from backend.code_jury import evaluate_jury_gate
from backend.security.safety_control import evaluate_execution_safety
from backend.tools.base import ToolCall, ToolResult, ToolSpec

if TYPE_CHECKING:
    from backend.orchestrator.worker_pool import WorkerPool
    from backend.skills.base import SkillRunner
    from backend.tools.registry import ToolRegistry


_PROJECT_ROOT_POSIX = Path(__file__).resolve().parents[2].as_posix()

NODE_PENDING = "pending"
NODE_RUNNING = "running"
NODE_SUCCEEDED = "succeeded"
NODE_FAILED = "failed"
NODE_BLOCKED = "blocked"
NODE_WAITING = "waiting"
NODE_WAITING_REVIEW = "waiting_review"
NODE_SKIPPED = "skipped"

RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_BLOCKED = "blocked"
RUN_WAITING = "waiting"
RUN_WAITING_REVIEW = "waiting_review"

SUPPORTED_NODE_TYPES = {
    "tool_call",
    "skill_call",
    "agent_task",
    "review_gate",
    "branch",
    "subgraph",
    "foreach",
    "waiter",
    "external_callback",
    "workflow.android_step",
}

SUCCESSFUL_DEPENDENCY_STATUSES = {NODE_SUCCEEDED, NODE_SKIPPED}
TERMINAL_NODE_STATUSES = {NODE_SUCCEEDED, NODE_FAILED, NODE_BLOCKED, NODE_SKIPPED}
TEMPLATE_REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def is_open_workflow_node_type(node_type: str) -> bool:
    normalized = (node_type or "").strip().lower()
    return normalized.startswith(("custom.", "external.", "integration.", "automation."))


def is_android_workflow_node_type(node_type: str) -> bool:
    return (node_type or "").strip().lower() in {"workflow.android_step", "automation.android_step"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class WorkflowNodeDefinition:
    node_id: str
    node_type: str
    label: str = ""
    tool_name: str = ""
    skill_name: str = ""
    assigned_agent: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    review_gate: str = ""
    retry_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "label": self.label,
            "tool_name": self.tool_name,
            "skill_name": self.skill_name,
            "assigned_agent": self.assigned_agent,
            "arguments": dict(self.arguments),
            "depends_on": list(self.depends_on),
            "review_gate": self.review_gate,
            "retry_policy": dict(self.retry_policy),
            "metadata": dict(self.metadata),
        }


def _node_config_value(node: WorkflowNodeDefinition, key: str, default: Any = "") -> Any:
    if key in node.arguments:
        return node.arguments.get(key)
    if key in node.metadata:
        return node.metadata.get(key)
    return default


def _node_worker_needs(node: WorkflowNodeDefinition, arguments: dict[str, Any] | None = None) -> list[str]:
    arguments = dict(arguments or {})
    raw = arguments.get("needs")
    if raw is None:
        raw = node.metadata.get("needs")
    if raw is None:
        raw = node.arguments.get("needs")
    needs: list[str] = []
    if isinstance(raw, str):
        needs.append(raw)
    elif isinstance(raw, (list, tuple)):
        needs.extend(str(item) for item in raw if str(item).strip())
    if is_android_workflow_node_type(node.node_type):
        operation = str(arguments.get("operation") or arguments.get("android_operation") or _node_config_value(node, "operation") or "").strip()
        for value in ("android", operation):
            if value and value not in needs:
                needs.append(value)
        if operation.startswith("pdd.") and "pdd" not in needs:
            needs.append("pdd")
        if operation.startswith("android.screenshot") and "android.screenshot" not in needs:
            needs.append("android.screenshot")
        if operation.startswith("android.ui") and "android.ui" not in needs:
            needs.append("android.ui")
    return list(dict.fromkeys(needs))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "ready", "received", "succeeded", "success"}


def _execution_request_snapshot(request: Any) -> dict[str, Any]:
    if request is None:
        return {}
    return {
        "target": str(getattr(request, "target", "") or ""),
        "operation": str(getattr(request, "operation", "") or ""),
        "params": dict(getattr(request, "params", {}) or {}),
    }


def _workflow_jury_required(node: WorkflowNodeDefinition, review_payload: dict[str, Any]) -> bool:
    if "jury_required" in review_payload:
        return _truthy(review_payload.get("jury_required"))
    gate = review_payload.get("jury_gate") if isinstance(review_payload.get("jury_gate"), dict) else {}
    if "required" in gate:
        return _truthy(gate.get("required"))
    for source in (node.metadata, node.arguments):
        for key in ("jury_required", "requires_jury", "code_jury_required"):
            if key in source:
                return _truthy(source.get(key))
    review_gate = str(node.review_gate or node.metadata.get("review_gate") or "").lower()
    return "jury" in review_gate or "code_review" in review_gate or "ui_review" in review_gate


def _workflow_jury_review_type(node: WorkflowNodeDefinition) -> str:
    explicit = str(node.metadata.get("jury_review_type") or node.arguments.get("jury_review_type") or "").strip().lower()
    if explicit in {"code", "ui", "pr"}:
        return explicit
    review_gate = str(node.review_gate or "").lower()
    text = " ".join([node.node_id, node.label, review_gate, str(node.metadata), str(node.arguments)]).lower()
    if "ui" in text or "screenshot" in text or "截图" in text or "界面" in text:
        return "ui"
    if "pr" in text or "pull_request" in text:
        return "pr"
    return "code"


def _review_payload_outputs(review_payload: dict[str, Any]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for key in ("jury_gate", "jury_report_id", "review_note"):
        if key in review_payload:
            outputs[key] = review_payload.get(key)
    report = review_payload.get("jury_report") if isinstance(review_payload.get("jury_report"), dict) else {}
    if report and "jury_report_id" not in outputs:
        outputs["jury_report_id"] = str(report.get("report_id") or "")
    return outputs


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    version: str = "0.1.0"
    description: str = ""
    nodes: tuple[WorkflowNodeDefinition, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "nodes": [node.snapshot() for node in self.nodes],
            "metadata": dict(self.metadata),
        }

    def validate(self) -> list[str]:
        issues: list[str] = []
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            issues.append("duplicate_node_id")
        node_id_set = set(node_ids)
        dependencies_by_node = {node.node_id: tuple(node.depends_on) for node in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in node_id_set:
                    issues.append(f"missing_dependency:{node.node_id}:{dep}")
            if node.node_type == "tool_call" and not node.tool_name:
                issues.append(f"missing_tool_name:{node.node_id}")
            if node.node_type == "skill_call" and not node.skill_name:
                issues.append(f"missing_skill_name:{node.node_id}")
            if node.node_type == "subgraph" and not _node_config_value(node, "workflow_name"):
                issues.append(f"missing_subgraph_workflow:{node.node_id}")
            if node.node_type == "foreach" and _node_config_value(node, "max_iterations", 20):
                try:
                    if int(_node_config_value(node, "max_iterations", 20)) < 1:
                        issues.append(f"invalid_foreach_max_iterations:{node.node_id}")
                except (TypeError, ValueError):
                    issues.append(f"invalid_foreach_max_iterations:{node.node_id}")
            if node.node_type == "external_callback" and not (_node_config_value(node, "callback_id") or _node_config_value(node, "callback_url")):
                issues.append(f"missing_callback_reference:{node.node_id}")
            if node.node_type == "waiter" and not (_node_config_value(node, "wait_for") or _node_config_value(node, "signal")):
                issues.append(f"missing_wait_condition:{node.node_id}")
            if is_android_workflow_node_type(node.node_type) and not (_node_config_value(node, "operation") or _node_config_value(node, "android_operation")):
                issues.append(f"missing_android_operation:{node.node_id}")
            if node.node_type not in SUPPORTED_NODE_TYPES and not is_open_workflow_node_type(node.node_type):
                issues.append(f"unsupported_node_type:{node.node_id}:{node.node_type}")
            for issue in _validate_node_ports(node):
                issues.append(f"{issue}:{node.node_id}")
        for node in self.nodes:
            target_input_kind = _node_input_port_kind(node)
            for dep in node.depends_on:
                source = next((item for item in self.nodes if item.node_id == dep), None)
                if source is not None and not _are_ports_compatible(_node_output_port_kind(source), target_input_kind, source.node_type, node.node_type):
                    issues.append(f"incompatible_ports:{dep}->{node.node_id}:{_node_output_port_kind(source)}->{target_input_kind}")
        cycle = _find_dependency_cycle(dependencies_by_node)
        if cycle:
            issues.append(f"dependency_cycle:{'->'.join(cycle)}")
        return issues


@dataclass(frozen=True)
class WorkflowNodeRun:
    node_id: str
    status: str = NODE_PENDING
    attempts: int = 0
    started_at: str = ""
    finished_at: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    assigned_agent: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "outputs": dict(self.outputs),
            "error": self.error,
            "assigned_agent": self.assigned_agent,
        }


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    workflow_name: str
    workflow_version: str
    status: str = RUN_PENDING
    inputs: dict[str, Any] = field(default_factory=dict)
    nodes: dict[str, WorkflowNodeRun] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "status": self.status,
            "inputs": dict(self.inputs),
            "nodes": {node_id: node.snapshot() for node_id, node in self.nodes.items()},
            "artifacts": list(self.artifacts),
            "events": list(self.events),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _ref_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    refs: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            refs.append(dict(item))
        elif str(item).strip():
            refs.append(str(item))
    return refs


def build_agent_interaction_envelope(
    run: WorkflowRun,
    node_id: str,
    *,
    agent_id: str = "",
    artifact_refs: list[Any] | None = None,
    knowledge_refs: list[Any] | None = None,
    audit_event_id: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "spiritkin.agent_interaction.v1",
        "run_id": run.run_id,
        "workflow_name": run.workflow_name,
        "workflow_version": run.workflow_version,
        "node_id": node_id,
        "agent_id": agent_id,
        "artifact_refs": _ref_list(artifact_refs or []),
        "knowledge_refs": _ref_list(knowledge_refs or []),
        "audit_event_id": audit_event_id,
        "payload": dict(payload or {}),
    }


def start_workflow_run(definition: WorkflowDefinition, inputs: dict[str, Any] | None = None, *, run_id: str = "") -> WorkflowRun:
    issues = definition.validate()
    start_inputs = dict(inputs or {})
    issues.extend(_missing_required_start_inputs(definition, start_inputs))
    if issues:
        raise ValueError(f"invalid workflow definition: {', '.join(issues)}")
    resolved_run_id = run_id or f"wfr_{uuid4().hex[:12]}"
    nodes = {
        node.node_id: WorkflowNodeRun(node_id=node.node_id, assigned_agent=node.assigned_agent)
        for node in definition.nodes
    }
    return WorkflowRun(
        run_id=resolved_run_id,
        workflow_name=definition.name,
        workflow_version=definition.version,
        inputs=start_inputs,
        nodes=nodes,
        events=[{"at": utc_now(), "type": "run_started", "payload": {"workflow": definition.name}}],
    )


def _interface_inputs(node: WorkflowNodeDefinition) -> list[dict[str, Any]]:
    contract = node.metadata.get("interface_contract") if isinstance(node.metadata.get("interface_contract"), dict) else {}
    raw = contract.get("inputs")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _missing_required_start_inputs(definition: WorkflowDefinition, inputs: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for node in definition.nodes:
        if node.depends_on:
            continue
        for item in _interface_inputs(node):
            if not _truthy(item.get("required")):
                continue
            name = str(item.get("name") or "").strip()
            if name and _node_arguments_reference_input(node.arguments, name) and _is_missing_value(inputs.get(name)):
                issues.append(f"missing_required_input:{node.node_id}:{name}")
    return issues


def _node_arguments_reference_input(value: Any, name: str) -> bool:
    if isinstance(value, str):
        for match in TEMPLATE_REF_RE.finditer(value):
            key = match.group(1).strip()
            if key == name or key == f"input.{name}":
                return True
        return False
    if isinstance(value, dict):
        return any(_node_arguments_reference_input(item, name) for item in value.values())
    if isinstance(value, list):
        return any(_node_arguments_reference_input(item, name) for item in value)
    return False


def _is_missing_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _port_kind_tokens(port_kind: str) -> set[str]:
    tokens = {
        item.strip().lower()
        for item in str(port_kind or "execution").replace(",", "|").replace(";", "|").replace(" ", "|").split("|")
        if item.strip()
    }
    return tokens or {"execution"}


def _node_ports(node: WorkflowNodeDefinition) -> list[dict[str, Any]]:
    raw = node.metadata.get("ports")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _node_input_port_kind(node: WorkflowNodeDefinition) -> str:
    ports = _node_ports(node)
    for port in ports:
        if str(port.get("direction") or "").strip().lower() == "input":
            return str(port.get("kind") or "execution")
    return _node_port_defaults(node.node_type)[0]


def _node_output_port_kind(node: WorkflowNodeDefinition) -> str:
    ports = _node_ports(node)
    for port in ports:
        if str(port.get("direction") or "").strip().lower() == "output":
            return str(port.get("kind") or "execution")
    return _node_port_defaults(node.node_type)[1]


def _normalize_workflow_node_type(node_type: str) -> str:
    normalized = (node_type or "").strip().lower()
    if is_open_workflow_node_type(normalized):
        return "other"
    return normalized if normalized in SUPPORTED_NODE_TYPES or is_android_workflow_node_type(normalized) else "other"


def _are_node_types_compatible(source_node_type: str, target_node_type: str) -> bool:
    source = _normalize_workflow_node_type(source_node_type)
    target = _normalize_workflow_node_type(target_node_type)
    if source == "other" or target == "other":
        return True
    if source == "review_gate" and target == "review_gate":
        return False
    return target in {
        "agent_task",
        "tool_call",
        "skill_call",
        "review_gate",
        "branch",
        "subgraph",
        "foreach",
        "waiter",
        "external_callback",
        "workflow.android_step",
        "automation.android_step",
    }


def _are_ports_compatible(source_output_kind: str, target_input_kind: str, source_node_type: str, target_node_type: str) -> bool:
    if not _are_node_types_compatible(source_node_type, target_node_type):
        return False
    emitted = _port_kind_tokens(source_output_kind)
    accepted = _port_kind_tokens(target_input_kind)
    return "*" in emitted or "*" in accepted or bool(emitted & accepted)


def _validate_node_ports(node: WorkflowNodeDefinition) -> list[str]:
    issues: list[str] = []
    for port in _node_ports(node):
        direction = str(port.get("direction") or "").strip().lower()
        if direction not in {"input", "output"}:
            issues.append("invalid_port_direction")
    return issues


def _find_dependency_cycle(dependencies_by_node: dict[str, tuple[str, ...]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node_id: str) -> list[str]:
        if node_id in visited or node_id not in dependencies_by_node:
            return []
        if node_id in visiting:
            try:
                start = stack.index(node_id)
            except ValueError:
                start = 0
            return [*stack[start:], node_id]
        visiting.add(node_id)
        stack.append(node_id)
        for dependency in dependencies_by_node.get(node_id, ()):
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)
        return []

    for node_id in dependencies_by_node:
        cycle = visit(node_id)
        if cycle:
            return cycle
    return []


class WorkflowRunner:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        skill_runner: SkillRunner | None = None,
        worker_pool: WorkerPool | None = None,
        state_sink: Callable[[WorkflowRun], None] | None = None,
    ):
        self._tool_registry = tool_registry
        self._skill_runner = skill_runner
        self._worker_pool = worker_pool
        self._state_sink = state_sink

    def runnable_nodes(self, definition: WorkflowDefinition, run: WorkflowRun) -> list[WorkflowNodeDefinition]:
        runnable: list[WorkflowNodeDefinition] = []
        for node in definition.nodes:
            state = run.nodes.get(node.node_id)
            if state is None or state.status != NODE_PENDING:
                continue
            if not self._pending_retry_ready(state):
                continue
            if self._inactive_branch_ancestor(definition, run, node.node_id) is not None:
                continue
            deps_done = all(self._dependency_satisfied(run, dep) for dep in node.depends_on)
            if deps_done:
                runnable.append(node)
        return runnable

    def run_next(self, definition: WorkflowDefinition, run: WorkflowRun, *, dry_run: bool = False) -> WorkflowRun:
        safety = evaluate_execution_safety(
            target="workflow",
            operation="run_next",
            actor=str(run.inputs.get("actor") or ""),
            dry_run=dry_run,
        )
        if not safety.allowed:
            return self._blocked_by_safety(run, safety.snapshot())
        run = self.apply_branch_skips(definition, run)
        runnable = self.runnable_nodes(definition, run)
        if not runnable:
            return self._refresh_run_status(definition, run)
        return self.run_node(definition, run, runnable[0].node_id, dry_run=dry_run)

    def run_node(self, definition: WorkflowDefinition, run: WorkflowRun, node_id: str, *, dry_run: bool = False) -> WorkflowRun:
        safety = evaluate_execution_safety(
            target="workflow",
            operation=f"run_node:{node_id}",
            actor=str(run.inputs.get("actor") or ""),
            dry_run=dry_run,
        )
        if not safety.allowed:
            return self._blocked_by_safety(run, safety.snapshot())
        node = next((item for item in definition.nodes if item.node_id == node_id), None)
        if node is None:
            raise KeyError(f"unknown workflow node: {node_id}")
        run = self.apply_branch_skips(definition, run)
        current = run.nodes[node_id]
        if current.status == NODE_FAILED and self._retry_allowed(node, current):
            run = self._manual_retry_node(definition, run, node, current)
            current = run.nodes[node_id]
        if current.status != NODE_PENDING:
            return run
        if self._inactive_branch_ancestor(definition, run, node_id) is not None:
            return self.apply_branch_skips(definition, run)
        if not all(self._dependency_satisfied(run, dep) for dep in node.depends_on):
            return self._update_node(run, current, status=NODE_BLOCKED, error="dependencies_not_satisfied")
        if not self._pending_retry_ready(current):
            return self._refresh_run_status(definition, run)

        started = replace(current, status=NODE_RUNNING, attempts=current.attempts + 1, started_at=utc_now(), error="")
        run = self._replace_node(run, started, status=RUN_RUNNING, event_type="node_started", payload={"node_id": node_id, "dry_run": dry_run})
        if not dry_run and self._state_sink is not None:
            self._state_sink(run)
        arguments = self._resolve_arguments(node.arguments, run.inputs, run.nodes)
        worker_requirement = self._worker_requirement_for_node(node, arguments)
        worker_schedule = self._schedule_worker(worker_requirement)
        worker_binding = self._worker_binding_for_schedule(worker_schedule)
        if worker_schedule is not None and worker_schedule.get("enforced") and worker_schedule.get("status") != "selected":
            blocked = replace(
                started,
                status=NODE_BLOCKED,
                finished_at=utc_now(),
                outputs={"arguments": arguments, "worker_requirement": worker_requirement, "worker_schedule": worker_schedule, "worker_binding": worker_binding or {}},
                error=str(worker_schedule.get("reason") or "worker_not_found"),
            )
            return self._replace_node(
                run,
                blocked,
                status=RUN_BLOCKED,
                event_type="worker_schedule_blocked",
                payload={"node_id": node_id, "worker_schedule": worker_schedule},
            )
        if dry_run:
            finished = replace(
                started,
                status=NODE_SUCCEEDED,
                finished_at=utc_now(),
                outputs={"dry_run": True, "planned": node.snapshot(), "arguments": arguments, "worker_requirement": worker_requirement, "worker_schedule": worker_schedule or {}, "worker_binding": worker_binding or {}},
            )
            return self._replace_node(run, finished, event_type="node_succeeded", payload={"node_id": node_id, "dry_run": True})

        if node.node_type == "tool_call":
            bound_arguments = self._arguments_with_worker_binding(arguments, worker_binding)
            result = self._tool_registry.invoke(ToolCall(node.tool_name, bound_arguments))
            return self._apply_tool_result(definition, run, started, result, worker_schedule=worker_schedule, worker_binding=worker_binding)
        if node.node_type == "skill_call":
            if self._skill_runner is None:
                result = ToolResult(False, "SkillRunner 未配置", error_code="skill_runner_missing")
            else:
                skill_result = self._skill_runner.run(node.skill_name, arguments, dry_run=False)
                result = ToolResult(skill_result.success, skill_result.message, data={"step_count": len(skill_result.step_results)}, metadata=skill_result.metadata)
            return self._apply_tool_result(definition, run, started, result, worker_schedule=worker_schedule, worker_binding=worker_binding)
        if node.node_type == "review_gate":
            waiting = replace(started, status=NODE_WAITING_REVIEW, finished_at=utc_now(), outputs={"review_gate": node.review_gate or "manual_review", "worker_schedule": worker_schedule or {}})
            return self._replace_node(run, waiting, status=RUN_WAITING_REVIEW, event_type="node_waiting_review", payload={"node_id": node_id, "review_gate": node.review_gate})
        if node.node_type == "agent_task":
            blocked = replace(started, status=NODE_BLOCKED, finished_at=utc_now(), error="agent_task_claim_required", outputs={"assigned_agent": node.assigned_agent, "worker_schedule": worker_schedule or {}})
            return self._replace_node(run, blocked, status=RUN_BLOCKED, event_type="node_blocked", payload={"node_id": node_id, "assigned_agent": node.assigned_agent})
        if node.node_type == "branch":
            selected = self._resolve_branch_selection(node, arguments)
            finished = replace(started, status=NODE_SUCCEEDED, finished_at=utc_now(), outputs={**selected, "worker_schedule": worker_schedule or {}})
            return self._refresh_run_status(
                definition,
                self.apply_branch_skips(
                    definition,
                    self._replace_node(run, finished, event_type="branch_selected", payload={"node_id": node_id, **selected}),
                ),
            )
        if node.node_type == "subgraph":
            workflow_name = str(arguments.get("workflow_name") or _node_config_value(node, "workflow_name") or "")
            waiting = replace(
                started,
                status=NODE_WAITING,
                finished_at="",
                outputs={"workflow_name": workflow_name, "inputs": arguments.get("inputs") if isinstance(arguments.get("inputs"), dict) else {}, "status": "subgraph_pending", "worker_schedule": worker_schedule or {}},
            )
            return self._replace_node(run, waiting, status=RUN_WAITING, event_type="subgraph_requested", payload={"node_id": node_id, "workflow_name": workflow_name})
        if node.node_type == "foreach":
            items = arguments.get("items")
            if items is None:
                items = arguments.get("array", [])
            if not isinstance(items, list):
                items = []
            max_iterations = self._foreach_max_iterations(node, arguments)
            selected_items = items[:max_iterations]
            iterations = [
                {
                    "index": index,
                    "item": item,
                    "input": {
                        str(arguments.get("item_key") or "item"): item,
                        "index": index,
                    },
                }
                for index, item in enumerate(selected_items)
            ]
            finished = replace(
                started,
                status=NODE_SUCCEEDED,
                finished_at=utc_now(),
                outputs={
                    "items": selected_items,
                    "iterations": iterations,
                    "count": len(selected_items),
                    "truncated": len(items) > len(selected_items),
                    "max_iterations": max_iterations,
                    "worker_schedule": worker_schedule or {},
                },
            )
            return self._refresh_run_status(definition, self._replace_node(run, finished, event_type="foreach_completed", payload={"node_id": node_id, "count": len(selected_items)}))
        if node.node_type == "waiter":
            if _truthy(arguments.get("ready") or arguments.get("received")):
                finished = replace(started, status=NODE_SUCCEEDED, finished_at=utc_now(), outputs={"wait_for": arguments.get("wait_for") or _node_config_value(node, "wait_for"), "released": True, "worker_schedule": worker_schedule or {}})
                return self._refresh_run_status(definition, self._replace_node(run, finished, event_type="waiter_released", payload={"node_id": node_id}))
            waiting = replace(
                started,
                status=NODE_WAITING,
                finished_at="",
                outputs={"wait_for": arguments.get("wait_for") or _node_config_value(node, "wait_for"), "signal": arguments.get("signal") or _node_config_value(node, "signal"), "status": "waiting", "worker_schedule": worker_schedule or {}},
            )
            return self._replace_node(run, waiting, status=RUN_WAITING, event_type="waiter_pending", payload={"node_id": node_id, "wait_for": waiting.outputs.get("wait_for")})
        if node.node_type == "external_callback":
            callback_payload = arguments.get("callback_payload") if isinstance(arguments.get("callback_payload"), dict) else {}
            if _truthy(arguments.get("received")) or callback_payload:
                finished = replace(started, status=NODE_SUCCEEDED, finished_at=utc_now(), outputs={"callback_payload": callback_payload, "received": True, "worker_schedule": worker_schedule or {}})
                return self._refresh_run_status(definition, self._replace_node(run, finished, event_type="external_callback_received", payload={"node_id": node_id}))
            callback_id = str(arguments.get("callback_id") or _node_config_value(node, "callback_id") or "")
            callback_url = str(arguments.get("callback_url") or _node_config_value(node, "callback_url") or "")
            waiting = replace(started, status=NODE_WAITING, finished_at="", outputs={"callback_id": callback_id, "callback_url": callback_url, "status": "callback_pending", "worker_schedule": worker_schedule or {}})
            return self._replace_node(run, waiting, status=RUN_WAITING, event_type="external_callback_pending", payload={"node_id": node_id, "callback_id": callback_id})
        if is_android_workflow_node_type(node.node_type):
            operation = str(arguments.get("operation") or arguments.get("android_operation") or _node_config_value(node, "operation") or "").strip()
            device_id = str(arguments.get("device_id") or _node_config_value(node, "device_id") or "android_device").strip() or "android_device"
            params = arguments.get("params") if isinstance(arguments.get("params"), dict) else {}
            waiting = replace(
                started,
                status=NODE_WAITING,
                finished_at="",
                outputs={
                    "node_type": node.node_type,
                    "device_id": device_id,
                    "operation": operation,
                    "params": dict(params),
                    "status": "android_step_pending",
                    "worker_requirement": worker_requirement,
                    "worker_schedule": worker_schedule or {},
                },
            )
            return self._replace_node(run, waiting, status=RUN_WAITING, event_type="android_step_pending", payload={"node_id": node_id, "device_id": device_id, "operation": operation})
        if is_open_workflow_node_type(node.node_type):
            return self._run_open_node(definition, run, started, node, arguments, worker_schedule=worker_schedule, worker_binding=worker_binding)
        return self._finish_failed_node(definition, run, started, error=f"unsupported_node_type:{node.node_type}", outputs={"arguments": arguments})

    def approve_review_node(self, definition: WorkflowDefinition, run: WorkflowRun, node_id: str, *, reviewer: str = "human", review_payload: dict[str, Any] | None = None) -> WorkflowRun:
        current = run.nodes[node_id]
        if current.status != NODE_WAITING_REVIEW:
            return run
        node = next((item for item in definition.nodes if item.node_id == node_id), None)
        review_payload = dict(review_payload or {})
        if node is not None and _workflow_jury_required(node, review_payload):
            jury_gate = evaluate_jury_gate(
                review_payload,
                "workflow.review_gate.jury",
                subject=f"{definition.name}:{node_id}",
                default_required=True,
                default_review_type=_workflow_jury_review_type(node),
            )
            if not jury_gate.allowed:
                blocked = replace(current, outputs={**current.outputs, "jury_gate": jury_gate.snapshot()}, error=jury_gate.reason)
                return self._replace_node(run, blocked, status=RUN_WAITING_REVIEW, event_type="node_review_blocked", payload={"node_id": node_id, "reviewer": reviewer, "jury_gate": jury_gate.snapshot()})
            review_payload["jury_gate"] = jury_gate.snapshot()
        approved = replace(current, status=NODE_SUCCEEDED, finished_at=utc_now(), outputs={**current.outputs, "approved_by": reviewer, "approved_at": utc_now(), **_review_payload_outputs(review_payload)})
        return self._refresh_run_status(definition, self._replace_node(run, approved, event_type="node_review_approved", payload={"node_id": node_id, "reviewer": reviewer}))

    def _worker_requirement_for_node(self, node: WorkflowNodeDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        operation = str(arguments.get("operation") or arguments.get("android_operation") or _node_config_value(node, "operation") or "").strip()
        if is_android_workflow_node_type(node.node_type):
            return {
                "needs": _node_worker_needs(node, arguments),
                "worker_type": "device_worker",
                "worker_subtype": "android_device_worker",
                "target": "android_device",
                "operation": operation,
            }
        needs = _node_worker_needs(node, arguments)
        if not needs:
            return {}
        return {
            "needs": needs,
            "worker_type": str(arguments.get("worker_type") or node.metadata.get("worker_type") or "").strip(),
            "worker_subtype": str(arguments.get("worker_subtype") or node.metadata.get("worker_subtype") or "").strip(),
            "target": str(arguments.get("target") or node.metadata.get("target") or "").strip(),
            "operation": str(arguments.get("operation") or node.metadata.get("operation") or "").strip(),
            "workspace": str(arguments.get("workspace") or node.metadata.get("workspace") or "").strip(),
            "permission_scope": str(arguments.get("permission_scope") or node.metadata.get("permission_scope") or "").strip(),
            "prefer_remote": _truthy(arguments.get("prefer_remote") or node.metadata.get("prefer_remote")),
        }

    def _schedule_worker(self, worker_requirement: dict[str, Any]) -> dict[str, Any] | None:
        if not worker_requirement or not worker_requirement.get("needs"):
            return None
        worker_pool = self._worker_pool
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

    def _worker_binding_for_schedule(self, worker_schedule: dict[str, Any] | None) -> dict[str, Any]:
        selected = (worker_schedule or {}).get("selected")
        if not isinstance(selected, dict):
            return {}
        namespaces = {str(item).strip().lower() for item in selected.get("capability_namespaces") or [] if str(item).strip()}
        targets = {str(item).strip().lower() for item in selected.get("targets") or [] if str(item).strip()}
        operations = {str(item).strip().lower() for item in selected.get("operations") or [] if str(item).strip()}
        worker_type = str(selected.get("worker_type") or "").strip()
        binding_type = ""
        execution_target = ""
        remote_node_id = ""
        if worker_type == "browser_worker" or "browser" in namespaces or "browser" in targets:
            binding_type = "browser"
            execution_target = "browser"
        if worker_type == "generic_remote_worker" and ("browser" in namespaces or "browser" in targets or any(item.startswith("browser.") for item in operations)):
            binding_type = "remote_browser"
            metadata = selected.get("metadata") if isinstance(selected.get("metadata"), dict) else {}
            remote_node_id = str(metadata.get("node_id") or str(selected.get("worker_id") or "").removeprefix("remote:")).strip()
            execution_target = f"remote:{remote_node_id}" if remote_node_id else str(selected.get("worker_id") or "")
        if not binding_type:
            return {}
        return {
            "binding_type": binding_type,
            "worker_id": str(selected.get("worker_id") or ""),
            "worker_type": worker_type,
            "worker_subtype": str(selected.get("worker_subtype") or ""),
            "execution_target": execution_target,
            "remote_node_id": remote_node_id,
            "permission_scope": str(selected.get("permission_scope") or ""),
            "capability_namespaces": sorted(namespaces),
        }

    @staticmethod
    def _arguments_with_worker_binding(arguments: dict[str, Any], worker_binding: dict[str, Any] | None) -> dict[str, Any]:
        if not worker_binding:
            return arguments
        return {**arguments, "worker_binding": dict(worker_binding)}

    def _apply_tool_result(
        self,
        definition: WorkflowDefinition,
        run: WorkflowRun,
        node_run: WorkflowNodeRun,
        result: ToolResult,
        *,
        worker_schedule: dict[str, Any] | None = None,
        worker_binding: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        node_definition = next((item for item in definition.nodes if item.node_id == node_run.node_id), None)
        execution_request = _execution_request_snapshot(result.execution_request)
        trajectory_record = self._append_workflow_node_trajectory(
            definition=definition,
            run=run,
            node_run=node_run,
            node_definition=node_definition,
            result=result,
            outputs={
                "data": result.data,
                "metadata": dict(result.metadata or {}),
                "execution_request": execution_request,
                "worker_schedule": worker_schedule or {},
                "worker_binding": worker_binding or {},
            },
        )
        result_metadata = dict(result.metadata or {})
        if trajectory_record:
            result_metadata["trajectory_record"] = trajectory_record
        if result.success:
            finished = replace(
                node_run,
                status=NODE_SUCCEEDED,
                finished_at=utc_now(),
                outputs={
                    "message": result.message,
                    "data": result.data,
                    "metadata": result_metadata,
                    "execution_request": execution_request,
                    "worker_schedule": worker_schedule or {},
                    "worker_binding": worker_binding or {},
                },
            )
            return self._refresh_run_status(definition, self._replace_node(run, finished, event_type="node_succeeded", payload={"node_id": node_run.node_id}))
        failure_outputs = {
            "metadata": result_metadata,
            "execution_request": _execution_request_snapshot(result.execution_request),
            "worker_schedule": worker_schedule or {},
            "worker_binding": worker_binding or {},
        }
        return self._finish_failed_node(
            definition,
            run,
            node_run,
            error=result.message or result.error_code,
            outputs=failure_outputs,
        )

    @staticmethod
    def _append_workflow_node_trajectory(
        *,
        definition: WorkflowDefinition,
        run: WorkflowRun,
        node_run: WorkflowNodeRun,
        node_definition: WorkflowNodeDefinition | None,
        result: ToolResult,
        outputs: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            from backend.executors.base import ExecutionRequest
            from backend.orchestrator.runtime_trajectory_log import (
                append_runtime_trajectory,
                trajectory_from_workflow_node,
                trajectory_logging_enabled,
            )
        except Exception as exc:
            return {"trajectory_log_error": str(exc)}
        if not trajectory_logging_enabled():
            return None
        execution_request = None
        if result.execution_request is not None:
            execution_request = ExecutionRequest(
                target=str(result.execution_request.target or ""),
                operation=str(result.execution_request.operation or ""),
                params=dict(result.execution_request.params or {}),
            )
        try:
            return append_runtime_trajectory(
                trajectory_from_workflow_node(
                    workflow_name=definition.name,
                    workflow_version=definition.version,
                    run_id=run.run_id,
                    node_id=node_run.node_id,
                    node_type=str(getattr(node_definition, "node_type", "") or ""),
                    tool_name=str(getattr(node_definition, "tool_name", "") or ""),
                    skill_name=str(getattr(node_definition, "skill_name", "") or ""),
                    success=bool(result.success),
                    message=result.message,
                    error_code=result.error_code,
                    execution_request=execution_request,
                    outputs=outputs,
                    inputs=run.inputs,
                    metadata={"workflow_metadata": dict(definition.metadata or {})},
                )
            )
        except Exception as exc:
            return {"trajectory_log_error": str(exc)}

    def _run_open_node(
        self,
        definition: WorkflowDefinition,
        run: WorkflowRun,
        started: WorkflowNodeRun,
        node: WorkflowNodeDefinition,
        arguments: dict[str, Any],
        *,
        worker_schedule: dict[str, Any] | None = None,
        worker_binding: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        executor = str(arguments.get("executor") or node.metadata.get("executor") or "").strip().lower()
        if executor == "tool_call":
            tool_name = str(arguments.get("tool_name") or node.tool_name or node.metadata.get("tool_name") or "").strip()
            if not tool_name:
                return self._finish_failed_node(definition, run, started, error=f"open_node_missing_tool_name:{node.node_type}", outputs={"arguments": arguments})
            tool_arguments = arguments.get("tool_arguments") if isinstance(arguments.get("tool_arguments"), dict) else arguments
            tool_arguments = self._arguments_with_worker_binding(tool_arguments, worker_binding)
            result = self._tool_registry.invoke(ToolCall(tool_name, tool_arguments))
            return self._apply_tool_result(definition, run, started, result, worker_schedule=worker_schedule, worker_binding=worker_binding)
        if executor == "skill_call":
            skill_name = str(arguments.get("skill_name") or node.skill_name or node.metadata.get("skill_name") or "").strip()
            if not skill_name:
                return self._finish_failed_node(definition, run, started, error=f"open_node_missing_skill_name:{node.node_type}", outputs={"arguments": arguments})
            if self._skill_runner is None:
                result = ToolResult(False, "SkillRunner 未配置", error_code="skill_runner_missing")
            else:
                skill_arguments = arguments.get("skill_arguments") if isinstance(arguments.get("skill_arguments"), dict) else arguments
                skill_result = self._skill_runner.run(skill_name, skill_arguments, dry_run=False)
                result = ToolResult(skill_result.success, skill_result.message, data={"step_count": len(skill_result.step_results)}, metadata=skill_result.metadata)
            return self._apply_tool_result(definition, run, started, result, worker_schedule=worker_schedule, worker_binding=worker_binding)
        if executor == "agent_task":
            blocked = replace(
                started,
                status=NODE_BLOCKED,
                finished_at=utc_now(),
                error="agent_task_claim_required",
                outputs={"assigned_agent": node.assigned_agent, "node_type": node.node_type, "arguments": arguments},
            )
            return self._replace_node(run, blocked, status=RUN_BLOCKED, event_type="node_blocked", payload={"node_id": node.node_id, "assigned_agent": node.assigned_agent, "open_node_type": node.node_type})

        completed_payload = arguments.get("result") if isinstance(arguments.get("result"), dict) else {}
        if _truthy(arguments.get("completed")) or completed_payload:
            finished = replace(
                started,
                status=NODE_SUCCEEDED,
                finished_at=utc_now(),
                outputs={"node_type": node.node_type, "arguments": arguments, "result": completed_payload, "completed": True},
            )
            return self._refresh_run_status(definition, self._replace_node(run, finished, event_type="node_succeeded", payload={"node_id": node.node_id, "open_node_type": node.node_type}))

        callback_id = str(arguments.get("callback_id") or _node_config_value(node, "callback_id") or node.node_id)
        callback_url = str(arguments.get("callback_url") or _node_config_value(node, "callback_url") or "")
        waiting = replace(
            started,
            status=NODE_WAITING,
            finished_at="",
            outputs={
                "node_type": node.node_type,
                "executor": executor or "external_callback",
                "arguments": arguments,
                "callback_id": callback_id,
                "callback_url": callback_url,
                "status": "open_node_pending",
            },
        )
        return self._replace_node(run, waiting, status=RUN_WAITING, event_type="external_callback_pending", payload={"node_id": node.node_id, "callback_id": callback_id, "open_node_type": node.node_type})

    def _update_node(self, run: WorkflowRun, node_run: WorkflowNodeRun, *, status: str, error: str = "") -> WorkflowRun:
        return self._replace_node(run, replace(node_run, status=status, error=error, finished_at=utc_now()), status=RUN_BLOCKED, event_type="node_blocked", payload={"node_id": node_run.node_id, "error": error})

    @staticmethod
    def _blocked_by_safety(run: WorkflowRun, safety: dict[str, Any]) -> WorkflowRun:
        events = list(run.events)
        events.append({"at": utc_now(), "type": "run_blocked_by_safety", "payload": {"safety": safety}})
        return replace(run, status=RUN_BLOCKED, events=events, updated_at=utc_now())

    @staticmethod
    def _replace_node(run: WorkflowRun, node_run: WorkflowNodeRun, *, status: str | None = None, event_type: str = "", payload: dict[str, Any] | None = None) -> WorkflowRun:
        nodes = dict(run.nodes)
        nodes[node_run.node_id] = node_run
        events = list(run.events)
        if event_type:
            events.append({"at": utc_now(), "type": event_type, "payload": payload or {}})
        return replace(run, status=status or run.status, nodes=nodes, events=events, updated_at=utc_now())

    @staticmethod
    def _dependency_satisfied(run: WorkflowRun, node_id: str) -> bool:
        state = run.nodes.get(node_id)
        return bool(state and state.status in SUCCESSFUL_DEPENDENCY_STATUSES)

    def apply_branch_skips(self, definition: WorkflowDefinition, run: WorkflowRun) -> WorkflowRun:
        updated = run
        changed = True
        while changed:
            changed = False
            nodes = dict(updated.nodes)
            events = list(updated.events)
            for node in definition.nodes:
                state = nodes.get(node.node_id)
                if state is None or state.status != NODE_PENDING:
                    continue
                branch_node = self._inactive_branch_ancestor(definition, updated, node.node_id)
                if branch_node is None:
                    continue
                skipped = replace(
                    state,
                    status=NODE_SKIPPED,
                    finished_at=utc_now(),
                    outputs={
                        **dict(state.outputs or {}),
                        "skipped_by_branch": branch_node.node_id,
                        "status": NODE_SKIPPED,
                    },
                    error="",
                )
                nodes[node.node_id] = skipped
                events.append(
                    {
                        "at": utc_now(),
                        "type": "node_skipped",
                        "payload": {"node_id": node.node_id, "branch_node_id": branch_node.node_id},
                    }
                )
                changed = True
            if changed:
                updated = replace(updated, nodes=nodes, events=events, updated_at=utc_now())
        return updated

    @staticmethod
    def _inactive_branch_ancestor(definition: WorkflowDefinition, run: WorkflowRun, node_id: str) -> WorkflowNodeDefinition | None:
        children = _dependency_children(definition)
        for branch in definition.nodes:
            if branch.node_type != "branch" or branch.node_id == node_id:
                continue
            branch_state = run.nodes.get(branch.node_id)
            if branch_state is None or branch_state.status != NODE_SUCCEEDED:
                continue
            branch_descendants = _reachable_nodes(children, [branch.node_id])
            if node_id not in branch_descendants:
                continue
            selected = branch_state.outputs.get("selected_node_ids") if isinstance(branch_state.outputs, dict) else []
            selected_ids = [str(item) for item in selected if str(item).strip()] if isinstance(selected, list) else []
            selected_reachable = _reachable_nodes(children, selected_ids)
            selected_reachable.update(selected_ids)
            if node_id not in selected_reachable:
                return branch
        return None

    @staticmethod
    def _pending_retry_ready(node_run: WorkflowNodeRun) -> bool:
        retry = node_run.outputs.get("retry") if isinstance(node_run.outputs, dict) else {}
        retry_after = str((retry if isinstance(retry, dict) else {}).get("retry_after_at") or "")
        if not retry_after:
            return True
        parsed = _parse_workflow_time(retry_after)
        return parsed is None or parsed <= datetime.now(UTC)

    @staticmethod
    def _foreach_max_iterations(node: WorkflowNodeDefinition, arguments: dict[str, Any]) -> int:
        try:
            value = int(arguments.get("max_iterations") or _node_config_value(node, "max_iterations", 20) or 20)
        except (TypeError, ValueError):
            value = 20
        return max(1, min(value, 1000))

    @staticmethod
    def _retry_policy_value(node: WorkflowNodeDefinition, key: str, default: Any) -> Any:
        policy = node.retry_policy if isinstance(node.retry_policy, dict) else {}
        if key in policy:
            return policy.get(key)
        retry = node.metadata.get("retry_policy") if isinstance(node.metadata.get("retry_policy"), dict) else {}
        if key in retry:
            return retry.get(key)
        return default

    def _retry_allowed(self, node: WorkflowNodeDefinition, node_run: WorkflowNodeRun) -> bool:
        try:
            max_attempts = int(self._retry_policy_value(node, "max_attempts", 1) or 1)
        except (TypeError, ValueError):
            max_attempts = 1
        return node_run.attempts < max(1, max_attempts)

    def _retry_backoff_seconds(self, node: WorkflowNodeDefinition) -> float:
        try:
            return max(0.0, float(self._retry_policy_value(node, "backoff_seconds", 0) or 0))
        except (TypeError, ValueError):
            return 0.0

    def _manual_retry_node(self, definition: WorkflowDefinition, run: WorkflowRun, node: WorkflowNodeDefinition, current: WorkflowNodeRun) -> WorkflowRun:
        pending = replace(
            current,
            status=NODE_PENDING,
            finished_at="",
            error="",
            outputs={
                **dict(current.outputs or {}),
                "retry": {
                    "manual": True,
                    "attempts_so_far": current.attempts,
                    "max_attempts": max(current.attempts + 1, int(self._retry_policy_value(node, "max_attempts", current.attempts + 1) or current.attempts + 1)),
                },
            },
        )
        return self._replace_node(run, pending, status=RUN_RUNNING, event_type="node_retry_requested", payload={"node_id": node.node_id})

    def _finish_failed_node(
        self,
        definition: WorkflowDefinition,
        run: WorkflowRun,
        node_run: WorkflowNodeRun,
        *,
        error: str,
        outputs: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        node_definition = next((item for item in definition.nodes if item.node_id == node_run.node_id), None)
        error_detail = _error_detail(error, outputs or {})
        failed_outputs = {
            **dict(node_run.outputs or {}),
            **dict(outputs or {}),
            "error_detail": error_detail,
        }
        if node_definition is not None and self._retry_allowed(node_definition, node_run):
            backoff_seconds = self._retry_backoff_seconds(node_definition)
            retry_after = datetime.now(UTC) + timedelta(seconds=backoff_seconds)
            pending = replace(
                node_run,
                status=NODE_PENDING,
                finished_at=utc_now(),
                outputs={
                    **failed_outputs,
                    "retry": {
                        "scheduled": True,
                        "attempts_so_far": node_run.attempts,
                        "max_attempts": int(self._retry_policy_value(node_definition, "max_attempts", node_run.attempts + 1) or node_run.attempts + 1),
                        "backoff_seconds": backoff_seconds,
                        "retry_after_at": retry_after.isoformat(timespec="seconds"),
                    },
                },
                error=error,
            )
            return self._replace_node(
                run,
                pending,
                status=RUN_RUNNING,
                event_type="node_retry_scheduled",
                payload={"node_id": node_run.node_id, "error": error, "attempts": node_run.attempts},
            )
        failed = replace(node_run, status=NODE_FAILED, finished_at=utc_now(), error=error, outputs=failed_outputs)
        return self._replace_node(run, failed, status=RUN_FAILED, event_type="node_failed", payload={"node_id": node_run.node_id, "error": failed.error})

    def _refresh_run_status(self, definition: WorkflowDefinition, run: WorkflowRun) -> WorkflowRun:
        statuses = [run.nodes[node.node_id].status for node in definition.nodes]
        if statuses and all(status in SUCCESSFUL_DEPENDENCY_STATUSES for status in statuses):
            return replace(run, status=RUN_SUCCEEDED, updated_at=utc_now())
        if any(status == NODE_FAILED for status in statuses):
            return replace(run, status=RUN_FAILED, updated_at=utc_now())
        if any(status == NODE_WAITING_REVIEW for status in statuses):
            return replace(run, status=RUN_WAITING_REVIEW, updated_at=utc_now())
        if any(status == NODE_WAITING for status in statuses):
            return replace(run, status=RUN_WAITING, updated_at=utc_now())
        if any(status == NODE_BLOCKED for status in statuses):
            return replace(run, status=RUN_BLOCKED, updated_at=utc_now())
        if any(status == NODE_RUNNING for status in statuses):
            return replace(run, status=RUN_RUNNING, updated_at=utc_now())
        return run

    @staticmethod
    def _resolve_branch_selection(node: WorkflowNodeDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        routes = arguments.get("routes") if isinstance(arguments.get("routes"), dict) else {}
        if not routes and isinstance(node.metadata.get("routes"), dict):
            routes = dict(node.metadata.get("routes") or {})
        route = str(arguments.get("route") or "").strip()
        condition = arguments.get("condition")
        if not route:
            if condition is None:
                condition = _node_config_value(node, "condition", condition)
            route = "true" if _evaluate_branch_condition(condition) else "false"
        selected = routes.get(route, [])
        if isinstance(selected, str):
            selected_node_ids = [selected]
        elif isinstance(selected, list):
            selected_node_ids = [str(item) for item in selected if str(item).strip()]
        else:
            selected_node_ids = []
        return {
            "selected_route": route,
            "selected_node_ids": selected_node_ids,
            "condition": condition,
        }

    @staticmethod
    def _resolve_arguments(arguments: dict[str, Any], inputs: dict[str, Any], nodes: dict[str, WorkflowNodeRun] | None = None) -> dict[str, Any]:
        return {
            key: WorkflowRunner._resolve_argument_value(value, inputs, nodes or {})
            for key, value in arguments.items()
        }

    @staticmethod
    def _resolve_argument_value(value: Any, inputs: dict[str, Any], nodes: dict[str, WorkflowNodeRun] | None = None) -> Any:
        if isinstance(value, str):
            matches = list(TEMPLATE_REF_RE.finditer(value))
            if len(matches) == 1 and matches[0].span() == (0, len(value)):
                return _resolve_template_reference(matches[0].group(1).strip(), inputs, nodes or {})
            if matches:
                def replace_match(match: re.Match[str]) -> str:
                    resolved = _resolve_template_reference(match.group(1).strip(), inputs, nodes or {})
                    return _stringify_interpolation_value(resolved)

                return TEMPLATE_REF_RE.sub(replace_match, value)
        if isinstance(value, dict):
            if str(value.get("op") or value.get("operator") or "").strip() or "left" in value or "right" in value or "equals" in value:
                return {
                    str(key): WorkflowRunner._resolve_argument_value(item, inputs, nodes or {})
                    for key, item in value.items()
                }
            return {
                str(key): WorkflowRunner._resolve_argument_value(item, inputs, nodes or {})
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [WorkflowRunner._resolve_argument_value(item, inputs, nodes or {}) for item in value]
        return value


def _resolve_template_reference(key: str, inputs: dict[str, Any], nodes: dict[str, WorkflowNodeRun]) -> Any:
    if key.startswith("input."):
        return _resolve_path(inputs, key[6:])
    if key.startswith("node."):
        return _resolve_node_output_reference(key, nodes)
    return inputs.get(key)


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _stringify_interpolation_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json_dumps_compact(value)


def json_dumps_compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _resolve_node_output_reference(key: str, nodes: dict[str, WorkflowNodeRun]) -> Any:
    parts = key.split(".")
    if len(parts) < 3 or parts[0] != "node":
        return None
    node = nodes.get(parts[1])
    if node is None:
        return None
    if parts[2] == "outputs":
        value: Any = node.outputs
        if len(parts) == 3:
            return value
    elif parts[2] == "status":
        value = node.status
    elif parts[2] == "error":
        value = node.error
    else:
        return None
    for part in parts[3:]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return value


def _evaluate_branch_condition(condition: Any) -> bool:
    if not isinstance(condition, dict):
        return _truthy(condition)
    left = condition.get("left", condition.get("value"))
    right = condition.get("right", condition.get("equals", True))
    op = str(condition.get("op") or condition.get("operator") or ("==" if "equals" in condition else "")).strip().lower()
    if op in {"", "truthy"}:
        return _truthy(left)
    if op in {"==", "eq", "equals"}:
        return left == right
    if op in {"!=", "ne", "not_equals"}:
        return left != right
    if op in {">", ">=", "<", "<="}:
        left_number = _number_or_none(left)
        right_number = _number_or_none(right)
        if left_number is None or right_number is None:
            return False
        if op == ">":
            return left_number > right_number
        if op == ">=":
            return left_number >= right_number
        if op == "<":
            return left_number < right_number
        return left_number <= right_number
    if op in {"contains", "in"}:
        if isinstance(right, (list, tuple, set)):
            return left in right
        return str(left) in str(right)
    return _truthy(left)


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_workflow_time(value: str) -> datetime | None:
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


def _dependency_children(definition: WorkflowDefinition) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {node.node_id: [] for node in definition.nodes}
    for node in definition.nodes:
        for dep in node.depends_on:
            children.setdefault(dep, []).append(node.node_id)
    return children


def _reachable_nodes(children: dict[str, list[str]], start_ids: list[str]) -> set[str]:
    reachable: set[str] = set()
    stack = [item for item in start_ids if item]
    while stack:
        node_id = stack.pop()
        for child in children.get(node_id, []):
            if child in reachable:
                continue
            reachable.add(child)
            stack.append(child)
    return reachable


def _error_detail(error: str, outputs: dict[str, Any]) -> dict[str, Any]:
    metadata = outputs.get("metadata") if isinstance(outputs.get("metadata"), dict) else {}
    execution_request = outputs.get("execution_request") if isinstance(outputs.get("execution_request"), dict) else {}
    return {
        "message": str(error or ""),
        "metadata": metadata,
        "execution_request": execution_request,
        "stderr": str(outputs.get("stderr") or metadata.get("stderr") or ""),
        "error_code": str(metadata.get("error_code") or ""),
        "recorded_at": utc_now(),
    }


def _node_port_defaults(node_type: str) -> tuple[str, str]:
    normalized = (node_type or "").strip().lower()
    if normalized == "agent_task":
        return "execution|artifact|knowledge", "execution|artifact|knowledge"
    if normalized == "tool_call":
        return "execution|artifact", "execution|artifact|automation"
    if normalized == "skill_call":
        return "execution|artifact|knowledge", "execution|artifact|knowledge"
    if normalized == "review_gate":
        return "execution|artifact|review", "execution|review"
    if normalized == "branch":
        return "execution|signal|control", "execution|control"
    if normalized == "subgraph":
        return "execution|artifact|control", "execution|artifact|control"
    if normalized == "foreach":
        return "execution|artifact|control", "execution|artifact|control"
    if normalized == "waiter":
        return "signal|control", "execution|signal"
    if normalized == "external_callback":
        return "signal|control", "execution|signal"
    if is_android_workflow_node_type(normalized):
        return "execution|automation|control", "execution|automation|signal"
    return "execution", "execution"


def workflow_node_type_schema() -> dict[str, Any]:
    node_types = [
        "agent_task",
        "tool_call",
        "skill_call",
        "review_gate",
        "branch",
        "subgraph",
        "foreach",
        "waiter",
        "external_callback",
        "workflow.android_step",
    ]
    compatibility: dict[str, dict[str, bool]] = {}
    for source in node_types:
        compatibility[source] = {}
        for target in node_types:
            compatibility[source][target] = _are_node_types_compatible(source, target)
    return {
        "node_types": [
            {
                "node_type": node_type,
                "label": _node_type_label(node_type),
                "default_input_port_kind": _node_port_defaults(node_type)[0],
                "default_output_port_kind": _node_port_defaults(node_type)[1],
            }
            for node_type in node_types
        ],
        "compatibility_matrix": compatibility,
        "port_kinds": ["*", "execution", "artifact", "knowledge", "signal", "review", "automation", "control"],
    }


def workflow_node_catalog(tool_specs: list[ToolSpec] | None = None, skill_specs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    type_schema = workflow_node_type_schema()
    builtins = [
        {
            "catalog_id": f"node_type:{item['node_type']}",
            "group": "flow",
            "node_type": item["node_type"],
            "label": item["label"],
            "parameters": {},
            "ports": {
                "input": item["default_input_port_kind"],
                "output": item["default_output_port_kind"],
            },
        }
        for item in type_schema["node_types"]
    ]
    tools = [
        {
            "catalog_id": f"tool:{spec.name}",
            "group": "tool",
            "node_type": "tool_call",
            "label": spec.name,
            "description": spec.description,
            "tool_name": spec.name,
            "target": spec.target,
            "operation": spec.operation,
            "risk_level": spec.risk_level,
            "authz_risk": spec.authz_risk,
            "read_only": spec.read_only,
            "parameters": dict(spec.schema or {}),
            "ports": {
                "input": _node_port_defaults("tool_call")[0],
                "output": _node_port_defaults("tool_call")[1],
            },
        }
        for spec in (tool_specs or [])
    ]
    skills = [
        {
            "catalog_id": f"skill:{item.get('name') or item.get('skill_name') or item.get('id')}",
            "group": "skill",
            "node_type": "skill_call",
            "label": str(item.get("display_name") or item.get("name") or item.get("skill_name") or item.get("id") or "skill"),
            "skill_name": str(item.get("name") or item.get("skill_name") or item.get("id") or ""),
            "parameters": dict(item.get("schema") or item.get("parameters") or {}),
            "ports": {
                "input": _node_port_defaults("skill_call")[0],
                "output": _node_port_defaults("skill_call")[1],
            },
        }
        for item in (skill_specs or [])
        if isinstance(item, dict)
    ]
    return {
        "schema_version": "spiritkin.workflow_node_catalog.v1",
        **type_schema,
        "catalog": [*builtins, *tools, *skills],
        "counts": {"builtins": len(builtins), "tools": len(tools), "skills": len(skills)},
    }


def workflow_definition_with_port_references(definition: WorkflowDefinition) -> WorkflowDefinition:
    nodes_by_id = {node.node_id: node for node in definition.nodes}
    updated_nodes: list[WorkflowNodeDefinition] = []
    changed = False
    for node in definition.nodes:
        arguments = dict(node.arguments or {})
        for dependency in node.depends_on:
            source = nodes_by_id.get(dependency)
            if source is None:
                continue
            if _arguments_reference_node(arguments, dependency):
                continue
            key = _port_reference_argument_key(source, node)
            if key in arguments:
                continue
            arguments[key] = f"{{{{node.{dependency}.outputs}}}}"
            changed = True
        updated_nodes.append(replace(node, arguments=arguments) if arguments != node.arguments else node)
    if not changed:
        return definition
    return replace(definition, nodes=tuple(updated_nodes))


def _arguments_reference_node(value: Any, node_id: str) -> bool:
    if isinstance(value, str):
        return any(match.group(1).strip().startswith(f"node.{node_id}.") for match in TEMPLATE_REF_RE.finditer(value))
    if isinstance(value, dict):
        return any(_arguments_reference_node(item, node_id) for item in value.values())
    if isinstance(value, list):
        return any(_arguments_reference_node(item, node_id) for item in value)
    return False


def _port_reference_argument_key(source: WorkflowNodeDefinition, target: WorkflowNodeDefinition) -> str:
    target_inputs = _interface_inputs(target)
    source_output_kind = _node_output_port_kind(source)
    source_tokens = _port_kind_tokens(source_output_kind)
    for item in target_inputs:
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not name:
            continue
        if not kind or _port_kind_tokens(kind) & source_tokens:
            return name
    safe_source = re.sub(r"[^A-Za-z0-9_]+", "_", source.node_id).strip("_") or "source"
    return f"from_{safe_source}"


def _node_type_label(node_type: str) -> str:
    normalized = (node_type or "").strip().lower()
    return {
        "agent_task": "Agent Task",
        "tool_call": "Tool Call",
        "skill_call": "Skill Call",
        "review_gate": "Review Gate",
        "branch": "Branch",
        "subgraph": "Subgraph",
        "foreach": "Foreach",
        "waiter": "Waiter",
        "external_callback": "External Callback",
        "workflow.android_step": "Android Step",
    }.get(normalized, normalized or "Node")


def _blueprint_metadata(
    node_type: str,
    x: int,
    y: int,
    *,
    input_kind: str = "",
    output_kind: str = "",
    responsibility: str = "",
    queue_label: str = "",
    inputs: list[dict[str, Any]] | None = None,
    outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    default_input, default_output = _node_port_defaults(node_type)
    resolved_input = input_kind or default_input
    resolved_output = output_kind or default_output
    return {
        "position": {"x": x, "y": y},
        "responsibility": responsibility,
        "queue_label": queue_label,
        "ports": [
            {
                "id": "exec_in",
                "direction": "input",
                "kind": resolved_input,
                "label": "In",
                "required": node_type not in {"waiter", "external_callback"},
            },
            {
                "id": "exec_out",
                "direction": "output",
                "kind": resolved_output,
                "label": "Out",
                "required": True,
            },
        ],
        "connection_policy": {
            "input_accepts": resolved_input,
            "output_emits": resolved_output,
            "type_label": node_type,
        },
        "interface_contract": {
            "summary": responsibility,
            "inputs": list(inputs or []),
            "outputs": list(outputs or []),
        },
    }


def build_ecommerce_auto_listing_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="ecommerce.auto_listing.v1",
        version="0.1.0",
        description="节点式电商自动化上架流程骨架：选品、搬运、productData、门禁、上架草稿、审核、发布。",
        nodes=(
            WorkflowNodeDefinition(
                "product_selection",
                "agent_task",
                "选品",
                assigned_agent="ecommerce",
                metadata=_blueprint_metadata(
                    "agent_task",
                    24,
                    112,
                    output_kind="product_candidate|knowledge|artifact",
                    responsibility="维护候选商品队列，确认当前要采集和上架的商品。",
                    queue_label="候选商品",
                    outputs=[{"name": "product_candidate", "kind": "knowledge", "required": True, "description": "待采集商品及选品理由"}],
                ),
            ),
            WorkflowNodeDefinition(
                "source_capture",
                "agent_task",
                "搬运/采集",
                assigned_agent="vision_model",
                depends_on=("product_selection",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    220,
                    44,
                    input_kind="product_candidate|knowledge|artifact",
                    output_kind="source_asset|artifact",
                    responsibility="采集商品链接、截图、主图和详情图等原始素材。",
                    queue_label="采集素材",
                    inputs=[{"name": "product_candidate", "kind": "knowledge", "required": True}],
                    outputs=[{"name": "source_asset", "kind": "artifact", "required": True, "description": "链接、截图、图片和 OCR 输入"}],
                ),
            ),
            WorkflowNodeDefinition(
                "mobile_link_intake",
                "tool_call",
                "手机链接入队",
                tool_name="ecommerce.task_queue.ingest_mobile_links",
                arguments={"include_latest": "{{include_latest}}", "project_root": "{{project_root}}", "links_jsonl": "{{links_jsonl}}"},
                depends_on=("source_capture",),
                metadata=_blueprint_metadata(
                    "tool_call",
                    416,
                    44,
                    input_kind="source_asset|artifact",
                    output_kind="task_queue|automation|artifact",
                    responsibility="把手机 PDD App 回传的商品网页链接转换为本地任务队列项。",
                    queue_label="入队链接",
                    inputs=[{"name": "links_jsonl", "kind": "artifact", "required": True}],
                    outputs=[{"name": "product_queue", "kind": "automation", "required": True, "description": "待解析商品任务队列"}],
                ),
            ),
            WorkflowNodeDefinition(
                "productdata_build",
                "skill_call",
                "接收扩展 productData",
                skill_name="ecommerce.browser_extension_productdata.workflow",
                arguments={
                    "project_root": "{{project_root}}",
                    "state_dir": "{{ecommerce_state_dir}}",
                    "task_id": "{{task_id}}",
                    "product_data_json": "{{product_data_json}}",
                    "control_plane_artifact_id": "{{control_plane_artifact_id}}",
                },
                depends_on=("mobile_link_intake",),
                metadata=_blueprint_metadata(
                    "skill_call",
                    612,
                    44,
                    input_kind="task_queue|source_asset|artifact",
                    output_kind="productdata|artifact",
                    responsibility="接收登录态浏览器扩展抓取的 rawData，并绑定规范化 productData Artifact。",
                    queue_label="ProductData 接入",
                    inputs=[{"name": "task_id", "kind": "automation", "required": False}, {"name": "product_data_json", "kind": "artifact", "required": False}],
                    outputs=[{"name": "productData", "kind": "artifact", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "listing_gate",
                "review_gate",
                "上架完整性门禁",
                review_gate="core_review",
                depends_on=("productdata_build",),
                metadata=_blueprint_metadata(
                    "review_gate",
                    808,
                    44,
                    input_kind="productdata|artifact|review",
                    output_kind="review|artifact",
                    responsibility="检查 productData 必填字段、图片资产和平台风险。",
                    inputs=[{"name": "productData", "kind": "artifact", "required": True}],
                    outputs=[{"name": "approval", "kind": "review", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "listing_draft",
                "agent_task",
                "生成上架草稿",
                assigned_agent="ecommerce",
                depends_on=("listing_gate",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    612,
                    196,
                    input_kind="review|productdata|artifact",
                    output_kind="listing_draft|artifact",
                    responsibility="生成平台上架草稿，保留可审核的标题、SKU、图文和价格字段。",
                    queue_label="上架草稿",
                    outputs=[{"name": "listing_draft", "kind": "artifact", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "publish_review",
                "review_gate",
                "发布前审核",
                review_gate="human_review",
                depends_on=("listing_draft",),
                metadata=_blueprint_metadata(
                    "review_gate",
                    808,
                    196,
                    input_kind="listing_draft|artifact|review",
                    output_kind="review|artifact",
                    responsibility="人工确认草稿可发布，或要求回到草稿节点修正。",
                    inputs=[{"name": "listing_draft", "kind": "artifact", "required": True}],
                    outputs=[{"name": "publish_approval", "kind": "review", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "publish_or_hold",
                "agent_task",
                "发布或保持草稿",
                assigned_agent="ecommerce",
                depends_on=("publish_review",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    1004,
                    196,
                    input_kind="review|listing_draft|artifact",
                    output_kind="publish_result|automation|artifact",
                    responsibility="根据审核结果发布商品，或保留草稿并记录阻塞原因。",
                    queue_label="发布队列",
                    outputs=[{"name": "publish_result", "kind": "automation", "required": True}],
                ),
            ),
        ),
        metadata={
            "blueprint_ready": True,
            "domain": "ecommerce",
            "status": "candidate",
            "display_name": "电商自动上架",
            "category": "电商",
            "parameters": [
                {"name": "links_jsonl", "label": "手机链接队列", "type": "text", "default": "state/mobile-links/links.jsonl", "placeholder": "state/mobile-links/links.jsonl"},
                {"name": "include_latest", "label": "包含 latest-link", "type": "boolean", "default": False},
                {"name": "task_id", "label": "任务 ID", "type": "text", "placeholder": "可选，指定已有任务"},
                {"name": "product_data_json", "label": "扩展 ProductData JSON", "type": "text", "placeholder": "由浏览器扩展自动写入"},
                {"name": "control_plane_artifact_id", "label": "控制面 Artifact ID", "type": "text", "placeholder": "由浏览器扩展自动写入"},
                {"name": "main_image", "label": "主图", "type": "text", "placeholder": "主图路径或 URL"},
                {"name": "detail_image", "label": "详情图", "type": "text", "placeholder": "详情图路径或 URL"},
            ],
        },
    )


def build_video_generation_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="content.video_generation.v1",
        version="0.1.0",
        description="节点式视频生成流程骨架：需求、脚本、素材、生成、质检、交付。",
        nodes=(
            WorkflowNodeDefinition(
                "brief",
                "agent_task",
                "视频需求",
                assigned_agent="video_animation",
                metadata=_blueprint_metadata(
                    "agent_task",
                    24,
                    116,
                    input_kind="creative_brief|knowledge|artifact",
                    output_kind="creative_brief|knowledge",
                    responsibility="确认视频目标、受众、主体、画幅、时长和平台约束。",
                    queue_label="需求条目",
                    inputs=[
                        {"name": "prompt", "kind": "knowledge", "required": True, "description": "用户视频提示词"},
                        {"name": "reference_images", "kind": "artifact", "required": False, "description": "参考图路径或 URL"},
                    ],
                    outputs=[{"name": "creative_brief", "kind": "knowledge", "required": True, "description": "结构化视频需求"}],
                ),
            ),
            WorkflowNodeDefinition(
                "script_storyboard",
                "agent_task",
                "脚本/分镜",
                assigned_agent="video_animation",
                depends_on=("brief",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    220,
                    44,
                    input_kind="creative_brief|knowledge",
                    output_kind="storyboard|knowledge|artifact",
                    responsibility="拆解镜头、字幕、动作、转场和时间轴。",
                    queue_label="分镜镜头",
                    inputs=[{"name": "creative_brief", "kind": "knowledge", "required": True}],
                    outputs=[
                        {"name": "script", "kind": "knowledge", "required": True, "description": "口播/字幕脚本"},
                        {"name": "storyboard", "kind": "artifact", "required": True, "description": "镜头列表和时间轴"},
                    ],
                ),
            ),
            WorkflowNodeDefinition(
                "asset_plan",
                "agent_task",
                "素材规划",
                assigned_agent="vision_model",
                depends_on=("script_storyboard",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    416,
                    44,
                    input_kind="storyboard|artifact|knowledge",
                    output_kind="asset_manifest|artifact|knowledge",
                    responsibility="为每个镜头整理角色、商品、背景、参考图和缺失素材。",
                    queue_label="素材清单",
                    inputs=[{"name": "storyboard", "kind": "artifact", "required": True}],
                    outputs=[{"name": "asset_manifest", "kind": "artifact", "required": True, "description": "可追踪素材清单"}],
                ),
            ),
            WorkflowNodeDefinition(
                "generation_config",
                "agent_task",
                "生成配置",
                assigned_agent="video_animation",
                depends_on=("asset_plan",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    612,
                    44,
                    input_kind="asset_manifest|artifact|knowledge",
                    output_kind="render_request|automation|artifact",
                    responsibility="把分镜和素材转换为模型参数、镜头批次和渲染请求。",
                    queue_label="渲染批次",
                    inputs=[{"name": "asset_manifest", "kind": "artifact", "required": True}],
                    outputs=[{"name": "render_request", "kind": "automation", "required": True, "description": "模型/渲染队列请求"}],
                ),
            ),
            WorkflowNodeDefinition(
                "render_or_request",
                "agent_task",
                "生成/提交渲染",
                assigned_agent="video_animation",
                depends_on=("generation_config",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    808,
                    44,
                    input_kind="render_request|automation|artifact",
                    output_kind="render_result|artifact|automation",
                    responsibility="执行本地渲染或提交外部视频生成任务，并追踪每个镜头状态。",
                    queue_label="渲染任务",
                    inputs=[{"name": "render_request", "kind": "automation", "required": True}],
                    outputs=[{"name": "render_result", "kind": "artifact", "required": True, "description": "视频片段、预览和渲染日志"}],
                ),
            ),
            WorkflowNodeDefinition(
                "quality_gate",
                "review_gate",
                "质量审核",
                review_gate="human_review",
                depends_on=("render_or_request",),
                metadata=_blueprint_metadata(
                    "review_gate",
                    808,
                    196,
                    input_kind="render_result|artifact|review",
                    output_kind="review|artifact",
                    responsibility="审核画面、节奏、品牌安全、字幕和平台约束。",
                    inputs=[{"name": "render_result", "kind": "artifact", "required": True}],
                    outputs=[{"name": "quality_approval", "kind": "review", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "delivery_package",
                "agent_task",
                "交付打包",
                assigned_agent="video_animation",
                depends_on=("quality_gate",),
                metadata=_blueprint_metadata(
                    "agent_task",
                    1004,
                    196,
                    input_kind="review|render_result|artifact",
                    output_kind="delivery_package|artifact",
                    responsibility="导出成片、封面、字幕、元数据和发布说明。",
                    queue_label="交付资产",
                    inputs=[{"name": "quality_approval", "kind": "review", "required": True}],
                    outputs=[{"name": "delivery_package", "kind": "artifact", "required": True, "description": "成片和发布资料包"}],
                ),
            ),
        ),
        metadata={
            "blueprint_ready": True,
            "domain": "video",
            "status": "candidate",
            "display_name": "视频生成",
            "category": "内容生成",
            "parameters": [
                {"name": "prompt", "label": "视频提示词", "type": "textarea", "required": True, "placeholder": "描述主体、镜头、动作、风格和用途"},
                {"name": "negative_prompt", "label": "负向提示词", "type": "textarea", "placeholder": "不希望出现的内容"},
                {"name": "duration_seconds", "label": "时长秒", "type": "number", "default": 8, "min": 1, "max": 60},
                {"name": "aspect_ratio", "label": "画幅", "type": "select", "default": "9:16", "options": ["9:16", "16:9", "1:1"]},
                {"name": "style", "label": "风格", "type": "text", "placeholder": "写实、产品广告、二次元、电影感等"},
                {"name": "reference_images", "label": "参考图", "type": "textarea", "placeholder": "路径或 URL，多项换行"},
                {"name": "target_platform", "label": "发布平台", "type": "select", "default": "电商主图视频", "options": ["电商主图视频", "抖音/小红书", "YouTube Shorts", "自定义"]},
                {"name": "model_profile", "label": "模型配置", "type": "text", "placeholder": "可选，如 local/video/default"},
            ],
        },
    )


def _safe_workflow_node_id(value: Any, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    out = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    if out and out[0].isdigit():
        out = f"w_{out}"
    return out[:64] or fallback


def _composition_component_workflow_name(component: Any) -> str:
    if isinstance(component, str):
        return component.strip()
    if isinstance(component, dict):
        return str(component.get("workflow_name") or component.get("name") or "").strip()
    return ""


def _composition_component_label(component: Any, workflow_name: str) -> str:
    if isinstance(component, dict):
        label = str(component.get("label") or component.get("display_name") or "").strip()
        if label:
            return label
    return workflow_name


def _composition_component_inputs(component: Any, node_id: str) -> Any:
    if isinstance(component, dict) and isinstance(component.get("inputs"), dict):
        return dict(component["inputs"])
    return "{{component_inputs_" + node_id + "}}"


def build_composed_workflow_definition(
    *,
    name: str = "custom.workflow.composed.v1",
    components: list[Any] | tuple[Any, ...] = (),
    mode: str = "serial",
    version: str = "0.1.0",
    display_name: str = "",
    description: str = "",
    workspace_id: str = "",
) -> WorkflowDefinition:
    normalized_mode = str(mode or "serial").strip().lower()
    if normalized_mode not in {"serial", "parallel"}:
        normalized_mode = "serial"
    workflow_name = str(name or "").strip() or "custom.workflow.composed.v1"
    nodes: list[WorkflowNodeDefinition] = []
    seen_ids: set[str] = set()
    for index, component in enumerate(components, start=1):
        child_workflow = _composition_component_workflow_name(component)
        if not child_workflow:
            continue
        base_node_id = _safe_workflow_node_id(child_workflow, f"workflow_{index}")
        node_id = base_node_id
        suffix = 2
        while node_id in seen_ids:
            node_id = f"{base_node_id}_{suffix}"
            suffix += 1
        seen_ids.add(node_id)
        depends_on = () if normalized_mode == "parallel" or not nodes else (nodes[-1].node_id,)
        nodes.append(
            WorkflowNodeDefinition(
                node_id,
                "subgraph",
                _composition_component_label(component, child_workflow),
                arguments={
                    "workflow_name": child_workflow,
                    "inputs": _composition_component_inputs(component, node_id),
                },
                depends_on=depends_on,
                metadata=_blueprint_metadata(
                    "subgraph",
                    24 + ((index - 1) % 4) * 220,
                    72 + ((index - 1) // 4) * 156,
                    input_kind="execution|artifact|control",
                    output_kind="execution|artifact|control",
                    responsibility=f"启动并等待子工作流 {child_workflow} 完成。",
                    inputs=[{"name": "component_inputs_" + node_id, "kind": "control", "required": False, "description": "传给该子工作流的输入对象"}],
                    outputs=[{"name": "child_run", "kind": "control", "required": True, "description": "子工作流运行 ID 和完成信号"}],
                ),
            )
        )
    metadata = {
        "blueprint_ready": True,
        "domain": "workflow",
        "status": "candidate",
        "display_name": display_name or "组合工作流",
        "category": "组合工作流",
        "composition": {
            "mode": normalized_mode,
            "component_count": len(nodes),
            "components": [
                {
                    "node_id": node.node_id,
                    "workflow_name": str(node.arguments.get("workflow_name") or ""),
                    "label": node.label,
                }
                for node in nodes
            ],
        },
        "parameters": [
            {
                "name": "composition_note",
                "label": "组合说明",
                "type": "textarea",
                "placeholder": "记录本次自由组合的目标、输入和验收口径",
            }
        ],
    }
    if str(workspace_id or "").strip():
        metadata["workspace_id"] = str(workspace_id).strip()
    return WorkflowDefinition(
        name=workflow_name,
        version=version or "0.1.0",
        description=description or "由多个子工作流自由组合而成的主控工作流。",
        nodes=tuple(nodes),
        metadata=metadata,
    )


def build_free_composition_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="workflow.free_composition.v1",
        version="0.1.0",
        description="自由组合工作流模板：先规划，再按输入启动一个或多个子工作流，最后人工确认组合结果。",
        nodes=(
            WorkflowNodeDefinition(
                "composition_plan",
                "agent_task",
                "组合规划",
                assigned_agent="main_text",
                metadata=_blueprint_metadata(
                    "agent_task",
                    24,
                    112,
                    input_kind="control|knowledge|artifact",
                    output_kind="control|knowledge",
                    responsibility="确认要组合的子工作流、输入映射、串并行关系和验收标准。",
                    queue_label="组合计划",
                    inputs=[{"name": "composition_goal", "kind": "knowledge", "required": True, "description": "组合工作流目标"}],
                    outputs=[{"name": "composition_plan", "kind": "knowledge", "required": True, "description": "子工作流编排计划"}],
                ),
            ),
            WorkflowNodeDefinition(
                "primary_subworkflow",
                "subgraph",
                "主子工作流",
                arguments={"workflow_name": "{{primary_workflow_name}}", "inputs": "{{primary_workflow_inputs}}"},
                depends_on=("composition_plan",),
                metadata=_blueprint_metadata(
                    "subgraph",
                    252,
                    52,
                    input_kind="control|artifact|knowledge",
                    output_kind="control|artifact",
                    responsibility="启动主要子工作流并等待完成信号。",
                    inputs=[{"name": "primary_workflow_name", "kind": "control", "required": True, "description": "第一个子工作流名称"}],
                    outputs=[{"name": "primary_child_run", "kind": "control", "required": True, "description": "主要子工作流运行 ID"}],
                ),
            ),
            WorkflowNodeDefinition(
                "secondary_subworkflow",
                "subgraph",
                "可选子工作流",
                arguments={"workflow_name": "{{secondary_workflow_name}}", "inputs": "{{secondary_workflow_inputs}}"},
                depends_on=("primary_subworkflow",),
                metadata=_blueprint_metadata(
                    "subgraph",
                    480,
                    52,
                    input_kind="control|artifact|knowledge",
                    output_kind="control|artifact",
                    responsibility="按组合计划启动第二个子工作流；不需要时可在编辑器里删除或替换。",
                    inputs=[{"name": "secondary_workflow_name", "kind": "control", "required": False, "description": "第二个子工作流名称"}],
                    outputs=[{"name": "secondary_child_run", "kind": "control", "required": False, "description": "第二个子工作流运行 ID"}],
                ),
            ),
            WorkflowNodeDefinition(
                "composition_review",
                "review_gate",
                "组合结果审核",
                review_gate="human_review",
                depends_on=("secondary_subworkflow",),
                metadata=_blueprint_metadata(
                    "review_gate",
                    708,
                    112,
                    input_kind="control|artifact|review",
                    output_kind="review|control",
                    responsibility="确认子工作流输出已经满足本次组合目标。",
                    inputs=[{"name": "composition_result", "kind": "artifact", "required": False, "description": "组合结果摘要或产物"}],
                    outputs=[{"name": "composition_approval", "kind": "review", "required": True, "description": "组合结果审核意见"}],
                ),
            ),
        ),
        metadata={
            "blueprint_ready": True,
            "domain": "workflow",
            "status": "candidate",
            "display_name": "自由组合工作流",
            "category": "组合工作流",
            "composition_template": True,
            "parameters": [
                {"name": "composition_goal", "label": "组合目标", "type": "textarea", "required": True, "placeholder": "说明这次组合要完成的业务目标"},
                {"name": "primary_workflow_name", "label": "主子工作流", "type": "select", "default": "ecommerce.auto_listing.v1", "options": ["ecommerce.auto_listing.v1", "content.video_generation.v1"]},
                {"name": "secondary_workflow_name", "label": "第二子工作流", "type": "select", "default": "content.video_generation.v1", "options": ["content.video_generation.v1", "ecommerce.auto_listing.v1"]},
                {"name": "primary_workflow_inputs", "label": "主子工作流输入 JSON", "type": "textarea", "placeholder": f'{{"project_root":"{_PROJECT_ROOT_POSIX}"}}'},
                {"name": "secondary_workflow_inputs", "label": "第二子工作流输入 JSON", "type": "textarea", "placeholder": "{\"prompt\":\"...\"}"},
            ],
        },
    )


def build_android_command_lifecycle_acceptance_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="android.command_lifecycle_acceptance.v1",
        version="0.1.0",
        description="Android 真机命令生命周期验收模板：打开 Bridge、采集 UI/截图、启动 PDD、分享素材、人工审核后创建上架草稿。",
        nodes=(
            WorkflowNodeDefinition(
                "open_bridge",
                "workflow.android_step",
                "打开 Android Bridge",
                arguments={"device_id": "{{device_id}}", "operation": "android.open_bridge", "needs": ["android", "android.open_bridge"], "params": {"reason": "workflow_acceptance"}},
                metadata=_blueprint_metadata(
                    "workflow.android_step",
                    24,
                    112,
                    input_kind="control",
                    output_kind="automation|signal",
                    responsibility="向 Android Control Worker 投递打开 Bridge 的命令，确认手机端接收命令通道可用。",
                    queue_label="Bridge 打开",
                    outputs=[{"name": "android_command", "kind": "automation", "required": True, "description": "已排队的 Android 命令 ID"}],
                ),
            ),
            WorkflowNodeDefinition(
                "ui_snapshot",
                "workflow.android_step",
                "采集 UI Snapshot",
                arguments={"device_id": "{{device_id}}", "operation": "android.ui_snapshot", "needs": ["android", "android.ui"], "params": {"purpose": "acceptance"}},
                depends_on=("open_bridge",),
                metadata=_blueprint_metadata(
                    "workflow.android_step",
                    232,
                    44,
                    input_kind="automation|signal",
                    output_kind="artifact|signal",
                    responsibility="通过无障碍通道采集当前 UI 树，验证设备状态可观测。",
                    queue_label="UI Snapshot",
                    outputs=[{"name": "ui_snapshot", "kind": "artifact", "required": True, "description": "手机端回传的 UI 树或摘要"}],
                ),
            ),
            WorkflowNodeDefinition(
                "request_screenshot_permission",
                "workflow.android_step",
                "请求截图授权",
                arguments={"device_id": "{{device_id}}", "operation": "android.screenshot.request_permission", "needs": ["android", "android.screenshot"], "params": {"purpose": "acceptance"}},
                depends_on=("ui_snapshot",),
                metadata=_blueprint_metadata(
                    "workflow.android_step",
                    440,
                    44,
                    input_kind="signal",
                    output_kind="automation|signal",
                    responsibility="触发屏幕捕获授权流程，保留用户授权状态作为后续截图前置证据。",
                    queue_label="截图授权",
                    outputs=[{"name": "screenshot_permission", "kind": "signal", "required": True, "description": "截图授权状态"}],
                ),
            ),
            WorkflowNodeDefinition(
                "capture_screenshot",
                "workflow.android_step",
                "采集截图",
                arguments={"device_id": "{{device_id}}", "operation": "android.screenshot.capture", "needs": ["android", "android.screenshot", "artifact"], "params": {"purpose": "acceptance", "artifact_label": "{{artifact_label}}"}},
                depends_on=("request_screenshot_permission",),
                metadata=_blueprint_metadata(
                    "workflow.android_step",
                    648,
                    44,
                    input_kind="signal",
                    output_kind="artifact|signal",
                    responsibility="采集真机截图并回传 artifact，作为 UI 命令验收证据。",
                    queue_label="截图采集",
                    outputs=[{"name": "screenshot_artifact", "kind": "artifact", "required": True, "description": "截图 artifact ID 或下载地址"}],
                ),
            ),
            WorkflowNodeDefinition(
                "launch_pdd",
                "workflow.android_step",
                "启动 PDD",
                arguments={"device_id": "{{device_id}}", "operation": "pdd.launch", "needs": ["android", "pdd"], "params": {"source": "workflow_acceptance"}},
                depends_on=("capture_screenshot",),
                metadata=_blueprint_metadata(
                    "workflow.android_step",
                    856,
                    44,
                    input_kind="artifact|signal",
                    output_kind="automation|signal",
                    responsibility="启动拼多多应用，验证平台 App 打开能力。",
                    queue_label="PDD 启动",
                    outputs=[{"name": "pdd_launch_result", "kind": "signal", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "share_product_image",
                "workflow.android_step",
                "分享商品图到 PDD",
                arguments={"device_id": "{{device_id}}", "operation": "pdd.share_image", "needs": ["android", "pdd", "artifact"], "params": {"artifact_id": "{{artifact_id}}", "caption": "{{caption}}"}},
                depends_on=("launch_pdd",),
                metadata=_blueprint_metadata(
                    "workflow.android_step",
                    856,
                    196,
                    input_kind="artifact|signal",
                    output_kind="automation|artifact|signal",
                    responsibility="把桌面产物分享到 Android 平台 App，验证 artifact 到真机的交付链路。",
                    queue_label="图片分享",
                    inputs=[{"name": "artifact_id", "kind": "artifact", "required": True, "description": "待分享商品图 artifact ID"}],
                    outputs=[{"name": "share_result", "kind": "signal", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "listing_review",
                "review_gate",
                "上架命令人工验收",
                review_gate="human_review",
                depends_on=("share_product_image",),
                metadata=_blueprint_metadata(
                    "review_gate",
                    648,
                    196,
                    input_kind="artifact|signal|review",
                    output_kind="review|control",
                    responsibility="人工确认截图、UI snapshot 和平台状态满足创建草稿前置条件。",
                    inputs=[{"name": "lifecycle_acceptance", "kind": "review", "required": True, "description": "前序 Android 命令生命周期验收结果"}],
                    outputs=[{"name": "create_listing_approval", "kind": "review", "required": True}],
                ),
            ),
            WorkflowNodeDefinition(
                "create_listing",
                "workflow.android_step",
                "创建 PDD 上架草稿",
                arguments={
                    "device_id": "{{device_id}}",
                    "operation": "pdd.create_listing",
                    "needs": ["android", "pdd"],
                    "params": {
                        "product_data_path": "{{product_data_path}}",
                        "draft_only": "{{draft_only}}",
                        "confirmed_high_risk": "{{confirmed_high_risk}}",
                    },
                },
                depends_on=("listing_review",),
                metadata=_blueprint_metadata(
                    "workflow.android_step",
                    440,
                    196,
                    input_kind="review|artifact|control",
                    output_kind="automation|signal",
                    responsibility="在人工验收后向真机投递创建上架草稿命令；发布仍应由独立审核控制。",
                    queue_label="创建草稿",
                    inputs=[{"name": "product_data_path", "kind": "artifact", "required": True}],
                    outputs=[{"name": "listing_command_result", "kind": "signal", "required": True}],
                ),
            ),
        ),
        metadata={
            "blueprint_ready": True,
            "domain": "mobile",
            "status": "candidate",
            "display_name": "Android 真机命令生命周期验收",
            "category": "移动端自动化",
            "android_worker_template": True,
            "acceptance_template": True,
            "parameters": [
                {"name": "device_id", "label": "Android 设备 ID", "type": "text", "default": "android_device"},
                {"name": "artifact_id", "label": "商品图 Artifact ID", "type": "text", "required": True},
                {"name": "artifact_label", "label": "截图标签", "type": "text", "default": "android_acceptance"},
                {"name": "caption", "label": "分享文案", "type": "textarea", "placeholder": "可选，写入分享命令的备注"},
                {"name": "product_data_path", "label": "productData 路径", "type": "text", "required": True},
                {"name": "draft_only", "label": "只创建草稿", "type": "checkbox", "default": True},
                {"name": "confirmed_high_risk", "label": "已确认高风险命令", "type": "checkbox", "default": False},
            ],
            "acceptance": {
                "required_evidence": ["command_id", "queued_at", "delivered_at", "completed_at", "device_id", "operation", "command_result"],
                "worker_id": "android_control_worker",
                "target": "android_device",
            },
        },
    )


def builtin_workflow_definitions() -> tuple[WorkflowDefinition, ...]:
    return (
        build_ecommerce_auto_listing_definition(),
        build_video_generation_definition(),
        build_free_composition_definition(),
        build_android_command_lifecycle_acceptance_definition(),
    )


def get_builtin_workflow_definition(name: str) -> WorkflowDefinition | None:
    for definition in builtin_workflow_definitions():
        if definition.name == name:
            return definition
    return None
