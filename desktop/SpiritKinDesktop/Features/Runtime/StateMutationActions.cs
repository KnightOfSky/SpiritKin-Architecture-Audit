using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal async Task NewSessionAsync(string? projectId = null)
    {
        var session = new DesktopSession
        {
            Id = NewId("session"),
            Title = string.IsNullOrWhiteSpace(projectId) ? "新会话" : "新项目会话",
            Status = "active",
            ProjectId = projectId,
            CreatedAt = NowSeconds(),
            UpdatedAt = NowSeconds(),
            Messages = new List<DesktopMessage>(),
        };
        _state.Sessions.Add(session);
        _state.ActiveSessionId = session.Id;
        _workspaceControllerValue.SetWorkspaceProjectContextId(string.IsNullOrWhiteSpace(projectId) ? "" : projectId);
        _workspaceControllerValue.SetQuickChatMode(false);
        _workspaceControllerValue.SetSessionFilter("active");
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = $"已新建会话：{session.Title}";
        await SaveStateAsync();
    }

    internal async Task CreateProjectFromSidebarAsync()
    {
        if (!_navigationControllerValue.TryPickNewProjectWorkspace(out var workspace, out var title))
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "已取消新建项目。";
            return;
        }
        var project = new DesktopItem
        {
            Id = NewId("project"),
            Title = title,
            Status = "active",
            WorkspacePath = workspace,
            CreatedAt = NowSeconds(),
            UpdatedAt = NowSeconds(),
        };
        _state.Projects.Add(project);
        _expandedProjectIds.Add(project.Id);
        _workspaceControllerValue.SetWorkspaceProjectContextId(project.Id);
        RenderState();
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"已从目录新建项目：{project.Title}";
        WorkspaceSidebar.ConnectionStatusText.Text = $"已新建项目：{project.Title}";
        _workspaceControllerValue.ShowWorkspacePage("chat");
        await SaveStateAsync();
    }

    internal async Task RefreshSessionsFromButtonAsync()
    {
        await LoadStateAsync();
        WorkspaceSidebar.ConnectionStatusText.Text = $"会话已刷新 · {DateTime.Now:T}";
    }

    internal async Task AddProjectAsync()
    {
        var typedTitle = WorkspaceSidebar.ProjectTitleBox.Text.Trim();
        if (!_navigationControllerValue.TryPickNewProjectWorkspace(out var pickedWorkspace, out var pickedTitle))
        {
            return;
        }
        var project = new DesktopItem
        {
            Id = NewId("project"),
            Title = string.IsNullOrWhiteSpace(typedTitle) ? pickedTitle : typedTitle,
            Status = "active",
            WorkspacePath = pickedWorkspace,
            CreatedAt = NowSeconds(),
            UpdatedAt = NowSeconds(),
        };
        _state.Projects.Add(project);
        _expandedProjectIds.Add(project.Id);
        _workspaceControllerValue.SetWorkspaceProjectContextId(project.Id);
        WorkspaceSidebar.ProjectTitleBox.Clear();
        RenderState();
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
        await SaveStateAsync();
    }

    internal async Task AddProjectFromEditorAsync()
    {
        var title = WorkbenchShell.ManagementPanels.ProjectTitleEditBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(title))
        {
            title = WorkspaceSidebar.ProjectTitleBox.Text.Trim();
        }
        if (!_navigationControllerValue.TryPickNewProjectWorkspace(out var editorWorkspace, out var pickedTitle))
        {
            WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "已取消新建项目。";
            return;
        }
        if (string.IsNullOrWhiteSpace(title))
        {
            title = pickedTitle;
        }
        var project = new DesktopItem
        {
            Id = NewId("project"),
            Title = title,
            Status = ComboText(WorkbenchShell.ManagementPanels.ProjectStatusBox),
            Detail = WorkbenchShell.ManagementPanels.ProjectDetailEditBox.Text.Trim(),
            WorkspacePath = _workspaceControllerValue.NormalizeWorkspacePath(editorWorkspace),
            CreatedAt = NowSeconds(),
            UpdatedAt = NowSeconds(),
        };
        _state.Projects.Add(project);
        _expandedProjectIds.Add(project.Id);
        _workspaceControllerValue.SetWorkspaceProjectContextId(project.Id);
        WorkspaceSidebar.ProjectTitleBox.Clear();
        RenderState();
        WorkspaceSidebar.ProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = $"已从目录新建项目：{project.Title}";
        await SaveStateAsync();
    }

    internal async Task AddTaskAsync()
    {
        var title = WorkspaceSidebar.TaskTitleBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(title))
        {
            return;
        }
        _state.Tasks.Add(new DesktopItem { Id = NewId("task"), Title = title, Status = "pending", Source = "wpf_desktop", CreatedAt = NowSeconds(), UpdatedAt = NowSeconds() });
        WorkspaceSidebar.TaskTitleBox.Clear();
        RenderState();
        await SaveStateAsync();
    }

    internal async Task AddTaskFromEditorAsync()
    {
        var title = WorkbenchShell.ManagementPanels.TaskTitleEditBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(title))
        {
            title = WorkspaceSidebar.TaskTitleBox.Text.Trim();
        }
        if (string.IsNullOrWhiteSpace(title))
        {
            title = $"任务 {DateTime.Now:HH:mm:ss}";
        }
        var task = new DesktopItem
        {
            Id = NewId("task"),
            Title = title,
            Status = ComboText(WorkbenchShell.ManagementPanels.TaskStatusBox),
            Detail = WorkbenchShell.ManagementPanels.TaskDetailEditBox.Text.Trim(),
            Source = "wpf_desktop",
            CreatedAt = NowSeconds(),
            UpdatedAt = NowSeconds(),
        };
        _state.Tasks.Add(task);
        WorkspaceSidebar.TaskTitleBox.Clear();
        RenderState();
        WorkspaceSidebar.TasksList.SelectedValue = task.Id;
        WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue = task.Id;
        await SaveStateAsync();
    }

    internal async Task SaveLocalNoteAsync()
    {
        var text = ChatWorkspace.PromptBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        AddMessage("system", text);
        ChatWorkspace.PromptBox.Clear();
        RenderState();
        await SaveStateAsync();
    }

    internal DesktopMessage AddMessage(string role, string text, string kind = "", double durationSeconds = 0, DesktopSession? targetSession = null)
    {
        text = CleanAvatarTags(text);
        // 回复/失败提示优先写回发起请求的会话；用户中途切换会话时不落到"当前正看着的"窗口。
        var active = _workspaceControllerValue.ActiveSession();
        var session = targetSession ?? active;
        var isActiveSession = ReferenceEquals(session, active)
            || string.Equals(session.Id, active.Id, StringComparison.OrdinalIgnoreCase);
        var now = NowSeconds();
        if (role.Equals("assistant", StringComparison.OrdinalIgnoreCase) && FindDuplicateRecentAssistantMessage(session, text, now) is { } duplicate)
        {
            return duplicate;
        }
        if (role.Equals("user", StringComparison.OrdinalIgnoreCase) && ShouldAutoTitleSession(session))
        {
            session.Title = SummarizeSessionTitle(text);
        }
        var message = new DesktopMessage { Id = NewId("msg"), Role = role, Text = text, Kind = kind, DurationSeconds = durationSeconds, CreatedAt = now, UpdatedAt = now };
        session.Messages.Add(message);
        session.UpdatedAt = now;
        if (isActiveSession)
        {
            _state.ActiveSessionId = session.Id;
            ScrollMessagesToEnd();
        }
        return message;
    }

    internal void ScrollMessagesToEnd()
    {
        if (_messageScrollPending)
        {
            // 流式追加会连续触发滚动请求；这里改为"再武装"而非丢弃，
            // 保证最后一次布局完成后仍会重新贴底，避免停在中途。
            _messageScrollRearm = true;
            return;
        }
        _messageScrollPending = true;
        try
        {
            Dispatcher.BeginInvoke(() =>
            {
                try
                {
                    EnsureMessageScrollHook();
                    if (_messageAutoScrollSticky)
                    {
                        if (_messages.Count > 0)
                        {
                            ChatWorkspace.MessagesList.ScrollIntoView(_messages[^1]);
                        }
                        var scrollViewer = FindVisualChild<ScrollViewer>(ChatWorkspace.MessagesList);
                        scrollViewer?.ScrollToEnd();
                    }
                }
                catch
                {
                    // Best-effort UI convenience; never block message rendering.
                }
                finally
                {
                    _messageScrollPending = false;
                    if (_messageScrollRearm)
                    {
                        _messageScrollRearm = false;
                        ScrollMessagesToEnd();
                    }
                }
            }, DispatcherPriority.ContextIdle);
        }
        catch
        {
            _messageScrollPending = false;
        }
    }

    // 只挂一次 ScrollChanged：内容变高时若处于"贴底"态就重新贴底（流式/展开卡增高后仍跟随），
    // 用户主动上滚离开底部则暂停自动跟随，回到底部后恢复。
    private void EnsureMessageScrollHook()
    {
        if (_messageScrollViewerHooked)
        {
            return;
        }
        var scrollViewer = FindVisualChild<ScrollViewer>(ChatWorkspace.MessagesList);
        if (scrollViewer is null)
        {
            return;
        }
        scrollViewer.ScrollChanged += OnMessagesScrollChanged;
        _messageScrollViewerHooked = true;
    }

    // 锚点跳转要滚回上方旧消息，虚拟化会触发 ExtentHeightChange≠0，
    // 若仍处于"贴底"态会被立即拽回底部；跳转前先暂停跟随，用户滚回底部后自动恢复。
    internal void SuspendMessageAutoScroll() => _messageAutoScrollSticky = false;

    private void OnMessagesScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        if (sender is not ScrollViewer scrollViewer)
        {
            return;
        }
        if (Math.Abs(e.ExtentHeightChange) > 0.5)
        {
            if (_messageAutoScrollSticky)
            {
                scrollViewer.ScrollToVerticalOffset(scrollViewer.ScrollableHeight);
            }
            return;
        }
        _messageAutoScrollSticky = scrollViewer.ScrollableHeight <= 0
            || scrollViewer.VerticalOffset >= scrollViewer.ScrollableHeight - 40;
    }

}


