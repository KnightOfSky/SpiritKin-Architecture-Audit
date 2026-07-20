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
    internal Dictionary<string, object?> BuildWorkflowRunPayload(bool dryRun)
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["project_root"] = _rootDir,
            ["dry_run"] = dryRun,
        };
    }

    internal Dictionary<string, object?> BuildWorkflowNodePayload(bool dryRun)
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["node_id"] = ActiveWorkflowNodeId(),
            ["project_root"] = _rootDir,
            ["dry_run"] = dryRun,
        };
    }

    internal Dictionary<string, object?> BuildWorkflowAgentNodePayload()
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["node_id"] = ActiveWorkflowNodeId(),
            ["project_root"] = _rootDir,
            ["agent_id"] = WorkflowAgentId(),
        };
    }

    internal Dictionary<string, object?> BuildWorkflowCompleteNodePayload(Dictionary<string, object?> outputs)
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["node_id"] = ActiveWorkflowNodeId(),
            ["project_root"] = _rootDir,
            ["agent_id"] = WorkflowAgentId(),
            ["outputs"] = outputs,
        };
    }

    internal Dictionary<string, object?> BuildWorkflowSignalNodePayload(Dictionary<string, object?> signalPayload)
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["node_id"] = ActiveWorkflowNodeId(),
            ["project_root"] = _rootDir,
            ["actor"] = WorkflowAgentId(),
            ["signal_payload"] = signalPayload,
        };
    }

    internal async Task CompleteOrSignalWorkflowNodeAsync()
    {
        var nodeType = ActiveWorkflowNodeType();
        if (nodeType is "waiter" or "external_callback" or "subgraph"
            || (IsOpenWorkflowNodeType(nodeType) && !string.Equals(ActiveWorkflowNodeExecutor(), "agent_task", StringComparison.OrdinalIgnoreCase)))
        {
            var signalPayload = PromptWorkflowJsonObject("节点信号 JSON", "填写 signal_payload JSON。", DefaultWorkflowOutputJson());
            if (signalPayload is null)
            {
                return;
            }
            await WorkflowActionAsync("signal_node", BuildWorkflowSignalNodePayload(signalPayload));
            return;
        }
        var outputs = PromptWorkflowJsonObject("节点 outputs JSON", "填写 outputs JSON。", DefaultWorkflowOutputJson());
        if (outputs is null)
        {
            return;
        }
        await WorkflowActionAsync("complete_agent_task", BuildWorkflowCompleteNodePayload(outputs));
    }

    internal Dictionary<string, object?> BuildWorkflowRetryNodePayload()
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["node_id"] = ActiveWorkflowNodeId(),
            ["project_root"] = _rootDir,
            ["actor"] = "wpf_desktop",
        };
    }

    internal Dictionary<string, object?> BuildWorkflowResetRunPayload()
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["project_root"] = _rootDir,
            ["actor"] = "wpf_desktop",
        };
    }

    private Dictionary<string, object?>? PromptWorkflowJsonObject(string title, string label, string defaultJson)
    {
        var raw = PromptText(title, label, defaultJson);
        if (raw is null)
        {
            return null;
        }
        if (!TryParseJsonObject(raw, out var value, out var error))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"JSON 无效：{error}";
            return null;
        }
        return value;
    }

    private static string DefaultWorkflowOutputJson()
    {
        return JsonSerializer.Serialize(
            new Dictionary<string, object?>
            {
                ["submitted_from"] = "spiritkin_wpf_desktop",
                ["at"] = DateTimeOffset.UtcNow.ToString("O"),
            },
            new JsonSerializerOptions { WriteIndented = true });
    }

    internal Dictionary<string, object?> BuildWorkflowReviewNodePayload()
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["node_id"] = ActiveWorkflowNodeId(),
            ["project_root"] = _rootDir,
            ["reviewer"] = WorkflowAgentId(),
        };
    }

    internal Dictionary<string, object?> BuildWorkflowRunManagementPayload(int keepRecent = 30)
    {
        return new Dictionary<string, object?>
        {
            ["run_id"] = ActiveWorkflowRunId(),
            ["workflow_name"] = ActiveWorkflowName(),
            ["project_root"] = _rootDir,
            ["actor"] = "wpf_desktop",
            ["keep_recent"] = keepRecent,
            ["include_archived"] = true,
        };
    }

    internal string WorkflowAgentId()
    {
        var value = ComboText(WorkbenchShell.ManagementPanels.WorkflowAgentSelectBox).Trim();
        return string.IsNullOrWhiteSpace(value) ? "ecommerce" : value;
    }

    internal string ActiveWorkflowName()
    {
        if (!string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowNameBox.Text))
        {
            return WorkbenchShell.ManagementPanels.WorkflowNameBox.Text.Trim();
        }
        var selected = WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList.SelectedValue as string;
        if (!string.IsNullOrWhiteSpace(selected))
        {
            return selected.Trim();
        }
        return string.IsNullOrWhiteSpace(_activeWorkflowName) ? "ecommerce.auto_listing.v1" : _activeWorkflowName;
    }

    internal string ActiveWorkflowRunId() => (WorkbenchShell.ManagementPanels.WorkflowRunsList.SelectedValue as string) ?? _activeWorkflowRunId;

    internal JsonElement ActiveWorkflowGraphDefinition() => _activeWorkflowDefinition;

    internal string ActiveWorkflowNodeId() => (WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedValue as string)
        ?? _workflowRunNodes.FirstOrDefault(item => item.Runnable)?.NodeId
        ?? _workflowRunNodes.FirstOrDefault()?.NodeId
        ?? "";

    internal string ActiveWorkflowNodeType()
    {
        if (TryFindActiveWorkflowDefinitionNode(out var activeNode))
        {
            return ReadJsonString(activeNode, "node_type", "agent_task");
        }
        return "agent_task";
    }

    internal string ActiveWorkflowNodeExecutor()
    {
        if (!TryFindActiveWorkflowDefinitionNode(out var activeNode)
            || !TryReadJsonObject(activeNode, "arguments", out var arguments))
        {
            return "";
        }
        return ReadJsonString(arguments, "executor");
    }

    internal bool TryFindActiveWorkflowDefinitionNode(out JsonElement activeNode)
    {
        activeNode = default;
        var nodeId = ActiveWorkflowNodeId();
        var definition = ActiveWorkflowGraphDefinition();
        if (definition.ValueKind == JsonValueKind.Object
            && definition.TryGetProperty("nodes", out var nodes)
            && nodes.ValueKind == JsonValueKind.Array)
        {
            foreach (var node in nodes.EnumerateArray())
            {
                if (string.Equals(ReadJsonString(node, "node_id"), nodeId, StringComparison.OrdinalIgnoreCase))
                {
                    activeNode = node.Clone();
                    return true;
                }
            }
        }
        return false;
    }

    internal async void WorkflowDefinitionCatalogList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingWorkflowSelection)
        {
            return;
        }
        var workflowName = WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList.SelectedValue as string;
        if (string.IsNullOrWhiteSpace(workflowName))
        {
            return;
        }
        WorkbenchShell.ManagementPanels.WorkflowNameBox.Text = workflowName;
        _activeWorkflowRunId = "";
        _resetWorkflowGraphViewportOnNextRender = true;
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已选择工作流定义：{workflowName}";
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/workflows", new Dictionary<string, object?>
            {
                ["action"] = "snapshot",
                ["workflow_name"] = workflowName,
                ["project_root"] = _rootDir,
            });
            RenderWorkflows(doc.RootElement.GetProperty("workflows"));
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"切换工作流失败：{ex.Message}";
        }
    }

    internal void WorkflowRunsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingWorkflowRunSelection)
        {
            return;
        }
        _activeWorkflowRunId = ActiveWorkflowRunId();
        RenderSelectedWorkflowRun();
    }

    internal void WorkflowRunNodesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingWorkflowRunNodeSelection)
        {
            return;
        }
        if (WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedItem is WorkflowNodeViewModel node)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"{node.Title}{Environment.NewLine}{node.Detail}".Trim();
            WorkbenchShell.ManagementPanels.WorkflowSelectedNodeText.Text = $"{node.NodeId} · {node.Status}";
            SelectWorkflowEditNode(node.NodeId);
            var run = FindWorkflowRun(_activeWorkflowRunId);
            var graphDefinition = ActiveWorkflowGraphDefinition();
            RenderWorkflowGraph(graphDefinition, run, node.NodeId);
            RenderWorkflowNodeDetail(run, graphDefinition, node.NodeId);
            RenderWorkflowInspectorNodeSummary(node.NodeId);
        }
    }
}
