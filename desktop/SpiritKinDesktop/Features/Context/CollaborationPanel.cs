using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class ContextController
{
    private double _pendingCollaborationToolCreatedAt;
    private string _collaborationToolDecisionInFlightId = "";
    private readonly HashSet<string> _resolvedCollaborationToolCallIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, string> _collaborationToolResultOrigins = new(StringComparer.OrdinalIgnoreCase);

    // 协作工作链：后端把模型协作步骤接入同一条 assistant.work_updated trace 流，带 surface=collaboration。
    // 按 detail.thread_id + agent_id 分桶：每个参与模型一张独立的 DesktopMessage（kind=work），
    // 复用与主聊天工作链相同的结构化字段（seq/span_id/parent_id/status/is_terminal/agent_id）与去重逻辑，
    // 不依赖文案判断状态。渲染时作为 WorkChainViewModel 注入协作对话时间线。
    internal void AppendCollaborationWorkEvent(RuntimeEvent ev)
    {
        if (!string.Equals(ev.Type, RealtimeContract.Events.AssistantWorkUpdated, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        var payload = ev.Payload;
        if (payload.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        var threadId = CollaborationWorkEventThreadId(payload);
        if (string.IsNullOrWhiteSpace(threadId))
        {
            return;
        }

        var meta = ComposerController.ReadTraceMeta(payload, ev.Type);
        if (IsCollaborationModelDispatchEvent(payload))
        {
            AppendCollaborationModelDispatchEvent(threadId, payload, meta);
            return;
        }
        var streamOutput = CollaborationWorkStreamOutput(payload);
        var detailInfo = CollaborationWorkEventDetail(payload);
        var toolInfo = CollaborationToolEventInfo(payload);
        // 链条完成的真实信号：worker 汇报 processed/failed，或生命周期 acked（回帖已确认）。
        // 网关 root 包装事件的 is_terminal 恒为 true（流式中途也一样），不能用它判定完成。
        var finished = detailInfo.WorkerStatus is "processed" or "failed"
            || string.Equals(detailInfo.Lifecycle, "acked", StringComparison.OrdinalIgnoreCase);
        var changed = false;
        if (!IsCollaborationChainNoiseEvent(meta, detailInfo, streamOutput.Text))
        {
            var message = EnsureCollaborationWorkChain(threadId, meta.AgentId, meta.MessageId);
            var text = string.IsNullOrWhiteSpace(streamOutput.Text) ? ReadJsonString(payload, "text") : streamOutput.Text;
            var stream = (streamOutput.Stream ?? "").Trim().ToLowerInvariant();
            var accumulated = streamOutput.Accumulated;
            var reasoningVisibility = stream == "reasoning"
                ? (streamOutput.ReasoningVisibility ?? "").Trim().ToLowerInvariant()
                : "";
            if (stream == "reasoning")
            {
                // Every participant card shows the reasoning stream it actually emitted.
                // Codex summaries are discrete events; provider process streams are
                // prefix-growing snapshots and keep using the accumulated text.
                var isPublicSummary = string.Equals(reasoningVisibility, "summary", StringComparison.OrdinalIgnoreCase);
                if (!isPublicSummary)
                {
                    // Keep the current delta separate from the accumulated snapshot.
                    // The merge path uses the snapshot only while this reasoning segment
                    // remains contiguous; after a tool/lifecycle event the delta starts a
                    // new visible thought instead of replacing the earlier one.
                    text = CodexStyleReasoningActivity("", text);
                }
                else if (isPublicSummary)
                {
                    // Public summaries are discrete Codex events, not prefix-growing token
                    // snapshots. A leading newline appends later summaries while normalization
                    // removes it for the first item in a new lane.
                    text = $"\n{text.Trim()}";
                    accumulated = "";
                }
            }
            // 卡已完结（权威回复已投影/收到完成信号）后迟到的流式碎片：丢弃。
            // 完结泳道拒收后碎片会落到 AppendStructuredWorkStep 另开新步骤，
            // 表现为"回复都出来了 step 还在增加"（事件通道积压时迟到几十秒很常见）。
            var cardFinished = !string.Equals(message.Subtitle, "running", StringComparison.OrdinalIgnoreCase)
                && !string.IsNullOrWhiteSpace(message.Subtitle);
            if (stream == "token" && !cardFinished && !string.IsNullOrWhiteSpace(text))
            {
                changed |= UpsertCollaborationStreamingDraft(threadId, meta.AgentId, meta.MessageId, text, streamOutput.Accumulated, interrupted: false);
            }
            if (stream == "lifecycle")
            {
                text = toolInfo.IsTool
                    ? LocalizeCollaborationToolLifecycle(detailInfo.Lifecycle, toolInfo, text)
                    : LocalizeCollaborationLifecycle(detailInfo.Lifecycle, text);
                if (toolInfo.IsTool
                    && detailInfo.Lifecycle is "tool_requested" or "permission_required" or "tool_running")
                {
                    changed |= RemoveCollaborationStreamingDraft(threadId, meta.AgentId, meta.MessageId);
                }
                if (string.Equals(detailInfo.Lifecycle, "request_failed", StringComparison.OrdinalIgnoreCase))
                {
                    changed |= MarkCollaborationStreamingDraftInterrupted(threadId, meta.AgentId, meta.MessageId);
                }
            }
            var kind = string.IsNullOrWhiteSpace(streamOutput.Text)
                ? MapCollaborationStepKind(ReadJsonString(payload, "kind", "thought"))
                : MapCollaborationStreamKind(stream);
            var title = toolInfo.IsTool
                ? CollaborationToolStepTitle(toolInfo, detailInfo.Lifecycle)
                : CollaborationWorkStepTitle(payload, stream, detailInfo.Lifecycle);
            var lateStreamFragment = cardFinished && stream is "reasoning" or "token" or "stdout" or "draft";
            // 流式泳道标记：这些通道的 Detail 是前缀增长文本，UI 侧按此走打字机（含卡完结后收尾批）。
            var streamLane = stream is "reasoning" or "draft" or "token" or "stdout";
            // event_id 全局唯一，作为唯一去重键；连续流式文本并入当前段，
            // lifecycle/tool/edit 边界之后的文本另起步骤，保留真实事件顺序。
            var toolStepChanged = toolInfo.IsTool
                && UpsertCollaborationToolStep(message, toolInfo, detailInfo.Lifecycle, text, meta);
            if (!lateStreamFragment
                && (toolStepChanged
                    || (!toolInfo.IsTool && TryMergeCollaborationStreamStep(message, stream, kind, title, text, meta, accumulated, reasoningVisibility))
                    || (!toolInfo.IsTool && _composer.AppendStructuredWorkStep(
                        message,
                        kind,
                        title,
                        text,
                        key: "",
                        meta: meta,
                        streamLane: streamLane,
                        reasoningVisibility: reasoningVisibility))))
            {
                // 完结卡收到迟到的生命周期里程碑（reply_posted 等）：只记步骤，不拨回 running。
                // 否则卡被重开，后续迟到思考碎片又能进来，表现为"回复出来了思考链还在滚"。
                if (!cardFinished)
                {
                    message.Subtitle = "running";
                }
                message.UpdatedAt = NowSeconds();
                changed = true;
            }
        }
        if (finished)
        {
            // 完成信号只终结该 agent 自己的卡；agent 缺失时（网关兜底事件）终结该 thread 所有运行中的卡。
            foreach (var chain in CollaborationWorkChainsFor(threadId, meta.AgentId))
            {
                if (FinalizeCollaborationWorkChain(chain))
                {
                    changed = true;
                }
            }
            // 修P：从待完工集合摘掉该模型；agentId 空白（网关兜底事件）不摘，靠既有"无 running 卡"检查兜底。
            if (!string.IsNullOrWhiteSpace(meta.AgentId)
                && _collaborationPendingResumeAgents.TryGetValue(threadId, out var pendingAgents))
            {
                pendingAgents.Remove(meta.AgentId);
                if (pendingAgents.Count == 0)
                {
                    _collaborationPendingResumeAgents.Remove(threadId);
                }
            }
            // 修H：本会话工作完结且此前有被暂停的旧会话串联 → 延迟弹提示问是否恢复。
            MaybePromptResumePausedCollaborationThreads(threadId);
        }
        if (!changed)
        {
            return;
        }

        // 高频流式事件（实测峰值 10 条/秒）逐条全量投影 + 重渲染会打满 UI 线程：
        // 思考链卡顿、锚点失灵、会话切换迟滞全是同一根因。此处只标脏，
        // 由 200ms 合帧定时器统一投影渲染一次（worker 端 token 本就 0.7s 合批，观感无损）。
        MarkCollaborationThreadProjectionDirty(threadId);
    }

    private void AppendCollaborationModelDispatchEvent(string threadId, JsonElement payload, TraceMeta meta)
    {
        var messageId = meta.MessageId;
        if (string.IsNullOrWhiteSpace(messageId)
            && payload.TryGetProperty("detail", out var detail)
            && detail.ValueKind == JsonValueKind.Object)
        {
            messageId = ReadJsonString(detail, "message_id");
        }
        if (string.IsNullOrWhiteSpace(messageId))
        {
            return;
        }

        var title = CollaborationModelDispatchStepTitle(payload);
        var text = ReadJsonString(payload, "text");
        foreach (var target in CollaborationModelDispatchTargets(payload))
        {
            // Dispatch belongs to the target model's reply group. The gateway's
            // terminal flag closes only the routing action, not the model turn, so
            // keep this card running until that participant posts its reply.
            var targetMeta = new TraceMeta
            {
                Seq = meta.Seq,
                RunId = meta.RunId,
                EventId = meta.EventId,
                SpanId = meta.SpanId,
                ParentId = meta.ParentId,
                Status = meta.Status,
                IsTerminal = false,
                AgentId = target.AgentId,
                MessageId = meta.MessageId,
                StepKind = "call",
                CallAgent = target.Label,
                CallModel = target.Model,
                CallProvider = target.Provider,
                CommandText = meta.CommandText,
                CommandOutput = meta.CommandOutput,
                ShellLabel = meta.ShellLabel,
            };
            var card = EnsureCollaborationWorkChain(threadId, target.AgentId, messageId);
            card.WorkAgent = CollaborationAgentDisplay(target.AgentId);
            var targetIdentity = string.Join(
                " · ",
                new[] { target.Label, target.Provider, target.Model }
                    .Where(value => !string.IsNullOrWhiteSpace(value)));
            var stepText = string.Equals(title, "调用模型", StringComparison.Ordinal)
                ? $"调用 {targetIdentity}。"
                : text;
            if (_composer.AppendStructuredWorkStep(
                    card,
                    "call",
                    title,
                    stepText,
                    key: $"dispatch:{messageId}:{target.AgentId}:{meta.SpanId}",
                    meta: targetMeta))
            {
                card.UpdatedAt = NowSeconds();
            }
        }
        MarkCollaborationThreadProjectionDirty(threadId);
    }

    internal static IReadOnlyList<(string AgentId, string Label, string Provider, string Model)> CollaborationModelDispatchTargets(JsonElement payload)
    {
        var targets = new List<(string AgentId, string Label, string Provider, string Model)>();
        if (!payload.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.Object)
        {
            return targets;
        }
        if (detail.TryGetProperty("call_targets", out var callTargets) && callTargets.ValueKind == JsonValueKind.Array)
        {
            foreach (var target in callTargets.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object))
            {
                var agentId = ReadJsonString(target, "agent_id").Trim();
                if (string.IsNullOrWhiteSpace(agentId)
                    || string.Equals(agentId, "all", StringComparison.OrdinalIgnoreCase)
                    || IsHumanAgentId(agentId)
                    || targets.Any(item => string.Equals(item.AgentId, agentId, StringComparison.OrdinalIgnoreCase)))
                {
                    continue;
                }
                targets.Add((
                    agentId,
                    ReadJsonString(target, "label", agentId).Trim(),
                    ReadJsonString(target, "provider").Trim(),
                    ReadJsonString(target, "model").Trim()));
            }
        }
        if (targets.Count > 0)
        {
            return targets;
        }
        foreach (var agentId in ReadJsonStringArray(detail, "targets"))
        {
            var normalized = (agentId ?? "").Trim();
            if (string.IsNullOrWhiteSpace(normalized)
                || string.Equals(normalized, "all", StringComparison.OrdinalIgnoreCase)
                || IsHumanAgentId(normalized)
                || targets.Any(item => string.Equals(item.AgentId, normalized, StringComparison.OrdinalIgnoreCase)))
            {
                continue;
            }
            targets.Add((normalized, normalized, "", ""));
        }
        return targets;
    }

    private static bool IsCollaborationModelDispatchEvent(JsonElement payload)
    {
        return payload.TryGetProperty("detail", out var detail)
            && detail.ValueKind == JsonValueKind.Object
            && string.Equals(ReadJsonString(detail, "card_kind"), "model_dispatch", StringComparison.OrdinalIgnoreCase);
    }

    internal static string CollaborationModelDispatchStepTitle(JsonElement payload)
    {
        if (!payload.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.Object)
        {
            return "模型调度";
        }
        return ReadJsonString(detail, "dispatch_stage").Trim().ToLowerInvariant() switch
        {
            "accepted" => "调用模型",
            "policy" => "路由模型",
            "route_bus" => "模型已接入",
            _ => "调用模型",
        };
    }

    // 协作投影合帧：事件处理只登记脏 thread，定时器每帧统一投影 + 渲染。
    private void MarkCollaborationThreadProjectionDirty(string threadId)
    {
        _collaborationDirtyThreads.Add(threadId);
        if (!_collaborationProjectionTimer.IsEnabled)
        {
            _collaborationProjectionTimer.Start();
        }
    }

    internal void FlushCollaborationProjection()
    {
        _collaborationProjectionTimer.Stop();
        if (_collaborationDirtyThreads.Count == 0)
        {
            return;
        }
        var threads = _collaborationDirtyThreads.ToList();
        _collaborationDirtyThreads.Clear();
        var activeThread = CurrentSessionCollaborationThreadId();
        var renderActive = false;
        var renderComposer = false;
        foreach (var threadId in threads)
        {
            // 投影按事件自身 thread 解析目标会话（后台会话也投），否则用户切走期间整轮丢卡；
            // 渲染仍只对激活会话做，避免后台线程刷屏。
            UpsertCollaborationWorkChainsForThread(threadId);
            if (_collaborationChatActive && string.Equals(threadId, activeThread, StringComparison.OrdinalIgnoreCase))
            {
                renderActive = true;
            }
            else if (_collaborationChatActive && string.Equals(threadId, CurrentCollaborationTaskId(), StringComparison.OrdinalIgnoreCase))
            {
                renderComposer = true;
            }
        }
        if (renderActive)
        {
            RenderActiveMessages(_workspaceController.ActiveSession());
            SyncQuickChatLayout(_workspaceController.ActiveSession());
        }
        if (renderComposer)
        {
            _composer.RenderCollaborationComposerModeOnly();
        }
    }

    // Only contiguous streamed text belongs to one visible segment. A lifecycle,
    // command, tool result, or edit between batches is an event boundary: later
    // reasoning starts a new row so the timeline reads thought -> tool -> thought.
    internal static bool TryMergeCollaborationStreamStep(
        DesktopMessage message,
        string stream,
        string kind,
        string title,
        string text,
        TraceMeta meta,
        string accumulated = "",
        string reasoningVisibility = "")
    {
        var normalizedStream = (stream ?? "").Trim().ToLowerInvariant();
        if (normalizedStream is not ("reasoning" or "draft" or "token" or "stdout")
            || string.IsNullOrEmpty(text))
        {
            return false;
        }
        var steps = message.Steps ?? new List<DesktopWorkStep>();
        DesktopWorkStep? lane = null;
        var laneIndex = -1;
        for (var i = steps.Count - 1; i >= 0; i--)
        {
            var candidate = steps[i];
            if (candidate.IsStreamLane
                && string.Equals(candidate.Kind, kind, StringComparison.OrdinalIgnoreCase)
                && string.Equals(candidate.Title, title, StringComparison.OrdinalIgnoreCase)
                && string.Equals(candidate.AgentId ?? "", meta.AgentId ?? "", StringComparison.OrdinalIgnoreCase))
            {
                lane = candidate;
                laneIndex = i;
                break;
            }
        }
        if (lane is null || lane.IsTerminal || laneIndex != steps.Count - 1)
        {
            return false;
        }
        // 同一事件重复投递：交给 AppendStructuredWorkStep 的 event_id 去重兜底。
        if (!string.IsNullOrEmpty(meta.EventId)
            && string.Equals(lane.EventId, meta.EventId, StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        string merged;
        var hasEarlierSegment = steps.Take(laneIndex).Any(candidate =>
            candidate.IsStreamLane
            && string.Equals(candidate.Kind, kind, StringComparison.OrdinalIgnoreCase)
            && string.Equals(candidate.Title, title, StringComparison.OrdinalIgnoreCase)
            && string.Equals(candidate.AgentId ?? "", meta.AgentId ?? "", StringComparison.OrdinalIgnoreCase));
        if (!hasEarlierSegment
            && normalizedStream is "reasoning" or "draft" or "token"
            && !string.IsNullOrEmpty(accumulated))
        {
            // 权威路径：worker 附带的累计全文整体覆盖泳道内容；更短的是旧批次重复投递，丢弃但仍
            // 认领 event_id（避免落到 AppendStructuredWorkStep 另开新步骤）。
            merged = accumulated.Length >= (lane.Detail ?? "").Length ? accumulated : lane.Detail ?? "";
        }
        else
        {
            merged = string.IsNullOrEmpty(lane.Detail) ? text : lane.Detail + text;
        }
        // 超长时保头+尾（开头是思路起点、结尾是结论，中间过程可省），避免"被截断"观感。
        const int maxChars = 24000;
        const int keepHead = 10000;
        const int keepTail = 10000;
        if (merged.Length > maxChars)
        {
            merged = merged[..keepHead] + "\n……（中间推理过程已省略）……\n" + merged[^keepTail..];
        }
        lane.Detail = merged;
        lane.EventId = string.IsNullOrEmpty(meta.EventId) ? lane.EventId : meta.EventId;
        lane.Status = string.IsNullOrEmpty(meta.Status) ? lane.Status : meta.Status;
        lane.CreatedAt = lane.CreatedAt > 0 ? lane.CreatedAt : NowSeconds();
        // 泳道一旦收过流式片段即视为流式泳道（旧数据/首建于非流式路径的泳道也能补上标记）。
        lane.IsStreamLane |= normalizedStream is "reasoning" or "draft" or "token" or "stdout";
        if (normalizedStream == "reasoning" && !string.IsNullOrWhiteSpace(reasoningVisibility))
        {
            lane.ReasoningVisibility = reasoningVisibility.Trim().ToLowerInvariant();
        }
        return true;
    }

    // 该 thread 下匹配 agent 的全部轮次卡；agent 为空返回全部（用于无 agent 归属的完成信号兜底与整体投影）。
    private IEnumerable<DesktopMessage> CollaborationWorkChainsFor(string threadId, string agentId)
    {
        if (!_collaborationWorkChains.TryGetValue(threadId, out var byAgent))
        {
            yield break;
        }
        var agentKey = NormalizeCollaborationWorkAgentKey(agentId);
        if (!string.IsNullOrEmpty(agentKey) && agentKey != "agent")
        {
            if (byAgent.TryGetValue(agentKey, out var cards))
            {
                foreach (var chain in cards)
                {
                    yield return chain;
                }
            }
            yield break;
        }
        foreach (var cards in byAgent.Values)
        {
            foreach (var chain in cards)
            {
                yield return chain;
            }
        }
    }

    private static string CollaborationWorkEventThreadId(JsonElement payload)
    {
        if (payload.TryGetProperty("detail", out var detail) && detail.ValueKind == JsonValueKind.Object)
        {
            var threadId = ReadJsonString(detail, "thread_id");
            if (!string.IsNullOrWhiteSpace(threadId))
            {
                return threadId.Trim();
            }
            var taskId = ReadJsonString(detail, "task_id");
            if (!string.IsNullOrWhiteSpace(taskId))
            {
                return taskId.Trim();
            }
        }
        var topThread = ReadJsonString(payload, "thread_id");
        return string.IsNullOrWhiteSpace(topThread) ? "" : topThread.Trim();
    }

    // 网关侧的记账动作：ack/已读/路由回执/轮询器结果，对用户没有推理价值，不进 Codex 式工作链。
    private static readonly HashSet<string> CollaborationBookkeepingActions = new(StringComparer.OrdinalIgnoreCase)
    {
        "ack_agent_route_bus_message",
        "ack_route_bus_message",
        "ack_agent_message",
        "mark_message_read",
        "read_message",
        "post_message",
        "send_message",
        "add_message",
        "request_model_review",
        "request_review_message",
        "run_participant_once",
        "run_collaboration_participant_once",
        "run_agent_route_bus_worker_once",
        "route_bus_worker_once",
        "dry_run_route_bus_worker",
    };

    // Codex 式链条只保留真实内容：推理/生命周期/输出流。噪声来源有三类：
    // 1) 网关每个动作都会额外包一对 root 事件（parent_id 为空，“Collaboration action ... started/completed.”），
    //    且其 detail.worker_event 与真实子事件重复，会把同一段推理重复投影；
    // 2) 记账动作（ack/已读/路由回执）；
    // 3) worker started/processed 状态标记（record_*_worker_event 且无 output 正文）。
    private static bool IsCollaborationChainNoiseEvent(
        TraceMeta meta,
        (string Action, string Lifecycle, string WorkerStatus) detailInfo,
        string streamText)
    {
        if (string.IsNullOrWhiteSpace(meta.ParentId))
        {
            return true;
        }
        if (CollaborationBookkeepingActions.Contains(detailInfo.Action))
        {
            return true;
        }
        if (string.Equals(detailInfo.Lifecycle, "acked", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        var isWorkerEventAction = string.Equals(detailInfo.Action, "record_agent_route_bus_worker_event", StringComparison.OrdinalIgnoreCase)
            || string.Equals(detailInfo.Action, "record_route_bus_worker_event", StringComparison.OrdinalIgnoreCase);
        return isWorkerEventAction && string.IsNullOrWhiteSpace(streamText);
    }

    private static (string Action, string Lifecycle, string WorkerStatus) CollaborationWorkEventDetail(JsonElement payload)
    {
        if (!payload.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.Object)
        {
            return ("", "", "");
        }
        var action = ReadJsonString(detail, "action").Trim();
        var lifecycle = "";
        var workerStatus = "";
        if (detail.TryGetProperty("worker_event", out var workerEvent) && workerEvent.ValueKind == JsonValueKind.Object)
        {
            workerStatus = ReadJsonString(workerEvent, "status").Trim().ToLowerInvariant();
            if (workerEvent.TryGetProperty("metadata", out var metadata) && metadata.ValueKind == JsonValueKind.Object)
            {
                lifecycle = ReadJsonString(metadata, "lifecycle").Trim().ToLowerInvariant();
            }
        }
        if (string.IsNullOrWhiteSpace(lifecycle))
        {
            lifecycle = ReadJsonString(detail, "lifecycle").Trim().ToLowerInvariant();
        }
        return (action, lifecycle, workerStatus);
    }

    internal static (bool IsTool, string ToolCallId, string Target, string Operation, string CommandPreview, string CommandOutput) CollaborationToolEventInfo(JsonElement payload)
    {
        if (!payload.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.Object)
        {
            return (false, "", "", "", "", "");
        }
        var toolCallId = ReadJsonString(detail, "tool_call_id").Trim();
        var target = ReadJsonString(detail, "target").Trim();
        var operation = ReadJsonString(detail, "operation").Trim();
        var commandPreview = CollaborationToolCommandPreview(detail);
        var commandOutput = ReadJsonString(detail, "command_output").Trim();
        if (detail.TryGetProperty("tool_call", out var toolCall) && toolCall.ValueKind == JsonValueKind.Object)
        {
            toolCallId = string.IsNullOrWhiteSpace(toolCallId) ? ReadJsonString(toolCall, "tool_call_id").Trim() : toolCallId;
            target = string.IsNullOrWhiteSpace(target) ? ReadJsonString(toolCall, "target").Trim() : target;
            operation = string.IsNullOrWhiteSpace(operation) ? ReadJsonString(toolCall, "operation").Trim() : operation;
            commandPreview = string.IsNullOrWhiteSpace(commandPreview) ? CollaborationToolCommandPreview(toolCall) : commandPreview;
            commandOutput = string.IsNullOrWhiteSpace(commandOutput) ? ReadJsonString(toolCall, "command_output").Trim() : commandOutput;
        }
        if (detail.TryGetProperty("worker_event", out var workerEvent)
            && workerEvent.ValueKind == JsonValueKind.Object
            && workerEvent.TryGetProperty("metadata", out var metadata)
            && metadata.ValueKind == JsonValueKind.Object)
        {
            toolCallId = string.IsNullOrWhiteSpace(toolCallId) ? ReadJsonString(metadata, "tool_call_id").Trim() : toolCallId;
            target = string.IsNullOrWhiteSpace(target) ? ReadJsonString(metadata, "target").Trim() : target;
            operation = string.IsNullOrWhiteSpace(operation) ? ReadJsonString(metadata, "operation").Trim() : operation;
            commandPreview = string.IsNullOrWhiteSpace(commandPreview) ? CollaborationToolCommandPreview(metadata) : commandPreview;
            commandOutput = string.IsNullOrWhiteSpace(commandOutput) ? ReadJsonString(metadata, "command_output").Trim() : commandOutput;
        }
        return (!string.IsNullOrWhiteSpace(toolCallId) || (!string.IsNullOrWhiteSpace(target) && !string.IsNullOrWhiteSpace(operation)), toolCallId, target, operation, commandPreview, commandOutput);
    }

    internal static string CollaborationToolCommandPreview(JsonElement container)
    {
        foreach (var key in new[] { "command", "cmd", "app_name", "app", "path", "url" })
        {
            var value = ReadJsonString(container, key).Trim();
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }
        }

        foreach (var nestedKey in new[] { "params", "arguments", "input" })
        {
            if (container.TryGetProperty(nestedKey, out var nested) && nested.ValueKind == JsonValueKind.Object)
            {
                var preview = CollaborationToolCommandPreview(nested);
                if (!string.IsNullOrWhiteSpace(preview))
                {
                    return preview;
                }
            }
        }
        return "";
    }

    internal static string CollaborationToolShellLabel(string target, string operation, string commandPreview)
    {
        if (string.IsNullOrWhiteSpace(commandPreview))
        {
            return "Tool";
        }
        if (!string.Equals(target, "external_cli", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(operation, "command_execution", StringComparison.OrdinalIgnoreCase))
        {
            return "Tool";
        }
        var executable = commandPreview.TrimStart();
        if (executable.StartsWith("cmd", StringComparison.OrdinalIgnoreCase))
        {
            return "CMD";
        }
        if (executable.StartsWith("powershell", StringComparison.OrdinalIgnoreCase)
            || executable.StartsWith("pwsh", StringComparison.OrdinalIgnoreCase))
        {
            return "PowerShell";
        }
        // The desktop runtime is PowerShell-first. Structured Codex events often
        // report only the inner command (for example `rg --files`), so preserve the
        // concrete command text and label that host shell instead of guessing CMD.
        return OperatingSystem.IsWindows() ? "PowerShell" : "Shell";
    }

    internal static bool UpsertCollaborationToolStep(
        DesktopMessage message,
        (bool IsTool, string ToolCallId, string Target, string Operation, string CommandPreview, string CommandOutput) tool,
        string lifecycle,
        string detail,
        TraceMeta meta)
    {
        if (!tool.IsTool)
        {
            return false;
        }
        var normalizedLifecycle = (lifecycle ?? "").Trim().ToLowerInvariant();
        var toolKey = !string.IsNullOrWhiteSpace(tool.ToolCallId)
            ? tool.ToolCallId
            : $"{tool.Target}.{tool.Operation}";
        var isResult = normalizedLifecycle is "tool_completed" or "tool_failed" or "tool_blocked";
        var isPermission = normalizedLifecycle is "permission_required" or "denied";
        var steps = message.Steps ??= new List<DesktopWorkStep>();
        var changed = false;

        if (isResult)
        {
            var call = steps.LastOrDefault(step => string.Equals(step.Key, $"tool:{toolKey}:call", StringComparison.OrdinalIgnoreCase));
            if (call is not null && !string.Equals(call.Status, "completed", StringComparison.OrdinalIgnoreCase))
            {
                call.Status = "completed";
                changed = true;
            }
        }

        var key = isResult ? $"tool:{toolKey}:result" : $"tool:{toolKey}:call";
        var kind = isResult ? "result" : isPermission ? "permission" : "command";
        var operationText = string.Join(".", new[] { tool.Target, tool.Operation }.Where(value => !string.IsNullOrWhiteSpace(value)));
        var commandText = string.IsNullOrWhiteSpace(tool.CommandPreview) ? operationText : tool.CommandPreview;
        var shellLabel = CollaborationToolShellLabel(tool.Target, tool.Operation, tool.CommandPreview);
        var status = normalizedLifecycle switch
        {
            "tool_completed" => "completed",
            "tool_failed" => "failed",
            "permission_required" => "running",
            "tool_blocked" or "denied" => "blocked",
            _ => "running",
        };
        var existing = steps.LastOrDefault(step => string.Equals(step.Key, key, StringComparison.OrdinalIgnoreCase));
        var displayDetail = detail;
        var resultOutput = isResult && !string.IsNullOrWhiteSpace(tool.CommandOutput) ? tool.CommandOutput : displayDetail;
        if (!isResult && !string.IsNullOrWhiteSpace(tool.CommandPreview))
        {
            displayDetail = tool.CommandPreview;
        }
        else if (!isResult && existing is not null && normalizedLifecycle is "approved" or "tool_requested" or "tool_running")
        {
            displayDetail = existing.Detail;
        }
        var title = CollaborationToolStepTitle(tool, normalizedLifecycle);
        if (existing is null)
        {
            steps.Add(new DesktopWorkStep
            {
                Kind = kind,
                Title = title,
                Detail = displayDetail,
                Key = key,
                CreatedAt = NowSeconds(),
                Seq = meta.Seq,
                RunId = meta.RunId,
                EventId = meta.EventId,
                SpanId = string.IsNullOrWhiteSpace(meta.SpanId) ? $"collab-tool:{toolKey}:{(isResult ? "result" : "call")}" : meta.SpanId,
                ParentId = meta.ParentId,
                Status = status,
                AgentId = meta.AgentId,
                CommandText = kind == "command" ? commandText : "",
                CommandOutput = isResult ? resultOutput : "",
                ShellLabel = kind == "command" ? shellLabel : "",
            });
            return true;
        }

        if (!string.Equals(existing.Kind, kind, StringComparison.OrdinalIgnoreCase)
            || !string.Equals(existing.Title, title, StringComparison.Ordinal)
            || !string.Equals(existing.Detail, displayDetail, StringComparison.Ordinal)
            || !string.Equals(existing.Status, status, StringComparison.OrdinalIgnoreCase)
            || !string.Equals(existing.EventId, meta.EventId, StringComparison.OrdinalIgnoreCase)
            || !string.Equals(existing.CommandText, kind == "command" ? commandText : "", StringComparison.Ordinal)
            || !string.Equals(existing.CommandOutput, isResult ? resultOutput : "", StringComparison.Ordinal)
            || !string.Equals(existing.ShellLabel, kind == "command" ? shellLabel : "", StringComparison.Ordinal))
        {
            existing.Kind = kind;
            existing.Title = title;
            existing.Detail = displayDetail;
            existing.Status = status;
            existing.EventId = meta.EventId;
            existing.AgentId = string.IsNullOrWhiteSpace(meta.AgentId) ? existing.AgentId : meta.AgentId;
            existing.CommandText = kind == "command" ? commandText : "";
            existing.CommandOutput = isResult ? resultOutput : "";
            existing.ShellLabel = kind == "command" ? shellLabel : "";
            changed = true;
        }
        return changed;
    }

    private static string CollaborationToolStepTitle(
        (bool IsTool, string ToolCallId, string Target, string Operation, string CommandPreview, string CommandOutput) tool,
        string lifecycle)
    {
        var operation = string.IsNullOrWhiteSpace(tool.Target)
            ? tool.Operation
            : $"{tool.Target}.{tool.Operation}";
        var prefix = (lifecycle ?? "").Trim().ToLowerInvariant() switch
        {
            "permission_required" or "denied" => "工具授权",
            "tool_completed" or "tool_failed" or "tool_blocked" => "工具结果",
            _ => "工具调用",
        };
        return string.IsNullOrWhiteSpace(operation) ? prefix : $"{prefix} · {operation}";
    }

    private static string LocalizeCollaborationToolLifecycle(
        string lifecycle,
        (bool IsTool, string ToolCallId, string Target, string Operation, string CommandPreview, string CommandOutput) tool,
        string fallback)
    {
        var operation = string.IsNullOrWhiteSpace(tool.Target)
            ? tool.Operation
            : $"{tool.Target}.{tool.Operation}";
        return (lifecycle ?? "").Trim().ToLowerInvariant() switch
        {
            "permission_required" => $"{operation} 正在等待人工授权",
            "approved" => $"{operation} 已获授权",
            "denied" => $"{operation} 已被拒绝",
            "tool_requested" => $"已提交真实工具请求 {operation}",
            "tool_running" => $"正在执行 {operation}",
            "tool_completed" => string.IsNullOrWhiteSpace(fallback) ? $"{operation} 执行完成" : fallback,
            "tool_failed" => string.IsNullOrWhiteSpace(fallback) ? $"{operation} 执行失败" : fallback,
            "tool_blocked" => string.IsNullOrWhiteSpace(fallback) ? $"{operation} 已阻塞" : fallback,
            _ => fallback,
        };
    }

    private static (string Text, string Stream, string Accumulated, string ReasoningVisibility) CollaborationWorkStreamOutput(JsonElement payload)
    {
        if (!payload.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.Object)
        {
            return ("", "", "", "");
        }
        if (!detail.TryGetProperty("worker_event", out var workerEvent) || workerEvent.ValueKind != JsonValueKind.Object)
        {
            return ("", "", "", "");
        }
        if (!workerEvent.TryGetProperty("metadata", out var metadata) || metadata.ValueKind != JsonValueKind.Object)
        {
            return ("", "", "", "");
        }
        var output = ReadJsonString(metadata, "output").Trim();
        if (string.IsNullOrWhiteSpace(output))
        {
            return ("", "", "", "");
        }
        // accumulated：worker 每批附带的该通道累计全文。桌面用它整体覆盖草稿/泳道，
        // 增量 delta 仅作老版本 worker 的回退路径（追加曾被重复投递/回滚搞坏文本）。
        return (
            output,
            ReadJsonString(metadata, "stream"),
            ReadJsonString(metadata, "accumulated"),
            ReadJsonString(metadata, "reasoning_visibility"));
    }

    internal static string CodexStyleReasoningActivity(string accumulated, string delta)
    {
        var process = !string.IsNullOrWhiteSpace(accumulated) ? accumulated : delta;
        return string.IsNullOrWhiteSpace(process)
            ? "正在分析当前请求与上下文"
            : process.Trim();
    }

    // 工作链 agent 分桶键：空/未知归到 "agent" 桶（网关兜底事件），其余按 agent_id 独立成卡。
    private static string NormalizeCollaborationWorkAgentKey(string agentId)
    {
        var normalized = (agentId ?? "").Trim().ToLowerInvariant();
        return string.IsNullOrEmpty(normalized) ? "agent" : normalized;
    }

    // 每轮一张卡：轮次键 = 被处理来件的 message_id（网关 detail.message_id）。
    // 同轮事件并入当前卡；轮次键变化或上一张卡已完结 → 新建卡，历史卡保持完结状态不复用。
    private DesktopMessage EnsureCollaborationWorkChain(string threadId, string agentId, string messageId)
    {
        if (!_collaborationWorkChains.TryGetValue(threadId, out var byAgent))
        {
            byAgent = new Dictionary<string, List<DesktopMessage>>(StringComparer.OrdinalIgnoreCase);
            _collaborationWorkChains[threadId] = byAgent;
        }
        var agentKey = NormalizeCollaborationWorkAgentKey(agentId);
        if (!byAgent.TryGetValue(agentKey, out var cards))
        {
            cards = new List<DesktopMessage>();
            byAgent[agentKey] = cards;
        }
        var roundKey = (messageId ?? "").Trim();
        var chainKey = $"{NormalizeCollaborationThreadKey(threadId)}|{agentKey}";
        var current = cards.LastOrDefault();
        if (current is not null)
        {
            _collaborationWorkChainRounds.TryGetValue(chainKey, out var currentRound);
            var running = string.Equals(current.Subtitle, "running", StringComparison.OrdinalIgnoreCase);
            // 无轮次键时沿用旧语义（运行中并入、完结即新轮）；有轮次键则以键比对为准，
            // 键相同即同轮（哪怕已完结，收尾事件仍归原卡），键不同即新轮（哪怕完成信号丢失）。
            var sameRound = string.IsNullOrEmpty(roundKey)
                ? running
                : string.IsNullOrEmpty(currentRound) || string.Equals(roundKey, currentRound, StringComparison.OrdinalIgnoreCase);
            if (sameRound)
            {
                if (!string.IsNullOrEmpty(roundKey) && string.IsNullOrEmpty(currentRound))
                {
                    _collaborationWorkChainRounds[chainKey] = roundKey;
                }
                return current;
            }
            if (running)
            {
                FinalizeCollaborationWorkChain(current);
            }
        }
        var roundSeq = _collaborationWorkChainRoundSeq.TryGetValue(chainKey, out var seq) ? seq + 1 : 1;
        _collaborationWorkChainRoundSeq[chainKey] = roundSeq;
        _collaborationWorkChainRounds[chainKey] = roundKey;
        // 轮次片段优先用 message_id（跨重启全局唯一，避免重启后序号归零与已持久化投影卡撞 Id）。
        var roundToken = string.IsNullOrEmpty(roundKey)
            ? $"t{(long)NowSeconds()}n{roundSeq}"
            : NormalizeCollaborationThreadKey(roundKey);
        var message = new DesktopMessage
        {
            Id = $"collab-work-{NormalizeCollaborationThreadKey(threadId)}-{NormalizeCollaborationThreadKey(agentKey)}-{roundToken}",
            Role = "assistant",
            Kind = "work",
            Subtitle = "running",
            WorkAgent = agentKey == "agent" ? "" : CollaborationAgentDisplay(agentId),
            WorkExpanded = true,
            CreatedAt = NowSeconds(),
            UpdatedAt = NowSeconds(),
            Steps = new List<DesktopWorkStep>(),
        };
        cards.Add(message);
        // 源卡只保留近几轮：更早的轮次已投影为会话消息持久保存，源卡淘汰不影响历史展示。
        if (cards.Count > 8)
        {
            cards.RemoveRange(0, cards.Count - 8);
        }
        return message;
    }

    private bool UpsertCollaborationWorkChainsForThread(string threadId)
    {
        if (!TryResolveSessionForCollaborationThread(threadId, out var session))
        {
            return false;
        }
        var threadKey = NormalizeCollaborationThreadKey(threadId);
        var changed = false;
        foreach (var source in CollaborationWorkChainsFor(threadId, ""))
        {
            changed |= UpsertSessionCollaborationWorkChain(session, threadKey, source);
        }
        return changed;
    }

    private bool UpsertSessionCollaborationWorkChain(DesktopSession session, string threadKey, DesktopMessage source)
    {
        // 源卡 Id 已含 agent + 轮次后缀，直接派生投影 Id，保证每轮一张独立投影卡。
        var targetId = $"session-{source.Id}";
        // 旧版单卡（无 agent 后缀）残留清理：分卡后它永远不会再被更新，留着会显示过期内容。
        var legacyId = $"session-collab-work-{threadKey}";
        var legacyRemoved = session.Messages.RemoveAll(message => string.Equals(message.Id, legacyId, StringComparison.OrdinalIgnoreCase)) > 0;
        var projectedSteps = (source.Steps ?? new List<DesktopWorkStep>())
            .Where(step => !IsHumanAgentId(step.AgentId))
            .ToList();
        if (projectedSteps.Count == 0)
        {
            return legacyRemoved;
        }
        var target = session.Messages.FirstOrDefault(message => string.Equals(message.Id, targetId, StringComparison.OrdinalIgnoreCase));
        var changed = legacyRemoved || target is null;
        if (target is null)
        {
            // 卡片迟到而回复已先落：锚到自己回复正上方（回复侧 epsilon=0.0005 的反向），保持配对位置；
            // 否则新一轮的卡锚定到"当前时间线最新一条普通消息之后"（首轮即用户提问之后、
            // 双工后续轮即触发它的那条模型回复之后），并保证不早于已存在的本线程工作卡，维持轮次顺序。
            double anchoredCreatedAt;
            DesktopMessage? pendingReply = null;
            if (_collaborationPendingReplyAnchors.TryGetValue(targetId, out var pendingReplyId))
            {
                pendingReply = session.Messages.FirstOrDefault(message => string.Equals(message.Id, pendingReplyId, StringComparison.OrdinalIgnoreCase));
                _collaborationPendingReplyAnchors.Remove(targetId);
            }
            if (pendingReply is not null)
            {
                anchoredCreatedAt = pendingReply.CreatedAt - 0.0005;
            }
            else
            {
                // 排除流式草稿：草稿是本卡自己的产物（首 token 同步落地、卡等合帧），
                // 计入会把卡锚到自己草稿的下面（批次九顺序跳变根因之一）。
                var latestMessageAt = session.Messages
                    .Where(message => !string.Equals(message.Kind, "work", StringComparison.OrdinalIgnoreCase)
                        && !string.Equals(message.Kind, "collaboration_stream_draft", StringComparison.OrdinalIgnoreCase))
                    .Select(message => message.CreatedAt)
                    .DefaultIfEmpty(0d)
                    .Max();
                var priorWorkAt = session.Messages
                    .Where(message => string.Equals(message.Kind, "work", StringComparison.OrdinalIgnoreCase)
                        && (message.Id ?? "").StartsWith($"session-collab-work-{threadKey}-", StringComparison.OrdinalIgnoreCase))
                    .Select(message => message.CreatedAt)
                    .DefaultIfEmpty(0d)
                    .Max();
                anchoredCreatedAt = latestMessageAt > 0 || priorWorkAt > 0
                    ? Math.Max(latestMessageAt, priorWorkAt) + 0.001
                    : NowSeconds();
            }
            target = new DesktopMessage
            {
                Id = targetId,
                Role = "assistant",
                Kind = "work",
                CreatedAt = anchoredCreatedAt,
                WorkExpanded = true,
            };
            session.Messages.Add(target);
        }
        changed |= !string.Equals(target.Subtitle, source.Subtitle, StringComparison.Ordinal)
            || target.Steps?.Count != projectedSteps.Count
            || Math.Abs(target.DurationSeconds - source.DurationSeconds) > 0.5
            || !(target.Steps ?? new List<DesktopWorkStep>())
                .Zip(projectedSteps, CollaborationProjectionStepEquals)
                .All(equal => equal);
        target.Subtitle = source.Subtitle;
        // Worked for 计时：直接采用源卡（内存权威）的用时与执行者标识，投影卡不再各自为政。
        target.DurationSeconds = source.DurationSeconds;
        target.WorkAgent = source.WorkAgent;
        target.UpdatedAt = NowSeconds();
        target.Steps = projectedSteps.Select(ProjectCollaborationWorkStep).ToList();
        session.UpdatedAt = NowSeconds();
        return changed;
    }

    internal static DesktopWorkStep ProjectCollaborationWorkStep(DesktopWorkStep step) => new()
    {
        Kind = step.Kind,
        Title = step.Title,
        Detail = step.Detail,
        Key = step.Key,
        CreatedAt = step.CreatedAt,
        Seq = step.Seq,
        EventId = step.EventId,
        RunId = step.RunId,
        SpanId = step.SpanId,
        ParentId = step.ParentId,
        Status = step.Status,
        IsTerminal = step.IsTerminal,
        AgentId = step.AgentId,
        IsStreamLane = step.IsStreamLane,
        ReasoningVisibility = step.ReasoningVisibility,
        CallAgent = step.CallAgent,
        CallModel = step.CallModel,
        CallProvider = step.CallProvider,
        CommandText = step.CommandText,
        CommandOutput = step.CommandOutput,
        ShellLabel = step.ShellLabel,
    };

    private static bool CollaborationProjectionStepEquals(DesktopWorkStep current, DesktopWorkStep source) =>
        string.Equals(current.Kind, source.Kind, StringComparison.Ordinal)
        && string.Equals(current.Title, source.Title, StringComparison.Ordinal)
        && string.Equals(current.Detail, source.Detail, StringComparison.Ordinal)
        && string.Equals(current.Status, source.Status, StringComparison.OrdinalIgnoreCase)
        && string.Equals(current.EventId, source.EventId, StringComparison.OrdinalIgnoreCase)
        && string.Equals(current.CallAgent, source.CallAgent, StringComparison.Ordinal)
        && string.Equals(current.CallModel, source.CallModel, StringComparison.Ordinal)
        && string.Equals(current.CallProvider, source.CallProvider, StringComparison.Ordinal)
        && string.Equals(current.CommandText, source.CommandText, StringComparison.Ordinal)
        && string.Equals(current.CommandOutput, source.CommandOutput, StringComparison.Ordinal)
        && string.Equals(current.ShellLabel, source.ShellLabel, StringComparison.Ordinal);

    private static string MapCollaborationStepKind(string eventKind)
    {
        return (eventKind ?? "").Trim().ToLowerInvariant() switch
        {
            "command" => "command",
            "result" => "result",
            "diff" => "diff",
            "permission" => "permission",
            _ => "thinking",
        };
    }

    // 流式通道 → 工作链步骤类型：reasoning=推理链，command=真实命令，edit=文件编辑，
    // lifecycle=模型请求或编排里程碑（叙事行，不算命令），stderr=告警，其余(token/stdout)=结果输出。
    private static string MapCollaborationStreamKind(string stream)
    {
        return (stream ?? "").Trim().ToLowerInvariant() switch
        {
            "stderr" => "permission",
            "reasoning" => "thinking",
            // draft=后位起草正文（v4 没抢到发言席位的草稿），与真思考链分泳道但同为思考类步骤。
            "draft" => "thinking",
            "lifecycle" => "thinking",
            "command" => "command",
            "edit" => "diff",
            _ => "result",
        };
    }

    // 标题不参与状态判断，仅用于显示：按流类型给中性语义标签（执行者已由 agent 徽标显示，避免重复署名）。
    private static string CollaborationWorkStepTitle(JsonElement payload, string stream, string lifecycle)
    {
        switch ((stream ?? "").Trim().ToLowerInvariant())
        {
            case "reasoning":
                return "思考";
            case "draft":
                return "起草";
            case "lifecycle":
                return CollaborationLifecycleStepTitle(lifecycle);
            case "command":
                return "执行命令";
            case "edit":
                return "编辑文件";
            case "stderr":
                return "告警";
            case "token":
            case "stdout":
                return "回复";
        }
        if (!string.IsNullOrWhiteSpace(stream))
        {
            return "输出";
        }
        return string.Equals(ReadJsonString(payload, "kind", "thought"), "command", StringComparison.OrdinalIgnoreCase)
            ? "工作指令"
            : "思考";
    }

    internal static string CollaborationLifecycleStepTitle(string lifecycle)
    {
        return (lifecycle ?? "").Trim().ToLowerInvariant() switch
        {
            "request_started" or "request_completed" or "request_failed" => "调用",
            _ => "思考",
        };
    }

    // 未识别的 lifecycle 保留 worker 原文（英文），识别的换成用户可读的中文节点。
    private static string LocalizeCollaborationLifecycle(string lifecycle, string fallback)
    {
        return (lifecycle ?? "").Trim().ToLowerInvariant() switch
        {
            "queued" => "消息已入队",
            "routed" => "消息已分发",
            "context_loaded" => "已加载协作上下文",
            "prompt_ready" => "提示词已就绪",
            "request_started" => "正在调用模型 API",
            "request_completed" => "模型调用完成",
            "request_failed" => "模型调用失败",
            "process_started" => "外部助手进程已启动",
            "process_exited" => "外部助手进程已退出",
            "process_timeout" => "外部助手进程超时",
            "reply_posting" => "正在发布回复",
            "reply_posted" => "回复已发布",
            "queue_live" => "率先想完，抢到发言席位（现场直播）",
            "queue_wait" => "其他参与者已先发言，转后台起草（其定稿后将修正发言）",
            "queue_context" => "前位已定稿，正结合草稿与定稿修正发言",
            "queue_revision" => "正在根据前位发言重修",
            "queue_revision_failed" => "修正失败，按草稿发布",
            "turn_wait" => "发言权被占用，正并行起草（轮到自己时修正发言）",
            "turn_context" => "已获得发言权，正结合最新定稿撰写发言",
            "turn_revision" => "已获得发言权，正根据最新定稿重修",
            "turn_revision_failed" => "修正失败，按草稿发言",
            "turn_cap_reached" => "双工轮次已达上限，发送一条新消息可继续",
            "turn_hard_cap_reached" => "双工已连续互聊达到硬熔断，发送一条新消息可继续",
            "turn_paused" => "双工已暂停（人工打断）",
            "auto_reply_disabled" => "模型互聊开关已关闭，本条回复未投递",
            "prompt_compacted" => "上下文超限，已压缩提示词重试",
            "empty_stream_response" => "正文为空，改用非流式重试",
            "finalized_from_reasoning" => "正文缺失，已基于思考草稿重新生成回复",
            "retry" => "调用失败，正在重试",
            _ => fallback,
        };
    }

    // 链条完成：补齐停留在 running/started 的步骤徽标、写入用时，避免完成后满屏 RUNNING。
    private bool FinalizeCollaborationWorkChain(DesktopMessage message)
    {
        var changed = false;
        if (!string.Equals(message.Subtitle, "worked", StringComparison.OrdinalIgnoreCase))
        {
            message.Subtitle = "worked";
            changed = true;
        }
        if (message.DurationSeconds <= 0)
        {
            message.DurationSeconds = Math.Max(1, NowSeconds() - message.CreatedAt);
            changed = true;
        }
        foreach (var step in message.Steps ?? new List<DesktopWorkStep>())
        {
            var status = (step.Status ?? "").Trim().ToLowerInvariant();
            if (status is "running" or "started" or "stream" or "queued")
            {
                step.Status = "completed";
                changed = true;
            }
        }
        if (changed)
        {
            message.UpdatedAt = NowSeconds();
        }
        return changed;
    }

    // 定稿补"回复"泳道：后位起草轮（v4 没抢到席位）正文走"起草"泳道、修订成稿不流式，
    // 卡内永远没有"回复"步骤，与前位直播卡结构不一致。权威回复投影时若卡内无回复泳道，
    // 补一步终态成稿，两类卡的 step 结构对齐（调用/思考/[起草]/回复）。
    // 已有"回复"泳道时改为 upsert：卡完结后迟到的流式批被丢弃，泳道文本常停在中途
    //（2026-07-09 实测截图：泳道截断在"脱离目的地"、下方气泡全文完整），
    // 权威全文是唯一可靠终值，比现存泳道长就覆盖，截断即自愈。
    private static bool EnsureCollaborationReplyStep(DesktopMessage message, string content, string agentId)
        => UpsertCollaborationReplyStep(message, content, agentId);

    // internal 供测试直呼（与 TryMergeCollaborationStreamStep 同例）。
    internal static bool UpsertCollaborationReplyStep(DesktopMessage message, string content, string agentId)
    {
        if (string.IsNullOrWhiteSpace(content))
        {
            return false;
        }
        var steps = message.Steps ??= new List<DesktopWorkStep>();
        var existing = steps.FirstOrDefault(step => string.Equals(step.Title, "回复", StringComparison.Ordinal));
        if (existing is not null)
        {
            if ((existing.Detail ?? "").Length >= content.Length
                || string.Equals(existing.Detail, content, StringComparison.Ordinal))
            {
                return false;
            }
            existing.Detail = content;
            existing.Status = "completed";
            message.UpdatedAt = NowSeconds();
            return true;
        }
        steps.Add(new DesktopWorkStep
        {
            Kind = "result",
            Title = "回复",
            Detail = content,
            Status = "completed",
            CreatedAt = NowSeconds(),
            AgentId = agentId ?? "",
        });
        message.UpdatedAt = NowSeconds();
        return true;
    }

    private void SyncCollaborationParticipants(JsonElement state)
    {
        if (!state.TryGetProperty("participants", out var registry) || registry.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        _collaborationParticipantOptions.Clear();
        _collaborationParticipantAliases.Clear();
        if (registry.TryGetProperty("participants", out var participants) && participants.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in participants.EnumerateArray())
            {
                var participantId = ReadJsonString(item, "participant_id").Trim();
                if (string.IsNullOrWhiteSpace(participantId))
                {
                    continue;
                }
                var aliases = ReadJsonStringArray(item, "aliases");
                var mention = ReadJsonString(item, "mention");
                if (string.IsNullOrWhiteSpace(mention))
                {
                    mention = $"@{(aliases.Length > 0 ? aliases[0] : participantId)}";
                }
                var option = new CollaborationParticipantOption(
                    participantId,
                    ReadJsonString(item, "label", participantId),
                    ReadJsonString(item, "kind"),
                    ReadJsonString(item, "status"),
                    mention,
                    ReadJsonBool(item, "can_chat", false),
                    ReadJsonBool(item, "can_execute", false),
                    ReadJsonBool(item, "requires_review", true),
                    aliases);
                _collaborationParticipantOptions.Add(option);
                AddCollaborationParticipantAlias(participantId, participantId);
                AddCollaborationParticipantAlias(mention.TrimStart('@'), participantId);
                foreach (var alias in aliases)
                {
                    AddCollaborationParticipantAlias(alias, participantId);
                }
            }
        }
        AddCollaborationParticipantAlias("all", "all");
        AddCollaborationParticipantAlias("全部", "all");
        AddCollaborationParticipantAlias("所有", "all");
        AddCollaborationParticipantAlias("review", "external_reviewer");
        AddCollaborationParticipantAlias("评审", "external_reviewer");
    }

    private void AddCollaborationParticipantAlias(string alias, string participantId)
    {
        var key = ComposerController.NormalizeAgentMentionKey(alias);
        if (!string.IsNullOrWhiteSpace(key) && !_collaborationParticipantAliases.ContainsKey(key))
        {
            _collaborationParticipantAliases[key] = participantId;
        }
    }

    internal async Task LoadCollaborationAsync()

    {
        try
        {
            using var doc = await GetJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration");
            RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
            await LoadCollaborationTurnGuardAsync(reportErrors: false);
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "协作状态已刷新。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CollaborationSummaryText.Text = $"协作模块加载失败：{ex.Message}";
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "协作模块加载失败。";
        }
    }

    internal async Task TickCollaborationSyncTimerAsync()
    {
        if (_collaborationSyncInFlight || (!_collaborationChatActive && WorkbenchShell.ManagementPanels.CollaborationPanel.Visibility != Visibility.Visible))
        {
            return;
        }
        _collaborationSyncInFlight = true;
        try
        {
            using var doc = await GetJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration");
            RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
            await LoadCollaborationTurnGuardAsync(reportErrors: false);
        }
        catch
        {
            // Keep background sync quiet; manual refresh still reports errors in the panel.
        }
        finally
        {
            _collaborationSyncInFlight = false;
        }
    }

    internal async Task CreateCollaborationTaskAsync()
    {
        var taskId = EnsureCollaborationTaskId();
        var title = WorkbenchShell.ManagementPanels.CollaborationTaskTitleBox.Text.Trim();
        var owner = CollaborationOwner();
        if (string.IsNullOrWhiteSpace(title))
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "请先填写任务标题。";
            return;
        }

        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action = "create_task",
            task_id = taskId,
            title,
            owner,
            status = "active",
            scope = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationTaskScopeBox.Text),
            allowed_files = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationAllowedFilesBox.Text),
            blocked_files = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationBlockedFilesBox.Text),
            verification_commands = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationVerificationBox.Text),
            note = WorkbenchShell.ManagementPanels.CollaborationTaskNoteBox.Text.Trim(),
        });
        EnsureOkResponse(doc.RootElement, "create collaboration task");
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"已创建协作任务：{ReadJsonString(doc.RootElement.GetProperty("task"), "task_id")}";
    }

    // 双工（模型互聊）开关：桌面 CheckBox <-> 网关 collaboration_auto_reply action（文件持久化，worker 同读）。
    private bool _syncingCollaborationAutoReply;
    private bool _syncingCollaborationTurnCap;
    // 用户每次手动切换开关 +1。网关冷启动时 Loaded 期的 get 回读可挂数十秒，
    // 若用户已在等待期间点开开关，迟到响应携带旧状态落地会把开关"按回去"（2026-07-09 实测：
    // 点开双工自动关）。回读前快照该序号，返回后序号已变即丢弃响应。
    private long _collaborationAutoReplyStamp;

    internal async Task LoadCollaborationAutoReplyAsync()
    {
        var stamp = _collaborationAutoReplyStamp;
        try
        {
            using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
            {
                action = "collaboration_auto_reply",
                op = "get",
                include_collaboration = false,
            });
            EnsureOkResponse(doc.RootElement, "get collaboration auto reply");
            if (stamp != _collaborationAutoReplyStamp)
            {
                return; // 等待期间用户已手动切换：迟到的旧状态作废。
            }
            var (enabled, source) = ReadCollaborationAutoReplyState(doc.RootElement);
            _syncingCollaborationAutoReply = true;
            try
            {
                ChatWorkspace.ChatDuplexToggle.IsChecked = enabled;
            }
            finally
            {
                _syncingCollaborationAutoReply = false;
            }
            if (string.Equals(source, "env", StringComparison.OrdinalIgnoreCase))
            {
                WorkbenchShell.ManagementPanels.CollaborationActionText.Text =
                    "双工开关当前由环境变量 SPIRITKIN_COLLABORATION_AUTO_REPLY 锁定，桌面切换不会生效。";
            }
        }
        catch
        {
            // 网关未就绪时静默；下次打开面板或重试时再回填。
        }
    }

    // 解析网关 auto_reply 状态：enabled 以响应为准（env 覆盖文件时请求值会失真），source 供锁定提示。
    private static (bool Enabled, string Source) ReadCollaborationAutoReplyState(JsonElement root)
    {
        if (!root.TryGetProperty("auto_reply", out var state) || state.ValueKind != JsonValueKind.Object)
        {
            return (false, "");
        }
        var enabled = state.TryGetProperty("enabled", out var flag) && flag.ValueKind == JsonValueKind.True;
        var source = state.TryGetProperty("source", out var src) && src.ValueKind == JsonValueKind.String
            ? src.GetString() ?? ""
            : "";
        return (enabled, source);
    }

    internal async Task SetCollaborationAutoReplyAsync(bool enabled)
    {
        if (_syncingCollaborationAutoReply)
        {
            return;
        }
        _collaborationAutoReplyStamp++;
        try
        {
            using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
            {
                action = "collaboration_auto_reply",
                op = "set",
                enabled,
                include_collaboration = false,
            });
            EnsureOkResponse(doc.RootElement, "set collaboration auto reply");
            // 回显以响应为准而非请求值：env 变量覆盖文件时，写入成功但实际状态未变，
            // 用请求值回显会掩盖锁定、下次回读又弹回（"开了又自动关"的另一半根因）。
            var (actual, source) = ReadCollaborationAutoReplyState(doc.RootElement);
            _syncingCollaborationAutoReply = true;
            try
            {
                ChatWorkspace.ChatDuplexToggle.IsChecked = actual;
            }
            finally
            {
                _syncingCollaborationAutoReply = false;
            }
            if (string.Equals(source, "env", StringComparison.OrdinalIgnoreCase) && actual != enabled)
            {
                WorkbenchShell.ManagementPanels.CollaborationActionText.Text =
                    "双工开关被环境变量 SPIRITKIN_COLLABORATION_AUTO_REPLY 锁定，桌面切换无效；请清除该变量后重启网关。";
                return;
            }
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = actual
                ? "模型互聊（双工）已开启：模型之间可自动回复，受轮次上限保护。"
                : "模型互聊（双工）已关闭：模型只回复人类消息。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"双工开关设置失败：{ex.Message}";
        }
    }

    internal async Task LoadCollaborationTurnGuardAsync(bool reportErrors = true)
    {
        try
        {
            var threadId = CurrentSessionCollaborationThreadId();
            using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
            {
                action = "turn_guard_status",
                thread_id = threadId,
                include_collaboration = false,
            });
            EnsureOkResponse(doc.RootElement, "get collaboration turn guard");
            RefreshCollaborationTurnGuardDisplay(doc.RootElement);
        }
        catch (Exception ex)
        {
            // 后台 3 秒 tick 的瞬时失败不清空显示，否则余额文字在"真值 ↔ --/--"间来回闪；
            // 只有手动刷新（reportErrors=true）才把失败显式亮出来。
            if (reportErrors)
            {
                WorkbenchShell.ManagementPanels.CollaborationTurnGuardText.Text = "双工余额 --/--";
                WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"双工余额刷新失败：{ex.Message}";
            }
        }
    }

    private void RefreshCollaborationTurnGuardDisplay(JsonElement root)
    {
        if (!root.TryGetProperty("turn_guard", out var guard) || guard.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        var defaultCap = ReadJsonInt(guard, "default_cap");
        var defaultHardCap = ReadJsonInt(guard, "default_hard_cap");
        if (guard.TryGetProperty("thread", out var thread) && thread.ValueKind == JsonValueKind.Object)
        {
            var remaining = ReadJsonInt(thread, "remaining");
            var cap = ReadJsonInt(thread, "cap");
            var hardCap = ReadJsonInt(thread, "hard_cap");
            var continuous = ReadJsonInt(thread, "continuous_auto_turns");
            var status = ReadJsonString(thread, "status", "active");
            var budget = cap > 0 ? $"{remaining}/{cap}" : "持续";
            var hard = hardCap > 0 ? $" · 硬熔断 {continuous}/{hardCap}" : "";
            WorkbenchShell.ManagementPanels.CollaborationTurnGuardText.Text = $"双工余额 {budget}{hard} · {CollaborationTurnGuardStatusLabel(status)}";
            var statusLabel = CollaborationTurnGuardStatusLabel(status);
            var headerStatus = statusLabel == "运行中" ? "" : $" · {statusLabel}";
            // 头部余额始终给数字。不限模式（cap=0）此前显示"余额 39/40（熔断前）"，
            // 用户读作"余额记账错了"（2026-07-09 实测反馈）——改为正向计数"连续互聊 1/40"，
            // 语义与后端 continuous_auto_turns 一致，人类发言清零属预期而非跳变。
            var headerBudget = cap > 0
                ? $"余额 {remaining}/{cap}"
                : hardCap > 0 ? $"连续互聊 {continuous}/{hardCap}" : "余额 不限";
            ChatWorkspace.ChatDuplexBalanceText.Text = $"{headerBudget}{headerStatus}";
            ChatWorkspace.ChatDuplexBalanceText.ToolTip = cap > 0
                ? "本会话双工剩余轮次；人类发言会自动续杯。"
                : hardCap > 0
                    ? $"模型连续互聊计数，达到 {hardCap} 轮自动熔断防止刷屏；你发一条消息即清零重新计数。"
                    : "双工轮次不限。";
        }
        else
        {
            var total = ReadJsonInt(guard, "total_threads");
            var budget = defaultCap > 0 ? $"--/{defaultCap}" : "持续";
            var hard = defaultHardCap > 0 ? $" · 硬熔断 {defaultHardCap}" : "";
            WorkbenchShell.ManagementPanels.CollaborationTurnGuardText.Text = $"双工余额 {budget}{hard} · 已跟踪 {total} 个线程";
            ChatWorkspace.ChatDuplexBalanceText.Text = defaultCap > 0 ? $"余额 --/{defaultCap}" : "余额 --";
        }
        var capText = defaultCap > 0 ? defaultCap.ToString(CultureInfo.InvariantCulture) : "0";
        var capBox = WorkbenchShell.ManagementPanels.CollaborationTurnCapBox;
        // 3 秒同步 tick 会反复回填此框：用户正在输入（焦点在框内）或值未变时不动它，
        // 否则输入被清、光标复位，面板看起来"跳来跳去"。
        if (capBox.IsKeyboardFocusWithin || string.Equals(capBox.Text.Trim(), capText, StringComparison.Ordinal))
        {
            return;
        }
        _syncingCollaborationTurnCap = true;
        try
        {
            capBox.Text = capText;
        }
        finally
        {
            _syncingCollaborationTurnCap = false;
        }
    }

    internal async Task SaveCollaborationTurnCapFromPanelAsync()
    {
        if (_syncingCollaborationTurnCap)
        {
            return;
        }
        var raw = WorkbenchShell.ManagementPanels.CollaborationTurnCapBox.Text.Trim();
        var cap = 0;
        if (!string.IsNullOrWhiteSpace(raw) && (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out cap) || cap < 0))
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "双工轮次上限必须是 0 或正整数；0/空表示持续互聊。";
            return;
        }
        Environment.SetEnvironmentVariable("SPIRITKIN_COLLABORATION_TURN_CAP", cap.ToString(CultureInfo.InvariantCulture), EnvironmentVariableTarget.Process);
        var threadId = CurrentSessionCollaborationThreadId();
        try
        {
            using var doc = await PostJsonAsync(
                $"{_workspaceController.ApiBase()}/desktop/collaboration",
                BuildCollaborationTurnCapPayload(threadId, cap));
            EnsureOkResponse(doc.RootElement, "set collaboration turn cap");
            RefreshCollaborationTurnGuardDisplay(doc.RootElement);
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = cap > 0
                ? $"双工轮次上限已设为 {cap}，当前会话即时生效。"
                : "双工轮次上限已设为持续互聊（0），当前会话即时生效。";
        }
        catch (Exception ex)
        {
            var budget = cap > 0 ? cap.ToString(CultureInfo.InvariantCulture) : "持续";
            WorkbenchShell.ManagementPanels.CollaborationTurnGuardText.Text = $"双工余额 --/{budget} · 当前会话应用失败";
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"双工轮次上限已写入本进程环境，但当前会话应用失败：{ex.Message}";
        }
    }

    internal async Task PauseCollaborationTurnsAsync()
    {
        var threadId = CurrentSessionCollaborationThreadId();
        if (string.IsNullOrWhiteSpace(threadId))
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "当前没有可暂停的协作线程。";
            return;
        }
        try
        {
            using var doc = await PostJsonAsync(
                $"{_workspaceController.ApiBase()}/desktop/collaboration",
                BuildCollaborationTurnPausePayload(threadId));
            EnsureOkResponse(doc.RootElement, "pause collaboration turns");
            RefreshCollaborationTurnGuardDisplay(doc.RootElement);
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "双工已暂停：当前正在生成的一条会自然收尾，后续模型互聊不再启动。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"停止双工失败：{ex.Message}";
        }
    }

    private static string CollaborationTurnGuardStatusLabel(string status)
    {
        return (status ?? "").Trim().ToLowerInvariant() switch
        {
            "awaiting_refill" => "等待人类续轮",
            "active" => "运行中",
            _ => string.IsNullOrWhiteSpace(status) ? "未知" : status,
        };
    }

    internal async Task ClaimCollaborationFilesAsync()
    {
        var patterns = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationClaimPatternsBox.Text);
        if (patterns.Length == 0)
        {
            patterns = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationAllowedFilesBox.Text);
        }
        if (patterns.Length == 0)
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "请先填写文件模式。";
            return;
        }

        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action = "claim_files",
            task_id = CurrentCollaborationTaskId(),
            owner = CollaborationOwner(),
            patterns,
            note = WorkbenchShell.ManagementPanels.CollaborationTaskNoteBox.Text.Trim(),
        });
        EnsureOkResponse(doc.RootElement, "claim collaboration files");
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "已记录文件占用。";
    }

    internal async Task RecordCollaborationDecisionAsync()
    {
        var title = WorkbenchShell.ManagementPanels.CollaborationDecisionTitleBox.Text.Trim();
        var decision = WorkbenchShell.ManagementPanels.CollaborationDecisionBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(title) && string.IsNullOrWhiteSpace(decision))
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "请先填写决策标题或内容。";
            return;
        }

        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action = "record_decision",
            task_id = CurrentCollaborationTaskId(),
            title = string.IsNullOrWhiteSpace(title) ? "Desktop collaboration decision" : title,
            decision,
            rationale = WorkbenchShell.ManagementPanels.CollaborationDecisionRationaleBox.Text.Trim(),
            actor = CollaborationOwner(),
        });
        EnsureOkResponse(doc.RootElement, "record collaboration decision");
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "已记录协作决策。";
    }

    internal async Task RecordCollaborationReviewAsync()
    {
        var summary = WorkbenchShell.ManagementPanels.CollaborationReviewSummaryBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(summary))
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "请先填写评审摘要。";
            return;
        }

        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action = "record_review",
            task_id = CurrentCollaborationTaskId(),
            reviewer = WorkbenchShell.ManagementPanels.CollaborationReviewReviewerBox.Text.Trim(),
            verdict = ComboText(WorkbenchShell.ManagementPanels.CollaborationReviewVerdictBox),
            summary,
            evidence = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationReviewEvidenceBox.Text),
        });
        EnsureOkResponse(doc.RootElement, "record collaboration review");
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "已记录协作评审。";
    }

    internal async Task BuildCollaborationContextPackAsync()
    {
        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action = "build_context_pack",
            task_id = CurrentCollaborationTaskId(),
            include_files = LinesFromText(WorkbenchShell.ManagementPanels.CollaborationContextFilesBox.Text),
            max_chars_per_file = 3000,
        });
        EnsureOkResponse(doc.RootElement, "build collaboration context pack");
        if (doc.RootElement.TryGetProperty("context_pack", out var contextPack))
        {
            WorkbenchShell.ManagementPanels.CollaborationContextPackPathBox.Text = ReadJsonString(contextPack, "pack_path");
        }
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "已生成协作上下文包。";
    }

    private void RenderCollaborationState(JsonElement state)
    {
        SyncCollaborationParticipants(state);
        SyncPendingCollaborationToolCall(state);
        var stateSignature = BuildCollaborationStateSignature(state);
        if (string.Equals(stateSignature, _lastCollaborationStateSignature, StringComparison.Ordinal))
        {
            var projected = ProjectCollaborationMessagesIntoActiveSession(state);
            RenderCollaborationThreads();
            if (_collaborationChatActive)
            {
                RenderActiveMessages(_workspaceController.ActiveSession());
                SyncQuickChatLayout(_workspaceController.ActiveSession());
            }
            else
            {
                RenderCollaborationChatMessagesIfChanged();
            }
            CommitActiveSessionCollaborationProjection(projected);
            return;
        }
        _lastCollaborationStateSignature = stateSignature;

        var overview = state.GetProperty("overview");
        WorkbenchShell.ManagementPanels.CollaborationSummaryText.Text =
            $"根目录：{ReadJsonString(state, "root")}{Environment.NewLine}" +
            $"任务：{ReadJsonInt(overview, "active_task_count")} active / {ReadJsonInt(overview, "task_count")} total · " +
            $"文件占用：{ReadJsonInt(overview, "active_file_claim_count")} · " +
            $"消息：{ReadJsonInt(overview, "unread_message_count")} unread / {ReadJsonInt(overview, "message_count")} total · " +
            $"决策：{ReadJsonInt(overview, "decision_count")} · 评审：{ReadJsonInt(overview, "review_count")}";

        CollaborationTasks.Clear();
        if (state.TryGetProperty("active_tasks", out var tasks) && tasks.ValueKind == JsonValueKind.Array)
        {
            foreach (var task in tasks.EnumerateArray().Reverse())
            {
                var taskId = ReadJsonString(task, "task_id");
                var taskStatus = ReadJsonString(task, "status", "active");
                var taskOwner = ReadJsonString(task, "owner", "unassigned");
                var taskTitle = FriendlyCollaborationText(ReadJsonString(task, "title", taskId));
                var taskNote = FriendlyCollaborationText(ReadJsonString(task, "note"));
                var taskMeta = $"{CollaborationTaskStatusLabel(taskStatus)} · {CollaborationAgentDisplay(taskOwner)}";
                if (!string.IsNullOrWhiteSpace(taskNote))
                {
                    taskMeta += $"{Environment.NewLine}{ShortText(taskNote, 160)}";
                }
                CollaborationTasks.Add(new ActionItemViewModel(
                    taskId,
                    string.IsNullOrWhiteSpace(taskTitle) ? taskId : taskTitle,
                    taskMeta,
                    "collaboration_task",
                    JsonSerializer.Serialize(task, _jsonOptions),
                    taskId,
                    taskStatus,
                    "",
                    taskOwner));
            }
        }

        CollaborationMessages.Clear();
        _collaborationThreadStatuses.Clear();
        if (state.TryGetProperty("thread_states", out var threadStates) && threadStates.ValueKind == JsonValueKind.Object)
        {
            foreach (var threadState in threadStates.EnumerateObject())
            {
                if (threadState.Value.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                var threadId = ReadJsonString(threadState.Value, "thread_id", threadState.Name);
                var status = ReadJsonString(threadState.Value, "status", "active");
                if (!string.IsNullOrWhiteSpace(threadId))
                {
                    _collaborationThreadStatuses[threadId] = status;
                }
            }
        }
        var projectedMessages = false;
        if (state.TryGetProperty("recent_messages", out var messages) && messages.ValueKind == JsonValueKind.Array)
        {
            PrimeCollaborationToolResultOrigins(messages);
            foreach (var message in messages.EnumerateArray().Reverse().Take(80))
            {
                var id = ReadJsonString(message, "message_id");
                var taskId = ReadJsonString(message, "task_id");
                var fromAgent = ReadJsonString(message, "from_agent", ReadJsonString(message, "from_model", "unknown"));
                var toAgents = ReadJsonStringArray(message, "to_agents");
                var toLabel = toAgents.Length == 0 ? ReadJsonString(message, "to_model", "all") : string.Join(", ", toAgents);
                var threadId = ReadJsonString(message, "thread_id", taskId);
                if (IsDeletedCollaborationThread(threadId))
                {
                    continue;
                }
                var status = ReadJsonString(message, "status", "open");
                var role = ReadJsonString(message, "role", "note");
                var content = CollaborationDisplayContent(ReadJsonString(message, "content"), 360);
                projectedMessages |= ProjectCollaborationMessageIntoActiveSession(message);
                if (IsCollaborationExecutorAgentId(fromAgent))
                {
                    continue;
                }
                CollaborationMessages.Add(new ActionItemViewModel(
                    id,
                    CollaborationMessageTitle(fromAgent, role),
                    content,
                    "collaboration_message",
                    ReadJsonString(message, "context_pack_path"),
                    threadId,
                    status,
                    toLabel,
                    fromAgent,
                    role,
                    CreatedAt: ReadJsonDouble(message, "created_at")));
            }
        }

        CollaborationClaims.Clear();
        if (state.TryGetProperty("file_claims", out var claims) && claims.ValueKind == JsonValueKind.Array)
        {
            foreach (var claim in claims.EnumerateArray().Reverse())
            {
                var claimId = ReadJsonString(claim, "claim_id");
                CollaborationClaims.Add(new ActionItemViewModel(
                    claimId,
                    $"{ReadJsonString(claim, "owner", "unknown")} · {ReadJsonString(claim, "task_id")}",
                    $"{JoinJsonArray(claim, "patterns", 5)}{Environment.NewLine}{ReadJsonString(claim, "note")}".Trim(),
                    "collaboration_claim",
                    "",
                    claimId));
            }
        }

        CollaborationDecisions.Clear();
        if (state.TryGetProperty("recent_decisions", out var decisions) && decisions.ValueKind == JsonValueKind.Array)
        {
            foreach (var decision in decisions.EnumerateArray().Reverse().Take(20))
            {
                var id = ReadJsonString(decision, "decision_id");
                CollaborationDecisions.Add(new ActionItemViewModel(
                    id,
                    $"{ReadJsonString(decision, "title", "Decision")} · {ReadJsonString(decision, "actor", "unknown")}",
                    $"{ReadJsonString(decision, "decision")}{Environment.NewLine}{ReadJsonString(decision, "rationale")}".Trim(),
                    "collaboration_decision",
                    "",
                    id));
            }
        }

        CollaborationReviews.Clear();
        if (state.TryGetProperty("recent_reviews", out var reviews) && reviews.ValueKind == JsonValueKind.Array)
        {
            foreach (var review in reviews.EnumerateArray().Reverse().Take(20))
            {
                var id = ReadJsonString(review, "review_id");
                CollaborationReviews.Add(new ActionItemViewModel(
                    id,
                    $"{ReadJsonString(review, "verdict", "comment")} · {ReadJsonString(review, "reviewer", "unknown")}",
                    $"{ReadJsonString(review, "task_id")}{Environment.NewLine}{ReadJsonString(review, "summary")}".Trim(),
                    "collaboration_review",
                    "",
                    id));
            }
        }

        if (state.TryGetProperty("source_files", out var sourceFiles) && string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.CollaborationContextPackPathBox.Text))
        {
            WorkbenchShell.ManagementPanels.CollaborationContextPackPathBox.Text = ReadJsonString(sourceFiles, "context_packs");
        }
        RenderCollaborationThreads();
        if (_collaborationChatActive)
        {
            RenderActiveMessages(_workspaceController.ActiveSession());
            SyncQuickChatLayout(_workspaceController.ActiveSession());
        }
        else
        {
            RenderCollaborationChatMessagesIfChanged();
        }
        CommitActiveSessionCollaborationProjection(projectedMessages);
    }

    private void SyncPendingCollaborationToolCall(JsonElement state)
    {
        if (!_collaborationChatActive
            || !state.TryGetProperty("agent_route_bus", out var routeBus)
            || routeBus.ValueKind != JsonValueKind.Object
            || !routeBus.TryGetProperty("recent_tool_calls", out var toolCalls)
            || toolCalls.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        var activeThread = CurrentSessionCollaborationThreadId();
        JsonElement? pending = null;
        var currentCallObservedResolved = false;
        foreach (var toolCall in toolCalls.EnumerateArray().Reverse())
        {
            if (toolCall.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            var candidateId = ReadJsonString(toolCall, "tool_call_id");
            var status = ReadJsonString(toolCall, "status");
            if (!ReadJsonString(toolCall, "context_id").Equals(activeThread, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (!string.IsNullOrWhiteSpace(_pendingCollaborationToolCallId)
                && string.Equals(candidateId, _pendingCollaborationToolCallId, StringComparison.OrdinalIgnoreCase)
                && !status.Equals("permission_required", StringComparison.OrdinalIgnoreCase))
            {
                currentCallObservedResolved = true;
                _resolvedCollaborationToolCallIds.Add(candidateId);
            }
            if (!status.Equals("permission_required", StringComparison.OrdinalIgnoreCase)
                || _resolvedCollaborationToolCallIds.Contains(candidateId)
                || string.Equals(_collaborationToolDecisionInFlightId, candidateId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            pending = toolCall;
            break;
        }
        if (pending is not { } call)
        {
            var currentCallIsSettled = currentCallObservedResolved
                || _resolvedCollaborationToolCallIds.Contains(_pendingCollaborationToolCallId)
                || string.Equals(_collaborationToolDecisionInFlightId, _pendingCollaborationToolCallId, StringComparison.OrdinalIgnoreCase);
            var pendingAge = _pendingCollaborationToolCreatedAt > 0
                ? Math.Max(0, NowSeconds() - _pendingCollaborationToolCreatedAt)
                : 0;
            if (!string.IsNullOrWhiteSpace(_pendingCollaborationToolCallId)
                && !currentCallIsSettled
                && pendingAge < 600)
            {
                // Some streaming snapshots briefly omit recent_tool_calls. Keep the
                // current gate stable until its own record reaches a settled state.
                return;
            }
            if (_state.Pending is not null
                && _state.Pending.TryGetValue("source", out var source)
                && string.Equals(Convert.ToString(source), "collaboration_tool_call", StringComparison.OrdinalIgnoreCase))
            {
                _state.Pending = null;
            }
            _pendingCollaborationToolCallId = "";
            _pendingCollaborationToolTarget = "";
            _pendingCollaborationToolOperation = "";
            _pendingCollaborationToolCreatedAt = 0;
            return;
        }

        var callId = ReadJsonString(call, "tool_call_id");
        if (string.Equals(_pendingCollaborationToolCallId, callId, StringComparison.OrdinalIgnoreCase)
            && _state.Pending is not null
            && _state.Pending.TryGetValue("tool_call_id", out var currentId)
            && string.Equals(Convert.ToString(currentId), callId, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        _pendingCollaborationToolCallId = callId;
        _pendingCollaborationToolTarget = ReadJsonString(call, "target", "local_pc");
        _pendingCollaborationToolOperation = ReadJsonString(call, "operation", "execute");
        _pendingCollaborationToolCreatedAt = ReadJsonDouble(call, "created_at");
        if (_pendingCollaborationToolCreatedAt <= 0)
        {
            _pendingCollaborationToolCreatedAt = NowSeconds();
        }
        _state.Pending = new Dictionary<string, object?>
        {
            ["source"] = "collaboration_tool_call",
            ["tool_call_id"] = _pendingCollaborationToolCallId,
            ["target"] = _pendingCollaborationToolTarget,
            ["operation"] = _pendingCollaborationToolOperation,
            ["risk_level"] = "high",
            ["created_at"] = _pendingCollaborationToolCreatedAt,
        };
    }

    internal async Task<bool> TryHandlePendingCollaborationToolCallAsync(string controlText)
    {
        if (!_collaborationChatActive || string.IsNullOrWhiteSpace(_pendingCollaborationToolCallId))
        {
            return false;
        }
        var normalized = (controlText ?? "").Trim().ToLowerInvariant();
        var approve = normalized.Contains("确认", StringComparison.OrdinalIgnoreCase)
            || normalized is "yes" or "y" or "approve" or "approved";
        var deny = normalized.Contains("取消", StringComparison.OrdinalIgnoreCase)
            || normalized.Contains("拒绝", StringComparison.OrdinalIgnoreCase)
            || normalized is "no" or "n" or "deny" or "denied";
        if (!approve && !deny)
        {
            return false;
        }

        var toolCallId = _pendingCollaborationToolCallId;
        var toolTarget = _pendingCollaborationToolTarget;
        var toolOperation = _pendingCollaborationToolOperation;
        _collaborationToolDecisionInFlightId = toolCallId;
        _state.Pending = null;
        RenderState();
        JsonElement? collaborationState = null;
        try
        {
            using (var decision = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
            {
                action = "decide_tool_call",
                tool_call_id = toolCallId,
                decision = approve ? "approved" : "denied",
                actor = "human_desktop",
                execute_on_approve = approve,
            }))
            {
                EnsureOkResponse(decision.RootElement, "decide collaboration tool call");
                AppendCollaborationWorkEventsFromResponse(decision.RootElement);
                if (decision.RootElement.TryGetProperty("collaboration", out var collaboration))
                {
                    collaborationState = collaboration.Clone();
                }
            }

            if (_resolvedCollaborationToolCallIds.Count >= 256)
            {
                _resolvedCollaborationToolCallIds.Clear();
            }
            _resolvedCollaborationToolCallIds.Add(toolCallId);
            if (collaborationState is { } state)
            {
                RenderCollaborationState(state);
            }
            else
            {
                await LoadCollaborationAsync();
            }
        }
        catch
        {
            _collaborationToolDecisionInFlightId = "";
            _pendingCollaborationToolCallId = toolCallId;
            _pendingCollaborationToolTarget = toolTarget;
            _pendingCollaborationToolOperation = toolOperation;
            _state.Pending = new Dictionary<string, object?>
            {
                ["source"] = "collaboration_tool_call",
                ["tool_call_id"] = toolCallId,
                ["target"] = toolTarget,
                ["operation"] = toolOperation,
                ["risk_level"] = "high",
                ["created_at"] = _pendingCollaborationToolCreatedAt > 0 ? _pendingCollaborationToolCreatedAt : NowSeconds(),
            };
            RenderState();
            throw;
        }

        var actionText = approve ? "已确认并执行" : "已拒绝";
        ChatWorkspace.SendStatusText.Text = $"{actionText}协作工具调用：{toolTarget}.{toolOperation}";
        ChatWorkspace.SendStatusText.Visibility = Visibility.Visible;
        _collaborationToolDecisionInFlightId = "";
        _pendingCollaborationToolCallId = "";
        _pendingCollaborationToolTarget = "";
        _pendingCollaborationToolOperation = "";
        _pendingCollaborationToolCreatedAt = 0;
        _state.Pending = null;
        RenderState();
        return true;
    }

    private bool ProjectCollaborationMessagesIntoActiveSession(JsonElement state)
    {
        if (!state.TryGetProperty("recent_messages", out var messages)
            || messages.ValueKind != JsonValueKind.Array)
        {
            return false;
        }
        PrimeCollaborationToolResultOrigins(messages);
        var changed = false;
        foreach (var session in _state.Sessions)
        {
            changed |= NormalizeLegacyCollaborationExecutorArtifacts(session);
        }
        foreach (var message in messages.EnumerateArray().Reverse().Take(80))
        {
            changed |= ProjectCollaborationMessageIntoActiveSession(message);
        }
        return changed;
    }

    private void PrimeCollaborationToolResultOrigins(JsonElement messages)
    {
        foreach (var message in messages.EnumerateArray())
        {
            var fromAgent = ReadJsonString(message, "from_agent", ReadJsonString(message, "from_model", ""));
            if (!IsCollaborationExecutorAgentId(fromAgent))
            {
                continue;
            }
            var messageId = ReadJsonString(message, "message_id");
            var parentMessageId = ReadJsonString(message, "parent_message_id");
            if (!string.IsNullOrWhiteSpace(messageId) && !string.IsNullOrWhiteSpace(parentMessageId))
            {
                _collaborationToolResultOrigins[messageId] = parentMessageId;
            }
        }
    }

    internal bool ProjectCollaborationMessagesIntoActiveSessionFromCache()
    {
        if (!_collaborationChatActive)
        {
            return false;
        }
        var changed = false;
        var threadId = CurrentSessionCollaborationThreadId();
        if (!TryResolveSessionForCollaborationThread(threadId, out var session))
        {
            return false;
        }
        foreach (var item in CollaborationMessages.Where(item => string.Equals(item.Target, threadId, StringComparison.OrdinalIgnoreCase)))
        {
            var desktopMessageId = $"collab-reply-{item.Id}";
            if (IsCollaborationExecutorAgentId(item.OwnerRole))
            {
                changed |= session.Messages.RemoveAll(message => string.Equals(message.Id, desktopMessageId, StringComparison.OrdinalIgnoreCase)) > 0;
                continue;
            }
            if (IsHumanCollaborationMessage(item) || string.IsNullOrWhiteSpace(item.Meta))
            {
                continue;
            }
            var existing = session.Messages.FirstOrDefault(message => string.Equals(message.Id, desktopMessageId, StringComparison.OrdinalIgnoreCase));
            var timestamp = NowSeconds();
            // 发言人标识：与 ProjectCollaborationMessageToSession 一致用 agent 展示名，
            // 否则两条投影路径会在 agent 名和 item.Type 之间来回覆盖 Subtitle。
            var subtitle = AgentRouteLabel(item);
            if (existing is null)
            {
                // 锚点与权威路径同源（配对工作卡 CreatedAt+0.0005）：缓存路径若先用"现在"插入（落时间线尾部），
                // 权威路径随后改写锚点会让回复气泡从底部跳回卡片下方——发言顺序肉眼跳变。
                // 锚点 miss 时回退消息真实 created_at（批次九补：此前退 NowSeconds() 仍甩到时间线末尾）。
                var anchored = TryAnchorCachedCollaborationReply(session, threadId, item)
                    ?? (item.CreatedAt > 0 ? item.CreatedAt : timestamp);
                session.Messages.Add(new DesktopMessage
                {
                    Id = desktopMessageId,
                    Role = "assistant",
                    Text = item.Meta,
                    Subtitle = subtitle,
                    CreatedAt = anchored,
                    UpdatedAt = timestamp,
                });
                session.UpdatedAt = Math.Max(session.UpdatedAt, timestamp);
                changed = true;
            }
            else if (!string.Equals(existing.Text, item.Meta, StringComparison.Ordinal)
                || !string.Equals(existing.Subtitle, subtitle, StringComparison.Ordinal))
            {
                existing.Text = item.Meta;
                existing.Subtitle = subtitle;
                existing.UpdatedAt = timestamp;
                session.UpdatedAt = Math.Max(session.UpdatedAt, timestamp);
                changed = true;
            }
        }
        foreach (var workChain in CollaborationWorkChainsFor(threadId, ""))
        {
            changed |= UpsertSessionCollaborationWorkChain(session, NormalizeCollaborationThreadKey(threadId), workChain);
        }
        return changed;
    }

    // 缓存投影的配对锚点：worker 回复 Id 固定为 reply-{agent}-{parent}，据此反推配对工作卡
    //（与权威路径 ProjectCollaborationMessageToSession 同一 Id 规则），命中则用同一锚点
    // workCard.CreatedAt+0.0005——两条投影路径赋值一致，权威路径的补正成为无操作，消息不再跳位。
    private double? TryAnchorCachedCollaborationReply(DesktopSession session, string threadId, ActionItemViewModel item)
    {
        var fromAgent = (item.OwnerRole ?? "").Trim();
        var replyId = (item.Id ?? "").Trim();
        if (string.IsNullOrEmpty(fromAgent) || string.IsNullOrEmpty(replyId))
        {
            return null;
        }
        var agentKey = NormalizeCollaborationThreadKey(NormalizeCollaborationWorkAgentKey(fromAgent));
        var prefix = $"reply-{agentKey}-";
        if (!replyId.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) || replyId.Length <= prefix.Length)
        {
            return null;
        }
        var parentKey = NormalizeCollaborationThreadKey(replyId[prefix.Length..]);
        var workCardId = $"session-collab-work-{NormalizeCollaborationThreadKey(threadId)}-{agentKey}-{parentKey}";
        var workCard = session.Messages.FirstOrDefault(message => string.Equals(message.Id, workCardId, StringComparison.OrdinalIgnoreCase));
        return workCard is null ? null : workCard.CreatedAt + 0.0005;
    }

    private bool ProjectCollaborationMessageIntoActiveSession(JsonElement message)
    {
        var threadId = ReadJsonString(message, "thread_id", ReadJsonString(message, "task_id"));
        if (string.IsNullOrWhiteSpace(threadId))
        {
            return false;
        }
        return ProjectCollaborationMessageToSession(message, threadId);
    }

    private bool ProjectCollaborationMessageToSession(JsonElement message, string threadId)
    {
        var fromAgent = ReadJsonString(message, "from_agent", ReadJsonString(message, "from_model", "unknown"));
        if (IsHumanAgentId(fromAgent))
        {
            return false;
        }
        var messageId = ReadJsonString(message, "message_id");
        if (string.IsNullOrWhiteSpace(messageId))
        {
            return false;
        }
        if (!TryResolveSessionForCollaborationThread(threadId, out var session))
        {
            return false;
        }
        var desktopMessageId = $"collab-reply-{messageId}";
        if (IsCollaborationExecutorAgentId(fromAgent))
        {
            var executorOriginMessageId = ReadJsonString(message, "parent_message_id");
            if (!string.IsNullOrWhiteSpace(executorOriginMessageId))
            {
                _collaborationToolResultOrigins[messageId] = executorOriginMessageId;
            }
            return session.Messages.RemoveAll(item => string.Equals(item.Id, desktopMessageId, StringComparison.OrdinalIgnoreCase)) > 0;
        }
        var content = CollaborationDisplayContent(ReadJsonString(message, "content"), 4000);
        if (string.IsNullOrWhiteSpace(content))
        {
            return false;
        }
        var timestamp = ReadJsonDouble(message, "created_at");
        if (timestamp <= 0)
        {
            timestamp = NowSeconds();
        }
        var subtitle = CollaborationAgentDisplay(fromAgent);
        // 卡片正文配对：回复锚定在"自己那张思考卡"正下方而不是时间线末尾。
        // 工作卡轮次键 = 被处理来件的 message_id = 本回复的 parent_message_id，
        // 据此推导投影卡 Id；epsilon 取 0.0005（小于工作卡之间的 0.001 间距）。
        var anchored = timestamp;
        var parentMessageId = ReadJsonString(message, "parent_message_id");
        var legacyContinuationMessageId = parentMessageId;
        if (_collaborationToolResultOrigins.TryGetValue(parentMessageId, out var originMessageId))
        {
            parentMessageId = originMessageId;
        }
        var mergedLegacyContinuation = !string.Equals(legacyContinuationMessageId, parentMessageId, StringComparison.OrdinalIgnoreCase)
            && MergeLegacyCollaborationContinuationCard(session, threadId, fromAgent, legacyContinuationMessageId, parentMessageId);
        if (!string.IsNullOrWhiteSpace(parentMessageId))
        {
            var draftKey = CollaborationStreamingDraftKey(threadId, fromAgent, parentMessageId);
            _collaborationStreamingDrafts.Remove(draftKey);
            // 草稿气泡（Id 与正式回复不同）定稿后必须从时间线删掉，否则与正式回复并存显示两个气泡。
            var draftId = CollaborationStreamingDraftId(threadId, fromAgent, parentMessageId);
            session.Messages.RemoveAll(item => string.Equals(item.Id, draftId, StringComparison.OrdinalIgnoreCase));
        }
        var existing = session.Messages.FirstOrDefault(item => string.Equals(item.Id, desktopMessageId, StringComparison.OrdinalIgnoreCase));
        if (!string.IsNullOrWhiteSpace(parentMessageId))
        {
            var workCardId = $"session-collab-work-{NormalizeCollaborationThreadKey(threadId)}"
                + $"-{NormalizeCollaborationThreadKey(NormalizeCollaborationWorkAgentKey(fromAgent))}"
                + $"-{NormalizeCollaborationThreadKey(parentMessageId)}";
            var workCard = session.Messages.FirstOrDefault(item => string.Equals(item.Id, workCardId, StringComparison.OrdinalIgnoreCase));
            if (workCard is not null)
            {
                _collaborationPendingReplyAnchors.Remove(workCardId);
                // 收尾兜底：权威回复已投影即代表该轮生成结束。processed/acked 事件可能晚到或丢失，
                // 若不在此终结，会出现"回复气泡已定稿、思考卡仍 Running 继续滚动"的错觉。
                // 投影卡每轮同步会被源卡覆盖（Subtitle/Steps 均以源卡为权威），因此源卡与投影卡都要终结。
                FinalizeCollaborationWorkChain(workCard);
                // 后位起草轮（v4 没抢到发言席位）：正文全程走"起草"泳道，修订成稿不再流式，
                // 卡内会永远缺"回复"泳道——与前位直播卡结构不一致（用户点名）。
                // 权威回复到达即补一步成稿，两类卡结构对齐。源卡每轮同步覆盖投影卡步骤，两边都补。
                EnsureCollaborationReplyStep(workCard, content, fromAgent);
                // 卡片位置一经落地不再互换（2026-07-09 去掉 v3 遗留的"先说完的排前面"CreatedAt 互换）：
                // v4 发言席位已保证"先想完的先直播"，互换只剩坏处——中途重排让 RenderActiveMessages
                // 前缀失配走整批 Clear+Add，打字机/揭示状态全被销毁，表现为 step 一块块 + 气泡卡顿 + 位置跳动。
                anchored = workCard.CreatedAt + 0.0005;
            }
            else
            {
                // 卡片尚未投影（重启丢内存链 / 事件晚到）：登记待配对，卡片落地时反向锚到本回复上方。
                _collaborationPendingReplyAnchors[workCardId] = desktopMessageId;
            }
            foreach (var sourceChain in CollaborationWorkChainsFor(threadId, fromAgent))
            {
                if (string.Equals($"session-{sourceChain.Id}", workCardId, StringComparison.OrdinalIgnoreCase))
                {
                    FinalizeCollaborationWorkChain(sourceChain);
                    EnsureCollaborationReplyStep(sourceChain, content, fromAgent);
                }
            }
        }
        if (existing is null)
        {
            session.Messages.Add(new DesktopMessage
            {
                Id = desktopMessageId,
                Role = "assistant",
                Text = content,
                Subtitle = subtitle,
                CreatedAt = anchored,
                UpdatedAt = timestamp,
            });
            session.UpdatedAt = Math.Max(session.UpdatedAt, timestamp);
            return true;
        }
        if (!string.Equals(existing.Text, content, StringComparison.Ordinal)
            || !string.Equals(existing.Subtitle, subtitle, StringComparison.Ordinal)
            || Math.Abs(existing.CreatedAt - anchored) > 0.0001)
        {
            existing.Text = content;
            existing.Subtitle = subtitle;
            // 缓存投影路径可能先用真实时间插入过这条回复；配对锚点是确定值，补正一次即稳定。
            existing.CreatedAt = anchored;
            existing.UpdatedAt = Math.Max(existing.UpdatedAt, timestamp);
            session.UpdatedAt = Math.Max(session.UpdatedAt, timestamp);
            return true;
        }
        return mergedLegacyContinuation;
    }

    internal static bool IsCollaborationExecutorAgentId(string? agentId)
    {
        return (agentId ?? "").Trim().StartsWith("executor_", StringComparison.OrdinalIgnoreCase);
    }

    internal static bool NormalizeLegacyCollaborationExecutorArtifacts(DesktopSession session)
    {
        var executorMessages = session.Messages
            .Where(message => message.Role.Equals("assistant", StringComparison.OrdinalIgnoreCase)
                && (message.Subtitle ?? "").StartsWith("Executor ", StringComparison.OrdinalIgnoreCase)
                && (message.Text ?? "").StartsWith("Tool result:", StringComparison.OrdinalIgnoreCase))
            .ToList();
        var changed = false;
        foreach (var executorMessage in executorMessages)
        {
            const string replyPrefix = "collab-reply-";
            var resultMessageId = executorMessage.Id.StartsWith(replyPrefix, StringComparison.OrdinalIgnoreCase)
                ? executorMessage.Id[replyPrefix.Length..]
                : "";
            var normalizedResultId = NormalizeCollaborationThreadKey(resultMessageId);
            var continuation = session.Messages.FirstOrDefault(message =>
                message.Kind.Equals("work", StringComparison.OrdinalIgnoreCase)
                && message.Id.EndsWith($"-{normalizedResultId}", StringComparison.OrdinalIgnoreCase));
            if (continuation is not null)
            {
                var origin = session.Messages
                    .Where(message => message.Kind.Equals("work", StringComparison.OrdinalIgnoreCase)
                        && !ReferenceEquals(message, continuation)
                        && string.Equals(message.WorkAgent, continuation.WorkAgent, StringComparison.OrdinalIgnoreCase)
                        && message.CreatedAt <= continuation.CreatedAt)
                    .OrderByDescending(message => message.CreatedAt)
                    .FirstOrDefault();
                if (origin is not null)
                {
                    foreach (var step in continuation.Steps)
                    {
                        var duplicate = origin.Steps.Any(existing =>
                            (!string.IsNullOrWhiteSpace(step.EventId) && string.Equals(existing.EventId, step.EventId, StringComparison.OrdinalIgnoreCase))
                            || (!string.IsNullOrWhiteSpace(step.Key) && string.Equals(existing.Key, step.Key, StringComparison.OrdinalIgnoreCase)));
                        if (!duplicate)
                        {
                            origin.Steps.Add(step);
                        }
                    }
                    origin.Subtitle = continuation.Subtitle;
                    origin.DurationSeconds = Math.Max(origin.DurationSeconds, continuation.DurationSeconds);
                    origin.UpdatedAt = Math.Max(origin.UpdatedAt, continuation.UpdatedAt);

                    var anchoredReplyTime = origin.CreatedAt + 0.0005;
                    foreach (var finalReply in session.Messages.Where(message =>
                        message.Role.Equals("assistant", StringComparison.OrdinalIgnoreCase)
                        && message.Id.EndsWith(resultMessageId, StringComparison.OrdinalIgnoreCase)))
                    {
                        finalReply.CreatedAt = anchoredReplyTime;
                    }
                    session.Messages.RemoveAll(message =>
                        message.Role.Equals("assistant", StringComparison.OrdinalIgnoreCase)
                        && message.Id.StartsWith("collab-reply-reply-", StringComparison.OrdinalIgnoreCase)
                        && !message.Id.EndsWith(resultMessageId, StringComparison.OrdinalIgnoreCase)
                        && Math.Abs(message.CreatedAt - anchoredReplyTime) < 0.0002);
                    session.Messages.Remove(continuation);
                }
            }
            changed |= session.Messages.Remove(executorMessage);
        }
        var duplicateReplyGroups = session.Messages
            .Where(message => message.Role.Equals("assistant", StringComparison.OrdinalIgnoreCase)
                && message.Id.StartsWith("collab-reply-reply-", StringComparison.OrdinalIgnoreCase))
            .GroupBy(message => Math.Round(message.CreatedAt, 4))
            .Where(group => group.Count() > 1)
            .ToList();
        foreach (var group in duplicateReplyGroups)
        {
            var anchoredTime = group.Key;
            var matchesWorkCard = session.Messages.Any(message =>
                message.Kind.Equals("work", StringComparison.OrdinalIgnoreCase)
                && Math.Abs((message.CreatedAt + 0.0005) - anchoredTime) < 0.0002);
            if (!matchesWorkCard)
            {
                continue;
            }
            var keep = group.OrderByDescending(message => message.UpdatedAt).First();
            foreach (var duplicate in group.Where(message => !ReferenceEquals(message, keep)).ToList())
            {
                changed |= session.Messages.Remove(duplicate);
            }
        }
        return changed;
    }

    private static bool MergeLegacyCollaborationContinuationCard(
        DesktopSession session,
        string threadId,
        string agentId,
        string continuationMessageId,
        string originMessageId)
    {
        var prefix = $"session-collab-work-{NormalizeCollaborationThreadKey(threadId)}"
            + $"-{NormalizeCollaborationThreadKey(NormalizeCollaborationWorkAgentKey(agentId))}-";
        var continuationId = prefix + NormalizeCollaborationThreadKey(continuationMessageId);
        var originId = prefix + NormalizeCollaborationThreadKey(originMessageId);
        var continuation = session.Messages.FirstOrDefault(item => string.Equals(item.Id, continuationId, StringComparison.OrdinalIgnoreCase));
        var origin = session.Messages.FirstOrDefault(item => string.Equals(item.Id, originId, StringComparison.OrdinalIgnoreCase));
        if (continuation is null || origin is null || ReferenceEquals(continuation, origin))
        {
            return false;
        }

        origin.Steps ??= new List<DesktopWorkStep>();
        foreach (var step in continuation.Steps ?? Enumerable.Empty<DesktopWorkStep>())
        {
            var duplicate = origin.Steps.Any(existing =>
                (!string.IsNullOrWhiteSpace(step.EventId) && string.Equals(existing.EventId, step.EventId, StringComparison.OrdinalIgnoreCase))
                || (!string.IsNullOrWhiteSpace(step.Key) && string.Equals(existing.Key, step.Key, StringComparison.OrdinalIgnoreCase)));
            if (!duplicate)
            {
                origin.Steps.Add(step);
            }
        }
        origin.Subtitle = continuation.Subtitle;
        origin.UpdatedAt = Math.Max(origin.UpdatedAt, continuation.UpdatedAt);
        session.Messages.Remove(continuation);
        return true;
    }

    internal static string CollaborationStreamingDraftKey(string threadId, string agentId, string parentMessageId)
    {
        return $"{NormalizeCollaborationThreadKey(threadId)}|{NormalizeCollaborationWorkAgentKey(agentId)}|{NormalizeCollaborationThreadKey(parentMessageId)}";
    }

    internal static string CollaborationStreamingDraftId(string threadId, string agentId, string parentMessageId)
    {
        var replyMessageId = $"reply-{NormalizeCollaborationThreadKey(NormalizeCollaborationWorkAgentKey(agentId))}-{NormalizeCollaborationThreadKey(parentMessageId)}";
        return $"collab-reply-{replyMessageId}";
    }

    private bool UpsertCollaborationStreamingDraft(string threadId, string agentId, string parentMessageId, string delta, string accumulated, bool interrupted)
    {
        if (string.IsNullOrWhiteSpace(threadId) || string.IsNullOrWhiteSpace(agentId) || string.IsNullOrWhiteSpace(parentMessageId))
        {
            return false;
        }
        if (!TryResolveSessionForCollaborationThread(threadId, out var session))
        {
            return false;
        }
        var draftKey = CollaborationStreamingDraftKey(threadId, agentId, parentMessageId);
        var draftId = CollaborationStreamingDraftId(threadId, agentId, parentMessageId);
        var existing = session.Messages.FirstOrDefault(message => string.Equals(message.Id, draftId, StringComparison.OrdinalIgnoreCase));
        // 定稿后迟到的流式批次兜底：worker 的 reply message_id 与草稿同源（reply-{agent}-{parent}），
        // 正式回复投影会占用同一 Id 并把副标题改成纯 agent 名。此时草稿登记已被移除，
        // 不得再把定稿消息改写回"生成中"，否则副标题在日期与生成中之间反复横跳。
        if (existing is not null
            && !_collaborationStreamingDrafts.ContainsKey(draftKey)
            && !(existing.Subtitle ?? "").Contains("生成中", StringComparison.Ordinal))
        {
            return false;
        }
        _collaborationStreamingDrafts[draftKey] = draftId;
        var now = NowSeconds();
        var subtitle = interrupted ? $"{CollaborationAgentDisplay(agentId)} · 生成中断" : $"{CollaborationAgentDisplay(agentId)} · 生成中";
        var suffix = interrupted ? "\n\n〔生成中断〕" : "";
        var changed = false;
        if (existing is null)
        {
            var workCardId = $"session-collab-work-{NormalizeCollaborationThreadKey(threadId)}"
                + $"-{NormalizeCollaborationThreadKey(NormalizeCollaborationWorkAgentKey(agentId))}"
                + $"-{NormalizeCollaborationThreadKey(parentMessageId)}";
            var workCard = session.Messages.FirstOrDefault(message => string.Equals(message.Id, workCardId, StringComparison.OrdinalIgnoreCase));
            var anchored = workCard is not null ? workCard.CreatedAt + 0.0005 : now;
            if (workCard is null)
            {
                // 草稿竞态（批次九）：首 token 同步建草稿，投影卡要等 200ms 合帧才落地。
                // 卡缺席时登记反向锚——卡落地取 draft.CreatedAt-0.0005 排到草稿正上方，
                // 否则卡按"最新消息+0.001"落到自己草稿下面，定稿删草稿时回复肉眼跳位。
                _collaborationPendingReplyAnchors[workCardId] = draftId;
            }
            session.Messages.Add(new DesktopMessage
            {
                Id = draftId,
                Role = "assistant",
                Kind = "collaboration_stream_draft",
                Text = string.Concat(string.IsNullOrEmpty(accumulated) ? delta ?? "" : accumulated, suffix),
                Subtitle = subtitle,
                CreatedAt = anchored,
                UpdatedAt = now,
            });
            session.UpdatedAt = Math.Max(session.UpdatedAt, now);
            return true;
        }
        var currentBody = existing.Text ?? "";
        var interruptTag = currentBody.IndexOf("〔生成中断〕", StringComparison.Ordinal);
        if (interruptTag >= 0)
        {
            currentBody = currentBody[..interruptTag].TrimEnd('\n');
        }
        string nextText;
        if (!string.IsNullOrEmpty(accumulated))
        {
            // 权威路径：worker 附带的累计全文整体覆盖。只接受不短于当前的快照——
            // 更短意味着这是被重复投递/乱序送达的旧批次，直接丢弃；
            // 状态同步把草稿回滚成旧文本时，下一批 accumulated 会立即恢复全文。
            nextText = accumulated.Length >= currentBody.Length ? accumulated : currentBody;
        }
        else
        {
            // 回退路径（旧版 worker 无 accumulated）：结尾去重后增量追加。
            nextText = currentBody;
            if (!string.IsNullOrEmpty(delta) && !nextText.EndsWith(delta, StringComparison.Ordinal))
            {
                nextText += delta;
            }
        }
        if (interrupted)
        {
            nextText += suffix;
        }
        if (!string.Equals(existing.Text, nextText, StringComparison.Ordinal))
        {
            existing.Text = nextText;
            changed = true;
        }
        if (!string.Equals(existing.Subtitle, subtitle, StringComparison.Ordinal))
        {
            existing.Subtitle = subtitle;
            changed = true;
        }
        if (changed)
        {
            existing.UpdatedAt = now;
            session.UpdatedAt = Math.Max(session.UpdatedAt, now);
        }
        return changed;
    }

    private bool MarkCollaborationStreamingDraftInterrupted(string threadId, string agentId, string parentMessageId)
    {
        return UpsertCollaborationStreamingDraft(threadId, agentId, parentMessageId, "", "", interrupted: true);
    }

    private bool RemoveCollaborationStreamingDraft(string threadId, string agentId, string parentMessageId)
    {
        if (string.IsNullOrWhiteSpace(threadId) || string.IsNullOrWhiteSpace(agentId) || string.IsNullOrWhiteSpace(parentMessageId))
        {
            return false;
        }
        var draftKey = CollaborationStreamingDraftKey(threadId, agentId, parentMessageId);
        var draftId = CollaborationStreamingDraftId(threadId, agentId, parentMessageId);
        _collaborationStreamingDrafts.Remove(draftKey);
        if (!TryResolveSessionForCollaborationThread(threadId, out var session))
        {
            return false;
        }
        return session.Messages.RemoveAll(message => string.Equals(message.Id, draftId, StringComparison.OrdinalIgnoreCase)) > 0;
    }

    private bool TryResolveSessionForCollaborationThread(string threadId, out DesktopSession session)
    {
        session = _workspaceController.ActiveSession();
        var normalized = NormalizeCollaborationThreadKey(threadId);
        const string prefix = "session-";
        if (normalized.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            var sessionKey = normalized[prefix.Length..];
            var match = _state.Sessions.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), sessionKey, StringComparison.OrdinalIgnoreCase));
            if (match is not null)
            {
                session = match;
                return true;
            }
        }
        return string.Equals(NormalizeCollaborationThreadKey(CurrentSessionCollaborationThreadId()), normalized, StringComparison.OrdinalIgnoreCase);
    }

    private void CommitActiveSessionCollaborationProjection(bool changed)
    {
        if (!changed)
        {
            return;
        }
        RenderState();
        ScrollMessagesToEnd();
        _ = SaveStateAsync();
    }

    internal static object BuildCollaborationTurnRefillPayload(string threadId, string actor = "human_desktop") => new
    {
        action = "refill_turns",
        thread_id = threadId,
        actor,
    };

    internal static object BuildCollaborationTurnCapPayload(string threadId, int cap, string actor = "human_desktop") => new
    {
        action = "set_thread_turn_cap",
        thread_id = threadId,
        cap,
        actor,
        include_collaboration = false,
    };

    internal static object BuildCollaborationTurnPausePayload(string threadId, string actor = "human_desktop") => new
    {
        action = "pause_turns",
        thread_id = threadId,
        actor,
        include_collaboration = false,
    };

    private async Task RefillCollaborationTurnsForHumanMessageAsync(string threadId)
    {
        if (string.IsNullOrWhiteSpace(threadId))
        {
            return;
        }
        try
        {
            using var doc = await PostJsonAsync(
                $"{_workspaceController.ApiBase()}/desktop/collaboration",
                BuildCollaborationTurnRefillPayload(threadId));
            EnsureOkResponse(doc.RootElement, "refill collaboration turns");
            RefreshCollaborationTurnGuardDisplay(doc.RootElement);
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"双工续轮失败，仍继续发送：{ex.Message}";
        }
    }

    // 新会话优先（批次九）：发送前检测其他 thread 的协作串联是否仍在生成，
    // 询问用户是否暂停旧会话（turn guard pause_turns 软停，worker 条间检查点生效，
    // 正在生成的那一条自然完成——不 kill worker：worker 是 agent 级全局进程，杀掉连新会话一起断）。
    // internal：普通聊天发送路径（DesktopCommandSending）也要接入——普通消息同样占用本地模型。
    internal async Task MaybePauseOtherCollaborationThreadsAsync(string currentThreadId)
    {
        if (string.IsNullOrWhiteSpace(currentThreadId))
        {
            return;
        }
        // "旧会话仍在进行"不能只看 running 卡：双工串联每条生成完即 Finalize，条与条的
        // 间隙期（worker 轮询 5s + 排队）没有任何 running 卡，只看 running 必然 miss
        //（2026-07-09 实测：新会话发消息没弹中断询问）。补一条近期活跃判定：
        // 最近 180s 内有卡更新过即视为串联仍在滚动（双工链条间隔实测远小于该窗口）。
        var recentThreshold = NowSeconds() - 180;
        var busyThreads = _collaborationWorkChains
            .Where(entry => !string.Equals(entry.Key, currentThreadId, StringComparison.OrdinalIgnoreCase)
                && !_pausedCollaborationThreads.Contains(entry.Key)
                && !_pausePromptDeclinedThreads.Contains(entry.Key)
                && !IsDeletedCollaborationThread(entry.Key)
                && entry.Value.Values.Any(cards => cards.Any(card =>
                    string.Equals(card.Subtitle, "running", StringComparison.OrdinalIgnoreCase)
                    || card.UpdatedAt >= recentThreshold)))
            .Select(entry => entry.Key)
            .ToList();
        if (busyThreads.Count == 0)
        {
            return;
        }
        if (!ConfirmAction(
            "旧会话协作仍在进行",
            "旧会话的模型串联还在生成，会占用本地模型拖慢本会话。是否暂停旧会话、优先处理本会话？（正在生成的那一条会自然完成）",
            "暂停旧会话"))
        {
            // 拒绝后本会话内不再重复询问这些 thread；用户回到旧会话发消息会自动解除暂停语义。
            foreach (var threadId in busyThreads)
            {
                _pausePromptDeclinedThreads.Add(threadId);
            }
            return;
        }
        var paused = 0;
        foreach (var threadId in busyThreads)
        {
            try
            {
                using var doc = await PostJsonAsync(
                    $"{_workspaceController.ApiBase()}/desktop/collaboration",
                    BuildCollaborationTurnPausePayload(threadId));
                EnsureOkResponse(doc.RootElement, "pause collaboration turns");
                _pausedCollaborationThreads.Add(threadId);
                paused++;
            }
            catch (Exception ex)
            {
                WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"暂停旧会话协作失败（{threadId}）：{ex.Message}";
            }
        }
        if (paused > 0)
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text =
                $"已暂停 {paused} 个旧会话协作，优先处理本会话；本会话完成后会询问是否恢复。";
        }
    }

    // 新会话工作完结后提示恢复被暂停的旧串联（refill_turns 清 turn_paused，worker 下一轮轮询续上）。
    private void MaybePromptResumePausedCollaborationThreads(string finishedThreadId)
    {
        if (_pausedCollaborationThreads.Count == 0
            || _resumePromptInFlight
            || !string.Equals(finishedThreadId, CurrentSessionCollaborationThreadId(), StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        // 该 thread 仍有 running 卡说明本轮还没全部完结（多模型各自一张卡）。
        if (CollaborationWorkChainsFor(finishedThreadId, "").Any(card =>
            string.Equals(card.Subtitle, "running", StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }
        // 修P：仍有参与模型未收到完成信号 → 不弹。"无 running 卡"在模型轮与轮的空隙
        // （一个完工、另一个还没开卡）会误判全部完成，需按发送时登记的参与模型集合把关。
        if (_collaborationPendingResumeAgents.TryGetValue(finishedThreadId, out var stillPending)
            && stillPending.Count > 0)
        {
            return;
        }
        _resumePromptInFlight = true;
        // 延迟到事件流之外弹窗：finished 事件处理在渲染路径上，模态框会阻塞合帧投影。
        _ = _dispatcher.BeginInvoke(async () =>
        {
            try
            {
                var pausedThreads = _pausedCollaborationThreads.ToList();
                if (pausedThreads.Count == 0)
                {
                    return;
                }
                var resume = ConfirmAction(
                    "本会话工作已完成",
                    "是否恢复此前暂停的旧会话协作？",
                    "恢复");
                _pausedCollaborationThreads.Clear();
                if (!resume)
                {
                    WorkbenchShell.ManagementPanels.CollaborationActionText.Text =
                        "旧会话保持暂停；回到旧会话发消息即自动恢复。";
                    return;
                }
                var resumed = 0;
                foreach (var threadId in pausedThreads)
                {
                    try
                    {
                        using var doc = await PostJsonAsync(
                            $"{_workspaceController.ApiBase()}/desktop/collaboration",
                            BuildCollaborationTurnRefillPayload(threadId));
                        EnsureOkResponse(doc.RootElement, "resume collaboration turns");
                        resumed++;
                    }
                    catch (Exception ex)
                    {
                        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"恢复旧会话协作失败（{threadId}）：{ex.Message}";
                    }
                }
                if (resumed > 0)
                {
                    WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"已恢复 {resumed} 个旧会话协作串联。";
                }
            }
            finally
            {
                _resumePromptInFlight = false;
            }
        });
    }

    internal async Task SendCollaborationMessageAsync(string action)
    {
        var content = WorkbenchShell.ManagementPanels.CollaborationMessageContentBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(content))
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "请先填写消息内容。";
            return;
        }
        var threadId = EnsureCollaborationTaskId();

        if (string.Equals(CollaborationMessageFrom(), "human_desktop", StringComparison.OrdinalIgnoreCase))
        {
            await RefillCollaborationTurnsForHumanMessageAsync(threadId);
        }
        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action,
            task_id = threadId,
            thread_id = threadId,
            from_agent = CollaborationMessageFrom(),
            to_agents = new[] { ComboText(WorkbenchShell.ManagementPanels.CollaborationMessageToBox).Trim() },
            from_model = CollaborationMessageFrom(),
            to_model = ComboText(WorkbenchShell.ManagementPanels.CollaborationMessageToBox).Trim(),
            role = ComboText(WorkbenchShell.ManagementPanels.CollaborationMessageRoleBox).Trim(),
            content,
            context_pack_path = CollaborationMessageContextPackPath(),
            metadata = new
            {
                permission_mode = _composer.ComposerPermissionMode(),
                full_access_granted = _composer.FullAccessGranted(),
            },
        });
        EnsureOkResponse(doc.RootElement, action);
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        var toAgent = ComboText(WorkbenchShell.ManagementPanels.CollaborationMessageToBox).Trim();
        EnsureCollaborationWorkersForAgents(new[] { toAgent }, threadId);
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = action == "request_model_review" ? "已发送模型评审请求。" : $"已发送协作消息。{AutoWorkerStatusSuffix(new[] { toAgent })}";
    }

    internal bool RemoveSessionCollaborationAgent(string agent)
    {
        var normalized = (agent ?? "").Trim();
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return false;
        }
        var session = _workspaceController.ActiveSession();
        session.CollaborationAgents ??= new List<string>();
        session.CollaborationOptOut ??= new List<string>();
        var removed = session.CollaborationAgents.RemoveAll(item => string.Equals(item, normalized, StringComparison.OrdinalIgnoreCase)) > 0;
        if (!removed)
        {
            return false;
        }
        if (!session.CollaborationOptOut.Contains(normalized, StringComparer.OrdinalIgnoreCase))
        {
            session.CollaborationOptOut.Add(normalized);
        }
        session.UpdatedAt = NowSeconds();
        _ = SaveStateAsync();
        return true;
    }

    // 输入命令移除参与者："移除 @xxx" / "remove @xxx" / "退出 @xxx"。命中时返回 true（消息不再发送）。
    internal bool TryHandleCollaborationRemoveCommand(string rawText)
    {
        var match = Regex.Match(
            (rawText ?? "").Trim(),
            @"^(移除|删除|退出|remove|leave)\s*@(?<name>[A-Za-z0-9_.:/\-\u4e00-\u9fff]{1,128})\s*$",
            RegexOptions.IgnoreCase);
        if (!match.Success)
        {
            return false;
        }
        var agent = ResolveCollaborationAgentMention(match.Groups["name"].Value);
        string hint;
        if (string.IsNullOrWhiteSpace(agent))
        {
            hint = $"没有识别到参与者：@{match.Groups["name"].Value}";
        }
        else if (RemoveSessionCollaborationAgent(agent))
        {
            // 只移出当前会话，不停全局 worker：它可能还在服务其他会话。
            hint = $"已将 {agent} 移出当前会话，之后无 @ 的消息不再发给它。";
            RenderState();
        }
        else
        {
            hint = $"{agent} 不在当前会话参与者中。";
        }
        ChatWorkspace.SendStatusText.Text = hint;
        ChatWorkspace.SendStatusText.Visibility = Visibility.Visible;
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = hint;
        return true;
    }

    // 头部参与者 chip 的 × 点击入口：移除 + 停 worker + 提示 + 重渲染。
    internal void RemoveCollaborationParticipantInteractive(string agent)
    {
        if (!RemoveSessionCollaborationAgent(agent))
        {
            return;
        }
        // 只移出当前会话，不停全局 worker：它可能还在服务其他会话。
        var hint = $"已将 {agent} 移出当前会话，之后无 @ 的消息不再发给它。";
        ChatWorkspace.SendStatusText.Text = hint;
        ChatWorkspace.SendStatusText.Visibility = Visibility.Visible;
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = hint;
        RenderState();
    }

    internal async Task SendCollaborationMessageFromComposerAsync(string rawText)
    {
        if (TryHandleCollaborationRemoveCommand(rawText))
        {
            return;
        }
        var route = ParseCollaborationComposerRoute(rawText);
        if (route.ToAgents.Length == 0)
        {
            const string hint = "当前会话还没有协作参与者，请先 @ 一个模型加入（例如 @model_deepseek）。";
            ChatWorkspace.SendStatusText.Text = hint;
            ChatWorkspace.SendStatusText.Visibility = Visibility.Visible;
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = hint;
            return;
        }
        var threadId = _composer.BindCurrentSessionCollaborationThread();
        var content = BuildCurrentSessionCollaborationContent(route.Content);
        WorkbenchShell.ManagementPanels.CollaborationMessageContentBox.Text = content;
        WorkbenchShell.ManagementPanels.CollaborationMessageFromBox.Text = "human_desktop";
        SetComboText(WorkbenchShell.ManagementPanels.CollaborationMessageRoleBox, route.Role);
        SetComboText(WorkbenchShell.ManagementPanels.CollaborationMessageToBox, route.ToAgents.Length == 1 ? route.ToAgents[0] : "all");
        // 修H：worker 是 agent 级全局串行进程，旧会话双工串联会占住本地模型拖慢本会话。
        // 发送前先问用户是否暂停旧会话（turn guard pause_turns，正在生成的那一条自然完成）。
        await MaybePauseOtherCollaborationThreadsAsync(threadId);
        await RefillCollaborationTurnsForHumanMessageAsync(threadId);
        // One logical send keeps one client-generated id across transport retries.
        // Without this, a request that was persisted but lost its HTTP response was
        // retried as a second message and every collaboration worker executed it again.
        var postedMessageId = $"desktop-{Guid.NewGuid():N}";
        try
        {
            var payload = new
            {
                action = route.Role == "review_request" ? "request_model_review" : "post_message",
                task_id = threadId,
                thread_id = threadId,
                from_agent = "human_desktop",
                to_agents = route.ToAgents,
                role = route.Role,
                content,
                message_id = postedMessageId,
                context_pack_path = CollaborationMessageContextPackPath(),
                metadata = new
                {
                    permission_mode = _composer.ComposerPermissionMode(),
                    full_access_granted = _composer.FullAccessGranted(),
                },
            };
            JsonDocument doc;
            try
            {
                doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", payload);
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
            {
                // 网关瞬断/超时自动重试一次（2026-07-09 实测：新会话首条消息发送失败被静默吞掉，
                // 用户"迟迟没有回应"却不知道消息压根没发出去）。重试仍失败才提示人工重发。
                ChatWorkspace.SendStatusText.Text = "发送遇阻，正在自动重试…";
                ChatWorkspace.SendStatusText.Visibility = Visibility.Visible;
                await Task.Delay(TimeSpan.FromSeconds(2));
                doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", payload);
            }
            using (doc)
            {
                EnsureOkResponse(doc.RootElement, "send collaboration message");
                if (doc.RootElement.TryGetProperty("message", out var postedMessage)
                    && postedMessage.ValueKind == JsonValueKind.Object)
                {
                    postedMessageId = ReadJsonString(postedMessage, "message_id", postedMessageId);
                }
                // 修P：发送成功且此前暂停过旧会话 → 登记本会话所有参与模型为待完工。
                // 全部完工（集合清空）后才弹恢复提示，避免一个模型完工就误判全部完成提前弹窗。
                if (_pausedCollaborationThreads.Count > 0)
                {
                    _collaborationPendingResumeAgents[threadId] = new HashSet<string>(
                        route.ToAgents.Where(agent => !string.IsNullOrWhiteSpace(agent)),
                        StringComparer.OrdinalIgnoreCase);
                }
                AppendCollaborationWorkEventsFromResponse(doc.RootElement);
                RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            // 网关瞬断/响应中断不是致命错误：提示重发即可。此前未捕获会顺着 async 冒到 Dispatcher 直接杀进程（2026-07-07 闪退）。
            // TaskCanceledException 同样要接住：60s 超时走的是取消而非 HttpRequestException（2026-07-09 新会话无回应根因之一）。
            var hint = $"协作消息发送失败（网关连接中断/超时，已重试一次仍未成功），请重发：{ex.Message}";
            ChatWorkspace.SendStatusText.Text = hint;
            ChatWorkspace.SendStatusText.Visibility = Visibility.Visible;
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = hint;
            return;
        }
        EnsureCollaborationWorkersForAgents(route.ToAgents, threadId);
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = $"已发送到：{string.Join(", ", route.ToAgents)}{AutoWorkerStatusSuffix(route.ToAgents)}";
    }

    private string BuildCurrentSessionCollaborationContent(string userText)
    {
        var session = _workspaceController.ActiveSession();
        var project = _workspaceController.ProjectForSession(session);
        var runtime = _workspaceController.ActiveProjectRuntimeProfile();
        var recent = session.Messages
            .Where(message => !string.Equals(message.Kind, "work", StringComparison.OrdinalIgnoreCase))
            .OrderBy(message => message.CreatedAt)
            .TakeLast(16)
            .Select(message =>
            {
                var role = string.Equals(message.Role, "user", StringComparison.OrdinalIgnoreCase) ? "User" : "Assistant";
                return $"{role}: {ComposerController.TrimStatusText(message.Text, 900)}";
            })
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .ToArray();
        var activeProjectTitle = project is null ? "Chats" : $"{project.Title} ({project.Id})";
        var workspace = runtime.WorkspacePath;
        var attachments = _composer.PendingAttachmentDisplayPaths(8);
        var context = new List<string>
        {
            "SpiritKin collaboration request.",
            $"Current session: {session.Title} ({session.Id})",
            $"Current project: {activeProjectTitle}",
            $"Workspace path: {workspace}",
            $"Runtime: package_manager={runtime.PackageManager}; dependency_file={BlankIfEmpty(runtime.DependencyFilePath)}; env_file={BlankIfEmpty(runtime.EnvFilePath)}; start_command={BlankIfEmpty(runtime.StartCommand)}",
        };
        if (!string.IsNullOrWhiteSpace(project?.Detail))
        {
            context.Add($"Project note: {ComposerController.TrimStatusText(project.Detail, 900)}");
        }
        if (attachments.Length > 0)
        {
            context.Add("Attached files:");
            context.AddRange(attachments.Select(path => $"- {path}"));
        }
        if (recent.Length > 0)
        {
            context.Add("Recent conversation:");
            context.AddRange(recent);
        }
        context.Add("User request:");
        context.Add(userText.Trim());
        return string.Join(Environment.NewLine, context);
    }

    private static string BlankIfEmpty(string value)
    {
        return string.IsNullOrWhiteSpace(value) ? "--" : value.Trim();
    }

    private void AppendCollaborationWorkEventsFromResponse(JsonElement root)
    {
        if (!root.TryGetProperty("work_events", out var events) || events.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var item in events.EnumerateArray())
        {
            var ev = item.Deserialize<RuntimeEvent>(_jsonOptions);
            if (ev is not null)
            {
                AppendCollaborationWorkEvent(ev);
            }
        }
    }

    internal async Task MarkSelectedCollaborationMessageReadAsync()
    {
        if (WorkbenchShell.ManagementPanels.CollaborationMessagesList.SelectedItem is not ActionItemViewModel selected)
        {
            WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "请先选择一条消息。";
            return;
        }
        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action = "mark_message_read",
            message_id = selected.Id,
            reader = CollaborationMessageFrom(),
        });
        EnsureOkResponse(doc.RootElement, "mark message read");
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        WorkbenchShell.ManagementPanels.CollaborationActionText.Text = "已标记消息为已读。";
    }

    internal async Task MarkCurrentCollaborationThreadReadAsync()
    {
        var targets = VisibleCollaborationMessages()
            .Where(item => IsOpenCollaborationMessage(item) && !IsHumanCollaborationMessage(item))
            .ToList();
        if (targets.Count == 0)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "当前协作线程没有未读模型消息。";
            return;
        }

        JsonElement? latestState = null;
        foreach (var message in targets)
        {
            using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
            {
                action = "mark_message_read",
                message_id = message.Id,
                reader = "human_desktop",
            });
            EnsureOkResponse(doc.RootElement, "mark thread messages read");
            latestState = doc.RootElement.GetProperty("collaboration").Clone();
        }
        if (latestState is { } state)
        {
            RenderCollaborationState(state);
        }
        WorkspaceSidebar.ConnectionStatusText.Text = $"已标记当前协作线程 {targets.Count} 条消息为已读。";
    }

    internal async Task SetCurrentCollaborationThreadStatusAsync(string threadId, string status)
    {
        if (string.IsNullOrWhiteSpace(threadId))
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "请先选择协作线程。";
            return;
        }
        var wasActiveThread = string.Equals(CurrentCollaborationTaskId(), threadId, StringComparison.OrdinalIgnoreCase);
        var action = status.Equals("archived", StringComparison.OrdinalIgnoreCase)
            ? "archive_thread"
            : status.Equals("deleted", StringComparison.OrdinalIgnoreCase)
                ? "delete_thread"
                : "restore_thread";
        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/collaboration", new
        {
            action,
            thread_id = threadId,
            status,
            title = CollaborationThreadTitle(threadId),
        });
        EnsureOkResponse(doc.RootElement, action);
        RenderCollaborationState(doc.RootElement.GetProperty("collaboration"));
        if (status.Equals("deleted", StringComparison.OrdinalIgnoreCase) && wasActiveThread)
        {
            SetActiveCollaborationThread(FallbackCollaborationThreadAfterDelete(threadId));
            RenderCollaborationThreads();
            RenderCollaborationChatMessagesIfChanged(force: true);
        }
        WorkspaceSidebar.ConnectionStatusText.Text = status.Equals("archived", StringComparison.OrdinalIgnoreCase)
            ? "已归档协作线程。"
            : status.Equals("deleted", StringComparison.OrdinalIgnoreCase)
                ? "已删除协作线程。"
                : "已恢复协作线程。";
    }

    internal void CollaborationMessagesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (WorkbenchShell.ManagementPanels.CollaborationMessagesList.SelectedItem is ActionItemViewModel selected)
        {
            SetActiveCollaborationThread(selected.Target);
            if (!string.IsNullOrWhiteSpace(selected.Command))
            {
                WorkbenchShell.ManagementPanels.CollaborationMessageContextPackBox.Text = selected.Command;
            }
            WorkbenchShell.ManagementPanels.CollaborationMessageDetailText.Text = $"{selected.Type}{Environment.NewLine}{selected.Meta}".Trim();
        }
    }

    private void RenderCollaborationChatMessages()
    {
        var activeThread = EnsureCollaborationTaskId();
        var visibleMessages = VisibleCollaborationMessages().ToList();
        ChatWorkspace.ActiveTitleText.Text = $"模型协作对话 · {CollaborationThreadTitle(activeThread)}";
        ChatWorkspace.ActiveMetaText.Text = $"{visibleMessages.Count} / {CollaborationMessages.Count} 条 · {activeThread} · @Codex / @ClaudeCode / @all";
        _messages.Clear();
        foreach (var item in visibleMessages.AsEnumerable().Reverse())
        {
            var role = IsHumanCollaborationMessage(item) ? "user" : "assistant";
            var text = string.IsNullOrWhiteSpace(item.Meta) ? item.Type : item.Meta.Trim();
            _messages.Add(MessageViewModel.FromMessage(new DesktopMessage
            {
                Id = item.Id,
                Role = role,
                Text = text,
                Subtitle = item.Type,
                CreatedAt = 0,
                UpdatedAt = 0,
            }));
        }
        // 协作工作链：把该 thread 的真实 trace 步骤按参与模型分卡挂在对话末尾（Codex-like 多 Agent 工作链）。
        foreach (var workChain in CollaborationWorkChainsFor(activeThread, "")
            .Where(chain => chain.Steps is { Count: > 0 })
            .OrderBy(chain => chain.CreatedAt))
        {
            _messages.Add(WorkChainViewModel.FromMessage(workChain));
        }
    }

    internal void RenderCollaborationChatMessagesIfChanged(bool force = false)
    {
        if (!_collaborationChatActive)
        {
            return;
        }
        var signature = BuildCollaborationChatSignature();
        if (!force && string.Equals(signature, _lastCollaborationChatSignature, StringComparison.Ordinal))
        {
            var activeThread = EnsureCollaborationTaskId();
            ChatWorkspace.ActiveTitleText.Text = $"模型协作对话 · {CollaborationThreadTitle(activeThread)}";
            ChatWorkspace.ActiveMetaText.Text = $"{VisibleCollaborationMessages().Count()} / {CollaborationMessages.Count} 条 · {activeThread} · @Codex / @ClaudeCode / @all";
            return;
        }
        _lastCollaborationChatSignature = signature;
        RenderCollaborationChatMessages();
        ScrollMessagesToEnd();
    }

    private string BuildCollaborationChatSignature()
    {
        var messages = string.Join(
            "\n",
            VisibleCollaborationMessages().Select(item => $"{item.Id}|{item.Type}|{item.Target}|{item.Meta}"));
        // 工作链步骤数 + 终态纳入签名，使 trace 步骤增量也能触发当前线程重渲染。
        var activeThread = CurrentCollaborationTaskId();
        if (!string.IsNullOrWhiteSpace(activeThread))
        {
            foreach (var workChain in CollaborationWorkChainsFor(activeThread, ""))
            {
                var lastDetailLength = workChain.Steps?.LastOrDefault()?.Detail?.Length ?? 0;
                messages += $"\nwork:{workChain.Id}:{workChain.Steps?.Count ?? 0}:{lastDetailLength}:{workChain.Subtitle}";
            }
        }
        return messages;
    }

    internal void RenderCollaborationThreads()
    {
        var activeThread = EnsureCollaborationTaskId();
        RenderCollaborationContextOptions(activeThread);
        var groups = CollaborationMessages
            .Where(item => !string.IsNullOrWhiteSpace(item.Target)
                && !IsDeletedCollaborationThread(item.Target)
                && CollaborationThreadInCurrentScope(item.Target, activeThread))
            .GroupBy(item => item.Target, StringComparer.OrdinalIgnoreCase)
            .ToList();
        var hasActiveThread = groups.Any(group => string.Equals(group.Key, activeThread, StringComparison.OrdinalIgnoreCase));
        var signature = string.Join(
            "\n",
            groups.Select(group =>
            {
                var latest = group.First();
                var nonHumanOpenCount = group.Count(item => IsOpenCollaborationMessage(item) && !IsHumanCollaborationMessage(item));
                return $"{group.Key}|{CollaborationThreadStatus(group.Key)}|{group.Count()}|{nonHumanOpenCount}|{latest.Id}|{latest.Type}|{latest.Meta}";
            }).Prepend($"active:{activeThread}|has:{hasActiveThread}"));
        if (string.Equals(signature, _lastCollaborationThreadSignature, StringComparison.Ordinal))
        {
            _syncingCollaborationThreadSelection = true;
            try
            {
                WorkspaceSidebar.CollaborationThreadsList.SelectedValue = activeThread;
            }
            finally
            {
                _syncingCollaborationThreadSelection = false;
            }
            return;
        }
        _lastCollaborationThreadSignature = signature;

        _syncingCollaborationThreadSelection = true;
        try
        {
            CollaborationThreads.Clear();
            foreach (var group in groups)
            {
                var latest = group.First();
                var count = group.Count();
                var openCount = group.Count(item => IsOpenCollaborationMessage(item) && !IsHumanCollaborationMessage(item));
                var preview = CollaborationMessagePreview(latest);
                CollaborationThreads.Add(new SessionViewModel(
                    group.Key,
                    CollaborationThreadTitle(group.Key),
                    $"{count} 条 · {AgentRouteLabel(latest)}{(string.IsNullOrWhiteSpace(preview) ? "" : $" · {preview}")}",
                    CollaborationThreadStatus(group.Key),
                    isPinned: false,
                    isUnread: openCount > 0));
            }
            WorkspaceSidebar.CollaborationThreadsList.SelectedValue = activeThread;
        }
        finally
        {
            _syncingCollaborationThreadSelection = false;
        }
    }

    private IEnumerable<ActionItemViewModel> VisibleCollaborationMessages()
    {
        var threadId = CurrentCollaborationTaskId();
        if (IsDeletedCollaborationThread(threadId))
        {
            return Array.Empty<ActionItemViewModel>();
        }
        return string.IsNullOrWhiteSpace(threadId)
            ? CollaborationMessages.Where(item => !IsDeletedCollaborationThread(item.Target))
            : CollaborationMessages.Where(item => string.Equals(item.Target, threadId, StringComparison.OrdinalIgnoreCase));
    }

    private void RenderCollaborationContextOptions(string activeThread)
    {
        var options = BuildCollaborationContextOptions(activeThread).ToList();
        var signature = string.Join("\n", options.Select(item => $"{item.Id}|{item.Title}|{item.Command}"));
        if (!string.Equals(signature, _lastCollaborationContextSignature, StringComparison.Ordinal))
        {
            _lastCollaborationContextSignature = signature;
            _syncingCollaborationContextSelection = true;
            try
            {
                CollaborationContextOptions.Clear();
                foreach (var item in options)
                {
                    CollaborationContextOptions.Add(item);
                }
            }
            finally
            {
                _syncingCollaborationContextSelection = false;
            }
        }
        _syncingCollaborationContextSelection = true;
        try
        {
            WorkspaceSidebar.CollaborationContextBox.SelectedValue = activeThread;
            RenderCollaborationScopeSelectors(activeThread);
        }
        finally
        {
            _syncingCollaborationContextSelection = false;
        }
    }

    private void RenderCollaborationScopeSelectors(string activeThread)
    {
        var projectOptions = BuildCollaborationProjectScopeOptions().ToList();
        var selectedProjectScope = CollaborationProjectScopeForThread(activeThread);
        if (!projectOptions.Any(item => string.Equals(item.Id, selectedProjectScope, StringComparison.OrdinalIgnoreCase)))
        {
            selectedProjectScope = CollaborationChatsScopeId;
        }

        var sessionOptions = BuildCollaborationSessionScopeOptions(selectedProjectScope).ToList();
        var selectedSessionScope = CollaborationSessionScopeForThread(activeThread, selectedProjectScope);
        if (!sessionOptions.Any(item => string.Equals(item.Id, selectedSessionScope, StringComparison.OrdinalIgnoreCase)))
        {
            selectedSessionScope = sessionOptions.FirstOrDefault()?.Id ?? "";
        }

        var signature = string.Join("\n", projectOptions.Select(item => $"p|{item.Id}|{item.Title}"))
            + "\n"
            + string.Join("\n", sessionOptions.Select(item => $"s|{item.Id}|{item.Title}|{item.Command}"));
        if (!string.Equals(signature, _lastCollaborationScopeSignature, StringComparison.Ordinal))
        {
            _lastCollaborationScopeSignature = signature;
            _syncingCollaborationContextSelection = true;
            try
            {
                CollaborationProjectScopes.Clear();
                foreach (var option in projectOptions)
                {
                    CollaborationProjectScopes.Add(option);
                }
                CollaborationSessionScopes.Clear();
                foreach (var option in sessionOptions)
                {
                    CollaborationSessionScopes.Add(option);
                }
            }
            finally
            {
                _syncingCollaborationContextSelection = false;
            }
        }

        _syncingCollaborationContextSelection = true;
        try
        {
            WorkspaceSidebar.CollaborationProjectScopeBox.SelectedValue = selectedProjectScope;
            WorkspaceSidebar.CollaborationSessionScopeBox.SelectedValue = selectedSessionScope;
        }
        finally
        {
            _syncingCollaborationContextSelection = false;
        }
    }

    private IEnumerable<QuickCommandViewModel> BuildCollaborationProjectScopeOptions()
    {
        yield return new QuickCommandViewModel(CollaborationChatsScopeId, "Chats", "普通会话范围");
        foreach (var project in _state.Projects
            .Where(item => !WorkspaceController.IsArchived(item.Status))
            .OrderByDescending(item => item.UpdatedAt))
        {
            yield return new QuickCommandViewModel(project.Id, $"项目：{project.Title}", project.Id);
        }
    }

    private IEnumerable<QuickCommandViewModel> BuildCollaborationSessionScopeOptions(string projectScopeId)
    {
        if (string.Equals(projectScopeId, CollaborationChatsScopeId, StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(projectScopeId))
        {
            yield return new QuickCommandViewModel($"project-{NormalizeCollaborationThreadKey(CollaborationChatsScopeId)}", "Chats 全部会话", "project");
            foreach (var session in _state.Sessions
                .Where(item => string.IsNullOrWhiteSpace(item.ProjectId))
                .Where(item => !WorkspaceController.IsArchived(item.Status))
                .OrderByDescending(item => string.Equals(item.Id, _workspaceController.ActiveSession().Id, StringComparison.OrdinalIgnoreCase))
                .ThenByDescending(item => item.UpdatedAt)
                .Take(30))
            {
                yield return new QuickCommandViewModel($"session-{NormalizeCollaborationThreadKey(session.Id)}", $"会话：{session.Title}", "session");
            }
            yield break;
        }

        var project = _state.Projects.FirstOrDefault(item => string.Equals(item.Id, projectScopeId, StringComparison.OrdinalIgnoreCase));
        if (project is null)
        {
            yield break;
        }
        yield return new QuickCommandViewModel($"project-{NormalizeCollaborationThreadKey(project.Id)}", "项目全部会话", "project");
        foreach (var session in _state.Sessions
            .Where(item => string.Equals(item.ProjectId, project.Id, StringComparison.OrdinalIgnoreCase))
            .Where(item => !WorkspaceController.IsArchived(item.Status))
            .OrderByDescending(item => string.Equals(item.Id, _workspaceController.ActiveSession().Id, StringComparison.OrdinalIgnoreCase))
            .ThenByDescending(item => item.UpdatedAt)
            .Take(30))
        {
            yield return new QuickCommandViewModel($"session-{NormalizeCollaborationThreadKey(session.Id)}", $"会话：{session.Title}", "session");
        }
    }

    private string CollaborationProjectScopeForThread(string threadId)
    {
        var normalized = (threadId ?? "").Trim();
        if (normalized.StartsWith("project-", StringComparison.OrdinalIgnoreCase))
        {
            var key = normalized[8..];
            var project = _state.Projects.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), key, StringComparison.OrdinalIgnoreCase));
            return project?.Id ?? CollaborationChatsScopeId;
        }
        if (normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            var key = normalized[8..];
            var session = _state.Sessions.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), key, StringComparison.OrdinalIgnoreCase));
            return string.IsNullOrWhiteSpace(session?.ProjectId) ? CollaborationChatsScopeId : session.ProjectId!;
        }
        return string.IsNullOrWhiteSpace(_workspaceController.WorkspaceProjectContextId) ? CollaborationChatsScopeId : _workspaceController.WorkspaceProjectContextId;
    }

    private string CollaborationSessionScopeForThread(string threadId, string projectScopeId)
    {
        var normalized = (threadId ?? "").Trim();
        if (normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            return normalized;
        }
        if (normalized.StartsWith("project-", StringComparison.OrdinalIgnoreCase))
        {
            return normalized;
        }
        return string.Equals(projectScopeId, CollaborationChatsScopeId, StringComparison.OrdinalIgnoreCase)
            ? $"project-{NormalizeCollaborationThreadKey(CollaborationChatsScopeId)}"
            : $"project-{NormalizeCollaborationThreadKey(projectScopeId)}";
    }

    private IEnumerable<QuickCommandViewModel> BuildCollaborationContextOptions(string activeThread)
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        void AddOption(List<QuickCommandViewModel> items, string id, string title, string command)
        {
            if (string.IsNullOrWhiteSpace(id) || !seen.Add(id))
            {
                return;
            }
            items.Add(new QuickCommandViewModel(id, title, command));
        }

        var result = new List<QuickCommandViewModel>();
        var active = _workspaceController.ActiveSession();
        var activeProject = _workspaceController.ProjectForSession(active);
        var activeProjectId = activeProject?.Id ?? "";
        var selectedTaskId = WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue as string ?? WorkspaceSidebar.TasksList.SelectedValue as string;

        if (!string.IsNullOrWhiteSpace(activeProjectId))
        {
            AddOption(result, $"project-{NormalizeCollaborationThreadKey(activeProjectId)}", $"项目：{activeProject!.Title}", "当前会话所属项目");
        }
        if (!string.IsNullOrWhiteSpace(active.Id))
        {
            AddOption(result, $"session-{NormalizeCollaborationThreadKey(active.Id)}", $"会话：{active.Title}", string.IsNullOrWhiteSpace(activeProjectId) ? "当前 Chats 会话" : "当前项目会话");
        }
        foreach (var task in _state.Tasks
            .Where(item => !string.IsNullOrWhiteSpace(selectedTaskId) && string.Equals(item.Id, selectedTaskId, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(item => item.UpdatedAt))
        {
            AddOption(result, $"task-{NormalizeCollaborationThreadKey(task.Id)}", $"任务：{task.Title}", task.Id);
        }
        if (!string.IsNullOrWhiteSpace(activeProjectId))
        {
            foreach (var session in _state.Sessions
                .Where(item => string.Equals(item.ProjectId, activeProjectId, StringComparison.OrdinalIgnoreCase))
                .OrderByDescending(item => string.Equals(item.Id, active.Id, StringComparison.OrdinalIgnoreCase))
                .ThenByDescending(item => item.UpdatedAt)
                .Take(20))
            {
                AddOption(result, $"session-{NormalizeCollaborationThreadKey(session.Id)}", $"会话：{session.Title}", activeProject!.Title);
            }
        }
        foreach (var thread in CollaborationMessages.Select(item => item.Target).Where(item => !string.IsNullOrWhiteSpace(item)).Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (IsDeletedCollaborationThread(thread) || !CollaborationThreadInCurrentScope(thread, activeThread))
            {
                continue;
            }
            AddOption(result, thread, CollaborationThreadTitle(thread), "已有协作线程");
        }
        if (!string.IsNullOrWhiteSpace(activeThread) && !IsDeletedCollaborationThread(activeThread))
        {
            AddOption(result, activeThread, CollaborationThreadTitle(activeThread), "当前协作线程");
        }
        AddOption(result, DefaultCollaborationThreadId(), "当前上下文默认", "任务 > 项目 > 会话");
        return result;
    }

    internal void SetActiveCollaborationThread(string threadId)
    {
        if (string.IsNullOrWhiteSpace(threadId))
        {
            return;
        }
        _activeCollaborationThreadId = threadId.Trim();
        SyncWorkspaceProjectContextFromCollaborationThread(_activeCollaborationThreadId);
        WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text = _activeCollaborationThreadId;
        _lastCollaborationChatSignature = "";
        _lastCollaborationContextSignature = "";
        _lastCollaborationThreadSignature = "";
    }

    private void SyncWorkspaceProjectContextFromCollaborationThread(string threadId)
    {
        var normalized = (threadId ?? "").Trim();
        if (normalized.StartsWith("project-", StringComparison.OrdinalIgnoreCase))
        {
            var projectKey = normalized[8..];
            if (string.Equals(projectKey, NormalizeCollaborationThreadKey(CollaborationChatsScopeId), StringComparison.OrdinalIgnoreCase))
            {
                _workspaceController.ClearWorkspaceProjectContextId();
                return;
            }
            var project = _state.Projects.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), projectKey, StringComparison.OrdinalIgnoreCase));
            _workspaceController.SetWorkspaceProjectContextId(project?.Id ?? _workspaceController.WorkspaceProjectContextId);
            return;
        }
        if (normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            var sessionKey = normalized[8..];
            var session = _state.Sessions.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), sessionKey, StringComparison.OrdinalIgnoreCase));
            _workspaceController.SetWorkspaceProjectContextId(session?.ProjectId ?? "");
        }
    }

    internal string DefaultCollaborationThreadId()
    {
        var selectedTaskId = WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue as string ?? WorkspaceSidebar.TasksList.SelectedValue as string;
        if (!string.IsNullOrWhiteSpace(selectedTaskId)
            && _state.Tasks.Any(task => string.Equals(task.Id, selectedTaskId, StringComparison.OrdinalIgnoreCase)))
        {
            return $"task-{NormalizeCollaborationThreadKey(selectedTaskId)}";
        }

        var active = _workspaceController.ActiveSession();
        var project = _workspaceController.ProjectForSession(active);
        if (!string.IsNullOrWhiteSpace(_workspaceController.WorkspaceProjectContextId)
            && _state.Projects.Any(item => string.Equals(item.Id, _workspaceController.WorkspaceProjectContextId, StringComparison.OrdinalIgnoreCase)))
        {
            return $"project-{NormalizeCollaborationThreadKey(_workspaceController.WorkspaceProjectContextId)}";
        }
        if (project is not null && !string.IsNullOrWhiteSpace(project.Id))
        {
            return $"project-{NormalizeCollaborationThreadKey(project.Id)}";
        }
        return string.IsNullOrWhiteSpace(active.Id)
            ? $"desktop-collab-{DateTimeOffset.Now:yyyyMMdd-HHmmss}"
            : $"session-{NormalizeCollaborationThreadKey(active.Id)}";
    }

    private string FallbackCollaborationThreadAfterDelete(string deletedThreadId)
    {
        var existing = CollaborationThreads
            .FirstOrDefault(item => !string.Equals(item.Id, deletedThreadId, StringComparison.OrdinalIgnoreCase)
                && !IsDeletedCollaborationThread(item.Id));
        if (existing is not null)
        {
            return existing.Id;
        }
        var fallback = DefaultCollaborationThreadId();
        return string.Equals(fallback, deletedThreadId, StringComparison.OrdinalIgnoreCase) || IsDeletedCollaborationThread(fallback)
            ? $"thread-{DateTimeOffset.Now:yyyyMMdd-HHmmss}"
            : fallback;
    }

    private string CollaborationThreadTitle(string threadId)
    {
        var normalized = threadId.Trim();
        if (normalized.StartsWith("task-", StringComparison.OrdinalIgnoreCase))
        {
            var taskId = normalized[5..];
            var task = _state.Tasks.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), taskId, StringComparison.OrdinalIgnoreCase));
            return task is null ? $"任务：{taskId}" : $"任务：{task.Title}";
        }
        if (normalized.StartsWith("project-", StringComparison.OrdinalIgnoreCase))
        {
            var projectId = normalized[8..];
            if (string.Equals(projectId, NormalizeCollaborationThreadKey(CollaborationChatsScopeId), StringComparison.OrdinalIgnoreCase))
            {
                return "Chats 全部会话";
            }
            var project = _state.Projects.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), projectId, StringComparison.OrdinalIgnoreCase));
            return project is null ? $"项目：{projectId}" : $"项目：{project.Title}";
        }
        if (normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            var sessionId = normalized[8..];
            var session = _state.Sessions.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), sessionId, StringComparison.OrdinalIgnoreCase));
            return session is null ? $"会话：{sessionId}" : $"会话：{session.Title}";
        }
        if (normalized.StartsWith("thread-", StringComparison.OrdinalIgnoreCase))
        {
            return $"话题：{normalized[7..]}";
        }
        return normalized;
    }

    internal string CollaborationThreadStatus(string threadId)
    {
        return _collaborationThreadStatuses.TryGetValue(threadId, out var status) && !string.IsNullOrWhiteSpace(status)
            ? status
            : "active";
    }

    internal bool IsDeletedCollaborationThread(string threadId)
    {
        return !string.IsNullOrWhiteSpace(threadId)
            && _collaborationThreadStatuses.TryGetValue(threadId, out var status)
            && status.Equals("deleted", StringComparison.OrdinalIgnoreCase);
    }

    internal bool CollaborationThreadInCurrentScope(string threadId, string activeThread)
    {
        if (string.IsNullOrWhiteSpace(threadId) || IsDeletedCollaborationThread(threadId))
        {
            return false;
        }
        var normalized = threadId.Trim();
        if (string.Equals(normalized, activeThread, StringComparison.OrdinalIgnoreCase))
        {
            return CollaborationThreadEntityExists(normalized);
        }
        if (!CollaborationThreadEntityExists(normalized))
        {
            return false;
        }

        var activeScope = CollaborationScopeFromThread(activeThread);
        var activeProjectKey = activeScope.ProjectKey;
        var activeSessionKey = activeScope.SessionKey;

        if (normalized.StartsWith("project-", StringComparison.OrdinalIgnoreCase))
        {
            if (string.Equals(normalized[8..], NormalizeCollaborationThreadKey(CollaborationChatsScopeId), StringComparison.OrdinalIgnoreCase))
            {
                return string.IsNullOrWhiteSpace(activeProjectKey);
            }
            return !string.IsNullOrWhiteSpace(activeProjectKey)
                && string.Equals(normalized[8..], activeProjectKey, StringComparison.OrdinalIgnoreCase);
        }
        if (normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            var sessionKey = normalized[8..];
            if (!string.IsNullOrWhiteSpace(activeSessionKey) && string.Equals(sessionKey, activeSessionKey, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
            if (string.IsNullOrWhiteSpace(activeProjectKey))
            {
                return false;
            }
            return _state.Sessions.Any(session =>
                string.Equals(NormalizeCollaborationThreadKey(session.Id), sessionKey, StringComparison.OrdinalIgnoreCase)
                && string.Equals(NormalizeCollaborationThreadKey(session.ProjectId ?? ""), activeProjectKey, StringComparison.OrdinalIgnoreCase));
        }
        if (normalized.StartsWith("task-", StringComparison.OrdinalIgnoreCase))
        {
            var selectedTaskId = WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue as string ?? WorkspaceSidebar.TasksList.SelectedValue as string;
            return !string.IsNullOrWhiteSpace(selectedTaskId)
                && string.Equals(normalized[5..], NormalizeCollaborationThreadKey(selectedTaskId), StringComparison.OrdinalIgnoreCase);
        }

        return false;
    }

    private bool CollaborationThreadEntityExists(string threadId)
    {
        var normalized = (threadId ?? "").Trim();
        if (normalized.StartsWith("project-", StringComparison.OrdinalIgnoreCase))
        {
            var projectKey = normalized[8..];
            return string.Equals(projectKey, NormalizeCollaborationThreadKey(CollaborationChatsScopeId), StringComparison.OrdinalIgnoreCase)
                || _state.Projects.Any(project => string.Equals(NormalizeCollaborationThreadKey(project.Id), projectKey, StringComparison.OrdinalIgnoreCase));
        }
        if (normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            var sessionKey = normalized[8..];
            return _state.Sessions.Any(session => string.Equals(NormalizeCollaborationThreadKey(session.Id), sessionKey, StringComparison.OrdinalIgnoreCase));
        }
        if (normalized.StartsWith("task-", StringComparison.OrdinalIgnoreCase))
        {
            var taskKey = normalized[5..];
            return _state.Tasks.Any(task => string.Equals(NormalizeCollaborationThreadKey(task.Id), taskKey, StringComparison.OrdinalIgnoreCase));
        }
        return true;
    }

    private (string ProjectKey, string SessionKey) CollaborationScopeFromThread(string threadId)
    {
        var normalized = (threadId ?? "").Trim();
        if (normalized.StartsWith("project-", StringComparison.OrdinalIgnoreCase))
        {
            var projectKey = normalized[8..];
            return string.Equals(projectKey, NormalizeCollaborationThreadKey(CollaborationChatsScopeId), StringComparison.OrdinalIgnoreCase)
                ? ("", "")
                : (projectKey, "");
        }
        if (normalized.StartsWith("session-", StringComparison.OrdinalIgnoreCase))
        {
            var sessionKey = normalized[8..];
            var session = _state.Sessions.FirstOrDefault(item => string.Equals(NormalizeCollaborationThreadKey(item.Id), sessionKey, StringComparison.OrdinalIgnoreCase));
            return string.IsNullOrWhiteSpace(session?.ProjectId)
                ? ("", sessionKey)
                : (NormalizeCollaborationThreadKey(session.ProjectId), sessionKey);
        }
        if (!string.IsNullOrWhiteSpace(_workspaceController.WorkspaceProjectContextId))
        {
            return (NormalizeCollaborationThreadKey(_workspaceController.WorkspaceProjectContextId), "");
        }
        var active = _workspaceController.ActiveSession();
        var activeProject = _workspaceController.ProjectForSession(active);
        return string.IsNullOrWhiteSpace(activeProject?.Id)
            ? ("", NormalizeCollaborationThreadKey(active.Id))
            : (NormalizeCollaborationThreadKey(activeProject.Id), NormalizeCollaborationThreadKey(active.Id));
    }

    private static string AgentRouteLabel(ActionItemViewModel item)
    {
        return string.IsNullOrWhiteSpace(item.OwnerRole) ? item.Type : CollaborationAgentDisplay(item.OwnerRole);
    }

    private static string CollaborationMessagePreview(ActionItemViewModel item)
    {
        return ShortText(Regex.Replace((item.Meta ?? "").Trim(), @"\s+", " "), 46);
    }

    internal static bool IsHumanCollaborationMessage(ActionItemViewModel item)
    {
        if (!string.IsNullOrWhiteSpace(item.OwnerRole))
        {
            return IsHumanAgentId(item.OwnerRole);
        }
        return item.Type.Contains("human_desktop", StringComparison.OrdinalIgnoreCase)
            || item.Type.Contains("user", StringComparison.OrdinalIgnoreCase)
            || item.Type.Contains("我", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsOpenCollaborationMessage(ActionItemViewModel item)
    {
        return string.IsNullOrWhiteSpace(item.Priority)
            || item.Priority.Equals("open", StringComparison.OrdinalIgnoreCase)
            || item.Priority.Equals("pending", StringComparison.OrdinalIgnoreCase);
    }

    private static string CollaborationMessageTitle(string fromAgent, string role)
    {
        return $"{CollaborationAgentDisplay(fromAgent)} · {CollaborationRoleLabel(role)}";
    }

    private static string CollaborationDisplayContent(string value, int maxLength)
    {
        var text = StripCollaborationToolPayloads(value ?? "");
        text = StripCollaborationEnvelope(text);
        text = FriendlyCollaborationText(text);
        return string.IsNullOrWhiteSpace(text) ? "" : ShortText(text.Trim(), maxLength);
    }

    internal static string StripCollaborationToolPayloads(string value)
    {
        var text = value ?? "";
        const string marker = "spiritkin_tool_call";
        var searchFrom = 0;
        while (searchFrom < text.Length)
        {
            var markerIndex = text.IndexOf(marker, searchFrom, StringComparison.OrdinalIgnoreCase);
            if (markerIndex < 0)
            {
                break;
            }
            var objectStart = text.LastIndexOf('{', markerIndex);
            if (objectStart < 0)
            {
                searchFrom = markerIndex + marker.Length;
                continue;
            }
            var objectEnd = FindCollaborationPayloadObjectEnd(text, objectStart);
            if (objectEnd <= objectStart)
            {
                text = text[..objectStart];
                break;
            }
            text = text.Remove(objectStart, objectEnd - objectStart);
            searchFrom = Math.Max(0, objectStart - 1);
        }
        text = Regex.Replace(text, @"```(?:json)?\s*```", "", RegexOptions.IgnoreCase);
        text = Regex.Replace(text, @"(?:\r?\n){3,}", Environment.NewLine + Environment.NewLine);
        return text.Trim();
    }

    private static int FindCollaborationPayloadObjectEnd(string text, int objectStart)
    {
        var depth = 0;
        var quote = '\0';
        var escaped = false;
        for (var index = objectStart; index < text.Length; index++)
        {
            var ch = text[index];
            if (quote != '\0')
            {
                if (escaped)
                {
                    escaped = false;
                    continue;
                }
                if (ch == '\\')
                {
                    escaped = true;
                    continue;
                }
                if (ch == quote)
                {
                    quote = '\0';
                }
                continue;
            }
            if (ch is '\'' or '"')
            {
                quote = ch;
                continue;
            }
            if (ch == '{')
            {
                depth++;
                continue;
            }
            if (ch != '}')
            {
                continue;
            }
            depth--;
            if (depth == 0)
            {
                return index + 1;
            }
        }
        return -1;
    }

    private static string StripCollaborationEnvelope(string value)
    {
        var lines = (value ?? "")
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.None)
            .Select(line => line.Trim())
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .Where(line => !Regex.IsMatch(line, @"^(open|closed|done|pending|active)\s*·\s*.+->.+", RegexOptions.IgnoreCase))
            .Where(line => !Regex.IsMatch(line, @"^session-session_[\w.-]+$", RegexOptions.IgnoreCase))
            .Where(line => !Regex.IsMatch(line, @"^thread-[\w.-]+$", RegexOptions.IgnoreCase))
            .ToList();
        return string.Join(Environment.NewLine, lines).Trim();
    }

    internal static string CollaborationAgentDisplay(string value)
    {
        var key = ComposerController.NormalizeAgentMentionKey(value);
        return key switch
        {
            "codex" or "codexcli" => "Codex",
            "claude" or "claudecode" or "claudecodecli" or "cc" => "Claude Code",
            "maintext" or "spirit" => "Spirit",
            "all" or "全部" or "所有" => "全部",
            "externalreviewer" or "review" or "reviewer" => "评审",
            "human" or "user" or "me" or "humandesktop" or "我" => "我",
            "unassigned" or "" => "未分配",
            _ => UiDisplayText.HumanizeIdentifier(value ?? "", value ?? ""),
        };
    }

    private static string CollaborationRoleLabel(string value)
    {
        return (value ?? "").Trim().ToLowerInvariant() switch
        {
            "question" => "提问",
            "answer" => "回复",
            "review_request" => "请求评审",
            "review_result" => "评审结果",
            "decision" => "决策",
            "status" => "状态",
            "note" or "" => "消息",
            _ => UiDisplayText.HumanizeIdentifier(value ?? "", value ?? ""),
        };
    }

    private static string CollaborationTaskStatusLabel(string value)
    {
        return (value ?? "").Trim().ToLowerInvariant() switch
        {
            "active" or "running" => "进行中",
            "pending" or "queued" => "等待中",
            "done" or "completed" => "已完成",
            "blocked" => "受阻",
            "archived" => "已归档",
            _ => string.IsNullOrWhiteSpace(value) ? "进行中" : UiDisplayText.HumanizeIdentifier(value ?? "", value ?? ""),
        };
    }

    private static string FriendlyCollaborationText(string value)
    {
        var text = (value ?? "").Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return "";
        }
        var replacements = new (string From, string To)[]
        {
            ("Codex collaboration control plane backend", "Codex 协作后端"),
            ("Initial backend/API landing for separate Collaboration page.", "协作页后端接口初版。"),
            ("backend/API landing", "后端接口"),
            ("separate Collaboration page", "独立协作页"),
            ("Collaboration page", "协作页"),
            ("control plane backend", "后端协调服务"),
            ("control plane", "协调服务"),
            ("collaboration backend", "协作后端"),
        };
        foreach (var (from, to) in replacements)
        {
            text = text.Replace(from, to, StringComparison.OrdinalIgnoreCase);
        }
        return Regex.Replace(text, @"[ \t]+", " ").Trim();
    }

    private static bool IsHumanAgentId(string value)
    {
        var key = ComposerController.NormalizeAgentMentionKey(value);
        return key is "human" or "user" or "me" or "humandesktop" or "我";
    }

    internal static string NormalizeCollaborationThreadKey(string value)
    {
        var normalized = Regex.Replace((value ?? "").Trim().ToLowerInvariant(), @"[^\w\u4e00-\u9fff.-]+", "-").Trim('-');
        return string.IsNullOrWhiteSpace(normalized) ? "default" : normalized;
    }

    private static double ReadJsonDouble(JsonElement element, string key, double fallback = 0)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
        {
            return number;
        }
        return double.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : fallback;
    }

    private static string BuildCollaborationStateSignature(JsonElement state)
    {
        var parts = new List<string>();
        foreach (var key in new[] { "overview", "thread_states", "active_tasks", "recent_messages", "file_claims", "recent_decisions", "recent_reviews", "source_files" })
        {
            if (state.TryGetProperty(key, out var value))
            {
                parts.Add($"{key}:{value.GetRawText()}");
            }
        }
        return string.Join("\n", parts);
    }

    internal string AutoWorkerStatusSuffix(IEnumerable<string> agents)
    {
        var targets = CollaborationWorkerTargetsForAgents(agents).ToList();
        return targets.Count == 0 ? "" : $" worker：{string.Join(", ", targets)}";
    }

    internal void CollaborationTasksList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (WorkbenchShell.ManagementPanels.CollaborationTasksList.SelectedItem is ActionItemViewModel selected)
        {
            SetActiveCollaborationThread($"task-{NormalizeCollaborationThreadKey(selected.Id)}");
            PopulateCollaborationTaskEditor(selected);
        }
    }

    private void PopulateCollaborationTaskEditor(ActionItemViewModel selected)
    {
        if (string.IsNullOrWhiteSpace(selected.Command))
        {
            WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text = selected.Id;
            return;
        }
        try
        {
            using var doc = JsonDocument.Parse(selected.Command);
            var task = doc.RootElement;
            WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text = $"task-{NormalizeCollaborationThreadKey(ReadJsonString(task, "task_id", selected.Id))}";
            WorkbenchShell.ManagementPanels.CollaborationTaskOwnerBox.Text = ReadJsonString(task, "owner", "codex");
            WorkbenchShell.ManagementPanels.CollaborationTaskTitleBox.Text = ReadJsonString(task, "title");
            WorkbenchShell.ManagementPanels.CollaborationTaskScopeBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(task, "scope"));
            WorkbenchShell.ManagementPanels.CollaborationAllowedFilesBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(task, "allowed_files"));
            WorkbenchShell.ManagementPanels.CollaborationBlockedFilesBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(task, "blocked_files"));
            WorkbenchShell.ManagementPanels.CollaborationVerificationBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(task, "verification_commands"));
            WorkbenchShell.ManagementPanels.CollaborationTaskNoteBox.Text = ReadJsonString(task, "note");
        }
        catch (JsonException)
        {
            WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text = selected.Id;
        }
    }

    internal string EnsureCollaborationTaskId()
    {
        if (_collaborationChatActive)
        {
            return _composer.BindCurrentSessionCollaborationThread();
        }
        var taskId = CurrentCollaborationTaskId();
        if (!string.IsNullOrWhiteSpace(taskId))
        {
            _activeCollaborationThreadId = taskId;
            return taskId;
        }
        taskId = DefaultCollaborationThreadId();
        SetActiveCollaborationThread(taskId);
        return taskId;
    }

    internal string CurrentCollaborationTaskId()
    {
        if (_collaborationChatActive)
        {
            return CurrentSessionCollaborationThreadId();
        }
        var taskId = WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text.Trim();
        return string.IsNullOrWhiteSpace(taskId) ? _activeCollaborationThreadId : taskId;
    }

    internal string CurrentSessionCollaborationThreadId()
    {
        return $"session-{NormalizeCollaborationThreadKey(_workspaceController.ActiveSession().Id)}";
    }

    private string CollaborationOwner()
    {
        var owner = WorkbenchShell.ManagementPanels.CollaborationTaskOwnerBox.Text.Trim();
        return string.IsNullOrWhiteSpace(owner) ? "wpf_desktop" : owner;
    }

    private string CollaborationMessageFrom()
    {
        var from = WorkbenchShell.ManagementPanels.CollaborationMessageFromBox.Text.Trim();
        return string.IsNullOrWhiteSpace(from) ? CollaborationOwner() : from;
    }

    private void SyncCollaborationMessageFormFromComposer()
    {
        var from = ComboText(ChatWorkspace.CollaborationComposerFromBox).Trim();
        var to = ComboText(ChatWorkspace.CollaborationComposerToBox).Trim();
        var role = ComboText(ChatWorkspace.CollaborationComposerRoleBox).Trim();
        WorkbenchShell.ManagementPanels.CollaborationMessageFromBox.Text = string.IsNullOrWhiteSpace(from) ? "user" : from;
        SetComboText(WorkbenchShell.ManagementPanels.CollaborationMessageToBox, string.IsNullOrWhiteSpace(to) ? "claude_code" : to);
        SetComboText(WorkbenchShell.ManagementPanels.CollaborationMessageRoleBox, string.IsNullOrWhiteSpace(role) ? "question" : role);
    }

    private const string CollaborationMentionPattern = @"(?<![\w./-])@(?<name>[A-Za-z0-9_.:/\-\u4e00-\u9fff]{1,128})";

    internal static IReadOnlyList<string> ExtractCollaborationMentionNames(string? text)
    {
        return Regex.Matches(text ?? "", CollaborationMentionPattern)
            .Cast<Match>()
            // A trailing ':' is message punctuation (`@Codex: ...`), while an
            // internal ':' remains part of model ids such as `qwen3-vl:4b`.
            .Select(match => match.Groups["name"].Value.TrimEnd(':'))
            .Where(name => !string.IsNullOrWhiteSpace(name))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    internal static bool ContainsExplicitCollaborationParticipantName(string? text, string? alias)
    {
        var raw = (alias ?? "").Trim().TrimStart('@');
        if (string.IsNullOrWhiteSpace(raw))
        {
            return false;
        }
        var compact = ComposerController.NormalizeAgentMentionKey(raw);
        if (string.IsNullOrWhiteSpace(compact)
            || compact is "all" or "全部" or "所有" or "review" or "评审" or "code" or "cc")
        {
            return false;
        }
        var hasCjk = raw.Any(ch => ch is >= '\u4e00' and <= '\u9fff');
        if (hasCjk)
        {
            return compact.Length >= 2 && (text ?? "").Contains(raw, StringComparison.OrdinalIgnoreCase);
        }
        if (compact.Length < 5 && !string.Equals(compact, "gpt", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        return Regex.IsMatch(text ?? "", $@"(?<![A-Za-z0-9_]){Regex.Escape(raw)}(?![A-Za-z0-9_])", RegexOptions.IgnoreCase);
    }

    private CollaborationComposerRoute ParseCollaborationComposerRoute(string rawText)
    {
        var content = rawText.Trim();
        var mentions = new List<string>();
        foreach (var mentionName in ExtractCollaborationMentionNames(content))
        {
            var agent = ResolveCollaborationAgentMention(mentionName);
            if (!string.IsNullOrWhiteSpace(agent) && !mentions.Contains(agent, StringComparer.OrdinalIgnoreCase))
            {
                mentions.Add(agent);
            }
        }
        foreach (var option in _collaborationParticipantOptions.Where(item => item.CanChat))
        {
            var names = new[] { option.ParticipantId, option.Label, option.Mention.TrimStart('@') }
                .Concat(option.Aliases ?? Array.Empty<string>());
            var matchedName = names.FirstOrDefault(name => ContainsExplicitCollaborationParticipantName(content, name));
            if (string.IsNullOrWhiteSpace(matchedName))
            {
                continue;
            }
            var aliasOwner = ResolveCollaborationAgentMention(matchedName);
            if (!string.IsNullOrWhiteSpace(aliasOwner)
                && !string.Equals(aliasOwner, option.ParticipantId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (!mentions.Contains(option.ParticipantId, StringComparer.OrdinalIgnoreCase))
            {
                mentions.Add(option.ParticipantId);
            }
        }
        // Registry state can arrive one refresh behind the composer. Keep the
        // built-in main-agent name deterministic during that short window.
        if (ContainsExplicitCollaborationParticipantName(content, "Spirit")
            && !mentions.Contains(DefaultSessionCollaborationAgent, StringComparer.OrdinalIgnoreCase))
        {
            mentions.Add(DefaultSessionCollaborationAgent);
        }


        var role = content.Contains("@review", StringComparison.OrdinalIgnoreCase) || content.Contains("@评审", StringComparison.OrdinalIgnoreCase)
            ? "review_request"
            : "question";
        var cleaned = StripCollaborationComposerMentions(content);
        cleaned = string.IsNullOrWhiteSpace(cleaned) ? content : cleaned;
        // \u4f1a\u8bdd\u6210\u5458\u5236\uff1a@ \u8fc7\u4e00\u6b21\u7684\u6a21\u578b\u52a0\u5165\u5f53\u524d\u4f1a\u8bdd\uff1b\u65e0 @ \u7684\u6d88\u606f\u53ea\u53d1\u7ed9\u5df2\u52a0\u5165\u7684\u6210\u5458\uff0c\u672a\u52a0\u5165\u7684\u4e0d\u6253\u6270\u3002
        // \u672c\u5730\u4e3b\u6a21\u578b main_text \u662f\u9ed8\u8ba4\u6210\u5458\uff08\u9664\u975e\u7528\u6237\u663e\u5f0f\u79fb\u9664\u8fc7\uff09\u3002
        // \u9ed8\u8ba4\u6210\u5458\u548c @ \u6210\u5458\u5fc5\u987b\u5408\u5e76\u6210\u4e00\u6b21\u767b\u8bb0\uff1a\u5206\u4e24\u6b21\u767b\u8bb0\u4f1a\u89e6\u53d1\u4e24\u6b21\u4fdd\u5b58\uff0c
        // \u7b2c\u4e00\u6b21\u4fdd\u5b58\u7684\u670d\u52a1\u7aef\u56de\u5305\u4f1a\u8986\u76d6\u672c\u5730\u72b6\u6001\uff0c\u628a\u7b2c\u4e8c\u6279\u521a\u52a0\u5165\u7684\u6210\u5458\u51b2\u6389\uff08\u4e22\u53c2\u4e0e\u8005\u7ade\u6001\uff09\u3002
        var register = new List<string>();
        var session = _workspaceController.ActiveSession();
        session.CollaborationOptOut ??= new List<string>();
        if (!session.CollaborationOptOut.Contains(DefaultSessionCollaborationAgent, StringComparer.OrdinalIgnoreCase))
        {
            register.Add(DefaultSessionCollaborationAgent);
        }
        string[] targets;
        if (mentions.Count == 0)
        {
            RegisterSessionCollaborationAgents(register);
            targets = SessionCollaborationAgents();
        }
        else if (mentions.Contains("all", StringComparer.OrdinalIgnoreCase))
        {
            targets = CollaborationBroadcastTargets();
            RegisterSessionCollaborationAgents(register.Concat(targets));
        }
        else
        {
            targets = mentions
                .Where(agent => !IsHumanAgentId(agent))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();
            RegisterSessionCollaborationAgents(register.Concat(targets));
            targets = targets.Length > 0 ? targets : SessionCollaborationAgents();
        }
        return new CollaborationComposerRoute(
            CurrentSessionCollaborationThreadId(),
            targets,
            role,
            cleaned);
    }

    private string StripCollaborationComposerMentions(string content)
    {
        var cleaned = content ?? "";
        var aliases = _collaborationParticipantOptions
            .SelectMany(option => new[] { option.ParticipantId, option.Label, option.Mention.TrimStart('@') }
                .Concat(option.Aliases ?? Array.Empty<string>()))
            .Where(alias => !string.IsNullOrWhiteSpace(alias))
            .Select(alias => alias!)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderByDescending(alias => alias.Length);
        foreach (var alias in aliases)
        {
            cleaned = Regex.Replace(
                cleaned,
                $@"(?<![\w./-])@{Regex.Escape(alias)}(?=$|[\s,，。！？!?;；:：])",
                "",
                RegexOptions.IgnoreCase);
        }
        cleaned = Regex.Replace(cleaned, CollaborationMentionPattern, "").Trim();
        cleaned = Regex.Replace(cleaned, @"^\s*[:：,，]\s*", "");
        return string.IsNullOrWhiteSpace(cleaned) ? (content ?? "").Trim() : cleaned;
    }

    internal const string DefaultSessionCollaborationAgent = "main_text";

    internal string[] SessionCollaborationAgents()
    {
        var session = _workspaceController.ActiveSession();
        return (session.CollaborationAgents ?? new List<string>())
            .Where(agent => !string.IsNullOrWhiteSpace(agent) && !IsHumanAgentId(agent) && !string.Equals(agent, "all", StringComparison.OrdinalIgnoreCase))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private void RegisterSessionCollaborationAgents(IEnumerable<string> agents)
    {
        var session = _workspaceController.ActiveSession();
        session.CollaborationAgents ??= new List<string>();
        session.CollaborationOptOut ??= new List<string>();
        var changed = false;
        foreach (var agent in agents)
        {
            if (string.IsNullOrWhiteSpace(agent)
                || IsHumanAgentId(agent)
                || string.Equals(agent, "all", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            var optOutRemoved = session.CollaborationOptOut.RemoveAll(item => string.Equals(item, agent, StringComparison.OrdinalIgnoreCase)) > 0;
            if (!session.CollaborationAgents.Contains(agent, StringComparer.OrdinalIgnoreCase))
            {
                session.CollaborationAgents.Add(agent);
                changed = true;
            }
            changed = changed || optOutRemoved;
        }
        if (changed)
        {
            session.UpdatedAt = NowSeconds();
            _ = SaveStateAsync();
        }
    }

    // 仅用于显式 @all/@全部：后端不会把字面量 "all" 扇出给模型，必须在客户端解析成就绪参与者的具体 id。
    private string[] CollaborationBroadcastTargets()
    {
        var targets = _collaborationParticipantOptions
            .Where(item => item.CanChat
                && !string.Equals(item.Kind, "worker", StringComparison.OrdinalIgnoreCase)
                && string.Equals(item.Status, "ready", StringComparison.OrdinalIgnoreCase))
            .Select(item => item.ParticipantId)
            .Where(id => !string.IsNullOrWhiteSpace(id) && !IsHumanAgentId(id))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        return targets.Length > 0 ? targets : new[] { "all" };
    }

    internal bool IsCollaborationAddressedMessage(string rawText)
    {
        foreach (var mentionName in ExtractCollaborationMentionNames(rawText))
        {
            var agent = ResolveCollaborationAgentMention(mentionName);
            if (!string.IsNullOrWhiteSpace(agent) && !IsHumanAgentId(agent))
            {
                return true;
            }
        }
        foreach (var option in _collaborationParticipantOptions.Where(item => item.CanChat))
        {
            var names = new[] { option.ParticipantId, option.Label, option.Mention.TrimStart('@') }
                .Concat(option.Aliases ?? Array.Empty<string>());
            var matchedName = names.FirstOrDefault(name => ContainsExplicitCollaborationParticipantName(rawText, name));
            if (string.IsNullOrWhiteSpace(matchedName))
            {
                continue;
            }
            var aliasOwner = ResolveCollaborationAgentMention(matchedName);
            if ((string.IsNullOrWhiteSpace(aliasOwner)
                    || string.Equals(aliasOwner, option.ParticipantId, StringComparison.OrdinalIgnoreCase)))
            {
                return true;
            }
        }
        if (ContainsExplicitCollaborationParticipantName(rawText, "Spirit"))
        {
            return true;
        }
        return false;
    }

    internal string ResolveCollaborationAgentMention(string value)
    {
        var key = ComposerController.NormalizeAgentMentionKey(value);
        if (!string.IsNullOrWhiteSpace(key) && _collaborationParticipantAliases.TryGetValue(key, out var participantId))
        {
            return participantId;
        }
        var fallback = NormalizeCollaborationAgentMention(value);
        if (!string.IsNullOrWhiteSpace(fallback))
        {
            return fallback;
        }
        return "";
    }

    private static string NormalizeCollaborationAgentMention(string value)
    {
        var key = ComposerController.NormalizeAgentMentionKey(value);
        return key switch
        {
            "我" or "human" or "user" or "me" or "humandesktop" => "human_desktop",
            "codex" or "codexcli" => "codex",
            "claude" or "claudecode" or "claudecli" or "cc" => "claude_code",
            "gpt" or "openai" or "cloudmodel" or "云端模型" => "cloud_model",
            "maintext" or "spirit" or "主agent" or "主模型" => "main_text",
            "programming" or "编程agent" or "编程" => "programming",
            "visionmodel" or "视觉agent" or "视觉" => "vision_model",
            "gamedevelopment" or "游戏agent" or "游戏开发" => "game_development",
            "ecommerce" or "电商agent" or "电商" => "ecommerce",
            "all" or "全部" or "所有" => "all",
            "review" or "reviewer" or "评审" or "审查" => "external_reviewer",
            _ => "",
        };
    }

    internal static string NormalizeCollaborationThreadId(string value)
    {
        var normalized = Regex.Replace((value ?? "").Trim().ToLowerInvariant(), @"[^\w\u4e00-\u9fff.-]+", "-").Trim('-');
        return string.IsNullOrWhiteSpace(normalized) ? "" : $"thread-{normalized}";
    }

    private sealed record CollaborationComposerRoute(string ThreadId, string[] ToAgents, string Role, string Content);

    private string CollaborationMessageContextPackPath()
    {
        var path = WorkbenchShell.ManagementPanels.CollaborationMessageContextPackBox.Text.Trim();
        return string.IsNullOrWhiteSpace(path) ? WorkbenchShell.ManagementPanels.CollaborationContextPackPathBox.Text.Trim() : path;
    }

    private static string JoinJsonArray(JsonElement element, string key, int limit)
    {
        var values = ReadJsonStringArray(element, key);
        if (values.Length == 0)
        {
            return "--";
        }
        var visible = values.Take(limit).ToArray();
        var suffix = values.Length > visible.Length ? $" +{values.Length - visible.Length}" : "";
        return string.Join(", ", visible) + suffix;
    }

    private static string ShortText(string value, int maxLength)
    {
        if (value.Length <= maxLength)
        {
            return value;
        }
        return value[..Math.Max(0, maxLength - 3)] + "...";
    }
}
