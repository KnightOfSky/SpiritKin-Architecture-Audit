using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    private async void MenuNewChat_Click(object sender, RoutedEventArgs e) => await _runtimeController.NewSessionAsync();

    private async void MenuNewProject_Click(object sender, RoutedEventArgs e) => await _runtimeController.CreateProjectFromSidebarAsync();

    private void MenuOpenWorkspace_Click(object sender, RoutedEventArgs e) => _navigationController.OpenWorkspaceInExplorer();

    private async void MenuRefreshAll_Click(object sender, RoutedEventArgs e) => await _runtimeController.RefreshAllAsync();

    private void MenuExit_Click(object sender, RoutedEventArgs e) => Close();

    private void MenuUndo_Click(object sender, RoutedEventArgs e) => _shellInteractionController.ExecuteTextEditAction("undo");

    private void MenuFind_Click(object sender, RoutedEventArgs e) => _globalSearchController.OpenGlobalSearch();

    private async void MenuDelete_Click(object sender, RoutedEventArgs e) => await _navigationController.DeleteCurrentSelectionAsync();

    private void MenuCut_Click(object sender, RoutedEventArgs e) => _shellInteractionController.ExecuteTextEditAction("cut");

    private void MenuCopy_Click(object sender, RoutedEventArgs e) => _shellInteractionController.ExecuteTextEditAction("copy");

    private void MenuPaste_Click(object sender, RoutedEventArgs e) => _shellInteractionController.ExecuteTextEditAction("paste");

    private void MenuSelectAll_Click(object sender, RoutedEventArgs e) => _shellInteractionController.ExecuteTextEditAction("select_all");

    private void MenuQuickChat_Click(object sender, RoutedEventArgs e)
    {
        _workspaceController.OpenQuickChat();
    }

    private void OpenVoiceCall()
    {
        _voiceCallController ??= new VoiceCallSessionController(
            this,
            _rootDir,
            _runtimeController,
            () =>
            {
                _workspaceController.OpenQuickChat();
                ChatWorkspace.EmptyPromptBox.Focus();
            });
        _ = _voiceCallController.OpenAsync();
    }

    private void MenuManagement_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("tasks");

    private void MenuShowActiveChats_Click(object sender, RoutedEventArgs e)
    {
        ShowChatsWithFilter("active");
    }

    private void MenuShowArchivedChats_Click(object sender, RoutedEventArgs e)
    {
        ShowChatsWithFilter("archived");
    }

    private void MenuShowAllChats_Click(object sender, RoutedEventArgs e)
    {
        ShowChatsWithFilter("all");
    }

    private void MenuOpenTasks_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("tasks", "tasks");

    private void MenuOpenProjects_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("tasks", "projects");

    private void MenuOpenCommands_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("commands");

    private void MenuOpenModels_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("models");

    private void MenuMinimize_Click(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;

    private void MenuMaximizeRestore_Click(object sender, RoutedEventArgs e) => ToggleWindowMaximized();

    private async void MenuRefreshAvatar_Click(object sender, RoutedEventArgs e) => await _workspaceController.LoadAvatarAsync();

    private async void MenuFloatAvatar_Click(object sender, RoutedEventArgs e) => await _workspaceController.OpenAvatarFloatWindowAsync();

    private void MenuOpenDiagnostics_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("diagnostics");

    private void MenuOpenLogs_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("logs");

    private void MenuOpenServices_Click(object sender, RoutedEventArgs e) => _workspaceController.OpenManagementPage("services");

    private void MenuToggleDesktopTts_Click(object sender, RoutedEventArgs e)
    {
        ToggleDesktopTtsFromMenu();
    }

    private void ShowChatsWithFilter(string filter)
    {
        _workspaceController.ClearWorkspaceProjectContextId();
        _workspaceController.SetSessionFilter(filter);
        _runtimeController.RenderState();
        _workspaceController.ShowWorkspacePage("chat");
    }

    private void ToggleDesktopTtsFromMenu()
    {
        var enabled = TitleBar.AppMenu.IsDesktopTtsChecked;
        _composerController.SetDesktopTtsEnabled(enabled);
        _runtimeController.SyncDesktopTtsMenu();
        if (!enabled)
        {
            _runtimeController.StopDesktopTtsPlayback();
        }
        WorkspaceSidebar.ConnectionStatusText.Text = enabled ? "Desktop TTS 已开启。" : "Desktop TTS 已关闭。";
        _ = _runtimeController.SaveStateAsync();
    }

    /// <summary>菜单选择日/夜/跟随系统：热切主题、同步勾选、持久化偏好、联动 WebView2 内嵌页。</summary>
    private void ApplyThemeModeFromMenu(ThemeManager.ThemeMode mode)
    {
        var isDark = ThemeManager.ApplyMode(mode);
        _runtimeController.RenderState();
        TitleBar.AppMenu.SetThemeMode(ThemeManager.SerializeMode(mode));
        TitleBar.SetThemeToggleState(isDark);
        StartAtelierAmbientMotion();
        _composerController.SetSetting(ThemeManager.ThemeModeSettingKey, ThemeManager.SerializeMode(mode));
        WorkspaceSidebar.ConnectionStatusText.Text = mode switch
        {
            ThemeManager.ThemeMode.Light => "外观已切换：日间。",
            ThemeManager.ThemeMode.Dark => "外观已切换：夜间。",
            _ => isDark ? "外观已切换：跟随系统（当前夜间）。" : "外观已切换：跟随系统（当前日间）。",
        };
        _ = _runtimeController.SaveStateAsync();
    }

    /// <summary>启动/状态加载后回放持久化的主题偏好（默认跟随系统），并同步菜单勾选。</summary>
    private void InitializeThemeFromState()
    {
        var raw = _composerController.GetSettingString(ThemeManager.ThemeModeSettingKey, "dark");
        var mode = ThemeManager.ParseMode(raw);
        var isDark = ThemeManager.ApplyMode(mode);
        _runtimeController.RenderState();
        TitleBar.AppMenu.SetThemeMode(ThemeManager.SerializeMode(mode));
        TitleBar.SetThemeToggleState(isDark);
    }

    private void MenuAbout_Click(object sender, RoutedEventArgs e)
    {
        _navigationController.ShowAppDialog("About SpiritKin", "SpiritKin Desktop Workbench", destructive: false, confirmText: "确定", cancelText: "");
    }

    private void ToggleChatsCollapsed()
    {
        _chatsCollapsed = !_chatsCollapsed;
        WorkspaceSidebar.SessionsList.Visibility = _chatsCollapsed ? Visibility.Collapsed : Visibility.Visible;
        WorkspaceSidebar.ToggleChatsButton.Content = _chatsCollapsed ? "›" : "⌄";
    }

    private void ToggleProjectsCollapsed()
    {
        _projectsCollapsed = !_projectsCollapsed;
        WorkspaceSidebar.ProjectsList.Visibility = _projectsCollapsed ? Visibility.Collapsed : Visibility.Visible;
        WorkspaceSidebar.ToggleProjectsButton.Content = _projectsCollapsed ? "›" : "⌄";
    }

    private void ToggleCollaborationCollapsed()
    {
        _contextController.ToggleCollaborationCollapsed();
    }

}


