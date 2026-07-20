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

public sealed class SearchCapabilityViewModel
{
    public SearchCapabilityViewModel(string model, string provider, string[] strengths, string bestFor, bool local)
    {
        Model = string.IsNullOrWhiteSpace(model) ? "model" : model;
        Provider = string.IsNullOrWhiteSpace(provider) ? "--" : provider;
        Strengths = strengths;
        BestFor = bestFor;
        Local = local;
        var labels = strengths.Select(UiDisplayText.SearchStrength).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray();
        Title = $"{Model} · {UiDisplayText.Provider(Provider)}";
        Meta = $"{(local ? "本地/开源可用" : "云端")} · {(labels.Length == 0 ? "--" : string.Join("、", labels))}";
        Detail = BestFor;
    }

    public string Model { get; }
    public string Provider { get; }
    public string[] Strengths { get; }
    public string BestFor { get; }
    public bool Local { get; }
    public string Title { get; }
    public string Meta { get; }
    public string Detail { get; }
}

public sealed class KnowledgeJobViewModel
{
    public KnowledgeJobViewModel(
        string jobId,
        string jobType,
        string status,
        string targetId,
        string targetPath,
        string summary,
        string error,
        string actor,
        double startedAt,
        double completedAt,
        int durationMs,
        bool empty = false)
    {
        JobId = string.IsNullOrWhiteSpace(jobId) ? "knowledge-job" : jobId;
        JobType = string.IsNullOrWhiteSpace(jobType) ? "knowledge_job" : jobType;
        Status = string.IsNullOrWhiteSpace(status) ? "completed" : status;
        TargetId = targetId;
        TargetPath = targetPath;
        Summary = summary;
        Error = error;
        Actor = string.IsNullOrWhiteSpace(actor) ? "system" : actor;
        StartedAt = startedAt;
        CompletedAt = completedAt;
        DurationMs = Math.Max(0, durationMs);
        IsEmpty = empty;
        StatusLabel = LabelForStatus(Status);
        Title = empty
            ? "暂无知识索引 / 同步任务"
            : $"{LabelForJobType(JobType)} · {(string.IsNullOrWhiteSpace(TargetId) ? "--" : TargetId)}";
        var completed = FormatTime(CompletedAt);
        var duration = DurationMs > 0 ? $"{DurationMs} ms" : "--";
        Meta = empty
            ? "执行索引或同步后会在这里显示最近任务。"
            : $"{completed} · {Actor} · {duration} · {ShortPath(TargetPath, 72)}";
        Detail = empty ? "" : string.IsNullOrWhiteSpace(Error) ? Summary : $"{Error}{(string.IsNullOrWhiteSpace(Summary) ? "" : Environment.NewLine + Summary)}";
        StatusBrush = BrushForStatus(Status, empty);
    }

    public string JobId { get; }
    public string JobType { get; }
    public string Status { get; }
    public string TargetId { get; }
    public string TargetPath { get; }
    public string Summary { get; }
    public string Error { get; }
    public string Actor { get; }
    public double StartedAt { get; }
    public double CompletedAt { get; }
    public int DurationMs { get; }
    public bool IsEmpty { get; }
    public string Title { get; }
    public string StatusLabel { get; }
    public string Meta { get; }
    public string Detail { get; }
    public Brush StatusBrush { get; }

    public static KnowledgeJobViewModel FromJson(JsonElement job)
    {
        return new KnowledgeJobViewModel(
            ReadString(job, "job_id", "knowledge-job"),
            ReadString(job, "job_type", "knowledge_job"),
            ReadString(job, "status", "completed"),
            ReadString(job, "target_id"),
            ReadString(job, "target_path"),
            ReadString(job, "summary"),
            ReadString(job, "error"),
            ReadString(job, "actor", "system"),
            ReadDouble(job, "started_at"),
            ReadDouble(job, "completed_at"),
            ReadInt(job, "duration_ms"));
    }

    public static KnowledgeJobViewModel Empty()
    {
        return new KnowledgeJobViewModel("", "", "skipped", "", "", "", "", "", 0, 0, 0, empty: true);
    }

    private static string LabelForStatus(string status) => status.ToLowerInvariant() switch
    {
        "completed" => "完成",
        "failed" => "失败",
        "running" => "运行中",
        "queued" => "排队",
        "skipped" => "跳过",
        _ => status,
    };

    private static string LabelForJobType(string jobType) => jobType.ToLowerInvariant() switch
    {
        "index" => "索引",
        "index_all" => "重建全部",
        "index_unindexed" => "索引未索引",
        "sync_source" => "同步外部源",
        _ => UiDisplayText.HumanizeIdentifier(jobType, jobType),
    };

    private static Brush BrushForStatus(string status, bool empty) => empty
        ? new SolidColorBrush(Color.FromRgb(148, 163, 184))
        : status.ToLowerInvariant() switch
        {
            "completed" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
            "failed" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
            "running" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
            "queued" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
            "skipped" => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
            _ => new SolidColorBrush(Color.FromRgb(100, 116, 139)),
        };

    private static string ShortPath(string path, int maxLength)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return "--";
        }
        var normalized = path.Trim();
        if (normalized.Length <= maxLength)
        {
            return normalized;
        }
        var tailLength = Math.Max(1, maxLength - 3);
        return "..." + normalized.Substring(Math.Max(0, normalized.Length - tailLength));
    }

    private static string FormatTime(double seconds)
    {
        return seconds <= 0 ? "--" : DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g");
    }

    private static string ReadString(JsonElement element, string key, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        var text = value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? "",
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            _ => "",
        };
        return string.IsNullOrWhiteSpace(text) ? fallback : text;
    }

    private static int ReadInt(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
        {
            return number;
        }
        return int.TryParse(ReadString(element, key), out var parsed) ? parsed : 0;
    }

    private static double ReadDouble(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
        {
            return number;
        }
        return double.TryParse(ReadString(element, key), NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed) ? parsed : 0;
    }
}

public sealed class ModuleManagementViewModel
{
    public ModuleManagementViewModel(
        string moduleId,
        string label,
        string status,
        string summary,
        string description,
        string endpoint,
        string desktopPage,
        string businessCapability = "",
        string managementGroup = "",
        string ownerRole = "",
        string criticality = "",
        string maturity = "",
        string sla = "",
        string riskLevel = "",
        string riskSummary = "",
        int healthScore = 0,
        string governanceState = "",
        int actionCount = 0,
        int highActionCount = 0,
        int mediumActionCount = 0)
    {
        ModuleId = moduleId;
        Label = string.IsNullOrWhiteSpace(label) ? moduleId : label;
        Status = string.IsNullOrWhiteSpace(status) ? "unknown" : status;
        Summary = summary;
        Description = description;
        Endpoint = endpoint;
        DesktopPage = desktopPage;
        BusinessCapability = string.IsNullOrWhiteSpace(businessCapability) ? "平台能力" : businessCapability;
        ManagementGroup = string.IsNullOrWhiteSpace(managementGroup) ? "平台" : managementGroup;
        OwnerRole = string.IsNullOrWhiteSpace(ownerRole) ? "模块负责人" : ownerRole;
        Criticality = string.IsNullOrWhiteSpace(criticality) ? "medium" : criticality;
        Maturity = string.IsNullOrWhiteSpace(maturity) ? "emerging" : maturity;
        Sla = string.IsNullOrWhiteSpace(sla) ? "weekly review" : sla;
        RiskLevel = string.IsNullOrWhiteSpace(riskLevel) ? "medium" : riskLevel;
        RiskSummary = string.IsNullOrWhiteSpace(riskSummary) ? "无风险摘要。" : riskSummary;
        HealthScore = healthScore <= 0 ? FallbackHealthScore(Status, RiskLevel) : healthScore;
        GovernanceState = string.IsNullOrWhiteSpace(governanceState) ? "review_required" : governanceState;
        ActionCount = actionCount;
        HighActionCount = highActionCount;
        MediumActionCount = mediumActionCount;
        StatusLabelText = LabelForStatus(Status);
        StatusLabel = StatusLabelText;
        RiskLabel = LabelForRisk(RiskLevel);
        CriticalityLabel = LabelForCriticality(Criticality);
        GovernanceLabel = LabelForGovernance(GovernanceState);
        HealthLabel = $"健康 {HealthScore}";
        OwnerLine = $"{UiDisplayText.HumanizeIdentifier(ManagementGroup, ManagementGroup)} · {UiDisplayText.HumanizeIdentifier(OwnerRole, OwnerRole)} · {UiDisplayText.ServiceLevel(Sla)}";
        EnterpriseLine = $"{BusinessCapability} · {CriticalityLabel} · {UiDisplayText.Maturity(Maturity)}";
        ActionLine = $"事项 {ActionCount} · 高优先 {HighActionCount} · 中优先 {MediumActionCount}";
        Type = $"{StatusLabelText} · {Label}";
        Meta = $"{Summary}{Environment.NewLine}{OwnerLine}{Environment.NewLine}{RiskSummary}".Trim();
        StatusBrush = StatusBrushFor(Status);
        RiskBrush = RiskBrushFor(RiskLevel);
        HealthBrush = HealthScore >= 85
            ? new SolidColorBrush(Color.FromRgb(22, 163, 74))
            : HealthScore >= 65
                ? new SolidColorBrush(Color.FromRgb(217, 119, 6))
                : new SolidColorBrush(Color.FromRgb(220, 38, 38));
    }

    public string ModuleId { get; }
    public string Label { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string StatusLabelText { get; }
    public string Summary { get; }
    public string Description { get; }
    public string Endpoint { get; }
    public string DesktopPage { get; }
    public string BusinessCapability { get; }
    public string ManagementGroup { get; }
    public string OwnerRole { get; }
    public string Criticality { get; }
    public string CriticalityLabel { get; }
    public string Maturity { get; }
    public string Sla { get; }
    public string RiskLevel { get; }
    public string RiskLabel { get; }
    public string RiskSummary { get; }
    public int HealthScore { get; }
    public string HealthLabel { get; }
    public string GovernanceState { get; }
    public string GovernanceLabel { get; }
    public int ActionCount { get; }
    public int HighActionCount { get; }
    public int MediumActionCount { get; }
    public string OwnerLine { get; }
    public string EnterpriseLine { get; }
    public string ActionLine { get; }
    public string Type { get; }
    public string Meta { get; }
    public Brush StatusBrush { get; }
    public Brush RiskBrush { get; }
    public Brush HealthBrush { get; }

    private static int FallbackHealthScore(string status, string riskLevel)
    {
        if (status.Equals("blocked", StringComparison.OrdinalIgnoreCase) || riskLevel.Equals("high", StringComparison.OrdinalIgnoreCase))
        {
            return 55;
        }
        return status.Equals("ready", StringComparison.OrdinalIgnoreCase) && riskLevel.Equals("low", StringComparison.OrdinalIgnoreCase) ? 92 : 74;
    }

    private static Brush StatusBrushFor(string status) => status.ToLowerInvariant() switch
    {
        "ready" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
        "needs_attention" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
        "blocked" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
        _ => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
    };

    private static Brush RiskBrushFor(string risk) => risk.ToLowerInvariant() switch
    {
        "high" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
        "medium" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
        "low" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
        _ => new SolidColorBrush(Color.FromRgb(100, 116, 139)),
    };

    private static string LabelForStatus(string status) => status.ToLowerInvariant() switch
    {
        "ready" => "就绪",
        "needs_attention" => "注意",
        "blocked" => "阻塞",
        _ => status,
    };

    private static string LabelForRisk(string risk) => risk.ToLowerInvariant() switch
    {
        "high" => "高风险",
        "medium" => "中风险",
        "low" => "低风险",
        _ => risk,
    };

    private static string LabelForCriticality(string criticality) => criticality.ToLowerInvariant() switch
    {
        "critical" => "关键",
        "high" => "高重要",
        "medium" => "中重要",
        "low" => "低重要",
        _ => criticality,
    };

    private static string LabelForGovernance(string governanceState) => governanceState.ToLowerInvariant() switch
    {
        "controlled" => "受控",
        "review_required" => "待治理",
        "blocked" => "阻塞",
        _ => governanceState,
    };
}
