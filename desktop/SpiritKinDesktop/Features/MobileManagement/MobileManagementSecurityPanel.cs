using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal void RenderMobileSecurity(JsonElement state)
    {
        if (!TryReadJsonObject(state, "security", out var security))
        {
            WorkbenchShell.ManagementPanels.MobileSecurityText.Text = "安全姿态：当前快照没有移动端安全摘要。";
            return;
        }

        var status = UiDisplayText.Status(ReadSafeJsonString(security, "status", "review"));
        var scope = MobileNetworkScopeLabel(ReadSafeJsonString(security, "network_scope", "unknown"));
        var tailscaleIp = ReadSafeJsonString(security, "pc_tailscale_ip", "--");
        var tokenText = BuildMobileSecurityTokenText(security);
        var warningLines = BuildMobileSecurityWarningLines(security);
        var hint = ReadSafeJsonString(security, "operator_hint");
        var lines = new List<string>
        {
            $"安全姿态：{status} · 网络范围：{scope} · PC Tailscale IP：{(string.IsNullOrWhiteSpace(tailscaleIp) ? "--" : tailscaleIp)}",
            $"访问令牌：{tokenText}",
        };
        lines.AddRange(BuildMobileBindingLines(state));
        if (warningLines.Length == 0)
        {
            lines.Add("风险提示：暂无阻断项；公网访问仍必须使用 HTTPS/WSS 或 Tailscale。");
        }
        else
        {
            lines.AddRange(warningLines);
        }
        if (!string.IsNullOrWhiteSpace(hint))
        {
            lines.Add($"建议：{hint}");
        }
        WorkbenchShell.ManagementPanels.MobileSecurityText.Text = string.Join(Environment.NewLine, lines);
    }

    internal static IEnumerable<string> BuildMobileBindingLines(JsonElement state)
    {
        if (!TryReadJsonObject(state, "binding", out var binding))
        {
            yield break;
        }
        var workspaceId = ReadSafeJsonString(binding, "workspace_id", "--");
        yield return $"绑定：桌面主控 / iOS 主控 / Android 手机端 · 工作区={workspaceId}";
        if (TryReadJsonObject(binding, "android", out var android))
        {
            var receiver = ReadSafeJsonString(android, "receiver_url", "--");
            var page = ReadSafeJsonString(android, "pairing_page_url", "--");
            yield return $"Android 手机端：{UiDisplayText.ShortTechnical(receiver, 96)} · 配对页 {UiDisplayText.ShortTechnical(page, 96)}";
        }
        if (TryReadJsonObject(binding, "ios", out var ios))
        {
            var pwa = ReadSafeJsonString(ios, "pwa_url", "--");
            yield return $"iOS 主控台：{UiDisplayText.ShortTechnical(pwa, 120)}";
        }
        if (binding.TryGetProperty("setup_steps", out var steps) && steps.ValueKind == JsonValueKind.Array)
        {
            foreach (var step in steps.EnumerateArray().Take(3))
            {
                var severity = UiDisplayText.Risk(ReadJsonString(step, "severity", "low"));
                var title = ReadJsonString(step, "title", "--");
                var detail = ReadJsonString(step, "detail");
                yield return string.IsNullOrWhiteSpace(detail)
                    ? $"绑定步骤：{severity} · {title}"
                    : $"绑定步骤：{severity} · {title} - {detail}";
            }
        }
    }

    internal static string BuildMobileSecurityTokenText(JsonElement security)
    {
        if (!TryReadJsonObject(security, "tokens", out var tokens))
        {
            return "--";
        }
        return string.Join(
            " · ",
            TokenLabel(tokens, "command_gateway", "命令网关"),
            TokenLabel(tokens, "android_endpoint", "Android"),
            TokenLabel(tokens, "ios_endpoint", "iOS"));
    }

    internal static string TokenLabel(JsonElement tokens, string key, string label)
    {
        return $"{label} {(ReadSafeJsonBool(tokens, key) ? "已配置" : "未配置")}";
    }

    internal static string[] BuildMobileSecurityWarningLines(JsonElement security)
    {
        if (!security.TryGetProperty("warnings", out var warnings) || warnings.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return warnings.EnumerateArray()
            .Take(5)
            .Select(item =>
            {
                var severity = UiDisplayText.Risk(ReadJsonString(item, "severity", "medium"));
                var title = ReadJsonString(item, "title", "--");
                var detail = ReadJsonString(item, "detail");
                return string.IsNullOrWhiteSpace(detail)
                    ? $"风险提示：{severity} · {title}"
                    : $"风险提示：{severity} · {title} - {detail}";
            })
            .ToArray();
    }

    internal static string MobileNetworkScopeLabel(string scope)
    {
        return (scope ?? "").Trim().ToLowerInvariant() switch
        {
            "local_only" => "本机",
            "tailscale" => "Tailscale",
            "private_lan" => "局域网",
            "public_or_unknown" => "公网/未知",
            "mixed" => "混合",
            _ => UiDisplayText.Status(scope ?? ""),
        };
    }
}

