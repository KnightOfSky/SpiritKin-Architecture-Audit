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
    internal Point WorkflowGraphCanvasPoint(Point point)
    {
        return point;
    }

    internal bool TryConnectWorkflowGraphNode(string targetNodeId, string targetInputKind = "")
    {
        if (string.IsNullOrWhiteSpace(_workflowConnectSourceNodeId))
        {
            return false;
        }
        var sourceNodeId = _workflowConnectSourceNodeId;
        _workflowConnectSourceNodeId = "";
        var sourceOutputKind = _workflowConnectSourceOutputKind;
        _workflowConnectSourceOutputKind = "execution";
        if (string.Equals(sourceNodeId, targetNodeId, StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "不能把节点连接到自身。";
            RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), targetNodeId);
            return true;
        }
        var target = _workflowEditNodes.FirstOrDefault(node => string.Equals(node.NodeId, targetNodeId, StringComparison.OrdinalIgnoreCase));
        if (target is null)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"未找到目标节点：{targetNodeId}";
            RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), ActiveWorkflowEditNodeId());
            return true;
        }
        var source = _workflowEditNodes.FirstOrDefault(node => string.Equals(node.NodeId, sourceNodeId, StringComparison.OrdinalIgnoreCase));
        if (source is not null)
        {
            sourceOutputKind = WorkflowConnectionRules.NormalizePortKind(string.IsNullOrWhiteSpace(sourceOutputKind) ? source.OutputPortKind : sourceOutputKind);
            var inputKind = WorkflowConnectionRules.NormalizePortKind(string.IsNullOrWhiteSpace(targetInputKind) ? target.InputPortKind : targetInputKind);
            if (!WorkflowConnectionRules.ArePortsCompatible(sourceOutputKind, inputKind, source.NodeType, target.NodeType))
            {
                var reason = WorkflowConnectionRules.IncompatibilityReason(source.NodeType, target.NodeType, sourceOutputKind, inputKind);
                WorkbenchShell.ManagementPanels.WorkflowActionText.Text = reason;
                WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = reason;
                RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), targetNodeId);
                return true;
            }
        }
        if (!ApplyWorkflowDependencyToNode(targetNodeId, sourceNodeId, add: true))
        {
            RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), targetNodeId);
            return true;
        }
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = targetNodeId;
        var targetKindLabel = string.IsNullOrWhiteSpace(targetInputKind) ? target.InputPortKind : targetInputKind;
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已连接：{sourceNodeId} --[{sourceOutputKind}]-> {targetNodeId} [{targetKindLabel}]。保存定义后连线会持久化。";
        return true;
    }

    internal void StartWorkflowGraphConnect()
    {
        StartWorkflowGraphConnect(ActiveWorkflowEditNodeId());
    }

    internal void StartWorkflowGraphConnect(string sourceNodeId)
    {
        if (string.IsNullOrWhiteSpace(sourceNodeId))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请先选择一个源节点，再启动画布连线。";
            return;
        }
        SelectSingleWorkflowGraphNode(sourceNodeId);
        _workflowConnectSourceNodeId = sourceNodeId;
        _workflowConnectSourceOutputKind = "execution";
        var sourceNode = _workflowGraphNodes.FirstOrDefault(node => string.Equals(node.NodeId, sourceNodeId, StringComparison.OrdinalIgnoreCase));
        if (sourceNode is not null)
        {
            var sourcePin = sourceNode.OutputPins.FirstOrDefault();
            _workflowConnectionStartPoint = WorkflowOutputPortPoint(sourcePin);
            _workflowConnectSourceOutputKind = sourcePin?.Kind ?? sourceNode.OutputPortKind;
        }
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), sourceNodeId);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"画布连线模式：源节点 {sourceNodeId} emits {_workflowConnectSourceOutputKind}。把输出端口连到兼容输入端口。";
    }

    internal void SetWorkflowGraphZoom(double zoom)
    {
        _workflowGraphZoom = Math.Clamp(Math.Round(zoom, 2), 0.5, 1.5);
        WorkbenchShell.ManagementPanels.WorkflowGraphScaleTransform.ScaleX = _workflowGraphZoom;
        WorkbenchShell.ManagementPanels.WorkflowGraphScaleTransform.ScaleY = _workflowGraphZoom;
        UpdateWorkflowGraphCanvasSize();
        WorkbenchShell.ManagementPanels.WorkflowZoomText.Text = $"{Math.Round(_workflowGraphZoom * 100):0}%";
    }

    internal void WorkflowGraphScrollViewer_PreviewMouseWheel(object sender, MouseWheelEventArgs e)
    {
        var beforeZoom = _workflowGraphZoom;
        var viewportPoint = e.GetPosition(WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer);
        var canvasBefore = new Point(
            (WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.HorizontalOffset + viewportPoint.X) / Math.Max(0.01, beforeZoom),
            (WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.VerticalOffset + viewportPoint.Y) / Math.Max(0.01, beforeZoom));
        SetWorkflowGraphZoom(beforeZoom + (e.Delta > 0 ? 0.08 : -0.08));
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.UpdateLayout();
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToHorizontalOffset(Math.Max(0, canvasBefore.X * _workflowGraphZoom - viewportPoint.X));
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToVerticalOffset(Math.Max(0, canvasBefore.Y * _workflowGraphZoom - viewportPoint.Y));
        e.Handled = true;
    }

    internal void UpdateWorkflowGraphZoomHostSize()
    {
        WorkbenchShell.ManagementPanels.WorkflowGraphZoomHost.Width = Math.Max(1, WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Width * _workflowGraphZoom);
        WorkbenchShell.ManagementPanels.WorkflowGraphZoomHost.Height = Math.Max(1, WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Height * _workflowGraphZoom);
    }
}
