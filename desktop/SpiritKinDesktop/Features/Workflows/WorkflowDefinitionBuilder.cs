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
    internal bool ValidateWorkflowDefinitionEditor(bool showSuccess)
    {
        var ok = TryBuildWorkflowDefinitionPayload(out _, out var error);
        if (ok)
        {
            if (showSuccess)
            {
                WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = $"定义校验通过 · 节点 {_workflowEditNodes.Count} · 可保存并启动运行。";
                WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "工作流定义校验通过。";
            }
            return true;
        }
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = error;
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = error;
        return false;
    }

    internal void AutoLayoutWorkflowEditNodes()
    {
        if (!ValidateWorkflowDefinitionEditor(showSuccess: false))
        {
            return;
        }
        PushWorkflowUndoSnapshot("auto layout");
        var layerByNode = ComputeWorkflowNodeLayers(_workflowEditNodes);
        var laneRowByLayer = new Dictionary<string, Dictionary<int, int>>(StringComparer.OrdinalIgnoreCase);
        var selectedNodeId = ActiveWorkflowEditNodeId();
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            var editNode = _workflowEditNodes[i];
            var layer = layerByNode.TryGetValue(editNode.NodeId, out var resolvedLayer) ? resolvedLayer : 0;
            var laneKey = LaneKeyForNodeType(editNode.NodeType);
            if (!laneRowByLayer.TryGetValue(laneKey, out var rowByLayer))
            {
                rowByLayer = new Dictionary<int, int>();
                laneRowByLayer[laneKey] = rowByLayer;
            }
            rowByLayer.TryGetValue(layer, out var row);
            rowByLayer[layer] = row + 1;
            _workflowEditNodes[i] = editNode.WithPosition(24 + layer * WorkflowNodeHorizontalGap, WorkflowLaneLayout.ClampNodeYToLane(editNode.NodeType, LaneDefaultY(editNode.NodeType) + row * 18, force: true));
        }
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = SelectExistingId(selectedNodeId, _workflowEditNodes.Select(item => item.NodeId));
        if (WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem is WorkflowEditNodeViewModel selectedNode)
        {
            RenderWorkflowNodeEditor(selectedNode);
        }
        RefreshWorkflowDefinitionPreviewFromEditor(selectedNodeId);
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = "已按依赖层级和节点类型泳道自动排布节点。";
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "节点蓝图已重新排布；纵向位置由节点类型泳道约束。";
    }

    internal static Dictionary<string, int> ComputeWorkflowNodeLayers(IEnumerable<WorkflowEditNodeViewModel> nodes)
    {
        var byId = nodes.ToDictionary(node => node.NodeId, StringComparer.OrdinalIgnoreCase);
        var layerByNode = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        int Resolve(WorkflowEditNodeViewModel node)
        {
            if (layerByNode.TryGetValue(node.NodeId, out var layer))
            {
                return layer;
            }
            var dependencyLayers = node.DependsOn
                .Where(byId.ContainsKey)
                .Select(dep => Resolve(byId[dep]))
                .DefaultIfEmpty(-1);
            layer = dependencyLayers.Max() + 1;
            layerByNode[node.NodeId] = layer;
            return layer;
        }
        foreach (var node in byId.Values)
        {
            Resolve(node);
        }
        return layerByNode;
    }

    internal void RefreshWorkflowDefinitionPreviewFromEditor(string? selectedNodeId)
    {
        if (!TryBuildWorkflowDefinitionPayload(out var definition, out var error))
        {
            WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = error;
            return;
        }
        var workflowName = Convert.ToString(definition["name"]) ?? WorkbenchShell.ManagementPanels.WorkflowNameBox.Text.Trim();
        if (!string.Equals(workflowName, _activeWorkflowName, StringComparison.OrdinalIgnoreCase))
        {
            _activeWorkflowName = workflowName;
            _activeWorkflowRunId = "";
            _workflowRunNodes.Clear();
            _syncingWorkflowRunSelection = true;
            _syncingWorkflowRunNodeSelection = true;
            try
            {
                WorkbenchShell.ManagementPanels.WorkflowRunsList.SelectedValue = null;
                WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedValue = null;
            }
            finally
            {
                _syncingWorkflowRunSelection = false;
                _syncingWorkflowRunNodeSelection = false;
            }
            WorkbenchShell.ManagementPanels.WorkflowRunSummaryText.Text = "当前定义草稿尚未启动运行。";
        }
        using var doc = JsonDocument.Parse(JsonSerializer.Serialize(definition, _jsonOptions));
        _activeWorkflowDefinition = doc.RootElement.Clone();
        _workflowDefinitionNodes.Clear();
        foreach (var node in _workflowEditNodes)
        {
            _workflowDefinitionNodes.Add(node.ToWorkflowNodeViewModel());
        }
        var displayName = Convert.ToString(((Dictionary<string, object?>)definition["metadata"]!)["display_name"]) ?? WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Text.Trim();
        WorkbenchShell.ManagementPanels.WorkflowBlueprintSummaryText.Text = $"{WorkflowDisplayText.WorkflowName(workflowName, displayName)} · 节点 {_workflowEditNodes.Count} · {WorkflowDisplayText.CategoryLabel(WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Text.Trim())} · {WorkflowDisplayText.TechnicalLine(workflowName)}";
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), selectedNodeId);
    }

    internal bool TryBuildWorkflowDefinitionPayload(out Dictionary<string, object?> definition, out string error)
    {
        definition = new Dictionary<string, object?>();
        error = "";
        var workflowName = WorkbenchShell.ManagementPanels.WorkflowNameBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(workflowName))
        {
            error = "工作流定义缺少 workflow_name。";
            return false;
        }
        var nodeIds = _workflowEditNodes.Select(item => item.NodeId).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray();
        if (nodeIds.Length == 0)
        {
            error = "工作流定义至少需要一个节点。";
            return false;
        }
        if (nodeIds.Length != nodeIds.Distinct(StringComparer.OrdinalIgnoreCase).Count())
        {
            error = "工作流节点 ID 存在重复。";
            return false;
        }
        var nodeSet = nodeIds.ToHashSet(StringComparer.OrdinalIgnoreCase);
        if (TryFindWorkflowCycle(_workflowEditNodes, out var cycle))
        {
            error = $"工作流依赖存在循环：{cycle}";
            return false;
        }
        var editNodeById = _workflowEditNodes.ToDictionary(node => node.NodeId, StringComparer.OrdinalIgnoreCase);
        var nodes = new List<Dictionary<string, object?>>();
        var runtimeParameterNames = new List<string>();
        foreach (var node in _workflowEditNodes)
        {
            var dependsOn = node.DependsOn;
            var missing = dependsOn.FirstOrDefault(dep => !nodeSet.Contains(dep));
            if (!string.IsNullOrWhiteSpace(missing))
            {
                error = $"节点 {node.NodeId} 依赖不存在：{missing}";
                return false;
            }
            foreach (var dependency in dependsOn)
            {
                if (!editNodeById.TryGetValue(dependency, out var source))
                {
                    continue;
                }
                if (!WorkflowConnectionRules.ArePortsCompatible(source.OutputPortKind, node.InputPortKind, source.NodeType, node.NodeType))
                {
                    error = $"连线不兼容：{source.NodeId} -> {node.NodeId}。{WorkflowConnectionRules.IncompatibilityReason(source.NodeType, node.NodeType, source.OutputPortKind, node.InputPortKind)}";
                    return false;
                }
            }
            if (!TryParseJsonObject(node.ArgumentsJson, out var arguments, out error))
            {
                error = $"节点 {node.NodeId} arguments JSON 错误：{error}";
                return false;
            }
            CollectWorkflowRuntimeParameterNames(arguments, runtimeParameterNames);
            var nodeType = string.IsNullOrWhiteSpace(node.NodeType) ? "agent_task" : node.NodeType;
            var inputPortKind = WorkflowConnectionRules.NormalizePortKind(node.InputPortKind);
            var outputPortKind = WorkflowConnectionRules.NormalizePortKind(node.OutputPortKind);
            if (nodeType == "tool_call" && string.IsNullOrWhiteSpace(node.ToolName))
            {
                error = $"节点 {node.NodeId} 是 tool_call，但缺少 tool_name。";
                return false;
            }
            if (nodeType == "skill_call" && string.IsNullOrWhiteSpace(node.SkillName))
            {
                error = $"节点 {node.NodeId} 是 skill_call，但缺少 skill_name。";
                return false;
            }
            if (nodeType == "waiter" && !JsonObjectHasValue(arguments, "wait_for") && !JsonObjectHasValue(arguments, "signal"))
            {
                error = $"节点 {node.NodeId} 是 waiter，但缺少 wait_for 或 signal。";
                return false;
            }
            if (nodeType == "external_callback" && !JsonObjectHasValue(arguments, "callback_id") && !JsonObjectHasValue(arguments, "callback_url"))
            {
                error = $"节点 {node.NodeId} 是 external_callback，但缺少 callback_id 或 callback_url。";
                return false;
            }
            if (nodeType == "subgraph" && !JsonObjectHasValue(arguments, "workflow_name"))
            {
                error = $"节点 {node.NodeId} 是 subgraph，但缺少 workflow_name。";
                return false;
            }
            nodes.Add(new Dictionary<string, object?>
            {
                ["node_id"] = node.NodeId,
                ["node_type"] = nodeType,
                ["label"] = node.Title,
                ["tool_name"] = node.ToolName,
                ["skill_name"] = node.SkillName,
                ["assigned_agent"] = node.AssignedAgent,
                ["arguments"] = arguments,
                ["depends_on"] = dependsOn,
                ["review_gate"] = node.ReviewGate,
                ["metadata"] = new Dictionary<string, object?>
                {
                    ["position"] = new Dictionary<string, object?>
                    {
                        ["x"] = node.X,
                        ["y"] = node.Y,
                        ["lane"] = LaneKeyForNodeType(nodeType),
                    },
                    ["ports"] = new[]
                    {
                        new Dictionary<string, object?>
                        {
                            ["id"] = "exec_in",
                            ["direction"] = "input",
                            ["kind"] = inputPortKind,
                            ["label"] = "In",
                            ["color"] = WorkflowPortKindColorHex(inputPortKind, WorkflowNodeTypeColorHex(nodeType)),
                        },
                        new Dictionary<string, object?>
                        {
                            ["id"] = "exec_out",
                            ["direction"] = "output",
                            ["kind"] = outputPortKind,
                            ["label"] = "Out",
                            ["color"] = WorkflowPortKindColorHex(outputPortKind, WorkflowNodeTypeColorHex(nodeType)),
                        },
                    },
                    ["connection_policy"] = new Dictionary<string, object?>
                    {
                        ["input_accepts"] = inputPortKind,
                        ["output_emits"] = outputPortKind,
                        ["type_label"] = WorkflowConnectionRules.TypeLabel(nodeType),
                    },
                    ["edge_notes"] = BuildWorkflowEdgeNotesMetadata(node.NodeId, dependsOn),
                },
            });
        }
        definition = new Dictionary<string, object?>
        {
            ["name"] = workflowName,
            ["version"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowVersionBox.Text) ? "0.1.0" : WorkbenchShell.ManagementPanels.WorkflowVersionBox.Text.Trim(),
            ["description"] = WorkbenchShell.ManagementPanels.WorkflowDescriptionBox.Text.Trim(),
            ["nodes"] = nodes,
            ["metadata"] = new Dictionary<string, object?>
            {
                ["blueprint_ready"] = true,
                ["display_name"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Text) ? workflowName : WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Text.Trim(),
                ["category"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Text) ? "自定义" : WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Text.Trim(),
                ["domain"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Text) ? "custom" : WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Text.Trim(),
                ["status"] = "candidate",
                ["parameters"] = MergeWorkflowParameterDefinitions(ReadWorkflowParameterDefinitionsFromActiveDefinition(), runtimeParameterNames),
            },
        };
        return true;
    }

}
