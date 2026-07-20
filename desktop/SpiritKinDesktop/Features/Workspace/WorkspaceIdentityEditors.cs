using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace SpiritKinDesktop;

internal sealed partial class WorkspaceController
{
    internal static string DetectPackageManager(string workspace)
    {
        if (File.Exists(Path.Combine(workspace, "uv.lock")))
        {
            return "uv";
        }
        if (File.Exists(Path.Combine(workspace, "poetry.lock")))
        {
            return "poetry";
        }
        if (File.Exists(Path.Combine(workspace, "pyproject.toml")) || File.Exists(Path.Combine(workspace, "requirements.txt")))
        {
            return "pip";
        }
        if (File.Exists(Path.Combine(workspace, "pnpm-lock.yaml")))
        {
            return "pnpm";
        }
        if (File.Exists(Path.Combine(workspace, "yarn.lock")))
        {
            return "yarn";
        }
        if (File.Exists(Path.Combine(workspace, "package.json")))
        {
            return "npm";
        }
        if (Directory.EnumerateFiles(workspace, "*.csproj", SearchOption.TopDirectoryOnly).Any())
        {
            return "dotnet";
        }
        return "auto";
    }

    internal void RenderProjectRuntimeSummary(DesktopItem? project)
    {
        var workspace = ResolveProjectWorkspace(project) ?? _rootDir;
        var profile = BuildProjectRuntimeProfile(project, workspace);
        var env = string.IsNullOrWhiteSpace(profile.EnvFilePath) ? "env 未设置" : $"env {UiDisplayText.ShortTechnical(profile.EnvFilePath, 54)}";
        var deps = string.IsNullOrWhiteSpace(profile.DependencyFilePath) ? "依赖文件未设置" : $"依赖 {UiDisplayText.ShortTechnical(profile.DependencyFilePath, 54)}";
        var start = string.IsNullOrWhiteSpace(profile.StartCommand) ? "启动命令未设置" : $"启动 {UiDisplayText.ShortTechnical(profile.StartCommand, 54)}";
        WorkbenchShell.ManagementPanels.ProjectRuntimeSummaryText.Text = $"{profile.PackageManager} · {env} · {deps} · {start}";
    }

    internal string? ResolveProjectWorkspace(DesktopItem? project)
    {
        if (project is null)
        {
            return null;
        }
        var candidates = new List<string>();
        candidates.AddRange(ExtractWorkspaceCandidates(project.Detail));
        if (!string.IsNullOrWhiteSpace(project.WorkspacePath))
        {
            candidates.Add(project.WorkspacePath);
        }
        foreach (var candidate in candidates)
        {
            var expanded = Environment.ExpandEnvironmentVariables(candidate.Trim().Trim('"'));
            if (string.IsNullOrWhiteSpace(expanded))
            {
                continue;
            }
            var fullPath = Path.GetFullPath(Path.IsPathRooted(expanded) ? expanded : Path.Combine(_rootDir, expanded));
            if (Directory.Exists(fullPath))
            {
                return fullPath;
            }
        }
        return null;
    }

    internal string NormalizeWorkspacePath(string raw)
    {
        var expanded = Environment.ExpandEnvironmentVariables(raw.Trim().Trim('"'));
        return Path.GetFullPath(Path.IsPathRooted(expanded) ? expanded : Path.Combine(_rootDir, expanded));
    }

    internal static IEnumerable<string> ExtractWorkspaceCandidates(string? detail)
    {
        if (string.IsNullOrWhiteSpace(detail))
        {
            yield break;
        }
        foreach (var rawLine in detail.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var line = rawLine.Trim();
            var markerIndex = line.IndexOf('：');
            if (markerIndex < 0)
            {
                markerIndex = line.IndexOf(':');
            }
            if (markerIndex >= 0)
            {
                var key = line[..markerIndex].Trim().ToLowerInvariant();
                if (key is "workspace" or "workspace_path" or "working_directory" or "path" || key.Contains("工作区") || key.Contains("目录"))
                {
                    yield return line[(markerIndex + 1)..].Trim();
                }
            }
            if (Path.IsPathRooted(line))
            {
                yield return line;
            }
        }
    }

    internal void RenderWorkspaceIdentity(DesktopItem? project)
    {
        var workspace = ResolveProjectWorkspace(project) ?? _rootDir;
        var projectTitle = string.IsNullOrWhiteSpace(project?.Title) ? "未命名项目" : project.Title.Trim();
        var isProjectContext = project is not null;
        WorkspaceSidebar.WorkspaceRootText.Text = string.IsNullOrWhiteSpace(projectTitle)
            ? $"工作区：{workspace}"
            : $"工作区：{projectTitle}";
        WorkspaceSidebar.WorkspaceRootText.ToolTip = workspace;
        WorkbenchShell.WorkbenchWorkspaceRootText.Text = workspace;
        WorkbenchShell.WorkbenchWorkspaceRootText.ToolTip = workspace;
        WorkbenchShell.WorkbenchWorkspaceHintText.Text = isProjectContext
            ? $"/ {projectTitle}"
            : "/ 安静仪表带";
    }

    internal DesktopItem? SelectedTask()
    {
        var id = WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue as string ?? WorkspaceSidebar.TasksList.SelectedValue as string;
        return string.IsNullOrWhiteSpace(id) ? _state.Tasks.OrderByDescending(item => item.UpdatedAt).FirstOrDefault() : _state.Tasks.FirstOrDefault(item => item.Id == id);
    }

    internal void RenderEditors()
    {
        var active = ActiveSession();
        var editorSession = ManagedEditorSession();
        WorkbenchShell.ManagementPanels.SessionTitleEditBox.Text = editorSession.Title;
        SetComboText(WorkbenchShell.ManagementPanels.SessionStatusBox, editorSession.Status);
        WorkbenchShell.ManagementPanels.ArchiveSessionButton.Content = string.Equals(editorSession.Status, "archived", StringComparison.OrdinalIgnoreCase) ? "恢复会话" : "归档会话";
        var editorSessionTitle = string.IsNullOrWhiteSpace(editorSession.Title) ? editorSession.Id : editorSession.Title;
        var editorProject = ProjectForSession(editorSession);
        var ownerLabel = editorProject is null ? "Chats" : $"项目：{editorProject.Title}";
        WorkbenchShell.ManagementPanels.SessionManagementStatusText.Text = IsArchived(editorSession.Status)
            ? $"正在编辑“{editorSessionTitle}” · {ownerLabel} · 已归档 · {editorSession.Messages.Count} 条消息。"
            : $"正在编辑“{editorSessionTitle}” · {ownerLabel} · 活动 · {editorSession.Messages.Count} 条消息。";
        WorkbenchShell.ManagementPanels.SelectedSessionDetailText.Text =
            $"ID: {editorSession.Id}{Environment.NewLine}" +
            $"所属: {ownerLabel}{Environment.NewLine}" +
            $"创建: {FormatTime(editorSession.CreatedAt)} · 更新: {FormatTime(editorSession.UpdatedAt)}";
        WorkbenchShell.ManagementPanels.OpenManagedSessionButton.IsEnabled = !string.Equals(active.Id, editorSession.Id, StringComparison.OrdinalIgnoreCase);
        WorkbenchShell.ManagementPanels.MoveSessionToChatsButton.IsEnabled = !string.IsNullOrWhiteSpace(editorSession.ProjectId);
        WorkbenchShell.ManagementPanels.PinManagedSessionButton.Content = editorSession.IsPinned ? "取消置顶" : "置顶";

        var selectedProject = SelectedProject();
        RenderWorkspaceIdentity(CurrentWorkspaceProject());
        WorkbenchShell.ManagementPanels.ProjectTitleEditBox.Text = selectedProject?.Title ?? "";
        WorkbenchShell.ManagementPanels.ProjectDetailEditBox.Text = selectedProject?.Detail ?? "";
        WorkbenchShell.ManagementPanels.ProjectWorkspacePathBox.Text = selectedProject is null
            ? ""
            : ResolveProjectWorkspace(selectedProject) ?? selectedProject.WorkspacePath ?? _rootDir;
        WorkbenchShell.ManagementPanels.ProjectEnvFileBox.Text = selectedProject?.EnvFilePath ?? "";
        WorkbenchShell.ManagementPanels.ProjectDependencyFileBox.Text = selectedProject?.DependencyFilePath ?? "";
        SetComboText(WorkbenchShell.ManagementPanels.ProjectPackageManagerBox, selectedProject?.PackageManager ?? "auto");
        WorkbenchShell.ManagementPanels.ProjectStartCommandBox.Text = selectedProject?.StartCommand ?? "";
        RenderProjectRuntimeSummary(selectedProject);
        SetComboText(WorkbenchShell.ManagementPanels.ProjectStatusBox, selectedProject?.Status ?? "active");
        WorkbenchShell.ManagementPanels.ArchiveProjectButton.Content = selectedProject is not null && IsArchived(selectedProject.Status) ? "恢复项目" : "归档项目";

        var selectedTask = SelectedTask();
        WorkbenchShell.ManagementPanels.TaskTitleEditBox.Text = selectedTask?.Title ?? "";
        WorkbenchShell.ManagementPanels.TaskDetailEditBox.Text = selectedTask?.Detail ?? "";
        SetComboText(WorkbenchShell.ManagementPanels.TaskStatusBox, selectedTask?.Status ?? "pending");
    }
}


