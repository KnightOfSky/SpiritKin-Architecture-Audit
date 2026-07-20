# SpiritKin Light Cloud Control Plane

Date: 2026-06-14

## Decision

SpiritKin should not start as a heavy SaaS that uploads every image and runs
all browser/mobile automation in the cloud. The practical first deployment is a
light control plane plus local execution.

The owner terminals are:

- local desktop terminal
- iOS terminal/PWA

The controlled execution endpoints are:

- remote worker desktop on the operator or customer machine
- Android Control Bridge APK on the phone

The control plane owns coordination:

- account/workspace boundary
- ecommerce workflow templates
- workflow run state
- remote worker registration and task assignment
- Android Bridge device state and command queue
- artifact metadata and lifecycle
- audit events

The local worker owns execution:

- browser/RPA execution
- Android device interaction
- local product images and screenshots
- detailed runtime logs

## Storage Model

Local/dev mode uses one JSON state file:

```text
state/control_plane/control_state.json
state/control_plane/artifacts/<workspace_id>/<artifact_id>/*
```

The implementation is:

```text
scripts/control_plane_store.py
```

The local JSON state is schema-versioned. Current version:

```text
2
```

Version 2 adds:

- `schema`: migration metadata and current schema version.
- `action_log`: the unified bounded action log used by management/audit views.
- Automatic migration from older state files when `ControlPlaneStore.load()`
  runs.
- Migration definitions are centralized in `MIGRATIONS` inside
  `scripts/control_plane_store.py`, so tests and snapshots can inspect the
  available upgrade path.

Production/multi-user mode should keep the same contracts but move the backend:

| Area | Local mode | Production mode |
| --- | --- | --- |
| Workflow templates/runs | JSON state file | Postgres/SQLite service DB |
| Device/worker registry | JSON state file | service DB |
| Artifact metadata | JSON state file | service DB |
| Artifact files | local disk | S3/MinIO/OSS/COS |
| Commands/events/action log | JSON state file | DB plus bounded event/action tables |

Every object should be scoped by:

```text
tenant_id / workspace_id / project_id / artifact_id
```

The first implementation currently uses `workspace_id`; tenant/project fields
can be added without changing the endpoint shape.

Each workspace also carries an `artifact_policy`:

- `backend`: `local_disk` by default, or `filesystem_object_store` for a
  mounted object-store style volume.
- `backend_root`: optional storage root for the filesystem backend.
- `max_workspace_bytes`: per-workspace artifact byte quota.
- `max_workspace_artifacts`: per-workspace artifact count quota.
- `max_file_bytes`: per-file hard limit.
- `default_ttl_hours`: default artifact TTL when the caller does not specify one.
- `cleanup_on_quota`: reserved for future auto-prune behavior.

Update it through the same `update_workspace_policy` endpoint:

```text
POST /ios/control/action {
  "action": "update_workspace_policy",
  "workspace_id": "...",
  "policy": {
    "artifact_policy": {
      "backend": "filesystem_object_store",
      "backend_root": "state/control_plane/object-store",
      "max_workspace_bytes": 1073741824,
      "max_workspace_artifacts": 1000
    }
  }
}
```

Artifact writes now fail closed when the workspace byte/count quota is exceeded.
Snapshot responses include an `artifacts.quota` summary for the current
workspace or all workspaces.

Each workspace also carries an `execution_policy`:

- `control_allowed_actions`: allowlist for non-management control actions such
  as `start_workflow_run` and `queue_android_command`.
- `control_denied_actions`: explicit denylist for non-management control
  actions. It wins over the allowlist.
- `android_allowed_operations`: allowlist for Android commands.
- `android_denied_operations`: explicit denylist that wins over the allowlist.
- `workflow_allowed_templates`: workflow template allowlist.
- `worker_allowed_capabilities`: worker capability allowlist.
- `require_promote_gate`, `approved_promotions`, and `default_task_budget`:
  worker execution governance.

Update it through:

```text
POST /ios/control/action {
  "action": "update_workspace_policy",
  "workspace_id": "...",
  "policy": {
    "control_allowed_actions": ["snapshot", "action_log", "start_workflow_run"],
    "control_denied_actions": ["update_workspace_policy"],
    "android_allowed_operations": ["pdd.launch", "android.ui_snapshot"],
    "android_denied_operations": ["clipboard.write"],
    "workflow_allowed_templates": ["ecommerce.auto_listing.v1"]
  }
}
```

The policy is enforced before non-management control actions, queueing Android
commands, or starting workflow runs. The configured management token is treated
as the owner role and bypasses workspace control-action allow/deny lists. This
is a logical execution boundary; process/container isolation is a separate
runtime layer still required for untrusted local code execution.

Each workspace also has a `runtime_profile`:

- `workspace_root`: logical per-workspace execution root.
- `venv_path`: planned Python venv path for local worker execution.
- `dependency_files`: dependency manifests the worker should honor.
- `dependency_policy`: `project_local_only`, `locked`, or `container_only`.
- `allowed_local_commands`: local command allowlist for future CLI adapters.
- `forbidden_paths`: runtime paths/services that must not be imported or called.

Update it through:

```text
POST /ios/control/action {
  "action": "update_runtime_profile",
  "workspace_id": "...",
  "runtime_profile": {
    "venv_path": "state/workspaces/.../.venv",
    "dependency_policy": "locked",
    "allowed_local_commands": ["python"]
  }
}
```

The profile is attached to workflow runs and worker tasks. It is currently a
contract and audit boundary; automatic venv/container creation and command
execution are intentionally not performed by the control plane yet.

Android commands also pass a server-side capability preflight:

- `android_command_catalog` is included in snapshots and declares each
  operation's risk tier plus required device capability/package/artifact or
  Accessibility conditions.
- Broadcast commands (`device_id: "*"`) are allowed through workspace policy, but
  high-risk operations such as PDD listing carry a preflight warning.
- Targeted commands to a known `device_id` are rejected before queueing if the
  device belongs to another workspace, did not report the required capability,
  lacks PDD, lacks required Accessibility state, or references an unavailable
  artifact.
- The resulting command stores its `preflight` object and the audit log stores
  the risk/preflight status for later review.

## State Maintenance and Audit

The control plane keeps two related histories:

- `events`: compact internal event stream for backwards-compatible snapshots.
- `action_log`: normalized management/audit stream with `action`, `status`,
  `workspace_id`, `actor`, `target_type`, `target_id`, timestamp, and summary.

Use the action log as the single management-facing history. It is exposed by:

```text
GET  /action-log?workspace_id=<workspace>&limit=50
GET  /ios/control/action-log?workspace_id=<workspace>&limit=50
POST /ios/control/action {"action":"action_log","workspace_id":"..."}
```

Routine state cleanup is also centralized:

```text
POST /ios/control/action {"action":"cleanup_state","workspace_id":"...","older_than_hours":168}
POST /ios/control/action {"action":"validate_state","workspace_id":"..."}
```

`cleanup_state` currently:

- runs artifact TTL cleanup
- expires overdue pending pairing tokens
- marks stale Remote Workers, Android devices, and iOS terminals offline
- prunes old completed/failed/cancelled/expired Android commands
- writes one action log entry for the cleanup summary

`validate_state` is read-only. It checks schema version, required collections,
orphan workflow/task references, missing artifact files, and stale action-log
references. The iOS/Web control page exposes it as `检查状态`.

## Workflow Execution

The automatic listing workflow is not owned by the Android app or the remote
worker. It is owned by the control plane as a workflow template and workflow
run.

Execution flow:

```text
iOS/Desktop management terminal
  -> start workflow run
  -> control plane creates worker task
  -> remote worker heartbeat claims task
  -> worker executes PC/browser/data/image-preparation steps
  -> control plane queues Android commands for phone-side steps
  -> Android Bridge executes assigned phone app actions
  -> worker and Android Bridge post results
  -> control plane updates run state
```

Control plane and workspace definitions:

- Control plane: the coordination service and state store. It owns workflow
  templates/runs, worker tasks, Android command queues, artifact metadata,
  pairing tokens, and audit history.
- Workspace: the scoped owner/project/customer area inside a control plane.
  A workspace groups the devices, Remote Worker Agents, Android Bridges,
  artifacts, links, and workflow runs that belong together.
- Remote Worker Agent: a PC/browser/data executor attached to a workspace. It
  claims assigned tasks and reports results; it does not replace the control
  plane.
- Android Bridge: a phone executor attached to a workspace. It handles Android
  app actions, image share intents, heartbeat/command sync, and PDD UI
  automation when enabled.

Product image flow:

```text
selection agent or human upload
  -> Artifact Store
  -> workflow run inputs include artifact_id
  -> listing/moving agent consumes artifact_id
  -> Android Bridge receives command referencing artifact_id
  -> Android side downloads/receives image and performs phone-side action
```

Current Android APK already supports uploading shared images into the artifact
store, downloading artifacts by command, sharing downloaded images to target
apps, local cache cleanup, pairing token/QR binding, and background sync.

## Remote Worker Role

Remote Worker Agent is an execution node, not the source of truth.

It should:

- heartbeat to `/worker/heartbeat`
- declare capabilities such as `ecommerce.auto_listing` and `android.bridge`
- receive assigned tasks
- execute only the assigned step/run
- post results to `/worker/result`

It should not:

- own the master workflow registry
- keep the only copy of product images
- bypass workspace permissions
- directly mutate another user's workflow without control-plane authorization

Current control-plane worker governance:

- Workspace `execution_policy.worker_allowed_capabilities` limits which reported
  worker capabilities are authorized for task assignment.
- Remote Worker endpoints can now be paired like Android endpoints:
  create a `remote_worker` pairing token from `/ios/control/pairing`, bind it
  through `POST /worker/pair`, then use the token on `/worker/heartbeat` and
  `/worker/result`.
- Set `SPIRITKIN_REQUIRE_WORKER_TOKEN=1` before starting the receiver to require
  a bound Remote Worker token. Management-token callers remain useful for local
  diagnostics when `SPIRITKIN_MANAGEMENT_TOKEN` is configured.
- Set `SPIRITKIN_PRODUCTION_MODE=1` for managed deployments. Production mode
  makes Android and Remote Worker pairing tokens required by default, gates
  pairing/owner actions behind the management token, and refuses owner/control
  access if `SPIRITKIN_MANAGEMENT_TOKEN` is not configured.
- Workflow runs carry a `governance` object with `promote_mode`
  (`dry_run`, `debug`, or `production`), `dry_run`, `debug`, and a bounded task
  `budget`.
- `require_promote_gate: true` blocks `production` workflow runs until
  `POST /ios/control/action {"action":"approve_workflow_promotion", ...}` adds
  the template to `approved_promotions`.
- Worker tasks include `required_capabilities`, `governance`, and `budget`.
  Heartbeat assignment skips tasks when the worker lacks or is not authorized for
  the required capability, and records `worker_task_claim_skipped` in the
  unified action log.
- Worker task assignment now has a lease. The lease is based on
  `budget.max_runtime_seconds` plus a 300-second grace period. If a Worker
  disappears after claiming a task, the next Worker heartbeat or
  `cleanup_state` reclaims the expired task. The task is requeued until
  `budget.max_retries` is exhausted, then the task and workflow run are marked
  failed with `worker_task_lease_expired`.
- `/worker/result` rejects results that exceed declared usage budget, and rejects
  publish/submit side-effect reports from `dry_run` tasks.

The lightweight worker loop is:

```powershell
python scripts\control_plane_worker.py `
  --server http://127.0.0.1:8791 `
  --workspace-id local-ecommerce `
  --worker-id local-worker-1 `
  --pairing-token <remote_worker_pairing_token> `
  --state-dir state\workers\local-worker-1 `
  --prepare-runtime `
  --allow-cli `
  --once
```

For long-running use, write a config once and let the worker restore its bound
token from runtime state after the first pairing:

```powershell
python scripts\control_plane_worker.py `
  --server http://127.0.0.1:8791 `
  --workspace-id local-ecommerce `
  --worker-id local-worker-1 `
  --state-dir state\workers\local-worker-1 `
  --allow-cli `
  --prepare-runtime `
  --write-config state\workers\local-worker-1\worker.json

python scripts\control_plane_worker.py `
  --config state\workers\local-worker-1\worker.json `
  --pairing-token <remote_worker_pairing_token>

python scripts\control_plane_worker.py `
  --config state\workers\local-worker-1\worker.json `
  --max-consecutive-errors 12 `
  --error-backoff 10
```

Worker package metadata can be generated for distribution or verification:

```powershell
$env:SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET = "<optional-hmac-secret>"
python scripts\control_plane_worker.py `
  --release-manifest state\workers\worker-release-manifest.json
```

The manifest includes package name, version, entrypoint, file SHA-256 hashes,
file sizes, default capabilities, and an optional `hmac-sha256` signature.

An installable zip can also be built:

```powershell
python scripts\control_plane_worker.py `
  --package-zip state\workers\spiritkin-control-plane-worker.zip
```

The zip includes the Worker script, docs, `worker.example.json`,
`run-worker.cmd`, `install-worker-gui.ps1`, `setup-worker.ps1`,
`update-worker.ps1`, `install-worker-scheduled-task.ps1`, and
`worker-release-manifest.json`.
Use `setup-worker.ps1` on the target Windows machine to write `worker.json`,
optionally pair once, and optionally register a Scheduled Task:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-worker.ps1 `
  -ServerUrl http://127.0.0.1:8791 `
  -WorkspaceId local-ecommerce `
  -WorkerId worker-1 `
  -PairingToken <remote_worker-token> `
  -InstallScheduledTask
```

For a native Windows setup form, run the GUI wrapper after extracting the zip:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-worker-gui.ps1 `
  -ServerUrl http://127.0.0.1:8791 `
  -WorkspaceId local-ecommerce `
  -PairingToken <remote_worker-token>
```

It opens a Windows Forms installer for Server URL, Workspace ID, Worker ID,
pairing token, install directory, and optional Scheduled Task registration.

The control plane can also host that Worker package directly:

```powershell
Invoke-RestMethod http://127.0.0.1:8791/worker/package/manifest
Invoke-WebRequest http://127.0.0.1:8791/worker/package -OutFile spiritkin-control-plane-worker.zip
```

`GET /worker/package/manifest` returns the zip download URL, outer zip SHA-256
and size, the embedded file manifest, and serving validation. `GET
/worker/package` serves the current zip, rebuilding it when the Worker script or
included docs are newer than the cached package. Remote Worker pairing responses
also include `package_manifest_url`, `package_download_url`, and
`setup_command`. Use `update-worker.ps1 -ManifestUrl <package_manifest_url>` to
download, SHA-256 verify, and expand the latest hosted zip.

Worker configs can opt into in-place self-update:

```powershell
python scripts\control_plane_worker.py `
  --update-manifest-url http://127.0.0.1:8791/worker/package/manifest `
  --update-install-dir C:\SpiritKinWorker `
  --check-update
```

When `auto_update` is true in `worker.json` or `--auto-update` is passed, the
Worker checks the hosted manifest before the heartbeat loop. If a newer version
is available, it downloads the zip, verifies SHA-256, extracts it into the Worker
install directory, records the update in `runtime-state.json`, and exits so the
Scheduled Task/process manager can restart it on the updated files.

It provides a fail-closed operation registry:

- `workflow.execute.auto_listing`: dry-run/debug planning for Android steps;
  with `--allow-production` and production governance, queues the planned
  Android commands through the control plane and reports `queued_android_commands`
  command IDs in the Worker result.
- `local.cli.run`: governed subprocess execution under the workspace root.
- `langgraph.run`: launches a Python module command for LangGraph adapters.
- `crewai.run`: launches a Python module command for CrewAI adapters.

The matching built-in workflow templates are:

- `local.cli.run.v1`
- `langgraph.run.v1`
- `crewai.run.v1`

They are included in default `workflow_allowed_templates` and can be disabled
per workspace through `update_workspace_policy`.

`--prepare-runtime` creates the workspace root and optional Python venv declared
by `runtime_profile`. `--allow-cli` is required before any subprocess adapter can
run. Production execution is still disabled unless `--allow-production` is set,
and all adapters remain subject to runtime profile allowlists, task budget, and
the promote gate. Worker results are written to a durable local outbox before
POSTing `/worker/result`; successful POST deletes the outbox file, while network
or server failures leave it for the next loop. The default outbox is
`state/workers/<worker_id>/outbox`, or `<state-dir>/outbox` when `--state-dir`
is set. Pairing writes the bound token to `<state-dir>/runtime-state.json`, so a
process manager can restart the worker with only `--config`.

For another user, there are two valid deployment models:

- Self-managed: they have their own control plane/workspace. Their Remote Worker
  Agent and Android Bridge bind there, and they manage their own workflows/data.
- Owner-managed: you host/manage the control plane workspace. Their Remote
  Worker Agent and Android Bridge bind to that workspace, and you can manage,
  inspect, assign, pause, retry, and audit their ecommerce workflows.

## Android Bridge Role

Android Bridge is a controlled device endpoint.

Current supported responsibilities:

- share PDD text links to `/android/link`
- heartbeat to `/android/heartbeat`
- receive queued commands
- launch app, open URL, write clipboard
- upload shared images to `/android/artifact`
- download artifacts by command
- share downloaded images to PDD or another target app
- local cache cleanup
- pairing token/deep-link binding
- background sync service

Missing high-priority responsibilities:

- upload screenshots
- deeper PDD seller-flow calibration

## iOS Terminal Role

iOS terminal is a management terminal, not an execution worker.

Current supported responsibilities:

- open `/ios/terminal`
- inspect workspace-scoped snapshot
- upload mobile work images to `/mobile/artifacts`
- start ecommerce auto-listing workflow run
- queue Android commands
- cleanup expired artifacts
- create Android/Remote Worker pairing tokens for the bound workspace

Current terminal-token boundary:

- With `SPIRITKIN_MANAGEMENT_TOKEN` configured, the global management token can
  mint an `ios_terminal` pairing token from `/ios/control/pairing`.
- `POST /ios/control/pair` binds that token to a concrete iOS/Web terminal and
  workspace.
- The bound terminal token can call `/ios/control/snapshot`,
  `/ios/control/action-log`, `/ios/control/action`, `/mobile/artifacts`, and
  Android/Worker pairing generation for only its workspace.
- Bound terminal control actions are evaluated against the workspace
  `execution_policy.control_allowed_actions` and
  `execution_policy.control_denied_actions`.
- A bound terminal token cannot mint another `ios_terminal` owner token.

Production gaps:

- workspace switching
- HTTPS/Tailscale deployment guidance

## Endpoint Summary

Run the local control plane:

```powershell
python scripts\mobile_link_receiver.py
```

Default port:

```text
8791
```

Endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /android/health` | control plane health and snapshot |
| `POST /android/link` | Android shared PDD link intake |
| `POST /android/heartbeat` | Android device state, command drain, result return |
| `POST /android/command` | queue Android command |
| `POST /android/artifact` | Android artifact upload |
| `POST /mobile/artifacts` | iOS/Desktop artifact upload |
| `GET /ios/terminal` | iOS management PWA |
| `GET /ios/control/snapshot` | management snapshot |
| `GET /ios/control/action-log` | management action log |
| `GET /ios/control/pairing` | create Android, Worker, or iOS terminal pairing token |
| `POST /ios/control/pair` | bind iOS/Web terminal to an `ios_terminal` token |
| `POST /ios/control/action` | management action |
| `POST /worker/heartbeat` | remote worker registration/task claim |
| `POST /worker/result` | remote worker result |

## Deployment Shape

For managed external users, prefer:

- Android phone: install `SpiritKin Control Bridge`, bind with
  `workspace_id + pairing token`, enable background sync, and enable the PDD
  accessibility service only for workflows that need Android UI automation.
- Their PC or local machine: run Remote Worker Agent for browser/desktop/data
  preparation steps. For ecommerce auto-listing, the worker prepares product
  data/images and the Android Bridge executes phone-side app steps.
- Connectivity: Tailscale is the simplest private-network option when the
  control plane runs on the owner's local PC. If using a hosted/light cloud
  control plane with HTTPS, external users do not need to join the owner's
  Tailscale network; they connect outward to the cloud endpoint with their own
  workspace token.
- Data ownership choices:
  - Self-managed user: they run or use their own control plane, so their images,
    links, workflow runs, and worker state stay under their own workspace/store.
  - Owner-managed user: they bind to the owner's hosted/managed workspace, so
    artifacts and links are visible to the owner for support, assignment, audit,
    and workflow control.
  - The Android Bridge UI should show only the user's own recent uploads/links;
    workspace, artifact IDs, filesystem paths, and audit details belong in
    desktop/iOS/admin management views.

```text
your iOS/Desktop terminal
  -> lightweight control server
  -> other people's local workers
  -> their Android Bridges
```

The controlled machines should call out to the server. Avoid requiring inbound
ports on customer networks.

## Cloud Deploy

The repository now includes a minimal cloud bundle:

- `Dockerfile`
- `docker-compose.yml`
- `.env.cloud.example`
- `deploy/caddy/Caddyfile`
- `docs/cloud_deploy_smoke_test.md`

Bring it up on a VM or small cloud instance:

```powershell
copy .env.cloud.example .env.cloud
docker compose --env-file .env.cloud up -d --build
```

The default compose stack runs:

- `control-plane`: the Python receiver on `8791`
- `caddy`: public HTTPS ingress
- `minio`: S3-compatible artifact storage
- `minio-init`: bucket bootstrap

Keep the direct Python port private. Expose only the HTTPS edge to the public
internet. The expected cloud setup is:

- public DNS name on `CADDY_HOST`
- long random `SPIRITKIN_MANAGEMENT_TOKEN`
- `SPIRITKIN_PRODUCTION_MODE=1`
- S3-compatible artifact backend, defaulting to MinIO in the compose stack

If you use an external object store instead of the bundled MinIO service, set:

- `SPIRITKIN_ARTIFACT_S3_ENDPOINT_URL`
- `SPIRITKIN_ARTIFACT_S3_BUCKET`
- `SPIRITKIN_ARTIFACT_S3_REGION`
- `SPIRITKIN_ARTIFACT_S3_PREFIX`
- `SPIRITKIN_ARTIFACT_S3_PUBLIC_BASE_URL` if you want a public object URL

Artifact download still goes through the control plane by default. The object
store is the persistence layer, not the primary user-facing endpoint.

Use `docs/cloud_deploy_smoke_test.md` as the owner-only acceptance checklist
before handing the endpoint to any external user.

Android Bridge should not hard-code the owner's port. It should bind through a
per-user receiver URL or QR code:

```text
server_url
workspace_id
device_id
pairing_token
expires_at
```

Local private mode can use:

```text
http://<desktop-or-tailscale-ip>:8791/android/link
```

Managed mode should use:

```text
https://<control-plane-host>/android/link
```

## Near-Term Build Priorities

1. Android artifact download/share-to-app command.
2. Pairing token and QR payload.
3. Workspace/tenant permissions on every management action.
4. Promote-gate/budget decisions backed by the unified action log.
5. Remote Worker executable loop for `/worker/heartbeat` and `/worker/result`.
6. Optional S3/MinIO artifact backend behind the same store contract.
7. Desktop/iOS UI panels for workers, Android devices, workflow runs, artifacts,
   cleanup, and failure retry.
