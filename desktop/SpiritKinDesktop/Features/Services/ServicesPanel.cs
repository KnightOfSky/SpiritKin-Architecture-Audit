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
    internal async Task LoadServicesAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/services");
            var root = doc.RootElement.GetProperty("services");
            RenderServices(root.GetProperty("services"));
            if (doc.RootElement.TryGetProperty("service_ports", out var servicePorts))
            {
                RenderServicePorts(servicePorts);
            }
            RenderServiceActions(root);
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = $"服务加载失败：{ex.Message}";
        }
    }

    private void RenderServices(JsonElement services)
    {
        _services.Clear();
        foreach (var service in services.EnumerateArray())
        {
            var status = ReadJsonString(service, "status");
            var pid = ReadJsonString(service, "pid");
            var port = ReadJsonInt(service, "port");
            var endpoint = port > 0 ? $"127.0.0.1:{port}" : "process";
            _services.Add(new ServiceViewModel(
                ReadJsonString(service, "service_id"),
                ReadJsonString(service, "label"),
                status,
                $"{endpoint} · pid {pid} · {ReadJsonString(service, "description")}",
                status == "running"));
        }
    }

    private void RenderServicePorts(JsonElement servicePorts)
    {
        var previousSelection = WorkbenchShell.ManagementPanels.ServicePortsList.SelectedValue as string;
        _servicePorts.Clear();
        if (servicePorts.ValueKind != JsonValueKind.Object)
        {
            WorkbenchShell.ManagementPanels.ServicePortConfigText.Text = "端口注册表不可用。";
            ResetServicePortRestartPrompt();
            return;
        }
        var configPath = ReadJsonString(servicePorts, "config_path");
        var issueCount = servicePorts.TryGetProperty("issues", out var issues) && issues.ValueKind == JsonValueKind.Array ? issues.GetArrayLength() : 0;
        WorkbenchShell.ManagementPanels.ServicePortConfigText.Text = $"配置文件：{(string.IsNullOrWhiteSpace(configPath) ? "--" : UiDisplayText.ShortTechnical(configPath, 92))} · 问题 {issueCount} · 保存后需重启相关服务生效";
        RenderProjectPortProfileHint(servicePorts, issueCount, configPath);
        var duplicatePorts = servicePorts.TryGetProperty("duplicate_ports", out var duplicateElement) && duplicateElement.ValueKind == JsonValueKind.Object
            ? duplicateElement.EnumerateObject().Select(item => item.Name).ToHashSet(StringComparer.OrdinalIgnoreCase)
            : new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (servicePorts.TryGetProperty("services", out var services) && services.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in services.EnumerateArray())
            {
                var serviceId = ReadJsonString(item, "service_id");
                var label = ReadJsonString(item, "label", serviceId);
                var port = ReadJsonInt(item, "port");
                var defaultPort = ReadJsonInt(item, "default_port");
                var envVar = ReadJsonString(item, "env_var");
                var envValue = ReadJsonString(item, "env_value");
                var configValue = ReadJsonString(item, "config_value");
                var source = ReadJsonString(item, "source", "default");
                var editable = ReadJsonBool(item, "editable", true);
                var listening = ReadJsonBool(item, "listening", false) ? "监听中" : "未监听";
                var required = ReadJsonBool(item, "required", true) ? "必需" : "可选";
                var duplicate = duplicatePorts.Contains(port.ToString(CultureInfo.InvariantCulture)) ? " · 重复端口" : "";
                var overrideText = source switch
                {
                    "env" => $"{envVar}={envValue}",
                    "config" => $"配置={configValue}",
                    _ => "默认",
                };
                var url = ReadJsonString(item, "url");
                var lockText = editable ? "可编辑" : "环境变量优先";
                _servicePorts.Add(new ServicePortViewModel(
                    serviceId,
                    label,
                    port,
                    defaultPort,
                    envVar,
                    envValue,
                    configValue,
                    source,
                    editable,
                    $"{label} · {port}",
                    $"{serviceId} · {required} · {listening}{duplicate} · {lockText}{Environment.NewLine}默认 {defaultPort} · {overrideText}{Environment.NewLine}{url}".Trim()));
            }
        }
        if (_servicePorts.Count == 0)
        {
            WorkbenchShell.ManagementPanels.ServicePortConfigText.Text = "暂无端口注册信息。";
            WorkbenchShell.ManagementPanels.ServicePortValueBox.Clear();
            ResetServicePortRestartPrompt();
            return;
        }
        WorkbenchShell.ManagementPanels.ServicePortsList.SelectedValue = !string.IsNullOrWhiteSpace(previousSelection) && _servicePorts.Any(item => item.Id == previousSelection)
            ? previousSelection
            : _servicePorts.First().Id;
        RenderSelectedServicePortEditor();
    }

    private void RenderSelectedServicePortEditor()
    {
        var selected = _servicePorts.FirstOrDefault(item => item.Id == (WorkbenchShell.ManagementPanels.ServicePortsList.SelectedValue as string)) ?? _servicePorts.FirstOrDefault();
        if (selected is null)
        {
            WorkbenchShell.ManagementPanels.ServicePortValueBox.Clear();
            WorkbenchShell.ManagementPanels.SaveServicePortButton.IsEnabled = false;
            WorkbenchShell.ManagementPanels.ResetServicePortButton.IsEnabled = false;
            WorkbenchShell.ManagementPanels.RepairServicePortsButton.IsEnabled = true;
            return;
        }
        WorkbenchShell.ManagementPanels.ServicePortValueBox.Text = selected.EditValue;
        WorkbenchShell.ManagementPanels.SaveServicePortButton.IsEnabled = selected.Editable;
        WorkbenchShell.ManagementPanels.ResetServicePortButton.IsEnabled = !string.IsNullOrWhiteSpace(selected.ConfigValue);
        WorkbenchShell.ManagementPanels.RepairServicePortsButton.IsEnabled = true;
        if (!selected.Editable)
        {
            WorkbenchShell.ManagementPanels.ServicePortConfigText.Text = $"{selected.Label} 当前由 {selected.EnvVar}={selected.EnvValue} 锁定；保存的配置只会在移除环境变量后生效。";
        }
    }

    private void RenderProjectPortProfileHint(JsonElement servicePorts, int issueCount, string configPath)
    {
        var baseText = $"配置文件：{(string.IsNullOrWhiteSpace(configPath) ? "--" : UiDisplayText.ShortTechnical(configPath, 92))} · 问题 {issueCount} · 保存后需重启相关服务生效";
        var project = CurrentPortProfileProject();
        if (project is null || servicePorts.ValueKind != JsonValueKind.Object)
        {
            WorkbenchShell.ManagementPanels.ServicePortConfigText.Text = baseText;
            WorkbenchShell.ManagementPanels.ApplyProjectPortProfileButton.IsEnabled = false;
            WorkbenchShell.ManagementPanels.DeleteProjectPortProfileButton.IsEnabled = false;
            return;
        }
        var profileId = NormalizeLocalProfileId(project.Id);
        var hasProfile = TryGetProjectPortProfile(servicePorts, profileId, out var profile);
        WorkbenchShell.ManagementPanels.SaveProjectPortProfileButton.IsEnabled = true;
        WorkbenchShell.ManagementPanels.ApplyProjectPortProfileButton.IsEnabled = hasProfile && !ProjectPortProfileMatchesCurrent(servicePorts, profile);
        WorkbenchShell.ManagementPanels.DeleteProjectPortProfileButton.IsEnabled = hasProfile;
        if (!hasProfile)
        {
            WorkbenchShell.ManagementPanels.ServicePortConfigText.Text = $"{baseText} · 当前项目尚无端口 Profile";
            return;
        }
        var label = ReadJsonString(profile, "label", $"{project.Title} 端口 Profile");
        WorkbenchShell.ManagementPanels.ServicePortConfigText.Text = WorkbenchShell.ManagementPanels.ApplyProjectPortProfileButton.IsEnabled
            ? $"{baseText} · 当前项目 Profile「{label}」与当前端口覆盖不一致，可手动应用"
            : $"{baseText} · 当前项目 Profile「{label}」已匹配";
    }

}

