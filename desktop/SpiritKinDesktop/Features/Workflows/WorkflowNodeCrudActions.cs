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
    internal void AddWorkflowEditNode()
    {
        PushWorkflowUndoSnapshot("add node");
        var index = _workflowEditNodes.Count + 1;
        var nodeId = UniqueWorkflowNodeId($"node_{index}");
        var node = new WorkflowEditNodeViewModel(
            nodeId,
            $"节点 {index}",
            "agent_task",
            WorkflowAgentId(),
            _workflowEditNodes.LastOrDefault()?.NodeId ?? "",
            "",
            "",
            "",
            "{}",
            24 + ((_workflowEditNodes.Count) % 4) * WorkflowNodeHorizontalGap,
            LaneDefaultY("agent_task"));
        _workflowEditNodes.Add(node);
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = node.NodeId;
        RenderWorkflowNodeEditor(node);
        RefreshWorkflowDefinitionPreviewFromEditor(node.NodeId);
    }

    internal WorkflowEditNodeViewModel? AddWorkflowEditNodeFromCanvas(string nodeType, string? dependencyNodeId, Point? preferredPoint)
    {
        var normalizedType = string.IsNullOrWhiteSpace(nodeType) ? "agent_task" : nodeType.Trim();
        var dependency = string.IsNullOrWhiteSpace(dependencyNodeId) ? "" : dependencyNodeId.Trim();
        var source = _workflowEditNodes.FirstOrDefault(node => string.Equals(node.NodeId, dependency, StringComparison.OrdinalIgnoreCase));
        var inputPortKind = WorkflowConnectionRules.DefaultInputPortKindForNodeType(normalizedType);
        var outputPortKind = WorkflowConnectionRules.DefaultOutputPortKindForNodeType(normalizedType);
        if (source is not null && !WorkflowConnectionRules.ArePortsCompatible(source.OutputPortKind, inputPortKind, source.NodeType, normalizedType))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = WorkflowConnectionRules.IncompatibilityReason(source.NodeType, normalizedType, source.OutputPortKind, inputPortKind);
            return null;
        }
        var nodeId = UniqueWorkflowNodeId(DefaultWorkflowNodeIdBase(normalizedType));
        var x = source is not null
            ? source.X + WorkflowNodeHorizontalGap
            : Math.Max(0, Math.Round(preferredPoint?.X ?? 24));
        var rawY = source is not null
            ? LaneDefaultY(normalizedType)
            : Math.Round(preferredPoint?.Y ?? LaneDefaultY(normalizedType));
        var node = new WorkflowEditNodeViewModel(
            nodeId,
            DefaultWorkflowNodeLabel(normalizedType),
            normalizedType,
            normalizedType == "agent_task" ? WorkflowAgentId() : "",
            dependency,
            normalizedType == "tool_call" ? "" : "",
            normalizedType == "skill_call" ? "" : "",
            normalizedType == "review_gate" ? "core_review" : "",
            DefaultWorkflowNodeArguments(normalizedType),
            x,
            ClampWorkflowNodeYToLane(normalizedType, rawY),
            inputPortKind,
            outputPortKind);
        PushWorkflowUndoSnapshot(string.IsNullOrWhiteSpace(dependency) ? "add root node" : "add child node");
        _workflowEditNodes.Add(node);
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = node.NodeId;
        SelectSingleWorkflowGraphNode(node.NodeId);
        RenderWorkflowNodeEditor(node);
        RefreshWorkflowDefinitionPreviewFromEditor(node.NodeId);
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = normalizedType is "tool_call" or "skill_call"
            ? "已新增节点；保存前需要填写 tool_name 或 skill_name。"
            : "已新增节点；应用右侧属性后保存定义才会影响新运行。";
        return node;
    }

    internal void DuplicateWorkflowEditNode(string nodeId)
    {
        var source = _workflowEditNodes.FirstOrDefault(node => string.Equals(node.NodeId, nodeId, StringComparison.OrdinalIgnoreCase));
        if (source is null)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"未找到节点：{nodeId}";
            return;
        }
        PushWorkflowUndoSnapshot("duplicate node");
        var copyId = UniqueWorkflowNodeId($"{source.NodeId}_copy");
        var copy = new WorkflowEditNodeViewModel(
            copyId,
            $"{source.Title} Copy",
            source.NodeType,
            source.AssignedAgent,
            source.DependsOnText,
            source.ToolName,
            source.SkillName,
            source.ReviewGate,
            source.ArgumentsJson,
            source.X + 34,
            ClampWorkflowNodeYToLane(source.NodeType, source.Y + 22),
            source.InputPortKind,
            source.OutputPortKind);
        _workflowEditNodes.Add(copy);
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = copy.NodeId;
        SelectSingleWorkflowGraphNode(copy.NodeId);
        RenderWorkflowNodeEditor(copy);
        RefreshWorkflowDefinitionPreviewFromEditor(copy.NodeId);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已复制节点：{source.NodeId} -> {copy.NodeId}";
    }

    internal void ApplyWorkflowNodeEditor()
    {
        var nodeId = WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(nodeId))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "节点缺少 node_id。";
            return;
        }
        if (!TryParseWorkflowNodeArguments(out var argumentsJson, out var error))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = error;
            return;
        }
        var existing = WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem as WorkflowEditNodeViewModel;
        var oldNodeId = existing?.NodeId ?? "";
        if (_workflowEditNodes.Any(item => !ReferenceEquals(item, existing) && string.Equals(item.NodeId, nodeId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"节点 ID 已存在：{nodeId}";
            return;
        }
        PushWorkflowUndoSnapshot("apply node");
        var nodeType = ComboText(WorkbenchShell.ManagementPanels.WorkflowNodeTypeBox).Trim();
        var fallbackX = existing?.X ?? 24 + (_workflowEditNodes.Count % 4) * WorkflowNodeHorizontalGap;
        var fallbackY = existing?.Y ?? LaneDefaultY(nodeType);
        var inputPortKind = WorkflowConnectionRules.NormalizePortKind(ComboText(WorkbenchShell.ManagementPanels.WorkflowNodeInputPortKindBox));
        var outputPortKind = WorkflowConnectionRules.NormalizePortKind(ComboText(WorkbenchShell.ManagementPanels.WorkflowNodeOutputPortKindBox));
        var node = new WorkflowEditNodeViewModel(
            nodeId,
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowNodeLabelBox.Text) ? nodeId : WorkbenchShell.ManagementPanels.WorkflowNodeLabelBox.Text.Trim(),
            nodeType,
            ComboText(WorkbenchShell.ManagementPanels.WorkflowNodeAgentBox).Trim(),
            string.Join(Environment.NewLine, SplitLooseList(WorkbenchShell.ManagementPanels.WorkflowNodeDependsOnBox.Text)),
            WorkbenchShell.ManagementPanels.WorkflowNodeToolNameBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.WorkflowNodeSkillNameBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.WorkflowNodeReviewGateBox.Text.Trim(),
            argumentsJson,
            ReadWorkflowNodeCoordinate(WorkbenchShell.ManagementPanels.WorkflowNodeXBox.Text, fallbackX),
            ClampWorkflowNodeYToLane(nodeType, ReadWorkflowNodeCoordinate(WorkbenchShell.ManagementPanels.WorkflowNodeYBox.Text, fallbackY)),
            string.IsNullOrWhiteSpace(inputPortKind) ? WorkflowConnectionRules.DefaultInputPortKindForNodeType(nodeType) : inputPortKind,
            string.IsNullOrWhiteSpace(outputPortKind) ? WorkflowConnectionRules.DefaultOutputPortKindForNodeType(nodeType) : outputPortKind);
        if (existing is null)
        {
            _workflowEditNodes.Add(node);
        }
        else
        {
            var selectedIndex = WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedIndex;
            _workflowEditNodes[selectedIndex] = node;
            if (!string.Equals(oldNodeId, nodeId, StringComparison.OrdinalIgnoreCase))
            {
                for (var i = 0; i < _workflowEditNodes.Count; i++)
                {
                    _workflowEditNodes[i] = _workflowEditNodes[i].ReplaceDependency(oldNodeId, nodeId);
                }
            }
        }
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = node.NodeId;
        SelectSingleWorkflowGraphNode(node.NodeId);
        RenderWorkflowNodeEditor(node);
        RefreshWorkflowDefinitionPreviewFromEditor(node.NodeId);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"节点已应用：{node.NodeId}";
    }

    internal void DeleteWorkflowEditNode()
    {
        DeleteWorkflowEditNode((WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem as WorkflowEditNodeViewModel)?.NodeId);
    }

    internal void DeleteWorkflowEditNode(string? nodeId)
    {
        var node = _workflowEditNodes.FirstOrDefault(item => string.Equals(item.NodeId, nodeId, StringComparison.OrdinalIgnoreCase));
        if (node is null)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请先选择要删除的节点。";
            return;
        }
        if (!ConfirmDestructiveAction("删除工作流节点", $"确定要删除节点“{node.NodeId}”吗？依赖它的节点会移除对应依赖。"))
        {
            return;
        }
        PushWorkflowUndoSnapshot("delete node");
        _workflowEditNodes.Remove(node);
        _selectedWorkflowNodeIds.Remove(node.NodeId);
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            _workflowEditNodes[i] = _workflowEditNodes[i].WithoutDependency(node.NodeId);
        }
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = _workflowEditNodes.FirstOrDefault()?.NodeId;
        RefreshWorkflowDefinitionPreviewFromEditor(WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue as string);
    }

}
