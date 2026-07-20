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

public sealed class WorkflowGraphNodeViewModel
{
    public const double NodeWidth = 248;
    public const double NodeHeight = 190;
    public const double PinStartOffset = 40;
    public const double PinRowHeight = 26;
    public const double PinCenterInset = 7.5;
    public const double PinRadius = 7.5;

    public WorkflowGraphNodeViewModel(string nodeId, string title, string nodeType, string status, string detail, double x, double y, bool runnable, bool selected, string connectSourceNodeId = "", string connectSourceNodeType = "", string connectSourceOutputKind = "", string inputPortKind = "execution", string outputPortKind = "execution", string actor = "", string progressLine = "", string queueLine = "", string contractLine = "", string currentQueueItemLine = "")
    {
        NodeId = nodeId;
        Title = string.IsNullOrWhiteSpace(title) ? nodeId : title;
        NodeType = string.IsNullOrWhiteSpace(nodeType) ? "--" : nodeType;
        Status = string.IsNullOrWhiteSpace(status) ? "pending" : status;
        StatusLabel = WorkflowDisplayText.StatusLabel(Status);
        SecondaryLine = $"{WorkflowDisplayText.NodeTypeLabel(NodeType)} · {WorkflowDisplayText.TechnicalLine(NodeId)}";
        ActorLine = WorkflowDisplayText.ActorLabel(actor);
        Detail = detail;
        InputPortKind = WorkflowConnectionRules.NormalizePortKind(inputPortKind);
        OutputPortKind = WorkflowConnectionRules.NormalizePortKind(outputPortKind);
        InputPortLabel = WorkflowConnectionRules.PortKindLabel(InputPortKind);
        OutputPortLabel = WorkflowConnectionRules.PortKindLabel(OutputPortKind);
        InputPortChip = $"In {InputPortLabel}";
        OutputPortChip = $"Out {OutputPortLabel}";
        ProgressLine = string.IsNullOrWhiteSpace(progressLine) ? "进度：未启动" : progressLine;
        QueueLine = string.IsNullOrWhiteSpace(queueLine) ? "队列：--" : queueLine;
        ContractLine = string.IsNullOrWhiteSpace(contractLine) ? $"接口：{InputPortLabel} -> {OutputPortLabel}" : contractLine;
        CurrentQueueItemLine = string.IsNullOrWhiteSpace(currentQueueItemLine) ? "" : currentQueueItemLine;
        CurrentQueueItemVisibility = string.IsNullOrWhiteSpace(CurrentQueueItemLine) ? Visibility.Collapsed : Visibility.Visible;
        ErrorText = Status is "failed" or "blocked" || detail.Contains("error", StringComparison.OrdinalIgnoreCase)
            ? string.IsNullOrWhiteSpace(detail) ? Status : detail
            : "";
        ErrorVisibility = string.IsNullOrWhiteSpace(ErrorText) ? Visibility.Collapsed : Visibility.Visible;
        X = x;
        Y = y;
        Runnable = runnable;
        Selected = selected;
        StatusBrush = WorkflowNodeViewModel.BrushForStatus(Status);
        BorderBrush = selected
            ? new SolidColorBrush(Color.FromRgb(233, 149, 65))
            : runnable
                ? new SolidColorBrush(Color.FromRgb(253, 200, 0))
                : BrushForNodeType(NodeType);
        BackgroundBrush = selected
            ? new SolidColorBrush(Color.FromRgb(237, 244, 255))
            : new SolidColorBrush(Color.FromRgb(255, 255, 255));
        BorderThickness = selected ? new Thickness(3) : new Thickness(runnable ? 2 : 1);
        ShadowEffect = selected
            ? new DropShadowEffect
            {
                Color = Color.FromRgb(233, 149, 65),
                Direction = 0,
                ShadowDepth = 0,
                BlurRadius = 16,
                Opacity = 0.28,
            }
            : runnable
                ? new DropShadowEffect
                {
                    Color = Color.FromRgb(253, 200, 0),
                    Direction = 0,
                    ShadowDepth = 0,
                    BlurRadius = 12,
                    Opacity = 0.2,
                }
                : null;
        InputPinBrush = BrushForPortKind(InputPortKind, BrushForNodeType(NodeType));
        OutputPinBrush = BrushForPortKind(OutputPortKind, BrushForNodeType(NodeType));
        var isConnectSource = !string.IsNullOrWhiteSpace(connectSourceNodeId)
            && string.Equals(connectSourceNodeId, NodeId, StringComparison.OrdinalIgnoreCase);
        var hasConnectSource = !string.IsNullOrWhiteSpace(connectSourceNodeId);
        InputPins = WorkflowGraphPortPinViewModel.BuildInputs(
            this,
            InputPortKind,
            hasConnectSource,
            isConnectSource,
            connectSourceNodeType,
            connectSourceOutputKind);
        OutputPins = WorkflowGraphPortPinViewModel.BuildOutputs(this, OutputPortKind, isConnectSource);
        var compatibleTarget = hasConnectSource
            && !isConnectSource
            && WorkflowConnectionRules.ArePortsCompatible(connectSourceOutputKind, InputPortKind, connectSourceNodeType, NodeType);
        InputPinStrokeBrush = compatibleTarget
            ? new SolidColorBrush(Color.FromRgb(34, 197, 94))
            : hasConnectSource && !isConnectSource
                ? new SolidColorBrush(Color.FromRgb(220, 38, 38))
                : new SolidColorBrush(Color.FromRgb(255, 255, 255));
        InputPinStrokeThickness = hasConnectSource && !isConnectSource ? 3 : 2;
        OutputPinStrokeBrush = isConnectSource
            ? new SolidColorBrush(Color.FromRgb(253, 200, 0))
            : new SolidColorBrush(Color.FromRgb(255, 255, 255));
        OutputPinStrokeThickness = isConnectSource ? 3 : 2;
        InputPortToolTip = hasConnectSource && !isConnectSource
            ? compatibleTarget
                ? $"可连接：{connectSourceOutputKind} -> {InputPortKind}"
                : WorkflowConnectionRules.IncompatibilityReason(connectSourceNodeType, NodeType, connectSourceOutputKind, InputPortKind)
            : $"输入端口：accepts {InputPortKind}";
        OutputPortToolTip = isConnectSource ? $"当前连线源：emits {OutputPortKind}" : $"输出端口：emits {OutputPortKind}";
        LaneLabel = LaneLabelForNodeType(NodeType);
    }

    public string NodeId { get; }
    public string Title { get; }
    public string NodeType { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string SecondaryLine { get; }
    public string ActorLine { get; }
    public string Detail { get; }
    public string InputPortKind { get; }
    public string OutputPortKind { get; }
    public string InputPortLabel { get; }
    public string OutputPortLabel { get; }
    public string InputPortChip { get; }
    public string OutputPortChip { get; }
    public IReadOnlyList<WorkflowGraphPortPinViewModel> InputPins { get; }
    public IReadOnlyList<WorkflowGraphPortPinViewModel> OutputPins { get; }
    public string ProgressLine { get; }
    public string QueueLine { get; }
    public string ContractLine { get; }
    public string CurrentQueueItemLine { get; }
    public Visibility CurrentQueueItemVisibility { get; }
    public string ErrorText { get; }
    public Visibility ErrorVisibility { get; }
    public double X { get; }
    public double Y { get; }
    public bool Runnable { get; }
    public bool Selected { get; }
    public Brush StatusBrush { get; }
    public Brush BorderBrush { get; }
    public Thickness BorderThickness { get; }
    public Brush BackgroundBrush { get; }
    public Effect? ShadowEffect { get; }
    public Brush InputPinBrush { get; }
    public Brush OutputPinBrush { get; }
    public Brush InputPinStrokeBrush { get; }
    public double InputPinStrokeThickness { get; }
    public Brush OutputPinStrokeBrush { get; }
    public double OutputPinStrokeThickness { get; }
    public string InputPortToolTip { get; }
    public string OutputPortToolTip { get; }
    public string LaneLabel { get; }

    public static WorkflowGraphNodeViewModel FromJson(JsonElement definitionNode, JsonElement runState, JsonElement detail, bool runnable, bool selected, int index, string connectSourceNodeId = "", string connectSourceNodeType = "", string connectSourceOutputKind = "")
    {
        var nodeId = ReadString(definitionNode, "node_id");
        var nodeType = ReadString(definitionNode, "node_type", "--");
        var status = runState.ValueKind == JsonValueKind.Object ? ReadString(runState, "status", "pending") : "pending";
        if (runnable && status == "pending")
        {
            status = "runnable";
        }
        var actor = runState.ValueKind == JsonValueKind.Object
            ? ReadString(runState, "assigned_agent")
            : "";
        if (string.IsNullOrWhiteSpace(actor))
        {
            actor = ReadString(definitionNode, "assigned_agent",
                ReadString(definitionNode, "tool_name",
                    ReadString(definitionNode, "skill_name",
                        ReadString(definitionNode, "review_gate", "--"))));
        }
        var attempts = runState.ValueKind == JsonValueKind.Object ? ReadInt(runState, "attempts") : 0;
        var error = runState.ValueKind == JsonValueKind.Object ? ReadString(runState, "error") : "";
        var position = ReadPosition(definitionNode, index, nodeType);
        return new WorkflowGraphNodeViewModel(
            nodeId,
            ReadString(definitionNode, "label", nodeId),
            nodeType,
            status,
            string.IsNullOrWhiteSpace(error)
                ? $"{WorkflowDisplayText.NodeTypeLabel(nodeType)} · {WorkflowDisplayText.ActorLabel(actor)} · 尝试 {attempts}"
                : error,
            position.X,
            position.Y,
            runnable,
            selected,
            connectSourceNodeId,
            connectSourceNodeType,
            connectSourceOutputKind,
            ReadInputPortKind(definitionNode),
            ReadOutputPortKind(definitionNode),
            actor,
            BuildProgressLine(detail, status, runnable),
            BuildQueueLine(detail, actor),
            BuildContractLine(detail, ReadInputPortKind(definitionNode), ReadOutputPortKind(definitionNode)),
            BuildCurrentQueueItemLine(detail));
    }

    private static string BuildProgressLine(JsonElement detail, string status, bool runnable)
    {
        if (detail.ValueKind == JsonValueKind.Object
            && TryReadObject(detail, "progress", out var progress))
        {
            var percent = ReadDouble(progress, "percent", 0);
            return $"进度：{percent:0.#}%";
        }
        return runnable ? "进度：可执行" : $"进度：{WorkflowDisplayText.StatusLabel(status)}";
    }

    private static string BuildQueueLine(JsonElement detail, string actor)
    {
        if (TryReadObject(detail, "node_queue_summary", out var summary))
        {
            var total = ReadInt(summary, "total");
            var currentIndex = ReadInt(summary, "current_index");
            var remaining = ReadInt(summary, "remaining");
            var current = "";
            if (summary.TryGetProperty("current_item", out var currentItem) && currentItem.ValueKind == JsonValueKind.Object)
            {
                current = QueueItemLabel(currentItem);
            }
            if (!string.IsNullOrWhiteSpace(current))
            {
                return $"节点队列：当前 {current} · 后续 {Math.Max(0, remaining)} / 共 {total}";
            }
            if (total > 0)
            {
                return $"节点队列：待处理 {remaining} / 共 {total}";
            }
            if (currentIndex > 0)
            {
                return $"节点队列：已到第 {currentIndex} 项";
            }
        }
        if (detail.ValueKind == JsonValueKind.Object
            && detail.TryGetProperty("node_task_queue", out var nodeQueue)
            && nodeQueue.ValueKind == JsonValueKind.Array)
        {
            var items = nodeQueue.EnumerateArray()
                .Take(3)
                .Select(QueueItemLabel)
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .ToArray();
            if (items.Length > 0)
            {
                return $"节点队列：{string.Join(" | ", items)}";
            }
        }
        if (detail.ValueKind == JsonValueKind.Object
            && detail.TryGetProperty("agent_task_queue", out var queue)
            && queue.ValueKind == JsonValueKind.Array)
        {
            var items = queue.EnumerateArray()
                .Take(3)
                .Select(item => $"{ReadString(item, "node_id", "node")}:{WorkflowDisplayText.StatusLabel(ReadString(item, "status", "pending"))}")
                .ToArray();
            if (items.Length > 0)
            {
                return $"Agent 队列：{string.Join(" | ", items)}";
            }
        }
        return $"节点队列：-- · {WorkflowDisplayText.ActorLabel(actor)}";
    }

    private static string BuildCurrentQueueItemLine(JsonElement detail)
    {
        if (!TryReadObject(detail, "node_queue_summary", out var summary)
            || !summary.TryGetProperty("current_item", out var currentItem)
            || currentItem.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        var label = QueueItemLabel(currentItem);
        var source = ReadString(currentItem, "queue_source");
        var key = ReadString(currentItem, "queue_key");
        var sourceText = string.IsNullOrWhiteSpace(source) ? "" : $" · {source}{(string.IsNullOrWhiteSpace(key) ? "" : $"/{key}")}";
        return string.IsNullOrWhiteSpace(label) ? "" : $"当前项：{label}{sourceText}";
    }

    private static string BuildContractLine(JsonElement detail, string inputPortKind, string outputPortKind)
    {
        var input = WorkflowConnectionRules.PortKindLabel(inputPortKind);
        var output = WorkflowConnectionRules.PortKindLabel(outputPortKind);
        if (TryReadObject(detail, "interface_contract", out var contract))
        {
            var summary = ReadString(contract, "summary");
            var inputNames = ContractItemNames(contract, "inputs", 2);
            var outputNames = ContractItemNames(contract, "outputs", 2);
            var names = $"{(inputNames.Length == 0 ? input : string.Join(", ", inputNames))} -> {(outputNames.Length == 0 ? output : string.Join(", ", outputNames))}";
            return string.IsNullOrWhiteSpace(summary) ? $"接口：{names}" : $"接口：{names} · {summary}";
        }
        return $"接口：{input} -> {output}";
    }

    private static string[] ContractItemNames(JsonElement contract, string key, int limit)
    {
        if (!contract.TryGetProperty(key, out var items) || items.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return items.EnumerateArray()
            .Take(limit)
            .Select(item => ReadString(item, "name", ReadString(item, "kind")))
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

    private static string QueueItemLabel(JsonElement item)
    {
        if (item.ValueKind != JsonValueKind.Object)
        {
            return item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText();
        }
        var label = ReadString(item, "label",
            ReadString(item, "title",
                ReadString(item, "task_id",
                    ReadString(item, "product_id",
                        ReadString(item, "id", "item")))));
        var status = WorkflowDisplayText.StatusLabel(ReadString(item, "status", "pending"));
        return $"{label}:{status}";
    }

    public static string ReadInputPortKind(JsonElement definitionNode) =>
        ReadConnectionPolicyKind(
            definitionNode,
            "input_accepts",
            "input",
            WorkflowConnectionRules.DefaultInputPortKindForNodeType(ReadString(definitionNode, "node_type", "agent_task")));

    public static string ReadOutputPortKind(JsonElement definitionNode) =>
        ReadConnectionPolicyKind(
            definitionNode,
            "output_emits",
            "output",
            WorkflowConnectionRules.DefaultOutputPortKindForNodeType(ReadString(definitionNode, "node_type", "agent_task")));

    private static string ReadConnectionPolicyKind(JsonElement definitionNode, string policyKey, string direction, string fallback)
    {
        var fallbackKind = WorkflowConnectionRules.NormalizePortKind(fallback);
        if (TryReadObject(definitionNode, "metadata", out var metadata))
        {
            if (TryReadObject(metadata, "connection_policy", out var policy))
            {
                var kind = NormalizeDisplayPortKind(ReadString(policy, policyKey), fallbackKind);
                if (!string.IsNullOrWhiteSpace(kind))
                {
                    return kind;
                }
            }
            if (metadata.TryGetProperty("ports", out var ports) && ports.ValueKind == JsonValueKind.Array)
            {
                foreach (var port in ports.EnumerateArray())
                {
                    if (string.Equals(ReadString(port, "direction"), direction, StringComparison.OrdinalIgnoreCase))
                    {
                        var kind = NormalizeDisplayPortKind(ReadString(port, "kind"), fallbackKind);
                        if (!string.IsNullOrWhiteSpace(kind))
                        {
                            return kind;
                        }
                    }
                }
            }
        }
        return fallbackKind;
    }

    private static string NormalizeDisplayPortKind(string kind, string fallback)
    {
        var normalized = WorkflowConnectionRules.NormalizePortKind(kind);
        var fallbackKind = WorkflowConnectionRules.NormalizePortKind(fallback);
        return WorkflowConnectionRules.HasKnownPortKind(normalized) ? normalized : fallbackKind;
    }

    private static (double X, double Y) ReadPosition(JsonElement definitionNode, int index, string nodeType)
    {
        if (TryReadObject(definitionNode, "metadata", out var metadata)
            && TryReadObject(metadata, "position", out var position))
        {
            var x = ReadDouble(position, "x", 24 + (index % 4) * WorkflowLaneLayout.NodeHorizontalGap);
            var y = ReadDouble(position, "y", WorkflowLaneLayout.LaneDefaultY(nodeType));
            return (x, WorkflowLaneLayout.ClampNodeYToLane(nodeType, y));
        }
        return (24 + (index % 4) * WorkflowLaneLayout.NodeHorizontalGap, WorkflowLaneLayout.LaneDefaultY(nodeType));
    }

    private static Brush BrushForNodeType(string nodeType) => nodeType.ToLowerInvariant() switch
    {
        "agent_task" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
        "tool_call" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
        "skill_call" => new SolidColorBrush(Color.FromRgb(37, 99, 235)),
        "review_gate" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
        "branch" => new SolidColorBrush(Color.FromRgb(124, 58, 237)),
        "subgraph" => new SolidColorBrush(Color.FromRgb(8, 145, 178)),
        "waiter" => new SolidColorBrush(Color.FromRgb(15, 118, 110)),
        "external_callback" => new SolidColorBrush(Color.FromRgb(190, 18, 60)),
        "workflow.android_step" => new SolidColorBrush(Color.FromRgb(13, 148, 136)),
        "automation.android_step" => new SolidColorBrush(Color.FromRgb(13, 148, 136)),
        _ => new SolidColorBrush(Color.FromRgb(100, 116, 139)),
    };

    private static Brush BrushForPortKind(string portKind, Brush fallback) => WorkflowConnectionRules.NormalizePortKind(portKind) switch
    {
        "artifact" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
        "knowledge" => new SolidColorBrush(Color.FromRgb(124, 58, 237)),
        "signal" => new SolidColorBrush(Color.FromRgb(15, 118, 110)),
        "review" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
        "automation" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
        "control" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
        "execution" => fallback,
        _ => fallback,
    };

    private static string LaneLabelForNodeType(string nodeType) => nodeType.ToLowerInvariant() switch
    {
        "agent_task" => "Agent 任务",
        "tool_call" => "工具调用",
        "skill_call" => "Skill 调用",
        "review_gate" => "审核门",
        "branch" => "条件分支",
        "subgraph" => "子工作流",
        "waiter" => "等待信号",
        "external_callback" => "外部回调",
        "workflow.android_step" => "Android 步骤",
        "automation.android_step" => "Android 步骤",
        _ => "节点",
    };

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
