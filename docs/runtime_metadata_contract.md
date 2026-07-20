# SpiritKinAI Runtime Metadata Contract

Last updated: 2026-06-29

This document defines the shared metadata contract for the AI OS / Runtime direction. It is the bridge between the current implementation and the next architecture layer: Context Kernel, Scheduler, Agent Protocol, Execution Finalizer, Capability Graph, and Evolution.

Metadata is not a comment field. It is the schedulable, auditable description that lets the runtime choose models, Agents, Workflows, Skills, Workers, policies, and review gates without hardcoding every path.

## Current Foundations

The repo already has the right hooks:

- `backend/orchestrator/workflow_graph.py`: `WorkflowDefinition.metadata` and `WorkflowNodeDefinition.metadata`.
- `backend/skills/base.py`: `SkillSpec.metadata`, plus explicit runtime fields for output schema, cost, latency, success rate, required capabilities, Worker needs, side effects, and artifacts.
- `backend/orchestrator/worker_pool.py`: `WorkerDescriptor.metadata`, `WorkerRequirement.metadata`, and explainable schedule decisions.
- `backend/orchestrator/capability_graph.py`: `CapabilityRecord` and `CapabilityBinding` as the current Capability Graph spine.
- `backend/orchestrator/runtime_metadata.py`: normalized `RuntimeMetadata` contract for new runtime objects.
- `backend/orchestrator/context_store.py`: append-only Context Kernel seed.
- `backend/orchestrator/agent_protocol.py`: structured Agent message envelope.
- `backend/orchestrator/execution_finalizer.py`: VERIFY -> SCORE -> COMMIT skeleton.
- `backend/orchestrator/workflow_runtime_contracts.py`: Workflow run adapter that emits Context patches and Finalizer input without changing legacy Workflow storage.

Migration rule: new metadata fields must be additive and backward compatible. Legacy Workflows, Skills, and Workers can run with defaults; enforcement should happen at promotion, scheduling, review, or finalization boundaries.

## Universal Runtime Metadata

All runtime-addressable objects should converge on these common fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Metadata schema version, currently `spiritkin.runtime_metadata.v1`. |
| `object_type` | `workflow`, `node`, `skill`, `worker`, `agent`, `model_provider`, `execution`, `artifact`, or `policy`. |
| `object_id` | Stable runtime id. |
| `domain` | Domain such as `general`, `commerce`, `code`, `game`, `media`, `device`, or `runtime`. |
| `owner` | Owning Agent, module, or human/operator role. |
| `version` | Object version when applicable. |
| `status` | `candidate`, `active`, `deprecated`, `archived`, or `unknown`. |
| `tags` | Search and routing tags. |
| `source` | Human-authored, generated, imported, learned, or migrated source. |
| `risk_level` | `low`, `medium`, `high`, or a policy-specific risk label. |
| `permission_scope` | Required permission boundary. |
| `cost_hint` | Human-readable cost band for scheduler ranking. |
| `latency_hint_ms` | Expected latency for scheduling. |
| `success_rate` | Observed or estimated success rate. |
| `maturity` | Experimental, preview, production, hardware-validated, etc. |
| `policy_refs` | Policy and review references. |
| `context_refs` | Context ids or view refs needed to run. |
| `artifact_refs` | Produced or required artifacts. |
| `audit_refs` | Audit records, review ids, test/eval ids. |

## Object-Specific Metadata

### Workflow

Workflow metadata should describe a reusable AI Blueprint, not a fixed pipeline:

- `blueprint_type`: `workflow`, `function`, `macro`, or `event`.
- `template_params`: parameter names and defaults for factory-style Workflows.
- `supported_platforms`: platform instances such as `douyin`, `tiktok`, `xiaohongshu`, `local_pc`, or `android`.
- `required_capabilities`: Capability Graph ids.
- `required_workers`: Worker needs or WorkerRequirement refs.
- `input_schema_ref` / `output_schema_ref`: stable schema references.
- `trigger_events`: events that can start the Workflow.
- `finalizer_policy`: verification and commit policy.
- `success_criteria`: criteria the Finalizer must verify.
- `rollback_policy`: compensating action or manual review policy.
- `evolution_metrics`: success, failure, latency, cost, and review scores.

### Workflow Node

Node metadata should make each node schedulable:

- `node_kind`: `agent`, `skill`, `tool`, `workflow`, `condition`, `loop`, `parallel`, `event`, `review`, or `wait`.
- `required_capabilities`: capabilities required by this node.
- `worker_needs`: Worker scheduling needs.
- `input_contract` / `output_contract`: local data contract.
- `timeout_ms` / `retry_policy`: execution guardrails.
- `review_gate`: review type or policy id.
- `side_effects`: file write, network write, device action, purchase, publish, delete, etc.
- `artifact_contract`: artifacts produced or consumed by the node.

### Skill

Skill is a function-level schedulable capability. It should include:

- `input_schema` and `output_schema`.
- `required_capabilities` and `required_worker_needs`.
- `cost_hint`, `latency_hint_ms`, and `success_rate`.
- `risk_level`, `confirmation_policy`, and `rollback_strategy`.
- `side_effects` and `artifact_contract`.
- `success_criteria` and `eval_cases`.

### Worker

Worker metadata should describe execution capacity, not model intelligence:

- `worker_type` and `worker_subtype`.
- `capabilities` and `capability_namespaces`.
- `targets` and `operations`.
- `workspace` and `permission_scope`.
- `health_status`, `health_detail`, and `queue_depth`.
- `locality`: local, remote, cloud, device, browser, service, etc.
- `device_binding` or `node_id` when applicable.
- `cost_hint`, `latency_hint_ms`, and reliability.

### Agent

Agent metadata should describe role and allowed behavior:

- `role`: Master, Domain Agent, reviewer, worker-facing coordinator, or external assistant.
- `domain`: commerce, code, game, media, general, etc.
- `model_profile` and `provider_refs`.
- `allowed_skills`, `allowed_tools`, and `allowed_workers`.
- `knowledge_refs`.
- `router_policy`.
- `permission_scope` and review requirements.

### Model Provider

Model providers should be replaceable:

- `provider_type`: llama.cpp, OpenAI, Claude, Gemini, local OpenAI-compatible, etc.
- `endpoint` and `model_family`.
- `context_window`, modalities, and tool/function support.
- `cost_hint`, `latency_hint_ms`, health, and fallback group.
- `data_boundary`: local, private cloud, cloud, sensitive-disallowed, etc.

Current provider observation rule:

- `test_provider` and `sync_provider_models` actions write JSONL observations to `state/model_provider_health.jsonl`.
- Action results include `duration_ms`, `health_status`, `checked_at`, `model_count`, `context_id`, `context_patch_id`, and `context_path`.
- The explicit action snapshot also appends `/model/providers/health` Context patches under `model_provider:<provider>:<model>`. This is audit-only and does not trigger any automatic provider probe.
- Provider runtime metadata may read the latest observation for `health_status`, `latency_hint_ms`, `last_checked_at`, and `observed_model_count`.
- The runtime must not automatically probe cloud providers just to refresh metadata; health checks are user/desktop-triggered to avoid cost and rate-limit side effects.

### Execution

Execution metadata should make every run replayable:

- `context_id`, `task_id`, `workflow_run_id`, and `trace_id`.
- `selected_model`, `selected_agent`, `selected_worker`, and schedule decision.
- `input_snapshot_ref` and output artifacts.
- `verification_result`, `score`, and finalizer verdict.
- `audit_refs`, review ids, and permission ids.

### Perception Context

Perception is not a separate top-level Context system. It is an optional
Context Kernel view attached to a normal Agent request.

Current opt-in metadata:

- `include_perception_context`, `include_screen_context`,
  `screen_context_enabled`, or `perception_context_enabled`: explicit request
  to collect a read-only screen perception snapshot before planning.
- `perception_context_mode`: `ocr` / `text` for screen text extraction, or the
  default visual understanding path.
- `perception_context_query`: optional query passed to screen understanding.
- `perception_region`: optional region passed to the device backend.

When enabled, `AgentCluster` builds a governed `ExecutionRequest` for
`screen.screen_understand` or `screen.screen_extract_text`, evaluates policy,
executes through WorkerPool, merges the summary into `visual_context`, and
stores the structured `perception_context` record on reply metadata. If policy
denies the read or the operation would require confirmation, no screen data is
collected and the blocked reason is returned as metadata.

## Context Kernel

The Context Kernel is the unified state spine. Phase 1 is append-only and read-oriented; later phases can enforce ownership and patch rules.

Recommended context graph:

```text
Intent
  -> Task
  -> WorkflowRun
  -> NodeExecution
  -> SkillRun / ToolCall
  -> WorkerExecution
  -> Artifact
  -> Memory / Evaluation
```

Views:

- `/full`: Master and diagnostics.
- `/task`: Domain Agent task context.
- `/worker`: minimum Worker execution context.

Rule: Agents, Workflows, and Workers should not invent private state when a Context patch or artifact ref is the correct source of truth.

Current write path status:

- `/desktop/context` remains a read mirror for `context` and `runtime_context`.
- `JsonlContextStore` provides an independent append-only Context patch ledger at `state/context/context_patches.jsonl` by default. It can persist and reload `ContextPatch` records and filter them by `context_id` and view without taking ownership of legacy state sources.
- `/desktop/context` exposes this ledger as `context_ledger` so governed writes and future runtime patches are visible beside the read-only mirror.
- `write_intent_preview` is available as a governance contract, and `write_intents` exposes the append-only intent ledger.
- `ContextWriteIntent` records `context_id`, `target_path`, `operation`, `payload`, `actor`, and `requires_review`.
- `context_write_intents.py` supports submit, approve, reject, list, and applied transitions.
- `context_write_applier.py` currently allows approved `/context/policy` intents, mapped through `save_context_policy()`; approved `/project/overview/proposal` intents, mapped through `propose_project_overview_change()`; and approved collaboration append intents mapped through `post_collaboration_message()`, `record_collaboration_decision()`, or `record_collaboration_review()`. Other paths remain reviewable but not applicable until an explicit owner/applier exists.
- Successful `context_write_applier.py` applications append `/context/write_intents/applied` patches to `JsonlContextStore` with intent id, target path, operation, result type, and applied payload.

## Agent Protocol

Agent collaboration should use structured, governed messages instead of free-form implicit chat:

| Field | Meaning |
| --- | --- |
| `sender` / `recipient` | Agent, assistant, or human actor ids. |
| `message_type` | `question`, `answer`, `plan`, `decision`, `review_request`, `review`, `event`, or `handoff`. |
| `context_id` / `task_id` | Runtime scope. |
| `expected_output_schema` | Required response shape when needed. |
| `permission_scope` | Permission boundary. |
| `deadline_at` | Optional deadline. |
| `requires_review` | Whether a review gate is required. |
| `artifacts` | Linked files, patches, screenshots, test output, or context packs. |
| `metadata` | Additional routing and audit data. |

Rule: Domain Agents should not call Workers directly. They should request actions through Master/Router/ToolRegistry so permission, scheduling, audit, and review stay intact.

Current protocol status:

- `AgentEnvelope` is the canonical message payload and normalizes message type and permission scope.
- `AgentRoutePolicy` provides the first deterministic routing guard: allowed senders, recipients, message types, permission scopes, review-required scopes, context-required message types, and blocked direct recipients such as worker/executor targets.
- `InMemoryAgentRouter.try_send()` returns an `AgentRouteResult` instead of throwing, records an audit event for both allowed and blocked messages, and only stores messages that passed policy.
- `InMemoryAgentRouter.send()` keeps the older convenience API but now raises on blocked routes.
- `JsonlAgentRouteBus` is the durable route-bus seed at `state/agent_route_bus/`. It uses the same `AgentRoutePolicy`, writes all route audit events to `route_audit.jsonl`, and writes only accepted `AgentEnvelope` snapshots to `messages.jsonl`.
- Collaboration message writes now evaluate `AgentRoutePolicy` before appending to the collaboration `messages.jsonl`. Message snapshots include `route_verdict`, `route_audit_event`, and `route_bus_event`; invalid direct worker/executor routes or unreviewed privileged scopes are rejected before persistence.
- Accepted collaboration messages are mirrored into `JsonlAgentRouteBus`, while the collaboration ledger remains the UI read/write source for compatibility. This establishes a reusable durable contract for future Codex, Claude Code, local Agent, and service-worker routing without migrating the desktop page yet.
- `build_collaboration_snapshot()` exposes an `agent_route_bus` summary with storage paths, routed/blocked counts, and recent messages/audit events. The Context mirror maps it to `/agent_route_bus/summary` so `/desktop/context.runtime_context` can show whether collaboration messages reached the durable bus.
- Collaboration actions include a read-only `list_agent_route_bus_messages` query. It can filter bus messages by `to_agent`/`recipient`, `thread_id`/`context_id`, and `task_id`, and optionally includes route audit events. It does not mark messages as read or acknowledge consumption.
- `JsonlAgentRouteBus` now has a separate `message_acks.jsonl` ledger. Collaboration action `ack_agent_route_bus_message` records a consumer-level ack for an Agent bus message, and `list_agent_route_bus_messages` can pass `include_acked=false` to hide messages already acked by that consumer. This does not change collaboration UI `read_by` or message status.
- Collaboration action `run_agent_route_bus_worker_once` is the first dry-run worker loop for the durable bus. It reads one unacked message for an Agent, optionally acks it, and can post a deterministic dry-run answer for testing. It does not call a real model or external command; non-dry-run returns `real_worker_not_enabled`.
- Route-bus workers write diagnostic events to `worker_events.jsonl` through `record_agent_route_bus_worker_event` or the dry-run worker action. These events capture idle, processed, failed, and disabled states; they are diagnostic only and do not replace `message_acks.jsonl` as the consumption ledger.
- Collaboration action `agent_route_bus_worker_status` reports the route-bus worker control-plane state without consuming messages. It returns `mode=dry_run_only`, storage paths, supported actions, per-Agent pending/ack counts, recent worker events, and external assistant command readiness from Agent Management. `real_worker_status` becomes `ready` only when a configured enabled CLI command is discoverable; the backend still does not invoke that command from this status action. `build_collaboration_snapshot()` exposes the same data as `agent_route_bus_worker`, and the Context mirror writes a compact `/agent_route_bus/worker_status` patch.
- `scripts/collaboration_agent_worker.py` now defaults to `--transport route_bus`, so desktop-started Codex/Claude Code workers consume durable bus messages with `ack_agent_route_bus_message`. `--transport legacy_inbox` remains available for the older collaboration inbox/read_by path.
- `scripts/collaboration_mailbox.py inbox/read/watch` also default to `route_bus`, making manual Agent inbox inspection use the same `message_acks.jsonl` consumption ledger as the worker. `--transport legacy_inbox` remains available when the older collaboration `read_by` state needs to be inspected.
- `scripts/collaboration_mailbox.py status` wraps `agent_route_bus_worker_status` as a non-consuming CLI diagnostic for pending messages, ack counts, and configured external assistant readiness.
- Real CLI execution in `collaboration_agent_worker.py` fails closed unless the selected external assistant exists in Agent Management, is `enabled=true`, and has a non-empty command. Dry-run remains available for bus testing without invoking external models.

## Execution Finalizer

Tasks must not naturally end just because a Workflow stopped. The Finalizer closes the loop:

```text
VERIFY -> SCORE -> COMMIT
```

Lifecycle:

```text
CREATED
PLANNED
RUNNING
PARTIAL_SUCCESS
WAITING
FAILED
COMPLETED
COMMITTED
```

The Finalizer should verify success criteria, score output quality and risk, decide `commit`, `retry`, `review`, or `wait`, and write the final status and audit refs back to Context.

Current scheduler task status:

- Workflow runs already produce `ExecutionSummary` and terminal Finalizer verdict sidecars.
- Collaboration and explicitly bound ecommerce tasks can receive terminal Workflow verdict sync.
- In-memory `TaskQueue` tasks now run through `scheduler_task_finalizer.py` after complete/fail/block paths in `AgentCluster.process_next_queued_task()`.
- `ScheduledTask.snapshot().finalizer` includes `decision`, `next_status`, `score`, `verified`, `reasons`, `updated_at`, `source`, `context_id`, `context_patch_id`, and `context_path`.
- Each scheduler task verdict appends a `/scheduler/tasks/finalizer` `ContextPatch` under `task:<task_id>` with scheduler status, stage statuses, result/error summary, and the finalizer snapshot. This keeps non-Workflow queued tasks visible in the Context Kernel without changing the in-memory queue owner.
- First-pass scheduler mapping is intentionally simple: `complete` -> `COMPLETED/success=True`, `failed` and `blocked` -> `FAILED/success=False`, `queued` and `running` -> `WAITING/success=False`.
- SkillRunner now appends `skill_runner.run` trajectories for dry-runs,
  successful executions, allowlist blocks, safety blocks, missing Skills, and
  failed required steps. This feeds the same evolution JSONL log as
  AgentCluster executor/failure trajectories without changing Skill return
  semantics.
- WorkflowRunner now appends `workflow_runner.node` trajectories for tool and
  Skill node successes/failures. The node output metadata receives a
  `trajectory_record` reference when logging succeeds, while logging failures
  stay non-fatal and are reported as `trajectory_log_error`.
- Collaboration route-bus terminal worker events now append
  `collaboration.worker_event` trajectories. Streaming stdout/stderr/token
  events remain work-event/UI telemetry only, so the evolution dataset is not
  polluted with partial token chunks.
- Android command result reports now append `android.command_result`
  trajectories, and RemoteWorker execute/package/rollback terminal results
  append `remote.worker_result` trajectories.

## Scheduler And Capability Graph

The Scheduler should eventually rank choices by:

- Required capability match.
- Worker availability, health, queue depth, and locality.
- Model capability, context window, cost, latency, and data boundary.
- Risk level, permission scope, and review requirement.
- Historical success rate and evolution score.

The current `CapabilityRecord` already links tools, Skills, Workflows, Workers, Agents, policy refs, and knowledge refs. The next step is to normalize metadata into those records consistently and use them as the Master Scheduler input.

Current Worker seed status:

- The default AgentCluster runtime now registers ready `executor:python_worker`, `executor:git_worker`, `executor:ffmpeg_worker`, and `executor:service_rag_worker` records.
- `python.run_script`, `git.status`, `git.diff`, `git.commit`, `ffmpeg.probe`, `ffmpeg.transcode`, `rag.search`, `knowledge.retrieve`, and `embedding.create` create governed `ExecutionRequest` records that WorkerPool can schedule to the matching worker.
- The Python/Git/FFmpeg workers constrain paths to the workspace and avoid shell execution. Service RAG is read-only for retrieval; `embedding.create` fails closed unless a real embedding provider is configured.
- OpenClaw can be assembled with in-memory/local JSON simulation or a real HTTP controller transport via `SPIRITKIN_OPENCLAW_HTTP_BASE_URL`, `SPIRITKIN_OPENCLAW_HTTP_TOKEN`, and `SPIRITKIN_OPENCLAW_HTTP_TIMEOUT`. This creates an executable transport path; hardware validation remains a separate promotion requirement.
- `WorkerPool.snapshot()` still includes planned, non-schedulable descriptors for `python_worker`, `ffmpeg_worker`, `git_worker`, and `service_rag_worker`.
- Planned seeds expose `worker_type`, `worker_subtype`, `capability_namespaces`, `permission_scope`, and `maturity=planned`.
- `capabilities_from_worker_descriptor()` can convert these descriptors into CapabilityGraph records with `planned=true` and `schedulable=false`.
- Planned workers are taxonomy and CapabilityGraph-facing metadata only. They must not be selected by `WorkerPool.schedule()`; real capacity comes from ready executor descriptors.
- `CapabilityRegistry.recommend()` is the first read-only candidate-selection API for Master/Scheduler use. It ranks capabilities by query, domain, required capability ids, required Worker needs, and schedulability, and returns `CapabilityRecommendation` with scored `CapabilityCandidate` records.
- Each `CapabilityCandidate` now includes `worker_evidence` for every declared worker requirement. Evidence reports whether a matching worker descriptor is `ready`, `planned`, or `missing`, plus matched worker ids, matched capability ids, health statuses, reasons, and gaps. This is explanatory evidence only; it does not dispatch a Worker.
- Recommendation is not execution. Planned Worker seed capabilities are filtered out by default and only appear when `include_planned=true`, still marked `schedulable=false` with a `not_schedulable` gap.
- `HybridPlannerPipeline` now includes the read-only recommendation result in `hybrid_planner.capability_recommendation`, so scheduler metadata can explain candidate capabilities without changing the selected route.

## Migration Checklist

P0, additive and low risk:

- Keep this contract current.
- Use `RuntimeMetadata` for new runtime-facing objects.
- Keep `SkillSpec` metadata fields explicit and persisted.
- Write Context patches for important task/workflow/worker events without enforcing ownership yet.
- Record Finalizer verdicts in run outputs or task ledger metadata.

P1, integration:

- Add a read-only ContextStore mirror around existing task, workflow, Agent, and Worker state.
- Route cross-Agent communication through `AgentEnvelope` and `AgentRoutePolicy`.
- Add Finalizer calls at Workflow completion and task completion points.
- Normalize model/provider metadata and health into the same metadata language.
- Current P1 seed status:
  - Workflow runs can be converted into Context patches and `ExecutionSummary`.
  - `JsonWorkflowStore.save_run()` persists Workflow context records and terminal-run Finalizer verdicts to JSONL sidecars.
  - `backend/orchestrator/context_mirror.py` mirrors desktop project/session state, collaboration ledger state, and ecommerce queue state into ContextStore patches.
  - `JsonlContextStore` persists append-only Context patches as a reusable ledger seed while old desktop/collaboration/ecommerce stores remain the write owners.
  - `/desktop/context` now returns `runtime_context` and `context_ledger` alongside the existing context policy snapshot.
  - Terminal Workflow verdicts sync bound collaboration tasks when `run.inputs.task_id`, `collaboration_task_id`, or `ledger_task_id` is present.
  - Terminal Workflow verdicts sync explicitly bound ecommerce queue tasks when `run.inputs.ecommerce_task_id` / `commerce_task_id` or matching `metadata` keys are present.
  - Collaboration messages expose `agent_envelope` while preserving legacy message fields.
  - Collaboration CLI and background worker paths now prefer `agent_envelope` for sender, message type, content, context id, and context-pack artifacts while retaining legacy field fallback.
  - `InMemoryAgentRouter` now evaluates `AgentRoutePolicy`, blocks direct worker/executor recipients and unreviewed privileged scopes, and records route audit events.
  - `JsonlAgentRouteBus` now persists accepted `AgentEnvelope` snapshots, blocked/accepted route audit events, and consumer-level message acks. Accepted collaboration messages are mirrored into the bus while collaboration storage remains UI-owned; snapshots and collaboration actions expose bus status/messages/acks plus a dry-run one-message worker loop for UI debug and future workers.
  - `/desktop/collaboration` applies the same route guard before writing collaboration messages and returns route verdict/audit metadata on accepted messages.
  - In-memory scheduler `TaskQueue` terminal paths now write Finalizer verdict snapshots and `/scheduler/tasks/finalizer` Context ledger patches.
  - Model provider snapshots expose normalized `runtime_metadata`, and provider test/sync actions record health/latency observations.
  - `/desktop/context` exposes `write_intent_preview`, an append-only reviewed `write_intents` ledger, a narrow approved `/context/policy` write applier, a Project Overview proposal applier, and collaboration append appliers for messages, decisions, and reviews. Successful applies also write Context ledger patches.
  - WorkerPool taxonomy includes planned Python, FFmpeg, Git, and Service RAG worker seeds without making those seeds schedulable; ready executor paths provide the executable capacity.
  - CapabilityGraph has a read-only `recommend()` API that returns explainable candidate rankings without dispatching Workers.
  - Hybrid planner snapshots expose those rankings as `capability_recommendation`; AgentCluster scheduler metadata carries the same additive field.
  - `backend/orchestrator/runtime_trajectory_log.py` appends AgentCluster execution/failure, SkillRunner, WorkflowRunner node, collaboration route-bus terminal worker, Android command result, and RemoteWorker result trajectories to `state/evolution/trajectories.jsonl` by default. `/desktop/evolution` reads the same log, derives eval cases from failed trajectories, and can export a self-training chat JSONL dataset from those cases.

P2, enforcement:

- Make Context Kernel the source of truth for task state transitions.
- Require schema and finalizer policy before promoting candidate Workflows and Skills.
- Let Capability Graph select Workflow, Skill, Worker, and Model candidates for Master.
- Add evolution analysis and promotion gates based on execution traces.

## Coding Standards For This Migration

- Do not add runtime concepts into `AgentCluster`, `WorkflowRunner`, or WPF views as large inline blocks.
- Prefer small modules by responsibility: metadata contract, context store, agent protocol, finalizer, scheduler adapters.
- Keep old paths working while adding normalized snapshots.
- Add tests at the contract boundary before enforcing behavior.
- Avoid prompt-only semantics when a structured metadata field can carry the decision.
