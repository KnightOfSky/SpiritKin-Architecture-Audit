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
    internal void DisconnectWorkflowNodeInputs(string nodeId)
    {
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            var node = _workflowEditNodes[i];
            if (!string.Equals(node.NodeId, nodeId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (node.DependsOn.Length == 0)
            {
                WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"节点 {nodeId} 没有输入依赖。";
                return;
            }
            PushWorkflowUndoSnapshot("disconnect node inputs");
            _workflowEditNodes[i] = new WorkflowEditNodeViewModel(node.NodeId, node.Title, node.NodeType, node.AssignedAgent, "", node.ToolName, node.SkillName, node.ReviewGate, node.ArgumentsJson, node.X, node.Y, node.InputPortKind, node.OutputPortKind);
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = node.NodeId;
            RenderWorkflowNodeEditor(_workflowEditNodes[i]);
            RefreshWorkflowDefinitionPreviewFromEditor(node.NodeId);
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已断开 {nodeId} 的输入依赖。";
            return;
        }
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"未找到节点：{nodeId}";
    }

    internal void DisconnectWorkflowNodeOutputs(string nodeId)
    {
        var changed = false;
        PushWorkflowUndoSnapshot("disconnect node outputs");
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            var node = _workflowEditNodes[i];
            if (!node.DependsOn.Any(dep => string.Equals(dep, nodeId, StringComparison.OrdinalIgnoreCase)))
            {
                continue;
            }
            _workflowEditNodes[i] = node.WithoutDependency(nodeId);
            changed = true;
        }
        if (!changed)
        {
            PopWorkflowUndoSnapshotIfReason("disconnect node outputs");
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"节点 {nodeId} 没有输出连线。";
            return;
        }
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = SelectExistingId(nodeId, _workflowEditNodes.Select(item => item.NodeId));
        RefreshWorkflowDefinitionPreviewFromEditor(nodeId);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已断开 {nodeId} 的输出连线。";
    }

    internal void DisconnectWorkflowEdge(string sourceNodeId, string targetNodeId)
    {
        if (string.IsNullOrWhiteSpace(sourceNodeId) || string.IsNullOrWhiteSpace(targetNodeId))
        {
            return;
        }
        for (var i = 0; i < _workflowEditNodes.Count; i++)
        {
            var node = _workflowEditNodes[i];
            if (!string.Equals(node.NodeId, targetNodeId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (!node.DependsOn.Any(dep => string.Equals(dep, sourceNodeId, StringComparison.OrdinalIgnoreCase)))
            {
                WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"连线不存在：{sourceNodeId} -> {targetNodeId}";
                return;
            }
            PushWorkflowUndoSnapshot("disconnect edge");
            _workflowEditNodes[i] = node.WithoutDependency(sourceNodeId);
            _workflowEdgeNotes.Remove(WorkflowEdgeNoteKey(targetNodeId, sourceNodeId));
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = targetNodeId;
            RenderWorkflowNodeEditor(_workflowEditNodes[i]);
            RefreshWorkflowDefinitionPreviewFromEditor(targetNodeId);
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已断开单条连线：{sourceNodeId} -> {targetNodeId}。保存定义后持久化。";
            return;
        }
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"未找到目标节点：{targetNodeId}";
    }

    internal string WorkflowEdgeNoteKey(string targetNodeId, string sourceNodeId) => $"{ActiveWorkflowName()}::{targetNodeId}<-{sourceNodeId}";

    internal string ReadWorkflowEdgeNote(string targetNodeId, string sourceNodeId)
    {
        if (string.IsNullOrWhiteSpace(targetNodeId) || string.IsNullOrWhiteSpace(sourceNodeId))
        {
            return "";
        }
        if (_workflowEdgeNotes.TryGetValue(WorkflowEdgeNoteKey(targetNodeId, sourceNodeId), out var cached))
        {
            return cached;
        }
        if (_activeWorkflowDefinition.ValueKind != JsonValueKind.Object
            || !_activeWorkflowDefinition.TryGetProperty("nodes", out var nodes)
            || nodes.ValueKind != JsonValueKind.Array)
        {
            return "";
        }
        foreach (var node in nodes.EnumerateArray())
        {
            if (!string.Equals(ReadJsonString(node, "node_id"), targetNodeId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (TryReadJsonObject(node, "metadata", out var metadata)
                && TryReadJsonObject(metadata, "edge_notes", out var edgeNotes)
                && edgeNotes.TryGetProperty(sourceNodeId, out var note))
            {
                return note.ValueKind == JsonValueKind.String ? note.GetString() ?? "" : note.GetRawText();
            }
        }
        return "";
    }

    internal Dictionary<string, string> BuildWorkflowEdgeNotesMetadata(string targetNodeId, IEnumerable<string> sourceNodeIds)
    {
        var notes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var sourceNodeId in sourceNodeIds)
        {
            var note = ReadWorkflowEdgeNote(targetNodeId, sourceNodeId).Trim();
            if (!string.IsNullOrWhiteSpace(note))
            {
                notes[sourceNodeId] = note;
            }
        }
        return notes;
    }

    internal void SetWorkflowEdgeNote(string sourceNodeId, string targetNodeId, string note)
    {
        if (string.IsNullOrWhiteSpace(sourceNodeId) || string.IsNullOrWhiteSpace(targetNodeId))
        {
            return;
        }
        var normalized = note.Trim();
        var key = WorkflowEdgeNoteKey(targetNodeId, sourceNodeId);
        _workflowEdgeNotes[key] = normalized;
        RefreshWorkflowDefinitionPreviewFromEditor(targetNodeId);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = string.IsNullOrWhiteSpace(normalized)
            ? $"已隐藏连线注释：{sourceNodeId} -> {targetNodeId}。保存定义后持久化。"
            : $"已更新连线注释：{sourceNodeId} -> {targetNodeId}。保存定义后持久化。";
    }

}
