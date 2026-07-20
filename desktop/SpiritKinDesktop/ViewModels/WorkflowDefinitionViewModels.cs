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

public sealed class WorkflowDefinitionViewModel
{
    public WorkflowDefinitionViewModel(string name, string displayName, string version, string category, string domain, string status, string description, int nodeCount, bool saved, string nodeSummary)
    {
        Name = string.IsNullOrWhiteSpace(name) ? "workflow" : name;
        DisplayName = WorkflowDisplayText.WorkflowName(Name, displayName);
        Version = string.IsNullOrWhiteSpace(version) ? "--" : version;
        Category = WorkflowDisplayText.CategoryLabel(category, domain);
        Domain = string.IsNullOrWhiteSpace(domain) ? "--" : domain;
        Status = string.IsNullOrWhiteSpace(status) ? "candidate" : status;
        Description = description;
        NodeCount = nodeCount;
        NodeSummary = string.IsNullOrWhiteSpace(nodeSummary) ? $"节点 {NodeCount}" : nodeSummary;
        Saved = saved;
        SourceLabel = saved ? "已保存" : "内置";
        Meta = $"{WorkflowDisplayText.TechnicalLine(Name)} · {Category} · 节点 {NodeCount} · 版本 {Version}";
        StatusBrush = saved
            ? new SolidColorBrush(Color.FromRgb(22, 163, 74))
            : new SolidColorBrush(Color.FromRgb(2, 80, 204));
    }

    public string Name { get; }
    public string DisplayName { get; }
    public string Version { get; }
    public string Category { get; }
    public string Domain { get; }
    public string Status { get; }
    public string Description { get; }
    public int NodeCount { get; }
    public string NodeSummary { get; }
    public bool Saved { get; }
    public string SourceLabel { get; }
    public string Meta { get; }
    public Brush StatusBrush { get; }

    public static WorkflowDefinitionViewModel FromJson(JsonElement definition, bool saved)
    {
        TryReadObject(definition, "metadata", out var metadata);
        var nodeCount = definition.TryGetProperty("nodes", out var nodes) && nodes.ValueKind == JsonValueKind.Array
            ? nodes.GetArrayLength()
            : 0;
        var nodeSummary = nodes.ValueKind == JsonValueKind.Array
            ? string.Join("、", nodes.EnumerateArray()
                .Select(node => ReadString(node, "label", ReadString(node, "node_id")))
                .Where(text => !string.IsNullOrWhiteSpace(text))
                .Take(6))
            : "";
        return new WorkflowDefinitionViewModel(
            ReadString(definition, "name"),
            ReadString(metadata, "display_name", ReadString(definition, "name")),
            ReadString(definition, "version", "--"),
            ReadString(metadata, "category", "workflow"),
            ReadString(metadata, "domain", "--"),
            ReadString(metadata, "status", "candidate"),
            ReadString(definition, "description"),
            nodeCount,
            saved,
            string.IsNullOrWhiteSpace(nodeSummary) ? "" : $"节点式流程：{nodeSummary}");
    }

    private static bool TryReadObject(JsonElement element, string key, out JsonElement value)
    {
        value = default;
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var candidate) || candidate.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        value = candidate;
        return true;
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
}

public sealed class WorkflowRunViewModel
{
    public WorkflowRunViewModel(string id, string workflowName, string status, string countsJson, int runnableCount, string updatedAt)
    {
        Id = id;
        WorkflowName = string.IsNullOrWhiteSpace(workflowName) ? "workflow" : workflowName;
        Title = WorkflowDisplayText.WorkflowName(WorkflowName);
        Status = string.IsNullOrWhiteSpace(status) ? "--" : status;
        StatusLabel = WorkflowDisplayText.StatusLabel(Status);
        Meta = $"运行 {WorkflowDisplayText.ShortId(id)} · 可执行 {runnableCount}";
        Detail = string.IsNullOrWhiteSpace(updatedAt)
            ? $"{WorkflowDisplayText.TechnicalLine(WorkflowName)}{Environment.NewLine}{countsJson}"
            : $"{updatedAt}{Environment.NewLine}{WorkflowDisplayText.TechnicalLine(WorkflowName)}{Environment.NewLine}{countsJson}";
        StatusBrush = WorkflowNodeViewModel.BrushForStatus(Status);
    }

    public string Id { get; }
    public string NodeId => Id;
    public string WorkflowName { get; }
    public string Title { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string Meta { get; }
    public string Detail { get; }
    public bool Runnable => false;
    public Brush StatusBrush { get; }
}

public sealed class WorkflowVersionViewModel
{
    public WorkflowVersionViewModel(string versionId, string definitionVersion, string savedAt, string actor, string action, string reason, int nodeCount)
    {
        VersionId = versionId;
        DefinitionVersion = string.IsNullOrWhiteSpace(definitionVersion) ? "--" : definitionVersion;
        SavedAt = savedAt;
        Actor = string.IsNullOrWhiteSpace(actor) ? "system" : actor;
        Action = string.IsNullOrWhiteSpace(action) ? "save_definition" : action;
        Reason = reason;
        NodeCount = nodeCount;
        Title = $"{DefinitionVersion} · {SavedAt}";
        Meta = $"{Actor} · {Action} · 节点 {NodeCount}";
        Detail = string.IsNullOrWhiteSpace(Reason) ? VersionId : $"{Reason}{Environment.NewLine}{VersionId}";
    }

    public string VersionId { get; }
    public string DefinitionVersion { get; }
    public string SavedAt { get; }
    public string Actor { get; }
    public string Action { get; }
    public string Reason { get; }
    public int NodeCount { get; }
    public string Title { get; }
    public string Meta { get; }
    public string Detail { get; }

    public static WorkflowVersionViewModel FromJson(JsonElement version)
    {
        return new WorkflowVersionViewModel(
            ReadString(version, "version_id"),
            ReadString(version, "definition_version", "--"),
            ReadString(version, "saved_at", "--"),
            ReadString(version, "actor", "system"),
            ReadString(version, "action", "save_definition"),
            ReadString(version, "reason"),
            ReadInt(version, "node_count"));
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
}

public sealed class WorkflowSwimlaneViewModel
{
    public WorkflowSwimlaneViewModel(string title, string key, double y, double width, double height)
    {
        Title = title;
        Key = key;
        Y = y;
        Width = width;
        Height = height;
        BackgroundBrush = key switch
        {
            "agent_task" => new SolidColorBrush(Color.FromRgb(248, 251, 255)),
            "tool_call" => new SolidColorBrush(Color.FromRgb(244, 250, 246)),
            "review_gate" => new SolidColorBrush(Color.FromRgb(255, 250, 235)),
            _ => new SolidColorBrush(Color.FromRgb(248, 250, 252)),
        };
        BorderBrush = new SolidColorBrush(Color.FromRgb(216, 226, 242));
    }

    public string Title { get; }
    public string Key { get; }
    public double Y { get; }
    public double Width { get; }
    public double Height { get; }
    public Brush BackgroundBrush { get; }
    public Brush BorderBrush { get; }
}

public sealed class WorkflowTaskProgressViewModel
{
    public WorkflowTaskProgressViewModel(string label, string status, double percent, string detail = "")
    {
        Label = string.IsNullOrWhiteSpace(label) ? "任务" : label;
        Status = string.IsNullOrWhiteSpace(status) ? "pending" : status;
        StatusLabel = WorkflowDisplayText.StatusLabel(Status);
        Percent = Math.Clamp(percent, 0, 100);
        PercentText = $"{Percent:0.#}%";
        Brush = WorkflowNodeViewModel.BrushForStatus(Status);
        Detail = string.IsNullOrWhiteSpace(detail) ? $"{Label} · {StatusLabel} · {PercentText}" : detail;
    }

    public string Label { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public double Percent { get; }
    public string PercentText { get; }
    public Brush Brush { get; }
    public string Detail { get; }

    public static WorkflowTaskProgressViewModel Empty(string nodeStatus)
    {
        var status = string.IsNullOrWhiteSpace(nodeStatus) ? "pending" : nodeStatus;
        return new WorkflowTaskProgressViewModel("暂无节点任务", status, StatusPercent(status), "当前节点还没有 node_task_queue；运行后会在这里显示该节点任务列表。");
    }

    public static WorkflowTaskProgressViewModel FromJson(JsonElement item, int index, string nodeStatus)
    {
        if (item.ValueKind != JsonValueKind.Object)
        {
            var text = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText();
            return new WorkflowTaskProgressViewModel(string.IsNullOrWhiteSpace(text) ? $"任务{index}" : text, "pending", 0, text);
        }
        var label = ReadString(item, "label",
            ReadString(item, "title",
                ReadString(item, "task_id",
                    ReadString(item, "product_id",
                        ReadString(item, "id", $"任务{index}")))));
        var status = ReadString(item, "status", ReadString(item, "state", nodeStatus));
        var percent = ReadPercent(item, status);
        var source = ReadString(item, "queue_source");
        var key = ReadString(item, "queue_key");
        var detail = $"{label} · {WorkflowDisplayText.StatusLabel(status)} · {percent:0.#}%";
        if (!string.IsNullOrWhiteSpace(source))
        {
            detail += $" · {source}{(string.IsNullOrWhiteSpace(key) ? "" : $"/{key}")}";
        }
        return new WorkflowTaskProgressViewModel(label, status, percent, detail);
    }

    private static double ReadPercent(JsonElement item, string status)
    {
        if (TryReadObject(item, "progress", out var progress))
        {
            var percent = ReadDouble(progress, "percent", -1);
            if (percent >= 0)
            {
                return percent;
            }
        }
        var directPercent = ReadDouble(item, "percent", -1);
        if (directPercent >= 0)
        {
            return directPercent;
        }
        var progressPercent = ReadDouble(item, "progress_percent", -1);
        return progressPercent >= 0 ? progressPercent : StatusPercent(status);
    }

    private static double StatusPercent(string status)
    {
        return (status ?? "").Trim().ToLowerInvariant() switch
        {
            "succeeded" or "success" or "done" or "complete" or "completed" or "ready" => 100,
            "running" or "active" or "in_progress" or "processing" or "claimed" or "current" => 50,
            "waiting_review" => 50,
            "waiting" => 35,
            "blocked" or "failed" or "error" => 0,
            _ => 0,
        };
    }

    private static bool TryReadObject(JsonElement element, string key, out JsonElement value)
    {
        value = default;
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var candidate) || candidate.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        value = candidate;
        return true;
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

    private static double ReadDouble(JsonElement element, string key, double fallback)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
        {
            return number;
        }
        return double.TryParse(ReadString(element, key), out var parsed) ? parsed : fallback;
    }
}
