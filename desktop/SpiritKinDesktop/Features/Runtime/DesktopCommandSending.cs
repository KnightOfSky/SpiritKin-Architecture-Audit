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
    internal bool CommandSendInProgress => _commandSendCts is { IsCancellationRequested: false };

    internal async Task SendOrStopCommandAsync(string? forcedText = null)
    {
        if (CommandSendInProgress)
        {
            await StopActiveCommandAsync();
            return;
        }

        await SendCommandAsync(forcedText);
    }

    internal async Task StopActiveCommandAsync()
    {
        var requestCts = _commandSendCts;
        if (requestCts is null || requestCts.IsCancellationRequested)
        {
            return;
        }

        var cancelledRequestId = _latestCommandRequestId;
        var sessionId = SessionForCommandRequest(cancelledRequestId)?.Id ?? _workspaceControllerValue.ActiveSession().Id;
        var cancellationRequestId = NewCommandRequestId();
        _commandSendCts = null;
        _latestCommandRequestId = cancellationRequestId;
        TrackCommandRequestSession(cancellationRequestId, sessionId);
        requestCts.Cancel();
        StopDesktopTtsPlayback();
        SetCommandSendState(false);
        _composerControllerValue.CancelAssistantWork();
        WorkspaceSidebar.ConnectionStatusText.Text = "正在停止生成...";

        try
        {
            await RegisterCommandCancellationAsync(cancellationRequestId, cancelledRequestId, sessionId);
            WorkspaceSidebar.ConnectionStatusText.Text = "已停止生成";
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "已停止当前显示；后端中断通知未送达";
        }

        await SaveStateAsync();
    }

    private async Task RegisterCommandCancellationAsync(string requestId, string cancelledRequestId, string sessionId)
    {
        var metadata = _composerControllerValue.BuildComposerCommandMetadata();
        metadata["request_id"] = requestId;
        metadata["session_id"] = sessionId;
        metadata["cancelled_request_id"] = cancelledRequestId;
        metadata["control_action"] = "cancel_generation";
        metadata["interrupt_mode"] = "latest_wins";
        metadata["supersedes_previous"] = true;
        var payload = new
        {
            text = "停止当前生成",
            channel = "desktop",
            metadata,
        };
        using var request = new HttpRequestMessage(HttpMethod.Post, _workspaceControllerValue.CommandUrl());
        _workspaceControllerValue.ApplyAuth(request);
        request.Content = new StringContent(JsonSerializer.Serialize(payload, _jsonOptions), Encoding.UTF8, "application/json");
        using var timeoutCts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        using var response = await _http.SendAsync(request, timeoutCts.Token);
        response.EnsureSuccessStatusCode();
    }

    private void SetCommandSendState(bool isSending)
    {
        var label = isSending ? "停止" : "发送";
        var toolTip = isSending ? "停止当前生成" : "发送";
        foreach (var button in new[] { ChatWorkspace.SendButton, ChatWorkspace.EmptySendButton })
        {
            button.Content = label;
            button.ToolTip = toolTip;
            button.IsEnabled = true;
            System.Windows.Automation.AutomationProperties.SetName(button, label);
        }
    }

    internal async Task SendCommandAsync(string? forcedText = null, bool steerConversation = false)
    {
        var text = (forcedText ?? ChatWorkspace.PromptBox.Text).Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        if (_contextControllerValue.CollaborationChatActive
            && IsConfirmationControlText(text)
            && await _contextControllerValue.TryHandlePendingCollaborationToolCallAsync(text))
        {
            ChatWorkspace.PromptBox.Clear();
            ChatWorkspace.EmptyPromptBox.Clear();
            return;
        }
        // 协作模式开启时全部消息走协作路由（未 @ 时广播给全部参与者）；未开启时仅 @ 消息触发引导开启。
        if (_contextControllerValue.CollaborationChatActive || _contextControllerValue.IsCollaborationAddressedMessage(text))
        {
            if (!EnsureCollaborationMentionCanSend(text))
            {
                return;
            }
            if (!IsConfirmationControlText(text)
                && _composerControllerValue.ComposerPermissionMode() == "full_access"
                && !_composerControllerValue.EnsureFullAccessGranted())
            {
                return;
            }
            _workspaceControllerValue.CommitQuickChatDraft();
            _composerControllerValue.EnableCollaborationRoutingForAddressedMessage();
            AddMessage("user", text);
            _workspaceControllerValue.SetQuickChatMode(false);
            ChatWorkspace.PromptBox.Clear();
            ChatWorkspace.EmptyPromptBox.Clear();
            // 先投递再持久化。整份桌面状态保存可能排在其它 UI 保存之后，不能让
            // 首条 @/模型名消息因此延迟到用户第二次发送才真正进入协作路由。
            await _contextControllerValue.SendCollaborationMessageFromComposerAsync(text);
            await SaveStateAsync();
            return;
        }
        var confirmationControl = IsConfirmationControlText(text);
        if (!confirmationControl && _composerControllerValue.ComposerPermissionMode() == "full_access" && !_composerControllerValue.EnsureFullAccessGranted())
        {
            return;
        }
        _workspaceControllerValue.CommitQuickChatDraft();
        _composerControllerValue.EnsureActiveSessionProject();
        AddMessage("user", text);
        _workspaceControllerValue.SetQuickChatMode(false);
        if (_composerControllerValue.PursueGoalEnabled && string.IsNullOrWhiteSpace(_composerControllerValue.PursueGoalText))
        {
            _composerControllerValue.SetPursueGoalText(text);
            _composerControllerValue.RenderComposerAttachmentStatus();
        }
        ChatWorkspace.PromptBox.Clear();
        ChatWorkspace.EmptyPromptBox.Clear();
        // 修M（批次十返工）：普通聊天同样占用本地模型。中断询问此前只挂在协作发送路径，
        // 新会话发普通消息时旧会话双工串联还在滚动却不弹询问（2026-07-09 实测）。
        // 确认控制词（是/确认等）是对进行中命令的应答，不该被模态框打断。
        if (!confirmationControl)
        {
            await _contextControllerValue.MaybePauseOtherCollaborationThreadsAsync(_contextControllerValue.CurrentSessionCollaborationThreadId());
        }
        await SendCommandCoreAsync(text, steerConversation, supersedePrevious: !confirmationControl, confirmationControl: confirmationControl);
    }

    internal async Task ResendEditedMessageAsync(string messageId, string editedText)
    {
        var text = editedText.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        if (_contextControllerValue.CollaborationChatActive || _contextControllerValue.IsCollaborationAddressedMessage(text))
        {
            if (!EnsureCollaborationMentionCanSend(text, restoreToComposer: true))
            {
                return;
            }
            if (_composerControllerValue.ComposerPermissionMode() == "full_access"
                && !_composerControllerValue.EnsureFullAccessGranted())
            {
                return;
            }
            _composerControllerValue.EnableCollaborationRoutingForAddressedMessage();
            // 协作路径重发也要退出编辑态，否则气泡停留在编辑框上。
            _editingMessageId = "";
            AddMessage("user", text);
            _workspaceControllerValue.SetQuickChatMode(false);
            ChatWorkspace.PromptBox.Clear();
            ChatWorkspace.EmptyPromptBox.Clear();
            // 同主协作分支：路由优先，状态持久化不能阻塞首次投递。
            await _contextControllerValue.SendCollaborationMessageFromComposerAsync(text);
            await SaveStateAsync();
            return;
        }
        if (_composerControllerValue.ComposerPermissionMode() == "full_access" && !_composerControllerValue.EnsureFullAccessGranted())
        {
            return;
        }
        var session = _workspaceControllerValue.ActiveSession();
        var ordered = session.Messages.OrderBy(message => message.CreatedAt).ToList();
        var index = ordered.FindIndex(message => string.Equals(message.Id, messageId, StringComparison.OrdinalIgnoreCase));
        if (index < 0)
        {
            return;
        }
        var message = ordered[index];
        if (!message.Role.Equals("user", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        var now = NowSeconds();
        var retained = new List<DesktopMessage>();
        var removed = new List<string>();
        for (var i = 0; i < ordered.Count; i++)
        {
            var item = ordered[i];
            var removeGeneratedForEditedTurn = i < index
                && IsGeneratedTurnMessage(item)
                && item.CreatedAt >= message.CreatedAt;
            if (i > index || removeGeneratedForEditedTurn)
            {
                if (!string.IsNullOrWhiteSpace(item.Id))
                {
                    removed.Add(item.Id);
                }
                continue;
            }
            retained.Add(item);
        }
        TrackDeletedMessages(session.Id, removed);
        message.Text = text;
        message.UpdatedAt = now;
        session.Messages = retained;
        session.UpdatedAt = now;
        _editingMessageId = "";
        _workspaceControllerValue.SetQuickChatMode(false);
        RenderState();
        await SendCommandCoreAsync(text, steerConversation: false, supersedePrevious: true);
    }

    private bool EnsureCollaborationMentionCanSend(string text, bool restoreToComposer = false)
    {
        if (_contextControllerValue.CollaborationChatActive)
        {
            _composerControllerValue.BindCurrentSessionCollaborationThread();
            return true;
        }

        if (restoreToComposer)
        {
            _editingMessageId = "";
            RenderState();
            ChatWorkspace.PromptBox.Text = text;
            ChatWorkspace.PromptBox.CaretIndex = ChatWorkspace.PromptBox.Text.Length;
        }

        const string message = "请先开启模型协作";
        ChatWorkspace.SendStatusText.Text = message;
        ChatWorkspace.SendStatusText.Visibility = Visibility.Visible;
        WorkspaceSidebar.ConnectionStatusText.Text = "请先开启模型协作后再发送 @ Agent 消息。";
        WorkspaceSidebar.ConnectionStatusText.Visibility = Visibility.Visible;
        _composerControllerValue.RenderComposerModeButtonStates();
        if (!string.IsNullOrWhiteSpace(ChatWorkspace.PromptBox.Text))
        {
            ChatWorkspace.PromptBox.Focus();
            ChatWorkspace.PromptBox.CaretIndex = ChatWorkspace.PromptBox.Text.Length;
        }
        else
        {
            ChatWorkspace.EmptyPromptBox.Focus();
            ChatWorkspace.EmptyPromptBox.CaretIndex = ChatWorkspace.EmptyPromptBox.Text.Length;
        }
        return false;
    }

    private async Task SendCommandCoreAsync(string text, bool steerConversation, bool supersedePrevious = false)
    {
        await SendCommandCoreAsync(text, steerConversation, supersedePrevious, confirmationControl: false);
    }

    internal Task SendConfirmationControlAsync(string text)
    {
        return SendCommandCoreAsync(text, steerConversation: false, supersedePrevious: false, confirmationControl: true);
    }

    internal static bool IsConfirmationControlText(string text)
    {
        var normalized = Regex.Replace((text ?? "").Trim().ToLowerInvariant(), @"[\s,.;!?，。；！？、]+", "");
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return false;
        }
        if (normalized.Contains("确认执行", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("可以执行", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("继续执行", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("取消执行", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("不要执行", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("停止执行", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("中止执行", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        var exact = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "确认",
            "确认执行",
            "可以执行",
            "执行吧",
            "继续执行",
            "取消",
            "取消执行",
            "不要执行",
            "别执行",
            "停止执行",
            "中止执行",
        };
        return exact.Contains(normalized)
            || (normalized.Length <= 8
                && (normalized.Contains("确认", StringComparison.OrdinalIgnoreCase)
                    || normalized.Contains("取消", StringComparison.OrdinalIgnoreCase)
                    || normalized.Contains("别执行", StringComparison.OrdinalIgnoreCase)
                    || normalized.Contains("不要执行", StringComparison.OrdinalIgnoreCase)));
    }

    private async Task SendCommandCoreAsync(string text, bool steerConversation, bool supersedePrevious, bool confirmationControl)
    {
        var confirmationPendingInfo = confirmationControl ? PendingInfo(_state.Pending) : null;
        var requestId = NewCommandRequestId();
        // 记住发起会话：回复（HTTP 兜底/WS 事件/失败提示）到达时写回它，而非"当时激活的"会话。
        TrackCommandRequestSession(requestId, _workspaceControllerValue.ActiveSession().Id);
        CancellationTokenSource requestCts;
        if (supersedePrevious)
        {
            requestCts = ReplaceActiveCommandRequest(requestId);
            SetCommandSendState(true);
        }
        else
        {
            requestCts = new CancellationTokenSource();
            _latestCommandRequestId = requestId;
        }
        if (confirmationControl)
        {
            MarkPendingConfirmationConsumed(confirmationPendingInfo, requestId);
            _state.Pending = null;
            ChatWorkspace.ConfirmBar.Visibility = Visibility.Collapsed;
            ChatWorkspace.ConfirmButton.IsEnabled = false;
            ChatWorkspace.CancelButton.IsEnabled = false;
            RenderState();
            await SaveStateAsync();
        }
        else
        {
            ClearConsumedPendingConfirmation();
        }
        _ = _workspaceControllerValue.SyncAvatarSessionAsync(requestId);
        _composerControllerValue.StartAssistantWork(text, steerConversation ? text : "");
        RenderState();
        if (!confirmationControl)
        {
            _ = SaveStateAsync();
        }
        ChatWorkspace.SendStatusText.Visibility = Visibility.Collapsed;
        WorkspaceSidebar.ConnectionStatusText.Text = supersedePrevious ? (steerConversation ? "正在重新引导..." : "正在重新生成...") : (steerConversation ? "正在引导..." : "正在发送...");

        try
        {
            var metadata = _composerControllerValue.BuildComposerCommandMetadata(steerConversation);
            metadata["request_id"] = requestId;
            metadata["supersedes_previous"] = supersedePrevious;
            if (confirmationControl)
            {
                metadata["confirmation_control"] = true;
                metadata["pending_target"] = confirmationPendingInfo?.Target ?? "";
                metadata["pending_operation"] = confirmationPendingInfo?.Operation ?? "";
                metadata["confirmation_choice"] = text;
            }
            else
            {
                metadata["interrupt_mode"] = "latest_wins";
            }
            var attachments = _composerControllerValue.BuildPendingAttachmentPayload();
            var documents = _composerControllerValue.BuildPendingDocumentPayload();
            var payload = new
            {
                text,
                channel = "desktop",
                metadata,
                attachments = attachments.Count > 0 ? attachments : null,
                documents = documents.Count > 0 ? documents : null,
            };
            using var request = new HttpRequestMessage(HttpMethod.Post, _workspaceControllerValue.CommandUrl());
            _workspaceControllerValue.ApplyAuth(request);
            request.Content = new StringContent(JsonSerializer.Serialize(payload, _jsonOptions), Encoding.UTF8, "application/json");
            using var response = await _http.SendAsync(request, requestCts.Token);
            var body = await response.Content.ReadAsStringAsync(requestCts.Token);
            if (IsStaleCommandRequest(requestId))
            {
                return;
            }
            if ((int)response.StatusCode == 204 || (int)response.StatusCode == 409)
            {
                _composerControllerValue.CompleteAssistantWork();
                return;
            }
            response.EnsureSuccessStatusCode();
            var envelope = JsonSerializer.Deserialize<CommandEnvelope>(body, _jsonOptions);
            if (IsStaleCommandRequest(requestId) || IsStaleCommandEnvelope(envelope, requestId))
            {
                return;
            }
            if (envelope?.CollaborationRedirect == true)
            {
                HandleCommandCollaborationRedirect(envelope);
                ChatWorkspace.SendStatusText.Visibility = Visibility.Collapsed;
                WorkspaceSidebar.ConnectionStatusText.Text = "";
                _composerControllerValue.ClearComposerAttachments();
                _composerControllerValue.CompleteAssistantWork();
                await SaveStateAsync();
                return;
            }
            var speechText = DesktopTtsEnabled() ? ResolveDesktopSpeechText(envelope) : "";
            if (!steerConversation && !string.IsNullOrWhiteSpace(speechText))
            {
                SpeakDesktopReply(speechText);
            }
            if (envelope?.Events is not null)
            {
                var events = envelope.Events
                    .Where(ev => !IsStaleCommandRequest(requestId) && !IsStaleEvent(ev, requestId))
                    .ToList();
                // 连接态止血：WS 实时连接存活时，思考/路由步骤已由 assistant.work_updated 逐条投影，
                // 此处 HTTP 兜底应跳过以免同一逻辑步骤重复（后端 work_updated.detail 暂为空，无法靠结构化 key 跨路去重）。
                // WS 断线时才用 HTTP 兜底。这是连接态止血，非最终语义；最终去重回到结构化 trace key。
                if (!_workspaceControllerValue.WsConnected)
                {
                    _composerControllerValue.AppendAssistantRuntimeWorkSteps(events);
                }
                await _workspaceControllerValue.PostAvatarRuntimeEventsAsync(events, requestId);
                foreach (var ev in events)
                {
                    ApplyEvent(ev, projectWork: !_workspaceControllerValue.WsConnected);
                }
            }
            if (envelope?.Events is null && envelope?.Reply?.Text is { Length: > 0 } replyText)
            {
                AddMessage("assistant", replyText, targetSession: SessionForCommandRequest(requestId));
            }
            ApplyReplyPendingFallback(envelope, confirmationControl);
            if (confirmationControl)
            {
                _confirmationChoiceInFlight = false;
                _state.Pending = null;
                RenderState();
            }
            await _workbenchControllerValue.RefreshGitChangesAsync();
            ChatWorkspace.SendStatusText.Visibility = Visibility.Collapsed;
            WorkspaceSidebar.ConnectionStatusText.Text = "";
            _composerControllerValue.ClearComposerAttachments();
            _composerControllerValue.CompleteAssistantWork();
            await SaveStateAsync();
        }
        catch (OperationCanceledException) when (IsStaleCommandRequest(requestId))
        {
            // A newer user message replaced this request; suppress stale failure UI.
        }
        catch (Exception ex)
        {
            if (IsStaleCommandRequest(requestId))
            {
                return;
            }
            _composerControllerValue.AppendAssistantWorkStep("工作指令", $"执行停止：{ComposerController.TrimStatusText(ex.Message, 220)}");
            _composerControllerValue.CompleteAssistantWork();
            AddMessage("system", $"发送失败：{ex.Message}", targetSession: SessionForCommandRequest(requestId));
            ChatWorkspace.SendStatusText.Visibility = Visibility.Collapsed;
            WorkspaceSidebar.ConnectionStatusText.Text = confirmationControl ? "确认请求已发送，后端仍在执行或未及时返回。" : "发送失败";
            if (confirmationControl)
            {
                _state.Pending = null;
            }
            RenderState();
            await SaveStateAsync();
        }
        finally
        {
            if (confirmationControl)
            {
                ChatWorkspace.ConfirmButton.IsEnabled = _state.Pending is not null;
                ChatWorkspace.CancelButton.IsEnabled = _state.Pending is not null;
            }
            if (ReferenceEquals(_commandSendCts, requestCts))
            {
                _commandSendCts = null;
                SetCommandSendState(false);
            }
            requestCts.Dispose();
        }
    }

    private void HandleCommandCollaborationRedirect(CommandEnvelope envelope)
    {
        _composerControllerValue.EnableCollaborationRoutingForAddressedMessage();
        if (envelope.Events is not null)
        {
            foreach (var ev in envelope.Events)
            {
                ApplyEvent(ev, projectWork: false);
            }
        }

        var agents = envelope.Message?.ToAgents?
            .Where(agent => !string.IsNullOrWhiteSpace(agent))
            .ToArray()
            ?? Array.Empty<string>();
        var threadId = envelope.Message?.ThreadId
            ?? envelope.Message?.TaskId
            ?? _contextControllerValue.CurrentSessionCollaborationThreadId();
        _contextControllerValue.EnsureCollaborationWorkersForAgents(agents, threadId);
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = agents.Length == 0
            ? "已切换到模型协作。"
            : $"已切换到模型协作并发送到：{string.Join(", ", agents)}{_contextControllerValue.AutoWorkerStatusSuffix(agents)}";
        RenderState();
    }

}
