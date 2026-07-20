using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    internal async void LogsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        if (WorkbenchShell.ManagementPanels.LogsList.SelectedValue is string logId && !string.IsNullOrWhiteSpace(logId))
        {
            await LoadLogsAsync(logId);
        }
    }

    internal void OpenSelectedLogFile()
    {
        if (!TryGetSelectedLogPath(out var path))
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = "没有选中的日志文件。";
            return;
        }
        if (!File.Exists(path))
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = $"日志文件不存在：{path}";
            return;
        }
        Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
    }

    internal void OpenSelectedLogFolder()
    {
        if (!TryGetSelectedLogPath(out var path))
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = "没有选中的日志文件。";
            return;
        }
        if (File.Exists(path))
        {
            Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{path}\"") { UseShellExecute = true });
            return;
        }
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(directory) && Directory.Exists(directory))
        {
            Process.Start(new ProcessStartInfo("explorer.exe", directory) { UseShellExecute = true });
        }
        else
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = $"日志目录不存在：{directory}";
        }
    }

    internal async Task ArchiveSelectedLogAsync()
    {
        if (!TryGetSelectedLogPath(out var path))
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = "没有选中的日志文件。";
            return;
        }
        if (!File.Exists(path))
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = $"日志文件不存在：{path}";
            return;
        }
        var archiveDir = Path.Combine(_rootDir, "state", "log_archives", DateTime.Now.ToString("yyyyMMdd"));
        Directory.CreateDirectory(archiveDir);
        var target = Path.Combine(archiveDir, $"{Path.GetFileNameWithoutExtension(path)}.{DateTime.Now:HHmmss}{Path.GetExtension(path)}");
        File.Move(path, target, overwrite: false);
        WorkbenchShell.ManagementPanels.LogTailBox.Text = $"日志已归档：{target}";
        await LoadLogsAsync();
        await LoadDailyAsync();
    }

    internal async Task DeleteSelectedLogAsync()
    {
        if (!TryGetSelectedLogPath(out var path))
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = "没有选中的日志文件。";
            return;
        }
        if (!File.Exists(path))
        {
            WorkbenchShell.ManagementPanels.LogTailBox.Text = $"日志文件不存在：{path}";
            return;
        }
        if (!ConfirmDestructiveAction("删除日志", $"确定要删除日志文件吗？{Environment.NewLine}{path}"))
        {
            return;
        }
        File.Delete(path);
        WorkbenchShell.ManagementPanels.LogTailBox.Text = $"日志已删除：{path}";
        await LoadLogsAsync();
        await LoadDailyAsync();
    }

    internal bool TryGetSelectedLogPath(out string path)
    {
        path = "";
        var logId = WorkbenchShell.ManagementPanels.LogsList.SelectedValue as string;
        if (string.IsNullOrWhiteSpace(logId) || !_logPaths.TryGetValue(logId, out var resolved) || string.IsNullOrWhiteSpace(resolved))
        {
            return false;
        }
        path = resolved;
        return true;
    }

    internal void CopySelectedDiagnosticCommand()
    {
        if (WorkbenchShell.ManagementPanels.DiagnosticIssuesList.SelectedItem is not ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "先选择一个诊断问题。";
            return;
        }
        if (string.IsNullOrWhiteSpace(item.Command))
        {
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "该问题没有可复制命令。";
            return;
        }
        Clipboard.SetText(item.Command);
        WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "修复命令已复制。";
    }

    internal async Task RunSelectedDiagnosticCommandAsync()
    {
        if (WorkbenchShell.ManagementPanels.DiagnosticIssuesList.SelectedItem is not ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "先选择一个诊断问题。";
            return;
        }
        if (string.IsNullOrWhiteSpace(item.Command))
        {
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "该问题没有可运行命令。";
            return;
        }
        if (item.Command.StartsWith("desktop-repair:", StringComparison.OrdinalIgnoreCase))
        {
            await RepairDiagnosticIssueAsync(item.Id);
            return;
        }
        if (!ConfirmAction(
            "运行诊断命令",
            $"将在内置终端中运行：{Environment.NewLine}{item.Command}",
            "运行"))
        {
            return;
        }
        WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "正在内置终端运行诊断命令...";
        await Workbench.RunCommandInIntegratedTerminalAsync(item.Command);
        WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "诊断命令已在内置终端执行。";
        await Task.Delay(900);
        await LoadDiagnosticsAsync();
    }

    internal async Task AutoRepairDiagnosticsAsync()
    {
        if (!ConfirmAction(
            "一键自修复",
            "将自动修复高优先级诊断问题：启动/重启本项目托管服务，或把 3D manifest 指向已存在的默认模型。不会提交 Git，也不会删除代码文件。",
            "修复"))
        {
            return;
        }
        await RepairDiagnosticsAsync(new { action = "self_repair" });
    }

    internal async Task RepairDiagnosticIssueAsync(string issueId)
    {
        if (string.IsNullOrWhiteSpace(issueId))
        {
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "诊断问题缺少 issue_id。";
            return;
        }
        await RepairDiagnosticsAsync(new { action = "repair", issue_id = issueId });
    }

    internal async Task RepairDiagnosticsAsync(object payload)
    {
        WorkbenchShell.ManagementPanels.AutoRepairDiagnosticsButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.RunDiagnosticCommandButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = "正在执行自修复...";
        try
        {
            using var doc = await PostJsonAsync($"{Workspace.ApiBase()}/desktop/diagnostics", payload);
            var root = doc.RootElement;
            var ok = ReadJsonBool(root, "ok", false);
            var resultText = new List<string>();
            if (root.TryGetProperty("results", out var results) && results.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in results.EnumerateArray())
                {
                    resultText.Add($"{(ReadJsonBool(item, "ok", false) ? "OK" : "FAIL")} {ReadJsonString(item, "issue_id")}: {ReadJsonString(item, "status")} {ReadJsonString(item, "message")}".Trim());
                }
            }
            var summaryText = resultText.Count == 0
                ? (ok ? "自修复完成：没有需要自动处理的问题。" : "自修复未完成。")
                : string.Join(Environment.NewLine, resultText.Take(4));
            await Services.LoadServicesAsync();
            await LoadDiagnosticsAsync();
            await LoadDailyAsync();
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = summaryText;
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = $"自修复失败：{ex.Message}";
        }
        finally
        {
            WorkbenchShell.ManagementPanels.AutoRepairDiagnosticsButton.IsEnabled = true;
            WorkbenchShell.ManagementPanels.RunDiagnosticCommandButton.IsEnabled = true;
        }
    }

}
