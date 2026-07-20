# SpiritKinAI AI Runtime Kernel Specification

Last updated: 2026-07-01

This document defines the stable kernel architecture for SpiritKinAI as an AI
Operating System runtime. It is not a feature backlog and not a module list.
Its purpose is to keep the system powerful while preventing concept sprawl.

## 1. Design Rule

The runtime may grow in capability, but the number of first-class concepts
should stay small.

Before adding a new module, ask:

```text
Is this a new kernel concept, or is it an implementation detail of an existing
kernel concept?
```

If it can fit inside an existing concept, do not create a new top-level
concept, manager, router, or context type.

The target kernel should remain understandable through roughly these concepts:

```text
Intent
Planner
Context Kernel
Resource Registry
Capability Registry
Scheduler
Workflow
Skill Registry
Worker Registry
Agent
Memory
Model Registry
Governance
```

Everything else is an implementation detail, adapter, view, policy, or artifact.

Growth Runtime is an implementation layer under Evolution and Governance. It
must live under `backend/capability/growth/` for Capability ownership; it is not a new
execution plane or an ungoverned peer kernel. Its outputs are candidate
Capability/Workflow/Skill/Tool/Code/Model records that must pass the existing
review gate before entering a registry or becoming executable.

## 2. Layer Model

SpiritKinAI should be organized by responsibility, not by every feature name.

| Layer | Core concepts | Responsibility |
| --- | --- | --- |
| Interface | Intent | User, system, event, or Agent request entering the runtime. |
| Planning | Planner | Converts intent into capability needs or a capability DAG. |
| Runtime | Context Kernel, Resource Registry, Memory | Provides unified state, long-lived resources, context views, history, artifacts, and durable records. |
| Capability | Capability Registry | Defines what the system can do in stable, implementation-independent terms. |
| Orchestration | Workflow | Persists and replays capability graphs, review gates, and long-running procedures. |
| Scheduling | Scheduler | Selects concrete implementations for required capabilities. |
| Execution | Skill Registry, Worker Registry | Provides callable implementations and execution capacity. |
| Intelligence | Agent | Reasons, plans, reviews, and coordinates within governed scopes. |
| Models | Model Registry | Manages model providers, model artifacts, evaluation, fine-tuning, distillation, quantization, and deployment. |
| Governance | Governance | Permission, review, audit, safety, policy, security, and promotion gates. |

Do not add peer concepts such as `Skill Context`, `Workflow Context`,
`Agent Context`, `Skill Router`, or `Worker Manager` as top-level architecture.
Those names may exist in code temporarily, but architecturally they belong under
the concepts above.

## 3. Kernel Flow

The long-term runtime flow should be:

```text
Intent
  -> Planner
  -> Resource Registry
  -> Capability DAG
  -> Capability Registry
  -> Scheduler
  -> Skill Registry
  -> Worker Registry
  -> Worker
  -> Trace / Artifact / Memory / Evaluation
```

Workflow is a persisted form of a capability graph. It should not hard-bind to
specific Skills when a stable Capability id is available.

Preferred:

```text
Workflow node requires capability: commerce.product.publish
Scheduler selects implementation: skill:douyin.product.publish.v2
Worker Registry selects capacity: android_device_worker or browser_worker
```

Avoid:

```text
Workflow node directly calls skill:douyin.product.publish.v2
```

Direct Skill binding is acceptable for legacy workflows, narrow system tools,
or temporary migration, but new reusable workflows should target capabilities.

## 4. Core Concepts

### Intent

An Intent is an external or internal request entering the runtime.

Examples:

- User asks: "move this PDD product to Douyin shop".
- Agent asks for code review.
- Scheduled monitor asks to refresh model health.
- Mobile bridge emits a product link artifact.

Intent should not know which Skill or Worker will be used.

Minimum contract:

| Field | Meaning |
| --- | --- |
| `intent_id` | Stable request id. |
| `source` | Human, Agent, event, automation, remote node, mobile device. |
| `request` | Natural language or structured request. |
| `context_id` | Runtime context scope. |
| `constraints` | Deadline, privacy, platform, budget, risk, locality. |
| `artifacts` | Linked files, screenshots, URLs, records, or prior outputs. |

### Planner

Planner converts Intent into capability needs.

It should produce a capability DAG, not choose concrete Skills too early.

Example:

```text
Intent: publish this product to Douyin

Capability DAG:
  commerce.product.extract
  image.clean
  title.generate
  commerce.product.validate
  commerce.product.publish
```

Planner may use Agents and models, but the output should be structured and
schedulable.

### Context Kernel

Context Kernel is the only top-level context concept.

Do not create separate first-class context systems such as:

- Skill Context
- Workflow Context
- Agent Context
- Worker Context

Instead, Context Kernel provides views:

```text
context.view_for_agent(...)
context.view_for_workflow(...)
context.view_for_skill(...)
context.view_for_worker(...)
```

Each view is a projection over the same context graph.

Minimum graph:

```text
Intent
  -> Task
  -> CapabilityPlan / WorkflowRun
  -> NodeExecution
  -> SkillRun / ToolCall
  -> WorkerExecution
  -> Artifact
  -> Memory / Evaluation
```

The current code already has a Context Kernel seed in
`backend/orchestrator/context_store.py` and related context mirror/write-intent
modules. Future work should strengthen that kernel, not create more context
families.

### Resource Registry

Resource Registry manages real-world and durable digital assets that Agents own
or operate over.

Resources are not Skills and not Workflows. They are the persistent objects the
runtime must understand across runs.

Examples:

- Commerce stores: Douyin shop A, PDD account 1, TikTok shop.
- Advertising accounts: Qianchuan account, Google Ads account.
- Device resources: phone 1, Chrome profile 3, remote PC.
- Knowledge and media resources: product KB, image library, NAS folder.
- Project resources: code repository, game project, video project, document set.

Minimum contract:

| Field | Meaning |
| --- | --- |
| `resource_id` | Stable id. |
| `resource_type` | shop, account, device, profile, repository, kb, media_library, project. |
| `platform` | Douyin, PDD, Android, Chrome, Git, local_fs, etc. |
| `owner_agent` | Agent responsible for long-running management. |
| `credential_ref` | Reference to credential storage, never raw secret material. |
| `state_ref` | Runtime state snapshot or external sync cursor. |
| `policies` | Budget, risk, permission, ROI, privacy, locality. |
| `capability_constraints` | Capabilities supported or forbidden for this resource. |
| `health_status` | ready, degraded, blocked, unavailable. |
| `last_observed_at` | Last successful refresh or sync. |

Agents own Goals, Resources, Policies, and State. Workflows operate on
Resources for a bounded execution. Skills implement capabilities for a resource
type. Workers execute in a concrete environment.

For commerce, the Agent should reason over resources such as stores, products,
orders, inventory, budgets, and ad accounts. A Workflow such as price update or
product publish is only one repeatable execution plan over those resources.

### Capability Registry

Capability Registry defines what the runtime can do.

Capability is the stable API. Skill is only one implementation.

Examples:

- `commerce.product.search`
- `commerce.product.publish`
- `image.ocr`
- `image.search`
- `browser.navigate`
- `android.tap`
- `code.generate`
- `video.edit`

Minimum contract:

| Field | Meaning |
| --- | --- |
| `capability_id` | Stable id, namespace-style. |
| `version` | Contract version. |
| `description` | Human-readable meaning. |
| `input_schema` | Required input contract. |
| `output_schema` | Output contract. |
| `required_permission` | Permission boundary. |
| `risk_level` | Low, medium, high, critical. |
| `worker_requirements` | Required Worker needs or namespaces. |
| `estimated_cost` | Cost band or model/tool cost hint. |
| `latency_hint_ms` | Expected latency. |
| `rollback_policy` | Compensation or manual rollback rule. |
| `tags` | Search/routing tags. |
| `eval_cases` | Contract-level evaluation cases. |

Capability Registry may start as a projection over existing Skill, Worker, and
Tool metadata, but architecturally it should become the runtime's central API.

### Scheduler

Scheduler resolves capability needs into concrete implementations.

It does not plan user intent. It does not execute work. It selects.

Inputs:

- Capability id or capability DAG node.
- Context view.
- Available Skill implementations.
- Available Workers.
- Policies and review requirements.
- Health, latency, cost, success rate, locality.

Output:

- Selected Skill or implementation.
- Selected Worker or Worker class.
- Explanation.
- Rejected candidates.
- Required review or permission.

This is where old `Skill Router` behavior should end up. A Router that returns
a Skill id is a resolver inside Scheduler, not a top-level architecture concept.

### Workflow

Workflow persists and replays capability graphs.

It is useful for:

- Long-running procedures.
- Human review gates.
- Retry/replay.
- Visual authoring.
- Audit.
- Multi-Agent coordination.

Workflow should prefer capability-bound nodes:

```json
{
  "node_id": "publish_product",
  "node_type": "capability_call",
  "capability_id": "commerce.product.publish",
  "input": {"product": "{{validated_product}}"}
}
```

Legacy `skill_call` nodes remain valid, but new reusable workflows should move
toward `capability_call`.

### Skill Registry

Skill Registry stores concrete callable implementations.

A Skill should declare what it implements:

```json
{
  "name": "douyin.product.publish.v2",
  "implements": ["commerce.product.publish"],
  "input_schema": {},
  "output_schema": {},
  "side_effects": ["network_write", "publish"],
  "required_worker_needs": ["browser", "douyin_session"],
  "risk_level": "high"
}
```

Skill Registry should keep:

- Contract fields.
- Owner Agent.
- Required capabilities.
- Worker needs.
- Risk and permission.
- Cost and latency hints.
- Promotion status.
- Eval and replay cases.
- Health summary.

It should not become a planner or execution engine.

### Worker Registry

Worker Registry describes execution capacity.

Examples:

- Android Device Worker.
- Browser Worker.
- Python Worker.
- FFmpeg Worker.
- Git Worker.
- Remote Runtime Worker.
- Service/RAG Worker.

Worker Registry answers:

```text
Which workers can execute this implementation now?
```

Minimum contract:

| Field | Meaning |
| --- | --- |
| `worker_id` | Stable worker id. |
| `worker_type` | device, browser, execution, service, remote. |
| `capability_namespaces` | Supported namespaces. |
| `health_status` | ready, unavailable, degraded, planned. |
| `locality` | local, remote, mobile, cloud. |
| `workspace_scope` | Allowed workspace. |
| `permission_scope` | Required approval boundary. |
| `queue_depth` | Scheduling load. |
| `latency_hint_ms` | Expected latency. |
| `cost_hint` | Cost band. |

Workers are not Agents. They execute.

### Agent

Agent is an intelligence role.

Agents can:

- Plan.
- Review.
- Ask for capability execution.
- Produce structured output.
- Coordinate with other Agents.

Agents own:

- Goals.
- Resources.
- Policies.
- State.

Agents should not directly bypass Scheduler, Governance, Skill Registry, or
Worker Registry.

### Memory

Memory stores durable and summarized learning signals.

Memory should not replace Context Kernel. Context is the runtime state graph;
Memory is a long-lived source that can be projected into Context views.

### Model Registry

Model Registry manages replaceable brains and model artifacts.

Distillation belongs here, not in Runtime.

Model Lifecycle:

```text
Base Model
  -> Dataset Pipeline
  -> Fine Tune / LoRA / QLoRA
  -> Merge
  -> Distill
  -> Quantization
  -> Evaluation
  -> Publish
```

Each step produces or consumes a Model Artifact.

Runtime should not care whether a model was distilled. Runtime should care about:

- Model id.
- Capability profile.
- Context window.
- Modalities.
- Tool/function support.
- Cost.
- Latency.
- Evaluation results.
- Governance status.

### Governance

Governance owns:

- Permission.
- Confirmation.
- Review.
- Audit.
- Safety stop.
- Security policy.
- Promotion gates.

Governance should be called by Scheduler, Workflow, Skill Runtime, Worker
execution, Model Lifecycle, and Context write appliers. It should not be hidden
inside a router.

## 5. Contracts To Lock

### Capability Contract

Capability is the stable API between Planner, Workflow, Scheduler, and
implementations.

Required fields:

```json
{
  "capability_id": "image.ocr",
  "version": "1.0.0",
  "input_schema": {},
  "output_schema": {},
  "required_permission": "read_only",
  "risk_level": "low",
  "worker_requirements": ["vision", "ocr"],
  "estimated_cost": "low",
  "latency_hint_ms": 1000,
  "rollback_policy": "none",
  "tags": ["image", "text", "ocr"]
}
```

### Skill Contract

Required fields:

```json
{
  "name": "local.ocr.tesseract",
  "implements": ["image.ocr"],
  "input_schema": {},
  "output_schema": {},
  "side_effects": [],
  "required_worker_needs": ["python_runtime"],
  "risk_level": "low",
  "timeout_ms": 10000,
  "rollback_strategy": "none",
  "compensation": "",
  "eval_cases": []
}
```

Current code already has most of these fields in `SkillSpec`. The next step is
to enforce them at promotion and scheduling boundaries.

### Execution Trace Contract

Every execution should produce a trace suitable for replay, learning, and
evaluation.

Required fields:

| Field | Meaning |
| --- | --- |
| `trace_id` | Stable trace id. |
| `context_id` | Context graph scope. |
| `capability_id` | Requested capability. |
| `implementation_id` | Selected Skill or direct implementation. |
| `worker_id` | Selected worker. |
| `input_ref` | Input snapshot or hash/ref. |
| `output_ref` | Output artifact/ref. |
| `status` | completed, failed, blocked, cancelled. |
| `latency_ms` | Execution latency. |
| `cost` | Cost estimate or actual. |
| `error_code` | Failure class. |
| `review_refs` | Review/audit ids. |
| `permission_refs` | Permission/confirmation ids. |

Skill Health, Learning Pipeline, Evaluation, and Model Lifecycle should read
from this trace stream instead of inventing separate logs for every subsystem.

## 6. Naming Rules

Avoid top-level names that duplicate kernel concepts.

| Avoid as top-level concept | Use instead |
| --- | --- |
| `Skill Router` | Scheduler capability resolver. |
| `Skill Context` | Context Kernel skill view builder. |
| `Workflow Context` | Context Kernel workflow view builder. |
| `Agent Context` | Context Kernel agent view builder. |
| `Worker Manager` | Worker Registry plus Scheduler. |
| `Distillation Layer` | Model Lifecycle step. |
| `Training Manager` | Model Lifecycle or Dataset Pipeline component. |

Implementation files may keep old names during migration, but docs and new APIs
should move toward the kernel vocabulary.

## 7. Current Code Mapping

| Kernel concept | Current code |
| --- | --- |
| Intent | `/command`, desktop commands, collaboration messages, mobile artifacts. |
| Planner | `backend/orchestrator/planner.py`, `hybrid_planner.py`, AgentCluster planning. |
| Context Kernel | `backend/orchestrator/context_store.py`, `context_mirror.py`, context write intents. |
| Resource Registry | `backend/orchestrator/resource_registry.py` contract seed plus JSON persistence (`JsonResourceRegistryStore`), `/desktop/resource-registry` metadata CRUD, and `AgentCluster.resource_registry_snapshot` runtime view. Current resources can be loaded from the persisted registry and are also projected from Worker descriptors, local device/workspace state, and active ecommerce projects; reviewed onboarding UI and credential binding policy are still pending. |
| Capability Registry | `backend/orchestrator/capability_graph.py` today; should become the canonical registry/API. |
| Scheduler | `backend/orchestrator/worker_pool.py`, AgentCluster scheduling, future capability resolver. |
| Workflow | `backend/orchestrator/workflow_graph.py`, `backend/app/workflow_management.py`. |
| Skill Registry | `backend/skills/`, `backend/app/skills_console.py`. |
| Worker Registry | `WorkerPool`, Android worker registry, remote node descriptors. |
| Agent | `backend/agents/`, `agent_management.py`, Agent Capability Container. |
| Memory | `backend/memory/`, learning records, context patches as runtime memory source. |
| Model Registry | `model_catalog.py`, `replaceable_brain.py`, training package code. |
| Governance | review gates, code jury, safety control, audit/action logs. |

The file `backend/app/skill_router.py` is a transition helper. Its long-term
home is Scheduler/Capability Resolution. It must not grow into a God Object.

## 8. Migration Direction

### Phase 1: Contract Cleanup

- Keep existing Skill Registry.
- Keep existing Workflow runtime.
- Keep Resource Registry as a thin contract/runtime-view layer over current project/device/shop/profile/KB descriptors.
- Add `implements` / `required_capabilities` to Skills.
- Make Skill snapshot/search/index capability-aware.
- Keep `/desktop/skill-router` as a temporary API for route experiments.
- Move `SkillContextPack` language toward Context Kernel view builder.

### Phase 2: Capability-First Workflow

- Add `capability_call` node type.
- Let Workflow nodes target `capability_id`.
- Let Workflow nodes reference `resource_id` for durable targets such as stores, browser profiles, repositories, phones, and knowledge bases.
- Scheduler resolves capability -> Skill -> Worker.
- Keep legacy `skill_call` nodes for compatibility.

### Phase 3: Unified Execution Trace

- SkillRunner, WorkflowRunner, Android Bridge, Remote Worker, and desktop
  project runner all emit the same Execution Trace contract.
- Skill Health is computed from traces.
- Learning Pipeline and Evaluation consume traces.

### Phase 4: Model Lifecycle

- Treat dataset, LoRA, merged model, distilled model, quantized model, benchmark,
  and deployment as Model Artifacts.
- Runtime routes by published Model capability profile, not by training method.
- Distillation becomes one lifecycle step, never a runtime layer.

## 9. Architecture Guardrails

1. Router resolves only. It does not execute, log, mutate memory, or enforce
   permission by itself.
2. Context is singular. New context needs become views or builders inside
   Context Kernel.
3. Workflow depends on Capability where possible, not concrete Skill.
4. Agent manages Goals, Resources, Policies, and State.
5. Resource persists. Workflow only performs bounded execution over resources.
6. Skill is replaceable implementation, not stable API.
7. Worker executes. Worker is not an Agent.
8. Model artifacts are replaceable. Runtime sees model capability profile.
9. Governance is explicit at boundaries, not hidden in helpers.
10. Health and learning should derive from execution traces.
11. New top-level concepts require a spec update and a mapping to the kernel
    concepts.

## 10. Immediate Decisions

- Do not add Knowledge Distillation as a runtime layer.
- Rename current distillation discussion to Model Lifecycle / Dataset Pipeline.
- Treat `Skill Router` as transitional; future architecture name is Capability
  Resolver under Scheduler.
- Treat `Skill Context` as Context Kernel skill view, not a separate context.
- Promote Capability Registry from supporting graph to first-class kernel API.
- Promote Resource Registry to a first-class kernel API for long-lived digital assets.
- Prefer fewer stronger concepts over more managers, routers, and registries.
