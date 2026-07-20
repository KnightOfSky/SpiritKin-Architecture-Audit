# Runtime 1.0 Freeze Audit

Audit date: 2026-07-20  
Security-audit baseline: `b92c99be67f1`; directory ownership status includes the subsequent tested migration on this branch.
Reviewer: GPT-5.6 read-only adversarial audit plus repository test suites

The check marks in the product checklist mean "discussed and recommended". They do not prove implementation. This report uses these implementation states instead:

- `PASS`: implemented on a production path and covered by current tests.
- `PARTIAL`: a useful implementation exists, but the requested unified contract or production closure is incomplete.
- `FAIL`: the requested system-wide contract is missing or the frozen architecture is violated.

## Executive Decision

**Runtime 1.0 must not be declared frozen.**

The two validated P0 paths are closed: unfenced desktop Workflow execution is rejected, and a claimed migration cannot replay another Host's private lease. Migration now validates Checkpoint integrity, ownership, epoch, freshness, current Run state, and current Definition before changing the lease. Corrupt Host, Checkpoint, and World top-level state and non-object nested records fail closed.

Freeze remains blocked by incomplete durable-state schemas and recovery, physical directory violations, and missing unified Lifecycle, State Machine, Event Bus, and Provider interfaces.

## 24-Part Checklist

| Part | Status | Evidence and remaining gap |
| --- | --- | --- |
| 1. Runtime | `PARTIAL` | Host, Checkpoint, Migration, Context, audit trails, health/recovery slices and persistence stores exist. There is no single Runtime Transaction owner, full Persistence recovery/WAL/schema migration, or unified Scheduler/Governor contract. |
| 2. Agent | `PARTIAL` | Planner, policy, resources, state/memory, owner metadata, review and benchmark slices exist. They are not governed by one lifecycle/state-machine contract. |
| 3. Workflow | `PARTIAL` | Definition, designer APIs, runtime, versions, review, memory, mining, Checkpoint and migration-related continuity exist. Execution is now Host-fenced. Lifecycle and cross-store transaction semantics remain incomplete. |
| 4. Capability | `PARTIAL` | Capability Graph/Registry, resolver behavior, contracts and benchmark records exist. Builder/Growth has been moved under `backend/capability/growth/`; the unified Capability lifecycle and contract enforcement remain incomplete. |
| 5. Skill | `PARTIAL` | Registry, routing, runtime, context view, orchestration, evaluation, promotion and versions exist across `backend/skills` and app helpers. Contract validation and lifecycle enforcement are not universal. |
| 6. Worker | `PARTIAL` | WorkerPool plus Android, Browser, Python, Remote and local execution workers exist; health and scheduling are implemented. Desktop/iOS Host-versus-Worker ownership and common state machine/benchmark enforcement are not fully normalized. |
| 7. World Model | `PARTIAL` | Structured Observation and World State exist. ARKit is wired. Browser/Android/Desktop/Vision/Camera/OCR are named provider types but are not all implemented as equivalent Observation Providers. |
| 8. Vision | `PARTIAL` | The ARKit path follows Reality -> Observation -> World and rejects raw image/depth-map/point-cloud persistence. Other Vision paths are not yet unified behind the same provider contract. |
| 9. Evaluation | `PARTIAL` | A BenchmarkRuntime and several benchmark/report types exist. One execution harness does not yet enforce Model/Agent/Workflow/Skill/Worker/Vision/Runtime/E2E/Growth/Regression consistently. |
| 10. Learning | `PARTIAL` | Trajectories, datasets, review/eval data, training workbench, promotion and model lifecycle work exist. Physical ownership is split across `training`, `growth`, app and memory packages; Distillation remains a later model-lifecycle step. |
| 11. UI | `PASS` | Dashboard, Spirit home, shared design tokens/navigation, Runtime/Workflow monitoring, Review and Developer surfaces exist across desktop/iOS/PWA. Menus are grouped instead of adding every feature as a top-level item. Native iOS visual/device acceptance still requires Xcode and an iPhone. |
| 12. Provider | `FAIL` | Model provider configurations and several concrete providers exist, but there is no common Model/Tool/Worker/Vision/Storage Provider interface. An older Vision route still has concrete-model coupling. |
| 13. Review | `PARTIAL` | Architecture, Runtime/UI, human review and model-jury/cloud-review mechanisms exist. They are not exposed as one uniform Review contract. |
| 14. World Growth | `PARTIAL` | Observation updates World and Growth Runtime can mine trajectories/capability gaps. The complete Observation -> World Update -> Planner -> Action -> Reality loop is not one atomic continuously governed runtime. |
| 15. Three Hosts | `PARTIAL` | Desktop Host and iOS control/observation Host contracts exist; Cloud/Remote Host protocol exists. A continuously deployed Cloud Host is not part of this local delivery. Android remains a Worker. |
| 16. ARKit | `PARTIAL` | Native Swift provider emits structured observations and never uploads raw frames through this API. Windows validates source contracts only; camera, LiDAR, permission, power and real network behavior need Xcode/iPhone acceptance. |
| 17. Self Growth | `PARTIAL` | Builder behavior is governed, reviewed and non-activating by default, and now lives under Capability ownership at `backend/capability/growth/`. End-to-end promotion and lifecycle unification remain incomplete. |
| 18. Lifecycle | `FAIL` | No universal Draft -> Candidate -> Review -> Approved -> Stable -> Deprecated -> Archived implementation covers every runtime object. Current object-specific statuses differ. |
| 19. Metadata | `PARTIAL` | `RuntimeMetadata` normalizes many common fields, but the requested id/owner/version/status/risk/permission/benchmark/dependency/tags set is not mandatory for every object type. |
| 20. State Machine | `FAIL` | Workflow, Skill, Worker, Agent and Model use separate status strings and local transition logic. There is no shared transition validator/audit contract. |
| 21. Contract | `PARTIAL` | Workflow, Capability and Skill schemas include many input/output/resource/permission fields. Runtime validation is not uniformly applied at every execution boundary. |
| 22. Event Bus | `FAIL` | Realtime WebSocket events, route-bus JSONL and local event persistence exist, but there is no single typed publish/subscribe Event Bus replacing direct cross-module calls. |
| 23. Frozen directories | `PARTIAL` | The explicit `growth`, `scheduler`, `events`, `training`, and duplicate `eval` packages were migrated to `capability/growth`, `runtime/scheduler`, `runtime/events`, `model/training`, and `evaluation`; architecture tests prohibit those five legacy paths. Other historical backend packages still need a separate ownership map before the entire 13-directory freeze can pass. |
| 24. GPT-5.6 key audit | `PARTIAL` | Host and migration fencing are fixed. Checkpoint, Metadata, Contract, Benchmark and Observation are partial. Lifecycle, State Machine, Event Bus and Provider fail. Persistence remains partial. |

## Key Item Matrix

| Key item | Result |
| --- | --- |
| Runtime Host | `PASS` for exclusive Workflow execution/fencing |
| Runtime Checkpoint | `PARTIAL` |
| Runtime Migration | `PASS` for the audited lease-transfer safety properties |
| Runtime Persistence | `PARTIAL` |
| Lifecycle | `FAIL` |
| Metadata | `PARTIAL` |
| Contract | `PARTIAL` |
| State Machine | `FAIL` |
| Event Bus | `FAIL` |
| Benchmark Framework | `PARTIAL` |
| Provider Interface | `FAIL` |
| Observation Pipeline | `PARTIAL` |

## Security Closure

- Default ToolRegistry and desktop workflow management reject `run_next`, `run_node`, and `auto_advance_runs` without a Runtime Host-provided fenced store.
- The legacy command-gateway Workflow auto-advance loop is disabled.
- Migration prepare/request/claim validate Checkpoint checksum, workspace, source Host, source epoch, active/latest state, current Run freshness and current Definition digest before changing the lease.
- Replaying a claimed migration returns a private lease only while that exact migration still owns the current target lease and epoch.
- Host, Checkpoint and World state reject invalid JSON, schema versions, top-level types, collection types and non-object nested records.
- Remaining persistence work includes required-field schemas for every nested record, schema migration, backup/recovery or WAL, and transactions spanning Host, Checkpoint, Workflow, Observation and World stores.

## Verification

- Focused Runtime/security suite after the final fix: `89 passed`.
- Main repository suite: `198 passed, 4 subtests passed`.
- Full backend unit suite after directory ownership migration: `1559 passed, 19 subtests passed`.
- Directory/evaluation/growth/scheduler/training focused suite: `203 passed, 4 subtests passed`.
- iOS/realtime bridge suite: `49 passed`.
- Ruff and `git diff --check`: passed.
- Native iOS compile/install: not run on Windows; requires macOS/Xcode/signing and a real device or simulator.

## Freeze Entry Conditions

1. Finish mapping the remaining historical backend packages into the frozen ownership model; five explicit legacy packages have already moved and are protected by an architecture regression test.
2. Implement one lifecycle contract and one state-machine transition/audit contract for Workflow, Skill, Worker, Agent and Model.
3. Implement a typed Event Bus boundary and remove direct calls at the selected Runtime seams.
4. Define common Provider interfaces and migrate the old concrete Vision/model path.
5. Add durable-state required-field schemas, versioned migrations, recovery/backup policy and cross-store transaction/compensation semantics.
6. Complete the unified benchmark harness and non-ARKit Observation Providers.
7. Re-run the adversarial audit and full test/build/device acceptance matrix.

Only after all seven conditions pass should the Runtime be labeled `1.0 Freeze`.
