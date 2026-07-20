using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal async Task LoadDiagnosticsAsync()
    {
        try
        {
            await _safetyControllerValue.LoadAsync();
            using var doc = await GetJsonAsync($"{_workspaceControllerValue.ApiBase()}/desktop/diagnostics");
            var root = doc.RootElement.GetProperty("diagnostics");
            _diagnosticChecks.Clear();
            foreach (var check in root.GetProperty("checks").EnumerateArray())
            {
                var ok = check.TryGetProperty("ok", out var okEl) && okEl.GetBoolean();
                var severity = ReadJsonString(check, "severity");
                _diagnosticChecks.Add(new EventViewModel(
                    $"{(ok ? "OK" : "FAIL")} · {ReadJsonString(check, "name")}",
                    $"{ReadJsonString(check, "category")} · {severity} · {ReadJsonString(check, "detail")}"));
            }
            _diagnosticIssues.Clear();
            foreach (var issue in root.GetProperty("issues").EnumerateArray())
            {
                var command = "";
                var steps = "";
                if (issue.TryGetProperty("repair_steps", out var stepArray) && stepArray.ValueKind == JsonValueKind.Array)
                {
                    var stepTexts = new List<string>();
                    foreach (var step in stepArray.EnumerateArray())
                    {
                        var stepCommand = ReadJsonString(step, "command");
                        if (string.IsNullOrWhiteSpace(command) && !string.IsNullOrWhiteSpace(stepCommand))
                        {
                            command = stepCommand;
                        }
                        stepTexts.Add($"{ReadJsonString(step, "status")}:{ReadJsonString(step, "title")}");
                    }
                    steps = string.Join(" | ", stepTexts);
                }
                _diagnosticIssues.Add(new ActionItemViewModel(
                    ReadJsonString(issue, "issue_id"),
                    $"{ReadJsonString(issue, "severity")} · {ReadJsonString(issue, "title")}",
                    $"{ReadJsonString(issue, "detail")}{Environment.NewLine}{steps}".Trim(),
                    "diagnostic",
                    command,
                    ""));
            }
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = _diagnosticIssues.Count == 0 ? "未发现需要处理的问题。" : "选择问题后可复制命令，或点击一键自修复处理高优先级服务/模型问题。";
        }
        catch (Exception ex)
        {
            _diagnosticIssues.Clear();
            _diagnosticIssues.Add(new ActionItemViewModel("diagnostics-load-failed", "诊断加载失败", ex.Message, "diagnostic", "", ""));
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "诊断加载失败。";
        }
    }

    internal async Task LoadModuleManagementAsync()
    {
        try
        {
            using var doc = await _desktopApi.GetModuleManagementAsync();
            RenderModuleManagement(doc.RootElement.GetProperty("module_management"));
        }
        catch (Exception ex)
        {
            _moduleManagementControllerValue.Modules.Clear();
            _moduleManagementControllerValue.Actions.Clear();
            WorkbenchShell.ManagementPanels.ModuleManagementSummaryText.Text = $"统一模块管理加载失败：{ex.Message}";
            WorkbenchShell.ManagementPanels.ModuleManagementPortfolioText.Text = "--";
            WorkbenchShell.ManagementPanels.ModuleManagementRiskText.Text = "--";
            WorkbenchShell.ManagementPanels.ModuleManagementGovernanceText.Text = "--";
            WorkbenchShell.ManagementPanels.ModuleManagementActionText.Text = "请确认 command gateway 正在运行并支持 /desktop/module-management。";
        }
    }

    internal async Task ScanModuleManagementAsync()
    {
        try
        {
            WorkbenchShell.ManagementPanels.ModuleManagementActionText.Text = "正在扫描模块治理状态...";
            using var doc = await _desktopApi.ScanModuleManagementAsync();
            RenderModuleManagement(doc.RootElement.GetProperty("module_management"));
            WorkbenchShell.ManagementPanels.ModuleManagementActionText.Text = "模块扫描完成。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ModuleManagementActionText.Text = $"模块扫描失败：{ex.Message}";
        }
    }

    private void RenderModuleManagement(JsonElement state)
    {
        var previousModule = WorkbenchShell.ManagementPanels.ModuleManagementModulesList.SelectedValue as string;
        _moduleManagementControllerValue.Modules.Clear();
        if (state.TryGetProperty("modules", out var modules) && modules.ValueKind == JsonValueKind.Array)
        {
            foreach (var module in modules.EnumerateArray())
            {
                _moduleManagementControllerValue.Modules.Add(new ModuleManagementViewModel(
                    ReadJsonString(module, "module_id"),
                    ReadJsonString(module, "label"),
                    ReadJsonString(module, "status"),
                    ReadJsonString(module, "summary"),
                    ReadJsonString(module, "description"),
                    ReadJsonString(module, "endpoint"),
                    ReadJsonString(module, "desktop_page"),
                    ReadJsonString(module, "business_capability"),
                    ReadJsonString(module, "management_group"),
                    ReadJsonString(module, "owner_role"),
                    ReadJsonString(module, "criticality"),
                    ReadJsonString(module, "maturity"),
                    ReadJsonString(module, "sla"),
                    ReadJsonString(module, "risk_level"),
                    ReadJsonString(module, "risk_summary"),
                    ReadJsonInt(module, "health_score"),
                    ReadJsonString(module, "governance_state"),
                    ReadJsonInt(module, "action_count"),
                    ReadJsonInt(module, "high_action_count"),
                    ReadJsonInt(module, "medium_action_count")));
            }
        }

        _moduleManagementControllerValue.Actions.Clear();
        if (state.TryGetProperty("action_items", out var actions) && actions.ValueKind == JsonValueKind.Array)
        {
            var index = 0;
            foreach (var action in actions.EnumerateArray())
            {
                var moduleId = ReadJsonString(action, "module_id");
                var endpoint = ReadJsonString(action, "endpoint");
                var priority = ReadJsonString(action, "priority", "medium");
                var title = ReadJsonString(action, "title", "待处理事项");
                var detail = ReadJsonString(action, "detail");
                var id = ReadJsonString(action, "proposal_id");
                if (string.IsNullOrWhiteSpace(id))
                {
                    id = $"module-action-{index++}-{moduleId}";
                }
                _moduleManagementControllerValue.Actions.Add(new ActionItemViewModel(
                    id,
                    title,
                    detail,
                    moduleId,
                    endpoint,
                    moduleId,
                    priority,
                    ReadJsonString(action, "module_label", ModuleManagementController.ModuleLabel(moduleId)),
                    ReadJsonString(action, "owner_role"),
                    ReadJsonString(action, "risk_level"),
                    ReadJsonString(action, "operator_hint"),
                    ReadJsonString(action, "management_group"),
                    ReadJsonString(action, "criticality"),
                    ReadJsonString(action, "sla")));
            }
        }

        WorkbenchShell.ManagementPanels.ModuleManagementModulesList.SelectedValue = SelectExistingId(previousModule, _moduleManagementControllerValue.Modules.Select(item => item.ModuleId))
            ?? _moduleManagementControllerValue.Modules.FirstOrDefault()?.ModuleId;
        if (_moduleManagementControllerValue.Actions.Count > 0 && WorkbenchShell.ManagementPanels.ModuleManagementActionsList.SelectedItem is null)
        {
            WorkbenchShell.ManagementPanels.ModuleManagementActionsList.SelectedIndex = 0;
        }
        WorkbenchShell.ManagementPanels.ModuleManagementSummaryText.Text = ModuleManagementController.BuildSummary(state);
        WorkbenchShell.ManagementPanels.ModuleManagementPortfolioText.Text = ModuleManagementController.BuildPortfolioText(state);
        WorkbenchShell.ManagementPanels.ModuleManagementRiskText.Text = ModuleManagementController.BuildRiskText(state);
        WorkbenchShell.ManagementPanels.ModuleManagementGovernanceText.Text = ModuleManagementController.BuildGovernanceText(state);
        _moduleManagementControllerValue.UpdateActionText();
    }
}
