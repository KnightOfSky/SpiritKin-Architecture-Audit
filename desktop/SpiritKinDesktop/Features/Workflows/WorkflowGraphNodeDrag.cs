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
    internal void WorkflowGraphNode_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is not WorkflowGraphNodeViewModel node)
        {
            return;
        }
        if ((Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control)
        {
            ToggleWorkflowGraphNodeSelection(node.NodeId);
        }
        else if (_selectedWorkflowNodeIds.Count != 1 || !_selectedWorkflowNodeIds.Contains(node.NodeId))
        {
            SelectSingleWorkflowGraphNode(node.NodeId);
        }
        _workflowDragNodeId = node.NodeId;
        _workflowDragStartPoint = WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas));
        _workflowDragNodeStart = new Point(node.X, node.Y);
        _workflowDragSelectionStart = _workflowEditNodes
            .Where(item => _selectedWorkflowNodeIds.Contains(item.NodeId))
            .ToDictionary(item => item.NodeId, item => new Point(item.X, item.Y), StringComparer.OrdinalIgnoreCase);
        if (_workflowDragSelectionStart.Count == 0)
        {
            _workflowDragSelectionStart[node.NodeId] = new Point(node.X, node.Y);
        }
        _workflowDraggingNode = false;
        _workflowDragHistoryPushed = false;
        _lastWorkflowDragRenderAt = DateTime.MinValue;
        if (sender is UIElement element)
        {
            element.CaptureMouse();
        }
        e.Handled = true;
    }

    internal void WorkflowGraphNode_MouseMove(object sender, MouseEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_workflowDragNodeId) || e.LeftButton != MouseButtonState.Pressed)
        {
            return;
        }
        var current = WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas));
        var delta = current - _workflowDragStartPoint;
        if (!_workflowDraggingNode && Math.Abs(delta.X) + Math.Abs(delta.Y) < 5)
        {
            return;
        }
        if (!_workflowDragHistoryPushed)
        {
            PushWorkflowUndoSnapshot("move node");
            _workflowDragHistoryPushed = true;
        }
        _workflowDraggingNode = true;
        MoveWorkflowEditNodes(_workflowDragSelectionStart, delta);
        e.Handled = true;
    }

    internal void WorkflowGraphNode_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is not WorkflowGraphNodeViewModel node)
        {
            return;
        }
        if (sender is UIElement element)
        {
            element.ReleaseMouseCapture();
        }
        var wasDragging = _workflowDraggingNode;
        _workflowDragNodeId = "";
        _workflowDragSelectionStart.Clear();
        _workflowDraggingNode = false;
        _workflowDragHistoryPushed = false;
        if (wasDragging)
        {
            RefreshWorkflowDefinitionPreviewFromEditor(node.NodeId);
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"节点位置已更新：{string.Join(", ", _selectedWorkflowNodeIds.DefaultIfEmpty(node.NodeId))}。保存定义后位置会持久化。";
            e.Handled = true;
            return;
        }
        if (TryConnectWorkflowGraphNode(node.NodeId))
        {
            e.Handled = true;
            return;
        }
        SelectSingleWorkflowGraphNode(node.NodeId);
        WorkbenchShell.ManagementPanels.WorkflowSelectedNodeText.Text = $"{node.NodeId} · {node.Status}";
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"{node.Title}{Environment.NewLine}{node.Detail}".Trim();
        var run = FindWorkflowRun(_activeWorkflowRunId);
        var graphDefinition = ActiveWorkflowGraphDefinition();
        RenderWorkflowNodeDetail(run, graphDefinition, node.NodeId);
        e.Handled = true;
    }

    internal void WorkflowGraphCanvas_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (_workflowPanning)
        {
            EndWorkflowGraphPan();
            e.Handled = true;
            return;
        }
        if (_workflowDraggingConnection)
        {
            var targetPin = WorkflowInputPortPinAtPoint(WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas)));
            _workflowDraggingConnection = false;
            UpdateWorkflowConnectionPreview(default);
            WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.ReleaseMouseCapture();
            if (targetPin is not null)
            {
                TryConnectWorkflowGraphNode(targetPin.NodeId, targetPin.Kind);
            }
            else
            {
                CancelWorkflowGraphConnection("连线已取消。");
            }
            e.Handled = true;
            return;
        }
        if (_workflowSelecting)
        {
            CompleteWorkflowSelection(WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas)));
            e.Handled = true;
            return;
        }
        if (!string.IsNullOrWhiteSpace(_workflowConnectSourceNodeId))
        {
            CancelWorkflowGraphConnection("连线已取消。");
            e.Handled = true;
        }
    }

    internal void MoveWorkflowEditNode(string nodeId, double x, double y)
    {
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            if (!string.Equals(_workflowEditNodes[i].NodeId, nodeId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            _workflowEditNodes[i] = _workflowEditNodes[i].WithPosition(Math.Round(x), Math.Round(y));
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = nodeId;
            RefreshWorkflowDefinitionPreviewFromEditor(nodeId);
            return;
        }
    }

    internal void MoveWorkflowEditNodes(Dictionary<string, Point> startPoints, Vector delta)
    {
        if (startPoints.Count == 0)
        {
            return;
        }
        var firstNodeId = startPoints.Keys.FirstOrDefault() ?? "";
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            var node = _workflowEditNodes[i];
            if (!startPoints.TryGetValue(node.NodeId, out var start))
            {
                continue;
            }
            var x = Math.Max(0, Math.Round(start.X + delta.X));
            var y = ClampWorkflowNodeYToLane(node.NodeType, Math.Round(start.Y + delta.Y));
            _workflowEditNodes[i] = node.WithPosition(x, y);
        }
        _syncingWorkflowNodeSelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = firstNodeId;
        }
        finally
        {
            _syncingWorkflowNodeSelection = false;
        }
        var now = DateTime.UtcNow;
        if (_lastWorkflowDragRenderAt != DateTime.MinValue && now - _lastWorkflowDragRenderAt < TimeSpan.FromMilliseconds(32))
        {
            return;
        }
        _lastWorkflowDragRenderAt = now;
        RefreshWorkflowDefinitionPreviewFromEditor(firstNodeId);
    }

    internal static string LaneKeyForNodeType(string nodeType) => WorkflowLaneLayout.LaneKeyForNodeType(nodeType);

    internal static double LaneDefaultY(string nodeType) => WorkflowLaneLayout.LaneDefaultY(nodeType);

    internal static double ClampWorkflowNodeYToLane(string nodeType, double y) => WorkflowLaneLayout.ClampNodeYToLane(nodeType, y);

    internal void ToggleWorkflowLaneAlignment(bool enabled)
    {
        WorkflowLaneLayout.AlignToLanes = enabled;
        RefreshWorkflowDefinitionPreviewFromEditor(ActiveWorkflowEditNodeId());
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = enabled
            ? "已开启泳道辅助对齐；拖拽节点会限制在类型泳道内。"
            : "已关闭泳道强制对齐；节点可在画布上自由摆放。";
    }

}
