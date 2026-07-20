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

internal sealed partial class RuntimeController
{
    private async Task ForkActiveChatFromMessageAsync(DesktopSession sourceSession, string messageId)
    {
        var ordered = sourceSession.Messages.OrderBy(message => message.CreatedAt).ToList();
        var index = ordered.FindIndex(message => string.Equals(message.Id, messageId, StringComparison.OrdinalIgnoreCase));
        if (index < 0)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "未找到要 Fork 的消息。";
            return;
        }
        var now = NowSeconds();
        var fork = new DesktopSession
        {
            Id = NewId("session"),
            Title = $"{sourceSession.Title} · fork",
            Status = "active",
            ProjectId = sourceSession.ProjectId,
            CreatedAt = now,
            UpdatedAt = now,
            Messages = ordered
                .Take(index + 1)
                .Select(message => new DesktopMessage
                {
                    Id = NewId("msg"),
                    Role = message.Role,
                    Kind = message.Kind,
                    Text = message.Text,
                    Subtitle = message.Subtitle,
                    DurationSeconds = message.DurationSeconds,
                    CreatedAt = message.CreatedAt,
                    UpdatedAt = now,
                })
                .ToList(),
        };
        _state.Sessions.Add(fork);
        _state.ActiveSessionId = fork.Id;
        _workspaceControllerValue.SetQuickChatMode(false);
        _workspaceControllerValue.SetSessionFilter("active");
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = "已从该消息 Fork 新会话。";
        await SaveStateAsync();
    }

    internal async Task AddAutomationFromActiveChatAsync()
    {
        var active = _workspaceControllerValue.ActiveSession();
        var title = _navigationControllerValue.PromptText("添加自动化", "自动化名称", $"跟进：{active.Title}");
        if (string.IsNullOrWhiteSpace(title))
        {
            return;
        }
        var now = NowSeconds();
        _state.Tasks.Add(new DesktopItem
        {
            Id = NewId("automation"),
            Title = title.Trim(),
            Status = "pending",
            Source = "chat_automation",
            Detail = $"来自会话：{active.Title} ({active.Id})",
            CreatedAt = now,
            UpdatedAt = now,
        });
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = "已把自动化请求加入任务列表。";
        await SaveStateAsync();
    }

    internal async Task SummarizeActiveSessionTitleAsync()
    {
        var active = _workspaceControllerValue.ActiveSession();
        var title = SummarizeSessionTitle(BuildTitleSeed(active));
        if (string.IsNullOrWhiteSpace(title))
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "当前会话还没有足够内容生成标题。";
            return;
        }
        active.Title = title;
        active.UpdatedAt = NowSeconds();
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = $"已根据对话内容更新标题：{title}";
        await SaveStateAsync();
    }

    private static string BuildTitleSeed(DesktopSession session)
    {
        var user = session.Messages.LastOrDefault(message => message.Role.Equals("user", StringComparison.OrdinalIgnoreCase))?.Text;
        if (!string.IsNullOrWhiteSpace(user))
        {
            return user;
        }
        return session.Messages.LastOrDefault()?.Text ?? session.Title;
    }

    private ResolvedMessage? ResolveMessageFromMenu(object sender)
    {
        var menu = (sender as MenuItem)?.Parent as ContextMenu;
        var id = (menu?.PlacementTarget as FrameworkElement)?.Tag as string;
        return ResolveMessageById(id);
    }

    private ResolvedMessage? ResolveMessageById(string? id)
    {
        if (string.IsNullOrWhiteSpace(id))
        {
            return null;
        }
        var active = _workspaceControllerValue.ActiveSession();
        if (active.Messages.FirstOrDefault(message => string.Equals(message.Id, id, StringComparison.OrdinalIgnoreCase)) is { } activeMessage)
        {
            return new ResolvedMessage(active, activeMessage);
        }
        foreach (var session in _state.Sessions)
        {
            if (session.Messages.FirstOrDefault(message => string.Equals(message.Id, id, StringComparison.OrdinalIgnoreCase)) is { } message)
            {
                return new ResolvedMessage(session, message);
            }
        }
        return null;
    }

    private void MessageCopyMenu_Click(object sender, RoutedEventArgs e)
    {
        if (ResolveMessageFromMenu(sender) is not { } resolved)
        {
            return;
        }
        Clipboard.SetText(resolved.Message.Text);
        WorkspaceSidebar.ConnectionStatusText.Text = "已复制消息。";
    }

    private void MessageEditMenu_Click(object sender, RoutedEventArgs e)
    {
        if (ResolveMessageFromMenu(sender) is not { } resolved)
        {
            return;
        }
        BeginMessageEdit(resolved);
    }

    private void MessageCopyButton_Click(object sender, RoutedEventArgs e)
    {
        if (ResolveMessageFromElement(sender) is not { } resolved)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "未找到要复制的消息。";
            return;
        }
        Clipboard.SetText(resolved.Message.Text);
        WorkspaceSidebar.ConnectionStatusText.Text = "已复制消息。";
    }

    private void MessageEditButton_Click(object sender, RoutedEventArgs e)
    {
        if (ResolveMessageFromElement(sender) is not { } resolved)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "未找到要编辑的消息。";
            return;
        }
        BeginMessageEdit(resolved);
    }

    private void BeginMessageEdit(ResolvedMessage resolved)
    {
        var message = resolved.Message;
        if (!string.Equals(_state.ActiveSessionId, resolved.Session.Id, StringComparison.OrdinalIgnoreCase))
        {
            _state.ActiveSessionId = resolved.Session.Id;
        }
        _workspaceControllerValue.SetQuickChatMode(false);
        // 编辑只是改文字重发，不应顺手关掉协作模式（关掉会导致 @ 消息重发被拦）。
        _workspaceControllerValue.SetWorkspaceProjectContextId(_workspaceControllerValue.ProjectForSession(resolved.Session)?.Id ?? "");
        _editingMessageId = message.Id;
        _workspaceControllerValue.ShowWorkspacePage("chat");
        RenderState();
        FocusEditingMessageBox(message.Id);
    }

    private void CancelMessageEdit()
    {
        _editingMessageId = "";
        RenderState();
    }

    private async void MessageCancelEditButton_Click(object sender, RoutedEventArgs e)
    {
        CancelMessageEdit();
        await SaveStateAsync();
    }

    private async void MessageSendEditButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not FrameworkElement { Tag: string id })
        {
            return;
        }
        var viewModel = _messages.OfType<MessageViewModel>().FirstOrDefault(message => string.Equals(message.Id, id, StringComparison.OrdinalIgnoreCase));
        if (viewModel is null)
        {
            return;
        }
        await ResendEditedMessageAsync(id, viewModel.EditText);
    }

    private void FocusEditingMessageBox(string messageId)
    {
        _ = Dispatcher.InvokeAsync(() =>
        {
            var container = ChatWorkspace.MessagesList.ItemContainerGenerator.ContainerFromItem(_messages.FirstOrDefault(item => item.Id == messageId)) as DependencyObject;
            var textBox = FindVisualChild<TextBox>(container, box => box.IsVisible && string.Equals((box.DataContext as MessageViewModel)?.Id, messageId, StringComparison.OrdinalIgnoreCase));
            if (textBox is null)
            {
                return;
            }
            textBox.Focus();
            textBox.CaretIndex = textBox.Text.Length;
        }, System.Windows.Threading.DispatcherPriority.Background);
    }

    private async void MessageForkMenu_Click(object sender, RoutedEventArgs e)
    {
        if (ResolveMessageFromMenu(sender) is not { } resolved)
        {
            return;
        }
        await ForkActiveChatFromMessageAsync(resolved.Session, resolved.Message.Id);
    }

    private async void MessageForkButton_Click(object sender, RoutedEventArgs e)
    {
        var resolved = ResolveMessageFromElement(sender);
        if (resolved is null)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "未找到要 Fork 的消息。";
            return;
        }
        await ForkActiveChatFromMessageAsync(resolved.Session, resolved.Message.Id);
    }

    private ResolvedMessage? ResolveMessageFromElement(object sender)
    {
        if (sender is not DependencyObject dependency)
        {
            return null;
        }
        for (var current = dependency; current is not null; current = VisualTreeHelper.GetParent(current))
        {
            if (current is not FrameworkElement element)
            {
                continue;
            }
            if (element.DataContext is MessageViewModel viewModel && ResolveMessageById(viewModel.Id) is { } fromDataContext)
            {
                return fromDataContext;
            }
            if (element.Tag is string id && ResolveMessageById(id) is { } fromTag)
            {
                return fromTag;
            }
        }
        return null;
    }

    private void WorkMessageExpander_Expanded(object sender, RoutedEventArgs e)
    {
        SetWorkMessageExpanded(sender, true);
    }

    private void WorkMessageExpander_Collapsed(object sender, RoutedEventArgs e)
    {
        SetWorkMessageExpanded(sender, false);
    }

    private static void SetWorkMessageExpanded(object sender, bool expanded)
    {
        if (sender is not Expander { IsLoaded: true, DataContext: WorkChainViewModel viewModel })
        {
            return;
        }

        // The Expander template creates its internal ToggleButton before the item is
        // loaded. Keep expansion as transient view state so template initialization
        // cannot persist a false collapsed value or queue a full conversation save.
        viewModel.IsExpanded = expanded;
    }

    private void MessageItem_MouseEnter(object sender, MouseEventArgs e)
    {
    }

    private sealed record ResolvedMessage(DesktopSession Session, DesktopMessage Message);

    private static T? FindVisualChild<T>(DependencyObject? parent, Func<T, bool>? predicate = null)
        where T : DependencyObject
    {
        if (parent is null)
        {
            return null;
        }

        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = VisualTreeHelper.GetChild(parent, i);
            if (child is T match && (predicate is null || predicate(match)))
            {
                return match;
            }

            var nested = FindVisualChild(child, predicate);
            if (nested is not null)
            {
                return nested;
            }
        }

        return null;
    }

}


