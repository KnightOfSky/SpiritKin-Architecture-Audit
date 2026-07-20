using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text.Json;
using System.Windows;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal void RenderMobileWorkspaces(JsonElement state)
    {
        var previous = ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim();
        var defaultWorkspace = ReadSafeJsonString(state, "default_workspace_id", "local-ecommerce");
        var workspaces = new List<string>();
        if (state.ValueKind == JsonValueKind.Object
            && state.TryGetProperty("workspaces", out var workspaceArray)
            && workspaceArray.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in workspaceArray.EnumerateArray())
            {
                var id = item.ValueKind == JsonValueKind.Object
                    ? ReadJsonString(item, "workspace_id")
                    : JsonElementText(item);
                if (!string.IsNullOrWhiteSpace(id)
                    && !workspaces.Contains(id, StringComparer.OrdinalIgnoreCase))
                {
                    workspaces.Add(id);
                }
            }
        }
        if (workspaces.Count == 0)
        {
            workspaces.Add(defaultWorkspace);
        }
        WorkbenchShell.ManagementPanels.MobileWorkspaceBox.Items.Clear();
        foreach (var workspaceId in workspaces)
        {
            WorkbenchShell.ManagementPanels.MobileWorkspaceBox.Items.Add(workspaceId);
        }
        var target = string.IsNullOrWhiteSpace(previous) ? defaultWorkspace : previous;
        WorkbenchShell.ManagementPanels.MobileWorkspaceBox.Text = target;
        WorkbenchShell.ManagementPanels.MobileWorkspaceBox.SelectedItem = workspaces.FirstOrDefault(item => string.Equals(item, target, StringComparison.OrdinalIgnoreCase));
        if (string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobilePairingStatusText.Text) || WorkbenchShell.ManagementPanels.MobilePairingStatusText.Text == "--")
        {
            WorkbenchShell.ManagementPanels.MobilePairingStatusText.Text = $"当前工作区：{target}。点击生成配对码后发给 Android 手机端绑定。";
        }
    }

    internal void RenderMobileWorkspaceDevices(JsonElement state)
    {
        var items = new List<EventViewModel>();
        AppendAccountConsoleItems(items, state);
        if (state.ValueKind == JsonValueKind.Object
            && state.TryGetProperty("workspace_devices", out var overview)
            && overview.ValueKind == JsonValueKind.Object
            && overview.TryGetProperty("items", out var workspaceItems)
            && workspaceItems.ValueKind == JsonValueKind.Array)
        {
            foreach (var workspace in workspaceItems.EnumerateArray())
            {
                if (workspace.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                var workspaceId = ReadJsonString(workspace, "workspace_id", "--");
                var name = ReadJsonString(workspace, "name", workspaceId);
                var status = UiDisplayText.Status(ReadJsonString(workspace, "status", "active"));
                var counts = TryReadJsonObject(workspace, "counts", out var countsElement) ? countsElement : default;
                var detail = string.Join(
                    Environment.NewLine,
                    new[]
                    {
                        $"状态：{status} · 手机端 {ReadSafeJsonString(counts, "android", "0")} · iOS 主控 {ReadSafeJsonString(counts, "ios_controllers", "0")} · 远程执行 {ReadSafeJsonString(counts, "remote_workers", "0")}",
                        $"绑定 {ReadSafeJsonString(counts, "active_bindings", "0")} · 待批准 {ReadSafeJsonString(counts, "pairing_requests", "0")} · 待用配对码 {ReadSafeJsonString(counts, "pending_pairings", "0")} · 最近活动 {ReadJsonString(workspace, "last_seen_at", "--")}",
                        BuildWorkspaceDeviceLine(workspace, "android_devices", "Android 手机端"),
                        BuildWorkspaceDeviceLine(workspace, "ios_controllers", "iOS 主控端"),
                        BuildWorkspaceDeviceLine(workspace, "remote_workers", "远程执行端"),
                        "展开下方 Android 手机端条目，可对单台设备执行启停、检查、修复和清队列。",
                    }.Where(line => !string.IsNullOrWhiteSpace(line)));
                items.Add(new EventViewModel($"{workspaceId} · {name}", detail));
                AppendPairingRequestItems(items, workspace, workspaceId);
                AppendAndroidDeviceManagementItems(items, workspace, workspaceId);
            }
        }
        if (items.Count == 0)
        {
            items.Add(new EventViewModel("暂无工作区设备", "刷新后仍为空时，先确认云端控制面是否在线，以及手机端是否已绑定到工作区。"));
        }
        WorkbenchShell.ManagementPanels.MobileWorkspaceDevicesList.ItemsSource = items;
    }

    internal static void AppendAccountConsoleItems(List<EventViewModel> items, JsonElement state)
    {
        if (state.ValueKind != JsonValueKind.Object
            || !state.TryGetProperty("accounts", out var accounts)
            || accounts.ValueKind != JsonValueKind.Object
            || !accounts.TryGetProperty("items", out var accountItems)
            || accountItems.ValueKind != JsonValueKind.Array)
        {
            return;
        }

        foreach (var account in accountItems.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object))
        {
            var accountId = ReadJsonString(account, "account_id", "--");
            var name = ReadJsonString(account, "name", accountId);
            var status = UiDisplayText.Status(ReadJsonString(account, "status", "active"));
            var summary = TryReadJsonObject(account, "usage_summary", out var summaryElement) ? summaryElement : default;
            var workspaceIds = JsonArrayText(account, "workspace_ids");
            var detail = string.Join(
                Environment.NewLine,
                new[]
                {
                    $"账户：{name} · {accountId} · {status}",
                    $"工作区：{workspaceIds}",
                    $"配额：{AccountQuotaLine(summary, "workspace_count", "max_workspaces", "工作区")} · {AccountQuotaLine(summary, "worker_count", "max_workers", "Worker")} · {AccountQuotaLine(summary, "scrapes_this_period", "max_scrapes_per_period", "抓取")}",
                    $"计费周期：{ReadSafeJsonString(summary, "period_start", "--")} → {ReadSafeJsonString(summary, "period_end", "--")}",
                    "账户自助控制台只能查看和绑定自己账户下的远程 Worker，不能执行主控管理动作或 Blueprint workflow.graph.* 动作。",
                }.Where(line => !string.IsNullOrWhiteSpace(line)));
            items.Add(new EventViewModel($"账户自助 · {name}", detail));
        }
    }

    internal static string BuildAccountConsoleSummary(JsonElement state)
    {
        if (state.ValueKind != JsonValueKind.Object
            || !state.TryGetProperty("accounts", out var accounts)
            || accounts.ValueKind != JsonValueKind.Object
            || !accounts.TryGetProperty("items", out var accountItems)
            || accountItems.ValueKind != JsonValueKind.Array)
        {
            return "";
        }

        var parts = new List<string>();
        foreach (var account in accountItems.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object).Take(3))
        {
            var name = ReadJsonString(account, "name", ReadJsonString(account, "account_id", "--"));
            var summary = TryReadJsonObject(account, "usage_summary", out var summaryElement) ? summaryElement : default;
            parts.Add($"{name}: {AccountQuotaLine(summary, "workspace_count", "max_workspaces", "工作区")}, {AccountQuotaLine(summary, "worker_count", "max_workers", "Worker")}, {AccountQuotaLine(summary, "scrapes_this_period", "max_scrapes_per_period", "抓取")}");
        }

        return parts.Count == 0 ? "" : $"账户自助：{string.Join("；", parts)}";
    }

    internal static string AccountQuotaLine(JsonElement summary, string usedKey, string limitKey, string label)
    {
        var used = ReadSafeJsonInt(summary, usedKey);
        var limit = ReadSafeJsonInt(summary, limitKey);
        return limit <= 0
            ? $"{label} {used}/不限"
            : $"{label} {used}/{limit}";
    }

    private static string JsonArrayText(JsonElement source, string propertyName)
    {
        if (!source.TryGetProperty(propertyName, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return "--";
        }
        var items = value.EnumerateArray().Select(JsonElementText).Where(item => !string.IsNullOrWhiteSpace(item)).ToList();
        return items.Count == 0 ? "--" : string.Join(", ", items);
    }

    internal static void AppendPairingRequestItems(List<EventViewModel> items, JsonElement workspace, string workspaceId)
    {
        if (!workspace.TryGetProperty("pairing_requests", out var array) || array.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var request in array.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object))
        {
            var requestId = ReadJsonString(request, "request_id", ReadJsonString(request, "token_id", "--"));
            var role = ReadJsonString(request, "device_role", ReadJsonString(request, "role", "android_bridge"));
            var deviceId = ReadJsonString(request, "device_id", "--");
            var createdAt = ReadJsonString(request, "created_at", "--");
            var detail = string.Join(
                Environment.NewLine,
                new[]
                {
                    $"请求编号：{requestId}",
                    $"工作区：{workspaceId}",
                    $"设备角色：{role} · 手机编号：{deviceId}",
                    $"请求时间：{createdAt}",
                    "选择这一行后，可在下方批准或拒绝该手机绑定请求。",
                });
            items.Add(new EventViewModel($"{workspaceId} · 配对请求 · {requestId}", detail));
        }
    }

    internal static void AppendAndroidDeviceManagementItems(List<EventViewModel> items, JsonElement workspace, string workspaceId)
    {
        if (!workspace.TryGetProperty("android_devices", out var array) || array.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var device in array.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object))
        {
            var deviceId = ReadJsonString(device, "device_id", "--");
            var status = UiDisplayText.Status(ReadJsonString(device, "status", "--"));
            var lastSeen = ReadJsonString(device, "last_seen_at", "--");
            var foreground = ReadJsonString(device, "foreground_package");
            var workflowText = BuildDeviceWorkflowControlText(device);
            var detail = string.Join(
                Environment.NewLine,
                new[]
                {
                    $"手机编号：{deviceId}",
                    $"状态：{status} · 前台：{(string.IsNullOrWhiteSpace(foreground) ? "--" : foreground)} · 最近活动：{lastSeen}",
                    string.IsNullOrWhiteSpace(workflowText) ? "设备工作流：未配置" : $"设备工作流：{workflowText}",
                    "选择这一行后，下方操作只影响这台手机，不影响同工作区其他设备。",
                });
            items.Add(new EventViewModel($"{workspaceId} · 设备控制 · {deviceId}", detail));
        }
    }

    internal static string BuildWorkspaceDeviceLine(JsonElement workspace, string key, string label)
    {
        if (!TryReadJsonArray(workspace, key) || !workspace.TryGetProperty(key, out var array) || array.ValueKind != JsonValueKind.Array)
        {
            return "";
        }
        var parts = array.EnumerateArray()
            .Where(item => item.ValueKind == JsonValueKind.Object)
            .Select(item =>
            {
                var id = ReadJsonString(item, "device_id", "--");
                var status = UiDisplayText.Status(ReadJsonString(item, "status", "--"));
                var lastSeen = ReadJsonString(item, "last_seen_at");
                var foreground = ReadJsonString(item, "foreground_package");
                var suffix = string.IsNullOrWhiteSpace(foreground) ? "" : $" · 前台 {foreground}";
                if (!string.IsNullOrWhiteSpace(lastSeen))
                {
                    suffix += $" · {lastSeen}";
                }
                var workflows = BuildDeviceWorkflowControlLine(item);
                return $"{id}({status}{suffix}){workflows}";
            })
            .Take(6)
            .ToArray();
        return parts.Length == 0 ? "" : $"{label}：{string.Join("；", parts)}";
    }

    internal static string BuildDeviceWorkflowControlLine(JsonElement device)
    {
        var text = BuildDeviceWorkflowControlText(device);
        return string.IsNullOrWhiteSpace(text) ? "" : $" · 工作流[{text}]";
    }

    internal static string BuildDeviceWorkflowControlText(JsonElement device)
    {
        if (!device.TryGetProperty("workflow_controls", out var controls) || controls.ValueKind != JsonValueKind.Array)
        {
            return "";
        }
        var parts = controls.EnumerateArray()
            .Where(item => item.ValueKind == JsonValueKind.Object)
            .Select(item =>
            {
                var workflowId = ReadJsonString(item, "workflow_id", "ecommerce.auto_listing.v1");
                var enabled = ReadJsonBool(item, "enabled", true);
                var status = UiDisplayText.Status(ReadJsonString(item, "status", enabled ? "enabled" : "paused"));
                var repair = ReadJsonString(item, "last_repair_type");
                var repairText = string.IsNullOrWhiteSpace(repair) ? "" : $" · 修复 {repair}";
                return $"{workflowId}:{status}{repairText}";
            })
            .Take(3)
            .ToArray();
        return parts.Length == 0 ? "" : string.Join(", ", parts);
    }

    internal void MobileWorkspaceDevicesList_SelectionChanged(object? sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (WorkbenchShell.ManagementPanels.MobileWorkspaceDevicesList.SelectedItem is not EventViewModel selected)
        {
            return;
        }
        var title = selected.Type ?? "";
        var workspaceId = title.Split('·')[0].Trim();
        if (!string.IsNullOrWhiteSpace(workspaceId))
        {
            WorkbenchShell.ManagementPanels.MobileDeviceWorkflowWorkspaceBox.Text = workspaceId;
        }
        var detail = selected.Meta ?? "";
        var match = System.Text.RegularExpressions.Regex.Match(detail, @"手机编号：([^\r\n]+)");
        if (!match.Success)
        {
            match = System.Text.RegularExpressions.Regex.Match(detail, @"Android 手机端：([^(\s；]+)");
        }
        if (match.Success)
        {
            var deviceId = match.Groups[1].Value.Trim();
            WorkbenchShell.ManagementPanels.MobileDeviceWorkflowDeviceBox.Text = deviceId;
            WorkbenchShell.ManagementPanels.MobileCommandDeviceIdBox.Text = deviceId;
        }
        if (string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileDeviceWorkflowIdBox.Text))
        {
            WorkbenchShell.ManagementPanels.MobileDeviceWorkflowIdBox.Text = "ecommerce.auto_listing.v1";
        }
        var requestMatch = System.Text.RegularExpressions.Regex.Match(detail, @"请求编号：([^\r\n]+)");
        if (requestMatch.Success)
        {
            WorkbenchShell.ManagementPanels.MobilePairingRequestWorkspaceBox.Text = workspaceId;
            WorkbenchShell.ManagementPanels.MobilePairingRequestIdBox.Text = requestMatch.Groups[1].Value.Trim();
        }
    }

    internal Dictionary<string, object?> BuildPairingRequestPayload(bool includeRequestId)
    {
        var workspaceId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobilePairingRequestWorkspaceBox.Text)
            ? ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim()
            : WorkbenchShell.ManagementPanels.MobilePairingRequestWorkspaceBox.Text.Trim();
        var payload = new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase)
        {
            ["workspace_id"] = string.IsNullOrWhiteSpace(workspaceId) ? "local-ecommerce" : workspaceId,
        };
        if (includeRequestId)
        {
            var requestId = WorkbenchShell.ManagementPanels.MobilePairingRequestIdBox.Text.Trim();
            if (string.IsNullOrWhiteSpace(requestId))
            {
                throw new InvalidOperationException("请先在“工作区设备管理”里选择一条配对请求，或手动填写请求编号。");
            }
            payload["request_id"] = requestId;
        }
        return payload;
    }
    internal void RenderAndroidPairingResult(JsonElement pairing)
    {
        var token = ReadJsonString(pairing, "pairing_token");
        var workspaceId = ReadJsonString(pairing, "workspace_id", ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim());
        var deepLink = ReadJsonString(pairing, "deep_link");
        var pageUrl = ReadJsonString(pairing, "pairing_page_url");
        var expiresAt = ReadJsonString(pairing, "expires_at");
        if (!string.IsNullOrWhiteSpace(workspaceId))
        {
            WorkbenchShell.ManagementPanels.MobileWorkspaceBox.Text = workspaceId;
        }
        WorkbenchShell.ManagementPanels.MobilePairingTokenBox.Text = token;
        WorkbenchShell.ManagementPanels.MobilePairingDeepLinkBox.Text = deepLink;
        WorkbenchShell.ManagementPanels.MobilePairingPageUrlBox.Text = pageUrl;
        WorkbenchShell.ManagementPanels.MobilePairingStatusText.Text = $"已生成 {workspaceId} 的 Android 配对码，有效期到 {expiresAt}。";
    }
    internal string BuildAndroidPairingBundleText()
    {
        var workspaceId = ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim();
        var token = WorkbenchShell.ManagementPanels.MobilePairingTokenBox.Text.Trim();
        var receiverUrl = WorkbenchShell.ManagementPanels.MobileAndroidReceiverUrlBox.Text.Trim();
        var deepLink = WorkbenchShell.ManagementPanels.MobilePairingDeepLinkBox.Text.Trim();
        var pageUrl = WorkbenchShell.ManagementPanels.MobilePairingPageUrlBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(workspaceId)
            && string.IsNullOrWhiteSpace(token)
            && string.IsNullOrWhiteSpace(receiverUrl)
            && string.IsNullOrWhiteSpace(deepLink)
            && string.IsNullOrWhiteSpace(pageUrl))
        {
            return "";
        }
        var lines = new List<string>
        {
            "SpiritKin Android 手机端绑定信息",
            $"工作区={workspaceId}",
            $"配对码={token}",
            $"主控地址={receiverUrl}",
        };
        if (!string.IsNullOrWhiteSpace(deepLink))
        {
            lines.Add($"deep_link={deepLink}");
        }
        if (!string.IsNullOrWhiteSpace(pageUrl))
        {
            lines.Add($"pairing_page={pageUrl}");
        }
        return string.Join(Environment.NewLine, lines);
    }
    internal string BuildIosNativeConfigBundleText()
    {
        var baseUrl = WorkbenchShell.ManagementPanels.MobileIosBaseUrlBox.Text.Trim();
        var deepLink = WorkbenchShell.ManagementPanels.MobileIosNativeDeepLinkBox.Text.Trim();
        var configJson = WorkbenchShell.ManagementPanels.MobileIosNativeConfigBox.Text.Trim();
        var pairingUrl = WorkbenchShell.ManagementPanels.MobileIosPairingUrlBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(baseUrl)
            && string.IsNullOrWhiteSpace(deepLink)
            && string.IsNullOrWhiteSpace(configJson))
        {
            return "";
        }
        var lines = new List<string>
        {
            "SpiritKin iOS 原生主控配置信息",
            $"主控地址={baseUrl}",
            "桌面端配对地址请打开配置内容中的 pairing_url，生成 device_role=ios_terminal 的一次性配对码。",
        };
        if (!string.IsNullOrWhiteSpace(pairingUrl))
        {
            lines.Add($"pairing_url={pairingUrl}");
        }
        if (!string.IsNullOrWhiteSpace(deepLink))
        {
            lines.Add($"deep_link={deepLink}");
        }
        if (!string.IsNullOrWhiteSpace(configJson))
        {
            lines.Add("配置内容=");
            lines.Add(configJson);
        }
        return string.Join(Environment.NewLine, lines);
    }
    internal void OpenMobileUrl(string url, string label)
    {
        if (string.IsNullOrWhiteSpace(url) || !Uri.TryCreate(url.Trim(), UriKind.Absolute, out var uri))
        {
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"{label} 地址为空或无效。";
            return;
        }
        Process.Start(new ProcessStartInfo(uri.ToString()) { UseShellExecute = true });
        WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"已打开 {label}。";
    }
    internal void CopyMobileText(string text, string message)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = "没有可复制内容。";
            return;
        }
        Clipboard.SetText(text.Trim());
        WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = message;
    }
}
