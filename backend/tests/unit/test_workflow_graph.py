import json
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.collaboration import create_collaboration_task, load_collaboration_tasks
from backend.app.workflow_management import handle_workflow_management_action as _handle_workflow_management_action
from backend.app.workflow_task_finalizer_port import DefaultCollaborationTaskFinalizerPort
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.orchestrator.ecommerce_task_queue import (
    STATUS_IMAGE_QUEUED,
    load_queue,
    new_task,
    save_queue,
    task_by_id,
)
from backend.orchestrator.worker_pool import WorkerDescriptor, WorkerPool
from backend.orchestrator.workflow_graph import (
    NODE_BLOCKED,
    NODE_FAILED,
    NODE_PENDING,
    NODE_RUNNING,
    NODE_SKIPPED,
    NODE_SUCCEEDED,
    NODE_WAITING,
    NODE_WAITING_REVIEW,
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_RUNNING,
    RUN_SUCCEEDED,
    RUN_WAITING,
    RUN_WAITING_REVIEW,
    WorkflowDefinition,
    WorkflowNodeDefinition,
    WorkflowRunner,
    build_android_command_lifecycle_acceptance_definition,
    build_ecommerce_auto_listing_definition,
    build_free_composition_definition,
    build_video_generation_definition,
    start_workflow_run,
)
from backend.orchestrator.workflow_store import JsonWorkflowStore
from backend.tools import BaseTool, ToolCall, ToolRegistry, ToolResult, ToolSpec
from backend.tools import build_default_tool_registry as _build_default_tool_registry


def build_default_tool_registry(*args, **kwargs):
    kwargs.setdefault(
        "workflow_store_factory",
        lambda arguments: JsonWorkflowStore(
            arguments.get("workflow_state_dir") or None,
            project_root=arguments.get("project_root") or None,
        ),
    )
    return _build_default_tool_registry(*args, **kwargs)


def handle_workflow_management_action(payload):
    with patch("backend.app.workflow_management.build_default_tool_registry", side_effect=build_default_tool_registry):
        return _handle_workflow_management_action(payload)


class OkTool(BaseTool):
    def __init__(self):
        self.spec = ToolSpec("demo.ok", "ok", "demo", "ok")

    def invoke(self, call: ToolCall) -> ToolResult:
        return ToolResult(True, "ok", data={"called": call.arguments})


class FailingTool(BaseTool):
    def __init__(self):
        self.spec = ToolSpec("demo.fail", "fail", "demo", "fail")

    def invoke(self, call: ToolCall) -> ToolResult:
        return ToolResult(False, "boom", error_code="demo_failed", metadata={"reason": "unit"})


class FlakyTool(BaseTool):
    def __init__(self):
        self.spec = ToolSpec("demo.flaky", "flaky", "demo", "flaky")
        self.calls = 0

    def invoke(self, call: ToolCall) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            return ToolResult(False, "temporary boom", error_code="temporary_failed", metadata={"stderr": "traceback line"})
        return ToolResult(True, "ok", data={"called": call.arguments, "calls": self.calls})


class PersistedRunningProbeTool(BaseTool):
    def __init__(self, store: JsonWorkflowStore, run_id: str):
        self.spec = ToolSpec("demo.persisted_running", "probe", "demo", "probe")
        self.store = store
        self.run_id = run_id
        self.observed_status = ""

    def invoke(self, call: ToolCall) -> ToolResult:
        run = self.store.load_run(self.run_id)
        self.observed_status = run.nodes["probe"].status if run is not None else "missing"
        return ToolResult(True, "ok")


class WorkflowGraphTests(unittest.TestCase):
    def test_default_workflow_execution_requires_runtime_host_fencing(self):
        registry = _build_default_tool_registry()

        direct = registry.invoke(
            ToolCall(
                "workflow.graph.run_next",
                {"run_id": "attacker-run", "allow_unfenced_execution": True},
            )
        )
        managed = _handle_workflow_management_action(
            {
                "action": "run_next",
                "run_id": "attacker-run",
                "allow_unfenced_execution": True,
            }
        )

        self.assertFalse(direct.success)
        self.assertEqual(direct.error_code, "runtime_host_execution_required")
        self.assertFalse(managed["ok"])
        self.assertEqual(managed["action_result"]["error_code"], "runtime_host_execution_required")

    def test_definition_validation_catches_missing_dependency(self):
        definition = WorkflowDefinition(
            name="bad",
            nodes=(WorkflowNodeDefinition("a", "tool_call", tool_name="demo.ok", depends_on=("missing",)),),
        )

        self.assertEqual(definition.validate(), ["missing_dependency:a:missing"])

    def test_definition_validation_catches_dependency_cycle(self):
        definition = WorkflowDefinition(
            name="cycle",
            nodes=(
                WorkflowNodeDefinition("a", "agent_task", depends_on=("b",)),
                WorkflowNodeDefinition("b", "agent_task", depends_on=("c",)),
                WorkflowNodeDefinition("c", "agent_task", depends_on=("a",)),
            ),
        )

        self.assertEqual(definition.validate(), ["dependency_cycle:a->b->c->a"])

    def test_definition_validation_rejects_incompatible_port_kinds(self):
        definition = WorkflowDefinition(
            name="ports",
            nodes=(
                WorkflowNodeDefinition(
                    "review",
                    "review_gate",
                    metadata={"ports": [{"direction": "output", "kind": "review"}]},
                ),
                WorkflowNodeDefinition(
                    "wait",
                    "waiter",
                    arguments={"wait_for": "review_signal"},
                    depends_on=("review",),
                    metadata={"ports": [{"direction": "input", "kind": "signal"}]},
                ),
            ),
        )

        self.assertEqual(definition.validate(), ["incompatible_ports:review->wait:review->signal"])

    def test_start_run_rejects_missing_required_root_input(self):
        definition = WorkflowDefinition(
            name="required",
            nodes=(
                WorkflowNodeDefinition(
                    "capture",
                    "tool_call",
                    tool_name="demo.ok",
                    arguments={"product": "{{product}}"},
                    metadata={"interface_contract": {"inputs": [{"name": "product", "required": True}]}},
                ),
            ),
        )

        with self.assertRaisesRegex(ValueError, "missing_required_input:capture:product"):
            start_workflow_run(definition, {})

    def test_runner_executes_dependency_order_and_review_gate(self):
        definition = WorkflowDefinition(
            name="demo.workflow",
            nodes=(
                WorkflowNodeDefinition("a", "tool_call", tool_name="demo.ok", arguments={"x": 1}),
                WorkflowNodeDefinition("b", "review_gate", review_gate="core_review", depends_on=("a",)),
                WorkflowNodeDefinition("c", "tool_call", tool_name="demo.ok", depends_on=("b",)),
            ),
        )
        run = start_workflow_run(definition)
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))

        run = runner.run_next(definition, run)
        run = runner.run_next(definition, run)

        self.assertEqual(run.nodes["a"].status, NODE_SUCCEEDED)
        self.assertEqual(run.nodes["b"].status, NODE_WAITING_REVIEW)
        self.assertEqual(run.status, RUN_WAITING_REVIEW)

        run = runner.approve_review_node(definition, run, "b", reviewer="tester")
        run = runner.run_next(definition, run)

        self.assertEqual(run.nodes["b"].status, NODE_SUCCEEDED)
        self.assertEqual(run.nodes["c"].status, NODE_SUCCEEDED)
        self.assertEqual(run.status, RUN_SUCCEEDED)

    def test_runner_persists_tool_node_trajectory(self):
        definition = WorkflowDefinition(
            name="trajectory.workflow",
            nodes=(WorkflowNodeDefinition("a", "tool_call", tool_name="demo.ok", arguments={"x": 1}),),
        )
        run = start_workflow_run(definition, {"actor": "tester", "user_input": "run workflow"})
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))

        with TemporaryDirectory() as tmp:
            trajectory_path = Path(tmp) / "trajectories.jsonl"
            with patch.dict("os.environ", {"SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path)}, clear=False):
                run = runner.run_next(definition, run)

            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(run.nodes["a"].status, NODE_SUCCEEDED)
        record = run.nodes["a"].outputs["metadata"]["trajectory_record"]
        self.assertEqual(record["metadata"]["source"], "workflow_runner.node")
        self.assertEqual(records[0]["metadata"]["workflow_name"], "trajectory.workflow")
        self.assertEqual(records[0]["metadata"]["node_id"], "a")
        self.assertEqual(records[0]["overall_success"], True)
        self.assertEqual(records[0]["agent_id"], "tester")

    def test_runner_persists_failed_tool_node_trajectory(self):
        definition = WorkflowDefinition(
            name="trajectory.failed.workflow",
            nodes=(WorkflowNodeDefinition("bad", "tool_call", tool_name="demo.fail"),),
        )
        run = start_workflow_run(definition, {"user_input": "fail workflow"})
        runner = WorkflowRunner(tool_registry=ToolRegistry([FailingTool()]))

        with TemporaryDirectory() as tmp:
            trajectory_path = Path(tmp) / "trajectories.jsonl"
            with patch.dict("os.environ", {"SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path)}, clear=False):
                run = runner.run_next(definition, run)

            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(run.nodes["bad"].status, NODE_FAILED)
        self.assertEqual(run.nodes["bad"].outputs["metadata"]["trajectory_record"]["bottleneck_stage"], "workflow_node")
        self.assertEqual(records[0]["metadata"]["source"], "workflow_runner.node")
        self.assertEqual(records[0]["overall_success"], False)
        self.assertEqual(records[0]["steps"][0]["error_code"], "demo_failed")

    def test_runner_records_selected_worker_when_node_declares_needs(self):
        definition = WorkflowDefinition(
            name="worker.schedule.demo",
            nodes=(
                WorkflowNodeDefinition(
                    "browser_step",
                    "tool_call",
                    tool_name="demo.ok",
                    arguments={"needs": ["browser"], "url": "https://example.com"},
                ),
            ),
        )
        run = start_workflow_run(definition)
        worker_pool = WorkerPool(
            external_workers=[
                WorkerDescriptor(
                    worker_id="remote-browser",
                    label="Remote Browser",
                    kind="remote_runtime",
                    worker_type="generic_remote_worker",
                    worker_subtype="remote_runtime_worker",
                    capabilities=("browser.open_url",),
                    capability_namespaces=("browser",),
                    targets=("browser",),
                    operations=("browser.open_url",),
                    permission_scope="remote",
                    health_status="ready",
                )
            ]
        )
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]), worker_pool=worker_pool)

        run = runner.run_next(definition, run)
        schedule = run.nodes["browser_step"].outputs["worker_schedule"]
        binding = run.nodes["browser_step"].outputs["worker_binding"]
        called = run.nodes["browser_step"].outputs["data"]["called"]

        self.assertEqual(run.nodes["browser_step"].status, NODE_SUCCEEDED)
        self.assertEqual(schedule["status"], "selected")
        self.assertEqual(schedule["selected"]["worker_id"], "remote-browser")
        self.assertEqual(schedule["candidates"][0]["matched_needs"], ["browser"])
        self.assertEqual(binding["binding_type"], "remote_browser")
        self.assertEqual(binding["execution_target"], "remote:remote-browser")
        self.assertEqual(called["worker_binding"]["binding_type"], "remote_browser")

    def test_runner_blocks_when_declared_needs_have_no_worker(self):
        definition = WorkflowDefinition(
            name="worker.schedule.missing",
            nodes=(WorkflowNodeDefinition("android_step", "tool_call", tool_name="demo.ok", arguments={"needs": ["android.adb"]}),),
        )
        run = start_workflow_run(definition)
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]), worker_pool=WorkerPool())

        run = runner.run_next(definition, run)
        schedule = run.nodes["android_step"].outputs["worker_schedule"]

        self.assertEqual(run.nodes["android_step"].status, NODE_BLOCKED)
        self.assertEqual(run.status, RUN_BLOCKED)
        self.assertEqual(schedule["status"], "missing")
        self.assertEqual(schedule["requirement"]["needs"], ["android.adb"])
        self.assertEqual(schedule["reason"], "no workers registered")

    def test_workflow_graph_tool_uses_injected_worker_pool_for_run_next(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker_pool = WorkerPool(
                external_workers=[
                    WorkerDescriptor(
                        worker_id="local-browser",
                        label="Local Browser",
                        kind="local_runtime",
                        worker_type="browser_worker",
                        worker_subtype="local_browser_worker",
                        capabilities=("browser.open_url",),
                        capability_namespaces=("browser",),
                        targets=("browser",),
                        operations=("browser.open_url",),
                        health_status="ready",
                    )
                ]
            )
            registry = build_default_tool_registry(worker_pool=worker_pool)
            registry.register(OkTool())
            saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "worker.tool.schedule.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {
                                    "node_id": "open_url",
                                    "node_type": "tool_call",
                                    "tool_name": "demo.ok",
                                    "arguments": {"needs": ["browser"], "url": "https://example.com"},
                                }
                            ],
                        },
                    },
                )
            )
            started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {"project_root": str(root), "workflow_name": "worker.tool.schedule.v1"},
                )
            )
            advanced = registry.invoke(
                ToolCall(
                    "workflow.graph.run_next",
                    {"project_root": str(root), "run_id": started.data["run"]["run_id"]},
                )
            )
            node = advanced.data["run"]["nodes"]["open_url"]
            schedule = node["outputs"]["worker_schedule"]
            binding = node["outputs"]["worker_binding"]
            called = node["outputs"]["data"]["called"]

        self.assertTrue(saved.success)
        self.assertTrue(started.success)
        self.assertTrue(advanced.success)
        self.assertEqual(node["status"], NODE_SUCCEEDED)
        self.assertEqual(schedule["status"], "selected")
        self.assertTrue(schedule["enforced"])
        self.assertEqual(schedule["selected"]["worker_id"], "local-browser")
        self.assertEqual(binding["binding_type"], "browser")
        self.assertEqual(binding["execution_target"], "browser")
        self.assertEqual(called["worker_binding"]["worker_id"], "local-browser")

    def test_workflow_graph_tool_binds_remote_browser_worker_for_tool_call(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker_pool = WorkerPool(
                external_workers=[
                    WorkerDescriptor(
                        worker_id="remote:office-pc",
                        label="Office PC",
                        kind="remote_runtime",
                        worker_type="generic_remote_worker",
                        worker_subtype="remote_runtime_worker",
                        capabilities=("browser.open_url",),
                        capability_namespaces=("browser",),
                        targets=("browser", "desktop"),
                        operations=("browser.open_url",),
                        permission_scope="remote",
                        health_status="ready",
                        metadata={"node_id": "office-pc", "workspace": "office"},
                    )
                ]
            )
            registry = build_default_tool_registry(worker_pool=worker_pool)
            registry.register(OkTool())
            registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "worker.remote_browser.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {
                                    "node_id": "open_url",
                                    "node_type": "tool_call",
                                    "tool_name": "browser.open_url",
                                    "arguments": {"needs": ["browser"], "prefer_remote": True, "url": "https://example.com"},
                                }
                            ],
                        },
                    },
                )
            )
            started = registry.invoke(ToolCall("workflow.graph.start_run", {"project_root": str(root), "workflow_name": "worker.remote_browser.v1"}))
            advanced = registry.invoke(ToolCall("workflow.graph.run_next", {"project_root": str(root), "run_id": started.data["run"]["run_id"]}))
            node = advanced.data["run"]["nodes"]["open_url"]
            binding = node["outputs"]["worker_binding"]
            execution_request = node["outputs"]["execution_request"]

        self.assertTrue(advanced.success)
        self.assertEqual(node["status"], NODE_SUCCEEDED)
        self.assertEqual(binding["binding_type"], "remote_browser")
        self.assertEqual(binding["execution_target"], "remote:office-pc")
        self.assertEqual(binding["remote_node_id"], "office-pc")
        self.assertEqual(execution_request["target"], "remote:office-pc")
        self.assertEqual(execution_request["operation"], "browser_open_url")
        self.assertEqual(execution_request["params"]["node_id"], "office-pc")
        self.assertEqual(execution_request["params"]["remote_target"], "browser")
        self.assertEqual(execution_request["params"]["worker_binding"]["execution_target"], "remote:office-pc")

    def test_runner_requires_jury_report_for_jury_review_gate(self):
        definition = WorkflowDefinition(
            name="demo.jury.workflow",
            nodes=(
                WorkflowNodeDefinition("a", "tool_call", tool_name="demo.ok"),
                WorkflowNodeDefinition("b", "review_gate", review_gate="code_jury", depends_on=("a",)),
            ),
        )
        run = start_workflow_run(definition)
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))

        run = runner.run_next(definition, run)
        run = runner.run_next(definition, run)
        blocked = runner.approve_review_node(definition, run, "b", reviewer="tester")
        approved = runner.approve_review_node(
            definition,
            run,
            "b",
            reviewer="tester",
            review_payload={
                "jury_report": {
                    "report_id": "jury_report_workflow",
                    "decision": "approved",
                    "overall_score": 90,
                    "summary": {"structured_review_count": 1},
                    "package": {
                        "package_id": "demo.jury.workflow:b",
                        "review_type": "code",
                    },
                    "promotion_gate": {"eligible": True},
                }
            },
        )

        self.assertEqual(blocked.nodes["b"].status, NODE_WAITING_REVIEW)
        self.assertEqual(blocked.nodes["b"].outputs["jury_gate"]["allowed"], False)
        self.assertEqual(blocked.status, RUN_WAITING_REVIEW)
        self.assertEqual(approved.nodes["b"].status, NODE_SUCCEEDED)
        self.assertEqual(approved.nodes["b"].outputs["jury_gate"]["allowed"], True)
        self.assertEqual(approved.nodes["b"].outputs["jury_report_id"], "jury_report_workflow")

    def test_ecommerce_auto_listing_definition_is_blueprint_ready(self):
        definition = build_ecommerce_auto_listing_definition()

        self.assertEqual(definition.validate(), [])
        self.assertEqual(definition.metadata["blueprint_ready"], True)
        self.assertEqual(definition.nodes[0].node_id, "product_selection")
        self.assertEqual(definition.nodes[-1].node_id, "publish_or_hold")

    def test_video_generation_definition_is_blueprint_ready(self):
        definition = build_video_generation_definition()

        self.assertEqual(definition.validate(), [])
        self.assertEqual(definition.metadata["display_name"], "视频生成")
        self.assertEqual(definition.nodes[0].node_id, "brief")
        self.assertEqual(definition.nodes[-1].node_id, "delivery_package")

    def test_android_command_lifecycle_acceptance_definition_is_blueprint_ready(self):
        definition = build_android_command_lifecycle_acceptance_definition()
        operations = {str(node.arguments.get("operation") or "") for node in definition.nodes if node.node_type == "workflow.android_step"}

        self.assertEqual(definition.validate(), [])
        self.assertTrue(definition.metadata["android_worker_template"])
        self.assertTrue(definition.metadata["acceptance_template"])
        self.assertIn("android.ui_snapshot", operations)
        self.assertIn("android.screenshot.capture", operations)
        self.assertIn("pdd.create_listing", operations)
        android_steps = [node for node in definition.nodes if node.node_type == "workflow.android_step"]
        self.assertTrue(all(node.arguments.get("needs") for node in android_steps))
        self.assertIn("pdd", next(node for node in android_steps if node.node_id == "create_listing").arguments["needs"])
        self.assertEqual(definition.nodes[-1].node_id, "create_listing")

    def test_free_composition_definition_is_blueprint_ready(self):
        definition = build_free_composition_definition()

        self.assertEqual(definition.validate(), [])
        self.assertEqual(definition.metadata["display_name"], "自由组合工作流")
        self.assertTrue(definition.metadata["composition_template"])
        self.assertEqual([node.node_type for node in definition.nodes], ["agent_task", "subgraph", "subgraph", "review_gate"])

    def test_workflow_management_can_archive_delete_and_cleanup_runs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = WorkflowDefinition(
                name="cleanup.demo.v1",
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="writer_agent"),),
            )
            store = JsonWorkflowStore(project_root=root)
            store.save_definition(definition)
            archived_candidate = start_workflow_run(definition, run_id="wfr_old_done")
            archived_candidate = replace(archived_candidate, status="succeeded", updated_at="2026-01-01T00:00:00+00:00")
            active = start_workflow_run(definition, run_id="wfr_active")
            active = replace(active, status="running", updated_at="2026-01-02T00:00:00+00:00")
            recent = start_workflow_run(definition, run_id="wfr_recent_done")
            recent = replace(recent, status="succeeded", updated_at="2026-01-03T00:00:00+00:00")
            store.save_run(archived_candidate)
            store.save_run(active)
            store.save_run(recent)

            archived = handle_workflow_management_action({"project_root": str(root), "action": "archive_run", "run_id": "wfr_old_done", "workflow_name": "cleanup.demo.v1"})
            deleted = handle_workflow_management_action({"project_root": str(root), "action": "delete_run", "run_id": "wfr_recent_done", "workflow_name": "cleanup.demo.v1"})
            cleanup = handle_workflow_management_action({"project_root": str(root), "action": "cleanup_runs", "workflow_name": "cleanup.demo.v1", "keep_recent": 0})

            self.assertTrue(archived["ok"])
            self.assertEqual(archived["action_result"]["data"]["run"]["status"], "archived")
            self.assertTrue(deleted["ok"])
            self.assertNotIn("wfr_recent_done", {run["run_id"] for run in deleted["workflows"]["runs"]})
            self.assertTrue(cleanup["ok"])
            self.assertEqual(cleanup["action_result"]["data"]["removed"], 1)
            remaining = {run["run_id"]: run["status"] for run in cleanup["workflows"]["runs"]}
            self.assertEqual(remaining, {"wfr_active": "running"})

    def test_workflow_store_does_not_audit_unchanged_definition_heartbeat(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = WorkflowDefinition(
                name="heartbeat.demo.v1",
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="writer_agent"),),
            )
            store = JsonWorkflowStore(project_root=root)
            store.save_definition(definition, actor="workflow_auto_advance")
            store.save_definition(definition, actor="workflow_auto_advance")

            events = store.list_audit_events(workflow_name="heartbeat.demo.v1", limit=20)
            self.assertEqual([event["action"] for event in events], ["definition_saved"])

    def test_workflow_store_persists_runtime_context_and_finalizer_verdict(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = WorkflowDefinition(
                name="runtime.contract.demo.v1",
                metadata={"success_criteria": ["done"]},
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="writer_agent"),),
            )
            store = JsonWorkflowStore(project_root=root)
            store.save_definition(definition)
            run = start_workflow_run(definition, run_id="wfr_runtime_contract")
            run = replace(
                run,
                status="succeeded",
                events=[
                    *run.events,
                    {"at": "2026-06-29T00:00:00+00:00", "type": "success_checks", "payload": {"success_checks": {"done": True}}},
                ],
            )

            store.save_run(run)
            context_records = store.list_runtime_context_patches(run_id="wfr_runtime_contract")
            verdicts = store.list_finalizer_verdicts(run_id="wfr_runtime_contract")

            self.assertEqual(len(context_records), 1)
            self.assertEqual(context_records[0]["context_id"], "workflow:wfr_runtime_contract")
            self.assertEqual(context_records[0]["patches"][0]["path"], "/workflow/run")
            self.assertEqual(len(verdicts), 1)
            self.assertEqual(verdicts[0]["verdict"]["decision"], "commit")
            self.assertEqual(verdicts[0]["verdict"]["next_status"], "COMMITTED")

    def test_workflow_finalizer_updates_bound_collaboration_task(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_collaboration_task({"task_id": "collab-1", "title": "Publish", "owner": "codex"}, root / "state" / "collaboration")
            definition = WorkflowDefinition(
                name="runtime.task.demo.v1",
                metadata={"success_criteria": ["done"]},
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="writer_agent"),),
            )
            store = JsonWorkflowStore(
                project_root=root,
                collaboration_task_port=DefaultCollaborationTaskFinalizerPort(),
            )
            store.save_definition(definition)
            run = start_workflow_run(definition, {"task_id": "collab-1"}, run_id="wfr_task_commit")
            run = replace(
                run,
                status="succeeded",
                events=[
                    *run.events,
                    {"at": "2026-06-29T00:00:00+00:00", "type": "success_checks", "payload": {"success_checks": {"done": True}}},
                ],
            )

            store.save_run(run)
            task = next(item for item in load_collaboration_tasks(root / "state" / "collaboration") if item.task_id == "collab-1")
            sync_event = next(
                event
                for event in store.list_audit_events(workflow_name="runtime.task.demo.v1")
                if event["action"] == "workflow_finalizer_task_sync"
            )

            self.assertEqual(task.status, "complete")
            self.assertIn("finalizer commit -> COMMITTED", task.note)
            self.assertEqual(sync_event["payload"]["status"], "updated")

    def test_workflow_finalizer_blocks_bound_task_when_success_criteria_missing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_collaboration_task({"task_id": "collab-2", "title": "Publish", "owner": "codex"}, root / "state" / "collaboration")
            definition = WorkflowDefinition(
                name="runtime.task.blocked.v1",
                metadata={"success_criteria": ["done"]},
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="writer_agent"),),
            )
            store = JsonWorkflowStore(
                project_root=root,
                collaboration_task_port=DefaultCollaborationTaskFinalizerPort(),
            )
            store.save_definition(definition)
            run = start_workflow_run(definition, {"task_id": "collab-2"}, run_id="wfr_task_block")
            run = replace(run, status="succeeded")

            store.save_run(run)
            task = next(item for item in load_collaboration_tasks(root / "state" / "collaboration") if item.task_id == "collab-2")
            verdict = store.list_finalizer_verdicts(run_id="wfr_task_block")[-1]["verdict"]

            self.assertEqual(verdict["decision"], "retry")
            self.assertEqual(task.status, "blocked")

    def test_workflow_finalizer_updates_bound_ecommerce_task(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = load_queue(project_root=root)
            queue["tasks"].append(new_task("eco-1", "source_image_upload", STATUS_IMAGE_QUEUED, "test", {"title": "Product"}))
            save_queue(queue, project_root=root)
            definition = WorkflowDefinition(
                name="runtime.ecommerce.task.demo.v1",
                metadata={"success_criteria": ["done"]},
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="commerce_agent"),),
            )
            store = JsonWorkflowStore(project_root=root)
            store.save_definition(definition)
            run = start_workflow_run(definition, {"ecommerce_task_id": "eco-1"}, run_id="wfr_ecommerce_commit")
            run = replace(
                run,
                status="succeeded",
                events=[
                    *run.events,
                    {"at": "2026-06-29T00:00:00+00:00", "type": "success_checks", "payload": {"success_checks": {"done": True}}},
                ],
            )

            store.save_run(run)
            updated_task = task_by_id(load_queue(project_root=root), "eco-1")
            sync_event = next(
                event
                for event in store.list_audit_events(workflow_name="runtime.ecommerce.task.demo.v1")
                if event["action"] == "workflow_finalizer_task_sync"
            )

            self.assertIsNotNone(updated_task)
            self.assertEqual(updated_task["status"], "workflow_complete")
            self.assertEqual(updated_task["workflow_run_id"], "wfr_ecommerce_commit")
            self.assertEqual(updated_task["checks"]["workflow_finalizer"]["decision"], "commit")
            self.assertEqual(sync_event["payload"]["metadata"]["queue"], "ecommerce")
            self.assertEqual(sync_event["payload"]["status"], "updated")

    def test_workflow_finalizer_blocks_bound_ecommerce_task_when_success_criteria_missing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = load_queue(project_root=root)
            queue["tasks"].append(new_task("eco-2", "source_image_upload", STATUS_IMAGE_QUEUED, "test", {"title": "Product"}))
            save_queue(queue, project_root=root)
            definition = WorkflowDefinition(
                name="runtime.ecommerce.task.blocked.v1",
                metadata={"success_criteria": ["done"]},
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="commerce_agent"),),
            )
            store = JsonWorkflowStore(project_root=root)
            store.save_definition(definition)
            run = start_workflow_run(definition, {"metadata": {"ecommerce_task_id": "eco-2"}}, run_id="wfr_ecommerce_block")
            run = replace(run, status="succeeded")

            store.save_run(run)
            updated_task = task_by_id(load_queue(project_root=root), "eco-2")
            verdict = store.list_finalizer_verdicts(run_id="wfr_ecommerce_block")[-1]["verdict"]

            self.assertIsNotNone(updated_task)
            self.assertEqual(verdict["decision"], "retry")
            self.assertEqual(updated_task["status"], "workflow_blocked")
            self.assertEqual(updated_task["checks"]["workflow_finalizer"]["decision"], "retry")
            self.assertIn("missing_success_criteria", updated_task["checks"]["workflow_finalizer"]["reasons"])

    def test_workflow_finalizer_skips_task_sync_without_binding(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = WorkflowDefinition(
                name="runtime.task.skip.v1",
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="writer_agent"),),
            )
            store = JsonWorkflowStore(project_root=root)
            store.save_definition(definition)
            run = start_workflow_run(definition, run_id="wfr_task_skip")
            run = replace(run, status="succeeded")

            store.save_run(run)
            sync_event = next(
                event
                for event in store.list_audit_events(workflow_name="runtime.task.skip.v1")
                if event["action"] == "workflow_finalizer_task_sync"
            )

            self.assertEqual(sync_event["payload"]["status"], "skipped")

    def test_workflow_management_snapshot_includes_runtime_contract(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = WorkflowDefinition(
                name="runtime.snapshot.demo.v1",
                metadata={"success_criteria": ["done"]},
                nodes=(WorkflowNodeDefinition("a", "agent_task", assigned_agent="writer_agent"),),
            )
            store = JsonWorkflowStore(project_root=root)
            store.save_definition(definition)
            run = start_workflow_run(definition, run_id="wfr_runtime_snapshot")
            run = replace(
                run,
                status="succeeded",
                events=[
                    *run.events,
                    {"at": "2026-06-29T00:00:00+00:00", "type": "success_checks", "payload": {"success_checks": {"done": True}}},
                ],
            )
            store.save_run(run)

            snapshot = handle_workflow_management_action(
                {"project_root": str(root), "action": "snapshot", "workflow_name": "runtime.snapshot.demo.v1"}
            )
            run_snapshot = next(item for item in snapshot["workflows"]["runs"] if item["run_id"] == "wfr_runtime_snapshot")

            self.assertTrue(snapshot["ok"])
            self.assertEqual(run_snapshot["runtime_contract"]["context_record"]["context_id"], "workflow:wfr_runtime_snapshot")
            self.assertEqual(run_snapshot["runtime_contract"]["finalizer_verdict"]["verdict"]["decision"], "commit")

    def test_workflow_runner_resolves_arguments_from_run_inputs(self):
        definition = WorkflowDefinition(
            name="demo.workflow",
            nodes=(WorkflowNodeDefinition("a", "tool_call", tool_name="demo.ok", arguments={"value": "{{value}}", "nested": {"item": "{{value}}"}, "items": ["{{value}}"]}),),
        )
        run = start_workflow_run(definition, {"value": 42})
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))

        run = runner.run_next(definition, run)

        self.assertEqual(run.nodes["a"].outputs["data"]["called"], {"value": 42, "nested": {"item": 42}, "items": [42]})

    def test_workflow_runner_resolves_arguments_from_node_outputs(self):
        definition = WorkflowDefinition(
            name="node.outputs.workflow",
            nodes=(
                WorkflowNodeDefinition("source", "tool_call", tool_name="demo.ok", arguments={"product": "{{product}}"}),
                WorkflowNodeDefinition("next", "tool_call", tool_name="demo.ok", arguments={"product": "{{node.source.outputs.data.called.product}}"}, depends_on=("source",)),
            ),
        )
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))
        run = start_workflow_run(definition, {"product": "sku-1"})

        run = runner.run_next(definition, run)
        run = runner.run_next(definition, run)

        self.assertEqual(run.nodes["next"].outputs["data"]["called"], {"product": "sku-1"})

    def test_branch_skips_unselected_path_and_allows_join(self):
        definition = WorkflowDefinition(
            name="branch.skip.workflow",
            nodes=(
                WorkflowNodeDefinition("choose", "branch", arguments={"condition": True, "routes": {"true": ["left"], "false": ["right"]}}),
                WorkflowNodeDefinition("left", "tool_call", tool_name="demo.ok", depends_on=("choose",)),
                WorkflowNodeDefinition("right", "tool_call", tool_name="demo.ok", depends_on=("choose",)),
                WorkflowNodeDefinition("join", "tool_call", tool_name="demo.ok", depends_on=("left", "right")),
            ),
        )
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))
        run = start_workflow_run(definition)

        run = runner.run_next(definition, run)
        run = runner.run_next(definition, run)
        run = runner.run_next(definition, run)

        self.assertEqual(run.nodes["right"].status, NODE_SKIPPED)
        self.assertEqual(run.nodes["join"].status, NODE_SUCCEEDED)
        self.assertEqual(run.status, RUN_SUCCEEDED)

    def test_retry_policy_requeues_failed_node_and_keeps_error_detail(self):
        flaky = FlakyTool()
        definition = WorkflowDefinition(
            name="retry.workflow",
            nodes=(WorkflowNodeDefinition("flaky", "tool_call", tool_name="demo.flaky", retry_policy={"max_attempts": 2, "backoff_seconds": 0}),),
        )
        runner = WorkflowRunner(tool_registry=ToolRegistry([flaky]))
        run = start_workflow_run(definition)

        run = runner.run_next(definition, run)

        self.assertEqual(run.status, RUN_RUNNING)
        self.assertEqual(run.nodes["flaky"].status, NODE_PENDING)
        self.assertEqual(run.nodes["flaky"].attempts, 1)
        self.assertEqual(run.nodes["flaky"].outputs["error_detail"]["message"], "temporary boom")
        self.assertEqual(run.nodes["flaky"].outputs["error_detail"]["stderr"], "traceback line")

        run = runner.run_next(definition, run)

        self.assertEqual(run.nodes["flaky"].status, NODE_SUCCEEDED)
        self.assertEqual(run.nodes["flaky"].outputs["data"]["calls"], 2)
        self.assertEqual(run.status, RUN_SUCCEEDED)

    def test_workflow_runner_resolves_embedded_input_and_full_node_output_references(self):
        definition = WorkflowDefinition(
            name="interpolation.workflow",
            nodes=(
                WorkflowNodeDefinition("source", "tool_call", tool_name="demo.ok", arguments={"product": "{{input.product}}"}),
                WorkflowNodeDefinition(
                    "next",
                    "tool_call",
                    tool_name="demo.ok",
                    arguments={
                        "label": "sku={{input.product}}/{{node.source.outputs.data.called.product}}",
                        "source_outputs": "{{node.source.outputs}}",
                    },
                    depends_on=("source",),
                ),
            ),
        )
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))
        run = start_workflow_run(definition, {"product": "sku-1"})

        run = runner.run_next(definition, run)
        run = runner.run_next(definition, run)

        called = run.nodes["next"].outputs["data"]["called"]
        self.assertEqual(called["label"], "sku=sku-1/sku-1")
        self.assertEqual(called["source_outputs"]["data"]["called"]["product"], "sku-1")

    def test_branch_supports_structured_numeric_conditions(self):
        definition = WorkflowDefinition(
            name="branch.workflow",
            nodes=(
                WorkflowNodeDefinition(
                    "choose",
                    "branch",
                    arguments={
                        "condition": {"left": "{{score}}", "op": ">=", "right": 80},
                        "routes": {"true": ["publish"], "false": ["review"]},
                    },
                ),
            ),
        )
        runner = WorkflowRunner(tool_registry=ToolRegistry([OkTool()]))
        run = start_workflow_run(definition, {"score": 90})

        run = runner.run_next(definition, run)

        self.assertEqual(run.nodes["choose"].outputs["selected_route"], "true")
        self.assertEqual(run.nodes["choose"].outputs["selected_node_ids"], ["publish"])

    def test_open_custom_node_waits_and_accepts_signal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_tool_registry()
            saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "custom.static_ip_demo.v1",
                            "version": "0.1.0",
                            "description": "custom open node demo",
                            "nodes": [
                                {
                                    "node_id": "static_ip_rotation",
                                    "node_type": "custom.static_ip_rotation",
                                    "label": "Static IP Rotation",
                                    "assigned_agent": "ecommerce",
                                    "arguments": {
                                        "executor": "external_callback",
                                        "callback_id": "{{ip_change_callback_id}}",
                                        "static_ip_pool": "{{static_ip_pool}}",
                                        "rotation_policy": {"mode": "sticky", "ttl_minutes": 120},
                                    },
                                    "depends_on": [],
                                }
                            ],
                            "metadata": {"display_name": "Custom Static IP Demo", "category": "custom"},
                        },
                    },
                )
            )
            started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {
                        "project_root": str(root),
                        "workflow_name": "custom.static_ip_demo.v1",
                        "inputs": {"ip_change_callback_id": "ip-change-1", "static_ip_pool": "pool-a"},
                    },
                )
            )
            run_id = started.data["run"]["run_id"]
            advanced = registry.invoke(ToolCall("workflow.graph.run_next", {"project_root": str(root), "run_id": run_id}))
            signaled = registry.invoke(
                ToolCall(
                    "workflow.graph.signal_node",
                    {
                        "project_root": str(root),
                        "run_id": run_id,
                        "node_id": "static_ip_rotation",
                        "signal_payload": {"new_ip": "203.0.113.10", "proxy_profile": "pool-a/sticky"},
                    },
                )
            )

            self.assertTrue(saved.success)
            self.assertTrue(started.success)
            self.assertTrue(advanced.success)
            self.assertTrue(signaled.success)
            self.assertEqual(advanced.data["run"]["nodes"]["static_ip_rotation"]["status"], NODE_WAITING)
            self.assertEqual(advanced.data["run"]["nodes"]["static_ip_rotation"]["outputs"]["callback_id"], "ip-change-1")
            self.assertEqual(advanced.data["run"]["nodes"]["static_ip_rotation"]["outputs"]["arguments"]["static_ip_pool"], "pool-a")
            self.assertEqual(signaled.data["run"]["nodes"]["static_ip_rotation"]["status"], NODE_SUCCEEDED)
            self.assertEqual(signaled.data["run"]["nodes"]["static_ip_rotation"]["outputs"]["signal_payload"]["new_ip"], "203.0.113.10")

    def test_android_workflow_step_queues_command_and_reconciles_result(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            android_state = root / "android-companion.json"
            registry = build_default_tool_registry()
            saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "android.step.demo.v1",
                            "version": "0.1.0",
                            "description": "android step demo",
                            "nodes": [
                                {
                                    "node_id": "open_app",
                                    "node_type": "workflow.android_step",
                                    "label": "打开手机 App",
                                    "arguments": {
                                        "device_id": "{{device_id}}",
                                        "operation": "app.launch",
                                        "params": {"app_name": "{{app_name}}"},
                                    },
                                    "depends_on": [],
                                }
                            ],
                            "metadata": {"display_name": "Android Step Demo", "category": "mobile"},
                        },
                    },
                )
            )
            started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {
                        "project_root": str(root),
                        "workflow_name": "android.step.demo.v1",
                        "inputs": {"device_id": "phone1", "app_name": "Feishu"},
                    },
                )
            )
            run_id = started.data["run"]["run_id"]
            advanced = registry.invoke(
                ToolCall(
                    "workflow.graph.run_next",
                    {
                        "project_root": str(root),
                        "android_companion_state": str(android_state),
                        "run_id": run_id,
                    },
                )
            )
            node = advanced.data["run"]["nodes"]["open_app"]
            command_id = node["outputs"]["command_id"]
            self.assertEqual(node["outputs"]["worker_requirement"]["worker_type"], "device_worker")
            self.assertEqual(node["outputs"]["worker_requirement"]["worker_subtype"], "android_device_worker")
            self.assertIn("android", node["outputs"]["worker_requirement"]["needs"])
            self.assertIn("app.launch", node["outputs"]["worker_requirement"]["needs"])
            companion = AndroidCompanionStore(android_state)
            drained = companion.drain_commands("phone1")
            companion.update_heartbeat(
                {
                    "device_id": "phone1",
                    "command_results": [
                        {
                            "command_id": command_id,
                            "operation": "app.launch",
                            "status": "completed",
                            "message": "启动应用: Feishu",
                            "result": {"foreground": "Feishu"},
                        }
                    ],
                }
            )
            reconciled = registry.invoke(
                ToolCall(
                    "workflow.graph.run_next",
                    {
                        "project_root": str(root),
                        "android_companion_state": str(android_state),
                        "run_id": run_id,
                    },
                )
            )

            self.assertTrue(saved.success)
            self.assertTrue(started.success)
            self.assertTrue(advanced.success)
            self.assertTrue(reconciled.success)
            self.assertEqual(node["status"], NODE_WAITING)
            self.assertEqual(drained[0]["operation"], "app.launch")
            self.assertEqual(drained[0]["params"]["app_name"], "Feishu")
            self.assertEqual(reconciled.data["run"]["nodes"]["open_app"]["status"], NODE_SUCCEEDED)
            self.assertEqual(reconciled.data["run"]["status"], RUN_SUCCEEDED)
            self.assertEqual(reconciled.data["run"]["nodes"]["open_app"]["outputs"]["command_result"]["result"]["foreground"], "Feishu")
            acceptance = reconciled.data["run"]["nodes"]["open_app"]["outputs"]["lifecycle_acceptance"]
            self.assertTrue(acceptance["accepted"])
            self.assertTrue(acceptance["evidence_complete"])
            self.assertEqual(acceptance["operation"], "app.launch")
            self.assertEqual(acceptance["command_id"], command_id)

    def test_android_workflow_step_binds_default_device_from_scheduled_worker(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            android_state = root / "android-companion.json"
            companion = AndroidCompanionStore(android_state)
            companion.update_heartbeat(
                {
                    "device_id": "phone1",
                    "device_state": {
                        "battery_pct": 88,
                        "screen_capture_authorized": True,
                        "pdd_accessibility_granted": True,
                        "pdd_accessibility_connected": True,
                    },
                    "capabilities": ["app.launch", "android.ui_snapshot"],
                    "command_catalog": [{"operation": "app.launch", "required_capabilities": ["app.launch"]}],
                }
            )
            from backend.orchestrator.android_worker_registry import android_worker_descriptor

            snapshot = companion.snapshot()
            worker_pool = WorkerPool(external_workers=[android_worker_descriptor(snapshot["worker"], companion=snapshot)])
            registry = build_default_tool_registry(worker_pool=worker_pool)
            saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "android.step.autobind.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {
                                    "node_id": "open_app",
                                    "node_type": "workflow.android_step",
                                    "arguments": {
                                        "operation": "app.launch",
                                        "params": {"app_name": "Feishu"},
                                    },
                                }
                            ],
                        },
                    },
                )
            )
            started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {"project_root": str(root), "workflow_name": "android.step.autobind.v1"},
                )
            )
            advanced = registry.invoke(
                ToolCall(
                    "workflow.graph.run_next",
                    {
                        "project_root": str(root),
                        "android_companion_state": str(android_state),
                        "run_id": started.data["run"]["run_id"],
                    },
                )
            )
            node = advanced.data["run"]["nodes"]["open_app"]
            drained = companion.drain_commands("phone1")

        self.assertTrue(saved.success)
        self.assertTrue(started.success)
        self.assertTrue(advanced.success)
        self.assertEqual(node["status"], NODE_WAITING)
        self.assertEqual(node["outputs"]["device_id"], "phone1")
        self.assertEqual(node["outputs"]["device_selection"]["source"], "worker_default")
        self.assertEqual(node["outputs"]["worker_schedule"]["status"], "selected")
        self.assertEqual(node["outputs"]["worker_schedule"]["selected"]["worker_id"], "android_control_worker")
        self.assertEqual(drained[0]["operation"], "app.launch")

    def test_android_workflow_step_blocks_high_risk_permission_tier(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            android_state = root / "android-companion.json"
            registry = build_default_tool_registry()
            registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "android.step.highrisk.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {
                                    "node_id": "shell_delete",
                                    "node_type": "workflow.android_step",
                                    "arguments": {
                                        "device_id": "phone1",
                                        "operation": "adb.shell.rm",
                                        "params": {"command": "rm -rf /sdcard"},
                                    },
                                }
                            ],
                        },
                    },
                )
            )
            started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {
                        "project_root": str(root),
                        "workflow_name": "android.step.highrisk.v1",
                    },
                )
            )
            advanced = registry.invoke(
                ToolCall(
                    "workflow.graph.run_next",
                    {
                        "project_root": str(root),
                        "android_companion_state": str(android_state),
                        "run_id": started.data["run"]["run_id"],
                    },
                )
            )
            companion = AndroidCompanionStore(android_state)

        node = advanced.data["run"]["nodes"]["shell_delete"]
        self.assertTrue(advanced.success)
        self.assertEqual(advanced.data["run"]["status"], RUN_BLOCKED)
        self.assertEqual(node["status"], NODE_BLOCKED)
        self.assertIn("Android command tier high_risk", node["error"])
        self.assertEqual(companion.snapshot()["pending_command_count"], 0)

    def test_workflow_graph_tools_persist_definition_and_run_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_tool_registry()

            saved = registry.invoke(ToolCall("workflow.graph.save_ecommerce_definition", {"project_root": str(root)}))
            started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {
                        "project_root": str(root),
                        "workflow_name": "ecommerce.auto_listing.v1",
                        "inputs": {"include_latest": False, "project_root": str(root)},
                    },
                )
            )
            run_id = started.data["run"]["run_id"]
            claimed = registry.invoke(
                ToolCall(
                    "workflow.graph.claim_agent_task",
                    {
                        "project_root": str(root),
                        "run_id": run_id,
                        "node_id": "product_selection",
                        "agent_id": "ecommerce",
                    },
                )
            )
            assigned = registry.invoke(
                ToolCall(
                    "workflow.graph.assign_agent",
                    {
                        "project_root": str(root),
                        "run_id": run_id,
                        "node_id": "source_capture",
                        "agent_id": "vision_model",
                    },
                )
            )
            completed = registry.invoke(
                ToolCall(
                    "workflow.graph.complete_agent_task",
                    {
                        "project_root": str(root),
                        "run_id": run_id,
                        "node_id": "product_selection",
                        "agent_id": "ecommerce",
                        "outputs": {"selected": 1},
                    },
                )
            )
            dry = registry.invoke(ToolCall("workflow.graph.run_next", {"project_root": str(root), "run_id": run_id, "dry_run": True}))
            listed = registry.invoke(ToolCall("workflow.graph.list_runs", {"project_root": str(root), "workflow_name": "ecommerce.auto_listing.v1"}))
            saved_video = registry.invoke(ToolCall("workflow.graph.save_builtin_definition", {"project_root": str(root), "workflow_name": "content.video_generation.v1"}))
            saved_composition = registry.invoke(ToolCall("workflow.graph.save_builtin_definition", {"project_root": str(root), "workflow_name": "workflow.free_composition.v1"}))
            custom_saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "custom.demo.v1",
                            "version": "0.1.0",
                            "description": "custom",
                            "nodes": [
                                {
                                    "node_id": "custom_start",
                                    "node_type": "agent_task",
                                    "label": "Custom Start",
                                    "assigned_agent": "programming",
                                    "arguments": {},
                                    "depends_on": [],
                                }
                            ],
                            "metadata": {"display_name": "Custom Demo", "category": "custom"},
                        },
                    },
                )
            )
            custom_started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {
                        "project_root": str(root),
                        "workflow_name": "custom.demo.v1",
                        "inputs": {"project_root": str(root)},
                    },
                )
            )
            custom_saved_v2 = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "custom.demo.v1",
                            "version": "0.2.0",
                            "description": "custom v2",
                            "nodes": [
                                {
                                    "node_id": "custom_start",
                                    "node_type": "agent_task",
                                    "label": "Custom Start V2",
                                    "assigned_agent": "programming",
                                    "arguments": {},
                                    "depends_on": [],
                                }
                            ],
                            "metadata": {"display_name": "Custom Demo", "category": "custom"},
                        },
                    },
                )
            )
            from backend.orchestrator.workflow_store import JsonWorkflowStore

            store = JsonWorkflowStore(project_root=root)
            versions = store.list_definition_versions("custom.demo.v1")
            rolled_back = registry.invoke(
                ToolCall(
                    "workflow.graph.rollback_definition",
                    {
                        "project_root": str(root),
                        "workflow_name": "custom.demo.v1",
                        "version_id": versions[-1]["version_id"],
                    },
                )
            )
            custom_deleted = registry.invoke(ToolCall("workflow.graph.delete_definition", {"project_root": str(root), "workflow_name": "custom.demo.v1"}))
            started_video = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {
                        "project_root": str(root),
                        "workflow_name": "content.video_generation.v1",
                        "inputs": {"prompt": "生成一段产品广告视频", "duration_seconds": 8},
                    },
                )
            )
            composed = registry.invoke(
                ToolCall(
                    "workflow.graph.compose_definition",
                    {
                        "project_root": str(root),
                        "workflow_name": "custom.combo.ecom_video.v1",
                        "display_name": "电商 + 视频组合",
                        "mode": "serial",
                        "components": [
                            {"workflow_name": "ecommerce.auto_listing.v1", "label": "电商上架", "inputs": {"project_root": str(root)}},
                            {"workflow_name": "content.video_generation.v1", "label": "商品视频", "inputs": {"prompt": "生成商品视频"}},
                        ],
                    },
                )
            )
            composed_started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {
                        "project_root": str(root),
                        "workflow_name": "custom.combo.ecom_video.v1",
                        "inputs": {"project_root": str(root)},
                    },
                )
            )

            self.assertTrue(saved.success)
            self.assertTrue(started.success)
            self.assertTrue(claimed.success)
            self.assertTrue(assigned.success)
            self.assertTrue(completed.success)
            self.assertTrue(dry.success)
            self.assertTrue(listed.success)
            self.assertTrue(saved_video.success)
            self.assertTrue(saved_composition.success)
            self.assertTrue(custom_saved.success)
            self.assertTrue(custom_started.success)
            self.assertTrue(custom_saved_v2.success)
            self.assertTrue(rolled_back.success)
            self.assertTrue(custom_deleted.success)
            self.assertTrue(started_video.success)
            self.assertTrue(composed.success)
            self.assertTrue(composed_started.success)
            self.assertEqual(claimed.data["run"]["nodes"]["product_selection"]["status"], NODE_RUNNING)
            self.assertEqual(assigned.data["run"]["nodes"]["source_capture"]["assigned_agent"], "vision_model")
            self.assertEqual(completed.data["run"]["nodes"]["product_selection"]["status"], NODE_SUCCEEDED)
            self.assertEqual(dry.data["run"]["nodes"]["source_capture"]["status"], NODE_SUCCEEDED)
            self.assertEqual(custom_started.data["run"]["nodes"]["custom_start"]["assigned_agent"], "programming")
            self.assertEqual(rolled_back.data["definition"]["version"], "0.1.0")
            self.assertGreaterEqual(len(store.list_audit_events(workflow_name="custom.demo.v1")), 3)
            self.assertEqual(started_video.data["run"]["workflow_name"], "content.video_generation.v1")
            self.assertEqual(composed.data["definition"]["nodes"][1]["depends_on"], ["ecommerce_auto_listing_v1"])
            self.assertEqual(composed_started.data["run"]["workflow_name"], "custom.combo.ecom_video.v1")
            self.assertTrue((root / "state" / "workflows" / "definitions.json").exists())
            self.assertEqual(listed.data["count"], 1)

    def test_workflow_graph_auto_advance_runs_executes_machine_nodes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_tool_registry()
            registry.register(OkTool())
            saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "auto.advance.demo.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {"node_id": "a", "node_type": "tool_call", "tool_name": "demo.ok", "arguments": {"value": 1}},
                                {"node_id": "b", "node_type": "tool_call", "tool_name": "demo.ok", "arguments": {"value": "{{node.a.outputs.data.called.value}}"}, "depends_on": ["a"]},
                            ],
                        },
                    },
                )
            )
            started = registry.invoke(ToolCall("workflow.graph.start_run", {"project_root": str(root), "workflow_name": "auto.advance.demo.v1"}))
            advanced = registry.invoke(ToolCall("workflow.graph.auto_advance_runs", {"project_root": str(root), "run_id": started.data["run"]["run_id"], "max_steps_per_run": 5}))

        self.assertTrue(saved.success)
        self.assertTrue(advanced.success)
        self.assertEqual(advanced.data["auto_advance"]["advanced_steps"], 2)
        report = advanced.data["auto_advance"]["reports"][0]
        self.assertEqual(report["status"], RUN_SUCCEEDED)
        self.assertEqual([step["node_id"] for step in report["steps"]], ["a", "b"])

    def test_workflow_runner_persists_running_before_tool_side_effect(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonWorkflowStore(project_root=root)
            definition = WorkflowDefinition(
                name="persist-before-side-effect",
                nodes=(WorkflowNodeDefinition(node_id="probe", node_type="tool_call", tool_name="demo.persisted_running"),),
            )
            run = start_workflow_run(definition, {"workspace_id": "tenant-a"}, run_id="run-persist-before-side-effect")
            store.save_definition(definition)
            store.save_run(run)
            tool = PersistedRunningProbeTool(store, run.run_id)
            runner = WorkflowRunner(tool_registry=ToolRegistry([tool]), state_sink=store.save_run)

            completed = runner.run_next(definition, run)

        self.assertEqual(tool.observed_status, NODE_RUNNING)
        self.assertEqual(completed.nodes["probe"].status, NODE_SUCCEEDED)

    def test_workflow_graph_foreach_runs_child_workflow_serially(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_tool_registry()
            registry.register(OkTool())
            child_saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "foreach.child.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {
                                    "node_id": "echo",
                                    "node_type": "tool_call",
                                    "tool_name": "demo.ok",
                                    "arguments": {"item": "{{input.item}}", "index": "{{input.index}}"},
                                }
                            ],
                        },
                    },
                )
            )
            parent_saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "foreach.parent.v1",
                            "version": "0.1.0",
                            "nodes": [
                                {
                                    "node_id": "loop",
                                    "node_type": "foreach",
                                    "arguments": {
                                        "workflow_name": "foreach.child.v1",
                                        "items": ["sku-1", "sku-2"],
                                        "max_iterations": 2,
                                    },
                                }
                            ],
                        },
                    },
                )
            )
            started = registry.invoke(ToolCall("workflow.graph.start_run", {"project_root": str(root), "workflow_name": "foreach.parent.v1"}))
            run_id = started.data["run"]["run_id"]
            advances = [
                registry.invoke(ToolCall("workflow.graph.auto_advance_runs", {"project_root": str(root), "max_runs": 10, "max_steps_per_run": 5}))
                for _ in range(6)
            ]
            final_run = JsonWorkflowStore(project_root=root).load_run(run_id)
            loop_outputs = final_run.nodes["loop"].outputs

        self.assertTrue(child_saved.success)
        self.assertTrue(parent_saved.success)
        self.assertTrue(all(item.success for item in advances))
        self.assertEqual(final_run.status, RUN_SUCCEEDED)
        self.assertEqual(loop_outputs["count"], 2)
        self.assertEqual([item["item"] for item in loop_outputs["results"]], ["sku-1", "sku-2"])
        self.assertEqual(loop_outputs["results"][1]["child_outputs"]["echo"]["data"]["called"]["index"], 1)

    def test_subgraph_inputs_resolve_upstream_node_outputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_tool_registry()
            registry.register(OkTool())
            registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "subgraph.child.inputs.v1",
                            "nodes": [{"node_id": "echo", "node_type": "tool_call", "tool_name": "demo.ok", "arguments": {"value": "{{input.value}}"}}],
                        },
                    },
                )
            )
            registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "subgraph.parent.inputs.v1",
                            "nodes": [
                                {"node_id": "source", "node_type": "tool_call", "tool_name": "demo.ok", "arguments": {"value": "{{input.value}}"}},
                                {
                                    "node_id": "child",
                                    "node_type": "subgraph",
                                    "arguments": {"workflow_name": "subgraph.child.inputs.v1", "inputs": {"value": "{{node.source.outputs.data.called.value}}"}},
                                    "depends_on": ["source"],
                                },
                            ],
                        },
                    },
                )
            )
            started = registry.invoke(ToolCall("workflow.graph.start_run", {"project_root": str(root), "workflow_name": "subgraph.parent.inputs.v1", "inputs": {"value": "sku-9"}}))
            run_id = started.data["run"]["run_id"]
            registry.invoke(ToolCall("workflow.graph.run_next", {"project_root": str(root), "run_id": run_id}))
            requested = registry.invoke(ToolCall("workflow.graph.run_next", {"project_root": str(root), "run_id": run_id}))
            child_run_id = requested.data["child_run"]["run_id"]
            child_run = JsonWorkflowStore(project_root=root).load_run(child_run_id)

        self.assertEqual(child_run.inputs["value"], "sku-9")

    def test_workflow_graph_auto_advance_times_out_claimed_agent_task(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_tool_registry()
            registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "agent.timeout.demo.v1",
                            "version": "0.1.0",
                            "nodes": [{"node_id": "draft", "node_type": "agent_task", "assigned_agent": "writer_agent"}],
                        },
                    },
                )
            )
            started = registry.invoke(ToolCall("workflow.graph.start_run", {"project_root": str(root), "workflow_name": "agent.timeout.demo.v1"}))
            run_id = started.data["run"]["run_id"]
            claimed = registry.invoke(ToolCall("workflow.graph.claim_agent_task", {"project_root": str(root), "run_id": run_id, "node_id": "draft", "agent_id": "writer_agent"}))

            store = JsonWorkflowStore(project_root=root)
            run = store.load_run(run_id)
            old_started_at = (datetime.now(UTC) - timedelta(seconds=120)).isoformat(timespec="seconds")
            old_node = replace(run.nodes["draft"], started_at=old_started_at)
            store.save_run(replace(run, nodes={**run.nodes, "draft": old_node}))

            advanced = registry.invoke(
                ToolCall(
                    "workflow.graph.auto_advance_runs",
                    {"project_root": str(root), "run_id": run_id, "agent_task_timeout_seconds": 1, "max_steps_per_run": 1},
                )
            )
            final_error = store.load_run(run_id).nodes["draft"].error

        self.assertTrue(claimed.success)
        self.assertTrue(advanced.success)
        report = advanced.data["auto_advance"]["reports"][0]
        self.assertEqual(report["timed_out"][0]["node_id"], "draft")
        self.assertEqual(report["status"], RUN_FAILED)
        self.assertEqual(final_error, "agent_task_timeout")

    def test_workflow_store_concurrent_definition_writes_preserve_both(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonWorkflowStore(project_root=root)
            errors: list[BaseException] = []

            def save(name: str) -> None:
                try:
                    store.save_definition(
                        WorkflowDefinition(
                            name=name,
                            nodes=(WorkflowNodeDefinition("start", "agent_task", assigned_agent="agent"),),
                        ),
                        actor="unit",
                        reason="concurrent write",
                    )
                except BaseException as exc:
                    errors.append(exc)

            first = threading.Thread(target=save, args=("concurrent.a.v1",))
            second = threading.Thread(target=save, args=("concurrent.b.v1",))
            first.start()
            second.start()
            first.join()
            second.join()
            names = {definition.name for definition in store.list_definitions()}

        self.assertEqual(errors, [])
        self.assertIn("concurrent.a.v1", names)
        self.assertIn("concurrent.b.v1", names)

    def test_workflow_management_composes_definition(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = handle_workflow_management_action(
                {
                    "project_root": str(root),
                    "action": "compose_definition",
                    "workflow_name": "custom.combo.management.v1",
                    "display_name": "管理端组合",
                    "mode": "parallel",
                    "components": [
                        "ecommerce.auto_listing.v1",
                        {"workflow_name": "content.video_generation.v1", "label": "视频工作流"},
                    ],
                }
            )

            self.assertTrue(result["ok"])
            definition = result["action_result"]["data"]["definition"]
            self.assertEqual(definition["metadata"]["composition"]["mode"], "parallel")
            self.assertEqual(len(definition["nodes"]), 2)
            self.assertEqual(definition["nodes"][0]["node_type"], "subgraph")
            self.assertEqual(definition["nodes"][1]["depends_on"], [])

    def test_workflow_management_exposes_trace_replay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = {
                "name": "trace.demo.v1",
                "version": "0.1.0",
                "description": "trace demo",
                "nodes": [
                    {
                        "node_id": "draft",
                        "node_type": "agent_task",
                        "label": "Draft",
                        "assigned_agent": "writer_agent",
                        "arguments": {},
                        "depends_on": [],
                    }
                ],
                "metadata": {"display_name": "Trace Demo"},
            }
            created = handle_workflow_management_action(
                {"project_root": str(root), "action": "upsert_definition", "actor": "desktop", "definition": definition}
            )
            started = handle_workflow_management_action(
                {"project_root": str(root), "action": "start_run", "actor": "desktop", "workflow_name": "trace.demo.v1"}
            )
            run_id = started["action_result"]["data"]["run"]["run_id"]
            claimed = handle_workflow_management_action(
                {
                    "project_root": str(root),
                    "action": "claim_agent_task",
                    "actor": "writer_agent",
                    "run_id": run_id,
                    "node_id": "draft",
                    "agent_id": "writer_agent",
                }
            )
            completed = handle_workflow_management_action(
                {
                    "project_root": str(root),
                    "action": "complete_agent_task",
                    "actor": "writer_agent",
                    "run_id": run_id,
                    "node_id": "draft",
                    "agent_id": "writer_agent",
                    "outputs": {"draft_id": "d1"},
                    "artifact_refs": [{"kind": "document", "id": "artifact://draft/d1"}],
                    "knowledge_refs": ["kb://draft-guidance"],
                    "audit_event_id": "audit-123",
                }
            )
            replay = handle_workflow_management_action({"project_root": str(root), "action": "trace_replay", "run_id": run_id})

            self.assertTrue(created["ok"])
            self.assertTrue(started["ok"])
            self.assertTrue(claimed["ok"])
            self.assertTrue(completed["ok"])
            self.assertTrue(replay["ok"])
            timeline = replay["trace_replay"]["timeline"]
            self.assertEqual([step["type"] for step in timeline], ["run_started", "agent_task_claimed", "agent_task_completed"])
            self.assertEqual(timeline[1]["state_after"]["node_states"]["draft"]["status"], NODE_RUNNING)
            self.assertEqual(timeline[2]["state_after"]["node_states"]["draft"]["status"], NODE_SUCCEEDED)
            final_outputs = replay["trace_replay"]["final_state"]["node_states"]["draft"]["outputs"]
            self.assertEqual(final_outputs["draft_id"], "d1")
            envelope = final_outputs["interaction_envelope"]
            self.assertEqual(envelope["run_id"], run_id)
            self.assertEqual(envelope["node_id"], "draft")
            self.assertEqual(envelope["artifact_refs"], [{"kind": "document", "id": "artifact://draft/d1"}])
            self.assertEqual(envelope["knowledge_refs"], ["kb://draft-guidance"])
            self.assertEqual(envelope["audit_event_id"], "audit-123")
            snapshot_run = replay["workflows"]["runs"][0]
            self.assertEqual(snapshot_run["run_id"], run_id)
            self.assertTrue(snapshot_run["trace_replay"]["can_replay"])
            node_details = snapshot_run["selected_node_details"]["draft"]
            self.assertEqual(node_details["interaction_envelopes"][-1]["audit_event_id"], "audit-123")

    def test_workflow_management_exposes_node_task_queue(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = {
                "name": "queue.demo.v1",
                "version": "0.1.0",
                "description": "queue demo",
                "nodes": [
                    {
                        "node_id": "draft",
                        "node_type": "agent_task",
                        "label": "Draft",
                        "assigned_agent": "writer_agent",
                        "arguments": {},
                        "depends_on": [],
                    }
                ],
                "metadata": {"display_name": "Queue Demo"},
            }
            created = handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": definition})
            started = handle_workflow_management_action(
                {
                    "project_root": str(root),
                    "action": "start_run",
                    "workflow_name": "queue.demo.v1",
                    "inputs": {
                        "node_task_queue": {
                            "draft": [
                                {"task_id": "product-1", "title": "商品 1"},
                                {"task_id": "product-2", "title": "商品 2"},
                            ]
                        }
                    },
                }
            )
            run_id = started["action_result"]["data"]["run"]["run_id"]
            claimed = handle_workflow_management_action(
                {"project_root": str(root), "action": "claim_agent_task", "run_id": run_id, "node_id": "draft", "agent_id": "writer_agent"}
            )
            completed = handle_workflow_management_action(
                {
                    "project_root": str(root),
                    "action": "complete_agent_task",
                    "run_id": run_id,
                    "node_id": "draft",
                    "agent_id": "writer_agent",
                    "outputs": {"product_queue": [{"product_id": "product-3", "status": "ready"}]},
                }
            )

            self.assertTrue(created["ok"])
            self.assertTrue(started["ok"])
            self.assertTrue(claimed["ok"])
            self.assertTrue(completed["ok"])
            detail = completed["workflows"]["runs"][0]["selected_node_details"]["draft"]
            self.assertIn("node_task_queue", detail)
            self.assertEqual([item["label"] for item in detail["node_task_queue"][:2]], ["商品 1", "商品 2"])
            self.assertEqual(detail["node_task_queue"][2]["label"], "product-3")
            self.assertEqual(detail["node_task_queue"][2]["queue_source"], "node.outputs")
            self.assertEqual(detail["node_queue_summary"]["current_item"]["label"], "商品 1")
            self.assertEqual([item["label"] for item in detail["node_queue_summary"]["next_items"][:2]], ["商品 2", "product-3"])
            self.assertEqual(detail["node_queue_summary"]["status_counts"]["pending"], 2)

    def test_video_generation_definition_exposes_typed_ports_and_contracts(self):
        definition = build_video_generation_definition()
        render = next(node for node in definition.nodes if node.node_id == "render_or_request")
        quality = next(node for node in definition.nodes if node.node_id == "quality_gate")

        self.assertEqual(render.metadata["connection_policy"]["input_accepts"], "render_request|automation|artifact")
        self.assertEqual(render.metadata["connection_policy"]["output_emits"], "render_result|artifact|automation")
        self.assertEqual(render.metadata["queue_label"], "渲染任务")
        self.assertEqual(render.metadata["interface_contract"]["outputs"][0]["name"], "render_result")
        self.assertEqual(quality.metadata["interface_contract"]["inputs"][0]["name"], "render_result")

    def test_workflow_management_supports_branch_waiter_callback_and_signal_nodes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = {
                "name": "semantic.demo.v1",
                "version": "0.1.0",
                "description": "semantic demo",
                "nodes": [
                    {
                        "node_id": "choose",
                        "node_type": "branch",
                        "label": "Choose Route",
                        "arguments": {"condition": True, "routes": {"true": ["wait_for_upload"], "false": []}},
                        "depends_on": [],
                    },
                    {
                        "node_id": "wait_for_upload",
                        "node_type": "waiter",
                        "label": "Wait For Upload",
                        "arguments": {"wait_for": "upload_ready"},
                        "depends_on": ["choose"],
                    },
                    {
                        "node_id": "callback",
                        "node_type": "external_callback",
                        "label": "External Callback",
                        "arguments": {"callback_id": "cb-demo"},
                        "depends_on": ["wait_for_upload"],
                    },
                ],
                "metadata": {"display_name": "Semantic Demo"},
            }
            created = handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": definition})
            started = handle_workflow_management_action({"project_root": str(root), "action": "start_run", "workflow_name": "semantic.demo.v1"})
            run_id = started["action_result"]["data"]["run"]["run_id"]
            branch = handle_workflow_management_action({"project_root": str(root), "action": "run_next", "run_id": run_id})
            waiter = handle_workflow_management_action({"project_root": str(root), "action": "run_next", "run_id": run_id})
            released = handle_workflow_management_action(
                {"project_root": str(root), "action": "signal_node", "run_id": run_id, "node_id": "wait_for_upload", "signal_payload": {"upload_id": "u1"}}
            )
            callback = handle_workflow_management_action({"project_root": str(root), "action": "run_next", "run_id": run_id})
            completed = handle_workflow_management_action(
                {
                    "project_root": str(root),
                    "action": "signal_node",
                    "run_id": run_id,
                    "node_id": "callback",
                    "actor": "callback_service",
                    "signal_payload": {"status": "ok"},
                    "artifact_refs": ["artifact://callback/result"],
                }
            )

            self.assertTrue(created["ok"])
            self.assertTrue(branch["ok"])
            self.assertEqual(branch["action_result"]["data"]["run"]["nodes"]["choose"]["outputs"]["selected_route"], "true")
            self.assertTrue(waiter["ok"])
            self.assertEqual(waiter["action_result"]["data"]["run"]["nodes"]["wait_for_upload"]["status"], NODE_WAITING)
            self.assertEqual(waiter["action_result"]["data"]["run"]["status"], RUN_WAITING)
            self.assertTrue(released["ok"])
            self.assertEqual(released["action_result"]["data"]["run"]["nodes"]["wait_for_upload"]["status"], NODE_SUCCEEDED)
            self.assertTrue(callback["ok"])
            self.assertEqual(callback["action_result"]["data"]["run"]["nodes"]["callback"]["status"], NODE_WAITING)
            self.assertTrue(completed["ok"])
            self.assertEqual(completed["action_result"]["data"]["run"]["nodes"]["callback"]["status"], NODE_SUCCEEDED)
            self.assertEqual(completed["action_result"]["data"]["run"]["status"], RUN_SUCCEEDED)
            replay = handle_workflow_management_action({"project_root": str(root), "action": "trace_replay", "run_id": run_id})
            self.assertIn("branch_selected", [step["type"] for step in replay["trace_replay"]["timeline"]])
            self.assertIn("external_callback_received", [step["type"] for step in replay["trace_replay"]["timeline"]])

    def test_workflow_management_subgraph_node_starts_child_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = {
                "name": "child.demo.v1",
                "version": "0.1.0",
                "description": "child",
                "nodes": [
                    {
                        "node_id": "child_start",
                        "node_type": "agent_task",
                        "label": "Child Start",
                        "assigned_agent": "child_agent",
                        "arguments": {},
                        "depends_on": [],
                    }
                ],
                "metadata": {"display_name": "Child Demo"},
            }
            parent = {
                "name": "parent.demo.v1",
                "version": "0.1.0",
                "description": "parent",
                "nodes": [
                    {
                        "node_id": "child_flow",
                        "node_type": "subgraph",
                        "label": "Child Flow",
                        "arguments": {"workflow_name": "child.demo.v1", "inputs": {"source": "parent"}},
                        "depends_on": [],
                    }
                ],
                "metadata": {"display_name": "Parent Demo"},
            }
            child_saved = handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": child})
            parent_saved = handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": parent})
            started = handle_workflow_management_action({"project_root": str(root), "action": "start_run", "workflow_name": "parent.demo.v1"})
            run_id = started["action_result"]["data"]["run"]["run_id"]
            requested = handle_workflow_management_action({"project_root": str(root), "action": "run_next", "run_id": run_id})
            child_run_id = requested["action_result"]["data"]["run"]["nodes"]["child_flow"]["outputs"]["child_run_id"]
            completed = handle_workflow_management_action(
                {"project_root": str(root), "action": "signal_node", "run_id": run_id, "node_id": "child_flow", "signal_payload": {"child_run_id": child_run_id}}
            )

            self.assertTrue(child_saved["ok"])
            self.assertTrue(parent_saved["ok"])
            self.assertTrue(requested["ok"])
            self.assertEqual(requested["action_result"]["data"]["run"]["nodes"]["child_flow"]["status"], NODE_WAITING)
            self.assertEqual(requested["action_result"]["data"]["child_run"]["run_id"], child_run_id)
            self.assertEqual(requested["action_result"]["data"]["child_run"]["workflow_name"], "child.demo.v1")
            self.assertTrue(completed["ok"])
            self.assertEqual(completed["action_result"]["data"]["run"]["status"], RUN_SUCCEEDED)

    def test_workflow_upsert_generates_argument_references_for_port_connections(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_tool_registry()
            registry.register(OkTool())
            saved = registry.invoke(
                ToolCall(
                    "workflow.graph.upsert_definition",
                    {
                        "project_root": str(root),
                        "definition": {
                            "name": "port.reference.demo.v1",
                            "nodes": [
                                {"node_id": "source", "node_type": "tool_call", "tool_name": "demo.ok", "arguments": {"value": "{{input.value}}"}},
                                {"node_id": "target", "node_type": "tool_call", "tool_name": "demo.ok", "depends_on": ["source"]},
                            ],
                        },
                    },
                )
            )
            target = next(node for node in saved.data["definition"]["nodes"] if node["node_id"] == "target")

            self.assertTrue(saved.success)
            self.assertEqual(target["arguments"]["from_source"], "{{node.source.outputs}}")

            started = registry.invoke(
                ToolCall(
                    "workflow.graph.start_run",
                    {"project_root": str(root), "workflow_name": "port.reference.demo.v1", "inputs": {"value": "sku-1"}},
                )
            )
            run_id = started.data["run"]["run_id"]
            registry.invoke(ToolCall("workflow.graph.run_next", {"project_root": str(root), "run_id": run_id}))
            advanced = registry.invoke(ToolCall("workflow.graph.run_next", {"project_root": str(root), "run_id": run_id}))
            called = advanced.data["run"]["nodes"]["target"]["outputs"]["data"]["called"]
            self.assertEqual(called["from_source"]["data"]["called"]["value"], "sku-1")

    def test_auto_advance_reconciles_completed_subgraph_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = {
                "name": "child.auto.v1",
                "nodes": [
                    {
                        "node_id": "done",
                        "node_type": "waiter",
                        "arguments": {"wait_for": "unit", "ready": True},
                    }
                ],
            }
            parent = {
                "name": "parent.auto.v1",
                "nodes": [
                    {
                        "node_id": "child_flow",
                        "node_type": "subgraph",
                        "arguments": {"workflow_name": "child.auto.v1", "inputs": {"source": "parent"}},
                    }
                ],
            }
            self.assertTrue(handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": child})["ok"])
            self.assertTrue(handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": parent})["ok"])
            started = handle_workflow_management_action({"project_root": str(root), "action": "start_run", "workflow_name": "parent.auto.v1"})
            run_id = started["action_result"]["data"]["run"]["run_id"]
            requested = handle_workflow_management_action({"project_root": str(root), "action": "run_next", "run_id": run_id})
            child_run_id = requested["action_result"]["data"]["run"]["nodes"]["child_flow"]["outputs"]["child_run_id"]

            handle_workflow_management_action({"project_root": str(root), "action": "auto_advance_runs", "max_runs": 5, "max_steps_per_run": 5})
            reconciled = handle_workflow_management_action({"project_root": str(root), "action": "auto_advance_runs", "max_runs": 5, "max_steps_per_run": 5})

            store = JsonWorkflowStore(project_root=root)
            parent_run = store.load_run(run_id)
            child_run = store.load_run(child_run_id)
            self.assertEqual(child_run.status, RUN_SUCCEEDED)
            self.assertEqual(parent_run.nodes["child_flow"].status, NODE_SUCCEEDED)
            self.assertEqual(parent_run.nodes["child_flow"].outputs["child_status"], RUN_SUCCEEDED)
            self.assertEqual(parent_run.status, RUN_SUCCEEDED)
            self.assertTrue(reconciled["ok"])

    def test_workflow_node_catalog_includes_registered_tools_and_foreach(self):
        registry = build_default_tool_registry()
        registry.register(OkTool())
        catalog_result = registry.invoke(ToolCall("workflow.graph.list_node_catalog", {}))
        catalog = catalog_result.data["node_catalog"]["catalog"]
        ids = {item["catalog_id"] for item in catalog}
        node_types = {item["node_type"] for item in catalog}

        self.assertTrue(catalog_result.success)
        self.assertIn("tool:demo.ok", ids)
        self.assertIn("foreach", node_types)

    def test_workflow_management_enforces_governance_contracts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            forbidden = {
                "name": "forbidden.demo.v1",
                "version": "0.1.0",
                "description": "forbidden",
                "nodes": [
                    {
                        "node_id": "callback",
                        "node_type": "external_callback",
                        "label": "Callback",
                        "arguments": {"callback_id": "cb1"},
                        "depends_on": [],
                    }
                ],
                "metadata": {"governance": {"forbidden_node_types": ["external_callback"]}},
            }
            forbidden_result = handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": forbidden})

            governed = {
                "name": "contract.demo.v1",
                "version": "0.1.0",
                "description": "contract",
                "nodes": [
                    {
                        "node_id": "draft",
                        "node_type": "agent_task",
                        "label": "Draft",
                        "assigned_agent": "writer_agent",
                        "arguments": {},
                        "depends_on": [],
                    }
                ],
                "metadata": {
                    "governance": {
                        "interface_contracts": {
                            "nodes": {
                                "draft": {
                                    "required_outputs": ["draft_id"],
                                    "requires_artifact_refs": True,
                                }
                            }
                        }
                    }
                },
            }
            created = handle_workflow_management_action({"project_root": str(root), "action": "upsert_definition", "definition": governed})
            started = handle_workflow_management_action({"project_root": str(root), "action": "start_run", "workflow_name": "contract.demo.v1"})
            run_id = started["action_result"]["data"]["run"]["run_id"]
            claimed = handle_workflow_management_action(
                {"project_root": str(root), "action": "claim_agent_task", "run_id": run_id, "node_id": "draft", "agent_id": "writer_agent"}
            )
            denied = handle_workflow_management_action(
                {"project_root": str(root), "action": "complete_agent_task", "run_id": run_id, "node_id": "draft", "agent_id": "writer_agent", "outputs": {}}
            )
            allowed = handle_workflow_management_action(
                {
                    "project_root": str(root),
                    "action": "complete_agent_task",
                    "run_id": run_id,
                    "node_id": "draft",
                    "agent_id": "writer_agent",
                    "outputs": {"draft_id": "d2"},
                    "artifact_refs": ["artifact://draft/d2"],
                }
            )

            self.assertFalse(forbidden_result["ok"])
            self.assertEqual(forbidden_result["action_result"]["error_code"], "workflow_contract_violation")
            self.assertIn("forbidden_node_type:callback:external_callback", forbidden_result["action_result"]["metadata"]["issues"])
            self.assertTrue(created["ok"])
            self.assertTrue(started["ok"])
            self.assertTrue(claimed["ok"])
            self.assertFalse(denied["ok"])
            self.assertEqual(denied["action_result"]["error_code"], "workflow_contract_violation")
            self.assertIn("missing_required_output:draft:draft_id", denied["action_result"]["metadata"]["issues"])
            self.assertIn("missing_artifact_refs:draft", denied["action_result"]["metadata"]["issues"])
            self.assertTrue(allowed["ok"])
            denials = [
                event
                for event in JsonWorkflowStore(project_root=root).list_audit_events(workflow_name="contract.demo.v1")
                if event.get("action") == "contract_violation"
            ]
            self.assertGreaterEqual(len(denials), 1)

    def test_workflow_management_enforces_action_time_permissions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            definition = {
                "name": "governed.demo.v1",
                "version": "0.1.0",
                "description": "governed",
                "nodes": [
                    {
                        "node_id": "start",
                        "node_type": "agent_task",
                        "label": "Start",
                        "assigned_agent": "owner_agent",
                        "arguments": {},
                        "depends_on": [],
                    }
                ],
                "metadata": {
                    "display_name": "Governed Demo",
                    "permissions": {
                        "mode": "restricted",
                        "owners": ["owner_agent"],
                        "editors": ["editor_agent"],
                        "approvers": ["reviewer_agent"],
                    },
                },
            }

            created = handle_workflow_management_action(
                {"project_root": str(root), "action": "upsert_definition", "actor": "owner_agent", "definition": definition}
            )
            denied_update = handle_workflow_management_action(
                {"project_root": str(root), "action": "upsert_definition", "actor": "intruder", "definition": {**definition, "version": "0.2.0"}}
            )
            editor_update = handle_workflow_management_action(
                {"project_root": str(root), "action": "upsert_definition", "actor": "editor_agent", "definition": {**definition, "version": "0.3.0"}}
            )
            denied_start = handle_workflow_management_action(
                {"project_root": str(root), "action": "start_run", "actor": "intruder", "workflow_name": "governed.demo.v1"}
            )
            desktop_owned_definition = {
                **definition,
                "name": "desktop.owned.demo.v1",
                "metadata": {
                    "display_name": "Desktop Owned Demo",
                    "permissions": {"mode": "restricted", "owners": ["desktop"]},
                },
            }
            desktop_owned_created = handle_workflow_management_action(
                {"project_root": str(root), "action": "upsert_definition", "definition": desktop_owned_definition}
            )
            desktop_owned_denied = handle_workflow_management_action(
                {"project_root": str(root), "action": "start_run", "actor": "intruder", "workflow_name": "desktop.owned.demo.v1"}
            )

            self.assertTrue(created["ok"])
            self.assertFalse(denied_update["ok"])
            self.assertEqual(denied_update["action_result"]["error_code"], "workflow_permission_denied")
            self.assertTrue(editor_update["ok"])
            self.assertFalse(denied_start["ok"])
            self.assertEqual(denied_start["action_result"]["error_code"], "workflow_permission_denied")
            self.assertTrue(desktop_owned_created["ok"])
            self.assertFalse(desktop_owned_denied["ok"])
            self.assertEqual(desktop_owned_denied["action_result"]["error_code"], "workflow_permission_denied")
            denials = [
                event
                for event in JsonWorkflowStore(project_root=root).list_audit_events(workflow_name="governed.demo.v1")
                if event.get("action") == "permission_denied"
            ]
            self.assertGreaterEqual(len(denials), 2)


if __name__ == "__main__":
    unittest.main()
