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
    internal void WorkflowEditNodesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingWorkflowNodeSelection)
        {
            return;
        }
        if (WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem is not WorkflowEditNodeViewModel node)
        {
            ClearWorkflowNodeEditor();
            return;
        }
        RenderWorkflowNodeEditor(node);
        if (!_selectedWorkflowNodeIds.Contains(node.NodeId))
        {
            SelectSingleWorkflowGraphNode(node.NodeId);
        }
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), node.NodeId);
    }

    internal void SelectWorkflowEditNode(string? nodeId)
    {
        if (string.IsNullOrWhiteSpace(nodeId) || !_workflowEditNodes.Any(item => string.Equals(item.NodeId, nodeId, StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }
        _syncingWorkflowNodeSelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = nodeId;
        }
        finally
        {
            _syncingWorkflowNodeSelection = false;
        }
        if (WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem is WorkflowEditNodeViewModel node)
        {
            RenderWorkflowNodeEditor(node);
        }
    }

    internal void RenderWorkflowNodeEditor(WorkflowEditNodeViewModel node)
    {
        _syncingWorkflowNodeEditor = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text = node.NodeId;
            WorkbenchShell.ManagementPanels.WorkflowNodeLabelBox.Text = node.Title;
            WorkbenchShell.ManagementPanels.WorkflowNodeXBox.Text = Math.Round(node.X).ToString("0");
            WorkbenchShell.ManagementPanels.WorkflowNodeYBox.Text = Math.Round(node.Y).ToString("0");
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeTypeBox, node.NodeType);
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeAgentBox, node.AssignedAgent);
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeInputPortKindBox, node.InputPortKind);
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeOutputPortKindBox, node.OutputPortKind);
            WorkbenchShell.ManagementPanels.WorkflowNodeDependsOnBox.Text = node.DependsOnText;
            WorkbenchShell.ManagementPanels.WorkflowNodeToolNameBox.Text = node.ToolName;
            WorkbenchShell.ManagementPanels.WorkflowNodeSkillNameBox.Text = node.SkillName;
            WorkbenchShell.ManagementPanels.WorkflowNodeReviewGateBox.Text = node.ReviewGate;
            WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text = node.ArgumentsJson;
        }
        finally
        {
            _syncingWorkflowNodeEditor = false;
        }
        RenderWorkflowNodeArgumentSchema(node.ArgumentsJson);
        RenderWorkflowDependencyOptions(node);
    }

    internal void ClearWorkflowNodeEditor()
    {
        _syncingWorkflowNodeEditor = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowNodeLabelBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowNodeXBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowNodeYBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeTypeBox, "agent_task");
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeAgentBox, WorkflowAgentId());
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeInputPortKindBox, WorkflowConnectionRules.DefaultInputPortKindForNodeType("agent_task"));
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeOutputPortKindBox, WorkflowConnectionRules.DefaultOutputPortKindForNodeType("agent_task"));
            WorkbenchShell.ManagementPanels.WorkflowNodeDependsOnBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowNodeToolNameBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowNodeSkillNameBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowNodeReviewGateBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text = "{}";
        }
        finally
        {
            _syncingWorkflowNodeEditor = false;
        }
        RenderWorkflowNodeArgumentSchema("{}");
        RenderWorkflowDependencyOptions(null);
    }

    internal string ActiveWorkflowEditNodeId() =>
        (WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem as WorkflowEditNodeViewModel)?.NodeId
        ?? WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text.Trim();

}
