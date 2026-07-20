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
    internal static bool TryFindWorkflowCycle(IEnumerable<WorkflowEditNodeViewModel> nodes, out string cycle)
    {
        var byId = nodes.ToDictionary(node => node.NodeId, StringComparer.OrdinalIgnoreCase);
        var visiting = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var stack = new List<string>();
        var detectedCycle = "";

        bool Visit(string nodeId)
        {
            if (visited.Contains(nodeId) || !byId.ContainsKey(nodeId))
            {
                return false;
            }
            if (visiting.Contains(nodeId))
            {
                var start = stack.FindIndex(item => string.Equals(item, nodeId, StringComparison.OrdinalIgnoreCase));
                var path = start >= 0 ? stack.Skip(start).Concat(new[] { nodeId }) : stack.Append(nodeId);
                detectedCycle = string.Join(" -> ", path);
                return true;
            }

            visiting.Add(nodeId);
            stack.Add(nodeId);
            foreach (var dependency in byId[nodeId].DependsOn.Where(byId.ContainsKey))
            {
                if (Visit(dependency))
                {
                    return true;
                }
            }
            stack.RemoveAt(stack.Count - 1);
            visiting.Remove(nodeId);
            visited.Add(nodeId);
            return false;
        }

        foreach (var node in byId.Values)
        {
            if (Visit(node.NodeId))
            {
                cycle = detectedCycle;
                return true;
            }
        }
        cycle = "";
        return false;
    }
}
