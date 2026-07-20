# Runtime Review Entry

## Runtime Surfaces

| Surface | Entry or owner | Role |
| --- | --- | --- |
| Local application runtime | `backend/main.py`, `backend/app/runtime.py` | Builds the interactive local runtime and Agent cluster |
| Command gateway | `backend/app/command_gateway.py` | HTTP command, state and management API for clients |
| Mobile/control plane | `scripts/mobile_link_receiver.py` | Mobile APIs, control-plane endpoints, artifacts and worker coordination |
| Workflow Runtime Host | `scripts/runtime_host.py`, `backend/orchestrator/runtime_host.py` | Fenced host identity, heartbeat, claims, checkpoints and workflow execution |
| Remote Worker | `backend/remote/worker.py` | Standalone HTTP execution node with heartbeat, auth and package import |
| Control-plane worker | `scripts/control_plane_worker.py` | Background processing around control-plane state and tasks |

## Runtime Flow to Verify

```text
request/event
  -> application or control-plane API
  -> request coordinator / planner
  -> queued task or workflow node
  -> selected skill/tool/executor
  -> local executor, Runtime Host, or Remote Worker
  -> result / artifact / trace
  -> durable store and client projection
```

## Remote Worker Independence Standard

The intended boundary is stronger than "callable over HTTP". A Remote Worker
should be deployable without the desktop application and should not require the
source checkout, desktop state directory, local model process, or callback into
a single owner process unless that dependency is an explicit protocol.

Review:

- node identity, registration and capability advertisement;
- heartbeat expiry and health transitions;
- task claim/lease ownership and duplicate execution;
- retries, cancellation, timeouts and idempotency;
- package/artifact transfer and signature verification;
- filesystem and working-directory assumptions;
- credential references and token rotation;
- offline recovery and reconciliation after network partitions;
- whether Runtime Host and Remote Worker are overlapping concepts.

## Persistence and Continuity

The code includes local locking, atomic replacement in selected stores,
checkpointing, host fencing and migration checks. The audit must determine
whether these guarantees are applied consistently across all state writers and
whether they remain correct across multiple machines and shared storage.

## Maturity Labels for Review

- Local interactive runtime: implemented.
- Command and management APIs: implemented, with a wide surface.
- Fenced Runtime Host: implemented baseline.
- Standalone Remote Worker: implemented baseline.
- Fully remote, horizontally scalable execution fabric: do not assume; verify.
- Multi-tenant runtime isolation: future direction; do not assume.
