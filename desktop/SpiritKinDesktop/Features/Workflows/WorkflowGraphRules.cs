using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

namespace SpiritKinDesktop;

internal static class WorkflowLaneLayout
{
    public const double LaneHeight = 268;
    public const double LanePaddingTop = 40;
    public const double LanePaddingBottom = 18;
    public const double NodeHorizontalGap = 276;

    public static string LaneKeyForNodeType(string nodeType) => nodeType.Trim().ToLowerInvariant() switch
    {
        "agent_task" => "agent_task",
        "tool_call" or "skill_call" => "tool_call",
        _ when WorkflowOpenNodeRules.IsOpenNodeType(nodeType) => "tool_call",
        "review_gate" => "review_gate",
        _ => "other",
    };

    public static double LaneTopForNodeType(string nodeType) => LaneKeyForNodeType(nodeType) switch
    {
        "agent_task" => 0,
        "tool_call" => LaneHeight,
        "review_gate" => LaneHeight * 2,
        _ => LaneHeight * 3,
    };

    public static double LaneDefaultY(string nodeType) => LaneTopForNodeType(nodeType) + LanePaddingTop;

    public static bool AlignToLanes { get; set; }

    public static double ClampNodeYToLane(string nodeType, double y, bool force = false)
    {
        if (!force && !AlignToLanes)
        {
            return Math.Round(Math.Max(0, y));
        }
        var top = LaneTopForNodeType(nodeType) + LanePaddingTop;
        var bottom = LaneTopForNodeType(nodeType) + LaneHeight - WorkflowGraphNodeViewModel.NodeHeight - LanePaddingBottom;
        return Math.Round(Math.Clamp(y, top, Math.Max(top, bottom)));
    }
}

internal static class WorkflowConnectionRules
{
    private static readonly object SchemaLock = new();
    private static readonly Dictionary<string, string> SchemaInputPortKinds = new(StringComparer.OrdinalIgnoreCase);
    private static readonly Dictionary<string, string> SchemaOutputPortKinds = new(StringComparer.OrdinalIgnoreCase);
    private static readonly Dictionary<string, string> SchemaTypeLabels = new(StringComparer.OrdinalIgnoreCase);
    private static readonly Dictionary<string, Dictionary<string, bool>> SchemaCompatibility = new(StringComparer.OrdinalIgnoreCase);

    public static void ApplySchema(JsonElement workflowsOrSchema)
    {
        var schema = workflowsOrSchema;
        if (workflowsOrSchema.ValueKind == JsonValueKind.Object
            && workflowsOrSchema.TryGetProperty("node_catalog", out var nodeCatalog)
            && nodeCatalog.ValueKind == JsonValueKind.Object)
        {
            schema = nodeCatalog;
        }
        else if (workflowsOrSchema.ValueKind == JsonValueKind.Object
            && workflowsOrSchema.TryGetProperty("schema", out var nestedSchema)
            && nestedSchema.ValueKind == JsonValueKind.Object)
        {
            schema = nestedSchema;
        }
        if (schema.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        var inputKinds = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var outputKinds = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var labels = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (schema.TryGetProperty("node_types", out var nodeTypes) && nodeTypes.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in nodeTypes.EnumerateArray())
            {
                var nodeType = JsonString(item, "node_type").Trim().ToLowerInvariant();
                if (string.IsNullOrWhiteSpace(nodeType))
                {
                    continue;
                }
                var input = JsonString(item, "default_input_port_kind");
                var output = JsonString(item, "default_output_port_kind");
                var label = JsonString(item, "label");
                if (!string.IsNullOrWhiteSpace(input))
                {
                    inputKinds[nodeType] = NormalizePortKind(input);
                }
                if (!string.IsNullOrWhiteSpace(output))
                {
                    outputKinds[nodeType] = NormalizePortKind(output);
                }
                if (!string.IsNullOrWhiteSpace(label))
                {
                    labels[nodeType] = label;
                }
            }
        }

        var compatibility = new Dictionary<string, Dictionary<string, bool>>(StringComparer.OrdinalIgnoreCase);
        if (schema.TryGetProperty("compatibility_matrix", out var matrix) && matrix.ValueKind == JsonValueKind.Object)
        {
            foreach (var source in matrix.EnumerateObject())
            {
                if (source.Value.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                var row = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
                foreach (var target in source.Value.EnumerateObject())
                {
                    if (target.Value.ValueKind == JsonValueKind.True || target.Value.ValueKind == JsonValueKind.False)
                    {
                        row[target.Name.Trim().ToLowerInvariant()] = target.Value.GetBoolean();
                    }
                }
                compatibility[source.Name.Trim().ToLowerInvariant()] = row;
            }
        }

        lock (SchemaLock)
        {
            SchemaInputPortKinds.Clear();
            SchemaOutputPortKinds.Clear();
            SchemaTypeLabels.Clear();
            SchemaCompatibility.Clear();
            foreach (var item in inputKinds)
            {
                SchemaInputPortKinds[item.Key] = item.Value;
            }
            foreach (var item in outputKinds)
            {
                SchemaOutputPortKinds[item.Key] = item.Value;
            }
            foreach (var item in labels)
            {
                SchemaTypeLabels[item.Key] = item.Value;
            }
            foreach (var item in compatibility)
            {
                SchemaCompatibility[item.Key] = item.Value;
            }
        }
    }

    public static bool IsCompatible(string sourceNodeType, string targetNodeType)
    {
        return AreNodeTypesCompatible(sourceNodeType, targetNodeType);
    }

    public static bool ArePortsCompatible(string sourceOutputKind, string targetInputKind, string sourceNodeType, string targetNodeType)
    {
        if (!AreNodeTypesCompatible(sourceNodeType, targetNodeType))
        {
            return false;
        }
        var emittedKinds = PortKindSet(sourceOutputKind);
        var acceptedKinds = PortKindSet(targetInputKind);
        return acceptedKinds.Contains("*", StringComparer.OrdinalIgnoreCase)
            || emittedKinds.Contains("*", StringComparer.OrdinalIgnoreCase)
            || emittedKinds.Overlaps(acceptedKinds);
    }

    public static string IncompatibilityReason(string sourceNodeType, string targetNodeType, string sourceOutputKind = "execution", string targetInputKind = "execution")
    {
        var source = TypeLabel(sourceNodeType);
        var target = TypeLabel(targetNodeType);
        if (NormalizeNodeType(sourceNodeType) == "review_gate" && NormalizeNodeType(targetNodeType) == "review_gate")
        {
            return $"不建议直接连接 {source} 到 {target}；审核后应回到任务、工具或 Skill。";
        }
        if (AreNodeTypesCompatible(sourceNodeType, targetNodeType))
        {
            return $"端口类型不兼容：输出 {NormalizePortKind(sourceOutputKind)}，输入接受 {NormalizePortKind(targetInputKind)}。";
        }
        return $"当前执行流规则不允许 {source} 到 {target}。";
    }

    public static string NormalizePortKind(string portKind)
    {
        var normalized = (portKind ?? "").Trim().ToLowerInvariant();
        return string.IsNullOrWhiteSpace(normalized) ? "execution" : normalized;
    }

    public static string DefaultInputPortKindForNodeType(string nodeType)
    {
        var schemaKey = RawNodeTypeKey(nodeType);
        lock (SchemaLock)
        {
            if (SchemaInputPortKinds.TryGetValue(schemaKey, out var schemaKind))
            {
                return schemaKind;
            }
        }
        return NormalizeNodeType(nodeType) switch
    {
        "agent_task" => "execution|artifact|knowledge",
        "tool_call" => "execution|artifact",
        "skill_call" => "execution|artifact|knowledge",
        "review_gate" => "execution|artifact|review",
        "branch" => "execution|signal|control",
        "subgraph" => "execution|artifact|control",
        "foreach" => "execution|artifact|control",
        "waiter" => "signal|control",
        "external_callback" => "signal|control",
        "workflow.android_step" => "execution|automation|control",
        "automation.android_step" => "execution|automation|control",
        _ when WorkflowOpenNodeRules.IsOpenNodeType(nodeType) => "execution|automation|signal|control",
        _ => "execution",
    };
    }

    public static string DefaultOutputPortKindForNodeType(string nodeType)
    {
        var schemaKey = RawNodeTypeKey(nodeType);
        lock (SchemaLock)
        {
            if (SchemaOutputPortKinds.TryGetValue(schemaKey, out var schemaKind))
            {
                return schemaKind;
            }
        }
        return NormalizeNodeType(nodeType) switch
    {
        "agent_task" => "execution|artifact|knowledge",
        "tool_call" => "execution|artifact|automation",
        "skill_call" => "execution|artifact|knowledge",
        "review_gate" => "execution|review",
        "branch" => "execution|control",
        "subgraph" => "execution|artifact|control",
        "foreach" => "execution|artifact|control",
        "waiter" => "execution|signal",
        "external_callback" => "execution|signal",
        "workflow.android_step" => "execution|automation|signal",
        "automation.android_step" => "execution|automation|signal",
        _ when WorkflowOpenNodeRules.IsOpenNodeType(nodeType) => "execution|automation|signal",
        _ => "execution",
    };
    }

    public static string PrimaryPortKind(string portKind)
    {
        var kinds = PortKindTokens(portKind);
        return kinds.FirstOrDefault(kind => !string.Equals(kind, "execution", StringComparison.OrdinalIgnoreCase))
            ?? kinds.FirstOrDefault()
            ?? "execution";
    }

    public static string PortKindLabel(string portKind)
    {
        return string.Join("/", PortKindTokens(portKind).Select(PortKindTokenLabel));
    }

    public static IReadOnlyList<string> PortKindTokens(string portKind)
    {
        var tokens = NormalizePortKind(portKind)
            .Split(new[] { '|', ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(NormalizePortKind)
            .Where(kind => !string.IsNullOrWhiteSpace(kind))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        return tokens.Length == 0 ? new[] { "execution" } : tokens;
    }

    public static bool HasKnownPortKind(string portKind)
    {
        return PortKindTokens(portKind).All(IsKnownPortKindToken);
    }

    internal static string PortKindTokenLabel(string portKind) => NormalizePortKind(portKind) switch
    {
        "*" => "任意",
        "execution" => "执行",
        "artifact" => "产物",
        "knowledge" => "知识",
        "signal" => "信号",
        "review" => "审核",
        "automation" => "自动化",
        "control" => "控制",
        _ => portKind,
    };

    internal static bool IsKnownPortKindToken(string portKind) => NormalizePortKind(portKind) switch
    {
        "*" or "execution" or "artifact" or "knowledge" or "signal" or "review" or "automation" or "control" => true,
        _ => false,
    };

    public static string TypeLabel(string nodeType)
    {
        var fallback = NormalizeNodeType(nodeType) switch
    {
        "agent_task" => "Agent 任务",
        "tool_call" => "工具调用",
        "skill_call" => "Skill 调用",
        "review_gate" => "审核门",
        "branch" => "条件分支",
        "subgraph" => "子工作流",
        "foreach" => "循环",
        "waiter" => "等待信号",
        "external_callback" => "外部回调",
        "workflow.android_step" => "Android 步骤",
        "automation.android_step" => "Android 步骤",
        _ => "节点",
    };
        if (fallback != "节点")
        {
            return fallback;
        }
        var schemaKey = RawNodeTypeKey(nodeType);
        lock (SchemaLock)
        {
            if (SchemaTypeLabels.TryGetValue(schemaKey, out var schemaLabel) && !string.IsNullOrWhiteSpace(schemaLabel))
            {
                return schemaLabel;
            }
        }
        return fallback;
    }

    internal static bool AreNodeTypesCompatible(string sourceNodeType, string targetNodeType)
    {
        var sourceKey = RawNodeTypeKey(sourceNodeType);
        var targetKey = RawNodeTypeKey(targetNodeType);
        lock (SchemaLock)
        {
            if (SchemaCompatibility.TryGetValue(sourceKey, out var row)
                && row.TryGetValue(targetKey, out var compatible))
            {
                return compatible;
            }
        }
        var source = NormalizeNodeType(sourceNodeType);
        var target = NormalizeNodeType(targetNodeType);
        if (source == "other" || target == "other")
        {
            return true;
        }
        if (source == "review_gate" && target == "review_gate")
        {
            return false;
        }
        return target is "agent_task" or "tool_call" or "skill_call" or "review_gate" or "branch" or "subgraph" or "foreach" or "waiter" or "external_callback" or "workflow.android_step" or "automation.android_step";
    }

    private static string RawNodeTypeKey(string nodeType) => (nodeType ?? "").Trim().ToLowerInvariant();

    private static string JsonString(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return "";
        }
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? "",
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            _ => "",
        };
    }

    internal static HashSet<string> PortKindSet(string portKind)
    {
        return PortKindTokens(portKind).ToHashSet(StringComparer.OrdinalIgnoreCase);
    }

    internal static string NormalizeNodeType(string nodeType) => (nodeType ?? "").Trim().ToLowerInvariant() switch
    {
        "agent_task" => "agent_task",
        "tool_call" => "tool_call",
        "skill_call" => "skill_call",
        "review_gate" => "review_gate",
        "branch" => "branch",
        "subgraph" => "subgraph",
        "foreach" => "foreach",
        "waiter" => "waiter",
        "external_callback" => "external_callback",
        "workflow.android_step" => "workflow.android_step",
        "automation.android_step" => "automation.android_step",
        _ when WorkflowOpenNodeRules.IsOpenNodeType(nodeType) => "other",
        _ => "other",
    };
}

internal static class WorkflowOpenNodeRules
{
    public static bool IsOpenNodeType(string? nodeType)
    {
        var normalized = (nodeType ?? "").Trim().ToLowerInvariant();
        return normalized.StartsWith("custom.", StringComparison.Ordinal)
            || normalized.StartsWith("external.", StringComparison.Ordinal)
            || normalized.StartsWith("integration.", StringComparison.Ordinal)
            || normalized.StartsWith("automation.", StringComparison.Ordinal);
    }
}
