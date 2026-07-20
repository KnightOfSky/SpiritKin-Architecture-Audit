# SpiritKinAI Project Management Overview

Last updated: 2026-06-29

## Purpose

This is the shared project brief for human operators, Codex, Claude Opus, the main Agent, and other external model reviewers. Use this document as the primary collaboration context before proposing architecture, UI, runtime, model, Skill, workflow, or avatar changes.

The desktop Management -> Project Overview tab reads and proposes edits to this file through `/desktop/project-overview`. Edits from the desktop UI should become review proposals with diffs before replacing the document.

## Current Collaboration Status

- This file is the main source of truth for cross-model project coordination.
- `docs/ai_collaboration_context.md` is a compact supplementary handoff for external models; it should point back to this file rather than replace it.
- Claude Opus is currently working on desktop UI changes and refactoring. Codex should avoid broad WPF UI refactors or conflicting layout rewrites unless explicitly assigned.
- Codex should focus on backend/runtime integration, verification, documentation, narrow bug fixes, and making UI-facing contracts clearer while Opus owns active UI restructuring.
- Collaboration should become a separate Management page. Project Overview should keep the primary source document and a small collaboration summary/link, while the Collaboration page owns task ledgers, file claims, context packs, decisions, and reviews.
- Before either model changes shared areas, read this file plus the relevant linked documents and check the current worktree for unrelated user or model changes.

## Handoff Center

Read these documents before adding new Agent, model, training, workflow, desktop, avatar, or automation work:

- `docs/current_architecture_snapshot.md`: current runtime, backend, desktop modules, Agent, Search/RAG, Skill, model, and governance map.
- `docs/project_dictionary.md`: canonical project terms, aliases, ID scopes, status vocabulary, and module ownership.
- `docs/agent_cluster_optimal_plan.md`: unified safety kernel plus mixed model/framework Agent cluster plan.
- `docs/mainwindow_carve_plan.md`: in-progress WPF MainWindow carve plan (supersedes the archived desktop enterprise architecture plan).
- `docs/avatar_3d_animatable_model_pipeline.md`: 3D avatar asset and motion pipeline constraints.
- `docs/model_distribution_plan.md`: model/provider distribution and route profile policy.
- `docs/runtime_metadata_contract.md`: Runtime Metadata, Context Kernel, Agent Protocol, Execution Finalizer, Scheduler, and Capability Graph migration contract.
- `docs/archive/codex_handoff.md`: detailed historical engineering handoff and prior architecture notes (archived; superseded by `docs/ai_collaboration_context.md`).
- `docs/ai_collaboration_context.md`: compact external-model briefing that should stay aligned with this overview.
- `docs/landing_and_test_handoff.md`: landing status, usability boundaries, API entry points, cloud training flow, and verification commands.
- `docs/development_constitution.md`: mandatory cross-client performance, safety, growth, and verification rules.

## Runtime Entry Points

| Surface | Entry | Role |
| --- | --- | --- |
| Native desktop | `desktop/SpiritKinDesktop/SpiritKinDesktop.csproj` | Main WPF operator console for chat, projects, tasks, services, diagnostics, learning, models, agents, skills, KB, Search/RAG, governance, workflows, and the embedded 3D avatar. |
| Web desktop console | `frontend/desktop_console.html` on `http://127.0.0.1:8787` | Browser-based management console over the same `/desktop/*` APIs and realtime bridge. |
| 3D avatar | `frontend/avatar_3d.html?config=models/spirit3d/manifest.json` | Three.js avatar surface used by external browser and desktop WebView2. |
| Command gateway | `backend/app/command_gateway.py` on `http://127.0.0.1:8788` | HTTP management and command API, including `/desktop/*` endpoints. |
| Event bridge | `ws://127.0.0.1:8765` | Realtime runtime events for assistant messages, state, avatar, speech, services, and UI sync. |
| Voice/runtime loop | `backend/main.py` -> `backend/app/runtime.py` | Local voice/hotword/runtime assembly path. |
| Optional remote worker | `http://127.0.0.1:8790` | Future/optional remote execution node. |

Default local service ports:

- Frontend/static server: `8787`
- Realtime WebSocket event bridge: `8765`
- Command gateway: `8788`
- Optional remote worker: `8790`

## Architecture Principles

SpiritKin should remain a governed runtime, not a direct-action chatbot. The execution chain must stay:

```text
Agent / Skill / external assistant
  -> ToolRegistry
  -> permission / review / confirmation gate
  -> Executor / Worker
  -> Device / connector / remote worker
```

Rules:

- External models and CLI assistants may review, propose, or synthesize plans by default.
- High-risk actions must not bypass permission, confirmation, audit, review, or safety-stop gates.
- Models are replaceable brains. Skills, Workflows, Capability Graph, Knowledge, Policy, Evals, and Worker scheduling should remain model-independent.
- Use structured contracts, snapshots, DTOs, and explicit state files instead of prompt-only implicit behavior.
- Treat metadata as a runtime contract, not notes. Workflow, Node, Skill, Worker, Agent, Model Provider, and Execution metadata should be schedulable, auditable, and compatible with `docs/runtime_metadata_contract.md`.
- Keep web and WPF behavior aligned when both call the same backend APIs.
- When a browser feature affects desktop WebView behavior, verify the desktop service path too.

## AI OS Runtime Direction

The landing architecture is:

```text
Intent
  -> Context Kernel
  -> Master Scheduler
  -> Agent Protocol
  -> Workflow Graph Runtime
  -> Skill
  -> Worker Pool
  -> Execution Finalizer
  -> Evolution / Promotion
```

Current implementation should move toward this shape with additive adapters, not broad rewrites:

- Context Kernel starts as an append-only mirror of task, workflow, Agent, Worker, artifact, and execution records.
- Context mirror now exposes active desktop project/session, collaboration ledger, and ecommerce queue summaries through `/desktop/context.runtime_context`.
- ContextStore now also has a JSONL append-only ledger seed at `state/context/context_patches.jsonl` for persisted `ContextPatch` records. `/desktop/context` exposes it as `context_ledger`. It is additive and does not replace the existing desktop, collaboration, workflow, or ecommerce state owners.
- Resource Registry now has a JSON persistence store plus the thin runtime
  asset view in `AgentCluster`. `/desktop/resource-registry` can create, list,
  and delete long-lived resource metadata records without storing credential
  secrets. `resource_registry_snapshot` and `capability_inventory.resource_registry`
  include persisted resources, Worker-derived resources, the local desktop
  device, current workspace repository, and active ecommerce projects. It is
  still not a credential vault or reviewed onboarding UI.
- Agent Protocol turns Codex, Claude Code, local Agents, and reviewers into structured message participants.
- Workflow remains an AI Blueprint: reusable Workflow, Function, Macro, Event, and nested Workflow nodes.
- Workflow runs can now be adapted into Context patches and Finalizer input without changing legacy Workflow storage; the Workflow store persists runtime context and terminal-run verdict sidecars.
- Bound Workflow runs can update collaboration task status from Finalizer verdicts: commit -> complete, review -> review, wait -> waiting, retry -> blocked.
- Explicitly bound ecommerce Workflow runs can update ecommerce queue task status from Finalizer verdicts via `ecommerce_task_id` / `commerce_task_id`, while generic `task_id` remains scoped to collaboration task ledgers.
- In-memory scheduler tasks now run through `backend/orchestrator/scheduler_task_finalizer.py` after complete/fail/block paths. `ScheduledTask.snapshot()` includes a `finalizer` block with decision, next status, score, verified flag, reasons, source, update time, and the Context patch refs. Each verdict also appends a `/scheduler/tasks/finalizer` patch to the Context ledger under `task:<task_id>`.
- `/desktop/context` now includes `write_intent_preview` plus an append-only write-intent ledger. It validates context id, target path, operation, actor, payload, and review requirement, and supports submit/approve/reject/list actions. Governed appliers are intentionally narrow: approved `/context/policy` intents can update ContextPolicy through `save_context_policy()`, approved `/project/overview/proposal` intents create pending Project Overview proposals through the existing diff/review ledger without overwriting the document, and approved `/collaboration/message`, `/collaboration/decision`, and `/collaboration/review` intents append collaboration ledger records without starting workers or executing commands. Successful applies append `/context/write_intents/applied` patches into the Context ledger.
- Model provider `test_provider` and `sync_provider_models` actions now append runtime health observations to `state/model_provider_health.jsonl` and return duration, health status, checked time, observed model count, and Context patch refs. Provider runtime metadata reads the latest observation without running automatic cloud checks. The same explicit action snapshot also appends `/model/providers/health` patches to the Context ledger.
- Collaboration messages now carry structured `AgentEnvelope` data, and mailbox/worker scripts prefer the envelope for sender, type, context, content, and context-pack artifacts with legacy fallback.
- `/desktop/collaboration` message writes now pass through `AgentRoutePolicy` before persistence. Accepted message snapshots include `route_verdict` and `route_audit_event`; direct worker/executor recipients and unreviewed privileged scopes are rejected.
- `JsonlAgentRouteBus` adds a durable Agent route-bus seed under `state/agent_route_bus/`, writing all route audits to `route_audit.jsonl` and accepted `AgentEnvelope` messages to `messages.jsonl`. Accepted collaboration messages are mirrored into this bus, while the collaboration UI ledger remains the desktop read/write source.
- Collaboration snapshots now include `agent_route_bus` status, and `/desktop/context.runtime_context` mirrors it at `/agent_route_bus/summary` for debugging and future UI display.
- Collaboration actions now include read-only `list_agent_route_bus_messages`, filtering by recipient/Agent, context/thread, and task. It is for debug and future worker inbox reads; it does not mark messages consumed.
- Agent route bus consumption now uses `message_acks.jsonl` through `ack_agent_route_bus_message`. This ack is worker/Agent-consumption state only and does not mutate collaboration UI read state.
- `run_agent_route_bus_worker_once` provides a dry-run-only one-message worker loop over the durable route bus: read one unacked message, optionally ack it, and optionally post a deterministic dry-run answer. It does not call real models or external commands.
- Route-bus worker diagnostics append to `state/agent_route_bus/worker_events.jsonl` for idle, processed, failed, or disabled worker states. This is diagnostic only; consumption remains `message_acks.jsonl`.
- `agent_route_bus_worker_status` exposes the dry-run worker control-plane state without consuming messages: default tracked Agents are `codex` and `claude_code`, each with pending/ack counts, latest pending message, latest worker event, and external assistant readiness. Collaboration snapshots include this as `agent_route_bus_worker`, and `/desktop/context.runtime_context` mirrors the compact form at `/agent_route_bus/worker_status`.
- Skill remains the function-level capability layer, with input/output schemas, cost, latency, success rate, required capabilities, Worker needs, side effects, and artifact contracts.
- Worker is the execution node abstraction. Android Bridge, OpenClaw, Desktop, Browser, Remote, Python, FFmpeg, Git, and service connectors should converge behind Worker descriptors and requirements.
- Execution Finalizer owns VERIFY -> SCORE -> COMMIT so completed tasks become auditable committed results, retries, reviews, or waits.
- Capability Graph should become the queryable map from intent to Workflow, Skill, Worker, model, policy, knowledge, and artifact requirements.
- Capability Graph now has a read-only `recommend()` candidate-selection seed. It ranks capability candidates by query, domain, required capabilities, Worker needs, and schedulability, but does not dispatch Workers. Planned worker capabilities are hidden by default and only appear as non-schedulable candidates when explicitly requested.
- Growth Runtime now lives under `backend/capability/growth/` as the governed Capability-owned implementation layer. `/desktop/growth` and `/ios/growth` expose the same candidate-only pipeline for gap analysis, Workflow mining, Skill/Tool/Code/Model growth, one-way parent/child Builder escalation, explicit Human escalation, review, and registry artifacts. It never installs tools, applies generated code, or activates a candidate automatically. The full contract is `docs/growth_runtime.md`.
- Hybrid planner snapshots and AgentCluster scheduler metadata now include `capability_recommendation`, making capability choice explainable without changing the deterministic route.

## Backend Architecture

| Layer | Main modules | Responsibility |
| --- | --- | --- |
| App/control plane | `backend/app/` | Runtime assembly, desktop APIs, services, model/learning management, module status, governance, Search/RAG management. |
| Orchestration | `backend/orchestrator/` | `AgentCluster`, planner, session manager, route decisions, context, worker scheduling, tool/agent dispatch. |
| Agents | `backend/agents/` plus managed Agent config | Native specialist agents for general text, programming, vision, video/animation, game development, ecommerce, and Skill execution. |
| Tools | `backend/tools/` | Tool specs and registry. Tools are semantic capabilities, not raw device drivers. |
| Execution | `backend/executors/` | Converts approved tool actions into local, Android, OpenClaw, remote, browser, or future connector work. |
| Devices/actions | `backend/devices/`, `backend/action/` | Device adapters and high-level action semantics. |
| Knowledge/RAG | `backend/knowledge/` | Document ingest, chunking, stores, retrieval, embedding retriever, reranker, indexer, connectors. |
| Memory | `backend/memory/` | Short-term history, summaries, longer-lived memory/personality state hooks. |
| Perception/expression | `backend/perception/`, `backend/expression/` | ASR, wakeword, opt-in screen perception context, vision analysis, TTS, speech events, avatar state. |
| Security/governance | `backend/app/review_gate.py`, `backend/app/module_governance.py`, audit/permission modules | Confirmation gates, review policies, module maturity, audit records, proposal queues. |
| Training/eval | `backend/model/training/`, `backend/evaluation/`, evolution management | Learning records, failure trajectories, eval cases, self-training package generation. |

Important desktop endpoints:

- `/desktop/state`
- `/desktop/context`
- `/desktop/project-overview`
- `/desktop/collaboration`
- `/desktop/services`
- `/desktop/diagnostics`
- `/desktop/logs`
- `/desktop/learning`
- `/desktop/workflows`
- `/desktop/skills`
- `/desktop/agent-management`
- `/desktop/knowledge-base`
- `/desktop/search-management`
- `/desktop/module-management`
- `/desktop/ecosystem-review`
- `/desktop/model-catalog`

## Worker And Capability Runtime

The execution layer is evolving from direct executor/bridge calls into a capability-scheduled worker model:

```text
Agent
  -> Skill
  -> CapabilityRecord
  -> WorkerPool
  -> WorkerDescriptor
  -> Executor / Remote node / Android queue / service connector
```

Current taxonomy:

- Android Bridge -> Android Device Worker
- OpenClaw -> Desktop Device Worker, with in-memory/local JSON simulation and optional HTTP controller transport configured through `SPIRITKIN_OPENCLAW_HTTP_BASE_URL`; real hardware validation is still required before production use.
- Remote Worker -> Generic Remote Worker / Remote Runtime Worker
- Browser Automation -> Browser Worker, including an opt-in process-backed
  `BrowserWorkerExecutor` configured by `SPIRITKIN_BROWSER_WORKER_COMMAND`.
- ADB -> Android Worker capability namespace
- Playwright -> Browser Worker capability namespace
- Python Runtime -> ready local `PythonWorkerExecutor` for workspace-contained `.py` scripts, exposed through `python.run_script` and WorkerPool scheduling.
- Git Runtime -> ready local `GitWorkerExecutor` for workspace-contained `git.status`, `git.diff`, and governed `git.commit`.
- FFmpeg Runtime -> ready local `FFmpegWorkerExecutor` for workspace-contained `ffmpeg.probe` / `ffmpeg.transcode`, fail-closed when FFmpeg binaries are missing.
- Service RAG -> ready local `ServiceRAGWorkerExecutor` for configured retriever-backed `rag.search` / `knowledge.retrieve`; embedding creation requires a real configured provider.

Current scheduling behavior:

- `WorkerPool.schedule()` supports capability needs, worker type/subtype, workspace, permission scope, and remote preference.
- Workflow nodes can record `worker_requirement`, `worker_schedule`, `worker_binding`, and concrete device selection.
- Browser-capable workflow tool calls can bind local browser execution or remote
  browser execution. Local process-backed Browser Worker execution is available
  through `browser.worker_health`, `browser.worker_open_url`, and
  `browser.worker_search`; it fails closed unless the browser worker command is
  configured.
- Planned seeds still exist for `python_worker`, `ffmpeg_worker`, `git_worker`, and `service_rag_worker` with `maturity=planned` and `schedulable=false`. They document the target taxonomy and feed planned CapabilityGraph records. Real capacity comes from the separate ready executor records; the seeds themselves remain non-executable.
- `CapabilityRegistry.recommend()` can produce explainable candidate rankings for Master/Scheduler planning while preserving the rule that planned workers are not executable capacity.
- Capability recommendations now include per-requirement `worker_evidence`, marking required workers as `ready`, `planned`, or `missing` from current worker descriptors. This is read-only planning evidence, not Worker dispatch.
- `HybridPlannerPipeline` exposes the recommendation under scheduler metadata, so future Master selection can consume the same structure.
- The remaining execution-runtime gap is production browser deployment
  validation. The local Browser worker process protocol and WorkerPool routing
  are implemented and unit-tested, but the real Playwright/browser profile
  process still needs environment-specific smoke tests before production use.
- Runtime metadata is now the common language for this layer. `WorkerDescriptor`, `WorkerRequirement`, `WorkflowNodeDefinition`, `SkillSpec`, and `CapabilityRecord` should carry normalized scheduling and audit fields instead of hiding routing decisions in prompts or UI text.

## Desktop Application State

The WPF desktop is the primary operator surface. It currently includes:

- Chat/session/project/task workbench.
- Embedded WebView2 avatar panel for `frontend/avatar_3d.html`.
- Context policy and pinned-context management.
- Project Overview shared document editing and review proposals.
- Collaboration control plane for cross-model tasks, file ownership, decisions, reviews, and context packs.
- Services, diagnostics, logs, sync, daily summaries.
- Learning, model provider settings, self-training export.
- Workflows with graph editing, node details, run controls, version/audit/governance state.
- Skills, Agent Cluster, knowledge bases, Search/RAG, MCP, mobile, safety, evolution, and governance panels.
- Ecosystem governance proposals now expose a read-only triage view so operators can distinguish low-risk apply-after-review items, manual/high-risk task work, stale noise, and completed/rejected records.
- Integrated terminal and global search overlay.

Current WPF structure:

- `MainWindow.xaml` should remain a thin shell: window chrome, root rows, split columns, and top-level control hosts.
- Shared styles and non-interactive templates live in `Resources/MainWindowResources.xaml` and `Resources/MainWindowDataTemplates.xaml`.
- Interactive templates that forward to existing `MainWindow` handlers live in `Resources/MainWindowInteractionTemplates.xaml`.
- Top-level controls include `Controls/WorkspaceSidebarView.xaml`, `Controls/ChatWorkspaceView.xaml`, `Controls/WorkbenchShellView.xaml`, and `Controls/ManagementPanelsView.xaml`.
- Feature code is increasingly split under `desktop/SpiritKinDesktop/Features/*`.

Recent WPF stability note:

- `App.xaml` now loads shared WPF resources at application level so split `UserControl` XAML can resolve resources such as `LineBrush`, templates, and shared styles during startup.
- `MainWindow.xaml` no longer owns those shared dictionaries directly.
- `dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj --no-restore` passed after this startup fix.
- `/desktop/module-management` GET uses a lightweight cached aggregate snapshot for desktop responsiveness. It disables live mobile probes only within the current request thread and reads the saved ecosystem-review state instead of doing a full repository scan. Use the Module Management `扫描` action or `/desktop/ecosystem-review` when a full governance scan is needed.

Opus UI/refactor boundary:

- Opus is currently handling UI changes and refactoring.
- Avoid broad edits to `MainWindow.xaml`, `ManagementPanelsView.xaml`, `WorkbenchShellView.xaml`, `ChatWorkspaceView.xaml`, shared styles, and feature UI layout files unless coordination explicitly assigns the change.
- Backend contract changes that affect UI should be documented here and kept backward compatible where possible.

## Collaboration Control Plane

The cross-model collaboration layer is separate from Project Overview:

- Project Overview: source-of-truth markdown, architecture state, and high-level collaboration summary.
- Collaboration page: operational task ledger, model ownership, file claims, context packs, decisions, and reviews.

Backend/API:

- Module: `backend/app/collaboration.py`
- Endpoint: `/desktop/collaboration`
- Schema: `spiritkin.collaboration.v1`
- Module management id: `collaboration`

State files:

```text
state/collaboration/tasks.jsonl
state/collaboration/messages.jsonl
state/collaboration/decisions.jsonl
state/collaboration/reviews.jsonl
state/collaboration/file_claims.json
state/collaboration/context_packs/
```

Supported actions:

- `create_task`: records task id, title, owner Agent/collaboration endpoint, scope, allowed files, blocked files, and verification commands.
- `update_task`: updates task status or metadata by task id.
- `post_message`: records Agent-to-Agent dialogue for a task/thread, including `thread_id`, `from_agent`, `to_agents`, `role`, `content`, and optional `context_pack_path`. Legacy `from_model` / `to_model` fields remain for compatibility.
- `request_model_review`: records a `review_request` message for another Agent or CLI assistant; a model such as Opus should be represented by an Agent wrapper such as `external_reviewer`.
- `mark_message_read`: appends read status for a message while keeping the message ledger append-only.
- `list_messages`: filters messages by task/thread/Agent for CLI or desktop consumers.
- `claim_files`: records active file ownership patterns to reduce conflicts between Codex, Claude Code, and other Agent endpoints.
- `record_decision`: records architecture/product/process decisions.
- `record_review`: records review verdicts and evidence from reviewer models.
- `build_context_pack`: writes a JSON context pack containing this overview, task metadata, active claims, recent decisions/reviews, and selected file previews.

CLI bridge for Codex / Claude Code:

```powershell
python scripts/collaboration_mailbox.py send --from-agent codex --to-agent claude_code --thread-id ui-refactor --content "question"
python scripts/collaboration_mailbox.py inbox --agent claude_code --unread
python scripts/collaboration_mailbox.py reply --message-id message-id --from-agent claude_code --content "answer"
python scripts/collaboration_mailbox.py read --message-id message-id --reader claude_code
python scripts/collaboration_mailbox.py watch --agent claude_code
python scripts/collaboration_mailbox.py status --agent claude_code --agent codex

# Legacy compatibility: use the older collaboration inbox/read_by ledger.
python scripts/collaboration_mailbox.py inbox --agent claude_code --transport legacy_inbox --unread
```

Agent worker bridge:

```powershell
# Dry run: proves the loop without invoking the real CLI.
python scripts/collaboration_agent_worker.py --agent claude_code --once --dry-run

# Real worker: reads the durable route bus, sends prompt to the configured external assistant command, replies to the same thread.
python scripts/collaboration_agent_worker.py --agent claude_code --assistant-id claude_code

# Legacy compatibility: read the old collaboration inbox/read_by ledger instead of the route bus.
python scripts/collaboration_agent_worker.py --agent claude_code --transport legacy_inbox --once --dry-run
```

Desktop one-click worker control:

- Management -> `协作` -> `模型协作状态` now exposes a background worker selector, optional `thread_id` filter, `dry-run` checkbox, `启动 worker`, and `停止`.
- `全部协作 Agent` starts one worker for `claude_code` and one for `codex`.
- `Claude Code` maps to the `claude_code` external assistant; `Codex` maps to `codex_cli` through `scripts/collaboration_agent_worker.py`.
- Worker stdout/stderr logs are written to `state/logs/collaboration_worker_<agent>.out.log` and `.err.log`.
- Real model smoke on 2026-06-29 succeeded in thread `real-model-smoke-20260629`: Claude Code replied `CLAUDE_REAL_MODEL_OK`, then Codex replied `CODEX_REAL_MODEL_OK`.
- PowerShell `codex` can hit a stale npm shim path, but `cmd /c codex` works on this machine. The worker path uses shell execution and the smoke passed. Codex may emit non-blocking plugin/auth warnings on stderr.
- Desktop shutdown stops any worker processes it launched.

Worker behavior:

- Default transport is `route_bus`: reads `JsonlAgentRouteBus` through `list_agent_route_bus_messages` and consumes with `ack_agent_route_bus_message`.
- `scripts/collaboration_mailbox.py inbox/read/watch` also default to `route_bus`, so manual inspection and the worker see the same per-Agent consumption ledger. Use `--transport legacy_inbox` only when debugging the older collaboration `read_by` path.
- `scripts/collaboration_mailbox.py status` is a non-consuming diagnostic for pending route-bus messages, ack counts, and external assistant readiness. It does not start a worker or invoke a model.
- `--transport legacy_inbox` preserves the old `/desktop/collaboration` `to_agent` inbox/read_by behavior for compatibility.
- `run_agent_route_bus_worker_once` still provides a dry-run-only backend worker loop for tests and diagnostics.
- `agent_route_bus_worker_status` is the non-consuming status endpoint. It reports per-Agent pending/ack counts, latest worker events, and external assistant command readiness from `state/desktop_console/agent_management.json`; it does not invoke the CLI.
- Uses `thread_id` to keep multiple collaboration conversations separated.
- Invokes the external assistant command from `state/desktop_console/agent_management.json`.
- Real CLI execution requires the selected external assistant to exist, be `enabled=true`, and have a non-empty command. Missing or disabled assistant config fails closed without posting a reply or acking the route-bus message; dry-run still works without invoking a CLI.
- Posts an `answer` message back to the sender and acks the route-bus message for that Agent only. Legacy mode marks the original collaboration message read for that Agent.
- Multiple recipients remain independent because route-bus ack is consumer scoped.
- Smoke test on 2026-06-28 created thread `smoke-collab-20260628`: Codex posted a question, the `claude_code` dry-run worker posted an answer, and both messages were marked read. Snapshot after the test showed `messages=0/2`.
- Real CLI test on 2026-06-28 created thread `real-dialogue-20260628`: Codex -> Claude Code produced `CLAUDE_DIALOGUE_OK`; Claude Code -> Codex in the same thread produced `CODEX_DIALOGUE_OK`; all messages were marked read and the snapshot showed `messages=0/11`.
- Worker config now uses non-interactive commands: `claude -p --output-format text` for `claude_code`, and `codex exec --skip-git-repo-check --color never -` for `codex_cli`. The worker forces UTF-8 subprocess IO so Chinese collaboration prompts reach Codex correctly.

Use `--api http://127.0.0.1:8788` or `SPIRITKIN_DESKTOP_API` when the desktop command gateway is not on the default port.

Current UI placement decision:

- Desktop Management now has a separate `协作` page wired to `/desktop/collaboration`.
- Keep Project Overview focused on markdown proposal/review and a compact collaboration status panel.
- Codex should keep the backend contract stable and document any schema changes here; UI refactors should preserve the page's task ledger, Agent dialogue, file claims, context packs, decisions, and reviews.

## Desktop Refactor Direction

Target architecture (original diagnosis archived at `docs/archive/desktop_enterprise_architecture_plan.md`; current execution follows `docs/mainwindow_carve_plan.md`):

```text
WPF Shell
  -> Feature Modules
  -> Desktop App Services
  -> Backend Control Plane
  -> Runtime Plane
```

Rules:

- Views bind to view models; view models call app services.
- App services should own JSON/HTTP/event parsing.
- Feature modules should not directly mutate another module's controls.
- Cross-module actions should go through a command bus or navigation service.
- Risky execution actions must carry audit metadata and review-gate state.
- Refactor incrementally; do not rewrite the desktop in one pass.

Recommended extraction order:

1. Stabilize boundaries with `DesktopApiClient`, DTOs, event bus, and reusable rules.
2. Extract workflows first.
3. Add typed workflow pins and schema forms.
4. Split services/logs/diagnostics, then Skills, Agents, Search/RAG, governance, and remaining modules.
5. Add performance telemetry, module-level cache/ETags, and targeted invalidation.

## Workflow Architecture

Current workflow capabilities include:

- Built-in and saved workflow definitions.
- Node graph editing with lanes/swimlanes.
- Drag, pan, zoom, multi-select, undo/redo.
- Port-based connection preview and compatibility feedback.
- Cycle and invalid self-link blocking.
- Runtime `depends_on` semantics.
- Node progress, assigned Agent, Skill mapping, repair suggestions, and detailed node output/state.
- Workflow version history, audit events, and governance metadata.
- Android and browser worker scheduling metadata.

Target workflow direction:

- Move from node-only dependencies to typed pin edges.
- Store edges as `source_node`, `source_pin`, `target_node`, `target_pin`, `edge_kind`, and optional schema mapping.
- Preserve generated `depends_on` for scheduler compatibility.
- Show invalid edges visibly and provide explicit fix actions.
- Keep repair suggestion-first; do not auto-modify risky workflows.

Initial pin families:

- `exec`
- `artifact`
- `knowledge`
- `review`
- `signal`
- `agent`

## Agent Cluster And Model Routing

Architecture decision:

- `SpiritKin Runtime / AgentCluster` remains the global coordinator.
- A deterministic safety kernel handles permissions, confirmations, audit, tool whitelist, pending state, budgets, timeouts, fallback, and final execution permission.
- A coordinator LLM can route, plan, summarize, and ask clarification questions, but should not directly execute high-risk actions.
- Specialist Agents use an `AgentAdapter` contract, whether native, LangGraph, CrewAI, Codex/Claude CLI, MCP, remote worker, or future GUI automation Agent.

Current enabled/tracked roles:

- `main_text`: primary general Agent.
- `programming`: code editing, tests, debugging, review. It now receives a
  governed read-only code workspace context from WorkerPool/GitWorker
  (`git.status` and optional `git.diff`) before prompting; edits and commits
  still go through normal tools, confirmation, and review gates.
- `vision_model`: image/screen/video-frame understanding.
- `video_animation`: animation, storyboard, media planning.
- `game_development`: game planning/UI/scripts/testing workflows.
- `ecommerce`: product, listing, operations, replay.
- `skill_runner`: deterministic Skill execution.
- `external_reviewer`: tracked as a review-only role candidate.

Model distribution policy:

- Keep the main execution path stable with a consistent text model family.
- Mix specialized models for vision, embeddings, reranking, ASR/TTS, and external review.
- External reviewers should be review-only by default.
- Route profiles should be evaluated before becoming defaults.
- No stronger model bypasses confirmation gates.

Known local model configuration from current architecture docs:

- Text/reasoning and vision: OpenAI-compatible Qwen GGUF through the managed llama.cpp service on port `8080`.
- Embedding: a dedicated llama.cpp Nomic instance on port `8081`; reranking reuses the Qwen service on port `8080`.
- Search/RAG remains configurable through management UI; small/local embedding indexes can persist through `JsonVectorStore`, while production-scale hybrid retrieval is still pending.

Do not put API keys into this document.

## Search, RAG, And Knowledge

Current direction:

- Runtime KB backend can use embeddings with hashing/token-overlap fallback.
- OpenAI-compatible embeddings are configurable through `config/config.yaml` and `/desktop/search-management`.
- LLM reranker can reorder retrieved ids and falls back when unavailable.
- Per-Agent and domain knowledge bases live under `state/knowledge_bases`.
- `JsonVectorStore` can persist small/local embedding indexes via `vector_store_path` / `SPIRITKIN_VECTOR_STORE_PATH`.
- The next stronger retrieval step is production-scale vector storage plus hybrid lexical/vector ranking.

Important constraint:

- Knowledge/RAG should improve context selection, not become a way to paste the whole repository into every prompt.

## Skills, Learning, Evolution, And Governance

Skills:

- Skill registry management lives under `/desktop/skills`.
- Candidate Skills should be reviewed, tested, promoted, archived, and exported through governed paths.
- External studio/process patterns can become SpiritKin-owned candidate Skills, not imported unchecked commands.

Learning:

1. Human or Agent records a concrete failure in the Learning tab.
2. Optional external model review proposes a candidate correction.
3. Human accepts or edits the correction.
4. Record is saved into `state/learning/learning_records.jsonl`.
5. Training dataset exports to `state/learning/self_training_dataset.jsonl`.
6. Skill or Agent changes still require tests or manual verification before promotion.

Evolution:

- Current seeded evolution Skill candidates are templates, not active production Skills.
- Auto code apply and auto Skill promotion should remain disabled unless explicitly governed.
- AgentCluster now appends execution/failure trajectories to the evolution JSONL log by default; SkillRunner dry-runs/executions append `skill_runner.run` trajectories, WorkflowRunner tool/skill nodes append `workflow_runner.node` trajectories, collaboration route-bus terminal worker events append `collaboration.worker_event` trajectories, Android command results append `android.command_result`, and RemoteWorker execute/package/rollback results append `remote.worker_result`. Failed trajectories can be exported as `state/evolution/eval_cases.jsonl`, then used to build self-training chat JSONL exports with linked eval report metadata. Real-device and production worker paths still need validation before promotion decisions are fully representative.

Governance:

- Module maturity, risks, and proposals are exposed through `/desktop/ecosystem-review` and module management surfaces.
- Critical/high-risk modules need owners, verification commands, tests, or documented manual validation.
- Skills, model changes, training packages, remote exports, and risky execution should stay behind review gates.

## 3D Avatar Current State

Current primary avatar route:

- Page: `frontend/avatar_3d.html`
- Manifest: `frontend/models/spirit3d/manifest.json`
- Default GLB: `frontend/models/spirit3d/reference/bangboo_pmx_glb_screen.glb`
- Desktop embedded URL is built from `Features/Common/MainWindowHelpers.cs`.

Stable asset decisions:

- The current avatar is treated as a Bangboo-style electronic-screen character.
- Do not enable the old `controls_legs` route as default.
- Do not rotate internal bones on the current default GLB; earlier attempts can tear ears, visor, accessories, or body pieces.
- Expressions should use the GLB screen material, not a large floating external canvas.
- Real head/hand/leg motion needs a clean Blender rig with manually checked weights before enabling deeper skeletal animation.

Recent avatar behavior synced into the desktop/web route:

- Action queue: each movement/action should finish before the next starts.
- Turn transitions pivot first, then walk; legs should not shuffle during the turn phase.
- Left mouse drag model observation and wheel zoom are disabled.
- The model platform/floor is hidden.
- The avatar is visually aligned to the room background by anchor rather than hard split positioning.
- Forward/back/left/right locomotion persists through `/avatar-state/locomotion`.
- Backward depth is constrained; forward depth has a larger usable range.
- Boundary messages are explicit when movement cannot continue.
- Right control panel can be shown/hidden; desktop embed mode hides it.
- Camera fit is based on neutral body fit rather than the moved world position, so refresh should not visually hide persisted boundary state.

Recent supporting files:

- `frontend/avatar_3d.html`
- `frontend/models/spirit3d/manifest.json`
- `backend/app/static_frontend_server.py`
- `desktop/SpiritKinDesktop/Features/Workspace/LocalServiceRuntime.cs`
- `runtime/avatar_locomotion_state.json`

Important desktop parity decision:

- Desktop now starts `backend.app.static_frontend_server` instead of plain `python -m http.server` so WebView and external Edge browser share `/avatar-state/locomotion`.
- Frontend health now checks both `avatar_3d.html` and `/avatar-state/locomotion`.

Avatar verification commands:

```powershell
$html = Get-Content -LiteralPath 'frontend/avatar_3d.html' -Raw
$m = [regex]::Match($html, '<script type="module">([\s\S]*?)</script>')
if (-not $m.Success) { throw 'module script not found' }
$tmp = Join-Path $env:TEMP 'avatar_3d_module_check.mjs'
Set-Content -LiteralPath $tmp -Value $m.Groups[1].Value -Encoding UTF8
node --check $tmp

node -e "JSON.parse(require('fs').readFileSync('frontend/models/spirit3d/manifest.json','utf8')); console.log('manifest ok')"

dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj --no-restore
```

## Current Worktree Awareness

At the time this document was updated, the worktree had multiple active changes. Treat them as user/model work and do not revert unrelated files.

Known recent Codex-owned or Codex-touched areas:

- `docs/project_management_overview.md`
- `docs/ai_collaboration_context.md`
- `README.md`
- `frontend/avatar_3d.html`
- `frontend/models/spirit3d/manifest.json`
- `backend/app/static_frontend_server.py`
- `desktop/SpiritKinDesktop/Features/Workspace/LocalServiceRuntime.cs`
- `desktop/SpiritKinDesktop/App.xaml`
- `desktop/SpiritKinDesktop/MainWindow.xaml`

Other modified areas may belong to the user or Opus:

- `backend/app/learning_workflow.py`
- `backend/services/conversation_engine.py`
- `backend/tests/unit/test_command_gateway.py`
- `backend/tests/unit/test_conversation_engine.py`
- `desktop/SpiritKinDesktop/Features/Composer/ComposerSelectorMenus.cs`
- `desktop/SpiritKinDesktop/Features/Learning/ModelProviderDefinitions.cs`
- `desktop/SpiritKinDesktop/ViewModels/UiDisplayText.cs`

Untracked/generated items observed:

- `runtime/`
- `k.includes('leg')`
- `x.role.includes('leg')`

Guidance:

- Ignore unrelated dirty files unless they affect the assigned task.
- If edits must touch a file Opus is actively refactoring, coordinate first or keep the patch extremely narrow.
- Prefer documentation or backend-compatible contract updates when UI ownership is unclear.

## Known Risks And Open Work

- WPF refactor is in progress and broad UI edits can conflict with Opus work.
- Desktop startup previously failed when shared resources were only window-scoped; application-level resource loading now fixes the immediate `LineBrush` startup issue.
- Management API availability depends on command gateway health; desktop UI may launch even when backend services need restart.
- 3D avatar movement feels close but should be regression-tested in both external Edge and desktop WebView after changes.
- Web and desktop management surfaces are not always fully equivalent; parity should be checked before declaring a feature complete.
- WorkerPool scheduling is implemented incrementally; not every runtime type uses it yet.
- Planned worker seeds are taxonomy-only. They must not be treated as executable capacity until registered as ready descriptors.
- Context write intent has an append-only ledger, review transitions, a narrow `/context/policy` applier, a Project Overview proposal applier, and collaboration append appliers for messages, decisions, and reviews. Other paths are still not applied. The old desktop/collaboration/ecommerce state sources still own writes until each path gets an explicit governed applier.
- Provider health/latency metadata updates only when a user or desktop action triggers provider test/sync. There is no automatic cloud health probe by design.
- Workflow typed pins are a target architecture, while current runtime still largely uses `depends_on`.
- RAG retrieval now has a lightweight persistent JSON vector store, but not yet a production-scale hybrid vector backend.
- External model/CLI assistants must remain review-only unless a governed workflow explicitly allows writes.
- Governance proposals should be worked from `proposal_triage`: approve low-risk executable work, convert manual/high-risk items into tracked tasks, and archive stale noise.

## Collaboration Protocol For Codex And Opus

1. Read this file first, then the linked handoff docs relevant to the task.
2. Check `git status --short` before editing.
3. Create or update a collaboration task for non-trivial cross-model work.
4. Claim active file patterns when a model owns a working area.
5. Preserve unrelated dirty files.
6. Use the desktop Project Overview mechanism for reviewed document proposals when working through the app UI.
7. Keep UI refactor ownership with Opus unless the user assigns UI work to Codex.
8. Codex should document backend contracts and runtime state changes clearly so Opus can wire UI against stable shapes.
9. Any change that affects desktop/WebView/avatar parity must be verified on the local service route.
10. Any change that affects Agent execution, Skills, tools, workers, remote nodes, mobile, or ecommerce automation must preserve review gates and auditability.
11. Update this document after architecture-level decisions, service-route changes, major UI boundaries, model routing changes, or avatar behavior changes.

## Standard Verification Commands

Use the narrowest command that covers the changed surface:

```powershell
# Backend gateway and Agent policy tests
python -m unittest backend.tests.unit.test_command_gateway backend.tests.unit.test_agent_cluster.AgentClusterTests.test_skill_assist_policy_can_block_skill_before_run -v

# Desktop build
dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj --no-restore

# Desktop build without apphost when only compile validation is needed
dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj --no-restore -p:UseAppHost=false

# Avatar module syntax and manifest JSON
$html = Get-Content -LiteralPath 'frontend/avatar_3d.html' -Raw
$m = [regex]::Match($html, '<script type="module">([\s\S]*?)</script>')
if (-not $m.Success) { throw 'module script not found' }
$tmp = Join-Path $env:TEMP 'avatar_3d_module_check.mjs'
Set-Content -LiteralPath $tmp -Value $m.Groups[1].Value -Encoding UTF8
node --check $tmp
node -e "JSON.parse(require('fs').readFileSync('frontend/models/spirit3d/manifest.json','utf8')); console.log('manifest ok')"
```

## Document Maintenance

- Keep this file concise enough for model context, but detailed enough to prevent duplicate architecture discovery.
- Move long implementation logs to `docs/ai_collaboration_context.md` (dated review/resolution sections).
- Move focused avatar details to `docs/avatar_3d_animatable_model_pipeline.md`.
- Move desktop roadmap detail to `docs/mainwindow_carve_plan.md`.
- Keep this file as the high-signal index and current collaboration agreement. Superseded documents go to `docs/archive/` (see `docs/README.md`).
