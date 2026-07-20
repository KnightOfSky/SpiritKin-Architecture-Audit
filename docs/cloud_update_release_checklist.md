# Cloud Update Release Checklist

Date: 2026-07-04

Use this checklist when shipping a control-plane, Remote Worker, or Android
Bridge update to the owner-only cloud deployment. It complements
`docs/cloud_deploy_smoke_test.md`; it is not a substitute for a real smoke run.

## Release Scope

Classify the change before building:

- Control-plane only: Python backend, `/ios/terminal`, `/control`, docs served
  by the cloud package, or Docker/container behavior.
- Remote Worker: `scripts/control_plane_worker.py`, worker install scripts,
  worker docs, package manifest, or worker update behavior.
- Android Bridge APK: files under `mobile-link-bridge/src`,
  `mobile-link-bridge/res`, or `mobile-link-bridge/AndroidManifest.xml`.

Android APK versions and cloud package versions are intentionally separate.
Only bump the Android `versionCode` / `versionName` when Android source or
manifest files changed.

## Preflight

From the repo root on the release machine:

```powershell
cd D:\SpiritKinAI
python -m unittest tests.test_control_plane_worker tests.test_control_plane_store tests.test_mobile_link_receiver
python -m py_compile scripts/control_plane_worker.py scripts/control_plane_store.py scripts/mobile_link_receiver.py backend/mobile/ios_endpoint.py backend/app/mobile_management.py
docker compose --env-file .env.cloud config
```

Expected:

- Unit tests pass.
- Compile checks pass.
- Compose resolves without printing secret values.
- `CONTROL_PLANE_DIRECT_BIND` remains `127.0.0.1:8791` unless intentionally
  testing LAN exposure.

## Remote Worker Package

The cloud control plane serves the latest worker package through:

```text
GET /worker/package/manifest
GET /worker/package
```

Release steps:

1. Bump `WORKER_VERSION` in `scripts/control_plane_worker.py` when worker
   behavior, install scripts, or package contents changed.
2. Build or let the control plane lazily build the package with
   `build_worker_package`.
3. Verify the manifest:

```powershell
curl https://<control-plane-host>/worker/package/manifest
```

Expected:

- Package is `spiritkin-control-plane-worker`.
- Manifest version matches the bumped `WORKER_VERSION`.
- `download_url` points at `/worker/package`.
- `serving_validation.status` is `ok`.
- SHA-256 and embedded file manifest are present.

Worker self-update path:

- Existing workers with `auto_update=true` and `update_manifest_url` set call
  `check_and_apply_update` before entering the worker loop.
- If a newer manifest is available, the worker verifies SHA-256, expands the
  package into `update_install_dir`, records update state, and exits so the
  scheduled task/service can restart it.
- Account ID and local proxy settings stay in the worker config; local proxy
  URLs are used only as subprocess environment variables and are not sent in
  heartbeat payloads.

## Android Bridge APK

Release steps when Android source changed:

1. Bump `android:versionCode` and `android:versionName` in
   `mobile-link-bridge/AndroidManifest.xml`.
2. Build the APK:

```powershell
.\mobile-link-bridge\build.ps1
```

3. Verify generated files:

```text
mobile-link-bridge/out/mobile-link-bridge.apk
mobile-link-bridge/out/release-manifest.json
mobile-link-bridge/out/mobile-link-bridge.apk.sha256
mobile-link-bridge/out/release-history.json
```

4. Promote through the human approval gate from desktop Mobile Management or
   `/ios/terminal` using `approve_android_apk_release`.
5. Verify:

```powershell
curl https://<control-plane-host>/android/apk/manifest
curl -I https://<control-plane-host>/android/apk
```

Expected:

- Manifest exposes the new version, size, SHA-256, rollback metadata, and
  `serving_validation.status=ok`.
- APK download is blocked until approval and available after approval.

## Control Page / Cloud Package

The management pages are served directly by the control plane:

```text
GET /control
GET /ios/terminal
GET /ios/control
```

Release steps:

1. Build the cloud package without including `.env`, `.env.cloud`, state files,
   worker runtime state, browser profiles, cookies, or store credentials.
2. Upload the package to the VM.
3. Extract over `/opt/SpiritKinAI` or the configured deployment directory.
4. Restart with build while preserving volumes:

```bash
sudo docker compose --env-file .env.cloud up -d --build
```

Do not use `down -v` during normal updates. It deletes persisted accounts,
pairings, worker bindings, artifact metadata, usage counters, MinIO data, and
promotion approvals.

Expected:

- `/android/health` works through HTTPS.
- `/control` and `/ios/terminal` render the updated page immediately after the
  control-plane container restarts.
- Existing management tokens, account-console tokens, worker tokens, Android
  bindings, and usage counters remain valid.

## Post-Deploy Smoke

Run these checks after every cloud update:

```bash
sudo docker compose --env-file .env.cloud ps
curl http://127.0.0.1:8791/android/health
curl https://<control-plane-host>/android/health
curl -s https://<control-plane-host>/worker/package/manifest
curl -s https://<control-plane-host>/android/apk/manifest
```

Then validate the owner flows:

- Open `/control` with the management token.
- Open `/ios/terminal` with an owner or bound terminal token.
- Create or inspect an account and workspace.
- Create a remote-worker pairing token.
- Pair a test worker or confirm an existing worker heartbeat.
- Confirm account usage and artifact quota summaries still render.
- Restart again with `up -d --build` and confirm the same account, workspace,
  pairings, and usage are still present.

## Secrets Boundary

Never put these values in release notes, collaboration docs, package archives,
or screenshots:

- `SPIRITKIN_MANAGEMENT_TOKEN`
- Worker pairing tokens or bound worker bearer tokens
- MinIO/S3 access keys
- Browser profile paths, cookies, store passwords, PDD/Douyin login sessions
- Local proxy URLs unless the operator explicitly provides a redacted example

Credential-bearing browser profiles and ecommerce sessions stay on the user's
worker machine. The cloud control plane stores only account/workspace metadata,
pairing/binding records, usage counters, artifacts allowed by policy, and
auditable command/workflow state.
