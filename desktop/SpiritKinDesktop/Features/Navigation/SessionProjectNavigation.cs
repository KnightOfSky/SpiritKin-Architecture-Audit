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
    internal void SessionsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        if (WorkspaceSidebar.SessionsList.SelectedItem is SessionViewModel selected)
        {
            ClearProjectSelectionForStandaloneChat();
            ActivateSessionFromSidebar(selected.Id);
        }
    }

    internal void ProjectsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        if (WorkspaceSidebar.ProjectsList.SelectedItem is ProjectViewModel selected && selected.IsSession)
        {
            ClearStandaloneChatSelectionForProject();
            Workspace.SetWorkspaceProjectContextId(selected.ProjectId);
            ActivateSessionFromSidebar(selected.SessionId);
            return;
        }
        if (WorkspaceSidebar.ProjectsList.SelectedItem is ProjectViewModel selectedProject && selectedProject.IsProject)
        {
            ClearStandaloneChatSelectionForProject();
            Workspace.SetWorkspaceProjectContextId(selectedProject.ProjectId);
            SyncCollaborationScopeToProject(selectedProject.ProjectId);
        }
        Workspace.RenderEditors();
    }

    internal void RightProjectsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        if (WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue is string id)
        {
            Workspace.SetWorkspaceProjectContextId(id);
            WorkspaceSidebar.ProjectsList.SelectedValue = id;
        }
        Workspace.RenderEditors();
    }

    internal void ProjectSessionsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        if (WorkbenchShell.ManagementPanels.ProjectSessionsList.SelectedItem is ProjectViewModel selected && selected.IsSession)
        {
            ActivateSessionFromSidebar(selected.SessionId);
        }
        SyncProjectSessionButtons();
    }

    internal void ManagedSessionsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Workspace.RenderEditors();
    }

    internal ProjectViewModel? SelectedManagedSession()
    {
        return WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedItem as ProjectViewModel;
    }

    internal DesktopSession ManagedEditorSession()
    {
        var selectedId = SelectedManagedSession()?.SessionId;
        if (!string.IsNullOrWhiteSpace(selectedId)
            && _state.Sessions.FirstOrDefault(session => string.Equals(session.Id, selectedId, StringComparison.OrdinalIgnoreCase)) is { } selected)
        {
            return selected;
        }
        return Workspace.ActiveSession();
    }

    internal void OpenSelectedManagedSession()
    {
        var session = ManagedEditorSession();
        ActivateSessionFromSidebar(session.Id);
        WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue = session.Id;
        WorkbenchShell.ManagementPanels.SessionManagementStatusText.Text = $"已打开会话：{session.Title}";
    }

    internal async Task MoveSelectedManagedSessionToChatsAsync()
    {
        var session = ManagedEditorSession();
        if (string.IsNullOrWhiteSpace(session.ProjectId))
        {
            WorkbenchShell.ManagementPanels.SessionManagementStatusText.Text = $"会话“{session.Title}”已经在 Chats。";
            return;
        }
        session.PreviousProjectId = session.ProjectId;
        session.ProjectId = null;
        session.UpdatedAt = NowSeconds();
        _state.ActiveSessionId = session.Id;
        RenderState();
        WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue = session.Id;
        WorkbenchShell.ManagementPanels.SessionManagementStatusText.Text = $"已将会话“{session.Title}”移到 Chats。";
        await SaveStateAsync();
    }

    internal async Task ToggleSelectedManagedSessionPinnedAsync()
    {
        var session = ManagedEditorSession();
        await ToggleSessionPinnedAsync(session.Id);
        WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue = session.Id;
    }

    internal void SyncProjectSessionButtons()
    {
        var selected = SelectedManagedProjectSession();
        var hasSelection = selected is not null;
        WorkbenchShell.ManagementPanels.RenameProjectSessionButton.IsEnabled = hasSelection;
        WorkbenchShell.ManagementPanels.ArchiveProjectSessionButton.IsEnabled = hasSelection;
        WorkbenchShell.ManagementPanels.DeleteProjectSessionButton.IsEnabled = hasSelection;
        WorkbenchShell.ManagementPanels.ArchiveProjectSessionButton.Content = selected is not null && WorkspaceController.IsArchived(selected.Status) ? "恢复项目会话" : "归档项目会话";
    }

    internal void ProjectsList_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (_rendering || WorkspaceSidebar.ProjectsList.SelectedItem is not ProjectViewModel selected)
        {
            return;
        }
        if (selected.IsSession)
        {
            ClearStandaloneChatSelectionForProject();
            Workspace.SetWorkspaceProjectContextId(selected.ProjectId);
            ActivateSessionFromSidebar(selected.SessionId);
            return;
        }
        if (selected.IsProject)
        {
            ClearStandaloneChatSelectionForProject();
            Workspace.SetWorkspaceProjectContextId(selected.ProjectId);
            SyncCollaborationScopeToProject(selected.ProjectId);
            if (_expandedProjectIds.Contains(selected.ProjectId))
            {
                _expandedProjectIds.Remove(selected.ProjectId);
            }
            else
            {
                _expandedProjectIds.Add(selected.ProjectId);
            }
            RenderState();
        }
    }

    internal void ActivateSessionFromSidebar(string sessionId)
    {
        if (string.IsNullOrWhiteSpace(sessionId))
        {
            return;
        }
        if (string.Equals(sessionId, ContextController.CollaborationChatSessionId, StringComparison.OrdinalIgnoreCase))
        {
            ActivateCollaborationChatFromSidebar();
            return;
        }
        // 同会话点击快路径：会话列表只由 SelectionChanged 驱动，避免一次点击重复刷新；
        // 已选中会话只确保返回聊天页，不触发网络刷新或协作模式变更。
        if (string.Equals(_state.ActiveSessionId, sessionId, StringComparison.OrdinalIgnoreCase))
        {
            Workspace.ShowWorkspacePage("chat");
            return;
        }
        var collaborationWasActive = Context.CollaborationChatActive;
        if (!collaborationWasActive)
        {
            Context.ClearActiveCollaborationThread();
        }
        var targetSession = _state.Sessions.FirstOrDefault(session => string.Equals(session.Id, sessionId, StringComparison.OrdinalIgnoreCase)) ?? Workspace.ActiveSession();
        var previousProjectId = Workspace.WorkspaceProjectContextId;
        var targetProjectId = Workspace.ProjectForSession(targetSession)?.Id ?? "";
        Workspace.SetWorkspaceProjectContextId(targetProjectId);
        if (!collaborationWasActive)
        {
            WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text = "";
        }
        Context.ClearCollaborationChatSignature();
        _state.ActiveSessionId = sessionId;
        if (collaborationWasActive)
        {
            // 模型协作是 Composer 模式，不应被会话导航隐式关闭。切换后只重绑到目标会话线程。
            Context.SetActiveCollaborationThread(Context.CurrentSessionCollaborationThreadId());
        }
        Workspace.SetQuickChatMode(false);
        if (!string.Equals(previousProjectId, targetProjectId, StringComparison.OrdinalIgnoreCase))
        {
            Workbench.ResetTerminalSession("项目已切换，终端将在下一条命令前重载运行 Profile。");
        }
        RenderActiveSessionSwitch();
        if (collaborationWasActive)
        {
            _ = Context.LoadCollaborationAsync();
        }
        _ = Workspace.SyncAvatarSessionAsync();
        _ = SaveStateAsync();
        Workspace.ShowWorkspacePage("chat");
    }

    internal void ActivateCollaborationChatFromSidebar()
    {
        Context.SetCollaborationChatActive(true);
        Workspace.SetWorkspaceProjectContextId(Workspace.ProjectForSession(Workspace.ActiveSession())?.Id ?? "");
        WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text = "";
        Context.SetActiveCollaborationThread(Context.DefaultCollaborationThreadId());
        Context.ClearCollaborationChatSignature();
        Workspace.SetQuickChatMode(false);
        _ = Context.LoadCollaborationAsync();
        RenderState();
        Workspace.ShowWorkspacePage("chat");
    }

    internal void CollaborationThreadsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering || Context.SyncingCollaborationThreadSelection)
        {
            return;
        }
        if (WorkspaceSidebar.CollaborationThreadsList.SelectedItem is SessionViewModel selected)
        {
            ActivateCollaborationThreadFromSidebar(selected.Id);
        }
    }

    internal void ClearProjectSelectionForStandaloneChat()
    {
        _rendering = true;
        try
        {
            WorkspaceSidebar.ProjectsList.SelectedIndex = -1;
        }
        finally
        {
            _rendering = false;
        }
    }

    internal void ClearStandaloneChatSelectionForProject()
    {
        _rendering = true;
        try
        {
            WorkspaceSidebar.SessionsList.SelectedIndex = -1;
        }
        finally
        {
            _rendering = false;
        }
    }

    internal void CollaborationThreadsList_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (_rendering || Context.SyncingCollaborationThreadSelection)
        {
            return;
        }
        if (WorkspaceSidebar.CollaborationThreadsList.SelectedItem is SessionViewModel selected)
        {
            ActivateCollaborationThreadFromSidebar(selected.Id);
        }
    }

    internal void CollaborationContextBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering || Context.SyncingCollaborationContextSelection)
        {
            return;
        }
        if (WorkspaceSidebar.CollaborationContextBox.SelectedItem is QuickCommandViewModel selected)
        {
            ActivateCollaborationThreadFromSidebar(selected.Id);
        }
    }

    internal void CollaborationProjectScopeBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering || Context.SyncingCollaborationContextSelection)
        {
            return;
        }
        if (WorkspaceSidebar.CollaborationProjectScopeBox.SelectedItem is not QuickCommandViewModel selected)
        {
            return;
        }
        if (!string.Equals(selected.Id, ContextController.CollaborationChatsScopeId, StringComparison.OrdinalIgnoreCase))
        {
            Workspace.SetWorkspaceProjectContextId(selected.Id);
            WorkspaceSidebar.ProjectsList.SelectedValue = selected.Id;
        }
        else
        {
            Workspace.ClearWorkspaceProjectContextId();
        }
        var defaultThread = string.Equals(selected.Id, ContextController.CollaborationChatsScopeId, StringComparison.OrdinalIgnoreCase)
            ? $"project-{ContextController.NormalizeCollaborationThreadKey(ContextController.CollaborationChatsScopeId)}"
            : $"project-{ContextController.NormalizeCollaborationThreadKey(selected.Id)}";
        Context.SetActiveCollaborationThread(defaultThread);
        Context.ClearCollaborationScopeSignature();
        Context.RenderCollaborationThreads();
        Context.RenderCollaborationChatMessagesIfChanged(force: true);
    }

    internal void CollaborationSessionScopeBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering || Context.SyncingCollaborationContextSelection)
        {
            return;
        }
        if (WorkspaceSidebar.CollaborationSessionScopeBox.SelectedItem is QuickCommandViewModel selected)
        {
            ActivateCollaborationThreadFromSidebar(selected.Id);
        }
    }

    internal void SyncCollaborationScopeToProject(string projectId)
    {
        if (string.IsNullOrWhiteSpace(projectId))
        {
            return;
        }
        var projectThread = $"project-{ContextController.NormalizeCollaborationThreadKey(projectId)}";
        Context.SetActiveCollaborationThread(projectThread);
        Context.ClearCollaborationScopeSignature();
        Context.RenderCollaborationThreads();
    }

    internal void ActivateCollaborationThreadFromSidebar(string threadId)
    {
        if (string.IsNullOrWhiteSpace(threadId))
        {
            return;
        }
        Context.SetCollaborationChatActive(true);
        Workspace.SetQuickChatMode(false);
        Context.SetActiveCollaborationThread(threadId);
        ActivateDesktopSessionForCollaborationThread(threadId);
        _ = Context.LoadCollaborationAsync();
        RenderState();
        Workspace.ShowWorkspacePage("chat");
    }

    internal void ActivateDesktopSessionForCollaborationThread(string threadId)
    {
        var normalized = (threadId ?? "").Trim();
        if (!normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        var sessionKey = normalized[8..];
        var session = _state.Sessions.FirstOrDefault(item => string.Equals(ContextController.NormalizeCollaborationThreadKey(item.Id), sessionKey, StringComparison.OrdinalIgnoreCase));
        if (session is null)
        {
            return;
        }
        _state.ActiveSessionId = session.Id;
        Workspace.SetWorkspaceProjectContextId(session.ProjectId ?? "");
        if (!string.IsNullOrWhiteSpace(session.ProjectId))
        {
            _expandedProjectIds.Add(session.ProjectId);
        }
        Workbench.ResetTerminalSession("协作绑定会话已切换，终端将在下一条命令前重载运行 Profile。");
    }

    internal void NewCollaborationThreadFromSidebar()
    {
        var topic = PromptText("新建协作话题", "话题名称", "ui-refactor");
        if (string.IsNullOrWhiteSpace(topic))
        {
            return;
        }
        ActivateCollaborationThreadFromSidebar(ContextController.NormalizeCollaborationThreadId(topic));
    }

}

