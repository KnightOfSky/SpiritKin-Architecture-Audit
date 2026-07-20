# SpiritKinAI Current Architecture Snapshot

Last updated: 2026-06-29

This document is the current system map for human operators, the main Agent, reviewer models, and RAG retrieval. It should be read before adding new Agent routing, Search/RAG, Skill, model, training, desktop, or governance work.

For stable kernel boundaries and the long-term target architecture, read `docs/ai_runtime_kernel_spec.md` before introducing new top-level concepts. That spec defines the AI Runtime Kernel concepts and treats names such as Skill Router or Skill Context as implementation details under Scheduler and Context Kernel, not permanent peer layers.

For canonical terminology, aliases, ID scopes, status vocabulary, and module ownership, also read `docs/project_dictionary.md`. That dictionary is the naming reference; this file is the architecture map.

## 1. Runtime Entry Points

| Surface | Entry | Role |
| --- | --- | --- |
| Native desktop | `desktop/SpiritKinDesktop/SpiritKinDesktop.csproj` | Main WPF operator console for chat, services, diagnostics, learning, models, agents, skills, KB, Search/RAG, evolution, and governance. |
| Web desktop console | `frontend/desktop_console.html` on `http://127.0.0.1:8787` | Browser-based control surface for the same management APIs and realtime event bridge. |
| Command gateway | `backend/app/command_gateway.py` on `http://127.0.0.1:8788` | HTTP management and command API, including `/desktop/*` endpoints. |
| Event bridge | realtime WebSocket on `ws://127.0.0.1:8765` | Runtime event fanout for assistant messages, state, avatar, speech, services, and UI sync. |
| Voice/runtime loop | `backend/main.py` -> `backend/app/runtime.py` | Local voice/hotword/runtime assembly path. |
| Avatar frontends | `frontend/avatar_3d.html`, `frontend/live2d.html`, `frontend/spirit_avatar.html` | Visual expression and event-driven avatar surfaces. |

Current local service targets:

- Frontend: `8787`
- Event bridge: `8765`
- Command gateway: `8788`
- Optional remote worker: `8790`

## 2. Backend Layers

| Layer | Main modules | Responsibility |
| --- | --- | --- |
| App/control plane | `backend/app/` | Runtime assembly, desktop APIs, services, model/learning management, module status, governance, Search/RAG management. |
| Orchestration | `backend/orchestrator/` | `AgentCluster`, planner, session manager, route decisions, short context, tool/agent dispatch. |
| Agents | `backend/agents/` plus managed Agent config | Native specialist agents for programming, vision, ecommerce, and extensible domain adapters. |
| Tools | `backend/tools/` | Tool specs and registry. Tools are semantic capabilities, not raw device drivers. |
| Execution | `backend/executors/` | Turns approved actions into executable work on local PC, OpenClaw, remote nodes, or future connectors. |
| Devices/actions | `backend/devices/`, `backend/action/` | Device adapters and high-level action semantics. |
| Knowledge/RAG | `backend/knowledge/` | Document ingest, chunking, stores, retrieval, embedding retriever, reranker, indexer, connectors. |
| Memory | `backend/memory/` | Short-term history, summaries, and longer-lived LPM/personality state hooks. |
| Perception/expression | `backend/perception/`, `backend/expression/` | ASR, wakeword, opt-in screen perception context, vision analysis, TTS, speech events, avatar state. |
| Security/governance | `backend/app/review_gate.py`, `backend/app/module_governance.py`, audit/permission modules | Confirmation gates, review policies, module maturity, audit records, proposal queue. |
| Training/eval | `backend/model/training/`, `backend/evaluation/`, evolution management | Learning records, failure trajectories, eval cases, self-training package generation. |

Hard rule for execution:

```text
Agent / Skill / external assistant
  -> ToolRegistry
  -> permission / review / confirmation gate
  -> Executor
  -> Device / connector / remote worker
```

External models and external CLIs may review or propose, but should not bypass the execution guard.

Kernel boundary rule:

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
```

Workflow should increasingly persist Capability requirements and Resource targets
rather than concrete Skill names. Existing `skill_call` nodes remain valid for
compatibility, but new reusable workflows should target Capability ids wherever
possible.

## 2.1 Worker / Capability Runtime

The execution layer is now treated as a natural evolution of the existing
executor, bridge, and automation pieces rather than a rewrite. Existing names
remain valid at the implementation boundary, but runtime snapshots classify them
by responsibility through `WorkerPool` taxonomy:

```text
Runtime Kernel
  -> Master Scheduler / AgentCluster
  -> Agent Pool
  -> Skill Registry
  -> Capability Graph
  -> Worker Pool
       -> Device Workers
       -> Browser Workers
       -> Execution Workers
       -> Service Workers
       -> Generic Remote Workers
```

Current positioning:

| Old implementation name | Current worker positioning | Notes |
| --- | --- | --- |
| Android Bridge | Android Device Worker | Android phone/tablet controlled through queued mobile commands; capabilities include `android.*`, `adb.*`, `pdd.*`, artifact/image operations. |
| OpenClaw | Desktop Device Worker | Physical/software device worker behind `OpenClawExecutor`; supports in-memory/local JSON simulation and configurable HTTP controller transport, but still requires real hardware validation for complex moves. |
| Remote Worker | Generic Remote Worker / Remote Runtime Worker | Remote execution node advertising heartbeat targets and capabilities; scheduler should route by capability, not location. |
| Browser Automation | Browser Worker | Browser capability namespace. `BrowserWorkerExecutor` now provides an opt-in JSON-over-stdin process bridge when `SPIRITKIN_BROWSER_WORKER_COMMAND` is configured; remote browser routing still uses `RemoteExecutor`. |
| ADB | Android Worker Capability | Low-level capability namespace under Android Device Worker, not an Agent-level concept. |
| Playwright | Browser Worker Capability | Low-level capability namespace under Browser Worker. |
| Python Runtime | Python/Execution Worker | `PythonWorkerExecutor` is registered by default and can run workspace-contained `.py` scripts through `python.run_script` -> WorkerPool. |
| FFmpeg | Media/Execution Worker | `FFmpegWorkerExecutor` is registered by default for workspace-contained `ffmpeg.probe` / `ffmpeg.transcode`; it fails closed when local FFmpeg binaries are unavailable. |
| Git | Git/Execution Worker | `GitWorkerExecutor` is registered by default for workspace-contained `git.status`, `git.diff`, and governed `git.commit`. |
| Service RAG | Service Worker | `ServiceRAGWorkerExecutor` is registered by default for configured retriever-backed `rag.search` / `knowledge.retrieve`; embedding creation fails closed unless a real provider is configured. |

Important boundary:

```text
Agent
  -> Skill
  -> CapabilityRecord
  -> WorkerPool
  -> WorkerDescriptor
  -> Executor / Remote node / Android queue / service connector
```

`backend/orchestrator/worker_pool.py` now exposes `worker_type`,
`worker_subtype`, `capability_namespaces`, `legacy_names`, and a `taxonomy`
summary in snapshots. `backend/orchestrator/android_worker_registry.py` registers
the Android Bridge as `device_worker/android_device_worker` without changing the
existing Android command path. This keeps the old implementation stable while
making the new capability/responsibility model explicit.

Scheduler status:

- `WorkerPool.schedule()` accepts `needs`, `worker_type`, `worker_subtype`,
  `workspace`, `permission_scope`, and `prefer_remote`.
- Scheduling is capability-based and explainable: the decision returns selected
  worker, candidates, rejected workers, matched/missing needs, score, reasons,
  and penalties.
- Remote node heartbeats from `NodeRegistry` can be converted into
  `WorkerDescriptor` records, so remote PCs/browsers/runtimes enter the same
  WorkerPool taxonomy and scheduling candidate list.
- Workflow Android steps now expose `worker_requirement` and the built-in Android
  lifecycle workflow declares `needs` for Android UI, screenshot, artifact, and
  PDD steps.
- `WorkflowRunner` can now consume `WorkerPool.schedule()` before node dispatch.
  When a node declares `needs` and a WorkerPool is configured, the runner writes
  `worker_schedule` into node outputs; missing workers block the node with an
  explainable schedule decision. When no WorkerPool is configured, the runner
  records `worker_pool_not_configured` without changing legacy execution.
- The default `workflow.graph.run_next` / `workflow.graph.run_node` tools can now
  receive the active AgentCluster WorkerPool, so desktop/API-triggered workflow
  runs use the same schedule decision path. Android workflow-step queueing also
  records `worker_requirement` and `worker_schedule`; with an injected WorkerPool
  it blocks if no paired/ready Android Device Worker is available.
- Android Device Worker descriptors now expose online device metadata. When an
  Android workflow step does not specify a concrete `device_id`, the workflow
  tool resolves the scheduled Android worker to a concrete online device and
  records `device_selection` in node outputs.
- Browser-capable workflow tool calls now record `worker_binding` in node
  outputs and pass it into tool arguments. A local Browser Worker binds to
  `execution_target=browser`; a Remote Runtime Worker with browser capabilities
  binds to `execution_target=remote:<node_id>`.
- Execution tools now consume browser `worker_binding`: local browser requests
  route to `target=browser`; remote browser requests route to
  `target=remote:<node_id>` with `remote_target=browser`, preserving capability
  and risk lookup through the original browser tool record.
- WorkerPool snapshots now expose ready `executor:python_worker`,
  `executor:git_worker`, `executor:ffmpeg_worker`, and
  `executor:service_rag_worker` when the default AgentCluster runtime is used.
  These executors are workspace-governed and keep subprocess or provider
  failures explicit.
- `BrowserWorkerExecutor` is available as a process-backed local Browser Worker.
  It supports `browser.health_check`, `browser_open_url`, and `browser_search`
  over a JSON stdin/stdout protocol. It is not registered as ready unless
  `SPIRITKIN_BROWSER_WORKER_COMMAND` is configured, so missing browser
  automation fails closed instead of appearing as executable capacity.
- WorkerPool snapshots still include non-schedulable planned seeds for
  `python_worker`, `ffmpeg_worker`, `git_worker`, and `service_rag_worker`.
  These appear under `planned_workers` and planned taxonomy fields only; real
  capacity comes from the separate ready executor records.
- `capabilities_from_worker_descriptor()` converts Worker descriptors,
  including planned seeds, into CapabilityGraph records. Planned records are
  marked `planned=true` and `schedulable=false`, so the graph can explain future
  capability coverage without creating executable capacity.
- This is intentionally incremental. Browser automation now has a local process
  Worker bridge and RemoteExecutor binding path, but production Playwright or
  cloud-browser deployments still need environment-specific validation.

## 2.2 Runtime Metadata / Context Kernel Status

The current runtime already exposes metadata hooks in Workflows, Workflow nodes,
Skills, Workers, Worker requirements, and Capability Graph records. The missing
piece was a shared contract for what those fields mean. That contract now lives
in `docs/runtime_metadata_contract.md` and is backed by small runtime modules:

- `backend/orchestrator/runtime_metadata.py`: normalized runtime metadata fields.
- `backend/orchestrator/context_store.py`: append-only Context Kernel seed with
  `/full`, `/task`, and `/worker` views plus a JSONL ledger store for persisted
  Context patches.
- `backend/orchestrator/context_mirror.py`: read-only mirror that turns desktop
  project/session state, collaboration ledgers, and ecommerce queue state into
  ContextStore patches.
- `backend/orchestrator/agent_protocol.py`: structured Agent message envelope,
  route policy, route verdict, in-memory route audit events, and a JSONL route
  bus seed.
- `backend/orchestrator/execution_finalizer.py`: VERIFY -> SCORE -> COMMIT
  finalizer skeleton.
- `backend/orchestrator/workflow_runtime_contracts.py`: Workflow run adapter for
  Context patches and Finalizer input without changing existing run storage.
- `backend/orchestrator/scheduler_task_finalizer.py`: in-memory scheduler task
  adapter that converts `ScheduledTask` terminal states into `ExecutionSummary`
  and writes finalizer verdict snapshots back to the task queue and Context
  ledger.

Implementation status:

- `SkillSpec` now has explicit output schema, cost, latency, success rate,
  required capability, Worker need, side effect, and artifact contract fields.
- `JsonlSkillSpecStore` persists those fields while remaining compatible with
  old Skill records.
- `capability_from_skill()` propagates Skill runtime metadata into
  `CapabilityRecord` and `CapabilityBinding` snapshots.
- Workflow run snapshots can now emit Context Kernel patches and
  `ExecutionSummary` for the Finalizer.
- `JsonWorkflowStore.save_run()` persists runtime Context records in
  `runtime_context.jsonl` and terminal-run Finalizer verdicts in
  `finalizer_verdicts.jsonl` beside the Workflow state files.
- Desktop Workflow run snapshots expose the latest runtime contract record.
- `/desktop/context` now returns `runtime_context` with ContextStore patches for
  active desktop session/project, collaboration summary/tasks/messages, and
  ecommerce queue summary/tasks, alongside the existing context policy snapshot.
- `JsonlContextStore` can persist and reload Context patches in
  `state/context/context_patches.jsonl` by default. This is a reusable ledger
  seed, not a replacement for existing module-owned state files.
- `/desktop/context` exposes the persisted ledger as `context_ledger`.
- `/desktop/context` also returns `write_intent_preview` and
  `write_intents`. The write-intent path validates target path, operation,
  actor, payload, context id, and review requirement, then stores submitted,
  approved, rejected, or applied records in an append-only ledger. The first
  applier is intentionally narrow: approved `/context/policy` intents can update
  ContextPolicy through `save_context_policy()`, and approved
  `/project/overview/proposal` intents create pending Project Overview proposals
  through `propose_project_overview_change()`. Approved
  `/collaboration/message` intents append collaboration messages through
  `post_collaboration_message()`, preserving AgentEnvelope metadata and unread
  semantics without invoking workers. Approved `/collaboration/decision` and
  `/collaboration/review` intents append decision/review ledger records through
  the existing collaboration module. Other target paths remain reviewable but not
  applicable.
- Successful governed applies append `/context/write_intents/applied` patches to
  `JsonlContextStore`, giving the Context Kernel an auditable record of the
  state mutation result without becoming the write owner yet.
- Scheduler `TaskQueue` finalizer verdicts append `/scheduler/tasks/finalizer`
  patches under `task:<task_id>`, so non-Workflow queued tasks now have a
  Context Kernel audit trail in addition to their in-memory task snapshot.
- Terminal Workflow verdicts now sync bound collaboration tasks when run inputs
  include `task_id`, `collaboration_task_id`, or `ledger_task_id`.
- Terminal Workflow verdicts now sync explicitly bound ecommerce queue tasks when
  run inputs include `ecommerce_task_id` / `commerce_task_id` or matching
  metadata keys. Generic `task_id` remains reserved for collaboration task
  ledgers to avoid cross-queue ambiguity.
- Collaboration messages now include `agent_envelope` while keeping legacy
  `from_model` / `to_model` compatibility fields.
- Collaboration CLI and background worker delivery now prefer `agent_envelope`
  for sender, message type, content, context id, and context-pack artifacts,
  with fallback to legacy message fields.
- `InMemoryAgentRouter` now has a deterministic `AgentRoutePolicy` layer.
  `try_send()` returns an allow/block verdict and records route audit events;
  direct worker/executor recipients and unreviewed privileged scopes are blocked
  before a message is added to the bus.
- `JsonlAgentRouteBus` persists the same route decisions under
  `state/agent_route_bus/`: accepted `AgentEnvelope` messages go to
  `messages.jsonl`, while accepted and blocked route audits go to
  `route_audit.jsonl`.
- `/desktop/collaboration` now uses the same route policy when posting
  collaboration messages. Accepted messages expose `route_verdict` and
  `route_audit_event`, then mirror into `JsonlAgentRouteBus` with
  `route_bus_event`; rejected routes are not appended to the collaboration
  ledger or the bus.
- `build_collaboration_snapshot()` exposes `agent_route_bus` status, and the
  Context mirror maps it to `/agent_route_bus/summary` in
  `/desktop/context.runtime_context`.
- `handle_collaboration_action()` supports read-only
  `list_agent_route_bus_messages` queries for recipient/context/task filtered
  route-bus messages and optional route audits. This is not an acknowledgement
  or read-state mutation.
- `JsonlAgentRouteBus` now writes consumer-level acks to `message_acks.jsonl`.
  `ack_agent_route_bus_message` records bus consumption, and
  `list_agent_route_bus_messages` can hide already acked messages with
  `include_acked=false`. Collaboration `read_by` remains separate.
- Route-bus worker diagnostics are appended to `worker_events.jsonl`. These
  records capture idle, processed, failed, or disabled worker states and recent
  errors; they are diagnostic events, not message consumption state.
- `run_agent_route_bus_worker_once` is a dry-run-only one-message worker loop
  over the durable route bus. It can read one unacked message, ack it, and post
  a deterministic test answer; real model/CLI execution is intentionally not
  enabled in this backend action.
- `agent_route_bus_worker_status` exposes the non-consuming worker control
  plane for desktop/debug callers. It reports `dry_run_only` mode,
  supported actions, storage paths, per-Agent pending/ack counts, recent worker
  events, and external assistant command readiness from Agent Management.
  `real_worker_status` is
  `ready` only when an enabled CLI command is discoverable; the status action
  does not invoke the CLI. Collaboration snapshots carry the same payload as
  `agent_route_bus_worker`, and the Context mirror writes the compact summary to
  `/agent_route_bus/worker_status`.
- `scripts/collaboration_agent_worker.py` now defaults to `--transport
  route_bus`, so desktop-started Codex/Claude Code workers consume durable bus
  messages and ack them through `message_acks.jsonl`. `--transport
  legacy_inbox` remains available for the old collaboration inbox/read_by path.
- Real CLI worker execution fails closed unless the selected external assistant
  exists in Agent Management, is enabled, and has a non-empty command. Dry-run
  remains available for route-bus validation without invoking a model.
- Model provider snapshots include normalized runtime metadata for local/cloud
  boundary, cost hint, permission scope, and provider identity.
- Model provider `test_provider` and `sync_provider_models` actions record
  health observations in `state/model_provider_health.jsonl`; action results
  include `duration_ms`, `health_status`, `checked_at`, `model_count`, and
  Context patch refs. The same explicit action snapshot appends
  `/model/providers/health` patches under `model_provider:<provider>:<model>`.
  Runtime metadata surfaces the latest observation as latency and health hints
  without triggering automatic cloud probes.
- `AgentCluster.process_next_queued_task()` now finalizes in-memory scheduler
  tasks on complete/fail/block paths and stores the verdict in
  `ScheduledTask.finalizer`.
- `HybridPlannerPipeline` now attaches `capability_recommendation` to planner
  snapshots. This is an explainable CapabilityGraph ranking for scheduler
  metadata only; it does not change the selected route or dispatch Workers.
- Context Kernel, Agent Protocol, and Finalizer are contract-level modules for
  staged integration; they are not yet the only source of truth for existing
  Workflow or Agent execution.

Next integration steps:

- Add governed appliers for additional Context paths only after ownership,
  rollback, and module-specific state migration rules are ready.
- Extend Finalizer-to-task synchronization from current workflow/collaboration/
  ecommerce/scheduler paths to additional domain queues.
- Extend governed worker execution to additional domain runtimes only when
  execution policy, permissions, and tests are in place.

## 3. Desktop Management Modules

The unified module overview currently tracks seven first-class modules:

| Module | Endpoint | Current role |
| --- | --- | --- |
| Evolution loop | `/desktop/evolution` | Task trajectories, judge scoring, eval/action generation, paper/video artifacts, training package, review gate. |
| Growth Runtime | `/desktop/growth` and `/ios/growth` | Governed candidate pipeline for capability gaps, workflow mining, Skill/Tool/Code/Model growth, one-way parent/child Builder escalation, explicit Human escalation, review, and registry artifacts; never auto-activates. |
| Skills | `/desktop/skills` | Skill registry, candidate review, ownership, promotion/archive, remote export package source. |
| Agent cluster | `/desktop/agent-management` | Managed agents, route profiles, external assistants, remote targets, KB ownership. |
| Knowledge base | `/desktop/knowledge-base` | Per-agent and domain KB directories, import, indexing, file counts. |
| Search/RAG | `/desktop/search-management` | Web search provider, KB backend, embedding, reranker, model capability matrix, missing capability report. |
| Models | `/desktop/model-catalog` and `/desktop/learning` | Base model catalog, provider settings, reviewer/assist model configuration. |
| Module governance | `/desktop/ecosystem-review` | Ecosystem score, module governance, pending proposals, structured proposal triage, risk queue. |
| Resource Registry | `/desktop/resource-registry` | Long-lived resource metadata for shops, accounts, browser profiles, devices, repositories, KBs, and media libraries. |

## 4. Agent Cluster

Enabled agents:

| Agent | Domain | Role | Model/provider | KB |
| --- | --- | --- | --- | --- |
| `main_text` | general | primary | OpenAI-compatible `qwen/qwen3.6-35b-a3b` | `kb_main_text` |
| `programming` | programming | specialist | OpenAI-compatible `qwen/qwen3.6-35b-a3b` | `kb_programming` |
| `vision_model` | vision | specialist | OpenAI-compatible `qwen3-vl:4b` | `kb_vision_model` |
| `video_animation` | video_animation | specialist | OpenAI-compatible `qwen/qwen3.6-35b-a3b` | `kb_video_animation` |
| `game_development` | game_development | specialist | OpenAI-compatible `qwen/qwen3.6-35b-a3b` | `kb_game_development` |
| `ecommerce` | ecommerce | specialist | OpenAI-compatible `qwen/qwen3.6-35b-a3b` | `kb_ecommerce` |
| `skill_runner` | skill | executor | deterministic `SkillRunner` | `kb_skill_runner` |

Current specialist depth:

- `programming` now receives a governed read-only code workspace view before
  prompting: `AgentCluster` collects `git.status` and optional `git.diff`
  through WorkerPool/GitWorker and attaches `code_workspace_context` metadata.
  This makes the coding Agent more than a prompt persona for repository-aware
  diagnosis while keeping edits/commits behind normal tools and review gates.
- `vision_model` can use explicit screen actions and opt-in perception context.
- `ecommerce`, `game_development`, and `video_animation` still need deeper
  domain tools, durable account/store/media Resource records, KB material, and
  tested Workflows before they should be called production-specialized Agents.

Disabled but tracked:

- `external_reviewer`: review domain, intended for cloud/API/CLI reviewer integration.

Route profiles:

- `default_hybrid`: local primary text plus vision and optional reviewer.
- `cloud_review_gate`: local execution plus reviewer-only second pass.
- `cloud_fallback_chain`: local-first fallback chain.

## 5. Search/RAG and Knowledge

Current configuration:

| Capability | Current value |
| --- | --- |
| Runtime KB backend | `embedding` |
| Web search config | `brave,duckduckgo`; runtime can fall back when keyed provider is unavailable |
| Embedding provider | llama.cpp OpenAI-compatible |
| Embedding model | `text-embedding-nomic-embed-text-v1.5` |
| Embedding endpoint | `http://127.0.0.1:8081/v1` |
| Reranker provider | llama.cpp OpenAI-compatible chat |
| Reranker model | `qwen/qwen3.6-35b-a3b` |
| Reranker endpoint | `http://127.0.0.1:8080/v1` |

Implementation notes:

- Hashing/token-overlap retrieval remains as fallback.
- OpenAI-compatible embeddings are configurable through `config/config.yaml` and `/desktop/search-management`.
- LLM reranker returns ordered ids and falls back to token overlap when the provider fails.
- `JsonVectorStore` persists small/local embedding indexes when a vector store path is configured.
- The next stronger retrieval step is production-scale vector storage plus hybrid lexical/vector ranking.

## 6. Model Configuration

Configured local reviewer/assist model:

| Model id | Provider | Model | Endpoint | Role |
| --- | --- | --- | --- | --- |
| `lmstudio_qwen35b` (legacy stable id) | `llamacpp` | `qwen/qwen3.6-35b-a3b` | `http://127.0.0.1:8080/v1` | local reasoning reviewer |

Primary text model configuration:

- Provider: OpenAI-compatible local endpoint
- Model: `qwen/qwen3.6-35b-a3b`
- Base URL: `http://127.0.0.1:8080/v1`

Vision model configuration:

- Provider: OpenAI-compatible local/Ollama endpoint
- Model: `qwen3-vl:4b`
- Base URL: `http://localhost:11434/v1`

Cloud model APIs remain optional and are not required for the local Search/RAG and reviewer path.

## 7. Skills and Evolution

Seeded domain Skill candidates:

| Skill | Owner | Domain |
| --- | --- | --- |
| `evolution.code_generation.workflow` | `programming` | code generation |
| `evolution.automation_decision.workflow` | `main_text` | automation decision |
| `evolution.video_generation.workflow` | `video_animation` | video generation |
| `evolution.image_generation.workflow` | `vision_model` | image generation |
| `evolution.music_generation.workflow` | `video_animation` | music generation |
| `evolution.ecommerce_operations.workflow` | `ecommerce` | ecommerce operations |

These are candidate templates, not active production Skills. They should be reviewed, tested, and promoted one by one.

Evolution loop current data state:

- Task trajectories: AgentCluster now writes real execution/failure trajectories
  to `state/evolution/trajectories.jsonl` by default through
  `backend/orchestrator/runtime_trajectory_log.py`. SkillRunner dry-runs and
  executions also append `skill_runner.run` trajectories. WorkflowRunner
  tool/skill nodes append `workflow_runner.node` trajectories for both success
  and failure outcomes. Collaboration route-bus terminal worker events append
  `collaboration.worker_event` trajectories; stream token events are kept in
  the work-event log but are not added to the evolution dataset. Android
  command results append `android.command_result` trajectories, and RemoteWorker
  execute/package/rollback results append `remote.worker_result` trajectories.
- Failure samples: in-process `FailureLog` still records structured failures and
  now mirrors failure trajectories into the same durable trajectory log.
- Eval cases: `/desktop/evolution` derives eval cases from failed trajectories
  through `TrajectoryAnalyzer.generate_eval_cases()` and can export them to
  `state/evolution/eval_cases.jsonl` through `export_eval_cases`.
- Paper/video learning artifacts: empty
- Cloud/local training package: `export_self_training_dataset` can export a
  chat JSONL dataset from those trajectory-derived eval cases and register it in
  the dataset registry with a linked eval report path; package creation still
  stays behind the review gate.
- Core review gate: enabled
- Auto code apply: disabled
- Auto skill promotion: disabled

## 8. Governance and Safety

Governance is manual by design:

- Module maturity and risks are exposed through `/desktop/ecosystem-review`.
- Pending proposals include structured triage (`proposal_triage`) that separates
  low-risk apply-after-review items, manual/high-risk work that should become
  tracked tasks, stale noise, and done/rejected records.
- Critical/high modules need owner role, verification commands, tests or documented manual validation.
- Skills, model changes, training packages, remote exports, and risky execution should stay behind review gates.

## 9. Current Gaps

Priority gaps to close next:

1. Keep command gateway, Android/iOS endpoint, and remote worker behind token
   auth before LAN/public exposure. CORS is now allowlist/loopback based; `*`
   is only available through explicit development override.
2. Expand perception from the current explicit opt-in context path into
   production-grade sensing. `AgentCluster` can now request read-only screen
   perception through WorkerPool and inject `perception_context` into the
   planning/Agent prompt, but streaming ASR/VAD still fails closed unless a real
   backend or explicit heuristic development fallback is configured.
3. Replace hashing embedding defaults with real embedding models. Hashing
   embeddings now require explicit development opt-in and should not be used as
   semantic retrieval evidence.
4. Review and promote the six seeded candidate Skills into usable, tested Skills.
5. Validate trajectory capture on real Android/remote devices and extend it to
   any production-specific worker event types that are not yet exercised by
   unit tests.
6. Import more domain-specific material into each per-agent knowledge base and keep indexes current.
7. Move beyond the lightweight JSON vector store when KB volume grows.
   `JsonVectorStore` now persists small/local embedding indexes and
   `build_embedding_retriever_from_directory(..., vector_store_path=...)` can
   reuse it; production-scale vector DB or hybrid retrieval is still pending
   for larger knowledge bases.
8. Work through the governance proposal triage queue: approve low-risk items,
   convert manual/high-risk work into tracked tasks, and archive stale noise.
9. Configure external/cloud reviewer APIs only if local llama.cpp review is not enough for long-context or high-risk review.
10. Validate mobile/remote worker/OpenClaw/device paths on real hardware before treating them as production-ready.
11. Validate production browser automation against the actual local/remote
   Playwright or browser-worker deployment. The local process bridge,
   `ExecutionRequest` binding, RemoteExecutor route, and unit-tested WorkerPool
   scheduling path are in place; real browser profiles, credentials, and
   site-specific automation still need environment validation.
12. Populate Resource Registry with reviewed real assets: stores, accounts,
   browser profiles, knowledge bases, and media libraries. `ResourceRegistry`
   now has a JSON persistence store, `/desktop/resource-registry` metadata CRUD,
   and `AgentCluster` can load it via `resource_registry_path` /
   `SPIRITKIN_RESOURCE_REGISTRY_PATH`, then merge Worker descriptors, local
   device/workspace records, and active ecommerce projects into the runtime
   `resource_registry` snapshot. Production onboarding UI, credential binding
   policy, and real account inventory are still pending.

## 10. Verification Commands

Use these after changing management APIs, Search/RAG, Skills, KB, or desktop integration:

```powershell
python -m py_compile backend\app\settings.py backend\app\search_management.py backend\knowledge\embedding.py backend\knowledge\reranker.py backend\knowledge\embedding_retriever.py
python -m unittest backend.tests.unit.test_settings backend.tests.unit.test_tooling_and_remote backend.tests.unit.test_command_gateway backend.tests.unit.test_knowledge_base_management -v
dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj --no-restore -p:UseAppHost=false
```

For broader release confidence:

```powershell
python -m unittest discover backend.tests.unit -v
python scripts/validate_desktop_delivery.py
```
