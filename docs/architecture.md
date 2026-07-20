# Architecture Review Entry

This document describes the review map, not a verdict. Code remains the source
of truth. The intended kernel is defined in `ai_runtime_kernel_spec.md`; the
audit should identify where implementation matches, precedes, or diverges from
that model.

## Current Shape

SpiritKinAI is a cross-device, multi-agent runtime with four broad planes:

1. Interface plane: desktop, browser, iOS, Android bridge, browser extension,
   message channels and HTTP control surfaces.
2. Control plane: application assembly, command gateway, intent handling,
   planning, orchestration, workflow and governance.
3. Execution plane: skills, tools, executors, local devices, Runtime Hosts and
   Remote Workers.
4. Data plane: state files, workflow stores, context, memory, knowledge,
   artifacts, traces and evaluations.

## Actual Module Ownership

| Concern | Main implementation | Initial classification |
| --- | --- | --- |
| Local runtime | `backend/app/runtime.py`, `backend/main.py` | Implemented and broad |
| Unified runtime foundation | `backend/runtime/contracts.py`, `lifecycle.py`, `event_bus.py`, `providers.py`, `state_machine.py` | Implemented baseline with explicit lifecycle contracts |
| API/control surface | `backend/app/command_gateway.py`, `scripts/mobile_link_receiver.py` | Implemented; boundary size should be reviewed |
| Planning and Agent cluster | `backend/orchestrator/`, `backend/agents/` | Implemented with active evolution |
| Workflow | `backend/orchestrator/workflow_graph.py`, `workflow_store.py`, `runtime_host.py` | Implemented; distributed guarantees require audit |
| Skills and tools | `backend/skills/`, `backend/tools/`, `backend/executors/` | Implemented with legacy/direct-binding paths |
| Remote execution | `backend/remote/worker.py`, Runtime Host and control-plane scripts | Implemented baseline; independence is an audit question |
| Durable state | `backend/state_store.py`, workflow/context stores, control-plane store | Implemented primarily around files; shared-storage evolution is incomplete |
| Governance | `backend/security/`, review gates, tool authorization, `backend/capability/growth/` | Implemented in multiple surfaces; coherence requires audit |
| Model abstraction | `backend/model/`, provider/model catalog and configuration surfaces | Implemented abstraction; deployment pool is future direction |
| Client projections | Desktop, Web, iOS, Android bridge | Implemented at different maturity levels |
| Multi-tenant platform | deployment and metadata seeds only | Future direction, not assumed complete |

These classifications are orientation labels, not audit conclusions.

## Intended Kernel Concepts

The stable target vocabulary is:

```text
Intent -> Planner -> Context/Resource/Capability Registries -> Scheduler
       -> Workflow -> Skill Registry -> Worker Registry -> Worker
       -> Trace / Artifact / Memory / Evaluation
```

Agent is an intelligence and governance participant, not a substitute for
Workflow, Worker or storage. Provider is an adapter boundary, not a product
identity. Review duplicated routers, managers and context types against these
rules.

## Important Boundaries

- `backend/app/` may assemble use cases and expose APIs but should not become a
  second orchestration kernel.
- `backend/orchestrator/` owns task coordination and workflow execution but
  should depend on contracts rather than client or device details.
- Skills describe reusable implementations; executors and workers provide
  concrete execution capacity.
- Clients should project runtime state and issue commands, not own canonical
  workflow or Agent state.
- Durable records must survive process restart and should not assume one local
  machine if they are part of remote execution.

## First-Pass Deliverable

Before recommending changes, produce:

- an actual dependency and ownership map;
- intended-versus-actual differences;
- a maturity table: mature, partial, reserved, compatibility-only;
- the smallest set of first-class architecture concepts visible in code;
- evidence for every conclusion with file and line references.
