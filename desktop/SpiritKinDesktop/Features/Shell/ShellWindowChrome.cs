using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    private const int WmGetMinMaxInfo = 0x0024;
    private const uint MonitorDefaultToNearest = 0x00000002;
    private HwndSource? _windowHwndSource;

    private void MainWindow_SourceInitialized(object? sender, EventArgs e)
    {
        var handle = new WindowInteropHelper(this).Handle;
        _windowHwndSource = HwndSource.FromHwnd(handle);
        _windowHwndSource?.AddHook(WindowMessageHook);
    }

    private void DetachWindowChromeHook()
    {
        _windowHwndSource?.RemoveHook(WindowMessageHook);
        _windowHwndSource = null;
    }

    private IntPtr WindowMessageHook(
        IntPtr hwnd,
        int message,
        IntPtr wParam,
        IntPtr lParam,
        ref bool handled)
    {
        if (message != WmGetMinMaxInfo)
        {
            return IntPtr.Zero;
        }

        var monitor = MonitorFromWindow(hwnd, MonitorDefaultToNearest);
        var monitorInfo = new MonitorInfo
        {
            Size = Marshal.SizeOf<MonitorInfo>(),
        };
        if (monitor == IntPtr.Zero || !GetMonitorInfo(monitor, ref monitorInfo))
        {
            return IntPtr.Zero;
        }

        var info = Marshal.PtrToStructure<MinMaxInfo>(lParam);
        var work = monitorInfo.WorkArea;
        var bounds = monitorInfo.MonitorArea;
        info.MaxPosition.X = work.Left - bounds.Left;
        info.MaxPosition.Y = work.Top - bounds.Top;
        info.MaxSize.X = work.Right - work.Left;
        info.MaxSize.Y = work.Bottom - work.Top;
        info.MaxTrackSize = info.MaxSize;
        Marshal.StructureToPtr(info, lParam, fDeleteOld: false);
        handled = true;
        return IntPtr.Zero;
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        _workspaceController.SetStatus("启动本地服务...");
        await Task.Run(_workspaceController.EnsureLocalServices);
        await _runtimeController.RefreshAllAsync();
        // 状态已同步：回放持久化的主题偏好（覆盖 App 启动时的"跟随系统"兜底）。
        InitializeThemeFromState();
        StartAtelierAmbientMotion();
        _ = _contextController.LoadCollaborationAutoReplyAsync();
        _contextController.CleanOrphanCollaborationWorkers();
        _ = Dispatcher.InvokeAsync(() =>
        {
            _workspaceController.ShowWorkspacePage("chat");
        }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
        _ = Task.Run(() =>
        {
            _workbenchController.PrimeGitStatus();
        });
    }

    private async void MainWindow_Closed(object? sender, EventArgs e)
    {
        DetachWindowChromeHook();
        StopAtelierAmbientMotion();
        StopServiceHealthWatchdog();
        _controlMonitorTimer.Stop();
        _contextController.StopSyncTimer();
        _voiceCallController?.Dispose();
        _voiceCallController = null;
        _musicPlayerController.Dispose();
        _runtimeController.StopDesktopTtsPlayback();
        await _runtimeController.SaveStateAsync();
        _agentsController.StopExternalAssistantPromptOnShutdown();
        _workbenchController.DisposeTerminal();
        _workspaceController.DisposeRuntime();
        _http.Dispose();
    }

    private void ToggleWindowMaximized()
    {
        if (WindowState == WindowState.Maximized)
        {
            SystemCommands.RestoreWindow(this);
        }
        else
        {
            SystemCommands.MaximizeWindow(this);
        }
        SyncWindowChromeState();
    }

    private void SyncWindowChromeState()
    {
        TitleBar.CaptionButtons.SetMaximized(WindowState == WindowState.Maximized);
    }

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MinMaxInfo
    {
        public NativePoint Reserved;
        public NativePoint MaxSize;
        public NativePoint MaxPosition;
        public NativePoint MinTrackSize;
        public NativePoint MaxTrackSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private struct MonitorInfo
    {
        public int Size;
        public NativeRect MonitorArea;
        public NativeRect WorkArea;
        public uint Flags;
    }

}
