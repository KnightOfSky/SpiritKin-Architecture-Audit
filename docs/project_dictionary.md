# SpiritKinAI Project Dictionary

Last updated: 2026-06-29

This dictionary defines canonical project terms, IDs, state names, and module boundaries for human operators, Codex, Claude Opus, external reviewers, and future RAG indexing. Use this file when a concept has multiple possible names or when a model needs to decide which subsystem owns a change.

This is a coordination document, not a runtime source of truth. Code constants and schemas still own executable behavior. When code and this dictionary diverge, fix the dictionary or add a TODO with the exact file that must be reconciled.

## How To Use

- Prefer the canonical term in code comments, UI labels, docs, task names, and cross-model discussion.
- Add aliases only to clarify old names. Do not introduce new aliases unless legacy code already uses them.
- Keep entries short. Detailed architecture belongs in `docs/current_architecture_snapshot.md` or `docs/runtime_metadata_contract.md`.
- When adding a new runtime concept, add its owner path and relation to Agent, Workflow, Skill, Worker, Context, or Finalizer.

## Reading Map

| Need | Read |
| --- | --- |
| Stable AI Runtime Kernel concepts | `docs/ai_runtime_kernel_spec.md` |
| Current architecture | `docs/current_architecture_snapshot.md` |
| Runtime metadata and AI OS contract | `docs/runtime_metadata_contract.md` |
| Cross-model coordination | `docs/project_management_overview.md` |
| Compact external-model handoff | `docs/ai_collaboration_context.md` |
| Codebase path map (historical) | `docs/archive/codebase_map.md` — superseded by `docs/current_architecture_snapshot.md` |
| 3D avatar constraints | `docs/avatar_3d_animatable_model_pipeline.md` |

## Canonical Runtime Terms

| Canonical term | Avoid / aliases | Meaning | Main owner |
| --- | --- | --- | --- |
| AI OS Runtime | agent platform, assistant app | The whole governed runtime: intent, context, scheduler, agents, workflows, skills, workers, finalizer, evolution. | `docs/runtime_metadata_contract.md` |
| Master Scheduler | master, main brain, orchestrator | The decision layer that interprets goals, chooses workflow/agent/worker/model routes, and enforces policy boundaries. | `backend/orchestrator/` |
| Domain Agent | specialist, expert agent | A role-specific reasoning component such as commerce, code, game, media, or vision. It should analyze and decide, not directly drive devices. | `backend/agents/`, `backend/orchestrator/agent_cluster.py` |
| Model | brain, LLM | A concrete model such as Qwen, GPT, Claude, Gemini, or local vision model. Models are replaceable. | `backend/app/learning_workflow.py` |
| Model Provider | provider, backend, endpoint | The service adapter for local/cloud model access, such as llama.cpp, OpenAI-compatible, Claude, Gemini. | `backend/app/learning_workflow.py` |
| Model Provider Health | provider observation, health probe | User-triggered provider test/sync observation with health status, latency, checked time, error, model count, and `/model/providers/health` Context ledger refs. Not an automatic cloud probe. | `state/model_provider_health.jsonl`, `backend/app/learning_workflow.py`, `backend/app/model_provider_context.py` |
| Workflow | blueprint, graph, flow | Reusable AI Blueprint. It can contain Agent, Skill, Tool, Condition, Wait, Review, Subgraph, and future Event/Function/Macro nodes. | `backend/orchestrator/workflow_graph.py` |
| Workflow Function | function workflow | A reusable workflow fragment hidden behind a function-like node. Planned direction. | `docs/runtime_metadata_contract.md` |
| Workflow Macro | macro workflow | Reusable procedural block such as login, retry, wait, validation. Planned direction. | `docs/runtime_metadata_contract.md` |
| Skill | ability, capability function | Function-level semantic capability with schemas, cost, latency, worker needs, side effects, and artifact contracts. | `backend/skills/` |
| Tool | semantic tool | ToolRegistry callable that performs or requests a capability. Tools are not raw drivers. | `backend/tools/` |
| Worker | bridge, executor node | Runtime execution node. Android Bridge, Browser, Remote, Python, FFmpeg, Git, OpenClaw, and services should converge here. | `backend/orchestrator/worker_pool.py` |
| Executor | action executor | The layer that turns approved tool requests into actual local, device, remote, or service actions. | `backend/executors/` |
| Bridge | connector | Legacy or implementation-specific connector. Treat as a Worker implementation, not a top-level architecture concept. | `backend/mobile/`, `backend/devices/` |
| Capability Registry | capability API | Stable API of what the runtime can do. Workflow and Planner should target Capability ids; Skills and Workers are replaceable implementations. | `docs/ai_runtime_kernel_spec.md`, `backend/orchestrator/capability_graph.py` |
| Capability Graph | capability map | Queryable graph linking intent to workflow, skill, worker, model, policy, knowledge, and artifact requirements. | `backend/orchestrator/capability_graph.py` |
| CapabilityRecommendation | capability candidate ranking | Read-only Master/Scheduler planning result with scored capability candidates, reasons, gaps, and schedulability. It does not dispatch Workers. | `backend/orchestrator/capability_graph.py` |
| Capability Resolver | Skill Router, resolver | Scheduler-internal selection step that maps a Capability need to candidate Skills/Workers. It should resolve only and must not execute, mutate context, or become a top-level architecture concept. | `docs/ai_runtime_kernel_spec.md`, transitional `backend/app/skill_router.py` |
| Context Kernel | context store, shared state | Unified runtime state spine. Current version is additive sidecars and append-only context records. | `backend/orchestrator/context_store.py` |
| Context View | Skill Context, Agent Context, Workflow Context | Projection from the Context Kernel for a specific consumer. Do not create separate top-level Context families. | `docs/ai_runtime_kernel_spec.md`, `backend/orchestrator/context_store.py` |
| ContextWriteIntent | write intent preview | Governed request describing a future Context write. It validates intent and can be submitted/approved/rejected/applied in a ledger. Only explicitly owned paths can apply. | `backend/orchestrator/context_mirror.py`, `backend/orchestrator/context_write_intents.py` |
| Context Write Applier | context applier | Governed writer for approved ContextWriteIntent records. Current allowed paths are `/context/policy`, `/project/overview/proposal`, `/collaboration/message`, `/collaboration/decision`, and `/collaboration/review`. | `backend/orchestrator/context_write_applier.py` |
| Runtime Metadata | metadata contract | Schedulable and auditable object descriptors for workflows, skills, workers, agents, models, executions, artifacts, and policies. | `backend/orchestrator/runtime_metadata.py` |
| AgentEnvelope | structured message | Canonical cross-agent message payload. Legacy message fields remain fallback only. | `backend/orchestrator/agent_protocol.py` |
| AgentRoutePolicy | agent routing guard | Deterministic cross-agent route policy for allowed actors, message types, permission scopes, review-required scopes, blocked direct worker/executor recipients, and audit events. | `backend/orchestrator/agent_protocol.py` |
| JsonlAgentRouteBus | durable agent bus | JSONL route-bus seed that writes accepted `AgentEnvelope` messages to `messages.jsonl` and all allow/block route audits to `route_audit.jsonl`. Accepted collaboration messages mirror into it, and snapshots expose `/agent_route_bus/summary`, while collaboration UI storage remains owner. | `backend/orchestrator/agent_protocol.py`, `backend/app/collaboration.py`, `backend/orchestrator/context_mirror.py` |
| Route Bus Inbox Query | agent bus inbox | Read-only collaboration action `list_agent_route_bus_messages` for recipient/context/task filtered Agent bus messages and optional route audits. It does not mark messages consumed. | `backend/app/collaboration.py` |
| Route Bus Ack | agent bus ack | Consumer-level `message_acks.jsonl` record created by `ack_agent_route_bus_message`. It hides already consumed route-bus messages for that consumer when requested and does not change collaboration UI read state. | `backend/orchestrator/agent_protocol.py`, `backend/app/collaboration.py` |
| Route Bus Worker Event | worker event ledger | Diagnostic JSONL event written to `worker_events.jsonl` for route-bus worker idle, processed, failed, or disabled states. It does not route messages and does not prove message consumption. | `backend/orchestrator/agent_protocol.py`, `backend/app/collaboration.py`, `scripts/collaboration_agent_worker.py` |
| Route Bus Dry-Run Worker | agent bus worker preview | Dry-run-only action `run_agent_route_bus_worker_once` that reads one unacked route-bus message, optionally acks it, and can post a deterministic test answer without calling a real model. | `backend/app/collaboration.py` |
| Route Bus Worker Status | agent bus worker status | Non-consuming action `agent_route_bus_worker_status` and snapshot field `agent_route_bus_worker` for dry-run worker availability, external assistant command readiness, storage paths, and per-Agent pending/ack counts. It reports readiness only; it does not invoke CLI models. | `backend/app/collaboration.py`, `backend/app/collaboration_worker_status.py`, `backend/orchestrator/context_mirror.py` |
| Collaboration Agent Worker | model collaboration worker | Script bridge for Codex/Claude Code style external assistants. Default transport is `route_bus`, which reads `JsonlAgentRouteBus` and consumes messages through `ack_agent_route_bus_message`; `legacy_inbox` remains for old `read_by` behavior. | `scripts/collaboration_agent_worker.py` |
| Collaboration Mailbox CLI | mailbox, manual inbox | Human/script CLI for sending, replying, inspecting, consuming, watching, and diagnosing collaboration messages. `inbox/read/watch/status` default to the durable route bus so manual inspection matches worker consumption state. | `scripts/collaboration_mailbox.py` |
| Route Verdict | route audit | Per-message allow/block result for Agent routing. Collaboration messages include route verdict and audit metadata when accepted. | `backend/orchestrator/agent_protocol.py`, `backend/app/collaboration.py` |
| Execution Finalizer | finalizer, task closer | VERIFY -> SCORE -> COMMIT layer that decides commit, retry, review, or wait after execution. | `backend/orchestrator/execution_finalizer.py` |
| Scheduler Task Finalizer | task queue finalizer | Adapter that maps in-memory `ScheduledTask` state to `ExecutionSummary`, writes verdicts back to `TaskQueue`, and appends `/scheduler/tasks/finalizer` Context patches. | `backend/orchestrator/scheduler_task_finalizer.py`, `backend/orchestrator/task_queue.py` |
| Task Ledger | ledger, task book | Durable task list for collaboration and project work. | `backend/app/collaboration.py` |
| Context Pack | handoff pack | File bundle or markdown snapshot attached to a model collaboration message. | `backend/app/collaboration.py` |
| Review Gate | approval gate, jury gate | Explicit review/approval boundary before promotion, execution, publish, or risky action. | `backend/app/review_gate.py`, workflow review nodes |
| Evolution | promotion, learning loop | Evaluation and improvement loop for skills, workflows, models, and actions. | `backend/app/evolution*`, `backend/model/training/`, `backend/evaluation/` |
| Model Lifecycle | distillation layer, training layer | Dataset, fine-tune, LoRA/QLoRA, merge, distill, quantize, evaluate, and publish steps for Model Artifacts. It belongs to Model Registry, not Runtime. | `docs/ai_runtime_kernel_spec.md`, `backend/model/training/`, `backend/app/replaceable_brain.py` |
| Model Artifact | distilled model, LoRA, quantized model | Versioned model-related artifact produced by Model Lifecycle. Runtime should route by model capability profile, not by whether the artifact was distilled. | `docs/ai_runtime_kernel_spec.md` |
| Artifact | output file, evidence | Generated or captured file referenced by runs, tasks, messages, reviews, or context. | `state/`, `runtime/`, module-specific artifact dirs |
| Audit Event | audit log | Append-only evidence of important decisions, syncs, policy checks, and state changes. | module-specific audit/event files |

## Agent, Model, And Assistant Names

| Term | Meaning |
| --- | --- |
| Agent | Runtime participant with role, policy, tools, and model/provider routing. |
| Model | The LLM or multimodal model used by an Agent or external assistant. |
| External assistant | CLI/API assistant such as Codex CLI or Claude Code used through collaboration worker paths. |
| Codex | Coding assistant / agent participant. Do not call it the model unless discussing the underlying OpenAI model separately. |
| Claude Code | External coding agent/CLI participant. |
| Opus | Claude model family/name, not the same as Claude Code the agent/tool. |
| `human_desktop` | The desktop operator/user identity inside collaboration messages. |

Rule: use `Agent` for the actor, `Model` for the brain, and `Provider` for the endpoint. Example: "Claude Code Agent uses an Opus model through a Claude provider."

## Project Surfaces

| Surface | Canonical name | Main path / endpoint |
| --- | --- | --- |
| Native app | Desktop | `desktop/SpiritKinDesktop/SpiritKinDesktop.csproj` |
| Browser management UI | Web desktop console | `frontend/desktop_console.html`, `http://127.0.0.1:8787` |
| Avatar page | 3D avatar | `frontend/avatar_3d.html?config=models/spirit3d/manifest.json` |
| HTTP API | Command gateway | `backend/app/command_gateway.py`, `http://127.0.0.1:8788` |
| Realtime bridge | Event bridge | `ws://127.0.0.1:8765` |
| Static/dev frontend server | Static frontend server | `backend/app/static_frontend_server.py` |
| Voice/runtime loop | Runtime loop | `backend/main.py` -> `backend/app/runtime.py` |
| Optional remote execution | Remote worker | normally `http://127.0.0.1:8790` |

## IDs And Binding Rules

| ID | Scope | Rule |
| --- | --- | --- |
| `project_id` | Project/workspace | Stable project/workspace grouping. Desktop should derive workspace from selected project/session where possible. |
| `session_id` | Chat session | One user-facing chat/session thread. |
| `task_id` | Collaboration task ledger | Generic `task_id` is reserved for collaboration/project task ledgers unless a module explicitly documents another scope. |
| `collaboration_task_id` | Collaboration task ledger | Explicit alias for collaboration task binding. Preferred when avoiding ambiguity. |
| `ledger_task_id` | Collaboration task ledger | Legacy/alternate explicit collaboration ledger binding. |
| `ecommerce_task_id` | Ecommerce queue | Required for workflow finalizer sync into ecommerce queue. Do not rely on generic `task_id` for ecommerce queue sync. |
| `commerce_task_id` | Ecommerce queue | Accepted alias for `ecommerce_task_id`. |
| `thread_id` | Collaboration message thread | Message thread inside collaboration. Can correspond to a project, session, or task depending on binding range. |
| `workflow_run_id` / `run_id` | Workflow run | One concrete execution of a workflow definition. |
| `context_id` | Context Kernel | Runtime context scope, commonly `workflow:<run_id>` or a collaboration thread id. |
| `target_path` | Context write intent | Context path a future governed write would affect. Must be normalized like `/project/active/title`. |
| `message_id` | Collaboration message | One `AgentEnvelope`/legacy message record. |
| `artifact_id` / artifact path | Artifact | Refer to files or generated outputs through stable paths/refs. |

Binding rule: `ContextWriteIntent` approval is not proof that a write happened. Only `applied` records from an explicit governed applier prove the approved intent was handled. `/context/policy` mutates ContextPolicy; `/project/overview/proposal` creates a pending proposal and does not overwrite the overview until that proposal is separately approved; collaboration paths append ledger records and do not run workers.

Binding rule: when a workflow run should update a non-collaboration queue, use explicit module-specific keys such as `ecommerce_task_id`. This prevents a generic `task_id` from updating the wrong store.

## State And Status Vocabulary

### Workflow Run Status

| Status | Meaning |
| --- | --- |
| `running` | Run is active or can advance. |
| `waiting` | Run is waiting on a signal, callback, worker, or event. |
| `waiting_review` | Run is paused at a review gate. |
| `succeeded` | Workflow execution reached a successful terminal state. Finalizer still decides whether to commit. |
| `failed` | Workflow execution failed. |
| `blocked` | Run cannot continue without intervention or missing capability. |
| `archived` | Retained historical run, not active. |

### Workflow Node Status

| Status | Meaning |
| --- | --- |
| `pending` | Node has not started. |
| `running` | Node is executing or claimed. |
| `waiting` | Node is waiting for signal/callback/external event. |
| `waiting_review` | Node requires review approval. |
| `succeeded` | Node completed successfully. |
| `failed` | Node failed. |
| `blocked` | Node cannot run due to missing dependency/capability/permission. |

### Finalizer Decision

| Decision | Meaning | Typical mapped status |
| --- | --- | --- |
| `commit` | Verified and score passed threshold. | `COMMITTED`, collaboration `complete`, ecommerce `workflow_complete` |
| `retry` | Not verified or quality/risk insufficient. | collaboration `blocked`, ecommerce `workflow_blocked` |
| `review` | Partial result needs human/model review. | collaboration `review`, ecommerce `workflow_review` |
| `wait` | Incomplete but waiting is legitimate. | collaboration `waiting`, ecommerce `workflow_waiting` |

### Collaboration Task Status

| Status | Meaning |
| --- | --- |
| `active` | Work is open. |
| `waiting` | Waiting for input, worker, review, or dependency. |
| `review` | Needs review before completion or next phase. |
| `blocked` | Cannot proceed without intervention. |
| `complete` | Done and accepted by Finalizer or operator. |
| `archived` | Hidden from active work but retained. |

### Ecommerce Queue Status

| Status | Meaning |
| --- | --- |
| `link_received` | Product/source link was ingested. |
| `image_queued` | Image-based task has been queued. |
| `probe_captured` | Probe/OCR/screenshot artifacts were captured. |
| `productdata_ready` | Product data package passed listing gate. |
| `productdata_ready_with_gaps` | Product data exists but listing gate found gaps. |
| `workflow_complete` | Bound workflow finalizer committed the task. |
| `workflow_blocked` | Bound workflow finalizer requires retry/intervention. |
| `workflow_review` | Bound workflow finalizer requires review. |
| `workflow_waiting` | Bound workflow finalizer is waiting. |

## Module Ownership Dictionary

| Area | Owns | Main path |
| --- | --- | --- |
| Desktop shell/UI | WPF navigation, chat, management panels, embedded avatar | `desktop/SpiritKinDesktop/` |
| Web console | Browser management UI and avatar pages | `frontend/` |
| Command gateway | `/desktop/*` API routing | `backend/app/command_gateway.py` |
| Collaboration | model collaboration tasks, messages, file claims, context packs | `backend/app/collaboration.py`, `scripts/collaboration_*.py` |
| Workflow runtime | workflow definitions, runs, nodes, stores, contracts | `backend/orchestrator/workflow_graph.py`, `backend/orchestrator/workflow_store.py` |
| Runtime contracts | metadata, context, envelopes, finalizer, task sync | `backend/orchestrator/runtime_metadata.py`, `context_store.py`, `agent_protocol.py`, `execution_finalizer.py`, `workflow_task_finalizer.py` |
| Worker scheduling | worker descriptors, capability matching, schedule decisions | `backend/orchestrator/worker_pool.py` |
| Skills | skill specs, persistence, promotion | `backend/skills/` |
| Tools | tool registry and semantic tool calls | `backend/tools/` |
| Executors | approved action execution | `backend/executors/` |
| Devices | local PC, OpenClaw, Android/device adapters | `backend/devices/`, `backend/mobile/` |
| Knowledge/RAG | ingest, store, retrieval, search management | `backend/knowledge/`, `backend/app/search_management.py` |
| Model management | model/provider catalog and learning workflow | `backend/app/learning_workflow.py` |
| Ecommerce tasks | ecommerce task queue and product data artifacts | `backend/orchestrator/ecommerce_task_queue.py` |
| Avatar runtime | 3D avatar page, model manifest, locomotion persistence | `frontend/avatar_3d.html`, `frontend/models/spirit3d/manifest.json`, `backend/app/static_frontend_server.py` |

## Naming Rules

- Use `Worker` for runtime execution nodes; keep `Bridge` only when referring to legacy implementation names.
- Use `Skill` for semantic capability units and `Tool` for ToolRegistry callables.
- Use `Workflow` for reusable blueprints and `Workflow run` for one execution.
- Use `AgentEnvelope` for structured collaboration messages; legacy fields are fallback compatibility only.
- Use `AgentRoutePolicy` for cross-Agent routing checks before messages become durable transport, especially when permission scope or worker-facing requests are involved.
- Use `Finalizer` for post-execution verification and commit/retry/review/wait decisions.
- Do not call every background process an Agent. A worker without model reasoning is a Worker, not an Agent.
- Do not call Opus an Agent. Opus is a model; Claude Code is an external assistant/agent participant.

## Add / Update Checklist

When adding a concept:

1. Add the canonical name, aliases, meaning, and owner path here.
2. If it has runtime behavior, add metadata expectations to `docs/runtime_metadata_contract.md`.
3. If it changes architecture or active work, update `docs/current_architecture_snapshot.md` or `docs/project_management_overview.md`.
4. If external models need the context, update `docs/ai_collaboration_context.md`.
5. Add tests for code behavior. Do not rely on dictionary text as enforcement.
