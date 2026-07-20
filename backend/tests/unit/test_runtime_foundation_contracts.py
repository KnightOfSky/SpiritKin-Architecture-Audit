from __future__ import annotations

import pytest

from backend.app.agent_management import ManagedAgentConfig
from backend.app.learning_workflow import ModelProviderConfig
from backend.orchestrator.worker_pool import WorkerDescriptor
from backend.orchestrator.workflow_graph import WorkflowDefinition, WorkflowNodeDefinition, start_workflow_run
from backend.runtime import (
    InvalidLifecycleTransition,
    InvalidObjectStateTransition,
    ProviderContract,
    ProviderRegistry,
    RuntimeBusEvent,
    RuntimeContract,
    RuntimeEventBus,
    lifecycle_snapshot,
    object_state_snapshot,
    transition_lifecycle,
    transition_object_state,
)
from backend.runtime.events import EventPersistence
from backend.skills import SkillRegistry, SkillRunner, SkillSpec
from backend.tools import ToolRegistry


def test_universal_lifecycle_enforces_review_and_archive_boundaries():
    candidate = transition_lifecycle(
        object_type="skill",
        object_id="skill.demo",
        current="draft",
        target="candidate",
        actor="owner",
    )
    assert candidate.to_status == "candidate"
    assert lifecycle_snapshot(object_type="skill", object_id="skill.demo", status="active")["status"] == "stable"

    with pytest.raises(InvalidLifecycleTransition, match="archived to stable"):
        transition_lifecycle(
            object_type="skill",
            object_id="skill.demo",
            current="archived",
            target="stable",
            actor="owner",
        )


def test_shared_state_machines_cover_all_runtime_object_types():
    transitions = {
        "workflow": ("pending", "running"),
        "skill": ("candidate", "review"),
        "worker": ("planned", "ready"),
        "agent": ("review", "active"),
        "model": ("review", "active"),
    }
    for object_type, (source, target) in transitions.items():
        transition = transition_object_state(
            object_type=object_type,
            object_id=f"{object_type}.demo",
            current=source,
            target=target,
            actor="runtime",
        )
        assert transition.to_state == target
        snapshot = object_state_snapshot(object_type=object_type, object_id=f"{object_type}.demo", state=source)
        assert target in snapshot["allowed_transitions"]

    with pytest.raises(InvalidObjectStateTransition, match="succeeded to running"):
        transition_object_state(
            object_type="workflow",
            object_id="workflow.done",
            current="succeeded",
            target="running",
            actor="runtime",
        )


def test_runtime_contract_validates_input_output_resource_and_permission():
    contract = RuntimeContract(
        object_type="capability",
        object_id="commerce.product.publish",
        input_schema={
            "type": "object",
            "required": ["product_id"],
            "properties": {"product_id": {"type": "string"}, "quantity": {"type": "integer"}},
            "additionalProperties": False,
        },
        output_schema={"type": "object", "required": ["published"], "properties": {"published": {"type": "boolean"}}},
        resources=("store", "product"),
        permission="commerce.publish",
        schema_ref="schema://commerce/product-publish/v1",
    )

    assert contract.validate_input({"product_id": "p-1", "quantity": 1}).valid
    invalid = contract.validate_input({"quantity": "one", "extra": True})
    assert not invalid.valid
    assert "input.product_id:required" in invalid.issues
    assert "input.quantity:expected_integer" in invalid.issues
    assert "input.extra:additional_property" in invalid.issues
    assert contract.snapshot()["resource"] == ["store", "product"]
    assert contract.snapshot()["permission"] == "commerce.publish"


def test_runtime_event_bus_persists_typed_events_and_isolates_subscriber_failures():
    persistence = EventPersistence()
    bus = RuntimeEventBus(persistence=persistence)
    delivered: list[str] = []
    subscription_id = bus.subscribe("workflow.*", lambda event: delivered.append(event.topic))
    bus.subscribe("workflow.*", lambda _event: (_ for _ in ()).throw(RuntimeError("subscriber failed")))

    event = bus.publish(RuntimeBusEvent(topic="workflow.started", payload={"run_id": "run-1"}, source="runtime-host"))

    assert event.topic == "workflow.started"
    assert delivered == ["workflow.started"]
    assert persistence.stats()["total"] == 1
    assert bus.snapshot()["delivery_failure_count"] == 1
    assert bus.snapshot()["recent_delivery_errors"] == [
        {"topic": "workflow.started", "handler": "<lambda>", "error_type": "RuntimeError"}
    ]
    assert "run-1" not in str(bus.snapshot()["recent_delivery_errors"])
    assert bus.unsubscribe(subscription_id)


class _Provider:
    def __init__(self, provider_id: str, provider_type: str):
        self._contract = ProviderContract(
            provider_id=provider_id,
            provider_type=provider_type,
            capabilities=(f"{provider_type}.invoke",),
            status="ready",
        )

    def provider_contract(self) -> ProviderContract:
        return self._contract


def test_provider_registry_unifies_model_tool_worker_vision_and_storage():
    registry = ProviderRegistry()
    for provider_type in ("model", "tool", "worker", "vision", "storage"):
        registry.register(_Provider(f"{provider_type}.default", provider_type))

    assert registry.snapshot()["provider_count"] == 5
    assert len(registry.list_contracts(provider_type="vision")) == 1
    with pytest.raises(ValueError, match="unsupported provider_type"):
        ProviderContract(provider_id="invalid", provider_type="direct-qwen")


def test_real_runtime_objects_expose_unified_governance_projections():
    workflow = WorkflowDefinition(
        name="governed.workflow",
        metadata={"status": "candidate", "owner": "owner-agent", "benchmark_refs": ["benchmark:wf"]},
    )
    skill = SkillSpec(
        name="governed.skill",
        description="demo",
        metadata={"status": "candidate", "owner_agent_id": "owner-agent"},
    )
    worker = WorkerDescriptor(
        worker_id="worker.demo",
        label="Demo worker",
        health_status="ready",
        capabilities=("demo.run",),
    )
    agent = ManagedAgentConfig(agent_id="agent.demo", label="Demo agent", domain="demo")
    model = ModelProviderConfig("llamacpp", "local-model", True, "http://127.0.0.1:8080/v1")

    workflow_snapshot = workflow.snapshot()
    skill_snapshot = skill.governance_snapshot()
    worker_snapshot = worker.snapshot()
    agent_snapshot = agent.snapshot()
    model_snapshot = model.snapshot()

    for snapshot in (workflow_snapshot, skill_snapshot, worker_snapshot, agent_snapshot, model_snapshot):
        assert snapshot["runtime_metadata"]["id"]
        assert snapshot["runtime_metadata"]["lifecycle"]["status"]
        assert snapshot["lifecycle"]["status"]
    assert worker_snapshot["state_machine"]["state"] == "ready"
    assert agent_snapshot["state_machine"]["state"] == "active"
    assert model_snapshot["state_machine"]["state"] == "active"


def test_workflow_and_skill_enforce_runtime_input_contracts():
    input_schema = {
        "type": "object",
        "required": ["title"],
        "properties": {"title": {"type": "string"}},
    }
    workflow = WorkflowDefinition(
        name="contract.workflow",
        nodes=(WorkflowNodeDefinition("review", "review_gate"),),
        metadata={"input_schema": input_schema},
    )
    with pytest.raises(ValueError, match="contract:input.title:required"):
        start_workflow_run(workflow, {})

    skill = SkillSpec(name="contract.skill", description="demo", input_schema=input_schema)
    result = SkillRunner(SkillRegistry([skill]), ToolRegistry()).run("contract.skill", {})
    assert not result.success
    assert result.metadata["error_code"] == "skill_input_contract_violation"


def test_real_model_and_worker_implement_provider_protocol():
    registry = ProviderRegistry()
    worker = WorkerDescriptor(worker_id="worker.provider", label="Provider worker", health_status="ready")
    model = ModelProviderConfig("ollama", "qwen", True, "http://127.0.0.1:11434")

    registry.register(worker)
    registry.register(model)

    assert [item.provider_type for item in registry.list_contracts()] == ["model", "worker"]
