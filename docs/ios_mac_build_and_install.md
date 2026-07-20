# iOS Mac Build and Installation

## What to upload

Clone or transfer the SpiritKin source repository to the rented Mac. At a
minimum the build needs `ios/SpiritKinTerminal`, but the recommended unit is the
repository without local runtime state and credentials.

Never upload:

- `state/` runtime data or logs
- `.env` and bearer tokens
- browser profiles, cookies, store passwords, or WeChat credentials
- local model weights unless the Mac build explicitly needs them

## Generate the Xcode project

```bash
brew install xcodegen
cd ios/SpiritKinTerminal
xcodegen generate
open SpiritKinTerminal.xcodeproj
```

The workspace contains the main `SpiritKinTerminal` application and the
`SpiritKinShare` Share Extension.

## Configure signing

In Xcode, select both targets and configure **Signing & Capabilities**:

1. Select the Apple Developer Team.
2. Use unique bundle identifiers owned by that Team.
3. Register one App Group and assign it to both targets.
4. Replace `group.com.spiritkin.shared` in both entitlement files when the
   registered App Group uses a different identifier.

## Install

- Local or USB-forwarded iPhone: select the device and press Run. Trust the
  developer profile on the phone if iOS asks.
- Remote Mac without phone USB access: Archive and upload to TestFlight.
- Ad Hoc: register the phone UDID and export with an Ad Hoc provisioning
  profile.
- Simulator: useful for UI and API smoke tests, but it does not prove ARKit,
  camera, LiDAR, background delivery, or real signing.

A free Personal Team can install development builds for temporary testing.
TestFlight, Ad Hoc, and App Store distribution require a paid Apple Developer
membership.

## Connect to the Windows Runtime

Pair the iOS app with a workspace-scoped `ios_terminal` code. Use an HTTPS or
private-network address reachable from the iPhone. Do not enter the Windows
loopback address unless the Runtime actually runs on that iPhone.

The native app uses ARKit directly. Shortcuts and App Intents provide bounded,
user-invoked automation; they do not bypass the iOS sandbox or arbitrarily tap
inside other apps.
