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
    internal void NewExternalAssistant()
    {
        var assistant = new ExternalAssistantViewModel(
            UniqueId("assistant", _externalAssistants.Select(item => item.AssistantId)),
            "新外部助手",
            "cli",
            "",
            _rootDir,
            "general",
            false,
            false,
            true);
        _externalAssistants.Add(assistant);
        WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue = assistant.AssistantId;
        RenderSelectedExternalAssistantEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已新增外部助手：{assistant.AssistantId}。";
    }

    internal bool ApplySelectedExternalAssistantFromEditor(bool showMessage)
    {
        if (_externalAssistants.Count == 0 && !ExternalAssistantEditorHasContent())
        {
            return true;
        }
        var selectedIndex = WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedIndex;
        var fallbackId = selectedIndex >= 0 && selectedIndex < _externalAssistants.Count
            ? _externalAssistants[selectedIndex].AssistantId
            : UniqueId("assistant", _externalAssistants.Select(item => item.AssistantId));
        var updated = BuildExternalAssistantFromEditor(fallbackId);
        if (_externalAssistants.Where((_, index) => index != selectedIndex).Any(item => string.Equals(item.AssistantId, updated.AssistantId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"助手 ID 已存在：{updated.AssistantId}";
            return false;
        }
        SetRendering(true);
        try
        {
            if (selectedIndex >= 0 && selectedIndex < _externalAssistants.Count)
            {
                _externalAssistants[selectedIndex] = updated;
            }
            else
            {
                _externalAssistants.Add(updated);
            }
            WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue = updated.AssistantId;
        }
        finally
        {
            SetRendering(false);
        }
        if (showMessage)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已应用外部助手修改：{updated.AssistantId}";
        }
        return true;
    }

    internal ExternalAssistantViewModel BuildExternalAssistantFromEditor(string fallbackId)
    {
        var assistantId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ExternalAssistantIdBox.Text) ? fallbackId : WorkbenchShell.ManagementPanels.ExternalAssistantIdBox.Text.Trim();
        return new ExternalAssistantViewModel(
            assistantId,
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ExternalAssistantLabelBox.Text) ? assistantId : WorkbenchShell.ManagementPanels.ExternalAssistantLabelBox.Text.Trim(),
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.ExternalAssistantKindBox)) ? "cli" : ComboText(WorkbenchShell.ManagementPanels.ExternalAssistantKindBox),
            WorkbenchShell.ManagementPanels.ExternalAssistantCommandBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.ExternalAssistantWorkingDirectoryBox.Text.Trim(),
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.ExternalAssistantCategoryBox)) ? "general" : ComboText(WorkbenchShell.ManagementPanels.ExternalAssistantCategoryBox),
            WorkbenchShell.ManagementPanels.ExternalAssistantEnabledBox.IsChecked == true,
            WorkbenchShell.ManagementPanels.ExternalAssistantAllowWriteBox.IsChecked == true,
            WorkbenchShell.ManagementPanels.ExternalAssistantReviewOnlyBox.IsChecked == true);
    }

    internal void DeleteSelectedExternalAssistant()
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedIndex;
        if (selectedIndex < 0 || selectedIndex >= _externalAssistants.Count)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择要删除的外部助手。";
            return;
        }
        var removed = _externalAssistants[selectedIndex];
        if (!ConfirmDestructiveAction("删除外部助手", $"确定要删除外部助手“{removed.AssistantId}”吗？保存集群配置后生效。"))
        {
            return;
        }
        _externalAssistants.RemoveAt(selectedIndex);
        WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue = _externalAssistants.Count == 0 ? null : _externalAssistants[Math.Min(selectedIndex, _externalAssistants.Count - 1)].AssistantId;
        RenderSelectedExternalAssistantEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已删除外部助手：{removed.AssistantId}。保存集群配置后生效。";
    }

    internal void LaunchSelectedExternalAssistant()
    {
        if (!ApplySelectedExternalAssistantFromEditor(showMessage: false))
        {
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue as string;
        var assistant = _externalAssistants.FirstOrDefault(item => item.AssistantId == selectedId);
        if (assistant is null)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "请先选择外部助手。";
            return;
        }
        if (!assistant.Enabled)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "该助手未启用。";
            return;
        }
        if (!string.Equals(assistant.Kind, "cli", StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "API 类型助手不从桌面启动，请在学习或模型评审中调用。";
            return;
        }
        if (string.IsNullOrWhiteSpace(assistant.Command))
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "该助手没有配置命令。";
            return;
        }
        var workingDirectory = ResolveAssistantWorkingDirectory(assistant.WorkingDirectory);
        var runtime = ActiveProjectRuntimeProfile();
        Directory.CreateDirectory(workingDirectory);
        if (!ConfirmAction(
            "打开外部助手窗口",
            $"将打开一个独立 PowerShell 窗口运行外部助手：{Environment.NewLine}{assistant.Command}{Environment.NewLine}{Environment.NewLine}普通聊天和“内嵌发送”不会打开这个窗口。",
            "打开"))
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "已取消打开外部助手窗口。";
            return;
        }
        var command = assistant.Command.Replace("\"", "\\\"");
        var startInfo = new ProcessStartInfo("powershell.exe", $"-NoExit -NoProfile -Command \"Set-Location -LiteralPath '{workingDirectory.Replace("'", "''")}'; {command}\"")
        {
            UseShellExecute = false,
            CreateNoWindow = false,
            WindowStyle = ProcessWindowStyle.Normal,
            WorkingDirectory = workingDirectory,
        };
        ApplyEnvironment(startInfo, BuildProjectRuntimeEnvironment(runtime));
        Process.Start(startInfo);
        WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = $"已打开外部窗口：{assistant.Label} · {(assistant.ReviewOnly ? "review-only" : assistant.AllowWrite ? "allow-write" : "read-only")}";
    }

    internal async Task RunSelectedExternalAssistantPromptAsync()
    {
        if (!ApplySelectedExternalAssistantFromEditor(showMessage: false))
        {
            return;
        }
        if (_externalAssistantProcess is { HasExited: false })
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "外部助手正在运行，先停止当前会话。";
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue as string;
        var assistant = _externalAssistants.FirstOrDefault(item => item.AssistantId == selectedId);
        if (assistant is null)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "请先选择外部助手。";
            return;
        }
        if (!assistant.Enabled)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "该助手未启用。";
            return;
        }
        if (!string.Equals(assistant.Kind, "cli", StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "API 类型助手不支持内嵌 CLI 输出。";
            return;
        }
        if (string.IsNullOrWhiteSpace(assistant.Command))
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "该助手没有配置命令。";
            return;
        }

        var prompt = WorkbenchShell.ManagementPanels.ExternalAssistantPromptBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(prompt))
        {
            prompt = ChatWorkspace.PromptBox.Text.Trim();
        }
        if (string.IsNullOrWhiteSpace(prompt))
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "请输入要发送给外部助手的内容。";
            return;
        }

        var workingDirectory = ResolveAssistantWorkingDirectory(assistant.WorkingDirectory);
        var runtime = ActiveProjectRuntimeProfile();
        Directory.CreateDirectory(workingDirectory);
        var scriptPath = WriteExternalAssistantScript(assistant, workingDirectory);
        WorkbenchShell.ManagementPanels.ExternalAssistantOutputBox.Clear();
        AppendExternalAssistantOutput($"> {assistant.Command}{Environment.NewLine}");
        WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = $"正在运行：{assistant.Label}";
        WorkbenchShell.ManagementPanels.RunExternalAssistantPromptButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.StopExternalAssistantPromptButton.IsEnabled = true;
        _externalAssistantCts = new CancellationTokenSource();

        try
        {
            var startInfo = new ProcessStartInfo("powershell.exe", $"-NoProfile -ExecutionPolicy Bypass -File \"{scriptPath}\"")
            {
                WorkingDirectory = workingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            ApplyEnvironment(startInfo, BuildProjectRuntimeEnvironment(runtime));
            var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            process.OutputDataReceived += (_, args) =>
            {
                if (args.Data is not null)
                {
                    Dispatcher.Invoke(() => AppendExternalAssistantOutput(args.Data + Environment.NewLine));
                }
            };
            process.ErrorDataReceived += (_, args) =>
            {
                if (args.Data is not null)
                {
                    Dispatcher.Invoke(() => AppendExternalAssistantOutput("[stderr] " + args.Data + Environment.NewLine));
                }
            };
            _externalAssistantProcess = process;
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            await process.StandardInput.WriteLineAsync(prompt);
            process.StandardInput.Close();
            await process.WaitForExitAsync(_externalAssistantCts.Token);
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = $"外部助手已结束，退出码 {process.ExitCode}。";
        }
        catch (OperationCanceledException)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "外部助手会话已停止。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = $"外部助手运行失败：{ex.Message}";
        }
        finally
        {
            WorkbenchShell.ManagementPanels.RunExternalAssistantPromptButton.IsEnabled = true;
            WorkbenchShell.ManagementPanels.StopExternalAssistantPromptButton.IsEnabled = false;
            _externalAssistantProcess?.Dispose();
            _externalAssistantProcess = null;
            _externalAssistantCts?.Dispose();
            _externalAssistantCts = null;
        }
    }

    internal string WriteExternalAssistantScript(ExternalAssistantViewModel assistant, string workingDirectory)
    {
        var runDir = Path.Combine(_rootDir, "state", "desktop_console", "external_assistants");
        Directory.CreateDirectory(runDir);
        var scriptPath = Path.Combine(runDir, $"{SafeFileName(assistant.AssistantId)}.ps1");
        var command = assistant.Command.Replace("'", "''");
        var directory = workingDirectory.Replace("'", "''");
        var script = $"Set-Location -LiteralPath '{directory}'{Environment.NewLine}Invoke-Expression '{command}'{Environment.NewLine}";
        File.WriteAllText(scriptPath, script, Encoding.UTF8);
        return scriptPath;
    }

    internal void AppendExternalAssistantOutput(string text)
    {
        WorkbenchShell.ManagementPanels.ExternalAssistantOutputBox.AppendText(text);
        WorkbenchShell.ManagementPanels.ExternalAssistantOutputBox.ScrollToEnd();
    }

    internal void StopExternalAssistantPrompt(bool killOnly = false)
    {
        try
        {
            _externalAssistantCts?.Cancel();
            if (_externalAssistantProcess is { HasExited: false } process)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // best effort stop on shutdown/user cancel
        }
        if (!killOnly)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantActionText.Text = "已请求停止外部助手。";
        }
    }

    internal string ResolveAssistantWorkingDirectory(string raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return ActiveWorkspaceRoot();
        }
        var expanded = Environment.ExpandEnvironmentVariables(raw.Trim());
        return Path.GetFullPath(Path.IsPathRooted(expanded) ? expanded : Path.Combine(_rootDir, expanded));
    }

}

