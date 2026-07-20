# Mobile Link Bridge

Date: 2026-06-08

This bridge moves Pinduoduo links, shared product images, Android device state,
and command results from an Android phone back to the SpiritKin control plane.
The Android APK is now a controlled endpoint for ecommerce work, not only a
one-shot PDD link bridge.

Android is intentionally Bridge-only: it exposes pairing, device state,
artifacts, links, workflow execution, Accessibility, and screen-capture
controls, but it does not embed or render the 3D Avatar. The mobile 3D Avatar
belongs to the iOS controller surface (`/ios/terminal` and the native Control
destination).

Current built candidate APK: `2026.06.25.7`.

Current promoted/downloadable APK: `2026.06.25.4`. The `.7` candidate has
passed package, hash, and v1/v2/v3 signature validation, but remains blocked by
the promotion gate until Android device review and explicit human approval.

Current APK promotion gate: approved for `2026.06.25.4`
(`sha256=1b29c93c66e4d643674fcac1c87870ad7262b30467da2a8537f58b35647d6992`).
The 2026-06-26 desktop/iOS/backend pass did not change Android native source,
so the APK version was not bumped. The receiver now blocks `/android/apk`
downloads until `approve_android_apk_release` records a matching human
approval in `state/mobile/android-apk-promotion.json`.

Android disables Accessibility services after reinstall/upgrade as a system
security rule. The bridge cannot silently re-enable Accessibility unless the
device is managed through root, ADB secure settings, or Device Owner/MDM.
After reinstall, heartbeat should still report the phone as online and bound;
only PDD automation and UI snapshot commands are blocked until the user opens
Android Accessibility settings and enables `SpiritKin PDD Automation` again.
APK `2026.06.25.4` also fixes a long-poll heartbeat timeout mismatch: the
client waits long enough for the control plane's command long poll instead of
marking an idle heartbeat as failed. It also tries to restore the foreground
sync service after app replacement, boot, and user unlock. On aggressive OEM
ROMs, the user may still need to allow background running / ignore battery
optimization from the phone settings.

## Flow

1. Run the lightweight control plane receiver:

   ```powershell
   python scripts\mobile_link_receiver.py
   ```

   Shortcut:

   ```cmd
   scripts\start_mobile_receiver.cmd
   ```

2. Install the Android helper APK:

   ```powershell
   .\mobile-link-bridge\build.ps1
   adb install -r .\mobile-link-bridge\out\mobile-link-bridge.apk
   ```

3. On the phone, open `SpiritKin Control Bridge` to configure/test the receiver,
   sync commands, or upload image artifacts. Share a Pinduoduo text link or
   product image to `SpiritKin Control Bridge` for automatic forwarding.

4. Link intake still writes the legacy files for queue compatibility:

   - `state\mobile-links\links.jsonl`
   - `state\mobile-links\latest-link.txt`

5. The shared control state is stored in:

   - `state\control_plane\control_state.json`
   - `state\control_plane\artifacts\<workspace_id>\<artifact_id>\...`

The control-plane store implementation is:

```text
scripts\control_plane_store.py
```

For cloud deployment, run the control plane behind HTTPS rather than exposing
the raw `8791` port. The repository root now includes `Dockerfile`,
`docker-compose.yml`, `.env.cloud.example`, and `deploy/caddy/Caddyfile` for a small
cloud stack with MinIO-backed artifact storage. Use
`docs/cloud_deploy_smoke_test.md` for the owner-only validation checklist.

## Supported Link Shapes

- Pinduoduo web links under `yangkeduo.com`
- Pinduoduo web links under `pinduoduo.com`

The phone bridge rejects WeChat mini-program short links. Product intake starts
from a web link copied or shared by the PDD App so the logged-in browser
extension can consume it directly.

## Receiver

The receiver listens on:

```text
http://0.0.0.0:8791/android/link
```

Health check:

```powershell
curl http://127.0.0.1:8791/android/health
```

iOS management terminal:

```text
http://127.0.0.1:8791/ios/terminal
```

Local POST test:

```powershell
curl -Method POST http://127.0.0.1:8791/android/link `
  -ContentType 'application/json' `
  -Body '{"link":"https://mobile.yangkeduo.com/goods.html?goods_id=680378531283","source":"local-test"}'
```

## Android Helper

The Android receiver URL is saved on the phone by the management screen. The
compiled default is:

```text
http://100.83.63.91:8791/android/link
```

APK version `2026.06.14.12` adds automatic receiver discovery. `检测连接`,
background heartbeat, pairing, link upload, artifact upload, and in-app update
now first use the saved receiver URL; if it is unreachable, the APK probes the
compiled Tailscale/LAN candidates and common current-WLAN gateway hosts, then
saves the first healthy receiver. This does not create or start the desktop
receiver; the PC receiver must already be running on `8791`.

Source default:

```text
mobile-link-bridge\src\com\spiritkin\mobilelinkbridge\BridgeConfig.java
```

## Push / Install APK

Local ADB install to your connected Android phone:

```powershell
adb devices
adb install -r .\mobile-link-bridge\out\mobile-link-bridge.apk
```

If `adb devices` is empty, install through the phone browser using the receiver
download endpoint:

```text
http://100.83.63.91:8791/android/apk
```

Or use the APK file directly:

```text
D:\SpiritKinAI\mobile-link-bridge\out\mobile-link-bridge.apk
```

If Android reports install blocked, unlock the phone and allow USB install or
unknown-source install for the current installer. Then rerun `adb install -r`.

Send the APK to another user:

```text
D:\SpiritKinAI\mobile-link-bridge\out\mobile-link-bridge.apk
```

They install it on their Android phone, open `SpiritKin Control Bridge`, and
pair it to their workspace.

Create pairing payload/token:

```powershell
curl "http://127.0.0.1:8791/pairing?workspace_id=local-ecommerce&format=json"
```

In the full desktop app (`D:\SpiritKinAI`), the WPF Mobile Management page also
has a `Workspace / Android 配对` section. Select or type a `workspace_id`, click
`生成 token`, then copy the binding bundle or open the pairing page. The
`workspace_id` is assigned by the owner/control terminal; the token is the
short-lived binding secret sent with that workspace id.

The lightweight iOS/Web control page at `GET /ios/terminal` has the same
workspace pairing flow:

- Enter the management token in `控制权限` when `SPIRITKIN_MANAGEMENT_TOKEN` is
  configured. Click `生成本机 iOS token` once per browser/device to bind a
  workspace-scoped `ios_terminal` token; subsequent page requests can use that
  token instead of keeping the global management token in the browser.
- Enter or select the `workspace_id` in `Workspace / Android 配对`.
- Choose a concrete expiry time, then click `生成配对 token` to create the
  QR/deep-link/manual fields inside the control page. The page converts that
  expiry into the control-plane TTL.
- Click `生成 Worker token` to create a `remote_worker` binding token and a
  ready-to-run `scripts\control_plane_worker.py --pairing-token ...` command.
- Click `打开配对页` to open a standalone pairing view generated from the same
  authorized token response.
- The current iOS PWA can also approve the Android APK promotion gate, start
  `android.command_lifecycle_acceptance.v1`, run the scheduler benchmark, and
  queue the same advanced Android operations exposed by the current APK:
  `android.ui_snapshot`, `android.screenshot.request_permission`,
  `pdd.launch`, `pdd.share_image`, and `pdd.create_listing`.
- The same section lists pending pairing tokens, active Android/Worker
  bindings, and recent Android device status from the control-plane snapshot.
- The same page now acts as the lightweight product entry:
  - `工作流运行记录` separates active runs from finished history, can cancel
    active runs, retry finished runs, delete individual run records, or clear
    finished history for the current workspace. Manual run creation is folded
    under `手动启动工作流`.
    New runs include a mode (`dry_run`, `debug`, or `production`) and runtime
    budget; production runs can be blocked by the workspace promote gate until
    approved from management.
  - `Workspace Runtime` writes the workspace runtime profile: planned venv path,
    dependency policy, and allowed local command list for Remote Worker/CLI
    adapters.
  - `Artifact 预览` lists recent artifacts, previews image artifacts inline, and
    opens text/XML artifacts in a readable preview window.
  - `填入命令` copies an artifact id into the Android command parameter field
    for `artifact.download`, `image.share_to_app`, `pdd.share_image`, or
    `pdd.create_listing`.
  - `Android 诊断` is derived from heartbeat and recent command results. It
    reports `ready`, `warning`, or `blocked`, highlights Accessibility/PDD
    foreground/module issues, and can queue safe corrective commands such as
    `pdd.launch`, `android.ui_snapshot`,
    `android.open_accessibility_settings`, or `android.open_bridge`.
    Diagnostic commands are targeted to the specific Android `device_id`, and
    buttons are enabled only when the device reports the corresponding
    capability in heartbeat.
    Failed commands are classified (`accessibility`, `artifact_download`,
    `selector_or_foreground`, or `unknown`) and expose retry actions that reuse
    the original operation and params on the same device.
  - `Android 命令` uses the server-side command catalog to show each operation's
    risk tier, required heartbeat capability, and requirements such as
    Accessibility, PDD package presence, or `artifact_id`. Commands can be
    broadcast to the workspace or targeted to one reported `device_id`.
    Targeted commands are preflighted on the control plane before queueing:
    workspace mismatches, missing device capability, missing PDD package, missing
    required artifact, or missing Accessibility state are rejected immediately.
    Broadcast high-risk commands are allowed but stored with a preflight warning
    because the final target device is resolved by heartbeat.
    Screenshot diagnostics now use two commands:
    `android.screenshot.request_permission` opens Android MediaProjection
    consent on the phone, and `android.screenshot.capture` uploads a PNG screen
    artifact after consent.

The response includes:

- `pairing_token`
- `deep_link` such as `spiritkin://pair?...`
- `qr_png_data_url`

When opened from a browser, the same pairing URL renders a human-readable
pairing page with QR code, `Receiver URL`, `Workspace ID`, and a copyable
`Pairing Token`:

```text
http://100.83.63.91:8791/pairing?workspace_id=local-ecommerce
```

Opening this page creates a new short-lived token. Existing bound phones do not
need a new token unless the APK is reinstalled, app data is cleared, the device
is changed, or the previous unbound token expires.

Pairing options:

- Open the `deep_link` on the Android phone.
- Or paste receiver URL, workspace ID, and token into the APK fields and tap
  `绑定配对`.

For managed deployments, start the receiver with token enforcement:

```powershell
$env:SPIRITKIN_REQUIRE_PAIRING_TOKEN = "1"
python scripts\mobile_link_receiver.py
```

After pairing:

1. Tap `检测连接`.
2. Tap `同步主控配置`.
3. Tap `打开无障碍设置`.
4. Enable `SpiritKin PDD Automation`. On some Android skins, the visible switch
   title may be the app label `SpiritKin Control Bridge`; the service
   description below it is `SpiritKin PDD Automation`.
5. Return to the app and verify heartbeat reports PDD accessibility enabled.

Use an address reachable from the phone. On this machine Tailscale currently
shows `100.83.63.91`. The old `8765/link` receiver conflicts with the runtime
event bridge, so the active project now uses the Android endpoint on
`8791/android/link`. You can enter either `http://100.83.63.91:8791/android`
or the full `/android/link` URL inside the app. From APK `2026.06.14.12`, the
app can also recover automatically by trying saved/default/Tailscale/LAN
receiver candidates when `检测连接` or heartbeat fails.

Management screen:

- Edit and save PC receiver URL.
- Check `/health`.
- Check and install Bridge APK updates from the current receiver. First-time
  install still uses browser/ADB; later updates can use the in-app `检查更新`
  button.
- Send a PDD link from the current clipboard.
- Register an image URL/path from clipboard text as an artifact. This does not
  read a raw bitmap from Android's clipboard; use Android share sheet for real
  image files.
- Share image files into the APK and upload them to `/android/artifact`.
  Successful uploads are shown in the phone's `我的上传记录`. APK
  `2026.06.23.1` adds `云端图片管理` in the same section: the bound Android
  device can refresh its own uploaded artifact files, copy an artifact id, and
  delete an individual cloud file. Multi-image shares are listed as separate
  file rows under the same artifact id, so one image can be removed without
  deleting the rest. Deleting the last file marks the artifact deleted in the
  control plane.
  New images are added by sharing from the Android album/file picker into the
  app. Replacing an image is delete old file, then share the replacement image.
  `清空上传记录` only clears local display history, and `清理图片缓存` only removes
  downloaded image files cached inside the Android app.
- Sync `/android/heartbeat` for device status, command drain, and command
  result return.
- Use `连接状态` for a short binding/sync summary. Detailed internal activity is
  kept for diagnostics but is not shown on the Android home screen.
- Desktop and iOS control surfaces now render the same workspace/device view:
  each workspace lists its Android phone executors, iOS controllers, remote
  execution nodes, active bindings, and pending pairing records. Use this view
  first when a device appears in the wrong workspace or a phone is not receiving
  commands.
- Share entry auto-forwards `ACTION_SEND text/plain` links from WeChat or PDD.
  Successful link forwarding is shown in the phone's `我的链接记录`. Links are
  stored in the bound control plane and mirrored to the legacy ecommerce queue
  only when the phone is bound to this local control plane.
  The phone can clear its local link record. This does not delete links already
  ingested by the bound control plane.
- Share entry uploads `ACTION_SEND image/*` and `ACTION_SEND_MULTIPLE image/*`
  as artifact files.
- Command sync can now consume artifact images:
  - `artifact.download` downloads an artifact file into Android cache.
  - `image.share_to_app` downloads an artifact file and opens an Android share
    intent, optionally targeting a package such as `com.xunmeng.pinduoduo`.
  - `artifact.cache.cleanup` clears cached artifact files on the phone.
- PDD Automation status is diagnostic:
  - `pdd_accessibility_granted` tells whether Android settings list the service
    as enabled.
  - `pdd_accessibility_connected` tells whether the AccessibilityService process
    is currently connected and usable.
  - The APK page now distinguishes "not granted" from "granted but service not
    connected"; if the latter appears, return to the app and sync, or toggle the
    service off/on in Android accessibility settings.
  - APK version `2026.06.14.4` refreshes the PDD Automation row after each
    status refresh/sync instead of keeping the initial Activity render.
- Automation module registry:
  - APK version `2026.06.14.5` adds a `Control Bridge Modules` registry view.
  - APK version `2026.06.14.8` renames the visible section to `可用工作流` and
    shows user-facing workflow usage instead of low-level module descriptions.
  - `同步主控配置` only pulls the controller-assigned workflow list and pending
    Android commands for this device. Workflow enable/disable is managed from
    desktop/iOS controllers per device.
  - Android Bridge displays execution modules and their readiness:
    `Command Sync`, `Artifact / Image`, `PDD Automation`, `UI Snapshot`, and
    `Extension Workflows`.
  - Heartbeat now reports `automation_modules` so desktop/iOS management can
    show module readiness before dispatching workflow steps.
  - Full workflow lifecycle management stays in desktop/iOS主控: create, pause,
    retry, cancel, assign to Android/Worker, and inspect results.
- In-app APK update:
  - APK version `2026.06.14.6` adds `检查更新`.
  - APK version `2026.06.14.7` fixes the update button spacing so it no longer
    sits too close to the surrounding controls on high-scale Android layouts.
  - APK version `2026.06.14.8` updates the workflow/help wording on the Android
    home screen.
  - APK version `2026.06.14.9` fixes Android endpoint URL derivation for update
    checks and shows clearer manifest/download/install progress.
  - APK version `2026.06.14.10` adds user-facing local history cards:
    `我的上传记录` and `我的链接记录`, and hides owner/control-plane internals from
    normal Android users.
- APK version `2026.06.14.11` adds local cleanup buttons for upload history,
    link history, and Android cached image files.
- APK version `2026.06.14.12` adds automatic receiver discovery and retry:
  health check, heartbeat, pairing, link forwarding, artifact upload, and
  update checks can recover when the saved PC URL is stale, as long as the
  desktop receiver is running and reachable on Tailscale or the current LAN.
- APK version `2026.06.15.1` upgrades the update path to manifest v2 and
  verifies package name, version, APK size, and SHA-256 before opening the
  system installer.
- APK version `2026.06.15.2` returns structured `android.ui_snapshot` command
  results with uploaded artifact metadata and reports the Accessibility
  foreground package in heartbeat state.
- APK version `2026.06.15.3` adds remote corrective commands for diagnostics:
  `android.open_accessibility_settings` and `android.open_bridge`, and reports
  them in heartbeat capabilities.
- APK version `2026.06.15.4` reports a heartbeat `command_catalog` with each
  supported command's risk tier, required capability, Accessibility/artifact
  requirements, and required package hints.
- APK version `2026.06.15.5` adds MediaProjection screenshot diagnostics:
  request permission from Android and upload current-screen PNG artifacts.
- APK version `2026.06.23.1` adds Android-side cloud image management for the
  current bound device: refresh uploaded files, copy artifact ids, and delete a
  single uploaded file from Artifact Store.
- APK version `2026.06.23.6` aligns Android visible wording with the desktop
  and iOS controllers: the phone is shown as `Android 手机端`, generic controls
  are grouped under `通用手机端能力`, and the app label no longer exposes the
  internal Bridge name.
- APK version `2026.06.23.7` aligns Android command execution with the cloud
  control plane. The phone now reports and executes `device.status`,
  `list_installed_apps`, `app.close` as a controlled no-op, `accessibility.tap`,
  and the standard `android.screenshot.capture` name while keeping the old
  `screenshot.capture` alias for compatibility.
- APK version `2026.06.23.8` fixes Android pairing diagnostics. When `绑定手机`
  fails, the phone now displays the control-plane error text, such as expired
  pairing token, role mismatch, or invalid token, instead of only showing
  `HTTP 400`.
- APK version `2026.06.23.9` renames `绑定手机` to `绑定到工作区`, explains that
  binding is only needed for first-time workspace/device registration, and
  translates `pairing token is not pending` into the actionable fix: generate a
  fresh Android pairing token from the controller.
- APK version `2026.06.23.10` adds Android-initiated binding. The phone can tap
  `请求配对码并绑定`; the control plane creates a short-lived Android token,
  binds the device, returns the bearer token, and the phone displays the token
  expiry. Manual pairing remains available under advanced connection settings.
- APK version `2026.06.23.11` simplifies the Android surface for operators:
  recent internal records are no longer shown on the home screen, and the
  controller manages per-device workflow lists independently.
- APK version `2026.06.23.12` renames that phone action to `同步主控配置`,
  stops presenting it as an on/off switch, shows binding validity as
  `YY-MM-DD HH:mm:ss` plus remaining time, and disables repeat pairing while the
  current binding is still valid.
- APK version `2026.06.23.13` improves the cloud controller page's auth
  handling: management actions now show an explicit top-page prompt when the
  browser is missing the cloud management token or is using a view-only iOS
  terminal token.
- APK version `2026.06.23.14` keeps the controller's active binding list clean:
  re-binding the same device replaces the previous active token, revoked or
  replaced bindings move to a collapsed history section, and the controller can
  clear historical binding/pairing records without touching current devices.
- APK version `2026.06.23.15` adds run-record management and pairing expiry
  clarity: the controller can delete workflow runs and clear finished run
  history, pairing tokens are generated from a concrete expiry time, and the
  Android Bridge clears a stale local binding when heartbeat receives 401/403
  so the operator is prompted to request and bind a fresh pairing token.
- APK version `2026.06.23.16` changes Android-initiated pairing to a controller
  approval flow. The phone creates a binding request, the controller chooses the
  expiry and approves it, then the phone receives the token and binds. A valid
  binding automatically starts background command sync, duplicate sync buttons
  are removed, and the phone can revoke its local/cloud binding from the same
  pairing button.
- APK version `2026.06.23.17` fixes controller/Android time display to local
  `YY-MM-DD HH:mm:ss` and folds long controller management lists into collapsible
  panels: workflow active/history runs, online diagnostics, and product image
  groups.
- APK version `2026.06.23.18` tightens controller cleanup and Android sync
  recovery: old iOS controller entries can be cleared from the controller,
  legacy workspace policies automatically allow Android status checks, and the
  Android Bridge restarts background sync when the app returns to foreground if
  the service was killed by the system.
- APK version `2026.06.23.19` restructures the cloud controller around
  workspace-first management: Android phones, pairing requests, pending pairing
  tokens, bindings, remote workers, workflow controls, diagnostics, and product
  image summaries are grouped under each workspace card, while iOS controller
  records move into a separate controller-management panel.
- APK version `2026.06.23.20` finishes the workspace-first controller move:
  full workflow run management and product image group management now live under
  each workspace card, and the old global workflow/image sections are removed to
  avoid duplicated lists.
- APK version `2026.06.23.21` removes duplicate device-level diagnostic and
  product-image summary panels from each Android device card; those are now
  shown only once at the workspace level.
- APK version `2026.06.23.22` makes Android command pickup near-immediate for
  controller actions such as opening PDD: the foreground heartbeat polls about
  every 5 seconds, avoids overlapping sync requests, and starts via the Android
  8+ foreground-service path.
- APK version `2026.06.23.23` switches Android command pickup to long polling:
  an idle heartbeat can wait for a queued command and return as soon as the
  controller enqueues it. The Android app now confirms before unpairing, and
  the controller silently refreshes device/workflow state while keeping button
  operation feedback visible.
- APK version `2026.06.25.1` fixes Android-initiated pairing and heartbeat
  recovery: the phone waits for controller approval, binds immediately after
  approval, starts background sync, and runs one immediate command sync. The
  app also suppresses automatic heartbeat while unbound, waiting for approval,
  or expired, so production servers no longer return repeated 401 heartbeat
  failures during pairing.
- APK version `2026.06.25.2` hardens heartbeat recovery. A temporary 401/403 no
  longer clears the local token immediately; the app keeps the token for retry
  and only clears the binding after three consecutive auth failures. The
  control plane also lets valid Android tokens in a recovery window heartbeat
  back to active, so reinstall/rebind recovery does not break existing
  heartbeat.
- APK version `2026.06.25.7` is the post-refactor mobile UI candidate. The app
  uses three persistent bottom destinations (`状态` / `工作流` / `连接`), keeps
  advanced connection and workflow modules collapsed until needed, follows the
  shared light/dark token resources, and enforces a 48dp minimum touch target
  for ordinary actions. The signed package was rebuilt from current source
  after the Edge/Android completion audit. It is not the promoted download
  until the Android APK release gate receives explicit human approval.
- Cloud controller package `2026.06.25.1` consolidates controller-only tools
  into one `主控端管理` panel: control token, current iOS/Web controller records,
  remote execution runtime, controller-side image upload, manual Android
  troubleshooting commands, and controller status output now live together.
  Workspace cards remain responsible for Android phones, pairing, per-device
  workflows, diagnostics, and product image groups. Desktop mobile management
  now forwards the same add/delete per-device workflow actions to the control
  plane so desktop and iOS/Web controllers use the same backend operations.
- Cloud controller package `2026.06.25.2` keeps expanded controller sections
  open across silent auto-refreshes by persisting `details` state in the browser.
  It also adds desktop-side pairing request handling so desktop controllers can
  approve/reject Android binding requests and clear pairing/binding history.

Android APK and cloud package versions are intentionally independent. The
latest built Android APK candidate is `2026.06.25.7`; the currently promoted
download remains `2026.06.25.4`. Cloud controller packages use the
separate `spiritkin-cloud-YYYY.MM.DD.N.tar.gz` name. Only bump
`mobile-link-bridge` `versionCode` / `versionName` and rebuild the APK when
files under `mobile-link-bridge/src`, `mobile-link-bridge/res`, or
`mobile-link-bridge/AndroidManifest.xml` change.

## Workspace / Device Workflow Controls

Background sync is only the transport channel. It means the Android phone can
heartbeat, receive queued commands, and return command results. It does not
mean an ecommerce workflow is allowed to run.

Workflow enablement is scoped by:

```text
workspace_id + Android device_id + workflow_id
```

The default ecommerce workflow id is:

```text
ecommerce.auto_listing.v1
```

Desktop Mobile Management and `/ios/terminal` now use the same control-plane
actions:

- `add_device_workflow`: add or enable one workflow on one Android phone in one
  workspace.
- `delete_device_workflow`: remove one workflow from one Android phone in one
  workspace.
- `set_device_workflow_state`: enable or pause one workflow on one Android
  phone in one workspace.
- `repair_device_workflow`: queue a targeted repair/check command for that
  device, such as `device.status`, `app.launch` for PDD,
  `android.open_accessibility_settings`, or
  `android.screenshot.request_permission`.
- `clear_android_commands`: clear queued/delivered Android commands for a
  workspace, optionally scoped to one device.
- `approve_pairing_request` / `reject_pairing_request`: approve or reject an
  Android-initiated binding request.
- `clear_pairing_history` / `clear_binding_history`: clean old pairing/binding
  records for the selected workspace without deleting active device state.

When `start_workflow_run` receives `inputs.device_id` for a paused workflow, the
control plane rejects the run before dispatching worker or Android commands.
This keeps each Android phone's workflow state independent even when multiple
phones are bound to the same workspace.

In the desktop app, select a row under `工作区与设备` whose title includes
`Android 手机端`. The selected row fills `工作区`, `手机编号`, and the manual
command target, then the `选中设备工作流管理` buttons operate only on that
phone. In `/ios/terminal`, each Android device card shows the same workflow
status and exposes the same enable/pause/repair actions.

Cleanup scope:

- `清空上传记录` and `清空链接记录` remove only this phone's local display history.
- `清理图片缓存` removes downloaded/shared image files cached inside the Android
  app.
- `云端图片管理` deletes cloud artifact files uploaded by the currently bound
  Android device in its workspace. It cannot list or delete another Android
  device's uploads, iOS uploads, worker artifacts, workflow audit artifacts, or
  another workspace's files.
- Ingested links and non-Android artifacts already stored in a control plane are
  still managed by that control plane's desktop/iOS/admin cleanup policy.
- The control plane serves `GET /android/apk/manifest` and `GET /android/apk`.
- The update manifest is versioned (`manifest_version: 2`) and now includes
  package name, SHA-256, size, compatibility hints, and rollback metadata.
- The control plane adds `serving_validation` to the served manifest. It checks
  the local `mobile-link-bridge.apk` file name, size, and SHA-256 against the
  release manifest so a stale or swapped APK is visible before Android installs
  it.
- `mobile-link-bridge/build.ps1` writes release metadata beside the APK:
  `out/release-manifest.json`, `out/mobile-link-bridge.apk.sha256`,
  `out/release-history.json`, and archived signed APKs under `out/releases/`.
- `GET /android/apk` serves the latest signed APK. Add
  `?version_code=<code>` to download an archived signed release listed in
  `release-history.json`.
- The Android updater verifies package name, version, file size, and SHA-256
  before launching the system installer.
- If Android blocks installation, the APK opens the system "install unknown
  apps" permission page; after granting it, tap `检查更新` again.
  - The receiver URL must be reachable from the phone, for example the current
    Tailscale URL `http://100.83.63.91:8791/android/link`.

Supported management endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /` / `GET /control` | unified product entry: status summary, pairing, artifact, workflow, Android diagnostics, APK update links |
| `GET /pairing` | browser pairing page; add `format=json` for token/deep-link/QR JSON |
| `GET /android/apk/manifest` | latest Android Bridge APK manifest for in-app update |
| `GET /android/apk` | latest Android Bridge APK download; supports `?version_code=<code>` for archived releases |
| `GET /android/artifacts` | list cloud artifact files uploaded by the currently bound Android device; supports `format=lines` for APK UI |
| `GET /worker/package/manifest` | latest Remote Worker zip manifest with download URL, SHA-256, size, and embedded file manifest |
| `GET /worker/package` | latest Remote Worker zip package download |
| `POST /android/pair` | bind Android Bridge to a pairing token |
| `POST /android/link` | PDD link intake |
| `POST /android/heartbeat` | device state and command sync |
| `POST /android/artifact` | Android image/ref artifact upload |
| `POST /android/artifacts/delete-file` | delete one uploaded cloud artifact file owned by the currently bound Android device |
| `GET /android/artifact/<artifact_id>` | Android artifact file download |
| `POST /android/command` | queue Android command |
| `GET /ios/terminal` / `GET /ios/control` | full iOS/Web management workspace used by the unified entry |
| `GET /ios/control/pairing` | management-token-gated iOS terminal pairing; management or workspace-scoped iOS terminal token can create Android/Worker pairing payloads |
| `POST /ios/control/pair` | bind an iOS/Web control terminal to an `ios_terminal` pairing token |
| `POST /ios/control/action` | management or policy-scoped iOS terminal actions including workflow start/cancel/retry, workspace policy update, cleanup, state validation |
| `POST /worker/pair` | bind a Remote Worker to a `remote_worker` pairing token |
| `POST /worker/heartbeat` | remote worker task claim; also reclaims expired assigned task leases |
| `POST /worker/result` | remote worker task result; rejected after task lease expiry |

ADB trigger for clipboard mode:

```powershell
adb shell am start -n com.spiritkin.mobilelinkbridge/.MainActivity
```

ADB trigger for share-text mode:

```powershell
adb shell 'am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283" -n com.spiritkin.mobilelinkbridge/.ShareActivity'
```

Wrap the remote `am start` command in quotes when the text contains `#` or `&`;
otherwise Android's shell treats them as comment/background operators.

ADB trigger for command heartbeat:

```powershell
adb shell am start -n com.spiritkin.mobilelinkbridge/.MainActivity
```

Image sharing should normally be tested from the Android share sheet because it
uses `content://` read permissions granted by Android.

## Notes

- The bridge stores only the extracted Pinduoduo link, not the full share text.
- Shared images are stored as control-plane artifacts with a workspace
  `artifact_policy` default TTL and byte/count quotas.
- Android can now download an artifact by `artifact_id` and share it via a
  standard Android share intent. Deep in-app PDD field placement still needs a
  dedicated accessibility/ADB/Appium layer.
- Pairing is available:
  - Browser `GET /pairing?workspace_id=<id>` shows a pairing page with QR,
    token, receiver URL, and deep link.
  - API `GET /pairing?workspace_id=<id>&format=json` returns `pairing_token`, a
    `spiritkin://pair?...` deep link, and `qr_png_data_url`.
  - Android Bridge can open the deep link, save `workspace_id` and token, and
    call `/android/pair`.
  - Android requests send `Authorization: Bearer <token>`.
  - Local development accepts unpaired Android calls by default. Set
    `SPIRITKIN_REQUIRE_PAIRING_TOKEN=1` before starting
    `scripts\mobile_link_receiver.py` to require pairing.
- Management token gate:
  - Set `SPIRITKIN_MANAGEMENT_TOKEN=<secret>` before starting the receiver to
    require an owner token on `/management/*` and `/ios/control/*`.
  - Send it as `Authorization: Bearer <secret>` or `X-SpiritKin-Token`.
  - When this token is configured, owner terminal token generation is
    management-gated: `/pairing`, `/management/pairing`, and
    `/ios/control/pairing?device_role=ios_terminal` require the management
    token. Local development stays open when no management token is configured.
  - A bound `ios_terminal` token can call `/ios/control/snapshot`,
    `/ios/control/action-log`, `/ios/control/action`, `/mobile/artifacts`, and
    `/ios/control/pairing` for Android/Worker tokens only inside its paired
    workspace. A mismatched `workspace_id` is rejected. It cannot mint another
    `ios_terminal` owner token.
  - Bound `ios_terminal` control actions are checked against workspace
    `execution_policy.control_allowed_actions` and
    `execution_policy.control_denied_actions`. The configured management token
    remains the owner role and bypasses those per-workspace control-action
    allow/deny lists.
  - Android pairing tokens are device tokens, not owner tokens. They can only
    route Android commands inside their bound workspace; cross-workspace command
    dispatch requires the management token.
  - Remote Worker tokens use the same pairing store with `device_role` set to
    `remote_worker`. A bound Worker token overrides caller-supplied
    `workspace_id` and `worker_id` on heartbeat/result, so a worker cannot claim
    or report tasks outside its paired workspace by changing JSON fields.
  - Set `SPIRITKIN_REQUIRE_WORKER_TOKEN=1` to require Remote Worker pairing
    tokens on `/worker/heartbeat` and `/worker/result`.
  - Set `SPIRITKIN_PRODUCTION_MODE=1` for managed deployments. Production mode
    defaults Android Bridge and Remote Worker endpoints to token-required, gates
    pairing pages/actions behind the management token, and rejects owner/control
    actions if `SPIRITKIN_MANAGEMENT_TOKEN` is missing. Local development remains
    open when production mode and explicit token requirements are not set.
- Worker task leases:
  - A claimed task gets `lease_expires_at` from `budget.max_runtime_seconds`
    plus 300 seconds of grace.
  - The next Worker heartbeat or `cleanup_state` requeues expired assigned
    tasks while `budget.max_retries` remains.
  - Once retries are exhausted, the Worker task and Workflow run fail with
    `worker_task_lease_expired`.
- Worker result outbox:
  - `scripts\control_plane_worker.py` writes each task result to a local JSON
    outbox before posting `/worker/result`.
  - The outbox file is deleted only after the server accepts the result.
  - `--state-dir <path>` stores worker runtime state, including the bound Worker
    token after `--pairing-token` succeeds. The default outbox becomes
    `<state-dir>\outbox`.
  - `--write-config <path>` writes a non-secret Worker config. After first
    pairing, restart with `--config <path>` and the token is restored from
    `<state-dir>\runtime-state.json`.
  - `--max-consecutive-errors` and `--error-backoff` let a process manager decide
    when to restart a failing long-running Worker.
  - `--release-manifest <path>` writes signed/versioned Worker package metadata:
    package name, version, entrypoint, file SHA-256 hashes, file sizes, default
    capabilities, and optional `hmac-sha256` signature from
    `SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET`.
  - `--package-zip <path>` builds a distributable Worker zip. It contains the
    Worker script, docs, `worker.example.json`, `run-worker.cmd`,
    `install-worker-gui.ps1`, `setup-worker.ps1`, `update-worker.ps1`,
    `install-worker-scheduled-task.ps1`, and `worker-release-manifest.json`.
  - `install-worker-gui.ps1` opens a Windows Forms setup flow for Server URL,
    Workspace ID, Worker ID, pairing token, install directory, and optional
    Scheduled Task registration. It delegates installation to `setup-worker.ps1`.
  - `setup-worker.ps1` writes a target-machine `worker.json`, can perform the
    first `--pairing-token` bind, and can call the Scheduled Task installer.
    `update-worker.ps1 -ManifestUrl <url>` downloads the hosted package,
    verifies SHA-256, and expands it into the install directory.
  - `setup-worker.ps1` also writes `update_manifest_url` into `worker.json`.
    Set `auto_update: true` or pass `--auto-update` to check that manifest before
    the Worker heartbeat loop. If a newer package is available, the Worker
    verifies SHA-256, extracts the zip over the install directory, records the
    update in `runtime-state.json`, and exits so the process manager can restart
    it.
  - The control plane hosts the same installable Worker package through
    `GET /worker/package/manifest` and `GET /worker/package`. The manifest
    includes the zip download URL, outer zip SHA-256/size, embedded file
    manifest, optional HMAC signature, and serving validation.
  - Remote Worker pairing JSON includes `package_manifest_url` and
    `package_download_url`, plus `setup_command` and `gui_install_command` for
    the extracted zip, so another machine can fetch the Worker bundle before
    running the pairing command.
  - `workflow.execute.auto_listing` now has a production Worker adapter path:
    dry-run/debug still returns planned Android commands only, while
    `--allow-production` with production governance queues those Android commands
    through `/ios/control/action` and reports `queued_android_commands` command
    IDs in the Worker result.
  - Use `--outbox-dir <path>` to override the default
    `state\workers\<worker_id>\outbox`.
- Artifact access boundary:
  - `POST /android/artifact` and `GET /android/artifact/<id>` use the Android
    pairing token when present. A bound Android token always writes and reads
    inside its bound `workspace_id`; a mismatched `?workspace_id=` is rejected.
  - `POST /mobile/artifacts` and `GET /mobile/artifacts/<id>` are owner/mobile
    control-plane endpoints. If `SPIRITKIN_MANAGEMENT_TOKEN` is set, these
    endpoints require either the management token or a bound `ios_terminal`
    token; bound terminal tokens are scoped to their workspace.
  - Credential redline: PDD/Douyin cookies, browser profiles, passwords,
    session data, access/refresh tokens, and local profile paths must never be
    uploaded to the control plane. Remote worker results are reduced to a
    whitelist before they enter the durable outbox, and the control plane also
    rejects sensitive key shapes on `/worker/result`, Android heartbeat/result
    payloads, and artifact uploads. `pairing_token` is allowed only as an
    endpoint authentication field; uploaded content must not contain tokens.
  - Artifact writes enforce workspace `artifact_policy` quotas:
    `max_workspace_bytes`, `max_workspace_artifacts`, and `max_file_bytes`.
    The default backend is `local_disk`; `filesystem_object_store` can point at
    a mounted object-store style directory through `backend_root`.
  - Local development remains open when neither pairing enforcement nor a
    management token is configured.
- Background heartbeat is available:
  - The APK includes `HeartbeatService`, a foreground service that syncs every
    about 1 second between long-poll requests while enabled. Idle heartbeats can
    wait server-side for a command, so controller actions normally arrive
    without pressing `立即同步一次`.
  - The management screen has a background heartbeat toggle.
- PDD automation foundation:
  - `pdd.launch` starts PDD.
  - `pdd.share_image` downloads an artifact image and shares it to
    `com.xunmeng.pinduoduo`.
  - `pdd.create_listing` opens the PDD execution entry and hands off to the
    accessibility service when enabled.
  - `PddAutomationService` is registered as an Android AccessibilityService.
  - It can dump the active UI tree with `android.ui_snapshot`, upload that
    text tree as an Artifact, and return `artifact_id`, `download_url`,
    `foreground_package`, and `snapshot_chars` in the command result.
  - The control plane turns heartbeat fields and failed command results into an
    Android diagnostic summary:
    `accessibility.not_granted`, `accessibility.not_connected`,
    `foreground.not_pdd`, `ui_snapshot.needs_accessibility`, `pdd.missing`, and
    `command.failed`.
  - It can search text/content descriptions, find nearby edit fields, set
    title/price/description, scroll, and only click publish/submit when
    `allow_submit=true`.
  - It is conservative: missing fields/buttons cause a failed command result,
    not random clicking.
  - PDD uses SurfaceView/dynamic views in several screens, so real devices still
    need iterative selector tuning from uploaded UI snapshots.
  - Bitmap screenshots are available through the MediaProjection flow:
    queue `android.screenshot.request_permission` to ask the user for consent,
    then queue `android.screenshot.capture` to upload a raw PNG screen artifact.
    ADB fallback remains useful when MediaProjection consent cannot be granted.
- Desktop-side ADB fallback is available when USB/Wireless ADB is enabled:
  `python scripts\android_adb_capture.py --workspace-id local-ecommerce`
  captures `screencap -p`, `uiautomator dump`, and foreground window metadata,
  then stores them as one `android_adb_diagnostic` Artifact in the control
  plane. Use `--dry-run` to inspect capture metadata without writing state.
- The APK is signed with `mobile-link-bridge\debug.keystore`; keep this file if
  you want `adb install -r` to keep working across rebuilds.

## Browser Extension Handoff

PDD web links returned by the Android PDD App are consumed first by the
project-owned Manifest V3 extension under
`browser-extension/pdd-product-extractor`. The extension uses a scoped
`browser_extension` pairing token, automatically claims PDD HTTP(S) links,
reads `window.rawData` in the already logged-in browser profile, and returns
product JSON as a control-plane Artifact and ecommerce task artifact.

See `docs/pdd_browser_extension.md` for installation, endpoint and data-gate
details.
