from __future__ import annotations

import pytest

from backend.mobile.ios_runtime_host import build_ios_runtime_host_snapshot, handle_ios_runtime_host_action
from backend.orchestrator.runtime_host import RuntimeCheckpointStore, RuntimeHostRegistry
from backend.orchestrator.workflow_graph import WorkflowNodeRun, WorkflowRun
from backend.orchestrator.workflow_store import JsonWorkflowStore


def test_ios_runtime_host_registers_only_a_non_executing_workspace_adapter(tmp_path):
    response = handle_ios_runtime_host_action(
        {"action": "register", "can_execute_workflows": True},
        workspace_id="tenant-a",
        actor_id="phone-a",
        project_root=tmp_path,
    )

    host = response["runtime_host"]["registry"]["hosts"][0]
    assert host["host_id"] == "ios:phone-a"
    assert host["workspace_id"] == "tenant-a"
    assert host["can_execute_workflows"] is False
    assert host["can_observe"] is True


def test_ios_controller_can_request_but_not_claim_migration(tmp_path):
    registry = RuntimeHostRegistry(tmp_path / "state" / "runtime" / "hosts.json")
    registry.register_host(host_id="desktop-a", workspace_id="tenant-a", host_type="desktop", can_execute_workflows=True, priority=90)
    registry.register_host(host_id="cloud-a", workspace_id="tenant-a", host_type="cloud", can_execute_workflows=True, priority=10)
    registry.elect("tenant-a")
    workflow_store = JsonWorkflowStore(tmp_path / "state" / "workflows", project_root=tmp_path)
    workflow_store.save_run(
        WorkflowRun(
            run_id="run-demo",
            workflow_name="workflow-demo",
            workflow_version="1.0",
            inputs={"workspace_id": "tenant-a"},
            nodes={"pending": WorkflowNodeRun(node_id="pending")},
        )
    )
    source_lease = registry.heartbeat("desktop-a")["lease"]
    checkpoint = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "state" / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
    ).create_checkpoint(
        "run-demo",
        workspace_id="tenant-a",
        host_id="desktop-a",
        fencing_token=source_lease["fencing_token"],
        epoch=source_lease["epoch"],
    )

    requested = handle_ios_runtime_host_action(
        {
            "action": "request_migration",
            "checkpoint_id": checkpoint["checkpoint_id"],
            "target_host_id": "cloud-a",
            "confirmed": True,
        },
        workspace_id="tenant-a",
        actor_id="phone-a",
        project_root=tmp_path,
    )
    assert requested["result"]["migration"]["source_host_id"] == "desktop-a"
    assert requested["result"]["migration"]["controller_requested"] is True
    assert "fencing_token" not in str(requested["runtime_host"])

    with pytest.raises(PermissionError, match="host authority"):
        handle_ios_runtime_host_action(
            {"action": "claim_migration", "migration_id": requested["result"]["migration"]["migration_id"]},
            workspace_id="tenant-a",
            actor_id="phone-a",
            project_root=tmp_path,
        )

    with pytest.raises(KeyError, match="unknown runtime checkpoint"):
        handle_ios_runtime_host_action(
            {
                "action": "request_migration",
                "checkpoint_id": "checkpoint-missing",
                "target_host_id": "cloud-a",
                "confirmed": True,
            },
            workspace_id="tenant-a",
            actor_id="phone-a",
            project_root=tmp_path,
        )


def test_runtime_host_snapshot_is_workspace_scoped(tmp_path):
    registry = RuntimeHostRegistry(tmp_path / "state" / "runtime" / "hosts.json")
    registry.register_host(host_id="desktop-a", workspace_id="tenant-a", host_type="desktop")
    registry.register_host(host_id="desktop-b", workspace_id="tenant-b", host_type="desktop")

    snapshot = build_ios_runtime_host_snapshot(workspace_id="tenant-a", project_root=tmp_path)

    assert [item["host_id"] for item in snapshot["registry"]["hosts"]] == ["desktop-a"]
