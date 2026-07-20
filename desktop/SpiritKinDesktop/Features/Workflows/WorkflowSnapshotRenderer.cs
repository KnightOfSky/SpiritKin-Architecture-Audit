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
    internal void RenderWorkflows(JsonElement workflows)
    {
        WorkflowConnectionRules.ApplySchema(workflows);
        RefreshWorkflowNodeTemplatesFromCatalog(workflows);
        _lastWorkflowSnapshot = workflows.Clone();
        var previousDefinition = _activeWorkflowName;
        var previousRun = !string.IsNullOrWhiteSpace(_activeWorkflowRunId)
            ? _activeWorkflowRunId
            : WorkbenchShell.ManagementPanels.WorkflowRunsList.SelectedValue as string;
        var previousNode = WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedValue as string;
        _workflowDefinitions.Clear();
        _workflowDefinitionNodes.Clear();
        _workflowRuns.Clear();
        _workflowVersions.Clear();

        var savedNames = ReadJsonStringArray(workflows, "saved_definition_names").ToHashSet(StringComparer.OrdinalIgnoreCase);
        var definitionsByName = new Dictionary<string, JsonElement>(StringComparer.OrdinalIgnoreCase);
        AddWorkflowDefinitions(workflows, "builtin_definitions", savedNames, definitionsByName);
        AddWorkflowDefinitions(workflows, "definitions", savedNames, definitionsByName, replaceExisting: true);
        if (TryReadJsonObject(workflows, "default_definition", out var fallbackDefinition))
        {
            var fallbackName = ReadJsonString(fallbackDefinition, "name");
            if (!string.IsNullOrWhiteSpace(fallbackName) && !definitionsByName.ContainsKey(fallbackName))
            {
                definitionsByName[fallbackName] = fallbackDefinition.Clone();
                _workflowDefinitions.Add(WorkflowDefinitionViewModel.FromJson(fallbackDefinition, savedNames.Contains(fallbackName)));
            }
        }

        var selectedDefinitionName = ReadJsonString(workflows, "selected_workflow_name", previousDefinition);
        var selectedDefinition = SelectWorkflowDefinition(definitionsByName, selectedDefinitionName, previousDefinition);
        var selectedWorkflowName = ReadSafeJsonString(selectedDefinition, "name", selectedDefinitionName);
        if (string.IsNullOrWhiteSpace(selectedWorkflowName))
        {
            selectedWorkflowName = string.IsNullOrWhiteSpace(previousDefinition) ? "ecommerce.auto_listing.v1" : previousDefinition;
        }
        var definitionChanged = !string.Equals(selectedWorkflowName, previousDefinition, StringComparison.OrdinalIgnoreCase);
        if (definitionChanged)
        {
            previousRun = "";
            previousNode = "";
            _activeWorkflowRunId = "";
            _selectedWorkflowNodeIds.Clear();
            _syncingWorkflowRunSelection = true;
            _syncingWorkflowRunNodeSelection = true;
            try
            {
                WorkbenchShell.ManagementPanels.WorkflowRunsList.SelectedValue = null;
                WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedValue = null;
            }
            finally
            {
                _syncingWorkflowRunSelection = false;
                _syncingWorkflowRunNodeSelection = false;
            }
        }
        _activeWorkflowName = selectedWorkflowName;

        _syncingWorkflowSelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList.SelectedValue = selectedWorkflowName;
        }
        finally
        {
            _syncingWorkflowSelection = false;
        }
        WorkbenchShell.ManagementPanels.WorkflowNameBox.Text = selectedWorkflowName;
        if (_workflowGraphNodes.Count == 0 || definitionChanged)
        {
            _resetWorkflowGraphViewportOnNextRender = true;
        }
        RenderWorkflowDefinition(selectedDefinition);

        if (workflows.TryGetProperty("runs", out var runs) && runs.ValueKind == JsonValueKind.Array)
        {
            foreach (var run in runs.EnumerateArray())
            {
                var runId = ReadJsonString(run, "run_id");
                var status = ReadJsonString(run, "status", "--");
                var workflowName = ReadJsonString(run, "workflow_name", "--");
                if (!string.Equals(workflowName, selectedWorkflowName, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                var counts = run.TryGetProperty("node_status_counts", out var statusCounts) ? statusCounts.GetRawText() : "{}";
                var runnableCount = run.TryGetProperty("runnable_node_ids", out var runnableNodes) && runnableNodes.ValueKind == JsonValueKind.Array
                    ? runnableNodes.GetArrayLength()
                    : 0;
                var updatedAt = ReadJsonString(run, "updated_at", ReadJsonString(run, "created_at"));
                _workflowRuns.Add(new WorkflowRunViewModel(runId, workflowName, status, counts, runnableCount, updatedAt));
            }
        }

        _activeWorkflowRunId = SelectExistingId(previousRun, _workflowRuns.Select(item => item.Id))
            ?? _workflowRuns.FirstOrDefault()?.Id
            ?? "";
        _syncingWorkflowRunSelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowRunsList.SelectedValue = string.IsNullOrWhiteSpace(_activeWorkflowRunId) ? null : _activeWorkflowRunId;
        }
        finally
        {
            _syncingWorkflowRunSelection = false;
        }
        RenderSelectedWorkflowRun(definitionChanged ? null : previousNode);
        RenderWorkflowGovernance(workflows);
        RenderWorkflowMetrics(workflows);
        UpdateWorkflowInputMode(selectedWorkflowName);
        WorkbenchShell.ManagementPanels.WorkflowSummaryText.Text = BuildWorkflowSummary(workflows);
        if (string.Equals(WorkbenchShell.ManagementPanels.WorkflowActionText.Text, "--", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowActionText.Text))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "工作流快照已加载。";
        }
    }

    internal void AddWorkflowDefinitions(JsonElement workflows, string propertyName, HashSet<string> savedNames, Dictionary<string, JsonElement> definitionsByName, bool replaceExisting = false)
    {
        if (!workflows.TryGetProperty(propertyName, out var definitions) || definitions.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var definition in definitions.EnumerateArray())
        {
            var name = ReadJsonString(definition, "name");
            if (string.IsNullOrWhiteSpace(name))
            {
                continue;
            }
            if (definitionsByName.ContainsKey(name))
            {
                if (!replaceExisting)
                {
                    continue;
                }
                definitionsByName[name] = definition.Clone();
                var existing = _workflowDefinitions.FirstOrDefault(item => string.Equals(item.Name, name, StringComparison.OrdinalIgnoreCase));
                if (existing is not null)
                {
                    _workflowDefinitions.Remove(existing);
                }
                _workflowDefinitions.Add(WorkflowDefinitionViewModel.FromJson(definition, savedNames.Contains(name)));
                continue;
            }
            definitionsByName[name] = definition.Clone();
            _workflowDefinitions.Add(WorkflowDefinitionViewModel.FromJson(definition, savedNames.Contains(name)));
        }
    }

    internal JsonElement SelectWorkflowDefinition(Dictionary<string, JsonElement> definitionsByName, params string[] candidates)
    {
        foreach (var candidate in candidates)
        {
            if (!string.IsNullOrWhiteSpace(candidate) && definitionsByName.TryGetValue(candidate, out var definition))
            {
                return definition;
            }
        }
        if (definitionsByName.TryGetValue("ecommerce.auto_listing.v1", out var ecommerceDefinition))
        {
            return ecommerceDefinition;
        }
        return definitionsByName.Values.FirstOrDefault();
    }

    internal void RenderWorkflowDefinition(JsonElement definition)
    {
        _workflowDefinitionNodes.Clear();
        _workflowSwimlanes.Clear();
        _workflowGraphNodes.Clear();
        _workflowGraphEdges.Clear();
        if (definition.ValueKind != JsonValueKind.Object)
        {
            WorkbenchShell.ManagementPanels.WorkflowDefinitionNameText.Text = "--";
            WorkbenchShell.ManagementPanels.WorkflowDefinitionMetaText.Text = "暂无定义";
            WorkbenchShell.ManagementPanels.WorkflowDefinitionDescriptionText.Text = "--";
            WorkbenchShell.ManagementPanels.WorkflowBlueprintSummaryText.Text = "选择工作流定义查看节点依赖";
            WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowVersionBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Clear();
            WorkbenchShell.ManagementPanels.WorkflowDescriptionBox.Clear();
            _workflowEditNodes.Clear();
            _selectedWorkflowNodeIds.Clear();
            UpdateWorkflowHistoryButtons();
            WorkbenchShell.ManagementPanels.WorkflowDynamicParametersPanel.Children.Clear();
            return;
        }

        var view = WorkflowDefinitionViewModel.FromJson(definition, saved: false);
        _activeWorkflowDefinition = definition.Clone();
        WorkbenchShell.ManagementPanels.WorkflowDefinitionNameText.Text = view.DisplayName;
        WorkbenchShell.ManagementPanels.WorkflowDefinitionMetaText.Text = view.Meta;
        WorkbenchShell.ManagementPanels.WorkflowDefinitionDescriptionText.Text = string.IsNullOrWhiteSpace(view.Description) ? "--" : view.Description;
        WorkbenchShell.ManagementPanels.WorkflowNameBox.Text = view.Name;
        WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Text = view.DisplayName;
        WorkbenchShell.ManagementPanels.WorkflowVersionBox.Text = string.Equals(view.Version, "--", StringComparison.OrdinalIgnoreCase) ? "0.1.0" : view.Version;
        WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Text = view.Category;
        WorkbenchShell.ManagementPanels.WorkflowDescriptionBox.Text = view.Description;

        var nodeCount = 0;
        _workflowEditNodes.Clear();
        _selectedWorkflowNodeIds.Clear();
        if (definition.TryGetProperty("nodes", out var nodes) && nodes.ValueKind == JsonValueKind.Array)
        {
            foreach (var node in nodes.EnumerateArray())
            {
                _workflowDefinitionNodes.Add(WorkflowNodeViewModel.ForDefinition(node));
                _workflowEditNodes.Add(WorkflowEditNodeViewModel.FromJson(node, nodeCount));
                nodeCount++;
            }
        }
        var firstDefinitionNodeId = SelectExistingId(WorkbenchShell.ManagementPanels.WorkflowRunNodesList.SelectedValue as string, _workflowEditNodes.Select(item => item.NodeId))
            ?? _workflowEditNodes.FirstOrDefault()?.NodeId;
        _syncingWorkflowNodeSelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = firstDefinitionNodeId;
        }
        finally
        {
            _syncingWorkflowNodeSelection = false;
        }
        if (!string.IsNullOrWhiteSpace(firstDefinitionNodeId))
        {
            _selectedWorkflowNodeIds.Add(firstDefinitionNodeId);
            if (WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem is WorkflowEditNodeViewModel selectedEditNode)
            {
                RenderWorkflowNodeEditor(selectedEditNode);
            }
        }
        _workflowUndoStack.Clear();
        _workflowRedoStack.Clear();
        UpdateWorkflowHistoryButtons();
        WorkbenchShell.ManagementPanels.WorkflowBlueprintSummaryText.Text = $"{view.DisplayName} · 节点 {nodeCount} · {view.Category} · {WorkflowDisplayText.TechnicalLine(view.Name)}";
        RenderWorkflowDynamicParameters(definition);
        RenderWorkflowGraph(definition, default, firstDefinitionNodeId);
        RenderWorkflowNodeDetail(default, definition, firstDefinitionNodeId ?? "");
        RenderWorkflowInspectorNodeSummary(firstDefinitionNodeId);
    }

}
