using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal void RenderWorkflowInspectorNodeSummary(string? nodeId)
    {
        if (string.IsNullOrWhiteSpace(nodeId))
        {
            WorkbenchShell.ManagementPanels.WorkflowInspectorNodeText.Text = "未选择节点。";
            return;
        }
        var run = FindWorkflowRun(_activeWorkflowRunId);
        var definition = ActiveWorkflowGraphDefinition();
        JsonElement definitionNode = default;
        if (definition.ValueKind == JsonValueKind.Object
            && definition.TryGetProperty("nodes", out var nodes)
            && nodes.ValueKind == JsonValueKind.Array)
        {
            definitionNode = nodes.EnumerateArray().FirstOrDefault(node => string.Equals(ReadJsonString(node, "node_id"), nodeId, StringComparison.OrdinalIgnoreCase));
        }
        if (definitionNode.ValueKind != JsonValueKind.Object)
        {
            WorkbenchShell.ManagementPanels.WorkflowInspectorNodeText.Text = $"节点 {nodeId} 不在当前定义中。";
            return;
        }
        JsonElement detail = default;
        if (run.ValueKind == JsonValueKind.Object
            && TryReadJsonObject(run, "selected_node_details", out var detailMap)
            && detailMap.TryGetProperty(nodeId, out var detailElement))
        {
            detail = detailElement;
        }
        var agent = ReadSafeJsonString(detail, "effective_agent", ReadSafeJsonString(definitionNode, "assigned_agent", "--"));
        var explicitSkill = ReadSafeJsonString(definitionNode, "skill_name", "");
        var nodeType = WorkflowDisplayText.NodeTypeLabel(ReadSafeJsonString(definitionNode, "node_type", "--"));
        var skills = detail.ValueKind == JsonValueKind.Object && TryReadJsonArray(detail, "available_skills")
            ? detail.GetProperty("available_skills").EnumerateArray().Take(6).Select(item => $"{ReadJsonString(item, "name")}:{WorkflowDisplayText.StatusLabel(ReadJsonString(item, "status", "--"))}").ToArray()
            : ReadAgentSkillMap(agent).Take(6).ToArray();
        var progress = detail.ValueKind == JsonValueKind.Object && TryReadJsonObject(detail, "progress", out var progressObject)
            ? $"{ReadJsonDouble(progressObject, "percent"):0.#}%"
            : "未启动运行";
        var nodeQueue = WorkflowNodeQueueLines(detail, 6);
        var agentQueue = WorkflowAgentQueueLines(detail, 6);
        var currentQueueItem = WorkflowNodeCurrentQueueLine(detail);
        var nextQueueItems = WorkflowNodeNextQueueLines(detail, 4);
        var builder = new StringBuilder();
        builder.AppendLine($"{nodeId} · {nodeType}");
        builder.AppendLine($"执行 Agent：{WorkflowDisplayText.ActorLabel(agent)}");
        builder.AppendLine($"输入 / 输出：{WorkflowContractDetailLine(detail, definitionNode)}");
        builder.AppendLine($"显式 Skill：{(string.IsNullOrWhiteSpace(explicitSkill) ? "--" : explicitSkill)}");
        builder.AppendLine($"Agent 可用 Skill：{(skills.Length == 0 ? "--" : string.Join(", ", skills))}");
        builder.AppendLine($"进度：{progress}");
        builder.AppendLine($"当前处理项：{(string.IsNullOrWhiteSpace(currentQueueItem) ? "--" : currentQueueItem)}");
        builder.AppendLine($"后续队列：{(nextQueueItems.Length == 0 ? "--" : string.Join(" | ", nextQueueItems))}");
        builder.AppendLine($"节点任务队列：{(nodeQueue.Length == 0 ? "运行后显示该节点任务队列" : string.Join(" | ", nodeQueue))}");
        builder.AppendLine($"Agent 节点队列：{(agentQueue.Length == 0 ? "--" : string.Join(" | ", agentQueue))}");
        WorkbenchShell.ManagementPanels.WorkflowInspectorNodeText.Text = builder.ToString().TrimEnd();
    }

    internal static string[] WorkflowNodeQueueLines(JsonElement detail, int limit)
    {
        if (detail.ValueKind != JsonValueKind.Object
            || !detail.TryGetProperty("node_task_queue", out var queue)
            || queue.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return queue.EnumerateArray()
            .Take(limit)
            .Select(WorkflowNodeQueueLine)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

    internal static string[] WorkflowAgentQueueLines(JsonElement detail, int limit)
    {
        if (detail.ValueKind != JsonValueKind.Object
            || !detail.TryGetProperty("agent_task_queue", out var queue)
            || queue.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return queue.EnumerateArray()
            .Take(limit)
            .Select(item => $"{ReadJsonString(item, "node_id", "node")}:{WorkflowDisplayText.StatusLabel(ReadJsonString(item, "status", "pending"))}")
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

    internal static string WorkflowNodeQueueSummaryLine(JsonElement detail)
    {
        if (!TryReadJsonObject(detail, "node_queue_summary", out var summary))
        {
            return "--";
        }
        var total = ReadJsonInt(summary, "total");
        var remaining = ReadJsonInt(summary, "remaining");
        var current = WorkflowNodeCurrentQueueLine(detail);
        return string.IsNullOrWhiteSpace(current)
            ? $"待处理 {remaining} / 共 {total}"
            : $"当前 {current} · 后续 {Math.Max(0, remaining)} / 共 {total}";
    }

    internal static string WorkflowNodeCurrentQueueLine(JsonElement detail)
    {
        if (!TryReadJsonObject(detail, "node_queue_summary", out var summary)
            || !summary.TryGetProperty("current_item", out var current)
            || current.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        return WorkflowNodeQueueLine(current);
    }

    internal static string[] WorkflowNodeNextQueueLines(JsonElement detail, int limit)
    {
        if (!TryReadJsonObject(detail, "node_queue_summary", out var summary)
            || !summary.TryGetProperty("next_items", out var nextItems)
            || nextItems.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return nextItems.EnumerateArray()
            .Take(limit)
            .Select(WorkflowNodeQueueLine)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

    internal static string WorkflowContractDetailLine(JsonElement detail, JsonElement definitionNode)
    {
        var inputKind = WorkflowGraphNodeViewModel.ReadInputPortKind(definitionNode);
        var outputKind = WorkflowGraphNodeViewModel.ReadOutputPortKind(definitionNode);
        var inputFallback = WorkflowConnectionRules.PortKindLabel(inputKind);
        var outputFallback = WorkflowConnectionRules.PortKindLabel(outputKind);
        if (!TryReadJsonObject(detail, "interface_contract", out var contract))
        {
            return $"{inputFallback} -> {outputFallback}";
        }
        var inputs = WorkflowContractItemLines(contract, "inputs", 3);
        var outputs = WorkflowContractItemLines(contract, "outputs", 3);
        var summary = ReadJsonString(contract, "summary");
        var line = $"{(inputs.Length == 0 ? inputFallback : string.Join(", ", inputs))} -> {(outputs.Length == 0 ? outputFallback : string.Join(", ", outputs))}";
        return string.IsNullOrWhiteSpace(summary) ? line : $"{line} · {summary}";
    }

    internal static string[] WorkflowContractItemLines(JsonElement contract, string key, int limit)
    {
        if (!contract.TryGetProperty(key, out var items) || items.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return items.EnumerateArray()
            .Take(limit)
            .Select(item =>
            {
                var name = ReadJsonString(item, "name", ReadJsonString(item, "kind", "item"));
                var kind = ReadJsonString(item, "kind");
                var required = ReadJsonBool(item, "required", false) ? "*" : "";
                return string.IsNullOrWhiteSpace(kind) ? $"{name}{required}" : $"{name}{required}:{kind}";
            })
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

    internal static string WorkflowNodeQueueLine(JsonElement item)
    {
        if (item.ValueKind != JsonValueKind.Object)
        {
            return item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText();
        }
        var label = ReadJsonString(item, "label",
            ReadJsonString(item, "title",
                ReadJsonString(item, "task_id",
                    ReadJsonString(item, "product_id",
                        ReadJsonString(item, "id", "item")))));
        var status = WorkflowDisplayText.StatusLabel(ReadJsonString(item, "status", "pending"));
        return $"{label}:{status}";
    }

    internal string[] ReadAgentSkillMap(string agentId)
    {
        if (string.IsNullOrWhiteSpace(agentId)
            || _lastWorkflowSnapshot.ValueKind != JsonValueKind.Object
            || !TryReadJsonObject(_lastWorkflowSnapshot, "agent_skill_map", out var map)
            || !map.TryGetProperty(agentId, out var skills)
            || skills.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return skills.EnumerateArray()
            .Select(item => $"{ReadJsonString(item, "name")}:{ReadJsonString(item, "status", "--")}")
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

}
