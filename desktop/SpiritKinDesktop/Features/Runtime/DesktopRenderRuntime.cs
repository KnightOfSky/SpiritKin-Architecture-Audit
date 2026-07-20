using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal void RenderState()
    {
        _rendering = true;
        var previousActiveSessionId = _state.ActiveSessionId;
        _state = _state.Normalized();
        var legacyCollaborationArtifactsRemoved = false;
        foreach (var session in _state.Sessions)
        {
            var previousMessageIds = session.Messages.Select(message => message.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
            if (!ContextController.NormalizeLegacyCollaborationExecutorArtifacts(session))
            {
                continue;
            }
            TrackDeletedMessages(
                session.Id,
                previousMessageIds.Except(session.Messages.Select(message => message.Id), StringComparer.OrdinalIgnoreCase));
            legacyCollaborationArtifactsRemoved = true;
        }
        if (legacyCollaborationArtifactsRemoved)
        {
            _ = SaveStateAsync();
        }
        EnsureActiveSessionMatchesFilter();
        var active = _workspaceControllerValue.ActiveSession();
        if (!string.IsNullOrWhiteSpace(active.ProjectId))
        {
            _expandedProjectIds.Add(active.ProjectId);
        }
        var selectedProjectId = _workspaceControllerValue.WorkspaceProjectContextId;
        var selectedManagedSessionId = WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue as string ?? previousActiveSessionId;
        var selectedManagedProjectId = selectedProjectId;
        var selectedProjectSessionId = WorkbenchShell.ManagementPanels.ProjectSessionsList.SelectedValue as string;
        _workspaceControllerValue.RenderWorkspaceIdentity(_workspaceControllerValue.CurrentWorkspaceProject());
        ChatWorkspace.ActiveTitleText.Text = "与 Spirit 的对话 · 编辑室视图";
        ChatWorkspace.ActiveTitleText.ToolTip = active.Title;
        var activeProject = _workspaceControllerValue.ProjectForSession(active);
        var collaborationMembers = (active.CollaborationAgents ?? new List<string>())
            .Where(agent => !string.IsNullOrWhiteSpace(agent))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
        ChatWorkspace.ActiveMetaText.Text = $"{active.Messages.Count} 条 · 更新 {FormatTime(active.UpdatedAt)}  Spirit  模型 Deepseek";
        ChatWorkspace.ActiveMetaText.ToolTip = activeProject is null
            ? active.Title
            : $"{activeProject.Title} · {active.Title}";
        RenderCollaborationMemberChips(collaborationMembers);
        var standaloneSessions = _state.Sessions.Where(session => string.IsNullOrWhiteSpace(session.ProjectId)).ToList();
        var activeSessionCount = standaloneSessions.Count(session => !WorkspaceController.IsArchived(session.Status));
        var archivedSessionCount = standaloneSessions.Count(session => WorkspaceController.IsArchived(session.Status));
        WorkspaceSidebar.SessionFilterSummaryText.Text = $"活动 {activeSessionCount} · 归档 {archivedSessionCount}";
        WorkspaceSidebar.ChatsSummaryText.Text = $"CHATS · 活动 {activeSessionCount} · 归档 {archivedSessionCount}";
        var activeProjectCount = _state.Projects.Count(project => !WorkspaceController.IsArchived(project.Status));
        var archivedProjectCount = _state.Projects.Count(project => WorkspaceController.IsArchived(project.Status));
        WorkspaceSidebar.ProjectsSummaryText.Text = $"PROJECTS · 活动 {activeProjectCount} · 归档 {archivedProjectCount}";
        _workspaceControllerValue.SyncSessionFilterSelection();

        _projects.Clear();
        _managedProjects.Clear();
        _managedSessions.Clear();
        foreach (var project in _state.Projects.OrderByDescending(p => p.UpdatedAt))
        {
            var projectSessions = _state.Sessions
                .Where(session => string.Equals(session.ProjectId, project.Id, StringComparison.OrdinalIgnoreCase))
                .OrderByDescending(session => session.IsPinned)
                .ThenByDescending(session => session.UpdatedAt)
                .ToList();
            var visibleProjectSessions = projectSessions
                .Where(_workspaceControllerValue.SessionMatchesCurrentFilter)
                .ToList();
            var expanded = _expandedProjectIds.Contains(project.Id);
            _projects.Add(ProjectViewModel.ForProject(project.Id, project.Title, project.Status, $"{visibleProjectSessions.Count}/{projectSessions.Count} 个会话", expanded));
            _managedProjects.Add(ProjectViewModel.ForProject(project.Id, project.Title, project.Status, $"{projectSessions.Count} 个会话", isExpanded: true));
            if (expanded)
            {
                foreach (var session in visibleProjectSessions)
                {
                    _projects.Add(ProjectViewModel.ForProjectSession(project.Id, session.Id, session.Title, $"{session.Messages.Count} 条 · {FormatTime(session.UpdatedAt)}", session.Status, session.IsPinned, session.IsUnread));
                }
            }
        }
        foreach (var session in _state.Sessions
            .OrderByDescending(session => session.IsPinned)
            .ThenBy(session => WorkspaceController.IsArchived(session.Status))
            .ThenByDescending(session => session.UpdatedAt))
        {
            var project = _workspaceControllerValue.ProjectForSession(session);
            var projectLabel = project is null ? "Chats" : project.Title;
            _managedSessions.Add(ProjectViewModel.ForManagedSession(
                session.ProjectId ?? "",
                session.Id,
                session.Title,
                $"{projectLabel} · {session.Messages.Count} 条 · {FormatTime(session.UpdatedAt)}",
                session.Status,
                session.IsPinned,
                session.IsUnread));
        }
        if (!string.IsNullOrWhiteSpace(selectedManagedSessionId) && _state.Sessions.Any(session => string.Equals(session.Id, selectedManagedSessionId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue = selectedManagedSessionId;
        }
        else if (!string.IsNullOrWhiteSpace(active.Id))
        {
            WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue = active.Id;
        }
        if (!string.IsNullOrWhiteSpace(selectedProjectId) && _state.Projects.Any(project => project.Id == selectedProjectId))
        {
            WorkspaceSidebar.ProjectsList.SelectedValue = selectedProjectId;
        }
        else if (!string.IsNullOrWhiteSpace(active.ProjectId))
        {
            WorkspaceSidebar.ProjectsList.SelectedValue = active.ProjectId;
        }
        else
        {
            WorkspaceSidebar.ProjectsList.SelectedIndex = -1;
        }
        if (!string.IsNullOrWhiteSpace(selectedManagedProjectId) && _state.Projects.Any(project => project.Id == selectedManagedProjectId))
        {
            WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = selectedManagedProjectId;
        }
        RenderManagedProjectSessions(selectedProjectSessionId);

        _contextControllerValue.RenderCollaborationChatSidebarEntry();

        _sessions.Clear();
        foreach (var session in standaloneSessions.Where(_workspaceControllerValue.SessionMatchesCurrentFilter).OrderByDescending(s => s.IsPinned).ThenByDescending(s => s.UpdatedAt))
        {
            _sessions.Add(new SessionViewModel(session.Id, session.Title, $"{session.Messages.Count} 条 · {FormatTime(session.UpdatedAt)}", session.Status, session.IsPinned, session.IsUnread));
        }
        if (_workspaceControllerValue.QuickChatMode)
        {
            WorkspaceSidebar.SessionsList.SelectedIndex = -1;
        }
        else if (string.IsNullOrWhiteSpace(active.ProjectId) && _workspaceControllerValue.SessionMatchesCurrentFilter(active))
        {
            WorkspaceSidebar.SessionsList.SelectedValue = active.Id;
        }

        _tasks.Clear();
        foreach (var task in _state.Tasks.OrderByDescending(t => t.UpdatedAt))
        {
            _tasks.Add(new TaskViewModel(task.Id, task.Title, task.Status, task.Detail ?? ""));
        }

        _quickCommands.Clear();
        foreach (var command in _state.QuickCommands.OrderBy(item => item.Title))
        {
            _quickCommands.Add(new QuickCommandViewModel(command.Id, command.Title, command.Command));
        }
        _workspaceControllerValue.RenderQuickCommandDropdown();

        RenderActiveMessages(active);
        SyncQuickChatLayout(active);

        var pendingInfo = PendingInfo(_state.Pending);
        if (ShouldSuppressConsumedPending(pendingInfo))
        {
            _state.Pending = null;
            pendingInfo = null;
        }
        if (pendingInfo is null)
        {
            ChatWorkspace.PendingText.Text = "";
            ChatWorkspace.PendingText.Visibility = Visibility.Collapsed;
        }
        else
        {
            ChatWorkspace.PendingText.Text = $"待确认：{pendingInfo.Target}.{pendingInfo.Operation}";
            ChatWorkspace.PendingText.Visibility = Visibility.Collapsed;
        }
        ChatWorkspace.ConfirmBar.Visibility = pendingInfo is null ? Visibility.Collapsed : Visibility.Visible;
        ChatWorkspace.ConfirmText.Text = pendingInfo is null ? "--" : $"{pendingInfo.Target}.{pendingInfo.Operation} · {pendingInfo.RiskLevel}";
        ChatWorkspace.ConfirmButton.IsEnabled = pendingInfo is not null;
        ChatWorkspace.CancelButton.IsEnabled = pendingInfo is not null;
        _events.Clear();
        foreach (var ev in _state.Events.TakeLast(40).Reverse())
        {
            _events.Add(new EventViewModel(ev.Type, ev.Time));
        }
        RenderTracePanel();
        WorkbenchShell.ManagementPanels.SyncText.Text = $"修订：{_state.Revision}{Environment.NewLine}更新者：{_state.UpdatedBy}{Environment.NewLine}更新时间：{FormatTime(_state.UpdatedAt)}{Environment.NewLine}接口：{_workspaceControllerValue.DesktopStateUrl()}{Environment.NewLine}实时通道：{WorkspaceSidebar.WsUrlBox.Text.Trim()}";
        SyncDesktopTtsMenu();
        _workbenchControllerValue.RenderWorkbenchStatus(active);
        _rendering = false;
        _workspaceControllerValue.RenderEditors();
        if (!string.Equals(previousActiveSessionId, active.Id, StringComparison.OrdinalIgnoreCase))
        {
            _ = _workspaceControllerValue.SyncAvatarSessionAsync();
        }
        if (!_contextControllerValue.CollaborationChatActive)
        {
            ScrollMessagesToEnd();
        }
    }

    // Session switching stays on the UI thread but only updates the conversation surface.
    // Rebuilding every management collection made a simple row selection feel like a full refresh.
    internal void RenderActiveSessionSwitch()
    {
        _rendering = true;
        try
        {
            var active = _workspaceControllerValue.ActiveSession();
            var activeProject = _workspaceControllerValue.ProjectForSession(active);
            _workspaceControllerValue.RenderWorkspaceIdentity(activeProject);

            ChatWorkspace.ActiveTitleText.Text = "与 Spirit 的对话 · 编辑室视图";
            ChatWorkspace.ActiveTitleText.ToolTip = active.Title;
            ChatWorkspace.ActiveMetaText.Text = $"{active.Messages.Count} 条 · 更新 {FormatTime(active.UpdatedAt)}  Spirit  模型 Deepseek";
            ChatWorkspace.ActiveMetaText.ToolTip = activeProject is null
                ? active.Title
                : $"{activeProject.Title} · {active.Title}";

            var collaborationMembers = (active.CollaborationAgents ?? new List<string>())
                .Where(agent => !string.IsNullOrWhiteSpace(agent))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
            RenderCollaborationMemberChips(collaborationMembers);
            RenderActiveMessages(active);
            SyncQuickChatLayout(active);
            _workbenchControllerValue.RenderWorkbenchStatus(active);
        }
        finally
        {
            _rendering = false;
        }

        if (!_contextControllerValue.CollaborationChatActive)
        {
            ScrollMessagesToEnd();
        }
    }

    // 头部协作参与者 chips：每个成员一个小胶囊 + × 移除。为空时隐藏面板。
    private void RenderCollaborationMemberChips(IReadOnlyList<string> members)
    {
        var panel = ChatWorkspace.CollaborationMembersPanel;
        panel.Children.Clear();
        // 双工开关和轮次余额只在协作场景（会话有参与模型或协作聊天开启）露出，普通会话不占头部空间。
        var collaborationVisible = _contextControllerValue.CollaborationChatActive || members.Count > 0;
        ChatWorkspace.ChatDuplexToggle.Visibility = collaborationVisible ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.ChatDuplexBalanceText.Visibility = collaborationVisible ? Visibility.Visible : Visibility.Collapsed;
        panel.Visibility = collaborationVisible ? Visibility.Visible : Visibility.Collapsed;
        if (!collaborationVisible)
        {
            return;
        }
        var label = new TextBlock
        {
            Text = members.Count == 0 ? "协作实时 · 等待加入成员" : "协作实时 · 参与：",
            FontSize = 12,
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(0, 0, 2, 0),
        };
        // 纯展示：chips 配色走 Fantasy 资源（胶囊底 Hover 蓝白、描边 Line、文字 Text），随主题联动。
        label.SetResourceReference(TextBlock.ForegroundProperty, "FantasyMutedBrush");
        panel.Children.Add(label);
        if (members.Count == 0)
        {
            return;
        }
        foreach (var member in members)
        {
            var agent = member;
            var display = ContextController.CollaborationAgentDisplay(agent);
            var chip = new Border
            {
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(10),
                Padding = new Thickness(8, 2, 4, 2),
                Margin = new Thickness(0, 0, 5, 0),
            };
            chip.SetResourceReference(Border.BackgroundProperty, "FantasyHoverBrush");
            chip.SetResourceReference(Border.BorderBrushProperty, "FantasyLineBrush");
            var row = new StackPanel { Orientation = Orientation.Horizontal };
            var chipText = new TextBlock
            {
                Text = display,
                FontSize = 12,
                VerticalAlignment = VerticalAlignment.Center,
            };
            chipText.SetResourceReference(TextBlock.ForegroundProperty, "FantasyTextBrush");
            row.Children.Add(chipText);
            var removeButton = new Button
            {
                Content = "×",
                Width = 16,
                Height = 16,
                MinWidth = 16,
                MinHeight = 16,
                Padding = new Thickness(0),
                Margin = new Thickness(4, 0, 0, 0),
                FontSize = 12,
                ToolTip = $"将 {display} 移出当前会话",
            };
            // × 按钮 hover 变危险红：样式集中在资源字典 CollaborationChipRemoveButton。
            removeButton.SetResourceReference(FrameworkElement.StyleProperty, "CollaborationChipRemoveButton");
            removeButton.Click += (_, _) => _contextControllerValue.RemoveCollaborationParticipantInteractive(agent);
            row.Children.Add(removeButton);
            chip.Child = row;
            panel.Children.Add(chip);
        }
    }

    internal void RenderActiveMessages(DesktopSession active)
    {
        var ordered = ProjectAtelierTimeline(active.Messages.OrderBy(message => message.CreatedAt).ToList());
        // 揭示动画只给"追加到已渲染对话"的新气泡：启动加载/会话切换时旧新 Id 无交集，不动画。
        var hadExistingItems = _messages.Count > 0;
        var previousIds = new HashSet<string>(_messages.Select(item => item.Id ?? ""), StringComparer.OrdinalIgnoreCase);
        var sameConversation = hadExistingItems && ordered.Any(message => previousIds.Contains(message.Id ?? ""));

        // 按 Id 最小差量同步（2026-07-09 结构性修复）：协作回复锚到自己思考卡正下方属于
        // "中间插入"，旧实现只认前缀匹配，一失配就整批 Clear+Add——销毁全部气泡/步骤 VM，
        // 打字机状态清零重来，表现为 step 一块块、流式气泡卡顿、拖选文本被吃。
        // 现在：先删消失项 → 逐位对齐（错位 Move、缺失 Insert）→ 原地更新，
        // VM 实例全程复用，任意中间插入/重排都不再触发整批重建。
        var wantedIds = new HashSet<string>(ordered.Select(message => message.Id ?? ""), StringComparer.OrdinalIgnoreCase);
        for (var index = _messages.Count - 1; index >= 0; index--)
        {
            if (!wantedIds.Contains(_messages[index].Id ?? ""))
            {
                _messages.RemoveAt(index);
            }
        }
        for (var index = 0; index < ordered.Count; index++)
        {
            var message = ordered[index];
            var isEditing = string.Equals(message.Id, _editingMessageId, StringComparison.OrdinalIgnoreCase);
            // 该 Id 现在的位置只可能在 index 或其后（前面已对齐）。
            var currentIndex = -1;
            for (var scan = index; scan < _messages.Count; scan++)
            {
                if (string.Equals(_messages[scan].Id, message.Id, StringComparison.OrdinalIgnoreCase))
                {
                    currentIndex = scan;
                    break;
                }
            }
            if (currentIndex < 0)
            {
                var item = CreateTimelineItem(message, isEditing);
                // 本轮新出现的助手气泡走打字机揭示（含中间插入的定稿回复），历史补载不动画。
                if (sameConversation
                    && !previousIds.Contains(message.Id ?? "")
                    && item is MessageViewModel bubble
                    && ShouldRevealOnArrival(message))
                {
                    bubble.RevealFromEmpty();
                }
                _messages.Insert(index, item);
                continue;
            }
            if (currentIndex != index)
            {
                _messages.Move(currentIndex, index);
            }
            // 同 Id 类型漂移（work↔message，理论上不该发生）：原位重建该项兜底。
            if (IsWorkMessage(message) != _messages[index] is WorkChainViewModel)
            {
                _messages[index] = CreateTimelineItem(message, isEditing);
                continue;
            }
            UpdateTimelineItem(_messages[index], message, isEditing);
        }
    }

    internal static List<DesktopMessage> ProjectAtelierTimeline(List<DesktopMessage> chronological)
    {
        var projected = new List<DesktopMessage>(chronological.Count);
        var index = 0;
        while (index < chronological.Count)
        {
            var current = chronological[index];
            if (!string.Equals((current.Role ?? "").Trim(), "user", StringComparison.OrdinalIgnoreCase))
            {
                projected.Add(current);
                index++;
                continue;
            }

            projected.Add(current);
            var turnEnd = index + 1;
            while (turnEnd < chronological.Count
                && !string.Equals((chronological[turnEnd].Role ?? "").Trim(), "user", StringComparison.OrdinalIgnoreCase))
            {
                turnEnd++;
            }

            var visibleTurnItems = chronological
                .Skip(index + 1)
                .Take(turnEnd - index - 1)
                .Where(candidate => !IsConfirmationPromptMessage(candidate))
                .ToList();
            var replyCount = visibleTurnItems.Count(candidate =>
                !IsWorkMessage(candidate)
                && string.Equals((candidate.Role ?? "").Trim(), "assistant", StringComparison.OrdinalIgnoreCase));
            if (replyCount > 1)
            {
                // Collaboration anchors every model work card immediately before
                // its own reply. Preserve those pairs instead of collapsing the
                // whole user turn to the final model response.
                projected.AddRange(visibleTurnItems);
                index = turnEnd;
                continue;
            }

            DesktopMessage? latestReply = null;
            var workItems = new List<DesktopMessage>();
            foreach (var candidate in visibleTurnItems)
            {
                if (IsWorkMessage(candidate))
                {
                    workItems.Add(candidate);
                }
                else if (string.Equals((candidate.Role ?? "").Trim(), "assistant", StringComparison.OrdinalIgnoreCase))
                {
                    latestReply = candidate;
                }
                else
                {
                    projected.Add(candidate);
                }
            }
            foreach (var workItem in workItems)
            {
                projected.Add(workItem);
            }
            if (latestReply is not null)
            {
                projected.Add(latestReply);
            }
            index = turnEnd;
        }
        return projected;
    }

    private static bool IsConfirmationPromptMessage(DesktopMessage message)
    {
        var text = (message.Text ?? "").Trim();
        return text.StartsWith("已取消 ", StringComparison.Ordinal)
            || (text.Contains("为安全起见", StringComparison.Ordinal)
                && text.Contains("确认执行", StringComparison.Ordinal)
                && text.Contains("取消执行", StringComparison.Ordinal));
    }

    // 新到气泡是否走打字机揭示：只对助手回复类气泡（用户/系统/命令/变更不揭示），
    // 且消息足够新鲜（3 分钟内）——防止后台同步补历史时对旧消息做动画。
    private static bool ShouldRevealOnArrival(DesktopMessage message)
    {
        var role = (message.Role ?? "").Trim().ToLowerInvariant();
        if (role is "user" or "system")
        {
            return false;
        }
        var kind = (message.Kind ?? "").Trim().ToLowerInvariant();
        if (kind is "changes" or "command")
        {
            return false;
        }
        if (string.IsNullOrWhiteSpace(message.Text))
        {
            return false;
        }
        var nowSeconds = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        return message.CreatedAt > nowSeconds - 180;
    }

    private static ChatTimelineItemViewModel CreateTimelineItem(DesktopMessage message, bool isEditing)
    {
        return IsWorkMessage(message)
            ? WorkChainViewModel.FromMessage(message)
            : MessageViewModel.FromMessage(message, isEditing);
    }

    private static void UpdateTimelineItem(ChatTimelineItemViewModel item, DesktopMessage message, bool isEditing)
    {
        if (IsWorkMessage(message) && item is WorkChainViewModel workChain)
        {
            workChain.UpdateFromMessage(message);
            return;
        }
        if (!IsWorkMessage(message) && item is MessageViewModel messageView)
        {
            messageView.UpdateFromMessage(message, isEditing);
            return;
        }
        throw new InvalidOperationException("Timeline item type changed without a timeline rebuild.");
    }

    private static bool IsWorkMessage(DesktopMessage message)
    {
        return string.Equals((message.Kind ?? "").Trim(), "work", StringComparison.OrdinalIgnoreCase);
    }

    internal void RenderTracePanel()
    {
        _traceEvents.Clear();
        var run = _workflowControllerValue.FindWorkflowRun(_workflowControllerValue.ActiveWorkflowRunIdValue);
        if (run.ValueKind == JsonValueKind.Object && TryReadJsonObject(run, "trace_replay", out var replay))
        {
            var runId = ReadJsonString(run, "run_id", "--");
            var workflowName = ReadJsonString(run, "workflow_name", "--");
            var status = ReadJsonString(run, "status", "--");
            var stepCount = ReadJsonInt(replay, "step_count");
            var eventCount = ReadJsonInt(replay, "event_count");
            WorkbenchShell.ManagementPanels.ExecutionText.Text = $"{workflowName} · {runId}{Environment.NewLine}状态：{WorkflowDisplayText.StatusLabel(status)} · 回放步骤 {stepCount} · 事件 {eventCount}{Environment.NewLine}更新时间：{ReadJsonString(run, "updated_at", "--")}";

            var routeLines = new List<string>();
            if (TryReadJsonArray(replay, "timeline"))
            {
                var timeline = replay.GetProperty("timeline");
                const int maxVisibleTraceSteps = 400;
                var totalSteps = timeline.GetArrayLength();
                var skippedSteps = Math.Max(0, totalSteps - maxVisibleTraceSteps);
                if (skippedSteps > 0)
                {
                    _traceEvents.Add(new EventViewModel("轨迹回放", $"仅显示最近 {maxVisibleTraceSteps} / {totalSteps} 个步骤；完整回放仍保留在运行快照中。"));
                }
                var stepOffset = 0;
                foreach (var step in timeline.EnumerateArray())
                {
                    if (stepOffset++ < skippedSteps)
                    {
                        continue;
                    }
                    var type = ReadJsonString(step, "type");
                    var nodeId = ReadJsonString(step, "node_id");
                    var summary = ReadJsonString(step, "summary", type);
                    if (type == "branch_selected" && routeLines.Count < 80)
                    {
                        var route = step.TryGetProperty("payload", out var payload) ? ReadJsonString(payload, "selected_route", "--") : "--";
                        routeLines.Add($"{nodeId}: {route}");
                    }
                    if (step.TryGetProperty("payload", out var stepPayload)
                        && TryReadJsonObject(stepPayload, "interaction_envelope", out var envelope)
                        && !string.IsNullOrWhiteSpace(ReadJsonString(envelope, "audit_event_id"))
                        && routeLines.Count < 80)
                    {
                        routeLines.Add($"{nodeId}: audit {ReadJsonString(envelope, "audit_event_id")}");
                    }
                    _traceEvents.Add(new EventViewModel($"{ReadJsonInt(step, "step_index")}. {type}", $"{ReadJsonString(step, "at", "--")} · {nodeId} · {summary}"));
                }
            }
            WorkbenchShell.ManagementPanels.RouteText.Text = routeLines.Count == 0 ? "当前回放没有分支或交互包路由信息。" : string.Join(Environment.NewLine, routeLines);
            if (_traceEvents.Count == 0)
            {
                _traceEvents.Add(new EventViewModel("workflow replay", "当前运行没有可回放事件。"));
            }
            return;
        }

        WorkbenchShell.ManagementPanels.ExecutionText.Text = DictText(_state.LastExecution);
        WorkbenchShell.ManagementPanels.RouteText.Text = DictText(_state.LastRoute);
        foreach (var ev in _events)
        {
            _traceEvents.Add(ev);
        }
        if (_traceEvents.Count == 0)
        {
            _traceEvents.Add(new EventViewModel("events", "暂无过程事件。"));
        }
    }

    private void RenderManagedProjectSessions(string? selectedSessionId = null)
    {
        _managedProjectSessions.Clear();
        var project = _workspaceControllerValue.SelectedProject();
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ProjectSessionStatusText.Text = "选择项目后可管理项目内会话";
            return;
        }

        var sessions = _state.Sessions
            .Where(session => string.Equals(session.ProjectId, project.Id, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(session => session.IsPinned)
            .ThenBy(session => WorkspaceController.IsArchived(session.Status))
            .ThenByDescending(session => session.UpdatedAt)
            .ToList();
        foreach (var session in sessions)
        {
            _managedProjectSessions.Add(ProjectViewModel.ForProjectSession(project.Id, session.Id, session.Title, $"{session.Messages.Count} 条 · {FormatTime(session.UpdatedAt)}", session.Status, session.IsPinned, session.IsUnread));
        }

        var activeCount = sessions.Count(session => !WorkspaceController.IsArchived(session.Status));
        var archivedCount = sessions.Count(session => WorkspaceController.IsArchived(session.Status));
        WorkbenchShell.ManagementPanels.ProjectSessionStatusText.Text = $"{project.Title} · 活动 {activeCount} · 归档 {archivedCount}";
        if (!string.IsNullOrWhiteSpace(selectedSessionId) && sessions.Any(session => string.Equals(session.Id, selectedSessionId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.ProjectSessionsList.SelectedValue = selectedSessionId;
        }
        else if (_managedProjectSessions.Count > 0)
        {
            WorkbenchShell.ManagementPanels.ProjectSessionsList.SelectedValue = _managedProjectSessions[0].SessionId;
        }
        _navigationControllerValue.SyncProjectSessionButtons();
    }

    internal void SyncQuickChatLayout(DesktopSession active)
    {
        // Composer modes change routing, not navigation. A quick-chat draft stays
        // on the draft surface until the first message is actually committed.
        var showQuickChat = ShouldShowQuickChat(
            _workspaceControllerValue.QuickChatMode,
            _contextControllerValue.CollaborationChatActive);
        ChatWorkspace.QuickChatEmptyPanel.Visibility = showQuickChat ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.MessagesList.Visibility = showQuickChat ? Visibility.Collapsed : Visibility.Visible;
        var showArtifactStrip = !showQuickChat && _messages.Count > 0;
        ChatWorkspace.ConversationArtifactsPanel.Visibility = showArtifactStrip ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.ChatArtifactsRow.Height = showArtifactStrip ? GridLength.Auto : new GridLength(0);
        ChatWorkspace.ChatInputSplitter.Visibility = showQuickChat ? Visibility.Collapsed : Visibility.Visible;
        ChatWorkspace.ChatComposerPanel.Visibility = showQuickChat ? Visibility.Collapsed : Visibility.Visible;
        // The title bar owns global safety controls. It must remain available even
        // when the active quick-chat session has no messages yet.
        ChatWorkspace.ChatHeaderBar.Visibility = Visibility.Visible;
        ChatWorkspace.ChatHeaderRow.Height = GridLength.Auto;
        ChatWorkspace.ChatHeaderRow.MinHeight = 66;
        ChatWorkspace.ChatSplitterRow.Height = showQuickChat ? new GridLength(0) : new GridLength(1);
        // Plan/Pursue Goal and attachments add an Auto status row. Keeping the
        // whole composer at a fixed 155 px clips that row into the toolbar.
        ChatWorkspace.ChatComposerRow.Height = showQuickChat ? new GridLength(0) : GridLength.Auto;
        _contextControllerValue.RenderCollaborationComposerState();
        _composerControllerValue.RenderComposerModeButtonStates();
        _composerControllerValue.RenderComposerSelectorText(active);
    }

    internal static bool ShouldShowQuickChat(bool quickChatMode, bool collaborationChatActive)
    {
        _ = collaborationChatActive;
        return quickChatMode;
    }

    private static void EnsureComboText(ComboBox combo, string fallback)
    {
        if (string.IsNullOrWhiteSpace(ComboText(combo)))
        {
            SetComboText(combo, fallback);
        }
    }

    private void EnsureActiveSessionMatchesFilter()
    {
        var active = _state.Sessions.FirstOrDefault(session => string.Equals(session.Id, _state.ActiveSessionId, StringComparison.OrdinalIgnoreCase));
        if (active is not null && _workspaceControllerValue.SessionMatchesCurrentFilter(active))
        {
            return;
        }

        var fallback = _state.Sessions
            .Where(session => string.IsNullOrWhiteSpace(session.ProjectId))
            .Where(_workspaceControllerValue.SessionMatchesCurrentFilter)
            .OrderByDescending(session => session.IsPinned)
            .ThenByDescending(session => session.UpdatedAt)
            .FirstOrDefault();
        if (fallback is not null)
        {
            _state.ActiveSessionId = fallback.Id;
        }
    }

}
