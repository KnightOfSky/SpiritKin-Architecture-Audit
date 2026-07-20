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
    internal async Task LoadWorkflowsAsync()
    {
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/workflows", new Dictionary<string, object?>
            {
                ["action"] = "snapshot",
                ["workflow_name"] = ActiveWorkflowName(),
                ["project_root"] = _rootDir,
            });
            RenderWorkflows(doc.RootElement.GetProperty("workflows"));
        }
        catch (Exception ex)
        {
            _workflowDefinitions.Clear();
            _workflowDefinitionNodes.Clear();
            _workflowSwimlanes.Clear();
            _workflowGraphNodes.Clear();
            _workflowGraphEdges.Clear();
            _workflowRuns.Clear();
            _workflowRunNodes.Clear();
            _workflowVersions.Clear();
            ResetWorkflowMetrics();
            WorkbenchShell.ManagementPanels.WorkflowSummaryText.Text = $"工作流加载失败：{ex.Message}";
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请确认 command gateway 正在运行并支持 /desktop/workflows。";
        }
    }

    internal async Task<bool> WorkflowActionAsync(string action, Dictionary<string, object?>? payload = null)
    {
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"正在执行 {action}...";
            var body = payload ?? new Dictionary<string, object?>();
            body["action"] = action;
            body["project_root"] = _rootDir;
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/workflows", body);
            if (doc.RootElement.TryGetProperty("action_result", out var actionResult) && actionResult.ValueKind == JsonValueKind.Object)
            {
                WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"{ReadJsonString(actionResult, "message", action)}{Environment.NewLine}{ReadJsonString(actionResult, "error_code")}".Trim();
            }
            RenderWorkflows(doc.RootElement.GetProperty("workflows"));
            await LoadModuleManagementAsync();
            return !doc.RootElement.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.False;
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"{action} 失败：{ex.Message}";
            return false;
        }
    }

}
