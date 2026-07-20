# SpiritKin for iOS

The native app keeps every previous SpiritKin Terminal control surface under the **Control** destination while presenting SpiritKin as the top-level product. Compact iPhone layouts use the system tab bar; regular-width iPad layouts use `NavigationSplitView` with a native sidebar.

Native SwiftUI control terminal for SpiritKin. It intentionally uses the same HTTP APIs as the temporary `/ios/terminal` web surface:

- `GET /ios/control/snapshot`
- `GET /ios/control/pairing` (workspace-scoped Remote Worker pairing creation)
- `POST /ios/control/action`
- `POST /ios/control/pair` (one-time `ios_terminal` pairing code exchange)
- `GET /ios/schemas/shortcuts.json`, `/health`, `/ios/jobs/<job_id>`
- `POST /ios/shortcut`
- `GET/POST /ios/sessions`, `/ios/domains`, `/ios/capabilities`, `/ios/pools`
- `GET/POST /ios/resources`, `/ios/ecommerce`, `/ios/monitor`, `/ios/growth`, `/ios/music`
- `GET /ios/channels`
- `POST /mobile/artifacts`

This directory is source-only on Windows. Generate and build the Xcode project on macOS:

```bash
brew install xcodegen
cd ios/SpiritKinTerminal
xcodegen generate
open SpiritKinTerminal.xcodeproj
```

Set a valid signing team in Xcode before installing to a device.

## Rent a Mac, sign, and install

Send or clone the whole repository on the Mac. The iOS target lives in
`ios/SpiritKinTerminal`, but keeping the repository layout avoids losing CI,
documentation, and future shared sources. Do not send `state/`, model files,
tokens, cookies, or `.env` credentials.

1. Install Xcode and XcodeGen, then generate the project:

   ```bash
   brew install xcodegen
   cd ios/SpiritKinTerminal
   xcodegen generate
   open SpiritKinTerminal.xcodeproj
   ```

2. In **Signing & Capabilities**, select your Apple Developer Team for both
   `SpiritKinTerminal` and `SpiritKinShare`. Replace the sample bundle IDs and
   App Group with identifiers registered under that Team. Both targets must use
   the same App Group.
3. If the rented Mac can access the iPhone through USB, trust the Mac on the
   phone, select the phone as the run destination, and press Run. With a free
   Personal Team this is suitable only for short-lived development installs.
4. For a remote rented Mac with no physical USB path, archive with
   **Product > Archive** and distribute through TestFlight. Ad Hoc distribution
   is also possible when the device UDID is registered in the provisioning
   profile. TestFlight, Ad Hoc, and App Store distribution require a paid Apple
   Developer membership.
5. The iPhone must reach the Runtime through an HTTPS URL or a private network
   such as Tailscale. `127.0.0.1` on the iPhone means the iPhone itself, not the
   Windows desktop.

The repository also includes a manual macOS workflow at
`.github/workflows/ios-native.yml`. Run it from GitHub Actions to install
XcodeGen, generate the project, and perform an unsigned iOS Simulator build.
That build gate proves source/project compatibility only; pairing, network,
notifications, background refresh, signing, and real-device acceptance remain
separate checks.

## Runtime Setup

1. Start the desktop services and mobile control receiver:

   ```powershell
   python scripts/start_desktop_console.py
   ```

   The receiver exposes the native control APIs on port `8791` by default. The
   `8792` port is the optional standalone iOS endpoint and must not be started
   on the same port as the static frontend server.

2. On the iPhone, set `Base URL` to the endpoint shown by desktop mobile management, for example `http://100.x.y.z:8791`.
3. For a real phone or pairing-enforced receiver, generate an `ios_terminal`
   pairing code from desktop mobile management, paste it into `绑定 iOS 主控`,
   and let the app exchange it for a workspace-scoped access token. Do not use
   the desktop command-gateway session token as the iOS token.
4. Use Refresh to load service, module, workflow, Android bridge, and safety state.

## Current Scope

- Native SwiftUI control shell. Only the full-width 3D Avatar stage uses
  `WKWebView` so iOS reuses the same runtime renderer and model as desktop/Web.
- Snapshot dashboard for services, modules, safety, workflows, Android companion, and recent commands.
- Workflow start and free composition actions.
- Workspace-scoped domain, capability, Skill/Workflow pool CRUD (global/built-in entries remain read-only).
- Growth Runtime candidate status, ecommerce queue/resource/monitoring views, and bounded Avatar lifecycle.
- Runtime Host and Checkpoint visibility with governed migration requests; execution lease secrets remain host-only.
- RealityKit/ARKit Observation Provider and shared World State. It publishes a bounded structured observation every two seconds while the scanner is visible and never uploads RGB frames, depth maps, point clouds, or video.
- Runtime-backed six-action Shortcuts catalog plus native Ask, health,
  clipboard, notification, and battery App Intents registered for Shortcuts,
  Siri, and Spotlight.
- Share Extension inbox for files and images. The extension stores data in an
  App Group without Runtime credentials; the main app uploads pending items to
  the workspace-scoped Artifact Store when it becomes active.
- Owner-only desktop music queue control and redacted WeChat iLink channel
  status. These controls are not part of tenant ecommerce consoles.
- Shared desktop conversation creation, switching, archiving, deletion, and asynchronous reply polling.
- System/light/dark appearance selection and source-backed model/worker status labels.
- Remote Worker one-time pairing generation, command sharing, expiry display, and cancellation.
- Android command queue controls, including `workflow.android_step`.
- Photo/file upload via base64 JSON payload to `/mobile/artifacts`.

Real-device acceptance is still required before this is considered production-ready.
