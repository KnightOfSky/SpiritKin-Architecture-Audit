using System;
using System.Collections.Generic;
using System.Text.Json;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal static string BindingIosPwaUrl(JsonElement binding, string fallbackBaseUrl)
    {
        if (TryReadJsonObject(binding, "ios", out var ios))
        {
            var pwa = ReadSafeJsonString(ios, "pwa_url");
            if (!string.IsNullOrWhiteSpace(pwa))
            {
                return pwa;
            }
        }
        return fallbackBaseUrl;
    }

    internal static string BuildMobileBindingPathText(JsonElement binding)
    {
        if (binding.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        var lines = new List<string>
        {
            $"绑定工作区：{ReadSafeJsonString(binding, "workspace_id", "--")}",
        };
        if (TryReadJsonObject(binding, "network", out var network))
        {
            lines.Add(
                $"绑定网络：{MobileNetworkScopeLabel(ReadSafeJsonString(network, "scope", "--"))} · 公网传输 {ReadSafeJsonString(network, "public_transport", "https_or_wss")} · 私有网络 {ReadSafeJsonString(network, "preferred_private_transport", "tailscale")}");
        }
        if (TryReadJsonObject(binding, "ios", out var ios))
        {
            lines.Add($"iOS 主控台：{ReadSafeJsonString(ios, "pwa_url", "--")}");
        }
        return string.Join(Environment.NewLine, lines) + Environment.NewLine;
    }
}

