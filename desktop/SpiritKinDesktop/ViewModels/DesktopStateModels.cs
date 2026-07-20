using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Effects;

namespace SpiritKinDesktop;

public sealed class StateEnvelope
{
    public bool Ok { get; set; }
    public DesktopState? State { get; set; }
}

public sealed class CommandEnvelope
{
    public bool Ok { get; set; }
    public ReplyPayload? Reply { get; set; }
    public List<RuntimeEvent>? Events { get; set; }
    public bool CollaborationRedirect { get; set; }
    public CollaborationRedirectMessage? Message { get; set; }
}

public sealed class CollaborationRedirectMessage
{
    public string? MessageId { get; set; }
    public string? ThreadId { get; set; }
    public string? TaskId { get; set; }
    public string? FromAgent { get; set; }
    public List<string>? ToAgents { get; set; }
    public string? Role { get; set; }
    public string? Content { get; set; }
}

public sealed class ReplyPayload
{
    public string? Text { get; set; }
    public string? SpokenText { get; set; }
    public string? ResponseKind { get; set; }
    public bool RequiresConfirmation { get; set; }
}

public sealed class RuntimeEvent
{
    public string Type { get; set; } = "";
    public JsonElement Payload { get; set; }
}

public sealed class DesktopState
{
    public string SchemaVersion { get; set; } = "spiritkin.desktop_console.v1";
    public string RuntimeSchemaVersion { get; set; } = "v1";
    public int Revision { get; set; }
    public string ActiveSessionId { get; set; } = "session_default";
    public List<DesktopSession> Sessions { get; set; } = new();
    public List<DesktopItem> Projects { get; set; } = new();
    public List<DesktopItem> Tasks { get; set; } = new();
    public List<QuickCommand> QuickCommands { get; set; } = new();
    public List<DesktopEvent> Events { get; set; } = new();
    public Dictionary<string, object?>? Pending { get; set; }
    public Dictionary<string, object?>? LastExecution { get; set; }
    public Dictionary<string, object?>? LastRoute { get; set; }
    public Dictionary<string, object?> Settings { get; set; } = new();
    public double UpdatedAt { get; set; }
    public string UpdatedBy { get; set; } = "wpf_desktop";

    public static DesktopState CreateDefault()
    {
        var session = DefaultSession();
        return new DesktopState
        {
            ActiveSessionId = session.Id,
            Sessions = new List<DesktopSession> { session },
            UpdatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
        };
    }

    public static DesktopSession DefaultSession()
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        return new DesktopSession { Id = "session_default", Title = "主会话", Status = "active", CreatedAt = now, UpdatedAt = now, Messages = new List<DesktopMessage>() };
    }

    public DesktopState Normalized()
    {
        Sessions ??= new List<DesktopSession>();
        Projects ??= new List<DesktopItem>();
        Tasks ??= new List<DesktopItem>();
        QuickCommands ??= new List<QuickCommand>();
        Events ??= new List<DesktopEvent>();
        Settings ??= new Dictionary<string, object?>();
        if (Sessions.Count == 0)
        {
            Sessions.Add(DefaultSession());
        }
        if (!Sessions.Any(s => s.Id == ActiveSessionId))
        {
            ActiveSessionId = Sessions[0].Id;
        }
        foreach (var session in Sessions)
        {
            session.Messages ??= new List<DesktopMessage>();
        }
        if (QuickCommands.Count == 0)
        {
            QuickCommands.AddRange(new[]
            {
                new QuickCommand { Id = "quick_scan_local_software", Title = "扫描本机软件", Command = "扫描本机软件", UpdatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() },
                new QuickCommand { Id = "quick_open_browser", Title = "打开浏览器", Command = "打开浏览器", UpdatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() },
                new QuickCommand { Id = "quick_confirm_execution", Title = "确认执行", Command = "确认执行", UpdatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() },
                new QuickCommand { Id = "quick_cancel_execution", Title = "取消执行", Command = "取消执行", UpdatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() },
            });
        }
        return this;
    }
}

public sealed class QuickCommand
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public string Command { get; set; } = "";
    public double UpdatedAt { get; set; }
}

public sealed class DesktopSession
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "未命名会话";
    public string Status { get; set; } = "active";
    public string? ProjectId { get; set; }
    public string? PreviousProjectId { get; set; }
    public bool IsPinned { get; set; }
    public bool IsUnread { get; set; }
    public double CreatedAt { get; set; }
    public double UpdatedAt { get; set; }
    public List<DesktopMessage> Messages { get; set; } = new();
    // 协作参与者：@ 过一次即加入本会话；无 @ 的消息只发给这些成员。
    public List<string> CollaborationAgents { get; set; } = new();
    // 用户显式移除过的参与者：默认成员（main_text）被移除后不再自动加回。
    public List<string> CollaborationOptOut { get; set; } = new();
}

public sealed class DesktopMessage
{
    public string Id { get; set; } = "";
    public string Role { get; set; } = "assistant";
    public string Kind { get; set; } = "";
    public string Text { get; set; } = "";
    public string Subtitle { get; set; } = "";
    public double DurationSeconds { get; set; }
    // 工作链归属 agent 展示名（协作分卡）：每个参与模型一张独立 work 卡时标识执行者。
    public string WorkAgent { get; set; } = "";
    public bool WorkExpanded { get; set; }
    public List<DesktopWorkStep> Steps { get; set; } = new();
    public double CreatedAt { get; set; }
    public double UpdatedAt { get; set; }
}

public sealed class DesktopWorkStep
{
    public string Kind { get; set; } = "thinking";
    public string Title { get; set; } = "";
    public string Detail { get; set; } = "";
    // 结构化语义去重键（如 route:general:、exec:local_pc.browser_search）。
    // 同一逻辑步骤无论来自 WS 实时事件还是 HTTP 汇总，都产出相同 Key，从而跨来源去重；为空时回退到全字段相等去重。
    public string Key { get; set; } = "";
    public double CreatedAt { get; set; }

    // ── trace event schema v1 兼容字段（后端 GPT 侧落地前默认空/0，渲染回退到 CreatedAt 排序、扁平结构）──
    // 排序键：>0 时优先于 CreatedAt。后端保证单调递增。
    public long Seq { get; set; }
    // 关联同一次 run 的所有步骤。
    public string RunId { get; set; } = "";
    // 幂等去重 id（replay/stream 重叠时），优先于语义 Key。
    public string EventId { get; set; } = "";
    // 生命周期合并键：同 SpanId 的 started/output/completed 折叠为单行状态机。
    public string SpanId { get; set; } = "";
    // 层级父节点，用于还原 run>step>tool 树；空则挂 run 根。
    public string ParentId { get; set; } = "";
    // 状态徽章：queued/running/completed/failed/blocked/cancelled 等；空表示无生命周期语义。
    public string Status { get; set; } = "";
    // terminal event 标记：run.completed/failed/cancelled 决定整体最终状态。
    public bool IsTerminal { get; set; }
    // 执行者标签（worker-1 / general / tool_time 等）；多 agent 场景用于分组与显示。
    public string AgentId { get; set; } = "";
    // 流式泳道标记：来自 worker reasoning/draft/token/stdout 流的步骤，Detail 前缀增长时走打字机
    //（含卡完结后的收尾批次）；非流式步骤（lifecycle/command/diff 等）维持直贴。
    public bool IsStreamLane { get; set; }
    // 推理正文可见级别：summary 可直接展示；private 只能投影为进度文案；空值兼容旧状态。
    public string ReasoningVisibility { get; set; } = "";
    // 外部调用卡片的结构化目标；主 Agent 自身模型活动不填写这些字段。
    public string CallAgent { get; set; } = "";
    public string CallModel { get; set; } = "";
    public string CallProvider { get; set; } = "";
    // Shell 卡片字段独立于展示摘要，生命周期合并时仍能同时保留命令和输出。
    public string CommandText { get; set; } = "";
    public string CommandOutput { get; set; } = "";
    public string ShellLabel { get; set; } = "";
}

public sealed class DesktopItem
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public string Status { get; set; } = "active";
    public string? Detail { get; set; }
    public string? WorkspacePath { get; set; }
    public string? EnvFilePath { get; set; }
    public string? DependencyFilePath { get; set; }
    public string? PackageManager { get; set; }
    public string? StartCommand { get; set; }
    public string? Source { get; set; }
    public double CreatedAt { get; set; }
    public double UpdatedAt { get; set; }
}

public sealed record ProjectRuntimeProfile(
    string ProjectId,
    string ProjectTitle,
    string WorkspacePath,
    string EnvFilePath,
    string DependencyFilePath,
    string PackageManager,
    string StartCommand);

public sealed class DesktopEvent
{
    public string Type { get; set; } = "";
    public string Time { get; set; } = "";
    public JsonElement Payload { get; set; }
}
