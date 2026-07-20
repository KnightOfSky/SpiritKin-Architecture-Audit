# SpiritKinAI Desktop Shell

This directory owns the Windows desktop shell. Its current production project is `SpiritKinDesktop/SpiritKinDesktop.csproj`.

## Scope

The desktop shell is responsible for:

- WPF operator console layout and navigation.
- WebView-hosted desktop console and avatar surfaces.
- Local service controls for frontend, realtime bridge, command gateway, and optional voice session.
- Native access to diagnostics, logs, learning, model settings, Agent management, Skills, knowledge bases, Search/RAG, evolution, and governance pages.
- Passing user actions to backend `/desktop/*` APIs instead of duplicating business rules in WPF.

The desktop shell should not own backend business logic. Runtime state, model provider settings, Skill registry updates, KB indexing, Search/RAG config, and governance decisions should continue to live behind backend management APIs.

## Subdomains

| Subdomain | Primary file or area | Owner role |
| --- | --- | --- |
| Main WPF shell | `SpiritKinDesktop/MainWindow.xaml` and `MainWindow.xaml.cs` | Desktop Shell Owner |
| WebView integration | Web preview/avatar hosting code in `MainWindow.xaml.cs` | Frontend + Desktop Owners |
| Service operations | service start/stop/restart handlers | Operations Owner |
| Management pages | models, learning, agents, skills, KB, Search/RAG, evolution, governance panels | Module owner plus Desktop Shell Owner |
| Release/build metadata | `SpiritKinDesktop/*.csproj`, app settings, assets | Desktop Shell Owner |

When the WPF surface grows further, split by subdomain first before adding new unrelated code to `MainWindow.xaml.cs`.

## API Boundary

Desktop controls should call these backend surfaces:

- `/desktop/services`
- `/desktop/diagnostics`
- `/desktop/logs`
- `/desktop/learning`
- `/desktop/model-catalog`
- `/desktop/agent-management`
- `/desktop/skills`
- `/desktop/knowledge-base`
- `/desktop/search-management`
- `/desktop/evolution`
- `/desktop/ecosystem-review`
- `/desktop/module-management`

The command gateway remains the source of truth for response schemas and safety decisions.

## Verification

Run after desktop shell changes:

```powershell
dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj --no-restore -p:UseAppHost=false
python scripts/validate_desktop_delivery.py
```

Run backend API tests when a desktop change depends on management endpoint behavior:

```powershell
python -m unittest backend.tests.unit.test_command_gateway -v
```
