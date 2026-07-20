# iOS Terminal Strategy

The preserved Terminal capability is embedded in the native **SpiritKin** app as the Control destination. The existing bundle identifier, URL scheme, control APIs, workflow/device/artifact screens, and raw snapshot tools remain compatible. iPhone uses the system tab bar and iPad uses a system sidebar rather than stretching the phone layout.

The current machine has no Mac/Xcode environment, so the practical iPhone terminal is the installable web/PWA surface at:

```text
/ios/terminal
```

Open it in Safari and use "Add to Home Screen". It now exposes:

- An iOS-only 3D Avatar runtime stage backed by `avatar_3d.html?embed=1&float=1`.
- The shared v4 light/dark color roles used by desktop, Web, and Android Bridge.
- PWA manifest at `/ios/terminal.webmanifest`
- Service worker at `/ios/service-worker.js`
- Apple touch icon at `/ios/apple-touch-icon.png`
- iOS standalone web-app meta tags

Set `SPIRITKIN_IOS_AVATAR_URL` when the Avatar runtime is exposed through a
different HTTPS/WSS route. Use a dedicated read-only runtime credential; do not
reuse the control-plane management token in the iframe URL.

The native SwiftUI source is preserved for a future Mac/cloud-Mac signing pass. Source lives in:

```text
ios/SpiritKinTerminal/
```

The native app keeps its control surfaces native. Only the 3D Avatar stage uses
`WKWebView` to reuse the shared runtime renderer. It reuses the current backend
control APIs:

- `GET /ios/native/snapshot`
- `POST /ios/native/action`
- `GET /ios/control/pairing`
- `GET /ios/schemas/shortcuts.json`
- `GET /ios/jobs/<job_id>`
- `POST /ios/shortcut`
- `POST /ios/control/action`
- `POST /mobile/artifacts`

The main shell now uses the same four destinations as the PWA prototype:
`对话 / 板块 / 设备 / 我的`. The conversation destination keeps the 3D Avatar
visible, sends messages through `POST /ios/shortcut`, and leaves the existing
control dashboard below the conversation surface. Artifacts, connection
settings, raw snapshots, and the iOS automation catalog remain available under
`我的` instead of occupying additional top-level tabs.

The board is domain-scoped rather than a flat workflow picker. It groups
registered workflows into `电商`, `内容与媒体`, `开发与自动化`, `系统与治理`,
and `其他`; the `电商` domain owns the full `/ios/terminal` surface for product
assets, listing review, Android listing operations, pairing, and audit. Native
conversation management is backed by the shared desktop state through
`GET/POST /ios/sessions`, including session switching, creation, archiving, and
deletion. Each Ask Spirit request carries the selected `session_id`.

Native config import accepts a URL scheme, a raw Base URL, or pasted JSON:

```text
spiritkin://pair?server_url=http%3A%2F%2F100.x.y.z%3A8791&workspace_id=local-ecommerce&pairing_token=one-time-code&device_role=ios_terminal
```

```json
{"base_url":"http://100.x.y.z:8791","workspace_id":"local-ecommerce","pairing_token":"one-time-code"}
```

## Build on macOS

```bash
brew install xcodegen
cd ios/SpiritKinTerminal
xcodegen generate
open SpiritKinTerminal.xcodeproj
```

For a repeatable source-level build gate, run the manual GitHub Actions workflow
`.github/workflows/ios-native.yml`. It generates the Xcode project and builds an
unsigned iOS Simulator target. It does not claim device signing, pairing,
background refresh, or real iPhone acceptance.

Then set Apple signing in Xcode and run on a real device.

This step is blocked until a Mac, cloud Mac, or CI runner with Xcode signing is available.

## Current Native Scope

- Full-width 3D Avatar stage on the Control destination. Android remains a Bridge/execution endpoint and does not embed this stage.
- Four-destination iPhone tab shell and iPad sidebar: Conversation, Workflows, Devices, and Profile.
- Native Ask Spirit conversation composer backed by `POST /ios/shortcut`. Pure conversation uses a bounded one-model-call path with thinking disabled; explicit tool, device, file, Terminal, status, and Workflow requests stay on the complete desktop Runtime path.
- Shared conversation sessions backed by `GET/POST /ios/sessions`.
- Domain-scoped workflow board with the e-commerce Terminal under the e-commerce domain.
- System/day/night appearance selector backed by the shared v4 semantic colors.
- iOS automation center whose six governed actions are loaded from `GET /ios/schemas/shortcuts.json`, plus native `Ask Spirit` and `Check Spirit Status` App Intents registered through `AppShortcutsProvider` for Shortcuts, Siri, and Spotlight.
- Status dashboard for services, module summary, safety, workflows, Android companion, and snapshot cache metadata.
- Workflow start form and free composition form.
- Android command queue, including `workflow.android_step` wrapper commands.
- Photo upload to mobile artifacts with base64 JSON payload.
- Settings for Base URL and `X-SpiritKin-iOS-Token`.
- Runtime Host registry/election and governed Workflow migration requests; the iOS controller never receives execution-host fencing secrets or resumes a Workflow directly.
- A RealityKit + ARKit World Observation surface that publishes structured pose/plane/depth-availability/location summaries to `/ios/observations`; RGB frames, depth maps, point clouds and recordings are not uploaded.
- `spiritkin://pair` / `spiritkin-terminal://pair` and pasted JSON config import for `server_url`/`base_url`, `pairing_token`, `token`, and `workspace_id`; pairing exchanges the one-time code for a workspace-scoped iOS token.
- Remote Worker one-time pairing creation, command sharing, expiry display, and cancellation from the native Devices surface. Pairing grants no automatic high-risk execution.
- Local notification permission/test hook.
- Background refresh task registration/scheduling skeleton.
- Safety soft stop, hard stop, and hard-stop resume confirmation controls.
- Workflow run detail view with node state, event list, governed review/signal/retry controls, and raw JSON.
- Workflow execution remains owned by the elected Runtime Host. iOS can create, inspect, approve, signal, retry, reset, archive, and delete scoped runs, but cannot directly advance a node or bypass the Host fencing token.
- Recent mobile artifact list with image preview through `/mobile/artifacts/<artifact_id>`.
- Raw snapshot debug view.
- Minimal AppIcon asset catalog for Xcode builds.

## Verification Status

- Windows source-contract tests cover App Intent registration, the Runtime-backed shortcut catalog, and Remote Worker pairing controls.
- The live production receiver currently returns six governed shortcut definitions and a healthy `/health` response.
- A paired local iOS request completed the authenticated asynchronous Ask Spirit path in 3.28 seconds on the managed 35B llama.cpp model. This is a local-machine result, not a cellular-network SLA.
- No Swift compiler or Xcode is available on the current Windows host. Simulator compilation, signing, Siri/Spotlight discovery, and real-device behavior are therefore not claimed as verified.

For no-Mac delivery, use the PWA/Shortcuts terminal. The remaining native gap is macOS/Xcode compilation and signing, real-device networking, notification/background behavior, and production acceptance. The source is implementation-ready for that gate, but is not yet an App Store-ready build.

## Remaining Implementation Gaps

- Generate the Xcode project and fix any compile-time Swift/XcodeGen issues found on macOS.
- Optional no-Mac path: keep improving `/ios/terminal` PWA until native signing is available.
- Add QR scanning to the existing one-time controller pairing import and show server-side expiry validation details before exchange.
- Add richer artifact download/open/share actions.
- Add push notification/server-driven event delivery; current notification support is local-only.
- Implement meaningful background refresh work; current BGTask handler only registers/schedules the capability.
- Add certificate/HTTPS guidance and reject unsafe public HTTP by policy.
- Add UI tests or snapshot tests once macOS CI is available.

## Real-Device Acceptance

Do not mark this complete until tested on iPhone:

1. Connect via Tailscale/LAN to the PC iOS endpoint.
2. Refresh snapshot with and without `SPIRITKIN_IOS_TOKEN`.
3. Save a composed workflow and start a run.
4. Queue `app.launch`, `url.open`, `clipboard.write`, and `workflow.android_step` Android commands.
5. Upload a photo and verify it appears in `state/mobile-artifacts`.
6. Run the full loop: iPhone -> workflow -> Android command -> Android result -> iPhone refreshed status.
7. Confirm `Ask Spirit` and `Check Spirit Status` appear in Shortcuts/Siri/Spotlight and return live Runtime results.
8. Generate and cancel a Remote Worker pairing code, then pair a real Worker to the same workspace and verify scoped monitoring.
