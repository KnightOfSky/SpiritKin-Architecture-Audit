using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal async Task LoadMobileManagementAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/mobile-management");
            RenderMobileManagement(doc.RootElement.GetProperty("mobile_management"));
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = "移动端设备状态已刷新。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.MobileManagementSummaryText.Text = $"移动端设备状态加载失败：{ex.Message}";
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = "请确认 command gateway 正在运行并支持 /desktop/mobile-management。";
        }
    }

    internal async Task LoadControlMonitorAsync(bool autoRepair = true, bool updateUi = true)
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/control-monitor");
            if (!doc.RootElement.TryGetProperty("monitor", out var monitor))
            {
                if (updateUi)
                {
                    WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = "控制面监控没有返回快照。";
                }
                return;
            }
            var incidentCount = ReadSafeJsonInt(monitor, "incident_count", 0);
            var repairableCount = ReadSafeJsonInt(monitor, "auto_repairable_count", 0);
            var status = ReadSafeJsonString(monitor, "status", "unknown");
            if (updateUi)
            {
                WorkbenchShell.ManagementPanels.MobileManagementSummaryText.Text =
                    $"控制面监控：{UiDisplayText.Status(status)} · {incidentCount} 个事件 · {repairableCount} 个可自动修复";
            }
            if (autoRepair && repairableCount > 0)
            {
                using var repair = await PostJsonAsync($"{ApiBase()}/desktop/control-monitor", new Dictionary<string, object?>
                {
                    ["action"] = "auto_repair",
                    ["workspace_id"] = ReadSafeJsonString(monitor, "workspace_id", ""),
                });
                var repairResult = repair.RootElement.TryGetProperty("repair_result", out var result) ? result : default;
                if (updateUi)
                {
                    WorkbenchShell.ManagementPanels.MobileManagementActionText.Text =
                        $"控制面监控已刷新：{incidentCount} 个事件 · 自动修复已执行" +
                        (repairResult.ValueKind == JsonValueKind.Object ? $" · {ReadSafeJsonString(repairResult, "status", "完成")}" : "");
                }
            }
            else if (updateUi)
            {
                WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = incidentCount == 0
                    ? "控制面监控正常。"
                    : "控制面发现需要人工确认的事件。";
            }
        }
        catch (Exception ex)
        {
            if (updateUi)
            {
                WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"控制面监控失败：{ex.Message}";
            }
        }
    }
    internal void RenderMobileManagement(JsonElement state)
    {
        var android = TryReadJsonObject(state, "android", out var androidElement) ? androidElement : default;
        var ios = TryReadJsonObject(state, "ios", out var iosElement) ? iosElement : default;
        var androidEndpoint = TryReadJsonObject(android, "endpoint", out var androidEndpointElement) ? androidEndpointElement : default;
        var iosEndpoint = TryReadJsonObject(ios, "endpoint", out var iosEndpointElement) ? iosEndpointElement : default;
        var binding = TryReadJsonObject(state, "binding", out var bindingElement) ? bindingElement : default;
        var androidHealth = TryReadJsonObject(androidEndpoint, "health", out var androidHealthElement) ? androidHealthElement : default;
        var iosHealth = TryReadJsonObject(iosEndpoint, "health", out var iosHealthElement) ? iosHealthElement : default;
        var activeDevice = TryReadJsonObject(android, "active_device", out var activeDeviceElement) ? activeDeviceElement : default;
        var apk = TryReadJsonObject(android, "apk", out var apkElement) ? apkElement : default;
        var installed = TryReadJsonObject(android, "installed", out var installedElement) ? installedElement : default;
        var androidWorker = TryReadJsonObject(android, "worker", out var androidWorkerElement) ? androidWorkerElement : default;
        var promotionGate = TryReadJsonObject(androidWorker, "promotion_gate", out var promotionGateElement) ? promotionGateElement : default;
        var companion = TryReadJsonObject(android, "companion", out var companionElement) ? companionElement : default;
        var artifacts = TryReadJsonObject(android, "artifacts", out var artifactsElement) ? artifactsElement : default;
        var androidHealthOk = ReadSafeJsonBool(androidHealth, "ok");
        var iosHealthOk = ReadSafeJsonBool(iosHealth, "ok");
        var serial = ReadSafeJsonString(activeDevice, "serial", "");
        var apkExists = ReadSafeJsonBool(apk, "exists");
        var installedOk = ReadSafeJsonBool(installed, "installed");
        var androidPort = ReadSafeJsonInt(androidEndpoint, "port", RealtimeContract.DefaultPorts.AndroidEndpoint);
        var iosPort = ReadSafeJsonInt(iosEndpoint, "port", RealtimeContract.DefaultPorts.IosEndpoint);
        var iosControlPort = ReadSafeJsonInt(iosEndpoint, "control_port", androidPort);
        var receiverUrl = ReadSafeJsonString(android, "receiver_url");
        var iosBaseUrl = ReadSafeJsonString(iosEndpoint, "tailscale_base_url");
        RenderMobileWorkspaces(state);
        RenderMobileWorkspaceDevices(state);
        RenderMobileSecurity(state);
        var accountSummary = BuildAccountConsoleSummary(state);
        WorkbenchShell.ManagementPanels.MobileAndroidEndpointMetricText.Text = androidHealthOk ? "在线" : "离线";
        WorkbenchShell.ManagementPanels.MobileAndroidEndpointMetricText.Foreground = androidHealthOk ? new SolidColorBrush(Color.FromRgb(22, 163, 74)) : new SolidColorBrush(Color.FromRgb(217, 119, 6));
        WorkbenchShell.ManagementPanels.MobileAdbMetricText.Text = string.IsNullOrWhiteSpace(serial) ? "未连接" : "已连接";
        WorkbenchShell.ManagementPanels.MobileAdbMetricText.Foreground = string.IsNullOrWhiteSpace(serial) ? new SolidColorBrush(Color.FromRgb(217, 119, 6)) : new SolidColorBrush(Color.FromRgb(22, 163, 74));
        WorkbenchShell.ManagementPanels.MobileApkMetricText.Text = apkExists ? installedOk ? "已安装" : "已构建" : "缺失";
        WorkbenchShell.ManagementPanels.MobileApkMetricText.Foreground = installedOk ? new SolidColorBrush(Color.FromRgb(22, 163, 74)) : new SolidColorBrush(Color.FromRgb(217, 119, 6));
        WorkbenchShell.ManagementPanels.MobileIosEndpointMetricText.Text = iosHealthOk ? "在线" : "离线";
        WorkbenchShell.ManagementPanels.MobileIosEndpointMetricText.Foreground = iosHealthOk ? new SolidColorBrush(Color.FromRgb(22, 163, 74)) : new SolidColorBrush(Color.FromRgb(148, 163, 184));
        WorkbenchShell.ManagementPanels.MobileAndroidReceiverUrlBox.Text = receiverUrl;
        if (string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobilePairingPageUrlBox.Text))
        {
            WorkbenchShell.ManagementPanels.MobilePairingPageUrlBox.Text = ReadSafeJsonString(android, "pairing_url");
        }
        WorkbenchShell.ManagementPanels.MobileIosBaseUrlBox.Text = BindingIosPwaUrl(binding, iosBaseUrl);
        WorkbenchShell.ManagementPanels.MobileAdbDeviceIpBox.Text = ReadSafeJsonString(android, "device_ip");
        var knownPort = ReadSafeJsonInt(android, "known_port");
        WorkbenchShell.ManagementPanels.MobileAdbKnownPortBox.Text = knownPort > 0 ? knownPort.ToString(CultureInfo.InvariantCulture) : "";
        WorkbenchShell.ManagementPanels.MobileManagementSummaryText.Text =
            $"Android 服务 {androidPort} {(androidHealthOk ? "在线" : "离线")} · " +
            $"本机调试 {(string.IsNullOrWhiteSpace(serial) ? "未连接" : serial)} · " +
            $"安装包 {(apkExists ? installedOk ? "已安装" : "已构建" : "缺失")} · " +
            $"iOS 主控 {iosControlPort} {(iosHealthOk ? "在线" : "离线/可选")} · PWA {iosPort}" +
            (string.IsNullOrWhiteSpace(accountSummary) ? "" : $"{Environment.NewLine}{accountSummary}");
        WorkbenchShell.ManagementPanels.MobileAndroidHealthText.Text =
            $"健康状态：{HealthText(androidHealth)}{Environment.NewLine}" +
            $"本机地址：{ReadSafeJsonString(androidEndpoint, "local_health_url", "--")}{Environment.NewLine}" +
            $"Tailscale: {ReadSafeJsonString(androidEndpoint, "tailscale_health_url", "--")}";
        WorkbenchShell.ManagementPanels.MobileAndroidDeviceText.Text =
            $"本机调试工具：{ReadSafeJsonString(android, "adb_path", "--")}{Environment.NewLine}" +
            $"手机：{(string.IsNullOrWhiteSpace(serial) ? "--" : serial)} · {ReadSafeJsonString(activeDevice, "state", "--")} · {ReadSafeJsonString(activeDevice, "detail", "--")}{Environment.NewLine}" +
            $"安装状态：{(installedOk ? "已安装" : "未安装")} · 版本 {ReadSafeJsonString(installed, "version_name", "--")} · {ReadSafeJsonString(installed, "last_update_time", "--")}";
        WorkbenchShell.ManagementPanels.MobileAndroidApkText.Text =
            $"安装包：{(apkExists ? UiDisplayText.ShortTechnical(ReadSafeJsonString(apk, "path"), 100) : "缺失")}{Environment.NewLine}" +
            $"{BuildAndroidApkPromotionText(promotionGate)}{Environment.NewLine}" +
            $"手机端源码目录：{UiDisplayText.ShortTechnical(ReadSafeJsonString(android, "bridge_root"), 100)}{Environment.NewLine}" +
            $"重连脚本：{UiDisplayText.ShortTechnical(ReadSafeJsonString(android, "reconnect_script"), 100)}";
        var androidWorkerText = BuildAndroidWorkerText(androidWorker);
        var androidCompanionText = BuildAndroidCompanionText(companion);
        WorkbenchShell.ManagementPanels.MobileAndroidCompanionText.Text = string.IsNullOrWhiteSpace(androidWorkerText)
            ? androidCompanionText
            : $"{androidWorkerText}{Environment.NewLine}{Environment.NewLine}{androidCompanionText}";
        WorkbenchShell.ManagementPanels.MobileArtifactText.Text = BuildMobileArtifactText(artifacts);
        WorkbenchShell.ManagementPanels.MobileIosHealthText.Text =
            $"健康状态：{HealthText(iosHealth)}{Environment.NewLine}" +
            $"本机地址：{ReadSafeJsonString(iosEndpoint, "local_health_url", "--")}{Environment.NewLine}" +
            $"主控台：{UiDisplayText.Status(ReadSafeJsonString(ios, "terminal_status", "--"))}";
        RenderIosNativeTerminalConfig(ios);
        WorkbenchShell.ManagementPanels.MobileIosShortcutsBox.Text = BuildIosShortcutText(ios);
        WorkbenchShell.ManagementPanels.MobileBridgePathsText.Text =
            $"PC Tailscale IP：{ReadSafeJsonString(androidEndpoint, "pc_tailscale_ip", ReadSafeJsonString(iosEndpoint, "pc_tailscale_ip", "--"))}{Environment.NewLine}" +
            $"{BuildMobileBindingPathText(binding)}" +
            $"Android 手机端基础地址：{receiverUrl.Replace("/link", "", StringComparison.OrdinalIgnoreCase)}{Environment.NewLine}" +
            $"默认工作区：{ReadSafeJsonString(state, "default_workspace_id", "local-ecommerce")}{Environment.NewLine}" +
            $"移动端工作素材目录：{UiDisplayText.ShortTechnical(ReadSafeJsonString(artifacts, "root", "--"), 100)}{Environment.NewLine}" +
            $"手机端状态文件：{UiDisplayText.ShortTechnical(ReadSafeJsonString(companion, "state_path", "--"), 100)}{Environment.NewLine}" +
            "拼多多/微信链接会进入移动端链接队列，并导入电商任务队列。";
    }

    internal void RenderIosNativeTerminalConfig(JsonElement ios)
    {
        if (!TryReadJsonObject(ios, "native_terminal", out var nativeTerminal))
        {
            WorkbenchShell.ManagementPanels.MobileIosNativeStatusText.Text = "当前快照没有 iOS 原生主控配置。";
            WorkbenchShell.ManagementPanels.MobileIosNativeDeepLinkBox.Text = "";
            WorkbenchShell.ManagementPanels.MobileIosNativeConfigBox.Text = "";
            WorkbenchShell.ManagementPanels.MobileIosPairingUrlBox.Text = "";
            return;
        }
        var appId = ReadJsonString(nativeTerminal, "app_id", "com.spiritkin.terminal");
        var scheme = ReadJsonString(nativeTerminal, "scheme", "spiritkin");
        var workspaceId = ReadJsonString(nativeTerminal, "workspace_id", "--");
        var hasPairingToken = ReadJsonBool(nativeTerminal, "has_pairing_token", ReadJsonBool(nativeTerminal, "has_token", false));
        var requiresPairing = ReadJsonBool(nativeTerminal, "requires_pairing", !hasPairingToken);
        var pairingStatus = hasPairingToken ? "配对码已包含" : requiresPairing ? "需要桌面端生成 ios_terminal 配对码" : "需在设置中绑定";
        WorkbenchShell.ManagementPanels.MobileIosNativeStatusText.Text = $"{appId} · {scheme} · 工作区 {workspaceId} · {pairingStatus}";
        WorkbenchShell.ManagementPanels.MobileIosNativeDeepLinkBox.Text = ReadJsonString(nativeTerminal, "deep_link");
        WorkbenchShell.ManagementPanels.MobileIosNativeConfigBox.Text = ReadJsonString(nativeTerminal, "config_json");
        WorkbenchShell.ManagementPanels.MobileIosPairingUrlBox.Text = ReadJsonString(nativeTerminal, "pairing_url");
    }
}
