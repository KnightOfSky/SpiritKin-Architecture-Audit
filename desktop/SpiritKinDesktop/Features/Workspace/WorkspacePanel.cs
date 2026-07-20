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
    internal void SessionFilterBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering || _updatingSessionFilter)
        {
            return;
        }
        _sessionFilter = (WorkspaceSidebar.SessionFilterBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "active";
        RenderState();
    }

    internal void SetSessionFilter(string filter)
    {
        _sessionFilter = string.IsNullOrWhiteSpace(filter) ? "active" : filter;
        SyncSessionFilterSelection();
    }

    internal void SyncSessionFilterSelection()
    {
        _updatingSessionFilter = true;
        try
        {
            foreach (var item in WorkspaceSidebar.SessionFilterBox.Items.OfType<ComboBoxItem>())
            {
                if (string.Equals(item.Tag as string, _sessionFilter, StringComparison.OrdinalIgnoreCase))
                {
                    WorkspaceSidebar.SessionFilterBox.SelectedItem = item;
                    return;
                }
            }
            WorkspaceSidebar.SessionFilterBox.SelectedIndex = 0;
            _sessionFilter = "active";
        }
        finally
        {
            _updatingSessionFilter = false;
        }
    }

    internal bool SessionMatchesCurrentFilter(DesktopSession session)
    {
        return _sessionFilter switch
        {
            "archived" => IsArchived(session.Status),
            "all" => true,
            _ => !IsArchived(session.Status),
        };
    }

    internal static bool IsArchived(string? status) => string.Equals(status, "archived", StringComparison.OrdinalIgnoreCase);

    internal void WorkspaceNavList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var page = (WorkspaceSidebar.WorkspaceNavList.SelectedItem as ListBoxItem)?.Tag as string ?? "chat";
        if (page == "management"
            && WorkbenchShell.ManagementPanelHost.Visibility != Visibility.Visible
            && (WorkbenchShell.RightNavList.SelectedItem as ListBoxItem)?.Tag is not "overview")
        {
            SelectListBoxItemByTag(WorkbenchShell.RightNavList, "overview");
        }
        ApplyWorkspacePage(page);
    }

    internal void WorkspaceNavList_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        var page = (WorkspaceSidebar.WorkspaceNavList.SelectedItem as ListBoxItem)?.Tag as string ?? "chat";
        if (page == "chat")
        {
            OpenQuickChat();
        }
    }

    internal void OpenQuickChat()
    {
        _prepareQuickChat();
        _quickChatMode = true;
        _workspaceProjectContextId = "";
        ShowWorkspacePage("chat");
        RenderState();
    }

    internal void ShowWorkspacePage(string page)
    {
        SelectListBoxItemByTag(WorkspaceSidebar.WorkspaceNavList, page);
        ApplyWorkspacePage(page);
    }

    private void ApplyWorkspacePage(string page)
    {
        var management = page == "management";
        var workbenchCollapsed = WorkbenchPanelCollapsed();
        DesktopDiagnosticLog.Write(
            _rootDir,
            "workspace",
            "apply_workspace_page",
            page,
            $"management={management}; workbenchCollapsed={workbenchCollapsed}");
        ChatWorkspace.ChatWorkspacePage.Visibility = management ? Visibility.Collapsed : Visibility.Visible;
        ChatSplitterColumn.Width = new GridLength(management ? 0 : 6);
        ChatColumn.MinWidth = management ? 0 : 560;
        ChatColumn.Width = management ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        RightSplitterColumn.Width = new GridLength(management ? 0 : 6);
        RightPanelColumn.Width = management
            ? new GridLength(1, GridUnitType.Star)
            : new GridLength(300);
        RightPanelColumn.MinWidth = management ? 560 : 300;
        RightPanelColumn.MaxWidth = management ? double.PositiveInfinity : 300;

        WorkbenchShell.WorkbenchToolbar.Visibility = Visibility.Collapsed;
        WorkbenchShell.WorkbenchToolbarRow.Height = new GridLength(0);
        WorkbenchShell.AvatarPanel.Visibility = management ? Visibility.Collapsed : Visibility.Visible;
        WorkbenchShell.AvatarPanelSplitter.Visibility = Visibility.Collapsed;
        if (management)
        {
            WorkbenchShell.WorkbenchPanelRow.MinHeight = 0;
            WorkbenchShell.WorkbenchPanelRow.MaxHeight = double.PositiveInfinity;
            WorkbenchShell.WorkbenchPanelRow.Height = new GridLength(1, GridUnitType.Star);
            WorkbenchShell.AvatarPanelRow.MinHeight = 0;
            WorkbenchShell.AvatarPanelRow.MaxHeight = 0;
            WorkbenchShell.AvatarPanelRow.Height = new GridLength(0);
            WorkbenchShell.AvatarSplitterRow.Height = new GridLength(0);
        }
        else
        {
            WorkbenchShell.ApplyWorkbenchPanelFinalGeometry();
        }
        WorkbenchShell.WorkbenchStatusPanel.Visibility = !management && !workbenchCollapsed ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.WorkbenchRestoreBar.Visibility = !management && workbenchCollapsed ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.CollapseWorkbenchPanelButton.ToolTip = "收起桌面状态";

        Grid.SetRow(WorkbenchShell.AvatarPanel, 3);
        Grid.SetRowSpan(WorkbenchShell.AvatarPanel, 1);
        Grid.SetRow(WorkbenchShell.ManagementPanelHost, management ? 0 : 3);
        Grid.SetRowSpan(WorkbenchShell.ManagementPanelHost, management ? 4 : 1);
        WorkbenchShell.ManagementPanelHost.Visibility = management ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanelHost.Margin = management ? new Thickness(14, 12, 14, 12) : new Thickness(0);
    }

    internal DesktopSession CommitQuickChatDraft()
    {
        var session = ResolveQuickChatSessionForSend(
            _state,
            _quickChatMode,
            NewId("session"),
            NowSeconds(),
            out var materialized);
        if (!materialized)
        {
            return session;
        }

        _quickChatMode = false;
        _workspaceProjectContextId = "";
        SetSessionFilter("active");
        return session;
    }

    internal static DesktopSession ResolveQuickChatSessionForSend(
        DesktopState state,
        bool quickChatMode,
        string sessionId,
        double now,
        out bool materialized)
    {
        var active = state.Sessions.FirstOrDefault(session =>
            string.Equals(session.Id, state.ActiveSessionId, StringComparison.OrdinalIgnoreCase));
        if (!quickChatMode)
        {
            active ??= state.Sessions.FirstOrDefault();
            if (active is not null)
            {
                state.ActiveSessionId = active.Id;
                materialized = false;
                return active;
            }
        }

        var session = new DesktopSession
        {
            Id = sessionId,
            Title = "新会话",
            Status = "active",
            CreatedAt = now,
            UpdatedAt = now,
            Messages = new List<DesktopMessage>(),
        };
        state.Sessions.Add(session);
        state.ActiveSessionId = session.Id;
        materialized = true;
        return session;
    }

    internal void OpenManagementPage(string module, string? subPage = null)
    {
        ShowWorkspacePage("management");
        SelectListBoxItemByTag(WorkbenchShell.RightNavList, module);
        if (module == "tasks" && !string.IsNullOrWhiteSpace(subPage))
        {
            SelectListBoxItemByTag(WorkbenchShell.ManagementPanels.TaskSubNavList, subPage);
            if (subPage == "projects")
            {
                EnsureManagedProjectSelection();
            }
        }
        if (module == "agents" && !string.IsNullOrWhiteSpace(subPage))
        {
            SelectListBoxItemByTag(WorkbenchShell.ManagementPanels.AgentSubNavList, subPage);
        }
    }

    internal static void SelectListBoxItemByTag(ListBox listBox, string tag)
    {
        foreach (var item in listBox.Items.OfType<ListBoxItem>())
        {
            if (string.Equals(item.Tag as string, tag, StringComparison.OrdinalIgnoreCase))
            {
                listBox.SelectedItem = item;
                return;
            }
        }
    }

    private static string FirstNonEmpty(params string?[] values)
    {
        foreach (var value in values)
        {
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }
        }
        return "";
    }

    internal void EnsureManagedProjectSelection()
    {
        var selectedId = FirstNonEmpty(
            WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue as string,
            _workspaceProjectContextId,
            WorkspaceSidebar.ProjectsList.SelectedValue as string);
        if (!string.IsNullOrWhiteSpace(selectedId) && _state.Projects.Any(project => project.Id == selectedId))
        {
            _workspaceProjectContextId = selectedId;
            WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = selectedId;
            WorkspaceSidebar.ProjectsList.SelectedValue = selectedId;
        }
        else if (_managedProjects.Count > 0)
        {
            _workspaceProjectContextId = _managedProjects[0].ProjectId;
            WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = _managedProjects[0].ProjectId;
            WorkspaceSidebar.ProjectsList.SelectedValue = _managedProjects[0].ProjectId;
        }
        RenderEditors();
        ResetManagedProjectsListScroll();
    }

    private void ResetManagedProjectsListScroll()
    {
        try
        {
            Dispatcher.BeginInvoke(() =>
            {
                try
                {
                    FindVisualChild<ScrollViewer>(WorkbenchShell.ManagementPanels.RightProjectsList)?.ScrollToTop();
                }
                catch
                {
                    // Best-effort layout reset for the compact project picker.
                }
            }, DispatcherPriority.Loaded);
        }
        catch
        {
        }
    }

    internal void TaskSubNavList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var page = (WorkbenchShell.ManagementPanels.TaskSubNavList.SelectedItem as ListBoxItem)?.Tag as string ?? "sessions";
        WorkbenchShell.ManagementPanels.SessionManagementPage.Visibility = page == "sessions" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ProjectManagementPage.Visibility = page == "projects" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.TaskManagementPage.Visibility = page == "tasks" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "projects")
        {
            EnsureManagedProjectSelection();
        }
    }

    internal void AgentSubNavList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var page = (WorkbenchShell.ManagementPanels.AgentSubNavList.SelectedItem as ListBoxItem)?.Tag as string ?? "policy";
        WorkbenchShell.ManagementPanels.AgentPolicyPage.Visibility = page == "policy" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ExternalAssistantsPage.Visibility = page == "assistants" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.AgentAdaptersPage.Visibility = page == "adapters" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ManagedAgentsPage.Visibility = page == "agents" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.KnowledgeBasesPage.Visibility = page == "knowledge" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.RouteProfilesPage.Visibility = page == "routes" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.RemoteTargetsPage.Visibility = page == "remote" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.AgentStatusPage.Visibility = page == "status" ? Visibility.Visible : Visibility.Collapsed;
    }

    internal void ToggleRightNavigation()
    {
        _rightNavCollapsed = !_rightNavCollapsed;
        WorkbenchShell.RightNavColumn.Width = new GridLength(_rightNavCollapsed ? 46 : 112);
        WorkbenchShell.RightNavHeaderPanel.Visibility = _rightNavCollapsed ? Visibility.Collapsed : Visibility.Visible;
        WorkbenchShell.RightNavList.Visibility = _rightNavCollapsed ? Visibility.Collapsed : Visibility.Visible;
        WorkbenchShell.ToggleRightNavButton.Content = _rightNavCollapsed ? "›" : "‹";
    }

    internal void QuickCommandBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingQuickCommandSelection)
        {
            return;
        }
        if (ChatWorkspace.QuickCommandBox.SelectedItem is QuickCommandViewModel command && !string.IsNullOrWhiteSpace(command.Command))
        {
            ChatWorkspace.PromptBox.Text = command.Command;
            ChatWorkspace.PromptBox.Focus();
            ChatWorkspace.PromptBox.CaretIndex = ChatWorkspace.PromptBox.Text.Length;
            _syncingQuickCommandSelection = true;
            try
            {
                ChatWorkspace.QuickCommandBox.SelectedIndex = 0;
            }
            finally
            {
                _syncingQuickCommandSelection = false;
            }
        }
    }

    internal void RenderQuickCommandDropdown()
    {
        _syncingQuickCommandSelection = true;
        try
        {
            ChatWorkspace.QuickCommandBox.ItemsSource = _quickCommands
                .Prepend(new QuickCommandViewModel("", "Quick commands", ""))
                .ToList();
            ChatWorkspace.QuickCommandBox.SelectedIndex = 0;
        }
        finally
        {
            _syncingQuickCommandSelection = false;
        }
    }

}


