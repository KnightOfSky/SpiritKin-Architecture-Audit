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

public sealed class SessionViewModel
{
    public SessionViewModel(string id, string title, string subtitle, string status, bool isPinned, bool isUnread)
    {
        Id = id;
        Title = isUnread ? $"{title} *" : title;
        Subtitle = subtitle;
        Status = string.IsNullOrWhiteSpace(status) ? "active" : status;
        StatusLabel = LabelForStatus(Status);
        ArchiveActionLabel = Status.Equals("archived", StringComparison.OrdinalIgnoreCase) ? "恢复" : "归档";
        PinActionLabel = isPinned ? "取消置顶" : "置顶";
        UnreadActionLabel = isUnread ? "标记为已读" : "标记为未读";
        StatusBrush = new SolidColorBrush(Status.Equals("archived", StringComparison.OrdinalIgnoreCase) ? Color.FromRgb(148, 163, 184) : Color.FromRgb(22, 163, 74));
    }

    public string Id { get; }
    public string Title { get; }
    public string Subtitle { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string ArchiveActionLabel { get; }
    public string PinActionLabel { get; }
    public string UnreadActionLabel { get; }
    public Brush StatusBrush { get; }

    private static string LabelForStatus(string status)
    {
        return status.Equals("archived", StringComparison.OrdinalIgnoreCase) ? "归档" : "活动";
    }
}

public sealed class ProjectViewModel
{
    private ProjectViewModel(string id, string projectId, string sessionId, string title, string subtitle, string status, bool isSession, bool isPinned, bool isUnread, bool isExpanded, bool flat = false)
    {
        Id = id;
        ProjectId = projectId;
        SessionId = sessionId;
        Title = isSession && isUnread ? $"{title} *" : title;
        Status = status;
        Subtitle = subtitle;
        IsSession = isSession;
        Glyph = flat ? "" : isSession ? "  " : isExpanded ? "⌄" : "›";
        TitleWeight = isSession ? FontWeights.Normal : FontWeights.SemiBold;
        ItemPadding = flat ? new Thickness(7, 6, 7, 6) : isSession ? new Thickness(26, 5, 7, 5) : new Thickness(7, 6, 7, 6);
        StatusLabel = LabelForStatus(status);
        StatusBrush = new SolidColorBrush(status.Equals("archived", StringComparison.OrdinalIgnoreCase) ? Color.FromRgb(148, 163, 184) : Color.FromRgb(22, 163, 74));
        ArchiveActionLabel = status.Equals("archived", StringComparison.OrdinalIgnoreCase) ? "恢复" : "归档";
        PinActionLabel = isPinned ? "取消置顶" : "置顶";
        UnreadActionLabel = isUnread ? "标记为已读" : "标记为未读";
        PauseActionLabel = status.Contains("paused", StringComparison.OrdinalIgnoreCase) ? "恢复" : "暂停";
        ProjectActionVisibility = isSession ? Visibility.Collapsed : Visibility.Visible;
        ProjectSessionActionVisibility = isSession ? Visibility.Visible : Visibility.Collapsed;
        SessionActionVisibility = isSession ? Visibility.Visible : Visibility.Collapsed;
    }

    public static ProjectViewModel ForProject(string projectId, string title, string status, string subtitle, bool isExpanded) => new(projectId, projectId, "", title, subtitle, status, isSession: false, isPinned: false, isUnread: false, isExpanded);
    public static ProjectViewModel ForProjectSession(string projectId, string sessionId, string title, string subtitle, string status, bool isPinned, bool isUnread) => new($"project_session_{projectId}_{sessionId}", projectId, sessionId, title, subtitle, status, isSession: true, isPinned, isUnread, isExpanded: false);
    public static ProjectViewModel ForManagedSession(string projectId, string sessionId, string title, string subtitle, string status, bool isPinned, bool isUnread) => new(sessionId, projectId, sessionId, title, subtitle, status, isSession: true, isPinned, isUnread, isExpanded: false, flat: true);

    public string Id { get; }
    public string ProjectId { get; }
    public string SessionId { get; }
    public string Title { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string Subtitle { get; }
    public bool IsProject => !IsSession;
    public bool IsSession { get; }
    public string Glyph { get; }
    public FontWeight TitleWeight { get; }
    public Thickness ItemPadding { get; }
    public Brush StatusBrush { get; }
    public string ArchiveActionLabel { get; }
    public string PinActionLabel { get; }
    public string UnreadActionLabel { get; }
    public string PauseActionLabel { get; }
    public Visibility ProjectActionVisibility { get; }
    public Visibility ProjectSessionActionVisibility { get; }
    public Visibility SessionActionVisibility { get; }

    private static string LabelForStatus(string status)
    {
        if (status.Equals("archived", StringComparison.OrdinalIgnoreCase))
        {
            return "归档";
        }
        if (status.Equals("active", StringComparison.OrdinalIgnoreCase))
        {
            return "活动";
        }
        return status;
    }
}
public sealed class TaskViewModel
{
    public TaskViewModel(string id, string title, string status, string detail)
    {
        Id = id;
        Title = title;
        Status = string.IsNullOrWhiteSpace(status) ? "pending" : status;
        StatusLabel = UiDisplayText.Status(Status);
        Detail = detail;
        StatusBrush = Status.ToLowerInvariant() switch
        {
            "running" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
            "complete" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
            "completed" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
            "blocked" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
            "failed" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
            "pending" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
            _ => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
        };
    }

    public string Id { get; }
    public string Title { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string Detail { get; }
    public Brush StatusBrush { get; }
}
public sealed record EventViewModel(string Type, string Meta);
public sealed record RuntimeHostViewModel(
    string HostId,
    string Label,
    string HostType,
    string Status,
    string ExecutionStatus,
    bool CanExecuteWorkflows,
    bool CanObserve,
    bool IsLeaseOwner,
    int Epoch)
{
    public string Type => $"{(string.IsNullOrWhiteSpace(Label) ? HostId : Label)} · {Status}";
    public string Meta => $"{HostType} · {(CanExecuteWorkflows ? $"Workflow 执行器 {ExecutionStatus}" : CanObserve ? "Observation Adapter" : "控制 Adapter")}{(IsLeaseOwner ? $" · 当前租约 epoch {Epoch}" : "")}";
    public bool CanReceiveMigration => CanExecuteWorkflows
        && string.Equals(Status, "online", StringComparison.OrdinalIgnoreCase)
        && ExecutionStatus is not ("fenced" or "error" or "not_reported")
        && !IsLeaseOwner;
}
public sealed record RuntimeCheckpointViewModel(
    string CheckpointId,
    string RunId,
    string WorkflowName,
    int Sequence,
    string SourceHostId,
    string Status,
    string CreatedAt)
{
    public string Type => $"{(string.IsNullOrWhiteSpace(WorkflowName) ? RunId : WorkflowName)} · #{Sequence}";
    public string Meta => $"{Status} · {SourceHostId} · {CreatedAt}";
}
public sealed record GrowthCandidateViewModel(
    string CandidateId,
    string Kind,
    string Title,
    string Status,
    string PromotionStatus,
    string CurrentStage,
    string WorkspaceId,
    string[] Stages,
    string ParentCandidateId = "",
    string ResolutionStatus = "unrouted",
    string ResolutionTarget = "",
    string ChildCandidateId = "",
    string RemoteResearchReportId = "",
    int RemoteResearchResultCount = 0,
    string RemoteResearchQuery = "",
    bool BuilderPrepared = false,
    int BuilderInventoryMatchCount = 0,
    string BuilderVerificationStatus = "",
    string BuilderRegistryTarget = "",
    bool BuilderHumanRequired = false,
    bool SandboxBundlePrepared = false,
    string SandboxBundleId = "",
    int SandboxBundleFileCount = 0,
    string SandboxExecutionStatus = "",
    string SandboxExecutionId = "",
    int SandboxExitCode = 0,
    string BenchmarkId = "",
    string BenchmarkPromotionStatus = "",
    double BenchmarkOverallScore = 0,
    double BenchmarkOverallDelta = 0)
{
    public string Type => $"{Kind} · {Title}";
    public string Meta => $"状态 {Status} · 阶段 {CurrentStage} · 工作区 {(string.IsNullOrWhiteSpace(WorkspaceId) ? "全局" : WorkspaceId)} · {(string.IsNullOrWhiteSpace(ParentCandidateId) ? "根候选" : $"父候选 {ParentCandidateId}")} · {(ResolutionStatus == "unrouted" ? "尚未路由" : $"路由 {ResolutionTarget}")}";
    public string NextStage
    {
        get
        {
            var index = Array.IndexOf(Stages, CurrentStage);
            return index >= 0 && index + 1 < Stages.Length ? Stages[index + 1] : "";
        }
    }
    public bool CanAdvance => string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase)
        && !string.IsNullOrWhiteSpace(NextStage)
        && !string.Equals(CurrentStage, "review", StringComparison.OrdinalIgnoreCase)
        && (!string.Equals(NextStage, "review", StringComparison.OrdinalIgnoreCase)
            || string.Equals(BenchmarkPromotionStatus, "passed", StringComparison.OrdinalIgnoreCase));
    public bool CanApprove => string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && string.Equals(CurrentStage, "review", StringComparison.OrdinalIgnoreCase);
    public bool CanReject => string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && !string.Equals(CurrentStage, "registry", StringComparison.OrdinalIgnoreCase);
    public bool CanRegister => string.Equals(Status, "approved", StringComparison.OrdinalIgnoreCase) && string.Equals(CurrentStage, "review", StringComparison.OrdinalIgnoreCase);
    public bool CanResearch => string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && CurrentStage is not ("review" or "registry");
    public bool CanEscalate => string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && EscalationTargets.Length > 0;
    public string[] EscalationTargets => Kind.ToLowerInvariant() switch
    {
        "capability" => new[] { "workflow", "skill", "tool", "code", "model", "human" },
        "workflow" => new[] { "skill", "tool", "code", "model", "human" },
        "skill" => new[] { "tool", "code", "model", "human" },
        "tool" => new[] { "code", "model", "human" },
        "code" => new[] { "model", "human" },
        "model" => new[] { "human" },
        _ => Array.Empty<string>(),
    };
    public bool CanPrepareBuilder => !string.IsNullOrWhiteSpace(CandidateId) && Status.ToLowerInvariant() is not ("rejected" or "registered" or "escalated" or "needs_human");
    public bool CanVerifyBuilder => BuilderPrepared && string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && (Kind.ToLowerInvariant() switch
    {
        "capability" => string.Equals(CurrentStage, "design", StringComparison.OrdinalIgnoreCase),
        "workflow" => CurrentStage is "dry_run" or "benchmark",
        "skill" or "tool" or "code" => CurrentStage is "sandbox" or "dry_run" or "benchmark",
        "model" => string.Equals(CurrentStage, "benchmark", StringComparison.OrdinalIgnoreCase),
        _ => false,
    });
    public bool CanPrepareSandboxBundle => BuilderPrepared && string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && (Kind.ToLowerInvariant() switch
    {
        "skill" or "code" => CurrentStage is "design" or "sandbox" or "dry_run" or "benchmark",
        "tool" => CurrentStage is "research" or "sandbox" or "dry_run" or "benchmark",
        _ => false,
    });
    public bool CanExecuteSandbox => SandboxBundlePrepared && string.Equals(BuilderVerificationStatus, "passed", StringComparison.OrdinalIgnoreCase) && string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && Kind.ToLowerInvariant() is "skill" or "tool" or "code" && CurrentStage is "sandbox" or "dry_run" or "benchmark";
    public bool CanRecordBenchmark => string.Equals(Status, "candidate", StringComparison.OrdinalIgnoreCase) && string.Equals(CurrentStage, "benchmark", StringComparison.OrdinalIgnoreCase);
    public bool CanRunModelJury => CanRecordBenchmark && string.Equals(Kind, "model", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(BenchmarkId);
    public string BuilderSummary => BuilderPrepared
        ? $"Builder 已准备 · 匹配 {BuilderInventoryMatchCount} · 验证 {(string.IsNullOrWhiteSpace(BuilderVerificationStatus) ? "未运行" : BuilderVerificationStatus)} · 目标 {(string.IsNullOrWhiteSpace(BuilderRegistryTarget) ? "--" : BuilderRegistryTarget)}{(BuilderHumanRequired ? " · 需人工补源" : "")}"
        : "Builder 工件尚未准备";
    public string ResearchSummary => string.IsNullOrWhiteSpace(RemoteResearchReportId)
        ? "公开仓库研究尚未运行"
        : $"公开仓库研究 {RemoteResearchResultCount} 条 · {RemoteResearchReportId} · {RemoteResearchQuery}";
    public string SandboxSummary => SandboxBundlePrepared
        ? $"Sandbox Bundle {SandboxBundleId} · {SandboxBundleFileCount} 个文件 · 执行 {(string.IsNullOrWhiteSpace(SandboxExecutionStatus) ? "未运行" : SandboxExecutionStatus)}{(string.IsNullOrWhiteSpace(SandboxExecutionId) ? "" : $" · {SandboxExecutionId} · exit {SandboxExitCode}")}"
        : "Sandbox Bundle 尚未准备";
    public string BenchmarkSummary => string.IsNullOrWhiteSpace(BenchmarkId)
        ? "Benchmark 尚未记录"
        : $"Benchmark {BenchmarkPromotionStatus} · 总分 {BenchmarkOverallScore:F1} · Δ {BenchmarkOverallDelta:+0.0;-0.0;0.0} · {BenchmarkId}";
}
public sealed record ActionItemViewModel(
    string Id,
    string Type,
    string Meta,
    string Kind,
    string Command,
    string Target,
    string Priority = "",
    string ModuleLabel = "",
    string OwnerRole = "",
    string RiskLevel = "",
    string OperatorHint = "",
    string ManagementGroup = "",
    string Criticality = "",
    string Sla = "",
    // 消息真实创建时间（unix 秒）。协作回复的缓存投影在锚点 miss 时按它落位，
    // 而不是 NowSeconds() 甩到时间线末尾（2026-07-09 顺序跳变残留根因）。
    double CreatedAt = 0)
{
    public string PriorityLabel => LabelForPriority(Priority);
    public string ModuleDisplay => string.IsNullOrWhiteSpace(ModuleLabel) ? Kind : ModuleLabel;
    public string OwnerDisplay => string.IsNullOrWhiteSpace(OwnerRole) ? "模块负责人" : UiDisplayText.HumanizeIdentifier(OwnerRole, OwnerRole);
    public string RiskDisplay => string.IsNullOrWhiteSpace(RiskLevel) ? "--" : UiDisplayText.Risk(RiskLevel);
    public string GovernanceMeta => $"{OwnerDisplay} · {RiskDisplay} · {UiDisplayText.ServiceLevel(Sla)}".Trim(' ', '·');
    public string OperatorDisplay => string.IsNullOrWhiteSpace(OperatorHint) ? Meta : OperatorHint;
    public Brush PriorityBrush => Priority.ToLowerInvariant() switch
    {
        "high" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
        "medium" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
        "low" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
        _ => new SolidColorBrush(Color.FromRgb(100, 116, 139)),
    };

    private static string LabelForPriority(string priority) => priority.ToLowerInvariant() switch
    {
        "high" => "高",
        "medium" => "中",
        "low" => "低",
        _ => string.IsNullOrWhiteSpace(priority) ? "--" : priority,
    };
}
public sealed record ChangeViewModel(string Id, string Title, string Meta, string Diff, string ProposedMarkdown);
public sealed record GitChangeViewModel(string Path, string Status, string Delta, string SortKey);
public sealed record GitChangeSnapshot(List<GitChangeViewModel> Changes, string DeltaSummary, string GithubCliStatus, string BranchStatus);
public sealed record QuickCommandViewModel(string Id, string Title, string Command)
{
    public override string ToString() => Title;
}

public sealed record GlobalSearchResultViewModel(
    string Scope,
    string Title,
    string Detail,
    string TargetKind,
    string TargetId,
    string Page,
    string SubPage,
    int Score);
