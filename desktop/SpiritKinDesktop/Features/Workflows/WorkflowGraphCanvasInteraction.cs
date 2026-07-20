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
    internal void WorkflowGraphCanvas_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (!IsWorkflowCanvasBackgroundSource(e.OriginalSource as DependencyObject))
        {
            return;
        }
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Focus();
        if (_workflowDraggingConnection || !string.IsNullOrWhiteSpace(_workflowConnectSourceNodeId))
        {
            CancelWorkflowGraphConnection("连线已取消。");
            e.Handled = true;
            return;
        }
        if ((Keyboard.Modifiers & ModifierKeys.Shift) != ModifierKeys.Shift)
        {
            BeginWorkflowGraphPan(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer));
            e.Handled = true;
            return;
        }
        _workflowSelectionStartPoint = WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas));
        _workflowSelecting = true;
        WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle.Visibility = Visibility.Visible;
        Canvas.SetLeft(WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle, _workflowSelectionStartPoint.X);
        Canvas.SetTop(WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle, _workflowSelectionStartPoint.Y);
        WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle.Width = 0;
        WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle.Height = 0;
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.CaptureMouse();
        e.Handled = true;
    }

    internal static bool IsWorkflowCanvasBackgroundSource(DependencyObject? source)
    {
        while (source is not null && source is not System.Windows.Controls.ContextMenu)
        {
            if (source is FrameworkElement { DataContext: WorkflowGraphNodeViewModel })
            {
                return false;
            }
            if (source is Canvas)
            {
                return true;
            }
            if (source is ItemsControl)
            {
                return true;
            }
            source = VisualTreeHelper.GetParent(source) ?? LogicalTreeHelper.GetParent(source);
        }
        return false;
    }

    internal void WorkflowGraphCanvas_MouseRightButtonDown(object sender, MouseButtonEventArgs e)
    {
        _workflowLastCanvasContextPoint = WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas));
    }

    internal void WorkflowGraphCanvas_MouseMove(object sender, MouseEventArgs e)
    {
        var current = WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas));
        if (_workflowDraggingConnection)
        {
            UpdateWorkflowConnectionPreview(current);
            e.Handled = true;
            return;
        }
        if (_workflowPanning && e.LeftButton == MouseButtonState.Pressed)
        {
            UpdateWorkflowGraphPan(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer));
            e.Handled = true;
            return;
        }
        if (!_workflowSelecting || e.LeftButton != MouseButtonState.Pressed)
        {
            return;
        }
        var left = Math.Min(_workflowSelectionStartPoint.X, current.X);
        var top = Math.Min(_workflowSelectionStartPoint.Y, current.Y);
        WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle.Width = Math.Abs(current.X - _workflowSelectionStartPoint.X);
        WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle.Height = Math.Abs(current.Y - _workflowSelectionStartPoint.Y);
        Canvas.SetLeft(WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle, left);
        Canvas.SetTop(WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle, top);
        e.Handled = true;
    }

    internal void CompleteWorkflowSelection(Point endPoint)
    {
        _workflowSelecting = false;
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.ReleaseMouseCapture();
        WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle.Visibility = Visibility.Collapsed;
        var left = Math.Min(_workflowSelectionStartPoint.X, endPoint.X);
        var top = Math.Min(_workflowSelectionStartPoint.Y, endPoint.Y);
        var right = Math.Max(_workflowSelectionStartPoint.X, endPoint.X);
        var bottom = Math.Max(_workflowSelectionStartPoint.Y, endPoint.Y);
        var selected = _workflowEditNodes
            .Where(node => node.X + WorkflowGraphNodeViewModel.NodeWidth >= left
                && node.X <= right
                && node.Y + WorkflowGraphNodeViewModel.NodeHeight >= top
                && node.Y <= bottom)
            .Select(node => node.NodeId)
            .ToArray();
        if ((Keyboard.Modifiers & ModifierKeys.Control) != ModifierKeys.Control)
        {
            _selectedWorkflowNodeIds.Clear();
        }
        foreach (var nodeId in selected)
        {
            _selectedWorkflowNodeIds.Add(nodeId);
        }
        var first = _selectedWorkflowNodeIds.FirstOrDefault() ?? ActiveWorkflowEditNodeId();
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = first;
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), first);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = selected.Length == 0 ? "框选未命中节点。" : $"框选节点：{string.Join(", ", selected)}";
    }

    internal void BeginWorkflowGraphPan(Point point)
    {
        _workflowPanning = true;
        _workflowPanStartPoint = point;
        _workflowPanStartHorizontalOffset = WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.HorizontalOffset;
        _workflowPanStartVerticalOffset = WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.VerticalOffset;
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Cursor = Cursors.ScrollAll;
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.CaptureMouse();
    }

    internal void UpdateWorkflowGraphPan(Point point)
    {
        if (!_workflowPanning)
        {
            return;
        }
        var dx = point.X - _workflowPanStartPoint.X;
        var dy = point.Y - _workflowPanStartPoint.Y;
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToHorizontalOffset(Math.Max(0, _workflowPanStartHorizontalOffset - dx));
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToVerticalOffset(Math.Max(0, _workflowPanStartVerticalOffset - dy));
    }

    internal void EndWorkflowGraphPan()
    {
        if (!_workflowPanning)
        {
            return;
        }
        _workflowPanning = false;
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Cursor = Cursors.Arrow;
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.ReleaseMouseCapture();
    }

    internal void CancelWorkflowGraphConnection(string message)
    {
        _workflowDraggingConnection = false;
        _workflowConnectSourceNodeId = "";
        _workflowConnectSourceOutputKind = "execution";
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.ReleaseMouseCapture();
        UpdateWorkflowConnectionPreview(default);
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), ActiveWorkflowEditNodeId());
        if (!string.IsNullOrWhiteSpace(message))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = message;
        }
    }

    internal void WorkflowGraphOutputPort_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (!TryWorkflowOutputPin(sender, out var pin))
        {
            return;
        }
        var node = pin.Node;
        _workflowConnectSourceNodeId = node.NodeId;
        _workflowConnectSourceOutputKind = pin.Kind;
        _workflowDraggingConnection = true;
        _workflowConnectionStartPoint = WorkflowOutputPortPoint(pin);
        SelectSingleWorkflowGraphNode(node.NodeId);
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), node.NodeId);
        UpdateWorkflowConnectionPreview(WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas)));
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.CaptureMouse();
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"从 {node.NodeId} 输出引脚拖线：emits {pin.Kind}。绿色输入引脚可连接，红色不可连接。";
        e.Handled = true;
    }

    internal void WorkflowGraphOutputPort_MouseMove(object sender, MouseEventArgs e)
    {
        if (!_workflowDraggingConnection || e.LeftButton != MouseButtonState.Pressed)
        {
            return;
        }
        UpdateWorkflowConnectionPreview(WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas)));
        e.Handled = true;
    }

    internal void WorkflowGraphOutputPort_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (sender is UIElement element)
        {
            element.ReleaseMouseCapture();
        }
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.ReleaseMouseCapture();
        var targetPin = WorkflowInputPortPinAtPoint(WorkflowGraphCanvasPoint(e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas)));
        if (targetPin is not null)
        {
            _workflowDraggingConnection = false;
            UpdateWorkflowConnectionPreview(default);
            TryConnectWorkflowGraphNode(targetPin.NodeId, targetPin.Kind);
        }
        else
        {
            CancelWorkflowGraphConnection("连线已取消。");
        }
        e.Handled = true;
    }

    internal void WorkflowGraphInputPort_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (!TryWorkflowInputPin(sender, out var pin))
        {
            return;
        }
        var node = pin.Node;
        if (!_workflowDraggingConnection && string.IsNullOrWhiteSpace(_workflowConnectSourceNodeId))
        {
            SelectSingleWorkflowGraphNode(node.NodeId);
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"{node.NodeId} 输入引脚：accepts {pin.Kind}。";
            e.Handled = true;
            return;
        }
        if (_workflowDraggingConnection && sender is UIElement element)
        {
            element.ReleaseMouseCapture();
        }
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.ReleaseMouseCapture();
        _workflowDraggingConnection = false;
        UpdateWorkflowConnectionPreview(default);
        TryConnectWorkflowGraphNode(node.NodeId, pin.Kind);
        e.Handled = true;
    }

}
