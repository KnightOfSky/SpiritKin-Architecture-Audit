using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;

namespace SpiritKinDesktop;

internal sealed partial class ServicesController
{
    private async Task ServicePortActionAsync(object payload)
    {
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/service-ports", payload);
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "端口动作失败");
            var action = doc.RootElement.GetProperty("service_port_action");
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = ReadJsonString(action, "message", "端口配置已更新。");
            if (doc.RootElement.TryGetProperty("service_ports", out var servicePorts))
            {
                RenderServicePorts(servicePorts);
            }
            if (action.TryGetProperty("restart_guidance", out var guidance))
            {
                RenderServicePortRestartGuidance(guidance);
            }
            await LoadDiagnosticsAsync();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = $"端口动作失败：{ex.Message}";
            await LoadServicesAsync();
        }
    }

    private void RenderServiceActions(JsonElement root)
    {
        _serviceActions.Clear();
        if (!root.TryGetProperty("actions", out var actions) || actions.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var item in actions.EnumerateArray().Reverse())
        {
            _serviceActions.Add(new EventViewModel(
                $"{ReadJsonString(item, "status")} · {ReadJsonString(item, "service_id")}",
                $"{ReadJsonString(item, "action")} · {FormatTimeFromDouble(ReadJsonString(item, "created_at"))}{Environment.NewLine}{ReadJsonString(item, "message")}".Trim()));
        }
    }

    private void RenderServiceActionResult(JsonElement actionResult)
    {
        var action = actionResult.TryGetProperty("action_record", out var record) && record.ValueKind == JsonValueKind.Object ? ReadJsonString(record, "action") : "";
        _serviceActions.Insert(0, new EventViewModel(
            $"{ReadJsonString(actionResult, "status")} · {ReadJsonString(actionResult, "service_id")}",
            $"{action}{Environment.NewLine}{ReadJsonString(actionResult, "message")}".Trim()));
        while (_serviceActions.Count > 20)
        {
            _serviceActions.RemoveAt(_serviceActions.Count - 1);
        }
    }

    private async Task ServiceActionAsync(string serviceId, string action)
    {
        WorkbenchShell.ManagementPanels.ServiceActionText.Text = $"{serviceId} {action}...";
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/services", new { action, service_id = serviceId });
            var actionResult = doc.RootElement.GetProperty("service_action");
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = $"{ReadJsonString(actionResult, "status")} · {ReadJsonString(actionResult, "message")}";
            if (actionResult.TryGetProperty("services", out var services))
            {
                RenderServices(services);
            }
            RenderServiceActionResult(actionResult);
            await LoadLogsAsync();
            await LoadDailyAsync();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = $"服务动作失败：{ex.Message}";
            await LoadServicesAsync();
        }
    }
}

