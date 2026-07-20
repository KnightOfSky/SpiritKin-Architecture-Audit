# Deployment Review Entry

## Current Deployment Surfaces

- Local owner runtime and desktop console
- HTTP command gateway and mobile/control-plane receiver
- `Dockerfile` for the control-plane service
- `Dockerfile.runtime-host` for a fenced Workflow Runtime Host
- `docker-compose.yml` for control plane, optional runtime host, MinIO and Caddy
- Android bridge, native iOS terminal and browser extension as remote clients
- Local llama.cpp model services as the default model runtime, with Provider
  compatibility for alternatives

The audit should compare these files with actual environment lookups, ports,
health checks, storage paths and trust assumptions in code.

## Three-Stage Evolution Direction

### Phase 1: API plus Remote Worker

Goal: separate owner-facing clients and the control API from execution nodes.

Required contracts:

- stable authenticated API;
- independent worker installation and identity;
- capability advertisement and scheduling metadata;
- durable task claims, leases, retries and reconciliation;
- artifact transfer without shared local paths;
- observable health, trace and audit records.

### Phase 2: Self-Hosted Model Service

Goal: move model execution behind a separately deployable Provider service.

Required contracts:

- Provider-neutral model request and capability metadata;
- health, routing, timeout, quota and fallback semantics;
- separation of model artifacts from application deployment;
- versioned model identity and evaluation records;
- no hard dependency on a desktop-started local process.

### Phase 3: Model Pool plus Multi-Tenant Infrastructure

Goal: schedule across a pool of model capacity while isolating tenants and
resources. This does not mean renting the private SpiritKin core itself.

Required contracts:

- tenant-scoped identity, policy, storage, encryption and audit;
- resource and model quotas;
- scheduler isolation and noisy-neighbor controls;
- per-tenant secrets and Provider configuration;
- data retention, deletion and export boundaries;
- owner-private Agents and resources excluded from tenant visibility.

## Review Questions

- Does current API state assume one filesystem or process?
- Can MinIO/S3 artifacts replace shared paths end to end?
- Are service identities distinct from user and worker identities?
- Do health checks prove readiness or only process liveness?
- Are secrets passed by reference and environment, never persisted in payloads?
- Are migrations compatible with rolling or multi-host deployment?
- Which interfaces already support the three stages, and which need redesign?
