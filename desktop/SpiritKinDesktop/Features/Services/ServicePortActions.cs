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
    internal async Task SaveSelectedServicePortAsync()
    {
        var selected = _servicePorts.FirstOrDefault(item => item.Id == (WorkbenchShell.ManagementPanels.ServicePortsList.SelectedValue as string));
        if (selected is null)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "请先选择一个端口。";
            return;
        }
        if (!int.TryParse(WorkbenchShell.ManagementPanels.ServicePortValueBox.Text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var port))
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "端口必须是数字。";
            return;
        }
        await ServicePortActionAsync(new { action = "save_port", service_id = selected.ServiceId, port, actor = "wpf_desktop" });
    }

    internal async Task ResetSelectedServicePortAsync()
    {
        var selected = _servicePorts.FirstOrDefault(item => item.Id == (WorkbenchShell.ManagementPanels.ServicePortsList.SelectedValue as string));
        if (selected is null)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "请先选择一个端口。";
            return;
        }
        await ServicePortActionAsync(new { action = "reset_port", service_id = selected.ServiceId, actor = "wpf_desktop" });
    }

    internal async Task RepairServicePortsAsync()
    {
        await ServicePortActionAsync(new { action = "repair_duplicates", actor = "wpf_desktop" });
    }

    internal void ResetServicePortRestartPrompt()
    {
        _pendingPortRestartServiceIds.Clear();
        _pendingPortMigrationText = "";
        _pendingCommandGatewayUrl = "";
        _pendingEventBridgeUrl = "";
        _pendingPortRestartIncludesCommandGateway = false;
        WorkbenchShell.ManagementPanels.ServicePortRestartText.Text = "端口变化后会在这里显示重启与迁移提示。";
        WorkbenchShell.ManagementPanels.RestartPortServicesButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.ApplyServicePortClientUrlsButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.CopyServicePortMigrationButton.IsEnabled = false;
    }

    private void RenderServicePortRestartGuidance(JsonElement guidance)
    {
        _pendingPortRestartServiceIds.Clear();
        _pendingPortMigrationText = "";
        _pendingCommandGatewayUrl = "";
        _pendingEventBridgeUrl = "";
        _pendingPortRestartIncludesCommandGateway = false;
        if (guidance.ValueKind != JsonValueKind.Object)
        {
            ResetServicePortRestartPrompt();
            return;
        }

        if (guidance.TryGetProperty("managed_service_ids", out var managedIds) && managedIds.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in managedIds.EnumerateArray())
            {
                var serviceId = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText();
                if (!string.IsNullOrWhiteSpace(serviceId) && !_pendingPortRestartServiceIds.Any(id => string.Equals(id, serviceId, StringComparison.OrdinalIgnoreCase)))
                {
                    _pendingPortRestartServiceIds.Add(serviceId);
                }
            }
        }

        _pendingPortRestartIncludesCommandGateway = _pendingPortRestartServiceIds.Any(id => string.Equals(id, "command_gateway", StringComparison.OrdinalIgnoreCase));
        var lines = new List<string>();
        var message = ReadJsonString(guidance, "message");
        if (!string.IsNullOrWhiteSpace(message))
        {
            lines.Add(message);
        }
        if (guidance.TryGetProperty("manual_steps", out var steps) && steps.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in steps.EnumerateArray())
            {
                var step = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText();
                if (!string.IsNullOrWhiteSpace(step))
                {
                    lines.Add($"- {step}");
                }
            }
        }
        if (guidance.TryGetProperty("migration_notes", out var notes) && notes.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in notes.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                var title = ReadJsonString(item, "title");
                var detail = ReadJsonString(item, "detail");
                var targetUrl = ReadJsonString(item, "target_url");
                var note = $"{title}：{detail}";
                if (!string.IsNullOrWhiteSpace(targetUrl))
                {
                    note += $" ({targetUrl})";
                }
                CapturePendingClientUrl(ReadJsonString(item, "service_id"), targetUrl);
                if (!string.IsNullOrWhiteSpace(note.Trim('：', ' ')))
                {
                    lines.Add($"- {note.Trim()}");
                }
            }
        }
        CapturePendingClientUrlsFromServices(guidance);

        _pendingPortMigrationText = string.Join(Environment.NewLine, lines.Where(line => !string.IsNullOrWhiteSpace(line)));
        WorkbenchShell.ManagementPanels.ServicePortRestartText.Text = string.IsNullOrWhiteSpace(_pendingPortMigrationText)
            ? "端口配置已更新；无额外重启提示。"
            : _pendingPortMigrationText;
        WorkbenchShell.ManagementPanels.RestartPortServicesButton.IsEnabled = _pendingPortRestartServiceIds.Count > 0;
        WorkbenchShell.ManagementPanels.ApplyServicePortClientUrlsButton.IsEnabled = PendingClientUrlNeedsApply();
        WorkbenchShell.ManagementPanels.CopyServicePortMigrationButton.IsEnabled = !string.IsNullOrWhiteSpace(_pendingPortMigrationText);
    }

    private void CapturePendingClientUrl(string serviceId, string targetUrl)
    {
        if (string.IsNullOrWhiteSpace(serviceId) || string.IsNullOrWhiteSpace(targetUrl))
        {
            return;
        }
        if (string.Equals(serviceId, "command_gateway", StringComparison.OrdinalIgnoreCase))
        {
            _pendingCommandGatewayUrl = targetUrl.Trim().TrimEnd('/');
        }
        else if (string.Equals(serviceId, "event_bridge", StringComparison.OrdinalIgnoreCase))
        {
            _pendingEventBridgeUrl = targetUrl.Trim();
        }
    }

    private void CapturePendingClientUrlsFromServices(JsonElement guidance)
    {
        if (!guidance.TryGetProperty("services", out var services) || services.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var item in services.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object || !ReadJsonBool(item, "port_changed"))
            {
                continue;
            }
            var serviceId = ReadJsonString(item, "service_id");
            var afterUrl = ReadJsonString(item, "after_url");
            var afterPort = ReadJsonInt(item, "after_port");
            if (string.Equals(serviceId, "command_gateway", StringComparison.OrdinalIgnoreCase))
            {
                _pendingCommandGatewayUrl = afterPort > 0 ? $"http://127.0.0.1:{afterPort}" : afterUrl.Replace("/command", "", StringComparison.OrdinalIgnoreCase).TrimEnd('/');
            }
            else if (string.Equals(serviceId, "event_bridge", StringComparison.OrdinalIgnoreCase))
            {
                _pendingEventBridgeUrl = !string.IsNullOrWhiteSpace(afterUrl) ? afterUrl : afterPort > 0 ? $"ws://127.0.0.1:{afterPort}" : "";
            }
        }
    }

    private bool PendingClientUrlNeedsApply()
    {
        return (!string.IsNullOrWhiteSpace(_pendingCommandGatewayUrl) && !string.Equals(ApiBase(), _pendingCommandGatewayUrl.TrimEnd('/'), StringComparison.OrdinalIgnoreCase))
            || (!string.IsNullOrWhiteSpace(_pendingEventBridgeUrl) && !string.Equals(WorkspaceSidebar.WsUrlBox.Text.Trim(), _pendingEventBridgeUrl.Trim(), StringComparison.OrdinalIgnoreCase));
    }

    internal void ApplyPendingServicePortClientUrls()
    {
        var applied = new List<string>();
        if (!string.IsNullOrWhiteSpace(_pendingCommandGatewayUrl) && !string.Equals(ApiBase(), _pendingCommandGatewayUrl.TrimEnd('/'), StringComparison.OrdinalIgnoreCase))
        {
            WorkspaceSidebar.ApiUrlBox.Text = _pendingCommandGatewayUrl.TrimEnd('/');
            applied.Add("API");
        }
        if (!string.IsNullOrWhiteSpace(_pendingEventBridgeUrl) && !string.Equals(WorkspaceSidebar.WsUrlBox.Text.Trim(), _pendingEventBridgeUrl.Trim(), StringComparison.OrdinalIgnoreCase))
        {
            WorkspaceSidebar.WsUrlBox.Text = _pendingEventBridgeUrl.Trim();
            StartWebSocket();
            applied.Add("WS");
        }
        WorkbenchShell.ManagementPanels.ApplyServicePortClientUrlsButton.IsEnabled = PendingClientUrlNeedsApply();
        WorkbenchShell.ManagementPanels.ServiceActionText.Text = applied.Count == 0
            ? "桌面连接地址已是最新。"
            : $"已应用桌面连接地址：{string.Join(", ", applied)}。";
    }

    internal async Task RestartPendingPortServicesAsync()
    {
        if (_pendingPortRestartServiceIds.Count == 0)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "没有待重启的托管服务。";
            return;
        }
        if (_pendingPortRestartIncludesCommandGateway &&
            !ConfirmAction("重启命令网关", "命令网关重启会短暂断开桌面 API；如果端口已改变，重启后可能需要手动更新右上角 Api URL 后刷新。", "继续重启"))
        {
            return;
        }

        var serviceIds = _pendingPortRestartServiceIds
            .OrderBy(id => string.Equals(id, "command_gateway", StringComparison.OrdinalIgnoreCase) ? 1 : 0)
            .ToList();
        foreach (var serviceId in serviceIds)
        {
            await ServiceActionAsync(serviceId, "restart");
        }
        _pendingPortRestartServiceIds.Clear();
        WorkbenchShell.ManagementPanels.RestartPortServicesButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.ServicePortRestartText.Text = "已提交相关托管服务重启；如客户端保存了旧 URL，请按迁移提示更新。";
        await LoadServicesAsync();
    }

    internal void CopyServicePortMigrationText()
    {
        if (string.IsNullOrWhiteSpace(_pendingPortMigrationText))
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "没有可复制的迁移提示。";
            return;
        }
        Clipboard.SetText(_pendingPortMigrationText);
        WorkbenchShell.ManagementPanels.ServiceActionText.Text = "已复制端口迁移提示。";
    }

}


