# SpiritKinAI AI Collaboration Context

Last updated: 2026-07-06

This document is the shared context file for Codex, Claude Opus, GPT, Gemini, DeepSeek, Qwen, and other reviewer or implementation models. Use it before discussing architecture changes, code improvements, model routing, desktop behavior, or avatar/runtime UX.

## Purpose

- Give external models a compact project brief without sending the whole repository.
- Record current decisions and recent changes that are easy to miss from code alone.
- Provide a stable handoff target for cross-model review, critique, and improvement proposals.
- Keep implementation discussion grounded in verifiable files and commands.

This document is not the full architecture record. It is an index plus current working context.

## Required Reading Order

Read these first when asked to review or improve the project:

1. `docs/ai_collaboration_context.md` - this cross-model collaboration brief.
2. `docs/current_architecture_snapshot.md` - current runtime, backend, desktop, Agent, model, Search/RAG, Skill, and governance map.
3. `docs/ai_runtime_kernel_spec.md` - stable AI Runtime Kernel concepts, layer boundaries, naming rules, and long-term capability-first architecture.
4. `docs/project_management_overview.md` - operator-facing project management overview.
5. `docs/project_dictionary.md` - canonical terms, aliases, ID scopes, statuses, and module ownership.
6. `docs/runtime_metadata_contract.md` - Runtime Metadata, Context Kernel, Agent Protocol, Execution Finalizer, Scheduler, and Capability Graph migration contract.
7. `docs/README.md` - docs index; superseded historical documents (including the old `codex_handoff.md`) live in `docs/archive/`.
8. `docs/avatar_3d_animatable_model_pipeline.md` - 3D avatar asset and motion pipeline constraints.
9. `docs/agent_cluster_optimal_plan.md` - multi-Agent/model strategy and architecture direction.
10. `docs/model_distribution_plan.md` - model/provider distribution policy.

Use `README.md` only as a broad entry point; some avatar wording there is older than the current Three.js 3D direction.

## Current Product Direction

- The primary user surface is the WPF desktop app under `desktop/SpiritKinDesktop`.
- The primary avatar surface is `frontend/avatar_3d.html` with the Bangboo GLB model from `frontend/models/spirit3d/manifest.json`.
- Live2D is no longer active roadmap work unless explicitly requested; historical Live2D notes remain only as background context.
- Frontend service target is normally `http://127.0.0.1:8787`.
- Event bridge target is normally `ws://127.0.0.1:8765`.
- Command gateway target is normally `http://127.0.0.1:8788`.

## Current Architecture Rules

- Do not bypass permission, review, confirmation, audit, or safety-stop gates.
- External models and CLI assistants should review, propose, or synthesize plans by default. They should not directly execute live actions unless an explicit governed workflow permits it.
- The execution chain should remain:

```text
Agent / Skill / external assistant
  -> ToolRegistry
  -> permission / review / confirmation gate
  -> Executor / Worker
  -> Device / connector / remote worker
```

- Models are replaceable brains. Skills, Workflows, Capability Graph, Knowledge, Policy, and Evals should remain model-independent.
- Do not add new top-level Router, Manager, Registry, or Context concepts unless they cannot fit inside the AI Runtime Kernel concepts in `docs/ai_runtime_kernel_spec.md`.
- Capability is the stable API. Workflows should move toward Capability requirements; Skills and Workers are replaceable implementations selected by Scheduler.
- Distillation is a Model Lifecycle step and Model Artifact lineage, not a Runtime layer.
- Prefer structured contracts and snapshots over prompt-only implicit behavior.
- Treat metadata as schedulable runtime data. Workflow, Skill, Worker, Agent, model provider, execution, and artifact metadata should follow `docs/runtime_metadata_contract.md`.
- `/desktop/context` includes `runtime_context`, a read-only ContextStore mirror of active desktop project/session state, collaboration ledger state, and ecommerce queue state.
- `/desktop/context` also includes `write_intent_preview` and an append-only `write_intents` ledger. Context write intents can be submitted, approved, rejected, listed, and applied only for explicit owner paths. Approved `/context/policy` intents update ContextPolicy; approved `/project/overview/proposal` intents create pending Project Overview proposals without overwriting the document; approved collaboration intents append messages, decisions, or reviews without starting workers.
- Model collaboration messages should be read through `agent_envelope` first. Legacy `from_model`, `to_model`, `from_agent`, `to_agents`, `role`, and `content` fields remain compatibility fallbacks.
- Agent routing now has a durable JSONL seed, `JsonlAgentRouteBus`, under `state/agent_route_bus/`. It records accepted `AgentEnvelope` messages and allow/block route audits. Accepted collaboration messages mirror into this bus, while collaboration UI storage remains the desktop-owned ledger.
- Collaboration snapshots expose `agent_route_bus`, and `/desktop/context.runtime_context` mirrors it as `/agent_route_bus/summary`.
- Collaboration action `list_agent_route_bus_messages` can read route-bus messages by Agent recipient, context/thread, and task without marking them consumed.
- Collaboration action `ack_agent_route_bus_message` records Agent-bus consumption in `message_acks.jsonl`; it does not change desktop chat read state.
- Collaboration action `run_agent_route_bus_worker_once` is dry-run-only: it processes one unacked route-bus message, can ack it, and can post a deterministic test answer without invoking a real model.
- Route-bus worker diagnostics are recorded in `state/agent_route_bus/worker_events.jsonl` for idle, processed, failed, and disabled states. This is diagnostic only; message consumption still comes from `message_acks.jsonl`.
- Collaboration action `agent_route_bus_worker_status` is the non-consuming status endpoint for the route-bus worker seed. It reports default `codex` and `claude_code` pending/ack counts, recent worker events, storage paths, and external assistant command readiness from Agent Management; `/desktop/context.runtime_context` mirrors the compact result as `/agent_route_bus/worker_status`.
- `scripts/collaboration_agent_worker.py` defaults to `--transport route_bus`, so desktop-started external assistant workers consume durable bus messages and ack them through `message_acks.jsonl`. Use `--transport legacy_inbox` only for older inbox/read_by behavior.
- `scripts/collaboration_agent_worker.py` auto-injects this document (`docs/ai_collaboration_context.md`) as the first section of every external-assistant prompt via `load_collaboration_context_brief()`. This makes the collaboration entry point system-level, not just a human convention. The brief is truncated at 8000 chars and can be disabled with `SPIRITKIN_DISABLE_COLLABORATION_CONTEXT=1`.
- WPF collaboration mention routing should resolve aliases from the backend participant registry before using built-in compatibility aliases. This keeps user/provider/model aliases authoritative while preserving fallback mentions such as `@Codex`, `@ClaudeCode`, `@all`, and local specialist Agent aliases before the registry snapshot has loaded.
- `scripts/collaboration_mailbox.py inbox/read/watch/status` also default to the route bus, so manual CLI inspection, one-click desktop workers, and background workers share the same Agent consumption ledger.
- Terminal Workflow runs now write Context/Finalizer sidecars and can sync finalizer verdicts into bound collaboration tasks or explicitly bound ecommerce queue tasks.
- In-memory scheduler tasks now also receive Finalizer verdict snapshots after queued task complete/fail/block paths, and each verdict appends a `/scheduler/tasks/finalizer` Context patch under `task:<task_id>`.
- Model provider test/sync actions record health observations in `state/model_provider_health.jsonl`, expose duration, health status, checked time, model count, and append `/model/providers/health` Context patches. There is no automatic cloud provider probe.
- WorkerPool taxonomy and CapabilityGraph include planned non-schedulable seeds for Python, FFmpeg, Git, and Service RAG workers, but default AgentCluster now also registers concrete ready executors for all four: `executor:python_worker`, `executor:git_worker`, `executor:ffmpeg_worker`, and `executor:service_rag_worker`.
- Real Codex <-> Claude Code collaboration smoke passed on 2026-06-29 in thread `real-model-smoke-20260629`: Claude Code produced `CLAUDE_REAL_MODEL_OK`, then Codex produced `CODEX_REAL_MODEL_OK`. TODO(verify): this is a runtime event claim, not reproducible from version-controlled code; the smoke artifacts live under `state/agent_route_bus/` (gitignored). Re-run the smoke to reconfirm before relying on it.
- Keep web and WPF surfaces aligned; if a web feature affects runtime behavior, verify whether desktop WebView uses the same frontend service and URL.

## 2026-07-02 Collaboration Closure Notes

- The collaboration context brief is now verified by `backend.tests.unit.test_collaboration_worker_script.CollaborationAgentWorkerScriptTests.test_build_prompt_injects_collaboration_context_brief`.
- Treat this document as the shared entry point, but still verify architecture claims against source files and tests. Some runtime smoke evidence lives in gitignored state/log files and must be re-run when needed.
- Generated runtime artifacts belong under `runtime/`, which is ignored by git. Do not commit screenshots, local logs, WebView caches, or avatar locomotion state.

## 2026-07-02 Cross-Model Review Record

- Current "Codex + OPUS" review was not a live model-to-model conversation inside the route bus. It was an independent review by multiple models using this document as the shared entry point, then comparing conclusions. Phrase future summaries as "shared entry point + converged conclusions" unless route-bus messages and acks show direct model communication.
- `run_agent_route_bus_worker_once` remains a dry-run seed. Non-dry-run returns `real_worker_not_enabled`; real external-assistant operation depends on `scripts/collaboration_agent_worker.py`, local assistant configuration, and runtime state. Do not describe route-bus real workers as production-complete until a repeatable smoke proves it.
- Security token strategy is mostly implemented, not merely planned: `scripts/start_desktop_console.py::resolve_session_token()` generates a session token when none is supplied, command gateway GET/POST paths call `token_is_authorized()`, and localhost bypass is disabled by default unless explicitly enabled with `SPIRITKIN_ALLOW_LOCALHOST_WITHOUT_TOKEN` or `SPIRITKIN_DEV_ALLOW_LOCALHOST_AUTH_BYPASS`. Remaining work is visibility and operator guidance: show bypass/development mode clearly in UI/docs and document LAN/public exposure requirements.
- The most concrete next implementation target is visible environment validation for Browser, Android, Remote, and OpenClaw workers. Operators should see why a worker is unavailable, degraded, preview-only, or validated instead of inferring it from missing executors or silent `from_environment()` fallbacks.
- RAG and Search quality remain configuration-dependent. Without a real embedding/reranker provider, hashing embeddings and token-overlap ranking are fallback behavior and should not be treated as strong semantic retrieval.

## 2026-07-02 Joint Review: Progress, Architecture, Gaps

This section records an actual cross-model review round between GPT and Claude Opus, done through this document as the shared entry point.

**Nature of the collaboration (do not overstate):** This was NOT a live in-system model-to-model conversation. The route bus real worker is still a dry-run seed (`backend/app/collaboration.py:1303-1321` returns `real_worker_not_enabled` when `dry_run=False`). What happened: GPT and Claude Opus each evaluated the project independently against this same entry document, then their conclusions were reconciled by the human operator. Treat this as "shared entry point + converged conclusions", not "two models talking through the bus".

**Progress (agreed):** The project is no longer a documentation-only shell. Runtime P1 seeds are real and test-anchored: WorkerPool, CapabilityGraph/Registry, ContextStore, AgentEnvelope/JsonlAgentRouteBus, ExecutionFinalizer, Resource Registry, and multiple worker executors. Accurate framing: a local desktop control plane plus an AI Runtime Kernel seed exists; it is not yet a production-grade AI OS.

**Architecture integrity (agreed):** Boundaries are clear. Execution flows Agent/Skill -> ToolRegistry -> permission/review/safety -> WorkerPool -> Executor/Worker. Route bus is real durable JSONL. Python/Git/FFmpeg/Service RAG are default ready executors; Browser worker is enabled only after explicit configuration. Perception Context is a first-class optional input under policy gate. Resource Registry is the durable-object entry point but onboarding/credential binding is not fully productized. Context Kernel/Finalizer is wired into Workflow/scheduler sidecars but is not yet the single system-wide state owner.

**Code-verified corrections to the gap list (Claude Opus, 2026-07-02):**

- Security token strategy is further along than the review implied. The security helpers ARE wired into the command gateway: `do_GET` (`backend/app/command_gateway.py:1864`) and `do_POST` (`:1972`) both enforce `token_is_authorized` and return 401 on failure. Desktop launch auto-generates a session token when none is set: `resolve_session_token` (`scripts/start_desktop_console.py:38-39`) falls back to `secrets.token_urlsafe(18)` and injects it into the child env. Both localhost-bypass env flags default to False (`backend/security/http.py:23-27`), so the default posture is "deny unauthenticated requests", not "dev-mode open bypass". The remaining work is visibility/docs (surface bypass as an explicit dev mode; document mandatory checks before LAN/public exposure), not missing enforcement code.

**Confirmed real gaps (agreed, code-verified):**

- Route bus real worker is dry-run only; no CI-reproducible evidence of real model-to-model collaboration.
- Device environment validation is missing. `BrowserWorkerExecutor.from_environment` (`backend/executors/browser_worker_executor.py:51-54`) silently returns None when `SPIRITKIN_BROWSER_WORKER_COMMAND` is unset, so operators get no visible "why is this worker disabled" status. Android/Remote/OpenClaw need the same explicit validation markers.
- Context Kernel is still an append-only ledger plus a small set of appliers, not a unified state owner.
- RAG quality depends on real embedding/reranker config; without it the system degrades to hashing/token-overlap (`backend/knowledge/embedding.py`, `reranker.py`), which is not strong semantic retrieval.
- Real-device validation is still absent for Browser, Android, Remote, and OpenClaw paths.
- E-commerce still has placeholder inventory; specialist agents need more real domain tools and KB.

**Next-step priority (agreed):** favor reproducible real-run evidence over more abstraction. (1) Write a local smoke script covering route-bus real worker, three-service startup, avatar state, and command-gateway auth. (2) Add environment-validation status to Browser/Android/Remote/OpenClaw instead of silent None. (3) Extend Context Kernel by exactly one owner/applier path (Workflow run or scheduler task). (4) Make the localhost token posture explicit in UI/docs. (5) Finish real embedding/reranker config plus failure-degradation observability before expanding KB.

**Work feedback — next-step (2) implemented (Claude Opus, 2026-07-02):** Environment validation for the four optional workers is now visible instead of silent. New module `backend/orchestrator/worker_environment.py` produces a `WorkerEnvironmentReport` per worker (`worker_id`, `label`, `status`, `registered`, `reason`, `remediation`, `env_signals`, `metadata`) using a shared status vocabulary — `available` / `preview_only` / `not_configured` / `degraded`. Coverage: Browser (`SPIRITKIN_BROWSER_WORKER_COMMAND` present → available, else not_configured with the exact env var to set); OpenClaw (`SPIRITKIN_OPENCLAW_HTTP_BASE_URL` present → available/http, else preview_only/in_memory but still registered, so simulated hardware is labeled as such); Remote (runtime `node_count>0` → available, else not_configured); Android (companion status maps needs_pairing/endpoint_offline/offline → not_configured, needs_attention/degraded → degraded, else available). Wired into the operator surface as `AgentCluster.worker_environment_snapshot` and embedded in `capability_inventory_snapshot["worker_environment"]` (`backend/orchestrator/agent_cluster.py:406-451`), sourcing `remote_node_count` from `node_registry.list_nodes()` and android status/device_count from `AndroidCompanionStore().snapshot()`, both behind try/except so the snapshot never fails when a backend is absent. Tests: `backend/tests/unit/test_worker_environment.py` (9 passed); agent_cluster + architecture-layer regression suite (145 passed). This closes the "device environment validation is missing" gap at line 114; real-device validation (line 117) is still open and separate.

**Work feedback — realtime duplex groundwork (Claude Opus, 2026-07-03):** The operator asked for realtime duplex conversation "not just external models — all models with each other AND with me". This is a cross-system change touching a safety-sensitive surface (models auto-executing, model-to-model loops that can run forever and burn API cost), so it was scoped with the operator into three approved decisions: (a) autonomy = turn-cap plus human refill ("人工续杯"); (b) transport = extend the existing event bridge (ws 8765); (c) execution landing = keep the external worker, switch from polling to push trigger. Delivered in this round:

- **Turn-limit guardrail (safety prerequisite, built and verified first):** New module `backend/app/collaboration_turn_guard.py` persists per-thread turn budget under the collaboration state root (shared across the per-agent worker processes, survives restart). API: `record_turn_and_check` (consume one turn if allowed, flip thread to `awaiting_refill` at cap), `check_turn_allowance` (read-only), `refill_turns` (human top-up, extends cap + reactivates), `reset_turns`, `turn_guard_snapshot`. Default cap `SPIRITKIN_COLLABORATION_TURN_CAP=6`. Only a **model→model automatic `answer`** consumes budget — human authors and model→human replies never do (`_is_automatic_model_reply` in `collaboration.py`). The guard is enforced **server-side inside `post_collaboration_message`** (after route verdict, before persist), so external workers cannot bypass it — hitting the cap raises and pauses the auto-reply chain until a human refills.
- **Push delivery instead of polling:** `push_collaboration_message_to_event_bridge` fans every persisted collaboration message out to the event bridge via the existing `dispatch_runtime_event(resolve_event_sink_url(), …)` chain (event type `collaboration.message`). Best-effort (swallows failures so persistence never depends on the bridge); disable with `SPIRITKIN_DISABLE_COLLABORATION_PUSH`. This replaces the 3–15s poll tick for delivery latency; the external worker still performs the actual model call.
- **Operator surface:** new collaboration actions `refill_turns` / `reset_turns` / `turn_guard_status`, and `turn_guard` embedded in `build_collaboration_snapshot`.
- **Tests:** `test_collaboration_turn_guard.py` (9) + `test_collaboration_turn_guard_integration.py` (7) green; existing collaboration/runtime-contract/gateway suites unaffected; full unit suite **966 passed**.

**Honest status of "duplex realtime":** Still NOT a live in-process model-to-model conversation loop. What now exists: (1) realtime *push* of messages to all subscribers (was polling), and (2) a governed turn budget that makes automatic model→model reply chains safe to enable via the external worker. The remaining gap to true duplex is a push-triggered worker that reacts to the `collaboration.message` event (instead of its 5s poll) and an explicit enable switch for automatic replies — deliberately left for operator sign-off, since turning it on spends real API budget.

**Work feedback — single-personality presentation of collaboration (Claude Opus, 2026-07-03):** Follow-up to the duplex groundwork. The operator clarified the design intent: the multi-model unified boundary should surface as **"the model's output" — one personality**, not multiple models each speaking, which "看起来多个模型多个表现，像是分裂人格" (looks like split personality). The unified boundary *decision器* refactor was explicitly deferred ("暂不决定"); this round only makes the avatar render collaboration as a single self. Delivered:

- **Backend presentation tag (authoritative):** `push_collaboration_message_to_event_bridge` now stamps each pushed `collaboration.message` payload with a `presentation` field via new helper `_classify_collaboration_presentation(from_agent, to_agents, role)` (reuses `_is_human_collaboration_agent`). Three surfaces (constants `PRESENTATION_USER`/`PRESENTATION_OUTWARD`/`PRESENTATION_INTERNAL`): `user` = human authored → echo as the user's own turn; `outward` = a model speaking to the human (or a broadcast `answer` where the human is an implicit audience) → voice as the single personality, no model name; `internal` = model↔model deliberation with no human recipient → background "thinking" only. Classification is server-side so every subscriber agrees on the surface.
- **Frontend single-personality rendering:** `applyCollaborationMessage(p, replay)` in `frontend/avatar_3d.html` (dispatched from `applyEvent` on `collaboration.message`) branches on `presentation`: `user` → `msg('user', …)`; `outward` → `msg('agent', …)` + `startReplyPerformance` + `showReplyOnScreen` (the avatar speaks as one voice); `internal` → no bubble at all, just a transient `thinking` expression. No per-model bubbles, no model names — internal deliberation is ambiance, not a speaking character.
- **Tests:** `test_collaboration_presentation.py` (4) covers user/outward/internal/mixed-recipient classification; frontend module passes `node --check` (UTF-8-safe extraction). Full unit suite **972 passed**, zero regression.

**Still pending (unchanged, needs operator sign-off):** browser visual verification of the avatar rendering (cannot be claimed from code alone); the push-triggered auto-reply worker + enable switch; and the deferred unified-boundary decision器.

**Work feedback — push-triggered auto-reply worker + explicit enable switch (Claude Opus, 2026-07-04):** Closes the second pending item above. Two safety-critical properties were preserved: automatic model→model replies stay **OFF by default**, and enabling them is a single explicit operator action. Delivered:

- **Enable switch, enforced twice (defense in depth + cost gate):** New `collaboration_auto_reply_enabled()` reads `SPIRITKIN_COLLABORATION_AUTO_REPLY` (default off). Server-side, `post_collaboration_message` now rejects any automatic model→model `answer` with `auto_reply_disabled` *before* the turn guard runs, so no external worker can post auto-replies while the switch is off. Worker-side, `scripts/collaboration_agent_worker.py` skips model-authored messages entirely when auto-reply is off (`should_skip_model_message`) — the skip happens **before** the model API call, so a disabled switch costs zero tokens; skipped messages are recorded to the route bus with `status="skipped"` + `reason=auto_reply_disabled` and marked consumed, so old backlog does not suddenly fire when the switch is later enabled. Human-authored questions are always processed regardless of the switch.
- **Push trigger replacing the poll tick:** The worker now subscribes to the event bridge (`websockets` client on a daemon thread, `resolve_push_ws_url` falls back to `resolve_event_sink_url()`), filters frames with `should_wake_for_event` (type `collaboration.message`, sender ≠ self, recipient is me/`all`/broadcast), and sets a `threading.Event`; the main loop's `time.sleep(interval)` became `wake.wait(timeout=interval)` — reaction latency drops from the 5s poll tick to near-instant on push, while the poll remains as fallback when the bridge is down (listener reconnects with exponential backoff, `--no-push` opts out, `--push-url` overrides).
- **CLI:** `--auto-reply` (per-process opt-in equivalent to the env), `--no-push`, `--push-url`.
- **Tests:** turn-guard integration fixture now sets the switch explicitly, plus a new disabled-by-default test asserting model→model is rejected while human→model and model→human still flow; worker script suite gains `should_skip_model_message` / `should_wake_for_event` / `resolve_push_ws_url` coverage. Targeted collaboration suites 58 passed; ruff clean.

**Still pending after this round:** browser visual verification of the avatar rendering, and the fixed-spokesperson `outward_speaker` rule (approved approach: only the designated speaker's replies surface as `outward`, other models demoted to `internal`; arbitration deferred until real traffic).

## 2026-07-03 Full-Project Defect Review (Claude / Fable 5)

Independent whole-repo review via this entry point, covering backend, frontend, desktop, tests, and security. Findings are code-verified with file:line evidence; ranked by severity.

**🔴 Critical:**

1. **No CI at all.** No `.github/workflows` or equivalent. 88 test files (966+ passing) run only by hand; there is no regression gate. This undermines every "tests green" claim in this document the moment anyone forgets to run them.
2. **Token comparison is not constant-time and empty token = allow-all.** `backend/security/http.py:46-54` `token_matches` uses `==` (timing side channel; `hmac.compare_digest` exists in the repo only at `backend/remote/package_security.py:93`), and returns `True` when `expected_token` is empty — an unset `SPIRIT_MOBILE_TOKEN` degrades the command gateway to localhost bypass. Also `is_local_request` (`http.py:42-43`) trusts the spoofable `Host` header (DNS-rebinding risk). This refines the earlier "token strategy is mostly implemented" note at the 2026-07-02 section: enforcement is wired, but the primitive itself is weak.
3. **`shell=True` on externally influenced strings.** `backend/remote/worker.py:317-320` runs verification commands with `shell=True`; same pattern at `backend/devices/local_pc.py:180,211` and `scripts/collaboration_agent_worker.py:747,1107`. If any of these strings can be shaped remotely, this is command injection.
4. **God objects at every layer.** `AgentCluster` (`backend/orchestrator/agent_cluster.py`, 2737 lines, 138 methods in one class); `command_gateway.py` 2108 lines; `collaboration.py` 1992 lines. Desktop is worse: `partial class MainWindow` spans **126 files / ~28,400 lines** (82% of desktop source) with ~120 `Click=` handlers in code-behind — pseudo-MVVM (ViewModels exist by name; only one `ICommand` in the codebase).

**🟠 Major:**

5. **orchestrator ⇄ app layering has collapsed.** `backend/orchestrator/context_write_applier.py:6-15` imports `backend.app.collaboration` at top level while 6+ `backend/app/` files import `backend.orchestrator`; circularity is masked by in-function deferred imports (`agent_cluster.py:571,1281,2392`). One-way rule (app → orchestrator) needs to be declared and enforced. (`devices/` is clean.)
6. **Exception-swallowing is systemic.** 221 `except Exception` in non-test code (`command_gateway.py` ×32, `agent_cluster.py` ×24) plus 28 silent `except: pass` — including in the places that must not lose failures: `backend/runtime/events/persistence.py` (event persistence fails silently) and `backend/evaluation/failure_db.py` (the failure DB swallows its own failures).
7. **Execution-loop feedback gap persists in the collaboration worker.** The orchestrator main path does feed stderr back (`execution_retry.py:60-101`, retry loop `agent_cluster.py:1592-1614`), but `scripts/collaboration_agent_worker.py:758` only prints stderr on returncode 0 instead of feeding it into the model context, and message-processing failures (`:77-92`) log without any automatic retry.
8. **Frontend/desktop contract is stringly-typed on both sides.** 14+ event types (`assistant.message`, `avatar.state`, …) and ports 8765/8787/8788 are hardcoded independently in 5 frontend HTML pages and 10+ desktop C# sites (`MainWindowState.cs:245-247`, `LocalServiceRuntime.cs:35-37`, all HTML placeholders). No single contract file; renaming one event means 10+ synchronized edits. (`docs/trace_event_frontend_contract.md` exists as prose but nothing enforces it.)
9. **Five frontend pages copy-paste the same WS/command bridge** with divergent reconnect semantics (`avatar_3d.html:776`, `desktop_console.html:1525`, `index.html:99`, `live2d.html:52`, `spirit_avatar.html:38`); `js/api.js` (38 lines) is unused for this. Root cause: no frontend build system at all (no package.json; Three.js via unpkg CDN importmap), so there is nowhere to put shared modules.

**🟡 Moderate:**

10. Test coverage is one-sided: Python unit tests are healthy, but frontend JS = 0 tests, desktop C# = 0 test projects, `scripts/` (40+ files) ≈ 0. `pyproject.toml:2` pins `testpaths = ["backend/tests/unit"]`, so the root `tests/` (5 files) is silently never collected.
11. Hardcoded machine-specific paths: `backend/mobile/ios_endpoint.py:659` and `backend/orchestrator/workflow_graph.py:1497` embed `D:/SpiritKinAI`; `backend/app/mobile_management.py:534` embeds `C:\Users\Administrator\...adb.exe`. These break any second machine/CI immediately.
12. Chinese prompt text is inlined across 124 non-test Python files (`agent_cluster.py` ×154, `workflow_graph.py` ×149, retry prompt at `execution_retry.py:88-101`) with no prompt resource layer — hard to tune, version, or localize.
13. `CORS`: default posture is fine (allowlist → loopback, `http.py:77-80`), but `SPIRITKIN_ALLOW_ANY_CORS_ORIGIN=1` reflects arbitrary origins (`http.py:75-76`) — worse than `*` if credentials headers are ever added. Keep it out of any LAN/public runbook.
14. Docs sprawl: 30+ top-level docs with overlapping scope (`codebase_map.md`, `current_architecture_snapshot.md`, `project_architecture_and_dev_log.md`, this file). Required-reading order helps, but stale-doc risk grows with each review round appended here.
15. Zero `TODO`/`FIXME` markers in the codebase — known debt (all of the above) is tracked nowhere in code, only in docs like this one.
16. Repo hygiene is mostly good (`.env`/`.env.cloud` untracked and gitignored; no hardcoded real secrets found — only `"lm-studio"` placeholders), but binary reference assets are tracked (`frontend/models/spirit3d/reference/*.png` 1.7 MB, `*.glb` 1.2 MB — consider Git LFS), and local `spiritkin-cloud-*.tar.gz` bundles should be audited to ensure `.env.cloud` never entered them.

**Suggested order of attack:** (1) minimal CI (pytest + ruff + `node --check` + `dotnet build`) — it protects everything else; (2) fix `token_matches` (constant-time compare, empty token ⇒ deny) and audit the `shell=True` call chains; (3) extract a shared frontend/desktop event+port contract (generate both sides from one schema); (4) feed stderr back + add bounded retry in `collaboration_agent_worker.py` to close the execution loop; (5) then start carving `AgentCluster` and `MainWindow` by feature — big-bang rewrites are explicitly not recommended.

### 2026-07-03 Resolution Update (Claude / Fable 5)

- **#1 CI — closed.** `.github/workflows/ci.yml` now gates on: ruff lint (blocking; the 449-item backlog was cleared first — 401 auto-fixed, rest hand-fixed, `UP038` ignored and `E402` per-file-ignored for `scripts/`), Python unit tests on ubuntu with a slim dependency set (`requirements-dev.txt` + pillow/numpy/opencv-python-headless/faster-whisper — torch/transformers are lazily imported and deliberately omitted; the set was verified in a clean venv, 999 tests green), `node --check` over `frontend/**/*.js`, and `dotnet build -c Release` of the WPF project on windows-latest.
- **#2 auth primitive — closed.** `token_matches` uses `hmac.compare_digest` and an empty expected token now denies; `is_local_request` prefers the socket `client_ip` over the spoofable `Host` header, and every localhost-bypass call site (command gateway, Android/iOS endpoints, remote worker) now requires the request to actually be local. iOS query-token compare is constant-time. Tests cover the spoofed-local-Host-from-remote-IP case.
- **#3 shell=True — closed for model-influenced input.** `local_pc.launch_app` launches via argv with `shell=False`; the remaining `shell=True` sites (`remote/worker.py` signature-verified packages behind an opt-in env, `collaboration_agent_worker.py` operator-configured assistant command lines) are annotated with their trust boundaries.
- **#8 contract extraction — closed.** `backend/app/realtime_contract.py` is the single source of truth (26 event types + ports derived from `service_ports.PORT_SPECS`); `scripts/generate_realtime_contract.py` regenerates `frontend/js/realtime_contract.js` and `desktop/.../RealtimeContract.g.cs`, and `test_realtime_contract.py` fails on drift. Backend + all C# event/port literals converged to the constants; the 5 HTML pages consume `window.SPIRITKIN_CONTRACT.defaultPorts` (UI display strings for event labels intentionally left inline).
- **#7 worker execution loop — closed.** stderr was already folded into failure replies (`[stderr]` sections + `stderr_summary` lifecycle events); `collaboration_agent_worker.py` now adds `process_worker_message_with_retry` — bounded retry (`--max-attempts`, default 2, linear `--retry-backoff`) with `lifecycle=retry` worker events, while `WorkerConfigurationError` (disabled/unconfigured/no-command assistants) fails fast without retry.
- **#11 machine-bound paths — closed.** iOS terminal page and workflow composition placeholder derive `project_root` from the repo location at runtime; adb resolves via `SPIRITKIN_ADB_PATH`/`ANDROID_HOME`/`ANDROID_SDK_ROOT`, then (probes permitting) a `%LOCALAPPDATA%` WinGet glob and `PATH` — the per-user absolute path is gone; the desktop console workspace hint renders from project state. (Known remaining defaults: `MainWindowHelpers.cs` `DefaultWorkspaceRoot` is a validated seed behind `SPIRITKIN_WORKSPACE_ROOT` + upward discovery; `validate_desktop_delivery.py` keeps Anaconda python paths as probed candidates.)
- **#6 silent exception swallowing — closed for the data-loss spots.** `JsonlEventPersistence` and `JsonlFailureSampleDB` write failures now warn once to stderr ("…stay in memory only"; the flag resets after a successful write) and load failures are logged instead of dropped. The broad `except Exception` sweeps elsewhere are tracked under the god-object carve — narrowing them file-by-file is only tractable once modules shrink.
- **#13 CORS reflection — closed.** `SPIRITKIN_ALLOW_ANY_CORS_ORIGIN` now returns `*` instead of reflecting the request Origin (browsers reject `*` with credentials, so wildcard stays safe if a credentialed header is added later); `Vary: Origin` is sent only for non-wildcard grants. Default remains loopback-only, explicit allowlist via env.
- **#10 root `tests/` never collected — closed.** `pyproject.toml` `testpaths` now includes `tests`; CI's bare `python -m pytest -q` picks it up automatically (only third-party import in scope is a guarded lazy `qrcode`). Full suite: 1162 passed (was 1008).
- **#5 layering rule — declared and enforced; debt frozen.** `test_layering_rules.py` encodes the one-way app→orchestrator rule: the existing reverse imports are pinned in shrink-only allowlists (top-level: `context_write_applier`, `decision_cache`, `worker_pool`, `workflow_graph`; deferred: `agent_cluster`, `context_mirror`, `workflow_task_finalizer`), so any new orchestrator→app import fails CI and each fix must shrink the list. `devices/` is asserted app-free. Unwinding the seven frozen files remains open under the carve.
- **#4 AgentCluster carve — Python side done (7 slices, 2753 → 2218 lines).** Slice 1: six stateless prompt/context helpers → `orchestrator/prompt_context.py` (plan-mode steps/text, goal metadata/context, attachment context, action-request detection). Slice 2: five more (web-search intent/query/requested, software/hardware inventory formatters) → same module; dead `_normalize_confirmation_text` shim deleted. Slice 3: eight reply/context metadata helpers → `orchestrator/reply_metadata.py`; RequestCoordinator pass-through shims inlined; dead `_serialize_knowledge_hit` deleted. Slice 4: agent status snapshot/reply assembly → `orchestrator/agent_status.py` + `build_worker_environment_snapshot` in `orchestrator/worker_environment.py` (cluster keeps the deferred `backend.app` imports and passes pre-fetched data in, so the layering allowlist never grows). Slice 5: default executor construction → `orchestrator/cluster_wiring.py`; resource registry store/record registration → `orchestrator/resource_registry.py`; dead `_build_capability_registry`/`_register_runtime_resource` deleted. Slice 6: agent runtime policy assembly + capability/skill filtering for containers → `orchestrator/agent_container.py`. Slice 7: nine pure execution/confirmation `AgentReply` builders (executor-missing, policy-denied, success/failure, confirmation mismatch/duplicate) → `orchestrator/execution_replies.py`; the stateful retry loop stays in the cluster. Behavior locked by `test_prompt_context.py`, `test_reply_metadata.py`, `test_agent_status.py`, `test_cluster_wiring.py`, `test_resource_registry.py`, `test_agent_runtime_policy.py`, `test_execution_replies.py`. Remaining #4 scope: `command_gateway.py` (2108) / `collaboration.py` (1992) opportunistic; desktop `MainWindow` (126 files) needs its own plan.
- **#9 frontend WS bridge — closed.** `frontend/js/realtime_bridge.js` (classic IIFE, `window.SPIRITKIN_BRIDGE`) now owns WS connection lifecycle for all five pages via `createRealtimeConnection({getUrl,onEvent,onOpen,onStatus})`: unified exponential backoff (`min(10s, 1s·1.6^n)`), manual-close/timer guards, old-socket handler detachment (fixes desktop_console's double-connect leak), attempts reset on open. live2d gains reconnection (previously none); avatar_3d/desktop_console move off fixed 2s. Shared `commandHeaders` (both `X-SpiritKin-Token` + `Bearer`), non-throwing `postCommand` (avatar_3d must apply `d.events`/`d.reply` even on `!ok`; 204→`{ok:true}`), superset `escapeHtml`, and default URL builders replace per-page copies. Kept per-page: applyEvent dispatch/replay semantics, viseme/emoji tables, desktop_console's `authHeaders` (GET sites must not gain `Content-Type`, which would trigger CORS preflight). Verified: `node --check` on bridge + all five extracted inline scripts, Node functional self-test (headers/escape/URL + reconnect backoff sequence 1000ms→1600ms→connected). Browser smoke pending operator (desktop WebView2 + index console).
- Still open from this review: #4 desktop side (MainWindow carve — execution plan in `docs/mainwindow_carve_plan.md`, assigned to GPT with per-slice Claude review; `command_gateway`/`collaboration` opportunistic), #5 (allowlist unwinding), #12, #14–#16.

**MainWindow carve — slice 0 completed (Codex/GPT, 2026-07-04):** Added the C# regression gate before moving any desktop logic. New traditional solution `SpiritKinAI.sln` includes `desktop/SpiritKinDesktop/SpiritKinDesktop.csproj` and new `desktop/SpiritKinDesktop.Tests/SpiritKinDesktop.Tests.csproj` (xUnit, `net8.0-windows`, WPF enabled, project reference to the desktop app). `desktop/SpiritKinDesktop/Properties/AssemblyInfo.cs` grants `InternalsVisibleTo("SpiritKinDesktop.Tests")` so future extracted internal controllers can stay non-public and still be unit-tested. Initial tests are intentionally no-window smoke coverage: realtime contract stable ports/schema and default `DesktopState` normalization. CI desktop job now restores/builds/tests the solution (`dotnet restore SpiritKinAI.sln`, `dotnet build SpiritKinAI.sln -c Release --no-restore`, `dotnet test SpiritKinAI.sln -c Release --no-build`). Verification passed locally: restore green; Release solution build green with 0 warnings/0 errors; `dotnet test` green (2 passed). No MainWindow behavior moved in this slice.

**MainWindow carve — slice 1 safety pilot completed (Codex/GPT, 2026-07-04):** Extracted `Features/Safety/SafetyPanel.cs` partial methods into ordinary class `Features/Safety/SafetyController.cs`; deleted the old `MainWindow` partial safety file instead of leaving forwarding shells. Dependencies are constructor-injected: `Func<string, Task<JsonDocument>>` GET, `Func<string, object, Task<JsonDocument>>` POST, `Func<string>` API base, `ChatWorkspaceView`, `ObservableCollection<EventViewModel>` for the safety history list, and `PromptText` callback for hard-stop resume confirmation. Safety-owned state (`lastSafetyActive`, `lastSafetyMode`) moved into the controller; shared UI collection `_safetyEvents` remains in the shell state until the later state split. Call sites now invoke `_safetyController.LoadAsync()` / `_safetyController.SetStopAsync(...)` directly from refresh/diagnostics/bootstrap. Added `SafetyControllerTests` for JSON helper behavior; no WPF window is instantiated. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 8 passed. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 1 modules pilot completed (Codex/GPT, 2026-07-04):** Extracted `Features/Modules/ModuleManagementPanel.cs` into ordinary `Features/Modules/ModuleManagementController.cs` and deleted the old `MainWindow` partial file. This slice intentionally moved only the module summary/navigation/action-text surface, not the `/desktop/module-management` load/render loop still colocated in `DiagnosticsModuleRuntime.cs`. Dependencies are constructor-injected: `ManagementPanelsView`, the module action collection, and `OpenManagementPage` callback. Diagnostics rendering now calls `ModuleManagementController.BuildSummary/BuildPortfolioText/BuildRiskText/BuildGovernanceText/ModuleLabel`; selection handlers and global search call `_moduleManagementController.UpdateActionText()`; module open buttons call `_moduleManagementController.OpenSelectedModule/OpenSelectedAction()` directly. Added `ModuleManagementControllerTests` for route mapping, status labels, and summary text. Verification passed locally after restore: `dotnet restore SpiritKinAI.sln`; `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 22 passed. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 1 search pilot completed (Codex/GPT, 2026-07-04):** Extracted `Features/Search/SearchManagementPanel.cs` into ordinary `Features/Search/SearchManagementController.cs` and deleted the old `MainWindow` partial file. Dependencies are constructor-injected: `ManagementPanelsView`, the search gap/model/job collections, GET/POST delegates, API base provider, and refresh callbacks for Agent Management + Module Management. Call sites now use `_searchManagementController.LoadAsync()`, `SaveRuntimeConfigAsync()`, and `IndexUnindexedKnowledgeAsync()` directly from bootstrap, knowledge-base/source actions, and navigation activation. Added `SearchManagementControllerTests` covering runtime status text, including explicit hashing/token-overlap degradation language. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 23 passed. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 1 MCP pilot completed (Codex/GPT, 2026-07-04):** Extracted `Features/Mcp/McpManagementPanel.cs` into ordinary `Features/Mcp/McpManagementController.cs` and deleted the old `MainWindow` partial file. Dependencies are constructor-injected: `ManagementPanelsView`, MCP server/tool/audit collections, GET/POST delegates, API base provider, JSON options, Module Management refresh callback, destructive-confirmation callback, and the existing `_rendering` get/set callbacks for selection-change suppression. Bootstrap and navigation now call `_mcpManagementController` directly for load/new/save/approve/reject/enable/disable/delete/selection-change/page activation. Added `McpManagementControllerTests` for policy text rendering. Verification passed locally after restore: `dotnet restore SpiritKinAI.sln`; `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 25 passed. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 1 evolution pilot completed (Codex/GPT, 2026-07-04):** Extracted Evolution-specific logic from `Features/Evolution/EvolutionPanel.cs` into ordinary `Features/Evolution/EvolutionController.cs`. Dependencies are constructor-injected: `ManagementPanelsView`, five Evolution collections, GET/POST delegates, API base provider, and Skills refresh callback. Bootstrap, refresh, and navigation activation now call `_evolutionController` for load/export/build-package/enforce-ownership/ingest-paper/ingest-video/seed-templates/save-review-gate. The original `EvolutionPanel.cs` now contains only Learning/model-review handlers (`BuildReviewPromptAsync`, `RequestModelReviewAsync`, `RequestMultiModelReviewAsync`), which are intentionally left for the later Learning slice rather than moved into the Evolution controller. Added `EvolutionControllerTests` for summary text. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 26 passed. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 2 pilot state split completed (Codex/GPT, 2026-07-04):** Moved the five pilot clusters' list state out of `Features/Shell/MainWindowState.cs` and into their owning controllers: `SafetyController.Events`, `ModuleManagementController.Modules/Actions`, `SearchManagementController.Gaps/ModelCapabilities/KnowledgeJobs`, `McpManagementController.Servers/ToolMappings/AuditEvents`, and `EvolutionController.LoopSteps/Actions/AgentSkills/Artifacts/DomainSkills`. `MainWindow` no longer stores or constructor-passes these collections; Bootstrap binds ItemsSource directly to the controller-owned collections. Remaining cross-panel readers (`DiagnosticsModuleRuntime`, global search, navigation lazy-load checks) now read through the controllers. No behavior changes intended; this is ownership cleanup for the later Shell/Bootstrap slice. Verification passed locally: `dotnet restore SpiritKinAI.sln`; `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 26 passed. `git diff --check` reports only existing CRLF normalization warnings. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 3 workflows completed (Codex/GPT, 2026-07-04):** Extracted the Workflow cluster into ordinary `Features/Workflows/WorkflowController.cs` plus its existing workflow partials converted from `MainWindow` partials to `WorkflowController` partials. Workflow-owned state moved out of `Features/Shell/MainWindowState.cs`: definitions, graph nodes/edges, swimlanes, runs, run-node/task progress lists, editor nodes/templates/dependencies, versions, snapshot/active-run fields, graph interaction state, undo/redo stacks, and zoom state. Bootstrap now binds workflow ItemsSource and commands through `_workflowController`; cross-feature callers route through controller APIs for global search, shell keyboard actions, trace rendering, Android lifecycle workflow action, interaction-template actions, refresh-all, and workflow lazy-load navigation. Added `WorkflowControllerTests` for summary rendering and cycle detection. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 29 passed. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 4 Agents sub-slice completed (Codex/GPT, 2026-07-04):** Converted the `Features/Agents/*` cluster into ordinary `AgentsController` partials and deleted direct agent-owned state from `MainWindowState`: agents, external assistants, adapters, knowledge bases/sources, route profiles, remote targets, recommendations, the last agent-management snapshot, remote-export path, and external-assistant process/CTS. The controller receives only narrow dependencies: shell/chat views, GET/POST/API delegates, module/search refresh callbacks, confirmation callbacks, workspace/runtime environment callbacks, style lookup, JSON options, root path, remote-worker port, read-only assist-model/skill collections, and rendering get/set callbacks. Bootstrap, composer mentions, global search, skills owner selection, desktop refresh, shell shutdown, and cross-panel selection now call `_agentsController` directly. `AgentsController.cs` was split into small partials (`133/356/76` lines) to avoid creating a new god file. Added `AgentsControllerTests` for ID generation, remote-export ID sanitization, error response surfacing, and distribution-summary rendering. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 33 passed. Slice 4 is not complete yet: Composer, Learning, MobileManagement, Workbench, Services, and Workspace still need the same extraction pass. Manual desktop panel smoke was not run in this environment.

**MainWindow carve — slice 4 Composer sub-slice completed (Codex/GPT, 2026-07-04):** Converted the `Features/Composer/*` cluster into ordinary `ComposerController` partials. Composer-owned attachment queues, mention trigger index, setting keys, model/attachment records, assistant work timing, trace metadata parsing, composer selector state, and prompt/mention/menu handlers moved out of `MainWindow`. Runtime, collaboration, workbench, learning model sync, shell menu, and interaction-template call sites now use `_composerController` APIs for command metadata, attachment payloads, assistant work steps, collaboration composer mode, mention normalization, TTS setting, and composer selector rendering. `TraceMeta`, `GitCommandResult`, and `CollaborationParticipantOption` were promoted to small internal shared records because collaboration/workbench still legitimately consume them. `AssistantWorkPanel.cs` was split by moving static work-event description/trace helpers to `AssistantWorkDescriptions.cs`; Composer files now stay under 337 lines. Added `ComposerControllerTests` for mention alias normalization, execution work-step keying, and trace metadata extraction. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 38 passed. Slice 4 is still in progress: Learning, MobileManagement, Workbench, Services, and Workspace remain. Manual desktop composer smoke was not run in this environment.

## Recent 3D Avatar Context

Recent local work made the Three.js avatar behavior more stateful and closer to desktop use:

- `frontend/avatar_3d.html`
  - Action queue: one action completes before the next starts.
  - Walk transitions pivot before stepping; the avatar no longer shuffles legs during a turn.
  - Left mouse rotate and wheel zoom are disabled for model observation.
  - The model floor/platform is hidden.
  - The canvas aligns to the background room by background anchor.
  - Forward/back/left/right locomotion persists across refreshes.
  - Forward depth now has a larger usable range; backward depth is constrained.
  - Movement boundary messages are explicit instead of looking like button failure.
  - Initial camera fit uses the neutral model body, not the currently moved world position, so refresh no longer visually hides boundary state.
  - The right control panel can be shown/hidden; in desktop `embed=1` mode it is hidden.

- `frontend/models/spirit3d/manifest.json`
  - Default initial locomotion is around `x=0, z=0.2, yaw=0`.
  - Walk bounds and visual depth scaling are tuned for the current room background.

- `backend/app/static_frontend_server.py`
  - Adds `/avatar-state/locomotion` GET/POST to persist 3D avatar position in `runtime/avatar_locomotion_state.json`.
  - Keeps no-cache headers for frontend development resources.

- `desktop/SpiritKinDesktop/Features/Workspace/LocalServiceRuntime.cs`
  - Desktop now starts `backend.app.static_frontend_server` instead of plain `python -m http.server`.
  - Desktop frontend health check requires both `avatar_3d.html` and `/avatar-state/locomotion`.
  - This keeps WebView and external browser behavior aligned for avatar position persistence.

## 3D Avatar Verification Commands

Use these when changing avatar behavior:

```powershell
# Check module script syntax extracted from avatar_3d.html
$html = Get-Content -LiteralPath 'frontend/avatar_3d.html' -Raw
$m = [regex]::Match($html, '<script type="module">([\s\S]*?)</script>')
if (-not $m.Success) { throw 'module script not found' }
$tmp = Join-Path $env:TEMP 'avatar_3d_module_check.mjs'
Set-Content -LiteralPath $tmp -Value $m.Groups[1].Value -Encoding UTF8
node --check $tmp

# Check manifest JSON
node -e "JSON.parse(require('fs').readFileSync('frontend/models/spirit3d/manifest.json','utf8')); console.log('manifest ok')"

# Check desktop build after service or WebView changes
dotnet build desktop/SpiritKinDesktop/SpiritKinDesktop.csproj
```

For browser checks on this Windows workspace, Playwright CLI has been used through:

```powershell
E:\Node\npx.cmd --yes --package @playwright/cli playwright-cli open --browser msedge "http://127.0.0.1:8787/avatar_3d.html?config=models/spirit3d/manifest.json"
```

## Current High-Value Improvement Areas

- Desktop/Web parity audit: ensure every management feature either has WPF parity or a documented web-only fallback.
- Avatar/runtime task narration: make all Workflow, SkillRunner, Android Bridge, Remote Worker, and desktop project runner producers emit richer structured progress instead of relying on browser heuristics.
- Model governance: run real local scheduler benchmarks against LM Studio/Ollama/vLLM outputs and wire results into BrainRouter promotion.
- Runtime closure: add more Context write appliers only after ownership, rollback, and module-state migration rules are clear. Current applied paths are `/context/policy`, `/project/overview/proposal`, `/collaboration/message`, `/collaboration/decision`, and `/collaboration/review`.
- Worker runtime: validate production Browser worker processes beyond the existing LocalPC/Remote browser binding path, and keep extending governed executor patterns only where there is a real execution backend plus tests.
- Code/UI Jury: add richer WPF/web review panels, screenshot capture, reviewer budget controls, and PR metadata import.
- Skill acquisition: complete signed package/source verification, Harness replay gates, rollback, and scheduled discovery under explicit policy.
- Remote/Android workers: real-device validation, stronger signed command/package policy, and worker revocation flows.
- Knowledge/RAG: incremental external source sync, conflict UI, Git/web/MCP source kinds, and persistent vector DB only if KB scale requires it.
- Security hardening before LAN/public exposure: replace permissive CORS, require token/Tailscale/HTTPS outside localhost, protect secrets in OS credential storage, and keep external CLIs review-only by default.

## Collaboration Instructions For Models

- Start with findings and risks when reviewing code. Do not bury bugs behind summaries.
- Cite local files and exact lines when possible.
- Prefer focused patches over broad rewrites.
- Preserve existing architecture boundaries unless the task explicitly asks for redesign.
- For UI or avatar work, verify with browser automation or screenshots when feasible.
- For desktop changes, run `dotnet build desktop/SpiritKinDesktop/SpiritKinDesktop.csproj`.
- For backend changes, run the narrow relevant unit tests first, then broaden if shared contracts changed.
- Do not recommend adding a new framework unless it removes concrete complexity or matches an existing planned adapter boundary.
- Do not treat a model recommendation as current without verification; model catalogs and provider capabilities change quickly.

## Open Questions For Future Review

- Should avatar locomotion state be per-session/project rather than a single global runtime file?
- Should the desktop avatar WebView have a first-class reset-position button outside the hidden control panel?
- Should `runtime/avatar_locomotion_state.json` move under `state/` for consistency with other persisted runtime state?
- How should browser/WebView localStorage state be reconciled with server-side state across multiple open clients?
- Should external model reviewers consume this file through the Knowledge/RAG system automatically when project context is requested?

**MainWindow carve — slice 4 Learning sub-slice completed (Codex/GPT, 2026-07-04):** Converted the `Features/Learning/*` cluster into ordinary `LearningController` partials and moved Learning-owned state out of `MainWindowState`: assist models, provider definitions, provider status list, provider-selection sync flag, and Ollama/LM Studio process handles. The model-review handlers that were intentionally left in `Features/Evolution/EvolutionPanel.cs` during slice 1 are now owned by `Features/Learning/ModelReviewActions.cs`; the old EvolutionPanel partial was deleted instead of kept as a forwarding shell. Bootstrap, refresh-all/runtime refresh, global search, model list selection, keyboard delete handling, and provider/model buttons now call `_learningController` directly. Agents and Composer receive the same `LearningController.AssistModels` collection as a read-only dependency, so model registry ownership is centralized while cross-cluster consumers remain narrow. The controller depends on shell view, HTTP client, GET/POST/API delegates, active session callback, destructive-confirmation callback, owner window callback for folder dialogs, and two Composer model-selection callbacks. Added `LearningControllerTests` for learning status summary rendering, provider model-name parsing, and stable local model IDs. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 42 passed. Manual desktop Learning/Models panel smoke was not run in this environment. Slice 4 remains in progress: MobileManagement, Workbench, Services, and Workspace still need extraction.


**MainWindow carve — slice 4 MobileManagement sub-slice completed (Codex/GPT, 2026-07-04):** Converted the `Features/MobileManagement/*` cluster into ordinary `MobileManagementController` partials. The cluster has no persistent collection state, but its API calls, mobile action payload builders, Android/iOS binding copy/open helpers, state-maintenance actions, and workspace-device selection handler are now owned by the controller instead of `MainWindow`. Dependencies are narrow constructor inputs: `WorkbenchShellView`, GET/POST/API delegates, Module Management refresh callback, `WorkflowController` for Android lifecycle acceptance workflow start, and `rootDir` for workflow inputs. Bootstrap and right-nav lazy loading now call `_mobileManagementController` directly. The former 862-line mobile panel file was split into action, workspace, text-helper, binding, security, permission, and state-maintenance partials; all MobileManagement files are now <=351 lines. Added `MobileManagementControllerTests` for network scope labels, Android worker summary rendering, and mobile security warnings. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 47 passed. Manual mobile panel/device smoke was not run in this environment. Slice 4 remains in progress: Workbench, Services, and Workspace still need extraction.

**MainWindow carve — slice 4 Workbench sub-slice completed (Codex/GPT, 2026-07-04):** Converted the `Features/Workbench/*` cluster into ordinary `WorkbenchController` partials and moved Workbench-owned state out of `MainWindowState`: workbench progress/source lists, Git change list, terminal session/reader state, Git selection/loading/cache fields, panel collapsed state, and last-known branch. MainWindow now constructs Workbench before Composer and passes Workbench Git APIs into Composer instead of exposing MainWindow Git helpers. Runtime command send, state rendering, Navigation diagnostic/project launch actions, Operations sync status, shell menus, and workspace layout now call `_workbenchController` or `WorkbenchController` static process helpers directly. The controller depends on the sidebar/shell/chat/terminal views, terminal row, root path, state/session/workspace/runtime callbacks, API/frontend/command URL callbacks, frontend service starter, save/confirmation/prompt/menu callbacks, dispatcher provider, and late-bound Composer status callbacks to avoid a constructor cycle. Added `WorkbenchControllerTests` for Git path/numstat/counter parsing, duration formatting, and terminal input-key detection. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 62 passed. Manual Workbench/terminal/Git UI smoke was not run in this environment. Slice 4 remains in progress: Services and Workspace still need extraction.

**MainWindow carve — slice 4 Services sub-slice completed (Codex/GPT, 2026-07-04):** Converted the `Features/Services/*` cluster into ordinary `ServicesController` partials and moved service-owned state out of `MainWindowState`: service list, service-port list, service action log, pending port restart IDs, migration text, pending client URLs, and command-gateway restart flag. Bootstrap now binds service ItemsSource through `_servicesController`; service buttons from interaction templates, refresh/service-port/profile buttons, runtime refresh, daily navigation targeting, global search, and session-switch service reloads all call controller APIs directly. Dependencies are constructor-injected: sidebar/shell views, GET/POST/API delegates, diagnostics/logs/daily refresh callbacks, active session/project callbacks, project workspace resolver, active workspace callback, confirmation callbacks, and WebSocket restart callback. Added shared `JsonResponseHelpers` and used it for the new Services controller path instead of adding another `EnsureOkResponse` copy. Added `ServicesControllerTests` for project port profile ID normalization, profile lookup/matching, and shared readable error handling. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 68 passed. Manual Services panel/port restart smoke was not run in this environment. Slice 4 remains in progress: Workspace still needs extraction.

**MainWindow carve — slice 4 Workspace sub-slice completed (Codex/GPT, 2026-07-04):** Converted `Features/Workspace/*` into ordinary `WorkspaceController` partials and moved Workspace-owned state out of `MainWindowState`: WebSocket client/CTS/connected flag, workspace project context ID, quick-chat/session-filter/right-nav flags, avatar and web-preview floating windows/views, last avatar scope, frontend/event-bridge/command-gateway/remote-worker ports, and frontend directory ownership. Workspace now owns API/command/state/frontend/avatar URL construction, auth header application, local service startup/port resolution, active session/project/runtime profile resolution, workspace identity rendering, quick-chat/page navigation, avatar sync, and WebSocket event intake. MainWindow constructs Workspace first and passes its narrow APIs into Workbench, Services, Agents, Composer, Learning, and other existing controllers; a late callback is used for Workbench panel collapsed state to avoid a constructor cycle. Bootstrap event wiring now points directly at `_workspaceController` for workspace nav, session filter, quick commands, task/agent subnav, right-nav toggle, avatar/web preview, local service startup, and shutdown disposal. Added `WorkspaceControllerTests` for package-manager detection, workspace candidate extraction, port parsing, `.env` parsing, and runtime environment application; this also fixed two small pure-logic edge cases (full-width colon workspace labels before Windows drive colons, and invalid port strings leaving a nonzero `out` value). Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 78 passed. Manual Workspace/avatar/WebSocket/local-service smoke was not run in this environment. Slice 4 is complete; remaining slices are Context/Collaboration, Navigation, Runtime, and Shell/Bootstrap.

**MainWindow carve — slice 5 Context/Collaboration completed (Codex/GPT, 2026-07-04):** Converted `Features/Context/ContextOverviewPanel.cs`, `CollaborationPanel.cs`, and `CollaborationWorkerRuntime.cs` into ordinary `ContextController` partials. Context-owned state moved out of `MainWindowState`: context suggestions, project-overview proposals, collaboration tasks/messages/thread scopes/claims/decisions/reviews, participant alias directory, active thread/signature flags, collaboration sync timer, thread work-chain cache, and collaboration worker process tables. MainWindow now constructs `ContextController` after Workspace/MobileManagement and before Composer; Composer receives late-bound collaboration callbacks and the controller receives a late Composer callback to break the constructor cycle without a MainWindow reference. Runtime event replay, command redirect handling, refresh-all, Shell menus, sidebar thread actions, cross-panel lazy load, and collaboration worker shutdown now call `_contextController` directly. The controller depends on sidebar/shell/chat views, WorkspaceController, root path, JSON options, state/render/save callbacks, GET/POST delegates, active message render/layout callbacks, prompt callback, timeline collection, and Dispatcher. Added `ContextControllerTests` for collaboration thread key/id normalization and worker-agent alias normalization. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 91 passed. Manual Context/Collaboration panel smoke was not run in this environment. Remaining slices are Navigation, Runtime, and Shell/Bootstrap.

**MainWindow carve — slice 6 Navigation completed (Codex/GPT, 2026-07-04):** Converted `Features/Navigation/*` into ordinary `NavigationController` partials and moved navigation/dialog ownership out of `MainWindow`: app dialogs, sidebar/session/project/task selection, daily/log/diagnostic actions, quick commands, project/session/task editor actions, collaboration sidebar thread activation, and navigation-owned interaction-template handlers. Bootstrap, shell menus, global search, runtime project creation helpers, skills delete confirmation, and keyboard delete dispatch now call `_navigationController` directly instead of relying on old MainWindow handlers. `NavigationController` receives only narrow view/state/save/API callbacks plus late-bound controller providers for cross-panel navigation; deletion routing was moved to `NavigationDeletionActions.cs` and interaction-template routing to `NavigationInteractionTemplateActions.cs` instead of keeping forwarding shells. Added `NavigationControllerTests` for stable generated ID shape. Verification passed locally after rebuilding the test assembly: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` 94 passed. Manual Navigation/sidebar/dialog smoke was not run in this environment. Remaining slices are Runtime and Shell/Bootstrap.

**MainWindow carve — slice 7 Runtime completed (Codex/GPT, 2026-07-04):** Converted `Features/Runtime/*`, `Features/Operations/OperationsPanel.cs`, and message context actions into ordinary `RuntimeController` partials. Runtime-owned command send/retry/confirmation/TTS/render/state-save/event-apply/diagnostics/log/daily/sync/module-management state moved out of `MainWindowState`; `WorkspaceController` now receives late runtime callbacks after construction so it can still be the first controller initialized. Shared low-level helpers were moved under `DesktopRuntimeHelpers` through `Features/Common/*` instead of keeping them as `MainWindow` partial methods, which lets extracted controllers reuse JSON/runtime/ComboBox helper logic without inheritance. Added `RuntimeInteractionTemplateActions.cs` for message interaction-template dispatch and `RuntimeControllerTests` for confirmation-control text detection. As a cleanup discovered during the slice, `Features/Skills/SkillsPanel.cs` was also extracted into `SkillsController`, moving skill/source collections and skill editor actions out of `MainWindow`; Bootstrap/global search/navigation now call `_skillsController` directly. Verification passed locally with a rebuilt test assembly: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release` 105 passed. Manual Runtime/Skills desktop panel smoke was not run in this environment. Remaining slice is Shell/Bootstrap final cleanup.

**MainWindow carve — slice 8 Shell/Bootstrap completed (Codex/GPT, 2026-07-04):** Finished the final Shell cleanup and hit Fable's hard acceptance metric: `partial class MainWindow` is now 10 unique files, down from the original 126-file spread, and all remaining partials are Shell composition/window concerns (`MainWindow.xaml.cs`, `MainWindowState`, bootstrap wiring, interaction-template dispatch, keyboard/menu/sidebar/window/lifecycle smoke). Extracted the last non-window Shell helpers into ordinary controllers: `ShellInteractionController` owns menu styling plus text-edit context menus/actions, and `GlobalSearchController` owns the global search overlay, search scoring/tokenization, result collection, and navigation dispatch. Workbench, Composer, Navigation, Shell sidebar menus, title-bar menu events, and app dialogs now use the Shell interaction controller instead of `MainWindow` helper methods; the global search ItemsSource and events now bind through `_globalSearchController`. Added `ShellInteractionControllerTests` and `GlobalSearchControllerTests`. Note: `MainWindowBootstrap.cs` still centralizes event order as the composition root; wiring was not pushed back into business controllers because that would make the extracted controllers own Shell/UserControl lifecycle. Verification passed locally: `dotnet build SpiritKinAI.sln -c Release --no-restore` 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release` 110 passed. Manual desktop Shell/global-search/text-edit smoke was not run in this environment. MainWindow carve goal is complete.

**MainWindow carve — interim Claude review note for remaining slices (Claude Opus, 2026-07-04):** Slices 0-1 were reviewed line-by-line earlier; slice 2-4 final acceptance happens after GPT finishes. Two items for the remaining slices, please apply going forward rather than waiting for the final review round:

- **Issue A (rework, growing):** `EnsureOkResponse(JsonElement, string)` is now *defined* in 7 controllers (`AgentsController.Helpers.cs:334`, `EvolutionController.cs:282`, `LearningController.Helpers.cs:348`, `McpManagementController.cs:430`, `DesktopApiRuntime.cs:37`, `SafetyController.cs:132`, `SearchManagementController.cs:247`), and the JSON text-extraction helpers are similarly copied per controller. This violates carve plan rule 7 (no per-slice helper copies). `Features/Common/ComboBoxHelpers.cs` is the right pattern — please add a shared `Features/Common/JsonResponseHelpers.cs` (internal static) now and point new slices at it; converging the existing 7 copies can be one small cleanup sub-slice at the end.
- **Issue B (consistency):** `SearchManagementController.Render` dropped the `_rendering` re-entrancy guard while the MCP controller kept it. No observed behavior difference yet, but pick one convention for all controllers (keeping the guard is safer for ComboBox-driven re-entry).

**MainWindow carve — final acceptance (Claude / Fable 5, 2026-07-04):** Carve accepted; review item #4 (desktop side) is closed.

- **Hard metric verified independently:** `partial class MainWindow` is exactly 10 files, all Shell composition/window concerns. `MainWindowState.cs` re-audited: only shell-level fields remain (controller instances, session/project/message collections, the shared `_rendering` flag, sidebar collapse state, pending-deletion sets) — no domain state.
- **Re-verified on this machine:** `dotnet restore/build SpiritKinAI.sln -c Release` 0 warnings / 0 errors; `dotnet test` 110/110 passed. 19 controller test files cover every extracted controller.
- **Issue A — closed by Claude follow-up commit.** GPT created `Features/Common/JsonResponseHelpers.cs` and pointed the new slices (Context, Services) at it, but the 8 pre-existing local copies were never converged. Claude converged all 8 (Agents, Evolution, Safety, Mcp, Learning, DesktopApiRuntime, MobileManagement, Search) into one-line forwarders to the shared helper. Note: three of them (Agents/Learning/MobileManagement) used a different exception-message format (`"{actionLabel}: {error} {detail}"`); they now emit the shared format (`"{error}: {detail}"`, actionLabel as fallback). Only user-visible error text changes; the AgentsController test asserts substrings, not the full format, and still passes. Build/test re-run green after the change.
- **Issue B — closed as "no guard needed", verified.** The four Search panel ComboBoxes (`SearchWebProviderBox`, `SearchKnowledgeBackendBox`, `SearchEmbeddingProviderBox`, `SearchRerankerProviderBox`) have no event subscriptions anywhere in the codebase (XAML declares no handlers; only the controller reads/writes them), so a re-entrancy guard in `SearchManagementController.Render` would be dead code. MCP/Agents keep the shared guard because their lists do have selection handlers. Convention going forward: take the guard delegates only when the controller's controls have UI event subscriptions.
- Residual (accepted, not blocking): per-controller nested `JsonHelpers` read-helper copies still exist alongside the shared `JsonResponseHelpers`/`ComboBoxHelpers`; converging those is mechanical cleanup with many call sites and is left as ordinary refactoring debt, not tracked as a review item.

### 2026-07-04 Resolution Update (Claude / Fable 5)

- **#12 prompt resource layer — closed.** All instruction text sent to LLMs now lives in `backend/prompts/` (pure stdlib, `string.Template` so JSON-heavy bodies need no brace escaping): 4 domain-agent role prompts, execution retry, skill/jury/ecosystem review + skill-assist fallback, intent resolver, 3 ASR biasing variants, expression classifier — a keyed registry with `render_prompt`/`list_prompt_keys` plus `test_prompt_registry.py` (registry completeness, placeholder rendering, no-upward-imports). The 10 call sites keep their dynamic assembly and render byte-identical output. Scope note: the review's "124 files" figure was dominated by workflow/UI metadata text (`workflow_graph.py` ×149 are responsibility/label strings, not prompts); those are intentionally out of scope. Full suite 1215 passed.
- **#14 docs sprawl — closed.** 8 superseded docs moved to `docs/archive/` (codex_handoff, tmp_agent_handoff, codebase_map, data_flywheel_and_kb_policy, desktop_enterprise_architecture_plan, live2d_mobile_strategy, complete_agent_stack_roadmap, workflow_canvas notes) with history preserved; `docs/README.md` is the new purpose-grouped index; cross-references in project_dictionary/project_management_overview/this file and test fixtures updated. Hard-referenced and README-linked docs stayed put. Known residual: `landing_and_test_handoff.md` still overlaps this file's role as "first entry" — merge candidate for a later round.
- **#15 debt markers — closed.** `TODO(debt-#N)` markers now sit at the load-bearing sites: command_gateway/collaboration god modules (+ the duplex auto-reply gap gated on operator sign-off), AgentCluster carve remainder and its deferred app imports, the shrink-only layering allowlist, and the frontend no-build-system constraint (`realtime_bridge.js`). Grep `TODO(debt` for the ledger.
- **#16 binary/bundle audit — closed, no action needed.** Tracked binaries total ~3 MB and are *live runtime assets*, not stray references: `manifest.json` loads `reference/bangboo_pmx_glb_screen.glb` and `avatar_3d.html` uses `reference/spiritkin_home_room_bg.png`; the rest of `reference/` is already gitignored (`frontend/models/spirit3d/reference/*` with the runtime trio force-tracked). Git LFS was evaluated and rejected: 3 MB does not justify breaking plain clones/CI for collaborators without git-lfs. Both local `spiritkin-cloud-*.tar.gz` bundles were listed: they contain only `.env.cloud.example` with placeholder values — no `.env.cloud`, no secrets. `.env`/`.env.cloud` remain untracked and gitignored; only `.example` files are tracked.
- Still open from the 2026-07-03 review: only the frozen deferred-import inversion under #5 (app-registers-providers redesign, shrink-only allowlist in the meantime). #4 desktop side closed 2026-07-04 with the MainWindow carve final acceptance above; everything else from the 16-item list is closed.

### Bridge / Cloud / iOS Terminal Test Notes For Fable (Codex, 2026-07-04)

This is a factual handoff summary for Fable from the existing local tests, bridge docs, and the local Docker control-plane smoke run on 2026-07-04. It is not evidence of live Fable/Codex route-bus communication, and it does not mark unrun real-device or public cloud-VM checks as passed.

**Local bridge tests already covered by code:**

- Android endpoint auth defaults are closed: `test_android_endpoint_without_token_is_not_open_by_default` verifies no-token access is rejected unless explicit localhost bypass is enabled, and that bypass only applies to actual local requests.
- Android token handling is covered: configured `X-SpiritKin-Android-Token` succeeds, wrong token fails.
- Android command bridge is covered at unit level: command payload building, device status, app launch, missing app-name failure, command queue drain, command delivery/result state, safety-stop blocking, and runtime trajectory persistence for command results.
- Android APK lifecycle is covered: `/android/apk`, `/android/apk/manifest`, `/pairing`, and `/android/artifact/` routes are asserted; APK manifest exposes integrity, compatibility, rollback, release metadata, SHA-256, and promotion gate state; APK download is blocked until human approval and then returns the approved APK.
- iOS endpoint auth defaults are closed: `test_ios_endpoint_without_token_is_not_open_by_default` mirrors Android's no-token rejection and scoped localhost bypass behavior.
- iOS token handling is covered: both `X-SpiritKin-iOS-Token` and `?token=` query token paths are accepted when correct.
- iOS Shortcut/App Intent bridge is covered: shortcut query mapping, allowlisted action mapping, unsupported action rejection, shortcut output formatting, shortcut schema generation, App Intent schema generation, URL scheme generation, and action allowlist validation.
- iOS terminal/PWA surface is covered at unit level: `/ios/terminal.webmanifest`, `/ios/service-worker.js`, `/ios/apple-touch-icon.png`, standalone manifest fields, APK lifecycle buttons, scheduler benchmark action, Android command actions, and compact model-governance/module-management summaries.
- iOS control actions are covered for queueing Android commands, composing workflow definitions, starting workflow runs, and using compact cached snapshots.
- Realtime bridge is covered by `RealtimeEventHubTests`: runtime snapshots preserve recent events, keep latest avatar state per session even after event-history eviction, and reject malformed non-object messages.
- Desktop bridge contracts are covered in WPF tests: `DesktopContractSmokeTests` asserts the default realtime event bridge port is `8765`; `WorkbenchControllerTests` covers terminal input-key detection and terminal helper parsing; `MobileManagementControllerTests` covers mobile network-scope labels, Android worker summary text, and mobile security warning rendering.

**Known bridge/cloud addresses and endpoints from current docs:**

- Local Android receiver endpoint: `http://127.0.0.1:8791/android/link`; health check: `http://127.0.0.1:8791/android/health`.
- Local iOS/Web terminal: `http://127.0.0.1:8791/ios/terminal`.
- Current documented Tailscale receiver address for this machine: `http://100.83.63.91:8791/android/link`. The docs also say the app may accept `http://100.83.63.91:8791/android` and derive the full link route.
- Old `8765/link` receiver is intentionally no longer the Android link receiver because `8765` is reserved for the runtime realtime event bridge.
- Cloud production shape is documented as `https://<control-plane-host>/...` behind Caddy. Raw `8791` and MinIO `9001` should stay private; public access should go through HTTPS Caddy routes such as `/android/health`, `/ios/terminal`, `/ios/control/pairing`, `/android/apk`, `/worker/package/manifest`, and `/mobile/artifacts`.
- Cloud smoke checklist expects `CADDY_HOST`, `SPIRITKIN_MANAGEMENT_TOKEN`, `SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET`, and S3/MinIO settings in `.env.cloud`; direct `control-plane` port binding should stay `127.0.0.1:8791` unless intentionally testing LAN exposure. The local `.env.cloud` shape checked on 2026-07-04 uses placeholder `CADDY_HOST=control.example.com` and `CONTROL_PLANE_DIRECT_BIND=127.0.0.1:8791`; no management token, signing secret, pairing token, or worker token is recorded here.

**Local Docker owner-only cloud/control-plane smoke actually run on 2026-07-04:**

- Stack command: `docker compose --env-file .env.cloud up -d --build`; the control-plane container reached Docker health status `healthy`, with direct binding `127.0.0.1:8791->8791/tcp`.
- Health check: `GET http://127.0.0.1:8791/android/health` returned `{"ok":true,"service":"spiritkin-control-plane","production_mode":true}`.
- iOS/Web control surfaces: `GET http://127.0.0.1:8791/ios/terminal` returned HTTP 200 and contained the `account-console` UI; `GET http://127.0.0.1:8791/ios/control` also returned HTTP 200 and contained `account-console`.
- Worker package bridge: `GET http://127.0.0.1:8791/worker/package/manifest` returned `ok:true` with `worker_package.package=spiritkin-control-plane-worker` and `worker_package.serving_validation.status=ok`.
- Account/workspace/remote worker bridge smoke used non-secret IDs `acct-smoke-1783146796`, `tenant-smoke-1783146796`, and `worker-smoke-1783146796`: account creation, workspace registration, remote-worker pairing payload generation, one-shot local `scripts/control_plane_worker.py --once`, heartbeat visibility, and remote-worker listing all completed.
- Persistence smoke: after another `docker compose --env-file .env.cloud up -d --build` without `-v`, health stayed OK and the smoke account/workspace/worker records were still visible in the Docker volume state.
- Current live local stack recheck in this session: `docker compose --env-file .env.cloud ps` still showed `control-plane` healthy, Caddy/MinIO running, `GET /android/health` OK, `GET /ios/terminal` 200, and `GET /ios/control` 200.
- PWA asset sync gap closed by Codex on 2026-07-05: the script-backed cloud control-plane endpoint now serves `/ios/terminal.webmanifest`, `/ios/service-worker.js`, `/ios/icon.svg`, and `/ios/apple-touch-icon.png`; `/ios/terminal` also links the manifest/icons and registers the service worker. Verified in the rebuilt local Docker control-plane: all four asset routes returned HTTP 200 after `docker compose --env-file .env.cloud up -d --build control-plane`.

**Manual / smoke procedures documented but not fully re-run against real devices/public cloud in this note:**

- `docs/cloud_deploy_smoke_test.md` defines the owner-only cloud VM validation path: compose config/build/up, health checks, owner control access, iOS/Web terminal pairing, Android pairing, screenshot artifact smoke, artifact upload/download/cleanup through MinIO, worker package manifest, remote worker pairing, firewall exposure checks, and restart persistence.
- `docs/mobile_link_bridge.md` defines the Android bridge local flow: start `scripts\mobile_link_receiver.py`, build/install `mobile-link-bridge.apk`, pair Android to a workspace, forward PDD links, upload artifacts, drain Android commands, return command results, approve APK promotion, and use the iOS/Web controller for Android/Worker pairing.
- `docs/ios_native_terminal.md` states the current no-Mac delivery path is the installable PWA at `/ios/terminal`; native SwiftUI source under `ios/SpiritKinTerminal/` is preserved but not accepted as complete until built/signed on macOS/Xcode and tested on a real iPhone.

**Open validation gaps Fable should keep visible:**

- No current evidence here of a fresh public cloud-VM end-to-end run against a real `https://<control-plane-host>` address. The 2026-07-04 pass was local Docker owner-only control-plane smoke on `127.0.0.1:8791`, not a DNS/TLS/firewall validation.
- No current evidence here of native iOS app compile/sign/run, because this Windows machine has no Xcode. PWA/Shortcuts paths are test-covered; native iPhone acceptance still requires Mac/cloud-Mac/VMware macOS with Xcode plus real-device networking.
- Android bridge has broad unit coverage and detailed APK history, but real-device acceptance still depends on the phone being paired, reachable over Tailscale/LAN, Accessibility/MediaProjection permissions being granted, and PDD selector tuning from uploaded UI snapshots.
- Realtime event bridge is unit-tested at the event hub/contract level; it should still be manually smoke-tested through the desktop WebSocket/WebView path when changing bridge ports or startup wiring.

### Ecommerce SaaS Foundation Progress For Fable (Codex, 2026-07-04)

Foundation plan source: `docs/ecommerce_saas_foundation_plan.md`.

- Slices 0-2 are implemented in code: account entity/migration, account quotas and metered scrape usage, management actions, and account-scoped control-plane authorization. Main touched files: `scripts/control_plane_store.py`, `scripts/mobile_link_receiver.py`, `backend/app/mobile_management.py`, `backend/mobile/ios_endpoint.py`.
- Slice 3 is implemented in code: remote worker result whitelist/redaction, control-plane sensitive payload rejection, artifact upload redline, and doc boundary update. Main touched files: `backend/security/sensitive_payload.py`, `scripts/control_plane_worker.py`, `scripts/control_plane_store.py`, `backend/mobile/artifact_store.py`, `docs/mobile_link_bridge.md`.
- Slice 4 is implemented in code: `account_console` tokens are account-scoped through snapshot/action/pairing gates; `/ios/terminal` renders "我的账户" with workspace, worker, artifact/quota usage and self-service Worker pairing; desktop Mobile Management receives and displays the same account summary; `account_console` is explicitly blocked from `workflow.graph.*` until the Blueprint-to-control-plane metering bridge exists.
- Slice 5 is implemented in code: Remote Worker package example config, `setup-worker.ps1`, and `install-worker-gui.ps1` include account/workspace/pairing/local-proxy guidance; worker config persists account/local proxy locally; heartbeat exposes only `account_id` and `proxy_configured`; local CLI/LangGraph/CrewAI subprocesses receive proxy env vars without uploading browser profiles, cookies, store sessions, or proxy URLs to the control plane.
- Slice 6 is implemented as release/runbook plumbing: `docs/cloud_update_release_checklist.md` covers Worker self-update via `check_and_apply_update`, APK promotion via `approve_android_apk_release`, control pages served directly by the control plane, and `docker compose --env-file .env.cloud up -d --build` restart/persistence rules.
- Verification run for the foundation scope: `python -m unittest tests.test_control_plane_worker tests.test_mobile_link_receiver tests.test_control_plane_store -v` -> 158 passed; `python -m py_compile backend/app/mobile_management.py scripts/control_plane_worker.py scripts/mobile_link_receiver.py scripts/control_plane_store.py` -> passed; `dotnet build SpiritKinAI.sln -c Release --no-restore` -> 0 warnings/0 errors; `dotnet test SpiritKinAI.sln -c Release --no-build` -> 112 passed; `python -m ruff check .` -> passed; `python -m pytest -q` -> 1236 passed, 8 subtests passed.
- Local Docker owner-only cloud stack smoke also passed: compose up/build, health, `/ios/terminal`, `/ios/control`, worker package manifest, account/workspace creation, remote worker pairing/heartbeat, and persistence after redeploy were verified on `127.0.0.1:8791`.
- Boundary: public DNS/TLS cloud VM smoke, real Android bridge device validation, and native iOS build/sign/run are still not claimed as passed here.

## 2026-07-05 协作 UX 验证与分工清单 (Claude / Fable 5)

面向"多模型协作对话"第五轮实测反馈。本节把当前状态拆成 **A. 已交付待人工验证**、**B. 待排查/待改动（含负责人建议）**、**C. 设计决策待拍板**，供分派。命令统一在仓库根执行；桌面必须经官方启动器起：`python scripts/start_desktop_console.py --token <TOKEN> --open-mode wpf`（GBK 控制台需 `PYTHONIOENCODING=utf-8`）。运行中编译隔离用 `-p:BaseOutputPath=bin_verify/`。基线：`python -m pytest backend/tests/unit/test_collaboration_worker_script.py backend/tests/unit/test_runtime_contracts.py -q` 81 passed；`dotnet build desktop/SpiritKinDesktop/SpiritKinDesktop.csproj` 0/0。

### A. 已交付，待人工实测验证

| # | 项目 | 落点 | 验证入口 |
| - | ---- | ---- | -------- |
| A1 | 按轮分卡（每模型每轮一张独立工作卡，不再共用一张链） | `CollaborationPanel.cs` `EnsureCollaborationWorkChain`；卡 Id `collab-work-{thread}-{agent}-{round}` | 同一线程内让一个模型连回 2 轮 → 出现两张卡，不叠进一张 |
| A2 | 本地主模型默认参与（新会话无需先 @，main_text 自动入会） | `CollaborationPanel.cs:2335` `EnsureDefaultSessionCollaborationAgent` | 新会话直接发无 @ 消息 → main_text 回复；opt-out 后不自动加回 |
| A3 | 双工 fan-out（模型互聊，开关默认开） | `collaboration_agent_worker.py` `post_reply`；后端 `collaboration_auto_reply_enabled()` 文件优先默认 True | 勾"模型互聊(双工)" → 两模型互相追问一轮后被每线程 6 轮上限止住 |
| A4 | 参与者移除（头部 chip × / 输入"移除 @xx"） | `SendCollaborationMessageFromComposerAsync` 命令拦截 + `DesktopRenderRuntime.cs` chips | 点 × 或输入移除 → 成员消失、之后无 @ 不再发给它 |
| A5 | worker 自愈（异常退出自动重启 + 桌面重启清孤儿） | `CollaborationWorkerRuntime.cs` `TryAutoRestartCollaborationWorker` / `CleanOrphanCollaborationWorkers` | 手动 kill 一个 worker → 自动重启；杀桌面再启 → 无孤儿（校验只剩 bridge/gateway/frontend） |
| A6 | 话题跟随（模型不再硬转项目话题） | `collaboration_agent_worker.py` build_prompt 引导段 | 讲笑话线程里模型继续接梗，不自行拐到工作 |

### B. 待排查 / 待改动

| # | 问题（用户原话） | 我的定位结论 | 建议改动 | 负责人建议 |
| - | ---------------- | ------------ | -------- | ---------- |
| B1 | "为什么看不到本地模型的思考链" | 数据层**不缺**：`state/agent_route_bus/worker_events.jsonl` 里 main_text 有 1242 条 `stream=reasoning` 事件，`agent_id=main_text` 不被 `IsHumanAgentId` 过滤，管道应可见。**最可能**：用户观察的会话早于 A2（main_text 未入会→worker 未起→无链），或卡片标题"思考"未署本地模型名，用户没认出。 | 实测复现：新会话发无 @ 消息，确认 main_text 卡出现且标"主 Agent"。若确实丢失→在卡头加参与者名徽标区分。 | 用户先实测复现，Claude 据现象定改法 |
| B2 | "思考链还是被截断，卡片长度不够" | **右侧横向截断（已修）**：协作命令/输出块用共享 `TerminalTextBox` 样式（`MainWindowResources.xaml:694`），该样式未设 `TextWrapping`，TextBox 默认 `NoWrap`→长行被右边界裁掉。真终端 `IntegratedTerminalPanelView.xaml:30` 自设 NoWrap+横向滚动不能动，故在协作卡 TextBox（`MainWindowInteractionTemplates.xaml:646`）本地覆盖 `TextWrapping=Wrap`。纵向另有 `TryMergeCollaborationStreamStep` 相邻合并被 lifecycle 打断的问题见 B3。 | 已改 XAML；待实测确认命令块长行换行不再被裁。 | Claude 已改，用户实测验收 |
| B3 | Q1 "codex 是一段思考链+一段 tools call，现在的 step 有什么作用没看懂" | 现状：每个 work_updated 事件=一个 DesktopWorkStep；reasoning 已尝试合并但被 B2 打断。step 粒度太碎，才有"40 条上限"焦虑。 | **已改（2026-07-06）**：`TryMergeCollaborationStreamStep`（CollaborationPanel.cs）重构为泳道归并——每张卡固定语义泳道（思考/调用/执行命令/编辑文件/回复/告警），事件并入同 agent 同泳道最近的未完结步骤，允许跨越中间其他泳道（reasoning 不再被 lifecycle 打断），step 数由泳道种类封顶，40 上限焦虑消除。单测 ContextControllerTests 新增 4 条。 | Claude 已改，用户实测验收 |
| B4 | Q4 "窗口右侧加话题锚点小符号，滚动太麻烦" | 现无会话导航；需右侧细导航条，锚到每条用户消息/每轮，点击 ScrollIntoView。 | **已改（2026-07-06）**：`ChatWorkspaceView.xaml` 消息区右侧新增 anchor rail（`TopicAnchorsPanelElement`，圆点=用户消息锚点，hover 显首句 ToolTip，点击 ScrollIntoView）；逻辑在 `Features/Shell/TopicAnchorNavigation.cs`，锚点≥2 才显示，最多保留最近 24 个。 | Claude 已改，用户验收交互 |

### C. Q3 调度方案 —— 已拍板（2026-07-05，用户授权 Claude 定）

采纳用户提案的简化版：**并行思考 + 发言队列 + 单轮重修**。队列第 N 位只依据前面已定稿的发言（1..N-1）重修一轮，不反复重想——总修订成本有界（= 参与模型数）。三个实现决策：

1. **冻结信号**：以每位发言的 `reply_posted` 生命周期事件为"定稿冻结"点。第 N 位开始重修时，1..N-1 必须都已 reply_posted；流式中的草稿不算定稿。
2. **队列排序**：按"首个可提交草稿完成时间"排位（谁先想好谁先说），不按首个 reasoning 到达时间——避免"起跑快但想得慢"的模型抢位。
3. **与双工的关系**：统一规则——**任何一条来件触发多于一个参与者回复时，该轮内套发言队列**（用户消息轮、双工扇出轮都一样）；轮与轮之间仍由现有 turn guard（每线程 6 轮）限次，两层互不替代。

实施归属：Claude（与 B3 泳道重构同批交付）。

**已实施（2026-07-06）**：`scripts/collaboration_agent_worker.py` 新增发言队列——
- 触发条件：同一条来件有 ≥2 个模型收件人（`speak_queue_peers`）；开关 `SPIRITKIN_COLLABORATION_SPEAK_QUEUE`（默认开）。
- 协调机制：草稿完成即在 `state/collaboration/speak_queue/<message_id>/<agent>.json` 登记（登记时间=排序键）；后位轮询等前位文件出现 `posted_at`（定稿=消息落库，post_reply 后立即标记，含被闸门拒收的情况），超时 `SPIRITKIN_COLLABORATION_SPEAK_QUEUE_TIMEOUT`（默认 180s）放行防死锁。
- 重修：后位取前位定稿（`list_messages` 按 `parent_message_id` 过滤），拼"发言队列重修"提示词重跑一次助手；重修失败按原稿发布。生命周期事件 `queue_wait`/`queue_revision`/`queue_revision_failed` 已接入桌面工作卡中文文案。
- 单测：`test_collaboration_worker_script.py` 新增 `CollaborationSpeakQueueTests` 7 条（88 passed）。

**改造（2026-07-08，用户裁决）**：草稿后重修的呈现被用户否决——桌面上表现为"发出的发言被回收覆盖重写"，辩论里发过的观点不能收回。改为**先排队后发言**：
- 排队时机前移到生成之前（`enter_speak_queue`/`enter_speak_turn`，`register_speak_queue_entry` 以 `enqueued_at` 定序）：进队即锁定发言顺序，谁先进队谁先发言。
- 后位等前位定稿后，把定稿并入生成来件（`build_speak_after_message`）**一次成稿**再流式输出——桌面上后发言者的气泡只在轮到它时出现一次，不再有二稿覆盖。
- 前位生成失败会 `withdraw_speak_queue_entry` 撤出登记，等待方把缺席席位视为已完成，不空等；超时（默认放宽到 600s，须覆盖前位整段生成）仍放行防死锁。互聊轮发言权锁跨整段生成持有，stale 接管阈值放宽到 900s。
- 生命周期 `queue_context`/`turn_context` 替代 `queue_revision`/`turn_revision`（旧值保留桌面文案兼容历史事件）。

**再改造（2026-07-08 v3，用户裁决"并行思考+队列定序"）**：阻塞版损失并行性——后位干等前位、思考也停了。改为**并行起草 + 修订成稿**（用户明确接受多一次修订调用的开销）：
- `enter_speak_queue` 非阻塞：进队即定序，返回 `(queue_dir, ahead)`；有前位时**立即后台起草**——`_background_draft` 标记使 token 流改道 reasoning 泳道（进工作卡思考，不上正文气泡）。
- `enter_speak_turn` 非阻塞：`try_acquire_speak_turn_lock` 抢不到发言权时返回 `deferred=True`，同样并行起草。
- 草稿完成后 `revise_with_finalized_replies`：queue 路径等前位定稿（`wait_speak_queue_ahead_posted`+`fetch_round_replies`）、turn 路径此时才阻塞抢锁+`fetch_thread_replies_since`；有定稿则 `build_speak_after_message(message, replies, draft=草稿)` 修订一次成稿，**修订稿才首次**流式上正文气泡——已上屏发言绝不回收；修订失败按草稿发布不吞回复。
- 顺带修气泡撑大抽动：本地推理模型把 `<think>` 混进 content 流，`StreamTokenBatcher` token 通道按累计全文实时分离（`extract_think_text`，对跨 token 撕裂标签免疫），think 内文改道 reasoning 泳道，气泡只收干净正文。

**四改（2026-07-08 v4，用户实测否决"按进队时间定序"）**：v3 按收件/进队时间排先后，收件顺序基本随机——实测快模型（DeepSeek 秒回）被排到慢的本地模型后面陪跑，思考链"卡住、闪烁一下才出现"。改为**谁先想完谁先发言**：
- `enter_speak_queue` 只登记不定序，返回 `(queue_dir, peers)`；发言顺序由 `claim_speak_slot` 在**首个可见正文 token 产出时**判定（写 `speaking_at`，先写者胜；已定稿的同伴恒算前位）。
- `SpeakSlot`（每轮每 agent 一个，线程安全幂等）：第一个想完的抢到席位**现场直播**（`queue_live` 事件，token 上正文气泡）；其余判定瞬间起 token 改道思考泳道（`queue_wait`），草稿完成后照走 `revise_with_finalized_replies` 修订发言。
- 全程无可见正文（非流式回退）时，生成结束后补一次 `claim()` 兜底判定。
- 净效果：快模型永远不用等慢模型，气泡即刻流式；慢模型后想完自动变后台草稿+修订，顺序天然合理。

## 2026-07-05 全项目功能验收与优化分工清单 (Claude / Fable 5)

范围：整个项目。分工原则（用户 2026-07-05 指定）：**双工实时 + 事件流/工作卡由 Claude 实施**；其余模块 Claude 出验收项与优化方案，由用户与其他模型执行。每项标注【测】=人工/脚本验收、【改】=需要改动。回归基线：`python -m pytest -q` 1236 passed；`dotnet test SpiritKinAI.sln -c Release` 110 passed；`python -m ruff check .` passed。

### P1 全栈启动与服务治理（用户【测】，问题回报后派其他模型【改】）
- 【测】官方启动器全栈起停：`python scripts/start_desktop_console.py --token <T> --open-mode wpf` → 网关 8788（无 token 401 即正常）、事件桥 8765、前端 8787 全部就绪；桌面显示已连接。
- 【测】服务面板：端口重启、服务状态刷新、命令网关重启后桌面自动重连。
- 【测】桌面异常退出后重启：无孤儿进程（bridge/gateway/frontend/worker 各查一遍）。

### P2 主聊天与执行回路（用户【测】；优化项派编程向模型【改】）
- 【测】普通问答、@agent 强制路由、桌面指令确认流（pending → 确认/取消 → 执行结果回显）。
- 【测】开发计划路径：请求被整理成计划卡并等待确认。
- 【改·优化方案】执行回路三缺口：① 子进程 stderr 已捕获但未回喂模型重试——在执行失败分支把 stderr 摘要拼进重试 prompt（落点 backend/orchestrator 执行重试路径）；② 无自动重试上限策略——加"最多 2 次、指数退避"；③ CLI 探测盲区——启动时探测外部 CLI 可用性并在面板显示，避免运行时才失败。
- 【测】音频/TTS：edge_tts 与本地 provider 切换后实际发声（backend/expression/edge_tts.py 近期有改动，需回归）。

### P3 学习/模型管理（用户【测】；其他模型【改】）
- 【测】provider 模型列表同步（ProviderModelSync 近期有改动）：新增/删除 provider 后 assist model 下拉一致。
- 【测】Ollama / LM Studio 启停按钮、模型加载状态回显。
- 【改·优化方案】模型目录时效：provider 能力/目录变化快，建议加"目录快照时间戳 + 手动刷新"提示，避免拿过期模型名调用失败。

### P4 3D Avatar（用户【测】，问题派视觉向模型【改】）
- 【测】按文档 "3D Avatar Verification Commands" 一节：模块脚本语法、manifest JSON、桌面 WebView 加载、骨骼/定位截图比对。
- 【测】桌面浮窗 avatar 与前端页面 avatar 状态同步。

### P5 移动/云链路（SaaS foundation，用户 + Codex【测】【改】）
- 【测】已声明未验边界（见 2026-07-04 Codex 记录）：公网 DNS/TLS 云 VM 冒烟、Android 真机 bridge、原生 iOS 构建/签名/运行——这三项就是本模块的验收清单。
- 【测】本地 owner-only 云栈回归：compose up、`/ios/terminal`、账户/配对/心跳、redeploy 持久化（Codex 已跑通一轮，换机复验）。
- 【测/改】2026-07-05 Codex 回归发现并修复脚本版云控 PWA 静态资产 404：补 `/ios/terminal.webmanifest`、`/ios/service-worker.js`、`/ios/icon.svg`、touch icon 路由，容器重建后均为 HTTP 200；`tests.test_mobile_link_receiver` 已覆盖。
- 【改·优化方案】remote worker 执行端仍缺（真机链路最后一环）：按 worker package 里 `setup-worker.ps1` 装到一台真机/VM，跑一条端到端任务（下发→执行→结果白名单回传）。

### P6 电商自动化（用户【测】；电商/RPA 向模型【改】）
- 【改·优化方案】无 API 平台走扩展/RPA 的三缺口：① 商品上架流程自动化（表单填充+图片上传）；② 图片处理管线（缩放/水印/主图合规检查）；③ 换 IP/代理轮换接入 RPA 会话。建议先做 ①，以现有 ecommerce_rpa_test 状态目录为基础扩。
- 【测】现有电商任务面板：任务创建→RPA 会话启动→结果回写。

### P7 协作双工 + 事件流（Claude【改】，本清单上节 A/B/C）
- 顺序：B2 已修（右侧截断）→ B3 已修（泳道重构）→ C 已实施（发言队列单轮重修）→ B4 已修（右侧锚点导航）→ B1 待用户复现后定。
- 每批交付均跑：worker 单测 81 基线 + dotnet build 0/0 + 官方启动器重启全栈。

### P8 回归与工具链（用户【测】）
- 【测】全量基线四连：`python -m pytest -q`、`dotnet build SpiritKinAI.sln -c Release`、`dotnet test SpiritKinAI.sln -c Release`、`python -m ruff check .`（pytest/ruff 在 Anaconda 环境；管道命令注意 exit code 陷阱）。
- 【测】git status 里未提交的大批改动（backend/desktop/config 20+ 文件）需要一次收口提交前的整体回归。

## 2026-07-06 Collaboration Fix Plan Progress For Fable 5 (Codex)

Source plan: `docs/collaboration_fix_and_maintenance_plan.md`. This is a code-and-test handoff note, not evidence of live Codex/Opus/Fable route-bus discussion.

**User-facing project/session bug fixed in this round:**

- New-project path reuse: WPF no longer auto-seeds `project_spiritkinai_workspace` from the current repo path when there are no projects. This prevents creating a "new" project under the existing workspace path after the previous project was deleted.
- Project deletion cleanup: deleting a project now removes project-scoped sessions from desktop state and marks associated collaboration threads deleted locally (`project-{project_id}` and `session-{session_id}`). The backend `delete_thread` action is called best-effort, and stale backend messages for deleted project/session/task threads are filtered out of the current collaboration scope.
- Verification: `dotnet test desktop/SpiritKinDesktop.Tests/SpiritKinDesktop.Tests.csproj -c Release -p:UseAppHost=false -p:BaseOutputPath=bin_verify/ --filter FullyQualifiedName~NavigationControllerTests` -> 6 passed before the broader desktop test run below.

**Plan implementation progress:**

- A3 event bridge auth tightened end-to-end: `backend/app/realtime_bridge.py` accepts `SPIRITKIN_DESKTOP_TOKEN`, `SPIRITKIN_API_TOKEN`, or `SPIRITKIN_MOBILE_TOKEN`; runtime/avatar/worker/smoke clients send a first `runtime.auth` frame; auth frames are ignored after subscribe instead of being rebroadcast as runtime events.
- Startup/token alignment: `scripts/start_desktop_console.py` and `scripts/start_realtime_panel.py` WebSocket health probes now send the bridge auth frame. Desktop launch state stores the generated session token for `--status` bridge probing. Realtime panel now generates a session token for local starts too, stores it in detached state, and appends it to mobile/Tailscale/public avatar and Live2D URLs.
- B1 workflow graph completion tightened: dataflow, required input checks, port compatibility, and structured branch conditions were already test-covered. This round added `workflow.graph.auto_advance_runs` / workflow-management `auto_advance_runs`: a bounded scan that advances runnable non-agent nodes, reconciles waiting Android steps, and fails stale claimed `agent_task` nodes after `agent_task_timeout_seconds` / `SPIRITKIN_WORKFLOW_AGENT_TASK_TIMEOUT_SECONDS` (default 600s). It is an explicit action/helper, not a new always-on background service.
- B3 dynamic MCP registration fixed: discovered stdio MCP tool mappings are registered into the adapter before ToolRegistry entries are generated. This closes the earlier "listed but not resolvable" dynamic-tool failure.
- B4 cleanup narrowed: config-backed real embedding is already wired through `config/config.yaml` and settings. Hashing embeddings remain explicit dev fallback only (`SPIRITKIN_ALLOW_HASHING_EMBEDDINGS=1`); search management now exposes `embedding_dev_fallback_allowed` so operators can distinguish real semantic retrieval from fallback. `build_training_command(trainer="peft")` now fails explicitly instead of returning a command for missing `backend.model.training.peft_lora_train`. Streaming ASR remains backlog and now correctly reports "backend adapter is not implemented" instead of returning fake transcripts.

**Verification run on 2026-07-06:**

- `python -m pytest backend/tests/unit/test_desktop_console_launcher.py backend/tests/unit/test_start_realtime_panel.py backend/tests/unit/test_realtime_bridge.py -q` -> 49 passed.
- `python -m pytest backend/tests/unit/test_collaboration_worker_script.py backend/tests/unit/test_runtime_contracts.py backend/tests/unit/test_mcp_management.py -q` -> 113 passed.
- `python -m pytest backend/tests/unit/test_training_workbench.py backend/tests/unit/test_command_gateway.py::CommandGatewayTests::test_desktop_search_management_reports_rag_gaps_and_runtime_config backend/tests/unit/test_knowledge_base_management.py::KnowledgeBaseManagementTests::test_search_management_can_index_unindexed_knowledge_bases backend/tests/unit/test_knowledge_base_management.py::KnowledgeBaseManagementTests::test_search_management_surfaces_knowledge_job_failures backend/tests/unit/test_tooling_and_remote.py::ToolingAndRemoteTests::test_build_embedding_provider_rejects_hashing_without_explicit_dev_flag backend/tests/unit/test_tooling_and_remote.py::ToolingAndRemoteTests::test_build_embedding_provider_allows_hashing_with_explicit_dev_flag -q` -> 14 passed.
- Larger plan-related Python group: `python -m pytest backend/tests/unit/test_collaboration_worker_script.py backend/tests/unit/test_command_gateway_collaboration.py backend/tests/unit/test_agent_cluster.py backend/tests/unit/test_local_pc_device.py backend/tests/unit/test_runtime_contracts.py backend/tests/unit/test_mcp_management.py backend/tests/unit/test_realtime_bridge.py backend/tests/unit/test_start_realtime_panel.py backend/tests/unit/test_desktop_console_launcher.py backend/tests/unit/test_training_workbench.py backend/tests/unit/test_workflow_graph.py -q` -> 350 passed, 8 subtests passed.
- `python -m compileall -q backend scripts` -> passed.
- `dotnet build desktop/SpiritKinDesktop/SpiritKinDesktop.csproj -c Release -p:UseAppHost=false -p:BaseOutputPath=bin_verify/` -> 0 warnings, 0 errors.
- `dotnet test desktop/SpiritKinDesktop.Tests/SpiritKinDesktop.Tests.csproj -c Release -p:UseAppHost=false -p:BaseOutputPath=bin_verify/` -> 120 passed.
- Local full-stack smoke: `python scripts/smoke_local_stack.py --startup-timeout 20 --event-timeout 20` -> 9 passed (`bridge_ws_handshake`, gateway health, frontend index, missing/wrong token rejection, command HTTP OK, deterministic reply, avatar fields, WS assistant message).

**Still not claimed as done:**

- The official WPF/manual UI smoke from the plan (`python scripts/start_desktop_console.py --token <t> --open-mode wpf`, then real UI checks for refill, duplex stop notice, ≥8-round persona stability, and no-token WS rejection through a live client) has not been run in this note. The local script smoke above covers services/auth/event loop but not WPF interaction behavior.
- Workflow graph B1 is code/test-covered for the requested semantics through explicit `auto_advance_runs`, but this is not yet a daemonized background loop. Do not claim a continuously running scheduler service unless a later slice wires it into a managed service lifecycle.
- Route-bus real worker remains dependent on configured external assistants and `scripts/collaboration_agent_worker.py`; `run_agent_route_bus_worker_once` is still a dry-run seed.
- Public cloud DNS/TLS, real Android bridge, native iOS build/sign/run, and real OpenClaw hardware validation remain outside this local verification pass.

## 2026-07-06 Collaboration Fix Addendum A4 Progress For Fable 5 (Codex)

Source addendum: `docs/collaboration_fix_and_maintenance_plan.md` section A4, "持续互聊 + 人工软打断".

**Implemented in code:**

- Turn guard default is now continuous duplex: `SPIRITKIN_COLLABORATION_TURN_CAP` defaults to `0`, and empty/`0` means unlimited model-to-model turns. Finite positive caps still work for explicit bounded sessions.
- A separate hard fuse was added: `SPIRITKIN_COLLABORATION_TURN_HARD_CAP` defaults to `40`, with `0` disabling it. The guard tracks `continuous_auto_turns`, returns structured `turn_hard_cap_reached`, and resets the continuous counter when a human posts/refills/changes cap.
- `pause_turns` was added as the soft-stop action. It marks the thread `awaiting_refill` with `blocked_reason=turn_paused`; existing worker preflight then skips the next model-to-model generation before any thought card starts.
- `set_thread_turn_cap` was added so the management panel can change the active thread cap immediately without worker restart. The old env write remains for newly created threads/processes.
- `refill_turns` remains compatible with finite old threads: when default cap is now unlimited but an existing thread has a finite cap, a no-amount refill extends by that existing cap instead of adding zero.
- Collaboration message persistence now calls `record_human_activity` for human-authored messages, so a human message clears manual pause/hard fuse state before the next model round.
- Worker policy-suppression lifecycle mapping now recognizes `turn_hard_cap_reached` and `turn_paused` in addition to `turn_cap_reached` and `auto_reply_disabled`.
- WPF management panel now displays `持续` for unlimited mode plus hard-fuse progress, accepts empty/`0` as continuous mode, applies cap changes to the current thread via `set_thread_turn_cap`, and exposes a "停止双工" soft-stop button via `pause_turns`.

**Verification run on 2026-07-06 after A4:**

- `python -m pytest backend/tests/unit/test_collaboration_turn_guard.py backend/tests/unit/test_collaboration_turn_guard_integration.py backend/tests/unit/test_collaboration_worker_script.py -q` -> 102 passed.
- `dotnet test desktop/SpiritKinDesktop.Tests/SpiritKinDesktop.Tests.csproj -c Release -p:UseAppHost=false -p:BaseOutputPath=bin_verify_a4/` -> 122 passed.
- `dotnet build desktop/SpiritKinDesktop/SpiritKinDesktop.csproj -c Release -p:UseAppHost=false -p:BaseOutputPath=bin_verify_a4_build/` -> 0 warnings, 0 errors.
- `python -m compileall -q backend scripts` -> passed.
- Temporary `bin_verify_a4*` output directories were removed after verification.

**Still needs manual/Fable validation:**

- Run the official WPF stack and confirm the UI behavior: `python scripts/start_desktop_console.py --token <t> --open-mode wpf`.
- In the management panel, verify cap box `0`/empty shows continuous duplex, finite cap applies immediately, and "停止双工" prevents the next model-to-model reply while allowing the current in-flight generation to finish.
- With `SPIRITKIN_COLLABORATION_TURN_HARD_CAP=3`, verify the fourth automatic model-to-model turn pauses with the hard-fuse notice, then a human message resumes the thread.

## 2026-07-07 Collaboration Streaming/Recovery Batch Progress For Fable 5 (Codex)

Source: `docs/collaboration_fix_and_maintenance_plan.md` sections A5/A6/F1-F6. This note records code/test completion only; WPF live multi-model behavior still needs Fable/manual verification.

**Confirmed already present before this Codex slice:**

- A5 work-card/reply pairing was already mostly implemented: work cards are keyed by `(thread, agent, parent_message_id)`, background thread events are projected into their owning session instead of only the active session, late work cards can anchor above an already-landed reply, and source work-card retention is only an in-memory cache because projected cards persist in the session timeline.
- F3 per-round work-card timing was already mostly implemented: each parent message opens a new source/projection card; `FinalizeCollaborationWorkChain` freezes `DurationSeconds`, while running cards compute duration from their own `CreatedAt`.
- F5 UI cleanup was already present: the duplicate management-panel duplex checkbox is gone, the single duplex switch/balance lives in the chat header, `turn_guard_status` is in the work-event ignore list, and the worker loop re-reads `collaboration_auto_reply_enabled()` each poll.

**Implemented in this Codex slice:**

- F1 streaming reply drafts: desktop now projects `stream=token` collaboration worker events into a normal assistant draft bubble keyed by `(thread, agent, parent_message_id)`. `stream=reasoning` still stays in the work card and continues to merge as a growing reasoning lane.
- F1 final replacement: worker replies now use predictable message IDs (`reply-{agent}-{parent_message_id}`), so the streaming draft bubble is created with the same final `collab-reply-*` ID from the start. When the authoritative collaboration reply lands, it updates the same timeline item instead of renaming IDs or forcing a timeline rebuild. The existing work-card `CreatedAt + 0.0005` pairing semantics are preserved.
- F1 failure fallback: `request_failed` lifecycle marks an existing streaming draft with a visible "生成中断" status rather than leaving an unlabelled half reply.
- A6 local-worker fairness: `scripts/collaboration_agent_worker.py` now sorts each poll batch so human-authored messages are processed before model-to-model continuations, then by `created_at`. This prevents continuous duplex debates from starving a new human turn on a single local model worker.
- A6 visibility: if a human message was not the first processable item in the batch, the worker emits a `queued` lifecycle event with `reason=human_priority_queue` before processing it.
- F6 worker recovery diagnostics: the WPF worker `Exited` path now uses a nonblocking dispatcher callback with staged log lines (`exit-dispatch start`, memory unregister, PID unregister, sync controls, auto-restart evaluated). Exceptions in control sync/disposal are logged and no longer prevent PID cleanup or `TryAutoRestartCollaborationWorker`.

**Verification run on 2026-07-07 after this slice:**

- `python -m pytest backend/tests/unit/test_collaboration_worker_script.py -q` -> 79 passed.
- `python -m pytest backend/tests/unit/test_collaboration_worker_script.py backend/tests/unit/test_runtime_contracts.py -q` -> 111 passed.
- `dotnet test desktop/SpiritKinDesktop.Tests/SpiritKinDesktop.Tests.csproj -c Release -p:UseAppHost=false -p:BaseOutputPath=bin_verify_f3_tests/` -> 123 passed.
- `dotnet build desktop/SpiritKinDesktop/SpiritKinDesktop.csproj -c Release -p:UseAppHost=false -p:BaseOutputPath=bin_verify_f3_build/` -> 0 warnings, 0 errors.
- `python -m compileall -q backend scripts` -> passed.
- Temporary `bin_verify_f*` directories were removed after verification.

**Still needs manual/Fable validation:**

- F1 live WPF streaming: in a real DS/Spirit/Codex collaboration run, verify token batches appear in the chat bubble before final `post_message`, reasoning grows inside the work card, final answer replaces the draft without duplication, and a forced request failure labels the draft as interrupted.
- A6 live fairness: while one session is in continuous duplex debate, send a new human message in another session to `main_text`; verify the human turn is processed before older model-to-model continuation messages.
- F6 live recovery: kill two worker processes together with `taskkill /F`; verify both logs contain the new `exit-dispatch ...` stages, both PID entries are cleared, and auto-restart occurs within the backoff window unless user-stopped/restart-limit conditions apply.
