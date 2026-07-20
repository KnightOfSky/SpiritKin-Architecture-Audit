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

public sealed class WorkflowGraphPortPinViewModel
{
    public WorkflowGraphPortPinViewModel(
        WorkflowGraphNodeViewModel node,
        string direction,
        string kind,
        int index,
        bool compatible,
        bool incompatible,
        bool activeSource)
    {
        Node = node;
        Direction = direction;
        Kind = WorkflowConnectionRules.NormalizePortKind(kind);
        Index = index;
        Compatible = compatible;
        Incompatible = incompatible;
        ActiveSource = activeSource;
        Label = WorkflowConnectionRules.PortKindLabel(Kind);
        DisplayLabel = ShortPortLabel(Kind);
        Brush = BrushForKind(Kind);
        StrokeBrush = activeSource
            ? new SolidColorBrush(Color.FromRgb(253, 200, 0))
            : compatible
                ? new SolidColorBrush(Color.FromRgb(34, 197, 94))
                : incompatible
                    ? new SolidColorBrush(Color.FromRgb(220, 38, 38))
                    : new SolidColorBrush(Color.FromRgb(184, 199, 221));
        StrokeThickness = activeSource || compatible || incompatible ? 2.4 : 1;
        BackgroundBrush = compatible
            ? new SolidColorBrush(Color.FromRgb(240, 253, 244))
            : incompatible
                ? new SolidColorBrush(Color.FromRgb(254, 242, 242))
                : activeSource
                    ? new SolidColorBrush(Color.FromRgb(255, 247, 218))
                    : new SolidColorBrush(Color.FromRgb(248, 251, 255));
        TextBrush = compatible
            ? new SolidColorBrush(Color.FromRgb(21, 128, 61))
            : incompatible
                ? new SolidColorBrush(Color.FromRgb(185, 28, 28))
                : new SolidColorBrush(Color.FromRgb(75, 85, 99));
        ToolTip = BuildToolTip(compatible, incompatible, activeSource);
    }

    public WorkflowGraphNodeViewModel Node { get; }
    public string NodeId => Node.NodeId;
    public string NodeType => Node.NodeType;
    public string Direction { get; }
    public bool IsInput => string.Equals(Direction, "input", StringComparison.OrdinalIgnoreCase);
    public string Kind { get; }
    public int Index { get; }
    public bool Compatible { get; }
    public bool Incompatible { get; }
    public bool ActiveSource { get; }
    public string Label { get; }
    public string DisplayLabel { get; }
    public Brush Brush { get; }
    public Brush StrokeBrush { get; }
    public double StrokeThickness { get; }
    public Brush BackgroundBrush { get; }
    public Brush TextBrush { get; }
    public string ToolTip { get; }

    public static IReadOnlyList<WorkflowGraphPortPinViewModel> BuildInputs(
        WorkflowGraphNodeViewModel node,
        string inputPortKind,
        bool hasConnectSource,
        bool isConnectSource,
        string sourceNodeType,
        string sourceOutputKind)
    {
        var index = 0;
        return WorkflowConnectionRules.PortKindTokens(inputPortKind)
            .Select(kind =>
            {
                var compatible = hasConnectSource
                    && !isConnectSource
                    && WorkflowConnectionRules.ArePortsCompatible(sourceOutputKind, kind, sourceNodeType, node.NodeType);
                var incompatible = hasConnectSource && !isConnectSource && !compatible;
                return new WorkflowGraphPortPinViewModel(node, "input", kind, index++, compatible, incompatible, activeSource: false);
            })
            .ToArray();
    }

    public static IReadOnlyList<WorkflowGraphPortPinViewModel> BuildOutputs(
        WorkflowGraphNodeViewModel node,
        string outputPortKind,
        bool isConnectSource)
    {
        var index = 0;
        return WorkflowConnectionRules.PortKindTokens(outputPortKind)
            .Select(kind => new WorkflowGraphPortPinViewModel(node, "output", kind, index++, compatible: false, incompatible: false, activeSource: isConnectSource))
            .ToArray();
    }

    public Point CanvasPoint()
    {
        var y = Node.Y
            + Node.BorderThickness.Top
            + WorkflowGraphNodeViewModel.PinStartOffset
            + Index * WorkflowGraphNodeViewModel.PinRowHeight
            + 11.5;
        var x = IsInput
            ? Node.X + Node.BorderThickness.Left + WorkflowGraphNodeViewModel.PinCenterInset - WorkflowGraphNodeViewModel.PinRadius
            : Node.X + WorkflowGraphNodeViewModel.NodeWidth - Node.BorderThickness.Right - WorkflowGraphNodeViewModel.PinCenterInset + WorkflowGraphNodeViewModel.PinRadius;
        return new Point(x, y);
    }

    private string BuildToolTip(bool compatible, bool incompatible, bool activeSource)
    {
        if (activeSource)
        {
            return $"当前连线源：{NodeId} emits {Kind}";
        }
        if (compatible)
        {
            return $"可连接到 {NodeId}：accepts {Kind}";
        }
        if (incompatible)
        {
            return $"不可连接到 {NodeId}：accepts {Kind}";
        }
        return IsInput ? $"输入引脚：{NodeId} accepts {Kind}" : $"输出引脚：{NodeId} emits {Kind}";
    }

    private static Brush BrushForKind(string kind) => WorkflowConnectionRules.NormalizePortKind(kind) switch
    {
        "execution" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
        "artifact" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
        "knowledge" => new SolidColorBrush(Color.FromRgb(37, 99, 235)),
        "signal" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
        "review" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
        "automation" => new SolidColorBrush(Color.FromRgb(13, 148, 136)),
        "control" => new SolidColorBrush(Color.FromRgb(124, 58, 237)),
        "*" => new SolidColorBrush(Color.FromRgb(75, 85, 99)),
        _ => new SolidColorBrush(Color.FromRgb(75, 85, 99)),
    };

    private static string ShortPortLabel(string kind) => WorkflowConnectionRules.NormalizePortKind(kind) switch
    {
        "*" => "任意",
        "execution" => "执行",
        "artifact" => "产物",
        "knowledge" => "知识",
        "signal" => "信号",
        "review" => "审核",
        "automation" => "自动化",
        "control" => "控制",
        _ => WorkflowConnectionRules.PortKindLabel(kind),
    };
}

public sealed class WorkflowGraphEdgeViewModel
{
    public WorkflowGraphEdgeViewModel(string sourceNodeId, string targetNodeId, string note, Geometry geometry, Brush strokeBrush, double strokeThickness, double opacity, Thickness labelMargin)
    {
        SourceNodeId = sourceNodeId;
        TargetNodeId = targetNodeId;
        Note = string.IsNullOrWhiteSpace(note) ? "" : note.Trim();
        Label = Note;
        ToolTip = string.IsNullOrWhiteSpace(Note)
            ? $"系统依赖：{SourceNodeId} -> {TargetNodeId}。双击可添加注释，Alt+点击断开连线。"
            : $"注释：{Note}{Environment.NewLine}系统依赖：{SourceNodeId} -> {TargetNodeId}";
        EditNoteLabel = string.IsNullOrWhiteSpace(Note) ? "添加连线注释" : "编辑连线注释";
        BreakLinkLabel = $"断开连线 {SourceNodeId} -> {TargetNodeId}";
        Geometry = geometry;
        StrokeBrush = strokeBrush;
        StrokeThickness = strokeThickness;
        Opacity = opacity;
        LabelMargin = labelMargin;
        LabelVisibility = string.IsNullOrWhiteSpace(Note) ? Visibility.Collapsed : Visibility.Visible;
    }

    public string SourceNodeId { get; }
    public string TargetNodeId { get; }
    public string Note { get; }
    public string Label { get; }
    public string ToolTip { get; }
    public string EditNoteLabel { get; }
    public string BreakLinkLabel { get; }
    public Geometry Geometry { get; }
    public Brush StrokeBrush { get; }
    public double StrokeThickness { get; }
    public double Opacity { get; }
    public Thickness LabelMargin { get; }
    public Visibility LabelVisibility { get; }

    public static WorkflowGraphEdgeViewModel FromNodes(WorkflowGraphNodeViewModel from, WorkflowGraphNodeViewModel to, string note = "")
    {
        var (start, end) = CompatiblePortPoints(from, to);
        var dx = end.X - start.X;
        var dy = end.Y - start.Y;
        var horizontalCurve = Math.Max(56, Math.Abs(dx) * 0.46);
        var backflowOffset = dx < 0 ? Math.Min(120, Math.Abs(dx) * 0.35) : 0;
        var geometry = new PathGeometry(new[]
        {
            new PathFigure(
                start,
                new PathSegment[]
                {
                    new BezierSegment(
                        new Point(start.X + horizontalCurve + backflowOffset, start.Y),
                        new Point(end.X - horizontalCurve - backflowOffset, end.Y),
                        end,
                        isStroked: true),
                },
                closed: false),
        });
        var active = from.Status is "succeeded" or "running" or "waiting" or "runnable" || to.Status is "running" or "waiting" or "runnable";
        var labelX = Math.Max(0, (start.X + end.X) / 2 - 54);
        var labelY = Math.Max(0, (start.Y + end.Y) / 2 - 14);
        return new WorkflowGraphEdgeViewModel(
            from.NodeId,
            to.NodeId,
            note,
            geometry,
            active ? new SolidColorBrush(Color.FromRgb(2, 80, 204)) : new SolidColorBrush(Color.FromRgb(184, 199, 221)),
            active ? 3 : 2,
            active ? 0.95 : 0.72,
            new Thickness(labelX, labelY, 0, 0));
    }

    private static Point InputPortPoint(WorkflowGraphNodeViewModel node)
    {
        return node.InputPins.FirstOrDefault()?.CanvasPoint()
            ?? new Point(node.X, node.Y + WorkflowGraphNodeViewModel.NodeHeight / 2);
    }

    private static Point OutputPortPoint(WorkflowGraphNodeViewModel node)
    {
        return node.OutputPins.FirstOrDefault()?.CanvasPoint()
            ?? new Point(node.X + WorkflowGraphNodeViewModel.NodeWidth, node.Y + WorkflowGraphNodeViewModel.NodeHeight / 2);
    }

    private static (Point Start, Point End) CompatiblePortPoints(WorkflowGraphNodeViewModel from, WorkflowGraphNodeViewModel to)
    {
        foreach (var output in from.OutputPins)
        {
            foreach (var input in to.InputPins)
            {
                if (WorkflowConnectionRules.ArePortsCompatible(output.Kind, input.Kind, from.NodeType, to.NodeType))
                {
                    return (output.CanvasPoint(), input.CanvasPoint());
                }
            }
        }
        return (OutputPortPoint(from), InputPortPoint(to));
    }
}

public sealed class WorkflowEditNodeViewModel
{
    public WorkflowEditNodeViewModel(
        string nodeId,
        string title,
        string nodeType,
        string assignedAgent,
        string dependsOnText,
        string toolName,
        string skillName,
        string reviewGate,
        string argumentsJson,
        double x,
        double y,
        string inputPortKind = "execution",
        string outputPortKind = "execution")
    {
        NodeId = string.IsNullOrWhiteSpace(nodeId) ? "node" : nodeId.Trim();
        Title = string.IsNullOrWhiteSpace(title) ? NodeId : title.Trim();
        NodeType = string.IsNullOrWhiteSpace(nodeType) ? "agent_task" : nodeType.Trim();
        AssignedAgent = assignedAgent.Trim();
        DependsOnText = dependsOnText.Trim();
        ToolName = toolName.Trim();
        SkillName = skillName.Trim();
        ReviewGate = reviewGate.Trim();
        ArgumentsJson = string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson.Trim();
        InputPortKind = WorkflowConnectionRules.NormalizePortKind(inputPortKind);
        OutputPortKind = WorkflowConnectionRules.NormalizePortKind(outputPortKind);
        X = x;
        Y = y;
        Status = NodeType;
        StatusLabel = WorkflowDisplayText.StatusLabel(NodeType);
        var actor = string.IsNullOrWhiteSpace(AssignedAgent)
            ? string.IsNullOrWhiteSpace(ToolName)
                ? string.IsNullOrWhiteSpace(SkillName)
                    ? ReviewGate
                    : SkillName
                : ToolName
            : AssignedAgent;
        Meta = $"{WorkflowDisplayText.TechnicalLine(NodeId)} · {WorkflowDisplayText.NodeTypeLabel(NodeType)} · {WorkflowDisplayText.ActorLabel(actor)}";
        Detail = DependsOn.Length == 0 ? "依赖：无" : $"依赖：{string.Join(", ", DependsOn.Select(dep => WorkflowDisplayText.ShortId(dep)))}";
        StatusBrush = WorkflowNodeViewModel.BrushForStatus(NodeType);
    }

    public string NodeId { get; }
    public string Id => NodeId;
    public string Title { get; }
    public string NodeType { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string AssignedAgent { get; }
    public string DependsOnText { get; }
    public string ToolName { get; }
    public string SkillName { get; }
    public string ReviewGate { get; }
    public string ArgumentsJson { get; }
    public string InputPortKind { get; }
    public string OutputPortKind { get; }
    public double X { get; }
    public double Y { get; }
    public string Meta { get; }
    public string Detail { get; }
    public bool Runnable => false;
    public Brush StatusBrush { get; }
    public string[] DependsOn => DependsOnText.Split(new[] { "\r\n", "\n", "\r", "," }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    public WorkflowNodeViewModel ToWorkflowNodeViewModel()
    {
        return new WorkflowNodeViewModel(NodeId, Title, NodeType, Meta, Detail, runnable: false);
    }

    public WorkflowEditNodeViewModel WithPosition(double x, double y)
    {
        return new WorkflowEditNodeViewModel(NodeId, Title, NodeType, AssignedAgent, DependsOnText, ToolName, SkillName, ReviewGate, ArgumentsJson, x, y, InputPortKind, OutputPortKind);
    }

    public WorkflowEditNodeViewModel ReplaceDependency(string oldNodeId, string newNodeId)
    {
        if (string.IsNullOrWhiteSpace(oldNodeId) || string.IsNullOrWhiteSpace(newNodeId))
        {
            return this;
        }
        var updated = DependsOn
            .Select(dep => string.Equals(dep, oldNodeId, StringComparison.OrdinalIgnoreCase) ? newNodeId : dep)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        return new WorkflowEditNodeViewModel(NodeId, Title, NodeType, AssignedAgent, string.Join(Environment.NewLine, updated), ToolName, SkillName, ReviewGate, ArgumentsJson, X, Y, InputPortKind, OutputPortKind);
    }

    public WorkflowEditNodeViewModel WithoutDependency(string nodeId)
    {
        var updated = DependsOn
            .Where(dep => !string.Equals(dep, nodeId, StringComparison.OrdinalIgnoreCase))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        return new WorkflowEditNodeViewModel(NodeId, Title, NodeType, AssignedAgent, string.Join(Environment.NewLine, updated), ToolName, SkillName, ReviewGate, ArgumentsJson, X, Y, InputPortKind, OutputPortKind);
    }

    public static WorkflowEditNodeViewModel FromJson(JsonElement node, int index = 0)
    {
        var nodeId = ReadString(node, "node_id");
        var argumentsJson = node.ValueKind == JsonValueKind.Object && node.TryGetProperty("arguments", out var arguments)
            ? FormatElement(arguments)
            : "{}";
        var nodeType = ReadString(node, "node_type", "agent_task");
        var position = ReadPosition(node, index, nodeType);
        return new WorkflowEditNodeViewModel(
            nodeId,
            ReadString(node, "label", nodeId),
            nodeType,
            ReadString(node, "assigned_agent"),
            string.Join(Environment.NewLine, ReadStringArray(node, "depends_on")),
            ReadString(node, "tool_name"),
            ReadString(node, "skill_name"),
            ReadString(node, "review_gate"),
            argumentsJson,
            position.X,
            position.Y,
            WorkflowGraphNodeViewModel.ReadInputPortKind(node),
            WorkflowGraphNodeViewModel.ReadOutputPortKind(node));
    }

    private static (double X, double Y) ReadPosition(JsonElement node, int index, string nodeType)
    {
        if (TryReadObject(node, "metadata", out var metadata)
            && TryReadObject(metadata, "position", out var position))
        {
            return (ReadDouble(position, "x", 24 + (index % 4) * WorkflowLaneLayout.NodeHorizontalGap), WorkflowLaneLayout.ClampNodeYToLane(nodeType, ReadDouble(position, "y", WorkflowLaneLayout.LaneDefaultY(nodeType))));
        }
        return (24 + (index % 4) * WorkflowLaneLayout.NodeHorizontalGap, WorkflowLaneLayout.LaneDefaultY(nodeType));
    }

    private static string FormatElement(JsonElement element)
    {
        try
        {
            return JsonSerializer.Serialize(element, new JsonSerializerOptions { WriteIndented = true });
        }
        catch
        {
            return element.GetRawText();
        }
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
