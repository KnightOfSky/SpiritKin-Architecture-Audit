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
    internal async void SessionArchiveToggle_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string id)
        {
            return;
        }
        await ToggleSessionArchiveAsync(id);
    }

    internal async void SessionDelete_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string id)
        {
            return;
        }
        await DeleteSessionByIdAsync(id);
    }

    internal async Task DeleteSessionByIdAsync(string id)
    {
        var removed = _state.Sessions.FirstOrDefault(session => string.Equals(session.Id, id, StringComparison.OrdinalIgnoreCase));
        if (removed is null)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "没有找到要删除的会话。";
            return;
        }
        if (_state.Sessions.Count <= 1)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "至少保留一个会话，不能删除最后一个会话。";
            return;
        }
        if (!ConfirmDestructiveAction("删除会话", $"确定要删除“{removed.Title}”吗？删除后会从本地状态中移除，不能在归档视图恢复。"))
        {
            return;
        }
        _state.Sessions.RemoveAll(session => string.Equals(session.Id, id, StringComparison.OrdinalIgnoreCase));
        _pendingDeletedSessionIds.Add(id);
        if (!_state.Sessions.Any(session => session.Id == _state.ActiveSessionId))
        {
            var fallback = _state.Sessions
                .Where(session => string.IsNullOrWhiteSpace(session.ProjectId))
                .Where(Workspace.SessionMatchesCurrentFilter)
                .OrderBy(session => WorkspaceController.IsArchived(session.Status))
                .ThenByDescending(session => session.UpdatedAt)
                .FirstOrDefault()
                ?? _state.Sessions
                    .OrderBy(session => WorkspaceController.IsArchived(session.Status))
                    .ThenByDescending(session => session.UpdatedAt)
                    .First();
            _state.ActiveSessionId = fallback.Id;
        }
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = $"已删除会话：{removed.Title}";
        await SaveStateAsync();
    }

    internal async void ProjectPauseToggle_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string id)
        {
            return;
        }
        var project = _state.Projects.FirstOrDefault(item => item.Id == id);
        if (project is null)
        {
            return;
        }
        project.Status = string.Equals(project.Status, "paused", StringComparison.OrdinalIgnoreCase) ? "active" : "paused";
        project.UpdatedAt = NowSeconds();
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        RenderState();
        await SaveStateAsync();
    }

    internal async void ProjectComplete_Click(object sender, RoutedEventArgs e)
    {
        await UpdateProjectFromButtonAsync(sender, "complete");
    }

    internal async Task UpdateProjectFromButtonAsync(object sender, string status)
    {
        if (sender is not Button button || button.Tag is not string id)
        {
            return;
        }
        var project = _state.Projects.FirstOrDefault(item => item.Id == id);
        if (project is null)
        {
            return;
        }
        project.Status = status;
        project.UpdatedAt = NowSeconds();
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        RenderState();
        await SaveStateAsync();
    }

    internal async void ProjectDelete_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is string id)
        {
            await DeleteProjectByIdAsync(id);
        }
    }

    internal async void SessionRenameMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            await RenameSessionAsync(item.Id);
        }
    }

    internal async void SessionPinMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            await ToggleSessionPinnedAsync(item.Id);
        }
    }

    internal async void ProjectSessionPinMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            await ToggleSessionPinnedAsync(item.SessionId);
        }
    }

    internal async void ProjectSessionRenameMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            await RenameSessionAsync(item.SessionId);
        }
    }

    internal async void SessionArchiveMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            await ToggleSessionArchiveAsync(item.Id);
        }
    }

    internal async void SessionUnreadMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            await ToggleSessionUnreadAsync(item.Id);
        }
    }

    internal async void ProjectSessionArchiveMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            await ToggleSessionArchiveAsync(item.SessionId);
        }
    }

    internal async void ProjectSessionUnreadMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            await ToggleSessionUnreadAsync(item.SessionId);
        }
    }

    internal void SessionCopyIdMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            Clipboard.SetText(item.Id);
        }
    }

    internal void SessionCopyWorkingDirMenu_Click(object sender, RoutedEventArgs e)
    {
        Clipboard.SetText(_rootDir);
    }

    internal void SessionCopyDeeplinkMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            Clipboard.SetText(SessionDeeplink(item.Id));
        }
    }

    internal void SessionOpenExplorerMenu_Click(object sender, RoutedEventArgs e)
    {
        OpenWorkspaceInExplorer();
    }

    internal async void SessionDeleteMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            await DeleteSessionByIdAsync(item.Id);
        }
    }

    internal void CollaborationThreadOpenMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            ActivateCollaborationThreadFromSidebar(item.Id);
        }
    }

    internal async void CollaborationThreadArchiveMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            var nextStatus = item.Status.Equals("archived", StringComparison.OrdinalIgnoreCase) ? "active" : "archived";
            await Context.SetCurrentCollaborationThreadStatusAsync(item.Id, nextStatus);
        }
    }

    internal void CollaborationThreadCopyIdMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            Clipboard.SetText(item.Id);
            WorkspaceSidebar.ConnectionStatusText.Text = "已复制协作线程 ID。";
        }
    }

    internal async void CollaborationThreadDeleteMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            await Context.SetCurrentCollaborationThreadStatusAsync(item.Id, "deleted");
        }
    }

    internal void CollaborationThreadOpenManagementMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<SessionViewModel>(sender) is { } item)
        {
            Context.SetActiveCollaborationThread(item.Id);
        }
        Workspace.OpenManagementPage("collaboration");
    }

    internal async void ProjectNewChatMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            await NewSessionAsync(item.ProjectId);
        }
    }

    internal async void ProjectRenameMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            await RenameProjectAsync(item.ProjectId);
        }
    }

    internal async void ProjectArchiveMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            await ToggleProjectArchiveAsync(item.ProjectId);
        }
    }

    internal void ProjectOpenManagementMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            Workspace.SetWorkspaceProjectContextId(item.ProjectId);
            WorkspaceSidebar.ProjectsList.SelectedValue = item.ProjectId;
            Workspace.RenderEditors();
            Workspace.OpenManagementPage("tasks", "projects");
        }
    }

    internal void SidebarCopyIdMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item)
        {
            Clipboard.SetText(item.IsSession ? item.SessionId : item.ProjectId);
        }
    }

    internal void SidebarCopyWorkingDirMenu_Click(object sender, RoutedEventArgs e)
    {
        Clipboard.SetText(_rootDir);
    }

    internal void SidebarCopyDeeplinkMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is { } item && item.IsSession)
        {
            Clipboard.SetText(SessionDeeplink(item.SessionId));
        }
    }

    internal void SidebarOpenExplorerMenu_Click(object sender, RoutedEventArgs e)
    {
        OpenWorkspaceInExplorer();
    }

    internal async void SidebarDeleteMenu_Click(object sender, RoutedEventArgs e)
    {
        if (MenuTag<ProjectViewModel>(sender) is not { } item)
        {
            return;
        }
        if (item.IsSession)
        {
            await DeleteSessionByIdAsync(item.SessionId);
            return;
        }
        await DeleteProjectByIdAsync(item.ProjectId);
    }

    internal async Task RenameSessionAsync(string sessionId)
    {
        var session = _state.Sessions.FirstOrDefault(item => item.Id == sessionId);
        if (session is null)
        {
            return;
        }
        var title = PromptText("重命名会话", "会话标题", session.Title);
        if (string.IsNullOrWhiteSpace(title))
        {
            return;
        }
        session.Title = title.Trim();
        session.UpdatedAt = NowSeconds();
        RenderState();
        await SaveStateAsync();
    }

    internal async Task ToggleSessionArchiveAsync(string sessionId)
    {
        var session = _state.Sessions.FirstOrDefault(item => item.Id == sessionId);
        if (session is null)
        {
            return;
        }
        var restore = WorkspaceController.IsArchived(session.Status);
        session.Status = restore ? "active" : "archived";
        session.UpdatedAt = NowSeconds();
        _state.ActiveSessionId = session.Id;
        RenderState();
        var scope = string.IsNullOrWhiteSpace(session.ProjectId) ? "Chats" : "项目会话";
        WorkspaceSidebar.ConnectionStatusText.Text = restore ? $"已恢复{scope}：{session.Title}" : $"已归档{scope}：{session.Title}";
        await SaveStateAsync();
    }

    internal async Task ToggleSessionPinnedAsync(string sessionId)
    {
        var session = _state.Sessions.FirstOrDefault(item => item.Id == sessionId);
        if (session is null)
        {
            return;
        }
        session.IsPinned = !session.IsPinned;
        session.UpdatedAt = NowSeconds();
        RenderState();
        await SaveStateAsync();
    }

    internal async Task ToggleSessionUnreadAsync(string sessionId)
    {
        var session = _state.Sessions.FirstOrDefault(item => item.Id == sessionId);
        if (session is null)
        {
            return;
        }
        session.IsUnread = !session.IsUnread;
        session.UpdatedAt = NowSeconds();
        RenderState();
        await SaveStateAsync();
    }

    internal async Task ArchiveAllStandaloneChatsAsync()
    {
        var sessions = _state.Sessions
            .Where(session => string.IsNullOrWhiteSpace(session.ProjectId) && !WorkspaceController.IsArchived(session.Status))
            .ToList();
        if (sessions.Count == 0)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "没有可归档的 Chats 会话。";
            return;
        }
        if (!ConfirmDestructiveAction("归档全部 Chats", $"确定要归档 {sessions.Count} 个普通 Chats 会话吗？项目会话不会受影响。"))
        {
            return;
        }
        var now = NowSeconds();
        foreach (var session in sessions)
        {
            session.Status = "archived";
            session.UpdatedAt = now;
        }
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = $"已归档 {sessions.Count} 个 Chats 会话。";
        await SaveStateAsync();
    }

    internal async Task ArchiveAllProjectsAsync()
    {
        var projects = _state.Projects
            .Where(project => !WorkspaceController.IsArchived(project.Status))
            .ToList();
        if (projects.Count == 0)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "没有可归档的 Projects。";
            return;
        }
        if (!ConfirmDestructiveAction("归档全部 Projects", $"确定要归档 {projects.Count} 个项目吗？项目内会话会保留在项目里。"))
        {
            return;
        }
        var now = NowSeconds();
        foreach (var project in projects)
        {
            project.Status = "archived";
            project.UpdatedAt = now;
        }
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = $"已归档 {projects.Count} 个 Projects。";
        await SaveStateAsync();
    }

    internal async Task RenameProjectAsync(string projectId)
    {
        var project = _state.Projects.FirstOrDefault(item => item.Id == projectId);
        if (project is null)
        {
            return;
        }
        var title = PromptText("重命名项目", "项目名称", project.Title);
        if (string.IsNullOrWhiteSpace(title))
        {
            return;
        }
        project.Title = title.Trim();
        project.UpdatedAt = NowSeconds();
        RenderState();
        await SaveStateAsync();
    }

    internal async Task ToggleProjectArchiveAsync(string projectId)
    {
        var project = _state.Projects.FirstOrDefault(item => item.Id == projectId);
        if (project is null)
        {
            return;
        }
        var restore = WorkspaceController.IsArchived(project.Status);
        project.Status = restore ? "active" : "archived";
        project.UpdatedAt = NowSeconds();
        RenderState();
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = restore ? $"已恢复项目：{project.Title}" : $"已归档项目：{project.Title}";
        WorkspaceSidebar.ConnectionStatusText.Text = restore ? $"项目已恢复：{project.Title}" : $"项目已归档：{project.Title}";
        await SaveStateAsync();
    }

    internal DesktopItem? ResolveProjectInput(string input)
    {
        var text = input.Trim();
        if (int.TryParse(text, out var index) && index >= 1 && index <= _state.Projects.Count)
        {
            return _state.Projects[index - 1];
        }
        return _state.Projects.FirstOrDefault(project =>
            string.Equals(project.Id, text, StringComparison.OrdinalIgnoreCase) ||
            string.Equals(project.Title, text, StringComparison.OrdinalIgnoreCase));
    }

    internal static T? MenuTag<T>(object sender) where T : class => sender is MenuItem { Tag: T item } ? item : null;

    internal string SessionDeeplink(string sessionId) => $"spiritkin://desktop/session/{Uri.EscapeDataString(sessionId)}";

    internal void OpenWorkspaceInExplorer()
    {
        Process.Start(new ProcessStartInfo("explorer.exe", Workspace.ActiveWorkspaceRoot()) { UseShellExecute = true });
    }

}

