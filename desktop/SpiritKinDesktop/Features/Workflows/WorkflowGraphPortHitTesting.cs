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
    internal string WorkflowNodeIdAtPoint(Point point)
    {
        return _workflowGraphNodes
            .Where(node => point.X >= node.X
                && point.X <= node.X + WorkflowGraphNodeViewModel.NodeWidth
                && point.Y >= node.Y
                && point.Y <= node.Y + WorkflowGraphNodeViewModel.NodeHeight)
            .OrderByDescending(node => node.Selected)
            .Select(node => node.NodeId)
            .FirstOrDefault() ?? "";
    }

    internal string WorkflowInputPortNodeIdAtPoint(Point point)
    {
        return WorkflowInputPortPinAtPoint(point)?.NodeId ?? "";
    }

    internal WorkflowGraphNodeViewModel? WorkflowInputPortNodeAtPoint(Point point)
    {
        return WorkflowInputPortPinAtPoint(point)?.Node;
    }

    internal WorkflowGraphPortPinViewModel? WorkflowInputPortPinAtPoint(Point point)
    {
        return _workflowGraphNodes
            .SelectMany(node => node.InputPins)
            .Where(pin =>
            {
                var input = WorkflowInputPortPoint(pin);
                var dx = point.X - input.X;
                var dy = point.Y - input.Y;
                return (dx * dx) + (dy * dy) <= 22 * 22;
            })
            .OrderByDescending(pin => pin.Node.Selected)
            .ThenBy(pin => Math.Abs(point.Y - WorkflowInputPortPoint(pin).Y))
            .FirstOrDefault();
    }

    internal Point WorkflowInputPortPoint(WorkflowGraphNodeViewModel node)
    {
        return WorkflowInputPortPoint(node.InputPins.FirstOrDefault());
    }

    internal Point WorkflowOutputPortPoint(WorkflowGraphNodeViewModel node)
    {
        return WorkflowOutputPortPoint(node.OutputPins.FirstOrDefault());
    }

    internal static Point WorkflowInputPortPoint(WorkflowGraphPortPinViewModel? pin)
    {
        return pin?.CanvasPoint() ?? default;
    }

    internal static Point WorkflowOutputPortPoint(WorkflowGraphPortPinViewModel? pin)
    {
        return pin?.CanvasPoint() ?? default;
    }

    internal static bool TryWorkflowInputPin(object sender, out WorkflowGraphPortPinViewModel pin)
    {
        if ((sender as FrameworkElement)?.DataContext is WorkflowGraphPortPinViewModel candidate && candidate.IsInput)
        {
            pin = candidate;
            return true;
        }
        if ((sender as FrameworkElement)?.DataContext is WorkflowGraphNodeViewModel node)
        {
            pin = node.InputPins.FirstOrDefault() ?? new WorkflowGraphPortPinViewModel(node, "input", node.InputPortKind, 0, false, false, false);
            return true;
        }
        pin = null!;
        return false;
    }

    internal static bool TryWorkflowOutputPin(object sender, out WorkflowGraphPortPinViewModel pin)
    {
        if ((sender as FrameworkElement)?.DataContext is WorkflowGraphPortPinViewModel candidate && !candidate.IsInput)
        {
            pin = candidate;
            return true;
        }
        if ((sender as FrameworkElement)?.DataContext is WorkflowGraphNodeViewModel node)
        {
            pin = node.OutputPins.FirstOrDefault() ?? new WorkflowGraphPortPinViewModel(node, "output", node.OutputPortKind, 0, false, false, false);
            return true;
        }
        pin = null!;
        return false;
    }

    internal void UpdateWorkflowConnectionPreview(Point endPoint)
    {
        if (string.IsNullOrWhiteSpace(_workflowConnectSourceNodeId) || endPoint == default)
        {
            WorkbenchShell.ManagementPanels.WorkflowConnectionPreviewPath.Visibility = Visibility.Collapsed;
            WorkbenchShell.ManagementPanels.WorkflowConnectionPreviewPath.Data = null;
            return;
        }
        var start = _workflowConnectionStartPoint;
        var delta = Math.Max(64, Math.Abs(endPoint.X - start.X) / 2);
        WorkbenchShell.ManagementPanels.WorkflowConnectionPreviewPath.Data = new PathGeometry(new[]
        {
            new PathFigure(
                start,
                new PathSegment[]
                {
                    new BezierSegment(
                        new Point(start.X + delta, start.Y),
                        new Point(endPoint.X - delta, endPoint.Y),
                        endPoint,
                        isStroked: true),
                },
                closed: false),
        });
        WorkbenchShell.ManagementPanels.WorkflowConnectionPreviewPath.Stroke = WorkflowConnectionPreviewStroke(endPoint);
        WorkbenchShell.ManagementPanels.WorkflowConnectionPreviewPath.Visibility = Visibility.Visible;
    }

}
