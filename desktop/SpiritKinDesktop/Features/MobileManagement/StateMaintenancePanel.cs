using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal async Task LoadStateMaintenanceAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/state-maintenance");
            if (doc.RootElement.TryGetProperty("state_maintenance", out var state))
            {
                RenderStateMaintenance(state);
            }
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = "状态维护快照已刷新。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.StateMaintenanceText.Text = $"状态维护读取失败：{ex.Message}";
        }
    }

    internal async Task StateMaintenanceActionAsync(string action)
    {
        try
        {
            var payload = new Dictionary<string, object?>
            {
                ["action"] = action,
                ["actor"] = "wpf_desktop",
                ["keep_recent"] = 30,
                ["keep_android_commands"] = 300,
                ["keep_android_history"] = 120,
                ["keep_kb_jobs"] = 80,
                ["keep_skill_run_audit_events"] = 500,
                ["keep_project_runtime_events"] = 500,
            };
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/state-maintenance", payload);
            EnsureOkResponse(doc.RootElement, $"状态维护动作失败：{action}");
            if (doc.RootElement.TryGetProperty("state_maintenance", out var state))
            {
                RenderStateMaintenance(state);
            }
            var result = doc.RootElement.TryGetProperty("result", out var resultElement) ? resultElement : default;
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"{UiDisplayText.Status(ReadJsonString(result, "status", "ok"))} · {ReadJsonString(result, "message", action)}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"状态维护动作失败：{ex.Message}";
        }
    }

    internal void RenderStateMaintenance(JsonElement state)
    {
        if (!state.TryGetProperty("components", out var components) || components.ValueKind != JsonValueKind.Array)
        {
            WorkbenchShell.ManagementPanels.StateMaintenanceText.Text = "状态维护：暂无组件快照。";
            return;
        }
        var lines = new List<string>();
        if (state.TryGetProperty("summary", out var summary) && summary.ValueKind == JsonValueKind.Object)
        {
            lines.Add($"状态维护：组件 {ReadJsonString(summary, "component_count", "0")} · 条目 {ReadJsonString(summary, "total_count", "0")} · 关注 {ReadJsonString(summary, "attention_count", "0")}");
        }
        lines.AddRange(components.EnumerateArray().Take(10).Select(item =>
        {
            var label = ReadJsonString(item, "label", ReadJsonString(item, "component_id", "--"));
            var count = ReadJsonString(item, "count", "0");
            var size = ReadJsonString(item, "size_bytes", "0");
            var schema = ReadJsonString(item, "schema_version", "--");
            var flag = ReadJsonBool(item, "needs_attention") ? "需关注" : "正常";
            return $"{label}: {flag} · {count} 项 · {size} bytes · {schema}";
        }));
        WorkbenchShell.ManagementPanels.StateMaintenanceText.Text = string.Join(Environment.NewLine, lines);
    }
}

