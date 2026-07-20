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

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal void SelectSingleWorkflowGraphNode(string nodeId)
    {
        _selectedWorkflowNodeIds.Clear();
        if (!string.IsNullOrWhiteSpace(nodeId))
        {
            _selectedWorkflowNodeIds.Add(nodeId);
        }
        SelectWorkflowEditNode(nodeId);
        var run = FindWorkflowRun(_activeWorkflowRunId);
        var graphDefinition = ActiveWorkflowGraphDefinition();
        RenderWorkflowGraph(graphDefinition, run, nodeId);
        RenderWorkflowNodeDetail(run, graphDefinition, nodeId);
        RenderWorkflowInspectorNodeSummary(nodeId);
    }

    internal void ToggleWorkflowGraphNodeSelection(string nodeId)
    {
        if (string.IsNullOrWhiteSpace(nodeId))
        {
            return;
        }
        if (!_selectedWorkflowNodeIds.Add(nodeId))
        {
            _selectedWorkflowNodeIds.Remove(nodeId);
        }
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = _selectedWorkflowNodeIds.LastOrDefault() ?? nodeId;
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), nodeId);
    }

    internal void SelectAllWorkflowGraphNodes()
    {
        _selectedWorkflowNodeIds.Clear();
        foreach (var node in _workflowEditNodes)
        {
            _selectedWorkflowNodeIds.Add(node.NodeId);
        }
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = _workflowEditNodes.FirstOrDefault()?.NodeId;
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), ActiveWorkflowEditNodeId());
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已选择 {_selectedWorkflowNodeIds.Count} 个节点。";
    }

    internal static string NodeIdFromMenuSender(object sender)
    {
        return Convert.ToString((sender as MenuItem)?.Tag)?.Trim() ?? "";
    }

    internal void WorkflowGraphNodeEditMenu_Click(object sender, RoutedEventArgs e)
    {
        var nodeId = NodeIdFromMenuSender(sender);
        OpenWorkflowDesignerForNode(nodeId);
    }

    internal void OpenWorkflowDesignerForNode(string? nodeId)
    {
        var targetNodeId = string.IsNullOrWhiteSpace(nodeId) ? ActiveWorkflowEditNodeId() : nodeId.Trim();
        if (string.IsNullOrWhiteSpace(targetNodeId))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请先选择一个节点。";
            return;
        }
        SelectSingleWorkflowGraphNode(targetNodeId);
        WorkbenchShell.ManagementPanels.WorkflowDesignerExpander.IsExpanded = true;
        WorkbenchShell.ManagementPanels.WorkflowDesignerExpander.BringIntoView();
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.ScrollIntoView(WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"正在编辑节点：{targetNodeId}。在顶部定义框内的“工作流设计器”修改属性后点击“应用节点”，再保存定义。";
    }

    internal void WorkflowGraphNodeApplyInspectorMenu_Click(object sender, RoutedEventArgs e)
    {
        var nodeId = NodeIdFromMenuSender(sender);
        if (!string.Equals(ActiveWorkflowEditNodeId(), nodeId, StringComparison.OrdinalIgnoreCase))
        {
            SelectSingleWorkflowGraphNode(nodeId);
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已切换到节点：{nodeId}。再次右键可应用右侧属性。";
            return;
        }
        ApplyWorkflowNodeEditor();
    }

    internal void WorkflowGraphNodeConnectSourceMenu_Click(object sender, RoutedEventArgs e)
    {
        var nodeId = NodeIdFromMenuSender(sender);
        StartWorkflowGraphConnect(nodeId);
    }

    internal void WorkflowGraphNodeAddAgentChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("agent_task", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddToolChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("tool_call", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddSkillChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("skill_call", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddReviewChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("review_gate", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddBranchChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("branch", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddWaiterChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("waiter", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddCallbackChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("external_callback", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddSubgraphChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("subgraph", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeAddCustomChildMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("custom.open_node", NodeIdFromMenuSender(sender), null);
    }

    internal void WorkflowGraphNodeDuplicateMenu_Click(object sender, RoutedEventArgs e)
    {
        DuplicateWorkflowEditNode(NodeIdFromMenuSender(sender));
    }

    internal void WorkflowGraphNodeDisconnectInputsMenu_Click(object sender, RoutedEventArgs e)
    {
        DisconnectWorkflowNodeInputs(NodeIdFromMenuSender(sender));
    }

    internal void WorkflowGraphNodeDisconnectOutputsMenu_Click(object sender, RoutedEventArgs e)
    {
        DisconnectWorkflowNodeOutputs(NodeIdFromMenuSender(sender));
    }

    internal void WorkflowGraphEdgeBreakMenu_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as MenuItem)?.Tag is not WorkflowGraphEdgeViewModel edge)
        {
            return;
        }
        DisconnectWorkflowEdge(edge.SourceNodeId, edge.TargetNodeId);
    }

    internal void WorkflowGraphEdgeEditNoteMenu_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as MenuItem)?.Tag is not WorkflowGraphEdgeViewModel edge)
        {
            return;
        }
        EditWorkflowEdgeNote(edge);
    }

    internal void WorkflowGraphEdge_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag is not WorkflowGraphEdgeViewModel edge)
        {
            return;
        }
        if (e.ClickCount >= 2)
        {
            EditWorkflowEdgeNote(edge);
            e.Handled = true;
            return;
        }
        if ((Keyboard.Modifiers & ModifierKeys.Alt) != ModifierKeys.Alt)
        {
            return;
        }
        DisconnectWorkflowEdge(edge.SourceNodeId, edge.TargetNodeId);
        e.Handled = true;
    }

    internal void EditWorkflowEdgeNote(WorkflowGraphEdgeViewModel edge)
    {
        var value = PromptText("编辑连线注释", $"连线 {edge.SourceNodeId} -> {edge.TargetNodeId}", edge.Note);
        if (value is null)
        {
            return;
        }
        SetWorkflowEdgeNote(edge.SourceNodeId, edge.TargetNodeId, value);
    }

    internal void WorkflowGraphNodeRepairMenu_Click(object sender, RoutedEventArgs e)
    {
        var nodeId = NodeIdFromMenuSender(sender);
        SelectSingleWorkflowGraphNode(nodeId);
        var run = FindWorkflowRun(_activeWorkflowRunId);
        var graphDefinition = ActiveWorkflowGraphDefinition();
        RenderWorkflowNodeDetail(run, graphDefinition, nodeId);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已诊断节点 {nodeId}。修复建议显示在“Agent / Skill / 队列”区域；断线请右键节点选择“断开输入依赖”或“断开输出连线”。";
    }

    internal void WorkflowGraphNodeDeleteMenu_Click(object sender, RoutedEventArgs e)
    {
        DeleteWorkflowEditNode(NodeIdFromMenuSender(sender));
    }

    internal void WorkflowGraphCanvasAddAgentNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("agent_task", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddToolNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("tool_call", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddSkillNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("skill_call", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddReviewNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("review_gate", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddBranchNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("branch", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddWaiterNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("waiter", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddCallbackNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("external_callback", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddSubgraphNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("subgraph", null, _workflowLastCanvasContextPoint);
    }

    internal void WorkflowGraphCanvasAddCustomNodeMenu_Click(object sender, RoutedEventArgs e)
    {
        AddWorkflowEditNodeFromCanvas("custom.open_node", null, _workflowLastCanvasContextPoint);
    }

    internal async void WorkflowGraphCanvasSaveDefinitionMenu_Click(object sender, RoutedEventArgs e)
    {
        await SaveWorkflowDefinitionAsync();
    }

    internal void WorkflowGraphCanvasValidateDefinitionMenu_Click(object sender, RoutedEventArgs e)
    {
        ValidateWorkflowDefinitionEditor(showSuccess: true);
    }

    internal void WorkflowGraphCanvasAutoLayoutMenu_Click(object sender, RoutedEventArgs e)
    {
        AutoLayoutWorkflowEditNodes();
    }

    internal void WorkflowGraphCanvasResetViewMenu_Click(object sender, RoutedEventArgs e)
    {
        SetWorkflowGraphZoom(1.0);
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToHorizontalOffset(0);
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToVerticalOffset(0);
    }

    internal void UpdateWorkflowSelectionText()
    {
        if (_selectedWorkflowNodeIds.Count > 1)
        {
            WorkbenchShell.ManagementPanels.WorkflowSelectedNodeText.Text = $"已多选 {_selectedWorkflowNodeIds.Count} 个节点";
        }
    }

}
