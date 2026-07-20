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
    internal Brush WorkflowConnectionPreviewStroke(Point endPoint)
    {
        var target = WorkflowInputPortPinAtPoint(endPoint);
        if (target is null)
        {
            return new SolidColorBrush(Color.FromRgb(2, 80, 204));
        }
        var source = _workflowGraphNodes.FirstOrDefault(node => string.Equals(node.NodeId, _workflowConnectSourceNodeId, StringComparison.OrdinalIgnoreCase));
        var sourceOutputKind = string.IsNullOrWhiteSpace(_workflowConnectSourceOutputKind)
            ? source?.OutputPortKind ?? "execution"
            : _workflowConnectSourceOutputKind;
        var sourceNodeType = source?.NodeType ?? "";
        var compatible = WorkflowConnectionRules.ArePortsCompatible(sourceOutputKind, target.Kind, sourceNodeType, target.NodeType);
        return compatible
            ? new SolidColorBrush(Color.FromRgb(34, 197, 94))
            : new SolidColorBrush(Color.FromRgb(220, 38, 38));
    }

    internal void PushWorkflowUndoSnapshot(string reason)
    {
        if (_suppressWorkflowHistory)
        {
            return;
        }
        _workflowUndoStack.Push(CaptureWorkflowEditorSnapshot(reason));
        _workflowRedoStack.Clear();
        UpdateWorkflowHistoryButtons();
    }

    internal void PopWorkflowUndoSnapshotIfReason(string reason)
    {
        if (_workflowUndoStack.Count == 0 || !string.Equals(_workflowUndoStack.Peek().Reason, reason, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        _workflowUndoStack.Pop();
        UpdateWorkflowHistoryButtons();
    }

    internal WorkflowEditorSnapshot CaptureWorkflowEditorSnapshot(string reason)
    {
        return new WorkflowEditorSnapshot(
            _workflowEditNodes.ToList(),
            ActiveWorkflowEditNodeId(),
            reason);
    }

    internal void RestoreWorkflowEditorSnapshot(WorkflowEditorSnapshot snapshot)
    {
        _suppressWorkflowHistory = true;
        try
        {
            _workflowEditNodes.Clear();
            foreach (var node in snapshot.Nodes)
            {
                _workflowEditNodes.Add(node);
            }
            var selectedNodeId = SelectExistingId(snapshot.SelectedNodeId, _workflowEditNodes.Select(item => item.NodeId))
                ?? _workflowEditNodes.FirstOrDefault()?.NodeId
                ?? "";
            _selectedWorkflowNodeIds.Clear();
            if (!string.IsNullOrWhiteSpace(selectedNodeId))
            {
                _selectedWorkflowNodeIds.Add(selectedNodeId);
            }
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = selectedNodeId;
            if (WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem is WorkflowEditNodeViewModel selectedNode)
            {
                RenderWorkflowNodeEditor(selectedNode);
            }
            RefreshWorkflowDefinitionPreviewFromEditor(selectedNodeId);
        }
        finally
        {
            _suppressWorkflowHistory = false;
            UpdateWorkflowHistoryButtons();
        }
    }

    internal void UndoWorkflowEditor()
    {
        if (_workflowUndoStack.Count == 0)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "没有可撤销的工作流编辑。";
            return;
        }
        _workflowRedoStack.Push(CaptureWorkflowEditorSnapshot("redo restore"));
        var snapshot = _workflowUndoStack.Pop();
        RestoreWorkflowEditorSnapshot(snapshot);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已撤销：{snapshot.Reason}";
    }

    internal void RedoWorkflowEditor()
    {
        if (_workflowRedoStack.Count == 0)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "没有可重做的工作流编辑。";
            return;
        }
        _workflowUndoStack.Push(CaptureWorkflowEditorSnapshot("undo restore"));
        var snapshot = _workflowRedoStack.Pop();
        RestoreWorkflowEditorSnapshot(snapshot);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "已重做工作流编辑。";
    }

    internal void UpdateWorkflowHistoryButtons()
    {
        WorkbenchShell.ManagementPanels.WorkflowUndoButton.IsEnabled = _workflowUndoStack.Count > 0;
        WorkbenchShell.ManagementPanels.WorkflowRedoButton.IsEnabled = _workflowRedoStack.Count > 0;
    }

}
