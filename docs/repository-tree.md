# Repository Tree for First-Pass Review

This is a review-oriented depth-three tree. Generated artifacts and low-value
binary leaves are omitted. Reviewers can run `tree -L 3` locally for the exact
snapshot.

```text
.
|-- backend/
|   |-- action/                 high-level action intents
|   |-- agents/                 specialist Agent implementations
|   |-- app/                    application assembly and API surfaces
|   |-- capability/growth/      governed capability evolution sandbox
|   |-- channels/               external message channels
|   |-- devices/                concrete device adapters
|   |-- evaluation/             verification, replay and benchmark runtime
|   |-- executors/              local and remote execution adapters
|   |-- expression/             TTS, emotion and presentation output
|   |-- knowledge/              ingestion, indexing and retrieval
|   |-- memory/                 conversation and workflow memory
|   |-- mobile/                 iOS/mobile runtime contracts
|   |-- model/training/         dataset, training and model workbench code
|   |-- orchestrator/           planner, cluster, workflow and scheduling core
|   |-- perception/             audio, vision and world inputs
|   |-- remote/                 standalone Remote Worker and package security
|   |-- runtime/                runtime events and scheduler ownership roots
|   |-- security/               auth, safety and trust-boundary helpers
|   |-- skills/                 skill contracts, persistence and promotion
|   |-- tools/                  callable tool definitions and worker tools
|   |-- world/                  observed world state
|   |-- main.py                 thin local runtime entrypoint
|   `-- state_store.py          shared durable-state primitives
|-- browser-extension/          browser-side commerce extraction interface
|-- config/                     runtime, eval and authorization configuration
|-- deploy/                     reverse-proxy and deployment configuration
|-- design/                     design sources; binary references excluded
|-- desktop/
|   |-- SpiritKinDesktop/       WPF owner console and feature controllers
|   `-- SpiritKinDesktop.Tests/ desktop contract and controller tests
|-- docs/
|   |-- architecture.md         first-pass architecture map
|   |-- runtime.md              runtime and Remote Worker guide
|   |-- workflow.md             workflow lifecycle guide
|   |-- agent.md                Agent and AI Employee guide
|   |-- deployment.md           current and three-stage deployment direction
|   |-- decisions.md            owner constraints and architecture decisions
|   `-- ai_runtime_kernel_spec.md intended kernel model
|-- frontend/                   browser UI, realtime panels and client contracts
|-- ios/SpiritKinTerminal/      native iOS owner terminal
|-- mobile-link-bridge/         Android bridge, capture and command sync
|-- scripts/
|   |-- control_plane_store.py  control-plane durable data services
|   |-- control_plane_worker.py control-plane worker loop
|   |-- mobile_link_receiver.py HTTP control plane and mobile API
|   |-- runtime_host.py         fenced Workflow Runtime Host process
|   `-- collaboration_agent_worker.py external collaboration worker
|-- tests/                      service and integration-oriented tests
|-- .github/workflows/          CI and iOS build checks
|-- Dockerfile                  control-plane container
|-- Dockerfile.runtime-host     standalone runtime-host container
|-- docker-compose.yml          control plane, runtime host, MinIO and Caddy
|-- pyproject.toml              Python test and lint policy
`-- SpiritKinAI.sln             desktop solution
```

There is no single top-level `mobile/`, `workflow/`, `agent/`, or `skills/`
package. Those responsibilities are placed under `backend/`, while concrete
mobile clients live in `ios/` and `mobile-link-bridge/`. Whether this ownership
model is sufficiently clear is part of the audit.
