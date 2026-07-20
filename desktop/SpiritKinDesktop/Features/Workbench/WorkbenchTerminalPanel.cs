using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkbenchController
{
    private Button CloseTerminalButton => TerminalPanel.CloseTerminalButton;

    private TextBlock TerminalTitleText => TerminalPanel.TerminalTitleText;

    private TextBox TerminalOutputBox => TerminalPanel.TerminalOutputBox;

    internal void LaunchLocalEdgeBrowser()
    {
        EnsureFrontendService();
        var url = $"{FrontendBaseUrl()}/desktop_console.html?cmd={Uri.EscapeDataString(CommandUrl())}&ws={Uri.EscapeDataString(WorkspaceSidebar.WsUrlBox.Text.Trim())}";
        var candidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Microsoft", "Edge", "Application", "msedge.exe"),
        };
        var edge = candidates.FirstOrDefault(File.Exists);
        try
        {
            if (edge is not null)
            {
                Process.Start(new ProcessStartInfo(edge, url) { UseShellExecute = true });
            }
            else
            {
                Process.Start(new ProcessStartInfo("msedge", url) { UseShellExecute = true });
            }
            WorkspaceSidebar.ConnectionStatusText.Text = "已在本地 Edge 浏览器打开工作入口。";
        }
        catch (Exception ex)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = $"打开 Edge 失败：{ex.Message}";
        }
    }

    private static string? FindExistingPath(IEnumerable<string> paths)
    {
        return paths.FirstOrDefault(path => !string.IsNullOrWhiteSpace(path) && File.Exists(path));
    }

    internal const string WorkbenchPanelCollapsedSetting = "workbench.panelCollapsed.v4";

    internal async Task ToggleWorkbenchPanelAsync(bool forceOpen = false)
    {
        _workbenchPanelCollapsed = forceOpen ? false : !_workbenchPanelCollapsed;
        var collapsed = _workbenchPanelCollapsed;
        var persistVersion = Interlocked.Increment(ref _workbenchPanelPersistVersion);
        var state = State;
        state.Settings ??= new Dictionary<string, object?>();
        state.Settings[WorkbenchPanelCollapsedSetting] = collapsed;
        ApplyWorkbenchPanelCollapsed();
        // Rapid clicks only persist the final state. This prevents the shared state
        // save queue from delaying collaboration sends and later desktop interactions.
        await PersistWorkbenchPanelStateAsync(collapsed, persistVersion);
    }

    private async Task PersistWorkbenchPanelStateAsync(bool collapsed, int persistVersion)
    {
        await Task.Delay(250);
        if (Volatile.Read(ref _workbenchPanelPersistVersion) != persistVersion)
        {
            return;
        }
        await SaveStateAsync();
        if (_workbenchPanelCollapsed != collapsed
            || Volatile.Read(ref _workbenchPanelPersistVersion) != persistVersion)
        {
            return;
        }
        DesktopDiagnosticLog.Write(
            _rootDir,
            "workbench",
            collapsed ? "collapse_desktop_status" : "expand_desktop_status",
            "persisted",
            $"workbench={WorkbenchShell.WorkbenchPanelRow.ActualHeight:F1}; splitter={WorkbenchShell.AvatarSplitterRow.ActualHeight:F1}; avatar={WorkbenchShell.AvatarPanelRow.ActualHeight:F1}");
    }

    internal void RestoreWorkbenchPanelCollapsed()
    {
        var state = State;
        state.Settings ??= new Dictionary<string, object?>();
        _workbenchPanelCollapsed = !state.Settings.TryGetValue(WorkbenchPanelCollapsedSetting, out var value)
            ? false
            : value switch
            {
                bool boolean => boolean,
                string text => bool.TryParse(text, out var parsed) && parsed,
                System.Text.Json.JsonElement { ValueKind: System.Text.Json.JsonValueKind.False } => false,
                System.Text.Json.JsonElement { ValueKind: System.Text.Json.JsonValueKind.True } => true,
                _ => false,
            };
        ApplyWorkbenchPanelCollapsed();
    }

    private void ApplyWorkbenchPanelCollapsed()
    {
        var collapsed = _workbenchPanelCollapsed;

        DesktopDiagnosticLog.Write(
            _rootDir,
            "workbench",
            collapsed ? "collapse_desktop_status" : "expand_desktop_status",
            "restored",
            $"collapsed={collapsed}");

        WorkbenchShell.WorkbenchStatusPanel.Visibility = collapsed ? Visibility.Collapsed : Visibility.Visible;
        WorkbenchShell.WorkbenchRestoreBar.Visibility = collapsed ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.AvatarPanelSplitter.Visibility = Visibility.Collapsed;
        WorkbenchShell.CollapseWorkbenchPanelButton.ToolTip = "收起桌面状态";
        WorkbenchShell.AnimateWorkbenchPanelReveal(collapsed);
        ApplyWorkbenchPanelFinalState(collapsed);
    }

    private void ApplyWorkbenchPanelFinalState(bool collapsed)
    {
        WorkbenchShell.ApplyWorkbenchPanelFinalGeometry();
        WorkbenchShell.WorkbenchStatusPanel.Visibility = collapsed ? Visibility.Collapsed : Visibility.Visible;
        WorkbenchShell.WorkbenchRestoreBar.Visibility = collapsed ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.AvatarPanelSplitter.Visibility = Visibility.Collapsed;
    }

    internal async Task ShowTerminalAsync()
    {
        TerminalPanel.Visibility = Visibility.Visible;
        TerminalRow.Height = new GridLength(260);
        TerminalTitleText.Text = $"{Path.GetFileNameWithoutExtension(ResolveShellExecutable())} · workspace";
        try
        {
            await EnsureTerminalSessionAsync();
            FocusTerminalInput();
            WorkspaceSidebar.ConnectionStatusText.Text = "内置终端已连接。";
        }
        catch (Exception ex)
        {
            AppendTerminalLine($"内置终端启动失败：{ex.Message}");
            WorkspaceSidebar.ConnectionStatusText.Text = $"内置终端启动失败：{ex.Message}";
        }
    }

    internal void OpenPowerShellWindow()
    {
        _ = ShowTerminalAsync();
        WorkspaceSidebar.ConnectionStatusText.Text = "已打开内置终端。";
    }

    internal void HideTerminal()
    {
        TerminalPanel.Visibility = Visibility.Collapsed;
        TerminalRow.Height = new GridLength(0);
    }

}



