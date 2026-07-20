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

public sealed class WorkflowNodeViewModel
{
    public WorkflowNodeViewModel(string nodeId, string title, string status, string meta, string detail, bool runnable)
    {
        NodeId = nodeId;
        Title = string.IsNullOrWhiteSpace(title) ? nodeId : title;
        Status = string.IsNullOrWhiteSpace(status) ? "pending" : status;
        StatusLabel = WorkflowDisplayText.StatusLabel(Status);
        Meta = meta;
        Detail = detail;
        Runnable = runnable;
        StatusBrush = BrushForStatus(Status);
    }

    public string NodeId { get; }
    public string Id => NodeId;
    public string Title { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string Meta { get; }
    public string Detail { get; }
    public bool Runnable { get; }
    public Brush StatusBrush { get; }

    public static WorkflowNodeViewModel ForDefinition(JsonElement node)
    {
        var nodeId = ReadString(node, "node_id");
        var type = ReadString(node, "node_type", "--");
        var actor = WorkflowDisplayText.NodeActor(node);
        var depends = ReadStringArray(node, "depends_on");
        return new WorkflowNodeViewModel(
            nodeId,
            ReadString(node, "label", nodeId),
            type,
            $"{WorkflowDisplayText.TechnicalLine(nodeId)} · {WorkflowDisplayText.NodeTypeLabel(type)} · {WorkflowDisplayText.ActorLabel(actor)}",
            depends.Length == 0 ? "依赖：无" : $"依赖：{string.Join(", ", depends.Select(dep => WorkflowDisplayText.ShortId(dep)))}",
            runnable: false);
    }

    public static WorkflowNodeViewModel ForRun(JsonElement definitionNode, JsonElement runState, bool runnable)
    {
        var nodeId = ReadString(definitionNode, "node_id");
        var status = runState.ValueKind == JsonValueKind.Object ? ReadString(runState, "status", "pending") : "pending";
        var type = ReadString(definitionNode, "node_type", "--");
        var agent = runState.ValueKind == JsonValueKind.Object
            ? ReadString(runState, "assigned_agent", ReadString(definitionNode, "assigned_agent", "--"))
            : ReadString(definitionNode, "assigned_agent", "--");
        var error = runState.ValueKind == JsonValueKind.Object ? ReadString(runState, "error") : "";
        var attempts = runState.ValueKind == JsonValueKind.Object ? ReadInt(runState, "attempts") : 0;
        return new WorkflowNodeViewModel(
            nodeId,
            ReadString(definitionNode, "label", nodeId),
            runnable && status == "pending" ? "runnable" : status,
            $"{WorkflowDisplayText.TechnicalLine(nodeId)} · {WorkflowDisplayText.NodeTypeLabel(type)} · {WorkflowDisplayText.ActorLabel(agent)}",
            string.IsNullOrWhiteSpace(error) ? $"尝试 {attempts}" : error,
            runnable);
    }

    public static Brush BrushForStatus(string status) => status.ToLowerInvariant() switch
    {
        "succeeded" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
        "running" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
        "waiting" => new SolidColorBrush(Color.FromRgb(15, 118, 110)),
        "waiting_review" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
        "blocked" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
        "failed" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
        "runnable" => new SolidColorBrush(Color.FromRgb(253, 200, 0)),
        _ => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
    };

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

    private static string[] ReadStringArray(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return value.EnumerateArray()
            .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }
}
