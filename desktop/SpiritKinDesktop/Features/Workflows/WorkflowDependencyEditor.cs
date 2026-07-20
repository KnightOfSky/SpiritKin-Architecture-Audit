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
    internal void RenderWorkflowDependencyOptions(WorkflowEditNodeViewModel? selectedNode)
    {
        var selectedId = selectedNode?.NodeId ?? WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text.Trim();
        var selectedDependencies = selectedNode?.DependsOn ?? SplitLooseList(WorkbenchShell.ManagementPanels.WorkflowNodeDependsOnBox.Text);
        _workflowDependencyOptions.Clear();
        foreach (var node in _workflowEditNodes.Where(node => !string.Equals(node.NodeId, selectedId, StringComparison.OrdinalIgnoreCase)))
        {
            _workflowDependencyOptions.Add(new WorkflowDependencyOptionViewModel(node.NodeId, node.Title, $"{node.NodeId} · {node.NodeType}"));
        }
        SelectWorkflowDependencyOptions(selectedDependencies);
    }

    internal void SelectWorkflowDependencyOptions(IEnumerable<string> dependencyIds)
    {
        var dependencySet = dependencyIds.ToHashSet(StringComparer.OrdinalIgnoreCase);
        _syncingWorkflowDependencySelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowDependencyNodesList.SelectedItems.Clear();
            foreach (var option in _workflowDependencyOptions.Where(option => dependencySet.Contains(option.NodeId)))
            {
                WorkbenchShell.ManagementPanels.WorkflowDependencyNodesList.SelectedItems.Add(option);
            }
        }
        finally
        {
            _syncingWorkflowDependencySelection = false;
        }
    }

    internal void WorkflowDependencyNodesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingWorkflowDependencySelection)
        {
            return;
        }
        var count = WorkbenchShell.ManagementPanels.WorkflowDependencyNodesList.SelectedItems.Count;
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = count == 0
            ? "未选择依赖节点。"
            : $"已选择 {count} 个依赖节点；点击“加入依赖”生成连线。";
    }

    internal void WorkflowNodeTypeBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingWorkflowNodeEditor)
        {
            return;
        }
        var nodeType = ComboText(WorkbenchShell.ManagementPanels.WorkflowNodeTypeBox).Trim();
        if (string.IsNullOrWhiteSpace(nodeType))
        {
            nodeType = "agent_task";
        }
        SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeInputPortKindBox, WorkflowConnectionRules.DefaultInputPortKindForNodeType(nodeType));
        SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeOutputPortKindBox, WorkflowConnectionRules.DefaultOutputPortKindForNodeType(nodeType));
        WorkbenchShell.ManagementPanels.WorkflowNodeYBox.Text = ClampWorkflowNodeYToLane(
            nodeType,
            ReadWorkflowNodeCoordinate(WorkbenchShell.ManagementPanels.WorkflowNodeYBox.Text, LaneDefaultY(nodeType))).ToString("0");
        if (nodeType == "review_gate" && string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowNodeReviewGateBox.Text))
        {
            WorkbenchShell.ManagementPanels.WorkflowNodeReviewGateBox.Text = "core_review";
        }
        if (nodeType == "agent_task" && string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.WorkflowNodeAgentBox)))
        {
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeAgentBox, WorkflowAgentId());
        }
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = $"节点类型已切换为 {WorkflowConnectionRules.TypeLabel(nodeType)}；输入/输出端口已更新为默认接口约束。";
    }

    internal void AddSelectedWorkflowDependencies()
    {
        var currentNodeId = WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(currentNodeId))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请先选择或填写当前节点。";
            return;
        }
        var selected = WorkbenchShell.ManagementPanels.WorkflowDependencyNodesList.SelectedItems
            .OfType<WorkflowDependencyOptionViewModel>()
            .Select(item => item.NodeId)
            .Where(id => !string.Equals(id, currentNodeId, StringComparison.OrdinalIgnoreCase))
            .ToArray();
        string[] dependencies = SplitLooseList(WorkbenchShell.ManagementPanels.WorkflowNodeDependsOnBox.Text);
        foreach (var dependency in selected)
        {
            dependencies = dependencies
                .Concat(new[] { dependency })
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();
            ApplyWorkflowDependencyToNode(currentNodeId, dependency, add: true);
        }
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = dependencies.Length == 0 ? "当前节点没有依赖。" : $"当前节点依赖：{string.Join(" -> ", dependencies)}";
    }

    internal void RemoveSelectedWorkflowDependencies()
    {
        var selected = WorkbenchShell.ManagementPanels.WorkflowDependencyNodesList.SelectedItems
            .OfType<WorkflowDependencyOptionViewModel>()
            .Select(item => item.NodeId)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        if (selected.Count == 0)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请选择要移除的依赖。";
            return;
        }
        var dependencies = SplitLooseList(WorkbenchShell.ManagementPanels.WorkflowNodeDependsOnBox.Text)
            .Where(id => !selected.Contains(id))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        foreach (var dependency in selected)
        {
            ApplyWorkflowDependencyToNode(WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text.Trim(), dependency, add: false);
        }
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = dependencies.Length == 0 ? "当前节点没有依赖。" : $"当前节点依赖：{string.Join(" -> ", dependencies)}";
    }

    internal bool ApplyWorkflowDependencyToNode(string targetNodeId, string dependencyNodeId, bool add)
    {
        if (string.IsNullOrWhiteSpace(targetNodeId) || string.IsNullOrWhiteSpace(dependencyNodeId))
        {
            return false;
        }
        if (string.Equals(targetNodeId, dependencyNodeId, StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "节点不能依赖自身。";
            return false;
        }
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            var node = _workflowEditNodes[i];
            if (!string.Equals(node.NodeId, targetNodeId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (add)
            {
                var source = _workflowEditNodes.FirstOrDefault(item => string.Equals(item.NodeId, dependencyNodeId, StringComparison.OrdinalIgnoreCase));
                if (source is not null && !WorkflowConnectionRules.ArePortsCompatible(source.OutputPortKind, node.InputPortKind, source.NodeType, node.NodeType))
                {
                    var reason = WorkflowConnectionRules.IncompatibilityReason(source.NodeType, node.NodeType, source.OutputPortKind, node.InputPortKind);
                    WorkbenchShell.ManagementPanels.WorkflowActionText.Text = reason;
                    WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = reason;
                    return false;
                }
            }
            var dependencies = add
                ? node.DependsOn.Concat(new[] { dependencyNodeId }).Distinct(StringComparer.OrdinalIgnoreCase).ToArray()
                : node.DependsOn.Where(dep => !string.Equals(dep, dependencyNodeId, StringComparison.OrdinalIgnoreCase)).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
            PushWorkflowUndoSnapshot(add ? "add dependency" : "remove dependency");
            var updated = new WorkflowEditNodeViewModel(
                node.NodeId,
                node.Title,
                node.NodeType,
                node.AssignedAgent,
                string.Join(Environment.NewLine, dependencies),
                node.ToolName,
                node.SkillName,
                node.ReviewGate,
                node.ArgumentsJson,
                node.X,
                node.Y,
                node.InputPortKind,
                node.OutputPortKind);
            _workflowEditNodes[i] = updated;
            if (TryFindWorkflowCycle(_workflowEditNodes, out var cycle))
            {
                _workflowEditNodes[i] = node;
                PopWorkflowUndoSnapshotIfReason(add ? "add dependency" : "remove dependency");
                WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"连线会形成循环依赖：{cycle}";
                RenderWorkflowNodeEditor(node);
                RefreshWorkflowDefinitionPreviewFromEditor(node.NodeId);
                return false;
            }
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = updated.NodeId;
            RenderWorkflowNodeEditor(updated);
            RefreshWorkflowDefinitionPreviewFromEditor(updated.NodeId);
            return true;
        }
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"未找到节点：{targetNodeId}";
        return false;
    }
}
