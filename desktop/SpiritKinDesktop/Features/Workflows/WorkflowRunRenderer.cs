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
    internal void RenderSelectedWorkflowRun(string? selectedNodeId = null)
    {
        _workflowRunNodes.Clear();
        var run = FindWorkflowRun(_activeWorkflowRunId);
        if (run.ValueKind != JsonValueKind.Object)
        {
            WorkbenchShell.ManagementPanels.WorkflowRunSummaryText.Text = "暂无运行实例；点击“启动运行”。";
            WorkbenchShell.ManagementPanels.WorkflowNodeDetailText.Text = "启动运行后可查看节点定义、状态、输入输出和事件。";
            WorkbenchShell.ManagementPanels.WorkflowNodeOpsText.Text = "暂无运行实例。保存定义并启动后，会显示该节点任务队列、进度、可用 Skill 和修复建议。";
            RenderWorkflowTaskProgress(default, "pending");
            RenderWorkflowInspectorNodeSummary(selectedNodeId);
            RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), default, selectedNodeId);
            RenderTracePanel();
            return;
        }

        var runId = ReadJsonString(run, "run_id");
        var runnable = ReadJsonStringArray(run, "runnable_node_ids").ToHashSet(StringComparer.OrdinalIgnoreCase);
        var nodeStates = new Dictionary<string, JsonElement>(StringComparer.OrdinalIgnoreCase);
        if (run.TryGetProperty("nodes", out var runNodes) && runNodes.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in runNodes.EnumerateObject())
            {
                nodeStates[property.Name] = property.Value;
            }
        }
        var listDefinition = ActiveWorkflowGraphDefinition();
        if (listDefinition.ValueKind == JsonValueKind.Object && listDefinition.TryGetProperty("nodes", out var definitionNodes) && definitionNodes.ValueKind == JsonValueKind.Array)
        {
            foreach (var node in definitionNodes.EnumerateArray())
            {
                var nodeId = ReadJsonString(node, "node_id");
                nodeStates.TryGetValue(nodeId, out var state);
                _workflowRunNodes.Add(WorkflowNodeViewModel.ForRun(node, state, runnable.Contains(nodeId)));
            }
        }

        var nextNodeId = SelectExistingId(selectedNodeId, _workflowRunNodes.Select(item => item.NodeId))
            ?? _workflowRunNodes.FirstOrDefault(item => item.Runnable)?.NodeId
            ?? _workflowRunNodes.FirstOrDefault()?.NodeId;
        _syncingWorkflowRunNodeSelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedValue = nextNodeId;
        }
        finally
        {
            _syncingWorkflowRunNodeSelection = false;
        }
        var activeNodeId = WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedValue as string;
        SelectWorkflowEditNode(activeNodeId);
        var graphDefinition = ActiveWorkflowGraphDefinition();
        RenderWorkflowGraph(graphDefinition, run, activeNodeId);
        var runWorkflowName = ReadJsonString(run, "workflow_name", ActiveWorkflowName());
        WorkbenchShell.ManagementPanels.WorkflowRunSummaryText.Text = $"{WorkflowDisplayText.WorkflowName(runWorkflowName)} · {WorkflowDisplayText.StatusLabel(ReadJsonString(run, "status", "--"))} · 运行 {WorkflowDisplayText.ShortId(runId)} · 可执行 {runnable.Count}";
        RenderWorkflowNodeDetail(run, graphDefinition, activeNodeId ?? "");
        RenderWorkflowInspectorNodeSummary(activeNodeId);
        RenderTracePanel();
    }

    internal void RenderWorkflowNodeDetail(JsonElement run, JsonElement definition, string nodeId)
    {
        if (string.IsNullOrWhiteSpace(nodeId))
        {
            WorkbenchShell.ManagementPanels.WorkflowNodeDetailText.Text = "未选择节点。";
            WorkbenchShell.ManagementPanels.WorkflowNodeOpsText.Text = "未选择节点。";
            RenderWorkflowTaskProgress(default, "pending");
            return;
        }

        JsonElement definitionNode = default;
        if (definition.ValueKind == JsonValueKind.Object
            && definition.TryGetProperty("nodes", out var nodes)
            && nodes.ValueKind == JsonValueKind.Array)
        {
            definitionNode = nodes.EnumerateArray().FirstOrDefault(node => string.Equals(ReadJsonString(node, "node_id"), nodeId, StringComparison.OrdinalIgnoreCase));
        }

        JsonElement nodeState = default;
        if (TryReadJsonObject(run, "nodes", out var runNodes) && runNodes.TryGetProperty(nodeId, out var state))
        {
            nodeState = state;
        }

        JsonElement detail = default;
        if (TryReadJsonObject(run, "selected_node_details", out var detailMap) && detailMap.TryGetProperty(nodeId, out var detailElement))
        {
            detail = detailElement;
        }

        var dependencies = TryReadJsonArray(detail, "dependencies")
            ? string.Join(", ", detail.GetProperty("dependencies").EnumerateArray().Select(item => $"{ReadJsonString(item, "node_id")}:{WorkflowDisplayText.StatusLabel(ReadJsonString(item, "status", "--"))}"))
            : definitionNode.ValueKind == JsonValueKind.Object
                ? string.Join(", ", ReadJsonStringArray(definitionNode, "depends_on"))
                : "";
        if (string.IsNullOrWhiteSpace(dependencies))
        {
            dependencies = "--";
        }

        var effectiveAgent = ReadSafeJsonString(detail, "effective_agent",
            ReadSafeJsonString(nodeState, "assigned_agent", ReadSafeJsonString(definitionNode, "assigned_agent", "--")));

        var builder = new StringBuilder();
        builder.AppendLine($"{nodeId} · {WorkflowDisplayText.StatusLabel(ReadSafeJsonString(nodeState, "status", "pending"))} · {WorkflowDisplayText.NodeTypeLabel(ReadSafeJsonString(definitionNode, "node_type", "--"))}");
        builder.AppendLine($"名称：{ReadSafeJsonString(definitionNode, "label", nodeId)}");
        builder.AppendLine($"执行 Agent：{WorkflowDisplayText.ActorLabel(effectiveAgent)}");
        builder.AppendLine($"依赖：{dependencies}");
        builder.AppendLine($"尝试次数：{ReadSafeJsonInt(nodeState, "attempts")}");
        builder.AppendLine($"接口/端口约束：{WorkflowContractDetailLine(detail, definitionNode)}");
        builder.AppendLine($"队列摘要：{WorkflowNodeQueueSummaryLine(detail)}");
        builder.AppendLine($"开始时间：{ReadSafeJsonString(nodeState, "started_at", "--")}");
        builder.AppendLine($"结束时间：{ReadSafeJsonString(nodeState, "finished_at", "--")}");
        var error = ReadSafeJsonString(nodeState, "error");
        if (!string.IsNullOrWhiteSpace(error))
        {
            builder.AppendLine($"错误：{error}");
        }
        RenderWorkflowTaskProgress(detail, ReadSafeJsonString(nodeState, "status", "pending"));
        RenderWorkflowNodeOpsSummary(detail, definitionNode, nodeState, effectiveAgent);
        builder.AppendLine();
        builder.AppendLine("[节点定义]");
        builder.AppendLine(definitionNode.ValueKind == JsonValueKind.Object ? FormatJson(definitionNode) : "--");
        builder.AppendLine();
        builder.AppendLine("[状态/输出]");
        builder.AppendLine(nodeState.ValueKind == JsonValueKind.Object ? FormatJson(nodeState) : "--");
        builder.AppendLine();
        builder.AppendLine("[事件]");
        if (TryReadJsonArray(detail, "events"))
        {
            builder.AppendLine(FormatJson(detail.GetProperty("events")));
        }
        else
        {
            builder.AppendLine("--");
        }
        builder.AppendLine();
        builder.AppendLine("[交互包]");
        if (TryReadJsonArray(detail, "interaction_envelopes"))
        {
            builder.AppendLine(FormatJson(detail.GetProperty("interaction_envelopes")));
        }
        else
        {
            builder.AppendLine("--");
        }
        WorkbenchShell.ManagementPanels.WorkflowNodeDetailText.Text = builder.ToString().TrimEnd();
    }

    internal void RenderWorkflowNodeOpsSummary(JsonElement detail, JsonElement definitionNode, JsonElement nodeState, string effectiveAgent)
    {
        var nodeType = WorkflowDisplayText.NodeTypeLabel(ReadSafeJsonString(definitionNode, "node_type", "--"));
        var status = WorkflowDisplayText.StatusLabel(ReadSafeJsonString(nodeState, "status", "pending"));
        var progressText = "--";
        if (TryReadJsonObject(detail, "progress", out var progress))
        {
            progressText = $"{ReadJsonDouble(progress, "percent"):0.#}%";
        }
        var skills = new List<string>();
        if (TryReadJsonArray(detail, "available_skills"))
        {
            foreach (var skill in detail.GetProperty("available_skills").EnumerateArray().Take(5))
            {
                skills.Add($"{ReadJsonString(skill, "name")}:{WorkflowDisplayText.StatusLabel(ReadJsonString(skill, "status", "--"))}");
            }
        }
        var nodeQueue = WorkflowNodeQueueLines(detail, 6);
        var agentQueue = WorkflowAgentQueueLines(detail, 5);
        var queue = nodeQueue.Length > 0
            ? string.Join(" | ", nodeQueue)
            : "--";
        var agentQueueText = agentQueue.Length > 0
            ? string.Join(" | ", agentQueue)
            : "--";
        var currentQueueItem = WorkflowNodeCurrentQueueLine(detail);
        var nextQueueItems = WorkflowNodeNextQueueLines(detail, 4);
        var nextQueueText = nextQueueItems.Length > 0 ? string.Join(" | ", nextQueueItems) : "--";
        var contractText = WorkflowContractDetailLine(detail, definitionNode);
        var repairs = new List<string>();
        if (TryReadJsonArray(detail, "repair_suggestions"))
        {
            foreach (var item in detail.GetProperty("repair_suggestions").EnumerateArray().Take(4))
            {
                repairs.Add($"{ReadJsonString(item, "title")} - {ReadJsonString(item, "detail")}");
            }
        }
        var builder = new StringBuilder();
        builder.AppendLine($"执行 Agent：{WorkflowDisplayText.ActorLabel(effectiveAgent)} · {nodeType} · {status} · 进度 {progressText}");
        builder.AppendLine($"输入 / 输出：{contractText}");
        builder.AppendLine($"当前处理项：{(string.IsNullOrWhiteSpace(currentQueueItem) ? "--" : currentQueueItem)}");
        builder.AppendLine($"后续队列：{nextQueueText}");
        builder.AppendLine($"可用 Skill：{(skills.Count == 0 ? "--" : string.Join(", ", skills))}");
        builder.AppendLine($"节点任务队列：{queue}");
        builder.AppendLine($"Agent 节点队列：{agentQueueText}");
        builder.AppendLine($"修复建议：{(repairs.Count == 0 ? "暂无自动建议；查看详情和事件。" : string.Join(Environment.NewLine + "  ", repairs))}");
        WorkbenchShell.ManagementPanels.WorkflowNodeOpsText.Text = builder.ToString().TrimEnd();
    }

    internal void RenderWorkflowTaskProgress(JsonElement detail, string nodeStatus)
    {
        _workflowTaskProgress.Clear();
        if (detail.ValueKind != JsonValueKind.Object
            || !detail.TryGetProperty("node_task_queue", out var queue)
            || queue.ValueKind != JsonValueKind.Array)
        {
            _workflowTaskProgress.Add(WorkflowTaskProgressViewModel.Empty(nodeStatus));
            return;
        }

        var index = 0;
        foreach (var item in queue.EnumerateArray())
        {
            _workflowTaskProgress.Add(WorkflowTaskProgressViewModel.FromJson(item, ++index, nodeStatus));
        }
        if (_workflowTaskProgress.Count == 0)
        {
            _workflowTaskProgress.Add(WorkflowTaskProgressViewModel.Empty(nodeStatus));
        }
    }

    internal JsonElement FindWorkflowRun(string runId)
    {
        if (string.IsNullOrWhiteSpace(runId))
        {
            return default;
        }
        if (_lastWorkflowSnapshot.ValueKind != JsonValueKind.Object
            || !_lastWorkflowSnapshot.TryGetProperty("runs", out var runs)
            || runs.ValueKind != JsonValueKind.Array)
        {
            return default;
        }
        foreach (var run in runs.EnumerateArray())
        {
            if (string.Equals(ReadJsonString(run, "run_id"), runId, StringComparison.OrdinalIgnoreCase)
                && string.Equals(ReadJsonString(run, "workflow_name"), _activeWorkflowName, StringComparison.OrdinalIgnoreCase))
            {
                return run;
            }
        }
        return default;
    }

    internal static string BuildWorkflowSummary(JsonElement workflows)
    {
        if (!TryReadJsonObject(workflows, "overview", out var overview))
        {
            return "工作流快照已加载。";
        }
        var defaultName = ReadJsonString(overview, "default_workflow_name", "ecommerce.auto_listing.v1");
        return $"默认 {WorkflowDisplayText.WorkflowName(defaultName)} · 定义 {ReadJsonInt(overview, "definition_count")} · 运行 {ReadJsonInt(overview, "run_count")} · active {ReadJsonInt(overview, "active_run_count")}";
    }

}
