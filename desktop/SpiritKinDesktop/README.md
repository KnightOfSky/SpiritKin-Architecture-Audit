# SpiritKin Desktop

Windows desktop shell for SpiritKin.

This is a native WPF application. The chat workspace, sessions, projects, tasks, sync status, confirmation gate, and execution trace use WPF controls. WebView2 is used only for the Three.js 3D avatar panel.

Current native modules:

- Chat/session/project/task workbench with shared state through the command gateway.
- 3D avatar panel embedded through WebView2.
- Sync tab for shared revision, event sources, pending confirmation cleanup, event cleanup, and session compaction.
- Services tab for managed local services: frontend `8787`, event bridge `8765`, command gateway `8788`, and optional remote worker `8790`.
- Logs tab for project log discovery, error/warning counts, and tail viewing from `state/logs` and `tmp`.
- Daily tab for today's tasks, learning records, service health, and error-log summary.
- Diagnostics tab for local service ports, dependencies, project files, and repair steps.
- Skills tab for Skill registry management: create, edit, save, delete, review, promote, and export Skill specs.
- Learning tab for human or cloud-model correction records, review prompts, cloud model configuration, and self-training dataset export.
- Context tab for active-session context policy, pinned context, project docs/events/learning toggles, and project optimization suggestions.
- Project Overview tab for the shared management document used by human operators, the main Agent, and external model reviewers. Edits become review proposals with a diff and must be approved before replacing the document.
- Agent Cluster tab for per-Agent enable/model/capability management, Skill assistance switches, external CLI assistants such as Codex/Claude Code, route profiles such as primary+reviewer or primary+vision, and remote Skill package export.

## XAML Structure

`MainWindow.xaml` is now only the window chrome, root rows, split columns, and
top-level control hosts. Shared styles, non-interactive templates, and workflow
canvas item container styles live in `Resources/MainWindowResources.xaml` and
`Resources/MainWindowDataTemplates.xaml`.

Interactive item templates that need existing `MainWindow` event handlers live
in `Resources/MainWindowInteractionTemplates.xaml` with a small code-behind
forwarder. The title bar is composed by `Controls/WindowTitleBar.xaml`, with
smaller brand/menu/caption pieces in `Controls/WindowBrandMark.xaml`,
`Controls/WindowAppMenu.xaml`, and `Controls/WindowCaptionButtons.xaml`.
The embedded terminal and global search overlay are isolated in
`Controls/IntegratedTerminalPanelView.xaml` and
`Controls/GlobalSearchOverlayView.xaml`.
The main application areas live in `Controls/WorkspaceSidebarView.xaml`,
`Controls/ChatWorkspaceView.xaml`, `Controls/WorkbenchShellView.xaml`, and
`Controls/ManagementPanelsView.xaml`. `WorkspaceSidebarView` exposes its own
controls directly; the remaining bridge files under `Features/Shell` preserve
existing `MainWindow` code-behind member names for chat, workbench, and
management panels while those modules are migrated.

Validate this split with:

```powershell
dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj -p:UseAppHost=false -p:OutDir=tmp\wpf-build\
```

Run from the repository root:

```powershell
dotnet run --project desktop\SpiritKinDesktop\SpiritKinDesktop.csproj
```

Or use the service-aware launcher:

```powershell
python scripts\start_desktop_console.py
```

Delivery validation from the repository root:

```powershell
python scripts\validate_desktop_delivery.py --quick
python scripts\validate_desktop_delivery.py
```

The quick gate checks the native WPF build, no-window WPF startup smoke, desktop launcher wiring, runtime voice path, TTS provider helpers, backend `web.search` routing, Live2D manifest resources, and inline frontend script syntax. The full gate runs the broader desktop, voice, local PC, command gateway, runtime, remote tooling, ASR smoke helper, AgentCluster regression set, and a real non-GUI desktop launcher start/status/stop smoke on temporary ports.

External model review is optional. The Learning tab can manage multiple assist models, such as DeepSeek, GPT, Claude Opus, Gemini, or a local 35B model. Each assist model has an ID, provider type, Base URL, model name, API Key, role, priority, and enabled switch. The desktop can request one selected model or ask all enabled assist models to review the same problem.

```powershell
$env:YUNDUN_API_KEY="..."
$env:YUNDUN_BASE_URL="https://<your-cloud-provider>/v1"
$env:YUNDUN_MODEL="<model-name>"

$env:OPENAI_API_KEY="..."
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
$env:SPIRITKIN_REVIEW_MODEL="gpt-4.1"

$env:ANTHROPIC_API_KEY="..."
$env:SPIRITKIN_ANTHROPIC_MODEL="claude-3-7-sonnet-latest"

$env:GEMINI_API_KEY="..."
$env:SPIRITKIN_GEMINI_MODEL="gemini-2.5-pro"

$env:SPIRITKIN_ENABLE_OLLAMA="1"
$env:OLLAMA_HOST="http://127.0.0.1:11434"
$env:SPIRITKIN_OLLAMA_MODEL="qwen2.5-coder:7b"

# Evolution Paper/Video-to-Skills uses the managed llama.cpp service by default.
$env:LLAMACPP_BASE_URL="http://127.0.0.1:8080/v1"
$env:SPIRIT_TEXT_PROVIDER="llamacpp"
$env:SPIRIT_TEXT_MODEL="qwen/qwen3.6-35b-a3b"
$env:SPIRITKIN_TEXT_BASE_URL="http://127.0.0.1:8080/v1"
$env:SPIRIT_VISION_PROVIDER="llamacpp"
$env:SPIRIT_VISION_MODEL="qwen/qwen3.6-35b-a3b"
$env:SPIRIT_VISION_BASE_URL="http://127.0.0.1:8080/v1"
$env:SPIRIT_VISION_API_KEY=""
```

Ollama is only a local optional provider. It is not required for cloud model review and is hidden unless explicitly enabled.

The Evolution tab uses the text model to extract Paper-to-Skill summaries/actions and the vision model to extract Video-to-Skill UI operation sequences from screenshots or video frames. If llama.cpp is not reachable, ingestion falls back to the manually supplied summary/actions and records the model extraction failure in the artifact metadata.

The desktop service watchdog also monitors managed llama.cpp by checking both
the model catalog and a one-token, thinking-disabled chat completion. Three
consecutive generation failures trigger a bounded restart; at most three
restarts are attempted in a ten-minute window. Set
`SPIRITKIN_MODEL_SELF_HEAL=0` to disable this behavior.

Learning records are stored in `state/learning/learning_records.jsonl`. The exported training dataset defaults to `state/learning/self_training_dataset.jsonl` and can be overridden with `SPIRITKIN_LEARNING_DATASET`.

External coding assistants are configured as CLI adapters in the Agent Cluster tab. For example, Codex or Claude Code can be registered as review-only helpers with a command and working directory. They can be launched in a separate terminal or run from the embedded output pane. They are not allowed to write unless the `allow_write` switch is enabled.

Remote export creates JSON packages under `state/remote_exports/`. A package contains the target node, module type, Skill names, verification commands, and rollback note. The desktop can push the package to a configured remote worker, import/register it on the worker, or request execution/verification. Remote verification commands are disabled by default on the worker and require `SPIRITKIN_REMOTE_ALLOW_PACKAGE_COMMANDS=1`.

## Current implementation audit

Implemented as working desktop functions:

- Session/project/task CRUD persists through `/desktop/state`; session archive/delete and task/project status actions are wired.
- Service refresh/start/stop/restart is wired through `/desktop/services`.
- Sync maintenance is wired for refresh, clear events, clear pending confirmation, and session compaction.
- Logs/daily/diagnostics load real backend snapshots and provide local management actions: open/archive/delete logs, locate daily items, update daily task status, copy repair commands, and run repair commands in a confirmed PowerShell window.
- Skills loads and edits the Skill registry through `/desktop/skills`.
- Learning saves correction records, exports the dataset, builds review prompts, manages multiple external/local assist models, and can request one selected model or all enabled models for review when configured.
- Context policy save/load is wired.
- Project overview proposal, approval, rejection, and file open are wired.
- Agent cluster CRUD for agents, external assistants, route profiles, remote targets, plus remote package export/push/execute, is wired. CLI assistants can be launched from the desktop or run in the embedded output pane.
- Runtime route profiles are applied to the primary text LLM route, and enabled/disabled desktop Agent settings are applied when the runtime creates the Agent cluster.

Known gaps that are still not full operational management:

- Session deletion intentionally blocks deleting the final remaining session; the UI now reports this instead of silently doing nothing.
- Diagnostics repair commands are launched only after explicit confirmation and in a visible PowerShell window; the desktop intentionally does not silently apply repairs.
- Logs support open, folder locate, archive, and delete. Rotation policy is still manual via archive/delete rather than scheduled retention.
- Daily supports locating task/log/service/learning items and marking task status. Scheduling and assignment are not yet a calendar/workforce feature.
- Sync shows local state and event clients but does not yet manage remote devices, conflict resolution, or account-level multi-device sync.
- External assistants stream stdout/stderr in the embedded pane when the configured CLI reads stdin or accepts piped prompts; fully interactive terminal emulation is still handled by launching a separate CLI window.
- Agent route profiles currently enforce the primary text LLM route and desktop Agent enable/priority settings. Committee review and multi-model fan-out are still future route strategies.
- Remote package push/import/execute is wired. Worker-side verification command execution stays opt-in through `SPIRITKIN_REMOTE_ALLOW_PACKAGE_COMMANDS=1`.
- Learning records and model review are wired, but automatic skill correction/promotion still requires the separate Skill verification/promotion pipeline to be connected end to end.

Command gateway desktop endpoints:

- `GET /desktop/operations`: combined services, logs, sync, and daily snapshot.
- `GET/POST /desktop/services`: service status plus start/stop/restart actions. Command gateway restart is scheduled through a background helper after the HTTP response returns.
- `GET /desktop/logs`: project log list and selected log tail.
- `GET/POST /desktop/sync`: revision/event/pending state and maintenance actions.
- `GET /desktop/daily`: daily task, learning, service, and error-log summary.
