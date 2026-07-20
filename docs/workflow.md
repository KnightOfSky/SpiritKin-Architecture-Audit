# Workflow Review Entry

## Main Implementation

- Definition and graph model: `backend/orchestrator/workflow_graph.py`
- Persistence and version records: `backend/orchestrator/workflow_store.py`
- Runtime contracts: `backend/orchestrator/workflow_runtime_contracts.py`
- Host execution and checkpoints: `backend/orchestrator/runtime_host.py`
- Task and queued phases: `task_queue.py`, `queued_task_phase.py`
- Finalization: `workflow_task_finalizer.py`,
  `scheduler_task_finalizer.py`, `execution_finalizer.py`
- API management: `backend/app/workflow_management.py` and
  `workflow_run_management.py`
- Tool interface: `backend/tools/workflow_graph_tools.py`
- Desktop authoring and monitoring: `desktop/.../Features/Workflows/`

## Lifecycle to Reconstruct from Code

The audit should derive the real state machine rather than trusting a single
document. At minimum trace:

```text
definition -> version -> run -> node readiness -> claim/lease -> execution
           -> retry/review/failure -> finalization -> replay/audit
```

For every transition, identify the canonical writer, durable record, lock or
fence, emitted event and recovery behavior.

## Contract Questions

- Are new workflows capability-bound, skill-bound, tool-bound, or a mixture?
- Is cycle detection consistent between backend and desktop authoring?
- Can two hosts claim or finalize the same node?
- Are retries idempotent and are side effects compensatable?
- Does a process crash leave enough data to resume deterministically?
- Are definition versions immutable for existing runs?
- Are review gates durable and attributable?
- Is replay a first-class contract or a test-only harness?
- Which state is canonical when API, desktop cache and workflow store differ?

## Initial Classification

- Definition, graph editing and run records: implemented.
- Desktop workflow authoring: implemented and substantial.
- Host fencing, checkpointing and migration integrity: implemented baseline.
- Distributed queue semantics and multi-host fault tolerance: audit required.
- Capability-first workflow binding: target architecture with compatibility
  paths still present.
