# Desktop Atelier Direct-Fidelity QA

## Linear 3D, Call Semantics, And CMD Card Pass - 2026-07-15

### Evidence

- Source command-card reference: `C:\Users\ADMINI~1\AppData\Local\Temp\codex-clipboard-a890dcec-89fc-48a2-a6b5-82007da6dc42.png`
- Live CMD implementation: `D:\SpiritKinAI\tmp\ui-qa\final-live-cmd-card-clean2.png`
- Source and implementation in one focused comparison: `D:\SpiritKinAI\tmp\ui-qa\cmd-card-reference-comparison.png`
- Final collapsed 3D state: `D:\SpiritKinAI\tmp\ui-qa\final-collapsed-3d.png`
- Linear frame evidence: `D:\SpiritKinAI\state\logs\desktop_actions.jsonl`
- Viewport: 1480 x 940, native WPF desktop, Atelier light theme.
- State: real `打开 cmd` request executed by `local_pc`; the launched Command Prompt was closed after capture.

### Findings

No actionable P0, P1, or P2 findings remain.

- The 3D region now reallocates all three rows together over 320ms. Collapse samples were `p=0.000: 600/6/300`, `p=0.291: 436/4.3/465.8`, `p=0.513: 310.9/2.9/592.2`, `p=0.785: 157.3/1.3/747.4`, and `p=1.000: 36/0/870`; every value matches direct linear interpolation. Expansion produced the same linear relationship in reverse.
- Shared-state persistence no longer restores the previous expanded geometry after the animation. The persisted collapse remains `36/0/870`, and the persisted expansion remains `600/6/300`.
- Main Agent `main_text` model activity is normalized to a thinking lane, including persisted legacy model spans. Only structured external calls render a call group with target Agent, provider, model, and its routing/thinking steps.
- The live executor run contains no call group. Its expanded tool card displays `Ran command`, `CMD`, `$ cmd`, real executor output, a copy command action, and `Success`.
- The reference is dark while the active product theme is Atelier light. Component hierarchy, spacing, command/output treatment, copy action, expansion affordance, and terminal result alignment match the reference without overriding the user's theme.

### Patches Made

- Replaced target-layout measurement with interruptible elapsed-time row interpolation for the workbench, splitter, and 3D rows.
- Deferred shared-state persistence until the final animation frame and reasserted the final layout after the save response re-render.
- Added structured self-versus-external call metadata, including Agent/provider/model targets for collaboration dispatch.
- Added dedicated external-call and shell invocation view models instead of inferring cards from display phrases.
- Preserved command and output separately across span lifecycle merges, added the Codex-style CMD card, and backfilled clean `CMD / $ cmd` display for persisted summaries.
- Fixed the command Run binding to OneWay, removing the layout-blocking WPF parse exception.

### Verification

- Source and implementation were opened together in the focused comparison above; no overflow, overlap, nested-card issue, clipped text, or broken control was found.
- Both collapse and expansion completed with linear intermediate samples and stable persisted final geometry.
- A real CMD process launched from the desktop request, and the work card displayed the actual command and executor result.
- No new desktop dispatcher exception was generated after the binding fix.
- Native WPF tests: 196 passed, 0 failed, 0 skipped.
- Focused backend runtime/collaboration tests: 73 passed.
- Ruff, Python compile, touched-file `git diff --check`, and dark/light design-token validation: passed.
- Final runtime: frontend 8787, event bridge 8765, command gateway 8788, and responsive WPF desktop are running.

final result: passed

---

## Runtime Timeline And 3D Collapse Regression Pass - 2026-07-15

### Evidence

- Source visual truth:
  - `C:\Users\ADMINI~1\AppData\Local\Temp\codex-clipboard-f43a5abd-0a3b-47bb-bc68-24b4bb716af3.png`
  - `C:\Users\ADMINI~1\AppData\Local\Temp\codex-clipboard-8709201e-db48-4e8e-884e-9cb8ec64848d.png`
- Final native implementation: `D:\SpiritKinAI\tmp\ui-qa\four-regressions\final-runtime-command-timeline.png`
- Focused reported-versus-final comparison: `D:\SpiritKinAI\tmp\ui-qa\four-regressions\reported-vs-final-timeline.png`
- 3D expanded, transition, and collapsed comparison: `D:\SpiritKinAI\tmp\ui-qa\four-regressions\avatar-linear-transition-contact-sheet.png`
- Viewport: 1480 x 940, native WPF desktop, Atelier light theme.
- State: real `打开 cmd` executor run with the persisted work chain reloaded after a full desktop, gateway, event-bridge, and frontend restart. The reported screenshot is a general greeting run, so copy and route content intentionally differ; state ordering, model-call truth, tool grouping, and visual treatment are the comparison targets.

### Findings

No actionable P0, P1, or P2 findings remain.

- Completed lifecycle steps no longer appear after an orange stale running step. A later resolved span closes earlier unresolved active spans, and a completed run promotes every remaining active/pending lifecycle to its terminal state.
- Only structured `:model:` spans render as `Calling language model`. Legacy `:agent:general` preparation is not presented as proof of an LLM request.
- The real no-model executor smoke contains no model-call lane. Its command row is grouped as a Codex-style command invocation and reads `cmd · 完成`.
- Collapsing desktop status keeps the WebView2 stage alive and linearly reallocates the 3D row over 220ms. The three-frame contact sheet shows continuous height growth without a black frame, destroyed model, overlap, or clipped controls.

### Fidelity Surfaces

- Fonts and typography: existing Inter, Noto Sans SC, Orbitron, and JetBrains Mono roles are unchanged; the compact command preview remains readable and does not overflow.
- Spacing and layout rhythm: timeline nodes remain aligned on one rail; the command group uses the existing full reading lane; the 3D stage retains its fixed top anchor during row expansion.
- Colors and visual tokens: completed nodes and connectors use the existing success token; no stale copper current node remains between completed nodes. Dark/light token validation still passes.
- Image quality and asset fidelity: the real WebView2 room and 3D companion are preserved through the transition; no placeholder or replacement asset was introduced.
- Copy and content: model-call copy is gated by structured model evidence, and the executable preview comes from `ExecutionRequest.params` / execution result data rather than the generic operation name.

### Patches Made

- Normalized the execution frontier against the latest progressed span, including stale active spans followed by completed events.
- Added explicit model-span visibility while removing phrase-based and `:agent:general` model-call inference.
- Read executable previews from the real `ExecutionRequest.params` contract and nested result data.
- Preserved concrete command projections when a later compatibility result updates the same execution span.
- Added linear `GridLength` animation for 3D row redistribution with the system reduced-motion fallback.

### Verification

- Native WPF tests: 193 passed, 0 failed, 0 skipped.
- Runtime tests: 50 passed, 0 failed.
- Ruff, Python compile, and touched-file `git diff --check`: passed.
- Design token validation: passed for dark and light themes.
- Live executor smoke: `打开 cmd` completed without an LLM model span and rendered `cmd · 完成` in the command group.
- Final services: frontend 8787, event bridge 8765 (`bridge=True`), and command gateway 8788 listening; WPF responsive.

final result: passed

---

## Desktop Recovery And Maximized Layout Pass - 2026-07-15

### Evidence

- Source visual truth: `D:\SpiritKinAI\state\run\atelier_concept_day_1480x940.png`
- Native desktop at 1480 x 940: `D:\SpiritKinAI\state\run\atelier_desktop_1480_after_bugfix.png`
- Native desktop maximized at 1920 x 1040: `D:\SpiritKinAI\state\run\atelier_desktop_maximized_after_bugfix.png`
- Desktop action trace: `D:\SpiritKinAI\state\logs\desktop_actions.jsonl`

### Fixes And Findings

- Branch switching now runs outside the UI thread, presents an explicit dirty-worktree confirmation, terminates timed-out Git processes, reports failures, and writes structured action traces. A real `main` click with 146 dirty files was cancelled without freezing the process and produced a `switch_branch/cancelled` trace.
- The service-health banner now exposes a real `恢复` action. A controlled invalid-token run produced the 401 banner; recovery reloaded the current launch token, authenticated the state endpoint, refreshed the desktop state and real-time channel, and produced `recover_window_session/started` followed by `completed`.
- The left and right rails are fixed at the concept geometry of 216px and 300px. Maximization expands only the conversation workspace, so the top-left brand geometry no longer shifts.
- The concept's `01` comparison marker remains excluded from the native product brand. The title bar uses the concept's UI-font role and blue app mark; the sidebar retains the Orbitron brand role and copper mark.
- Day mode now uses the concept's exact paper base, five-pixel fiber rhythm, restrained 40px reading-edge shadows, copper send action, and Atelier-only faint brand metadata color without weakening global accessible text tokens.
- Unhandled logging ignores expected shutdown socket cancellations and rotates at 8MB. New branch and recovery actions use a dedicated structured JSONL trace.

### Verification

- WPF build: passed with 0 warnings and 0 errors.
- Desktop tests: 177 passed, 0 failed, 0 skipped.
- Design token validation: passed for dark and light themes.
- `git diff --check`: passed for the touched desktop files.
- Final desktop process is running, responsive, maximized, and launched through the normal desktop launcher.

final result: passed

---

## Responsive Daylight Typography Pass - 2026-07-15

### Evidence

- Source visual truth: `D:\SpiritKinAI\state\run\atelier_concept_day_1480x940.png`
- Implementation at source size: `D:\SpiritKinAI\state\run\atelier_light_responsive_1480_clean.png`
- Implementation maximized: `D:\SpiritKinAI\state\run\atelier_light_responsive_maximized_clean.png`
- Same-size comparison: `D:\SpiritKinAI\state\run\atelier_concept_vs_desktop_light_final.png`
- Viewports: 1480 x 940 and 1920 x 1040, native WPF desktop, Atelier light theme
- State: the source contains a populated editorial conversation while the implementation shows the real quick-chat empty state. The comparison therefore covers shell proportions, responsive reading axis, typography roles, surface color, and ambient lighting rather than message-content parity.

### Patches Made

- Replaced fixed shell side tracks with the source's `216:952:300` proportional relationship, retaining practical minimums and caps for the native window.
- Replaced the fixed 640/665/680/760px center lanes with responsive 10-column reading and composer lanes capped at 960/1080px.
- Removed the high-z-index full-window light curtains that washed out text and controls; retained the source-aligned paper fiber and restrained reading-column illumination.
- Matched the concept's typography hierarchy: 20px 900-weight brand, 18px 800-weight conversation title, 11px muted section labels, and regular 12px workbench values.
- Enabled ideal text formatting and automatic hinting on the conversation surface while preserving the existing bundled Inter, Noto Sans SC, Orbitron, and JetBrains Mono families.

### Findings

No actionable P0, P1, or P2 findings remain in the reviewed empty quick-chat state.

- [P3] The comparison source is populated and the live desktop session is empty because the local gateway reports a stale token. A message-state comparison should be repeated after a live session is available; no mock conversation was injected for the QA capture.

### Verification

- WPF build: passed with 0 warnings and 0 errors.
- Desktop tests: 177 passed, 0 failed, 0 skipped.
- Design token validation: passed for dark and light themes, including light faint text at 4.53:1.
- 1480px capture: the center composer is approximately 680px and the shell preserves the concept's three-column proportions.
- 1920px capture: the center composer expands to approximately 884px while the sidebars grow proportionally within their caps.
- Same-size visual comparison: passed for shell geometry, type hierarchy, paper color, reading-column illumination, clipping, and overlap.

final result: passed

---

## Evidence

- Source visual truth: `D:\SpiritKinAI\tmp\concept-live-1480x940.png`
- Source implementation: `C:\Users\Administrator\Documents\SpiritKinAI\ui-concepts\desktop-priority.html` (default `i=0`)
- Source CSS and motion: `C:\Users\Administrator\Documents\SpiritKinAI\ui-concepts\desktop-priority.css`
- Source interactions: `C:\Users\Administrator\Documents\SpiritKinAI\ui-concepts\desktop-priority.js`
- Implementation screenshot: `D:\SpiritKinAI\tmp\direct-fidelity-proof\wpf-final-dark.png`
- Light-theme screenshot: `D:\SpiritKinAI\tmp\direct-fidelity-proof\wpf-final-light-fixed-chat.png`
- Full-view comparison: `D:\SpiritKinAI\tmp\direct-fidelity-proof\concept-vs-wpf-final.png`
- Focused central comparison: `D:\SpiritKinAI\tmp\direct-fidelity-proof\concept-vs-wpf-central-final.png`
- Focused right-rail comparison: `D:\SpiritKinAI\tmp\direct-fidelity-proof\concept-vs-wpf-right-final.png`
- Motion frames: `D:\SpiritKinAI\tmp\direct-fidelity-proof\frame-0.png` through `frame-4.png`
- Viewport: 1480 x 940, WPF desktop, dark Atelier theme
- State: connected live conversation with real runtime events and embedded 3D avatar. The source has a pending Permission Gate; the selected live session has no pending approval, so the conditional gate is absent in the implementation capture.

## Full-View Comparison

The native shell now follows the live HTML geometry rather than the stale export: 34px title bar; 216px sidebar; 6px splitters; 950px conversation surface; 300px operations rail; and a 300px avatar panel. The content axis uses the browser's effective 665px width plus its 15px stable scrollbar gutter, while the composer remains 680px.

## Focused Region Comparison

- Conversation: header, message start position, editorial drop cap, phase line, Runtime Delta, tool stream, artifact strip, and composer share the source reading axis. No text is clipped.
- Operations rail: the status block, module rows, 138px graph, node coordinates, 14px event gap, and 300px avatar stage align with the source structure.
- Theme: dark and light both preserve readable message text. The previous light-theme hard-coded night text color is removed from the rendered message template.
- Motion: the 12s glass drift/light flow, 6.5s breathing rails/hearth, and 4.6s event/active-node motion are present and respect `SystemParameters.ClientAreaAnimation` or `prefers-reduced-motion` in the embedded stage.

## Fidelity Surfaces

- Fonts and typography: Inter, Noto Sans SC, Orbitron, and JetBrains Mono are bundled and used in the same UI/display/mono roles as the HTML. Message font size, 1.78 line height, drop cap, weights, and truncation are preserved.
- Spacing and layout rhythm: all fixed frame tracks and primary reading/composer widths match the live computed source values. The right rail no longer auto-scrolls away from its initial top state.
- Colors and visual tokens: Atelier charcoal, paper, copper, semantic state colors, paper fiber, and day/night mappings use the existing v4 token system. Token contrast validation passes in both themes.
- Image quality and asset fidelity: the real WebView2 avatar room and 3D model remain intact. Its embedded hearth overlay runs at 6.5s with reduced-motion fallback.
- Copy and content: shell labels and theme names match the concept. Conversation, artifact, and event values remain real application data.

## Findings

No actionable P0, P1, or P2 findings remain.

- [P3] The pending Permission Gate cannot appear in the selected live session without a real governed action awaiting approval. The conditional control, handlers, sizing, and breathing rail remain implemented.

## Patches Made Since Previous QA

- Re-measured the current HTML in Chrome instead of relying on the older exported PNG.
- Corrected right rail, avatar, chat row, artifact, composer, prompt, confirmation gate, message, and graph dimensions from computed source values.
- Rebuilt the 680px reading lane as a 665px native content lane plus 15px scrollbar gutter, eliminating clipped message headers.
- Matched the flat Atelier chat field and removed the extra hard reading-edge stroke.
- Added source-timed breathing rails and corrected active-node opacity.
- Prevented passive runtime events from scrolling the operations rail on startup.
- Matched the `夜间` / `日间` theme copy.
- Replaced hard-coded night message foregrounds with dynamic theme resources, fixing light-theme readability.

## Verification

- WPF build: passed with 0 warnings and 0 errors.
- Desktop tests: 146 passed, 0 failed, 0 skipped.
- Design token validation: passed for dark and light themes.
- Native motion proof: five frames captured 1.15s apart. The non-avatar shell changed by 586,014 pixels above a 2-level delta; the runtime card and event rail also changed independently.
- Dark and light 1480 x 940 captures: passed without overlap, clipping, or invisible message text.
- WPF and local services remain running in dark mode.

final result: passed

---

# Atelier Daylight Palette QA - 2026-07-14

## Evidence

- Source visual truth: `D:\SpiritKinAI\state\run\atelier_concept_day_1480x940.png`
- Implementation screenshot: `D:\SpiritKinAI\state\run\atelier_wpf_light_final.png`
- Full-view comparison: `D:\SpiritKinAI\state\run\atelier_day_concept_vs_wpf_final.png`
- Viewport: 1480 x 940, native WPF desktop, Atelier light theme
- State: the source contains a populated editorial conversation while the implementation is the real quick-chat empty state. Palette, ambient light, shell surfaces, and shared typography roles are compared; content geometry is intentionally out of scope.

## Fidelity Surfaces

- Fonts and typography: primary text remains `#273A4C` and muted text is consistently `#4D667B`. The legacy compatibility muted role no longer introduces a purple-gray cast.
- Spacing and layout rhythm: no layout values changed. The title bar, sidebar, chat canvas, workbench, composer, and avatar retain their native geometry.
- Colors and visual tokens: title bar and side rails now use the concept's cool paper surface. Daylight streaks use the source cyan and amber hues with native-compositing alpha compensation, preventing the previous dark-blue and muddy-brown overlay.
- Image quality and asset fidelity: the real WebView2 avatar stage remains unchanged and sharp.
- Copy and content: all product copy and runtime data remain live; no reference placeholder content was introduced.

## Focused Comparison

The full-view comparison is sufficient for this palette-only pass because the affected resources span the entire shell. The readable title, navigation, empty-state heading, composer labels, workbench labels, and status metadata provide focused text samples across all three foreground roles.

## Findings

No actionable P0, P1, or P2 findings remain.

- [P3] The ambient curtains animate, so a single implementation frame will not place each streak at the same x-coordinate as the source frame.

## Patches Made

- Restored the concept's cool-cyan and daylight-amber ambient hues in the light theme.
- Reduced WPF gradient alpha to compensate for native source-over compositing.
- Restored the 3% cool paper fiber treatment.
- Replaced pure-white compatibility panels with the concept's cool paper surface.
- Unified the compatibility muted foreground with the semantic muted token.

## Verification

- WPF build: passed with 0 warnings and 0 errors.
- Desktop tests: 177 passed, 0 failed, 0 skipped.
- Design token validation: passed for both dark and light themes.
- Final source and implementation screenshots were opened together at 1480 x 940.

final result: passed

---

# Work Chain Runtime Integrity QA - 2026-07-14

## Evidence

- Source visual truth: `C:\Users\ADMINI~1\AppData\Local\Temp\codex-clipboard-b2bbcbdd-c686-44ab-8512-5f6a62c24ba8.png`
- Implementation screenshot: `D:\SpiritKinAI\output\qa\native-workchain-command-session.png`
- Full native shell screenshot: `D:\SpiritKinAI\output\qa\native-workchain-current.png`
- Focused combined comparison: `D:\SpiritKinAI\output\qa\workchain-source-vs-current.png`
- Viewport: 1480 x 940, native WPF, Atelier dark theme
- State: persisted collaboration work chain after desktop and gateway restart

## Findings

No actionable P0, P1, or P2 findings remain.

- The reported duplicate model invocation lane is reduced to one invocation lane in the focused implementation capture.
- Call, thinking, tool command/result, and reply events remain attached to the same agent/message run identity.
- Only the current frontier is orange while earlier active events project as completed green; pending events remain neutral.
- Command groups retain expansion by stable span/command identity instead of list position.
- The upper-left brand and navigation region is fully visible with no clipping.

## Fidelity Surfaces

- Fonts and typography: existing Inter/Noto Sans SC/JetBrains Mono roles remain unchanged and readable.
- Spacing and layout rhythm: timeline nodes, one-pixel connector, content gutter, and actor labels remain centered and do not cross text.
- Colors and visual tokens: completed, current, pending, failed, and blocked states continue to use the Atelier semantic brushes.
- Image quality and asset fidelity: the real WebView2 avatar stage remains intact; no placeholder assets were introduced.
- Copy and content: historical audit text remains truthful; new runs no longer create duplicate model generations after reply transport failures.

## Verification

- Python collaboration/worker regression set: 144 passed.
- Additional conversation and gateway regression set: 38 passed.
- Native desktop tests: 169 passed.
- Native WPF build: 0 warnings, 0 errors.
- Frontend 8787, event bridge 8765, command gateway 8788: listening after restart.

final result: passed

---

# Composer Concept Port QA - 2026-07-13

- Source visual truth: `D:\SpiritKinAI\design\reference\atelier-editorial\atelier-editorial-night.png`
- Implementation screenshot: `D:\SpiritKinAI\tmp\composer-css-port\wpf-composer-handoff.png`
- Focused comparison: `D:\SpiritKinAI\tmp\composer-css-port\concept-vs-wpf-composer-handoff.png`
- Viewport: 1480 x 940
- State: dark Atelier theme, populated conversation, composer idle

## Evidence

- Full view: the native WPF shell was captured at the same 1480 x 940 viewport as the concept source; the composer remains inside the centered 680px reading lane with no clipping or overlap.
- Focused region: source and implementation crops were placed in one 1920 x 221 comparison. Prompt, toolbar, and send button share the same x/y positions and dimensions.
- Fonts and typography: 12px/11px artifact hierarchy, 13px prompt text, compact toolbar labels, and bold 12px send label match the source hierarchy. Native WPF rasterization remains an acceptable platform difference.
- Spacing and layout rhythm: implemented the source's 12px top padding, 87px prompt, 6px gap, 36px toolbar, and 14px bottom padding inside the 155px composer row.
- Colors and visual tokens: prompt paper fill, 40%-opacity line, copper button, dark night button ink, and light-theme button ink are mapped to theme resources.
- Image quality and asset fidelity: no image assets occur in this focused region; the existing real avatar WebView remains unchanged.
- Copy and content: product copy remains live. `Ready`, permission mode, artifact summary, and pending count intentionally reflect runtime state instead of the concept's static sample values.

## Findings

No actionable P0, P1, or P2 findings remain.

- [P3] Ambient reading-column light bands animate, so their captured position can differ from the static concept frame.

## Patches Made

- Ported the concept composer geometry directly into native WPF row and padding values.
- Replaced the inherited generic input/button treatment with composer-specific paper, line, copper, hover, pressed, disabled, and theme-aware ink states.
- Preserved all existing send, attachment, Agent mention, permission, collaboration, search, and quick-command bindings.

## Verification

- WPF build: passed with 0 warnings and 0 errors.
- Desktop tests: 146 passed, 0 failed, 0 skipped.
- Local services: frontend 8787, event bridge 8765, and command gateway 8788 are listening; WPF is running with the persisted session token.

final result: passed
