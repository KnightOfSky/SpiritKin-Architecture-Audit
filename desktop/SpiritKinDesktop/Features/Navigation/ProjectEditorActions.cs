using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    internal bool TryPickNewProjectWorkspace(out string workspace, out string title)
    {
        workspace = "";
        title = "";
        var driveRoots = ReadyDriveRoots();
        var initialDirectory = ProjectCreationInitialDirectory(_rootDir, driveRoots);
        var dialog = new OpenFolderDialog
        {
            Title = "在磁盘根目录选择或新建项目文件夹",
            InitialDirectory = initialDirectory,
            DefaultDirectory = initialDirectory,
            Multiselect = false,
        };
        foreach (var driveRoot in driveRoots)
        {
            dialog.CustomPlaces.Add(new FileDialogCustomPlace(driveRoot));
        }
        if (dialog.ShowDialog(_owner()) != true || string.IsNullOrWhiteSpace(dialog.FolderName))
        {
            return false;
        }
        workspace = Workspace.NormalizeWorkspacePath(dialog.FolderName);
        title = new DirectoryInfo(workspace).Name;
        if (string.IsNullOrWhiteSpace(title))
        {
            title = $"项目 {DateTime.Now:HH:mm:ss}";
        }
        if (TryFindExistingProjectByWorkspace(_state.Projects, workspace, Workspace.NormalizeWorkspacePath) is { } existing)
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"该目录已属于项目：{existing.Title}";
            WorkspaceSidebar.ConnectionStatusText.Text = $"已取消新建项目：目录已属于“{existing.Title}”。";
            workspace = "";
            title = "";
            return false;
        }
        return true;
    }

    internal static string ProjectCreationInitialDirectory(string applicationRoot, IEnumerable<string>? driveRoots = null)
    {
        var applicationDrive = Path.GetPathRoot(Environment.ExpandEnvironmentVariables(applicationRoot ?? ""));
        if (!string.IsNullOrWhiteSpace(applicationDrive) && Directory.Exists(applicationDrive))
        {
            return applicationDrive;
        }
        return (driveRoots ?? Array.Empty<string>())
            .FirstOrDefault(path => !string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
            ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
    }

    private static string[] ReadyDriveRoots()
    {
        try
        {
            return DriveInfo.GetDrives()
                .Where(drive => drive.IsReady)
                .Select(drive => drive.RootDirectory.FullName)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();
        }
        catch
        {
            return Array.Empty<string>();
        }
    }

    internal static DesktopItem? TryFindExistingProjectByWorkspace(
        IEnumerable<DesktopItem> projects,
        string workspace,
        Func<string, string> normalizeWorkspacePath)
    {
        if (string.IsNullOrWhiteSpace(workspace))
        {
            return null;
        }
        var normalizedWorkspace = NormalizeWorkspaceForComparison(workspace, normalizeWorkspacePath);
        if (string.IsNullOrWhiteSpace(normalizedWorkspace))
        {
            return null;
        }
        return projects.FirstOrDefault(project =>
            ProjectWorkspaceCandidates(project).Any(candidate => string.Equals(
                NormalizeWorkspaceForComparison(candidate, normalizeWorkspacePath),
                normalizedWorkspace,
                StringComparison.OrdinalIgnoreCase)));
    }

    private static IEnumerable<string> ProjectWorkspaceCandidates(DesktopItem project)
    {
        if (!string.IsNullOrWhiteSpace(project.WorkspacePath))
        {
            yield return project.WorkspacePath!;
        }
        foreach (var candidate in WorkspaceController.ExtractWorkspaceCandidates(project.Detail))
        {
            yield return candidate;
        }
    }

    private static string NormalizeWorkspaceForComparison(string path, Func<string, string> normalizeWorkspacePath)
    {
        try
        {
            return normalizeWorkspacePath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }
        catch
        {
            return "";
        }
    }

    internal sealed record ProjectDeletionResult(string ProjectTitle, int SessionCount);

    internal static ProjectDeletionResult? DeleteProjectAndSessionsFromState(
        DesktopState state,
        string projectId,
        ISet<string> pendingDeletedSessionIds,
        ISet<string> pendingDeletedProjectIds)
    {
        var project = state.Projects.FirstOrDefault(item => string.Equals(item.Id, projectId, StringComparison.OrdinalIgnoreCase));
        if (project is null)
        {
            return null;
        }
        var projectSessions = state.Sessions
            .Where(session => string.Equals(session.ProjectId, project.Id, StringComparison.OrdinalIgnoreCase))
            .ToList();
        foreach (var session in projectSessions)
        {
            pendingDeletedSessionIds.Add(session.Id);
        }
        state.Sessions.RemoveAll(session => projectSessions.Any(removed => string.Equals(removed.Id, session.Id, StringComparison.OrdinalIgnoreCase)));
        state.Projects.RemoveAll(item => string.Equals(item.Id, project.Id, StringComparison.OrdinalIgnoreCase));
        pendingDeletedProjectIds.Add(project.Id);
        if (state.Sessions.Count == 0)
        {
            state.Sessions.Add(DesktopState.DefaultSession());
        }
        if (!state.Sessions.Any(session => string.Equals(session.Id, state.ActiveSessionId, StringComparison.OrdinalIgnoreCase)))
        {
            state.ActiveSessionId = state.Sessions
                .OrderBy(session => WorkspaceController.IsArchived(session.Status))
                .ThenByDescending(session => session.UpdatedAt)
                .First()
                .Id;
        }
        return new ProjectDeletionResult(project.Title, projectSessions.Count);
    }

    internal async Task SaveSelectedProjectAsync()
    {
        var project = Workspace.SelectedProject();
        if (project is null)
        {
            return;
        }
        project.Title = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ProjectTitleEditBox.Text) ? project.Title : WorkbenchShell.ManagementPanels.ProjectTitleEditBox.Text.Trim();
        project.Status = ComboText(WorkbenchShell.ManagementPanels.ProjectStatusBox);
        project.Detail = WorkbenchShell.ManagementPanels.ProjectDetailEditBox.Text.Trim();
        project.WorkspacePath = Workspace.ResolveProjectWorkspace(project) ?? project.WorkspacePath ?? _rootDir;
        project.EnvFilePath = WorkbenchShell.ManagementPanels.ProjectEnvFileBox.Text.Trim();
        project.DependencyFilePath = WorkbenchShell.ManagementPanels.ProjectDependencyFileBox.Text.Trim();
        project.PackageManager = ComboText(WorkbenchShell.ManagementPanels.ProjectPackageManagerBox);
        project.StartCommand = WorkbenchShell.ManagementPanels.ProjectStartCommandBox.Text.Trim();
        project.UpdatedAt = NowSeconds();
        RenderState();
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"已保存项目：{project.Title}";
        WorkspaceSidebar.ConnectionStatusText.Text = $"项目已保存：{project.Title}";
        Workbench.ResetTerminalSession("项目运行 Profile 已保存，终端将在下一条命令前重载。");
        await SaveStateAsync();
    }

    internal void BrowseProjectWorkspacePath()
    {
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "项目工作区在新增项目时确定；如需更换，请新建项目。";
    }

    internal void OpenSelectedProjectWorkspace()
    {
        var project = Workspace.SelectedProject();
        var workspace = Workspace.ResolveProjectWorkspace(project) ?? Workspace.ActiveWorkspaceRoot();
        Process.Start(new ProcessStartInfo("explorer.exe", workspace) { UseShellExecute = true });
    }

    internal void OpenActiveWorkspaceFromStatus()
    {
        Process.Start(new ProcessStartInfo("explorer.exe", Workspace.ActiveWorkspaceRoot()) { UseShellExecute = true });
    }

    internal void ManageActiveWorkspaceFromStatus()
    {
        var project = Workspace.CurrentWorkspaceProject();
        if (project is not null)
        {
            Workspace.SetWorkspaceProjectContextId(project.Id);
            WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
            WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
            Workspace.RenderEditors();
        }
        Workspace.OpenManagementPage("tasks", "projects");
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "当前是默认工作区；请选择或新建项目后设置项目工作区。";
        }
    }

    internal void DetectSelectedProjectRuntimeProfile()
    {
        var project = Workspace.SelectedProject();
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "请先选择项目。";
            return;
        }
        var workspace = Workspace.ResolveProjectWorkspace(project) ?? _rootDir;
        WorkbenchShell.ManagementPanels.ProjectPackageManagerBox.Text = WorkspaceController.DetectPackageManager(workspace);
        WorkbenchShell.ManagementPanels.ProjectEnvFileBox.Text = FirstExistingRelative(workspace, ".env.local", ".env", "config/dev.env", "config/.env");
        WorkbenchShell.ManagementPanels.ProjectDependencyFileBox.Text = FirstExistingRelative(workspace, "uv.lock", "poetry.lock", "pyproject.toml", "requirements.txt", "package.json", "pnpm-lock.yaml", "yarn.lock");
        if (string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ProjectStartCommandBox.Text))
        {
            WorkbenchShell.ManagementPanels.ProjectStartCommandBox.Text = SuggestedStartCommand(WorkbenchShell.ManagementPanels.ProjectPackageManagerBox.Text, WorkbenchShell.ManagementPanels.ProjectDependencyFileBox.Text);
        }
        Workspace.RenderProjectRuntimeSummary(new DesktopItem
        {
            Id = project.Id,
            Title = project.Title,
            WorkspacePath = workspace,
            EnvFilePath = WorkbenchShell.ManagementPanels.ProjectEnvFileBox.Text.Trim(),
            DependencyFilePath = WorkbenchShell.ManagementPanels.ProjectDependencyFileBox.Text.Trim(),
            PackageManager = WorkbenchShell.ManagementPanels.ProjectPackageManagerBox.Text.Trim(),
            StartCommand = WorkbenchShell.ManagementPanels.ProjectStartCommandBox.Text.Trim(),
        });
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "已探测项目运行 Profile；保存项目后生效。";
    }

    internal async Task RunSelectedProjectStartCommandAsync()
    {
        var project = Workspace.SelectedProject();
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "请先选择项目。";
            return;
        }
        var command = WorkbenchShell.ManagementPanels.ProjectStartCommandBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(command))
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "请先填写项目启动命令。";
            return;
        }
        await SaveSelectedProjectAsync();
        var runtime = Workspace.ActiveProjectRuntimeProfile();
        var decision = await EvaluateProjectStartCommandAsync(project, runtime, command);
        if (!decision.Allowed)
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"启动命令已被策略阻断：{decision.Message}";
            await RecordProjectStartCommandAsync(project, runtime, command, "blocked", decision.Message);
            return;
        }
        if (decision.ReviewRequired && !ConfirmAction("确认项目启动命令", $"{decision.Message}{Environment.NewLine}{Environment.NewLine}{command}", "运行"))
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "已取消运行项目启动命令。";
            await RecordProjectStartCommandAsync(project, runtime, command, "canceled", decision.Message);
            return;
        }
        await RecordProjectStartCommandAsync(project, runtime, command, "started", decision.Message);
        await Workbench.RunCommandInIntegratedTerminalAsync(command);
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"已在项目终端运行启动命令：{command}";
    }

    internal async Task<ProjectRuntimeDecision> EvaluateProjectStartCommandAsync(DesktopItem project, ProjectRuntimeProfile runtime, string command)
    {
        try
        {
            using var doc = await PostJsonAsync($"{Workspace.ApiBase()}/desktop/project-runtime", new
            {
                action = "evaluate_start_command",
                actor = "wpf_desktop",
                project = BuildProjectRuntimePolicyPayload(project, runtime, command),
            });
            var root = doc.RootElement;
            var policy = root.TryGetProperty("execution_policy", out var policyElement) && policyElement.ValueKind == JsonValueKind.Object
                ? policyElement
                : default;
            if (policy.ValueKind != JsonValueKind.Object)
            {
                var ok = ReadJsonBool(root, "ok", false);
                return new ProjectRuntimeDecision(ok, false, ok ? "项目启动命令通过策略检查。" : "项目启动策略响应不可用。");
            }
            var allowed = ReadJsonBool(policy, "allowed", ReadJsonBool(root, "ok", false));
            var reviewRequired = ReadJsonBool(policy, "review_required", false);
            return new ProjectRuntimeDecision(allowed, reviewRequired, ProjectRuntimePolicyMessage(policy, allowed, reviewRequired));
        }
        catch (Exception ex)
        {
            return new ProjectRuntimeDecision(
                false,
                false,
                $"项目运行策略不可用：{ex.Message}");
        }
    }

    internal async Task RecordProjectStartCommandAsync(DesktopItem project, ProjectRuntimeProfile runtime, string command, string status, string message)
    {
        try
        {
            using var _ = await PostJsonAsync($"{Workspace.ApiBase()}/desktop/project-runtime", new
            {
                action = "record_start_command",
                actor = "wpf_desktop",
                status,
                message,
                project = BuildProjectRuntimePolicyPayload(project, runtime, command),
            });
        }
        catch
        {
            // Runtime audit is best-effort for the local terminal path; policy evaluation already guarded launch.
        }
    }

    internal static object BuildProjectRuntimePolicyPayload(DesktopItem project, ProjectRuntimeProfile runtime, string command) => new
    {
        id = project.Id,
        title = project.Title,
        workspace_path = runtime.WorkspacePath,
        env_file_path = runtime.EnvFilePath,
        dependency_file_path = runtime.DependencyFilePath,
        package_manager = runtime.PackageManager,
        start_command = command,
    };

    internal static string ProjectRuntimePolicyMessage(JsonElement policy, bool allowed, bool reviewRequired)
    {
        var blockers = ProjectRuntimeIssues(policy, "blockers");
        if (!allowed && blockers.Length > 0)
        {
            return string.Join(Environment.NewLine, blockers);
        }
        var warnings = ProjectRuntimeIssues(policy, "warnings");
        if (reviewRequired && warnings.Length > 0)
        {
            return "该启动命令需要确认：" + Environment.NewLine + string.Join(Environment.NewLine, warnings);
        }
        return allowed ? "项目启动命令通过策略检查。" : "项目启动命令未通过策略检查。";
    }

    internal static string[] ProjectRuntimeIssues(JsonElement policy, string key)
    {
        if (policy.ValueKind != JsonValueKind.Object || !policy.TryGetProperty(key, out var items) || items.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return items.EnumerateArray()
            .Select(item => ReadJsonString(item, "detail", ReadJsonString(item, "issue_id")))
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Take(5)
            .ToArray();
    }

    internal sealed record ProjectRuntimeDecision(bool Allowed, bool ReviewRequired, string Message);

    internal static string FirstExistingRelative(string workspace, params string[] candidates)
    {
        foreach (var candidate in candidates)
        {
            if (File.Exists(Path.Combine(workspace, candidate)))
            {
                return candidate;
            }
        }
        return "";
    }

    internal static string SuggestedStartCommand(string packageManager, string dependencyFile)
    {
        var manager = (packageManager ?? "").Trim().ToLowerInvariant();
        var file = (dependencyFile ?? "").Trim().ToLowerInvariant();
        if (manager is "npm")
        {
            return "npm run dev";
        }
        if (manager is "pnpm")
        {
            return "pnpm dev";
        }
        if (manager is "yarn")
        {
            return "yarn dev";
        }
        if (manager is "dotnet")
        {
            return "dotnet run";
        }
        if (manager is "uv")
        {
            return "uv run python -m main";
        }
        if (manager is "poetry")
        {
            return "poetry run python -m main";
        }
        return file.EndsWith("requirements.txt", StringComparison.OrdinalIgnoreCase) || file.EndsWith("pyproject.toml", StringComparison.OrdinalIgnoreCase)
            ? "python -m main"
            : "";
    }

    internal async Task DeleteSelectedProjectAsync()
    {
        var project = Workspace.SelectedProject();
        if (project is null)
        {
            return;
        }
        await DeleteProjectByIdAsync(project.Id);
    }

    internal async Task DeleteProjectByIdAsync(string projectId)
    {
        var project = _state.Projects.FirstOrDefault(item => item.Id == projectId);
        if (project is null)
        {
            return;
        }
        var removedTitle = project.Title;
        var projectSessions = _state.Sessions
            .Where(session => string.Equals(session.ProjectId, project.Id, StringComparison.OrdinalIgnoreCase))
            .ToList();
        var sessionCount = projectSessions.Count;
        var collaborationThreadIds = CollaborationThreadIdsForProjectDeletion(project.Id, projectSessions);
        if (!ConfirmDestructiveAction("删除项目", $"确定要删除项目“{removedTitle}”吗？项目内 {sessionCount} 个会话也会一并删除，不能在 Chats 或归档视图恢复。"))
        {
            return;
        }
        DeleteProjectAndSessionsFromState(_state, project.Id, _pendingDeletedSessionIds, _pendingDeletedProjectIds);
        Context.MarkCollaborationThreadsDeletedLocally(collaborationThreadIds);
        RenderState();
        Workspace.EnsureManagedProjectSelection();
        await DeleteCollaborationThreadsForRemovedProjectAsync(collaborationThreadIds);
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"已删除项目：{removedTitle}，并删除 {sessionCount} 个项目会话。";
        WorkspaceSidebar.ConnectionStatusText.Text = $"项目已删除：{removedTitle}";
        await SaveStateAsync();
    }

    internal static IReadOnlyList<string> CollaborationThreadIdsForProjectDeletion(string projectId, IEnumerable<DesktopSession> projectSessions)
    {
        var result = new List<string>();
        if (!string.IsNullOrWhiteSpace(projectId))
        {
            result.Add($"project-{ContextController.NormalizeCollaborationThreadKey(projectId)}");
        }
        foreach (var session in projectSessions)
        {
            if (!string.IsNullOrWhiteSpace(session.Id))
            {
                result.Add($"session-{ContextController.NormalizeCollaborationThreadKey(session.Id)}");
            }
        }
        return result
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    private async Task DeleteCollaborationThreadsForRemovedProjectAsync(IEnumerable<string> threadIds)
    {
        foreach (var threadId in threadIds)
        {
            try
            {
                using var doc = await PostJsonAsync($"{Workspace.ApiBase()}/desktop/collaboration", new
                {
                    action = "delete_thread",
                    thread_id = threadId,
                    status = "deleted",
                    title = threadId,
                });
                JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "delete collaboration thread");
            }
            catch
            {
                // Project deletion should not be blocked by an unavailable collaboration backend.
            }
        }
    }

    internal async Task ToggleSelectedProjectArchiveAsync()
    {
        var project = Workspace.SelectedProject();
        if (project is null)
        {
            return;
        }
        await ToggleProjectArchiveAsync(project.Id);
    }

    internal async Task NewSessionForSelectedProjectAsync()
    {
        var project = Workspace.SelectedProject();
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "请先选择项目。";
            return;
        }
        _expandedProjectIds.Add(project.Id);
        var beforeIds = _state.Sessions.Select(session => session.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        await NewSessionAsync(project.Id);
        var created = _state.Sessions.FirstOrDefault(session => !beforeIds.Contains(session.Id) && string.Equals(session.ProjectId, project.Id, StringComparison.OrdinalIgnoreCase));
        WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        Workspace.OpenManagementPage("tasks", "projects");
        if (created is not null)
        {
            WorkbenchShell.ManagementPanels.ProjectSessionsList.SelectedValue = created.Id;
        }
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"已在项目中新建会话：{project.Title}";
    }

}
