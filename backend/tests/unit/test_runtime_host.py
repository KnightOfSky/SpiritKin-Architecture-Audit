from __future__ import annotations

from dataclasses import replace

import pytest

from backend.orchestrator.runtime_host import (
    RuntimeCheckpointStore,
    RuntimeHostHeartbeatService,
    RuntimeHostRegistry,
    RuntimeHostWorkflowStore,
    RuntimeWorkflowHostService,
)
from backend.orchestrator.workflow_graph import (
    NODE_RUNNING,
    NODE_SUCCEEDED,
    NODE_WAITING_REVIEW,
    RUN_RUNNING,
    RUN_SUCCEEDED,
    WorkflowDefinition,
    WorkflowNodeDefinition,
    WorkflowNodeRun,
    WorkflowRun,
)
from backend.orchestrator.workflow_store import JsonWorkflowStore
from backend.state_store import StateCorruptionError


class FakeClock:
    def __init__(self, value: float = 1_800_000_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _registry(tmp_path, clock: FakeClock) -> RuntimeHostRegistry:
    return RuntimeHostRegistry(tmp_path / "runtime" / "hosts.json", clock=clock)


def test_host_election_is_workspace_scoped_and_fenced(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    registry.register_host(
        host_id="desktop-a",
        workspace_id="tenant-a",
        host_type="desktop",
        can_execute_workflows=True,
        priority=20,
    )
    registry.register_host(
        host_id="cloud-a",
        workspace_id="tenant-a",
        host_type="cloud",
        can_execute_workflows=True,
        priority=80,
    )

    public_lease = registry.elect("tenant-a")
    assert public_lease["host_id"] == "cloud-a"
    assert public_lease["epoch"] == 1
    assert "fencing_token" not in public_lease

    heartbeat = registry.heartbeat("cloud-a")
    private_lease = heartbeat["lease"]
    assert private_lease["fencing_token"]
    with registry.authorized_lease(
        workspace_id="tenant-a",
        host_id="cloud-a",
        fencing_token=private_lease["fencing_token"],
        epoch=private_lease["epoch"],
    ):
        pass

    registry.set_host_status("cloud-a", "draining")
    next_lease = registry.elect("tenant-a", reveal_fencing_token=True)
    assert next_lease["host_id"] == "desktop-a"
    assert next_lease["epoch"] == 2
    with pytest.raises(PermissionError, match="stale"):
        with registry.authorized_lease(
            workspace_id="tenant-a",
            host_id="cloud-a",
            fencing_token=private_lease["fencing_token"],
            epoch=private_lease["epoch"],
        ):
            pass


def test_expired_host_lease_fails_over_without_two_masters(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    for host_id, host_type, priority in (("desktop-a", "desktop", 90), ("cloud-a", "cloud", 20)):
        registry.register_host(
            host_id=host_id,
            workspace_id="tenant-a",
            host_type=host_type,
            can_execute_workflows=True,
            priority=priority,
            heartbeat_ttl_seconds=10,
        )
    first = registry.elect("tenant-a", reveal_fencing_token=True)
    assert first["host_id"] == "desktop-a"

    clock.advance(11)
    registry.heartbeat("cloud-a", elect_if_needed=False)
    second = registry.elect("tenant-a", reveal_fencing_token=True)

    assert second["host_id"] == "cloud-a"
    assert second["epoch"] > first["epoch"]
    snapshot = registry.snapshot(workspace_id="tenant-a")
    assert "fencing_token" not in snapshot["leases"][0]


def test_host_cannot_move_between_workspaces_or_publish_credentials(tmp_path):
    registry = _registry(tmp_path, FakeClock())
    registry.register_host(
        host_id="desktop-a",
        workspace_id="tenant-a",
        host_type="desktop",
        can_execute_workflows=True,
        endpoint_ref="https://user:secret@example.test/runtime?token=hidden",
    )

    with pytest.raises(PermissionError, match="another workspace"):
        registry.register_host(
            host_id="desktop-a",
            workspace_id="tenant-b",
            host_type="desktop",
        )
    host = registry.snapshot(workspace_id="tenant-a")["hosts"][0]
    assert "endpoint_ref" not in host
    assert "secret" not in str(host)


def test_fake_checkpoint_cannot_prepare_migration_or_change_lease(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path),
        clock=clock,
    )
    for host_id, host_type, priority in (
        ("desktop-a", "desktop", 90),
        ("cloud-a", "cloud", 10),
    ):
        registry.register_host(
            host_id=host_id,
            workspace_id="tenant-a",
            host_type=host_type,
            can_execute_workflows=True,
            priority=priority,
        )
    source = registry.elect("tenant-a", reveal_fencing_token=True)

    with pytest.raises(KeyError, match="unknown runtime checkpoint"):
        registry.request_migration(
            workspace_id="tenant-a",
            checkpoint_id="checkpoint-forged",
            target_host_id="cloud-a",
            requested_by="attacker",
            confirmed=True,
        )

    snapshot = registry.snapshot(workspace_id="tenant-a")
    assert snapshot["leases"][0]["host_id"] == source["host_id"]
    assert snapshot["leases"][0]["epoch"] == source["epoch"]
    assert snapshot["migrations"] == []
    assert "fencing_token" not in str(snapshot)


def _prepared_runtime_migration(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    definition = WorkflowDefinition(
        name="migration-security",
        version="1.0",
        nodes=(WorkflowNodeDefinition(node_id="review", node_type="review_gate"),),
    )
    workflow_store.save_definition(definition)
    workflow_store.save_run(
        WorkflowRun(
            run_id="run-migration-security",
            workflow_name=definition.name,
            workflow_version=definition.version,
            inputs={"workspace_id": "tenant-a"},
            nodes={"review": WorkflowNodeRun(node_id="review")},
        )
    )
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    for host_id, host_type, priority in (
        ("desktop-a", "desktop", 90),
        ("cloud-a", "cloud", 10),
    ):
        registry.register_host(
            host_id=host_id,
            workspace_id="tenant-a",
            host_type=host_type,
            can_execute_workflows=True,
            priority=priority,
        )
    source = registry.elect("tenant-a", reveal_fencing_token=True)
    checkpoint = checkpoints.create_checkpoint(
        "run-migration-security",
        workspace_id="tenant-a",
        host_id="desktop-a",
        fencing_token=source["fencing_token"],
        epoch=source["epoch"],
    )
    migration = registry.prepare_migration(
        workspace_id="tenant-a",
        checkpoint_id=checkpoint["checkpoint_id"],
        source_host_id="desktop-a",
        target_host_id="cloud-a",
        fencing_token=source["fencing_token"],
        epoch=source["epoch"],
        requested_by="owner",
        confirmed=True,
    )
    return registry, checkpoints, source, checkpoint, migration


def test_migration_claim_rejects_changed_source_lease(tmp_path):
    registry, _checkpoints, _source, _checkpoint, migration = _prepared_runtime_migration(tmp_path)
    registry.set_host_status("desktop-a", "draining")
    replacement = registry.elect("tenant-a", reveal_fencing_token=True)
    assert replacement["host_id"] == "cloud-a"

    with pytest.raises(PermissionError, match="source lease is stale"):
        registry.claim_migration(
            migration["migration_id"],
            target_host_id="cloud-a",
            requested_by="owner",
            confirmed=True,
        )


def test_migration_request_rejects_checkpoint_after_run_changes(tmp_path):
    registry, checkpoints, _source, checkpoint, _migration = _prepared_runtime_migration(tmp_path)
    current = checkpoints.workflow_store.load_run("run-migration-security")
    assert current is not None
    checkpoints.workflow_store.save_run(replace(current, updated_at="2999-01-01T00:00:00+00:00"))

    with pytest.raises(PermissionError, match="stale compared"):
        registry.request_migration(
            workspace_id="tenant-a",
            checkpoint_id=checkpoint["checkpoint_id"],
            target_host_id="cloud-a",
            requested_by="owner",
            confirmed=True,
        )

    snapshot = registry.snapshot(workspace_id="tenant-a")
    assert snapshot["leases"][0]["host_id"] == "desktop-a"
    assert snapshot["leases"][0]["epoch"] == 1


def test_migration_claim_revalidates_definition_before_switching_lease(tmp_path):
    registry, checkpoints, _source, _checkpoint, migration = _prepared_runtime_migration(tmp_path)
    definition = checkpoints.workflow_store.load_definition("migration-security")
    assert definition is not None
    checkpoints.workflow_store.save_definition(
        replace(
            definition,
            version="2.0",
            nodes=(*definition.nodes, WorkflowNodeDefinition(node_id="new-review", node_type="review_gate")),
        )
    )

    with pytest.raises(PermissionError, match="definition changed"):
        registry.claim_migration(
            migration["migration_id"],
            target_host_id="cloud-a",
            requested_by="owner",
            confirmed=True,
        )

    snapshot = registry.snapshot(workspace_id="tenant-a")
    assert snapshot["leases"][0]["host_id"] == "desktop-a"
    assert snapshot["leases"][0]["epoch"] == 1


def test_claimed_migration_replay_never_returns_another_hosts_lease(tmp_path):
    registry, _checkpoints, _source, _checkpoint, migration = _prepared_runtime_migration(tmp_path)
    claimed = registry.claim_migration(
        migration["migration_id"],
        target_host_id="cloud-a",
        requested_by="owner",
        confirmed=True,
    )
    old_target_token = claimed["lease"]["fencing_token"]
    registry.set_host_status("cloud-a", "draining")
    current = registry.elect("tenant-a", reveal_fencing_token=True)
    assert current["host_id"] == "desktop-a"

    with pytest.raises(PermissionError, match="claim is stale") as exc:
        registry.claim_migration(
            migration["migration_id"],
            target_host_id="cloud-a",
            requested_by="attacker",
            confirmed=True,
        )

    assert current["fencing_token"] not in str(exc.value)
    assert old_target_token not in str(exc.value)
    assert "fencing_token" not in str(registry.snapshot(workspace_id="tenant-a"))


def test_checkpoint_creation_enforces_workflow_workspace_ownership(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    workflow_store.save_run(
        WorkflowRun(
            run_id="run-tenant-b",
            workflow_name="workspace-test",
            workflow_version="1.0",
            inputs={"workspace_id": "tenant-b"},
            nodes={},
        )
    )
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    registry.register_host(
        host_id="desktop-a",
        workspace_id="tenant-a",
        host_type="desktop",
        can_execute_workflows=True,
    )
    lease = registry.elect("tenant-a", reveal_fencing_token=True)

    with pytest.raises(PermissionError, match="another workspace"):
        checkpoints.create_checkpoint(
            "run-tenant-b",
            workspace_id="tenant-a",
            host_id="desktop-a",
            fencing_token=lease["fencing_token"],
            epoch=lease["epoch"],
        )


def test_corrupt_runtime_state_fails_closed(tmp_path):
    host_path = tmp_path / "runtime" / "hosts.json"
    host_path.parent.mkdir(parents=True)
    host_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(StateCorruptionError, match="hosts.json"):
        RuntimeHostRegistry(host_path).snapshot()

    checkpoint_path = tmp_path / "runtime" / "checkpoints.json"
    checkpoint_path.write_text('{"schema_version":"unsupported"}', encoding="utf-8")
    registry = RuntimeHostRegistry(tmp_path / "runtime" / "healthy-hosts.json")
    checkpoints = RuntimeCheckpointStore(registry, path=checkpoint_path)
    with pytest.raises(StateCorruptionError, match="unsupported schema_version"):
        checkpoints.snapshot()

    malformed_host_path = tmp_path / "runtime" / "malformed-hosts.json"
    malformed_host_path.write_text(
        '{"schema_version":"spiritkin.runtime_host.v1","hosts":[],"leases":{},"epochs":{},"migrations":{},"events":[]}',
        encoding="utf-8",
    )
    with pytest.raises(StateCorruptionError, match="field 'hosts' must be an object"):
        RuntimeHostRegistry(malformed_host_path).snapshot()

    nested_host_path = tmp_path / "runtime" / "nested-hosts.json"
    nested_host_path.write_text(
        '{"schema_version":"spiritkin.runtime_host.v1","hosts":{"desktop-a":[]},"leases":{},"epochs":{},"migrations":{},"events":[]}',
        encoding="utf-8",
    )
    with pytest.raises(StateCorruptionError, match="record 'desktop-a' must be an object"):
        RuntimeHostRegistry(nested_host_path).snapshot()

    nested_checkpoint_path = tmp_path / "runtime" / "nested-checkpoints.json"
    nested_checkpoint_path.write_text(
        '{"schema_version":"spiritkin.runtime_checkpoint.v1","checkpoints":{"checkpoint-a":[]},"latest_by_run":{},"resumes":{}}',
        encoding="utf-8",
    )
    nested_checkpoints = RuntimeCheckpointStore(
        RuntimeHostRegistry(tmp_path / "runtime" / "nested-checkpoint-hosts.json"),
        path=nested_checkpoint_path,
    )
    with pytest.raises(StateCorruptionError, match="record 'checkpoint-a' must be an object"):
        nested_checkpoints.snapshot()


def test_checkpoint_resume_preserves_completed_nodes_and_reconciles_inflight(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    checkpoint_store = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    definition = WorkflowDefinition(
        name="migration-test",
        version="1.0",
        nodes=(
            WorkflowNodeDefinition(node_id="done", node_type="tool_call", tool_name="test.done"),
            WorkflowNodeDefinition(node_id="inflight", node_type="tool_call", tool_name="test.side_effect", depends_on=("done",)),
            WorkflowNodeDefinition(node_id="next", node_type="tool_call", tool_name="test.next", depends_on=("inflight",)),
        ),
    )
    workflow_store.save_definition(definition)
    run = WorkflowRun(
        run_id="run-migrate-1",
        workflow_name=definition.name,
        workflow_version=definition.version,
        status=RUN_RUNNING,
        inputs={"workspace_id": "tenant-a", "actor": "owner", "api_token": "must-not-leak"},
        nodes={
            "done": WorkflowNodeRun(node_id="done", status=NODE_SUCCEEDED, outputs={"value": 42}),
            "inflight": WorkflowNodeRun(node_id="inflight", status=NODE_RUNNING, attempts=1, outputs={"authorization": "secret"}),
            "next": WorkflowNodeRun(node_id="next"),
        },
    )
    workflow_store.save_run(run)

    for host_id, host_type in (("desktop-a", "desktop"), ("cloud-a", "cloud")):
        registry.register_host(
            host_id=host_id,
            workspace_id="tenant-a",
            host_type=host_type,
            can_execute_workflows=True,
            priority=90 if host_id == "desktop-a" else 10,
        )
    source_lease = registry.elect("tenant-a", reveal_fencing_token=True)
    checkpoint = checkpoint_store.create_checkpoint(
        run.run_id,
        workspace_id="tenant-a",
        host_id="desktop-a",
        fencing_token=source_lease["fencing_token"],
        epoch=source_lease["epoch"],
    )
    assert "run_snapshot" not in checkpoint
    stored_checkpoint_text = (tmp_path / "runtime" / "checkpoints.json").read_text(encoding="utf-8")
    assert "must-not-leak" not in stored_checkpoint_text
    assert '"authorization": "[redacted]"' in stored_checkpoint_text

    migration = registry.prepare_migration(
        workspace_id="tenant-a",
        checkpoint_id=checkpoint["checkpoint_id"],
        source_host_id="desktop-a",
        target_host_id="cloud-a",
        fencing_token=source_lease["fencing_token"],
        epoch=source_lease["epoch"],
        requested_by="owner",
        confirmed=True,
    )
    claim = registry.claim_migration(
        migration["migration_id"],
        target_host_id="cloud-a",
        requested_by="owner",
        confirmed=True,
    )
    target_lease = claim["lease"]
    result = checkpoint_store.resume_checkpoint(
        checkpoint["checkpoint_id"],
        target_host_id="cloud-a",
        fencing_token=target_lease["fencing_token"],
        epoch=target_lease["epoch"],
        requested_by="owner",
        confirmed=True,
    )

    restored = workflow_store.load_run(run.run_id)
    assert result["restart"] is False
    assert result["reconciliation_required"] is True
    assert restored is not None
    assert restored.nodes["done"].status == NODE_SUCCEEDED
    assert restored.nodes["inflight"].status == NODE_WAITING_REVIEW
    assert restored.nodes["inflight"].error == "runtime_resume_reconcile_inflight"
    assert restored.nodes["next"].status == "pending"

    same_result = checkpoint_store.resume_checkpoint(
        checkpoint["checkpoint_id"],
        target_host_id="cloud-a",
        fencing_token=target_lease["fencing_token"],
        epoch=target_lease["epoch"],
        requested_by="owner",
        confirmed=True,
    )
    assert same_result["resume_id"] == result["resume_id"]


def test_stale_checkpoint_cannot_overwrite_a_newer_run(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    definition = WorkflowDefinition(name="stale-test", nodes=(WorkflowNodeDefinition(node_id="one", node_type="review_gate"),))
    workflow_store.save_definition(definition)
    run = WorkflowRun(
        run_id="run-stale",
        workflow_name="stale-test",
        workflow_version="0.1.0",
        inputs={"workspace_id": "tenant-a"},
        nodes={"one": WorkflowNodeRun(node_id="one")},
    )
    workflow_store.save_run(run)
    for host_id in ("desktop-a", "cloud-a"):
        registry.register_host(host_id=host_id, workspace_id="tenant-a", host_type="desktop" if host_id == "desktop-a" else "cloud", can_execute_workflows=True, priority=10)
    source = registry.elect("tenant-a", reveal_fencing_token=True)
    checkpoint = checkpoints.create_checkpoint(
        run.run_id,
        workspace_id="tenant-a",
        host_id=source["host_id"],
        fencing_token=source["fencing_token"],
        epoch=source["epoch"],
    )
    newer = replace(run, updated_at="2999-01-01T00:00:00+00:00")
    workflow_store.save_run(newer)
    registry.set_host_status(source["host_id"], "draining")
    target = registry.elect("tenant-a", reveal_fencing_token=True)

    with pytest.raises(PermissionError, match="stale"):
        checkpoints.resume_checkpoint(
            checkpoint["checkpoint_id"],
            target_host_id=target["host_id"],
            fencing_token=target["fencing_token"],
            epoch=target["epoch"],
            requested_by="owner",
            confirmed=True,
        )


def test_checkpoint_cannot_resume_against_a_changed_workflow_definition(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    original_definition = WorkflowDefinition(
        name="definition-change",
        version="1.0",
        nodes=(WorkflowNodeDefinition(node_id="one", node_type="review_gate"),),
    )
    workflow_store.save_definition(original_definition)
    workflow_store.save_run(
        WorkflowRun(
            run_id="run-definition-change",
            workflow_name=original_definition.name,
            workflow_version=original_definition.version,
            inputs={"workspace_id": "tenant-a"},
            nodes={"one": WorkflowNodeRun(node_id="one")},
        )
    )
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    for host_id, host_type in (("desktop-a", "desktop"), ("cloud-a", "cloud")):
        registry.register_host(host_id=host_id, workspace_id="tenant-a", host_type=host_type, can_execute_workflows=True, priority=90 if host_id == "desktop-a" else 10)
    source = registry.elect("tenant-a", reveal_fencing_token=True)
    checkpoint = checkpoints.create_checkpoint(
        "run-definition-change",
        workspace_id="tenant-a",
        host_id="desktop-a",
        fencing_token=source["fencing_token"],
        epoch=source["epoch"],
    )
    workflow_store.save_definition(
        replace(original_definition, version="2.0", nodes=(*original_definition.nodes, WorkflowNodeDefinition(node_id="two", node_type="review_gate")))
    )
    registry.set_host_status("desktop-a", "draining")
    target = registry.elect("tenant-a", reveal_fencing_token=True)

    with pytest.raises(PermissionError, match="definition changed"):
        checkpoints.resume_checkpoint(
            checkpoint["checkpoint_id"],
            target_host_id="cloud-a",
            fencing_token=target["fencing_token"],
            epoch=target["epoch"],
            requested_by="owner",
            confirmed=True,
        )


def test_heartbeat_service_registers_and_elects_executable_host(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path),
        clock=clock,
    )
    service = RuntimeHostHeartbeatService(
        registry,
        checkpoints,
        host_id="desktop-a",
        workspace_id="tenant-a",
        host_type="desktop",
        priority=50,
    )

    heartbeat = service.tick()

    assert heartbeat["host"]["can_execute_workflows"] is True
    assert heartbeat["lease"]["host_id"] == "desktop-a"
    assert heartbeat["lease"]["fencing_token"]


def test_heartbeat_service_checkpoints_only_when_run_changes(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    workflow_store.save_run(
        WorkflowRun(
            run_id="run-auto-checkpoint",
            workflow_name="auto-checkpoint",
            workflow_version="1.0",
            inputs={"workspace_id": "tenant-a"},
            nodes={"pending": WorkflowNodeRun(node_id="pending")},
        )
    )
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    service = RuntimeHostHeartbeatService(
        registry,
        checkpoints,
        host_id="desktop-a",
        workspace_id="tenant-a",
    )

    service.tick()
    service.tick()
    assert checkpoints.snapshot(workspace_id="tenant-a")["count"] == 1

    run = workflow_store.load_run("run-auto-checkpoint")
    assert run is not None
    workflow_store.save_run(replace(run, updated_at="2999-01-01T00:00:00+00:00"))
    service.tick()
    assert checkpoints.snapshot(workspace_id="tenant-a")["count"] == 2


def test_runtime_host_skips_unscoped_legacy_runs(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    workflow_store.save_run(
        WorkflowRun(
            run_id="run-legacy-unscoped",
            workflow_name="legacy",
            workflow_version="1.0",
            nodes={"pending": WorkflowNodeRun(node_id="pending")},
        )
    )
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    RuntimeHostHeartbeatService(
        registry,
        checkpoints,
        host_id="desktop-a",
        workspace_id="tenant-a",
    ).tick()

    assert checkpoints.snapshot(workspace_id="tenant-a")["count"] == 0


def test_runtime_workflow_host_fails_over_and_continues_without_replaying_completed_nodes(tmp_path):
    clock = FakeClock()
    registry = _registry(tmp_path, clock)
    workflow_store = JsonWorkflowStore(tmp_path / "workflows", project_root=tmp_path)
    definition = WorkflowDefinition(
        name="host-failover",
        version="1.0",
        nodes=(
            WorkflowNodeDefinition(node_id="first", node_type="waiter", arguments={"wait_for": "first", "ready": True}),
            WorkflowNodeDefinition(node_id="second", node_type="waiter", arguments={"wait_for": "second", "ready": True}, depends_on=("first",)),
        ),
    )
    workflow_store.save_definition(definition)
    workflow_store.save_run(
        WorkflowRun(
            run_id="run-host-failover",
            workflow_name=definition.name,
            workflow_version=definition.version,
            inputs={"workspace_id": "tenant-a", "actor": "owner"},
            nodes={
                "first": WorkflowNodeRun(node_id="first"),
                "second": WorkflowNodeRun(node_id="second"),
            },
        )
    )
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=tmp_path / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
        clock=clock,
    )
    primary_heartbeat = RuntimeHostHeartbeatService(
        registry,
        checkpoints,
        host_id="desktop-a",
        workspace_id="tenant-a",
        host_type="desktop",
        priority=80,
        heartbeat_interval_seconds=2,
        heartbeat_ttl_seconds=5,
    )
    secondary_heartbeat = RuntimeHostHeartbeatService(
        registry,
        checkpoints,
        host_id="cloud-a",
        workspace_id="tenant-a",
        host_type="cloud",
        priority=20,
        heartbeat_interval_seconds=2,
        heartbeat_ttl_seconds=5,
    )
    primary_heartbeat.tick()
    secondary_heartbeat.tick()
    primary = RuntimeWorkflowHostService(primary_heartbeat, max_steps_per_run=1)

    first_result = primary.execute_once()
    after_first = workflow_store.load_run("run-host-failover")
    primary_snapshot = registry.snapshot(workspace_id="tenant-a")
    assert first_result["advanced_steps"] == 1
    assert primary_snapshot["hosts"][0]["execution"]["status"] == "active"
    assert "fencing_token" not in str(primary_snapshot)
    assert "status_path" not in str(primary_snapshot)
    assert after_first is not None
    assert after_first.nodes["first"].status == NODE_SUCCEEDED
    assert after_first.nodes["second"].status == "pending"

    stale_store = RuntimeHostWorkflowStore(
        workflow_store,
        registry=registry,
        checkpoints=checkpoints,
        workspace_id="tenant-a",
        host_id="desktop-a",
        lease_supplier=primary_heartbeat.private_lease,
    )
    clock.advance(6)
    secondary_heartbeat.tick()
    secondary = RuntimeWorkflowHostService(secondary_heartbeat, max_steps_per_run=1)
    second_result = secondary.execute_once()
    restored = workflow_store.load_run("run-host-failover")

    assert second_result["advanced_steps"] == 1
    assert restored is not None
    assert restored.run_id == "run-host-failover"
    assert restored.status == RUN_SUCCEEDED
    assert restored.nodes["first"].attempts == 1
    assert restored.nodes["second"].attempts == 1
    with pytest.raises(PermissionError, match="lease|fencing"):
        stale_store.save_run(restored)
