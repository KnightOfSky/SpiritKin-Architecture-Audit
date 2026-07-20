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
    internal void RenderWorkflowGraph(JsonElement definition, JsonElement run, string? selectedNodeId)
    {
        if (!string.IsNullOrWhiteSpace(selectedNodeId) && _selectedWorkflowNodeIds.Count == 0)
        {
            _selectedWorkflowNodeIds.Add(selectedNodeId);
        }
        _workflowGraphNodes.Clear();
        _workflowGraphEdges.Clear();
        _workflowSwimlanes.Clear();
        if (definition.ValueKind != JsonValueKind.Object || !definition.TryGetProperty("nodes", out var nodes) || nodes.ValueKind != JsonValueKind.Array)
        {
            SetWorkflowGraphEmptyState("当前工作流没有可显示的节点。");
            return;
        }

        var runnable = run.ValueKind == JsonValueKind.Object
            ? ReadJsonStringArray(run, "runnable_node_ids").ToHashSet(StringComparer.OrdinalIgnoreCase)
            : new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var nodeStates = new Dictionary<string, JsonElement>(StringComparer.OrdinalIgnoreCase);
        if (run.ValueKind == JsonValueKind.Object && run.TryGetProperty("nodes", out var runNodes) && runNodes.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in runNodes.EnumerateObject())
            {
                nodeStates[property.Name] = property.Value;
            }
        }
        var nodeDetails = new Dictionary<string, JsonElement>(StringComparer.OrdinalIgnoreCase);
        if (run.ValueKind == JsonValueKind.Object
            && run.TryGetProperty("selected_node_details", out var detailNodes)
            && detailNodes.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in detailNodes.EnumerateObject())
            {
                nodeDetails[property.Name] = property.Value;
            }
        }

        var nodeLookup = new Dictionary<string, WorkflowGraphNodeViewModel>(StringComparer.OrdinalIgnoreCase);
        var maxX = 0.0;
        var maxY = 0.0;
        var index = 0;
        var connectSourceNodeType = "";
        var connectSourceOutputKind = "";
        if (!string.IsNullOrWhiteSpace(_workflowConnectSourceNodeId))
        {
            foreach (var candidate in nodes.EnumerateArray())
            {
                if (string.Equals(ReadJsonString(candidate, "node_id"), _workflowConnectSourceNodeId, StringComparison.OrdinalIgnoreCase))
                {
                    connectSourceNodeType = ReadJsonString(candidate, "node_type", "agent_task");
                    connectSourceOutputKind = WorkflowGraphNodeViewModel.ReadOutputPortKind(candidate);
                    break;
                }
            }
        }
        foreach (var node in nodes.EnumerateArray())
        {
            var nodeId = ReadJsonString(node, "node_id");
            if (string.IsNullOrWhiteSpace(nodeId))
            {
                continue;
            }
            nodeStates.TryGetValue(nodeId, out var state);
            nodeDetails.TryGetValue(nodeId, out var detail);
            var isSelected = _selectedWorkflowNodeIds.Contains(nodeId)
                || string.Equals(selectedNodeId, nodeId, StringComparison.OrdinalIgnoreCase);
            var graphNode = WorkflowGraphNodeViewModel.FromJson(
                node,
                state,
                detail,
                runnable.Contains(nodeId),
                isSelected,
                index++,
                _workflowConnectSourceNodeId,
                connectSourceNodeType,
                connectSourceOutputKind);
            nodeLookup[nodeId] = graphNode;
            _workflowGraphNodes.Add(graphNode);
            maxX = Math.Max(maxX, graphNode.X + WorkflowGraphNodeViewModel.NodeWidth + 40);
            maxY = Math.Max(maxY, graphNode.Y + WorkflowGraphNodeViewModel.NodeHeight + 40);
        }

        if (_workflowGraphNodes.Count == 0)
        {
            SetWorkflowGraphEmptyState("当前工作流定义没有节点；请在左侧高级设计器新增节点，或恢复内置定义。");
            return;
        }

        foreach (var node in nodes.EnumerateArray())
        {
            var toId = ReadJsonString(node, "node_id");
            if (!nodeLookup.TryGetValue(toId, out var toNode))
            {
                continue;
            }
            foreach (var fromId in ReadJsonStringArray(node, "depends_on"))
            {
                if (!nodeLookup.TryGetValue(fromId, out var fromNode))
                {
                    continue;
                }
                _workflowGraphEdges.Add(WorkflowGraphEdgeViewModel.FromNodes(fromNode, toNode, ReadWorkflowEdgeNote(toId, fromId)));
            }
        }

        WorkbenchShell.ManagementPanels.WorkflowGraphEmptyText.Visibility = Visibility.Collapsed;
        _workflowGraphContentWidth = Math.Max(WorkflowGraphMinCanvasWidth, maxX);
        _workflowGraphContentHeight = Math.Max(WorkflowGraphMinCanvasHeight, maxY);
        UpdateWorkflowGraphCanvasSize();
        UpdateWorkflowSelectionText();
        UpdateWorkflowConnectionPreview(default);
        if (_resetWorkflowGraphViewportOnNextRender)
        {
            _resetWorkflowGraphViewportOnNextRender = false;
            ResetWorkflowGraphViewport();
        }
    }

    internal void SetWorkflowGraphEmptyState(string message)
    {
        WorkbenchShell.ManagementPanels.WorkflowGraphEmptyText.Text = message;
        WorkbenchShell.ManagementPanels.WorkflowGraphEmptyText.Visibility = Visibility.Visible;
        _workflowGraphContentWidth = WorkflowGraphMinCanvasWidth;
        _workflowGraphContentHeight = WorkflowGraphMinCanvasHeight;
        UpdateWorkflowGraphCanvasSize();
        UpdateWorkflowSelectionText();
        UpdateWorkflowConnectionPreview(default);
        if (_resetWorkflowGraphViewportOnNextRender)
        {
            _resetWorkflowGraphViewportOnNextRender = false;
            ResetWorkflowGraphViewport();
        }
    }

    internal void RenderWorkflowSwimlanes(double width, double height)
    {
        _workflowSwimlanes.Clear();
        var laneHeight = WorkflowLaneLayout.LaneHeight;
        var lanes = new[]
        {
            ("Agent / 人工任务", "agent_task", 0.0),
            ("工具 / Skill 自动化", "tool_call", laneHeight),
            ("审核门禁", "review_gate", laneHeight * 2),
        };
        foreach (var (title, key, y) in lanes)
        {
            _workflowSwimlanes.Add(new WorkflowSwimlaneViewModel(title, key, y, Math.Max(width, 980), laneHeight));
        }
        var extendedTop = laneHeight * 3;
        if (height > extendedTop)
        {
            _workflowSwimlanes.Add(new WorkflowSwimlaneViewModel("扩展节点", "other", extendedTop, Math.Max(width, 980), Math.Max(laneHeight, height - extendedTop)));
        }
    }

    internal double ResolveWorkflowGraphCanvasWidth(double contentWidth)
    {
        var viewportWidth = WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ViewportWidth;
        if (double.IsNaN(viewportWidth) || viewportWidth <= 0)
        {
            viewportWidth = WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ActualWidth;
        }
        var visibleWidth = viewportWidth > 0
            ? (viewportWidth / Math.Max(0.01, _workflowGraphZoom)) - WorkflowGraphCanvasPadding
            : WorkflowGraphMinCanvasWidth;
        return Math.Max(WorkflowGraphMinCanvasWidth, Math.Max(contentWidth, visibleWidth));
    }

    internal double ResolveWorkflowGraphCanvasHeight(double contentHeight)
    {
        var viewportHeight = WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ViewportHeight;
        if (double.IsNaN(viewportHeight) || viewportHeight <= 0)
        {
            viewportHeight = WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ActualHeight;
        }
        var visibleHeight = viewportHeight > 0
            ? (viewportHeight / Math.Max(0.01, _workflowGraphZoom)) - WorkflowGraphCanvasPadding
            : WorkflowGraphMinCanvasHeight;
        return Math.Max(WorkflowGraphMinCanvasHeight, Math.Max(contentHeight, visibleHeight));
    }

    internal void UpdateWorkflowGraphCanvasSize()
    {
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Width = ResolveWorkflowGraphCanvasWidth(_workflowGraphContentWidth);
        WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Height = ResolveWorkflowGraphCanvasHeight(_workflowGraphContentHeight);
        UpdateWorkflowGraphZoomHostSize();
        RenderWorkflowSwimlanes(WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Width, WorkbenchShell.ManagementPanels.WorkflowGraphCanvas.Height);
    }

    internal void WorkflowGraphScrollViewer_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        UpdateWorkflowGraphCanvasSize();
    }

    internal void ResetWorkflowGraphViewport()
    {
        Dispatcher.BeginInvoke(() =>
        {
            WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.UpdateLayout();
            WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToHorizontalOffset(0);
            WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.ScrollToVerticalOffset(0);
        }, DispatcherPriority.Loaded);
    }

    internal void JumpToWorkflowBlueprint()
    {
        WorkbenchShell.ManagementPanels.WorkflowGraphScrollViewer.BringIntoView();
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "已定位到节点蓝图。";
    }

    internal void JumpToWorkflowDesigner()
    {
        WorkbenchShell.ManagementPanels.WorkflowDesignerExpander.IsExpanded = true;
        WorkbenchShell.ManagementPanels.WorkflowDesignerExpander.BringIntoView();
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "已定位到工作流设计器。";
    }

}
