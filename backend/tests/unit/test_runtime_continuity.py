from __future__ import annotations

import pytest

from backend.app.runtime_continuity import build_runtime_continuity_snapshot, handle_runtime_continuity_action
from backend.orchestrator.runtime_host import RuntimeHostRegistry


def test_runtime_continuity_combines_host_checkpoint_and_world_without_secrets(tmp_path):
    registry = RuntimeHostRegistry(tmp_path / "state" / "runtime" / "hosts.json")
    registry.register_host(
        host_id="desktop-a",
        workspace_id="tenant-a",
        host_type="desktop",
        can_execute_workflows=True,
    )
    registry.elect("tenant-a", reveal_fencing_token=True)

    snapshot = build_runtime_continuity_snapshot(workspace_id="tenant-a", project_root=tmp_path)

    assert snapshot["status"] == "ready"
    assert snapshot["runtime_hosts"]["host_count"] == 1
    assert snapshot["checkpoints"]["count"] == 0
    assert snapshot["world"]["entity_count"] == 0
    assert snapshot["policy"]["workflow_restart_on_migration"] is False
    assert "fencing_token" not in str(snapshot)


def test_runtime_continuity_election_requires_confirmation(tmp_path):
    registry = RuntimeHostRegistry(tmp_path / "state" / "runtime" / "hosts.json")
    registry.register_host(
        host_id="desktop-a",
        workspace_id="tenant-a",
        host_type="desktop",
        can_execute_workflows=True,
    )
    with pytest.raises(PermissionError, match="explicit confirmation"):
        handle_runtime_continuity_action(
            {"action": "request_election", "workspace_id": "tenant-a"},
            project_root=tmp_path,
        )

    result = handle_runtime_continuity_action(
        {"action": "request_election", "workspace_id": "tenant-a", "confirmed": True},
        project_root=tmp_path,
    )
    assert result["runtime_continuity"]["active_lease"]["host_id"] == "desktop-a"
