using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace SpiritKinDesktop;

internal sealed partial class AgentsController
{
    internal async Task ExportRemoteModuleAsync()
    {
        if (!TryPrepareRemoteTransfer(out var targetId, out _, requireBaseUrl: false))
        {
            return;
        }
        var exportId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RemoteExportIdBox.Text)
            ? $"remote-export-{DateTimeOffset.UtcNow.ToUnixTimeSeconds()}"
            : WorkbenchShell.ManagementPanels.RemoteExportIdBox.Text.Trim();
        var skillNames = ResolveRemoteSkillNames();
        if (skillNames.Length == 0)
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "没有可导出的 Skill，请先填写 Skill 名称或在 Skills 管理中创建 Skill。";
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "远端包未导出：缺少 Skill。";
            return;
        }
        WorkbenchShell.ManagementPanels.RemoteExportIdBox.Text = exportId;
        WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"正在导出远端包：{exportId}...";
        var payload = new
        {
            action = "export_remote",
            export_id = exportId,
            target_id = targetId,
            module_type = "skill",
            skill_names = skillNames,
            include_training_dataset = true,
            reviewer = "wpf_desktop",
            core_review_approved = true,
            review_reason = "Manual desktop approval for remote export.",
        };
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/agent-management", payload);
            var export = doc.RootElement.GetProperty("export");
            _lastRemoteExportPath = ReadJsonString(export, "package_path");
            if (export.TryGetProperty("package", out var package) && package.ValueKind == JsonValueKind.Object)
            {
                var exportedId = ReadJsonString(package, "export_id");
                if (!string.IsNullOrWhiteSpace(exportedId))
                {
                    WorkbenchShell.ManagementPanels.RemoteExportIdBox.Text = exportedId;
                }
            }
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"远端包已导出：{Path.GetFileName(_lastRemoteExportPath)}";
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"已导出 {skillNames.Length} 个 Skill 到 {targetId}：{_lastRemoteExportPath}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"导出失败：{ex.Message}";
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "远端包导出失败。";
        }
    }

    internal async Task PushRemoteModuleAsync()
    {
        if (!TryPrepareRemoteTransfer(out var targetId, out var baseUrl, requireBaseUrl: true))
        {
            return;
        }
        if (!await EnsureRemotePackageAsync())
        {
            return;
        }
        WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"正在推送到 {targetId}...";
        var payload = new
        {
            action = "push_remote",
            package_path = _lastRemoteExportPath,
            target_id = targetId,
            base_url = baseUrl,
            reviewer = "wpf_desktop",
            core_review_approved = true,
            review_reason = "Manual desktop approval for remote push.",
        };
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/agent-management", payload);
            if (!doc.RootElement.TryGetProperty("push", out var push))
            {
                WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "推送失败：后端响应缺少 push。";
                return;
            }
            push.TryGetProperty("remote_response", out var response);
            var ok = ReadJsonBool(push, "ok", false) || (response.ValueKind == JsonValueKind.Object && ReadJsonBool(response, "ok", false));
            var packageId = response.ValueKind == JsonValueKind.Object ? ReadJsonString(response, "package_id") : "";
            var detail = response.ValueKind == JsonValueKind.Object
                ? $"{ReadJsonString(response, "error")} {ReadJsonString(response, "detail")} {ReadJsonString(response, "message")}".Trim()
                : "";
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = ok
                ? $"已推送到 {targetId}：{(string.IsNullOrWhiteSpace(packageId) ? Path.GetFileName(_lastRemoteExportPath) : packageId)}"
                : $"推送失败：{(string.IsNullOrWhiteSpace(detail) ? "远端未返回详情。" : detail)}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"推送失败：{ex.Message}";
        }
    }

    internal async Task ExecuteRemoteModuleAsync()
    {
        if (!TryPrepareRemoteTransfer(out var targetId, out var baseUrl, requireBaseUrl: true))
        {
            return;
        }
        if (!await EnsureRemotePackageAsync())
        {
            return;
        }
        if (!ConfirmAction(
            "执行远端包",
            "将把最近导出的远端包发送到 worker 并请求执行/验证。远端 worker 默认只导入登记；若开启 SPIRITKIN_REMOTE_ALLOW_PACKAGE_COMMANDS=1 才会运行验证命令。",
            "执行"))
        {
            return;
        }
        WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"正在请求 {targetId} 执行远端包...";
        var payload = new
        {
            action = "execute_remote",
            package_path = _lastRemoteExportPath,
            target_id = targetId,
            base_url = baseUrl,
            run_verification = true,
            reviewer = "wpf_desktop",
            core_review_approved = true,
            review_reason = "Manual desktop approval for remote execution.",
        };
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/agent-management", payload);
            if (!doc.RootElement.TryGetProperty("remote_execution", out var execution))
            {
                WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "远端执行失败：后端响应缺少 remote_execution。";
                return;
            }
            execution.TryGetProperty("remote_response", out var response);
            var status = response.ValueKind == JsonValueKind.Object ? ReadJsonString(response, "status") : "";
            var detail = response.ValueKind == JsonValueKind.Object
                ? $"{ReadJsonString(response, "error")} {ReadJsonString(response, "detail")} {ReadJsonString(response, "message")}".Trim()
                : "";
            var ok = ReadJsonBool(execution, "ok", false) || (response.ValueKind == JsonValueKind.Object && ReadJsonBool(response, "ok", false));
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = ok
                ? $"远端包已执行：{(string.IsNullOrWhiteSpace(status) ? "完成" : status)}"
                : $"远端包未完成：{(string.IsNullOrWhiteSpace(status + detail) ? "远端未返回详情。" : $"{status} {detail}".Trim())}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"远端执行失败：{ex.Message}";
        }
    }

    internal async Task<bool> EnsureRemotePackageAsync()
    {
        if (TryResolveRemoteExportPathFromInput())
        {
            return true;
        }
        await ExportRemoteModuleAsync();
        var ok = TryResolveRemoteExportPathFromInput();
        if (!ok)
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "没有可用远端包，请先导出。";
        }
        return ok;
    }

    internal bool TryPrepareRemoteTransfer(out string targetId, out string baseUrl, bool requireBaseUrl)
    {
        targetId = "";
        baseUrl = "";
        if (!ApplySelectedRemoteTargetFromEditor(showMessage: false))
        {
            return false;
        }
        targetId = CurrentRemoteTargetId();
        baseUrl = WorkbenchShell.ManagementPanels.RemoteTargetBaseUrlBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(targetId))
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "请先选择或填写远端目标 ID。";
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "远端目标未配置。";
            return false;
        }
        if (!requireBaseUrl)
        {
            return true;
        }
        if (string.IsNullOrWhiteSpace(baseUrl))
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "请先填写远端目标接口地址。";
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "远端目标缺少接口地址。";
            return false;
        }
        if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri) || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "远端目标接口地址必须是 http 或 https 地址。";
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "远端目标地址格式无效。";
            return false;
        }
        baseUrl = baseUrl.TrimEnd('/');
        WorkbenchShell.ManagementPanels.RemoteTargetBaseUrlBox.Text = baseUrl;
        return true;
    }

    internal string CurrentRemoteTargetId()
    {
        return ((WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.RemoteTargetIdBox.Text).Trim();
    }

    internal string[] ResolveRemoteSkillNames()
    {
        var explicitNames = WorkbenchShell.ManagementPanels.RemoteSkillNamesBox.Text
            .Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(name => !string.IsNullOrWhiteSpace(name))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (explicitNames.Length > 0)
        {
            return explicitNames;
        }
        var names = _skills
            .Where(skill => !skill.Status.Equals("archived", StringComparison.OrdinalIgnoreCase))
            .Select(skill => skill.Name)
            .Where(name => !string.IsNullOrWhiteSpace(name))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (names.Length > 0)
        {
            return names;
        }
        var editorName = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim();
        return string.IsNullOrWhiteSpace(editorName) ? Array.Empty<string>() : new[] { editorName };
    }

    internal bool TryResolveRemoteExportPathFromInput()
    {
        var exportId = WorkbenchShell.ManagementPanels.RemoteExportIdBox.Text.Trim();
        if (!string.IsNullOrWhiteSpace(exportId))
        {
            var candidate = Path.Combine(_rootDir, "state", "remote_exports", $"{SafeRemoteExportId(exportId)}.json");
            if (File.Exists(candidate))
            {
                _lastRemoteExportPath = candidate;
                WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = $"已找到远端包：{_lastRemoteExportPath}";
                return true;
            }
        }
        if (!string.IsNullOrWhiteSpace(_lastRemoteExportPath) && File.Exists(_lastRemoteExportPath))
        {
            return true;
        }
        return false;
    }

    internal void OpenRemoteExportsFolder()
    {
        var target = !string.IsNullOrWhiteSpace(_lastRemoteExportPath) ? _lastRemoteExportPath : Path.Combine(_rootDir, "state", "remote_exports");
        if (File.Exists(target))
        {
            Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{target}\"") { UseShellExecute = true });
            WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "已定位最近导出包。";
            return;
        }
        if (!Directory.Exists(target))
        {
            Directory.CreateDirectory(target);
        }
        Process.Start(new ProcessStartInfo("explorer.exe", target) { UseShellExecute = true });
        WorkbenchShell.ManagementPanels.RemoteExportActionText.Text = "已打开远端导出目录。";
    }
}
