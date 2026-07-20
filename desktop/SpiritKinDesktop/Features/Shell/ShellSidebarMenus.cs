using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    private void OpenChatsMenu()
    {
        var menu = new ContextMenu { PlacementTarget = WorkspaceSidebar.ChatsMenuButton, Placement = PlacementMode.Bottom };
        _shellInteractionController.AddContextMenuItem(menu, "显示活动会话", (_, _) => { _workspaceController.SetSessionFilter("active"); _runtimeController.RenderState(); });
        _shellInteractionController.AddContextMenuItem(menu, "显示归档会话", (_, _) => { _workspaceController.SetSessionFilter("archived"); _runtimeController.RenderState(); });
        _shellInteractionController.AddContextMenuItem(menu, "显示全部会话", (_, _) => { _workspaceController.SetSessionFilter("all"); _runtimeController.RenderState(); });
        menu.Items.Add(_shellInteractionController.CreateStyledSeparator());
        _shellInteractionController.AddContextMenuItem(menu, "归档全部 Chats", async (_, _) => await _navigationController.ArchiveAllStandaloneChatsAsync());
        _shellInteractionController.ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    private void OpenProjectsMenu()
    {
        var menu = new ContextMenu { PlacementTarget = WorkspaceSidebar.ProjectsMenuButton, Placement = PlacementMode.Bottom };
        _shellInteractionController.AddContextMenuItem(menu, "显示活动项目", (_, _) => { _workspaceController.SetSessionFilter("active"); _runtimeController.RenderState(); });
        _shellInteractionController.AddContextMenuItem(menu, "显示归档项目", (_, _) => { _workspaceController.SetSessionFilter("archived"); _runtimeController.RenderState(); });
        _shellInteractionController.AddContextMenuItem(menu, "显示全部项目", (_, _) => { _workspaceController.SetSessionFilter("all"); _runtimeController.RenderState(); });
        menu.Items.Add(new Separator());
        _shellInteractionController.AddContextMenuItem(menu, "新建项目", async (_, _) => await _runtimeController.CreateProjectFromSidebarAsync());
        _shellInteractionController.AddContextMenuItem(menu, "归档全部 Projects", async (_, _) => await _navigationController.ArchiveAllProjectsAsync());
        _shellInteractionController.AddContextMenuItem(menu, "打开项目管理", (_, _) => _workspaceController.OpenManagementPage("tasks", "projects"));
        _shellInteractionController.ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    private void OpenCollaborationMenu()
    {
        var menu = new ContextMenu { PlacementTarget = WorkspaceSidebar.CollaborationMenuButton, Placement = PlacementMode.Bottom };
        _shellInteractionController.AddContextMenuItem(menu, "打开当前协作会话", (_, _) => _navigationController.ActivateCollaborationChatFromSidebar());
        _shellInteractionController.AddContextMenuItem(menu, "新建独立协作会话", (_, _) => _navigationController.NewCollaborationThreadFromSidebar());
        _shellInteractionController.AddContextMenuItem(menu, "刷新协作诊断", async (_, _) => await _contextController.LoadCollaborationAsync());
        _shellInteractionController.AddContextMenuItem(menu, "标记当前协作会话已读", async (_, _) => await _contextController.MarkCurrentCollaborationThreadReadAsync());
        menu.Items.Add(_shellInteractionController.CreateStyledSeparator());
        _shellInteractionController.AddContextMenuItem(menu, "打开协作诊断", (_, _) => _workspaceController.OpenManagementPage("collaboration"));
        _shellInteractionController.AddContextMenuItem(menu, "复制当前协作会话 ID", (_, _) =>
        {
            Clipboard.SetText(_contextController.EnsureCollaborationTaskId());
            WorkspaceSidebar.ConnectionStatusText.Text = "已复制协作会话 ID。";
        });
        _shellInteractionController.ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    private void OpenChatActionsMenu()
    {
        var active = _workspaceController.ActiveSession();
        var menu = new ContextMenu { PlacementTarget = ChatWorkspace.ChatActionsButton, Placement = PlacementMode.Bottom };
        _shellInteractionController.AddContextMenuItem(menu, active.IsPinned ? "Unpin chat" : "Pin chat", async (_, _) => await _navigationController.ToggleSessionPinnedAsync(active.Id));
        _shellInteractionController.AddContextMenuItem(menu, "Rename chat", async (_, _) => await _navigationController.RenameSessionAsync(active.Id));
        _shellInteractionController.AddContextMenuItem(menu, "Summarize title", async (_, _) => await _runtimeController.SummarizeActiveSessionTitleAsync());
        _shellInteractionController.AddContextMenuItem(menu, active.Status.Equals("archived", StringComparison.OrdinalIgnoreCase) ? "Restore chat" : "Archive chat", async (_, _) => await _navigationController.ToggleSessionArchiveAsync(active.Id));
        menu.Items.Add(_shellInteractionController.CreateStyledSeparator());
        _shellInteractionController.AddContextMenuItem(menu, "Add automation...", async (_, _) => await _runtimeController.AddAutomationFromActiveChatAsync());
        _shellInteractionController.ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    private void OpenInlineWebPreviewMenu()
    {
        var menu = new ContextMenu { PlacementTarget = ChatWorkspace.InlineOpenWebPreviewButton, Placement = PlacementMode.Bottom };
        _shellInteractionController.AddContextMenuItem(menu, "Desktop preview", async (_, _) => await _workspaceController.OpenWebPreviewWindowAsync());
        _shellInteractionController.AddContextMenuItem(menu, "Local Edge browser", (_, _) => _workbenchController.LaunchLocalEdgeBrowser());
        _shellInteractionController.ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

}


