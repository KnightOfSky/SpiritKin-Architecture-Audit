from __future__ import annotations

from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from backend.mobile.ios_workflows import build_ios_workflow_snapshot, handle_ios_workflow_action
from backend.orchestrator.workflow_graph import WorkflowDefinition, WorkflowNodeDefinition, start_workflow_run
from backend.orchestrator.workflow_store import JsonWorkflowStore


def _definition(name: str, workspace_id: str = "") -> WorkflowDefinition:
    metadata = {"display_name": name}
    if workspace_id:
        metadata["workspace_id"] = workspace_id
    return WorkflowDefinition(
        name=name,
        nodes=(WorkflowNodeDefinition("wait", "waiter", "Wait", arguments={"wait_for": "signal"}),),
        metadata=metadata,
    )


def test_snapshot_hides_other_workspace_runs_and_definitions() -> None:
    with TemporaryDirectory() as tmp:
        store = JsonWorkflowStore(project_root=tmp)
        global_definition = _definition("global.workflow")
        owned_definition = _definition("owned.workflow", "tenant-a")
        hidden_definition = _definition("hidden.workflow", "tenant-b")
        for definition in (global_definition, owned_definition, hidden_definition):
            store.save_definition(definition)
        store.save_run(start_workflow_run(owned_definition, {"workspace_id": "tenant-a"}, run_id="run-a"))
        store.save_run(start_workflow_run(hidden_definition, {"workspace_id": "tenant-b"}, run_id="run-b"))
        store.save_run(start_workflow_run(global_definition, {}, run_id="run-unscoped"))

        with patch("backend.mobile.ios_workflows._project_root", return_value=tmp):
            snapshot = build_ios_workflow_snapshot(workspace_id="tenant-a")

    names = {item["name"] for item in snapshot["definitions"]}
    assert "global.workflow" in names
    assert "owned.workflow" in names
    assert "hidden.workflow" not in names
    assert [item["run_id"] for item in snapshot["runs"]] == ["run-a"]
    assert snapshot["project_root"] == ""
    assert snapshot["state_dir"] == ""


def test_start_run_overwrites_client_workspace_and_composition_is_scoped() -> None:
    with TemporaryDirectory() as tmp, patch("backend.mobile.ios_workflows._project_root", return_value=tmp):
        store = JsonWorkflowStore(project_root=tmp)
        component = _definition("component.workflow")
        store.save_definition(component)

        composed = handle_ios_workflow_action(
            {
                "action": "compose_definition",
                "workflow_name": "owned.composition",
                "components": [{"workflow_name": "component.workflow"}],
            },
            workspace_id="tenant-a",
        )
        assert composed["ok"]
        assert store.load_definition("owned.composition").metadata["workspace_id"] == "tenant-a"

        started = handle_ios_workflow_action(
            {
                "action": "start_run",
                "workflow_name": "owned.composition",
                "inputs": {"workspace_id": "tenant-b", "value": 1},
            },
            workspace_id="tenant-a",
        )
        run_id = started["action_result"]["data"]["run"]["run_id"]
        assert store.load_run(run_id).inputs["workspace_id"] == "tenant-a"


def test_ios_cannot_execute_nodes_or_touch_another_workspace_run() -> None:
    with TemporaryDirectory() as tmp, patch("backend.mobile.ios_workflows._project_root", return_value=tmp):
        store = JsonWorkflowStore(project_root=tmp)
        definition = _definition("tenant.workflow", "tenant-b")
        store.save_definition(definition)
        run = start_workflow_run(definition, {"workspace_id": "tenant-b"}, run_id="run-b")
        store.save_run(run)

        with pytest.raises(PermissionError, match="Runtime Host"):
            handle_ios_workflow_action({"action": "run_next", "run_id": "run-b"}, workspace_id="tenant-a")
        with pytest.raises(PermissionError, match="another workspace"):
            handle_ios_workflow_action(
                {"action": "signal_node", "run_id": "run-b", "node_id": "wait"},
                workspace_id="tenant-a",
            )


def test_upsert_cannot_hide_global_target_by_omitting_top_level_name() -> None:
    with TemporaryDirectory() as tmp, patch("backend.mobile.ios_workflows._project_root", return_value=tmp):
        store = JsonWorkflowStore(project_root=tmp)
        global_definition = _definition("global.workflow")
        store.save_definition(global_definition)

        with pytest.raises(PermissionError, match="global"):
            handle_ios_workflow_action(
                {
                    "action": "upsert_definition",
                    "definition": global_definition.snapshot(),
                },
                workspace_id="tenant-a",
            )
