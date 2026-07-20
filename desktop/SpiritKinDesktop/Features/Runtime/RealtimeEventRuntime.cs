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
    internal void ApplyEvent(RuntimeEvent ev, bool replay = false, bool projectWork = true)
    {
        if (ev.Type == RealtimeContract.Events.RuntimeSnapshot)
        {
            if (ev.Payload.ValueKind == JsonValueKind.Object && ev.Payload.TryGetProperty("events", out var events) && events.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in events.EnumerateArray())
                {
                    var snapshotEvent = item.Deserialize<RuntimeEvent>(_jsonOptions);
                    if (snapshotEvent is not null)
                    {
                        ApplyEvent(snapshotEvent, replay: true);
                    }
                }
            }
            return;
        }
        if (ev.Type == RealtimeContract.Events.DesktopStateUpdated)
        {
            // 自己保存成功后网关会广播 state_updated，又回到自己手里；此时全量重载
            // 会把"保存之后又产生的本地改动"（正在流式的草稿、刚 AddMessage 的用户消息）
            // 整体冲掉，表现为消息被吞、气泡闪、列表跳动。自己的回显直接跳过——
            // 保存回包本身已带最新状态。仅其他端（移动/网页）的更新才需要拉取。
            var updatedBy = ev.Payload.ValueKind == JsonValueKind.Object
                ? ReadJsonString(ev.Payload, "updated_by")
                : "";
            if (string.Equals(updatedBy, "spiritkin_wpf_desktop", StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            // 本地还有保存在途/排队时也不拉：拉回的必然是旧于本地的状态。
            if (System.Threading.Interlocked.CompareExchange(ref _pendingSaveRequests, 0, 0) > 0)
            {
                return;
            }
            _ = Dispatcher.InvokeAsync(async () => await LoadStateAsync());
            return;
        }
        if (ev.Type == RealtimeContract.Events.DesktopCollaborationUpdated
            || ev.Type == RealtimeContract.Events.CollaborationMessage)
        {
            _ = Dispatcher.InvokeAsync(async () => await _contextControllerValue.LoadCollaborationAsync());
            return;
        }

        var payload = ev.Payload;
        var eventText = payload.ValueKind == JsonValueKind.Object && payload.TryGetProperty("text", out var textElement) ? textElement.GetString() : "";
        if (!replay && IsStaleEvent(ev))
        {
            return;
        }
        if (ev.Type == RealtimeContract.Events.AssistantDelta)
        {
            if (!replay)
            {
                UpsertAssistantStreamDraft(ev);
                RecordEvent(ev, payload, replay);
            }
            return;
        }
        // 协作工作链独立投影：不落 DesktopState，仅存 ContextController 内存，故需在 replay（断线重连）
        // 时同样重建；event_id 幂等去重保证重复无副作用。不受主聊天 WS-vs-HTTP 连接态兜底门控影响。
        if (payload.ValueKind == JsonValueKind.Object && IsCollaborationWorkEvent(payload))
        {
            _contextControllerValue.AppendCollaborationWorkEvent(ev);
            RecordEvent(ev, payload, replay);
            return;
        }
        if (IsVoiceCallUiEvent(ev.Type))
        {
            if (!replay)
            {
                EventApplied?.Invoke(ev);
            }
            if (ev.Type != RealtimeContract.Events.AsrPartial)
            {
                RecordEvent(ev, payload, replay);
            }
            return;
        }
        if (!replay && projectWork)
        {
            AppendRealtimeWorkEvent(ev);
        }
        if (!replay)
        {
            EventApplied?.Invoke(ev);
        }
        RecordEvent(ev, payload, replay);

        switch (ev.Type)
        {
            case RealtimeContract.Events.AssistantMessage:
                if (!replay && !string.IsNullOrWhiteSpace(eventText))
                {
                    // WS 事件带 request_id：回复写回发起会话，用户切换会话后到达也不串窗。
                    var requestId = TryReadEventRequestId(ev);
                    var targetSession = SessionForCommandRequest(requestId);
                    if (!FinalizeAssistantStreamDraft(requestId, eventText, targetSession))
                    {
                        AddMessage("assistant", eventText, targetSession: targetSession);
                    }
                }
                if (payload.ValueKind == JsonValueKind.Object)
                {
                    var requiresConfirmation = payload.TryGetProperty("requires_confirmation", out var confirm) && confirm.ValueKind == JsonValueKind.True;
                    var responseKind = ReadJsonString(payload, "response_kind");
                    if (requiresConfirmation)
                    {
                        var pendingInfo = PendingInfoFromAssistantMessage(payload);
                        if (ShouldSuppressConsumedPending(pendingInfo))
                        {
                            _state.Pending = null;
                        }
                        else
                        {
                            _state.Pending = new Dictionary<string, object?>
                            {
                                ["target"] = pendingInfo?.Target,
                                ["operation"] = pendingInfo?.Operation,
                                ["risk_level"] = pendingInfo?.RiskLevel,
                                ["created_at"] = NowSeconds(),
                            };
                        }
                    }
                    else if (ShouldClearPendingForReply(responseKind, eventText))
                    {
                        _state.Pending = null;
                    }
                    if (payload.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Object && data.TryGetProperty("scheduler", out var scheduler))
                    {
                        _state.LastRoute = JsonElementToDictionary(scheduler);
                    }
                    if (!replay && payload.TryGetProperty("data", out var replyData) && replyData.ValueKind == JsonValueKind.Object)
                    {
                        ApplyComposerModeMetadata(replyData);
                    }
                }
                break;
            case RealtimeContract.Events.AssistantConfirmationRequested:
                var pending = JsonElementToDictionary(payload);
                pending["created_at"] = NowSeconds();
                if (ShouldSuppressConsumedPending(PendingInfo(pending)))
                {
                    _state.Pending = null;
                }
                else
                {
                    _state.Pending = pending;
                }
                break;
            case RealtimeContract.Events.AssistantExecutionUpdated:
                _state.LastExecution = JsonElementToDictionary(payload);
                _state.Pending = null;
                if (!replay)
                {
                    _composerControllerValue.IncrementAssistantCommandCount();
                }
                break;
            case RealtimeContract.Events.AssistantTaskUpdated:
                if (!replay)
                {
                    UpsertRuntimeItem(_state.Tasks, payload, "task", "Runtime 任务");
                }
                break;
            case RealtimeContract.Events.AssistantProjectUpdated:
                if (!replay)
                {
                    UpsertRuntimeItem(_state.Projects, payload, "project", "Runtime 项目");
                }
                break;
            case RealtimeContract.Events.ProactiveSuggested:
                if (!replay && !string.IsNullOrWhiteSpace(eventText))
                {
                    WorkspaceSidebar.ConnectionStatusText.Text = eventText;
                    WorkspaceSidebar.ConnectionStatusText.Visibility = Visibility.Visible;
                }
                break;
        }
        RenderState();
    }

    private static bool IsVoiceCallUiEvent(string eventType)
    {
        return eventType == RealtimeContract.Events.VoiceCallState
            || eventType == RealtimeContract.Events.VoiceCallTranscript
            || eventType == RealtimeContract.Events.AsrSpeechStarted
            || eventType == RealtimeContract.Events.AsrPartial
            || eventType == RealtimeContract.Events.AsrFinal;
    }

    private void UpsertAssistantStreamDraft(RuntimeEvent ev)
    {
        if (ev.Payload.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        var requestId = TryReadEventRequestId(ev);
        if (string.IsNullOrWhiteSpace(requestId) || _completedAssistantStreamRequestIds.Contains(requestId))
        {
            return;
        }
        var text = CleanAvatarTags(ReadJsonString(ev.Payload, "text"));
        if (string.IsNullOrEmpty(text))
        {
            return;
        }
        var session = SessionForCommandRequest(requestId) ?? _workspaceControllerValue.ActiveSession();
        DesktopMessage? draft = null;
        if (_assistantStreamDraftMessageIds.TryGetValue(requestId, out var draftId))
        {
            draft = session.Messages.FirstOrDefault(message => string.Equals(message.Id, draftId, StringComparison.OrdinalIgnoreCase));
        }
        if (draft is null)
        {
            var now = NowSeconds();
            draft = new DesktopMessage
            {
                Id = NewId("stream"),
                Role = "assistant",
                Kind = "assistant_stream_draft",
                Subtitle = "running",
                Text = text,
                CreatedAt = now,
                UpdatedAt = now,
            };
            session.Messages.Add(draft);
            _assistantStreamDraftMessageIds[requestId] = draft.Id;
        }
        else if (!string.Equals(draft.Text, text, StringComparison.Ordinal))
        {
            draft.Text = text;
            draft.UpdatedAt = NowSeconds();
        }
        session.UpdatedAt = NowSeconds();
        RenderState();
        ScrollMessagesToEnd();
    }

    private bool FinalizeAssistantStreamDraft(string requestId, string text, DesktopSession? targetSession)
    {
        if (string.IsNullOrWhiteSpace(requestId))
        {
            return false;
        }
        if (_completedAssistantStreamRequestIds.Count >= 64)
        {
            _completedAssistantStreamRequestIds.Clear();
        }
        _completedAssistantStreamRequestIds.Add(requestId);
        if (!_assistantStreamDraftMessageIds.Remove(requestId, out var draftId))
        {
            return false;
        }
        var session = targetSession ?? _workspaceControllerValue.ActiveSession();
        var draft = session.Messages.FirstOrDefault(message => string.Equals(message.Id, draftId, StringComparison.OrdinalIgnoreCase));
        if (draft is null)
        {
            return false;
        }
        draft.Text = CleanAvatarTags(text);
        draft.Kind = "";
        draft.Subtitle = "";
        draft.UpdatedAt = NowSeconds();
        session.UpdatedAt = draft.UpdatedAt;
        RenderState();
        ScrollMessagesToEnd();
        return true;
    }

    private void AppendRealtimeWorkEvent(RuntimeEvent ev)
    {
        if (IsStaleEvent(ev))
        {
            return;
        }
        if (ev.Payload.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        if (IsCollaborationWorkEvent(ev.Payload))
        {
            _contextControllerValue.AppendCollaborationWorkEvent(ev);
            return;
        }

        // schema v1 兼容：从事件载荷提取 seq/span_id/parent_id/status/terminal 等结构字段；
        // 后端未落地时全为空/0，下游渲染回退到 CreatedAt 排序与扁平结构。
        var meta = ComposerController.ReadTraceMeta(ev.Payload, ev.Type);

        switch (ev.Type)
        {
            case RealtimeContract.Events.AssistantWorkUpdated:
                {
                    var step = ComposerController.DescribeAssistantWorkUpdatedStep(ev.Payload);
                    _composerControllerValue.AppendAssistantWorkStep(step.Title, step.Detail, step.Key, meta, render: false);
                    break;
                }
            case RealtimeContract.Events.AssistantMessage:
                foreach (var step in ComposerController.DescribeAssistantMessageWorkSteps(ev.Payload))
                {
                    _composerControllerValue.AppendAssistantWorkStep(step.Title, step.Detail, step.Key, meta, render: false);
                }
                break;
            case RealtimeContract.Events.AssistantConfirmationRequested:
                {
                    var step = ComposerController.DescribeConfirmationWorkStep(ev.Payload);
                    _composerControllerValue.AppendAssistantWorkStep(step.Title, step.Detail, step.Key, meta, render: false);
                    break;
                }
            case RealtimeContract.Events.AssistantExecutionUpdated:
                {
                    var step = ComposerController.DescribeExecutionWorkStep(ev.Payload);
                    _composerControllerValue.AppendAssistantWorkStep(step.Title, step.Detail, step.Key, meta, render: false);
                    break;
                }
            case RealtimeContract.Events.AssistantTaskUpdated:
                _composerControllerValue.AppendAssistantWorkStep("工作指令", ComposerController.DescribeTaskWorkStep(ev.Payload), ComposerController.DescribeTaskWorkStepKey(ev.Payload), meta, render: false);
                break;
            case RealtimeContract.Events.AssistantProjectUpdated:
                _composerControllerValue.AppendAssistantWorkStep("工作指令", ComposerController.DescribeProjectWorkStep(ev.Payload), ComposerController.DescribeProjectWorkStepKey(ev.Payload), meta, render: false);
                break;
        }
    }

    private static bool IsCollaborationWorkEvent(JsonElement payload)
    {
        if (payload.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        if (ReadJsonString(payload, "surface").Equals("collaboration", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        if (!payload.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        return ReadJsonString(detail, "surface").Equals("collaboration", StringComparison.OrdinalIgnoreCase);
    }

    private static bool ShouldClearPendingForReply(string responseKind, string? text)
    {
        var kind = (responseKind ?? "").Trim().ToLowerInvariant();
        if (kind == "confirmation_request")
        {
            return false;
        }
        if (kind is "execution_result" or "confirmation_cancelled" or "policy_denied" or "stale_request")
        {
            return true;
        }
        var normalizedText = (text ?? "").Trim();
        return normalizedText.Contains("当前没有等待确认", StringComparison.OrdinalIgnoreCase)
            || normalizedText.Contains("已取消", StringComparison.OrdinalIgnoreCase);
    }

    private static string ResolveDesktopSpeechText(CommandEnvelope? envelope)
    {
        if (envelope?.Reply is { } reply)
        {
            if (!string.IsNullOrWhiteSpace(reply.SpokenText))
            {
                var spoken = CleanSpeechText(reply.SpokenText);
                if (!string.IsNullOrWhiteSpace(spoken))
                {
                    return spoken;
                }
            }
            if (!string.IsNullOrWhiteSpace(reply.Text))
            {
                return CleanSpeechText(reply.Text);
            }
        }
        if (envelope?.Events is null)
        {
            return "";
        }
        foreach (var ev in envelope.Events)
        {
            if (!string.Equals(ev.Type, RealtimeContract.Events.AssistantMessage, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            var payload = ev.Payload;
            if (payload.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            var spoken = ReadJsonString(payload, "spoken_text");
            if (!string.IsNullOrWhiteSpace(spoken))
            {
                var cleanSpoken = CleanSpeechText(spoken);
                if (!string.IsNullOrWhiteSpace(cleanSpoken))
                {
                    return cleanSpoken;
                }
            }
            var text = ReadJsonString(payload, "text");
            if (!string.IsNullOrWhiteSpace(text))
            {
                return CleanSpeechText(text);
            }
        }
        return "";
    }

}
