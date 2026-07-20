using System;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop.Controls;

public partial class WindowAppMenu : UserControl
{
    public event EventHandler? NewChatRequested;
    public event EventHandler? NewProjectRequested;
    public event EventHandler? OpenWorkspaceRequested;
    public event EventHandler? RefreshAllRequested;
    public event EventHandler? ExitRequested;
    public event EventHandler? UndoRequested;
    public event EventHandler? FindRequested;
    public event EventHandler? DeleteRequested;
    public event EventHandler? CutRequested;
    public event EventHandler? CopyRequested;
    public event EventHandler? PasteRequested;
    public event EventHandler? SelectAllRequested;
    public event EventHandler? QuickChatRequested;
    public event EventHandler? VoiceCallRequested;
    public event EventHandler? MusicPlayerRequested;
    public event EventHandler? ManagementRequested;
    public event EventHandler? ToggleDesktopTtsRequested;
    public event EventHandler? ThemeSystemRequested;
    public event EventHandler? ThemeLightRequested;
    public event EventHandler? ThemeDarkRequested;
    public event EventHandler? ShowActiveChatsRequested;
    public event EventHandler? ShowArchivedChatsRequested;
    public event EventHandler? ShowAllChatsRequested;
    public event EventHandler? OpenTasksRequested;
    public event EventHandler? OpenProjectsRequested;
    public event EventHandler? OpenCommandsRequested;
    public event EventHandler? OpenModelsRequested;
    public event EventHandler? MinimizeRequested;
    public event EventHandler? MaximizeRestoreRequested;
    public event EventHandler? RefreshAvatarRequested;
    public event EventHandler? FloatAvatarRequested;
    public event EventHandler? OpenDiagnosticsRequested;
    public event EventHandler? OpenLogsRequested;
    public event EventHandler? OpenServicesRequested;
    public event EventHandler? AboutRequested;

    public WindowAppMenu()
    {
        InitializeComponent();
    }

    public bool IsDesktopTtsChecked => DesktopTtsMenuItem.IsChecked == true;

    public void SetDesktopTtsState(bool enabled)
    {
        DesktopTtsMenuItem.IsChecked = enabled;
        DesktopTtsMenuItem.Header = enabled ? "Desktop TTS: on" : "Desktop TTS: off";
    }

    /// <summary>同步外观主题三态勾选(跟随系统/日间/夜间互斥)。mode: "system"|"light"|"dark"。</summary>
    public void SetThemeMode(string mode)
    {
        ThemeSystemMenuItem.IsChecked = string.Equals(mode, "system", StringComparison.OrdinalIgnoreCase);
        ThemeLightMenuItem.IsChecked = string.Equals(mode, "light", StringComparison.OrdinalIgnoreCase);
        ThemeDarkMenuItem.IsChecked = string.Equals(mode, "dark", StringComparison.OrdinalIgnoreCase);
    }

    private static void Raise(EventHandler? handler, object sender)
    {
        handler?.Invoke(sender, EventArgs.Empty);
    }

    private void NewChat_Click(object sender, RoutedEventArgs e) => Raise(NewChatRequested, sender);
    private void NewProject_Click(object sender, RoutedEventArgs e) => Raise(NewProjectRequested, sender);
    private void OpenWorkspace_Click(object sender, RoutedEventArgs e) => Raise(OpenWorkspaceRequested, sender);
    private void RefreshAll_Click(object sender, RoutedEventArgs e) => Raise(RefreshAllRequested, sender);
    private void Exit_Click(object sender, RoutedEventArgs e) => Raise(ExitRequested, sender);
    private void Undo_Click(object sender, RoutedEventArgs e) => Raise(UndoRequested, sender);
    private void Find_Click(object sender, RoutedEventArgs e) => Raise(FindRequested, sender);
    private void Delete_Click(object sender, RoutedEventArgs e) => Raise(DeleteRequested, sender);
    private void Cut_Click(object sender, RoutedEventArgs e) => Raise(CutRequested, sender);
    private void Copy_Click(object sender, RoutedEventArgs e) => Raise(CopyRequested, sender);
    private void Paste_Click(object sender, RoutedEventArgs e) => Raise(PasteRequested, sender);
    private void SelectAll_Click(object sender, RoutedEventArgs e) => Raise(SelectAllRequested, sender);
    private void QuickChat_Click(object sender, RoutedEventArgs e) => Raise(QuickChatRequested, sender);
    private void VoiceCall_Click(object sender, RoutedEventArgs e) => Raise(VoiceCallRequested, sender);
    private void MusicPlayer_Click(object sender, RoutedEventArgs e) => Raise(MusicPlayerRequested, sender);
    private void Management_Click(object sender, RoutedEventArgs e) => Raise(ManagementRequested, sender);
    private void ToggleDesktopTts_Click(object sender, RoutedEventArgs e) => Raise(ToggleDesktopTtsRequested, sender);
    private void ThemeSystem_Click(object sender, RoutedEventArgs e) => Raise(ThemeSystemRequested, sender);
    private void ThemeLight_Click(object sender, RoutedEventArgs e) => Raise(ThemeLightRequested, sender);
    private void ThemeDark_Click(object sender, RoutedEventArgs e) => Raise(ThemeDarkRequested, sender);
    private void ShowActiveChats_Click(object sender, RoutedEventArgs e) => Raise(ShowActiveChatsRequested, sender);
    private void ShowArchivedChats_Click(object sender, RoutedEventArgs e) => Raise(ShowArchivedChatsRequested, sender);
    private void ShowAllChats_Click(object sender, RoutedEventArgs e) => Raise(ShowAllChatsRequested, sender);
    private void OpenTasks_Click(object sender, RoutedEventArgs e) => Raise(OpenTasksRequested, sender);
    private void OpenProjects_Click(object sender, RoutedEventArgs e) => Raise(OpenProjectsRequested, sender);
    private void OpenCommands_Click(object sender, RoutedEventArgs e) => Raise(OpenCommandsRequested, sender);
    private void OpenModels_Click(object sender, RoutedEventArgs e) => Raise(OpenModelsRequested, sender);
    private void Minimize_Click(object sender, RoutedEventArgs e) => Raise(MinimizeRequested, sender);
    private void MaximizeRestore_Click(object sender, RoutedEventArgs e) => Raise(MaximizeRestoreRequested, sender);
    private void RefreshAvatar_Click(object sender, RoutedEventArgs e) => Raise(RefreshAvatarRequested, sender);
    private void FloatAvatar_Click(object sender, RoutedEventArgs e) => Raise(FloatAvatarRequested, sender);
    private void OpenDiagnostics_Click(object sender, RoutedEventArgs e) => Raise(OpenDiagnosticsRequested, sender);
    private void OpenLogs_Click(object sender, RoutedEventArgs e) => Raise(OpenLogsRequested, sender);
    private void OpenServices_Click(object sender, RoutedEventArgs e) => Raise(OpenServicesRequested, sender);
    private void About_Click(object sender, RoutedEventArgs e) => Raise(AboutRequested, sender);
}
