using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;

namespace SpiritKinDesktop;

internal sealed partial class ComposerController
{
    internal void StartAssistantWork(string userText, string steerText)
    {
        SetSetting(AssistantWorkStartedAtSetting, NowSeconds());
        SetSetting(AssistantWorkDurationSetting, 0);
        SetSetting(AssistantWorkLabelSetting, "Working");
        SetSetting(AssistantLastSteerSetting, steerText);
        SetSetting(AssistantCommandCountSetting, 0);
        SetSetting(AssistantDirtyCountStartSetting, CachedGitDirtyCount);
        RemoveSetting(AssistantWorkMessageIdSetting);
        var message = EnsureAssistantWorkMessage();
        if (message is not null)
        {
            // 工作链只投影后端真实事件（scheduler/plan/execution/tool）；不再注入任何写死的占位步骤。
            // 真实步骤由 AppendAssistantRuntimeWorkSteps 在收到 runtime 事件时填充。
            message.Subtitle = "running";
            message.WorkExpanded = true;
            RenderState();
        }
        _assistantWorkTimer.Start();
    }

    internal double CompleteAssistantWork()
    {
        var startedAt = GetSettingDouble(AssistantWorkStartedAtSetting);
        var duration = startedAt <= 0 ? 0 : Math.Max(0, NowSeconds() - startedAt);
        SetSetting(AssistantWorkDurationSetting, duration);
        SetSetting(AssistantWorkLabelSetting, "Worked");
        RemoveSetting(AssistantWorkStartedAtSetting);
        if (CurrentAssistantWorkMessage() is { } message)
        {
            // 仅当既无文本兜底也无真实结构化步骤时，才移除空工作气泡；避免丢弃只含 Steps 的真实事件。
            if (string.IsNullOrWhiteSpace(message.Text) && (message.Steps is null || message.Steps.Count == 0))
            {
                RemoveAssistantWorkMessage(message.Id);
                _assistantWorkTimer.Stop();
                return duration;
            }
            message.DurationSeconds = duration;
            message.Subtitle = "worked";
            message.UpdatedAt = NowSeconds();
            RenderState();
        }
        _assistantWorkTimer.Stop();
        return duration;
    }

    internal double CancelAssistantWork()
    {
        var startedAt = GetSettingDouble(AssistantWorkStartedAtSetting);
        var duration = startedAt <= 0 ? 0 : Math.Max(0, NowSeconds() - startedAt);
        SetSetting(AssistantWorkDurationSetting, duration);
        SetSetting(AssistantWorkLabelSetting, "Stopped");
        RemoveSetting(AssistantWorkStartedAtSetting);
        if (CurrentAssistantWorkMessage() is { } message)
        {
            if (string.IsNullOrWhiteSpace(message.Text) && (message.Steps is null || message.Steps.Count == 0))
            {
                RemoveAssistantWorkMessage(message.Id);
                _assistantWorkTimer.Stop();
                return duration;
            }

            foreach (var step in message.Steps ?? new List<DesktopWorkStep>())
            {
                var status = (step.Status ?? "").Trim().ToLowerInvariant();
                if (string.IsNullOrWhiteSpace(status) || status is "queued" or "pending" or "started" or "running" or "stream")
                {
                    step.Status = "cancelled";
                }
            }
            message.DurationSeconds = duration;
            message.Subtitle = "cancelled";
            message.UpdatedAt = NowSeconds();
            RenderState();
        }
        _assistantWorkTimer.Stop();
        return duration;
    }

    internal DesktopMessage? CurrentAssistantWorkMessage()
    {
        var workMessageId = GetSettingString(AssistantWorkMessageIdSetting);
        if (string.IsNullOrWhiteSpace(workMessageId))
        {
            return null;
        }
        return State.Sessions
            .SelectMany(session => session.Messages)
            .FirstOrDefault(message => string.Equals(message.Id, workMessageId, StringComparison.OrdinalIgnoreCase));
    }

    internal DesktopMessage? EnsureAssistantWorkMessage()
    {
        if (CurrentAssistantWorkMessage() is { } existing)
        {
            return existing;
        }
        if (GetSettingDouble(AssistantWorkStartedAtSetting) <= 0)
        {
            return null;
        }
        var workMessage = AddWorkMessage();
        workMessage.Subtitle = "running";
        workMessage.WorkExpanded = true;
        SetSetting(AssistantWorkMessageIdSetting, workMessage.Id);
        return workMessage;
    }

    internal void RemoveAssistantWorkMessage(string messageId)
    {
        if (string.IsNullOrWhiteSpace(messageId))
        {
            return;
        }
        foreach (var session in State.Sessions)
        {
            var removed = session.Messages.RemoveAll(message => string.Equals(message.Id, messageId, StringComparison.OrdinalIgnoreCase));
            if (removed <= 0)
            {
                continue;
            }
            session.UpdatedAt = NowSeconds();
            RemoveSetting(AssistantWorkMessageIdSetting);
            RenderState();
            return;
        }
    }

    internal void AppendAssistantWorkStep(string title, string detail = "", string key = "")
    {
        AppendAssistantWorkStep(title, detail, key, default);
    }

    internal void AppendAssistantWorkStep(string title, string detail, string key, TraceMeta meta, bool render = true)
    {
        if (!IsVisibleAssistantWorkStep(title))
        {
            return;
        }
        var block = FormatAssistantWorkBlock(title, detail);
        if (string.IsNullOrWhiteSpace(block))
        {
            return;
        }
        var message = EnsureAssistantWorkMessage();
        if (message is null)
        {
            return;
        }
        var kind = string.IsNullOrWhiteSpace(meta.StepKind)
            ? InferStepKind(title, detail)
            : meta.StepKind.Trim().ToLowerInvariant();
        var added = AppendStructuredWorkStep(message, kind, title, detail, key, meta);
        if (added)
        {
            AppendAssistantWorkBlock(message, block);
        }
        if (render)
        {
            RenderState();
        }
    }

    internal static string InferStepKind(string title, string detail)
    {
        var normalizedTitle = (title ?? "").Trim().ToLowerInvariant();
        var body = detail ?? "";
        if (normalizedTitle is "思考" or "thinking" or "thought" or "调用" or "call")
        {
            return "thinking";
        }
        if (body.Contains("需要确认", StringComparison.OrdinalIgnoreCase) || body.Contains("等待确认", StringComparison.OrdinalIgnoreCase))
        {
            return "permission";
        }
        if (body.Contains("提交到 agent 编排器", StringComparison.OrdinalIgnoreCase))
        {
            return "thinking";
        }
        if (body.Contains("执行桌面指令", StringComparison.OrdinalIgnoreCase) || body.Contains("结果：", StringComparison.OrdinalIgnoreCase) || body.Contains("已运行", StringComparison.OrdinalIgnoreCase))
        {
            return "result";
        }
        if (body.Contains("已变更", StringComparison.OrdinalIgnoreCase) || body.Contains("diff", StringComparison.OrdinalIgnoreCase))
        {
            return "diff";
        }
        return "command";
    }

    internal bool AppendStructuredWorkStep(
        DesktopMessage message,
        string kind,
        string title,
        string detail,
        string key = "",
        TraceMeta meta = default,
        bool streamLane = false,
        string reasoningVisibility = "")
    {
        var normalizedDetail = NormalizeWorkBlock(detail);
        var normalizedKey = (key ?? "").Trim();
        var step = new DesktopWorkStep
        {
            Kind = kind,
            Title = (title ?? "").Trim(),
            Detail = normalizedDetail,
            Key = normalizedKey,
            CreatedAt = NowSeconds(),
            Seq = meta.Seq,
            RunId = meta.RunId ?? "",
            EventId = meta.EventId ?? "",
            SpanId = meta.SpanId ?? "",
            ParentId = meta.ParentId ?? "",
            Status = meta.Status ?? "",
            IsTerminal = meta.IsTerminal,
            AgentId = meta.AgentId ?? "",
            // 主聊天模型推理同样是累计流。把 :reasoning span 标成泳道后，
            // 同段原位增长，工具边界后的续思考则保留为新的时间线节点。
            IsStreamLane = streamLane || IsReasoningStreamSpan(meta.SpanId),
            ReasoningVisibility = string.IsNullOrWhiteSpace(reasoningVisibility) && IsReasoningStreamSpan(meta.SpanId)
                ? "process"
                : (reasoningVisibility ?? "").Trim().ToLowerInvariant(),
            CallAgent = meta.CallAgent ?? "",
            CallModel = meta.CallModel ?? "",
            CallProvider = meta.CallProvider ?? "",
            CommandText = meta.CommandText ?? "",
            CommandOutput = meta.CommandOutput ?? "",
            ShellLabel = meta.ShellLabel ?? "",
        };
        message.Steps ??= new List<DesktopWorkStep>();
        // schema v1 幂等去重：event_id 全局唯一，优先于语义 key（replay/stream 重叠时同一事件只入一次）。
        if (!string.IsNullOrEmpty(step.EventId)
            && message.Steps.Any(existing => string.Equals(existing.EventId, step.EventId, StringComparison.OrdinalIgnoreCase)))
        {
            return false;
        }
        // 同 span 的后续生命周期事件（started→output→completed）必须放行入库，交给渲染层 CollapseBySpan
        // 折叠为单行状态跃迁；故仅在无 SpanId 时才走 key 去重。
        // 跨来源去重（WS 实时 vs HTTP 兜底）：无 span 但有结构化 key 时全局按 key 比对，
        // 不依赖中文文案，避免“模型选择 普通回答” vs “模型选择了 普通回答路径”重复。
        if (string.IsNullOrEmpty(step.SpanId)
            && !string.IsNullOrEmpty(normalizedKey)
            && message.Steps.Any(existing => string.Equals(existing.Key, normalizedKey, StringComparison.OrdinalIgnoreCase)))
        {
            return false;
        }
        if (TryMergeReasoningStreamStep(message, step))
        {
            message.UpdatedAt = NowSeconds();
            return true;
        }
        var last = message.Steps.LastOrDefault();
        var sameLifecycleSpan = last is not null
            && !string.IsNullOrWhiteSpace(step.SpanId)
            && string.Equals(last.SpanId, step.SpanId, StringComparison.OrdinalIgnoreCase);
        if (last is not null
            && string.Equals(last.Kind, step.Kind, StringComparison.OrdinalIgnoreCase)
            && string.Equals(last.Title, step.Title, StringComparison.OrdinalIgnoreCase)
            && string.Equals(last.Detail, step.Detail, StringComparison.OrdinalIgnoreCase)
            && (!sameLifecycleSpan || string.Equals(last.Status, step.Status, StringComparison.OrdinalIgnoreCase)))
        {
            return false;
        }
        message.Steps.Add(step);
        if (message.Steps.Count > 40)
        {
            message.Steps.RemoveRange(0, message.Steps.Count - 40);
        }
        message.UpdatedAt = NowSeconds();
        return true;
    }

    private static bool IsReasoningStreamSpan(string? spanId) =>
        (spanId ?? "").Trim().EndsWith(":reasoning", StringComparison.OrdinalIgnoreCase);

    internal static bool TryMergeReasoningStreamStep(DesktopMessage message, DesktopWorkStep incoming)
    {
        if (!incoming.IsStreamLane || !IsReasoningStreamSpan(incoming.SpanId))
        {
            return false;
        }

        var steps = message.Steps ??= new List<DesktopWorkStep>();
        var existingIndex = steps.FindLastIndex(existing =>
            existing.IsStreamLane
            && string.Equals(existing.SpanId, incoming.SpanId, StringComparison.OrdinalIgnoreCase)
            && string.Equals(existing.AgentId, incoming.AgentId, StringComparison.OrdinalIgnoreCase));
        if (existingIndex < 0)
        {
            return false;
        }

        // A command/call/diff marks a real event boundary. Reasoning that resumes
        // after it must become a new row instead of replacing the earlier thought.
        if (steps.Skip(existingIndex + 1).Any(IsReasoningStreamBoundary))
        {
            return false;
        }

        var existing = steps[existingIndex];
        var existingStatus = (existing.Status ?? "").Trim().ToLowerInvariant();
        var incomingStatus = (incoming.Status ?? "").Trim().ToLowerInvariant();
        if (existingStatus is "completed" or "failed" or "cancelled" or "canceled"
            && incomingStatus is "stream" or "running" or "started")
        {
            return false;
        }

        existing.Detail = incoming.Detail;
        existing.Key = incoming.Key;
        existing.CreatedAt = incoming.CreatedAt;
        existing.Seq = incoming.Seq;
        existing.EventId = incoming.EventId;
        existing.ParentId = incoming.ParentId;
        existing.Status = incoming.Status ?? "";
        existing.IsTerminal = incoming.IsTerminal;
        existing.ReasoningVisibility = incoming.ReasoningVisibility ?? "";
        return true;
    }

    private static bool IsReasoningStreamBoundary(DesktopWorkStep step)
    {
        var kind = (step.Kind ?? "").Trim().ToLowerInvariant();
        return kind is "call" or "command" or "diff" or "permission"
            || !string.IsNullOrWhiteSpace(step.CommandText);
    }

    internal void AppendAssistantWorkBlock(string text)
    {
        if (CurrentAssistantWorkMessage() is { } message)
        {
            AppendAssistantWorkBlock(message, text);
            RenderState();
        }
    }

    internal static void AppendAssistantWorkBlock(DesktopMessage message, string text)
    {
        var block = NormalizeWorkBlock(text);
        if (string.IsNullOrWhiteSpace(block))
        {
            return;
        }
        var existing = SplitWorkBlocks(message.Text)
            .ToList();
        if (!existing.Any(item => string.Equals(item, block, StringComparison.OrdinalIgnoreCase)))
        {
            existing.Add(block);
        }
        message.Text = string.Join($"{Environment.NewLine}{Environment.NewLine}", existing.TakeLast(12));
        message.UpdatedAt = NowSeconds();
    }

    internal static IEnumerable<string> SplitWorkBlocks(string text)
    {
        return Regex.Split(text ?? "", @"(?:\r?\n){2,}")
            .Select(NormalizeWorkBlock)
            .Where(item => !string.IsNullOrWhiteSpace(item));
    }

    internal static string NormalizeWorkBlock(string text)
    {
        var lines = (text ?? "")
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.None)
            .Select(line => string.Join(" ", line.Split(default(string[]), StringSplitOptions.RemoveEmptyEntries)))
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .ToList();
        return string.Join(Environment.NewLine, lines).Trim();
    }

    internal static string FormatAssistantWorkBlock(string title, string detail)
    {
        var normalized = (title ?? "").Trim().ToLowerInvariant();
        var clean = TrimStatusText((detail ?? "").Trim(), 360);
        if (string.IsNullOrWhiteSpace(clean))
        {
            return "";
        }
        var label = normalized is "工作指令" or "work" or "command"
            ? "工作指令"
            : normalized is "调用" or "call"
                ? "调用"
                : "思考";
        if (clean.StartsWith($"{label} · ", StringComparison.OrdinalIgnoreCase))
        {
            return clean;
        }
        if (clean.StartsWith($"{label}：", StringComparison.OrdinalIgnoreCase))
        {
            return $"{label} · {clean[(label.Length + 1)..].Trim()}";
        }
        return $"{label} · {clean}";
    }

    internal static bool IsVisibleAssistantWorkStep(string title)
    {
        var normalized = (title ?? "").Trim().ToLowerInvariant();
        return normalized is "思考" or "thinking" or "thought" or "调用" or "call" or "工作指令" or "work" or "command";
    }

    internal void TickAssistantWorkTimer()
    {
        var startedAt = GetSettingDouble(AssistantWorkStartedAtSetting);
        if (startedAt <= 0)
        {
            _assistantWorkTimer.Stop();
            return;
        }
        if (CurrentAssistantWorkMessage() is not { } message)
        {
            _assistantWorkTimer.Stop();
            return;
        }
        var elapsed = Math.Max(0, NowSeconds() - startedAt);
        message.DurationSeconds = elapsed;
        if (elapsed >= 45)
        {
            RenderState();
            return;
        }
        else if (elapsed >= 15)
        {
            RenderState();
            return;
        }
        RenderState();
    }

    internal void AddPostReplyArtifactMessages()
    {
        var commands = (int)GetSettingDouble(AssistantCommandCountSetting);
        if (commands > 0)
        {
            AppendAssistantWorkStep("工作指令", $"已运行 {commands} 条命令");
        }

        var dirtyStart = (int)GetSettingDouble(AssistantDirtyCountStartSetting);
        if (CachedGitDirtyCount > 0 && CachedGitDirtyCount != dirtyStart)
        {
            var delta = ChatWorkspace.InlineChangesMetaText.Text;
            var sample = string.Join(", ", GitChanges.Take(4).Select(item => item.Path));
            AppendAssistantWorkStep("工作指令", $"已变更 {CachedGitDirtyCount} 个文件{Environment.NewLine}{TrimStatusText($"{delta}; {sample}", 240)}");
        }
    }

    internal void AppendAssistantRuntimeWorkSteps(IReadOnlyList<RuntimeEvent> events)
    {
        foreach (var ev in events)
        {
            // HTTP 兜底路径与 WS 实时路径同样提取 schema v1 元字段；EventId 一致时与 WS 已入库步骤去重，
            // 使 HTTP 自动退化为 fallback（同事件不重复入库）。后端未落地 v1 时全空，回退语义 key 去重。
            var meta = ReadTraceMeta(ev.Payload, ev.Type);
            foreach (var step in DescribeRuntimeWorkSteps(ev))
            {
                AppendAssistantWorkStep(step.Title, step.Detail, step.Key, meta);
            }
        }
    }
}

